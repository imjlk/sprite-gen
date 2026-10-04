# SPDX-License-Identifier: Apache-2.0
"""Import final raster motion candidates, then freeze hash-bound annotations."""
from __future__ import annotations

import argparse
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path

from sprite_gen.spec import parts_assets as library, parts_contract as contract, parts_workspace as workspace
from sprite_gen.spec.runio import atomic_write_text, publish_guard, read_guard


_SOURCE = contract._record({
    "id": contract._id, "state": contract._id, "file": contract._relative_file,
    "source_state": contract._nullable(contract._id), "timebase": contract._positive,
    "loop": contract._nullable(contract._boolean), "fps": contract._nullable(contract._string),
    "timing_policy": contract._enum("exact", "round-boundaries"),
    "source_mode": contract._enum("external", "native-finalized"),
    "expected_files": contract._mapping(contract._hash, keys=contract._relative_file),
}, {"source_state": None, "timebase": 120000, "loop": None, "fps": None,
    "timing_policy": "exact", "source_mode": "external"})
_IMPORT = contract._record({
    "kind": contract._enum("parts-motion-import"), "schema_version": contract._positive,
    "id": contract._id, "rig": contract._ref, "clips": contract._records(_SOURCE, nonempty=True),
})


def _motion(value, path):
    normalized = contract.parse(value)
    if normalized["kind"] != "parts-motion":
        raise contract.PartsContractError(f"{path}: expected parts-motion")
    return normalized


_CANDIDATE = contract._record({
    "kind": contract._enum("parts-motion-candidate"), "schema_version": contract._positive,
    "id": contract._id, "request": _IMPORT, "motion": _motion,
    "source_assets": contract._list(contract._asset, nonempty=True, unique=True), "encoding_policy": contract._enum("rgba-png.v1"),
})
_ANNOTATION_SAMPLE = contract._record({
    "image_sha256": contract._hash, "sockets": contract._mapping(contract._location),
    "pose_id": contract._nullable(contract._id),
}, {"pose_id": None})
_ANNOTATIONS = contract._record({
    "kind": contract._enum("parts-motion-annotations"), "schema_version": contract._positive,
    "id": contract._id, "candidate_digest": contract._hash,
    "clips": contract._mapping(contract._mapping(_ANNOTATION_SAMPLE)),
})


def seal_aux(value, validator):
    if not isinstance(value, dict):
        raise contract.PartsContractError("document must be an object")
    value = deepcopy(value)
    supplied = value.pop("digest", None)
    normalized = validator(value, "document")
    if normalized["schema_version"] != 1:
        raise contract.PartsContractError("document.schema_version: only version 1 is supported")
    normalized["digest"] = contract.content_digest(normalized)
    if supplied is not None and contract._hash(supplied, "document.digest") != normalized["digest"]:
        raise contract.PartsContractError("document.digest: content differs from the declared digest")
    return normalized


def _input(path: Path):
    path = Path(path).absolute()
    return workspace.decode_json(library.read_file(path.parent, path.name, limit=workspace.MAX_JSON_BYTES))


def _active(assembly: Path):
    assembly = Path(assembly).absolute()
    root = assembly.parent.resolve()
    with read_guard(root):
        original = library.read_file(root, assembly.name, limit=workspace.MAX_JSON_BYTES)
        index, registry = workspace.read_workspace(assembly)
        if original != library.read_file(root, assembly.name, limit=workspace.MAX_JSON_BYTES):
            raise contract.PartsContractError("assembly changed while reading")
    return root, index, registry, library.sha256(original)


def annotation_template(candidate: dict, topology: dict):
    return {
        "kind": "parts-motion-annotations", "schema_version": 1, "id": candidate["id"],
        "candidate_digest": candidate["digest"], "clips": {
            clip["id"]: {sample["id"]: {
                "image_sha256": sample["image"]["sha256"], "sockets": {
                    socket["id"]: {"position": [0, 0], "visible": False, "confidence": "unverified"}
                    for socket in topology["sockets"]}, "pose_id": None,
            } for sample in clip["samples"]} for clip in candidate["motion"]["clips"]},
    }


