# SPDX-License-Identifier: Apache-2.0
"""Measured parts evidence, with explicit gaps in visual/contact knowledge."""
from __future__ import annotations

import argparse
import hashlib
from importlib.resources import files
import math
from pathlib import Path
import platform

from sprite_gen._deps import np
from PIL import __version__ as pillow_version

from sprite_gen.compose import compose_parts, parts_resolve, export_aseprite
from sprite_gen.curate import parts_motion
from sprite_gen.spec import parts_assets as assets, parts_contract as contract, parts_workspace as workspace

QA_POLICY = "parts.evidence.v1"
REVIEW_CHECKS = ("fitting", "occlusion", "motion", "contact", "style")

_STANCE = contract._record({"node": contract._id, "socket": contract._id,
    "samples": contract._list(contract._id, nonempty=True, unique=True), "max_slip_px": contract._integer})
_CONTACTS = contract._record({"kind": contract._enum("parts-contact-annotations"),
    "schema_version": contract._positive, "motion": contract._ref,
    "clips": contract._mapping(contract._record({"world_offsets": contract._mapping(contract._point),
                                              "stances": contract._list(_STANCE)}))})


def toolchain():
    from sprite_gen.compose.parts_build import EXPORT_POLICY
    # Package version alone cannot identify code edited in a source checkout.
    paths = ("spec/parts_contract.py", "spec/parts_assets.py", "compose/parts_resolve.py",
             "compose/compose_parts.py", "compose/compose_layers.py", "qa/inspect_parts.py",
             "curate/parts_review.py", "compose/parts_build.py", "compose/export_aseprite.py")
    digest = hashlib.sha256()
    for name in paths:
        path = files("sprite_gen").joinpath(name)
        if path.is_file():
            digest.update(name.encode() + b"\0" + path.read_bytes())
    return {"python": platform.python_version(), "pillow": pillow_version,
            "numpy": np.__version__, "sprite-gen": export_aseprite._package_version(),
            "export_policy": EXPORT_POLICY, "parts_code_sha256": digest.hexdigest()}


def input_references(appearance, registry):
    result, pending = {}, [contract.reference(appearance)]
    while pending:
        ref = pending.pop()
        if ref["id"] in result:
            continue
        doc = contract._get(registry, ref, None, "qa.inputs")
        result[ref["id"]] = ref
        pending.extend(parts_motion._references(doc))
    return [result[name] for name in sorted(result)]


def _contact_checks(contacts, motion, topology, mirror, canvas_width):
    contact_nodes = {n["id"] for n in topology["nodes"] if n["contact"]}
    if contacts is None:
        return [{"id": "contact-measurement", "status": "unverified" if contact_nodes else "passed",
                 "message": "no stance/world-travel annotation" if contact_nodes else "topology declares no contact nodes"}]
    contacts = _CONTACTS(contacts, "contacts")
    if contacts["schema_version"] != 1 or contacts["motion"] != contract.reference(motion):
        raise contract.PartsContractError("contact annotations differ from frozen motion revision")
    clips = {c["id"]: c for c in motion["clips"]}
    contract._members(contacts["clips"], clips, "contacts.clips")
    sockets = {s["id"]: s["node"] for s in topology["sockets"]}
    checks = []
    for clip_id, declaration in contacts["clips"].items():
        clip = clips[clip_id]
        samples = {s["id"]: s for s in clip["samples"]}
        contract._members(declaration["world_offsets"], samples, "contacts.world_offsets")
        for i, stance in enumerate(declaration["stances"]):
            if stance["node"] not in contact_nodes or sockets.get(stance["socket"]) != stance["node"]:
                raise contract.PartsContractError("stance must name a profile contact node and its socket")
            if stance["max_slip_px"] < 0:
                raise contract.PartsContractError("contact slip tolerance must be nonnegative")
            contract._members(stance["samples"], samples, "contacts.stance.samples")
            order = [next(i for i, s in enumerate(clip["samples"]) if s["id"] == name) for name in stance["samples"]]
            if order != list(range(order[0], order[0] + len(order))):
                raise contract.PartsContractError("stance sample IDs must form an ordered contiguous span")
            points = []
            for name in stance["samples"]:
                sample = samples[name]
                socket = sample["sockets"].get(stance["socket"])
                world = declaration["world_offsets"].get(name)
                if world is None or socket is None or not socket["visible"] or socket["confidence"] != "verified":
                    raise contract.PartsContractError("stance requires explicit world offset and verified visible socket for every sample")
                x, y = socket["position"]
                points.append([world[0] + (canvas_width - 1 - x if mirror else x), world[1] + y])
            drift = max(math.dist(points[0], p) for p in points)
            checks.append({"id": f"contact.{clip_id}.{i}", "status": "passed" if drift <= stance["max_slip_px"] else "error",
                "node": stance["node"], "socket": stance["socket"], "samples": stance["samples"],
                "world_points": points, "max_drift_px": drift, "max_slip_px": stance["max_slip_px"],
                "message": "declared contact socket stability; pixel contact and stance truth require visual review"})
    covered = {s["node"] for c in contacts["clips"].values() for s in c["stances"]}
    if contact_nodes - covered or set(clips) - set(contacts["clips"]):
        checks.append({"id": "contact-coverage", "status": "unverified", "message": "annotations cover only some nodes/clips"})
    if not checks:
        checks.append({"id": "contact-measurement", "status": "unverified", "message": "no declared stance evidence"})
    return checks


