# SPDX-License-Identifier: Apache-2.0
"""Judge a base still as pixel art before anything is filmed or layered from it, and say how to fix it.

A clip, a loop and every layer drawn over it inherit the still's grid, palette and noise.
The audit reads one image and reports four things: the pixel scale (upscaled pixel art,
native pixel art, or soft art that is neither), cleanliness (soft alpha, specks), the
palette as per-material ramps (flat 1-2 tone shading or a noisy cloud of colors), and
the outline. Every finding carries a concrete remedy. `--fix` writes the snapped still
(own grid, ramp-preserving palette, closed outline) and an integer-upscaled copy to film.
It judges craft, not design.
"""
from __future__ import annotations

import argparse
import colorsys
import json
from pathlib import Path

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.frames import extract, pixel_grid

AUDIT_POLICY = "still-audit.v2"
# Measured 2026-10-05/06 on judged stills: raw image-model "pixel art" (16k-28k colors,
# 9.1 x 10.1 px blocks, grid residual 0.25-0.48, soft alpha on every edge) against the
# same art snapped to its own grid (residual 0, 4-16 colors) and native fixtures.
LIMITS = {"grid_residual": 0.15, "soft_alpha_share": 0.05, "speck_share": 0.002, "pitch_anisotropy": 0.08,
          "native_colors": 64, "outline_share": 0.75, "ramp_steps": 3}
OUTLINE_LUMA = 60
MAJOR_MATERIAL = 0.08


def _components(mask):
    """8-connected component sizes of a boolean mask, largest first."""
    height, width = mask.shape
    seen = np.zeros_like(mask)
    sizes = []
    for y0, x0 in zip(*np.nonzero(mask)):
        if seen[y0, x0]:
            continue
        stack, count = [(y0, x0)], 0
        seen[y0, x0] = True
        while stack:
            y, x = stack.pop()
            count += 1
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    ny, nx = y+dy, x+dx
                    if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
        sizes.append(count)
    return sorted(sizes, reverse=True)


def _large_components(mask, minimum):
    """Mask of the 8-connected components of `mask` with at least `minimum` pixels."""
    height, width = mask.shape
    seen = np.zeros_like(mask)
    keep = np.zeros_like(mask)
    for y0, x0 in zip(*np.nonzero(mask)):
        if seen[y0, x0]:
            continue
        stack, island = [(y0, x0)], []
        seen[y0, x0] = True
        while stack:
            y, x = stack.pop()
            island.append((y, x))
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    ny, nx = y+dy, x+dx
                    if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
        if len(island) >= minimum:
            ys, xs = zip(*island)
            keep[list(ys), list(xs)] = True
    return keep


def grid_fit(image, pitch, *, phases=8):
    """Best square grid at `pitch`: its phase and the in-cell color spread left over.

    The residual is the opaque pixels' RMS distance from their cell's mean color over the
    RMS distance from the subject's mean color: near 0 when cells are flat blocks.
    """
    rgba = np.asarray(image.convert("RGBA")).astype(np.float64)
    weight = (rgba[..., 3] >= 128).astype(np.float64).ravel()
    ys, xs = np.mgrid[0:rgba.shape[0], 0:rgba.shape[1]]
    total_spread = 0.0
    for channel in range(3):
        values = rgba[..., channel].ravel()
        mean = (values*weight).sum()/max(weight.sum(), 1)
        total_spread += ((values-mean)**2*weight).sum()
    best = None
    for i in range(phases):
        for j in range(phases):
            ox, oy = pitch*i/phases, pitch*j/phases
            cx = np.floor((xs-ox)/pitch).astype(np.int64)
            cy = np.floor((ys-oy)/pitch).astype(np.int64)
            cell = ((cy-cy.min())*(cx.max()-cx.min()+1)+(cx-cx.min())).ravel()
            count = np.bincount(cell, weights=weight)
            spread = 0.0
            for channel in range(3):
                values = rgba[..., channel].ravel()
                total = np.bincount(cell, weights=values*weight)
                square = np.bincount(cell, weights=values*values*weight)
                spread += float((square-np.divide(total**2, count, out=np.zeros_like(total), where=count > 0)).sum())
            if best is None or spread < best[0]:
                best = (spread, ox, oy)
    spread, ox, oy = best
    return {"phase": [round(ox, 2), round(oy, 2)], "residual": round(float(np.sqrt(spread/max(total_spread, 1e-9))), 4)}