def import_motion(assembly: Path, spec: Path, source_root: Path) -> dict:
    root, index, registry, _ = _active(assembly)
    spec_path = Path(spec).absolute()
    raw_request = library.read_file(spec_path.parent, spec_path.name, limit=workspace.MAX_JSON_BYTES)
    request = _IMPORT(workspace.decode_json(raw_request), "import")
    if request["schema_version"] != 1 or request["rig"] != index["rig"]:
        raise contract.PartsContractError("import version/rig differs from selected workspace rig")
    rig = registry[index["rig"]["id"]]
    topology = registry[rig["topology"]["id"]]
    reader = library.SourceReader(source_root)
    source_guard = read_guard(reader.root) if any(
        clip["source_mode"] == "native-finalized" for clip in request["clips"]) else nullcontext()
    clips, payloads = [], {}
    with source_guard:
        for source in request["clips"]:
            clip, images = library.adapt_clip(reader, source, rig)
            clips.append(clip)
            payloads.update(images)
            if sum(map(len, payloads.values())) + reader.total_bytes > library.MAX_SNAPSHOT_BYTES:
                raise contract.PartsContractError("motion snapshot exceeds the byte budget")
    motion = contract.seal({"kind": "parts-motion", "schema_version": 1, "id": request["id"],
                            "rig": request["rig"], "clips": clips})
    # Preserve the actual source bytes too. Diagnostics never put an absolute
    # source path or wall-clock time into portable content digests.
    for data in reader.data.values():
        payloads[f"sources/{library.sha256(data)}.bin"] = data
    payloads[f"sources/{library.sha256(raw_request)}.bin"] = raw_request
    candidate = seal_aux({
        "kind": "parts-motion-candidate", "schema_version": 1, "id": request["id"],
        "request": request, "motion": motion, "encoding_policy": "rgba-png.v1",
        "source_assets": [{"file": name, "sha256": library.sha256(payloads[name])}
                          for name in sorted(payloads) if name.startswith("sources/")],
    }, _CANDIDATE)
    payloads["candidate.json"] = workspace.json_bytes(candidate)
    destination = library.publish_object(root, "candidates", candidate["id"], candidate["digest"], payloads)
    annotation_file = f"annotations/{candidate['digest']}.draft.json"
    with publish_guard(root):
        target = library.safe_path(root, annotation_file)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            atomic_write_text(target, workspace.json_bytes(annotation_template(candidate, topology)).decode("utf-8"))
    return {"candidate": (destination / "candidate.json").relative_to(root).as_posix(),
            "digest": candidate["digest"], "annotation_template": annotation_file,
            "samples": sum(len(c["samples"]) for c in clips), "status": "draft", "release_ready": False}


def _references(value):
    if isinstance(value, dict):
        if set(value) == {"id", "digest"}:
            yield value
        else:
            for item in value.values():
                yield from _references(item)
    elif isinstance(value, list):
        for item in value:
            yield from _references(item)


def _without_refs(value, removed):
    if isinstance(value, list):
        return [ref for ref in value if ref["id"] not in removed]
    return None if value is not None and value["id"] in removed else value


def _verify_candidate_sources(candidate, snapshot, rig):
    """Reconstruct from captured bytes to verify raster and timing provenance.

    Compare decoded pixels, so an encoder-version difference alone does not
    require rewriting imported PNGs or their annotation raster hashes.
    """
    blobs = {asset["sha256"]: snapshot.read(asset["file"], asset["sha256"])
             for asset in candidate["source_assets"]}
    requests = candidate["request"]["clips"]
    clips = candidate["motion"]["clips"]
    if [c["id"] for c in clips] != [r["id"] for r in requests]:
        raise contract.PartsContractError("candidate clip order differs from import request")
    for request, clip in zip(requests, clips):
        missing = set(request["expected_files"].values()) - blobs.keys()
        if missing:
            raise contract.PartsContractError("candidate is missing captured source bytes")
        captured = {name: blobs[digest] for name, digest in request["expected_files"].items()}
        virtual = library.SourceReader(snapshot.root / ".captured-source", captured=captured)
        regenerated, images = library.adapt_clip(virtual, request, rig)
        if any(clip[key] != regenerated[key] for key in ("id", "state", "view", "timebase", "loop", "source")):
            raise contract.PartsContractError("candidate timing/source provenance differs from captured source")
        if len(clip["samples"]) != len(regenerated["samples"]):
            raise contract.PartsContractError("candidate samples differ from captured source")
        for sample, expected in zip(clip["samples"], regenerated["samples"]):
            if any(sample[key] != expected[key] for key in ("id", "duration_ticks", "source_frame_id")):
                raise contract.PartsContractError("candidate sample order/timing differs from captured source")
            original = library.load_image(snapshot, sample["image"], size=(
                rig["canvas"]["width"], rig["canvas"]["height"]))
            decoded = library.image_bytes(images[expected["image"]["file"]])
            if original.tobytes() != decoded.tobytes():
                raise contract.PartsContractError("candidate raster differs from captured source geometry")


