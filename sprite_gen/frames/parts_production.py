# SPDX-License-Identifier: Apache-2.0
"""Part production: per-pose candidates, checks, part approval and the redo loop.

A part is produced against the frozen motion's poses. Every drawing that comes back
(a page an image model or an artist drew) is kept as an immutable candidate: the drawn
cell, the region taken as the part (its mask), the part raster, how it was cut and from
what. The production records which candidate each pose uses and the decision on it.

    parts-part-init    a production from a part config (slot, node, socket, cut, pieces)
    parts-part-config  change prompt, sheet, cut, wearing or pieces for drawings still to come
    parts-part-sheet   pose sheets for the poses still to draw (missing or rejected)
    parts-part-draw    optionally ask an image provider to draw those pages
    parts-part-slice   drawn pages -> candidates, selected for their poses
    parts-part-mask    replace a pose's region with an explicit mask
    parts-part-adopt   existing pack drawings -> candidates
    parts-part-check   per-pose checks of the selected drawings
    parts-part-build   selected drawings -> pieces -> imported pack
    parts-part-review  render the part on the body alone; approve or reject poses
    parts-part-status  what is drawn, checked, approved, and what to do next

A part is approved on the body alone, pose by pose, so an approved shoe is reused in
any outfit; combinations are judged separately by appearance reviews. Nothing here
releases anything.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path
import subprocess
import sys

from PIL import Image, ImageDraw

from sprite_gen._deps import np
from sprite_gen.compose import compose_parts, parts_resolve
from sprite_gen.curate import parts_motion, parts_review
from sprite_gen.frames import parts_import, parts_pose_sheet as sheets, parts_segments, pixel_grid
from sprite_gen.spec import parts_assets as assets, parts_contract as contract, parts_workspace as workspace
from sprite_gen.spec.runio import atomic_write_text, publish_guard

POLICY = "parts.production.v1"
_SHEET = contract._record({"crop": contract._nullable(contract._rectangle), "scale": contract._positive,
                           "page_size": contract._nullable(contract._positive), "aspect": contract._nullable(contract._string),
                           "columns": contract._nullable(contract._positive)},
                          {"crop": None, "scale": 4, "page_size": 12, "aspect": "1:1", "columns": None})
_CUT = contract._record({"method": contract._enum(*sheets.METHODS), "body_threshold": contract._nullable(contract._integer),
                         "tolerance": contract._integer, "near_body": contract._nullable(contract._integer),
                         "min_island": contract._integer, "outline": contract._boolean, "fill_holes": contract._integer,
                         "close_body": contract._integer, "region": contract._nullable(contract._rectangle),
                         "palette": contract._nullable(contract._relative_file)},
                        {"method": "difference", "body_threshold": 40, "tolerance": 80, "near_body": 2, "min_island": 12,
                         "outline": True, "fill_holes": 6, "close_body": 5, "region": None, "palette": None})
_PIECE = contract._record({"id": contract._id, "node": contract._id, "segments": contract._list(contract._string, nonempty=True)})
_PROMPT = contract._record({"text": contract._string, "refs": contract._list(contract._relative_file)}, {"refs": []})
_DRAW = contract._record({"provider": contract._id, "model": contract._nullable(contract._string)},
                         {"provider": "codex", "model": None})
_CONFIG = contract._record({
    "kind": contract._enum("parts-production-config"), "schema_version": contract._positive,
    "part_id": contract._id, "design_id": contract._id, "slot": contract._id, "node": contract._id,
    "socket": contract._id, "mode": contract._enum("raster", "held"), "states": contract._ids,
    "sheet": _SHEET, "cut": _CUT, "wearing": contract._ids, "pieces": contract._list(_PIECE),
    "by_component": contract._boolean, "segments": contract._nullable(contract._relative_file),
    "prompt": contract._nullable(_PROMPT), "draw": _DRAW,
}, {"mode": "raster", "draw": {}, "states": [], "sheet": {}, "cut": {}, "wearing": [], "pieces": [], "by_component": False,
    "segments": None, "prompt": None})
DECISIONS = ("unverified", "approved", "rejected")
# Check bounds, judged on the SD demo (docs/parts/10-production.md).
LIMITS = {"alignment": 0.7, "area_jump": 0.45, "dropped_share": 0.04, "specks": 0, "holes": 0}


def _folder(root, part_id):
    return root / "productions" / contract._id(part_id, "part")


def _load(root, part_id):
    path = _folder(root, part_id) / "production.json"
    if not path.is_file():
        raise contract.PartsContractError(f"no production for {part_id}; run parts-part-init")
    production = workspace.decode_json(path.read_bytes())
    # Fields added to the config schema later read with their defaults.
    production["config"] = _CONFIG(production["config"], "production.config")
    return production


def _save(root, production):
    folder = _folder(root, production["config"]["part_id"])
    folder.mkdir(parents=True, exist_ok=True)
    with publish_guard(root):
        atomic_write_text(folder / "production.json", workspace.json_bytes(production).decode("utf-8"))


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


def _todo(production, poses):
    rows = production["poses"]
    return [p for p in poses if rows.get(p, {}).get("selected") is None or rows[p].get("decision") == "rejected"]


def init(assembly, config):
    root, index, registry, _ = parts_motion._active(assembly)
    path = Path(config).absolute()
    value = _CONFIG(workspace.read_json(path.parent, path.name), "config")
    if value["schema_version"] != 1:
        raise contract.PartsContractError("only schema_version 1 is supported")
    rig = registry[index["rig"]["id"]]
    topology = registry[rig["topology"]["id"]]
    slots = {s["id"]: s for s in topology["slots"]}
    contract._members([value["slot"]], slots, "config.slot")
    slot = slots[value["slot"]]
    nodes = [value["node"], *(p["node"] for p in value["pieces"])]
    if slot["nodes"]:
        contract._members(nodes, slot["nodes"], "config.node")
    if slot["sockets"]:
        contract._members([value["socket"]], slot["sockets"], "config.socket")
    if value["pieces"] and value["segments"] is None:
        raise contract.PartsContractError("pieces are cut by body segments: give config.segments (parts-body-segments)")
    if _folder(root, value["part_id"]).joinpath("production.json").exists():
        raise contract.PartsContractError(f"production {value['part_id']} exists; edit its config through a new part ID")
    production = {"kind": "parts-production", "schema_version": 1, "policy": POLICY, "config": value,
                  "poses": {}, "batches": [], "built": None}
    _save(root, production)
    return {"production": str(_folder(root, value["part_id"]) / "production.json"), "part": value["part_id"],
            "status": "draft", "release_ready": False}


FIXED = ("part_id", "design_id", "slot", "node", "socket", "mode")


def configure(assembly, part, config):
    """Change how future drawings are made and cut (prompt, sheet, cut, wearing, pieces).

    Existing candidates keep the settings they were made with; the part's identity (ID,
    slot, node, socket, mode) is fixed once it has drawings. Every change is kept in history.
    """
    root, index, registry, _ = parts_motion._active(assembly)
    production = _load(root, part)
    path = Path(config).absolute()
    value = _CONFIG(workspace.read_json(path.parent, path.name), "config")
    changed = [k for k in FIXED if value[k] != production["config"][k]]
    if changed and any(row["candidates"] for row in production["poses"].values()):
        raise contract.PartsContractError(f"{part}: {changed} are fixed once poses are drawn; start a new part ID")
    if value["pieces"] and value["segments"] is None:
        raise contract.PartsContractError("pieces are cut by body segments: give config.segments")
    production.setdefault("history", []).append({"config": production["config"],
                                                 "replaced_by": contract.content_digest(value)})
    production["config"] = value
    _save(root, production)
    previous = production["history"][-1]["config"]
    return {"part": part, "changed": sorted(k for k in value if value[k] != previous.get(k)),
            "release_ready": False}


def sheet(assembly, part, *, poses=None):
    """Pose sheets of the poses still to draw: never drawn, or rejected at review."""
    root, index, registry, _ = parts_motion._active(assembly)
    production = _load(root, part)
    config = production["config"]
    motion = registry[index["motion"]["id"]]
    order, _ = _poses(motion, _states(production, index))
    wanted = list(poses) if poses else _todo(production, order)
    if not wanted:
        raise contract.PartsContractError(f"{part}: every pose has a selected drawing that is not rejected")
    contract._members(wanted, order, "poses")
    batch = "b" + contract.content_digest({"motion": index["motion"], "poses": wanted, "wearing": config["wearing"]})[:12]
    folder = _folder(root, part) / "batches" / batch
    look = config["sheet"]
    crop = ",".join(map(str, look["crop"])) if look["crop"] else None
    made = sheets.build_sheet(assembly, folder, by_pose=True, poses=wanted, crop=crop, scale=look["scale"],
                              page_size=look["page_size"], aspect=look["aspect"], columns=look["columns"],
                              part=part, wearing=config["wearing"] or None)
    prompt = None
    if config["prompt"] is not None:
        prompt = folder / "prompt.txt"
        prompt.write_text(config["prompt"]["text"] + " " + made["prompt_hint"]
                          + " Keep every figure exactly in its own cell: do not move, rearrange or re-space figures.")
    pages = [{"guide": Path(p["guide"]).relative_to(root).as_posix(), "sheet": Path(p["sheet"]).relative_to(root).as_posix(),
              "art": (Path(p["sheet"]).parent / "drawn.png").relative_to(root).as_posix()} for p in made["pages"]]
    entry = {"id": batch, "motion": index["motion"], "poses": wanted, "pages": pages,
             "prompt": None if prompt is None else prompt.relative_to(root).as_posix()}
    production["batches"] = [b for b in production["batches"] if b["id"] != batch] + [entry]
    _save(root, production)
    return {"part": part, "batch": batch, "poses": wanted, "pages": pages, "prompt": entry["prompt"],
            "refs": [] if config["prompt"] is None else config["prompt"]["refs"],
            "next": "draw each page to its art path (parts-part-draw, or by hand), then parts-part-slice",
            "status": "draft", "release_ready": False}


def draw(assembly, part, *, batch=None, provider=None, model=None, dry_run=False):
    """Ask an image provider (`sprite-gen gen`) to draw each page of a batch that has no art yet.

    The provider and model come from the config's `draw` unless given here.
    """
    root, index, _, _ = parts_motion._active(assembly)
    production = _load(root, part)
    settings = production["config"].get("draw") or {"provider": "codex", "model": None}
    provider, model = provider or settings["provider"], model or settings["model"]
    entry = _batch(production, batch)
    if entry["prompt"] is None:
        raise contract.PartsContractError(f"{part}: config has no prompt; draw the pages by hand")
    refs = [root / ref for ref in production["config"]["prompt"]["refs"]]
    results = []
    for page in entry["pages"]:
        art = root / page["art"]
        if art.exists():
            results.append({"art": page["art"], "status": "exists"})
            continue
        command = [sys.executable, "-m", "sprite_gen.cli", "gen", "--provider", provider, "--prompt-file",
                   str(root / entry["prompt"]), "--out", str(art), "--report", str(art.with_suffix(".report.json")),
                   "--ref", str(root / page["sheet"])]
        for ref in refs:
            command += ["--ref", str(ref)]
        if model:
            command += ["--model", model]
        if dry_run:
            results.append({"art": page["art"], "status": "planned", "command": command})
            continue
        done = subprocess.run(command, capture_output=True, text=True)
        results.append({"art": page["art"], "status": "drawn" if done.returncode == 0 else "failed",
                        "error": done.stderr[-400:] if done.returncode else None})
    return {"part": part, "batch": entry["id"], "pages": results, "release_ready": False}


def _batch(production, batch):
    if not production["batches"]:
        raise contract.PartsContractError("no sheet batch yet; run parts-part-sheet")
    if batch is None:
        return production["batches"][-1]
    found = [b for b in production["batches"] if b["id"] == batch]
    if not found:
        raise contract.PartsContractError(f"unknown batch {batch}")
    return found[0]


def _palette(root, config):
    return None if config["cut"]["palette"] is None else pixel_grid.read_palette(root / config["cut"]["palette"])


def _publish_candidate(root, part, record, images):
    payloads = {f"{name}.png": assets.png_bytes(image) for name, image in images.items()}
    record = {**record, "images": {name.removesuffix(".png"): {"file": name, "sha256": assets.sha256(data)}
                                   for name, data in payloads.items()}}
    record["digest"] = contract.content_digest(record)
    payloads["candidate.json"] = workspace.json_bytes(record)
    folder = assets.publish_object(_folder(root, part), "candidates", contract._id(record["pose_id"], "pose"),
                                   record["digest"], payloads)
    return record, folder.relative_to(root).as_posix()


def _select(production, pose, candidate, *, keep_approved=True):
    row = production["poses"].setdefault(pose, {"candidates": [], "selected": None, "decision": "unverified",
                                                "note": None, "reviewer": None, "check": None})
    row["candidates"].append(candidate)
    if not (keep_approved and row["decision"] == "approved"):
        row.update(selected=candidate, decision="unverified", note=None, reviewer=None, check=None)


def slice_batch(assembly, part, *, batch=None, pages=()):
    """Cut every drawn cell of a batch into a candidate and select it for its pose."""
    root, index, registry, _ = parts_motion._active(assembly)
    production = _load(root, part)
    config = production["config"]
    motion = registry[index["motion"]["id"]]
    rig = registry[index["rig"]["id"]]
    topology = registry[rig["topology"]["id"]]
    clips = {clip["id"]: clip for clip in motion["clips"]}
    load = compose_parts.AssetLoader(root, index)
    entry = _batch(production, batch) if not pages else None
    pairs = [(Path(g), Path(a)) for g, a in pages] if pages else \
        [(root / p["guide"], root / p["art"]) for p in entry["pages"] if (root / p["art"]).exists()]
    if not pairs:
        raise contract.PartsContractError("no drawn page to slice: draw the batch's art paths first")
    cut, colors = config["cut"], _palette(root, config)
    canvas = (rig["canvas"]["width"], rig["canvas"]["height"])
    made = []
    for guide, art in pairs:
        for cell, sample, drawn, page in sheets.drawn_cells([(guide, art)], index, clips, cut["tolerance"]):
            crop = page["crop"]
            under = sheets._under(page, cell, sample, motion, index, registry, topology, load)
            piece, info = sheets.cut_cell(drawn, under.crop(tuple(crop)), method=cut["method"],
                                          body_threshold=cut["body_threshold"], near_body=cut["near_body"],
                                          min_island=cut["min_island"], outline=cut["outline"], fill_holes=cut["fill_holes"],
                                          close_body=cut["close_body"], colors=colors)
            full = Image.new("RGBA", canvas)
            full.alpha_composite(piece, tuple(crop[:2]))
            if cut["region"] is not None:
                kept = Image.new("RGBA", canvas)
                kept.paste(full.crop(tuple(cut["region"])), tuple(cut["region"][:2]))
                full = kept
            drawn_full = Image.new("RGBA", canvas)
            drawn_full.alpha_composite(drawn, tuple(crop[:2]))
            mask = Image.fromarray(((np.asarray(full)[..., 3] > 0)*255).astype(np.uint8), "L")
            socket = sample["sockets"][config["socket"]]
            record = {"kind": "parts-production-candidate", "schema_version": 1, "policy": POLICY, "part": part,
                      "pose_id": sample["pose_id"], "clip": cell["clip"], "sample": cell["sample"],
                      "motion": index["motion"], "body_sha256": cell["body_sha256"],
                      "under_sha256": cell.get("under_sha256"), "wearing": page.get("wearing", []),
                      "method": {"cut": cut, "batch": None if entry is None else entry["id"]},
                      "source": {"guide_sha256": assets.sha256(guide.read_bytes()), "art_sha256": assets.sha256(art.read_bytes()),
                                 "origin": cell["origin"]},
                      "placement": {"format": "full-canvas", "pivot": list(socket["position"]),
                                    "target_socket": config["socket"], "offset": [0, 0]},
                      "alignment": info["alignment"], "holes_filled": info["holes"],
                      "pixels": int((np.asarray(full)[..., 3] > 0).sum())}
            record, location = _publish_candidate(root, part, record, {"drawn": drawn_full, "under": under,
                                                                        "mask": mask, "part": full})
            _select(production, sample["pose_id"], location)
            made.append({"pose": sample["pose_id"], "candidate": location, "pixels": record["pixels"],
                         "alignment": record["alignment"]})
    _save(root, production)
    return {"part": part, "candidates": made, "next": "parts-part-check, then parts-part-build and parts-part-review",
            "status": "draft", "release_ready": False}


def mask(assembly, part, pose, image):
    """Replace a pose's region with an explicit mask (white = part) on its drawn cell."""
    root, _, _, _ = parts_motion._active(assembly)
    production = _load(root, part)
    row = production["poses"].get(pose)
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
    colors = _palette(root, production["config"])
    if colors is not None:
        full = pixel_grid.snap(full, colors)
    new = {k: deepcopy(v) for k, v in record.items() if k not in ("digest", "images")}
    new["method"] = {**new["method"], "mask": assets.sha256(Path(image).read_bytes()), "replaces": record["digest"]}
    new["pixels"] = int(keep.sum())
    new, location = _publish_candidate(root, part, new, {"drawn": drawn, "under": Image.open(base / "under.png"),
                                                          "mask": Image.fromarray((keep*255).astype(np.uint8), "L"),
                                                          "part": full})
    _select(production, pose, location, keep_approved=False)
    _save(root, production)
    return {"part": part, "pose": pose, "candidate": location, "pixels": new["pixels"], "release_ready": False}


