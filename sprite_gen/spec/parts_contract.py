# SPDX-License-Identifier: Apache-2.0
"""Strict, filesystem-free declarations for the opt-in parts library.

No species, clothing or locomotion vocabulary is built into the renderer contract.
Parsing checks declarations and content digests; bundle validation additionally
checks exact references, interfaces and requested sample coverage. Neither opens
an image nor grants a release approval. See docs/parts/01-contract.md.
"""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import hashlib
import json
import re
from typing import Any, Callable, Iterable

Validator = Callable[[Any, str], Any]
ID = re.compile(r"[a-z][a-z0-9_.-]{0,127}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
DIGEST_DOMAIN = b"sprite-gen.parts.canonical.v1\0"


class PartsContractError(ValueError):
    """A declaration cannot be used as the content it claims to represent."""


def _fail(path: str, message: str):
    raise PartsContractError(f"{path}: {message}")


def _string(value, path):
    if not isinstance(value, str) or not value:
        _fail(path, "expected a nonempty string")
    return value


def _id(value, path):
    if not isinstance(value, str) or not ID.fullmatch(value):
        _fail(path, "expected an ID containing lowercase letters, digits, '.', '_' or '-'")
    return value


def _hash(value, path):
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        _fail(path, "expected a lowercase SHA-256 digest")
    return value


def _integer(value, path):
    if type(value) is not int:
        _fail(path, "expected a JSON integer, not a boolean or float")
    return value


def _positive(value, path):
    value = _integer(value, path)
    if value <= 0:
        _fail(path, "expected a positive integer")
    return value


def _boolean(value, path):
    if type(value) is not bool:
        _fail(path, "expected a JSON boolean")
    return value


def _enum(*values):
    def check(value, path):
        if not isinstance(value, str) or value not in values:
            _fail(path, f"expected one of {values}")
        return value
    return check


def _nullable(check):
    return lambda value, path: None if value is None else check(value, path)


def _list(check, *, nonempty=False, unique=False):
    def validate(value, path):
        if not isinstance(value, list) or (nonempty and not value):
            _fail(path, "expected a nonempty array" if nonempty else "expected an array")
        result = [check(item, f"{path}[{i}]") for i, item in enumerate(value)]
        if unique and len({json.dumps(item, sort_keys=True) for item in result}) != len(result):
            _fail(path, "duplicate entries")
        return result
    return validate


def _mapping(check, *, keys=_id):
    def validate(value, path):
        if not isinstance(value, dict):
            _fail(path, "expected an object")
        return {keys(key, f"{path}.key"): check(item, f"{path}.{key}")
                for key, item in value.items()}
    return validate


def _record(fields, defaults=None):
    defaults = defaults or {}
    def validate(value, path):
        if not isinstance(value, dict):
            _fail(path, "expected an object")
        unknown = set(value) - set(fields)
        missing = set(fields) - set(value) - set(defaults)
        if unknown:
            _fail(path, f"unknown fields: {sorted(unknown, key=str)}")
        if missing:
            _fail(path, f"missing fields: {sorted(missing)}")
        return {key: check(value[key] if key in value else deepcopy(defaults[key]), f"{path}.{key}")
                for key, check in fields.items()}
    return validate


def _records(check, *, nonempty=False):
    def validate(value, path):
        result = _list(check, nonempty=nonempty)(value, path)
        names = [item["id"] for item in result]
        if len(set(names)) != len(names):
            _fail(path, "duplicate ID")
        return result
    return validate


def _point(value, path):
    result = _list(_integer)(value, path)
    if len(result) != 2:
        _fail(path, "expected [x, y]")
    return result


def _rgb(value, path):
    result = _list(_integer)(value, path)
    if len(result) != 3 or any(v < 0 or v > 255 for v in result):
        _fail(path, "expected three RGB integers in 0..255")
    return result


def _relative_file(value, path):
    value = _string(value, path)
    if ("\\" in value or ":" in value or "\x00" in value or value.startswith("/")
            or any(part in {"", ".", ".."} for part in value.split("/"))):
        _fail(path, "file must be a portable relative path without traversal")
    return value


_ids = _list(_id, unique=True)
_ref = _record({"id": _id, "digest": _hash})
_asset = _record({"file": _relative_file, "sha256": _hash})


def _selection(value, path):
    return _list(_ref, unique=True)(value, path) if isinstance(value, list) else _nullable(_ref)(value, path)


def _selected_refs(value):
    return [] if value is None else value if isinstance(value, list) else [value]


_node = _record({"id": _id, "parent": _nullable(_id), "required": _boolean,
                 "symmetric_with": _nullable(_id), "contact": _boolean},
                {"parent": None, "required": True, "symmetric_with": None, "contact": False})
_socket = _record({"id": _id, "node": _id, "required": _boolean}, {"required": False})
_slot = _record({"id": _id, "nodes": _ids, "sockets": _ids, "optional": _boolean,
                 "max_count": _positive, "empty_policy": _enum("body-default", "no-piece"),
                 "semantic_role": _nullable(_id), "overrides": _ids},
                {"nodes": [], "sockets": [], "optional": False, "max_count": 1,
                 "empty_policy": "body-default", "semantic_role": None, "overrides": []})
_location = _record({"position": _point, "visible": _boolean,
                     "confidence": _enum("verified", "unverified")})
_sample = _record({"id": _id, "image": _asset, "duration_ticks": _positive,
                   "sockets": _mapping(_location), "pose_id": _nullable(_id),
                   "source_frame_id": _nullable(_string)},
                  {"pose_id": None, "source_frame_id": None})
_motion_source = _record({
    "format": _enum("image", "external", "atlas", "strip"), "file": _relative_file,
    "state": _nullable(_id), "files": _mapping(_hash, keys=_relative_file),
    "timing_policy": _enum("exact", "round-boundaries"),
    "duration_fractions": _list(_string, nonempty=True),
    "duration_errors_ticks": _list(_string, nonempty=True),
    "source_mode": _enum("external", "native-finalized"),
    "fps_override": _nullable(_string), "loop_override": _nullable(_boolean),
}, {"source_mode": "external", "fps_override": None, "loop_override": None})
_motion_freeze = _record({"candidate_digest": _hash, "annotations": _asset,
                          "source_assets": _list(_asset, unique=True), "encoding_policy": _id})
_clip = _record({"id": _id, "state": _id, "view": _id, "timebase": _positive,
                 "loop": _boolean, "samples": _records(_sample, nonempty=True),
                 "source": _nullable(_motion_source)}, {"source": None})
_mask = _record({"image": _asset, "target_node": _id,
                 "space": _enum("part-local", "rig-canvas"), "channel": _enum("luma", "alpha")},
                {"channel": "luma"})
_part_source = _record({"image": _asset, "format": _enum("full-canvas", "cropped", "local"),
                       "crop_offset": _point, "pivot": _point})
_placement = _record({"image": _asset, "pivot": _point, "target_socket": _id,
                      "visible": _boolean, "offset": _point, "mask": _nullable(_mask),
                      "before": _ids, "after": _ids, "allow_clip": _boolean,
                      "palette_regions": _mapping(_asset), "source": _nullable(_part_source)},
                     {"visible": True, "offset": [0, 0], "mask": None, "before": [], "after": [],
                      "allow_clip": False, "palette_regions": {}, "source": None})
_piece = _record({"id": _id, "slot": _nullable(_id), "node": _id,
                  "mode": _enum("raster", "held"), "sample_map": _mapping(_mapping(_placement)),
                  "provides": _ids, "replaces": _ids, "join_interface": _nullable(_id),
                  "palette_regions": _mapping(_asset)},
                 {"provides": [], "replaces": [], "join_interface": None, "palette_regions": {}})
_binding = _record({"id": _id, "rig": _ref, "motion": _ref, "view": _id,
                    "occupies": _ids, "requires": _ids, "excludes": _ids,
                    "pieces": _records(_piece, nonempty=True), "mirror_safe": _boolean},
                   {"requires": [], "excludes": [], "mirror_safe": False})
_group = _record({"id": _id, "label": _string, "parent": _nullable(_id)}, {"parent": None})
_choice = _record({"id": _id, "group": _id, "label": _string,
                   "options": _list(_ref, unique=True), "default": _selection,
                   "join_interface": _nullable(_id)}, {"options": [], "default": None, "join_interface": None})
_palette = _record({"id": _id, "label": _string, "default": _rgb})
_preset = _record({"id": _id, "label": _string, "assignments": _mapping(_selection)})
_pack_import = _record({"request_sha256": _hash, "source_assets": _list(_asset, nonempty=True, unique=True),
                       "style": _id, "encoding_policy": _id, "coverage": _enum("complete", "partial"),
                       "states": _list(_id, nonempty=True, unique=True)})
_review_evidence = _record({"appearance": _ref, "inputs": _list(_ref, nonempty=True, unique=True),
    "plan_digest": _hash, "preview_digest": _hash, "qa_digest": _hash,
    "artifacts": _list(_asset, nonempty=True, unique=True), "contacts": _nullable(_asset),
    "subject": _ref, "binding_id": _nullable(_id)}, {"contacts": None, "binding_id": None})

_COMMON = {"kind": _string, "schema_version": _positive, "id": _id}
_SCHEMAS = {
    "parts-topology": _record({**_COMMON, "nodes": _records(_node, nonempty=True),
                               "sockets": _records(_socket), "slots": _records(_slot)}),
    "parts-rig": _record({**_COMMON, "topology": _ref, "view": _id, "style": _id,
                          "canvas": _record({"width": _positive, "height": _positive}),
                          "default_body_ids": _ids, "mirror_allowed": _boolean},
                         {"default_body_ids": [], "mirror_allowed": False}),
    "parts-motion": _record({**_COMMON, "rig": _ref, "clips": _records(_clip, nonempty=True),
                             "freeze": _nullable(_motion_freeze)}, {"freeze": None}),
    "parts-pack": _record({**_COMMON, "design_id": _id, "bindings": _records(_binding, nonempty=True),
                           "role": _enum("part", "body"), "body_mode": _enum("segmented", "flat"),
                           "import": _nullable(_pack_import)},
                          {"role": "part", "body_mode": "segmented", "import": None}),
    "parts-catalog": _record({**_COMMON, "rig": _ref, "topology": _ref,
                              "groups": _records(_group), "slots": _records(_choice),
                              "palette_channels": _records(_palette), "presets": _records(_preset)},
                             {"palette_channels": [], "presets": []}),
    "parts-appearance": _record({**_COMMON, "rig": _ref, "motion": _ref, "catalog": _ref,
                                 "states": _list(_id, nonempty=True, unique=True),
                                 "selections": _mapping(_selection), "presets": _ids,
                                 "palette": _mapping(_rgb), "body": _list(_ref, unique=True),
                                 "mirror": _boolean},
                                {"selections": {}, "presets": [], "palette": {}, "body": [], "mirror": False}),
    "parts-review": _record({**_COMMON, "subject_digest": _hash,
                             "scope": _enum("part", "binding", "appearance"),
                             "status": _enum("approved", "rejected", "unverified"),
                             "render_policy": _id, "qa_policy": _id,
                             "evidence": _nullable(_review_evidence),
                             "checks": _mapping(_enum("approved", "rejected", "unverified")),
                             "reviewer": _nullable(_string), "note": _nullable(_string)},
                            {"evidence": None, "checks": {}, "reviewer": None, "note": None}),
    "parts-build-lock": _record({**_COMMON, "appearance": _ref, "inputs": _list(_ref, nonempty=True, unique=True),
                                 "toolchain": _mapping(_string, keys=_string),
                                 "render_policy": _id, "qa_policy": _id,
                                 "reviews": _list(_ref, unique=True), "outputs": _list(_asset, unique=True)},
                                {"reviews": [], "outputs": []}),
}
KINDS = tuple(_SCHEMAS)


def content_digest(normalized: dict) -> str:
    """Hash a normalized declaration, excluding only its own digest field."""
    payload = {key: value for key, value in normalized.items() if key != "digest"}
    try:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise PartsContractError(f"document: cannot encode canonical JSON: {exc}") from exc
    return hashlib.sha256(DIGEST_DOMAIN + encoded).hexdigest()


def _parents(records, path):
    by_id = {item["id"]: item for item in records}
    finished = set()
    for item in records:
        current, visited = item["id"], set()
        while current is not None and current not in finished:
            if current in visited:
                _fail(path, f"parent cycle at {current}")
            if current not in by_id:
                _fail(path, f"unknown parent {current}")
            visited.add(current)
            current = by_id[current]["parent"]
        finished.update(visited)


def _members(values, known, path):
    missing = set(values) - set(known)
    if missing:
        _fail(path, f"unknown references: {sorted(missing)}")


def _local(document):
    kind, path = document["kind"], document["id"]
    if kind == "parts-topology":
        nodes = {node["id"]: node for node in document["nodes"]}
        _parents(document["nodes"], f"{path}.nodes")
        if sum(n["parent"] is None for n in nodes.values()) != 1:
            _fail(f"{path}.nodes", "expected exactly one root node")
        for node in nodes.values():
            partner = node["symmetric_with"]
            if partner is not None and (partner == node["id"] or partner not in nodes
                                        or nodes[partner]["symmetric_with"] != node["id"]):
                _fail(f"{path}.nodes", "symmetry must name a reciprocal, distinct node")
        sockets = {s["id"]: s for s in document["sockets"]}
        for socket in sockets.values():
            _members([socket["node"]], nodes, f"{path}.sockets.node")
        for slot in document["slots"]:
            _members(slot["nodes"], nodes, f"{path}.slots.{slot['id']}.node")
            _members(slot["sockets"], sockets, f"{path}.slots.{slot['id']}.socket")
            _members(slot["overrides"], {s["id"] for s in document["slots"]}, f"{path}.slots.overrides")
        # Slot precedence is data, including for non-humanoid catalogs.
        relations = {s["id"]: s["overrides"] for s in document["slots"]}
        dependents = {name: [] for name in relations}
        counts = {name: len(targets) for name, targets in relations.items()}
        for name, targets in relations.items():
            for target in targets:
                dependents[target].append(name)
        ready = [name for name, count in counts.items() if count == 0]
        visited = 0
        while ready:
            visited += 1
            for name in dependents[ready.pop()]:
                counts[name] -= 1
                if counts[name] == 0:
                    ready.append(name)
        if visited != len(relations):
            _fail(path, "slot override cycle")
    elif kind == "parts-motion":
        pairs = [(c["state"], c["view"]) for c in document["clips"]]
        if len(set(pairs)) != len(pairs):
            _fail(path, "duplicate state/view clip")
        for clip in document["clips"]:
            if clip["source"] is not None:
                source = clip["source"]
                if source["file"] not in source["files"]:
                    _fail(path, "source descriptor has no source hash")
                if any(len(source[name]) != len(clip["samples"])
                       for name in ("duration_fractions", "duration_errors_ticks")):
                    _fail(path, "source timing coverage differs from sample count")
                end, previous = Fraction(0), 0
                for sample, duration_text, error_text in zip(
                        clip["samples"], source["duration_fractions"], source["duration_errors_ticks"]):
                    if max(len(duration_text), len(error_text)) > 256:
                        _fail(path, "source rational time exceeds the precision limit")
                    try:
                        duration, error = Fraction(duration_text), Fraction(error_text)
                    except (ValueError, ZeroDivisionError) as exc:
                        _fail(path, f"invalid source rational time: {exc}")
                    if duration <= 0:
                        _fail(path, "source duration must be positive")
                    exact = duration * clip["timebase"]
                    end += exact
                    if source["timing_policy"] == "exact":
                        if exact.denominator != 1:
                            _fail(path, "source duration is not exactly representable")
                        boundary = int(end)
                    else:
                        boundary = (2 * end.numerator + end.denominator) // (2 * end.denominator)
                    if sample["duration_ticks"] != boundary - previous or error != sample["duration_ticks"] - exact:
                        _fail(path, "source timing provenance differs from sample ticks")
                    previous = boundary
    elif kind == "parts-pack":
        targets = [json.dumps([b["rig"], b["motion"], b["view"]], sort_keys=True) for b in document["bindings"]]
        if len(set(targets)) != len(targets):
            _fail(path, "duplicate rig/motion/view binding")
        for binding in document["bindings"]:
            body = document["role"] == "body"
            if body and (binding["occupies"] or binding["requires"] or binding["excludes"]):
                _fail(path, "body bindings cannot occupy customization slots")
            if not body and not binding["occupies"]:
                _fail(path, "binding must declare its occupied slots")
            if document["body_mode"] == "flat" and (not body or len(binding["pieces"]) != 1):
                _fail(path, "flat body requires exactly one body piece")
            for piece in binding["pieces"]:
                if body and (piece["slot"] is not None or not piece["provides"] or piece["replaces"]):
                    _fail(path, "body pieces need null slots, provided nodes and no replacements")
                if not body and piece["slot"] not in binding["occupies"]:
                    _fail(path, "piece slot is outside binding occupies")
                _members(piece["replaces"], piece["provides"], f"{path}.{piece['id']}.provides")
                images = {(p["image"]["file"], p["image"]["sha256"])
                          for samples in piece["sample_map"].values() for p in samples.values()}
                if piece["mode"] == "held" and len(images) != 1:
                    _fail(path, "held piece must explicitly reuse exactly one image")
    elif kind == "parts-catalog":
        _parents(document["groups"], f"{path}.groups")
        groups = {g["id"] for g in document["groups"]}
        slots = {s["id"] for s in document["slots"]}
        for slot in document["slots"]:
            _members([slot["group"]], groups, f"{path}.group")
            if any(ref not in slot["options"] for ref in _selected_refs(slot["default"])):
                _fail(path, f"slot {slot['id']} default is not an option")
        for preset in document["presets"]:
            _members(preset["assignments"], slots, f"{path}.presets.{preset['id']}")


def seal(value: dict) -> dict:
    """Validate authoring data, fill defaults, and compute its content digest.

    A supplied digest is verified, never silently replaced; callers authoring a
    new revision must explicitly remove the old digest first.
    """
    if not isinstance(value, dict):
        _fail("document", "expected an object")
    if "digest" in value:
        return parse(value)
    kind = value.get("kind")
    if not isinstance(kind, str) or kind not in _SCHEMAS:
        _fail("document.kind", f"unknown kind {kind!r}")
    normalized = _SCHEMAS[kind](value, "document")
    if normalized["schema_version"] != 1:
        _fail("document.schema_version", "only schema_version 1 is supported")
    _local(normalized)
    normalized["digest"] = content_digest(normalized)
    return normalized


def parse(value: dict) -> dict:
    if not isinstance(value, dict) or "digest" not in value:
        _fail("document.digest", "a sealed document requires a digest")
    digest = _hash(value["digest"], "document.digest")
    normalized = seal({key: item for key, item in value.items() if key != "digest"})
    if normalized["digest"] != digest:
        _fail("document.digest", "content differs from the declared digest")
    return normalized


def reference(document: dict) -> dict:
    document = parse(document)
    return {"id": document["id"], "digest": document["digest"]}


def _get(registry, ref, kind, path):
    document = registry.get(ref["id"])
    if document is None:
        _fail(path, f"missing document {ref['id']}")
    if document["digest"] != ref["digest"]:
        _fail(path, f"digest mismatch for {ref['id']}")
    if kind is not None and document["kind"] != kind:
        _fail(path, f"expected {kind}, got {document['kind']}")
    return document


def resolve_selections(appearance: dict, catalog: dict) -> dict:
    """Resolve flat presets and explicit overrides; never alter caller data."""
    selected = {slot["id"]: deepcopy(slot["default"]) for slot in catalog["slots"]}
    presets = {p["id"]: p for p in catalog["presets"]}
    for name in appearance["presets"]:
        if name not in presets:
            _fail(appearance["id"], f"unknown preset {name}")
        selected.update(deepcopy(presets[name]["assignments"]))
    _members(appearance["selections"], selected, f"{appearance['id']}.selections")
    selected.update(deepcopy(appearance["selections"]))
    return selected


def _check_pack(document, registry):
    for binding in document["bindings"]:
        path = f"{document['id']}.{binding['id']}"
        rig = _get(registry, binding["rig"], "parts-rig", path)
        motion = _get(registry, binding["motion"], "parts-motion", path)
        if motion["rig"] != binding["rig"] or binding["view"] != rig["view"]:
            _fail(path, "binding rig/motion/view mismatch")
        topology = _get(registry, rig["topology"], "parts-topology", path)
        nodes = {n["id"] for n in topology["nodes"]}
        sockets = {s["id"] for s in topology["sockets"]}
        slots = {s["id"]: s for s in topology["slots"]}
        _members([*binding["occupies"], *binding["requires"], *binding["excludes"]], slots, path)
        clips = {c["id"]: c for c in motion["clips"]}
        for piece in binding["pieces"]:
            _members([piece["node"], *piece["provides"], *piece["replaces"]], nodes, path)
            if document["role"] == "body":
                if piece["node"] not in piece["provides"]:
                    _fail(path, "body piece must provide its primary node")
                if document["body_mode"] == "flat":
                    root = next(n["id"] for n in topology["nodes"] if n["parent"] is None)
                    if piece["node"] != root or piece["provides"] != [root]:
                        _fail(path, "flat body can only provide the root interface")
            slot = slots[piece["slot"]] if piece["slot"] is not None else {"nodes": [], "sockets": []}
            if slot["nodes"]:
                _members([piece["node"]], slot["nodes"], f"{path}.{piece['id']}.slot_node")
            _members(piece["sample_map"], clips, path)
            for clip_id, mapping in piece["sample_map"].items():
                samples = {s["id"]: s for s in clips[clip_id]["samples"]}
                _members(mapping, samples, path)
                for sample_id, placement in mapping.items():
                    _members([placement["target_socket"]], sockets, path)
                    if slot["sockets"]:
                        _members([placement["target_socket"]], slot["sockets"], f"{path}.{piece['id']}.slot_socket")
                    if placement["visible"]:
                        _members([placement["target_socket"]], samples[sample_id]["sockets"],
                                 f"{path}.{piece['id']}.{sample_id}.target_socket")
                    if placement["mask"] is not None:
                        _members([placement["mask"]["target_node"]], nodes, path)


def _check_appearance(document, registry, complete):
    path = document["id"]
    rig = _get(registry, document["rig"], "parts-rig", path)
    motion = _get(registry, document["motion"], "parts-motion", path)
    catalog = _get(registry, document["catalog"], "parts-catalog", path)
    if motion["rig"] != document["rig"] or catalog["rig"] != document["rig"]:
        _fail(path, "appearance rig does not match motion/catalog rig")
    selected = resolve_selections(document, catalog)
    _members(document["palette"], {c["id"] for c in catalog["palette_channels"]}, f"{path}.palette")
    topology = _get(registry, rig["topology"], "parts-topology", path)
    topology_slots = {s["id"]: s for s in topology["slots"]}
    catalog_slots = {s["id"]: s for s in catalog["slots"]}
    selected_bindings = {}
    occupied = {}
    covered_slots = set()
    for ref in document["body"]:
        pack = _get(registry, ref, "parts-pack", path)
        if pack["role"] != "body":
            _fail(path, "appearance body reference is not a body pack")
        bindings = [b for b in pack["bindings"] if b["rig"] == document["rig"]
                    and b["motion"] == document["motion"] and b["view"] == rig["view"]]
        if not bindings:
            _fail(path, f"no body binding for {pack['id']}")
        selected_bindings[pack["id"]] = bindings[0]
    for slot in catalog["slots"]:
        refs = _selected_refs(selected[slot["id"]])
        if len(refs) > topology_slots[slot["id"]]["max_count"]:
            _fail(path, f"selection exceeds max_count at {slot['id']}")
        if not refs:
            continue
        for ref in refs:
            if ref not in slot["options"]:
                _fail(path, f"slot {slot['id']} selection is not an option")
            pack = _get(registry, ref, "parts-pack", path)
            if pack["role"] != "part":
                _fail(path, "body pack cannot be selected as a customization option")
            bindings = [b for b in pack["bindings"] if b["rig"] == document["rig"]
                        and b["motion"] == document["motion"] and b["view"] == rig["view"]]
            if not bindings:
                _fail(path, f"no rig/motion/view binding for {pack['id']}")
            binding = bindings[0]
            if slot["id"] not in binding["occupies"]:
                _fail(path, f"{pack['id']} does not occupy {slot['id']}")
            if not any(piece["slot"] == slot["id"] for piece in binding["pieces"]):
                _fail(path, f"{pack['id']} has no piece for {slot['id']}")
            for name in binding["occupies"]:
                occupied.setdefault(name, set()).add(pack["id"])
                if len(occupied[name]) > topology_slots[name]["max_count"]:
                    _fail(path, f"conflicting occupied slot {name}")
            selected_bindings[pack["id"]] = binding
            covered_slots.update(piece["slot"] for piece in binding["pieces"])
    for name, binding in selected_bindings.items():
        _members(binding["requires"], covered_slots, f"{path}.{name}.requires")
        if set(binding["excludes"]) & set(occupied):
            _fail(path, f"{name} excludes a selected slot")
        for piece in binding["pieces"]:
            interface = catalog_slots.get(piece["slot"], {}).get("join_interface")
            if interface is not None and piece["join_interface"] != interface:
                _fail(path, f"join interface mismatch at {piece['slot']}")
    if complete:
        body_nodes = {node for ref in document["body"]
                      for piece in selected_bindings[ref["id"]]["pieces"] for node in piece["provides"]}
        for slot in topology_slots.values():
            if not slot["optional"] and slot["id"] not in covered_slots:
                fallback = slot["empty_policy"] == "body-default" and slot["nodes"] and set(slot["nodes"]) <= body_nodes
                if not fallback:
                    _fail(path, f"required slot {slot['id']} has no selection or body default")
        clips = [c for c in motion["clips"] if c["state"] in document["states"]]
        _members(document["states"], {c["state"] for c in clips}, f"{path}.states")
        for binding in selected_bindings.values():
            for piece in binding["pieces"]:
                for clip in clips:
                    mapping = piece["sample_map"].get(clip["id"], {})
                    for sample in clip["samples"]:
                        if sample["id"] not in mapping:
                            _fail(path, f"missing coverage: {piece['id']}/{clip['id']}/{sample['id']}")


def validate_bundle(documents: Iterable[dict], *, complete=False) -> dict[str, dict]:
    """Check a closed declaration graph; complete checks appearance coverage.

    This does not verify file bytes, fitting, body segmentation, drawing order,
    review signatures or release readiness. Those require later pipeline stages.
    """
    registry = {}
    for raw in documents:
        doc = parse(raw)
        if doc["id"] in registry:
            _fail("bundle", f"duplicate document ID {doc['id']}")
        registry[doc["id"]] = doc
    # Validate providers before consumers regardless of index/file order. A bad
    # catalog or binding must report its contract error, not a consumer KeyError.
    ordered = (doc for kind in KINDS for doc in registry.values() if doc["kind"] == kind)
    for doc in ordered:
        kind, path = doc["kind"], doc["id"]
        if kind == "parts-rig":
            _get(registry, doc["topology"], "parts-topology", path)
        elif kind == "parts-motion":
            rig = _get(registry, doc["rig"], "parts-rig", path)
            topology = _get(registry, rig["topology"], "parts-topology", path)
            known = {s["id"] for s in topology["sockets"]}
            required = {s["id"] for s in topology["sockets"] if s["required"]}
            for clip in doc["clips"]:
                if clip["view"] != rig["view"]:
                    _fail(path, "clip view differs from rig view")
                for sample in clip["samples"]:
                    _members(sample["sockets"], known, f"{path}.{sample['id']}.socket")
                    _members(required, sample["sockets"], f"{path}.{sample['id']}.required_socket")
                    for location in sample["sockets"].values():
                        x, y = location["position"]
                        if location["visible"] and not (0 <= x <= rig["canvas"]["width"]
                                                       and 0 <= y <= rig["canvas"]["height"]):
                            _fail(path, "visible socket is outside rig canvas")
        elif kind == "parts-pack":
            _check_pack(doc, registry)
        elif kind == "parts-catalog":
            rig = _get(registry, doc["rig"], "parts-rig", path)
            topology = _get(registry, doc["topology"], "parts-topology", path)
            if rig["topology"] != doc["topology"]:
                _fail(path, "catalog topology differs from rig topology")
            topology_slots = {s["id"]: s for s in topology["slots"]}
            catalog_slots = {s["id"]: s for s in doc["slots"]}
            _members(catalog_slots, topology_slots, path)
            for slot in doc["slots"]:
                if len(_selected_refs(slot["default"])) > topology_slots[slot["id"]]["max_count"]:
                    _fail(path, f"default exceeds max_count at {slot['id']}")
                for ref in slot["options"]:
                    pack = _get(registry, ref, "parts-pack", path)
                    if pack["role"] != "part":
                        _fail(path, "catalog option must be a customization part")
                    if not any(b["rig"] == doc["rig"] and slot["id"] in b["occupies"]
                               and any(p["slot"] == slot["id"] for p in b["pieces"])
                               for b in pack["bindings"]):
                        _fail(path, f"option {pack['id']} has no matching rig/slot binding")
            for preset in doc["presets"]:
                for slot, value in preset["assignments"].items():
                    if len(_selected_refs(value)) > topology_slots[slot]["max_count"]:
                        _fail(path, f"preset exceeds max_count at {slot}")
                    if any(ref not in catalog_slots[slot]["options"] for ref in _selected_refs(value)):
                        _fail(path, f"preset option is unavailable at {slot}")
        elif kind == "parts-appearance":
            _check_appearance(doc, registry, complete)
        elif kind == "parts-review" and doc["evidence"] is not None:
            evidence = doc["evidence"]
            _get(registry, evidence["appearance"], "parts-appearance", path)
            subject = _get(registry, evidence["subject"], "parts-appearance" if doc["scope"] == "appearance" else "parts-pack", path)
            for ref in evidence["inputs"]:
                _get(registry, ref, None, path)
            digest = subject["digest"]
            if doc["scope"] == "binding":
                matches = [b for b in subject["bindings"] if b["id"] == evidence["binding_id"]]
                if len(matches) != 1:
                    _fail(path, "review binding is missing")
                digest = content_digest(matches[0])
            elif evidence["binding_id"] is not None:
                _fail(path, "binding ID only applies to binding reviews")
            if digest != doc["subject_digest"]:
                _fail(path, "review subject digest differs from evidence")
        elif kind == "parts-build-lock":
            _get(registry, doc["appearance"], "parts-appearance", path)
            for ref in doc["inputs"]:
                _get(registry, ref, None, path)
            for ref in doc["reviews"]:
                _get(registry, ref, "parts-review", path)
    return registry


def legacy_topology(profile: str, *, landmarks: Iterable[str] = ()) -> dict:
    """Explicit legacy vocabulary adapter; never infers anatomy or image pieces."""
    from sprite_gen.compose.layers import LANDMARK_NAME, PROFILES
    if not isinstance(profile, str) or profile not in PROFILES:
        _fail("legacy", f"unknown legacy profile {profile!r}")
    if isinstance(landmarks, (str, bytes)):
        _fail("legacy", "landmarks must be a collection of names")
    extra = list(landmarks)
    if any(not isinstance(name, str) or not LANDMARK_NAME.fullmatch(name) for name in extra):
        _fail("legacy", "invalid legacy landmark name")
    names = sorted(set(PROFILES[profile]["required"]) | set(PROFILES[profile]["reserved"]) | set(extra))
    return seal({
        "kind": "parts-topology", "schema_version": 1, "id": f"legacy.{profile}",
        "nodes": [{"id": "root"}],
        "sockets": [{"id": name, "node": "root", "required": name in PROFILES[profile]["required"]}
                    for name in names],
        "slots": [],
    })
