# SPDX-License-Identifier: Apache-2.0
"""Pixel-art grid helpers: read art on its own drawn grid, snap to a palette, close the outline.

Image models and video clips return "pixel art" whose blocks are a few source pixels wide,
slightly off any fixed grid and edged with soft color. These helpers find the drawn grid
(pitch and phase), read one native pixel per cell, snap colors to an explicit palette and
close the silhouette's one-pixel contour. They know nothing about rigs or motion.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from PIL import Image

from sprite_gen._deps import np


class GridError(ValueError):
    """Raised when an image cannot be read on a pixel grid."""


def read_palette(path):
    """An explicit palette file: a JSON list of [r, g, b], or {\"colors\": [...]}; None passes through."""
    if path is None:
        return None
    value = json.loads(Path(path).read_bytes())
    colors = value.get("colors") if isinstance(value, dict) else value
    if (not isinstance(colors, list) or not 1 <= len(colors) <= 256
            or any(not isinstance(c, list) or len(c) != 3 or any(type(v) is not int or not 0 <= v <= 255 for v in c) for c in colors)):
        raise GridError("palette requires 1..256 explicit RGB colors")
    return np.array(colors, dtype=np.int32)


def snap(image, palette):
    """Binary alpha; each opaque pixel takes its nearest palette color."""
    rgba = np.asarray(image).copy()
    opaque = rgba[..., 3] >= 128
    rgba[..., 3] = np.where(opaque, 255, 0)
    if palette is not None and opaque.any():
        rgb = rgba[opaque, :3].astype(np.int32)
        nearest = np.argmin(((rgb[:, None, :]-palette[None, :, :])**2).sum(axis=-1), axis=1)
        rgba[opaque, :3] = palette[nearest]
    rgba[~opaque] = 0
    return Image.fromarray(rgba)


def silhouette_outline(image, color):
    """Paint the silhouette's outer boundary (opaque pixels 4-touching transparency) in `color`.

    Pixel-art bodies carry a one-pixel dark contour; a filmed loop draws it thin in places,
    and an unbroken contour is what reads as a clean line at native size.
    """
    a = np.asarray(image).copy()
    opaque = a[..., 3] > 0
    for _ in range(2):
        # One-pixel spurs (at most one opaque 4-neighbour) are soft video edges, not drawing.
        padded = np.pad(opaque, 1)
        neighbours = padded[:-2, 1:-1].astype(int)+padded[2:, 1:-1]+padded[1:-1, :-2]+padded[1:-1, 2:]
        opaque &= neighbours >= 2
    a[~opaque] = 0
    padded = np.pad(opaque, 1)
    edge = opaque & ~(padded[:-2, 1:-1] & padded[2:, 1:-1] & padded[1:-1, :-2] & padded[1:-1, 2:])
    a[edge, :3] = color
    return Image.fromarray(a)


def grid_cost(rgba, xs, ys, weight, pitch, ox, oy):
    """Color variance left inside grid cells: low when the grid matches the drawn pixels."""
    cx = np.floor((xs-ox)/pitch).astype(np.int64)
    cy = np.floor((ys-oy)/pitch).astype(np.int64)
    cell = ((cy-cy.min())*(cx.max()-cx.min()+1) + (cx-cx.min())).ravel()
    w = weight.ravel()
    count = np.bincount(cell, weights=w)
    cost = 0.0
    for channel in range(3):
        values = rgba[..., channel].ravel()
        total = np.bincount(cell, weights=values*w)
        square = np.bincount(cell, weights=values*values*w)
        cost += float((square - np.divide(total**2, count, out=np.zeros_like(total), where=count > 0)).sum())
    return cost


def subject_region(frame, pitch):
    rgba = np.asarray(frame.convert("RGBA")).astype(np.float64)
    box = frame.getbbox()
    if box is None:
        raise GridError("a frame has no opaque subject")
    pad = int(math.ceil(pitch))
    x0, y0 = max(0, box[0]-pad), max(0, box[1]-pad)
    x1, y1 = min(frame.width, box[2]+pad), min(frame.height, box[3]+pad)
    region = rgba[y0:y1, x0:x1]
    ys, xs = np.mgrid[y0:y1, x0:x1]
    return rgba, region, xs, ys, (region[..., 3] >= 128).astype(np.float64), (x0, y0, x1, y1)


def find_pitch(frames, guess, *, spread=0.03, steps=13, phases=8):
    """The drawn pixel size near `guess` (source px per native pixel): least in-cell variance.

    A clip filmed from an upscaled pixel-art still keeps that still's grid; the guess is the
    loop's tallest pose over the authored native body height, refined within +/- spread.
    """
    best = None
    sample = frames[:: max(1, len(frames)//3)][:3]
    for k in range(steps):
        pitch = guess*(1-spread+2*spread*k/(steps-1))
        cost = 0.0
        for frame in sample:
            _, region, xs, ys, weight, _ = subject_region(frame, pitch)
            cost += min(grid_cost(region, xs, ys, weight, pitch, pitch*i/phases, pitch*j/phases)
                        for i in range(phases) for j in range(phases))
        if best is None or cost < best[0]:
            best = (cost, pitch)
    return best[1]


def lattice_reduce(frame, pitch, *, steps=8):
    """Sample a pixel-art frame on its own drawn grid: one native pixel per grid cell.

    The phase is the one whose cells hold the least color variance. Each cell takes its inner
    pixels' median color and majority alpha, so a one-cell outline stays one pixel wide
    instead of splitting across two output pixels. Cell i starts near ox + i * pitch; the
    returned shift places it on the global lattice so frames keep their relative positions.
    """
    rgba, region, xs, ys, weight, (x0, y0, x1, y1) = subject_region(frame, pitch)
    best = None
    for i in range(steps):
        for j in range(steps):
            ox, oy = pitch*i/steps, pitch*j/steps
            cost = grid_cost(region, xs, ys, weight, pitch, ox, oy)
            if best is None or cost < best[0]:
                best = (cost, ox, oy)
    _, ox, oy = best
    cols = int(math.floor((frame.width-ox)/pitch))
    rows = int(math.floor((frame.height-oy)/pitch))
    out = np.zeros((rows, cols, 4), dtype=np.uint8)
    c0, c1 = int(max(0, (x0-ox)//pitch)), int(min(cols, (x1-ox)//pitch+1))
    r0, r1 = int(max(0, (y0-oy)//pitch)), int(min(rows, (y1-oy)//pitch+1))
    inset = pitch/4
    for r in range(r0, r1):
        top, bottom = int(round(oy+r*pitch+inset)), int(round(oy+(r+1)*pitch-inset))
        for c in range(c0, c1):
            left, right = int(round(ox+c*pitch+inset)), int(round(ox+(c+1)*pitch-inset))
            block = rgba[top:max(bottom, top+1), left:max(right, left+1)].reshape(-1, 4)
            opaque = block[block[:, 3] >= 128]
            if len(opaque)*2 < len(block) or not len(opaque):
                continue
            out[r, c, :3] = np.median(opaque[:, :3], axis=0).round().astype(np.uint8)
            out[r, c, 3] = 255
    # Cell i covers source [ox + i*pitch, ...); its logical column is i + round(ox/pitch).
    shift = (int(round(ox/pitch)), int(round(oy/pitch)))
    image = Image.fromarray(out)
    return image, shift


def speckle_share(image):
    """Share of opaque pixels that match none of their opaque 4-neighbours' colors.

    Clean pixel art is made of clusters; a redraw that came back as texture noise leaves
    lone pixels everywhere. Pixels with no opaque neighbour at all are counted too.
    """
    rgba = np.asarray(image.convert("RGBA"))
    opaque = rgba[..., 3] > 0
    if not opaque.any():
        return 0.0
    key = (rgba[..., 0].astype(np.int64) << 16) | (rgba[..., 1].astype(np.int64) << 8) | rgba[..., 2]
    key = np.where(opaque, key, -1)
    padded = np.pad(key, 1, constant_values=-2)
    same = np.zeros(key.shape, dtype=bool)
    for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        same |= padded[1+dy:1+dy+key.shape[0], 1+dx:1+dx+key.shape[1]] == key
    return round(float((opaque & ~same).sum()/opaque.sum()), 4)


def outlier_frames(images, *, speckle_factor=2.0, speckle_floor=0.12, area_band=0.45):
    """Frames of one layer that stand apart: speckled beyond its peers, or an area jump.

    `images` is an ordered list (a loop); each frame's area is compared with the median of
    its two neighbours. Returns [{index, reasons, speckle, area}], an empty list when clean.
    """
    speckles = [speckle_share(image) for image in images]
    areas = [int((np.asarray(image.convert("RGBA"))[..., 3] > 0).sum()) for image in images]
    typical = float(np.median(speckles)) if speckles else 0.0
    out = []
    for n, (speckle, area) in enumerate(zip(speckles, areas)):
        reasons = []
        if speckle > max(speckle_floor, speckle_factor*typical):
            reasons.append("speckle")
        neighbours = [areas[(n-1) % len(areas)], areas[(n+1) % len(areas)]]
        expected = float(np.median(neighbours))
        if expected and abs(area-expected) > area_band*expected:
            reasons.append("area")
        if reasons:
            out.append({"index": n, "reasons": reasons, "speckle": speckle, "area": area})
    return out


def small_holes(image, max_size):
    """Transparent 4-connected regions of at most `max_size` px enclosed by the layer's own pixels."""
    rgba = np.asarray(image.convert("RGBA"))
    clear = rgba[..., 3] == 0
    height, width = clear.shape
    seen = np.zeros_like(clear)
    holes = []
    for y0, x0 in zip(*np.nonzero(clear)):
        if seen[y0, x0]:
            continue
        stack, region, open_edge = [(y0, x0)], [], False
        seen[y0, x0] = True
        while stack:
            y, x = stack.pop()
            region.append((y, x))
            if len(region) > max_size:
                open_edge = True
            for ny, nx in ((y-1, x), (y+1, x), (y, x-1), (y, x+1)):
                if not (0 <= ny < height and 0 <= nx < width):
                    open_edge = True
                elif clear[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    stack.append((ny, nx))
        if not open_edge:
            holes.append(region)
    return holes


def fill_small_holes(image, max_size):
    """Fill enclosed holes of at most `max_size` px with their most common bordering color.

    A redrawn garment that leaves a few pixels unpainted lets the skin under it show
    through as a stain; a hole that small is never part of the design.
    """
    rgba = np.asarray(image.convert("RGBA")).copy()
    holes = small_holes(image, max_size)
    for region in holes:
        border = {}
        cells = set(region)
        for y, x in region:
            for ny, nx in ((y-1, x), (y+1, x), (y, x-1), (y, x+1)):
                if (ny, nx) not in cells and rgba[ny, nx, 3] > 0:
                    key = tuple(int(v) for v in rgba[ny, nx])
                    border[key] = border.get(key, 0)+1
        color = max(border, key=border.get)
        for y, x in region:
            rgba[y, x] = color
    return Image.fromarray(rgba), len(holes)


def close_over(part, body, *, neighbours=5, rounds=2):
    """Cover body pixels the layer almost covers: >= `neighbours` of their 8 neighbours are layer.

    Fills notches and thin gaps where a redrawn garment fell a pixel short of the limb it
    clothes. Only pixels inside the body's silhouette are filled; the layer never grows past it.
    """
    rgba = np.asarray(part.convert("RGBA")).copy()
    inside = np.asarray(body.convert("RGBA"))[..., 3] > 0
    filled = 0
    for _ in range(rounds):
        mine = rgba[..., 3] > 0
        padded = np.pad(mine, 1)
        count = sum(padded[1+dy:1+dy+mine.shape[0], 1+dx:1+dx+mine.shape[1]].astype(int)
                    for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dy or dx)
        targets = inside & ~mine & (count >= neighbours)
        if not targets.any():
            break
        for y, x in zip(*np.nonzero(targets)):
            votes = {}
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    ny, nx = y+dy, x+dx
                    if (dy or dx) and 0 <= ny < rgba.shape[0] and 0 <= nx < rgba.shape[1] and mine[ny, nx]:
                        key = tuple(int(v) for v in rgba[ny, nx])
                        votes[key] = votes.get(key, 0)+1
            rgba[y, x] = max(votes, key=votes.get)
            filled += 1
    return Image.fromarray(rgba), filled