def adopt(assembly, part, pack):
    """Take an existing pack's drawings as candidates: each pose's pieces flattened back to one drawing."""
    root, index, registry, _ = parts_motion._active(assembly)
    production = _load(root, part)
    config = production["config"]
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
    pieces = sorted(binding["pieces"], key=lambda p: (parts_resolve.rank({"node": p["node"], "provides": p["provides"]}, ranks), p["id"]))
    made, missing = [], []
    for pose in order:
        clip_id, sample = first[pose]
        placements = [(p, p.get("pose_map", {}).get(pose) or p["sample_map"].get(clip_id, {}).get(sample["id"])
                       if binding["motion"] == index["motion"] else p.get("pose_map", {}).get(pose)) for p in pieces]
        if any(placement is None for _, placement in placements):
            missing.append(pose)
            continue
        if config["mode"] == "held":
            placement = placements[0][1]
            image = assets.load_image(reader, placement["image"])
            record_placement = {"format": "local", "pivot": placement["pivot"], "target_socket": placement["target_socket"],
                                "offset": placement["offset"]}
            full = image
        else:
            full = Image.new("RGBA", canvas)
            for _, placement in placements:
                if not placement["visible"]:
                    continue
                offset = [t - p + d for t, p, d in zip(sample["sockets"][placement["target_socket"]]["position"],
                                                       placement["pivot"], placement["offset"])]
                layer = Image.new("RGBA", canvas)
                layer.paste(assets.load_image(reader, placement["image"]), tuple(offset))
                full.alpha_composite(layer)
            record_placement = {"format": "full-canvas", "pivot": list(sample["sockets"][config["socket"]]["position"]),
                                "target_socket": config["socket"], "offset": [0, 0]}
        alpha = np.asarray(full.convert("RGBA"))[..., 3] > 0
        record = {"kind": "parts-production-candidate", "schema_version": 1, "policy": POLICY, "part": part,
                  "pose_id": pose, "clip": clip_id, "sample": sample["id"], "motion": index["motion"],
                  "body_sha256": sample["image"]["sha256"], "under_sha256": None, "wearing": [],
                  "method": {"adopted": contract.reference(document)}, "source": None, "placement": record_placement,
                  "alignment": None, "holes_filled": 0, "pixels": int(alpha.sum())}
        record, location = _publish_candidate(root, part, record, {
            "mask": Image.fromarray((alpha*255).astype(np.uint8), "L"), "part": full.convert("RGBA")})
        _select(production, pose, location)
        made.append(pose)
    _save(root, production)
    return {"part": part, "adopted": len(made), "missing": missing, "release_ready": False}


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


