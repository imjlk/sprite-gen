# SPDX-License-Identifier: Apache-2.0
"""Immutable render/QA evidence and explicit decisions on exact revisions."""
from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path

from sprite_gen.compose import compose_parts, parts_resolve
from sprite_gen.curate import parts_motion
from sprite_gen.qa import inspect_parts
from sprite_gen.spec import parts_assets as assets, parts_contract as contract, parts_workspace as workspace
from sprite_gen.spec.runio import atomic_write_text, publish_guard


def _activate(assembly, review, folder, original_hash, index, registry):
    root = Path(assembly).absolute().parent.resolve()
    next_index = deepcopy(index)
    old = registry.get(review["id"])
    removed = set()
    if old is not None and old["digest"] != review["digest"]:
        removed = {review["id"]}
        changed = True
        while changed:
            changed = False
            for doc in registry.values():
                if doc["id"] not in removed and any(r["id"] in removed for r in parts_motion._references(doc)):
                    removed.add(doc["id"])
                    changed = True
    next_index["documents"] = {name: path for name, path in index["documents"].items() if name not in removed}
    next_index["documents"][review["id"]] = (folder / "review.json").relative_to(root).as_posix()
    contract.validate_bundle([d for name, d in registry.items() if name not in removed and name != review["id"]] + [review])
    with publish_guard(root):
        current = assets.read_file(root, Path(assembly).name, limit=workspace.MAX_JSON_BYTES)
        if assets.sha256(current) != original_hash:
            raise contract.PartsContractError("assembly changed during review; evidence preserved, regenerate review")
        atomic_write_text(Path(assembly), workspace.json_bytes(next_index).decode("utf-8"))
    return sorted(removed - {review["id"]})


def prepare(assembly, *, scope="appearance", subject=None, binding=None, contacts=None):
    root, index, registry, original_hash = parts_motion._active(assembly)
    annotations = workspace.read_json(Path(contacts).absolute().parent, Path(contacts).name) if contacts is not None else None
    plan, preview, qa, payloads = inspect_parts.collect(root, index, registry, annotations)
    if qa["status"] == "error":
        raise contract.PartsContractError("QA errors block review; inspect and correct contact/geometry evidence")
    if scope == "appearance":
        if subject is not None and subject != index["appearance"]["id"]:
            raise contract.PartsContractError("appearance review must target the active appearance")
        target = registry[index["appearance"]["id"]]
        subject_digest = target["digest"]
        if binding is not None:
            raise contract.PartsContractError("binding is only valid for binding scope")
    else:
        target = registry.get(subject)
        if target is None or target["kind"] != "parts-pack":
            raise contract.PartsContractError("part/binding scope requires an indexed part/body ID")
        if contract.reference(target) not in qa["inputs"]:
            raise contract.PartsContractError("subject is outside the active appearance dependency graph")
        subject_digest = target["digest"]
        if scope == "binding":
            matches = [b for b in target["bindings"] if b["id"] == binding and b["rig"] == index["rig"] and b["motion"] == index["motion"]]
            if len(matches) != 1:
                raise contract.PartsContractError("binding review requires the exact active rig/motion binding ID")
            subject_digest = contract.content_digest(matches[0])
        elif binding is not None:
            raise contract.PartsContractError("binding is only valid for binding scope")
    identity = "review." + contract.content_digest({"appearance": index["appearance"], "scope": scope,
                                                   "subject": subject_digest})[:32]
    review = contract.seal({"kind": "parts-review", "schema_version": 1, "id": identity,
        "subject_digest": subject_digest, "scope": scope, "status": "unverified",
        "render_policy": parts_resolve.RENDER_POLICY, "qa_policy": inspect_parts.QA_POLICY,
        "checks": {name: "unverified" for name in inspect_parts.REVIEW_CHECKS},
        "evidence": {"appearance": index["appearance"], "subject": contract.reference(target),
          "binding_id": binding, "inputs": qa["inputs"], "plan_digest": plan["digest"], "preview_digest": preview["digest"],
          "qa_digest": qa["digest"], "artifacts": [{"file": name, "sha256": assets.sha256(data)} for name, data in sorted(payloads.items())],
          "contacts": {"file": "contacts.json", "sha256": assets.sha256(payloads["contacts.json"])} if contacts is not None else None}})
    payloads["review.json"] = workspace.json_bytes(review)
    folder = assets.publish_object(root, "reviews", identity, review["digest"], payloads)
    invalidated = _activate(assembly, review, folder, original_hash, index, registry)
    return {"review": contract.reference(review), "review_hash": review["digest"], "scope": scope,
        "file": (folder / "review.json").relative_to(root).as_posix(),
        "preview": (folder / "preview.json").relative_to(root).as_posix(),
        "qa": (folder / "qa.json").relative_to(root).as_posix(),
        "status": "unverified", "release_ready": False, "invalidated": invalidated}


