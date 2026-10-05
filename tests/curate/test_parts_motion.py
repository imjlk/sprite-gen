# SPDX-License-Identifier: Apache-2.0
"""Hash-bound motion freezes, active revision invalidation and atomic history."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest
from PIL import Image

from conftest import run_script
from parts_helpers import external_source, import_spec, source_request, tree_hashes, verified_annotations, workspace_at
from sprite_gen.curate import parts_motion
from sprite_gen.spec import parts_assets as library, parts_contract as contract, parts_workspace as workspace


def setup_motion(tmp_path):
    root = tmp_path / "library"
    source = tmp_path / "source"
    assembly = workspace_at(root)
    external_source(source)
    spec = import_spec(tmp_path / "import.json", assembly, [source_request(source)])
    result = parts_motion.import_motion(assembly, spec, source)
    return root, source, assembly, spec, result


def test_import_and_freeze_preserve_sources_and_create_a_self_contained_snapshot(tmp_path):
    root, source, assembly, spec, result = setup_motion(tmp_path)
    original = tree_hashes(source)
    with pytest.raises(contract.PartsContractError, match="unverified"):
        parts_motion.freeze_motion(assembly, result["candidate"], root / result["annotation_template"])
    annotations = verified_annotations(root, result)
    frozen = parts_motion.freeze_motion(assembly, result["candidate"], annotations)
    assert tree_hashes(source) == original
    assert frozen["status"] == "frozen" and frozen["release_ready"] is False
    index, registry = workspace.read_workspace(assembly)
    motion = registry[index["motion"]["id"]]
    assert index["motion"] == frozen["motion"]
    assert [s["duration_ticks"] for s in motion["clips"][0]["samples"]] == [30000, 15000, 30000]
    assert motion["freeze"]["candidate_digest"] == result["digest"]
    before = tree_hashes((root / frozen["file"]).parent)
    Image.new("RGBA", (128, 128), "blue").save(source / "pose-0.png")
    assert tree_hashes((root / frozen["file"]).parent) == before
    # Retrying identical input reuses the immutable object and preserves edits.
    again = parts_motion.freeze_motion(assembly, result["candidate"], annotations)
    assert again["motion"] == frozen["motion"]


@pytest.mark.parametrize("mutation,match", [
    (lambda d: d.update(candidate_digest="f" * 64), "different candidate"),
    (lambda d: d["clips"]["walk.right"].pop("sample.1"), "sample coverage"),
    (lambda d: d["clips"]["walk.right"]["sample.0"].update(image_sha256="a" * 64), "raster hash"),
    (lambda d: d["clips"]["walk.right"]["sample.0"]["sockets"].pop("root"), "required_socket"),
    (lambda d: d["clips"]["walk.right"]["sample.0"]["sockets"]["root"].update(position=[999, 64]), "outside"),
])
def test_invalid_annotations_cannot_publish_or_activate_motion(tmp_path, mutation, match):
    root, _, assembly, _, result = setup_motion(tmp_path)
    path = verified_annotations(root, result)
    data = json.loads(path.read_text())
    mutation(data)
    path.write_bytes(workspace.json_bytes(data))
    previous = assembly.read_bytes()
    with pytest.raises(contract.PartsContractError, match=match):
        parts_motion.freeze_motion(assembly, result["candidate"], path)
    assert assembly.read_bytes() == previous and not (root / "motions").exists()


def test_candidate_raster_tampering_is_detected_before_freeze(tmp_path):
    root, _, assembly, _, result = setup_motion(tmp_path)
    annotations = verified_annotations(root, result)
    candidate = root / result["candidate"]
    data = json.loads(candidate.read_text())
    sample = data["motion"]["clips"][0]["samples"][0]
    Image.new("RGBA", (128, 128), "blue").save(candidate.parent / sample["image"]["file"])
    with pytest.raises(contract.PartsContractError, match="hash mismatch"):
        parts_motion.freeze_motion(assembly, result["candidate"], annotations)


def test_actual_native_finalize_is_read_only_and_does_not_infer_landmarks(fixture_run_dir, tmp_path):
    for script in ("extract_sprite_row_frames.py", "compose_sprite_atlas.py"):
        result = run_script(script, "--run-dir", str(fixture_run_dir))
        assert result.returncode == 0, result.stdout + result.stderr
    original = tree_hashes(fixture_run_dir)
    root = tmp_path / "library"
    assembly = workspace_at(root, preset="blob.fixture.v1")
    request = source_request(fixture_run_dir, "manifest.json", source_state="walk", source_mode="native-finalized")
    # The adapter reads only the finalized descriptor and its declared sheet.
    descriptor = json.loads((fixture_run_dir / "manifest.json").read_text())
    names = ["manifest.json", descriptor["sprite_sheet_alpha"]]
    request["expected_files"] = {name: library.sha256((fixture_run_dir / name).read_bytes()) for name in names}
    spec = import_spec(tmp_path / "import.json", assembly, [request])
    result = parts_motion.import_motion(assembly, spec, fixture_run_dir)
    candidate = json.loads((root / result["candidate"]).read_text())
    assert all(sample["sockets"] == {} for sample in candidate["motion"]["clips"][0]["samples"])
    path = verified_annotations(root, result, point=(48, 48))
    frozen = parts_motion.freeze_motion(assembly, result["candidate"], path)
    assert tree_hashes(fixture_run_dir) == original
    motion = contract.parse(json.loads((root / frozen["file"]).read_text()))
    native_ms = descriptor["animation"]["rows"]["walk"]["durations_ms"]
    assert [s["duration_ticks"] for s in motion["clips"][0]["samples"]] == [ms * 120 for ms in native_ms]


def test_repeat_import_is_portable_across_roots_and_preserves_annotation_edits(tmp_path):
    outputs = []
    for name in ["a", "b"]:
        base = tmp_path / name
        base.mkdir()
        root, source, assembly, spec, result = setup_motion(base)
        annotations = verified_annotations(root, result)
        edited = annotations.read_bytes()
        assert parts_motion.import_motion(assembly, spec, source) == result
        assert annotations.read_bytes() == edited
        frozen = parts_motion.freeze_motion(assembly, result["candidate"], annotations)
        outputs.append((result["digest"], frozen["motion"], tree_hashes((root / frozen["file"]).parent)))
    assert outputs[0] == outputs[1]


def test_failure_during_object_writes_preserves_the_active_index(tmp_path, monkeypatch):
    root, _, assembly, _, result = setup_motion(tmp_path)
    annotations = verified_annotations(root, result)
    previous = assembly.read_bytes()
    original = Path.write_bytes

    def fail_motion_file(path, data):
        if path.name == "motion.json":
            raise OSError("injected disk failure")
        return original(path, data)

    monkeypatch.setattr(Path, "write_bytes", fail_motion_file)
    with pytest.raises(OSError, match="disk failure"):
        parts_motion.freeze_motion(assembly, result["candidate"], annotations)
    assert assembly.read_bytes() == previous
    assert not (root / "motions").exists()
    assert not list(root.glob(".motions-*"))


def test_changed_assembly_cannot_be_overwritten_by_an_old_freeze(tmp_path, monkeypatch):
    root, _, assembly, _, result = setup_motion(tmp_path)
    annotations = verified_annotations(root, result)
    original = library.publish_object
    changed = []

    def write_after_snapshot(*args, **kwargs):
        folder = original(*args, **kwargs)
        if args[1] == "motions":
            index = json.loads(assembly.read_text())
            index["states"] = ["idle"]
            changed.append(workspace.json_bytes(index))
            assembly.write_bytes(changed[-1])
        return folder

    monkeypatch.setattr(library, "publish_object", write_after_snapshot)
    with pytest.raises(contract.PartsContractError, match="assembly changed"):
        parts_motion.freeze_motion(assembly, result["candidate"], annotations)
    assert assembly.read_bytes() == changed[-1]
    assert workspace.read_workspace(assembly)[0]["motion"] is None


def test_motion_revision_detaches_stale_parts_and_appearance_preserving_history(tmp_path):
    root, source, assembly, spec, result = setup_motion(tmp_path)
    first = parts_motion.freeze_motion(assembly, result["candidate"], verified_annotations(root, result))
    index, registry = workspace.read_workspace(assembly)
    motion = registry[index["motion"]["id"]]
    part = contract.seal({
        "kind": "parts-pack", "schema_version": 1, "id": "test.hair", "design_id": "hair-a",
        "bindings": [{"id": "hair-a.right", "rig": index["rig"], "motion": index["motion"], "view": "right",
                      "occupies": ["hair"], "pieces": [{"id": "front", "node": "hair_front", "slot": "hair",
                      "mode": "held", "sample_map": {"walk.right": {s["id"]: {
                      "image": {"file": "hair.png", "sha256": "a" * 64}, "pivot": [0, 0], "target_socket": "head"
                      } for s in motion["clips"][0]["samples"]}}}]}],
    })
    catalog = deepcopy(registry[index["catalog"]["id"]])
    catalog.pop("digest")
    catalog["slots"][0].update(options=[contract.reference(part)], default=contract.reference(part))
    catalog = contract.seal(catalog)
    appearance = contract.seal({"kind": "parts-appearance", "schema_version": 1, "id": "test.look",
                               "rig": index["rig"], "motion": index["motion"], "catalog": contract.reference(catalog), "states": ["walk"]})
    review = contract.seal({"kind": "parts-review", "schema_version": 1, "id": "test.review",
                           "subject_digest": contract.content_digest({"part": contract.reference(part), "motion": index["motion"]}),
                           "scope": "binding", "status": "approved", "render_policy": "parts-raster.v1", "qa_policy": "parts-qa.v1"})
    for document, filename in [(part, "old-part.json"), (catalog, "catalog.json"), (appearance, "old-look.json"), (review, "old-review.json")]:
        (root / filename).write_bytes(workspace.json_bytes(document))
        index["documents"][document["id"]] = filename
    index.update(catalog=contract.reference(catalog), appearance=contract.reference(appearance))
    assembly.write_bytes(workspace.json_bytes(index))
    workspace.read_workspace(assembly)
    old_frozen = tree_hashes((root / first["file"]).parent)
    data = json.loads((source / "motion.json").read_text())
    data["frames"][1]["duration"] = 0.25
    (source / "motion.json").write_text(json.dumps(data))
    import_spec(spec, assembly, [source_request(source)])
    next_candidate = parts_motion.import_motion(assembly, spec, source)
    updated = parts_motion.freeze_motion(assembly, next_candidate["candidate"], verified_annotations(root, next_candidate))
    index, registry = workspace.read_workspace(assembly)
    assert index["appearance"] is None and "test.hair" not in registry
    assert registry[index["catalog"]["id"]]["slots"][0]["options"] == []
    assert {entry["reference"]["id"] for entry in updated["invalidated"]} >= {"test.motion", "test.hair", "test.look", "test.review"}
    assert tree_hashes((root / first["file"]).parent) == old_frozen
    assert (root / "old-part.json").exists()
    assert (root / "old-review.json").exists()


def test_real_cli_import_and_freeze(tmp_path):
    root, source, assembly, spec, _ = setup_motion(tmp_path)
    prefix = [sys.executable, "-m", "sprite_gen.cli"]
    imported = subprocess.run([*prefix, "parts-motion-import", "--assembly", str(assembly), "--spec", str(spec), "--source-root", str(source)], capture_output=True, text=True)
    assert imported.returncode == 0, imported.stderr + imported.stdout
    result = json.loads(imported.stdout)
    path = verified_annotations(root, result)
    frozen = subprocess.run([*prefix, "parts-motion-freeze", "--assembly", str(assembly), "--candidate", result["candidate"], "--annotations", str(path)], capture_output=True, text=True)
    assert frozen.returncode == 0, frozen.stderr + frozen.stdout
    assert json.loads(frozen.stdout)["status"] == "frozen"


def test_curated_reorder_and_clone_use_final_rasters_without_deduplicating_samples(fixture_run_dir, tmp_path):
    from sprite_gen.curate.curation import stamp_curation, write_curation_atomic
    result = run_script("extract_sprite_row_frames.py", "--run-dir", str(fixture_run_dir))
    assert result.returncode == 0, result.stdout + result.stderr
    curation = stamp_curation(fixture_run_dir, {"kind": "sprite-gen-curation", "version": 1, "states": {
        "walk": {"selected": [0, 2, 3, 1], "clones": {"3": 0}}}})
    write_curation_atomic(fixture_run_dir, curation)
    result = run_script("compose_sprite_atlas.py", "--run-dir", str(fixture_run_dir))
    assert result.returncode == 0, result.stdout + result.stderr
    descriptor = json.loads((fixture_run_dir / "manifest.json").read_text())
    assert descriptor["curation_applied"] is True
    root = tmp_path / "library"
    assembly = workspace_at(root, preset="blob.fixture.v1")
    request = source_request(fixture_run_dir, "manifest.json", source_state="walk", source_mode="native-finalized")
    request["expected_files"] = {name: library.sha256((fixture_run_dir / name).read_bytes())
                                for name in ["manifest.json", descriptor["sprite_sheet_alpha"]]}
    spec = import_spec(tmp_path / "import.json", assembly, [request])
    imported = parts_motion.import_motion(assembly, spec, fixture_run_dir)
    candidate = json.loads((root / imported["candidate"]).read_text())
    samples = candidate["motion"]["clips"][0]["samples"]
    assert len(samples) == 4 and len({s["id"] for s in samples}) == 4
    assert samples[0]["image"] == samples[2]["image"]
    assert len({s["source_frame_id"] for s in samples}) == 4
    with pytest.raises(contract.PartsContractError, match="unverified"):
        parts_motion.freeze_motion(assembly, imported["candidate"], root / imported["annotation_template"])


def test_shifted_final_geometry_cannot_use_previous_raster_annotations(tmp_path):
    root, source, assembly, spec, imported = setup_motion(tmp_path)
    old = json.loads(verified_annotations(root, imported).read_text())
    with Image.open(source / "pose-0.png") as image:
        shifted = Image.new("RGBA", image.size)
        shifted.alpha_composite(image.convert("RGBA"), (6, 0))
    shifted.save(source / "pose-0.png")
    import_spec(spec, assembly, [source_request(source)])
    next_candidate = parts_motion.import_motion(assembly, spec, source)
    old["candidate_digest"] = next_candidate["digest"]  # Even if the caller rebinds the outer ID.
    path = tmp_path / "old-annotations.json"
    path.write_bytes(workspace.json_bytes(old))
    with pytest.raises(contract.PartsContractError, match="raster hash"):
        parts_motion.freeze_motion(assembly, next_candidate["candidate"], path)


def test_rehashed_candidate_cannot_claim_an_unrelated_source_geometry(tmp_path):
    root, _, assembly, _, result = setup_motion(tmp_path)
    candidate_file = root / result["candidate"]
    candidate = json.loads(candidate_file.read_text())
    encoded = library.png_bytes(Image.new("RGBA", (128, 128), "blue"))
    digest = library.sha256(encoded)
    reference = {"file": f"images/{digest}.png", "sha256": digest}
    (candidate_file.parent / reference["file"]).write_bytes(encoded)
    candidate.pop("digest")
    candidate["motion"].pop("digest")
    candidate["motion"]["clips"][0]["samples"][0]["image"] = reference
    candidate["motion"] = contract.seal(candidate["motion"])
    candidate = parts_motion.seal_aux(candidate, parts_motion._CANDIDATE)
    candidate_file.write_bytes(workspace.json_bytes(candidate))
    annotations = parts_motion.annotation_template(candidate, workspace.read_workspace(assembly)[1]["humanoid.v1"])
    for clip in annotations["clips"].values():
        for sample in clip.values():
            for socket in sample["sockets"].values():
                socket.update(position=[64, 64], visible=True, confidence="verified")
    path = tmp_path / "forged-annotations.json"
    path.write_bytes(workspace.json_bytes(annotations))
    with pytest.raises(contract.PartsContractError, match="captured source geometry"):
        parts_motion.freeze_motion(assembly, result["candidate"], path)


def test_nonobject_annotations_return_a_structured_cli_error(tmp_path, capsys):
    root, _, assembly, _, result = setup_motion(tmp_path)
    path = tmp_path / "bad.json"
    path.write_text("[]")
    assert parts_motion.run_freeze(assembly=assembly, candidate=result["candidate"], annotations=path) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_unavailable_publish_lock_is_a_structured_error(tmp_path, monkeypatch, capsys):
    from contextlib import contextmanager
    from sprite_gen.spec.runio import RWLockUnavailable
    root, source, assembly, spec, _ = setup_motion(tmp_path)

    @contextmanager
    def unavailable(*args):
        raise RWLockUnavailable("no lock backend")
        yield

    monkeypatch.setattr(parts_motion, "read_guard", unavailable)
    assert parts_motion.run_import(assembly=assembly, spec=spec, source_root=source) == 2
    assert "no lock backend" in json.loads(capsys.readouterr().out)["error"]


def _swing_source(root, offsets):
    from PIL import ImageDraw
    root.mkdir(parents=True)
    entries = []
    for n, dx in enumerate(offsets):
        image = Image.new("RGBA", (128, 128))
        draw = ImageDraw.Draw(image)
        draw.ellipse((48, 20, 80, 52), fill=(230, 180, 140, 255))
        draw.rectangle((54, 52, 74, 100), fill=(60, 90, 80, 255))
        draw.rectangle((60 + dx, 100, 68 + dx, 120), fill=(60, 90, 80, 255))
        image.save(root / f"pose-{n}.png")
        entries.append(f'{{"file":"pose-{n}.png","duration":0.125}}')
    (root / "motion.json").write_text('{"kind":"sprite-gen-asset","version":1,"frames":[' + ",".join(entries) + "]}")


@pytest.mark.parametrize("offsets,status", [
    ([0, 3, 6, 9, 12, 9, 6, 3], "passed"),
    ([0, 1, 9, 10, 0, 1, 9, 10], "warning"),
])
def test_import_reports_loop_jolt_and_strict_refuses_it(tmp_path, offsets, status):
    root, source = tmp_path / "library", tmp_path / "source"
    assembly = workspace_at(root)
    _swing_source(source, offsets)
    spec = import_spec(tmp_path / "import.json", assembly, [source_request(source, loop=True)])
    result = parts_motion.import_motion(assembly, spec, source)
    quality = result["motion_quality"][0]
    assert quality["status"] == status and quality["jolt"]["index"] >= 0
    assert bool(result["warnings"]) == (status == "warning")
    if status == "warning":
        with pytest.raises(contract.PartsContractError, match="motion quality"):
            parts_motion.import_motion(assembly, spec, source, strict_motion=True)


def test_short_or_repeated_clips_are_not_measured(tmp_path):
    root, source, assembly, spec, result = setup_motion(tmp_path)
    assert result["motion_quality"][0]["status"] == "unverified" and not result["warnings"]
