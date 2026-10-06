# SPDX-License-Identifier: Apache-2.0
"""Import pose-indexed body/part rasters without resizing or implicit alignment."""
from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path

from sprite_gen.compose.compose_layers import _clipped_pixels
from sprite_gen.curate import parts_motion
from sprite_gen.spec import parts_assets as library, parts_contract as contract, parts_workspace as workspace
from sprite_gen.spec.runio import atomic_write_text, publish_guard


_INPUT_PLACEMENT = contract._record({
    "image": contract._asset, "format": contract._enum("full-canvas", "cropped", "local"),
    "crop_offset": contract._point, "pivot": contract._point, "target_socket": contract._id,
    "visible": contract._boolean, "offset": contract._point, "allow_clip": contract._boolean,
    "mask": contract._nullable(contract._mask), "palette_regions": contract._mapping(contract._asset),
    "before": contract._ids, "after": contract._ids,
}, {"crop_offset": [0, 0], "visible": True, "offset": [0, 0], "allow_clip": False,
    "mask": None, "palette_regions": {}, "before": [], "after": []})
_INPUT_PIECE = contract._record({
    "id": contract._id, "slot": contract._nullable(contract._id), "node": contract._id,
    "mode": contract._enum("raster", "held"),
    "sample_map": contract._mapping(contract._mapping(_INPUT_PLACEMENT)),
    "provides": contract._ids, "replaces": contract._ids,
    "join_interface": contract._nullable(contract._id), "palette_regions": contract._mapping(contract._asset),
    "pose_map": contract._mapping(_INPUT_PLACEMENT), "cover_rule": contract._cover_rule,
}, {"provides": [], "replaces": [], "join_interface": None, "palette_regions": {}, "sample_map": {}},
   optional=("pose_map", "cover_rule"))
_IMPORT = contract._record({
    "kind": contract._enum("parts-pack-import"), "schema_version": contract._positive,
    "id": contract._id, "design_id": contract._id, "role": contract._enum("part", "body"),
    "body_mode": contract._enum("segmented", "flat"), "style": contract._id,
    "rig": contract._ref, "motion": contract._ref, "view": contract._id, "binding_id": contract._id,
    "occupies": contract._ids, "requires": contract._ids, "excludes": contract._ids,
    "pieces": contract._records(_INPUT_PIECE, nonempty=True), "states": contract._ids,
    "mirror_safe": contract._boolean, "covers": contract._ids,
}, {"role": "part", "body_mode": "segmented", "occupies": [], "requires": [], "excludes": [], "states": [], "mirror_safe": False},
   optional=("covers",))


def _normalize_asset(reader, reference, payloads, *, size=None):
    image = library.load_image(reader, reference, size=size)
    encoded = library.png_bytes(image)
    digest = library.sha256(encoded)
    filename = f"images/{digest}.png"
    payloads[filename] = encoded
    if sum(map(len, payloads.values())) + reader.total_bytes > library.MAX_SNAPSHOT_BYTES:
        raise contract.PartsContractError("part snapshot exceeds the byte budget")
    return {"file": filename, "sha256": digest}, image


def _coverage(binding, motion, states):
    missing = []
    clips = {clip["state"]: clip for clip in motion["clips"]}
    for state in states:
        if state not in clips:
            missing.append(f"state:{state}")
            continue
        clip = clips[state]
        for piece in binding["pieces"]:
            mapping = piece["sample_map"].get(clip["id"], {})
            missing.extend(f"{piece['id']}/{clip['id']}/{sample['id']}" for sample in clip["samples"] if sample["id"] not in mapping)
    return missing


def _replace_refs(value, old, new):
    if isinstance(value, dict):
        if value == old:
            return deepcopy(new)
        return {key: _replace_refs(item, old, new) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_refs(item, old, new) for item in value]
    return value


