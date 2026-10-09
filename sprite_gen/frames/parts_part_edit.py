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
        "type": contract._enum("translate", "recolor", "erase", "restore-source"),
        "bounds": contract._nullable(contract._rectangle),
        "dx": contract._integer,
        "dy": contract._integer,
        "from_color": contract._nullable(contract._string),
        "to_color": contract._nullable(contract._string),
    },
    {"bounds": None, "dx": 0, "dy": 0, "from_color": None, "to_color": None},
)


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
    raw = np.asarray(images["part"].convert("RGBA")).copy()
    height, width = raw.shape[:2]
    original = raw.copy()
    yy, xx = np.indices((height, width))
    for op in operations:
        bounds = op["bounds"]
        region = np.ones((height, width), dtype=bool)
        if bounds:
            a, b, c, d = bounds
            if not 0 <= a < c <= width or not 0 <= b < d <= height:
                raise contract.PartsContractError(
                    "edit region must be inside the native canvas"
                )
            region = (xx >= a) & (xx < c) & (yy >= b) & (yy < d)
        if op["type"] == "translate":
            dx, dy = op["dx"], op["dy"]
            if abs(dx) > width or abs(dy) > height:
                raise contract.PartsContractError("translation exceeds native canvas")
            ink = region & (raw[..., 3] > 0)
            if np.any(
                ink
                & (
                    (xx + dx < 0)
                    | (xx + dx >= width)
                    | (yy + dy < 0)
                    | (yy + dy >= height)
                )
            ):
                raise contract.PartsContractError("edit clips source pixels")
            moved = np.zeros_like(raw)
            ys, xs = np.nonzero(ink)
            moved[ys + dy, xs + dx] = raw[ys, xs]
            raw[ink] = 0
            layer = Image.fromarray(raw)
            layer.alpha_composite(Image.fromarray(moved))
            raw = np.asarray(layer).copy()
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
    if not changed:
        raise contract.PartsContractError("operations do not change any source pixels")
    new = {k: deepcopy(v) for k, v in record.items() if k not in ("digest", "images")}
    new["method"] = {
        **new["method"],
        "programmatic_edit": {
            "parent": record["digest"],
            "source_candidate": location,
            "operations": operations,
            "changed_pixels": changed,
            "provider_calls": 0,
        },
    }
    new.update(pixels=int((raw[..., 3] > 0).sum()), holes_filled=0)
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