def check(assembly, part):
    """Per-pose checks of each selected drawing; nothing is approved here."""
    root, index, registry, _ = parts_motion._active(assembly)
    production = _load(root, part)
    config = production["config"]
    motion = registry[index["motion"]["id"]]
    states = _states(production, index)
    order, _ = _poses(motion, states)
    near = _neighbours(motion, states)
    colors = _palette(root, config)
    area, rows = {}, {}
    for pose in order:
        row = production["poses"].get(pose)
        if row is None or row["selected"] is None:
            continue
        base = root / row["selected"]
        record = workspace.decode_json((base / "candidate.json").read_bytes())
        part_image = np.asarray(Image.open(base / "part.png").convert("RGBA"))
        mask = part_image[..., 3] > 0
        area[pose] = int(mask.sum())
        metrics = {"pixels": area[pose], "alignment": record["alignment"],
                   "specks": _islands(mask, 4), "holes": _pinholes(mask)}
        if colors is not None and mask.any():
            pixels = part_image[mask][:, :3]
            metrics["off_palette"] = int((np.abs(pixels[:, None, :].astype(int) - colors[None].astype(int)).max(-1).min(-1) > 0).sum())
        if "drawn" in record["images"] and "under" in record["images"]:
            # What the drawing changed but the part left out: a cut too tight (a hood off the back).
            drawn = Image.open(base / "drawn.png").convert("RGBA")
            changed = np.asarray(sheets.cover(drawn, Image.open(base / "under.png").convert("RGBA")))[..., 3] > 0
            metrics["dropped"] = int((changed & ~mask).sum())
        rows[pose] = metrics
    for pose, metrics in rows.items():
        others = [area[p] for p in near.get(pose, ()) if p in area]
        if others and metrics["pixels"]:
            reference = float(np.median(others))
            metrics["area_jump"] = round(abs(metrics["pixels"] - reference) / max(reference, 1), 3)
        findings = []
        if metrics["pixels"] == 0:
            findings.append(("warning", "empty: nothing drawn for this pose (hidden, or lost in the cut)"))
        if metrics["alignment"] is not None and metrics["alignment"] < LIMITS["alignment"]:
            findings.append(("error", f"alignment {metrics['alignment']}: the drawn figure does not sit on the body; redraw"))
        if metrics.get("area_jump", 0) > LIMITS["area_jump"]:
            findings.append(("warning", f"area differs {metrics['area_jump']:.0%} from neighbouring poses"))
        if metrics.get("dropped", 0) > max(8, LIMITS["dropped_share"]*metrics["pixels"]):
            findings.append(("warning", f"{metrics['dropped']} drawn pixels left out by the cut; loosen it or mask"))
        if metrics["specks"] > LIMITS["specks"]:
            findings.append(("warning", f"{metrics['specks']} specks under 4 px"))
        if metrics["holes"] > LIMITS["holes"]:
            findings.append(("warning", f"{metrics['holes']} px of pinholes (gaps of 12 px or less inside the part)"))
        if metrics.get("off_palette"):
            findings.append(("warning", f"{metrics['off_palette']} px off the palette"))
        status = "error" if any(level == "error" for level, _ in findings) else "warning" if findings else "passed"
        production["poses"][pose]["check"] = {"status": status, "metrics": metrics,
                                              "findings": [message for _, message in findings]}
    _save(root, production)
    summary = {s: sum(1 for p in rows if production["poses"][p]["check"]["status"] == s) for s in ("passed", "warning", "error")}
    return {"part": part, "checked": len(rows), "summary": summary,
            "findings": {p: production["poses"][p]["check"]["findings"] for p in rows if production["poses"][p]["check"]["findings"]},
            "release_ready": False}