def activate_pack(assembly, pack, filename, original_hash, index, registry):
    """Register a new revision and detach affected appearances/reviews, preserving files."""
    root = Path(assembly).absolute().parent.resolve()
    old = registry.get(pack["id"])
    if old is not None and old["kind"] != "parts-pack":
        raise contract.PartsContractError("part ID conflicts with another document kind")
    if old is not None and old["digest"] == pack["digest"]:
        with publish_guard(root):
            current = library.read_file(root, Path(assembly).name, limit=workspace.MAX_JSON_BYTES)
            if library.sha256(current) != original_hash:
                raise contract.PartsContractError("assembly changed during idempotent part import")
        return {"catalog": index["catalog"], "invalidated": [], "appearance_detached": False}
    catalog = deepcopy(registry[index["catalog"]["id"]])
    catalog.pop("digest")
    if old is not None:
        catalog = _replace_refs(catalog, contract.reference(old), contract.reference(pack))
    if pack["role"] == "part":
        slots = {piece["slot"] for binding in pack["bindings"] for piece in binding["pieces"]}
        known = {slot["id"]: slot for slot in catalog["slots"]}
        contract._members(slots, known, "part.catalog.slots")
        for slot_id in slots:
            if contract.reference(pack) not in known[slot_id]["options"]:
                known[slot_id]["options"].append(contract.reference(pack))
    catalog = contract.seal(catalog)
    removed = {pack["id"]} if old is not None else set()
    if catalog["digest"] != index["catalog"]["digest"]:
        removed.add(catalog["id"])
    if pack["role"] == "body":
        removed.update(doc["id"] for doc in registry.values() if doc["kind"] == "parts-appearance")
    # A changed pack can alter global occlusion, so every combination (appearance) review goes.
    # A part or binding review judges its pack on the body alone; it goes only when its own
    # subject or one of its inputs changes (the reference cascade below).
    removed.update(doc["id"] for doc in registry.values() if doc["kind"] == "parts-review"
                   and (doc["scope"] == "appearance" or doc["evidence"] is None or doc["evidence"]["appearance"] is not None))
    changed = True
    while changed:
        changed = False
        for doc in registry.values():
            if doc["id"] not in removed and any(ref["id"] in removed for ref in parts_motion._references(doc)):
                removed.add(doc["id"])
                changed = True
    next_registry = {name: doc for name, doc in registry.items() if name not in removed}
    next_registry.update({pack["id"]: pack, catalog["id"]: catalog})
    next_index = deepcopy(index)
    next_index["documents"] = {name: path for name, path in index["documents"].items() if name not in removed}
    next_index["fitting"] = [ref for ref in index["fitting"] if ref["id"] not in removed]
    next_index["stability"] = [ref for ref in index["stability"] if ref["id"] not in removed]
    next_index["documents"][pack["id"]] = filename
    if catalog["digest"] != index["catalog"]["digest"]:
        folder = library.publish_object(root, "catalogs", catalog["id"], catalog["digest"],
                                        {"catalog.json": workspace.json_bytes(catalog)})
        next_index["documents"][catalog["id"]] = (folder / "catalog.json").relative_to(root).as_posix()
    next_index["catalog"] = contract.reference(catalog)
    next_index["body"] = [ref for ref in index["body"] if ref["id"] not in removed]
    if pack["role"] == "body":
        next_index["body"].append(contract.reference(pack))
    if next_index["appearance"] is not None and next_index["appearance"]["id"] in removed:
        next_index["appearance"] = None
    contract.validate_bundle(next_registry.values())
    report = {"kind": "parts-pack-invalidation", "schema_version": 1, "part": contract.reference(pack),
              "previous_part": contract.reference(old) if old is not None else None,
              "invalidated": [{"reference": contract.reference(registry[name]), "file": index["documents"][name]}
                              for name in sorted(removed) if name in registry],
              "appearance_detached": index["appearance"] is not None and next_index["appearance"] is None}
    report["digest"] = contract.content_digest(report)
    folder = library.publish_object(root, "changes", pack["id"], report["digest"],
                                    {"invalidation.json": workspace.json_bytes(report)})
    with publish_guard(root):
        current = library.read_file(root, Path(assembly).name, limit=workspace.MAX_JSON_BYTES)
        if library.sha256(current) != original_hash:
            raise contract.PartsContractError("assembly changed; imported object is preserved, retry activation")
        atomic_write_text(root / Path(assembly).name, workspace.json_bytes(next_index).decode("utf-8"))
    return {"catalog": next_index["catalog"], "invalidated": report["invalidated"],
            "appearance_detached": report["appearance_detached"],
            "invalidation_report": (folder / "invalidation.json").relative_to(root).as_posix()}


