# SPDX-License-Identifier: Apache-2.0
"""Deterministic baked atlas snapshots gated by exact appearance reviews."""
from __future__ import annotations

import argparse
from fractions import Fraction
import math
from pathlib import Path

from PIL import Image

from sprite_gen.compose import export_aseprite, parts_resolve
from sprite_gen.curate import parts_motion, parts_review
from sprite_gen.qa import inspect_parts
from sprite_gen.spec import parts_assets as assets, parts_contract as contract, parts_workspace as workspace
from sprite_gen.spec.runio import atomic_write_text, publish_guard

EXPORT_POLICY = "parts.atlas-dedup.v1"


def _millisecond_timing(clip):
    end, previous, durations, errors = Fraction(0), 0, [], []
    for sample in clip["samples"]:
        exact = Fraction(sample["duration_ticks"] * 1000, clip["timebase"])
        end += exact
        boundary = (2 * end.numerator + end.denominator) // (2 * end.denominator)
        duration = boundary - previous
        if duration <= 0:
            raise contract.PartsContractError("Aseprite export cannot represent a sample below one millisecond")
        durations.append(duration)
        errors.append(str(Fraction(duration) - exact))
        previous = boundary
    return durations, errors


def bake(plan, preview, frame_payloads):
    """Dedup cells while preserving every ordered sample and exact duration tick."""
    width, height = plan["canvas"]["width"], plan["canvas"]["height"]
    cells, cell_by_hash = [], {}
    rows, animations, clips = {}, {}, []
    sample_cells = {}
    for clip in preview["clips"]:
        ids = []
        for sample in clip["samples"]:
            key = sample["rgba_sha256"]
            if key not in cell_by_hash:
                cell_by_hash[key] = len(cells)
                cells.append(assets.image_bytes(frame_payloads[sample["image"]["file"]]))
            ids.append(cell_by_hash[key])
        sample_cells[clip["id"]] = ids
    columns = max(1, math.isqrt(max(1, len(cells) - 1)) + 1)
    sheet_rows = (len(cells) + columns - 1) // columns
    sheet_size = (columns * width, sheet_rows * height)
    if sheet_size[0] * sheet_size[1] > assets.MAX_TOTAL_PIXELS:
        raise contract.PartsContractError("atlas exceeds the pixel budget")
    atlas = Image.new("RGBA", sheet_size)
    rects = []
    for i, image in enumerate(cells):
        x, y = (i % columns) * width, (i // columns) * height
        atlas.paste(image, (x, y))
        rects.append({"x": x, "y": y, "w": width, "h": height})
    for clip in preview["clips"]:
        durations, errors = _millisecond_timing(clip)
        clip_rects = [rects[i] for i in sample_cells[clip["id"]]]
        rows[clip["state"]] = clip_rects
        animations[clip["state"]] = {"durations_ms": durations, "loop": clip["loop"]}
        clips.append({key: clip[key] for key in ("id", "state", "view", "timebase", "loop")} | {
            "duration_ticks": sum(s["duration_ticks"] for s in clip["samples"]),
            "aseprite_timing_policy": "round-boundaries", "aseprite_duration_errors_ms": errors,
            "samples": [{"id": sample["id"], "duration_ticks": sample["duration_ticks"],
                         "cell": cell, "rect": rect, "sockets": sample["sockets"],
                         "rgba_sha256": sample["rgba_sha256"], "draw": sample["draw"],
                         "hidden": sample["hidden"], "suppressed": sample["suppressed"]}
                        for sample, cell, rect in zip(clip["samples"], sample_cells[clip["id"]], clip_rects)]})
    manifest = {"kind": "parts-baked-manifest", "schema_version": 1,
        "appearance": plan["appearance"], "rig": plan["rig"], "motion": plan["motion"],
        "plan_digest": plan["digest"], "render_policy": parts_resolve.RENDER_POLICY, "export_policy": EXPORT_POLICY,
        "mirror": plan["mirror"], "game_input": "atlas.png", "unique_cells": len(cells), "clips": clips,
        "frame_layout": {"sheetWidth": sheet_size[0], "sheetHeight": sheet_size[1],
                         "cellWidth": width, "cellHeight": height, "rows": rows}, "animation": {"rows": animations}}
    manifest["digest"] = contract.content_digest(manifest)
    # Pure mapping reuse; no fabricated legacy run or legacy IO adapter.
    aseprite = export_aseprite.aseprite_json(manifest, fmt="json-array")
    return {"atlas.png": assets.png_bytes(atlas), "manifest.json": workspace.json_bytes(manifest),
            "aseprite.json": workspace.json_bytes(aseprite)}, manifest


def _release_evidence(root, index, registry):
    matches = [doc for doc in registry.values() if doc["kind"] == "parts-review" and doc["scope"] == "appearance"
        and doc["status"] == "approved" and doc["subject_digest"] == index["appearance"]["digest"]
        and doc["evidence"] is not None and doc["evidence"]["appearance"] == index["appearance"]]
    if len(matches) != 1:
        raise contract.PartsContractError("release requires one current approved appearance review; part/binding approvals are insufficient")
    review = matches[0]
    if (set(review["checks"]) != set(inspect_parts.REVIEW_CHECKS) or any(v != "approved" for v in review["checks"].values())
        or review["reviewer"] is None):
        raise contract.PartsContractError("release review lacks explicit visual/motion/contact approval checks")
    from sprite_gen.frames import parts_production
    parts = parts_production.release_reviews(root, registry, registry[index["appearance"]["id"]])
    return parts_review.verify(root, index, registry, review), [contract.reference(review), *parts]


def build(assembly, out, *, mode="preview"):
    if mode not in {"preview", "release"}:
        raise contract.PartsContractError("build mode must be preview or release")
    root, index, registry, original_hash = parts_motion._active(assembly)
    if index["appearance"] is None:
        raise contract.PartsContractError("build requires an active appearance")
    output = Path(out).absolute().resolve()
    if output == root or root.is_relative_to(output) or (output.is_relative_to(root) and output.relative_to(root).parts[0] != "builds"):
        raise contract.PartsContractError("build output must be external or under workspace builds/, away from source objects")
    if mode == "release":
        (plan, preview, qa, payloads), reviews = _release_evidence(root, index, registry)
        for ref in reviews:
            payloads[f"reviews/{ref['id']}.json"] = workspace.json_bytes(registry[ref["id"]])
    else:
        plan, preview, qa, payloads = inspect_parts.collect(root, index, registry)
        reviews = []
    baked, manifest = bake(plan, preview, payloads)
    payloads.update(baked)
    report = {"kind": "parts-build-report", "schema_version": 1, "mode": mode,
        "appearance": index["appearance"], "qa_digest": qa["digest"], "preview_digest": preview["digest"],
        "render_policy": parts_resolve.RENDER_POLICY, "qa_policy": inspect_parts.QA_POLICY, "export_policy": EXPORT_POLICY,
        "review_refs": reviews, "automatic_qa_status": qa["status"], "states": index["states"],
        "clips": len(manifest["clips"]), "samples": sum(len(c["samples"]) for c in manifest["clips"]),
        "unique_cells": manifest["unique_cells"], "status": "approved" if mode == "release" else qa["status"],
        "release_ready": mode == "release", "limitations": "engine export; target game engine integration is not verified"}
    report["digest"] = contract.content_digest(report)
    payloads["report.json"] = workspace.json_bytes(report)
    lock = contract.seal({"kind": "parts-build-lock", "schema_version": 1,
        "id": "build." + index["appearance"]["digest"][:32], "appearance": index["appearance"],
        "inputs": qa["inputs"], "toolchain": {**qa["toolchain"], "export_policy": EXPORT_POLICY},
        "render_policy": parts_resolve.RENDER_POLICY, "qa_policy": inspect_parts.QA_POLICY, "reviews": reviews,
        "outputs": [{"file": name, "sha256": assets.sha256(data)} for name, data in sorted(payloads.items())]})
    contract.validate_bundle([*registry.values(), lock])
    payloads["build-lock.json"] = workspace.json_bytes(lock)
    output.mkdir(parents=True, exist_ok=True)
    folder = assets.publish_object(output, "snapshots", lock["id"], lock["digest"], payloads)
    pointer = {"kind": "parts-build-pointer", "schema_version": 1, "mode": mode,
        "build": contract.reference(lock), "directory": folder.relative_to(output).as_posix(),
        "manifest": (folder / "manifest.json").relative_to(output).as_posix(), "release_ready": mode == "release"}
    # Snapshot publication precedes the pointer. A failed write or stale assembly
    # leaves the previous release intact; preview has its own independent pointer.
    with publish_guard(root):
        current = assets.read_file(root, Path(assembly).name, limit=workspace.MAX_JSON_BYTES)
        if assets.sha256(current) != original_hash:
            raise contract.PartsContractError("assembly changed during build; snapshot preserved, current release pointer untouched")
        with publish_guard(output):
            target = assets.safe_path(output, f"{mode}.json")
            atomic_write_text(target, workspace.json_bytes(pointer).decode("utf-8"))
    return {"build": contract.reference(lock), "directory": str(folder),
            "manifest": str(folder / "manifest.json"), "atlas": str(folder / "atlas.png"),
            "mode": mode, "clips": report["clips"], "samples": report["samples"],
            "unique_cells": report["unique_cells"], "status": report["status"], "release_ready": mode == "release"}


def add_arguments(parser: argparse.ArgumentParser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--mode", choices=("preview", "release"), default="preview")


def run(**kwargs):
    return workspace._report(lambda: build(**kwargs))