def build(assembly, part, *, allow_incomplete=False):
    """Selected drawings -> a pose_map piece -> pieces by body segment -> imported pack."""
    root, index, registry, _ = parts_motion._active(assembly)
    production = _load(root, part)
    config = production["config"]
    motion = registry[index["motion"]["id"]]
    rig = registry[index["rig"]["id"]]
    order, _ = _poses(motion, _states(production, index))
    missing = [p for p in order if production["poses"].get(p, {}).get("selected") is None]
    if missing and not allow_incomplete:
        raise contract.PartsContractError(f"{part}: poses without a drawing: {missing}")
    folder = _folder(root, part) / "build"
    pose_map = {}
    for pose in order:
        row = production["poses"].get(pose)
        if row is None or row["selected"] is None:
            continue
        record = workspace.decode_json((root / row["selected"] / "candidate.json").read_bytes())
        image = (root / row["selected"] / "part.png").relative_to(_folder(root, part)).as_posix()
        pose_map[pose] = {**record["placement"], "image": {"file": image, "sha256": record["images"]["part"]["sha256"]},
                          "visible": record["pixels"] > 0}
    piece = {"id": config["slot"], "slot": config["slot"], "node": config["node"], "mode": config["mode"], "pose_map": pose_map}
    spec = {"kind": "parts-pack-import", "schema_version": 1, "id": part, "design_id": config["design_id"], "role": "part",
            "style": rig["style"], "rig": index["rig"], "motion": index["motion"], "view": rig["view"],
            "binding_id": part + ".binding", "occupies": [config["slot"]], "states": _states(production, index),
            "pieces": [piece]}
    folder.mkdir(parents=True, exist_ok=True)
    atomic_write_text(folder / "import.json", workspace.json_bytes(spec).decode("utf-8"))
    spec_file, source = folder / "import.json", _folder(root, part)
    if config["pieces"]:
        split = parts_segments.split_piece(spec_file, root / config["segments"], folder / "split", piece=config["slot"],
                                           into=[(p["id"], p["node"], p["segments"]) for p in config["pieces"]],
                                           source_root=source, by_component=config["by_component"])
        spec_file, source = Path(split["spec"]), Path(split["source_root"])
    imported = parts_import.import_pack(assembly, spec_file, source, allow_incomplete=allow_incomplete)
    production = _load(root, part)
    production["built"] = {"part": imported["part"], "selection": {p: production["poses"][p]["selected"] for p in pose_map}}
    _save(root, production)
    return {"part": imported["part"], "poses": len(pose_map), "missing": missing,
            "pieces": [p["id"] for p in config["pieces"]] or [config["slot"]], "release_ready": False}


