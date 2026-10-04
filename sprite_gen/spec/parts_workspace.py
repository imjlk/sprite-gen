# SPDX-License-Identifier: Apache-2.0
"""Create and inspect separate parts workspaces, never legacy sprite runs."""
from __future__ import annotations

import argparse
from importlib.resources import files
import json
from pathlib import Path
import shutil
import tempfile

from sprite_gen.spec import parts_contract as contract
from sprite_gen.spec.runio import RWLockUnavailable

MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_JSON_DEPTH = 64
PRESET_DIR = files("sprite_gen.spec").joinpath("parts_presets")


def preset_names() -> tuple[str, ...]:
    return tuple(sorted(p.name.removesuffix(".json") for p in PRESET_DIR.iterdir() if p.name.endswith(".json")))


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise contract.PartsContractError(f"JSON: duplicate field {key!r}")
        result[key] = value
    return result


def decode_json(data: bytes):
    if len(data) > MAX_JSON_BYTES:
        raise contract.PartsContractError("JSON exceeds the 4 MiB limit")
    def invalid_constant(value):
        raise contract.PartsContractError(f"JSON: non-finite constant {value}")
    try:
        result = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object,
                            parse_constant=invalid_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise contract.PartsContractError(f"invalid JSON: {exc}") from exc
    pending = [(result, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > MAX_JSON_DEPTH:
            raise contract.PartsContractError(f"JSON exceeds the {MAX_JSON_DEPTH} level nesting limit")
        if isinstance(item, dict):
            pending.extend((value, depth + 1) for value in item.values())
        elif isinstance(item, list):
            pending.extend((value, depth + 1) for value in item)
    return result


def relative_path(root: Path, value: str) -> Path:
    contract._relative_file(value, "workspace.file")
    resolved = (root / value).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise contract.PartsContractError(f"workspace.file escapes allowed root: {value}")
    return resolved


def read_json(root: Path, value: str):
    from sprite_gen.spec.parts_assets import read_file
    relative_path(root, value)
    return decode_json(read_file(root, value, limit=MAX_JSON_BYTES))


def json_bytes(data) -> bytes:
    return (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


_INDEX = contract._record({
    "kind": contract._enum("parts-workspace"), "schema_version": contract._positive,
    "id": contract._id, "documents": contract._mapping(contract._relative_file),
    "rig": contract._ref, "catalog": contract._ref,
    "motion": contract._nullable(contract._ref), "appearance": contract._nullable(contract._ref),
    "states": contract._list(contract._id, nonempty=True, unique=True),
    "body": contract._list(contract._ref, unique=True),
}, {"motion": None, "appearance": None, "body": []})


def read_workspace(assembly: Path, *, complete=False):
    assembly = Path(assembly).absolute()
    root = assembly.parent.resolve()
    index = _INDEX(read_json(root, assembly.name), "assembly")
    if index["schema_version"] != 1:
        raise contract.PartsContractError("assembly.schema_version: only version 1 is supported")
    documents = []
    for identity, filename in index["documents"].items():
        doc = contract.parse(read_json(root, filename))
        if doc["id"] != identity:
            raise contract.PartsContractError(f"assembly: document ID mismatch for {filename}")
        documents.append(doc)
    registry = contract.validate_bundle(documents, complete=complete)
    for key, kind in (("rig", "parts-rig"), ("catalog", "parts-catalog"),
                      ("motion", "parts-motion"), ("appearance", "parts-appearance")):
        if index[key] is not None:
            contract._get(registry, index[key], kind, f"assembly.{key}")
    if registry[index["catalog"]["id"]]["rig"] != index["rig"]:
        raise contract.PartsContractError("assembly: catalog/rig mismatch")
    if index["motion"] is not None and registry[index["motion"]["id"]]["rig"] != index["rig"]:
        raise contract.PartsContractError("assembly: motion/rig mismatch")
    for ref in index["body"]:
        pack = contract._get(registry, ref, "parts-pack", "assembly.body")
        if pack["role"] != "body" or not any(b["rig"] == index["rig"] and b["motion"] == index["motion"] for b in pack["bindings"]):
            raise contract.PartsContractError("assembly: body/rig/motion mismatch")
    if index["appearance"] is not None:
        appearance = registry[index["appearance"]["id"]]
        if any(appearance[key] != index[key] for key in ("rig", "catalog", "motion")):
            raise contract.PartsContractError("assembly: appearance differs from selected rig/catalog/motion")
        if appearance["body"] != index["body"]:
            raise contract.PartsContractError("assembly: appearance differs from selected body")
        if complete and appearance["states"] != index["states"]:
            raise contract.PartsContractError("assembly: requested states differ from appearance states")
    elif complete:
        raise contract.PartsContractError("assembly: no appearance; motions and selected part bindings are still required")
    return index, registry


def initial_documents(preset: str):
    if preset not in preset_names():
        raise contract.PartsContractError(f"unknown preset {preset!r}")
    source = decode_json(PRESET_DIR.joinpath(preset + ".json").read_bytes())
    if set(source) != {"topology", "rig", "catalog", "states"}:
        raise contract.PartsContractError("invalid packaged preset fields")
    topology = contract.seal(source["topology"])
    source["rig"]["topology"] = contract.reference(topology)
    rig = contract.seal(source["rig"])
    source["catalog"].update(rig=contract.reference(rig), topology=contract.reference(topology))
    catalog = contract.seal(source["catalog"])
    docs = [topology, rig, catalog]
    contract.validate_bundle(docs)
    return docs, source["states"]


def initialize(directory: Path, preset: str) -> dict:
    """Publish a draft scaffold as one folder. Existing contents are never replaced."""
    target = Path(directory).absolute()
    if target.is_symlink() or (target.exists() and (not target.is_dir() or any(target.iterdir()))):
        raise contract.PartsContractError(f"parts-init requires a new or empty directory: {target}")
    docs, states = initial_documents(preset)
    topology, rig, catalog = docs
    filenames = ["profiles/topology.json", "profiles/rig.json", "catalog.json"]
    index = {
        "kind": "parts-workspace", "schema_version": 1, "id": preset + ".workspace",
        "documents": {d["id"]: name for d, name in zip(docs, filenames)},
        "rig": contract.reference(rig), "catalog": contract.reference(catalog),
        "motion": None, "appearance": None, "states": states, "body": [],
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".parts-init-", dir=target.parent))
    try:
        for doc, filename in zip(docs, filenames):
            path = staging / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(json_bytes(doc))
        (staging / "assembly.json").write_bytes(json_bytes(index))
        read_workspace(staging / "assembly.json")
        if target.exists():
            target.rmdir()  # Fails if another process added any contents meanwhile.
        staging.rename(target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return index


def add_init_arguments(parser: argparse.ArgumentParser):
    parser.add_argument("--dir", required=True, type=Path)
    parser.add_argument("--preset", choices=preset_names(), default="human.sd.v1")


def add_validate_arguments(parser: argparse.ArgumentParser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--complete", action="store_true", help="also require an appearance and full requested sample coverage; does not grant release approval")


def _report(action):
    try:
        payload = action()
    except (contract.PartsContractError, OSError, ValueError, UnicodeError, RWLockUnavailable) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"ok": True, **payload}, ensure_ascii=False, indent=2))
    return 0


def run_init(*, dir: Path, preset="human.sd.v1") -> int:
    def action():
        index = initialize(dir, preset)
        return {"assembly": str(Path(dir).absolute() / "assembly.json"), "rig": index["rig"]["id"],
                "status": "draft", "release_ready": False}
    return _report(action)


def run_validate(*, assembly: Path, complete=False) -> int:
    def action():
        index, registry = read_workspace(assembly, complete=complete)
        catalog = registry[index["catalog"]["id"]]
        return {"documents": len(registry), "rig": index["rig"]["id"],
                "slots": [s["id"] for s in catalog["slots"]], "requested_states": index["states"],
                "validation": "complete-declarations" if complete else "declarations",
                "motion_present": index["motion"] is not None, "appearance_present": index["appearance"] is not None,
                "release_ready": False}
    return _report(action)