def ramps(image):
    """Group the art's colors into materials by hue, and count each material's light steps.

    Outline ink (luma under OUTLINE_LUMA) is left out. Neutral colors (low saturation)
    form one material; adjacent occupied hue bands (30 degrees each) merge, since a
    hue-shifted ramp crosses them. A step is a distinct lightness level (8 %
    buckets) holding at least 1 % of the material. Materials under MAJOR_MATERIAL of the
    art are reported but not judged.
    """
    rgba = np.asarray(image.convert("RGBA"))
    opaque = rgba[..., 3] >= 128
    colors, counts = np.unique(rgba[opaque][:, :3], axis=0, return_counts=True)
    total = counts.sum()
    groups = {}
    for color, count in zip(colors, counts):
        r, g, b = (int(v) for v in color)
        if 0.299*r+0.587*g+0.114*b < OUTLINE_LUMA:
            continue
        h, l, s = colorsys.rgb_to_hls(r/255, g/255, b/255)
        key = ("neutral", 0) if s < 0.15 or l > 0.92 else ("hue", int(round(h*12)) % 12)
        groups.setdefault(key, []).append((l, count, (r, g, b)))
    # A hue-shifted ramp (warm shadows, cool highlights) crosses neighbouring hue bands:
    # adjacent occupied bands form one material.
    hues = sorted(band for kind, band in groups if kind == "hue")
    merged, seen = [], set()
    for band in hues:
        if band in seen:
            continue
        run, k = [], band
        while ("hue", k % 12) in groups and k % 12 not in seen:
            seen.add(k % 12)
            run.append(k % 12)
            k += 1
        k = band-1
        while ("hue", k % 12) in groups and k % 12 not in seen:
            seen.add(k % 12)
            run.insert(0, k % 12)
            k -= 1
        merged.append(("hue", run))
    if ("neutral", 0) in groups:
        merged.append(("neutral", [0]))
    out = []
    for kind, bands in merged:
        items = [item for band in bands for item in groups[(kind, band)]]
        share = sum(c for _, c, _ in items)/total
        levels = {}
        for l, count, _ in items:
            levels[int(l*12.5)] = levels.get(int(l*12.5), 0)+count
        material = sum(levels.values())
        steps = sum(1 for v in levels.values() if v >= 0.01*material)
        anchor = max(items, key=lambda item: item[1])[2]
        name = f"{kind}-{bands[0]}" if len(bands) == 1 else f"{kind}-{bands[0]}..{bands[-1]}"
        out.append({"material": name, "sample_rgb": list(anchor), "share": round(float(share), 4),
                    "colors": len(items), "steps": steps, "major": bool(share >= MAJOR_MATERIAL)})
    return sorted(out, key=lambda m: -m["share"])


def outline_share(image):
    """Share of the silhouette's outer boundary drawn in dark outline ink."""
    rgba = np.asarray(image.convert("RGBA"))
    opaque = rgba[..., 3] >= 128
    padded = np.pad(opaque, 1)
    edge = opaque & ~(padded[:-2, 1:-1] & padded[2:, 1:-1] & padded[1:-1, :-2] & padded[1:-1, 2:])
    if not edge.any():
        return None
    luma = rgba[..., :3].astype(np.float64) @ np.array([0.299, 0.587, 0.114])
    return round(float((luma[edge] < OUTLINE_LUMA).mean()), 4)


def _native_art(image, pitch):
    if pitch is None or pitch < 1.5:
        return image.convert("RGBA")
    rgba = np.asarray(image.convert("RGBA")).copy()
    rgba[rgba[..., 3] < 128] = 0
    art, _ = pixel_grid.lattice_reduce(Image.fromarray(rgba), pitch)
    return art.crop(art.getbbox())


