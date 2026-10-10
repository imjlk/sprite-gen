# SPDX-License-Identifier: Apache-2.0
"""Part production: per-pose candidates, checks, part approval and the redo loop.

A part is produced against the frozen motion's poses. Every drawing that comes back
(a page an image model or an artist drew) is kept as an immutable candidate: the drawn
cell, what it was drawn over, the region taken as the part (its mask), the part raster,
how it was cut and from what. The production records which candidate each pose uses
and the decision on it.

    parts-part-init    a production from a config: a part (raster or held) or the body
    parts-part-config  change prompt, sheet, cut, wearing or pieces for drawings still to come
    parts-part-sheet   pose sheets for the poses still to draw (missing or rejected)
    parts-part-draw    optionally ask an image provider to draw those pages
    parts-part-slice   drawn pages -> candidates, selected for their poses
    parts-part-mask    replace a pose's region with an explicit mask
    parts-part-adopt   existing pack drawings -> candidates
    parts-part-check   per-pose checks of the selected drawings
    parts-part-build   selected drawings -> pieces -> imported pack (or segmented body)
    parts-part-review  render it on the body alone; approve or reject poses
    parts-part-status  what is drawn, checked, approved, and what to do next

A part has a main track and optional variants: drawings shown under a cover rule (the
hair as it looks under a hat), drawn over the parts that cover them. A held part is one
drawing made on a reference pose and hung on its socket in every pose. A body production
draws label maps per pose and cuts the motion's own rasters into node pieces.

A part is approved on the body alone, pose by pose, so an approved shoe is reused in any
outfit; combinations are judged separately by appearance reviews. Nothing here releases
anything.
"""
from __future__ import annotations

from io import BytesIO
from copy import deepcopy
from fractions import Fraction
from pathlib import Path
import subprocess
import sys

from PIL import Image, ImageDraw

from sprite_gen._deps import np
from sprite_gen.compose import compose_parts, parts_resolve
from sprite_gen.curate import parts_motion, parts_review
from sprite_gen.frames import parts_import, parts_pose_sheet as sheets, parts_reference, parts_quality, parts_segments, pixel_grid
from sprite_gen.spec import parts_assets as assets, parts_contract as contract, parts_workspace as workspace
from sprite_gen.spec.runio import atomic_write_text, publish_guard

POLICY = "parts.production.v1"
MAIN = "main"
_SHEET = contract._record({"crop": contract._nullable(contract._rectangle), "scale": contract._positive,
                           "gutter": contract._integer,
                           "page_size": contract._nullable(contract._positive), "aspect": contract._nullable(contract._string),
                           "columns": contract._nullable(contract._positive)},
                          {"crop": None, "scale": 4, "gutter": 8, "page_size": 12, "aspect": "1:1", "columns": None})
_CUT = contract._record({"method": contract._enum(*sheets.METHODS), "body_threshold": contract._nullable(contract._integer),
                         "tolerance": contract._integer, "near_body": contract._nullable(contract._integer),
                         "min_island": contract._integer, "outline": contract._boolean, "fill_holes": contract._integer,
                         "close_body": contract._integer, "region": contract._nullable(contract._rectangle),
                         "palette": contract._nullable(contract._relative_file),
                         "keep_palette": contract._nullable(contract._integer), "prune": contract._integer,
                         "rim": contract._integer, "inset": contract._integer, "specks": contract._integer,
                         "rescue": contract._nullable(contract._integer),
                         "against": contract._list(contract._relative_file),
                         "exclude": contract._list(contract._rectangle), "largest": contract._nullable(contract._integer)},
                        {"method": "difference", "body_threshold": 40, "tolerance": 80, "near_body": 2, "min_island": 12,
                         "outline": True, "fill_holes": 6, "close_body": 5, "region": None, "palette": None,
                         "keep_palette": None, "prune": 0, "rim": 0, "inset": 0, "specks": 0, "rescue": None, "against": [], "exclude": [], "largest": None})
_PIECE = contract._record({"id": contract._id, "node": contract._id, "segments": contract._list(contract._string, nonempty=True), "replaces": contract._ids}, optional=("replaces",))
_PROMPT = contract._record({"text": contract._string, "refs": contract._list(contract._relative_file)}, {"refs": []})
_DRAW = contract._record({"provider": contract._id, "model": contract._nullable(contract._string)},
                         {"provider": "codex", "model": None})
_FOLLOW = contract._record({"socket": contract._id})
_TRAIL = contract._record({"socket": contract._id, "gain": contract._string, "max_px": contract._positive},
                          {"gain": "1/2", "max_px": 1})
_VARIANT = contract._record({"id": contract._id, "node": contract._nullable(contract._id), "cover_rule": contract._cover_rule,
                             "wearing": contract._ids, "prompt": contract._nullable(_PROMPT), "cut": contract._nullable(_CUT)},
                            {"node": None, "wearing": [], "prompt": None, "cut": None})
_SEGMENT = contract._record({"color": contract._string, "provides": contract._ids}, {"provides": []})
_BODY = contract._record({"colors": contract._mapping(_SEGMENT), "near_lighter": contract._list(contract._list(contract._id)),
                          "tolerance": contract._integer}, {"near_lighter": [], "tolerance": 60})
_POSE_CHECK = contract._record({"required": contract._nullable(contract._relative_file),
                               "protected": contract._nullable(contract._relative_file),
                               "allowed": contract._nullable(contract._relative_file),
                               "occluded": contract._nullable(contract._relative_file)},
                              {"required": None, "protected": None, "allowed": None, "occluded": None})
_STABLE = contract._record({"id": contract._id, "socket": contract._id, "bounds": contract._rectangle,
                           "reference_pose": contract._nullable(contract._id), "max_alpha_pixels": contract._integer,
                           "max_rgb_pixels": contract._integer, "rgb_threshold": contract._integer},
                          {"reference_pose": None, "max_alpha_pixels": 0, "max_rgb_pixels": 0, "rgb_threshold": 24})
_QA = contract._record({"stable_regions": contract._records(_STABLE)}, {"stable_regions": []})
_CONFIG = contract._record({
    "kind": contract._enum("parts-production-config"), "schema_version": contract._positive,
    "part_id": contract._id, "design_id": contract._id, "role": contract._enum("part", "body"),
    "slot": contract._nullable(contract._id), "node": contract._nullable(contract._id), "socket": contract._nullable(contract._id),
    "mode": contract._enum("raster", "held"), "states": contract._ids, "reference_pose": contract._nullable(contract._id),
    "sheet": _SHEET, "cut": _CUT, "wearing": contract._ids, "pieces": contract._list(_PIECE),
    "by_component": contract._boolean, "segments": contract._nullable(contract._relative_file),
    "prompt": contract._nullable(_PROMPT), "draw": _DRAW, "follow": contract._nullable(_FOLLOW),
    "trail": contract._nullable(_TRAIL), "cover_rule": contract._nullable(contract._cover_rule), "covers": contract._ids,
    "variants": contract._list(_VARIANT), "body": contract._nullable(_BODY),
    "pose_checks": contract._mapping(_POSE_CHECK),
    "qa": _QA,
    "visibility": contract._enum("zmap", "mask"),
}, {"role": "part", "slot": None, "node": None, "socket": None, "mode": "raster", "draw": {}, "states": [],
    "reference_pose": None, "sheet": {}, "cut": {}, "wearing": [], "pieces": [], "by_component": False, "segments": None,
    "prompt": None, "follow": None, "trail": None, "cover_rule": None, "covers": [], "variants": [], "body": None,
    "pose_checks": {}, "qa": {}, "visibility": "zmap"})
DECISIONS = ("unverified", "approved", "rejected")
FIXED = ("part_id", "design_id", "role", "slot", "node", "socket", "mode")
# Check bounds, judged on the SD demo (docs/parts/10-production.md).
LIMITS = {"area_jump": 0.45, "dropped_share": 0.04, "specks": 0, "holes": 0, "painted_share": 0.5,
          "repainted_share": 1.0, "worn_drift": 0.1}


def _folder(root, part_id):
    return root / "productions" / contract._id(part_id, "part")


def _load(root, part_id):
    path = _folder(root, part_id) / "production.json"
    if not path.is_file():
        raise contract.PartsContractError(f"no production for {part_id}; run parts-part-init")
    production = workspace.decode_json(path.read_bytes())
    # Fields added to the config schema later read with their defaults.
    production["config"] = _CONFIG(production["config"], "production.config")
    if "tracks" not in production:  # a production from before variants: its poses are the main track
        production["tracks"] = {MAIN: {"poses": production.pop("poses", {}), "batches": production.pop("batches", [])}}
    for variant in production["config"]["variants"]:
        production["tracks"].setdefault(variant["id"], {"poses": {}, "batches": []})
    if production.get("built") and "selection" in production["built"] and MAIN not in production["built"]["selection"]:
        production["built"]["selection"] = {MAIN: production["built"]["selection"]}
    return production


def _save(root, production):
    folder = _folder(root, production["config"]["part_id"])
    folder.mkdir(parents=True, exist_ok=True)
    with publish_guard(root):
        atomic_write_text(folder / "production.json", workspace.json_bytes(production).decode("utf-8"))


def _track(production, track):
    name = track or MAIN
    if name not in production["tracks"]:
        raise contract.PartsContractError(f"{production['config']['part_id']} has no variant {name}")
    return production["tracks"][name]


def _settings(config, track):
    """Node, cover rule, wearing, prompt and cut of one track (a variant falls back to the main ones)."""
    if (track or MAIN) == MAIN:
        return {"node": config["node"], "cover_rule": config["cover_rule"], "wearing": config["wearing"],
                "prompt": config["prompt"], "cut": config["cut"]}
    variant = next(v for v in config["variants"] if v["id"] == track)
    return {"node": variant["node"] or config["node"], "cover_rule": variant["cover_rule"], "wearing": variant["wearing"],
            "prompt": variant["prompt"] or config["prompt"], "cut": variant["cut"] or config["cut"]}


def _states(production, index):
    return production["config"]["states"] or index["states"]


