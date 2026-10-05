# SPDX-License-Identifier: Apache-2.0
"""Video loops become rig-canvas native frames with one projection and their measured holds."""
import json

import pytest
from PIL import Image, ImageDraw

from parts_helpers import workspace_at
from sprite_gen._deps import np
from sprite_gen.curate import parts_motion
from sprite_gen.frames import parts_loop_native as native
from sprite_gen.spec import parts_contract as contract


def loop_dir(path, offsets, *, size=400, hold=None, bob=()):
    """A large-canvas loop: a head, a body and one swinging leg, drawn in a few flat colors."""
    (path / "cycle").mkdir(parents=True)
    for n, dx in enumerate(offsets):
        image = Image.new("RGBA", (size, size))
        draw = ImageDraw.Draw(image)
        dy = bob[n] if bob else 0
        draw.ellipse((150, 40+dy, 250, 140+dy), fill=(240, 190, 130, 255))
        draw.rectangle((170, 140+dy, 230, 300+dy), fill=(200, 200, 200, 255))
        draw.rectangle((190+dx, 300+dy, 210+dx, 360), fill=(250, 200, 0, 255))
        image.save(path / "cycle" / f"frame-{n:03d}.png")
    if hold:
        (path.parent / "loop.report.json").write_text(json.dumps(
            {"cycle_drawings": {"hold": hold, "length": len(offsets)}}))
    return path


def test_one_projection_keeps_bob_and_snaps_palette(tmp_path):
    assembly = workspace_at(tmp_path / "library")
    loop = loop_dir(tmp_path / "walk/loop", [0, 20, 40, 20], bob=[0, -10, 0, -10])
    palette = tmp_path / "palette.json"
    palette.write_text(json.dumps({"colors": [[240, 190, 130], [200, 200, 200], [230, 180, 60]]}))
    result = native.build(assembly, tmp_path / "out", [("walk", loop)], body_height=80, baseline=110, palette=palette,
                          resample="smooth")
    clip = result["clips"][0]
    assert clip["samples"] == 4 and clip["projection"]["scale"] == "80/331"  # tallest pose: 331 source px
    frames = [Image.open(tmp_path / "out/walk" / f"{n:03d}.png") for n in range(4)]
    assert all(f.size == (128, 128) for f in frames)
    # The lowest foot sits on the baseline and the bob survives the common projection.
    assert max(f.getbbox()[3] for f in frames) == 111
    assert frames[0].getbbox()[1] - frames[1].getbbox()[1] in (2, 3)
    pixels = np.concatenate([np.asarray(f).reshape(-1, 4) for f in frames])
    assert {tuple(px[:3]) for px in pixels[pixels[:, 3] > 0]} <= {(240, 190, 130), (200, 200, 200), (230, 180, 60)}
    assert set(np.unique(pixels[:, 3])) <= {0, 255}


def test_reported_hold_groups_drawings_and_spec_imports(tmp_path):
    assembly = workspace_at(tmp_path / "library")
    loop = loop_dir(tmp_path / "run/loop", [0, 0, 30, 30, 60, 60], hold=2)
    result = native.build(assembly, tmp_path / "out", [("walk", loop)], body_height=80, baseline=110)
    clip = result["clips"][0]
    assert clip["holds"] == [2, 2, 2] and clip["hold_source"] == "loop.report.json"
    imported = parts_motion.import_motion(assembly, tmp_path / "out/motion-import.json", tmp_path / "out")
    assert imported["samples"] == 3


def test_frame_difference_merges_only_within_tolerance(tmp_path):
    assembly = workspace_at(tmp_path / "library")
    loop = loop_dir(tmp_path / "idle", [0, 0, 30, 60])
    exact = native.build(assembly, tmp_path / "a", [("idle", loop, "2")], body_height=80, baseline=110)
    assert exact["clips"][0]["holds"] == [2, 1, 1] and exact["clips"][0]["fps"] == "2"


@pytest.mark.parametrize("kwargs,match", [
    ({"body_height": 200}, "outside"),
    ({"body_height": 80, "baseline": 50}, "leaves the rig canvas"),
    ({"body_height": 80, "fps": "0"}, "fps"),
])
def test_invalid_projection_is_refused(tmp_path, kwargs, match):
    assembly = workspace_at(tmp_path / "library")
    loop = loop_dir(tmp_path / "walk/loop", [0, 20])
    with pytest.raises(contract.PartsContractError, match=match):
        native.build(assembly, tmp_path / "out", [("walk", loop)], **{"baseline": 110, **kwargs})


def test_every_keeps_cycle_time_with_fewer_drawings(tmp_path):
    assembly = workspace_at(tmp_path / "library")
    loop = loop_dir(tmp_path / "walk/loop", [0, 10, 20, 30])
    result = native.build(assembly, tmp_path / "out", [("walk", loop)], body_height=80, baseline=110, every=[("walk", 2)])
    assert result["clips"][0]["holds"] == [2, 2] and result["clips"][0]["hold_source"] == "every 2 frames"
    with pytest.raises(contract.PartsContractError, match="every"):
        native.build(assembly, tmp_path / "x", [("walk", loop)], body_height=80, baseline=110, every=[("walk", 3)])


def pixel_loop(path, offsets, *, pitch=6):
    """Native pixel art (with a one-pixel outline) upscaled by `pitch` at an odd phase."""
    (path / "cycle").mkdir(parents=True)
    for n, dx in enumerate(offsets):
        art = Image.new("RGBA", (40, 50))
        draw = ImageDraw.Draw(art)
        draw.rectangle((10+dx, 5, 25+dx, 44), fill=(17, 6, 5, 255))
        draw.rectangle((11+dx, 6, 24+dx, 43), fill=(240, 190, 130, 255))
        big = Image.new("RGBA", (40*pitch+3, 50*pitch+5))
        big.alpha_composite(art.resize((40*pitch, 50*pitch), Image.Resampling.NEAREST), (3, 5))
        big.save(path / "cycle" / f"frame-{n:03d}.png")
    return path


def test_lattice_recovers_one_pixel_outlines(tmp_path):
    assembly = workspace_at(tmp_path / "library")
    loop = pixel_loop(tmp_path / "walk/loop", [0, 2])
    result = native.build(assembly, tmp_path / "out", [("walk", loop)], body_height=40, baseline=110)
    projection = result["clips"][0]["projection"]
    assert abs(projection["pitch"] - 6) < 0.1 and projection["native_body_height"] == 40
    frame = np.asarray(Image.open(tmp_path / "out/walk/000.png"))
    rows = frame[frame[..., 3] > 0]
    assert {tuple(px[:3]) for px in rows} == {(17, 6, 5), (240, 190, 130)}
    # The left outline is exactly one native pixel wide.
    ys, xs = np.nonzero(frame[..., 3])
    middle = frame[(ys.min()+ys.max())//2]
    left = int(np.nonzero(middle[..., 3])[0].min())
    assert tuple(middle[left, :3]) == (17, 6, 5) and tuple(middle[left+1, :3]) == (240, 190, 130)


def test_silhouette_outline_closes_the_contour_and_drops_spurs():
    image = Image.new("RGBA", (10, 10))
    ImageDraw.Draw(image).rectangle((2, 2, 7, 7), fill=(240, 190, 130, 255))
    image.putpixel((8, 4), (240, 190, 130, 255))
    out = np.asarray(native.silhouette_outline(image, [17, 6, 5]))
    assert out[4, 8, 3] == 0 and tuple(out[2, 4, :3]) == (17, 6, 5) and tuple(out[4, 4, :3]) == (240, 190, 130)