def audit(image, *, target_height=None):
    image = Image.open(image) if isinstance(image, (str, Path)) else image
    rgba = np.asarray(image.convert("RGBA"))
    alpha = rgba[..., 3]
    opaque = alpha >= 128
    if not opaque.any():
        raise ValueError("the still has no opaque subject")
    soft = (alpha > 0) & (alpha < 255)
    components = _components(alpha > 0)
    specks = sum(components[1:])
    colors, counts = np.unique(rgba[opaque][:, :3], axis=0, return_counts=True)
    ranked = np.cumsum(np.sort(counts)[::-1])
    box = Image.fromarray(opaque.astype(np.uint8)*255).getbbox()
    subject = image.convert("RGBA").crop(box)
    px, py = extract.estimate_pixel_grid_runlen(subject)
    height = box[3]-box[1]
    candidates = [v for v in (px, py) if v > 1.5]
    guess = height/target_height if target_height else (float(np.mean(candidates)) if candidates else None)
    fit = None if guess is None or guess < 1.5 else grid_fit(subject, guess)
    few_colors = int(np.searchsorted(ranked, 0.9*ranked[-1])+1) <= LIMITS["native_colors"]
    clean_alpha = soft.sum() <= LIMITS["soft_alpha_share"]*opaque.sum()
    if fit is not None and fit["residual"] <= LIMITS["grid_residual"]:
        scale, pitch = "upscaled", guess
    elif few_colors and clean_alpha:
        # Hard edges and a small palette at 1:1: flat runs fooled the pitch estimate.
        scale, pitch = "native", 1.0
    else:
        scale, pitch = "soft", guess
    art = _native_art(subject, pitch if scale != "native" else None)
    art_colors = np.unique(np.asarray(art)[np.asarray(art)[..., 3] >= 128][:, :3], axis=0)
    report = {
        "kind": "still-audit", "policy": AUDIT_POLICY, "size": list(image.size), "subject_box": list(box),
        "scale": scale, "pitch": None if pitch is None else round(pitch, 3),
        "pitch_runlen": [round(px, 3), round(py, 3)],
        "pitch_anisotropy": None if len(candidates) < 2 else round(abs(px-py)/((px+py)/2), 4),
        "grid": fit, "native_size": list(art.size),
        "soft_alpha_share": round(float(soft.sum()/opaque.sum()), 4), "specks": len(components)-1,
        "speck_share": round(specks/max(int((alpha > 0).sum()), 1), 5),
        "colors": int(len(colors)), "colors_for_90pct": int(np.searchsorted(ranked, 0.9*ranked[-1])+1),
        "native_colors": int(len(art_colors)), "outline_share": outline_share(art), "materials": ramps(art),
    }
    report["checks"], report["advice"] = _judge(report, target_height)
    report["status"] = "warning" if any(c["status"] == "warning" for c in report["checks"]) else "passed"
    return report


def _judge(report, target_height):
    checks, advice = [], []

    def check(key, value, ok, limit, remedy):
        status = "unverified" if value is None else "passed" if ok else "warning"
        checks.append({"id": key, "value": value, "limit": limit, "status": status})
        if status == "warning":
            advice.append({"for": key, "do": remedy})

    height_hint = f" --target-height {target_height}" if target_height else ""
    check("pixel_scale", report["scale"], report["scale"] != "soft", "upscaled or native",
          "not pixel art on one grid: run still-audit --fix" + height_hint + " to snap it to its own grid and "
          "palette, or regenerate asking for square pixels on one grid (each art pixel an N x N block)")
    check("soft_alpha_share", report["soft_alpha_share"], report["soft_alpha_share"] <= LIMITS["soft_alpha_share"],
          LIMITS["soft_alpha_share"], "binarize alpha (--fix does), or generate on a flat key color instead of soft transparency")
    check("speck_share", report["speck_share"], report["speck_share"] <= LIMITS["speck_share"], LIMITS["speck_share"],
          "remove stray islands around the subject (--fix keeps only the subject)")
    anisotropy = report["pitch_anisotropy"]
    check("pitch_anisotropy", anisotropy if report["scale"] == "upscaled" else None,
          anisotropy is None or anisotropy <= LIMITS["pitch_anisotropy"], LIMITS["pitch_anisotropy"],
          "blocks are not square: regenerate, or resample by the measured per-axis pitch before snapping")
    check("native_colors", report["native_colors"], report["native_colors"] <= LIMITS["native_colors"],
          LIMITS["native_colors"], "the colors are a noise cloud: snap to a palette of 24-40 colors built as ramps (--fix)")
    flat = [m["material"] for m in report["materials"] if m["major"] and m["steps"] < LIMITS["ramp_steps"]]
    check("ramp_steps", None if not report["materials"] else (len(flat) == 0 and "ok" or flat), not flat,
          f">= {LIMITS['ramp_steps']} light steps per major material",
          "flat shading: regenerate asking for 3-5 tone ramps per material (hue-shifted shadows, one highlight, "
          "a single light direction); a smaller palette cannot add steps that were never drawn")
    check("outline_share", report["outline_share"], report["outline_share"] is None or report["outline_share"] >= LIMITS["outline_share"],
          LIMITS["outline_share"], "the outer outline is broken: close it (--fix uses --silhouette-outline darkest), or ask for a "
          "continuous 1-pixel outline")
    return checks, advice


