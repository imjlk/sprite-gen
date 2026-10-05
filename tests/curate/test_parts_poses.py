# SPDX-License-Identifier: Apache-2.0
"""Poses as the drawing unit: pose IDs, pose-drawn parts, recomposed clips and coverage."""
import json
from pathlib import Path

from PIL import Image
import pytest

from parts_helpers import posed_workspace
from sprite_gen.curate import parts_motion, parts_poses
from sprite_gen.frames import parts_import, parts_rebind
from sprite_gen.spec import parts_assets as assets, parts_contract as contract, parts_workspace as workspace


def motion_of(assembly):
    index, registry = workspace.read_workspace(assembly)
    return registry[index["motion"]["id"]], index, registry


def pose_part(base, assembly, *, poses=("walk.01", "walk.02"), extra=None):
    source = base / "part-source"
    source.mkdir(exist_ok=True)
    refs = {}
    for n, pose in enumerate(poses):
        Image.new("RGBA", (5, 5), (40 * n + 60, 40, 40, 255)).save(source / f"{pose}.png")
        refs[pose] = {"file": f"{pose}.png", "sha256": assets.sha256((source / f"{pose}.png").read_bytes())}
    motion, index, registry = motion_of(assembly)
    rig = registry[index["rig"]["id"]]
    piece = {"id": "piece", "slot": "hair", "node": "hair_front", "mode": "raster",
             "pose_map": {pose: {"image": ref, "format": "local", "pivot": [2, 2], "target_socket": "head"}
                          for pose, ref in refs.items()}}
    if extra:
        piece.update(extra)
    value = {"kind": "parts-pack-import", "schema_version": 1, "id": "test.part", "design_id": "test",
             "role": "part", "style": rig["style"], "rig": index["rig"], "motion": index["motion"], "view": rig["view"],
             "binding_id": "test.binding", "occupies": ["hair"], "pieces": [piece]}
    path = base / "pose-part.json"
    path.write_bytes(workspace.json_bytes(value))
    return path, source, refs


def test_assign_names_each_distinct_drawing_once(tmp_path):
    assembly, report = posed_workspace(tmp_path)
    motion, _, _ = motion_of(assembly)
    poses = [s["pose_id"] for s in motion["clips"][0]["samples"]]
    assert poses == ["walk.01", "walk.02", "walk.01"]
    assert report["poses"] == 2 and report["shared"] == {"walk.01": ["walk.right/sample.0", "walk.right/sample.2"]}


def test_assign_takes_explicit_names_and_refuses_two_names_for_one_raster(tmp_path):
    assembly, _ = posed_workspace(tmp_path, names=[("walk.right", "sample.1", "passing")])
    motion, _, _ = motion_of(assembly)
    assert [s["pose_id"] for s in motion["clips"][0]["samples"]] == ["walk.01", "passing", "walk.01"]
    draft = tmp_path / "draft.json"
    root, index, registry, _ = parts_motion._active(assembly)
    candidate = next(p for p in root.glob("annotations/*.draft.json"))
    draft.write_bytes(candidate.read_bytes())
    with pytest.raises(contract.PartsContractError, match="two pose IDs"):
        parts_poses.assign(assembly, draft, names=[("walk.right", "sample.0", "a"), ("walk.right", "sample.2", "b")])


def test_pose_map_import_repeats_a_drawing_on_every_sample_of_its_pose(tmp_path):
    assembly, _ = posed_workspace(tmp_path)
    spec, source, refs = pose_part(tmp_path, assembly)
    result = parts_import.import_pack(assembly, spec, source)
    _, _, registry = motion_of(assembly)
    piece = registry[result["part"]["id"]]["bindings"][0]["pieces"][0]
    images = [p["source"]["image"]["file"] for p in piece["sample_map"]["walk.right"].values()]
    assert images == ["walk.01.png", "walk.02.png", "walk.01.png"] and set(piece["pose_map"]) == {"walk.01", "walk.02"}


def test_explicit_sample_entry_wins_and_unknown_poses_fail(tmp_path):
    assembly, _ = posed_workspace(tmp_path)
    spec, source, refs = pose_part(tmp_path, assembly, extra={"sample_map": {"walk.right": {"sample.2": {
        "image": None, "format": "local", "pivot": [2, 2], "target_socket": "head", "offset": [0, -1]}}}})
    value = json.loads(spec.read_bytes())
    value["pieces"][0]["sample_map"]["walk.right"]["sample.2"]["image"] = refs["walk.02"]
    spec.write_bytes(workspace.json_bytes(value))
    result = parts_import.import_pack(assembly, spec, source)
    _, _, registry = motion_of(assembly)
    mapping = registry[result["part"]["id"]]["bindings"][0]["pieces"][0]["sample_map"]["walk.right"]
    assert mapping["sample.2"]["offset"] == [0, -1] and mapping["sample.0"]["offset"] == [0, 0]
    spec, source, _ = pose_part(tmp_path, assembly, poses=("walk.01", "walk.02", "jump.01"))
    with pytest.raises(contract.PartsContractError, match="no motion sample has"):
        parts_import.import_pack(assembly, spec, source)