def _poses(motion, states):
    """Pose IDs in clip order for the requested states, and the first sample showing each."""
    order, first = [], {}
    for clip in motion["clips"]:
        if clip["state"] not in states:
            continue
        for sample in clip["samples"]:
            if sample["pose_id"] is None:
                raise contract.PartsContractError("production works by pose: give every sample a pose_id (parts-pose-assign)")
            if sample["pose_id"] not in first:
                first[sample["pose_id"]] = (clip["id"], sample)
                order.append(sample["pose_id"])
    return order, first


def _reference(config, order):
    reference = config["reference_pose"] or order[0]
    contract._members([reference], order, "reference_pose")
    return reference


def _redo(row):
    """Missing, rejected, or failing a check with an error nobody approved anyway."""
    return (row.get("selected") is None or row.get("decision") == "rejected"
            or row.get("rework", {}).get("status") in ("requested", "failed")
            or ((row.get("check") or {}).get("status") == "error" and row.get("decision") != "approved"))


def _redo_notes(production, rows, wanted, order):
    """What went wrong with the drawings being replaced, for the model to avoid this time."""
    looked = order if production["config"]["mode"] == "held" else wanted  # a held drawing is judged on every pose
    notes, failures, misaligned = [], [], False
    for pose in looked:
        row = rows["poses"].get(pose, {})
        if row.get("rework", {}).get("note") and row["rework"]["note"] not in notes:
            notes.append(row["rework"]["note"])
        if row.get("decision") == "rejected" and row.get("note") and row["note"] not in notes:
            notes.append(row["note"])
        misaligned |= pose in wanted and any(f.startswith("alignment") for f in (row.get("check") or {}).get("findings", []))
        check = row.get("check") or {}
        if pose in wanted and check.get("status") == "error":
            failures.append(pose + ": " + "; ".join(check.get("findings", [])))
    text = ""
    if misaligned:
        text += (" The previous drawing moved, resized or left out the figure: draw the whole figure exactly where it is"
                 " and exactly as big as it is in image 1.")
    if notes:
        text += " The previous drawing was sent back at review: " + "; ".join(notes) + ". Apply these requested visual changes to the new part."
    if failures:
        text += " The previous part failed these checks: " + "; ".join(failures) + ". " \
                "Cover required regions, leave protected regions visible, and stay within the allowed silhouette. " \
                "Match the reference pose's position and size."
    return text


def _todo(production, track, order):
    rows = _track(production, track)["poses"]
    if production["config"]["mode"] == "held":
        # One drawing for every pose: redraw it when it is missing or any pose sends it back.
        reference = _reference(production["config"], order)
        if _redo(rows.get(reference, {})) or any(_redo(rows.get(p, {})) for p in order):
            return [reference]
        return []
    return [p for p in order if _redo(rows.get(p, {}))]


def _validate_qa(value, index, registry):
    if not value["qa"]["stable_regions"]:
        return
    motion = registry[index["motion"]["id"]]
    order, _ = _poses(motion, value["states"] or index["states"])
    rig = registry[index["rig"]["id"]]
    topology = registry[rig["topology"]["id"]]
    for region in value["qa"]["stable_regions"]:
        contract._members([region["socket"]], {s["id"] for s in topology["sockets"]}, "stable region socket")
        if region["reference_pose"]:
            contract._members([region["reference_pose"]], order, "stable region reference pose")
        if min(region["max_alpha_pixels"], region["max_rgb_pixels"]) < 0 or not 0 <= region["rgb_threshold"] <= 255:
            raise contract.PartsContractError("stable region limits must be nonnegative and RGB threshold 0..255")
        a, b, c, d = region["bounds"]
        if c-a > rig["canvas"]["width"] or d-b > rig["canvas"]["height"]:
            raise contract.PartsContractError("stable region must not exceed the rig canvas")


def init(assembly, config):
    root, index, registry, _ = parts_motion._active(assembly)
    path = Path(config).absolute()
    value = _CONFIG(workspace.read_json(path.parent, path.name), "config")
    topology = registry[registry[index["rig"]["id"]]["topology"]["id"]]
    contract._members([node for piece in value["pieces"] for node in piece.get("replaces", [])], [node["id"] for node in topology["nodes"]], "piece replacement nodes")
    if value["schema_version"] != 1:
        raise contract.PartsContractError("only schema_version 1 is supported")
    _validate_qa(value, index, registry)
    rig = registry[index["rig"]["id"]]
    topology = registry[rig["topology"]["id"]]
    if value["role"] == "body":
        if value["body"] is None:
            raise contract.PartsContractError("a body production needs config.body: label colors per node")
        root_node = next(n["id"] for n in topology["nodes"] if n["parent"] is None)
        root_socket = next((s["id"] for s in topology["sockets"] if s["node"] == root_node), None)
        value.update(slot=None, node=value["node"] or root_node, socket=value["socket"] or root_socket, mode="raster")
        contract._members([n for spec in value["body"]["colors"].values() for n in spec["provides"]] + list(value["body"]["colors"]),
                          {n["id"] for n in topology["nodes"]}, "config.body.colors")
    else:
        slots = {s["id"]: s for s in topology["slots"]}
        if value["slot"] is None or value["node"] is None or value["socket"] is None:
            raise contract.PartsContractError("a part production needs slot, node and socket")
        contract._members([value["slot"]], slots, "config.slot")
        slot = slots[value["slot"]]
        nodes = [value["node"], *(p["node"] for p in value["pieces"]), *(v["node"] for v in value["variants"] if v["node"])]
        if slot["nodes"]:
            contract._members(nodes, slot["nodes"], "config.node")
        if slot["sockets"]:
            contract._members([value["socket"]], slot["sockets"], "config.socket")
        if value["pieces"] and value["segments"] is None:
            raise contract.PartsContractError("pieces are cut by body segments: give config.segments (a body production)")
    if _folder(root, value["part_id"]).joinpath("production.json").exists():
        raise contract.PartsContractError(f"production {value['part_id']} exists; change it with parts-part-config")
    production = {"kind": "parts-production", "schema_version": 1, "policy": POLICY, "config": value,
                  "tracks": {MAIN: {"poses": {}, "batches": []}, **{v["id"]: {"poses": {}, "batches": []} for v in value["variants"]}},
                  "built": None}
    _save(root, production)
    return {"production": str(_folder(root, value["part_id"]) / "production.json"), "part": value["part_id"],
            "tracks": list(production["tracks"]), "status": "draft", "release_ready": False}


def configure(assembly, part, config):
    """Change how future drawings are made and cut (prompt, sheet, cut, wearing, pieces, variants).

    Existing candidates keep the settings they were made with; the part's identity (ID,
    role, slot, node, socket, mode) is fixed once it has drawings. Every change is kept.
    """
    root, index, registry, _ = parts_motion._active(assembly)
    production = _load(root, part)
    path = Path(config).absolute()
    value = _CONFIG(workspace.read_json(path.parent, path.name), "config")
    _validate_qa(value, index, registry)
    topology = registry[registry[index["rig"]["id"]]["topology"]["id"]]
    contract._members([node for piece in value["pieces"] for node in piece.get("replaces", [])], [node["id"] for node in topology["nodes"]], "piece replacement nodes")
    if value["role"] == "body":
        value.update(slot=None, node=value["node"] or production["config"]["node"],
                     socket=value["socket"] or production["config"]["socket"], mode="raster")
    changed = [k for k in FIXED if value[k] != production["config"][k]]
    drawn = any(row["candidates"] for track in production["tracks"].values() for row in track["poses"].values())
    if changed and drawn:
        raise contract.PartsContractError(f"{part}: {changed} are fixed once poses are drawn; start a new part ID")
    if value["pieces"] and value["segments"] is None:
        raise contract.PartsContractError("pieces are cut by body segments: give config.segments")
    previous = production["config"]
    if any(previous[k] != value[k] for k in ("qa", "visibility", "pieces", "segments")):
        for track in production["tracks"].values():
            for row in track["poses"].values():
                row.update(check=None, decision="unverified", reviewer=None, note=None)
    production.setdefault("history", []).append({"config": previous, "replaced_by": contract.content_digest(value)})
    production["config"] = value
    for variant in value["variants"]:
        production["tracks"].setdefault(variant["id"], {"poses": {}, "batches": []})
    _save(root, production)
    return {"part": part, "changed": sorted(k for k in value if value[k] != previous.get(k)), "release_ready": False}


