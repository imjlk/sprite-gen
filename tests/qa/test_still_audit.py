# SPDX-License-Identifier: Apache-2.0
"""Base-still audit: pixel scale, cleanliness, ramps and outline, each with a remedy."""
import json
import subprocess
import sys

from PIL import Image, ImageDraw

from sprite_gen.qa import still_audit

SKIN = [(250, 200, 160), (225, 165, 125), (190, 130, 95)]
CLOTH = [(120, 170, 200), (80, 125, 160), (50, 85, 120)]


def shaded_art(*, ramps=True):
    """A 30 x 40 figure with a 1 px outline; each material in three tones, or one when ramps=False."""
    art = Image.new("RGBA", (30, 40))
    draw = ImageDraw.Draw(art)
    draw.rectangle((8, 4, 21, 37), fill=(17, 6, 5, 255))
    for n, (top, bottom, ramp) in enumerate(((5, 19, SKIN), (20, 36, CLOTH))):
        for k, color in enumerate(ramp if ramps else ramp[:1]*3):
            draw.rectangle((9+k*4, top, 12+k*4 if k < 2 else 20, bottom), fill=(*color, 255))
    return art


def test_upscaled_shaded_pixel_art_passes(tmp_path):
    shaded_art().resize((180, 240), Image.Resampling.NEAREST).save(tmp_path / "still.png")
    report = still_audit.audit(tmp_path / "still.png")
    assert report["status"] == "passed" and report["scale"] == "upscaled" and report["pitch"] == 6
    assert report["native_size"] == [14, 34] and report["outline_share"] == 1.0 and not report["advice"]


def test_native_art_is_recognised_and_flat_shading_gets_a_redraw_remedy(tmp_path):
    shaded_art(ramps=False).save(tmp_path / "flat.png")
    report = still_audit.audit(tmp_path / "flat.png")
    assert report["scale"] == "native" and report["pitch"] == 1.0
    advice = {a["for"]: a["do"] for a in report["advice"]}
    assert set(advice) == {"ramp_steps"} and "3-5 tone ramps" in advice["ramp_steps"]


def test_soft_still_warns_and_fix_snaps_it_to_its_grid(tmp_path):
    soft = shaded_art().resize((173, 231), Image.Resampling.BICUBIC)
    ImageDraw.Draw(soft).point((2, 2), fill=(255, 0, 0, 255))
    soft.save(tmp_path / "soft.png")
    report = still_audit.audit(tmp_path / "soft.png", target_height=34)
    warned = {c["id"] for c in report["checks"] if c["status"] == "warning"}
    assert report["scale"] == "soft" and "pixel_scale" in warned and report["specks"] == 1
    assert any("--fix" in a["do"] for a in report["advice"])
    fixed = still_audit.fix(tmp_path / "soft.png", tmp_path / "fixed.png", target_height=34, colors=12)
    after = still_audit.audit(fixed["filmable"])
    assert after["scale"] == "upscaled" and after["soft_alpha_share"] == 0 and after["specks"] == 0
    assert abs(fixed["native_size"][1] - 34) <= 2  # a soft edge may add a row on each side


def test_cli_reports_advice_and_noise_map(tmp_path):
    shaded_art().resize((180, 240), Image.Resampling.NEAREST).save(tmp_path / "s.png")
    done = subprocess.run([sys.executable, "-m", "sprite_gen.cli", "still-audit", "--image", str(tmp_path / "s.png"),
                           "--noise-map", str(tmp_path / "noise.png")], capture_output=True, text=True)
    payload = json.loads(done.stdout)
    assert done.returncode == 0 and payload["ok"] and payload["advice"] == [] and (tmp_path / "noise.png").is_file()
