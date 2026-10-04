# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from compose.test_parts_pipeline import populated_workspace
from curate.test_parts_review import ready
from parts_helpers import composition_fixture, tree_hashes
from sprite_gen.compose import compose_parts, parts_build, parts_resolve
from sprite_gen.curate import parts_appearance, parts_review
from sprite_gen.qa import inspect_parts
from sprite_gen.spec import parts_assets as assets, parts_contract as contract, parts_workspace as workspace


def approved(tmp_path, *, states=("walk",)):
    assembly, selection, checks = ready(tmp_path, states=states)
    review = parts_review.prepare(assembly)
    parts_review.decide(assembly, review["review_hash"], checks, "fixture-test", note="Synthetic engine fixture approval")
    return assembly, selection


def test_multi_state_bake_dedup_preserves_repeat_order_ticks_sockets_and_aseprite_mapping():
    docs, images = composition_fixture()
    plan = parts_resolve.resolve(docs[-1], docs)
    frames, clips = compose_parts.render_plan(plan, lambda pack, ref: images[(pack["id"], ref["file"])].copy())
    payloads, manifest = parts_build.bake(plan, {"clips": clips}, frames)
    assert manifest["unique_cells"] == 2 and len(manifest["clips"]) == 3
    for clip in manifest["clips"]:
        assert [s["cell"] for s in clip["samples"]] == [0, 1, 0]
        assert [s["duration_ticks"] for s in clip["samples"]] == [250, 125, 250]
        assert len(manifest["frame_layout"]["rows"][clip["state"]]) == 3
        assert all("hand_r" not in s["sockets"] for s in clip["samples"])  # Sockets retain authored IDs.
    aseprite = json.loads(payloads["aseprite.json"])
    assert [f["duration"] for f in aseprite["frames"]] == [250, 125, 250] * 3
    assert [t["name"] for t in aseprite["meta"]["frameTags"]] == ["idle", "walk", "run"]
    assert aseprite["frames"][0]["frame"] == aseprite["frames"][2]["frame"]


def test_release_is_deterministic_portable_and_source_read_only(tmp_path):
    assembly, _ = approved(tmp_path / "a")
    source_before = tree_hashes(assembly.parent.parent / "body-source")
    first = parts_build.build(assembly, tmp_path / "output-a", mode="release")
    repeated = parts_build.build(assembly, tmp_path / "output-a", mode="release")
    assert repeated == first
    relocated = tmp_path / "relocated"
    shutil.copytree(assembly.parent, relocated)
    second = parts_build.build(relocated / "assembly.json", tmp_path / "output-b", mode="release")
    assert first["build"] == second["build"]
    assert tree_hashes(Path(first["directory"])) == tree_hashes(Path(second["directory"]))
    assert tree_hashes(assembly.parent.parent / "body-source") == source_before
    lock = contract.parse(workspace.read_json(Path(first["directory"]), "build-lock.json"))
    for ref in lock["outputs"]:
        assert assets.sha256((Path(first["directory"]) / ref["file"]).read_bytes()) == ref["sha256"]
    assert lock["reviews"] and first["release_ready"] is True


def test_preview_never_promotes_release_and_part_review_is_insufficient(tmp_path):
    assembly, _, checks = ready(tmp_path)
    preview = parts_build.build(assembly, tmp_path / "output")
    assert preview["release_ready"] is False
    assert not (tmp_path / "output/release.json").exists()
    with pytest.raises(contract.PartsContractError, match="approved appearance"):
        parts_build.build(assembly, tmp_path / "output", mode="release")
    index, registry = workspace.read_workspace(assembly)
    result = parts_review.prepare(assembly, scope="part", subject=index["body"][0]["id"])
    parts_review.decide(assembly, result["review_hash"], checks, "fixture-test")
    with pytest.raises(contract.PartsContractError, match="approved appearance"):
        parts_build.build(assembly, tmp_path / "output", mode="release")