def sheet(assembly, part, *, poses=None, track=None):
    """Pose sheets of the poses still to draw: never drawn, or rejected at review."""
    root, index, registry, _ = parts_motion._active(assembly)
    production = _load(root, part)
    config = production["config"]
    rows = _track(production, track)
    settings = _settings(config, track)
    motion = registry[index["motion"]["id"]]
    order, _ = _poses(motion, _states(production, index))
    wanted = list(poses) if poses else _todo(production, track, order)
    if not wanted:
        raise contract.PartsContractError(f"{part}: every pose has a selected drawing that is not rejected")
    contract._members(wanted, order, "poses")
    # A redo is a new batch: what it replaces, the worn parts as they are now and the prompt are in its ID.
    batch = "b" + contract.content_digest({
        "motion": index["motion"], "poses": wanted, "track": track or MAIN,
        "wearing": [{"id": w, "digest": registry.get(w, {}).get("digest")} for w in settings["wearing"]],
        "replaces": [rows["poses"].get(p, {}).get("selected") for p in wanted],
        "prompt": settings["prompt"], "sheet": config["sheet"], "output_method": settings["cut"]["method"],
        "redo_notes": _redo_notes(production, rows, wanted, order),
        "inputs": {file: assets.sha256(assets.read_file(root, file)) for file in
                   [*((settings["prompt"] or {}).get("refs", [])),
                    *(file for pose in wanted for file in config["pose_checks"].get(pose, {}).values() if file)]}})[:12]
    folder = _folder(root, part) / "batches" / batch
    look = config["sheet"]
    crop = ",".join(map(str, look["crop"])) if look["crop"] else None
    made = sheets.build_sheet(assembly, folder, by_pose=True, poses=wanted, crop=crop, scale=look["scale"],
                              gutter=look["gutter"],
                              page_size=look["page_size"], aspect=look["aspect"], columns=look["columns"],
                              part=part, wearing=settings["wearing"] or None)
    prompt = None
    if settings["prompt"] is not None:
        prompt = folder / "prompt.txt"
        output = ("Draw ONLY the requested part in every cell; the body is a placement reference, not output. "
                  "Do not draw skin, face, hands, legs or other clothing. Leave all other pixels on the sheet's key background. "
                  if settings["cut"]["method"] == "part-only" else
                  "Keep the whole figure (head, body, limbs and feet) still drawn in every cell. ")
        prompt.write_text(settings["prompt"]["text"] + f" Keep this {made['size'][0]}x{made['size'][1]} image and its grid "
                          "exactly. " + output +
                          "Keep every figure exactly in its own cell: do not move, mirror, resize, rearrange or re-space figures."
                          + _redo_notes(production, rows, wanted, order))
    pages = [{"guide": Path(p["guide"]).relative_to(root).as_posix(), "sheet": Path(p["sheet"]).relative_to(root).as_posix(),
              "art": (Path(p["sheet"]).parent / "drawn.png").relative_to(root).as_posix()} for p in made["pages"]]
    reference = parts_reference.freeze(root, _folder(root, part), index, registry, config, settings, pages, prompt)
    inputs, reference_base = parts_reference.read(root, reference)
    previous = next((b for b in rows["batches"] if b["id"] == batch), {})
    entry = {"id": batch, "motion": index["motion"], "poses": wanted, "pages": pages,
             "prompt": None if prompt is None else prompt.relative_to(root).as_posix(),
             "reference": reference, "attempts": previous.get("attempts", []),
             "refs": [(reference_base / item["image"]["file"]).as_posix() for item in inputs["design"]]}
    rows["batches"] = [b for b in rows["batches"] if b["id"] != batch] + [entry]
    _save(root, production)
    return {"part": part, "track": track or MAIN, "batch": batch, "poses": wanted, "pages": pages,
            "prompt": entry["prompt"], "refs": entry["refs"], "reference": reference,
            "next": "draw each page to its art path (parts-part-draw, or by hand), then parts-part-slice",
            "status": "draft", "release_ready": False}


def draw(assembly, part, *, batch=None, provider=None, model=None, dry_run=False, track=None):
    """Ask an image provider (`sprite-gen gen`) to draw each page of a batch that has no art yet.

    The provider and model come from the config's `draw` unless given here.
    """
    root, index, _, _ = parts_motion._active(assembly)
    production = _load(root, part)
    settings = production["config"]["draw"]
    provider, model = provider or settings["provider"], model or settings["model"]
    entry = _batch(_track(production, track), batch)
    if entry["prompt"] is None:
        raise contract.PartsContractError(f"{part}: config has no prompt; draw the pages by hand")
    frozen, frozen_base = parts_reference.read(root, entry["reference"]) if entry.get("reference") else (None, None)
    if frozen:
        parts_reference.verify(root, frozen, frozen_base)
    results = []
    for number, page in enumerate(entry["pages"]):
        art = root / page["art"]
        if art.exists():
            results.append({"art": page["art"], "status": "exists"})
            continue
        prompt_file = frozen_base / frozen["prompt"]["file"] if frozen else entry["prompt"]
        sheet_file = frozen_base / frozen["pages"][number]["sheet"]["file"] if frozen else page["sheet"]
        command = [sys.executable, "-m", "sprite_gen.cli", "gen", "--provider", provider, "--prompt-file",
                   str(root / prompt_file), "--out", str(art), "--report", str(art.with_suffix(".report.json")),
                   "--ref", str(root / sheet_file)]
        for ref in entry.get("refs", []):
            command += ["--ref", str(root / ref)]
        if frozen and frozen["pages"][number].get("layers"):
            command += ["--ref", str(root / frozen_base / frozen["pages"][number]["layers"]["file"])]
        if model:
            command += ["--model", model]
        if dry_run:
            results.append({"art": page["art"], "status": "planned", "command": command})
            continue
        done = subprocess.run(command, capture_output=True, text=True)
        attempt = {"art": page["art"], "status": "drawn" if done.returncode == 0 else "failed",
                   "error": done.stderr[-400:] if done.returncode else None, "provider": provider, "model": model}
        entry.setdefault("attempts", []).append(attempt)
        _save(root, production)
        results.append(attempt)
    return {"part": part, "track": track or MAIN, "batch": entry["id"], "pages": results, "release_ready": False}


def _batch(rows, batch):
    if not rows["batches"]:
        raise contract.PartsContractError("no sheet batch yet; run parts-part-sheet")
    if batch is None:
        return rows["batches"][-1]
    found = [b for b in rows["batches"] if b["id"] == batch]
    if not found:
        raise contract.PartsContractError(f"unknown batch {batch}")
    return found[0]


def _palette(root, cut):
    return None if cut["palette"] is None else pixel_grid.read_palette(root / cut["palette"])


def _against(root, cut):
    """The palettes of parts this one must not take pixels from, as one color list."""
    lists = [pixel_grid.read_palette(root / path) for path in cut["against"]]
    return np.concatenate(lists) if lists else None


def _publish_candidate(root, part, record, images):
    payloads = {f"{name}.png": assets.png_bytes(image) for name, image in images.items()}
    record = {**record, "images": {name.removesuffix(".png"): {"file": name, "sha256": assets.sha256(data)}
                                   for name, data in payloads.items()}}
    record["digest"] = contract.content_digest(record)
    payloads["candidate.json"] = workspace.json_bytes(record)
    folder = assets.publish_object(_folder(root, part), "candidates", contract._id(record["pose_id"], "pose"),
                                   record["digest"], payloads)
    return record, folder.relative_to(root).as_posix()


def _select(rows, poses, candidate, *, keep_approved=True):
    for pose in poses:
        row = rows["poses"].setdefault(pose, {"candidates": [], "selected": None, "decision": "unverified",
                                              "note": None, "reviewer": None, "check": None})
        if candidate not in row["candidates"]:
            row["candidates"].append(candidate)
        if not (keep_approved and row["decision"] == "approved"):
            row.update(selected=candidate, decision="unverified", note=None, reviewer=None, check=None)
            if row.get("rework"):
                row["rework"].update(status="candidate-ready", result=candidate)


def _rgb(text):
    text = text.strip().lstrip("#")
    if len(text) != 6:
        raise contract.PartsContractError(f"expected #rrggbb, got {text}")
    return tuple(int(text[i:i+2], 16) for i in (0, 2, 4))


def candidate_record(root, state, pose, candidate, *, track=None):
    """Only immutable candidates belonging to this production/pose/track are exposed."""
    row = _track(state, track)["poses"].get(pose, {})
    if candidate not in row.get("candidates", []):
        raise contract.PartsContractError(f"{pose}: unknown candidate")
    record = workspace.decode_json(assets.read_file(root, candidate + "/candidate.json"))
    if (record["part"] != state["config"]["part_id"] or record["track"] != (track or MAIN) or
            (state["config"]["mode"] != "held" and record["pose_id"] != pose)):
        raise contract.PartsContractError(f"{pose}: candidate belongs to a different part, track or pose")
    if contract.content_digest({k: v for k, v in record.items() if k != "digest"}) != record["digest"]:
        raise contract.PartsContractError(f"{pose}: candidate record changed")
    for ref in record["images"].values():
        if assets.sha256(assets.read_file(root / candidate, ref["file"])) != ref["sha256"]:
            raise contract.PartsContractError(f"{pose}: candidate image changed")
    return record


def select_candidate(assembly, part, pose, candidate, *, track=None):
    """Curate an existing candidate; a changed selection always requires fresh review."""
    root, index, registry, _ = parts_motion._active(assembly)
    state = _load(root, part)
    record = candidate_record(root, state, pose, candidate, track=track)
    if record["motion"] != index["motion"]:
        raise contract.PartsContractError("candidate was drawn for a different motion revision")
    rows = _track(state, track)
    order, _ = _poses(registry[index["motion"]["id"]], _states(state, index))
    contract._members([pose], order, "pose")
    targets = order if state["config"]["mode"] == "held" else [pose]
    changed = any(rows["poses"][p]["selected"] != candidate for p in targets)
    if changed:
        _select(rows, targets, candidate, keep_approved=False)
        _save(root, state)
    return {"part": part, "pose": pose, "candidate": candidate, "changed": changed,
            "poses": targets, "release_ready": False}


def request_rework(assembly, part, poses, note, *, track=None):
    """Explicit art rework can target passed or approved drawings without a build."""
    if not isinstance(note, str) or not note.strip():
        raise contract.PartsContractError("rework requires concrete instructions")
    root, index, registry, _ = parts_motion._active(assembly)
    state = _load(root, part)
    order, _ = _poses(registry[index["motion"]["id"]], _states(state, index))
    if not isinstance(poses, list) or not poses or not all(isinstance(p, str) for p in poses):
        raise contract.PartsContractError("choose at least one rework pose")
    contract._members(poses, order, "rework poses")
    held = state["config"]["mode"] == "held"
    targets = order if held else list(dict.fromkeys(poses))
    rows = _track(state, track)
    previous = {}
    for pose in targets:
        row = rows["poses"].setdefault(pose, {"candidates": [], "selected": None, "check": None})
        previous[pose] = row["selected"]
        row.update(decision="unverified", reviewer=None, note=None,
                   rework={"status": "requested", "note": note.strip(), "replaces": row["selected"]})
    state.setdefault("rework_requests", []).append({"track": track or MAIN, "poses": targets,
                                                    "note": note.strip(), "replaces": previous})
    _save(root, state)
    return {"part": part, "poses": targets, "draw_poses": [_reference(state["config"], order)] if held else targets,
            "note": note.strip(), "art_approved": False}


