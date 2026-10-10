# SPDX-License-Identifier: Apache-2.0
"""Smooth source edges, explicit native geometry, and recorded registration."""

import json
from pathlib import Path
import numpy as np
import pytest
from PIL import Image
from parts_helpers import frozen_workspace
from test_parts_zmap_edit import ready
from sprite_gen.frames import (
    parts_pose_sheet as sheets,
    parts_part_edit as edit,
)
from sprite_gen.spec import parts_workspace as w, parts_contract as contract


def native_guide(tmp_path):
    assembly = frozen_workspace(tmp_path)
    made = sheets.build_sheet(
        assembly, tmp_path / "sheet", scale=1, gutter=0, columns=3
    )
    file = Path(made["guide"])
    guide = json.loads(file.read_text())
    guide["art_mode"] = "illustration"
    file.write_bytes(w.json_bytes(guide))
    index, registry = w.read_workspace(assembly)
    clips = {c["id"]: c for c in registry[index["motion"]["id"]]["clips"]}
    return assembly, file, guide, index, clips


def test_illustration_retains_even_faint_alpha_and_continuous_colors(tmp_path):
    _, file, guide, index, clips = native_guide(tmp_path)
    page = Image.new("RGBA", tuple(guide["size"]))
    for cell in guide["cells"]:
        x, y = cell["origin"]
        for n, alpha in enumerate((1, 64, 127, 128, 220, 254, 255)):
            page.putpixel(
                (x + 10 + n, y + 20), (31 + n * 17, 72 + n * 9, 150 - n * 7, alpha)
            )
    art = tmp_path / "art.png"
    page.save(art)
    for cell, _, raster, _ in sheets.drawn_cells([(file, art)], index, clips, 80):
        x, y = cell["origin"]
        assert np.array_equal(
            np.asarray(raster), np.asarray(page.crop((x, y, x + 128, y + 128)))
        )
        assert raster.getpixel((10, 20))[3] == 1


def test_illustration_rejects_implicit_resizing(tmp_path):
    _, file, guide, index, clips = native_guide(tmp_path)
    art = tmp_path / "art.png"
    Image.new("RGBA", (guide["size"][0] * 2, guide["size"][1] * 2)).save(art)
    with pytest.raises(contract.PartsContractError, match="native guide size"):
        list(sheets.drawn_cells([(file, art)], index, clips, 80))


def test_native_opaque_chroma_uses_the_existing_soft_alpha_engine(tmp_path):
    _, file, guide, index, clips = native_guide(tmp_path)
    page = Image.new("RGBA", tuple(guide["size"]), (*guide["key"], 255))
    for cell in guide["cells"]:
        x, y = cell["origin"]
        page.putpixel((x + 10, y + 20), (30, 60, 100, 255))
        page.putpixel((x + 11, y + 20), (142, 30, 178, 255))
    art = tmp_path / "art.png"
    page.save(art)
    for _, _, raster, _ in sheets.drawn_cells([(file, art)], index, clips, 80):
        assert raster.getpixel((0, 0))[3] == 0
        assert raster.getpixel((10, 20)) == (30, 60, 100, 255)
        assert 0 < raster.getpixel((11, 20))[3] < 255


def test_registered_part_keeps_aspect_and_other_source_components(tmp_path):
    assembly = ready(tmp_path)
    result = edit.edit(
        assembly,
        "test.badge",
        "walk.01",
        [{"type": "fit-alpha-box", "bounds": [50, 50, 62, 62]}],
    )
    raster = Image.open(assembly.parent / result["preview_candidate"] / "part.png")
    assert raster.getbbox() == (50, 52, 62, 60)
    assert not result["selected"] and result["provider_calls"] == 0
    selected = edit.edit(
        assembly,
        "test.badge",
        "walk.01",
        [
            {
                "type": "fit-alpha-box",
                "source_bounds": [36, 42, 38, 46],
                "bounds": [80, 80, 88, 96],
            }
        ],
    )
    raster = Image.open(assembly.parent / selected["preview_candidate"] / "part.png")
    assert raster.getpixel((40, 44)) == (200, 40, 40, 255)
    assert raster.getpixel((36, 44))[3] == 0 and raster.getpixel((82, 84))[3] == 255
    for op in (
        {"type": "fit-alpha-box"},
        {
            "type": "fit-alpha-box",
            "source_bounds": [0, 0, 500, 500],
            "bounds": [0, 0, 5, 5],
        },
    ):
        with pytest.raises(contract.PartsContractError):
            edit.edit(assembly, "test.badge", "walk.01", [op])


