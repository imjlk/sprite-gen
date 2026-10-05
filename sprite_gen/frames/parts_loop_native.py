# SPDX-License-Identifier: Apache-2.0
"""Bring finished video loops onto a parts rig canvas as native pixel frames.

`video-loop` cuts a cycle at the clip's own resolution. A parts rig needs every
state on one small canvas with one body size and one ground line. This step
applies one projection per loop (never per frame, so the gait's own bob and
sway survive), binarizes alpha, optionally snaps colors to an explicit
palette, and merges consecutive drawings a clip held on twos into one longer
sample. It writes `sprite-gen-asset` descriptors and a `parts-motion-import`
spec; importing, socket annotation and approval stay separate steps.
"""
from __future__ import annotations

import argparse
from fractions import Fraction
import json
import math
from pathlib import Path

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.curate import parts_motion
from sprite_gen.frames import extract, pixel_grid
from sprite_gen.frames.pixel_grid import (lattice_reduce, silhouette_outline, snap,
                                          find_pitch as loop_pitch)
from sprite_gen.spec import parts_assets as assets, parts_contract as contract, parts_workspace as workspace
from sprite_gen.spec.runio import atomic_write_text
from sprite_gen.util.resample import resize_cell

NATIVE_POLICY = "parts.loop-native.v1"


def _loop(value):
    state, sep, rest = value.partition("=")
    folder, at, fps = rest.rpartition("@") if "@" in rest else (rest, "", None)
    if not sep or not state or not folder or (at and not fps):
        raise argparse.ArgumentTypeError("expected STATE=LOOP_DIR or STATE=LOOP_DIR@FPS")
    return (state, Path(folder)) if fps is None else (state, Path(folder), fps)


def _every(value):
    state, sep, count = value.partition("=")
    if not sep or not state or not count.isdigit():
        raise argparse.ArgumentTypeError("expected STATE=N")
    return state, int(count)


def _frames(folder):
    cycle = folder / "cycle" if (folder / "cycle").is_dir() else folder
    files = sorted(cycle.glob("*.png"))
    if not files:
        raise contract.PartsContractError(f"{folder}: no frames")
    return files, [Image.open(path).convert("RGBA") for path in files]


def _hex(value):
    value = value.strip().lstrip("#")
    if len(value) != 6:
        raise contract.PartsContractError("outline color must be #RRGGBB or darkest")
    return [int(value[i:i+2], 16) for i in (0, 2, 4)]


def _difference(a, b, threshold=24):
    a, b = np.asarray(a).astype(np.int16), np.asarray(b).astype(np.int16)
    oa, ob = a[..., 3] > 0, b[..., 3] > 0
    rgb = np.max(np.abs(a[..., :3]-b[..., :3]), axis=-1) > threshold
    return int(np.count_nonzero(oa ^ ob) + np.count_nonzero(rgb & oa & ob)), int(max(np.count_nonzero(oa), 1))


def project(frames, *, canvas, body_height, baseline, center_x, resample="kcentroid"):
    """One scale and one offset for the whole loop, from its tallest pose and lowest foot."""
    boxes = [f.getchannel("A").point(lambda a: 255 if a >= 128 else 0).getbbox() for f in frames]
    if any(box is None for box in boxes):
        raise contract.PartsContractError("a loop frame has no opaque body")
    tallest = max(box[3]-box[1] for box in boxes)
    pitch = loop_pitch(frames, tallest/body_height) if resample == "lattice" and tallest/body_height >= 1.5 else None
    if resample == "lattice" and pitch is None:
        # Already native-sized pixel art: one source pixel is one native pixel.
        scale, size, scaled = Fraction(1), frames[0].size, [frame.copy() for frame in frames]
    elif pitch is not None:
        # Keep the art's own pixels: one drawn cell is one native pixel, so a one-cell
        # outline stays one pixel instead of splitting across two resampled pixels.
        reduced = [lattice_reduce(frame, pitch) for frame in frames]
        width = max(im.width+dx for im, (dx, _) in reduced)
        height = max(im.height+dy for im, (_, dy) in reduced)
        scaled = []
        for im, (dx, dy) in reduced:
            sheet = Image.new("RGBA", (width, height))
            sheet.alpha_composite(im, (dx, dy))
            scaled.append(sheet)
        scale = Fraction(1)/Fraction(pitch).limit_denominator(1000)
        size = (width, height)
    elif resample == "kcentroid":
        scale = Fraction(body_height, tallest)
        size = (max(1, round(frames[0].width*scale)), max(1, round(frames[0].height*scale)))
        # Each native pixel takes its block's dominant color cluster: thin dark outlines
        # survive instead of being averaged into the fill (extract._kcentroid_downscale).
        scaled = [extract._kcentroid_downscale(frame, *size) for frame in frames]
    elif resample == "smooth":
        scale = Fraction(body_height, tallest)
        size = (max(1, round(frames[0].width*scale)), max(1, round(frames[0].height*scale)))
        scaled = [resize_cell(frame, size) for frame in frames]
    else:
        raise contract.PartsContractError("resample must be lattice, kcentroid or smooth")
    alphas = [np.asarray(s)[..., 3] >= 128 for s in scaled]
    bottom = max(int(np.where(a.any(axis=1))[0].max()) for a in alphas)
    mass = sum(a.sum(axis=0) for a in alphas)
    centroid = float((mass*np.arange(mass.size)).sum()/max(mass.sum(), 1))
    offset = (round(center_x-centroid), baseline-bottom)
    placed = []
    for image, alpha in zip(scaled, alphas):
        ys, xs = np.where(alpha)
        if xs.min()+offset[0] < 0 or ys.min()+offset[1] < 0 or xs.max()+offset[0] >= canvas[0] or ys.max()+offset[1] >= canvas[1]:
            raise contract.PartsContractError("projected loop leaves the rig canvas; lower --body-height or move the anchor")
        native = Image.new("RGBA", tuple(canvas))
        native.alpha_composite(image, offset)
        placed.append(native)
    native_height = max(int(np.where(a.any(axis=1))[0].max()-np.where(a.any(axis=1))[0].min()+1) for a in alphas)
    return placed, {"scale": str(scale), "scaled_size": list(size), "offset": list(offset), "resample": resample,
                    "pitch": None if pitch is None else round(pitch, 3), "native_body_height": native_height,
                    "tallest_source_px": tallest, "source_size": list(frames[0].size)}