def slice_batch(assembly, part, *, batch=None, pages=(), track=None):
    """Cut every drawn cell of a batch into a candidate and select it for its pose."""
    root, index, registry, _ = parts_motion._active(assembly)
    production = _load(root, part)
    config = production["config"]
    rows = _track(production, track)
    settings = _settings(config, track)
    motion = registry[index["motion"]["id"]]
    rig = registry[index["rig"]["id"]]
    topology = registry[rig["topology"]["id"]]
    clips = {clip["id"]: clip for clip in motion["clips"]}
    order, _ = _poses(motion, _states(production, index))
    load = compose_parts.AssetLoader(root, index)
    entry = _batch(rows, batch) if not pages else None
    pairs = [(Path(g), Path(a)) for g, a in pages] if pages else \
        [(root / p["guide"], root / p["art"]) for p in entry["pages"] if (root / p["art"]).exists()]
    if not pairs:
        raise contract.PartsContractError("no drawn page to slice: draw the batch's art paths first")
    frozen, frozen_base = parts_reference.read(root, entry["reference"]) if entry and entry.get("reference") else (None, None)
    if frozen and (frozen["motion"] != index["motion"] or frozen["rig"] != index["rig"] or frozen["body"] != index["body"]):
        raise contract.PartsContractError("generation reference belongs to a different body, rig or motion revision")
    if frozen:
        parts_reference.verify(root, frozen, frozen_base)
        pairs = [(root / frozen_base / p["guide"]["file"], root / p["art"])
                 for p in frozen["pages"] if (root / p["art"]).exists()]
    cut = parts_reference.cut_policy(frozen, frozen_base) if frozen else settings["cut"]
    colors = _palette(root, cut)
    canvas = (rig["canvas"]["width"], rig["canvas"]["height"])
    made = []
    for guide, art in pairs:
        sheet_image = Image.open(Path(guide).parent / "pose-sheet.png")
        for cell, sample, drawn, page in sheets.drawn_cells([(guide, art)], index, clips, cut["tolerance"]):
            crop = page["crop"]
            under = sheets._under(page, cell, sample, motion, index, registry, topology, load, sheet_image=sheet_image)
            drawn_full = Image.new("RGBA", canvas)
            drawn_full.alpha_composite(drawn, tuple(crop[:2]))
            extra = {}
            guard_images = {}
            guards = ({name: (frozen_base / ref["file"]).as_posix()
                       for name, ref in frozen["poses"][sample["pose_id"]]["checks"].items()} if frozen else
                      config["pose_checks"].get(sample["pose_id"], {}))
            for name, file in guards.items():
                if file is not None:
                    encoded = assets.read_file(root, file)
                    if frozen and assets.sha256(encoded) != frozen["poses"][sample["pose_id"]]["checks"][name]["sha256"]:
                        raise contract.PartsContractError(f"{sample['pose_id']}: generation {name} mask changed")
                    guard = Image.open(BytesIO(encoded)).convert("L")
                    if guard.size != canvas:
                        raise contract.PartsContractError(f"{sample['pose_id']}: {name} mask must match the rig canvas")
                    guard_images[f"qa_{name}"] = guard
                    if name == "occluded":
                        guard_images["render_mask"] = Image.fromarray(
                            np.where(np.asarray(guard) > 0, 0, 255).astype(np.uint8))
            if config["role"] == "body":
                # The painted cell labels the body; every body pixel takes the nearest painted label.
                nodes = list(config["body"]["colors"])
                rgb = {node: _rgb(spec["color"]) for node, spec in config["body"]["colors"].items()}
                labels, painted = parts_segments.label_image(drawn_full, under, rgb, config["body"]["tolerance"])
                swapped = parts_segments.swap_by_shading(labels, under, nodes, [tuple(p) for p in config["body"]["near_lighter"]])
                full = parts_segments._labels_image(labels, rgb)
                area = int((np.asarray(under)[..., 3] > 0).sum())
                extra = {"painted_share": round(painted / area, 3) if area else 0.0, "swapped": swapped}
                alignment, holes = None, 0
            else:
                piece, info = sheets.cut_cell(drawn, under.crop(tuple(crop)), method=cut["method"],
                                              body_threshold=cut["body_threshold"], near_body=cut["near_body"],
                                              min_island=cut["min_island"], outline=cut["outline"],
                                              fill_holes=cut["fill_holes"], close_body=cut["close_body"], colors=colors,
                                              keep_palette=cut["keep_palette"], prune=cut["prune"], rim=cut["rim"],
                                              inset=cut["inset"], specks=cut["specks"], rescue=cut["rescue"],
                                              against=_against(root, cut),
                                              largest=None if cut["largest"] is None else cut["largest"]/100)
                full = Image.new("RGBA", canvas)
                full.alpha_composite(piece, tuple(crop[:2]))
                if cut["region"] is not None:
                    kept = Image.new("RGBA", canvas)
                    kept.paste(full.crop(tuple(cut["region"])), tuple(cut["region"][:2]))
                    full = kept
                for x0, y0, x1, y1 in cut["exclude"]:
                    # A zone another part owns (the face under capped hair): whatever was drawn there is theirs.
                    full.paste((0, 0, 0, 0), (x0, y0, x1, y1))
                alignment, holes = info["alignment"], info["holes"]
            if "render_mask" in guard_images:
                guard_images["render_part"] = _visible_part(full, guard_images["render_mask"])
            mask = Image.fromarray(((np.asarray(full)[..., 3] > 0)*255).astype(np.uint8), "L")
            socket = sample["sockets"][config["socket"]]
            record = {"kind": "parts-production-candidate", "schema_version": 1, "policy": POLICY, "part": part,
                      "track": track or MAIN, "pose_id": sample["pose_id"], "clip": cell["clip"], "sample": cell["sample"],
                      "motion": index["motion"], "body_sha256": cell["body_sha256"],
                      "under_sha256": cell.get("under_sha256"), "wearing": page.get("wearing", []),
                      "method": {"cut": cut if config["role"] == "part" else config["body"],
                                 "batch": None if entry is None else entry["id"]},
                      "source": {"guide_sha256": assets.sha256(guide.read_bytes()), "art_sha256": assets.sha256(art.read_bytes()),
                                 "origin": cell["origin"]},
                      "placement": {"format": "full-canvas", "pivot": list(socket["position"]),
                                    "target_socket": config["socket"], "offset": [0, 0]},
                      "generation_reference": None if not frozen else entry["reference"], "crop": crop,
                      "alignment": alignment, "holes_filled": holes,
                      "pixels": int((np.asarray(full)[..., 3] > 0).sum()), **extra}
            record, location = _publish_candidate(root, part, record, {"drawn": drawn_full, "under": under,
                                                                        "mask": mask, "part": full, **guard_images})
            # A held part is one drawing: the drawing made on its reference pose hangs on every pose.
            targets = order if config["mode"] == "held" else [sample["pose_id"]]
            _select(rows, targets, location, keep_approved=config["mode"] != "held")
            made.append({"pose": sample["pose_id"], "candidate": location, "pixels": record["pixels"],
                         "alignment": record["alignment"], **extra})
    _save(root, production)
    return {"part": part, "track": track or MAIN, "candidates": made,
            "next": "parts-part-check, then parts-part-build and parts-part-review", "status": "draft", "release_ready": False}