def test_authored_hands_cannot_own_shoe_or_leg_pixels():
    import importlib.util

    file = (
        Path(__file__).resolve().parents[2] / "scripts/dev/parts_illustration_demo.py"
    )
    spec = importlib.util.spec_from_file_location("illustration_fixture", file)
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
    body = Image.new("RGBA", (1254, 1254), (21, 70, 91, 255))
    labels, _, masks = demo.authored_labels(body)
    assert masks["hand_front"][740, 450]
    assert not masks["hand_front"][1160, 450]
    assert masks["foot_front"][1160, 450]
    coverage = sum(mask.astype("uint8") for mask in masks.values())
    assert np.all(coverage == 1)
    assert np.all(np.asarray(labels)[..., 3] == 255)
    for name in ("hand_front", "hand_back"):
        assert not masks[name][830:].any()


def test_fit_only_source_region_field_cannot_silently_change_other_edits(tmp_path):
    assembly = ready(tmp_path)
    with pytest.raises(
        contract.PartsContractError, match="source_bounds is only supported"
    ):
        edit.edit(
            assembly,
            "test.badge",
            "walk.01",
            [{"type": "translate", "dx": 1, "source_bounds": [0, 0, 4, 4]}],
        )


def test_bounded_hole_repair_keeps_original_pixels_and_open_notches(tmp_path):
    assembly = ready(tmp_path)
    erased = edit.edit(
        assembly,
        "test.badge",
        "walk.01",
        [
            {"type": "erase", "bounds": [38, 43, 39, 44]},
            {"type": "erase", "bounds": [36, 42, 37, 43]},
        ],
    )
    restored = edit.edit(
        assembly,
        "test.badge",
        "walk.01",
        [{"type": "fill-small-holes", "bounds": [36, 42, 42, 46], "max_pixels": 1}],
        candidate=erased["preview_candidate"],
    )
    before = np.asarray(
        Image.open(assembly.parent / erased["preview_candidate"] / "part.png")
    )
    after = np.asarray(
        Image.open(assembly.parent / restored["preview_candidate"] / "part.png")
    )
    assert tuple(after[43, 38]) == (200, 40, 40, 255)
    assert after[42, 36, 3] == 0
    assert np.array_equal(before[before[..., 3] > 0], after[before[..., 3] > 0])
    assert restored["changed_pixels"] == 1 and not restored["selected"]
    for op in (
        {"type": "fill-small-holes"},
        {"type": "fill-small-holes", "bounds": [0, 0, 128, 128], "max_pixels": 65},
    ):
        with pytest.raises(contract.PartsContractError):
            edit.edit(assembly, "test.badge", "walk.01", [op])


def test_registered_geometry_uses_fit_interfaces_and_keeps_original_source(tmp_path):
    from sprite_gen.frames import parts_production as p

    assembly = ready(tmp_path)
    fitted = edit.edit(
        assembly,
        "test.badge",
        "walk.01",
        [{"type": "fit-alpha-box", "bounds": [70, 70, 82, 82]}],
    )
    next_edit = edit.edit(
        assembly,
        "test.badge",
        "walk.01",
        [{"type": "recolor", "from_color": "#c82828", "to_color": "#00ff00"}],
        candidate=fitted["preview_candidate"],
    )
    p.select_candidate(
        assembly, "test.badge", "walk.01", next_edit["preview_candidate"]
    )
    p.check(assembly, "test.badge")
    metrics = p._load(assembly.parent, "test.badge")["tracks"]["main"]["poses"][
        "walk.01"
    ]["check"]["metrics"]
    assert (
        "dropped" not in metrics
        and "after registration" in metrics["source_comparison"]
    )
    record = json.loads(
        (
            assembly.parent / next_edit["preview_candidate"] / "candidate.json"
        ).read_text()
    )
    assert record["method"]["programmatic_edit"]["registered_geometry"]
    assert "drawn" in record["images"] and "under" in record["images"]