def activate_motion(assembly: Path, motion: dict, filename: str, original_hash: str,
                    index: dict, registry: dict) -> dict:
    """CAS the active index, preserving immutable history and invalidating consumers."""
    root = Path(assembly).absolute().parent.resolve()
    previous = registry.get(motion["id"])
    removed = {motion["id"]} if previous and previous["digest"] != motion["digest"] else set()
    if index["motion"] is not None and index["motion"] != contract.reference(motion):
        # v1 review subjects are opaque composite hashes. Without dependency
        # evidence, conservatively detach reviews rather than guess their scope.
        removed.update(doc["id"] for doc in registry.values() if doc["kind"] == "parts-review")
    changed = True
    while changed:
        changed = False
        removed_hashes = {registry[name]["digest"] for name in removed}
        for doc in registry.values():
            if doc["id"] in removed:
                continue
            if any(ref["id"] in removed for ref in _references(doc)) or (
                    doc["kind"] == "parts-review" and doc["subject_digest"] in removed_hashes):
                removed.add(doc["id"])
                changed = True
    next_index = deepcopy(index)
    next_registry = {name: doc for name, doc in registry.items() if name not in removed}
    next_index["documents"] = {name: path for name, path in index["documents"].items() if name not in removed}
    if index["catalog"]["id"] in removed:
        catalog = deepcopy(registry[index["catalog"]["id"]])
        catalog.pop("digest")
        for slot in catalog["slots"]:
            slot["options"] = [ref for ref in slot["options"] if ref["id"] not in removed]
            slot["default"] = _without_refs(slot["default"], removed)
        for preset in catalog["presets"]:
            preset["assignments"] = {slot: _without_refs(value, removed) for slot, value in preset["assignments"].items()}
        catalog = contract.seal(catalog)
        folder = library.publish_object(root, "catalogs", catalog["id"], catalog["digest"],
                                        {"catalog.json": workspace.json_bytes(catalog)})
        next_registry[catalog["id"]] = catalog
        next_index["catalog"] = contract.reference(catalog)
        next_index["documents"][catalog["id"]] = (folder / "catalog.json").relative_to(root).as_posix()
    next_registry[motion["id"]] = motion
    next_index["documents"][motion["id"]] = filename
    next_index["motion"] = contract.reference(motion)
    if next_index["appearance"] is not None:
        appearance = next_registry.get(next_index["appearance"]["id"])
        if appearance is None or appearance["motion"] != next_index["motion"]:
            next_index["appearance"] = None
    contract.validate_bundle(next_registry.values())
    report = {"kind": "parts-motion-invalidation", "schema_version": 1,
              "previous_motion": index["motion"], "motion": contract.reference(motion),
              "invalidated": [{"reference": contract.reference(registry[name]),
                               "file": index["documents"][name]} for name in sorted(removed)],
              "appearance_detached": index["appearance"] is not None and next_index["appearance"] is None}
    report_digest = contract.content_digest(report)
    report["digest"] = report_digest
    folder = library.publish_object(root, "changes", motion["id"], report_digest,
                                    {"invalidation.json": workspace.json_bytes(report)})
    with publish_guard(root):
        current = library.read_file(root, Path(assembly).name, limit=workspace.MAX_JSON_BYTES)
        if library.sha256(current) != original_hash:
            raise contract.PartsContractError("assembly changed; frozen object is preserved, retry before activating it")
        atomic_write_text(root / Path(assembly).name, workspace.json_bytes(next_index).decode("utf-8"))
    return {"invalidated": report["invalidated"], "appearance_detached": report["appearance_detached"],
            "invalidation_report": (folder / "invalidation.json").relative_to(root).as_posix()}