def _visible_part(raw, visibility):
    pixels = np.asarray(raw.convert("RGBA")).copy()
    pixels[..., 3] = (pixels[..., 3].astype(np.uint16) *
                     np.asarray(visibility.convert("L"), dtype=np.uint16) // 255).astype(np.uint8)
    return Image.fromarray(pixels)


def mask(assembly, part, pose, image, *, track=None):
    """Replace a pose's region with an explicit mask (white = part) on its drawn cell."""
    root, index, registry, _ = parts_motion._active(assembly)
    production = _load(root, part)
    if production["config"]["role"] == "body":
        raise contract.PartsContractError("a body is cut by its labels: redraw the label page instead")
    rows = _track(production, track)
    row = rows["poses"].get(pose)
    if row is None or row["selected"] is None:
        raise contract.PartsContractError(f"{pose} has no selected candidate to mask")
    base = root / row["selected"]
    record = workspace.decode_json((base / "candidate.json").read_bytes())
    if "drawn" not in record["images"]:
        raise contract.PartsContractError(f"{pose}: an adopted drawing has no drawn cell to mask")
    drawn = Image.open(base / "drawn.png").convert("RGBA")
    region = Image.open(image).convert("L")
    if region.size != drawn.size:
        raise contract.PartsContractError(f"mask must be the rig canvas {drawn.size}")
    keep = (np.asarray(region) >= 128) & (np.asarray(drawn)[..., 3] > 0)
    full = Image.fromarray(np.where(keep[..., None], np.asarray(drawn), 0).astype(np.uint8), "RGBA")
    colors = _palette(root, _settings(production["config"], track)["cut"])
    if colors is not None:
        full = pixel_grid.snap(full, colors)
    guards = {}
    for name, ref in record["images"].items():
        if name.startswith("qa_") or name == "render_mask":
            encoded = assets.read_file(base, ref["file"])
            if assets.sha256(encoded) != ref["sha256"]:
                raise contract.PartsContractError(f"{pose}: changed {name} mask")
            guards[name] = Image.open(BytesIO(encoded)).copy()
    if "render_mask" in guards:
        guards["render_part"] = _visible_part(full, guards["render_mask"])
    new = {k: deepcopy(v) for k, v in record.items() if k not in ("digest", "images")}
    new["method"] = {**new["method"], "mask": assets.sha256(Path(image).read_bytes()), "replaces": record["digest"]}
    new["pixels"] = int(keep.sum())
    new, location = _publish_candidate(root, part, new, {"drawn": drawn, "under": Image.open(base / "under.png"),
                                                          "mask": Image.fromarray((keep*255).astype(np.uint8), "L"),
                                                          "part": full, **guards})
    order, _ = _poses(registry[index["motion"]["id"]], _states(production, index))
    _select(rows, order if production["config"]["mode"] == "held" else [pose], location, keep_approved=False)
    _save(root, production)
    return {"part": part, "pose": pose, "candidate": location, "pixels": new["pixels"], "release_ready": False}


def adopt_labels(assembly, part, labels):
    """Take finished label maps (`<pose_id>.png` on the rig canvas) as a body production's candidates.

    They are used as they are; `near_lighter` corrects freshly painted pages only.
    """
    root, index, registry, _ = parts_motion._active(assembly)
    production = _load(root, part)
    config = production["config"]
    if config["role"] != "body":
        raise contract.PartsContractError("labels are adopted by a body production")
    motion = registry[index["motion"]["id"]]
    order, first = _poses(motion, _states(production, index))
    load = compose_parts.AssetLoader(root, index)
    rows = _track(production, None)
    rgb = {node: _rgb(spec["color"]) for node, spec in config["body"]["colors"].items()}
    made, missing = [], []
    for pose in order:
        file = Path(labels) / f"{pose}.png"
        if not file.is_file():
            missing.append(pose)
            continue
        clip_id, sample = first[pose]
        body = load(motion, sample["image"]).convert("RGBA")
        paint = Image.open(file).convert("RGBA")
        values, painted = parts_segments.label_image(paint, body, rgb, config["body"]["tolerance"])
        # Adopted labels are taken as final: correcting them again could flip a pair already fixed.
        swapped = []
        full = parts_segments._labels_image(values, rgb)
        area = int((np.asarray(body)[..., 3] > 0).sum())
        record = {"kind": "parts-production-candidate", "schema_version": 1, "policy": POLICY, "part": part, "track": MAIN,
                  "pose_id": pose, "clip": clip_id, "sample": sample["id"], "motion": index["motion"],
                  "body_sha256": sample["image"]["sha256"], "under_sha256": None, "wearing": [],
                  "method": {"adopted_labels": assets.sha256(file.read_bytes()), "body": config["body"]}, "source": None,
                  "placement": {"format": "full-canvas", "pivot": list(sample["sockets"][config["socket"]]["position"]),
                                "target_socket": config["socket"], "offset": [0, 0]},
                  "alignment": None, "holes_filled": 0, "pixels": int((np.asarray(full)[..., 3] > 0).sum()),
                  "painted_share": round(painted / area, 3) if area else 0.0, "swapped": swapped}
        record, location = _publish_candidate(root, part, record, {
            "drawn": paint, "under": body, "mask": Image.fromarray(((np.asarray(full)[..., 3] > 0)*255).astype(np.uint8), "L"),
            "part": full})
        _select(rows, [pose], location)
        made.append(pose)
    _save(root, production)
    return {"part": part, "adopted": len(made), "missing": missing, "release_ready": False}


def adopt(assembly, part, pack=None, *, piece=None, track=None, labels=None):
    """Take an existing pack's drawings as candidates: each pose's pieces flattened back to one drawing.

    `piece` takes one piece only (a pack's capped hair, into a variant track). A held part
    takes its one drawing; offsets per sample come back from the config's follow and trail.
    """
    if labels is not None:
        return adopt_labels(assembly, part, labels)
    if pack is None:
        raise contract.PartsContractError("adopt takes --pack (a part) or --labels (a body)")
    root, index, registry, _ = parts_motion._active(assembly)
    production = _load(root, part)
    config = production["config"]
    rows = _track(production, track)
    motion = registry[index["motion"]["id"]]
    path = Path(pack).absolute()
    document = contract.parse(workspace.decode_json(path.read_bytes()))
    if document["kind"] != "parts-pack" or len(document["bindings"]) != 1:
        raise contract.PartsContractError("adopt takes a parts-pack with one binding")
    binding = document["bindings"][0]
    reader = assets.SourceReader(path.parent)
    order, first = _poses(motion, _states(production, index))
    canvas = (registry[index["rig"]["id"]]["canvas"]["width"], registry[index["rig"]["id"]]["canvas"]["height"])
    ranks = parts_resolve.layer_ranks(registry[registry[index["rig"]["id"]]["topology"]["id"]])
    pieces = [p for p in binding["pieces"] if piece is None or p["id"] == piece]
    if not pieces:
        raise contract.PartsContractError(f"{document['id']} has no piece {piece}")
    pieces.sort(key=lambda p: (parts_resolve.rank({"node": p["node"], "provides": p["provides"]}, ranks), p["id"]))
    same_motion = binding["motion"] == index["motion"]

    def placement_of(p, pose):
        clip_id, sample = first[pose]
        found = p.get("pose_map", {}).get(pose)
        if found is None and same_motion:
            found = p["sample_map"].get(clip_id, {}).get(sample["id"])
        return found

    made, missing = [], []
    poses = [_reference(config, order)] if config["mode"] == "held" else order
    for pose in poses:
        clip_id, sample = first[pose]
        placements = [placement_of(p, pose) for p in pieces]
        if any(placement is None for placement in placements):
            missing.append(pose)
            continue
        full = Image.new("RGBA", canvas)
        for placement in placements:
            if not placement["visible"]:
                continue
            # Without the pack's own follow-through, so the config's follow and trail apply once.
            offset = [t - p for t, p in zip(sample["sockets"][placement["target_socket"]]["position"], placement["pivot"])]
            if config["mode"] == "raster":
                offset = [o + d for o, d in zip(offset, placement["offset"])]
            layer = Image.new("RGBA", canvas)
            layer.paste(assets.load_image(reader, placement["image"]), tuple(offset))
            full.alpha_composite(layer)
        alpha = np.asarray(full)[..., 3] > 0
        guards = {}
        for name, file in config["pose_checks"].get(pose, {}).items():
            if file is not None:
                guard = Image.open(BytesIO(assets.read_file(root, file))).convert("L")
                if guard.size != canvas:
                    raise contract.PartsContractError(f"{pose}: {name} mask must match the rig canvas")
                guards[f"qa_{name}"] = guard
                if name == "occluded":
                    guards["render_mask"] = Image.fromarray(np.where(np.asarray(guard)>0,0,255).astype(np.uint8))
        record = {"kind": "parts-production-candidate", "schema_version": 1, "policy": POLICY, "part": part,
                  "track": track or MAIN, "pose_id": pose, "clip": clip_id, "sample": sample["id"], "motion": index["motion"],
                  "body_sha256": sample["image"]["sha256"], "under_sha256": None, "wearing": [],
                  "method": {"adopted": contract.reference(document), "piece": piece}, "source": None,
                  "placement": {"format": "full-canvas", "pivot": list(sample["sockets"][config["socket"]]["position"]),
                                "target_socket": config["socket"], "offset": [0, 0]},
                  "alignment": None, "holes_filled": 0, "pixels": int(alpha.sum())}
        record, location = _publish_candidate(root, part, record, {
            "mask": Image.fromarray((alpha*255).astype(np.uint8), "L"), "part": full, **guards})
        _select(rows, order if config["mode"] == "held" else [pose], location)
        made.append(pose)
    _save(root, production)
    return {"part": part, "track": track or MAIN, "adopted": len(made), "missing": missing, "release_ready": False}


def _neighbours(motion, states):
    """For each pose, the poses just before and after it in every clip that shows it."""
    near = {}
    for clip in motion["clips"]:
        if clip["state"] not in states:
            continue
        ids = [s["pose_id"] for s in clip["samples"]]
        for n, pose in enumerate(ids):
            near.setdefault(pose, set()).update({ids[n-1], ids[(n+1) % len(ids)]} - {pose})
    return near


def _islands(mask, size):
    return sum(1 for component in parts_segments._components(mask) if component.sum() < size)


def _pinholes(mask, size=12):
    """Pixels of small enclosed gaps (skin showing through); a large enclosed gap is a shape (between legs)."""
    if not mask.any():
        return 0
    return int(sum(c.sum() for c in parts_segments._components(sheets.enclosed(mask)) if c.sum() <= size))


def _drawn_over_changed(record, context, base):
    """Worn parts whose current render under this drawing differs from the one it was drawn over
    by more than a few pixels (a recut hair is still the hair the hat was fitted to)."""
    if not record.get("wearing") or record.get("under_sha256") is None:
        return []
    motion, index, registry, topology, load = context
    clip = next(c for c in motion["clips"] if c["id"] == record["clip"])
    sample = next(s for s in clip["samples"] if s["id"] == record["sample"])
    dressed = sheets.worn_now(record["wearing"], record["clip"], sample, motion, index, registry, topology, load)
    if dressed is not None:
        if assets.sha256(assets.png_bytes(dressed)) == record["under_sha256"]:
            return []
        if (base / "under.png").exists():
            then, now = np.asarray(Image.open(base / "under.png").convert("RGBA")), np.asarray(dressed)
            bare = np.asarray(load(motion, sample["image"]).convert("RGBA"))
            worn = int((now != bare).any(-1).sum())
            if then.shape == now.shape and int((then != now).any(-1).sum()) <= LIMITS["worn_drift"]*worn:
                return []
    return [ref["id"] for ref in record["wearing"] if registry.get(ref["id"], {}).get("digest") != ref["digest"]] \
        or [ref["id"] for ref in record["wearing"]]


def _measure(root, config, settings, location, context):
    """Metrics of one candidate, and its findings as (level, message)."""
    base = root / location
    record = workspace.decode_json((base / "candidate.json").read_bytes())
    if record.get("generation_reference"):
        reference, reference_base = parts_reference.read(root, record["generation_reference"])
        parts_reference.verify(root, reference, reference_base)
    settings = {**settings, "cut": record.get("method", {}).get("cut", settings["cut"])}
    image = np.asarray(Image.open(base / "part.png").convert("RGBA"))
    mask = image[..., 3] > 0
    raw_mask = mask.copy()
    visible = np.ones(mask.shape, dtype=bool)
    metrics = {"pixels": int(mask.sum()), "alignment": record["alignment"], "coverage": None}
    if config["visibility"] == "mask" and "render_mask" in record["images"]:
        ref = record["images"]["render_mask"]
        encoded = assets.read_file(base, ref["file"])
        if assets.sha256(encoded) != ref["sha256"]:
            raise contract.PartsContractError(f"{record['pose_id']}: changed occlusion mask")
        visible = np.asarray(Image.open(BytesIO(encoded)).convert("L")) > 0
        metrics["occluded_pixels"] = int((mask & ~visible).sum())
        mask &= visible
    elif config["visibility"] == "zmap" and config["segments"] and context[3]["layers"]:
        from sprite_gen.frames import parts_candidate_scene
        required = None
        if ref := record["images"].get("qa_required"):
            encoded = assets.SourceReader(base).read(ref["file"], ref["sha256"])
            required = np.asarray(Image.open(BytesIO(encoded)).convert("L")) > 0
        visible = parts_candidate_scene.visibility(context[4].root, context[1], context[2], config,
            record["pose_id"], Image.fromarray(image), record["placement"], required=required)
        metrics["occluded_pixels"] = int((mask & ~visible).sum())
        mask &= visible
    metrics["inspection_space"] = "zmap-visible" if config["visibility"] == "zmap" and config["segments"] and context[3]["layers"] else "mask-visible" if config["visibility"] == "mask" else "raw-source"
    if record["alignment"] is not None and "drawn" in record["images"] and "under" in record["images"]:
        crop = record.get("crop", config["sheet"]["crop"])
        drawn, under = (Image.open(base / f"{n}.png").convert("RGBA") for n in ("drawn", "under"))
        if crop:
            drawn, under = drawn.crop(tuple(crop)), under.crop(tuple(crop))
        metrics["coverage"] = sheets.overlap(drawn, under)[1]
    findings = []
    if config["role"] == "body":
        metrics.update(painted_share=record.get("painted_share"), swapped=record.get("swapped", []))
        if metrics["painted_share"] is not None and metrics["painted_share"] < LIMITS["painted_share"]:
            findings.append(("warning", f"only {metrics['painted_share']:.0%} of the body was painted; labels are guessed"))
        if metrics["swapped"]:
            findings.append(("warning", f"near/far labels swapped by shading ({', '.join(metrics['swapped'])}); confirm"))
        return metrics, findings
    metrics.update(specks=_islands(raw_mask, 4), holes=_pinholes(raw_mask), visible_pixels=int(mask.sum()))
    # These authored interfaces are frozen with the candidate, not read from a
    # later mutable config. Matching the body's RGB is not a new garment's gate.
    for name in ("required", "protected", "allowed"):
        ref = record["images"].get(f"qa_{name}")
        if ref is not None:
            encoded = assets.read_file(base, ref["file"])
            if assets.sha256(encoded) != ref["sha256"]:
                raise contract.PartsContractError(f"{record['pose_id']}: changed {name} mask")
            region = np.asarray(Image.open(BytesIO(encoded)).convert("L")) > 0
            if name == "required":
                region &= visible
            count = int((region & ~mask).sum() if name == "required" else
                        (region & mask).sum() if name == "protected" else (~region & mask).sum())
            metrics[f"{name}_pixels"] = count
            if count:
                findings.append(("error", f"{name}: {count} missing or forbidden pixels"))
    colors = _palette(root, settings["cut"])
    if colors is not None and mask.any():
        pixels = image[mask][:, :3]
        metrics["off_palette"] = int((np.abs(pixels[:, None, :].astype(int) - colors[None].astype(int)).max(-1).min(-1) > 0).sum())
    edit = record["method"].get("programmatic_edit", {})
    registered = edit.get("registered_geometry", False) or any(op["type"] in ("translate", "fit-alpha-box") for op in edit.get("operations", []))
    if registered:
        metrics["source_comparison"] = "original retained; spatial cut comparison skipped after registration"
    if not registered and "drawn" in record["images"] and "under" in record["images"]:
        # What the drawing changed but the part left out: a cut too tight (a hood off the back).
        drawn = Image.open(base / "drawn.png").convert("RGBA")
        under = Image.open(base / "under.png").convert("RGBA")
        part_only = record["method"].get("cut", {}).get("method") == "part-only"
        changed = np.asarray(drawn)[..., 3] > 0 if part_only else np.asarray(sheets.cover(drawn, under))[..., 3] > 0
        if not part_only and colors is not None and settings["cut"]["keep_palette"] is not None:
            # Repainted body is left out on purpose; it only says how much the model strayed.
            repainted = sheets.repainted(drawn, under, colors, settings["cut"]["keep_palette"])
            metrics["repainted"] = int(repainted.sum())
            changed &= ~repainted
        metrics["dropped"] = int((changed & ~raw_mask).sum())
    for name in _drawn_over_changed(record, context, base):
        # Drawn over a worn part that now looks different (the hat under capped hair, redrawn).
        findings.append(("error", f"drawn over {name} as it was before; redraw over the current one"))
    if metrics["pixels"] == 0:
        findings.append(("warning", "empty: nothing drawn for this pose (hidden, or lost in the cut)"))
    if sheets.misaligned(metrics["alignment"], metrics["coverage"]):
        findings.append(("error", f"alignment {metrics['alignment']} (body coverage {metrics['coverage']}): "
                                  "the drawn figure does not sit on the body; redraw"))
    if metrics.get("dropped", 0) > max(8, LIMITS["dropped_share"]*metrics["pixels"]):
        findings.append(("warning", f"{metrics['dropped']} drawn pixels left out by the cut; loosen it or mask"))
    if metrics.get("repainted", 0) > LIMITS["repainted_share"]*max(metrics["pixels"], 1):
        findings.append(("warning", f"the model repainted {metrics['repainted']} px of the body (left out of the part); "
                                    "redraw if the part itself looks off"))
    if metrics["specks"] > LIMITS["specks"] and config["mode"] == "raster":  # a held face's eye glint is drawn on purpose
        findings.append(("warning", f"{metrics['specks']} specks under 4 px"))
    if metrics["holes"] > LIMITS["holes"]:
        findings.append(("warning", f"{metrics['holes']} px of pinholes (gaps of 12 px or less inside the part)"))
    if metrics.get("off_palette"):
        findings.append(("warning", f"{metrics['off_palette']} px off the palette"))
    return metrics, findings


def check(assembly, part, *, track=None):
    """Per-pose checks of each selected drawing, for every track (or one); nothing is approved here."""
    root, index, registry, _ = parts_motion._active(assembly)
    production = _load(root, part)
    config = production["config"]
    motion = registry[index["motion"]["id"]]
    states = _states(production, index)
    order, first = _poses(motion, states)
    near = _neighbours(motion, states)
    context = (motion, index, registry, registry[registry[index["rig"]["id"]]["topology"]["id"]],
               compose_parts.AssetLoader(root, index))
    summary, findings_by = {}, {}
    for name in ([track] if track else list(production["tracks"])):
        rows = production["tracks"][name]["poses"]
        settings = _settings(config, name)
        measured, area = {}, {}
        for pose in order:
            row = rows.get(pose)
            if row is None or row["selected"] is None:
                continue
            if row["selected"] not in measured:
                measured[row["selected"]] = _measure(root, config, settings, row["selected"], context)
            metrics, findings = deepcopy(measured[row["selected"]])
            area[pose] = metrics["pixels"]
            row["check"] = {"metrics": metrics, "findings": findings}
        for pose in area:
            row = rows[pose]
            metrics, findings = row["check"]["metrics"], row["check"]["findings"]
            others = [area[p] for p in near.get(pose, ()) if p in area]
            if config["mode"] == "raster" and config["role"] == "part" and others and metrics["pixels"]:
                reference = float(np.median(others))
                metrics["area_jump"] = round(abs(metrics["pixels"] - reference) / max(reference, 1), 3)
                if metrics["area_jump"] > LIMITS["area_jump"]:
                    findings.append(("warning", f"area differs {metrics['area_jump']:.0%} from neighbouring poses"))
            regions = parts_quality.temporal(root, rows, pose, config["qa"]["stable_regions"], first)
            if regions:
                metrics["stable_regions"] = regions
                findings.extend(("warning", f"stable region {r['id']} differs from {r['reference_pose']}")
                                for r in regions if r["status"] == "warning")
                findings.extend(("warning", f"stable region {r['id']} could not be inspected")
                                for r in regions if r["status"] == "미검사")
            status = "error" if any(level == "error" for level, _ in findings) else "warning" if findings else "passed"
            row["check"] = {"status": status, "metrics": metrics, "findings": [message for _, message in findings],
                            "criteria": parts_quality.criteria(metrics, mode=config["mode"]), "qa_policy": config["qa"], "art_approved": False}
        summary[name] = {s: sum(1 for p in area if rows[p]["check"]["status"] == s) for s in ("passed", "warning", "error")}
        findings_by.update({(p if name == MAIN else f"{name}:{p}"): rows[p]["check"]["findings"]
                            for p in area if rows[p]["check"]["findings"]})
    _save(root, production)
    return {"part": part, "summary": summary[MAIN] if list(summary) == [MAIN] else summary,
            "checked": sum(sum(v.values()) for v in summary.values()), "findings": findings_by, "release_ready": False}


def _offsets(config, motion, states, order, first):
    """Per sample offset of a held drawing: the follow socket's move from the reference pose, plus trail."""
    reference = _reference(config, order)
    reference_sample = first[reference][1]
    out = {}
    for clip in motion["clips"]:
        if clip["state"] not in states:
            continue
        trail = None
        if config["trail"] is not None:
            from sprite_gen.frames import parts_rebind
            positions = [s["sockets"][config["trail"]["socket"]]["position"] for s in clip["samples"]]
            trail = parts_rebind.lag_offsets(positions, Fraction(config["trail"]["gain"]), config["trail"]["max_px"],
                                             loop=clip["loop"])
        for n, sample in enumerate(clip["samples"]):
            offset = [0, 0]
            if config["follow"] is not None:
                name = config["follow"]["socket"]
                offset = [a - b for a, b in zip(sample["sockets"][name]["position"], reference_sample["sockets"][name]["position"])]
            if trail is not None:
                offset = [o + d for o, d in zip(offset, trail[n])]
            out[clip["id"], sample["id"]] = offset
    return out


def _track_piece(root, part, config, motion, states, name, rows, order, first):
    """One track's selected drawings as an import piece (pose_map, or sample_map when it trails)."""
    settings = _settings(config, name)
    placements = {}
    for pose in order:
        row = rows["poses"].get(pose)
        if row is None or row["selected"] is None:
            continue
        record = workspace.decode_json((root / row["selected"] / "candidate.json").read_bytes())
        # The raw drawing remains reviewable; only the derived visible raster is assembled.
        ref = record["images"].get("render_part", record["images"]["part"]) if config["visibility"] == "mask" else record["images"]["part"]
        image = (root / row["selected"] / ref["file"]).relative_to(_folder(root, part)).as_posix()
        placements[pose] = {**record["placement"], "image": {"file": image, "sha256": ref["sha256"]},
                            "visible": record["pixels"] > 0}
    piece = {"id": config["slot"] if name == MAIN else name, "slot": config["slot"], "node": settings["node"],
             "mode": config["mode"]}
    if settings["cover_rule"] is not None:
        piece["cover_rule"] = settings["cover_rule"]
    if config["mode"] == "held" and (config["follow"] or config["trail"]):
        offsets = _offsets(config, motion, states, order, first)
        piece["sample_map"] = {}
        for clip in motion["clips"]:
            if clip["state"] not in states:
                continue
            for sample in clip["samples"]:
                if sample["pose_id"] in placements:
                    piece["sample_map"].setdefault(clip["id"], {})[sample["id"]] = {
                        **placements[sample["pose_id"]], "offset": offsets[clip["id"], sample["id"]]}
    else:
        piece["pose_map"] = placements
    return piece, sorted(placements)


def _build_body(assembly, root, part, production, order):
    """Selected label maps -> the motion's rasters cut into node pieces -> imported segmented body."""
    config = production["config"]
    folder = _folder(root, part) / "build"
    labels = folder / "labels"
    labels.mkdir(parents=True, exist_ok=True)
    rows = production["tracks"][MAIN]["poses"]
    for pose in order:
        Image.open(root / rows[pose]["selected"] / "part.png").save(labels / f"{pose}.png")
    colors = folder / "colors.json"
    atomic_write_text(colors, workspace.json_bytes({node: {"color": spec["color"], "provides": spec["provides"]}
                                                    for node, spec in config["body"]["colors"].items()}).decode("utf-8"))
    cut = parts_segments.segment_body(assembly, folder / "segments", colors=colors, labels=labels, body_id=part,
                                      design_id=config["design_id"], tolerance=8)
    imported = parts_import.import_pack(assembly, Path(cut["spec"]), Path(cut["source_root"]))
    return imported, {"segments": (folder / "segments" / "segments.json").relative_to(root).as_posix(),
                      "unprovided_required_nodes": cut["unprovided_required_nodes"]}


def build(assembly, part, *, allow_incomplete=False):
    """Selected drawings -> pieces (main, split by body segment, and variants) -> imported pack."""
    root, index, registry, _ = parts_motion._active(assembly)
    production = _load(root, part)
    config = production["config"]
    motion = registry[index["motion"]["id"]]
    rig = registry[index["rig"]["id"]]
    states = _states(production, index)
    order, first = _poses(motion, states)
    missing = {name: [p for p in order if track["poses"].get(p, {}).get("selected") is None]
               for name, track in production["tracks"].items()}
    gaps = {name: poses for name, poses in missing.items() if poses}
    if gaps and not allow_incomplete:
        raise contract.PartsContractError(f"{part}: poses without a drawing: {gaps}")
    extra = {}
    if config["role"] == "body":
        imported, extra = _build_body(assembly, root, part, production, order)
    else:
        folder = _folder(root, part) / "build"
        pieces = []
        for name, track in production["tracks"].items():
            piece, _ = _track_piece(root, part, config, motion, states, name, track, order, first)
            pieces.append(piece)
        spec = {"kind": "parts-pack-import", "schema_version": 1, "id": part, "design_id": config["design_id"], "role": "part",
                "style": rig["style"], "rig": index["rig"], "motion": index["motion"], "view": rig["view"],
                "binding_id": part + ".binding", "occupies": [config["slot"]], "states": states, "pieces": pieces}
        if config["covers"]:
            spec["covers"] = config["covers"]
        folder.mkdir(parents=True, exist_ok=True)
        atomic_write_text(folder / "import.json", workspace.json_bytes(spec).decode("utf-8"))
        spec_file, source = folder / "import.json", _folder(root, part)
        if config["pieces"]:
            split = parts_segments.split_piece(spec_file, root / config["segments"], folder / "split", piece=config["slot"],
                                               into=[(p["id"], p["node"], p["segments"]) for p in config["pieces"]],
                                               source_root=source, by_component=config["by_component"])
            spec_file, source = Path(split["spec"]), Path(split["source_root"])
            if any(piece.get("replaces") for piece in config["pieces"]):
                split_spec = workspace.decode_json(spec_file.read_bytes())
                configured = {piece["id"]: piece for piece in config["pieces"]}
                for piece in split_spec["pieces"]:
                    piece["replaces"] = configured[piece["id"]].get("replaces", [])
                    piece["provides"] = list(piece["replaces"])
                atomic_write_text(spec_file, workspace.json_bytes(split_spec).decode())
        imported = parts_import.import_pack(assembly, spec_file, source, allow_incomplete=allow_incomplete)
    production = _load(root, part)
    production["built"] = {"part": imported["part"], "visibility": config["visibility"], "selection": {
        name: {p: row["selected"] for p, row in track["poses"].items() if p in order and row["selected"]}
        for name, track in production["tracks"].items()}}
    _save(root, production)
    return {"part": imported["part"], "role": config["role"], "tracks": list(production["tracks"]), "missing": gaps,
            "pieces": ([p["id"] for p in config["pieces"]] or [config["slot"]]) if config["role"] == "part" else None,
            **extra, "release_ready": False}


def _part_plan(index, registry, part_ref, states):
    """Resolve one part on the active body for production and inspection."""
    catalog = registry[index["catalog"]["id"]]
    slots = set()
    if part_ref is not None and registry[part_ref["id"]]["role"] == "part":
        slots = {piece["slot"] for binding in registry[part_ref["id"]]["bindings"] for piece in binding["pieces"]}
    selections = {slot["id"]: part_ref if slot["id"] in slots else None for slot in catalog["slots"]}
    appearance = contract.seal({"kind": "parts-appearance", "schema_version": 1, "id": "production.preview",
                                "rig": index["rig"], "motion": index["motion"], "catalog": index["catalog"],
                                "states": states, "selections": selections, "body": index["body"]})
    return parts_resolve.resolve(appearance, [d for d in registry.values() if d["kind"] != "parts-appearance"], complete=False)


def _render_alone(root, index, registry, part_ref, states):
    """Render each pose on the body alone through the shared resolver."""
    plan = _part_plan(index, registry, part_ref, states)
    load = compose_parts.AssetLoader(root, index)
    frames = {}
    motion = registry[index["motion"]["id"]]
    poses = {(c["id"], s["id"]): s["pose_id"] for c in motion["clips"] for s in c["samples"]}
    for clip in plan["clips"]:
        for sample in clip["samples"]:
            pose = poses[clip["id"], sample["id"]]
            if pose not in frames:
                frames[pose], _ = compose_parts.render_sample(plan, sample, load)
    return frames, plan


def drawn_over(root, location):
    """A candidate on what it was drawn over (the body, or the body wearing other parts)."""
    base = root / location
    part = Image.open(base / "part.png").convert("RGBA")
    image = Image.open(base / "under.png").convert("RGBA") if (base / "under.png").exists() else Image.new("RGBA", part.size)
    if image.size != part.size:
        return part
    image.alpha_composite(part)
    return image


def contact(frames, order, rows, *, scale=3, columns=8, crop=None):
    """Poses side by side, each labelled with its check and decision."""
    first = next(iter(frames.values()))
    box = crop or first.getbbox() or (0, 0, *first.size)
    for image in frames.values():
        bounds = image.getbbox()
        if bounds and crop is None:
            box = (min(box[0], bounds[0]), min(box[1], bounds[1]), max(box[2], bounds[2]), max(box[3], bounds[3]))
    w, h = (box[2]-box[0])*scale, (box[3]-box[1])*scale
    count = (len(order)+columns-1)//columns
    sheet = Image.new("RGBA", (w*columns, (h+14)*count), (236, 236, 236, 255))
    draw = ImageDraw.Draw(sheet)
    colour = {"approved": (40, 140, 60, 255), "rejected": (200, 40, 40, 255)}
    for n, pose in enumerate(order):
        x, y = n % columns*w, n//columns*(h+14)
        sheet.alpha_composite(frames[pose].crop(box).resize((w, h), Image.Resampling.NEAREST), (x, y))
        row = rows.get(pose, {})
        check_status = (row.get("check") or {}).get("status", "unchecked")
        draw.text((x+3, y+h), f"{pose} {check_status} {row.get('decision', '')}",
                  fill=colour.get(row.get("decision"), (0, 0, 0, 255)))
    return sheet


def _current(production, registry, order):
    built = production["built"]
    if built is None or registry.get(production["config"]["part_id"], {}).get("digest") != built["part"]["digest"]:
        return False
    if built.get("visibility", "mask") != production["config"]["visibility"]:
        return False
    for name, track in production["tracks"].items():
        selected = {p: row["selected"] for p, row in track["poses"].items() if p in order and row["selected"]}
        if selected != built["selection"].get(name, {}):
            return False
    return True


def review(assembly, part, *, reviewer=None, approve=(), reject=(), note=None, contact_sheet=None, track=None):
    """Judge the built part on the body alone, pose by pose, and seal the result as a part review.

    `approve`/`reject` take pose IDs of one track (the main one by default), or "all" for
    every checked pose without errors. A rejected pose goes back to the todo list of
    parts-part-sheet. The review covers every track and is bound to the exact built pack,
    so it holds in any outfit that selects that pack.
    """
    root, index, registry, original_hash = parts_motion._active(assembly)
    production = _load(root, part)
    config = production["config"]
    states = _states(production, index)
    motion = registry[index["motion"]["id"]]
    order, _ = _poses(motion, states)
    if not _current(production, registry, order):
        raise contract.PartsContractError(f"{part}: the selection changed since the build, or it was never built; "
                                          "run parts-part-build")
    rows = _track(production, track)["poses"]

    def expand(values):
        if list(values) == ["all"]:
            return [p for p in order if (rows[p].get("check") or {}).get("status") in ("passed", "warning")]
        contract._members(values, order, "poses")
        return list(values)
    approved, rejected = expand(approve), expand(reject)
    if set(approved) & set(rejected):
        raise contract.PartsContractError("a pose cannot be approved and rejected at once")
    if (approved or rejected) and not reviewer:
        raise contract.PartsContractError("a decision needs --reviewer")
    for pose in approved:
        if rows[pose].get("rework", {}).get("status") in ("requested", "failed"):
            raise contract.PartsContractError(f"{pose}: finish the requested rework before approving")
        if (rows[pose].get("check") or {}).get("status") in (None, "error"):
            raise contract.PartsContractError(f"{pose}: check it first, and fix errors before approving")
        rows[pose].update(decision="approved", reviewer=reviewer, note=note)
    for pose in rejected:
        rows[pose].update(decision="rejected", reviewer=reviewer, note=note)
    built = production["built"]
    frames, plan = _render_alone(root, index, registry, None if config["role"] == "body" else built["part"], states)
    sheets_ = {MAIN: contact(frames, order, production["tracks"][MAIN]["poses"])}
    for name, other in production["tracks"].items():
        if name != MAIN:
            # A variant shows only under its cover: judge it drawn over what covers it.
            renders = {p: drawn_over(root, other["poses"][p]["selected"]) for p in order}
            sheets_[name] = contact(renders, order, other["poses"])
    payloads = {f"contact.{name}.png": assets.png_bytes(image) for name, image in sheets_.items()}
    checks, report = {}, {}
    for name, other in production["tracks"].items():
        prefix = "" if name == MAIN else f"{name}."
        for p in order:
            checks[f"{prefix}pose.{p}"] = other["poses"][p]["decision"]
            report[f"{prefix}{p}"] = {k: other["poses"][p].get(k) for k in ("selected", "decision", "check", "reviewer", "note")}
    payloads["checks.json"] = workspace.json_bytes(report)
    status = "approved" if all(v == "approved" for v in checks.values()) else \
        "rejected" if any(v == "rejected" for v in checks.values()) else "unverified"
    subject = registry[part]
    inputs = [index["rig"], index["motion"], *index["body"]]
    if built["part"] not in inputs:
        inputs.append(built["part"])
    review_doc = contract.seal({"kind": "parts-review", "schema_version": 1, "id": "review.part." + part,
        "subject_digest": subject["digest"], "scope": "part", "status": status,
        "render_policy": parts_resolve.RENDER_POLICY, "qa_policy": POLICY, "checks": checks,
        "reviewer": reviewer, "note": note,
        "evidence": {"appearance": None, "subject": contract.reference(subject), "binding_id": None, "inputs": inputs,
                     "plan_digest": plan["digest"], "preview_digest": assets.sha256(payloads[f"contact.{MAIN}.png"]),
                     "qa_digest": contract.content_digest(report),
                     "artifacts": [{"file": n, "sha256": assets.sha256(d)} for n, d in sorted(payloads.items())]}})
    payloads["review.json"] = workspace.json_bytes(review_doc)
    folder = assets.publish_object(root, "reviews", review_doc["id"], review_doc["digest"], payloads)
    parts_review._activate(assembly, review_doc, folder, original_hash, index, registry)
    _save(root, production)
    if contact_sheet is not None:
        Path(contact_sheet).parent.mkdir(parents=True, exist_ok=True)
        sheets_[track or MAIN].save(contact_sheet)
    decided = production["tracks"][track or MAIN]["poses"]
    return {"part": part, "track": track or MAIN, "review": contract.reference(review_doc), "status": status,
            "approved": sum(v == "approved" for v in checks.values()),
            "rejected": sorted(p for p in order if decided[p]["decision"] == "rejected"),
            "unverified": sum(v == "unverified" for v in checks.values()),
            "contact": (folder / f"contact.{track or MAIN}.png").relative_to(root).as_posix(),
            "next": "parts-part-sheet draws the rejected poses again" if rejected else None, "release_ready": False}


def status(assembly, part=None):
    """Every production's tracks: poses drawn, checked, decided; and the next step for each."""
    root, index, registry, _ = parts_motion._active(assembly)
    motion = registry[index["motion"]["id"]] if index["motion"] else None
    if motion is None:
        raise contract.PartsContractError("status needs an active motion")
    names = [part] if part else sorted(p.name for p in (root / "productions").glob("*")
                                       if p.is_dir() and (p / "production.json").exists())
    report = []
    for name in names:
        production = _load(root, name)
        order, _ = _poses(motion, _states(production, index))
        current = _current(production, registry, order)
        for track_name, track in production["tracks"].items():
            rows = track["poses"]
            counts = {"poses": len(order), "missing": 0, "rejected": 0, "unchecked": 0, "errors": 0, "warnings": 0,
                      "approved": 0, "unverified": 0}
            for pose in order:
                row = rows.get(pose)
                if row is None or row["selected"] is None:
                    counts["missing"] += 1
                    continue
                check_status = (row.get("check") or {}).get("status")
                counts["unchecked"] += check_status is None
                counts["errors"] += check_status == "error"
                counts["warnings"] += check_status == "warning"
                counts[row["decision"]] += 1
            todo = _todo(production, track_name, order)
            steps = []
            if todo:
                steps.append(f"draw {len(todo)} poses: parts-part-sheet, draw, parts-part-slice")
            if counts["unchecked"]:
                steps.append("parts-part-check")
            if counts["errors"]:
                steps.append(f"fix {counts['errors']} poses with check errors (parts-part-edit or redraw)")
            if not current and not counts["missing"]:
                steps.append("parts-part-build")
            if current and counts["unverified"]:
                steps.append(f"review {counts['unverified']} poses: parts-part-review")
            report.append({"part": name if track_name == MAIN else f"{name}@{track_name}", "production": name,
                           "track": track_name, "role": production["config"]["role"], "mode": production["config"]["mode"],
                           **counts, "built_current": current,
                           "state": "approved" if counts["approved"] == len(order) and current else "in-progress",
                           "next": steps})
    return {"motion": index["motion"], "productions": report, "release_ready": False}


def release_reviews(root, registry, appearance):
    """Approved part reviews for every selected pack and body that has a production; raises when one is missing.

    A part made through a production is released only once it is approved on the body alone,
    pose by pose; the appearance review still judges the combination.
    """
    catalog = registry[appearance["catalog"]["id"]]
    selected = contract.resolve_selections(appearance, catalog)
    candidates = [ref for value in selected.values() for ref in contract._selected_refs(value)] + list(appearance["body"])
    refs = []
    for ref in candidates:
        if not (_folder(root, ref["id"]) / "production.json").exists():
            continue
        state = _load(root, ref["id"])
        order, _ = _poses(registry[appearance["motion"]["id"]], _states(state, appearance))
        if (not _current(state, registry, order) or
                any(track["poses"].get(p, {}).get("decision") != "approved"
                    for track in state["tracks"].values() for p in order)):
            raise contract.PartsContractError(f"release requires current approved production selections for {ref['id']}")
        review = registry.get("review.part." + ref["id"])
        if review is None or review["status"] != "approved" or review["subject_digest"] != ref["digest"]:
            raise contract.PartsContractError(f"release requires an approved part review of {ref['id']} (parts-part-review)")
        refs.append(contract.reference(review))
    return refs


def _poses_arg(value):
    return [p for p in value.split(",") if p]


def _with_track(parser):
    parser.add_argument("--variant", dest="track", help="a variant track (default: the main drawing)")


def add_config_arguments(parser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--part", required=True)
    parser.add_argument("--config", required=True, type=Path, help="parts-production-config JSON")


def add_init_arguments(parser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path, help="parts-production-config JSON")


def add_sheet_arguments(parser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--part", required=True)
    parser.add_argument("--poses", type=_poses_arg, help="comma-separated poses (default: missing and rejected ones)")
    _with_track(parser)


def add_draw_arguments(parser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--part", required=True)
    parser.add_argument("--batch")
    parser.add_argument("--provider", help="default: the config's draw.provider")
    parser.add_argument("--model")
    parser.add_argument("--dry-run", action="store_true", help="print the commands without drawing")
    _with_track(parser)


def add_slice_arguments(parser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--part", required=True)
    parser.add_argument("--batch")
    parser.add_argument("--page", action="append", default=[], dest="pages", type=sheets._page_pair,
                        help="GUIDE=ART instead of the batch's own pages (repeatable)")
    _with_track(parser)


def add_mask_arguments(parser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--part", required=True)
    parser.add_argument("--pose", required=True)
    parser.add_argument("--mask", required=True, type=Path, help="rig-canvas grayscale image, white = part")
    _with_track(parser)


def add_adopt_arguments(parser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--part", required=True)
    parser.add_argument("--pack", type=Path, help="part.json of an existing pack")
    parser.add_argument("--labels", type=Path, help="a body production: folder of painted label maps <pose_id>.png")
    parser.add_argument("--piece", help="only this piece of the pack")
    _with_track(parser)


def add_select_arguments(parser):
    add_part_arguments(parser)
    parser.add_argument("--pose", required=True)
    parser.add_argument("--candidate", required=True, help="workspace-relative candidate path recorded for this pose")
    _with_track(parser)


def add_rework_arguments(parser):
    add_part_arguments(parser)
    parser.add_argument("--poses", type=_poses_arg, required=True)
    parser.add_argument("--note", required=True, help="concrete visual changes, including for passed poses")
    _with_track(parser)


def add_part_arguments(parser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--part", required=True)


def add_check_arguments(parser):
    add_part_arguments(parser)
    _with_track(parser)


def add_build_arguments(parser):
    add_part_arguments(parser)
    parser.add_argument("--allow-incomplete", action="store_true")


def add_review_arguments(parser):
    add_part_arguments(parser)
    parser.add_argument("--reviewer")
    parser.add_argument("--approve", type=_poses_arg, default=[], help="poses to approve, or 'all' (checked, no errors)")
    parser.add_argument("--reject", type=_poses_arg, default=[], help="poses to send back for redrawing")
    parser.add_argument("--note")
    parser.add_argument("--contact-sheet", type=Path, help="also write the contact sheet here")
    _with_track(parser)


def add_status_arguments(parser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--part")


def _runner(action):
    return lambda **kwargs: workspace._report(lambda: action(**kwargs))


run_init, run_sheet, run_draw, run_slice = _runner(init), _runner(sheet), _runner(draw), _runner(slice_batch)
run_mask, run_adopt, run_check, run_build = _runner(mask), _runner(adopt), _runner(check), _runner(build)
run_review, run_status, run_config = _runner(review), _runner(status), _runner(configure)
run_select = _runner(select_candidate)
run_rework = _runner(request_rework)
