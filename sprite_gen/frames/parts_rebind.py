# SPDX-License-Identifier: Apache-2.0
"""Carry a held (one-drawing) part over to the active frozen motion.

A held piece is one image hung on one socket. When the base motion is
replaced, the drawing still fits as long as the new motion annotates the same
socket, so its placement can be repeated for every new sample. A piece drawn per
pose (`pose_map`) is carried by pose: every new sample whose `pose_id` it has
drawn gets that drawing again, so a clip recomposed from existing poses
(`parts-motion-compose`) needs no new art. Other raster pieces and pose-bound
rig-canvas masks are refused; draw them again on the new poses
(`parts-pose-sheet`). The result is an ordinary `parts-pack-import` spec;
nothing is imported or approved here.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from fractions import Fraction
import math
from pathlib import Path

from sprite_gen.curate import parts_motion
from sprite_gen.spec import parts_contract as contract, parts_workspace as workspace
from sprite_gen.spec.runio import atomic_write_text

_PLACEMENT_KEYS = ("image", "pivot", "target_socket", "offset", "allow_clip", "palette_regions", "before", "after", "mask")


def _override(value):
    piece, sep, rest = value.partition("=")
    socket, colon, pivot = rest.partition(":")
    try:
        point = [int(v) for v in pivot.split(",")] if colon else None
    except ValueError:
        point = None
    if not sep or not piece or not socket or (colon and (point is None or len(point) != 2)):
        raise argparse.ArgumentTypeError("expected PIECE=SOCKET or PIECE=SOCKET:X,Y")
    return piece, socket, point


def _secondary(value):
    piece, sep, rest = value.partition("=")
    socket, *numbers = rest.split(":")
    try:
        gain = Fraction(numbers[0]) if numbers else Fraction(1, 2)
        limit = int(numbers[1]) if len(numbers) > 1 else 1
    except (ValueError, ZeroDivisionError):
        gain = limit = None
    if not sep or not piece or not socket or gain is None or len(numbers) > 2 or not 0 < gain <= 2 or not 0 < limit <= 8:
        raise argparse.ArgumentTypeError("expected PIECE=SOCKET[:GAIN[:MAX_PX]] with 0 < GAIN <= 2, 1 <= MAX_PX <= 8")
    return piece, socket, gain, limit


def lag_offsets(positions, gain, limit, *, loop=True):
    """Integer drag for each sample: the socket's move since the previous sample, scaled and clamped."""
    out = []
    for n, here in enumerate(positions):
        before = positions[n-1] if n or loop else here
        out.append([max(-limit, min(limit, int(math.floor(gain*(b-h)+Fraction(1, 2))))) for b, h in zip(before, here)])
    return out


