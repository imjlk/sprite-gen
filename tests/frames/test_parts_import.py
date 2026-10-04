# SPDX-License-Identifier: Apache-2.0
"""Actual small RGBA parts, shared coordinate restoration and immutable registration."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

from PIL import Image
import pytest

from parts_helpers import frozen_workspace, part_spec, tree_hashes
from sprite_gen.frames import parts_import
from sprite_gen.spec import parts_assets as library, parts_contract as contract, parts_workspace as workspace


def inputs(tmp_path, **kwargs):
    assembly = frozen_workspace(tmp_path)
    source = tmp_path / "parts-source"
    source.mkdir()
    image = Image.new("RGBA", (5, 4), (50, 100, 150, 128))
    image.putpixel((2, 2), (0, 0, 0, 0))
    image.save(source / "piece.png")
    spec = part_spec(tmp_path / "part-import.json", assembly, source, **kwargs)
    return assembly, source, spec


def loaded_part(assembly, result):
    root = assembly.parent
    part = contract.parse(workspace.read_json(root, result["file"]))
    return part, library.SourceReader((root / result["file"]).parent)


def restored(assembly, result):
    part, reader = loaded_part(assembly, result)
    placement = part["bindings"][0]["pieces"][0]["sample_map"]["walk.right"]["sample.0"]
    index, registry = workspace.read_workspace(assembly)
    rig = registry[index["rig"]["id"]]
    sample = registry[index["motion"]["id"]]["clips"][0]["samples"][0]
    socket = sample["sockets"][placement["target_socket"]]["position"]
    offset = tuple(t - p + d for t, p, d in zip(socket, placement["pivot"], placement["offset"]))
    output = Image.new("RGBA", (rig["canvas"]["width"], rig["canvas"]["height"]))
    output.alpha_composite(library.load_image(reader, placement["image"]), offset)
    return output


def test_tiny_part_preserves_size_alpha_holes_and_source_bytes(tmp_path):
    assembly, source, spec = inputs(tmp_path)
    original = tree_hashes(source)
    result = parts_import.import_pack(assembly, spec, source)
    part, reader = loaded_part(assembly, result)
    placement = part["bindings"][0]["pieces"][0]["sample_map"]["walk.right"]["sample.0"]
    image = library.load_image(reader, placement["image"])
    assert image.size == (5, 4)
    assert image.getpixel((0, 0)) == (50, 100, 150, 128)
    assert image.getpixel((2, 2))[3] == 0
    assert tree_hashes(source) == original
    assert placement["pivot"] == [2, 2]
    assert result["status"] == "imported" and result["release_ready"] is False
    index, registry = workspace.read_workspace(assembly)
    assert result["part"] in registry[index["catalog"]["id"]]["slots"][0]["options"]


def test_full_canvas_and_cropped_offsets_restore_identical_pixels(tmp_path):
    outputs = []
    for kind in ["full-canvas", "cropped"]:
        base = tmp_path / kind
        assembly = frozen_workspace(base)
        source = base / "parts-source"
        source.mkdir()
        image = Image.new("RGBA", (128, 128))
        patch = Image.new("RGBA", (5, 4), (10, 20, 30, 127))
        patch.putpixel((2, 2), (0, 0, 0, 0))
        image.paste(patch, (60, 61))
        image = image if kind == "full-canvas" else image.crop((60, 61, 65, 65))
        image.save(source / "piece.png")
        spec = part_spec(base / "part.json", assembly, source, format=kind, pivot=(64, 64),
                         crop_offset=(60, 61) if kind == "cropped" else (0, 0))
        result = parts_import.import_pack(assembly, spec, source)
        outputs.append(restored(assembly, result).tobytes())
    assert outputs[0] == outputs[1]


@pytest.mark.parametrize("preset,slot,node,socket", [
    ("human.sd.v1", "upper", "arm_l", "shoulder_l"),
    ("human.tall.v1", "sleeve_l", "arm_l", "shoulder_l"),
    ("canine.medium.v1", "torso_gear", "torso", "back"),
    ("blob.fixture.v1", "ornament", "root", "top"),
    ("asymmetric.six-limb.v1", "appendage_0", "limb_0", "tip_0"),
])
def test_same_importer_accepts_body_specific_parts(tmp_path, preset, slot, node, socket):
    assembly = frozen_workspace(tmp_path, preset=preset)
    source = tmp_path / "parts-source"
    source.mkdir()
    Image.new("RGBA", (5, 4), "red").save(source / "piece.png")
    spec = part_spec(tmp_path / "part.json", assembly, source, slot=slot, node=node, socket=socket)
    result = parts_import.import_pack(assembly, spec, source)
    assert result["status"] == "imported"


def test_body_is_explicit_and_flat_body_cannot_claim_separated_limbs(tmp_path):
    assembly, source, spec = inputs(tmp_path, role="body", slot=None, node="root", socket="root", provides=["root"], body_mode="flat")
    result = parts_import.import_pack(assembly, spec, source)
    index, registry = workspace.read_workspace(assembly)
    assert index["body"] == [result["part"]]
    assert registry[result["part"]["id"]]["body_mode"] == "flat"
    data = json.loads(spec.read_text())
    data["pieces"][0]["provides"].append("foot_l")
    spec.write_bytes(workspace.json_bytes(data))
    with pytest.raises(contract.PartsContractError, match="flat body"):
        parts_import.import_pack(assembly, spec, source)


@pytest.mark.parametrize("field,value,match", [
    ("style", "photo.v1", "style/view"), ("view", "left", "style/view"),
    ("rig", {"id": "other.rig", "digest": "a" * 64}, "rig/motion"),
    ("motion", {"id": "other.motion", "digest": "a" * 64}, "rig/motion"),
])
def test_wrong_style_rig_or_view_is_not_retargeted(tmp_path, field, value, match):
    assembly, source, spec = inputs(tmp_path)
    data = json.loads(spec.read_text())
    data[field] = value
    spec.write_bytes(workspace.json_bytes(data))
    with pytest.raises(contract.PartsContractError, match=match):
        parts_import.import_pack(assembly, spec, source)


def test_missing_sample_is_an_explicit_draft_only_when_requested(tmp_path):
    assembly, source, spec = inputs(tmp_path)
    data = json.loads(spec.read_text())
    data["pieces"][0]["sample_map"]["walk.right"].pop("sample.1")
    spec.write_bytes(workspace.json_bytes(data))
    before = assembly.read_bytes()
    with pytest.raises(contract.PartsContractError, match="missing part coverage"):
        parts_import.import_pack(assembly, spec, source)
    assert assembly.read_bytes() == before
    result = parts_import.import_pack(assembly, spec, source, allow_incomplete=True)
    assert result["status"] == "partial" and any("sample.1" in p for p in result["missing_coverage"])


def test_mask_dimensions_are_checked_without_changing_part_geometry(tmp_path):
    assembly, source, spec = inputs(tmp_path)
    Image.new("L", (5, 4), 127).save(source / "mask.png")
    data = json.loads(spec.read_text())
    mask = {"image": {"file": "mask.png", "sha256": library.sha256((source / "mask.png").read_bytes())},
            "target_node": "hair_front", "space": "part-local"}
    for placement in data["pieces"][0]["sample_map"]["walk.right"].values():
        placement["mask"] = mask
    spec.write_bytes(workspace.json_bytes(data))
    result = parts_import.import_pack(assembly, spec, source)
    part, reader = loaded_part(assembly, result)
    placement = part["bindings"][0]["pieces"][0]["sample_map"]["walk.right"]["sample.0"]
    assert library.load_image(reader, placement["mask"]["image"]).size == (5, 4)
    Image.new("L", (1, 1), 127).save(source / "mask.png")
    for placement in data["pieces"][0]["sample_map"]["walk.right"].values():
        placement["mask"]["image"]["sha256"] = library.sha256((source / "mask.png").read_bytes())
    spec.write_bytes(workspace.json_bytes(data))
    with pytest.raises(contract.PartsContractError, match="canvas differs"):
        parts_import.import_pack(assembly, spec, source)


def test_source_path_symlink_and_clipping_fail_before_activation(tmp_path):
    assembly, source, spec = inputs(tmp_path)
    data = json.loads(spec.read_text())
    original = assembly.read_bytes()
    (source / "link.png").symlink_to(source / "piece.png")
    for placement in data["pieces"][0]["sample_map"]["walk.right"].values():
        placement["image"]["file"] = "link.png"
    spec.write_bytes(workspace.json_bytes(data))
    with pytest.raises(contract.PartsContractError, match="symlink"):
        parts_import.import_pack(assembly, spec, source)
    for placement in data["pieces"][0]["sample_map"]["walk.right"].values():
        placement["image"]["file"] = "piece.png"
        placement["offset"] = [1000, 0]
    spec.write_bytes(workspace.json_bytes(data))
    with pytest.raises(contract.PartsContractError, match="clips rig"):
        parts_import.import_pack(assembly, spec, source)
    assert assembly.read_bytes() == original


def test_revision_changes_catalog_hash_but_preserves_previous_snapshot(tmp_path):
    assembly, source, spec = inputs(tmp_path)
    first = parts_import.import_pack(assembly, spec, source)
    folder = (assembly.parent / first["file"]).parent
    original = tree_hashes(folder)
    Image.new("RGBA", (5, 4), "blue").save(source / "piece.png")
    part_spec(spec, assembly, source)
    second = parts_import.import_pack(assembly, spec, source)
    assert first["part"] != second["part"] and first["catalog"] != second["catalog"]
    assert tree_hashes(folder) == original
    assert parts_import.import_pack(assembly, spec, source)["part"] == second["part"]


def test_actual_cli_import(tmp_path):
    assembly, source, spec = inputs(tmp_path)
    result = subprocess.run([sys.executable, "-m", "sprite_gen.cli", "parts-import", "--assembly", str(assembly),
                             "--spec", str(spec), "--source-root", str(source)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr + result.stdout
    assert json.loads(result.stdout)["status"] == "imported"


def test_multiple_body_pieces_are_registered_without_customization_slots(tmp_path):
    assembly, source, spec = inputs(tmp_path, role="body", slot=None, node="root", socket="root", provides=["root"])
    value = json.loads(spec.read_text())
    second = deepcopy(value["pieces"][0])
    second.update(id="head-piece", node="head", provides=["head"])
    for placement in second["sample_map"]["walk.right"].values():
        placement["target_socket"] = "head"
    value["pieces"].append(second)
    spec.write_bytes(workspace.json_bytes(value))
    result = parts_import.import_pack(assembly, spec, source)
    part, _ = loaded_part(assembly, result)
    assert len(part["bindings"][0]["pieces"]) == 2
    assert part["bindings"][0]["occupies"] == []
    assert all(piece["slot"] is None for piece in part["bindings"][0]["pieces"])


def test_part_disk_failure_preserves_active_index_and_exposes_no_partial_object(tmp_path, monkeypatch):
    assembly, source, spec = inputs(tmp_path)
    before = assembly.read_bytes()
    write_bytes = Path.write_bytes

    def fail(path, data):
        if path.name == "part.json":
            raise OSError("injected disk failure")
        return write_bytes(path, data)

    monkeypatch.setattr(Path, "write_bytes", fail)
    with pytest.raises(OSError, match="disk failure"):
        parts_import.import_pack(assembly, spec, source)
    assert assembly.read_bytes() == before
    assert not (assembly.parent / "parts").exists()
    assert not list(assembly.parent.glob(".parts-*"))


def test_concurrent_index_change_cannot_be_overwritten_by_part_activation(tmp_path, monkeypatch):
    assembly, source, spec = inputs(tmp_path)
    original = library.publish_object
    changed = []

    def publish_then_edit(*args, **kwargs):
        folder = original(*args, **kwargs)
        if args[1] == "parts":
            value = json.loads(assembly.read_text())
            value["states"] = ["walk", "run"]
            changed.append(workspace.json_bytes(value))
            assembly.write_bytes(changed[-1])
        return folder

    monkeypatch.setattr(library, "publish_object", publish_then_edit)
    with pytest.raises(contract.PartsContractError, match="assembly changed"):
        parts_import.import_pack(assembly, spec, source)
    assert assembly.read_bytes() == changed[-1]
    assert "test.part" not in workspace.read_workspace(assembly)[1]


def test_palette_region_can_vary_per_sample_and_has_exact_part_dimensions(tmp_path):
    assembly, source, spec = inputs(tmp_path)
    Image.new("L", (5, 4), 255).save(source / "region.png")
    value = json.loads(spec.read_text())
    region = {"file": "region.png", "sha256": library.sha256((source / "region.png").read_bytes())}
    for placement in value["pieces"][0]["sample_map"]["walk.right"].values():
        placement["palette_regions"] = {"hair": region}
    spec.write_bytes(workspace.json_bytes(value))
    result = parts_import.import_pack(assembly, spec, source)
    part, reader = loaded_part(assembly, result)
    placement = part["bindings"][0]["pieces"][0]["sample_map"]["walk.right"]["sample.0"]
    assert library.load_image(reader, placement["palette_regions"]["hair"]).size == (5, 4)
