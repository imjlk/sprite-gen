# SPDX-License-Identifier: Apache-2.0
"""Derive per-pose interfaces from explicit slot/body profiles, never RGB heuristics."""
from io import BytesIO
from pathlib import Path
from PIL import Image
from sprite_gen._deps import np
from sprite_gen.compose import compose_parts
from sprite_gen.curate import parts_motion
from sprite_gen.spec import parts_assets as assets, parts_contract as contract, parts_workspace as workspace
from sprite_gen.frames import parts_production as production
from sprite_gen.spec.runio import atomic_write_text

_RULE = contract._record({"nodes": contract._ids, "socket": contract._nullable(contract._id),
                         "bounds": contract._nullable(contract._rectangle), "body_only": contract._boolean,
                         "pad": contract._integer}, {"nodes": [], "socket": None, "bounds": None, "body_only": True, "pad": 0})
_PROFILE = contract._record({"kind": contract._enum("parts-interface-profile"), "schema_version": contract._positive,
                            "id": contract._id, "slot": contract._id, "rig": contract._ref,
                            "body": contract._list(contract._ref), "rules": contract._mapping(contract._list(_RULE))})


def derive(assembly, part, profile, out):
    root, index, registry, _ = parts_motion._active(assembly)
    state = production._load(root, part)
    file = Path(profile).absolute()
    value = _PROFILE(workspace.read_json(file.parent, file.name), "profile")
    if value["schema_version"] != 1 or value["rig"] != index["rig"] or value["body"] != index["body"] or value["slot"] != state["config"]["slot"]:
        raise contract.PartsContractError("interface profile does not match the active slot/body/rig revision")
    contract._members(value["rules"], ("required", "protected", "allowed", "occluded"), "interface rules")
    motion = registry[index["motion"]["id"]]
    rig = registry[index["rig"]["id"]]
    topology = registry[rig["topology"]["id"]]
    nodes = {node["id"] for node in topology["nodes"]}
    for rules in value["rules"].values():
        for rule in rules:
            contract._members(rule["nodes"], nodes, "interface nodes")
            if rule["bounds"]:
                a, b, c, d = rule["bounds"]
                if c-a > rig["canvas"]["width"] or d-b > rig["canvas"]["height"]:
                    raise contract.PartsContractError("interface bounds must not exceed the rig canvas")
    _, first = production._poses(motion, production._states(state, index))
    loader = compose_parts.AssetLoader(root, index)
    payloads, checks, sources = {}, {}, {}
    for pose, (clip, sample) in first.items():
        body = np.asarray(loader(motion, sample["image"]).convert("RGBA"))[..., 3] > 0
        yy, xx = np.indices(body.shape)
        checks[pose] = {}
        for name, rules in value["rules"].items():
            combined = np.zeros_like(body)
            for rule in rules:
                if not 0 <= rule["pad"] <= 16:
                    raise contract.PartsContractError("interface pad must be 0..16 native pixels")
                mask = body.copy() if rule["body_only"] else np.ones_like(body)
                if rule["nodes"]:
                    if not state["config"]["segments"]:
                        raise contract.PartsContractError("node rules require body segments")
                    folder = Path(state["config"]["segments"]).parent
                    nodes = np.zeros_like(body)
                    for node in rule["nodes"]:
                        source = (folder / node / clip / f"{sample['id']}.png").as_posix()
                        encoded = assets.read_file(root, source)
                        sources[source] = assets.sha256(encoded)
                        image = np.asarray(Image.open(BytesIO(encoded)).convert("RGBA"))
                        if image.shape[:2] != body.shape:
                            raise contract.PartsContractError("segment differs from rig canvas")
                        nodes |= image[..., 3] > 0
                    mask &= nodes
                if rule["bounds"]:
                    socket = sample["sockets"].get(rule["socket"])
                    if not socket or not socket["visible"] or socket["confidence"] != "verified":
                        raise contract.PartsContractError("interface bounds require a verified socket")
                    x, y = socket["position"];a,b,c,d = rule["bounds"]
                    mask &= (xx >= x+a) & (xx < x+c) & (yy >= y+b) & (yy < y+d)
                elif not rule["nodes"]:
                    raise contract.PartsContractError("each interface rule needs nodes or socket bounds")
                count = rule["pad"]
                if count:
                    padded = np.pad(mask, count); grown = np.zeros_like(mask)
                    for dy in range(count*2+1):
                        for dx in range(count*2+1):
                            grown |= padded[dy:dy+mask.shape[0], dx:dx+mask.shape[1]]
                    mask = grown
                combined |= mask
            relative = f"{pose}/{name}.png"
            encoded = assets.png_bytes(Image.fromarray((combined*255).astype('uint8')))
            payloads[relative] = encoded
            checks[pose][name] = {"file": relative, "sha256": assets.sha256(encoded)}
    report = {"kind": "parts-derived-interfaces", "schema_version": 1, "profile": value,
              "motion": index["motion"], "poses": checks, "segment_sources": sources, "art_approved": False}
    digest = contract.content_digest(report);payloads["interfaces.json"] = workspace.json_bytes(report)
    saved = assets.publish_object(root, "interfaces", value["id"], digest, payloads)
    config = state["config"]
    config["pose_checks"] = {pose: {name:(saved / ref["file"]).relative_to(root).as_posix()
                                    for name,ref in masks.items()} for pose,masks in checks.items()}
    destination = Path(out).absolute()
    destination.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(destination, workspace.json_bytes(config).decode())
    return {"config": str(out), "interfaces": str(saved / "interfaces.json"), "poses": len(checks),
            "activated": False, "art_approved": False}


def add_arguments(parser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--part", required=True)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)


def run(**kwargs):
    return workspace._report(lambda: derive(**kwargs))