def _render_alone(root, index, registry, part_ref, states):
    """Each pose of the part on the body alone (no other selections), through the shared resolver."""
    catalog = registry[index["catalog"]["id"]]
    pack = registry[part_ref["id"]]
    slots = {piece["slot"] for binding in pack["bindings"] for piece in binding["pieces"]}
    selections = {slot["id"]: part_ref if slot["id"] in slots else None for slot in catalog["slots"]}
    appearance = contract.seal({"kind": "parts-appearance", "schema_version": 1, "id": "production.preview",
                                "rig": index["rig"], "motion": index["motion"], "catalog": index["catalog"],
                                "states": states, "selections": selections, "body": index["body"]})
    plan = parts_resolve.resolve(appearance, [d for d in registry.values() if d["kind"] != "parts-appearance"], complete=False)
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


def contact(frames, order, production, *, scale=3, columns=8, crop=None):
    """Poses side by side, each labelled with its check and decision."""
    first = next(iter(frames.values()))
    box = crop or first.getbbox() or (0, 0, *first.size)
    for image in frames.values():
        bounds = image.getbbox()
        if bounds and crop is None:
            box = (min(box[0], bounds[0]), min(box[1], bounds[1]), max(box[2], bounds[2]), max(box[3], bounds[3]))
    w, h = (box[2]-box[0])*scale, (box[3]-box[1])*scale
    rows = (len(order)+columns-1)//columns
    sheet = Image.new("RGBA", (w*columns, (h+14)*rows), (236, 236, 236, 255))
    draw = ImageDraw.Draw(sheet)
    colour = {"approved": (40, 140, 60, 255), "rejected": (200, 40, 40, 255)}
    for n, pose in enumerate(order):
        x, y = n % columns*w, n//columns*(h+14)
        sheet.alpha_composite(frames[pose].crop(box).resize((w, h), Image.Resampling.NEAREST), (x, y))
        row = production["poses"].get(pose, {})
        check = (row.get("check") or {}).get("status", "unchecked")
        draw.text((x+3, y+h), f"{pose} {check} {row.get('decision', '')}", fill=colour.get(row.get("decision"), (0, 0, 0, 255)))
    return sheet


