# SPDX-License-Identifier: Apache-2.0
"""Pose guide sheets for drawing one part per motion sample, and slicing them back.

`parts-pose-sheet` lays the frozen motion's final body rasters out on one
flat-key sheet at an integer scale. An image model or artist draws a part on
each cell. `parts-pose-slice` cuts the returned sheet with the same recorded
grid, removes the key, reduces each cell back to native pixels and writes
full-canvas PNGs plus a `parts-pack-import` spec. The spec pins each
sample's target socket as its pivot, so every raster lands exactly on its
own body frame. Nothing is imported, aligned, recolored by palette or
approved here; `parts-import`, fitting and review remain separate steps.
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.compose import compose_parts, parts_resolve
from sprite_gen.frames import extract, pixel_grid
from sprite_gen.curate import parts_motion
from sprite_gen.qa import parts_stability
from sprite_gen.spec import parts_assets as assets, parts_contract as contract, parts_workspace as workspace
from sprite_gen.spec.runio import atomic_write_text

SHEET_POLICY = "parts.pose-sheet.v1"
CLASS_POLICY = "parts.pose-classes.v1"
_CELL = contract._record({"clip": contract._id, "sample": contract._id, "body_sha256": contract._hash,
                          "origin": contract._point, "under_sha256": contract._hash}, optional=("under_sha256",))
_GUIDE = contract._record({
    "kind": contract._enum("parts-pose-sheet"), "schema_version": contract._positive,
    "policy": contract._string, "motion": contract._ref, "canvas": contract._point,
    "crop": contract._rectangle, "scale": contract._positive, "gutter": contract._integer,
    "columns": contract._positive, "size": contract._point, "key": contract._rgb,
    "sheet": contract._asset, "cells": contract._list(_CELL, nonempty=True), "prompt_hint": contract._string,
    "wearing": contract._list(contract._ref, unique=True),
    "art_mode": contract._enum("pixel", "illustration"),
}, optional=("wearing", "art_mode"))


_MEMBER = contract._record({"clip": contract._id, "sample": contract._id})
_CLASSES = contract._record({
    "kind": contract._enum("parts-pose-classes"), "schema_version": contract._positive, "policy": contract._string,
    "motion": contract._ref, "socket": contract._id, "bounds": contract._rectangle,
    "classes": contract._list(contract._record({"id": contract._id, "representative": _MEMBER,
                                                "members": contract._list(_MEMBER, nonempty=True)}), nonempty=True),
})


def _samples(motion, index, clips):
    available = {clip["id"]: clip for clip in motion["clips"]}
    chosen = clips or [clip["id"] for clip in motion["clips"] if clip["state"] in index["states"]] or list(available)
    if set(chosen) - set(available):
        raise contract.PartsContractError(f"pose sheet clips are missing: {sorted(set(chosen)-set(available))}")
    return [(clip_id, sample) for clip_id in chosen for sample in available[clip_id]["samples"]]


def pose_classes(assembly, out, *, socket, bounds, classes, clips=None, alpha_weight=3):
    """Group samples whose drawing around one socket looks alike; one representative per group.

    A part drawn once per group (a hair variant for each head angle) is then reused by every
    member at that member's socket. Grouping is k-medoids on the aligned region's alpha and
    color differences, seeded deterministically by farthest-first choice.
    """
    root, index, registry, _ = parts_motion._active(assembly)
    motion = registry[index["motion"]["id"]]
    samples = _samples(motion, index, clips)
    if not 1 <= classes <= len(samples):
        raise contract.PartsContractError(f"classes must be 1..{len(samples)}")
    bounds = _crop_box(bounds)
    load = compose_parts.AssetLoader(root, index)
    regions = []
    for clip_id, sample in samples:
        location = sample["sockets"].get(socket)
        if location is None:
            raise contract.PartsContractError(f"{clip_id}/{sample['id']} has no socket {socket}")
        x, y = location["position"]
        regions.append(load(motion, sample["image"]).convert("RGBA").crop((x+bounds[0], y+bounds[1], x+bounds[2], y+bounds[3])))
    n = len(regions)
    distance = np.zeros((n, n), dtype=np.int64)
    for i in range(n):
        for j in range(i+1, n):
            d = parts_stability.measure(regions[i], regions[j])
            distance[i, j] = distance[j, i] = d["alpha_pixels"]*alpha_weight + d["rgb_pixels"]
    medoids = [0]
    while len(medoids) < classes:
        medoids.append(int(np.argmax(distance[:, medoids].min(axis=1))))
    for _ in range(32):
        assign = np.argmin(distance[:, medoids], axis=1)
        updated = []
        for k in range(len(medoids)):
            members = np.where(assign == k)[0]
            updated.append(int(members[np.argmin(distance[np.ix_(members, members)].sum(axis=1))]))
        if updated == medoids:
            break
        medoids = updated
    assign = np.argmin(distance[:, medoids], axis=1)
    groups = []
    for k, medoid in enumerate(medoids):
        members = [{"clip": samples[i][0], "sample": samples[i][1]["id"]} for i in range(n) if assign[i] == k]
        groups.append({"id": f"class.{k}", "representative": {"clip": samples[medoid][0], "sample": samples[medoid][1]["id"]},
                       "members": members})
    document = _CLASSES({"kind": "parts-pose-classes", "schema_version": 1, "policy": CLASS_POLICY,
                         "motion": index["motion"], "socket": socket, "bounds": bounds, "classes": groups}, "classes")
    out = Path(out).absolute()
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(out, workspace.json_bytes(document).decode("utf-8"))
    spread = [int(distance[i, medoids[assign[i]]]) for i in range(n)]
    return {"policy": CLASS_POLICY, "classes": str(out), "count": len(groups),
            "sizes": [len(g["members"]) for g in groups], "max_member_cost": max(spread),
            "mean_member_cost": round(sum(spread)/n, 2), "status": "draft", "release_ready": False}


def _crop_box(value):
    box = [int(v) for v in value.split(",")] if isinstance(value, str) else list(value)
    if len(box) != 4 or box[0] >= box[2] or box[1] >= box[3]:
        raise contract.PartsContractError("bounds must be left,top,right,bottom relative to the socket")
    return box


def _read_classes(path, motion_ref):
    path = Path(path).absolute()
    document = _CLASSES(workspace.decode_json(assets.read_file(path.parent, path.name, limit=workspace.MAX_JSON_BYTES)), "classes")
    if document["motion"] != motion_ref:
        raise contract.PartsContractError("pose classes were made for a different motion revision")
    return document


def _key(value):
    value = value.strip().lstrip("#")
    if len(value) != 6:
        raise contract.PartsContractError("key must be a #RRGGBB color")
    return [int(value[i:i+2], 16) for i in (0, 2, 4)]


def _crop(value, canvas):
    if value is None:
        return [0, 0, *canvas]
    box = [int(v) for v in value.split(",")]
    if len(box) != 4 or box[0] < 0 or box[1] < 0 or box[2] > canvas[0] or box[3] > canvas[1] or box[0] >= box[2] or box[1] >= box[3]:
        raise contract.PartsContractError("crop must be left,top,right,bottom inside the rig canvas")
    return box


def _one_per_pose(samples):
    """The first sample of each pose_id: a pose is drawn once and reused wherever it plays."""
    if any(sample["pose_id"] is None for _, sample in samples):
        raise contract.PartsContractError("drawing by pose needs a pose_id on every sample (parts-pose-assign before freezing)")
    seen, kept = set(), []
    for clip_id, sample in samples:
        if sample["pose_id"] not in seen:
            seen.add(sample["pose_id"])
            kept.append((clip_id, sample))
    return kept


def worn_packs(registry, index, wearing):
    """The active packs a sheet is drawn over, each bound to the active motion."""
    packs = []
    for name in wearing or ():
        pack = registry.get(name)
        if pack is None or pack["kind"] != "parts-pack" or pack["role"] != "part":
            raise contract.PartsContractError(f"--wearing {name} is not an active part")
        if not any(b["motion"] == index["motion"] and b["rig"] == index["rig"] for b in pack["bindings"]):
            raise contract.PartsContractError(f"--wearing {name} has no binding for the active motion")
        packs.append(pack)
    return packs


def dressed_body(motion, index, topology, clip_id, sample, load, packs):
    """The motion raster with worn parts on it for this sample, in draw-table order.

    A part drawn over others (boots over trousers) is drawn and cut against this, so the
    model sees where it ends and the cut keeps only what is new. Masks are not applied.
    """
    canvas = load(motion, sample["image"]).convert("RGBA").copy()
    if not packs:
        return canvas
    ranks = parts_resolve.layer_ranks(topology)
    entries = []
    bindings = [(pack, next(b for b in pack["bindings"] if b["motion"] == index["motion"] and b["rig"] == index["rig"]))
                for pack in packs]
    covered = {tag for _, binding in bindings for tag in binding.get("covers", [])}
    for pack, binding in bindings:
        for piece in binding["pieces"]:
            rule = piece.get("cover_rule")
            if rule is not None and (rule["tag"] in covered) != (rule["show"] == "covered"):
                continue  # as the resolver does: worn hair without a hat is the full hair, not its capped piece
            placement = piece["sample_map"].get(clip_id, {}).get(sample["id"])
            socket = None if placement is None else sample["sockets"].get(placement["target_socket"])
            if placement is None or not placement["visible"] or socket is None or not socket["visible"]:
                continue
            entries.append((parts_resolve.rank({"node": piece["node"], "provides": piece["provides"]}, ranks),
                            f"{pack['id']}.{piece['id']}", pack, placement, socket))
    for _, _, pack, placement, socket in sorted(entries, key=lambda entry: entry[:2]):
        offset = [t - p + d for t, p, d in zip(socket["position"], placement["pivot"], placement["offset"])]
        layer = Image.new("RGBA", canvas.size)
        layer.paste(load(pack, placement["image"]).convert("RGBA"), tuple(offset))
        canvas.alpha_composite(layer)
    return canvas


def build_sheet(assembly, out, *, clips=None, scale=4, crop=None, key="#ff00ff", gutter=8, columns=None, part="part",
                classes=None, page_size=None, aspect=None, by_pose=False, poses=None, wearing=None):
    root, index, registry, _ = parts_motion._active(assembly)
    motion = registry[index["motion"]["id"]]
    if motion["freeze"] is None:
        raise contract.PartsContractError("pose sheet requires a frozen motion")
    rig = registry[index["rig"]["id"]]
    canvas = [rig["canvas"]["width"], rig["canvas"]["height"]]
    if rig.get("sampling", "nearest") == "smooth" and scale != 1:
        raise contract.PartsContractError("illustration pose sheets require native scale 1")
    samples = _samples(motion, index, clips)
    if by_pose and classes is not None:
        raise contract.PartsContractError("--by-pose and --classes are two ways to share drawings; choose one")
    if by_pose:
        samples = _one_per_pose(samples)
    if poses is not None:
        # Only these poses are drawn: new poses added to a motion, or poses that failed review.
        if not by_pose:
            raise contract.PartsContractError("--poses selects cells by pose; use it with --by-pose")
        unknown = set(poses) - {sample["pose_id"] for _, sample in samples}
        if unknown:
            raise contract.PartsContractError(f"unknown poses in the chosen clips: {sorted(unknown)}")
        samples = [(clip_id, sample) for clip_id, sample in samples if sample["pose_id"] in set(poses)]
    if classes is not None:
        # Only each group's representative is drawn; members reuse it when sliced.
        wanted = {(g["representative"]["clip"], g["representative"]["sample"])
                  for g in _read_classes(classes, index["motion"])["classes"]}
        samples = [(c, s) for c, s in samples if (c, s["id"]) in wanted]
        if len(samples) != len(wanted):
            raise contract.PartsContractError("pose class representatives are outside the chosen clips")
    if not 1 <= scale <= 16 or not 0 <= gutter <= 256 or (page_size is not None and page_size < 1):
        raise contract.PartsContractError("pose sheet scale must be 1..16, gutter 0..256 and page size positive")
    crop, key = _crop(crop, canvas), _key(key)
    if aspect is not None:
        ratio = aspect.split(":")
        if len(ratio) != 2 or not all(v.isdigit() and int(v) > 0 for v in ratio):
            raise contract.PartsContractError("aspect must be W:H, e.g. 1:1 or 3:2")
        aspect = (int(ratio[0]), int(ratio[1]))
    load = compose_parts.AssetLoader(root, index)
    packs = worn_packs(registry, index, wearing)
    topology = registry[rig["topology"]["id"]]
    def body_of(clip_id, sample):
        return dressed_body(motion, index, topology, clip_id, sample, load, packs)
    out = Path(out).absolute()
    pages = [samples] if page_size is None else [samples[i:i+page_size] for i in range(0, len(samples), page_size)]
    # Every page keeps the first page's grid: a short last page with fewer columns is a smaller
    # sheet, and an image model scales its content up to its fixed output size.
    columns = columns or math.ceil(math.sqrt(len(pages[0])))
    rows = math.ceil(len(pages[0])/columns)
    written = []
    for number, chunk in enumerate(pages, 1):
        folder = out if len(pages) == 1 else out / f"page-{number}"
        written.append(_page(folder, chunk, motion, index, body_of, canvas=canvas, crop=crop, scale=scale, key=key,
                             gutter=gutter, columns=columns, part=part, aspect=aspect, rows=rows,
                             smooth=rig.get("sampling", "nearest") == "smooth",
                             wearing=[contract.reference(pack) for pack in packs]))
    first = written[0]
    return {"policy": SHEET_POLICY, "sheet": first["sheet"], "guide": first["guide"], "pages": written,
            "cells": sum(p["cells"] for p in written), "size": first["size"], "scale": scale, "crop": crop,
            "prompt_hint": first["prompt_hint"], "status": "draft", "release_ready": False}


def _page(out, samples, motion, index, body_of, *, canvas, crop, scale, key, gutter, columns, part, aspect=None, rows=None,
          wearing=(), smooth=False):
    columns = columns or math.ceil(math.sqrt(len(samples)))
    cell = [(crop[2]-crop[0])*scale, (crop[3]-crop[1])*scale]
    rows = max(rows or 0, math.ceil(len(samples)/columns))
    size = [gutter+columns*(cell[0]+gutter), gutter+rows*(cell[1]+gutter)]
    if aspect is not None:
        # Pad with key on the right or bottom so the sheet has the model's output ratio.
        if size[0]*aspect[1] < size[1]*aspect[0]:
            size[0] = -(-size[1]*aspect[0]//aspect[1])
        else:
            size[1] = -(-size[0]*aspect[1]//aspect[0])
    sheet = Image.new("RGBA", tuple(size), (*key, 255))
    cells = []
    for n, (clip_id, sample) in enumerate(samples):
        origin = [gutter+n % columns*(cell[0]+gutter), gutter+n//columns*(cell[1]+gutter)]
        dressed = body_of(clip_id, sample)
        sheet.alpha_composite(dressed.crop(tuple(crop)).resize(tuple(cell), Image.Resampling.NEAREST), tuple(origin))
        cells.append({"clip": clip_id, "sample": sample["id"], "body_sha256": sample["image"]["sha256"], "origin": origin}
                     | ({"under_sha256": assets.sha256(assets.png_bytes(dressed))} if wearing else {}))
    out.mkdir(parents=True, exist_ok=True)
    data = assets.png_bytes(sheet)
    (out/"pose-sheet.png").write_bytes(data)
    hex_key = "#%02X%02X%02X" % tuple(key)
    guide = _GUIDE({
        "kind": "parts-pose-sheet", "schema_version": 1, "policy": SHEET_POLICY, "motion": index["motion"],
        "canvas": canvas, "crop": crop, "scale": scale, "gutter": gutter, "columns": columns, "size": size,
        "key": key, "sheet": {"file": "pose-sheet.png", "sha256": assets.sha256(data)}, "cells": cells,
        **({"wearing": list(wearing)} if wearing else {}),
        **({"art_mode": "illustration"} if smooth else {}),
        "prompt_hint": (f"Keep this {size[0]}x{size[1]} image and its {len(cells)}-cell grid exactly. In every cell, "
            f"draw only the {part} worn by that exact pose, at the same scale and position, "
            + ("as a smooth high-resolution illustration with antialiased alpha edges. " if smooth else f"as {scale}x{scale} pixel blocks. ")
            +
            f"Erase the body: every other pixel stays flat {hex_key}. Do not move, mirror or resize any cell."),
    }, "guide")
    atomic_write_text(out/"pose-sheet.json", workspace.json_bytes(guide).decode("utf-8"))
    return {"sheet": str(out/"pose-sheet.png"), "guide": str(out/"pose-sheet.json"), "cells": len(cells),
            "size": size, "prompt_hint": guide["prompt_hint"]}


def reduce_cell(block, pitch, key, tolerance, *, size=None):
    """Remove the flat key, then read one native pixel per drawn grid cell.

    The grid's phase is searched (pixel_grid.lattice_reduce): a model redraws the sheet
    with its own block offsets, and a fixed phase splits one-pixel outlines in two.
    """
    rgba = np.asarray(block.convert("RGBA")).copy()
    distance = np.max(np.abs(rgba[..., :3].astype(np.int16)-np.array(key, dtype=np.int16)), axis=-1)
    background = (rgba[..., 3] < 128) | (distance <= tolerance)
    keyed, unkeyed = extract._key_channel_split(tuple(key))
    if keyed:
        # Key-tinted fringe (a darker or lighter shade of the key) is background too.
        background |= extract._key_family_field(rgba[..., :3].astype(np.float64), keyed, unkeyed)
    rgba[background] = 0
    size = size or (int(round(block.width/pitch)), int(round(block.height/pitch)))
    out = Image.new("RGBA", size)
    if not (~background).any():
        return out
    image, (dx, dy) = pixel_grid.lattice_reduce(Image.fromarray(rgba), pitch)
    out.alpha_composite(image.crop((0, 0, max(0, min(image.width, size[0]-dx)), max(0, min(image.height, size[1]-dy)))), (dx, dy))
    return out


def subtract_body(part, body, threshold, *, radius=1):
    """Keep only pixels unlike the body; for sheets returned fully dressed.

    A redrawn sheet moves outlines and shading by a pixel, so a pixel counts as body when a
    body pixel of a similar color lies within `radius` px of it.
    """
    a, b = np.asarray(part).copy(), np.asarray(body.convert("RGBA")).astype(np.int16)
    rgb = a[..., :3].astype(np.int16)
    same = np.zeros(a.shape[:2], dtype=bool)
    for dy in range(-radius, radius+1):
        for dx in range(-radius, radius+1):
            shifted = np.roll(np.roll(b, dy, axis=0), dx, axis=1)
            same |= (shifted[..., 3] >= 128) & (np.max(np.abs(rgb-shifted[..., :3]), axis=-1) <= threshold)
    a[same] = 0
    return Image.fromarray(a)


def within_body(part, body, distance):
    """Drop part pixels farther than `distance` px (Chebyshev) from the body silhouette: stray fringe."""
    a = np.asarray(part).copy()
    near = np.asarray(body.convert("RGBA"))[..., 3] >= 128
    for _ in range(distance):
        grown = near.copy()
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                grown |= np.roll(np.roll(near, dy, axis=0), dx, axis=1)
        near = grown
    a[~near] = 0
    return Image.fromarray(a)


def drop_islands(part, minimum, *, body=None, luma=40, dark_share=0.8, body_share=0.7, threshold=24):
    """Remove 8-connected islands that are what a redrawn body leaves behind.

    Dropped: islands under `minimum` px, islands almost only of outline ink, and small
    islands (under 4 x minimum) mostly in the body's own colors. A garment island carries
    its material colors.
    """
    a = np.asarray(part).copy()
    opaque = a[..., 3] > 0
    dark = a[..., :3].astype(np.int32) @ np.array([299, 587, 114]) < luma*1000
    own = np.zeros_like(opaque)
    if body is not None:
        b = np.asarray(body.convert("RGBA"))
        colors = np.unique(b[b[..., 3] >= 128, :3], axis=0).astype(np.int16)
        if len(colors):
            rgb = a[..., :3].astype(np.int16)
            own = (np.abs(rgb[:, :, None, :]-colors[None, None, :, :]).max(axis=-1) <= threshold).any(axis=-1)
    seen = np.zeros_like(opaque)
    height, width = opaque.shape
    for y0, x0 in zip(*np.nonzero(opaque)):
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
                    if 0 <= ny < height and 0 <= nx < width and opaque[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
        ys, xs = zip(*island)
        if (len(island) < minimum or dark[list(ys), list(xs)].mean() >= dark_share
                or (len(island) < 4*minimum and own[list(ys), list(xs)].mean() >= body_share)):
            a[list(ys), list(xs)] = 0
    return Image.fromarray(a)


def keep_outline(part, dressed, *, luma=40):
    """Restore dark dressed pixels touching the part: its outline where it lies on the body's."""
    a, d = np.asarray(part).copy(), np.asarray(dressed).astype(np.int16)
    mask = a[..., 3] > 0
    grown = mask.copy()
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            grown |= np.roll(np.roll(mask, dy, axis=0), dx, axis=1)
    dark = (d[..., 3] > 0) & (d[..., :3].astype(np.int32) @ np.array([299, 587, 114]) < luma*1000)
    add = grown & ~mask & dark
    a[add] = d[add].astype(np.uint8)
    return Image.fromarray(a)


