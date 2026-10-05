# SPDX-License-Identifier: Apache-2.0
"""Propose socket coordinates for a motion annotation draft.

One authored reference region (a socket position plus relative bounds on one
sample) is matched against every other sample of the imported candidate with
an integer translation search. The template never changes, so positions do not
drift. Matching reads the final candidate rasters only; it never edits images,
infers anatomy or freezes the motion. Proposals stay `unverified` unless the
caller explicitly accepts samples within an authored cost budget.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path

from PIL import Image, ImageDraw

from sprite_gen.curate import parts_motion
from sprite_gen.qa import parts_stability
from sprite_gen.spec import parts_assets as library, parts_contract as contract, parts_workspace as workspace
from sprite_gen.spec.runio import atomic_write_text

TRACK_POLICY = "parts.socket-template-track.v1"
_TRACK = contract._record({
    "socket": contract._id, "mode": contract._enum("region", "constant"),
    "reference": contract._record({"clip": contract._id, "sample": contract._id, "position": contract._point}),
    "bounds": contract._nullable(contract._rectangle),
    "clips": contract._nullable(contract._list(contract._id, nonempty=True, unique=True)),
    "radius": contract._integer, "alpha_weight": contract._integer,
    "min_alpha": contract._integer, "rgb_threshold": contract._integer,
    "max_cost": contract._nullable(contract._integer), "visible": contract._boolean,
}, {"mode": "region", "bounds": None, "clips": None, "radius": 3, "alpha_weight": 3,
    "min_alpha": 128, "rgb_threshold": 24, "max_cost": None, "visible": True})
_SPEC = contract._record({"kind": contract._enum("parts-socket-track"), "schema_version": contract._positive,
                          "tracks": contract._list(_TRACK, nonempty=True)})


def _cost(template, image, position, track):
    x0, y0, x1, y1 = track["bounds"]
    x, y = position
    delta = parts_stability.measure(template, image.crop((x+x0, y+y0, x+x1, y+y1)),
                                    min_alpha=track["min_alpha"], rgb_threshold=track["rgb_threshold"])
    return delta["alpha_pixels"]*track["alpha_weight"] + delta["rgb_pixels"]


def match(template, image, seed, track):
    """Best integer position near `seed`; ties prefer the smallest move."""
    radius = track["radius"]
    scored = []
    for dy in range(-radius, radius+1):
        for dx in range(-radius, radius+1):
            position = [seed[0]+dx, seed[1]+dy]
            scored.append((_cost(template, image, position, track), abs(dx)+abs(dy), dy, dx, position))
    scored.sort(key=lambda row: row[:4])
    cost, _, dy, dx, position = scored[0]
    # A distinct runner-up at least two pixels away measures ambiguity.
    rival = next((row[0] for row in scored[1:] if max(abs(row[3]-dx), abs(row[2]-dy)) >= 2), None)
    return {"position": position, "cost": cost, "margin": None if rival is None else rival-cost,
            "at_edge": radius > 0 and max(abs(dx), abs(dy)) == radius}


def _load_spec(path):
    path = Path(path).absolute()
    spec = _SPEC(workspace.decode_json(library.read_file(path.parent, path.name, limit=workspace.MAX_JSON_BYTES)), "track")
    if spec["schema_version"] != 1:
        raise contract.PartsContractError("track.schema_version: only version 1 is supported")
    for n, track in enumerate(spec["tracks"]):
        if track["mode"] == "region" and track["bounds"] is None:
            raise contract.PartsContractError(f"track.tracks[{n}]: region mode requires bounds")
        if not 0 <= track["radius"] <= 16 or track["alpha_weight"] < 0:
            raise contract.PartsContractError(f"track.tracks[{n}]: radius must be 0..16 and alpha_weight nonnegative")
    return spec


def track_sockets(assembly, candidate, annotations, spec, out, *, accept=False, preview=None):
    root, index, registry, _ = parts_motion._active(assembly)
    candidate_file = library.safe_path(root, candidate)
    snapshot = library.SourceReader(candidate_file.parent)
    metadata = parts_motion.seal_aux(workspace.decode_json(snapshot.read(candidate_file.name)), parts_motion._CANDIDATE)
    if metadata["request"]["rig"] != index["rig"]:
        raise contract.PartsContractError("candidate rig differs from selected workspace")
    draft = parts_motion.seal_aux(parts_motion._input(annotations), parts_motion._ANNOTATIONS)
    draft.pop("digest")
    if draft["candidate_digest"] != metadata["digest"] or draft["id"] != metadata["id"]:
        raise contract.PartsContractError("annotations refer to a different candidate")
    rig = registry[index["rig"]["id"]]
    topology = registry[rig["topology"]["id"]]
    size = (rig["canvas"]["width"], rig["canvas"]["height"])
    clips = {clip["id"]: clip for clip in metadata["motion"]["clips"]}
    spec = _load_spec(spec)
    sockets = {socket["id"] for socket in topology["sockets"]}
    images = {}

    def image(clip_id, sample):
        key = (clip_id, sample["id"])
        if key not in images:
            if draft["clips"].get(clip_id, {}).get(sample["id"], {}).get("image_sha256") != sample["image"]["sha256"]:
                raise contract.PartsContractError(f"annotation raster hash differs from {clip_id}/{sample['id']}")
            images[key] = library.load_image(snapshot, sample["image"], size=size).convert("RGBA")
        return images[key]

    rows, claimed = [], set()
    for track in spec["tracks"]:
        if track["socket"] not in sockets:
            raise contract.PartsContractError(f"unknown topology socket: {track['socket']}")
        ref = track["reference"]
        if ref["clip"] not in clips or ref["sample"] not in {s["id"] for s in clips[ref["clip"]]["samples"]}:
            raise contract.PartsContractError(f"track reference sample is missing: {ref['clip']}/{ref['sample']}")
        targets = track["clips"] or list(clips)
        if set(targets) - set(clips):
            raise contract.PartsContractError(f"track clips are missing: {sorted(set(targets)-set(clips))}")
        # Different clips may need their own reference drawing of one socket.
        if claimed & {(track["socket"], clip_id) for clip_id in targets}:
            raise contract.PartsContractError(f"socket {track['socket']} is tracked twice in one clip")
        claimed |= {(track["socket"], clip_id) for clip_id in targets}
        template = None
        if track["mode"] == "region":
            x0, y0, x1, y1 = track["bounds"]
            x, y = ref["position"]
            sample = next(s for s in clips[ref["clip"]]["samples"] if s["id"] == ref["sample"])
            template = image(ref["clip"], sample).crop((x+x0, y+y0, x+x1, y+y1))
            if template.getchannel("A").point(lambda a: 255 if a >= track["min_alpha"] else 0).getbbox() is None:
                raise contract.PartsContractError(f"track {track['socket']}: reference region is transparent")
        for clip_id in targets:
            seed = ref["position"]
            for sample in clips[clip_id]["samples"]:
                if template is None:
                    image(clip_id, sample)
                    found = {"position": list(ref["position"]), "cost": 0, "margin": None, "at_edge": False}
                else:
                    found = match(template, image(clip_id, sample), seed, track)
                    seed = found["position"]
                within = track["max_cost"] is None or found["cost"] <= track["max_cost"]
                status = "proposed" if within and not found["at_edge"] else "needs-review"
                confidence = "verified" if accept and status == "proposed" else "unverified"
                draft["clips"][clip_id][sample["id"]]["sockets"][track["socket"]] = {
                    "position": found["position"], "visible": track["visible"], "confidence": confidence}
                rows.append({"socket": track["socket"], "clip": clip_id, "sample": sample["id"],
                             "mode": track["mode"], "bounds": track["bounds"], **found,
                             "status": status, "confidence": confidence})
    validated = parts_motion._ANNOTATIONS(draft, "annotations")
    out = Path(out).absolute()
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(out, workspace.json_bytes(validated).decode("utf-8"))
    if preview is not None:
        _preview(Path(preview), clips, image, rows)
    pending = sorted({f"{clip}/{sample}.{socket}" for clip, samples in validated["clips"].items()
                      for sample, value in samples.items() for socket, location in value["sockets"].items()
                      if location["confidence"] != "verified"})
    return {"policy": TRACK_POLICY, "annotations": str(out), "tracked": len(rows),
            "needs_review": sum(row["status"] == "needs-review" for row in rows),
            "unverified_sockets": len(pending), "unverified_examples": pending[:8],
            "accepted": accept, "results": rows, "status": "draft", "release_ready": False}


def _preview(path, clips, image, rows, *, scale=2):
    """Contact sheet: every tracked sample with its region box and socket cross."""
    keys = list(dict.fromkeys((row["clip"], row["sample"]) for row in rows))
    if not keys:
        return
    width, height = image(*_sample(clips, *keys[0])).size
    columns = min(8, len(keys))
    sheet = Image.new("RGBA", (columns*width*scale, -(-len(keys)//columns)*height*scale), (236, 236, 228, 255))
    draw = ImageDraw.Draw(sheet)
    for n, key in enumerate(keys):
        ox, oy = n % columns * width * scale, n // columns * height * scale
        sheet.alpha_composite(image(*_sample(clips, *key)).resize((width*scale, height*scale), Image.Resampling.NEAREST), (ox, oy))
        draw.rectangle((ox, oy, ox+width*scale-1, oy+height*scale-1), outline=(200, 200, 190, 255))
        for row in rows:
            if (row["clip"], row["sample"]) != key:
                continue
            color = (40, 140, 90, 255) if row["status"] == "proposed" else (210, 50, 40, 255)
            x, y = (v*scale for v in row["position"])
            bounds = row["bounds"]
            if bounds is not None:
                draw.rectangle((ox+x+bounds[0]*scale, oy+y+bounds[1]*scale,
                                ox+x+bounds[2]*scale-1, oy+y+bounds[3]*scale-1), outline=color)
            draw.line((ox+x-4, oy+y, ox+x+4, oy+y), fill=color)
            draw.line((ox+x, oy+y-4, ox+x, oy+y+4), fill=color)
        draw.text((ox+3, oy+2), f"{key[0]} {key[1]}", fill=(60, 60, 60, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path)


def _sample(clips, clip_id, sample_id):
    return clip_id, next(s for s in clips[clip_id]["samples"] if s["id"] == sample_id)


def add_arguments(parser: argparse.ArgumentParser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--candidate", required=True, help="workspace-relative candidate.json path")
    parser.add_argument("--annotations", required=True, type=Path, help="annotation draft to start from")
    parser.add_argument("--spec", required=True, type=Path, help="parts-socket-track JSON")
    parser.add_argument("--out", required=True, type=Path, help="annotation file to write")
    parser.add_argument("--accept", action="store_true",
                        help="mark proposals within max_cost and away from the search edge as verified")
    parser.add_argument("--preview", type=Path, help="optional PNG contact sheet of the proposals")


def run(**kwargs):
    return workspace._report(lambda: track_sockets(**kwargs))