def rebind_spec(assembly, part, out, *, sockets=(), follow=(), secondary=()):
    _, index, registry, _ = parts_motion._active(assembly)
    motion = registry[index["motion"]["id"]]
    if motion["freeze"] is None:
        raise contract.PartsContractError("rebinding requires a frozen active motion")
    rig = registry[index["rig"]["id"]]
    path = Path(part).absolute()
    pack = contract.parse(workspace.decode_json(path.read_bytes()))
    if pack["kind"] != "parts-pack" or len(pack["bindings"]) != 1:
        raise contract.PartsContractError("rebinding takes a parts-pack with one binding")
    binding = pack["bindings"][0]
    overrides = {piece: (socket, pivot) for piece, socket, pivot in sockets}
    follows = {piece: (socket, point) for piece, socket, point in follow}
    if any(point is None for _, point in follows.values()):
        raise contract.PartsContractError("--follow needs PIECE=SOCKET:X,Y, the socket position the drawing was made for")
    drags = {piece: (socket, gain, limit) for piece, socket, gain, limit in secondary}
    unknown = (set(overrides) | set(follows) | set(drags)) - {p["id"] for p in binding["pieces"]}
    if unknown:
        raise contract.PartsContractError(f"unknown pieces: {sorted(unknown)}")
    pieces, carried = [], []
    kept = ("id", "slot", "node", "mode", "provides", "replaces", "join_interface", "palette_regions")
    poses = {sample["pose_id"] for clip in motion["clips"] if clip["state"] in index["states"]
             for sample in clip["samples"]}
    for piece in binding["pieces"]:
        if piece.get("pose_map"):
            if piece["id"] in overrides or piece["id"] in follows or piece["id"] in drags:
                raise contract.PartsContractError(f"{piece['id']}: a pose-drawn piece is carried as drawn; socket options apply to held pieces")
            missing = sorted(str(pose) for pose in poses - set(piece["pose_map"]))
            if missing:
                raise contract.PartsContractError(f"{piece['id']}: the active motion has poses this piece has not drawn: {missing}")
            pose_map = {}
            for pose_id, template in piece["pose_map"].items():
                if pose_id not in poses:
                    continue
                pose_map[pose_id] = {key: deepcopy(template[key]) for key in _PLACEMENT_KEYS} | {
                    "visible": template["visible"], "format": "local", "crop_offset": [0, 0]}
            pieces.append({key: deepcopy(piece[key]) for key in kept} | {"pose_map": pose_map} |
                          ({"cover_rule": deepcopy(piece["cover_rule"])} if "cover_rule" in piece else {}))
            carried.append({"piece": piece["id"], "poses": sorted(pose_map), "socket": None, "pivot": None,
                            "follows": None, "secondary": None})
            continue
        placements = [p for mapping in piece["sample_map"].values() for p in mapping.values()]
        if piece["mode"] != "held" or len({p["image"]["sha256"] for p in placements}) != 1:
            raise contract.PartsContractError(f"{piece['id']}: only held one-drawing pieces can be rebound; redraw raster pieces")
        template = next((p for p in placements if p["visible"]), placements[0])
        if template["mask"] is not None and template["mask"]["space"] == "rig-canvas":
            raise contract.PartsContractError(f"{piece['id']}: a rig-canvas mask belongs to the old poses")
        placement = {key: deepcopy(template[key]) for key in _PLACEMENT_KEYS}
        placement.update(format="local", crop_offset=[0, 0])
        if piece["id"] in overrides:
            socket, pivot = overrides[piece["id"]]
            placement["target_socket"] = socket
            if pivot is not None:
                placement["pivot"] = pivot
        sample_map = {}
        for clip in motion["clips"]:
            if clip["state"] not in index["states"]:
                continue
            drag = None
            if piece["id"] in drags:
                name, gain, limit = drags[piece["id"]]
                if any(name not in sample["sockets"] for sample in clip["samples"]):
                    raise contract.PartsContractError(f"{clip['id']} lacks socket {name} for secondary motion")
                drag = lag_offsets([s["sockets"][name]["position"] for s in clip["samples"]], gain, limit, loop=clip["loop"])
            for number, sample in enumerate(clip["samples"]):
                location = sample["sockets"].get(placement["target_socket"])
                if location is None:
                    raise contract.PartsContractError(f"{clip['id']}/{sample['id']} has no socket {placement['target_socket']}")
                mapped = {**deepcopy(placement), "visible": location["visible"]}
                if piece["id"] in follows:
                    # Keep the slot's socket; move the drawing with another socket's displacement.
                    name, point = follows[piece["id"]]
                    moved = sample["sockets"].get(name)
                    if moved is None:
                        raise contract.PartsContractError(f"{clip['id']}/{sample['id']} has no socket {name}")
                    mapped["offset"] = [o+p-q for o, p, q in zip(placement["offset"], moved["position"], point)]
                    mapped["visible"] = location["visible"] and moved["visible"]
                if drag is not None:
                    # Follow-through: the drawing trails its socket by one sample.
                    mapped["offset"] = [o+d for o, d in zip(mapped["offset"], drag[number])]
                sample_map.setdefault(clip["id"], {})[sample["id"]] = mapped
        pieces.append({key: deepcopy(piece[key]) for key in kept} | {"sample_map": sample_map} |
                      ({"cover_rule": deepcopy(piece["cover_rule"])} if "cover_rule" in piece else {}))
        carried.append({"piece": piece["id"], "socket": placement["target_socket"], "pivot": placement["pivot"],
                        "follows": follows[piece["id"]][0] if piece["id"] in follows else None,
                        "secondary": None if piece["id"] not in drags else
                            {"socket": drags[piece["id"]][0], "gain": str(drags[piece["id"]][1]), "max_px": drags[piece["id"]][2]}})
    spec = {"kind": "parts-pack-import", "schema_version": 1, "id": pack["id"], "design_id": pack["design_id"],
            "role": pack["role"], "body_mode": pack["body_mode"], "style": rig["style"], "rig": index["rig"],
            "motion": index["motion"], "view": binding["view"], "binding_id": binding["id"],
            **{key: deepcopy(binding[key]) for key in ("occupies", "requires", "excludes", "mirror_safe")},
            **({"covers": deepcopy(binding["covers"])} if "covers" in binding else {}),
            "pieces": pieces}
    out = Path(out).absolute()
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(out, workspace.json_bytes(spec).decode("utf-8"))
    return {"spec": str(out), "source_root": str(path.parent), "part": pack["id"], "pieces": carried,
            "samples": sum(len(clip["samples"]) for clip in motion["clips"] if clip["state"] in index["states"]),
            "status": "draft", "release_ready": False}


