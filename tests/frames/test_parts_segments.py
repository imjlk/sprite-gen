# SPDX-License-Identifier: Apache-2.0
"""A body cut into node pieces by painted labels, and a garment split along the cut."""
import json
from pathlib import Path

from PIL import Image
import pytest

from parts_helpers import posed_workspace
from sprite_gen._deps import np
from sprite_gen.frames import parts_import, parts_segments
from sprite_gen.spec import parts_assets as assets, parts_contract as contract, parts_workspace as workspace


def paint(folder, pose, seeds):
    image = Image.new("RGBA", (128, 128))
    for (x, y), color in seeds:
        image.putpixel((x, y), (*color, 255))
    folder.mkdir(parents=True, exist_ok=True)
    image.save(folder / f"{pose}.png")


def colors(tmp_path):
    path = tmp_path / "colors.json"
    path.write_text(json.dumps({"leg_r": "#0000ff", "root": "#ff0000"}))
    return path


def test_nearest_fill_takes_the_closest_seed_deterministically():
    labels = np.full((5, 7), -1, dtype=np.int16)
    labels[2, 0], labels[2, 6] = 0, 1
    filled = parts_segments.nearest_fill(labels, np.ones((5, 7), dtype=bool))
    assert (filled[:, :3] == 0).all() and (filled[:, 4:] == 1).all() and (filled >= 0).all()


def test_segment_body_cuts_every_body_pixel_once_and_imports_by_pose(tmp_path):
    assembly, _ = posed_workspace(tmp_path)
    labels = tmp_path / "labels"
    for pose in ("walk.01", "walk.02"):
        paint(labels, pose, [((48, 36), (255, 0, 0)), ((48, 124), (0, 0, 250))])
    result = parts_segments.segment_body(assembly, tmp_path / "body", colors=colors(tmp_path), labels=labels,
                                         body_id="test.segmented")
    assert result["by_pose"] and result["cut"] == 2 and "torso" in result["unprovided_required_nodes"]
    whole = tmp_path / "whole.json"
    whole.write_text(json.dumps({"leg_r": {"color": "#0000ff", "provides": ["foot_r"]}, "root": "#ff0000"}))
    _, provides = parts_segments.read_colors(whole)
    assert provides == {"leg_r": ["leg_r", "foot_r"], "root": ["root"]}
    whole.write_text(json.dumps({"leg_r": {"color": "#0000ff", "provides": ["root"]}, "root": "#ff0000"}))
    with pytest.raises(contract.PartsContractError, match="two labels"):
        parts_segments.read_colors(whole)
    spec = json.loads(Path(result["spec"]).read_text())
    assert spec["body_mode"] == "segmented" and [p["id"] for p in spec["pieces"]] == ["leg_r", "root"]
    root = tmp_path / "body"
    for row in result["results"]:
        leg = np.asarray(Image.open(root / "leg_r" / row["clip"] / f"{row['sample']}.png"))[..., 3] > 0
        top = np.asarray(Image.open(root / "root" / row["clip"] / f"{row['sample']}.png"))[..., 3] > 0
        assert not (leg & top).any() and (leg | top).sum() == sum(row["pixels"].values())
        assert top[40].any() and leg[120].any() and not top[120].any()
    imported = parts_import.import_pack(assembly, Path(result["spec"]), root)
    _, registry = workspace.read_workspace(assembly)
    pack = registry[imported["part"]["id"]]
    assert pack["body_mode"] == "segmented"
    assert len(pack["bindings"][0]["pieces"][0]["sample_map"]["walk.right"]) == 3
    assert sorted(Path(result["labels"]).joinpath("walk.right").iterdir())[2].name == "sample.2.png"


def test_segment_body_refuses_missing_and_unknown_labels(tmp_path):
    assembly, _ = posed_workspace(tmp_path)
    labels = tmp_path / "labels"
    paint(labels, "walk.01", [((48, 36), (255, 0, 0))])
    with pytest.raises(contract.PartsContractError, match="no painted labels"):
        parts_segments.segment_body(assembly, tmp_path / "body", colors=colors(tmp_path), labels=labels, body_id="b")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"tail": "#ff0000"}))
    with pytest.raises(contract.PartsContractError, match="unknown references"):
        parts_segments.segment_body(assembly, tmp_path / "body", colors=bad, labels=labels, body_id="b")


def test_split_piece_follows_the_body_segment_under_each_pixel(tmp_path):
    assembly, _ = posed_workspace(tmp_path)
    labels = tmp_path / "labels"
    for pose in ("walk.01", "walk.02"):
        paint(labels, pose, [((48, 36), (255, 0, 0)), ((48, 124), (0, 0, 250))])
    body = parts_segments.segment_body(assembly, tmp_path / "body", colors=colors(tmp_path), labels=labels, body_id="b")
    index, registry = workspace.read_workspace(assembly)
    motion = registry[index["motion"]["id"]]
    rig = registry[index["rig"]["id"]]
    garment = tmp_path / "garment"
    garment.mkdir()
    shirt = Image.new("RGBA", (128, 128))
    shirt.paste((200, 40, 40, 255), (30, 30, 67, 110))
    shirt.save(garment / "shirt.png")
    ref = {"file": "shirt.png", "sha256": assets.sha256((garment / "shirt.png").read_bytes())}
    spec = {"kind": "parts-pack-import", "schema_version": 1, "id": "test.shirt", "design_id": "shirt",
            "role": "part", "style": rig["style"], "rig": index["rig"], "motion": index["motion"], "view": rig["view"],
            "binding_id": "shirt.binding", "occupies": ["upper"], "pieces": [{"id": "shirt", "slot": "upper",
            "node": "torso", "mode": "raster", "sample_map": {"walk.right": {s["id"]: {
                "image": ref, "format": "full-canvas", "pivot": [64, 64], "target_socket": "shoulder_l"}
                for s in motion["clips"][0]["samples"]}}}]}
    (garment / "import.json").write_bytes(workspace.json_bytes(spec))
    with pytest.raises(contract.PartsContractError, match="no piece takes"):
        parts_segments.split_piece(garment / "import.json", body["segments"], tmp_path / "x", piece="shirt",
                                   into=[("body", "torso", ["root"])])
    result = parts_segments.split_piece(garment / "import.json", body["segments"], tmp_path / "split", piece="shirt",
                                        into=[("body", "torso", ["root"]), ("sleeve", "arm_l", ["*"])])
    total = 37 * 80 * 3
    assert result["pieces"]["body"]["pixels"] + result["pieces"]["sleeve"]["pixels"] == total
    written = json.loads(Path(result["spec"]).read_text())
    assert [p["node"] for p in written["pieces"]] == ["torso", "arm_l"]
    sleeve = np.asarray(Image.open(tmp_path / "split" / "sleeve" / "walk.right" / "sample.0.png"))[..., 3] > 0
    assert sleeve[100].any() and not sleeve[40].any()
    imported = parts_import.import_pack(assembly, Path(result["spec"]), Path(result["source_root"]))
    assert imported["role"] == "part"


