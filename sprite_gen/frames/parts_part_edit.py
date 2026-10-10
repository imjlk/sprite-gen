# SPDX-License-Identifier: Apache-2.0
"""Bounded, source-preserving pixel edits create reviewable candidates, never approval."""

from copy import deepcopy
from pathlib import Path
from PIL import Image
from sprite_gen._deps import np
from sprite_gen.curate import parts_motion
from sprite_gen.frames import parts_production as p
from sprite_gen.spec import (
    parts_contract as contract,
    parts_workspace as workspace,
)

_OP = contract._record(
    {
        "type": contract._enum(
            "translate",
            "recolor",
            "erase",
            "restore-source",
            "fit-alpha-box",
            "rotate",
            "fill-small-holes",
        ),
        "bounds": contract._nullable(contract._rectangle),
        "source_bounds": contract._nullable(contract._rectangle),
        "piece": contract._nullable(contract._id),
        "max_pixels": contract._positive,
        "dx": contract._integer,
        "dy": contract._integer,
        "angle": contract._integer,
        "from_color": contract._nullable(contract._string),
        "to_color": contract._nullable(contract._string),
    },
    {
        "bounds": None,
        "source_bounds": None,
        "piece": None,
        "max_pixels": 12,
        "dx": 0,
        "dy": 0,
        "angle": 0,
        "from_color": None,
        "to_color": None,
    },
)


def translate_image(image, dx, dy, bounds=None):
    """Preview and saved edits share exact native-pixel translation and clipping gates."""
    dx = contract._integer(dx, "dx")
    dy = contract._integer(dy, "dy")
    raw = np.asarray(image.convert("RGBA")).copy()
    height, width = raw.shape[:2]
    if abs(dx) > width or abs(dy) > height:
        raise contract.PartsContractError("translation exceeds native canvas")
    yy, xx = np.indices((height, width))
    region = np.ones((height, width), dtype=bool)
    if bounds:
        a, b, c, d = contract._rectangle(bounds, "bounds")
        if not 0 <= a < c <= width or not 0 <= b < d <= height:
            raise contract.PartsContractError(
                "edit region must be inside the native canvas"
            )
        region = (xx >= a) & (xx < c) & (yy >= b) & (yy < d)
    ink = region & (raw[..., 3] > 0)
    if np.any(
        ink & ((xx + dx < 0) | (xx + dx >= width) | (yy + dy < 0) | (yy + dy >= height))
    ):
        raise contract.PartsContractError("edit clips source pixels")
    moved = np.zeros_like(raw)
    ys, xs = np.nonzero(ink)
    moved[ys + dy, xs + dx] = raw[ys, xs]
    raw[ink] = 0
    layer = Image.fromarray(raw)
    layer.alpha_composite(Image.fromarray(moved))
    return layer


