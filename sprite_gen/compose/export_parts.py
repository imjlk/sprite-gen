# SPDX-License-Identifier: Apache-2.0
"""Portable, independent overlay layers; no Cartesian appearance baking.

The first browser adapter supports one body (flat, or segmented into node pieces),
one choice per slot, ordering (explicit constraints and the topology's draw table),
cover rules (a hat showing the capped hair), palette regions, masks (on the piece's
own node or across layers) and explicit mirroring. Unsupported replacement and slot
precedence fail visibly rather than being flattened.
Exports are inspection drafts; geometry is not visual release approval.
"""
from __future__ import annotations

import argparse
import base64
from copy import deepcopy
from pathlib import Path

from sprite_gen.compose import compose_parts, parts_resolve
from sprite_gen.compose.parts_runtime import PartsPlayer
from sprite_gen.curate import parts_motion
from sprite_gen.qa import inspect_parts, parts_fit
from sprite_gen.spec import parts_assets as assets, parts_contract as contract, parts_workspace as workspace
from sprite_gen.spec.runio import atomic_write_text, publish_guard

EXPORT_POLICY = "parts.overlay-library.v1"
STATIC = Path(__file__).resolve().parents[1] / "serve" / "parts_player"


def pack(root, index, registry, *, title=None, interpolation="frame"):
    contract._enum("frame", "linear")(interpolation, "interpolation")
    if index["appearance"] is None:
        raise contract.PartsContractError("layer export requires an active appearance")
    appearance = registry[index["appearance"]["id"]]
    rig, catalog = registry[index["rig"]["id"]], registry[index["catalog"]["id"]]
    topology = registry[rig["topology"]["id"]]
    slots = {slot["id"]: slot for slot in topology["slots"]}
    if any(slots[s["id"]]["max_count"] != 1 or slots[s["id"]]["overrides"] for s in catalog["slots"]):
        raise contract.PartsContractError("portable overlay adapter does not support slot precedence or multiple choices")
    if len(appearance["body"]) != 1:
        raise contract.PartsContractError("portable overlay adapter requires exactly one body pack")
    plan, _, qa, evidence = inspect_parts.collect(root, index, registry)
    motion = registry[index["motion"]["id"]]
    clips = [deepcopy(next(c for c in motion["clips"] if c["state"] == state
                           and c["view"] == rig["view"])) for state in appearance["states"]]
    loader = compose_parts.AssetLoader(root, index)
    payloads, textures, refs = {}, {}, {}

    def texture(owner, ref):
        key = (owner["id"], ref["file"], ref["sha256"])
        if key not in refs:
            image = loader(owner, ref)
            data = assets.png_bytes(image)
            digest = assets.sha256(data)
            file = f"textures/{digest}.png"
            payloads[file] = data
            textures[digest] = {"id": digest, "file": file, "sha256": digest, "size": list(image.size)}
            refs[key] = digest
        return refs[key]

    identities = {r["id"] for r in appearance["body"]}
    identities.update(r["id"] for slot in catalog["slots"] for r in slot["options"])
    packs = {}
    for identity in sorted(identities):
        source = registry[identity]
        binding = parts_resolve._binding(source, appearance, rig)
        if appearance["mirror"] and not binding["mirror_safe"]:
            raise contract.PartsContractError("portable mirror requires every exported binding to be mirror-safe")
        value = {"id": identity, "reference": contract.reference(source), "role": source["role"],
                 "binding": binding["id"], "occupies": binding["occupies"],
                 "requires": binding["requires"], "excludes": binding["excludes"],
                 "covers": binding.get("covers", []), "pieces": []}
        for piece in binding["pieces"]:
            if piece["replaces"]:
                raise contract.PartsContractError("portable overlay adapter does not support body replacement")
            output = {key: deepcopy(piece[key]) for key in ("id", "slot", "node", "mode", "provides")}
            output["cover_rule"] = deepcopy(piece.get("cover_rule"))
            output["palette_regions"] = {name: texture(source, ref) for name, ref in piece["palette_regions"].items()}
            output["sample_map"] = {}
            for clip in clips:
                mapping = piece["sample_map"].get(clip["id"], {})
                if set(mapping) != {sample["id"] for sample in clip["samples"]}:
                    raise contract.PartsContractError(f"missing exported layer coverage: {identity}/{piece['id']}/{clip['id']}")
                rows = {}
                for sample in clip["samples"]:
                    placement = mapping[sample["id"]]
                    row = {key: deepcopy(placement[key]) for key in
                           ("pivot", "target_socket", "offset", "visible", "before", "after", "allow_clip")}
                    row["texture"] = texture(source, placement["image"])
                    row["image_key"] = [placement["image"]["file"], placement["image"]["sha256"]]
                    row["palette_regions"] = {name: texture(source, ref) for name, ref in placement["palette_regions"].items()}
                    row["mask"] = None
                    if placement["mask"] is not None:
                        mask = placement["mask"]
                        # The player applies a mask to every layer providing its target node,
                        # the same rule the Python renderer uses, so cross-layer masks travel.
                        row["mask"] = {**{k: mask[k] for k in ("target_node", "space", "channel")},
                                       "texture": texture(source, mask["image"])}
                    socket = sample["sockets"].get(placement["target_socket"])
                    if row["visible"] and (socket is None or socket["confidence"] != "verified"):
                        raise contract.PartsContractError("exported visible layer requires a verified socket")
                    rows[sample["id"]] = row
                output["sample_map"][clip["id"]] = rows
            value["pieces"].append(output)
        packs[identity] = value

    selected = contract.resolve_selections(appearance, catalog)
    default_ids = {slot: refs[0]["id"] if refs else "" for slot, value in selected.items()
                   for refs in [contract._selected_refs(value)]}
    metadata_slots = [{"id": slot["id"], "label": slot["label"], "selected": default_ids[slot["id"]],
        "optional": slots[slot["id"]]["optional"],
        "options": ([{"id": "", "label": "없음"}] if slots[slot["id"]]["optional"] else []) +
                   [{"id": ref["id"], "label": ref["id"]} for ref in slot["options"]]}
        for slot in catalog["slots"] if slot["options"]]
    fitting_docs = [registry[ref["id"]] for ref in index["fitting"]]
    fitted = parts_fit.index_entries(fitting_docs)
    player = PartsPlayer(appearance, registry.values(), loader)
    fit_results = {}
    # Each part is measured in a declared default context: one selection changed from
    # the appearance. A cross-layer mask from another choice (a hat over hair) is not
    # part of that context. Precedence is excluded by this adapter. Not art approval.
    for slot in catalog["slots"]:
        for ref in slot["options"]:
            player.set_appearance(appearance)
            player.set_selection({slot["id"]: ref})
            for clip in player.plan["clips"]:
                for sample in clip["samples"]:
                    for row in parts_fit.check_sample(player.plan, clip, sample, loader, fitted, only_parts={ref["id"]}):
                        if row["part"]["id"] == ref["id"]:
                            key = "|".join((row["part"]["id"], row["piece"], row["clip"], row["sample"]))
                            fit_results[key] = row
    runtime_clips = [{key: clip[key] for key in ("id", "state", "view", "timebase", "loop")} |
        {"samples": [{key: sample[key] for key in ("id", "duration_ticks", "sockets")} for sample in clip["samples"]]}
        for clip in clips]
    manifest = {"kind": "parts-layer-library", "schema_version": 1, "export_policy": EXPORT_POLICY,
        "appearance": index["appearance"], "motion": index["motion"], "canvas": rig["canvas"],
        "title": title or appearance["id"], "interpolation": interpolation, "mirror": appearance["mirror"],
        "body": [ref["id"] for ref in appearance["body"]], "nodes": [n["id"] for n in topology["nodes"]],
        "layers": topology.get("layers", []),
        "palette": plan["palette"], "defaults": default_ids, "slots": metadata_slots, "clips": runtime_clips,
        "packs": packs, "textures": textures, "fitting": fit_results,
        "fit_summary": parts_fit.summarize(list(fit_results.values()), declared=bool(fitted)),
        "qa": qa["fitting"], "stability": workspace.decode_json(evidence["stability.json"]),
        "appearance_clips_baked": 0, "art_approved": False, "release_ready": False,
        "toolchain": qa["toolchain"], "export_code_sha256": assets.sha256(Path(__file__).read_bytes()),
        "adapter_sha256": assets.sha256(b"\0".join((name.encode() + b"\0" + (STATIC / name).read_bytes())
            for name in ("index.html", "player.css", "player.js", "runtime.js"))),
        "capabilities": ["flat-body", "segmented-body", "overlay", "shared-clock", "variable-duration", "ordering",
                         "draw-table", "cover-rules", "palette", "self-mask", "mirror"],
        "fit_scope": "authored per-part geometry; visual quality and combination art approval remain pending"}
    manifest["digest"] = contract.content_digest(manifest)
    payloads["manifest.json"] = workspace.json_bytes(manifest)
    bundle = {"manifest": manifest, "textures": {identity: "data:image/png;base64," +
              base64.b64encode(payloads[ref["file"]]).decode("ascii") for identity, ref in textures.items()}}
    html = (STATIC / "index.html").read_text()
    html = html.replace('<script src="/runtime.js"></script>', '')
    html = html.replace('<link rel="stylesheet" href="/player.css">', '<style>' + (STATIC / "player.css").read_text() + '</style>')
    encoded = workspace.json_bytes(bundle).decode("utf-8").replace("<", "\\u003c")
    scripts = (STATIC / "runtime.js").read_text() + "\n" + (STATIC / "player.js").read_text()
    html = html.replace('<script type="module" src="/player.js"></script>',
        '<script id="parts-data" type="application/json">' + encoded + '</script><script type="module">' + scripts + '</script>')
    payloads["preview.html"] = html.encode("utf-8")
    return payloads, manifest, bundle


