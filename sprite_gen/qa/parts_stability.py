# SPDX-License-Identifier: Apache-2.0
"""Measure authored source regions after integer attachment alignment.

This checks the pinned, unmasked piece raster before appearance palettes or
other overlays. A region must be authored as stable; moving limbs, anatomy,
optical flow and acceptable art quality are never inferred.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path

from sprite_gen._deps import np
from sprite_gen.curate import parts_motion
from sprite_gen.spec import parts_assets as assets, parts_contract as contract, parts_workspace as workspace
from sprite_gen.spec.runio import atomic_write_text, publish_guard

STABILITY_POLICY = "parts.authored-source-stability.v1"
_IMPORT = contract._record({"kind": contract._enum("parts-stability-import"),
    "schema_version": contract._positive, "id": contract._id, "rig": contract._ref,
    "motion": contract._ref, "regions": contract._records(contract._stable_region, nonempty=True)})


def measure(reference, current, *, min_alpha=128, rgb_threshold=24):
    """Pixel deltas in already aligned regions; no raster modification."""
    if reference.size != current.size:
        raise contract.PartsContractError("stability regions require identical native sizes")
    if type(min_alpha) is not int or not 1 <= min_alpha <= 255 or type(rgb_threshold) is not int or not 0 <= rgb_threshold <= 255:
        raise contract.PartsContractError("invalid stability pixel threshold")
    a, b = np.asarray(reference.convert("RGBA")), np.asarray(current.convert("RGBA"))
    aa, bb = a[..., 3] >= min_alpha, b[..., 3] >= min_alpha
    rgb = np.max(np.abs(a[..., :3].astype(np.int16)-b[..., :3].astype(np.int16)), axis=-1)
    return {"alpha_pixels": int(np.count_nonzero(aa ^ bb)),
            "rgb_pixels": int(np.count_nonzero((rgb > rgb_threshold) & aa & bb)),
            "reference_pixels": int(np.count_nonzero(aa)), "current_pixels": int(np.count_nonzero(bb))}


def import_stability(assembly, spec):
    root, index, registry, original_hash = parts_motion._active(assembly)
    path = Path(spec).absolute()
    raw = assets.read_file(path.parent, path.name, limit=workspace.MAX_JSON_BYTES)
    request = _IMPORT(workspace.decode_json(raw), "stability")
    if (request["schema_version"] != 1 or request["rig"] != index["rig"] or request["motion"] != index["motion"]):
        raise contract.PartsContractError("stability version/rig/motion differs from active workspace")
    if registry[index["motion"]["id"]]["freeze"] is None:
        raise contract.PartsContractError("stability requires a frozen motion")
    old = registry.get(request["id"])
    if old is not None and old["kind"] != "parts-stability":
        raise contract.PartsContractError("stability ID conflicts with another document kind")
    source = {"file": "sources/" + assets.sha256(raw) + ".json", "sha256": assets.sha256(raw)}
    document = contract.seal({**request, "kind": "parts-stability", "source_assets": [source]})
    changed = old is None or old["digest"] != document["digest"]
    removed = {d["id"] for d in registry.values() if changed and d["kind"] in {"parts-review", "parts-build-lock"}}
    contract.validate_bundle([d for identity, d in registry.items() if identity != document["id"] and identity not in removed] + [document])
    folder = assets.publish_object(root, "stability", document["id"], document["digest"],
        {"stability.json": workspace.json_bytes(document), source["file"]: raw})
    updated = deepcopy(index)
    updated["documents"] = {identity: filename for identity, filename in index["documents"].items() if identity not in removed}
    updated["documents"][document["id"]] = (folder / "stability.json").relative_to(root).as_posix()
    updated["stability"] = [ref for ref in index["stability"] if ref["id"] != document["id"]] + [contract.reference(document)]
    with publish_guard(root):
        current = assets.read_file(root, Path(assembly).name, limit=workspace.MAX_JSON_BYTES)
        if assets.sha256(current) != original_hash:
            raise contract.PartsContractError("assembly changed; stability object preserved, retry activation")
        atomic_write_text(root / Path(assembly).name, workspace.json_bytes(updated).decode("utf-8"))
    return {"stability": contract.reference(document), "regions": len(document["regions"]),
            "invalidated": sorted(removed), "release_ready": False}


def inspect(index, registry, load):
    documents = [registry[ref["id"]] for ref in index["stability"]]
    appearance = registry[index["appearance"]["id"]]
    catalog = registry[appearance["catalog"]["id"]]
    active = {ref["id"] for ref in appearance["body"]}
    active.update(ref["id"] for value in contract.resolve_selections(appearance, catalog).values()
                  for ref in contract._selected_refs(value))
    motion = registry[index["motion"]["id"]]
    clips = {clip["id"]: clip for clip in motion["clips"]}
    results = []
    for document in documents:
        for region in document["regions"]:
            if region["part"]["id"] not in active:
                continue
            part = registry[region["part"]["id"]]
            binding = next(b for b in part["bindings"] if b["id"] == region["binding"])
            piece = next(p for p in binding["pieces"] if p["id"] == region["piece"])

            def pixels(clip_id, sample_id):
                sample = next(s for s in clips[clip_id]["samples"] if s["id"] == sample_id)
                placement = piece["sample_map"][clip_id][sample_id]
                socket, target = sample["sockets"].get(region["socket"]), sample["sockets"].get(placement["target_socket"])
                if (not placement["visible"] or socket is None or target is None
                    or not socket["visible"] or not target["visible"]):
                    return None
                if socket["confidence"] != "verified" or target["confidence"] != "verified":
                    raise contract.PartsContractError("stability requires verified native anchors")
                origin = [a-p+o for a, p, o in zip(target["position"], placement["pivot"], placement["offset"])]
                x, y = [a-b for a, b in zip(socket["position"], origin)]
                x0, y0, x1, y1 = region["bounds"]
                return load(region["part"], placement["image"]).crop((x+x0, y+y0, x+x1, y+y1))

            ref = region["reference"]
            reference = pixels(ref["clip"], ref["sample"])
            if reference is None:
                raise contract.PartsContractError("stability reference must be visible")
            for clip_id in region["clips"]:
                for sample in clips[clip_id]["samples"]:
                    row = {"declaration": contract.reference(document), "region": region["id"],
                        "label": region["label"], "part": region["part"], "piece": region["piece"],
                        "clip": clip_id, "state": clips[clip_id]["state"], "sample": sample["id"],
                        "reference": ref, "source_scope": "unmasked source before appearance palettes",
                        "art_approved": False, "release_ready": False}
                    current = pixels(clip_id, sample["id"])
                    if current is None:
                        row.update(status="unverified" if region["skip_hidden"] else "error", checks=[{
                            "id": "region-visibility", "status": "unverified" if region["skip_hidden"] else "error"}])
                    else:
                        delta = measure(reference, current, min_alpha=region["min_alpha"], rgb_threshold=region["rgb_threshold"])
                        checks = [{"id": key, "value": delta[key], "limit": region[limit],
                            "status": "passed" if delta[key] <= region[limit] else "error"}
                            for key, limit in (("alpha_pixels", "max_alpha_pixels"), ("rgb_pixels", "max_rgb_pixels"))]
                        if not delta["reference_pixels"] or not delta["current_pixels"]:
                            checks.append({"id": "region-nonempty", "status": "error", "limit": ">0"})
                        row.update(**delta, checks=checks, status="error" if any(c["status"] == "error" for c in checks) else "passed")
                    results.append(row)
    measured = [row for row in results if row["status"] != "unverified"]
    failed = sum(row["status"] == "error" for row in results)
    report = {"kind": "parts-stability-report", "schema_version": 1, "policy": STABILITY_POLICY,
        "motion": index["motion"], "declarations": [contract.reference(d) for d in documents],
        "status": "error" if failed else "passed" if measured else "unverified", "checked": len(measured),
        "failed": failed, "skipped": len(results)-len(measured), "results": results,
        "art_approved": False, "release_ready": False}
    report["digest"] = contract.content_digest(report)
    checks = [{"id": "source-region-stability", **{key: report[key] for key in ("status", "checked", "failed")},
               "message": "only authored stable source regions; motion and visual quality require explicit review"}]
    checks += [{"id": f"stability.{n}", "status": "error", "part": row["part"], "region": row["region"],
                "clip": row["clip"], "sample": row["sample"], "failed_checks": [c for c in row["checks"] if c["status"] == "error"]}
               for n, row in enumerate(results) if row["status"] == "error"]
    return report, checks


def add_arguments(parser: argparse.ArgumentParser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--spec", required=True, type=Path)


def run(**kwargs):
    return workspace._report(lambda: import_stability(**kwargs))