def verify(root, index, registry, review):
    """Recompute evidence from current bytes and policies, including masked assets."""
    evidence = review["evidence"]
    if (evidence is None or evidence["appearance"] != index["appearance"]
        or review["render_policy"] != parts_resolve.RENDER_POLICY or review["qa_policy"] != inspect_parts.QA_POLICY):
        raise contract.PartsContractError("review is stale or lacks current render/QA evidence")
    loader = compose_parts.AssetLoader(root, index)
    snapshots = {}
    for reference in evidence["artifacts"]:
        local = loader.relative_asset(review, reference)
        snapshots[reference["file"]] = loader.reader.read(local["file"], reference["sha256"])
    annotations = None
    if evidence["contacts"] is not None:
        if evidence["contacts"] not in evidence["artifacts"]:
            raise contract.PartsContractError("review contact asset is outside evidence artifacts")
        annotations = workspace.decode_json(snapshots[evidence["contacts"]["file"]])
    plan, preview, qa, payloads = inspect_parts.collect(root, index, registry, annotations)
    if (qa["status"] == "error" or evidence["inputs"] != qa["inputs"] or evidence["plan_digest"] != plan["digest"]
        or evidence["preview_digest"] != preview["digest"] or evidence["qa_digest"] != qa["digest"]
        or snapshots != payloads):
        raise contract.PartsContractError("review evidence differs from current inputs, assets, timeline or toolchain")
    return plan, preview, qa, payloads


def decide(assembly, review_hash, checks, reviewer, *, decision="approved", note=None):
    root, index, registry, original_hash = parts_motion._active(assembly)
    contract._hash(review_hash, "review_hash")
    candidates = [d for d in registry.values() if d["kind"] == "parts-review" and d["digest"] == review_hash]
    if len(candidates) != 1:
        raise contract.PartsContractError("review hash is no longer active; generate current evidence")
    review = candidates[0]
    if review["status"] != "unverified":
        raise contract.PartsContractError("decision requires an undecided review; generate a new candidate")
    _, _, _, payloads = verify(root, index, registry, review)
    path = Path(checks).absolute()
    declarations = contract._mapping(contract._enum("approved", "rejected", "unverified"))(
        workspace.read_json(path.parent, path.name), "checks")
    if set(declarations) != set(inspect_parts.REVIEW_CHECKS):
        raise contract.PartsContractError(f"checks must explicitly cover {inspect_parts.REVIEW_CHECKS}")
    if decision == "approved" and any(v != "approved" for v in declarations.values()):
        raise contract.PartsContractError("approval requires every visual/motion/contact check to be explicitly approved")
    if decision == "rejected" and "rejected" not in declarations.values():
        raise contract.PartsContractError("rejection requires at least one rejected check")
    result = {k: deepcopy(v) for k, v in review.items() if k != "digest"}
    result.update(status=decision, checks=declarations, reviewer=contract._string(reviewer, "reviewer"), note=note)
    result = contract.seal(result)
    payloads["review.json"] = workspace.json_bytes(result)
    folder = assets.publish_object(root, "reviews", result["id"], result["digest"], payloads)
    invalidated = _activate(assembly, result, folder, original_hash, index, registry)
    return {"review": contract.reference(result), "scope": result["scope"], "status": decision,
            "file": (folder / "review.json").relative_to(root).as_posix(), "invalidated": invalidated,
            "release_ready": decision == "approved" and result["scope"] == "appearance"}


def add_review_arguments(parser: argparse.ArgumentParser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--scope", choices=("appearance", "part", "binding"), default="appearance")
    parser.add_argument("--subject", help="indexed part/body ID for part or binding scope")
    parser.add_argument("--binding", help="exact binding ID for binding scope")
    parser.add_argument("--contacts", type=Path)


def add_decision_arguments(parser: argparse.ArgumentParser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--review-hash", required=True)
    parser.add_argument("--checks", required=True, type=Path)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--decision", choices=("approved", "rejected"), default="approved")
    parser.add_argument("--note")


def run_review(**kwargs):
    return workspace._report(lambda: prepare(**kwargs))


def run_decision(**kwargs):
    return workspace._report(lambda: decide(**kwargs))