def fix(image, out, *, target_height=None, colors=32, upscale=6):
    """Snap a still to its own grid, a ramp-keeping palette and a closed outline; write native + filmable copies."""
    still = Image.open(image) if isinstance(image, (str, Path)) else image
    report = audit(still, target_height=target_height)
    rgba = np.asarray(still.convert("RGBA")).copy()
    rgba[rgba[..., 3] < 128] = 0
    mask = rgba[..., 3] > 0
    sizes = _components(mask)
    if len(sizes) > 1:
        # Keep the subject: drop islands smaller than half a percent of it.
        rgba[~_large_components(mask, max(2, int(sizes[0]*0.005)))] = 0
    subject = Image.fromarray(rgba)
    box = subject.getbbox()
    pitch = report["pitch"]
    if report["scale"] != "native" and pitch is not None:
        guess = (box[3]-box[1])/target_height if target_height else pitch
        pitch = pixel_grid.find_pitch([subject], guess, spread=0.06, steps=25)
        art, _ = pixel_grid.lattice_reduce(subject, pitch)
    else:
        art = subject
    art = art.crop(art.getbbox())
    palette = extract.build_shared_palette([art], colors)
    darkest = min(palette, key=lambda c: 299*c[0]+587*c[1]+114*c[2])
    art = pixel_grid.silhouette_outline(pixel_grid.snap(art, np.array(palette)), [int(v) for v in darkest])
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    art.save(out)
    film = out.with_name(out.stem + f".x{upscale}.png")
    art.resize((art.width*upscale, art.height*upscale), Image.Resampling.NEAREST).save(film)
    return {"native": str(out), "filmable": str(film), "pitch": None if pitch is None else round(pitch, 3),
            "native_size": list(art.size), "palette": len(palette), "before": report["status"],
            "after": audit(film)["status"]}


def noise_map(image, pitch, phase, out):
    """Three panels: any-alpha coverage, the subject, and each pixel's distance from its grid cell's median."""
    rgba = np.asarray(image.convert("RGBA")).astype(np.int16)
    coverage = Image.fromarray(((rgba[..., 3] > 0)*255).astype(np.uint8)).convert("RGB")
    ys, xs = np.mgrid[0:rgba.shape[0], 0:rgba.shape[1]]
    cell = (np.floor((ys-phase[1])/pitch)*10**6+np.floor((xs-phase[0])/pitch)).astype(np.int64).ravel()
    flat = rgba[..., :3].reshape(-1, 3)
    order = np.argsort(cell, kind="stable")
    median = np.zeros_like(flat)
    bounds = np.flatnonzero(np.diff(cell[order]))+1
    for group in np.split(order, bounds):
        median[group] = np.median(flat[group], axis=0)
    deviation = np.clip(128+(flat-median)*4, 0, 255).astype(np.uint8).reshape(rgba.shape[0], rgba.shape[1], 3)
    subject = Image.new("RGBA", image.size, (40, 40, 40, 255))
    subject.alpha_composite(image.convert("RGBA"))
    panels = [coverage, subject.convert("RGB"), Image.fromarray(deviation)]
    sheet = Image.new("RGB", (image.width*3+20, image.height), (255, 255, 255))
    for n, panel in enumerate(panels):
        sheet.paste(panel, (n*(image.width+10), 0))
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)


def add_arguments(parser: argparse.ArgumentParser):
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--target-height", type=int,
                        help="intended native height in art pixels; the grid is fitted at subject height / this")
    parser.add_argument("--noise-map", type=Path, help="optional PNG: coverage, subject, deviation from the grid cell")
    parser.add_argument("--fix", type=Path, help="write the snapped native still here (and an upscaled .xN copy to film)")
    parser.add_argument("--colors", type=int, default=32, help="--fix palette size (default 32; keep ramps, not a noise cloud)")
    parser.add_argument("--upscale", type=int, default=6, help="--fix integer upscale of the filmable copy (default 6)")


def run(*, image, target_height=None, noise_map=None, fix=None, colors=32, upscale=6):
    try:
        report = audit(image, target_height=target_height)
        if noise_map is not None and report["grid"] is not None and report["pitch"]:
            still = Image.open(image)
            globals()["noise_map"](still.convert("RGBA").crop(report["subject_box"]), report["pitch"],
                                   report["grid"]["phase"], noise_map)
            report["noise_map"] = str(noise_map)
        if fix is not None:
            report["fix"] = globals()["fix"](image, fix, target_height=target_height, colors=colors, upscale=upscale)
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    print(json.dumps({"ok": True, **report}, indent=2))
    return 0