def review(assembly, part, *, reviewer=None, approve=(), reject=(), note=None, contact_sheet=None):
    """Judge the built part on the body alone, pose by pose, and seal the result as a part review.

    `approve`/`reject` take pose IDs, or "all" for every checked pose without errors. A
    rejected pose goes back to the todo list of parts-part-sheet. The review is bound to
    the exact built pack, so it holds in any outfit that selects that pack.
    """
    root, index, registry, original_hash = parts_motion._active(assembly)
    production = _load(root, part)
    built = production["built"]
    if built is None or registry.get(part, {}).get("digest") != built["part"]["digest"]:
        raise contract.PartsContractError(f"{part} is not built from its current selection; run parts-part-build")
    if any(production["poses"][p]["selected"] != c for p, c in built["selection"].items()):
        raise contract.PartsContractError(f"{part}: selection changed since the build; run parts-part-build")
    states = _states(production, index)
    order, _ = _poses(motion := registry[index["motion"]["id"]], states)
    def expand(values):
        if list(values) == ["all"]:
            return [p for p in order if (production["poses"][p].get("check") or {}).get("status") in ("passed", "warning")]
        contract._members(values, order, "poses")
        return list(values)
    approved, rejected = expand(approve), expand(reject)
    if set(approved) & set(rejected):
        raise contract.PartsContractError("a pose cannot be approved and rejected at once")
    if (approved or rejected) and not reviewer:
        raise contract.PartsContractError("a decision needs --reviewer")
    for pose in approved:
        if (production["poses"][pose].get("check") or {}).get("status") in (None, "error"):
            raise contract.PartsContractError(f"{pose}: check it first, and fix errors before approving")
        production["poses"][pose].update(decision="approved", reviewer=reviewer, note=note)
    for pose in rejected:
        production["poses"][pose].update(decision="rejected", reviewer=reviewer, note=note)
    frames, plan = _render_alone(root, index, registry, built["part"], states)
    sheet = contact(frames, order, production)
    payloads = {"contact.png": assets.png_bytes(sheet)}
    checks = {f"pose.{p}": production["poses"][p]["decision"] for p in order}
    report = {p: {k: production["poses"][p].get(k) for k in ("selected", "decision", "check", "reviewer", "note")} for p in order}
    payloads["checks.json"] = workspace.json_bytes(report)
    status = "approved" if all(v == "approved" for v in checks.values()) else \
        "rejected" if any(v == "rejected" for v in checks.values()) else "unverified"
    subject = registry[part]
    inputs = [index["rig"], index["motion"], *index["body"], built["part"]]
    review_doc = contract.seal({"kind": "parts-review", "schema_version": 1, "id": "review.part." + part,
        "subject_digest": subject["digest"], "scope": "part", "status": status,
        "render_policy": parts_resolve.RENDER_POLICY, "qa_policy": POLICY, "checks": checks,
        "reviewer": reviewer, "note": note,
        "evidence": {"appearance": None, "subject": contract.reference(subject), "binding_id": None, "inputs": inputs,
                     "plan_digest": plan["digest"], "preview_digest": assets.sha256(payloads["contact.png"]),
                     "qa_digest": contract.content_digest(report),
                     "artifacts": [{"file": n, "sha256": assets.sha256(d)} for n, d in sorted(payloads.items())]}})
    payloads["review.json"] = workspace.json_bytes(review_doc)
    folder = assets.publish_object(root, "reviews", review_doc["id"], review_doc["digest"], payloads)
    parts_review._activate(assembly, review_doc, folder, original_hash, index, registry)
    _save(root, production)
    if contact_sheet is not None:
        Path(contact_sheet).parent.mkdir(parents=True, exist_ok=True)
        sheet.save(contact_sheet)
    return {"part": part, "review": contract.reference(review_doc), "status": status,
            "approved": sum(v == "approved" for v in checks.values()), "rejected": sorted(p for p in order if checks[f"pose.{p}"] == "rejected"),
            "unverified": sum(v == "unverified" for v in checks.values()),
            "contact": (folder / "contact.png").relative_to(root).as_posix(),
            "next": "parts-part-sheet draws the rejected poses again" if rejected else None, "release_ready": False}