def collect(root, index, registry, contacts=None):
    if index["appearance"] is None:
        raise contract.PartsContractError("QA requires an active appearance")
    appearance = registry[index["appearance"]["id"]]
    if appearance["states"] != index["states"]:
        raise contract.PartsContractError("requested states differ from active appearance; create a new selection revision")
    plan = parts_resolve.resolve(appearance, registry.values())
    loader = compose_parts.AssetLoader(root, index)
    inputs = input_references(appearance, registry)
    # Byte-check the frozen motion, annotation and captured original sources,
    # not merely the pieces that happen to be visible in this appearance.
    for ref in inputs:
        doc = registry[ref["id"]]
        if doc["kind"] == "parts-motion":
            if doc["freeze"] is None:
                raise contract.PartsContractError("QA input graph contains an unfrozen motion")
            declarations = [s["image"] for c in doc["clips"] for s in c["samples"]]
            declarations += [doc["freeze"]["annotations"], *doc["freeze"]["source_assets"]]
        elif doc["kind"] == "parts-pack" and doc["import"] is not None:
            declarations = list(doc["import"]["source_assets"])
            for binding in doc["bindings"]:
                for piece in binding["pieces"]:
                    declarations.extend(piece["palette_regions"].values())
                    for mapping in piece["sample_map"].values():
                        for placement in mapping.values():
                            declarations.append(placement["image"])
                            declarations.extend(placement["palette_regions"].values())
                            if placement["mask"] is not None:
                                declarations.append(placement["mask"]["image"])
        else:
            declarations = []
        for asset in declarations:
            local = loader.relative_asset(doc, asset)
            loader.reader.read(local["file"], local["sha256"])
    payloads, clips = compose_parts.render_plan(plan, loader)
    preview = {"kind": "parts-preview", "schema_version": 1, "appearance": index["appearance"],
        "plan_digest": plan["digest"], "render_policy": parts_resolve.RENDER_POLICY,
        "canvas": plan["canvas"], "mirror": plan["mirror"], "clips": clips, "status": "unverified", "release_ready": False}
    preview["digest"] = contract.content_digest(preview)
    checks = [{"id": "declarations-coverage-assets", "status": "passed", "message": "exact revisions, requested samples and read asset hashes verified"}]
    measurements = []
    for clip in clips:
        for sample in clip["samples"]:
            image = assets.image_bytes(payloads[sample["image"]["file"]])
            bbox = image.getchannel("A").getbbox()
            measurements.append({"clip": clip["id"], "sample": sample["id"], "alpha_bbox": list(bbox) if bbox else None,
                                 "rgba_sha256": sample["rgba_sha256"], "duration_ticks": sample["duration_ticks"]})
            if bbox is None:
                checks.append({"id": f"empty.{clip['id']}.{sample['id']}", "status": "warning", "message": "composed sample is fully transparent"})
            clipped = sum(e["clipped_pixels"] for e in sample["draw"])
            if clipped:
                checks.append({"id": f"clip.{clip['id']}.{sample['id']}", "status": "warning", "message": f"{clipped} pixels clipped with explicit permission"})
    motion = registry[appearance["motion"]["id"]]
    topology = registry[plan["topology"]["id"]]
    checks += _contact_checks(contacts, motion, topology, plan["mirror"], plan["canvas"]["width"])
    checks += [{"id": name, "status": "unverified", "message": "requires review of this exact rendered appearance"} for name in REVIEW_CHECKS]
    qa = {"kind": "parts-qa", "schema_version": 1, "appearance": index["appearance"], "inputs": inputs,
          "plan_digest": plan["digest"], "preview_digest": preview["digest"], "render_policy": parts_resolve.RENDER_POLICY,
          "qa_policy": QA_POLICY, "toolchain": toolchain(), "checks": checks, "measurements": measurements,
          "asset_hashes": {name: assets.sha256(data) for name, data in sorted(loader.reader.data.items())},
          "contacts_sha256": assets.sha256(workspace.json_bytes(contacts)) if contacts is not None else None,
          "status": "error" if any(c["status"] == "error" for c in checks) else "unverified", "release_ready": False}
    qa["digest"] = contract.content_digest(qa)
    payloads.update({"plan.json": workspace.json_bytes(plan), "preview.json": workspace.json_bytes(preview), "qa.json": workspace.json_bytes(qa)})
    if contacts is not None:
        payloads["contacts.json"] = workspace.json_bytes(contacts)
    return plan, preview, qa, payloads


def inspect(assembly, contacts=None):
    root, index, registry, _ = parts_motion._active(assembly)
    annotations = workspace.read_json(Path(contacts).absolute().parent, Path(contacts).name) if contacts is not None else None
    _, _, qa, payloads = collect(root, index, registry, annotations)
    folder = assets.publish_object(root, "qa", index["appearance"]["id"], qa["digest"], payloads)
    return {"qa": (folder / "qa.json").relative_to(root).as_posix(), "digest": qa["digest"],
            "status": qa["status"], "release_ready": False, "checks": qa["checks"]}


def add_arguments(parser: argparse.ArgumentParser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--contacts", type=Path, help="explicit contact node/stance and world-travel annotations")


def run(*, assembly, contacts=None):
    value = {}
    def operation():
        value.update(inspect(assembly, contacts))
        return value
    result = workspace._report(operation)
    return result or (2 if value.get("status") == "error" else 0)