def test_split_by_component_keeps_one_stroke_in_one_piece(tmp_path):
    assembly, _ = posed_workspace(tmp_path)
    labels = tmp_path / "labels"
    for pose in ("walk.01", "walk.02"):
        paint(labels, pose, [((48, 36), (255, 0, 0)), ((48, 124), (0, 0, 250))])
    body = parts_segments.segment_body(assembly, tmp_path / "body", colors=colors(tmp_path), labels=labels, body_id="b")
    index, registry = workspace.read_workspace(assembly)
    motion = registry[index["motion"]["id"]]
    rig = registry[index["rig"]["id"]]
    garment = tmp_path / "garment"
    garment.mkdir()
    shoe = Image.new("RGBA", (128, 128))
    shoe.paste((200, 40, 40, 255), (40, 74, 50, 104))  # 4/5 over the leg, its top over the root piece
    shoe.save(garment / "shoe.png")
    ref = {"file": "shoe.png", "sha256": assets.sha256((garment / "shoe.png").read_bytes())}
    spec = {"kind": "parts-pack-import", "schema_version": 1, "id": "test.shoe", "design_id": "shoe",
            "role": "part", "style": rig["style"], "rig": index["rig"], "motion": index["motion"], "view": rig["view"],
            "binding_id": "shoe.binding", "occupies": ["footwear"], "pieces": [{"id": "shoe", "slot": "footwear",
            "node": "foot_l", "mode": "raster", "sample_map": {"walk.right": {s["id"]: {
                "image": ref, "format": "full-canvas", "pivot": [64, 64], "target_socket": "foot_l"}
                for s in motion["clips"][0]["samples"]}}}]}
    (garment / "import.json").write_bytes(workspace.json_bytes(spec))
    into = [("upper", "foot_r", ["root"]), ("lower", "foot_l", ["leg_r"])]
    pixel = parts_segments.split_piece(garment / "import.json", body["segments"], tmp_path / "p", piece="shoe", into=into)
    whole = parts_segments.split_piece(garment / "import.json", body["segments"], tmp_path / "c", piece="shoe", into=into,
                                       by_component=True)
    assert pixel["pieces"]["upper"]["pixels"] > 0 and pixel["pieces"]["lower"]["pixels"] > 0
    assert whole["pieces"]["upper"]["pixels"] == 0 and whole["pieces"]["lower"]["pixels"] == 10 * 30 * 3


def test_split_by_component_keeps_pixel_cut_for_strokes_without_a_clear_owner(tmp_path):
    assembly, _ = posed_workspace(tmp_path)
    labels = tmp_path / "labels"
    for pose in ("walk.01", "walk.02"):
        paint(labels, pose, [((48, 36), (255, 0, 0)), ((48, 124), (0, 0, 250))])
    body = parts_segments.segment_body(assembly, tmp_path / "body", colors=colors(tmp_path), labels=labels, body_id="b")
    index, registry = workspace.read_workspace(assembly)
    motion = registry[index["motion"]["id"]]
    rig = registry[index["rig"]["id"]]
    garment = tmp_path / "garment"
    garment.mkdir()
    pair = Image.new("RGBA", (128, 128))
    pair.paste((200, 40, 40, 255), (40, 60, 50, 100))  # one stroke over both pieces, half each
    pair.save(garment / "pair.png")
    ref = {"file": "pair.png", "sha256": assets.sha256((garment / "pair.png").read_bytes())}
    spec = {"kind": "parts-pack-import", "schema_version": 1, "id": "test.pair", "design_id": "pair",
            "role": "part", "style": rig["style"], "rig": index["rig"], "motion": index["motion"], "view": rig["view"],
            "binding_id": "pair.binding", "occupies": ["footwear"], "pieces": [{"id": "pair", "slot": "footwear",
            "node": "foot_l", "mode": "raster", "sample_map": {"walk.right": {s["id"]: {
                "image": ref, "format": "full-canvas", "pivot": [64, 64], "target_socket": "foot_l"}
                for s in motion["clips"][0]["samples"]}}}]}
    (garment / "import.json").write_bytes(workspace.json_bytes(spec))
    into = [("upper", "foot_r", ["root"]), ("lower", "foot_l", ["leg_r"])]
    whole = parts_segments.split_piece(garment / "import.json", body["segments"], tmp_path / "c", piece="pair", into=into,
                                       by_component=True)
    assert whole["pieces"]["upper"]["pixels"] > 0 and whole["pieces"]["lower"]["pixels"] > 0
