# SPDX-License-Identifier: Apache-2.0
"""Cross-state in-betweens — draw N frames between a frame of one state and a frame of another.

`interpolate` makes one in-between inside one state's primary strip. Chained states
(`attack -> idle`, `skill -> idle`, `idle -> attack` wind-up) need frames between two
*different* finished frames, often several of them, and often across a big change of
height (a crouch into a stand). This module:

1. takes the two endpoints from a run (`--from state:idx --to state:idx`, negative
   indices allowed) or from files (`--from-file a.png[:idx] --cell WxH` slices a sheet);
2. lays them out as ONE keyframe strip: slot 1 = A, slot N+2 = B, grey onion-skin ghosts
   of both silhouettes in the slots between, with a safe margin around every slot;
3. asks the image provider to fill all N slots in one call (`--method strip`), or films a
   first-last clip through the existing `video --image --last-frame` path and samples it
   (`--method video`);
4. refuses the result when the drawn endpoints do not match A and B (silhouette IoU under
   `--min-endpoint-iou`), so a drifted strip never becomes a take silently;
5. rescales each in-between by the drawn-to-keyframe scale, interpolated from the scale
   measured on the two drawn endpoints (clamped to the A..B height range), and puts
   its foot anchor on the shared ground row;
6. warns when content crosses a slot or source cell border (thin weapons reaching into the
   neighbour cell are the usual cause of mis-sliced sheets);
7. records the frames as a transition take on the destination state
   (`takes[].transition = {from_state, from_index, to_state, to_index, n}`) or, in file
   mode, as frames plus `tween.json` in `--out-dir`.

The provider is the one named; nothing retries on another provider (No silent fallback).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from sprite_gen._deps import np
from PIL import Image

from sprite_gen.util.cli_kwargs import namespace_from_kwargs
from sprite_gen.util.console import utf8_streams

PROVIDERS = ("codex", "grok")
METHODS = ("strip", "video")
ANCHORS = ("foot", "bottom-center")
DEFAULT_MIN_ENDPOINT_IOU = 0.6
DEFAULT_MARGIN = 0.12
MIN_SCALE_AGREE = 0.85
OUT_PAD = 8
VIDEO_MIN_MARGIN = 0.3
STEP_IOU_WARN = 0.45
ALPHA_MIN = 24
# Aspect ratios the image providers accept for a multi-ref edit; the strip is padded to the
# nearest one at or below its natural ratio so slots are never squeezed.
STRIP_ASPECTS = ((21, 9), (20, 9), (5, 2), (2, 1), (16, 9), (3, 2), (4, 3), (1, 1))

# generator signature: (strip_png, prompt, out_png, aspect "w:h") -> None. Tests inject a stub.
Generator = Callable[[Path, str, Path, str], None]
# video runner signature: (first_png, last_png, prompt, out_mp4) -> None.
VideoRunner = Callable[[Path, Path, str, Path], None]


@dataclass
class Endpoint:
    image: Image.Image          # tight RGBA crop of the figure
    source: str
    touches_border: list[str]   # sides of the source cell the content touches


# ---------------------------------------------------------------- endpoints

def parse_ref(spec: str) -> tuple[str, int]:
    """`state:idx` -> (state, idx); idx may be negative. The state may itself contain `/`."""
    if ":" not in spec:
        raise SystemExit(f"tween: expected <state>:<index>, got {spec!r}")
    state, _, idx = spec.rpartition(":")
    try:
        return state, int(idx)
    except ValueError:
        raise SystemExit(f"tween: frame index must be an integer in {spec!r}") from None


def _pick(items: list, index: int, what: str):
    if not -len(items) <= index < len(items):
        raise SystemExit(f"tween: {what} index {index} out of range ({len(items)} frames)")
    return items[index]


def _alpha(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("RGBA"))[..., 3]


def _keyed(image: Image.Image, chroma_rgb: tuple[int, int, int]) -> Image.Image:
    """RGBA as is when it has transparency; otherwise remove the flat chroma background."""
    rgba = image.convert("RGBA")
    if (_alpha(rgba) < 255).any():
        return rgba
    from sprite_gen.frames.extract import remove_chroma_background_ycbcr
    return remove_chroma_background_ycbcr(rgba, chroma_rgb)


def border_touches(rgba: Image.Image, band: int = 1) -> list[str]:
    a = _alpha(rgba) >= ALPHA_MIN
    sides = {"left": a[:, :band], "right": a[:, -band:], "top": a[:band, :], "bottom": a[-band:, :]}
    return [side for side, px in sides.items() if px.any()]


def _tight(rgba: Image.Image) -> Image.Image:
    box = Image.fromarray(((_alpha(rgba) >= ALPHA_MIN) * 255).astype("uint8")).getbbox()
    if box is None:
        raise SystemExit("tween: endpoint frame is empty (no opaque content)")
    return rgba.crop(box)


def endpoint_from_file(spec: str, cell: tuple[int, int] | None,
                       chroma_rgb: tuple[int, int, int]) -> Endpoint:
    """`path` (one frame) or `path:idx` with `cell` (a sheet of equal cells, row-major)."""
    path_s, idx = spec, None
    head, sep, tail = spec.rpartition(":")
    if sep and tail.lstrip("-").isdigit():
        path_s, idx = head, int(tail)
    path = Path(path_s)
    if not path.is_file():
        raise SystemExit(f"tween: endpoint file missing: {path}")
    image = Image.open(path)
    image.load()
    if idx is not None:
        if cell is None:
            raise SystemExit(f"tween: {spec!r} names a cell index; pass --cell WxH")
        cw, ch = cell
        cols, rows = image.width // cw, image.height // ch
        boxes = [(c * cw, r * ch, (c + 1) * cw, (r + 1) * ch) for r in range(rows) for c in range(cols)]
        image = image.crop(_pick(boxes, idx, str(path)))
    keyed = _keyed(image, chroma_rgb)
    return Endpoint(_tight(keyed), spec, border_touches(keyed))


def endpoint_from_run(run_dir: Path, request: dict[str, Any], state: str, index: int) -> Endpoint:
    from sprite_gen.frames.extract import (extract_component_images, remove_chroma_background_ycbcr,
                                           tighten_components)
    from sprite_gen.spec.layout import raw_rel
    if state not in request.get("states", {}):
        raise SystemExit(f"tween: unknown state: {state}")
    strip_path = run_dir / raw_rel(request, state)
    if not strip_path.is_file():
        raise SystemExit(f"tween: raw strip missing: {strip_path}")
    chroma_rgb = tuple(request["chroma_key"]["rgb"])
    frames = int(request["states"][state]["frames"])
    cleaned = remove_chroma_background_ycbcr(Image.open(strip_path).convert("RGBA"), chroma_rgb)
    components = extract_component_images(cleaned, frames)
    if not components:
        raise SystemExit(f"tween: could not extract {frames} components from {strip_path}")
    image = _pick(tighten_components(components), index, state)
    return Endpoint(_tight(image), f"{state}:{index}", [])


# ---------------------------------------------------------------- geometry

def foot_x(rgba: Image.Image, anchor: str = "foot") -> float:
    """Foot anchor x in the tight crop: mean x of the bottom 6 % of opaque rows."""
    a = _alpha(rgba) >= ALPHA_MIN
    if anchor == "bottom-center" or not a.any():
        return rgba.width / 2.0
    rows = np.nonzero(a.any(axis=1))[0]
    band = max(1, int(round(len(rows) * 0.06)))
    ys, xs = np.nonzero(a[rows[-band]:rows[-1] + 1])
    return float(xs.mean())


def _scaled(rgba: Image.Image, height: float) -> Image.Image:
    f = height / rgba.height
    return rgba.resize((max(1, round(rgba.width * f)), max(1, round(height))), Image.Resampling.LANCZOS)


@dataclass
class Layout:
    slot_w: int
    slot_h: int
    ground: int      # ground row inside a slot
    foot: int        # foot anchor column inside a slot
    heights: list[float]
    aspect: str


def plan_layout(a: Image.Image, b: Image.Image, n: int, margin: float, anchor: str) -> Layout:
    slots = n + 2
    fa, fb = foot_x(a, anchor), foot_x(b, anchor)
    left = max(fa, fb)
    right = max(a.width - fa, b.width - fb)
    tall = max(a.height, b.height)
    pad = int(round(max(left + right, tall) * margin))
    slot_w = int(round(left + right)) + 2 * pad
    slot_h = tall + 2 * pad
    natural = slots * slot_w / slot_h
    num, den = next(((w, h) for w, h in STRIP_ASPECTS if w / h <= natural), STRIP_ASPECTS[-1])
    if num / den < natural - 1e-9:
        slot_h = int(round(slots * slot_w * den / num))   # pad height so the strip hits a provider ratio
    heights = [a.height + (b.height - a.height) * k / (slots - 1) for k in range(slots)]
    return Layout(slot_w, slot_h, slot_h - pad, pad + int(round(left)), heights, f"{num}:{den}")


def place(canvas: Image.Image, figure: Image.Image, slot: int, lay: Layout, anchor: str) -> None:
    x = slot * lay.slot_w + lay.foot - int(round(foot_x(figure, anchor)))
    canvas.alpha_composite(figure, (x, lay.ground - figure.height))


def ghost(figure: Image.Image, weight: float) -> Image.Image:
    a = _alpha(figure).astype(np.float32) * (0.25 + 0.35 * weight)
    out = np.zeros((figure.height, figure.width, 4), np.uint8)
    out[..., :3] = 128
    out[..., 3] = a.clip(0, 255).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def keyframe_strip(a: Image.Image, b: Image.Image, lay: Layout, n: int, anchor: str,
                   chroma_rgb: tuple[int, int, int]) -> Image.Image:
    """A | ghosts x n | B on a flat chroma background, every figure foot-anchored on one ground row."""
    slots = n + 2
    strip = Image.new("RGBA", (slots * lay.slot_w, lay.slot_h), chroma_rgb + (255,))
    place(strip, a, 0, lay, anchor)
    place(strip, b, slots - 1, lay, anchor)
    for k in range(1, slots - 1):
        t = k / (slots - 1)
        h = lay.heights[k]
        place(strip, ghost(_scaled(a, h), 1 - t), k, lay, anchor)
        place(strip, ghost(_scaled(b, h), t), k, lay, anchor)
    return strip


def tween_prompt(n: int, description: str | None, chroma: tuple[str, str]) -> str:
    slots = n + 2
    middle = f"slots 2-{slots - 1}" if n > 1 else "slot 2"
    who = description or "the same character as the keyframes"
    name, hexv = chroma
    return (
        f"ANIMATION IN-BETWEEN task on a horizontal strip of {slots} equal slots. The attached image is "
        f"the strip: slot 1 (far left) is keyframe A and slot {slots} (far right) is keyframe B, two "
        f"finished drawings of the same character. {middle} hold GREY ONION-SKIN GHOSTS of A and B: "
        "replace each ghost with a finished drawing of the character in an in-between pose, evenly "
        "spaced in time from A to B, so the slots play as one smooth motion. SHORTEST PATH: each pose "
        "stays inside the space between the two ghost silhouettes in its slot, same stance width, "
        "same scale, same ground line; no new poses, swings, spins or flourishes that are in neither "
        "keyframe; every limb and held prop moves monotonically from its place in A to its place in B "
        "(it never passes beyond either keyframe). Rigid props keep their length and stay attached "
        "to the hand holding them, turning smoothly from their angle in A to their angle in B; never "
        "doubled. Every slot faces the same direction as the keyframes. Keep slot 1 and the last slot exactly as they are. Keep each figure fully inside "
        f"its own slot. Any further attached image is the character's reference art. Character: {who}. Same art style, palette and line quality as the keyframes. "
        f"Flat solid {name} {hexv} background everywhere, no ghosts left, no effects, no shadows, "
        "no text, no borders."
    )


# ---------------------------------------------------------------- verification

def silhouette_iou(x: Image.Image, y: Image.Image, anchor: str = "foot") -> float:
    """IoU of two silhouettes at their drawn scale, bottoms on one row and foot anchors aligned.

    Not scale-normalized on purpose: the strip is drawn at the keyframes' scale, so a drawn
    endpoint that changed size has drifted too."""
    h = max(x.height, y.height)
    xs, ys = x, y
    fx, fy = foot_x(xs, anchor), foot_x(ys, anchor)
    off = int(round(fx - fy))
    w = max(xs.width, ys.width) + abs(off) + 2
    ma = np.zeros((h, w), bool)
    mb = np.zeros((h, w), bool)
    ox = max(0, -off) + 1
    ma[h - xs.height:, ox:ox + xs.width] = _alpha(xs) >= 128
    mb[h - ys.height:, ox + off:ox + off + ys.width] = _alpha(ys) >= 128
    union = (ma | mb).sum()
    return float((ma & mb).sum() / union) if union else 0.0


def slice_slots(image: Image.Image, slots: int, chroma_rgb: tuple[int, int, int]
                ) -> tuple[list[Image.Image], list[str]]:
    """Cut a drawn strip into slot figures by connected component, not by fixed columns.

    Each component goes to the slot holding its centre, so a blade that reaches over a slot
    border stays with the figure holding it instead of being cut off there and showing up as a
    second blade in the neighbour slot. Components spanning a border are reported."""
    from sprite_gen.frames.extract import connected_components
    keyed = _keyed(image, chroma_rgb)
    arr = np.asarray(keyed)
    w = keyed.width / slots
    comps = [c for c in connected_components(keyed) if c["area"] >= 24]
    masks = [np.zeros(keyed.width * keyed.height, bool) for _ in range(slots)]
    warnings = []
    for c in comps:
        k = min(slots - 1, int(c["center_x"] // w))
        masks[k][np.asarray(c["pixels"])] = True
        x0, _, x1, _ = c["bbox"]
        if (x0 < k * w - 1 or x1 > (k + 1) * w + 1) and c["area"] >= 200:
            warnings.append(f"slot {k + 1} content crosses the slot border "
                            f"({'left' if x0 < k * w - 1 else 'right'}, {c['area']} px)")
    out = []
    for m in masks:
        cell = arr.copy()
        cell[..., 3] = np.where(m.reshape(keyed.height, keyed.width), cell[..., 3], 0)
        img = Image.fromarray(cell, "RGBA")
        if img.getbbox() is None:
            raise SystemExit("tween: a slot came back empty in the drawn strip")
        out.append(img)
    return out, warnings


def _largest_figure(rgba: Image.Image) -> Image.Image:
    """Drop specks: keep components holding >= 2 % of the largest component's pixels."""
    from sprite_gen.frames.extract import connected_components
    rgba = rgba.convert("RGBA")
    comps = connected_components(rgba)
    if len(comps) <= 1:
        return _tight(rgba)
    big = max(len(c["pixels"]) for c in comps)
    keep = np.zeros(rgba.width * rgba.height, bool)
    for c in comps:
        if len(c["pixels"]) >= big * 0.02:
            keep[np.asarray(c["pixels"])] = True
    arr = np.asarray(rgba).copy()
    arr[..., 3] = np.where(keep.reshape(rgba.height, rgba.width), arr[..., 3], 0)
    return _tight(Image.fromarray(arr, "RGBA"))