@pytest.mark.parametrize("change", ["palette", "states", "qa-policy", "asset"])
def test_old_approval_or_missing_states_never_replace_current_release(tmp_path, monkeypatch, change):
    assembly, selection = approved(tmp_path)
    output = tmp_path / "output"
    parts_build.build(assembly, output, mode="release")
    before = (output / "release.json").read_bytes()
    if change == "palette":
        data = json.loads(selection.read_bytes())
        data["palette"] = {"skin": [10, 20, 30]}
        selection.write_bytes(workspace.json_bytes(data))
        parts_appearance.create(assembly, selection)
    elif change == "states":
        data = json.loads(assembly.read_bytes())
        data["states"].append("run")
        assembly.write_bytes(workspace.json_bytes(data))
    elif change == "qa-policy":
        monkeypatch.setattr(inspect_parts, "QA_POLICY", "parts.evidence.v2")
    else:
        index, registry = workspace.read_workspace(assembly)
        body = registry[index["body"][0]["id"]]
        placement = body["bindings"][0]["pieces"][0]["sample_map"]["walk.right"]["sample.0"]
        target = (assembly.parent / index["documents"][body["id"]]).parent / placement["image"]["file"]
        target.write_bytes(b"corrupt")
    with pytest.raises(contract.PartsContractError):
        parts_build.build(assembly, output, mode="release")
    assert (output / "release.json").read_bytes() == before


def test_failed_snapshot_write_preserves_previous_release(tmp_path, monkeypatch):
    assembly, _ = approved(tmp_path)
    output = tmp_path / "output"
    parts_build.build(assembly, output, mode="release")
    before = (output / "release.json").read_bytes()
    original = Path.write_bytes
    def failure(path, data):
        if path.name == "atlas.png" and any(p.startswith(".snapshots-") for p in path.parts):
            raise OSError("injected publish failure")
        return original(path, data)
    monkeypatch.setattr(Path, "write_bytes", failure)
    with pytest.raises(OSError, match="injected"):
        parts_build.build(assembly, output, mode="release")
    assert (output / "release.json").read_bytes() == before
    assert not list(output.glob(".snapshots-*"))


def test_concurrent_assembly_change_leaves_current_release_pointer_untouched(tmp_path, monkeypatch):
    assembly, _ = approved(tmp_path)
    output = tmp_path / "output"
    parts_build.build(assembly, output, mode="release")
    before = (output / "release.json").read_bytes()
    original = assets.publish_object
    def concurrent(root, collection, identity, digest, payloads):
        folder = original(root, collection, identity, digest, payloads)
        if collection == "snapshots":
            data = json.loads(assembly.read_bytes())
            data["id"] = "concurrent-revision"
            assembly.write_bytes(workspace.json_bytes(data))
        return folder
    monkeypatch.setattr(assets, "publish_object", concurrent)
    with pytest.raises(contract.PartsContractError, match="assembly changed"):
        parts_build.build(assembly, output, mode="release")
    assert (output / "release.json").read_bytes() == before


def test_exact_ticks_remain_authoritative_when_aseprite_milliseconds_round():
    clip = {"timebase": 3, "samples": [{"duration_ticks": 1}] * 3}
    durations, errors = parts_build._millisecond_timing(clip)
    assert durations == [333, 334, 333] and sum(durations) == 1000
    assert errors == ["-1/3", "2/3", "-1/3"]
    with pytest.raises(contract.PartsContractError, match="millisecond"):
        parts_build._millisecond_timing({"timebase": 10000, "samples": [{"duration_ticks": 1}]})


def test_build_output_cannot_replace_source_objects_and_cli_release_runs(tmp_path):
    assembly, _ = approved(tmp_path, states=("idle", "walk", "run"))
    with pytest.raises(contract.PartsContractError, match="away from source"):
        parts_build.build(assembly, assembly.parent / "parts", mode="release")
    result = subprocess.run([sys.executable, "-m", "sprite_gen.cli", "parts-build", "--assembly", str(assembly),
       "--out", str(tmp_path / "cli-output"), "--mode", "release"], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["release_ready"] is True and payload["clips"] == 3 and payload["samples"] == 9
    manifest = json.loads(Path(payload["manifest"]).read_bytes())
    assert list(manifest["frame_layout"]["rows"]) == ["idle", "run", "walk"]  # Canonical JSON key order.
    assert [clip["state"] for clip in manifest["clips"]] == ["idle", "walk", "run"]
    assert all([s["duration_ticks"] for s in c["samples"]] == [30000, 15000, 30000] for c in manifest["clips"])