def compose_spec(path, clips, *, identity="test.motion", keep=None):
    value = {"kind": "parts-motion-compose", "schema_version": 1, "id": identity, "clips": clips}
    if keep is not None:
        value["keep"] = keep
    path.write_bytes(workspace.json_bytes(value))
    return path


def test_compose_reuses_poses_with_offsets_and_pose_parts_follow_after_rebind(tmp_path):
    assembly, _ = posed_workspace(tmp_path)
    spec, source, _ = pose_part(tmp_path, assembly)
    imported = parts_import.import_pack(assembly, spec, source)
    root = assembly.parent
    index, _ = workspace.read_workspace(assembly)
    part_file = root / index["documents"][imported["part"]["id"]]
    hop = {"id": "hop.right", "state": "hop", "loop": False, "samples": [
        {"pose": "walk.02", "duration_ticks": 12000},
        {"pose": "walk.01", "duration_ticks": 6000, "offset": [0, -3]},
        {"pose": "walk.02", "duration_ticks": 12000}]}
    result = parts_poses.compose(assembly, compose_spec(tmp_path / "compose.json", [hop]), tmp_path / "composed")
    assert result["status"] == "frozen" and [c["clip"] for c in result["clips"]] == ["walk.right", "hop.right"]
    assert any(item["reference"]["id"] == "test.part" for item in result["invalidated"])
    motion, index, _ = motion_of(assembly)
    clip = next(c for c in motion["clips"] if c["id"] == "hop.right")
    assert [s["pose_id"] for s in clip["samples"]] == ["walk.02", "walk.01", "walk.02"]
    assert [s["duration_ticks"] for s in clip["samples"]] == [12000, 6000, 12000]
    walk = next(c for c in motion["clips"] if c["id"] == "walk.right")
    assert clip["samples"][1]["sockets"]["head"]["position"] == [64, 61]
    assert walk["samples"][0]["sockets"]["head"]["position"] == [64, 64]
    # The pose-drawn part is carried by pose onto the new clip without new art.
    index["states"] = ["walk", "hop"]
    assembly.write_bytes(workspace.json_bytes(index))
    carried = parts_rebind.rebind_spec(assembly, part_file, tmp_path / "rebind.json")
    assert carried["pieces"][0]["poses"] == ["walk.01", "walk.02"]
    again = parts_import.import_pack(assembly, tmp_path / "rebind.json", Path(carried["source_root"]))
    _, _, registry = motion_of(assembly)
    mapping = registry[again["part"]["id"]]["bindings"][0]["pieces"][0]["sample_map"]["hop.right"]
    assert len(mapping) == 3 and again["missing_coverage"] == []
    report = parts_poses.coverage(assembly, markdown=tmp_path / "coverage.md")
    row = next(r for r in report["pieces"] if r["pack"] == "test.part")
    assert row["clips"]["hop.right"] == {"covered": 3, "samples": 3, "visible": 3, "drawings": 2, "by_pose": 3,
                                         "active_state": True}
    assert "| test.part.piece |" in (tmp_path / "coverage.md").read_text()


def test_compose_refuses_unknown_poses_and_offsets_off_the_canvas(tmp_path):
    assembly, _ = posed_workspace(tmp_path)
    bad = {"id": "hop.right", "state": "hop", "samples": [{"pose": "nope", "duration_ticks": 1000}]}
    with pytest.raises(contract.PartsContractError, match="unknown pose"):
        parts_poses.compose(assembly, compose_spec(tmp_path / "a.json", [bad]), tmp_path / "a")
    far = {"id": "hop.right", "state": "hop", "samples": [{"pose": "walk.01", "duration_ticks": 1000, "offset": [0, 40]}]}
    with pytest.raises(contract.PartsContractError, match="off the rig canvas"):
        parts_poses.compose(assembly, compose_spec(tmp_path / "b.json", [far]), tmp_path / "b")
    clash = {"id": "walk.right", "state": "hop", "samples": [{"pose": "walk.01", "duration_ticks": 1000}]}
    with pytest.raises(contract.PartsContractError, match="unique"):
        parts_poses.compose(assembly, compose_spec(tmp_path / "c.json", [clash]), tmp_path / "c")


def test_rebind_refuses_a_pose_part_missing_new_poses(tmp_path):
    assembly, _ = posed_workspace(tmp_path)
    spec, source, _ = pose_part(tmp_path, assembly, poses=("walk.01", "walk.02"))
    imported = parts_import.import_pack(assembly, spec, source)
    index, _ = workspace.read_workspace(assembly)
    part_file = assembly.parent / index["documents"][imported["part"]["id"]]
    value = json.loads(part_file.read_text())
    value.pop("digest")
    piece = value["bindings"][0]["pieces"][0]
    del piece["pose_map"]["walk.02"]
    trimmed = tmp_path / "trimmed.json"
    trimmed.write_text(json.dumps(contract.seal(value)))
    with pytest.raises(contract.PartsContractError, match="has not drawn"):
        parts_rebind.rebind_spec(assembly, trimmed, tmp_path / "out.json")


def test_coverage_reports_gaps_for_requested_states(tmp_path):
    assembly, _ = posed_workspace(tmp_path)
    report = parts_poses.coverage(assembly)
    assert report["poses"] == 2 and report["unposed_samples"] == 0 and report["status"] == "complete"
