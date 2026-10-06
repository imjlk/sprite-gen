# SPDX-License-Identifier: Apache-2.0
"""Pose guide sheets round-trip drawn parts onto exact per-sample native rasters."""
import json
import subprocess
import sys

import numpy as np
import pytest
from PIL import Image, ImageDraw

from parts_helpers import frozen_workspace
from sprite_gen.frames import parts_import, parts_pose_sheet as sheets
from sprite_gen.spec import parts_contract as contract, parts_workspace as workspace


def drawn_sheet(guide, path, *, size=None, body=None):
    """Draw a 6x4 native-pixel badge per cell at the guide scale, optionally over a body."""
    image = body.copy() if body is not None else Image.new("RGBA", tuple(guide["size"]), (*guide["key"], 255))
    draw = ImageDraw.Draw(image)
    s = guide["scale"]
    expected = {}
    for n, cell in enumerate(guide["cells"]):
        x, y = cell["origin"]
        nx, ny = 4 + n, 10  # native position inside the crop
        draw.rectangle((x+nx*s, y+ny*s, x+(nx+6)*s-1, y+(ny+4)*s-1), fill=(200, 40, 40, 255))
        expected[(cell["clip"], cell["sample"])] = (guide["crop"][0]+nx, guide["crop"][1]+ny)
    if size is not None:
        image = image.resize(size, Image.Resampling.LANCZOS)
    image.save(path)
    return expected