def export(assembly, out, *, title=None, interpolation="frame"):
    root, index, registry, original_hash = parts_motion._active(assembly)
    output = Path(out).absolute().resolve()
    if output == root or root.is_relative_to(output) or (output.is_relative_to(root) and output.relative_to(root).parts[0] != "builds"):
        raise contract.PartsContractError("layer export output must be external or under workspace builds/")
    payloads, manifest, _ = pack(root, index, registry, title=title, interpolation=interpolation)
    output.mkdir(parents=True, exist_ok=True)
    folder = assets.publish_object(output, "snapshots", "layers." + manifest["appearance"]["digest"][:32], manifest["digest"], payloads)
    with publish_guard(root):
        current = assets.read_file(root, Path(assembly).name, limit=workspace.MAX_JSON_BYTES)
        if assets.sha256(current) != original_hash:
            raise contract.PartsContractError("assembly changed during export; snapshot preserved, pointer untouched")
        with publish_guard(output):
            target = assets.safe_path(output, "preview.json")
            atomic_write_text(target, workspace.json_bytes({"manifest": (folder / "manifest.json").relative_to(output).as_posix(),
                "preview": (folder / "preview.html").relative_to(output).as_posix(), "release_ready": False}).decode("utf-8"))
    return {"directory": str(folder), "manifest": str(folder / "manifest.json"), "preview": str(folder / "preview.html"),
            "textures": len(manifest["textures"]), "shared_clips": len(manifest["clips"]),
            "appearance_clips_baked": 0, "release_ready": False}


def add_arguments(parser: argparse.ArgumentParser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--title")
    parser.add_argument("--interpolation", choices=("frame", "linear"), default="frame")


def run(**kwargs):
    return workspace._report(lambda: export(**kwargs))