def apply_operations(
    images, record, operations, *, sampling="nearest", allow_noop=False
):
    """Apply the same bounded edits to a single retained native raster."""
    raw = np.asarray(images["part"].convert("RGBA")).copy()
    height, width = raw.shape[:2]
    original = raw.copy()
    filled_holes = 0
    yy, xx = np.indices((height, width))
    for op in operations:
        if op["source_bounds"] is not None and op["type"] != "fit-alpha-box":
            raise contract.PartsContractError(
                "source_bounds is only supported for fit-alpha-box"
            )
        bounds = op["bounds"]
        region = np.ones((height, width), dtype=bool)
        if bounds:
            a, b, c, d = bounds
            if not 0 <= a < c <= width or not 0 <= b < d <= height:
                raise contract.PartsContractError(
                    "edit region must be inside the native canvas"
                )
            region = (xx >= a) & (xx < c) & (yy >= b) & (yy < d)
        if op["type"] == "fit-alpha-box":
            if not bounds:
                raise contract.PartsContractError(
                    "fitting requires an explicit target region"
                )
            raster = Image.fromarray(raw)
            selected = np.ones((height, width), dtype=bool)
            if op["source_bounds"]:
                left, top, right, bottom = op["source_bounds"]
                if not 0 <= left < right <= width or not 0 <= top < bottom <= height:
                    raise contract.PartsContractError(
                        "source region must be inside the native canvas"
                    )
                selected = (xx >= left) & (xx < right) & (yy >= top) & (yy < bottom)
                source = raw.copy()
                source[~selected] = 0
                raster = Image.fromarray(source)
            source_bounds = raster.getbbox()
            if source_bounds is None:
                raise contract.PartsContractError("cannot fit an empty part")
            cropped = raster.crop(source_bounds)
            factor = min((c - a) / cropped.width, (d - b) / cropped.height)
            size = (
                max(1, min(c - a, round(cropped.width * factor))),
                max(1, min(d - b, round(cropped.height * factor))),
            )
            cropped = (
                cropped.convert("RGBa")
                .resize(
                    size,
                    Image.Resampling.BILINEAR
                    if sampling == "smooth"
                    else Image.Resampling.NEAREST,
                )
                .convert("RGBA")
            )
            remainder = raw.copy()
            remainder[selected] = 0
            fitted = Image.fromarray(remainder)
            fitted.alpha_composite(
                cropped, (a + (c - a - size[0]) // 2, b + (d - b - size[1]) // 2)
            )
            raw = np.asarray(fitted).copy()
        elif op["type"] == "fill-small-holes":
            if not bounds or op["max_pixels"] > 64:
                raise contract.PartsContractError(
                    "hole repair requires a bounded region and max_pixels 1..64"
                )
            from sprite_gen.frames import pixel_grid

            repaired, count = pixel_grid.fill_small_holes(
                Image.fromarray(raw).crop(tuple(bounds)), op["max_pixels"]
            )
            raw[b:d, a:c] = np.asarray(repaired)
            filled_holes += count
        elif op["type"] == "rotate":
            if bounds or abs(op["angle"]) > 30:
                raise contract.PartsContractError(
                    "rotate a whole piece by at most 30 degrees"
                )
            raster = Image.fromarray(raw)
            box = raster.getbbox()
            if box is None:
                raise contract.PartsContractError("cannot rotate an empty part")
            rotated = (
                raster.crop(box)
                .convert("RGBa")
                .rotate(
                    op["angle"],
                    expand=True,
                    resample=Image.Resampling.BILINEAR
                    if sampling == "smooth"
                    else Image.Resampling.NEAREST,
                )
                .convert("RGBA")
            )
            x = round((box[0] + box[2] - rotated.width) / 2) + op["dx"]
            y = round((box[1] + box[3] - rotated.height) / 2) + op["dy"]
            if (
                x < 0
                or y < 0
                or x + rotated.width > width
                or y + rotated.height > height
            ):
                raise contract.PartsContractError("rotation clips source pixels")
            fitted = Image.new("RGBA", raster.size)
            fitted.alpha_composite(rotated, (x, y))
            raw = np.asarray(fitted).copy()
        elif op["type"] == "translate":
            raw = np.asarray(
                translate_image(Image.fromarray(raw), op["dx"], op["dy"], bounds)
            ).copy()
        elif op["type"] == "recolor":
            if not op["from_color"] or not op["to_color"]:
                raise contract.PartsContractError("recolor needs exact from/to colors")
            target = (
                region
                & (raw[..., 3] > 0)
                & np.all(raw[..., :3] == p._rgb(op["from_color"]), axis=-1)
            )
            raw[target, :3] = p._rgb(op["to_color"])
        elif op["type"] == "erase":
            if not bounds:
                raise contract.PartsContractError("erase requires an explicit region")
            raw[region] = 0
        else:
            if not bounds:
                raise contract.PartsContractError(
                    "restoration requires an explicit region"
                )
            if (
                record["method"].get("cut", {}).get("method") != "part-only"
                or "drawn" not in images
            ):
                raise contract.PartsContractError(
                    "source restoration requires retained part-only generation art"
                )
            raw[region] = np.asarray(images["drawn"].convert("RGBA"))[region]
    images["part"] = Image.fromarray(raw)
    images["mask"] = Image.fromarray((raw[..., 3] > 0).astype("uint8") * 255)
    if "render_mask" in images:
        images["render_part"] = p._visible_part(images["part"], images["render_mask"])
    changed = int(np.any(raw != original, axis=-1).sum())
    if not changed and not allow_noop:
        raise contract.PartsContractError("operations do not change any source pixels")
    return images, changed, filled_holes


def edit(assembly, part, pose, operations, *, candidate=None, track=None):
    root, index, registry, _ = parts_motion._active(assembly)
    state = p._load(root, part)
    rows = p._track(state, track)
    row = rows["poses"].get(pose)
    if not row or not row.get("selected"):
        raise contract.PartsContractError("choose a source candidate")
    location = candidate or row["selected"]
    record = p.candidate_record(root, state, pose, location, track=track)
    if record["motion"] != index["motion"]:
        raise contract.PartsContractError(
            "source belongs to a different motion revision"
        )
    operations = contract._list(_OP, nonempty=True)(operations, "operations")
    if len(operations) > 16:
        raise contract.PartsContractError("at most 16 operations per edit")
    base = root / location
    images = {
        name: Image.open(base / ref["file"]).copy()
        for name, ref in record["images"].items()
    }
    if record.get("independent_layers"):
        from sprite_gen.frames.parts_independent import edit_layers

        return edit_layers(
            root,
            state,
            row,
            location,
            record,
            images,
            operations,
            sampling=registry[index["rig"]["id"]].get("sampling", "nearest"),
        )
    if any(op["piece"] is not None for op in operations):
        raise contract.PartsContractError(
            "piece selection needs an independent-layer candidate"
        )
    images, changed, filled_holes = apply_operations(
        images,
        record,
        operations,
        sampling=registry[index["rig"]["id"]].get("sampling", "nearest"),
    )
    new = {k: deepcopy(v) for k, v in record.items() if k not in ("digest", "images")}
    new["method"] = {
        **new["method"],
        "programmatic_edit": {
            "parent": record["digest"],
            "source_candidate": location,
            "operations": operations,
            "changed_pixels": changed,
            "provider_calls": 0,
            "registered_geometry": record["method"]
            .get("programmatic_edit", {})
            .get("registered_geometry", False)
            or any(
                op["type"] in ("translate", "fit-alpha-box")
                for op in record["method"]
                .get("programmatic_edit", {})
                .get("operations", [])
            )
            or any(op["type"] in ("translate", "fit-alpha-box") for op in operations),
        },
    }
    new.update(
        pixels=int((np.asarray(images["part"])[..., 3] > 0).sum()),
        holes_filled=record.get("holes_filled", 0) + filled_holes,
    )
    saved, location = p._publish_candidate(root, part, new, images)
    if location not in row["candidates"]:
        row["candidates"].append(location)
    p._save(root, state)
    return {
        "part": part,
        "pose": pose,
        "preview_candidate": location,
        "digest": saved["digest"],
        "changed_pixels": changed,
        "selected": False,
        "provider_calls": 0,
        "art_approved": False,
        "next": "compare, select, check and build",
    }


def add_arguments(parser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--part", required=True)
    parser.add_argument("--pose", required=True)
    parser.add_argument("--recipe", required=True, type=Path)
    parser.add_argument("--variant", dest="track")
    parser.add_argument("--candidate")


def run(*, recipe, **kwargs):
    return workspace._report(
        lambda: edit(
            **kwargs, operations=workspace.decode_json(Path(recipe).read_bytes())
        )
    )