def reported_hold(folder, count):
    """The hold `video-loop` measured on this cycle (cycle_drawings), when its report sits beside the loop."""
    folder = Path(folder)
    for path in (folder / "loop.report.json", folder.parent / "loop.report.json", *folder.glob("*.loop.report.json")):
        if path.is_file():
            drawings = (workspace.decode_json(path.read_bytes()).get("cycle_drawings") or {})
            hold = drawings.get("hold")
            if type(hold) is int and hold > 1 and drawings.get("length") == count and count % hold == 0:
                return hold, path.name
    return None, None


def group_by_hold(frames, hold):
    """Fixed groups of `hold` frames, at the phase whose groups differ least inside."""
    n = len(frames)
    best = None
    for phase in range(hold):
        groups = [[(phase+k*hold+j) % n for j in range(hold)] for k in range(n//hold)]
        cost = sum(_difference(frames[g[0]], frames[i])[0] for g in groups for i in g[1:])
        if best is None or cost < best[0]:
            best = (cost, groups)
    return best[1]


def merge_holds(frames, tolerance):
    """Consecutive drawings that differ by at most `tolerance` of the body become one sample."""
    groups = [[0]]
    for n in range(1, len(frames)):
        changed, area = _difference(frames[groups[-1][0]], frames[n])
        if changed <= tolerance*area:
            groups[-1].append(n)
        else:
            groups.append([n])
    return groups


def build(assembly, out, loops, *, body_height, baseline, center_x=None, palette=None, fps="24",
          hold_tolerance=0.0, motion_id="parts.video.motion", every=(), resample="lattice", outline=None):
    root, index, registry, _ = parts_motion._active(assembly)
    rig = registry[index["rig"]["id"]]
    canvas = [rig["canvas"]["width"], rig["canvas"]["height"]]
    center_x = canvas[0]//2 if center_x is None else center_x
    if not 1 <= body_height <= canvas[1] or not 0 < baseline < canvas[1] or not 0 <= hold_tolerance < 1:
        raise contract.PartsContractError("body height, baseline or hold tolerance is outside the rig canvas")
    rate = Fraction(fps)
    if rate <= 0:
        raise contract.PartsContractError("fps must be positive")
    loops = [tuple(loop) if len(loop) == 3 else (*loop, None) for loop in loops]
    if len({loop[0] for loop in loops}) != len(loops):
        raise contract.PartsContractError("each state takes one loop")
    try:
        colors = pixel_grid.read_palette(palette)
    except pixel_grid.GridError as exc:
        raise contract.PartsContractError(str(exc)) from exc
    if outline == "darkest":
        if colors is None:
            raise contract.PartsContractError("--silhouette-outline darkest needs --palette")
        outline = [int(v) for v in min(colors.tolist(), key=lambda c: 299*c[0]+587*c[1]+114*c[2])]
    elif outline is not None:
        outline = _hex(outline)
    strides = dict(every)
    if any(type(n) is not int or n < 1 for n in strides.values()) or set(strides) - {loop[0] for loop in loops}:
        raise contract.PartsContractError("--every takes STATE=N for a listed state, N >= 1")
    out = Path(out).absolute()
    clips, report = [], []
    for state, folder, own_fps in loops:
        loop_rate = rate if own_fps is None else Fraction(own_fps)
        if loop_rate <= 0:
            raise contract.PartsContractError(f"{state}: fps must be positive")
        files, frames = _frames(Path(folder))
        placed, projection = project(frames, canvas=canvas, body_height=body_height, baseline=baseline,
                                     center_x=center_x, resample=resample)
        snapped = [snap(image, colors) for image in placed]
        if outline is not None:
            snapped = [silhouette_outline(image, outline) for image in snapped]
        hold, hold_source = reported_hold(folder, len(frames))
        stride = strides.get(state, 1)
        if stride > 1:
            # Fewer drawings, each shown `stride` frames: the cycle's length in time is unchanged.
            if hold or len(frames) % stride:
                raise contract.PartsContractError(f"{state}: --every needs an unheld cycle whose length it divides")
            groups = [list(range(n, n+stride)) for n in range(0, len(frames), stride)]
            hold_source = f"every {stride} frames"
        else:
            groups = group_by_hold(snapped, hold) if hold else merge_holds(snapped, hold_tolerance)
        target = out / state
        target.mkdir(parents=True, exist_ok=True)
        entries, expected = [], {}
        for n, group in enumerate(groups):
            filename = f"{n:03d}.png"
            data = assets.png_bytes(snapped[group[0]])
            (target/filename).write_bytes(data)
            expected[f"{state}/{filename}"] = assets.sha256(data)
            # Exact decimal text for n/fps is not always finite; the import rounds boundaries.
            entries.append({"id": f"{state}.cycle.{group[0]}", "file": filename,
                            "duration": float(Fraction(len(group))/loop_rate)})
        descriptor = {"kind": "sprite-gen-asset", "version": 1, "loop": True, "frames": entries}
        data = workspace.json_bytes(descriptor)
        (target/"motion.json").write_bytes(data)
        expected[f"{state}/motion.json"] = assets.sha256(data)
        clips.append({"id": f"{state}.{rig['view']}", "state": state, "file": f"{state}/motion.json", "source_state": None,
                      "timebase": 120000, "loop": True, "fps": None, "timing_policy": "round-boundaries",
                      "source_mode": "external", "expected_files": expected})
        report.append({"state": state, "loop": str(folder), "fps": str(loop_rate), "source_frames": len(files), "samples": len(groups),
                       "holds": [len(g) for g in groups], "hold_source": hold_source or "frame difference",
                       "first_source_frame": groups[0][0], "projection": projection})
    spec = {"kind": "parts-motion-import", "schema_version": 1, "id": motion_id, "rig": index["rig"], "clips": clips}
    atomic_write_text(out/"motion-import.json", workspace.json_bytes(spec).decode("utf-8"))
    return {"policy": NATIVE_POLICY, "spec": str(out/"motion-import.json"), "source_root": str(out),
            "canvas": canvas, "body_height": body_height, "baseline": baseline, "center_x": center_x,
            "palette": None if colors is None else len(colors), "clips": report, "status": "draft", "release_ready": False}


def add_arguments(parser: argparse.ArgumentParser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--loop", required=True, action="append", type=_loop, dest="loops",
                        help="STATE=DIR[@FPS] of a video-loop output (its cycle/ frames, or a folder of frames); repeatable")
    parser.add_argument("--body-height", required=True, type=int, help="native px of each loop's tallest pose")
    parser.add_argument("--baseline", required=True, type=int, help="rig-canvas row of each loop's lowest foot")
    parser.add_argument("--center-x", type=int, help="rig-canvas column of each loop's mean body centroid (default: center)")
    parser.add_argument("--palette", type=Path, help="JSON list (or {colors: [...]}) of RGB colors to snap to")
    parser.add_argument("--fps", default="24", help="cycle frame rate of loops without @FPS (default 24)")
    parser.add_argument("--hold-tolerance", type=float, default=0.0,
                        help="merge consecutive frames differing by at most this share of the body when the loop report names no hold (default 0: identical only)")
    parser.add_argument("--motion-id", default="parts.video.motion")
    parser.add_argument("--resample", choices=("lattice", "kcentroid", "smooth"), default="lattice",
                        help="lattice (default): sample the art's own pixel grid near tallest/--body-height, one cell "
                             "per native pixel; kcentroid: dominant color of resampled blocks; smooth: mixed")
    parser.add_argument("--silhouette-outline", dest="outline",
                        help="paint the silhouette's outer boundary: #RRGGBB, or darkest (the palette's darkest color)")
    parser.add_argument("--every", action="append", default=[], type=_every,
                        help="STATE=N keep every Nth frame of an unheld cycle, each shown N frames (e.g. walk=2)")


def run(**kwargs):
    return workspace._report(lambda: build(**kwargs))