def status(assembly, part=None):
    """Every production's poses: drawn, checked, decided; and the next step for each."""
    root, index, registry, _ = parts_motion._active(assembly)
    motion = registry[index["motion"]["id"]] if index["motion"] else None
    if motion is None:
        raise contract.PartsContractError("status needs an active motion")
    names = [part] if part else sorted(p.name for p in (root / "productions").glob("*") if (p / "production.json").exists())
    report = []
    for name in names:
        production = _load(root, name)
        order, _ = _poses(motion, _states(production, index))
        rows = production["poses"]
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
        built = production["built"]
        current = built is not None and registry.get(name, {}).get("digest") == built["part"]["digest"] and \
            all(rows.get(p, {}).get("selected") == c for p, c in built["selection"].items()) and \
            set(built["selection"]) == {p for p in order if rows.get(p, {}).get("selected")}
        steps = []
        if counts["missing"] or counts["rejected"]:
            steps.append(f"draw {counts['missing'] + counts['rejected']} poses: parts-part-sheet, draw, parts-part-slice")
        if counts["unchecked"]:
            steps.append("parts-part-check")
        if counts["errors"]:
            steps.append(f"fix {counts['errors']} poses with check errors (redraw or parts-part-mask)")
        if not current and not counts["missing"]:
            steps.append("parts-part-build")
        if current and counts["unverified"]:
            steps.append(f"review {counts['unverified']} poses: parts-part-review")
        report.append({"part": name, **counts, "built_current": current,
                       "state": "approved" if counts["approved"] == len(order) and current else "in-progress",
                       "next": steps})
    return {"motion": index["motion"], "productions": report, "release_ready": False}


