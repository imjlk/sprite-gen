# SPDX-License-Identifier: Apache-2.0
"""Revise an authored body topology and rebuild pixel-preserving body layers."""

from copy import deepcopy
from pathlib import Path
from sprite_gen._deps import np
from PIL import Image
from sprite_gen.curate import parts_motion
from sprite_gen.frames import (
    parts_segments,
    parts_import,
    parts_production as production,
)
from sprite_gen.spec import (
    parts_assets as assets,
    parts_contract as contract,
    parts_workspace as workspace,
)
from sprite_gen.spec.runio import atomic_write_text, publish_guard


def extend_topology(assembly, topology):
    """Explicit revision cascade; retain immutable raster bytes and native timing."""
    root, index, registry, original_hash = parts_motion._active(assembly)
    previous = registry[index["rig"]["id"]]["topology"]
    value = contract.seal(topology)
    if value["id"] != previous["id"] or value["kind"] != "parts-topology":
        raise contract.PartsContractError("topology must revise the active topology ID")
    if contract.reference(value) == previous:
        return {
            "rig": index["rig"],
            "motion": index["motion"],
            "body": index["body"],
            "art_approved": False,
        }
    # Validate selected sources before publishing any workspace revision.
    for file in (root / "productions").glob("*/production.json"):
        state = production._load(root, file.parent.name)
        for track_name, track in state["tracks"].items():
            for pose, row in track["poses"].items():
                if row.get("selected"):
                    record = production.candidate_record(
                        root, state, pose, row["selected"], track=track_name
                    )
                    if record["motion"] != index["motion"]:
                        raise contract.PartsContractError(
                            "candidate motion is not the source revision"
                        )
    dropped = {
        "parts-review",
        "parts-build-lock",
        "parts-appearance",
        "parts-fitting",
        "parts-stability",
    }
    changes = {previous["id"]: (previous, contract.reference(value))}
    revised = {value["id"]: value}
    documents = [
        d
        for d in registry.values()
        if d["kind"] not in dropped and d["id"] != value["id"]
    ]
    for kind in ("parts-rig", "parts-motion", "parts-pack", "parts-catalog"):
        for document in documents:
            if document["kind"] != kind:
                continue
            updated = deepcopy(document)
            for old, new in changes.values():
                updated = parts_import._replace_refs(updated, old, new)
            updated.pop("digest")
            updated = contract.seal(updated)
            revised[document["id"]] = updated
            changes[document["id"]] = (
                contract.reference(document),
                contract.reference(updated),
            )
    for document in documents:
        revised.setdefault(document["id"], document)
    contract.validate_bundle(revised.values())
    updated = deepcopy(index)
    updated.update(appearance=None, fitting=[], stability=[], documents={})
    for old, new in changes.values():
        updated = parts_import._replace_refs(updated, old, new)
    for name, document in revised.items():
        before = registry[name]
        source = root / Path(index["documents"][name]).parent
        payloads = {}

        def render_files(value):
            if isinstance(value, dict):
                if set(value) == {"file", "sha256"}:
                    yield value
                else:
                    for key, child in value.items():
                        if key not in ("import", "source"):
                            yield from render_files(child)
            elif isinstance(value, list):
                for child in value:
                    yield from render_files(child)

        for reference in render_files(before):
            payloads[reference["file"]] = assets.SourceReader(source).read(
                reference["file"], reference["sha256"]
            )
        for reference in (before.get("import") or {}).get("source_assets", []):
            payloads[reference["file"]] = assets.SourceReader(source).read(
                reference["file"], reference["sha256"]
            )
        payloads["document.json"] = workspace.json_bytes(document)
        folder = assets.publish_object(
            root, "revisions", name, document["digest"], payloads
        )
        updated["documents"][name] = (
            (folder / "document.json").relative_to(root).as_posix()
        )
    with publish_guard(root):
        if assets.sha256(assets.read_file(root, Path(assembly).name)) != original_hash:
            raise contract.PartsContractError(
                "assembly changed during topology refinement"
            )
        atomic_write_text(Path(assembly), workspace.json_bytes(updated).decode())
    # Raster poses are unchanged: rebind selected candidates with explicit ancestry,
    # rather than reinterpret or overwrite their immutable generation references.
    for file in (root / "productions").glob("*/production.json"):
        state = production._load(root, file.parent.name)
        for track in state["tracks"].values():
            for pose, row in track["poses"].items():
                if not row.get("selected"):
                    continue
                base = root / row["selected"]
                record = workspace.decode_json((base / "candidate.json").read_bytes())
                if record["motion"] != index["motion"]:
                    raise contract.PartsContractError(
                        "candidate motion is not the source revision"
                    )
                images = {
                    key: Image.open(base / ref["file"]).copy()
                    for key, ref in record["images"].items()
                }
                previous_digest = record["digest"]
                record = {
                    k: v for k, v in record.items() if k not in ("digest", "images")
                }
                record["motion"] = updated["motion"]
                record["method"] = {
                    **record["method"],
                    "body_refinement": {
                        "from_candidate": previous_digest,
                        "from_motion": index["motion"],
                        "pixel_preserving": True,
                    },
                }
                _, location = production._publish_candidate(
                    root, state["config"]["part_id"], record, images
                )
                production._select(track, [pose], location, keep_approved=False)
        state["built"] = None
        production._save(root, state)
    return {
        "rig": updated["rig"],
        "motion": updated["motion"],
        "body": updated["body"],
        "art_approved": False,
    }