# ---------------------------------------------------------------- providers

def gen_generator(provider: str, extra_refs: list[Path] | None = None) -> Generator:
    if provider not in PROVIDERS:
        raise SystemExit(f"tween: unknown provider {provider!r} (choose from {PROVIDERS})")
    from sprite_gen.gen import generate_image

    def run(strip: Path, prompt: str, out: Path, aspect: str) -> None:
        generate_image(provider, prompt, out, refs=[strip, *(extra_refs or [])],
                       aspect_ratio=aspect if provider == "grok" else None)
    return run


def video_runner_default(first: Path, last: Path, prompt: str, out: Path) -> None:
    from sprite_gen.gen.video import VideoRequest, generate_video
    generate_video(VideoRequest(image=first, last_frame=last, prompt=prompt, out=out, duration=2,
                                resolution="480p", generate_audio=False))


def video_frames(clip: Path, workdir: Path) -> list[Image.Image]:
    if shutil.which("ffmpeg") is None:
        raise SystemExit("tween --method video needs ffmpeg on PATH")
    frames = workdir / "clip-frames"
    frames.mkdir(exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(clip), str(frames / "%04d.png")], check=True)
    out = []
    for p in sorted(frames.glob("*.png")):
        with Image.open(p) as im:
            out.append(im.convert("RGB").copy())
    if len(out) < 3:
        raise SystemExit(f"tween: clip {clip} has {len(out)} frames; need at least 3")
    return out


