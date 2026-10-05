# SPDX-License-Identifier: Apache-2.0
"""Held parts and motion bodies carried onto a replaced frozen motion."""
import json

import pytest
from PIL import Image

from parts_helpers import frozen_workspace, part_spec
from sprite_gen.frames import parts_import, parts_rebind
from sprite_gen.spec import parts_contract as contract, parts_workspace as workspace


def held_part(tmp_path, assembly):
    source = tmp_path / "part-source"
    source.mkdir()
    Image.new("RGBA", (5, 5), (200, 40, 40, 255)).save(source / "piece.png")
    result = parts_import.import_pack(assembly, part_spec(tmp_path / "part.json", assembly, source), source)
    index, _ = workspace.read_workspace(assembly)
    return assembly.parent / index["documents"][result["part"]["id"]]


def test_body_from_motion_imports_each_sample_raster(tmp_path):
    assembly = frozen_workspace(tmp_path)
    result = parts_rebind.body_spec(assembly, tmp_path / "body.json", body_id="test.body")
    imported = parts_import.import_pack(assembly, tmp_path / "body.json", __import__("pathlib").Path(result["source_root"]))
    _, registry = workspace.read_workspace(assembly)
    body = registry[imported["part"]["id"]]
    motion = next(d for d in registry.values() if d["kind"] == "parts-motion")
    piece = body["bindings"][0]["pieces"][0]
    assert body["role"] == "body" and piece["mode"] == "raster" and result["samples"] == 3
    images = [s["image"]["sha256"] for s in motion["clips"][0]["samples"]]
    assert [p["image"]["sha256"] for p in piece["sample_map"]["walk.right"].values()] == images


def test_rebind_repeats_held_placement_and_follow_offsets(tmp_path):
    assembly = frozen_workspace(tmp_path)
    part = held_part(tmp_path, assembly)
    spec = parts_rebind.rebind_spec(assembly, part, tmp_path / "rebind.json", follow=[("piece", "head", [60, 64])])
    value = json.loads((tmp_path / "rebind.json").read_text())
    placements = list(value["pieces"][0]["sample_map"]["walk.right"].values())
    # Fixture sockets sit at (64, 64): offset follows head's move from (60, 64).
    assert spec["pieces"][0]["follows"] == "head" and all(p["offset"] == [4, 0] for p in placements)
    imported = parts_import.import_pack(assembly, tmp_path / "rebind.json", __import__("pathlib").Path(spec["source_root"]))
    assert imported["part"]["id"] == "test.part"


def test_rebind_refuses_pose_rasters_and_bad_requests(tmp_path):
    assembly = frozen_workspace(tmp_path)
    part = held_part(tmp_path, assembly)
    value = json.loads(part.read_text())
    value["bindings"][0]["pieces"][0]["mode"] = "raster"
    value.pop("digest")
    raster = tmp_path / "raster.json"
    raster.write_text(json.dumps(contract.seal(value)))
    with pytest.raises(contract.PartsContractError, match="held"):
        parts_rebind.rebind_spec(assembly, raster, tmp_path / "out.json")
    with pytest.raises(contract.PartsContractError, match="unknown pieces"):
        parts_rebind.rebind_spec(assembly, part, tmp_path / "out.json", sockets=[("nope", "head", None)])
    with pytest.raises(contract.PartsContractError, match="X,Y"):
        parts_rebind.rebind_spec(assembly, part, tmp_path / "out.json", follow=[("piece", "head", None)])


def test_lag_offsets_trail_the_previous_sample_and_clamp():
    assert parts_rebind.lag_offsets([[0, 30], [0, 28], [0, 31], [0, 30]], parts_rebind.Fraction(1, 2), 1) == \
        [[0, 0], [0, 1], [0, -1], [0, 1]]
    assert parts_rebind.lag_offsets([[0, 0], [5, 0]], parts_rebind.Fraction(1), 8, loop=False) == [[0, 0], [-5, 0]]


def test_rebind_secondary_motion_offsets_samples(tmp_path):
    assembly = frozen_workspace(tmp_path)
    part = held_part(tmp_path, assembly)
    spec = parts_rebind.rebind_spec(assembly, part, tmp_path / "rebind.json", secondary=[("piece", "head", parts_rebind.Fraction(1, 2), 1)])
    assert spec["pieces"][0]["secondary"] == {"socket": "head", "gain": "1/2", "max_px": 1}
    with pytest.raises(contract.PartsContractError, match="unknown pieces"):
        parts_rebind.rebind_spec(assembly, part, tmp_path / "x.json", secondary=[("nope", "head", parts_rebind.Fraction(1, 2), 1)])