def refine(assembly, *, topology, labels, colors, out, body_id):
    root, index, registry, _ = parts_motion._active(assembly)
    original = registry[index["motion"]["id"]]
    topology = workspace.decode_json(Path(topology).read_bytes())
    topology.pop("digest", None)
    topology = contract.seal(topology)
    palette, provides = parts_segments.read_colors(colors)
    contract._members(
        list(palette) + [n for names in provides.values() for n in names],
        [n["id"] for n in topology["nodes"]],
        "body labels",
    )
    contract._members([body_id], [ref["id"] for ref in index["body"]], "active body")
    loader = production.compose_parts.AssetLoader(root, index)
    for clip in original["clips"]:
        for sample in clip["samples"]:
            source = np.asarray(loader(original, sample["image"]).convert("RGBA"))
            file = Path(labels) / f"{sample['pose_id']}.png"
            painted = np.asarray(Image.open(file).convert("RGBA"))
            if (
                painted.shape != source.shape
                or not np.array_equal(painted[..., 3] > 0, source[..., 3] > 0)
                or np.any(painted[..., 3][source[..., 3] > 0] != 255)
            ):
                raise contract.PartsContractError(
                    "authored labels must cover each source pixel exactly"
                )
            known = np.zeros(source.shape[:2], dtype=bool)
            for color in palette.values():
                known |= np.all(painted[..., :3] == color, axis=-1)
            if np.any((source[..., 3] > 0) & ~known):
                raise contract.PartsContractError(
                    "authored body labels contain unknown colors"
                )
    revision = extend_topology(assembly, topology)
    root, index, registry, _ = parts_motion._active(assembly)
    all_states = list(dict.fromkeys(c["state"] for c in original["clips"]))
    active_states = index["states"]
    index["states"] = all_states
    atomic_write_text(Path(assembly), workspace.json_bytes(index).decode())
    try:
        result = parts_segments.segment_body(
            assembly, out, colors=colors, labels=labels, body_id=body_id, tolerance=0
        )
        # Refuse fuzzy or incomplete author labels: every source pixel is preserved
        # once, including RGBA and alpha; no art can be manufactured by labels.
        spec = workspace.decode_json(Path(result["spec"]).read_bytes())
        for clip in original["clips"]:
            for sample in clip["samples"]:
                source = production.compose_parts.AssetLoader(root, index)(
                    original, sample["image"]
                )
                reconstructed = Image.new("RGBA", source.size)
                coverage = np.zeros((source.height, source.width), dtype=np.uint16)
                for piece in spec["pieces"]:
                    placement = piece["pose_map"][sample["pose_id"]]
                    image = Image.open(
                        Path(result["source_root"]) / placement["image"]["file"]
                    ).convert("RGBA")
                    coverage += np.asarray(image)[..., 3] > 0
                    reconstructed.alpha_composite(image)
                if (
                    np.any(coverage > 1)
                    or reconstructed.tobytes() != source.convert("RGBA").tobytes()
                ):
                    raise contract.PartsContractError(
                        f"body refinement changes pixels: {sample['pose_id']}"
                    )
        imported = parts_import.import_pack(
            assembly, Path(result["spec"]), Path(result["source_root"])
        )
        # A new body changes zmap visibility even when topology/motion is the
        # same. Never carry old QC or human approval into that composition.
        for file in (root / "productions").glob("*/production.json"):
            state = production._load(root, file.parent.name)
            for track in state["tracks"].values():
                for row in track["poses"].values():
                    row.update(
                        check=None, decision="unverified", reviewer=None, note=None
                    )
            state["built"] = None
            production._save(root, state)
    finally:
        latest = workspace.decode_json(Path(assembly).read_bytes())
        latest["states"] = active_states
        atomic_write_text(Path(assembly), workspace.json_bytes(latest).decode())
    return {
        **result,
        "body": imported["part"],
        "revision": revision,
        "pixel_identical_samples": sum(len(c["samples"]) for c in original["clips"]),
        "timing_changed": False,
        "art_approved": False,
        "release_ready": False,
    }


def add_arguments(parser):
    for name in ("assembly", "topology", "labels", "colors", "out"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--body-id", required=True)


def run(**kwargs):
    return workspace._report(lambda: refine(**kwargs))