def release_reviews(root, registry, appearance):
    """Approved part reviews for every selected pack that has a production; raises when one is missing.

    A part made through a production is released only once it is approved on the body alone,
    pose by pose; the appearance review still judges the combination.
    """
    catalog = registry[appearance["catalog"]["id"]]
    selected = contract.resolve_selections(appearance, catalog)
    refs = []
    for value in selected.values():
        for ref in contract._selected_refs(value):
            if not (_folder(root, ref["id"]) / "production.json").exists():
                continue
            review = registry.get("review.part." + ref["id"])
            if review is None or review["status"] != "approved" or review["subject_digest"] != ref["digest"]:
                raise contract.PartsContractError(f"release requires an approved part review of {ref['id']} (parts-part-review)")
            refs.append(contract.reference(review))
    return refs


def _poses_arg(value):
    return [p for p in value.split(",") if p]


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


def add_draw_arguments(parser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--part", required=True)
    parser.add_argument("--batch")
    parser.add_argument("--provider", help="default: the config's draw.provider")
    parser.add_argument("--model")
    parser.add_argument("--dry-run", action="store_true", help="print the commands without drawing")


def add_slice_arguments(parser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--part", required=True)
    parser.add_argument("--batch")
    parser.add_argument("--page", action="append", default=[], dest="pages", type=sheets._page_pair,
                        help="GUIDE=ART instead of the batch's own pages (repeatable)")


def add_mask_arguments(parser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--part", required=True)
    parser.add_argument("--pose", required=True)
    parser.add_argument("--mask", required=True, type=Path, help="rig-canvas grayscale image, white = part")


def add_adopt_arguments(parser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--part", required=True)
    parser.add_argument("--pack", required=True, type=Path, help="part.json of an existing pack")


def add_part_arguments(parser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--part", required=True)


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


def add_status_arguments(parser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--part")


def _runner(action):
    return lambda **kwargs: workspace._report(lambda: action(**kwargs))


run_init, run_sheet, run_draw, run_slice = _runner(init), _runner(sheet), _runner(draw), _runner(slice_batch)
run_mask, run_adopt, run_check, run_build = _runner(mask), _runner(adopt), _runner(check), _runner(build)
run_review, run_status, run_config = _runner(review), _runner(status), _runner(configure)