def body_spec(assembly, out, *, body_id, design_id=None, binding_id=None):
    """A flat body pack whose drawing for each sample is that sample's own motion raster."""
    root, index, registry, _ = parts_motion._active(assembly)
    motion = registry[index["motion"]["id"]]
    if motion["freeze"] is None:
        raise contract.PartsContractError("a motion body requires a frozen active motion")
    rig = registry[index["rig"]["id"]]
    topology = registry[rig["topology"]["id"]]
    root_node = next(node["id"] for node in topology["nodes"] if node.get("parent") is None)
    root_socket = next((s["id"] for s in topology["sockets"] if s["node"] == root_node), None)
    if root_socket is None:
        raise contract.PartsContractError("the topology root has no socket to hang the body on")
    folder = (root / index["documents"][motion["id"]]).parent
    sample_map = {}
    for clip in motion["clips"]:
        if clip["state"] in index["states"]:
            sample_map[clip["id"]] = {sample["id"]: {
                "image": deepcopy(sample["image"]), "format": "full-canvas",
                "pivot": list(sample["sockets"][root_socket]["position"]), "target_socket": root_socket}
                for sample in clip["samples"]}
    spec = {"kind": "parts-pack-import", "schema_version": 1, "id": body_id, "design_id": design_id or body_id,
            "role": "body", "body_mode": "flat", "style": rig["style"], "rig": index["rig"], "motion": index["motion"],
            "view": rig["view"], "binding_id": binding_id or body_id + ".binding", "occupies": [],
            "pieces": [{"id": "body", "slot": None, "node": root_node, "mode": "raster", "provides": [root_node],
                        "sample_map": sample_map}]}
    out = Path(out).absolute()
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(out, workspace.json_bytes(spec).decode("utf-8"))
    return {"spec": str(out), "source_root": str(folder), "body": body_id,
            "samples": sum(len(m) for m in sample_map.values()), "status": "draft", "release_ready": False}


def add_body_arguments(parser: argparse.ArgumentParser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path, help="parts-pack-import spec to write")
    parser.add_argument("--body-id", required=True)
    parser.add_argument("--design-id")
    parser.add_argument("--binding-id")


def run_body(**kwargs):
    return workspace._report(lambda: body_spec(**kwargs))


def add_arguments(parser: argparse.ArgumentParser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--part", required=True, type=Path, help="part.json of the held part to carry over")
    parser.add_argument("--out", required=True, type=Path, help="parts-pack-import spec to write")
    parser.add_argument("--socket", action="append", type=_override, dest="sockets", default=[],
                        help="PIECE=SOCKET[:X,Y] hang a piece on another socket, optionally with a new pivot")
    parser.add_argument("--follow", action="append", type=_override, default=[],
                        help="PIECE=SOCKET:X,Y keep the piece's socket but offset it by SOCKET's move from X,Y")
    parser.add_argument("--secondary", action="append", type=_secondary, default=[],
                        help="PIECE=SOCKET[:GAIN[:MAX_PX]] trail SOCKET by one sample (follow-through; default 1/2, 1px)")


def run(**kwargs):
    return workspace._report(lambda: rebind_spec(**kwargs))