def test_native_sock_production_replaces_only_explicit_feet_and_restores_body(tmp_path):
    import importlib.util
    from PIL import ImageDraw
    from sprite_gen.compose.parts_runtime import PartsPlayer

    file = (
        Path(__file__).resolve().parents[2] / "scripts/dev/parts_illustration_demo.py"
    )
    spec = importlib.util.spec_from_file_location("illustration_production", file)
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
    out = tmp_path / "experiment"
    (out / "art").mkdir(parents=True)
    body = Image.new("RGBA", (1254, 1254))
    painter = ImageDraw.Draw(body)
    painter.rectangle((480, 1130, 540, 1210), fill=(200, 140, 100, 255))
    painter.rectangle((690, 1120, 800, 1180), fill=(200, 140, 100, 255))
    body.save(out / "art/body.png")
    socks = Image.new("RGBA", body.size)
    painter = ImageDraw.Draw(socks)
    painter.rectangle((470, 1000, 565, 1220), fill=(30, 70, 200, 255))
    painter.rectangle((660, 1000, 835, 1190), fill=(30, 70, 200, 255))
    socks.save(out / "art/socks.crew.png")
    from sprite_gen.spec.parts_assets import sha256

    with pytest.raises(ValueError, match="authored ownership profile"):
        demo.prepare(out)
    assert not (out / "library").exists()
    demo.prepare(out, expected_body_rgba=sha256(body.tobytes()))
    demo.import_part(out, "socks.crew")
    demo.finish(out)
    assembly = out / "library/assembly.json"
    index, registry = w.read_workspace(assembly)
    player = PartsPlayer.from_workspace(assembly, state="fit")
    dressed = player.layers(interpolate=False)
    assert dressed["masks"] == []
    assert not any(
        layer["slot"] is None and layer["node"] in ("foot_front", "foot_back")
        for layer in dressed["layers"]
    )
    assert {
        node
        for layer in dressed["layers"]
        if layer["slot"] == "socks"
        for node in layer["provides"]
    } == {"foot_front", "foot_back"}
    player.set_selection({"socks": None})
    restored = player.layers(interpolate=False)
    assert {layer["node"] for layer in restored["layers"] if layer["slot"] is None} >= {
        "foot_front",
        "foot_back",
    }
    assert player.frame()[0].tobytes() == body.tobytes()
    import shutil, subprocess

    if shutil.which("node"):
        from sprite_gen.compose import export_parts

        _, manifest, bundle = export_parts.pack(assembly.parent, index, registry)
        assert manifest["sampling"] == "smooth"
        cases = [
            {"socks": "illustration.socks.crew"},
            {"socks": ""},
            {"socks": "illustration.socks.crew", "bodyOnly": True},
        ]
        expected = []
        for selection in cases:
            player.set_selection(
                {
                    "socks": contract.reference(registry[selection["socks"]])
                    if selection["socks"] and not selection.get("bodyOnly")
                    else None
                }
            )
            expected.append(
                [
                    {key: layer[key] for key in ("id", "node", "provides", "position")}
                    for layer in player.layers(interpolate=False)["layers"]
                ]
            )
        adapter = (
            Path(__file__).resolve().parents[2]
            / "sprite_gen/serve/parts_player/runtime.js"
        )
        js = "const {LayerRuntime}=require(process.argv[1]);const d=JSON.parse(require('fs').readFileSync(0,'utf8'));const r=new LayerRuntime(d.bundle);process.stdout.write(JSON.stringify(d.cases.map(({bodyOnly,...s})=>r.scene('fit',0,s,{bodyOnly}).layers.map(({id,node,provides,position})=>({id,node,provides,position})))));"
        result = subprocess.run(
            ["node", "-e", js, str(adapter)],
            input=json.dumps({"bundle": bundle, "cases": cases}),
            text=True,
            capture_output=True,
            check=True,
        )
        assert json.loads(result.stdout) == expected
