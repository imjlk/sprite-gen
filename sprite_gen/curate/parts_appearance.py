# SPDX-License-Identifier: Apache-2.0
"""Pin an authored selection to the current immutable rig/motion/body/catalog."""
from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path

from sprite_gen.compose import parts_resolve
from sprite_gen.curate import parts_motion
from sprite_gen.spec import parts_assets as assets, parts_contract as contract, parts_workspace as workspace
from sprite_gen.spec.runio import atomic_write_text, publish_guard

_INPUT = contract._record({"kind": contract._enum("parts-appearance-selection"), "schema_version": contract._positive,
    "id": contract._id, "selections": contract._mapping(contract._selection), "presets": contract._ids,
    "palette": contract._mapping(contract._rgb), "mirror": contract._boolean},
    {"selections": {}, "presets": [], "palette": {}, "mirror": False})


def create(assembly, spec):
    root, index, registry, original_hash = parts_motion._active(assembly)
    path = Path(spec).absolute()
    request = _INPUT(workspace.read_json(path.parent, path.name), "selection")
    if request["schema_version"] != 1 or index["motion"] is None:
        raise contract.PartsContractError("selection requires version 1 and an active frozen motion")
    appearance = contract.seal({**request, "kind": "parts-appearance",
        **{key: index[key] for key in ("rig", "motion", "catalog", "body", "states")}})
    old = registry.get(appearance["id"])
    if old is not None and old["kind"] != "parts-appearance":
        raise contract.PartsContractError("appearance ID conflicts with another document kind")
    parts_resolve.resolve(appearance, registry.values())
    folder = assets.publish_object(root, "appearances", appearance["id"], appearance["digest"],
                                   {"appearance.json": workspace.json_bytes(appearance)})
    filename = (folder / "appearance.json").relative_to(root).as_posix()
    next_index = deepcopy(index)
    removed = set()
    if old is not None and old["digest"] != appearance["digest"]:
        removed.add(old["id"])
        changed = True
        while changed:
            changed = False
            for doc in registry.values():
                if doc["id"] not in removed and (any(r["id"] in removed for r in parts_motion._references(doc))
                    or (doc["kind"] == "parts-review" and doc["subject_digest"] == old["digest"])):
                    removed.add(doc["id"])
                    changed = True
    next_index["documents"] = {name: file for name, file in index["documents"].items() if name not in removed}
    next_index["documents"][appearance["id"]] = filename
    next_index["appearance"] = contract.reference(appearance)
    with publish_guard(root):
        current = assets.read_file(root, Path(assembly).name, limit=workspace.MAX_JSON_BYTES)
        if assets.sha256(current) != original_hash:
            raise contract.PartsContractError("assembly changed; appearance preserved, retry activation")
        atomic_write_text(Path(assembly), workspace.json_bytes(next_index).decode("utf-8"))
    return {"appearance": contract.reference(appearance), "file": filename,
            "invalidated": sorted(removed - {appearance["id"]}), "release_ready": False}


def add_arguments(parser: argparse.ArgumentParser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--spec", required=True, type=Path)


def run(*, assembly: Path, spec: Path):
    return workspace._report(lambda: create(assembly, spec))