def drawn_cells(pairs, index, clips, tolerance):
    """Yield (cell, sample, native cell raster, guide) for every cell of the drawn pages.

    Model outputs are often a uniformly resampled sheet: cells are read in the drawn
    image's own coordinates, at the pixel size the resampling gave each guide block.
    """
    for guide_file, art_file in pairs:
        guide_path = Path(guide_file).absolute()
        sheet = _GUIDE(workspace.decode_json(assets.read_file(guide_path.parent, guide_path.name, limit=workspace.MAX_JSON_BYTES)), "guide")
        if sheet["motion"] != index["motion"]:
            raise contract.PartsContractError("pose sheet was made for a different motion revision")
        drawn = Image.open(art_file).convert("RGBA")
        width, height = sheet["size"]
        if abs(drawn.width*height - drawn.height*width) > drawn.height*width/100:
            raise contract.PartsContractError(f"drawn sheet {drawn.size} does not match the guide aspect {width}x{height}")
        ratio = drawn.width / width
        crop, scale = sheet["crop"], sheet["scale"]
        cell = [(crop[2]-crop[0])*scale, (crop[3]-crop[1])*scale]
        for entry in sheet["cells"]:
            clip = clips.get(entry["clip"])
            sample = None if clip is None else next((s for s in clip["samples"] if s["id"] == entry["sample"]), None)
            if sample is None or sample["image"]["sha256"] != entry["body_sha256"]:
                raise contract.PartsContractError(f"pose sheet cell is stale: {entry['clip']}/{entry['sample']}")
            x, y = entry["origin"]
            box = tuple(int(round(v*ratio)) for v in (x, y, x+cell[0], y+cell[1]))
            if sheet.get("art_mode") == "illustration":
                if scale != 1 or drawn.size != tuple(sheet["size"]):
                    raise contract.PartsContractError("illustration art must match the native guide size at scale 1; no implicit resizing")
                native = drawn.crop(box)
                if np.all(np.asarray(native)[..., 3] == 255):
                    # Reuse the engine's soft-alpha chroma extraction, not the
                    # pixel lattice or a binary alpha threshold.
                    native = extract.remove_chroma_background(native, tuple(sheet["key"]), 96, 180, 18)
                yield entry, sample, native, sheet
            else:
                yield entry, sample, reduce_cell(drawn.crop(box), scale*ratio, sheet["key"], tolerance,
                                                 size=(cell[0]//scale, cell[1]//scale)), sheet


def _under(sheet, entry, sample, motion, index, registry, topology, load, *, sheet_image=None):
    """The raster a cell was drawn over: the body, or the body wearing the guide's parts.

    When the worn parts render differently now and the sheet image is given, the cell is
    read back from it: what the model saw, not what the parts look like today.
    """
    if not sheet.get("wearing"):
        return load(motion, sample["image"])
    dressed = worn_now(sheet["wearing"], entry["clip"], sample, motion, index, registry, topology, load)
    if dressed is not None and sheet_image is not None and assets.sha256(assets.png_bytes(dressed)) != entry["under_sha256"]:
        seen = sheet_cell(sheet, entry, sheet_image, dressed)
        if assets.sha256(assets.png_bytes(seen)) == entry["under_sha256"]:
            return seen
    if dressed is None or assets.sha256(assets.png_bytes(dressed)) != entry["under_sha256"]:
        # A worn pack may change without changing what it drew here (a hair gaining its capped
        # piece); only a different render means the sheet was drawn over something else.
        changed = [ref["id"] for ref in sheet["wearing"] if registry.get(ref["id"], {}).get("digest") != ref["digest"]]
        raise contract.PartsContractError(f"{entry['clip']}/{entry['sample']}: pose sheet was drawn over "
                                          f"{', '.join(changed) or 'worn parts'}, which has changed since")
    return dressed


def sheet_cell(sheet, entry, sheet_image, around):
    """`around` with the sheet crop replaced by what the cell shows, read back at native size."""
    crop, scale = sheet["crop"], sheet["scale"]
    width, height = crop[2]-crop[0], crop[3]-crop[1]
    x, y = entry["origin"]
    block = np.asarray(sheet_image.convert("RGBA"))[y:y+height*scale, x:x+width*scale][scale//2::scale, scale//2::scale].copy()
    block[(block[..., :3] == np.array(sheet["key"])).all(-1)] = 0
    canvas = around.copy()
    canvas.paste(Image.fromarray(block), tuple(crop[:2]))
    return canvas


def worn_now(wearing, clip_id, sample, motion, index, registry, topology, load):
    """The body dressed in the active versions of the worn parts, or None if one is gone."""
    if any(ref["id"] not in registry for ref in wearing):
        return None
    return dressed_body(motion, index, topology, clip_id, sample, load, [registry[ref["id"]] for ref in wearing])


def enclosed(mask):
    """Transparent pixels enclosed by the mask: not reachable from the image border."""
    from collections import deque
    height, width = mask.shape
    free, seen = ~mask, np.zeros(mask.shape, dtype=bool)
    queue = deque((y, x) for y in range(height) for x in range(width)
                  if (y in (0, height-1) or x in (0, width-1)) and free[y, x])
    for y, x in queue:
        seen[y, x] = True
    while queue:
        y, x = queue.popleft()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y+dy, x+dx
            if 0 <= ny < height and 0 <= nx < width and free[ny, nx] and not seen[ny, nx]:
                seen[ny, nx] = True
                queue.append((ny, nx))
    return free & ~seen


def cover(dressed, body, *, threshold=24, minimum=6, prune=2):
    """A part that covers the body, taken whole: what the drawing changed, its enclosed gaps filled.

    Subtracting the body pixel by pixel (with slack for redraw drift) also removes the part's
    pixels that match the body's outline or shading underneath, so the body shows through a
    hair or a hat band. Here the region is the silhouette of change, gaps inside it are the
    drawing's own pixels, and only rim pixels identical to the body under them are trimmed.
    """
    d, b = np.asarray(dressed).astype(int), np.asarray(body).astype(int)
    changed = (np.abs(d[..., :3]-b[..., :3]).max(-1) > threshold) | ((d[..., 3] > 0) != (b[..., 3] > 0))
    mask = changed & (d[..., 3] > 0)
    part = drop_islands(Image.fromarray(np.where(mask[..., None], d, 0).astype(np.uint8)), minimum, dark_share=1.01)
    mask = np.asarray(part)[..., 3] > 0
    mask |= enclosed(mask) & (d[..., 3] > 0)
    part = keep_outline(Image.fromarray(np.where(mask[..., None], d, 0).astype(np.uint8)), dressed, luma=60)
    mask = np.asarray(part)[..., 3] > 0
    same = np.abs(d - b).max(-1) == 0
    for _ in range(prune):
        padded = np.pad(mask, 1)
        rim = mask & ~(padded[2:, 1:-1] & padded[:-2, 1:-1] & padded[1:-1, 2:] & padded[1:-1, :-2])
        mask &= ~(rim & same)
    return Image.fromarray(np.where(mask[..., None], d, 0).astype(np.uint8))


METHODS = ("difference", "covering", "part-only")


def _nearest(rgb, colors):
    return np.abs(rgb[:, None, :] - np.asarray(colors, dtype=int)[None]).max(-1).min(-1)


def near_palette(part, colors, distance, *, under=None, window=2, luma=60, against=None):
    """Keep pixels within `distance` (max channel) of a palette color, and nearer to it than to
    what was under them nearby (`window` px; outline colors left out): drop the body the model
    repainted. Judged locally, an eye's white is not mistaken for a white shirt elsewhere."""
    pixels = np.asarray(part).copy()
    alpha = pixels[..., 3] > 0
    if alpha.any():
        rgb = pixels[..., :3].astype(int)
        nearest = _nearest(rgb[alpha], colors)
        keep = np.zeros(alpha.shape, dtype=bool)
        keep[alpha] = nearest <= distance
        if under is not None:
            keep[alpha] &= nearest < _local_distance(rgb, under, window, luma)[alpha]
        if against is not None and len(against):
            # Another part's colors (the face or the hat this one was drawn over, redrawn by
            # the model a little differently): nearer to them, the pixel is theirs.
            keep[alpha] &= nearest <= _nearest(rgb[alpha], against)
        pixels[alpha & ~keep] = 0
    return Image.fromarray(pixels)


def _local_distance(rgb, under, window, luma):
    """Per pixel, the max-channel distance to the nearest non-outline under color within `window`."""
    base = np.asarray(under)
    usable = (base[..., 3] > 0) & (base[..., :3].astype(np.int32) @ np.array([299, 587, 114]) >= luma*1000)
    best = np.full(rgb.shape[:2], 10**6)
    colors = base[..., :3].astype(int)
    for dy in range(-window, window+1):
        for dx in range(-window, window+1):
            there = _shifted(usable, dy, dx, False)
            gap = np.abs(rgb - _shifted(colors, dy, dx)).max(-1)
            best = np.where(there, np.minimum(best, gap), best)
    return best


def repainted(drawn, under, colors, distance):
    """Changed pixels `near_palette` would drop: the body the model repainted, not the part."""
    changed = np.asarray(cover(drawn, under))
    kept = np.asarray(near_palette(Image.fromarray(changed), colors, distance, under=under))
    return (changed[..., 3] > 0) & (kept[..., 3] == 0)


def lone_outline(part, under, *, luma=60):
    """Drop dark pixels off the figure with no lighter part pixel beside them: an outline with
    nothing inside (the edge of a hat the model drew a little higher than the one worn) is not
    the part. On the figure dark strokes stay (an eyebrow is all outline color)."""
    pixels = np.asarray(part).copy()
    alpha = pixels[..., 3] > 0
    dark = alpha & (pixels[..., :3].astype(np.int32) @ np.array([299, 587, 114]) < luma*1000)
    dark &= np.asarray(under)[..., 3] == 0
    fill = np.pad(alpha & ~dark, 1)
    beside = np.zeros_like(alpha)
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            beside |= fill[dy:dy+alpha.shape[0], dx:dx+alpha.shape[1]]
    pixels[dark & ~beside] = 0
    return Image.fromarray(pixels)


_STEPS = ((-1, 0), (1, 0), (0, -1), (0, 1))


def _shifted(array, dy, dx, fill=0):
    """array[y+dy, x+dx] at every (y, x), `fill` off the edge."""
    out = np.full_like(array, fill)
    h, w = array.shape[:2]
    out[max(0, -dy):h-max(0, dy), max(0, -dx):w-max(0, dx)] = array[max(0, dy):h-max(0, -dy), max(0, dx):w-max(0, -dx)]
    return out


def cover_rim(part, drawn, under, rounds, *, luma=60):
    """Grow the part over body the drawing left as background right beside it, `rounds` px.

    A model often draws hair or a hat a pixel or two inside the head it was given, and the
    bald skull then shows around it as a ring. The part grows outward with its own outline;
    the old outline, now inside, takes the color just inside it.
    """
    pixels = np.asarray(part).copy()
    alpha = pixels[..., 3] > 0
    exposed = (np.asarray(under)[..., 3] > 0) & (np.asarray(drawn)[..., 3] == 0)
    added = np.zeros_like(alpha)
    for _ in range(rounds):
        grow = exposed & ~alpha
        grown = np.zeros_like(alpha)
        for dy, dx in _STEPS:
            take = grow & ~grown & _shifted(alpha, dy, dx, False)
            pixels[take] = _shifted(pixels, dy, dx)[take]
            grown |= take
        if not grown.any():
            break
        alpha |= grown
        added |= grown
    dark = alpha & (pixels[..., :3].astype(np.int32) @ np.array([299, 587, 114]) < luma*1000)
    inside = alpha.copy()
    for dy, dx in _STEPS:
        inside &= _shifted(alpha, dy, dx, False)
    # Only the new edge keeps the outline: grown pixels inside it and the old outline they
    # cover take the fill color next to them, working outward from the part's own fill.
    near_added = added | np.any([_shifted(added, dy, dx, False) for dy, dx in _STEPS], axis=0)
    stale = dark & inside & near_added
    fill = alpha & ~dark
    for _ in range(rounds + 1):
        grew = np.zeros_like(alpha)
        for dy, dx in _STEPS:
            take = stale & ~grew & _shifted(fill, dy, dx, False)
            pixels[take] = _shifted(pixels, dy, dx)[take]
            grew |= take
        if not grew.any():
            break
        stale &= ~grew
        fill |= grew
    return Image.fromarray(pixels)


def add_back(part, drawn, under, colors, margin, *, window=2, luma=60):
    """`part` plus changed drawn pixels nearer a palette color than any nearby under color by `margin`."""
    changed = np.asarray(cover(drawn, under))
    alpha = changed[..., 3] > 0
    rgb = changed[..., :3].astype(int)
    keep = np.zeros(alpha.shape, dtype=bool)
    if alpha.any():
        nearest = _nearest(rgb[alpha], colors)
        keep[alpha] = nearest + margin < _local_distance(rgb, under, window, luma)[alpha]
    pixels = np.asarray(part).copy()
    add = keep & (pixels[..., 3] == 0)
    pixels[add] = changed[add]
    return Image.fromarray(pixels)


def keep_largest(part, share, *, reach=2):
    """Keep the solid core of the largest piece (and of any at least `share` of its size) and
    what lies within `reach` px of it: one hat, not the strands and face lines the model drew
    hanging from its brim. Thin lines are cut off by eroding first, so touching does not count."""
    from sprite_gen.frames.parts_segments import _components
    pixels = np.asarray(part).copy()
    mask = pixels[..., 3] > 0
    core = mask & np.all([_shifted(mask, dy, dx, False) for dy, dx in _STEPS], axis=0)
    components = list(_components(core))
    if not components:
        return part
    biggest = max(int(c.sum()) for c in components)
    kept = np.any([c for c in components if c.sum() >= share*biggest], axis=0)
    for _ in range(reach):
        kept = kept | np.any([_shifted(kept, dy, dx, False) for dy, dx in _STEPS], axis=0)
    pixels[mask & ~kept] = 0
    return Image.fromarray(pixels)


def drop_specks(part, minimum, *, luma=60):
    """Remove 8-connected bits under `minimum` px with no outline ink: crumbs of a repainted
    face. A mouth or eyebrow as small carries dark ink and stays."""
    from sprite_gen.frames.parts_segments import _components
    pixels = np.asarray(part).copy()
    dark = pixels[..., :3].astype(np.int32) @ np.array([299, 587, 114]) < luma*1000
    for component in _components(pixels[..., 3] > 0):
        if component.sum() < minimum and not (dark & component).any():
            pixels[component] = 0
    return Image.fromarray(pixels)


def inside_edge(part, under, depth):
    """Keep only pixels at least `depth` px inside the under silhouette: features drawn on a face
    (eyes, mouth), not the profile line the model redrew a pixel off."""
    inner = np.asarray(under)[..., 3] > 0
    for _ in range(depth):
        inner = inner & np.all([_shifted(inner, dy, dx, False) for dy, dx in _STEPS], axis=0)
    pixels = np.asarray(part).copy()
    pixels[~inner] = 0
    return Image.fromarray(pixels)


def prune_spurs(part, rounds):
    """Drop 1 px tails (at most one opaque 4-neighbour), `rounds` times: strands hanging off a cut."""
    pixels = np.asarray(part).copy()
    for _ in range(rounds):
        alpha = pixels[..., 3] > 0
        padded = np.pad(alpha, 1)
        neighbours = padded[:-2, 1:-1].astype(int) + padded[2:, 1:-1] + padded[1:-1, :-2] + padded[1:-1, 2:]
        tails = alpha & (neighbours <= 1)
        if not tails.any():
            break
        pixels[tails] = 0
    return Image.fromarray(pixels)


def overlap(drawn, body):
    """(IoU of the drawn figure and the body, share of the body the figure still covers)."""
    drawn_alpha, body_alpha = np.asarray(drawn)[..., 3] > 0, np.asarray(body)[..., 3] > 0
    both = float((drawn_alpha & body_alpha).sum())
    return round(both / max(1, (drawn_alpha | body_alpha).sum()), 3), round(both / max(1, body_alpha.sum()), 3)


def misaligned(alignment, coverage=None):
    """The drawn figure does not sit on the body (rescaled, shifted or dropped by the model).

    A part that adds a lot (big hair, a hood) lowers the IoU of a figure that sits right;
    it still covers the body, so between the two limits the coverage decides.
    """
    if alignment is None:
        return False
    if alignment < LOOSE_ALIGNMENT:
        return True
    return alignment < MIN_ALIGNMENT and (coverage is None or coverage < MIN_COVERAGE)


def cut_cell(drawn, body, *, method="difference", body_threshold=None, near_body=None, min_island=0, outline=False,
             fill_holes=0, close_body=0, colors=None, cover_threshold=24, keep_palette=None, prune=0, rim=0,
             inset=0, specks=0, rescue=None, against=None, largest=None):
    """One drawn cell to the part alone, on the cell's own crop of the canvas.

    `difference` removes what still matches the body (garments drawn over a dressed sheet);
    `covering` keeps everything the drawing changed, whole (hair, hats, loose hoods).
    `part-only` preserves an independently drawn layer without body subtraction or filling.
    Returns the part and {alignment with the body, coverage of the body, holes filled}.
    """
    contract._enum(*METHODS)(method, "method")
    if method == "part-only":
        # The provider already drew an independent layer. Never subtract matching body
        # colors, clip to its silhouette, or fill the layer with body-derived pixels.
        part = drawn.copy()
        if colors is not None:
            part = pixel_grid.snap(part, colors)
        return part, {"alignment": None, "coverage": None, "holes": 0}
    if near_body is not None:
        drawn = within_body(drawn, body, near_body)
    alignment = coverage = None
    part = drawn
    if body_threshold is not None or method == "covering":
        # A dressed cell redraws the body: its figure should cover the body's own pixels.
        # A model that rescaled or shifted the page shows up here before anything is cut.
        alignment, coverage = overlap(drawn, body)
    if method == "covering":
        part = cover(drawn, body, threshold=cover_threshold)
    elif body_threshold is not None:
        part = subtract_body(drawn, body, body_threshold)
        if min_island:
            part = drop_islands(part, min_island, body=body)
        if outline:
            part = keep_outline(part, drawn)
    if rescue is not None and colors is not None and method == "difference":
        # The difference cut drops what is near the body's color, but a far leg's dark denim
        # sits next to charcoal shorts: changed pixels clearly nearer the part's palette than to
        # anything under them nearby come back.
        part = add_back(part, drawn, body, colors, rescue)
    if keep_palette is not None and colors is not None:
        # An image model often repaints the body it was asked to keep (a darker face under new
        # hair): such pixels changed, but they are far from every color of the part's design.
        part = near_palette(part, colors, keep_palette, under=body, against=against)
        part = lone_outline(part, body)
        if method == "covering":
            # Fill the part's enclosed gaps (a strand matching the skull's outline), but only with
            # pixels that pass the same palette test: a face framed by bangs is enclosed too.
            mask = np.asarray(part)[..., 3] > 0
            gaps = Image.fromarray(np.where((enclosed(mask) & (np.asarray(drawn)[..., 3] > 0))[..., None],
                                            np.asarray(drawn), 0).astype(np.uint8))
            mask |= np.asarray(near_palette(gaps, colors, keep_palette, under=body, against=against))[..., 3] > 0
            part = keep_outline(Image.fromarray(np.where(mask[..., None], np.asarray(drawn), 0).astype(np.uint8)), drawn, luma=60)
    if inset:
        part = inside_edge(part, body, inset)
    if specks:
        part = drop_specks(part, specks)
    if largest is not None:
        part = keep_largest(part, largest)
    if prune:
        part = prune_spurs(part, prune)
    if rim:
        part = cover_rim(part, drawn, body, rim)
    holes = 0
    if fill_holes:
        part, holes = pixel_grid.fill_small_holes(part, fill_holes)
    if close_body:
        part, closed = pixel_grid.close_over(part, body, neighbours=close_body)
        holes += closed
    if colors is not None:
        part = pixel_grid.snap(part, colors)
    return part, {"alignment": alignment, "coverage": coverage, "holes": holes}


def slice_sheet(assembly, guide=None, art=None, out=None, *, part_id, slot, node, socket, piece="piece", design_id=None,
                binding_id=None, tolerance=48, body_threshold=None, mode="raster", pages=(), classes=None,
                palette=None, outline=False, near_body=None, min_island=0, region=None, fill_holes=0,
                close_body=0, by_pose=False, method="difference"):
    root, index, registry, _ = parts_motion._active(assembly)
    pairs = list(pages) + ([(guide, art)] if guide is not None else [])
    if not pairs or any(g is None or a is None for g, a in pairs) or out is None:
        raise contract.PartsContractError("slicing needs --out and a guide with its drawn art (--guide/--art or --page)")
    motion = registry[index["motion"]["id"]]
    rig = registry[index["rig"]["id"]]
    topology = registry[rig["topology"]["id"]]
    if socket not in {s["id"] for s in topology["sockets"]}:
        raise contract.PartsContractError(f"unknown topology socket: {socket}")
    if not 0 <= tolerance <= 255 or (body_threshold is not None and not 0 <= body_threshold <= 255):
        raise contract.PartsContractError("tolerance and body threshold must be 0..255")
    if region is not None:
        region = _crop(region, [rig["canvas"]["width"], rig["canvas"]["height"]]) if isinstance(region, str) else list(region)
    if not 0 <= min_island <= 4096:
        raise contract.PartsContractError("--min-island takes 0..4096 px")
    if near_body is not None and not 0 <= near_body <= 32:
        raise contract.PartsContractError("--within-body takes 0..32 px")
    if outline and body_threshold is None:
        raise contract.PartsContractError("outline restore applies to dressed sheets (--body-threshold)")
    groups = None
    if classes is not None:
        groups = _read_classes(classes, index["motion"])
        if groups["socket"] != socket:
            raise contract.PartsContractError("pose classes were grouped around a different socket")
    try:
        colors = pixel_grid.read_palette(palette)
    except pixel_grid.GridError as exc:
        raise contract.PartsContractError(str(exc)) from exc
    clips = {clip["id"]: clip for clip in motion["clips"]}
    load = compose_parts.AssetLoader(root, index)
    out = Path(out).absolute()
    drawn_samples, report = {}, []
    for entry, sample, reduced, sheet in drawn_cells(pairs, index, clips, tolerance):
        crop, canvas = sheet["crop"], sheet["canvas"]
        if (entry["clip"], entry["sample"]) in drawn_samples:
            raise contract.PartsContractError(f"{entry['clip']}/{entry['sample']} is drawn on two pages")
        body = _under(sheet, entry, sample, motion, index, registry, topology, load).crop(tuple(crop))
        reduced, info = cut_cell(reduced, body, method=method, body_threshold=body_threshold, near_body=near_body,
                                 min_island=min_island, outline=outline, fill_holes=fill_holes, close_body=close_body,
                                 colors=colors)
        alignment, coverage, holes = info["alignment"], info["coverage"], info["holes"]
        native = Image.new("RGBA", tuple(canvas))
        native.alpha_composite(reduced, tuple(crop[:2]))
        if region is not None:
            # Authored rig-canvas zone the garment may occupy; anything else is redraw debris.
            kept = Image.new("RGBA", tuple(canvas))
            kept.paste(native.crop(tuple(region)), tuple(region[:2]))
            native = kept
        location = sample["sockets"].get(socket)
        if location is None:
            raise contract.PartsContractError(f"{entry['clip']}/{entry['sample']} has no socket {socket}")
        filename = f"{entry['clip']}/{entry['sample']}.png"
        target = out/filename
        target.parent.mkdir(parents=True, exist_ok=True)
        data = assets.png_bytes(native)
        target.write_bytes(data)
        pixels = int(np.count_nonzero(np.asarray(native)[..., 3]))
        drawn_samples[entry["clip"], entry["sample"]] = (filename, data, pixels, list(location["position"]))
        report.append({"clip": entry["clip"], "sample": entry["sample"], "pixels": pixels, "file": filename,
                       "holes_filled": holes, "alignment": alignment, "coverage": coverage})
    # Without classes each drawn sample is its own drawing; with them members reuse their representative.
    owners = {key: key for key in drawn_samples}
    if groups is not None:
        for group in groups["classes"]:
            rep = (group["representative"]["clip"], group["representative"]["sample"])
            if rep not in drawn_samples:
                raise contract.PartsContractError(f"class representative {rep[0]}/{rep[1]} has no drawn cell")
            for member in group["members"]:
                owners.setdefault((member["clip"], member["sample"]), rep)
    if by_pose:
        if groups is not None:
            raise contract.PartsContractError("--by-pose and --classes are two ways to share drawings; choose one")
        pose_map = {}
        for (clip_id, sample_id), (filename, data, pixels, pivot) in drawn_samples.items():
            sample = next(s for s in clips[clip_id]["samples"] if s["id"] == sample_id)
            if sample["pose_id"] is None:
                raise contract.PartsContractError(f"{clip_id}/{sample_id} has no pose_id to draw by")
            if sample["pose_id"] in pose_map:
                raise contract.PartsContractError(f"pose {sample['pose_id']} is drawn twice")
            pose_map[sample["pose_id"]] = {
                "image": {"file": filename, "sha256": assets.sha256(data)}, "format": "full-canvas",
                "pivot": pivot, "target_socket": socket,
                "visible": bool(pixels) and sample["sockets"][socket]["visible"]}
        wanted = {s["pose_id"] for clip in motion["clips"] if clip["state"] in index["states"] for s in clip["samples"]}
        spec = {"kind": "parts-pack-import", "schema_version": 1, "id": part_id, "design_id": design_id or part_id,
                "role": "part", "style": rig["style"], "rig": index["rig"], "motion": index["motion"], "view": rig["view"],
                "binding_id": binding_id or part_id + ".binding", "occupies": [slot], "states": [],
                "pieces": [{"id": piece, "slot": slot, "node": node, "mode": mode, "pose_map": pose_map}]}
        atomic_write_text(out/"import.json", workspace.json_bytes(spec).decode("utf-8"))
        return {"policy": SHEET_POLICY, "spec": str(out/"import.json"), "source_root": str(out),
                "poses": len(pose_map), "drawn": len(report),
                "empty": [f"{r['clip']}/{r['sample']}" for r in report if not r["pixels"]],
                "missing_requested_poses": sorted(str(p) for p in wanted - set(pose_map)),
                "misaligned": _misaligned(report),
                "results": report, "status": "draft", "release_ready": False}
    sample_map = {}
    for (clip_id, sample_id), owner in owners.items():
        filename, data, pixels, pivot = drawn_samples[owner]
        sample = next(s for s in clips[clip_id]["samples"] if s["id"] == sample_id)
        location = sample["sockets"].get(socket)
        if location is None:
            raise contract.PartsContractError(f"{clip_id}/{sample_id} has no socket {socket}")
        # The pivot is the drawing's own socket: its sample lands at (0,0), a member moves with its socket.
        sample_map.setdefault(clip_id, {})[sample_id] = {
            "image": {"file": filename, "sha256": assets.sha256(data)}, "format": "full-canvas",
            "pivot": pivot, "target_socket": socket, "visible": bool(pixels) and location["visible"]}
    missing = [f"{clip['id']}/{s['id']}" for clip in motion["clips"] if clip["state"] in index["states"]
               for s in clip["samples"] if s["id"] not in sample_map.get(clip["id"], {})]
    spec = {"kind": "parts-pack-import", "schema_version": 1, "id": part_id, "design_id": design_id or part_id,
            "role": "part", "style": rig["style"], "rig": index["rig"], "motion": index["motion"], "view": rig["view"],
            "binding_id": binding_id or part_id + ".binding", "occupies": [slot], "states": [],
            "pieces": [{"id": piece, "slot": slot, "node": node, "mode": mode, "sample_map": sample_map}]}
    atomic_write_text(out/"import.json", workspace.json_bytes(spec).decode("utf-8"))
    return {"policy": SHEET_POLICY, "spec": str(out/"import.json"), "source_root": str(out),
            "samples": sum(len(m) for m in sample_map.values()), "drawn": len(report),
            "empty": [f"{r['clip']}/{r['sample']}" for r in report if not r["pixels"]],
            "missing_requested_samples": missing, "misaligned": _misaligned(report), "results": report,
            "status": "draft", "release_ready": False}


MIN_ALIGNMENT = 0.7  # judged drawn pages: aligned cells 0.90-0.97, a page the model rescaled ~0.35
LOOSE_ALIGNMENT = 0.5  # big hair on a figure that sits right: IoU 0.60-0.75, body coverage 0.87-0.95
MIN_COVERAGE = 0.8  # the model drew the part alone or shifted the figure: coverage ~0.4


def _misaligned(report):
    """Dressed cells whose figure does not sit on the body: redraw that page, do not trust the cut."""
    return [f"{r['clip']}/{r['sample']}" for r in report if misaligned(r["alignment"], r.get("coverage"))]


def add_sheet_arguments(parser: argparse.ArgumentParser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--clips", type=lambda v: [c for c in v.split(",") if c], help="comma-separated clip IDs; default requested states")
    parser.add_argument("--scale", type=int, default=4, help="integer upscale of each native pixel (default 4)")
    parser.add_argument("--crop", help="left,top,right,bottom rig-canvas region drawn per cell; default whole canvas")
    parser.add_argument("--key", default="#ff00ff", help="flat background key color (default #ff00ff)")
    parser.add_argument("--gutter", type=int, default=8)
    parser.add_argument("--columns", type=int)
    parser.add_argument("--part", default="part", help="part name used in the returned prompt hint")
    parser.add_argument("--classes", type=Path, help="parts-pose-classes JSON: draw only each class representative")
    parser.add_argument("--page-size", type=int, help="at most this many cells per sheet; pages go to page-N/ folders")
    parser.add_argument("--aspect", help="pad each sheet to this W:H ratio (an image model's output, e.g. 1:1 or 3:2)")
    parser.add_argument("--by-pose", action="store_true", help="one cell per pose_id instead of one per sample")
    parser.add_argument("--wearing", action="append", default=None,
                        help="active part ID already worn in every cell (repeatable); slicing removes it again")
    parser.add_argument("--poses", type=lambda v: [p for p in v.split(",") if p],
                        help="with --by-pose: comma-separated pose IDs to draw (new or failed poses only)")


def add_slice_arguments(parser: argparse.ArgumentParser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--guide", type=Path, help="pose-sheet.json from parts-pose-sheet")
    parser.add_argument("--art", type=Path, help="drawn sheet image")
    parser.add_argument("--page", action="append", default=[], dest="pages", type=_page_pair,
                        help="GUIDE=ART for one page of a paged sheet (repeatable)")
    parser.add_argument("--classes", type=Path, help="parts-pose-classes JSON: members reuse their representative's drawing")
    parser.add_argument("--palette", type=Path, help="JSON list (or {colors: [...]}) of RGB colors to snap to")
    parser.add_argument("--outline", action="store_true",
                        help="with --body-threshold: keep dark dressed pixels touching the part (its outline on the body's)")
    parser.add_argument("--min-island", type=int, default=0,
                        help="with --body-threshold: drop separated fragments smaller than this many px")
    parser.add_argument("--fill-holes", type=int, default=0,
                        help="fill enclosed holes of at most this many px with the bordering color (skin showing through)")
    parser.add_argument("--close-over-body", type=int, default=0, dest="close_body",
                        help="cover body pixels with at least this many of 8 neighbours in the layer (garment fell short)")
    parser.add_argument("--region", help="left,top,right,bottom rig-canvas zone the part may occupy; pixels outside are dropped")
    parser.add_argument("--within-body", type=int, dest="near_body",
                        help="drop drawn pixels farther than this many px from the body silhouette (stray fringe)")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--part-id", required=True)
    parser.add_argument("--slot", required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--socket", required=True, help="target socket pinned as each sample's pivot")
    parser.add_argument("--piece", default="piece")
    parser.add_argument("--design-id")
    parser.add_argument("--binding-id")
    parser.add_argument("--tolerance", type=int, default=48, help="max channel distance treated as key (default 48)")
    parser.add_argument("--body-threshold", type=int,
                        help="drop pixels within this channel distance of the body; for sheets returned dressed")
    parser.add_argument("--by-pose", action="store_true",
                        help="write a pose_map (one drawing per pose_id, reused by every sample of the pose)")
    parser.add_argument("--method", choices=METHODS, default="difference",
                        help="difference: remove body matches; covering: keep changed shapes; part-only: preserve an independent layer")


def _page_pair(value):
    guide, sep, art = value.partition("=")
    if not sep or not guide or not art:
        raise argparse.ArgumentTypeError("expected GUIDE=ART")
    return Path(guide), Path(art)


def add_class_arguments(parser: argparse.ArgumentParser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path, help="parts-pose-classes JSON to write")
    parser.add_argument("--socket", required=True)
    parser.add_argument("--bounds", required=True, help="left,top,right,bottom region relative to the socket")
    parser.add_argument("--classes", required=True, type=int, help="number of pose groups")
    parser.add_argument("--clips", type=lambda v: [c for c in v.split(",") if c], help="comma-separated clip IDs; default requested states")


def run_classes(**kwargs):
    return workspace._report(lambda: pose_classes(**kwargs))


def run_sheet(**kwargs):
    return workspace._report(lambda: build_sheet(**kwargs))


def run_slice(**kwargs):
    return workspace._report(lambda: slice_sheet(**kwargs))
