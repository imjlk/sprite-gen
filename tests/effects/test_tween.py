# SPDX-License-Identifier: Apache-2.0
"""Cross-state in-betweens (sprite_gen.effects.tween) plumbing.

No provider runs here: a stub generator "draws" the strip by filling each ghost slot, so the
tests check the strip layout, per-slot heights, foot anchor, endpoint refusal, border
warnings, the transition take record and the video sampling path without a network."""
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from sprite_gen import cli
from sprite_gen.effects import tween

MAGENTA = (255, 0, 255)
BODY = (90, 60, 30, 255)


def figure(w: int, h: int, *, sword: int = 0) -> Image.Image:
    im = Image.new("RGBA", (w + sword, h), (0, 0, 0, 0))
    for y in range(h):
        for x in range(w):
            im.putpixel((x, y), BODY)
    for x in range(w, w + sword):  # a thin blade reaching right at chest height
        im.putpixel((x, h // 3), (200, 200, 210, 255))
    return im


def fill_ghosts(strip_path: Path, prompt: str, out: Path, aspect: str, *, keep_ends: bool = True) -> None:
    assert "ONION-SKIN" in prompt and "#FF00FF" in prompt
    arr = np.asarray(Image.open(strip_path).convert("RGB")).copy()
    slots = int(prompt.split("strip of ")[1].split(" ")[0])
    w = arr.shape[1] // slots
    for k in range(1, slots - 1):
        cell = arr[:, k * w:(k + 1) * w]
        non = np.abs(cell.astype(int) - MAGENTA).sum(axis=2) > 60
        cell[non] = BODY[:3]
    if not keep_ends:
        arr[:, :w] = MAGENTA
        arr[10:30, 5:15] = BODY[:3]
    Image.fromarray(arr).save(out)


def endpoint(img: Image.Image, name: str) -> tween.Endpoint:
    return tween.Endpoint(img, name, [])


def test_parse_ref_allows_negative_and_slashed_states():
    assert tween.parse_ref("attack:-1") == ("attack", -1)
    assert tween.parse_ref("down/idle:0") == ("down/idle", 0)
    with pytest.raises(SystemExit):
        tween.parse_ref("attack")


def test_strip_heights_interpolate_and_feet_stay_on_anchor(tmp_path):
    crouch, stand = figure(30, 40), figure(24, 80)
    frames, report = tween.make_tween(endpoint(crouch, "a"), endpoint(stand, "b"), 3, workdir=tmp_path,
                                      generator=fill_ghosts)
    assert len(frames) == 3 and len({f.size for f in frames}) == 1
    heights = [tween._tight(f).height for f in frames]
    assert all(40 <= h <= 80 for h in heights) and heights == sorted(heights)
    assert all(e <= 2 for e in report["foot_error_px"])
    assert report["endpoint_match"]["iou_from"] > 0.9
    saved = json.loads((tmp_path / "tween.json").read_text())
    assert saved["n"] == 3 and (tmp_path / "keyframe-strip.png").is_file()
    w, h = Image.open(tmp_path / "keyframe-strip.png").size
    num, den = (int(v) for v in report["aspect"].split(":"))
    assert abs(w / h - num / den) < 0.02  # padded to a ratio the provider accepts


def test_drifted_endpoint_is_refused_and_raw_kept(tmp_path):
    def drift(strip, prompt, out, aspect):
        fill_ghosts(strip, prompt, out, aspect, keep_ends=False)
    with pytest.raises(SystemExit, match="refused"):
        tween.make_tween(endpoint(figure(30, 60), "a"), endpoint(figure(30, 60), "b"), 2,
                         workdir=tmp_path, generator=drift)
    assert (tmp_path / "raw.png").is_file()
    match = json.loads((tmp_path / "tween.json").read_text())["endpoint_match"]
    assert match["scale_agree"] < tween.MIN_SCALE_AGREE  # ends redrawn at different sizes


def test_content_crossing_slot_border_warns(tmp_path):
    def spill(strip, prompt, out, aspect):
        fill_ghosts(strip, prompt, out, aspect)
        im = Image.open(out).convert("RGB")
        w = im.width // 4
        for x in range(w + w // 2, 2 * w + 3):
            for y in range(im.height // 2, im.height // 2 + 12):
                im.putpixel((x, y), (200, 200, 210))
        im.save(out)
    frames, report = tween.make_tween(endpoint(figure(30, 60), "a"), endpoint(figure(30, 60), "b"), 2,
                                      workdir=tmp_path, generator=spill)
    assert any("slot 2 content crosses the slot border (right" in w for w in report["warnings"])
    blade = lambda f: (np.asarray(f)[..., :3] == (200, 200, 210)).all(axis=2).sum()
    assert blade(frames[0]) > 0 and blade(frames[1]) == 0  # stays with its owner, no second blade next door


def test_sheet_cell_touching_its_border_warns(tmp_path):
    sheet = Image.new("RGBA", (128, 64), (0, 0, 0, 0))
    sheet.alpha_composite(figure(20, 40, sword=44), (10, 12))  # blade runs into cell 1
    sheet.alpha_composite(figure(20, 40), (84, 12))
    sheet.save(tmp_path / "sheet.png")
    a = tween.endpoint_from_file(f"{tmp_path / 'sheet.png'}:0", (64, 64), MAGENTA)
    b = tween.endpoint_from_file(f"{tmp_path / 'sheet.png'}:-1", (64, 64), MAGENTA)
    assert a.touches_border == ["right"] and b.touches_border == ["left"]  # the blade shows in both cells
    _, report = tween.make_tween(a, b, 1, workdir=tmp_path / "w", generator=fill_ghosts,
                                 min_endpoint_iou=0.3)
    assert any("touches its cell border" in w for w in report["warnings"])


def _run(tmp_path: Path) -> Path:
    run = tmp_path / "run"
    (run / "raw").mkdir(parents=True)
    request = {
        "version": 1, "kind": "sprite-gen-request", "engine": "component-row",
        "character": {"id": "t", "description": "plumbing fixture"},
        "cell": {"shape": "square", "size": 128, "safe_margin": 6},
        "chroma_key": {"name": "magenta", "hex": "#FF00FF", "rgb": list(MAGENTA)},
        "states": {"attack": {"frames": 2, "fps": 8, "loop": False, "action": "a"},
                   "idle": {"frames": 2, "fps": 4, "loop": True, "action": "i"}},
    }
    (run / "sprite-request.json").write_text(json.dumps(request), encoding="utf-8")
    for state, sizes in (("attack", [(30, 50), (40, 44)]), ("idle", [(24, 70), (24, 72)])):
        strip = Image.new("RGBA", (256, 128), MAGENTA + (255,))
        for k, (w, h) in enumerate(sizes):
            strip.alpha_composite(figure(w, h), (20 + 128 * k, 120 - h))
        strip.save(run / "raw" / f"{state}.png")
    return run


def test_run_mode_records_transition_take_idempotently(tmp_path, monkeypatch):
    run = _run(tmp_path)
    monkeypatch.setattr(tween, "gen_generator", lambda provider, refs=None: fill_ghosts)
    args = dict(run_dir=run, from_ref="attack:-1", to_ref="idle:0", n=2, label="attack_recover")
    assert cli.COMMANDS["tween"][2](**args) == 0
    assert tween.run(**args) == 0
    req = json.loads((run / "sprite-request.json").read_text())
    takes = req["states"]["idle"]["takes"]
    assert len(takes) == 1 and takes[0]["frames"] == 2
    meta = takes[0]["transition"]
    assert (meta["from_state"], meta["from_index"], meta["to_state"], meta["n"]) == ("attack", -1, "idle", 2)
    assert (run / "raw" / "idle.takes" / "attack_recover.png").is_file()


def test_video_method_samples_clip_and_verifies(tmp_path, monkeypatch):
    a, b = figure(30, 40), figure(24, 80)
    calls = []

    def fake_runner(first, last, prompt, out):
        calls.append((first, last))
        out.write_bytes(b"mp4")

    def fake_frames(clip, workdir):
        f0, f1 = Image.open(calls[0][0]).convert("RGB"), Image.open(calls[0][1]).convert("RGB")
        return [Image.blend(f0, f1, t / 8) for t in range(9)]

    monkeypatch.setattr(tween, "video_frames", fake_frames)
    frames, report = tween.make_tween(endpoint(a, "a"), endpoint(b, "b"), 2, workdir=tmp_path,
                                      method="video", video_runner=fake_runner, min_endpoint_iou=0.5)
    assert len(frames) == 2 and report["method"] == "video" and len(calls) == 1


def test_mode_arguments_fail_loudly(tmp_path):
    with pytest.raises(SystemExit, match="either"):
        tween.run()
    with pytest.raises(SystemExit, match="--n"):
        tween.make_tween(endpoint(figure(4, 4), "a"), endpoint(figure(4, 4), "b"), 0, workdir=tmp_path)


@pytest.mark.parametrize("method", ["strip", "video"])
def test_no_frame_touches_its_canvas_and_ends_share_it(tmp_path, monkeypatch, method):
    """Regression: in-betweens drawn wider than the keyframes (a long blade reaching out) must not
    be clipped; the canvas is the union of A, B and the in-betweens with padding on every side."""
    a, b = figure(30, 60, sword=50), figure(24, 80)

    def wide(strip, prompt, out, aspect):
        fill_ghosts(strip, prompt, out, aspect)
        im = Image.open(out).convert("RGB")
        w = im.width // 4
        for x in range(w + w // 2, w + w - 2):  # blade to the slot's right edge
            for y in range(im.height // 2, im.height // 2 + 4):
                im.putpixel((x, y), (200, 200, 210))
        im.save(out)

    calls = []
    def runner(first, last, prompt, out):
        calls.append((first, last)); out.write_bytes(b"mp4")
    monkeypatch.setattr(tween, "video_frames", lambda clip, wd: [
        Image.blend(Image.open(calls[0][0]).convert("RGB"), Image.open(calls[0][1]).convert("RGB"), t / 6)
        for t in range(7)])
    frames, report = tween.make_tween(endpoint(a, "a"), endpoint(b, "b"), 2, workdir=tmp_path, method=method,
                                      generator=wide, video_runner=runner, min_endpoint_iou=0.3)
    for f in [*frames, *report["_ends"]]:
        assert f.size == tuple(report["canvas"])
        assert tween.border_touches(f, band=tween.OUT_PAD - 1) == []