def freeze_motion(assembly: Path, candidate: str, annotations: Path) -> dict:
    root, index, registry, original_hash = _active(assembly)
    candidate_file = library.safe_path(root, candidate)
    snapshot = library.SourceReader(candidate_file.parent)
    raw = snapshot.read(candidate_file.name)
    metadata = workspace.decode_json(raw)
    if not isinstance(metadata, dict) or "digest" not in metadata:
        raise contract.PartsContractError("candidate requires a digest")
    metadata = seal_aux(metadata, _CANDIDATE)
    motion = deepcopy(metadata["motion"])
    if (motion["id"] != metadata["id"] or metadata["request"]["id"] != metadata["id"]
            or motion["rig"] != index["rig"] or metadata["request"]["rig"] != index["rig"]):
        raise contract.PartsContractError("candidate ID/rig differs from selected workspace")
    annotation = seal_aux(_input(annotations), _ANNOTATIONS)
    if annotation["candidate_digest"] != metadata["digest"] or annotation["id"] != metadata["id"]:
        raise contract.PartsContractError("annotations refer to a different candidate")
    if set(annotation["clips"]) != {clip["id"] for clip in motion["clips"]}:
        raise contract.PartsContractError("annotation clip coverage differs from motion")
    rig = registry[index["rig"]["id"]]
    size = (rig["canvas"]["width"], rig["canvas"]["height"])
    _verify_candidate_sources(metadata, snapshot, rig)
    for clip in motion["clips"]:
        positions = annotation["clips"][clip["id"]]
        if set(positions) != {sample["id"] for sample in clip["samples"]}:
            raise contract.PartsContractError("annotation sample coverage differs from motion")
        for sample in clip["samples"]:
            location = positions[sample["id"]]
            if location["image_sha256"] != sample["image"]["sha256"]:
                raise contract.PartsContractError("annotation raster hash differs from final sample")
            library.load_image(snapshot, sample["image"], size=size)
            if any(socket["confidence"] != "verified" for socket in location["sockets"].values()):
                raise contract.PartsContractError("unverified socket blocks motion freeze")
            sample.update(sockets=location["sockets"], pose_id=location["pose_id"])
    encoded_annotations = workspace.json_bytes(annotation)
    motion.pop("digest")
    motion["freeze"] = {"candidate_digest": metadata["digest"], "encoding_policy": metadata["encoding_policy"],
                        "source_assets": metadata["source_assets"],
                        "annotations": {"file": "annotations.json", "sha256": library.sha256(encoded_annotations)}}
    motion = contract.seal(motion)
    topology = registry[rig["topology"]["id"]]
    contract.validate_bundle([topology, rig, motion])
    payloads = {name: data for name, data in snapshot.data.items() if name != candidate_file.name}
    payloads["annotations.json"] = encoded_annotations
    payloads["motion.json"] = workspace.json_bytes(motion)
    destination = library.publish_object(root, "motions", motion["id"], motion["digest"], payloads)
    filename = (destination / "motion.json").relative_to(root).as_posix()
    activated = activate_motion(assembly, motion, filename, original_hash, index, registry)
    return {"motion": contract.reference(motion), "file": filename, "status": "frozen",
            "release_ready": False, **activated}


def add_import_arguments(parser: argparse.ArgumentParser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)


def add_freeze_arguments(parser: argparse.ArgumentParser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--candidate", required=True, help="workspace-relative candidate.json path")
    parser.add_argument("--annotations", required=True, type=Path)


def run_import(*, assembly: Path, spec: Path, source_root: Path) -> int:
    return workspace._report(lambda: import_motion(assembly, spec, source_root))


def run_freeze(*, assembly: Path, candidate: str, annotations: Path) -> int:
    return workspace._report(lambda: freeze_motion(assembly, candidate, annotations))