def import_pack(assembly: Path, spec: Path, source_root: Path, *, allow_incomplete=False):
    root, index, registry, original_hash = parts_motion._active(assembly)
    path = Path(spec).absolute()
    raw_spec = library.read_file(path.parent, path.name, limit=workspace.MAX_JSON_BYTES)
    request = _IMPORT(workspace.decode_json(raw_spec), "import")
    if request["schema_version"] != 1 or request["rig"] != index["rig"] or request["motion"] != index["motion"]:
        raise contract.PartsContractError("part import version/rig/motion differs from active workspace")
    rig = registry[index["rig"]["id"]]
    motion = registry[index["motion"]["id"]]
    if motion["freeze"] is None:
        raise contract.PartsContractError("part import requires a frozen motion")
    if request["style"] != rig["style"] or request["view"] != rig["view"]:
        raise contract.PartsContractError("part style/view differs from rig; automatic retargeting is disabled")
    size = (rig["canvas"]["width"], rig["canvas"]["height"])
    clips = {clip["id"]: clip for clip in motion["clips"]}
    reader = library.SourceReader(source_root)
    payloads, pieces = {}, []
    for value in request["pieces"]:
        piece = deepcopy(value)
        for channel, region in value["palette_regions"].items():
            piece["palette_regions"][channel], _ = _normalize_asset(reader, region, payloads)
        contract._members(value["sample_map"], clips, f"{value['id']}.clips")

        def normalize(placement, where):
            expected_size = size if placement["format"] == "full-canvas" else None
            image_ref, image = _normalize_asset(reader, placement["image"], payloads, size=expected_size)
            if placement["format"] != "cropped" and placement["crop_offset"] != [0, 0]:
                raise contract.PartsContractError("crop_offset is only valid for cropped input")
            pivot = [v - d for v, d in zip(placement["pivot"], placement["crop_offset"])]
            normalized = {key: deepcopy(placement[key]) for key in (
                "target_socket", "visible", "offset", "allow_clip", "before", "after", "mask", "palette_regions")}
            normalized.update(image=image_ref, pivot=pivot, source={
                "image": placement["image"], "format": placement["format"],
                "crop_offset": placement["crop_offset"], "pivot": placement["pivot"]})
            if normalized["mask"] is not None:
                mask = normalized["mask"]
                mask["image"], _ = _normalize_asset(reader, mask["image"], payloads,
                                                   size=image.size if mask["space"] == "part-local" else size)
            for channel, region in placement["palette_regions"].items():
                normalized["palette_regions"][channel], _ = _normalize_asset(reader, region, payloads, size=image.size)
            for channel, region in piece["palette_regions"].items():
                data = library.image_bytes(payloads[region["file"]])
                if data.size != image.size:
                    raise contract.PartsContractError(f"{where}: piece palette region differs from image size; use sample-specific regions")
            return normalized, image

        def place(normalized, image, clip_id, sample):
            if normalized["visible"]:
                socket = sample["sockets"].get(normalized["target_socket"])
                if socket is None or socket["confidence"] != "verified":
                    raise contract.PartsContractError("visible part requires a verified target sample socket")
                offset = tuple(t - p + d for t, p, d in zip(socket["position"], normalized["pivot"], normalized["offset"]))
                if not normalized["allow_clip"] and _clipped_pixels(image, offset, size):
                    raise contract.PartsContractError(f"part clips rig canvas: {value['id']}/{clip_id}/{sample['id']}")
            piece["sample_map"].setdefault(clip_id, {})[sample["id"]] = normalized

        for clip_id, mapping in value["sample_map"].items():
            samples = {sample["id"]: sample for sample in clips[clip_id]["samples"]}
            contract._members(mapping, samples, f"{value['id']}.samples")
            for sample_id, placement in mapping.items():
                normalized, image = normalize(placement, f"{value['id']}/{clip_id}/{sample_id}")
                place(normalized, image, clip_id, samples[sample_id])
        if "pose_map" in value:
            # One drawing per pose, repeated on every sample of that pose; a sample listed
            # in sample_map keeps its own entry (a held frame drawn differently).
            used = set()
            for pose_id, placement in value["pose_map"].items():
                normalized, image = normalize(placement, f"{value['id']}/pose:{pose_id}")
                piece["pose_map"][pose_id] = normalized
                for clip in clips.values():
                    for sample in clip["samples"]:
                        if sample["pose_id"] != pose_id:
                            continue
                        used.add(pose_id)
                        if sample["id"] not in value["sample_map"].get(clip["id"], {}):
                            place(deepcopy(normalized), image, clip["id"], sample)
            if set(value["pose_map"]) - used:
                raise contract.PartsContractError(
                    f"{value['id']}: pose_map names poses no motion sample has: {sorted(set(value['pose_map']) - used)}")
        pieces.append(piece)
    binding = {"id": request["binding_id"], "rig": request["rig"], "motion": request["motion"], "view": request["view"],
               "occupies": request["occupies"], "requires": request["requires"], "excludes": request["excludes"],
               "pieces": pieces, "mirror_safe": request["mirror_safe"]}
    if "covers" in request:
        binding["covers"] = request["covers"]
    missing = _coverage(binding, motion, request["states"] or index["states"])
    if missing and not allow_incomplete:
        raise contract.PartsContractError(f"missing part coverage: {missing}")
    for data in [*reader.data.values(), raw_spec]:
        payloads[f"sources/{library.sha256(data)}.bin"] = data
    pack = contract.seal({"kind": "parts-pack", "schema_version": 1, "id": request["id"], "design_id": request["design_id"],
                          "role": request["role"], "body_mode": request["body_mode"], "bindings": [binding],
                          "import": {"request_sha256": library.sha256(raw_spec), "style": request["style"],
                          "encoding_policy": "rgba-png.v1", "coverage": "partial" if missing else "complete",
                          "states": request["states"] or index["states"],
                          "source_assets": [{"file": name, "sha256": library.sha256(data)}
                                            for name, data in sorted(payloads.items()) if name.startswith("sources/")]}})
    contract.validate_bundle([registry[rig["topology"]["id"]], rig, motion, pack])
    payloads["part.json"] = workspace.json_bytes(pack)
    folder = library.publish_object(root, "parts", pack["id"], pack["digest"], payloads)
    filename = (folder / "part.json").relative_to(root).as_posix()
    activated = activate_pack(assembly, pack, filename, original_hash, index, registry)
    return {"part": contract.reference(pack), "file": filename, "role": pack["role"],
            "missing_coverage": missing, "status": "partial" if missing else "imported", "release_ready": False, **activated}


def add_arguments(parser: argparse.ArgumentParser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--allow-incomplete", action="store_true", help="import a draft with explicit missing coverage; no release approval")


def run(*, assembly: Path, spec: Path, source_root: Path, allow_incomplete=False) -> int:
    return workspace._report(lambda: import_pack(assembly, spec, source_root, allow_incomplete=allow_incomplete))