# ---------------------------------------------------------------- main flow

def make_tween(a: Endpoint, b: Endpoint, n: int, *, workdir: Path, method: str = "strip",
               provider: str = "grok", anchor: str = "foot", margin: float = DEFAULT_MARGIN,
               min_endpoint_iou: float = DEFAULT_MIN_ENDPOINT_IOU, description: str | None = None,
               chroma: tuple[str, str, tuple[int, int, int]] = ("magenta", "#FF00FF", (255, 0, 255)),
               generator: Generator | None = None, video_runner: VideoRunner | None = None,
               extra_refs: list[Path] | None = None) -> tuple[list[Image.Image], dict[str, Any]]:
    """Return N in-between RGBA frames (slot-sized canvases) and the report. Raises SystemExit on refusal."""
    if not 1 <= n <= 6:
        raise SystemExit(f"tween: --n must be 1..6, got {n}")
    if method not in METHODS:
        raise SystemExit(f"tween: unknown method {method!r}")
    if anchor not in ANCHORS:
        raise SystemExit(f"tween: unknown anchor {anchor!r}")
    name, hexv, chroma_rgb = chroma
    workdir.mkdir(parents=True, exist_ok=True)
    warnings = [f"source {ep.source} touches its cell border ({', '.join(ep.touches_border)}): "
                "content may be cut off or belong to a neighbour cell"
                for ep in (a, b) if ep.touches_border]
    lay = plan_layout(a.image, b.image, n, margin, anchor)
    slots = n + 2
    prompt = tween_prompt(n, description, (name, hexv))
    (workdir / "prompt.txt").write_text(prompt, encoding="utf-8")
    if method == "strip":
        strip = keyframe_strip(a.image, b.image, lay, n, anchor, chroma_rgb)
        strip.convert("RGB").save(workdir / "keyframe-strip.png")
        raw = workdir / "raw.png"
        (generator or gen_generator(provider, extra_refs))(workdir / "keyframe-strip.png", prompt, raw, lay.aspect)
        with Image.open(raw) as im:
            drawn = im.convert("RGB").resize(strip.size, Image.Resampling.LANCZOS)
        cells, crossings = slice_slots(drawn, slots, chroma_rgb)
        warnings += crossings
        figures = [_largest_figure(c) for c in cells]
    else:
        lay = plan_layout(a.image, b.image, n, max(margin, VIDEO_MIN_MARGIN), anchor)
        ends = []
        for tag, ep in (("first", a), ("last", b)):
            canvas = Image.new("RGBA", (lay.slot_w, lay.slot_h), chroma_rgb + (255,))
            place(canvas, ep.image, 0, lay, anchor)
            canvas.convert("RGB").save(workdir / f"{tag}.png")
            ends.append(workdir / f"{tag}.png")
        clip = workdir / "clip.mp4"
        (video_runner or video_runner_default)(ends[0], ends[1], prompt, clip)
        frames = video_frames(clip, workdir)
        picks = [round(k * (len(frames) - 1) / (slots - 1)) for k in range(slots)]
        keyed = [_keyed(frames[i].resize((lay.slot_w, lay.slot_h)), chroma_rgb) for i in picks]
        for k, im in enumerate(keyed):
            if border_touches(im):
                warnings.append(f"clip frame {picks[k]} touches the clip border ({', '.join(border_touches(im))}): "
                                "the figure may be cut by the video frame")
        figures = [_largest_figure(im) for im in keyed]
    # The model draws at its own scale (it often fills a padded strip). Measure that scale on the
    # two drawn endpoints, compare silhouettes at the keyframes' scale, and refuse when the two
    # ends disagree on scale: a strip whose ends were redrawn at different sizes has drifted.
    k_a = a.image.height / figures[0].height
    k_b = b.image.height / figures[-1].height
    scale_agree = min(k_a, k_b) / max(k_a, k_b)
    iou_a = silhouette_iou(_scaled(figures[0], a.image.height), a.image, anchor)
    iou_b = silhouette_iou(_scaled(figures[-1], b.image.height), b.image, anchor)
    lo, hi = min(a.image.height, b.image.height), max(a.image.height, b.image.height)
    heights = []
    for k in range(1, slots - 1):
        t = k / (slots - 1)
        heights.append(min(max(figures[k].height * (k_a + (k_b - k_a) * t), lo), hi))
    report: dict[str, Any] = {
        "kind": "sprite-gen-tween", "method": method, "provider": provider, "n": n, "anchor": anchor,
        "from": a.source, "to": b.source, "slot": [lay.slot_w, lay.slot_h], "aspect": lay.aspect,
        "endpoint_match": {"iou_from": round(iou_a, 3), "iou_to": round(iou_b, 3), "min": min_endpoint_iou,
                           "scale_from": round(k_a, 3), "scale_to": round(k_b, 3),
                           "scale_agree": round(scale_agree, 3), "min_scale_agree": MIN_SCALE_AGREE},
        "heights": [round(h, 1) for h in heights], "keyframe_heights": [a.image.height, b.image.height],
        "warnings": warnings,
    }
    (workdir / "tween.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    if min(iou_a, iou_b) < min_endpoint_iou or scale_agree < MIN_SCALE_AGREE:
        raise SystemExit(
            f"tween: refused — drawn endpoints do not match the keyframes (IoU from {iou_a:.2f}, "
            f"to {iou_b:.2f}, need >= {min_endpoint_iou}; end scales agree {scale_agree:.2f}, need >= "
            f"{MIN_SCALE_AGREE}); the strip drifted. Raw kept in {workdir}. "
            "Re-run, lower --n, or try --method video / another --provider explicitly.")
    figs = [_scaled(figures[k], h) for k, h in zip(range(1, slots - 1), heights)]
    # Grow the output canvas (same for every frame) when an in-between is drawn wider or taller
    # than the slot, so nothing is clipped; the foot anchor keeps its distance from the bottom.
    # The output canvas is the union of A, B and every in-between placed on one foot anchor,
    # plus OUT_PAD on every side, so no frame of the set ever touches or leaves its canvas.
    every = [a.image, *figs, b.image]
    left = max(round(foot_x(f, anchor)) for f in every) + OUT_PAD
    right = max(f.width - round(foot_x(f, anchor)) for f in every) + OUT_PAD
    top = max(f.height for f in every) + OUT_PAD
    out_lay = Layout(left + right, top + OUT_PAD, top, left, lay.heights, lay.aspect)
    outs = []
    for fig in figs:
        canvas = Image.new("RGBA", (out_lay.slot_w, out_lay.slot_h), (0, 0, 0, 0))
        place(canvas, fig, 0, out_lay, anchor)
        outs.append(canvas)
    lay = out_lay
    ends = []
    for fig in (a.image, b.image):
        canvas = Image.new("RGBA", (out_lay.slot_w, out_lay.slot_h), (0, 0, 0, 0))
        place(canvas, fig, 0, out_lay, anchor)
        ends.append(canvas)
    report["canvas"] = [out_lay.slot_w, out_lay.slot_h]
    report["anchor_px"] = [out_lay.foot, out_lay.ground]
    report["_ends"] = ends
    chain = [a.image] + [_tight(o) for o in outs] + [b.image]
    steps = [round(silhouette_iou(x, y, anchor), 3) for x, y in zip(chain, chain[1:])]
    report["step_iou"] = steps
    if min(steps) < STEP_IOU_WARN:
        warnings.append(f"pose jump: silhouette IoU between consecutive frames {steps} "
                        f"(lowest under {STEP_IOU_WARN}); check for poses that are in neither keyframe")
    report["foot_error_px"] = [round(abs(foot_x(_tight(o), anchor) + _tight_box(o)[0] - lay.foot), 1) for o in outs]
    (workdir / "tween.json").write_text(json.dumps({k: v for k, v in report.items() if k != "_ends"}, indent=2), encoding="utf-8")
    for w in warnings:
        print(f"[tween] warning: {w}")
    return outs, report


def _tight_box(rgba: Image.Image) -> tuple[int, int, int, int]:
    return Image.fromarray(((_alpha(rgba) >= ALPHA_MIN) * 255).astype("uint8")).getbbox()


def record_transition_take(run_dir: Path, request: dict[str, Any], label: str, frames: list[Image.Image],
                           meta: dict[str, Any]) -> Path:
    """Write the frames as one take strip on the destination state with `transition` metadata (idempotent)."""
    from sprite_gen.spec.layout import take_raw_rel
    from sprite_gen.spec.runio import load_request, publish_guard, write_request
    state = meta["to_state"]
    chroma_rgb = tuple(request["chroma_key"]["rgb"])
    w, h = frames[0].size
    gap = max(8, w // 8)
    strip = Image.new("RGBA", (len(frames) * (w + gap) + gap, h), chroma_rgb + (255,))
    for k, f in enumerate(frames):
        strip.alpha_composite(f, (gap + k * (w + gap), 0))
    target = run_dir / take_raw_rel(request, state, label)
    target.parent.mkdir(parents=True, exist_ok=True)
    strip.convert("RGB").save(target)
    with publish_guard(run_dir):
        fresh = load_request(run_dir)
        takes = fresh["states"][state].setdefault("takes", [])
        entry = next((t for t in takes if t.get("label") == label), None)
        if entry is None:
            entry = {"label": label}
            takes.append(entry)
        entry["frames"] = len(frames)
        entry["transition"] = meta
        write_request(run_dir, fresh)
    return target


def _safe_label(label: str) -> str:
    if not label or "/" in label or label.startswith("."):
        raise SystemExit(f"tween: label must be filesystem-safe: {label!r}")
    return label


def _cell(value: str | None) -> tuple[int, int] | None:
    if value is None:
        return None
    try:
        w, h = (int(v) for v in value.lower().split("x"))
        return w, h
    except ValueError:
        raise SystemExit(f"tween: --cell must be WxH, got {value!r}") from None


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("run_dir", nargs="?", type=Path, default=None, help="run directory (with --from/--to)")
    parser.add_argument("--from", dest="from_ref", help="<state>:<index> in the run (negative index allowed)")
    parser.add_argument("--to", dest="to_ref", help="<state>:<index> in the run")
    parser.add_argument("--from-file", help="frame PNG, or SHEET.png:<index> with --cell")
    parser.add_argument("--to-file", help="frame PNG, or SHEET.png:<index> with --cell")
    parser.add_argument("--cell", help="cell size WxH when slicing a sheet")
    parser.add_argument("--out-dir", type=Path, help="file mode: where frames and tween.json go")
    parser.add_argument("--n", type=int, default=2, help="in-betweens to draw (1..6, default 2)")
    parser.add_argument("--method", choices=METHODS, default="strip")
    parser.add_argument("--provider", choices=PROVIDERS, default="grok")
    parser.add_argument("--anchor", choices=ANCHORS, default="foot")
    parser.add_argument("--margin", type=float, default=DEFAULT_MARGIN, help="safe margin per slot (fraction)")
    parser.add_argument("--min-endpoint-iou", type=float, default=DEFAULT_MIN_ENDPOINT_IOU)
    parser.add_argument("--description", help="character description for the prompt (run mode reads the request)")
    parser.add_argument("--chroma", default="magenta:#FF00FF", help="file mode background name:#hex")
    parser.add_argument("--ref", action="append", type=Path, default=[],
                        help="extra identity reference image for the strip method (repeatable)")
    parser.add_argument("--label", help="take label (default <from>_to_<to>)")


def _run(args: argparse.Namespace) -> int:
    utf8_streams()
    run_mode = args.from_ref is not None or args.to_ref is not None
    file_mode = args.from_file is not None or args.to_file is not None
    if run_mode == file_mode:
        raise SystemExit("tween: give either RUN_DIR --from --to, or --from-file --to-file --out-dir")
    if run_mode:
        if not (args.run_dir and args.from_ref and args.to_ref):
            raise SystemExit("tween: run mode needs RUN_DIR, --from and --to")
        from sprite_gen.spec.runio import load_request
        request = load_request(args.run_dir)
        (fs, fi), (ts, ti) = parse_ref(args.from_ref), parse_ref(args.to_ref)
        a = endpoint_from_run(args.run_dir, request, fs, fi)
        b = endpoint_from_run(args.run_dir, request, ts, ti)
        ck = request["chroma_key"]
        chroma = (ck["name"], ck["hex"], tuple(ck["rgb"]))
        desc = args.description or request.get("character", {}).get("description")
        label = _safe_label(args.label or f"{fs.replace('/', '-')}_to_{ts.replace('/', '-')}")
        work = args.run_dir / "tween" / label
        frames, report = make_tween(a, b, args.n, workdir=work, method=args.method, provider=args.provider,
                                    anchor=args.anchor, margin=args.margin,
                                    min_endpoint_iou=args.min_endpoint_iou, description=desc, chroma=chroma,
                                    extra_refs=args.ref)
        report.pop("_ends", None)
        meta = {"from_state": fs, "from_index": fi, "to_state": ts, "to_index": ti, "n": args.n,
                "method": args.method, "provider": args.provider, "endpoint_match": report["endpoint_match"]}
        target = record_transition_take(args.run_dir, request, label, frames, meta)
        print(f"[tween] transition take written: {target} ({fs}:{fi} -> {ts}:{ti}, n={args.n})")
        return 0
    if not (args.from_file and args.to_file and args.out_dir):
        raise SystemExit("tween: file mode needs --from-file, --to-file and --out-dir")
    name, _, hexv = args.chroma.partition(":")
    rgb = tuple(int(hexv.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    cell = _cell(args.cell)
    a = endpoint_from_file(args.from_file, cell, rgb)
    b = endpoint_from_file(args.to_file, cell, rgb)
    frames, report = make_tween(a, b, args.n, workdir=args.out_dir, method=args.method, provider=args.provider,
                                anchor=args.anchor, margin=args.margin, min_endpoint_iou=args.min_endpoint_iou,
                                description=args.description, chroma=(name, hexv, rgb), extra_refs=args.ref)
    ends = report.pop("_ends")
    ends[0].save(args.out_dir / "from.png")
    ends[1].save(args.out_dir / "to.png")
    for k, f in enumerate(frames):
        f.save(args.out_dir / f"tween-{k}.png")
    report["frames"] = [f"tween-{k}.png" for k in range(len(frames))]
    report["transition"] = {"from_state": args.from_file, "to_state": args.to_file, "n": args.n}
    (args.out_dir / "tween.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"[tween] {len(frames)} frames written to {args.out_dir}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sprite-gen tween", description=__doc__.splitlines()[0])
    add_arguments(parser)
    return parser


def run(**kwargs: object) -> int:
    return _run(namespace_from_kwargs(_build_parser(), kwargs))


def main(argv: list[str] | None = None) -> int:
    utf8_streams()
    return _run(_build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