def test_sheet_slice_and_import_place_each_raster_on_its_sample(tmp_path):
    assembly = frozen_workspace(tmp_path)
    result = sheets.build_sheet(assembly, tmp_path / "sheet", scale=3, crop="32,32,96,96", part="badge")
    guide = json.loads((tmp_path / "sheet/pose-sheet.json").read_text())
    assert result["cells"] == 3 and guide["size"] == result["size"] and "badge" in result["prompt_hint"]
    assert Image.open(tmp_path / "sheet/pose-sheet.png").size == tuple(guide["size"])
    expected = drawn_sheet(guide, tmp_path / "drawn.png", size=(guide["size"][0]*3//2, guide["size"][1]*3//2))
    sliced = sheets.slice_sheet(assembly, tmp_path / "sheet/pose-sheet.json", tmp_path / "drawn.png", tmp_path / "out",
                                part_id="test.badge", slot="hair", node="hair_front", socket="head")
    assert sliced["samples"] == 3 and not sliced["empty"] and not sliced["missing_requested_samples"]
    for row in sliced["results"]:
        image = Image.open(tmp_path / "out" / row["file"])
        x, y = expected[(row["clip"], row["sample"])]
        assert image.size == (128, 128) and image.getbbox() == (x, y, x+6, y+4)
        # LANCZOS resampling rings slightly; colors stay near the drawn ink.
        assert max(abs(a-b) for a, b in zip(image.getpixel((x, y)), (200, 40, 40, 255))) <= 24
    imported = parts_import.import_pack(assembly, tmp_path / "out/import.json", tmp_path / "out")
    _, registry = workspace.read_workspace(assembly)
    placement = registry[imported["part"]["id"]]["bindings"][0]["pieces"][0]["sample_map"]["walk.right"]["sample.0"]
    assert placement["target_socket"] == "head" and placement["visible"]


def test_dressed_sheet_subtracts_the_unchanged_body(tmp_path):
    assembly = frozen_workspace(tmp_path)
    sheets.build_sheet(assembly, tmp_path / "sheet", scale=2)
    guide = json.loads((tmp_path / "sheet/pose-sheet.json").read_text())
    body = Image.open(tmp_path / "sheet/pose-sheet.png").convert("RGBA")
    expected = drawn_sheet(guide, tmp_path / "dressed.png", body=body)
    plain = sheets.slice_sheet(assembly, tmp_path / "sheet/pose-sheet.json", tmp_path / "dressed.png", tmp_path / "plain",
                               part_id="test.badge", slot="hair", node="hair_front", socket="head")
    dressed = sheets.slice_sheet(assembly, tmp_path / "sheet/pose-sheet.json", tmp_path / "dressed.png", tmp_path / "out",
                                 part_id="test.badge", slot="upper", node="torso", socket="head", body_threshold=8)
    row = dressed["results"][0]
    x, y = expected[(row["clip"], row["sample"])]
    assert Image.open(tmp_path / "out" / row["file"]).getbbox() == (x, y, x+6, y+4)
    assert plain["results"][0]["pixels"] > dressed["results"][0]["pixels"]


def test_empty_cells_become_hidden_placements(tmp_path):
    assembly = frozen_workspace(tmp_path)
    sheets.build_sheet(assembly, tmp_path / "sheet", scale=2)
    guide = json.loads((tmp_path / "sheet/pose-sheet.json").read_text())
    Image.new("RGBA", tuple(guide["size"]), (*guide["key"], 255)).save(tmp_path / "blank.png")
    sliced = sheets.slice_sheet(assembly, tmp_path / "sheet/pose-sheet.json", tmp_path / "blank.png", tmp_path / "out",
                                part_id="test.badge", slot="hair", node="hair_front", socket="head")
    spec = json.loads((tmp_path / "out/import.json").read_text())
    assert len(sliced["empty"]) == 3
    assert not any(p["visible"] for samples in spec["pieces"][0]["sample_map"].values() for p in samples.values())


@pytest.mark.parametrize("kwargs,match", [
    ({"crop": "0,0,200,10"}, "crop"),
    ({"scale": 0}, "scale"),
    ({"clips": ["run.right"]}, "missing"),
    ({"key": "#fff"}, "key"),
])
def test_invalid_sheet_requests_are_refused(tmp_path, kwargs, match):
    assembly = frozen_workspace(tmp_path)
    with pytest.raises(contract.PartsContractError, match=match):
        sheets.build_sheet(assembly, tmp_path / "sheet", **kwargs)


def test_stale_guide_and_wrong_aspect_are_refused(tmp_path):
    assembly = frozen_workspace(tmp_path)
    sheets.build_sheet(assembly, tmp_path / "sheet", scale=2)
    guide_path = tmp_path / "sheet/pose-sheet.json"
    guide = json.loads(guide_path.read_text())
    Image.new("RGBA", (guide["size"][0], guide["size"][1]*2)).save(tmp_path / "tall.png")
    with pytest.raises(contract.PartsContractError, match="aspect"):
        sheets.slice_sheet(assembly, guide_path, tmp_path / "tall.png", tmp_path / "out",
                           part_id="test.badge", slot="hair", node="hair_front", socket="head")
    Image.new("RGBA", tuple(guide["size"])).save(tmp_path / "drawn.png")
    with pytest.raises(contract.PartsContractError, match="socket"):
        sheets.slice_sheet(assembly, guide_path, tmp_path / "drawn.png", tmp_path / "out",
                           part_id="test.badge", slot="upper", node="torso", socket="tail")
    guide["cells"][0]["body_sha256"] = "0" * 64
    guide_path.write_text(json.dumps(guide))
    with pytest.raises(contract.PartsContractError, match="stale"):
        sheets.slice_sheet(assembly, guide_path, tmp_path / "drawn.png", tmp_path / "out",
                           part_id="test.badge", slot="hair", node="hair_front", socket="head")


def test_cli_sheet_and_slice(tmp_path):
    assembly = frozen_workspace(tmp_path)
    prefix = [sys.executable, "-m", "sprite_gen.cli"]
    made = subprocess.run([*prefix, "parts-pose-sheet", "--assembly", str(assembly), "--out", str(tmp_path / "sheet"),
                           "--scale", "2"], capture_output=True, text=True)
    assert made.returncode == 0 and json.loads(made.stdout)["cells"] == 3
    guide = json.loads((tmp_path / "sheet/pose-sheet.json").read_text())
    drawn_sheet(guide, tmp_path / "drawn.png")
    cut = subprocess.run([*prefix, "parts-pose-slice", "--assembly", str(assembly), "--guide", str(tmp_path / "sheet/pose-sheet.json"),
                          "--art", str(tmp_path / "drawn.png"), "--out", str(tmp_path / "out"), "--part-id", "test.badge",
                          "--slot", "upper", "--node", "torso", "--socket", "head"], capture_output=True, text=True)
    assert cut.returncode == 0 and json.loads(cut.stdout)["samples"] == 3


def test_pages_and_aspect_split_a_sheet_and_slice_back_together(tmp_path):
    assembly = frozen_workspace(tmp_path)
    made = sheets.build_sheet(assembly, tmp_path / "sheet", scale=2, crop="32,32,96,96", page_size=2, aspect="3:2")
    assert [p["cells"] for p in made["pages"]] == [2, 1]
    pages = []
    for n, page in enumerate(made["pages"]):
        guide = json.loads(open(page["guide"]).read())
        assert guide["size"][0]*2 == guide["size"][1]*3 or abs(guide["size"][0]*2 - guide["size"][1]*3) <= 3
        drawn_sheet(guide, tmp_path / f"drawn-{n}.png")
        pages.append((page["guide"], tmp_path / f"drawn-{n}.png"))
    sliced = sheets.slice_sheet(assembly, None, None, tmp_path / "out", pages=pages,
                                part_id="test.badge", slot="hair", node="hair_front", socket="head")
    assert sliced["samples"] == 3 and not sliced["missing_requested_samples"]


def test_classes_reuse_representative_drawings(tmp_path):
    assembly = frozen_workspace(tmp_path)
    classes = sheets.pose_classes(assembly, tmp_path / "classes.json", socket="head", bounds="-8,-8,8,8", classes=1)
    assert classes["sizes"] == [3]
    made = sheets.build_sheet(assembly, tmp_path / "sheet", scale=2, classes=tmp_path / "classes.json")
    assert made["cells"] == 1
    guide = json.loads((tmp_path / "sheet/pose-sheet.json").read_text())
    drawn_sheet(guide, tmp_path / "drawn.png")
    sliced = sheets.slice_sheet(assembly, tmp_path / "sheet/pose-sheet.json", tmp_path / "drawn.png", tmp_path / "out",
                                part_id="test.badge", slot="hair", node="hair_front", socket="head",
                                classes=tmp_path / "classes.json")
    spec = json.loads((tmp_path / "out/import.json").read_text())
    images = {p["image"]["sha256"] for p in spec["pieces"][0]["sample_map"]["walk.right"].values()}
    assert sliced["samples"] == 3 and sliced["drawn"] == 1 and len(images) == 1
    with pytest.raises(contract.PartsContractError, match="different socket"):
        sheets.slice_sheet(assembly, tmp_path / "sheet/pose-sheet.json", tmp_path / "drawn.png", tmp_path / "x",
                           part_id="t", slot="hair", node="hair_front", socket="root", classes=tmp_path / "classes.json")


def test_cleanup_filters_keep_material_and_drop_debris():
    from sprite_gen._deps import np
    part = Image.new("RGBA", (20, 20))
    draw = ImageDraw.Draw(part)
    draw.rectangle((4, 4, 12, 12), fill=(20, 140, 140, 255))      # garment material
    draw.line((15, 2, 18, 2), fill=(10, 10, 10, 255))             # stray outline fragment
    draw.point((1, 18), fill=(20, 140, 140, 255))                 # speck
    cleaned = sheets.drop_islands(part, 4)
    assert cleaned.getbbox() == (4, 4, 13, 13)
    dressed = part.copy()
    ImageDraw.Draw(dressed).line((3, 3, 3, 13), fill=(10, 10, 10, 255))
    outlined = sheets.keep_outline(cleaned, dressed)
    assert outlined.getpixel((3, 8))[3] == 255 and outlined.getpixel((15, 2))[3] == 0
    body = Image.new("RGBA", (20, 20))
    ImageDraw.Draw(body).rectangle((5, 5, 11, 11), fill=(240, 190, 130, 255))
    near = sheets.within_body(part, body, 1)
    assert np.asarray(near)[2, 15, 3] == 0 and np.asarray(near)[8, 8, 3] == 255


def test_region_drops_pixels_outside_the_authored_zone(tmp_path):
    assembly = frozen_workspace(tmp_path)
    sheets.build_sheet(assembly, tmp_path / "sheet", scale=2)
    guide = json.loads((tmp_path / "sheet/pose-sheet.json").read_text())
    expected = drawn_sheet(guide, tmp_path / "drawn.png")
    sliced = sheets.slice_sheet(assembly, tmp_path / "sheet/pose-sheet.json", tmp_path / "drawn.png", tmp_path / "out",
                                part_id="test.badge", slot="hair", node="hair_front", socket="head", region="0,0,128,10")
    assert all(r["pixels"] == 0 for r in sliced["results"])


def test_by_pose_draws_each_pose_once_and_slices_a_pose_map(tmp_path):
    from parts_helpers import posed_workspace
    assembly, _ = posed_workspace(tmp_path)
    result = sheets.build_sheet(assembly, tmp_path / "sheet", scale=2, crop="32,32,96,96", by_pose=True)
    guide = json.loads((tmp_path / "sheet/pose-sheet.json").read_text())
    assert result["cells"] == 2 and [c["sample"] for c in guide["cells"]] == ["sample.0", "sample.1"]
    drawn_sheet(guide, tmp_path / "drawn.png")
    sliced = sheets.slice_sheet(assembly, tmp_path / "sheet/pose-sheet.json", tmp_path / "drawn.png", tmp_path / "out",
                                part_id="test.badge", slot="hair", node="hair_front", socket="head", by_pose=True)
    assert sliced["poses"] == 2 and sliced["missing_requested_poses"] == []
    spec = json.loads((tmp_path / "out/import.json").read_text())
    assert set(spec["pieces"][0]["pose_map"]) == {"walk.01", "walk.02"} and "sample_map" not in spec["pieces"][0]
    imported = parts_import.import_pack(assembly, tmp_path / "out/import.json", tmp_path / "out")
    _, registry = workspace.read_workspace(assembly)
    mapping = registry[imported["part"]["id"]]["bindings"][0]["pieces"][0]["sample_map"]["walk.right"]
    assert mapping["sample.0"]["image"] == mapping["sample.2"]["image"] != mapping["sample.1"]["image"]


def test_by_pose_needs_pose_ids(tmp_path):
    assembly = frozen_workspace(tmp_path)
    with pytest.raises(contract.PartsContractError, match="pose_id"):
        sheets.build_sheet(assembly, tmp_path / "sheet", by_pose=True)


def test_a_short_last_page_keeps_the_first_page_grid(tmp_path):
    from parts_helpers import posed_workspace
    assembly, _ = posed_workspace(tmp_path)
    result = sheets.build_sheet(assembly, tmp_path / "sheet", scale=2, page_size=2)
    first, last = result["pages"]
    assert first["cells"] == 2 and last["cells"] == 1 and first["size"] == last["size"]


def test_dressed_slice_reports_cells_that_do_not_sit_on_the_body(tmp_path):
    assembly = frozen_workspace(tmp_path)
    sheets.build_sheet(assembly, tmp_path / "sheet", scale=2)
    guide = json.loads((tmp_path / "sheet/pose-sheet.json").read_text())
    body = Image.open(tmp_path / "sheet/pose-sheet.png").convert("RGBA")
    drawn_sheet(guide, tmp_path / "dressed.png", body=body)
    kept = sheets.slice_sheet(assembly, tmp_path / "sheet/pose-sheet.json", tmp_path / "dressed.png", tmp_path / "a",
                              part_id="test.badge", slot="hair", node="hair_front", socket="head", body_threshold=8)
    assert kept["misaligned"] == [] and all(r["alignment"] > 0.9 for r in kept["results"])
    shifted = Image.new("RGBA", body.size, (*guide["key"], 255))
    shifted.alpha_composite(Image.open(tmp_path / "dressed.png").convert("RGBA"), (24, 0))
    shifted.save(tmp_path / "shifted.png")
    moved = sheets.slice_sheet(assembly, tmp_path / "sheet/pose-sheet.json", tmp_path / "shifted.png", tmp_path / "b",
                               part_id="test.badge", slot="hair", node="hair_front", socket="head", body_threshold=8)
    assert len(moved["misaligned"]) == 3


def test_poses_limit_a_sheet_to_the_poses_still_to_draw(tmp_path):
    from parts_helpers import posed_workspace
    assembly, _ = posed_workspace(tmp_path)
    result = sheets.build_sheet(assembly, tmp_path / "sheet", scale=2, by_pose=True, poses=["walk.02"])
    guide = json.loads((tmp_path / "sheet/pose-sheet.json").read_text())
    assert result["cells"] == 1 and guide["cells"][0]["sample"] == "sample.1"
    with pytest.raises(contract.PartsContractError, match="unknown poses"):
        sheets.build_sheet(assembly, tmp_path / "x", by_pose=True, poses=["run.01"])
    with pytest.raises(contract.PartsContractError, match="--by-pose"):
        sheets.build_sheet(assembly, tmp_path / "y", poses=["walk.02"])


def test_a_sheet_drawn_over_worn_parts_cuts_only_the_new_drawing(tmp_path):
    from parts_helpers import part_spec
    assembly = frozen_workspace(tmp_path)
    source = tmp_path / "worn"
    source.mkdir()
    Image.new("RGBA", (6, 6), (20, 160, 60, 255)).save(source / "piece.png")
    worn = parts_import.import_pack(assembly, part_spec(tmp_path / "worn.json", assembly, source, pivot=(3, 3)), source)
    sheets.build_sheet(assembly, tmp_path / "sheet", scale=2, wearing=[worn["part"]["id"]])
    guide = json.loads((tmp_path / "sheet/pose-sheet.json").read_text())
    assert guide["wearing"] == [worn["part"]] and all(c["under_sha256"] != c["body_sha256"] for c in guide["cells"])
    dressed = Image.open(tmp_path / "sheet/pose-sheet.png").convert("RGBA")
    expected = drawn_sheet(guide, tmp_path / "drawn.png", body=dressed)
    sliced = sheets.slice_sheet(assembly, tmp_path / "sheet/pose-sheet.json", tmp_path / "drawn.png", tmp_path / "out",
                                part_id="test.badge", slot="hair", node="hair_front", socket="head", body_threshold=8)
    for row in sliced["results"]:
        image = Image.open(tmp_path / "out" / row["file"])
        x, y = expected[(row["clip"], row["sample"])]
        assert image.getbbox() == (x, y, x+6, y+4)  # the worn green square is not in the cut
    plain = sheets.slice_sheet(assembly, tmp_path / "sheet/pose-sheet.json", tmp_path / "drawn.png", tmp_path / "x",
                               part_id="test.badge", slot="hair", node="hair_front", socket="head", body_threshold=8)
    assert plain["results"][0]["pixels"] == sliced["results"][0]["pixels"]
    with pytest.raises(contract.PartsContractError, match="not an active part"):
        sheets.build_sheet(assembly, tmp_path / "bad", wearing=["nope"])


def test_covering_keeps_part_pixels_that_match_the_body_under_them():
    body = Image.new("RGBA", (24, 24))
    draw = ImageDraw.Draw(body)
    draw.rectangle((4, 4, 19, 19), fill=(230, 180, 150, 255), outline=(40, 20, 20, 255))
    hat = body.copy()
    ImageDraw.Draw(hat).rectangle((2, 2, 21, 9), fill=(200, 170, 110, 255))
    ImageDraw.Draw(hat).rectangle((2, 6, 21, 7), fill=(40, 20, 20, 255))  # a band in the body's outline color
    band = [(x, 6) for x in range(2, 22)]
    cut, _ = sheets.cut_cell(hat, body, body_threshold=40)
    kept, info = sheets.cut_cell(hat, body, method="covering")
    assert sum(cut.getpixel(p)[3] > 0 for p in band) < len(band)  # pixel subtraction loses band pixels
    assert all(kept.getpixel(p)[3] > 0 for p in band) and info["alignment"] is not None
    assert kept.getpixel((10, 15))[3] == 0  # the body below the hat stays out of the part


def test_keep_palette_drops_a_repainted_body_and_prune_drops_hanging_strands():
    body = Image.new("RGBA", (24, 24))
    ImageDraw.Draw(body).rectangle((4, 4, 19, 19), fill=(230, 180, 150, 255), outline=(40, 20, 20, 255))
    drawn = body.copy()
    draw = ImageDraw.Draw(drawn)
    draw.rectangle((4, 4, 19, 19), fill=(170, 110, 90, 255), outline=(40, 20, 20, 255))  # the face repainted darker
    draw.rectangle((2, 2, 21, 8), fill=(150, 40, 30, 255))  # the hair asked for
    draw.line((12, 9, 12, 16), fill=(60, 20, 10, 255))  # a strand left hanging
    hair = np.array([[150, 40, 30], [60, 20, 10], [40, 20, 20]])
    loose, _ = sheets.cut_cell(drawn, body, method="covering")
    kept, _ = sheets.cut_cell(drawn, body, method="covering", colors=hair, keep_palette=24, prune=8)
    assert loose.getpixel((10, 14))[3] > 0  # covering alone takes the repainted face
    assert kept.getpixel((10, 14))[3] == 0 and kept.getpixel((10, 4))[3] > 0
    assert all(kept.getpixel((12, y))[3] == 0 for y in range(10, 17))
    arc = drawn.copy()
    ImageDraw.Draw(arc).line((2, 0, 21, 0), fill=(40, 20, 20, 255))  # a worn hat's edge redrawn higher
    ImageDraw.Draw(arc).line((8, 12, 11, 12), fill=(40, 20, 20, 255))  # a dark stroke on the face
    kept, _ = sheets.cut_cell(arc, body, method="covering", colors=hair, keep_palette=24)
    assert kept.getpixel((12, 0))[3] == 0


def test_a_big_part_on_a_figure_that_sits_right_is_not_misaligned():
    body = Image.new("RGBA", (40, 40))
    ImageDraw.Draw(body).rectangle((15, 10, 24, 35), fill=(230, 180, 150, 255))
    hair = body.copy()
    ImageDraw.Draw(hair).rectangle((11, 6, 28, 16), fill=(150, 40, 30, 255))  # lots added, body kept
    alone = Image.new("RGBA", (40, 40))
    ImageDraw.Draw(alone).rectangle((11, 6, 28, 16), fill=(150, 40, 30, 255))  # the hair drawn without the body
    big = sheets.overlap(hair, body)
    assert big[0] < sheets.MIN_ALIGNMENT and not sheets.misaligned(*big)
    assert sheets.misaligned(*sheets.overlap(alone, body))
    assert sheets.misaligned(0.6) and sheets.misaligned(0.45, 0.99)


def test_cover_rim_hides_the_skull_a_part_was_drawn_inside_of():
    body = Image.new("RGBA", (24, 24))
    ImageDraw.Draw(body).ellipse((4, 4, 19, 19), fill=(230, 180, 150, 255), outline=(40, 20, 20, 255))
    drawn = Image.new("RGBA", (24, 24))  # the hair drawn 2 px inside the head
    ImageDraw.Draw(drawn).ellipse((6, 6, 17, 17), fill=(90, 60, 50, 255), outline=(40, 20, 20, 255))
    hair = np.array([[90, 60, 50], [40, 20, 20]])
    tight, _ = sheets.cut_cell(drawn, body, method="covering", colors=hair, keep_palette=24)
    covered, _ = sheets.cut_cell(drawn, body, method="covering", colors=hair, keep_palette=24, rim=3)
    assert tight.getpixel((11, 4))[3] == 0 and covered.getpixel((11, 4))[3] > 0
    assert covered.getpixel((11, 4))[:3] == (40, 20, 20)  # the outline moved out
    assert covered.getpixel((11, 6))[:3] == (90, 60, 50)  # the old outline is filled
