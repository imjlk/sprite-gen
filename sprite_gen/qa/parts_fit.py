# SPDX-License-Identifier: Apache-2.0
"""Import authored fit interfaces and measure the same layers used by playback.

Interfaces are pinned to part, binding and motion revisions. No anatomy, colour
threshold or automatic art approval is inferred here.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path
from PIL import Image
from sprite_gen._deps import np

from sprite_gen.compose import compose_parts
from sprite_gen.compose.parts_runtime import PartsPlayer
from sprite_gen.curate import parts_motion
from sprite_gen.qa import parts_fitting
from sprite_gen.spec import parts_assets as assets, parts_contract as contract, parts_workspace as workspace
from sprite_gen.spec.runio import atomic_write_text, publish_guard

_IMPORT = contract._record({"kind": contract._enum("parts-fitting-import"),
    "schema_version": contract._positive, "id": contract._id, "rig": contract._ref,
    "motion": contract._ref, "entries": contract._records(contract._fit_entry, nonempty=True)})


def import_fitting(assembly, spec, source_root):
    root, index, registry, original_hash = parts_motion._active(assembly)
    path = Path(spec).absolute()
    raw = assets.read_file(path.parent, path.name, limit=workspace.MAX_JSON_BYTES)
    request = _IMPORT(workspace.decode_json(raw), "fitting")
    if (request["schema_version"] != 1 or request["rig"] != index["rig"]
            or request["motion"] != index["motion"]):
        raise contract.PartsContractError("fitting version/rig/motion differs from active workspace")
    motion = registry[index["motion"]["id"]]
    if motion["freeze"] is None:
        raise contract.PartsContractError("fitting import requires a frozen motion")
    # Validate coordinates and references before decoding any source image.
    draft = contract.seal({**request, "kind": "parts-fitting"})
    contract.validate_bundle([*registry.values(), draft] if draft["id"] not in registry else
                            [d for d in registry.values() if d["id"] != draft["id"]
                             and d["kind"] not in {"parts-review", "parts-build-lock"}] + [draft])
    rig = registry[index["rig"]["id"]]
    size = (rig["canvas"]["width"], rig["canvas"]["height"])
    reader, payloads = assets.SourceReader(source_root), {}
    for entry in request["entries"]:
        for name in ("required", "protected", "allowed"):
            mask = entry[name]
            if mask is None:
                continue
            image = assets.load_image(reader, mask["image"], size=size)
            data = assets.png_bytes(image)
            filename = f"masks/{assets.sha256(data)}.png"
            payloads[filename] = data
            mask["image"] = {"file": filename, "sha256": assets.sha256(data)}
    for data in [raw, *reader.data.values()]:
        payloads[f"sources/{assets.sha256(data)}.bin"] = data
    doc = contract.seal({**request, "kind": "parts-fitting",
        "source_assets": [{"file": name, "sha256": assets.sha256(data)}
                          for name, data in sorted(payloads.items()) if name.startswith("sources/")]})
    old = registry.get(doc["id"])
    if old is not None and old["kind"] != "parts-fitting":
        raise contract.PartsContractError("fitting ID conflicts with another document kind")
    payloads["fitting.json"] = workspace.json_bytes(doc)
    folder = assets.publish_object(root, "fitting", doc["id"], doc["digest"], payloads)
    next_index = deepcopy(index)
    removed = set()
    if old is None or old["digest"] != doc["digest"]:
        # All reviews consume the active fitting policy, including reports
        # created before fit declarations were introduced.
        removed = {d["id"] for d in registry.values() if d["kind"] in {"parts-review", "parts-build-lock"}}
    next_index["documents"] = {name: file for name, file in index["documents"].items() if name not in removed}
    next_index["documents"][doc["id"]] = (folder / "fitting.json").relative_to(root).as_posix()
    next_index["fitting"] = [r for r in index["fitting"] if r["id"] != doc["id"]] + [contract.reference(doc)]
    contract.validate_bundle([d for name, d in registry.items() if name not in removed and name != doc["id"]] + [doc])
    with publish_guard(root):
        current = assets.read_file(root, Path(assembly).name, limit=workspace.MAX_JSON_BYTES)
        if assets.sha256(current) != original_hash:
            raise contract.PartsContractError("assembly changed; fitting object preserved, retry activation")
        atomic_write_text(root / Path(assembly).name, workspace.json_bytes(next_index).decode("utf-8"))
    return {"fitting": contract.reference(doc), "file": next_index["documents"][doc["id"]],
            "entries": len(doc["entries"]), "invalidated": sorted(removed), "release_ready": False}


def index_entries(documents):
    indexed = {}
    for doc in documents:
        for entry in doc["entries"]:
            key = (entry["part"]["id"], entry["piece"], entry["clip"], entry["sample"])
            if key in indexed:
                raise contract.PartsContractError("multiple active fit declarations for the same part/piece/sample")
            indexed[key] = (doc, entry)
    return indexed


def check_sample(plan, clip, sample, load, indexed, *, only_parts=None):
    """Inspect final visible part pixels, including every mask in the scene."""
    if not indexed:
        return []
    size = (plan["canvas"]["width"], plan["canvas"]["height"])
    body, _ = compose_parts.render_sample(plan, sample, load,
        only_ids={e["id"] for e in sample["draw"] if e["role"] == "body"})
    results = []
    for draw in sample["draw"]:
        if draw["role"] == "body":
            continue
        if only_parts is not None and draw["pack"]["id"] not in only_parts:
            continue
        key = (draw["pack"]["id"], draw["piece_id"], clip["id"], sample["id"])
        identity = {"part": draw["pack"], "piece": draw["piece_id"], "slot": draw["slot"],
                    "clip": clip["id"], "state": clip["state"], "sample": sample["id"]}
        declaration = indexed.get(key)
        if declaration is None:
            results.append({**identity, "status": "error", "checks": [{"id": "missing-interface",
                "status": "error", "value": "missing", "limit": "authored fit declaration",
                "repair_action": "author-fit-interface"}], "art_approved": False, "release_ready": False})
            continue
        doc, entry = declaration
        if entry["part"] != draw["pack"] or entry["binding"] != draw["binding_id"]:
            raise contract.PartsContractError("fitting declaration differs from selected part revision")
        declaration_rules = deepcopy(entry["rules"])
        if plan["mirror"]:
            def reflected(rect):
                return [size[0]-rect[2], rect[1], size[0]-rect[0], rect[3]]
            declaration_rules["bounds"] = reflected(declaration_rules["bounds"])
            if declaration_rules["gap_region"] is not None:
                declaration_rules["gap_region"] = reflected(declaration_rules["gap_region"])
            for name in ("required_cover", "keep_clear"):
                declaration_rules[name] = [reflected(rect) for rect in declaration_rules[name]]
        rules = parts_fitting.FitRules(canvas=size, **declaration_rules)
        masks = {}
        for name, argument in (("required", "required_mask"), ("protected", "protected_mask"), ("allowed", "allowed_mask")):
            if entry[name] is not None:
                mask = entry[name]
                masks[argument] = compose_parts._coverage(load(contract.reference(doc), mask["image"]), mask["channel"])
                if plan["mirror"]:
                    masks[argument] = masks[argument].transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        part, _ = compose_parts.render_sample(plan, sample, load, only_ids={draw["id"]})
        measured = parts_fitting.measure(body, part, rules, **masks)
        results.append({**identity, "declaration": contract.reference(doc), "entry": entry["id"],
                        "bounds": declaration_rules["bounds"], **measured})
    return results


def summarize(results, *, declared=True):
    failed = [row for row in results if row["status"] == "error"]
    return {"status": "error" if failed else "passed" if declared else "unverified",
            "checked": len(results), "failed": len(failed), "art_approved": False,
            "release_ready": False}


def inspect(plan, load, documents):
    documents = tuple(documents)
    indexed = index_entries(documents)
    results = [row for clip in plan["clips"] for sample in clip["samples"]
               for row in check_sample(plan, clip, sample, load, indexed)]
    report = {"kind": "parts-fitting-report", "schema_version": 1,
        "appearance": plan["appearance"], "motion": plan["motion"], "fit_policy": parts_fitting.FIT_POLICY,
        "declarations": [contract.reference(d) for d in documents],
        **summarize(results, declared=bool(indexed)), "results": results}
    report["digest"] = contract.content_digest(report)
    checks = [{"id": "authored-fitting", "status": report["status"],
               "message": "measured authored interfaces; visual quality requires explicit review",
               "checked": report["checked"], "failed": report["failed"]}]
    checks += [{"id": f"fit.{i}", "status": row["status"], "part": row["part"],
                "piece": row["piece"], "clip": row["clip"], "sample": row["sample"],
                "failed_checks": [c for c in row["checks"] if c["status"] == "error"]}
               for i, row in enumerate(results) if row["status"] == "error"]
    return report, checks


def repair_plan(assembly, part, out, *, radius=2, patch_gaps=False):
    """Create a part-import proposal; activation is a separate existing command."""
    root, index, registry, _ = parts_motion._active(assembly)
    if type(radius) is not int or not 0 <= radius <= 3:
        raise contract.PartsContractError("translation repair radius must be between 0 and 3 pixels")
    output = Path(out).absolute().resolve()
    if output == root or root.is_relative_to(output) or (output.is_relative_to(root)
            and output.relative_to(root).parts[0] not in {"plans", "candidates"}):
        raise contract.PartsContractError("repair output must be external or under workspace plans/ or candidates/")
    pack = registry.get(part)
    if pack is None or pack["kind"] != "parts-pack" or pack["role"] != "part":
        raise contract.PartsContractError("repair plan requires an indexed customization part")
    if not index["fitting"]:
        raise contract.PartsContractError("repair plan requires authored fit interfaces")
    appearance = registry[index["appearance"]["id"]] if index["appearance"] else None
    if appearance is None:
        raise contract.PartsContractError("repair plan requires an active appearance")
    rig = registry[index["rig"]["id"]]
    bindings = [b for b in pack["bindings"] if b["rig"] == index["rig"] and b["motion"] == index["motion"]]
    if len(bindings) != 1:
        raise contract.PartsContractError("repair requires the exact active rig/motion binding")
    binding = bindings[0]
    load = compose_parts.AssetLoader(root, index)
    player = PartsPlayer(appearance, registry.values(), load)
    player.set_selection({piece["slot"]: contract.reference(pack) for piece in binding["pieces"]})
    indexed = index_entries([registry[ref["id"]] for ref in index["fitting"]])
    pieces = deepcopy(binding["pieces"])
    proposed = {piece["id"]: piece for piece in pieces}
    attempts, changed, unresolved, patched = [], [], [], {}
    for clip in player.plan["clips"]:
        for sample in clip["samples"]:
            initial = check_sample(player.plan, clip, sample, load, indexed)
            for row in initial:
                if row["part"]["id"] != part or row["status"] != "error":
                    continue
                render_id = f"{part}.{row['piece']}"
                draw = next(e for e in sample["draw"] if e["id"] == render_id)
                image = assets.png_bytes(load(draw["pack"], draw["placement"]["image"]))

                def evaluate(candidate):
                    trial = deepcopy(sample)
                    target = next(e for e in trial["draw"] if e["id"] == render_id)
                    target["offset"] = [v + d for v, d in zip(target["offset"], candidate.offset)]
                    target["placement"]["offset"] = [v + d for v, d in zip(target["placement"]["offset"], candidate.offset)]
                    candidate_image = assets.image_bytes(candidate.image)
                    def candidate_load(owner, reference):
                        if owner == draw["pack"] and reference == draw["placement"]["image"]:
                            return candidate_image
                        return load(owner, reference)
                    measured = check_sample(player.plan, clip, trial, candidate_load, indexed, only_parts={part})
                    target_report = next(r for r in measured if r["part"]["id"] == part and r["piece"] == row["piece"])
                    # A failing target cannot be accepted. Defer the full
                    # collateral check until its own interface passes; accepted
                    # proposals still recheck every previously passing part.
                    if target_report["status"] == "error":
                        return target_report
                    measured = check_sample(player.plan, clip, trial, candidate_load, indexed)
                    previously_passed = {(r["part"]["id"], r["piece"]) for r in initial if r["status"] == "passed"}
                    collateral = [r for r in measured if (r["part"]["id"], r["piece"]) in previously_passed and r["status"] == "error"]
                    if collateral:
                        target_report["checks"].append({"id": "other-part-fit", "status": "error", "value": len(collateral),
                            "limit": 0, "repair_action": "preserve-other-part-fits"})
                        target_report["status"] = "error"
                    return target_report

                candidate, history = parts_fitting.translation_repair(parts_fitting.FitCandidate(part, image), evaluate, radius=radius)
                if candidate is None and patch_gaps and draw["mode"] == "raster" and not player.plan["mirror"]:
                    declaration = indexed.get((part, row["piece"], clip["id"], sample["id"]))
                    if declaration is not None:
                        owner, entry = declaration
                        size = (player.plan["canvas"]["width"], player.plan["canvas"]["height"])
                        required = parts_fitting._region_mask(entry["rules"]["required_cover"], size, (0, 0))
                        protected = parts_fitting._region_mask(entry["rules"]["keep_clear"], size, (0, 0))
                        allowed = np.ones((size[1], size[0]), dtype=bool)
                        for name, target in (("required", required), ("protected", protected), ("allowed", allowed)):
                            if entry[name] is not None:
                                mask = entry[name]
                                values = np.asarray(compose_parts._coverage(load(contract.reference(owner), mask["image"]), mask["channel"])) >= 128
                                if name == "allowed":
                                    allowed &= values
                                else:
                                    target |= values
                        canvas, _ = compose_parts.render_sample(player.plan, sample, load, only_ids={render_id})
                        missing = required & (np.asarray(canvas)[..., 3] < 128)
                        if not np.any(missing & (protected | ~allowed)):
                            ys, xs = np.where(missing)
                            points = [(int(x)-draw["offset"][0], int(y)-draw["offset"][1]) for y, x in zip(ys, xs)]
                            proposal = parts_fitting.patch_declared_pixels(parts_fitting.FitCandidate(part, image), points)
                            if proposal is not None:
                                candidate, patch_history = parts_fitting.retry(parts_fitting.FitCandidate(part, image), evaluate,
                                    lambda current, failed, attempt: proposal if attempt == 1 else None)
                                history = {"translation": history, **patch_history}
                                if candidate is not None:
                                    patched[row["piece"], clip["id"], sample["id"]] = candidate.image
                identity = {"piece": row["piece"], "clip": clip["id"], "sample": sample["id"]}
                attempts.append({**identity, **history})
                if candidate is None:
                    unresolved.append(identity)
                else:
                    placement = proposed[row["piece"]]["sample_map"][clip["id"]][sample["id"]]
                    placement["offset"] = [v + d for v, d in zip(placement["offset"], candidate.offset)]
                    changed.append({**identity, "delta": list(candidate.offset)})
    candidate_root = output.with_suffix("").with_name(output.stem + "-source")
    if patched:
        # Copy exact existing inputs into a proposal source folder. The runtime
        # never reads the authoring workspace or changes the approved source.
        reader = assets.SourceReader((root / index["documents"][part]).parent)
        refs = []
        for piece in pieces:
            refs.extend(piece["palette_regions"].values())
            for mapping in piece["sample_map"].values():
                for placement in mapping.values():
                    refs.append(placement["image"])
                    refs.extend(placement["palette_regions"].values())
                    if placement["mask"] is not None:
                        refs.append(placement["mask"]["image"])
        for reference in refs:
            target = assets.safe_path(candidate_root, reference["file"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(reader.read(reference["file"], reference["sha256"]))
        for (piece_id, clip_id, sample_id), data in patched.items():
            filename = f"patched/{assets.sha256(data)}.png"
            target = candidate_root / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            proposed[piece_id]["sample_map"][clip_id][sample_id]["image"] = {"file": filename, "sha256": assets.sha256(data)}
    for piece in pieces:
        for mapping in piece["sample_map"].values():
            for placement in mapping.values():
                placement.pop("source", None)
                placement.update(format="local", crop_offset=[0, 0])
    spec = {"kind": "parts-pack-import", "schema_version": 1, "id": pack["id"], "design_id": pack["design_id"],
        "role": "part", "body_mode": pack["body_mode"], "style": rig["style"], "rig": index["rig"],
        "motion": index["motion"], "view": binding["view"], "binding_id": binding["id"],
        "states": index["states"], "pieces": pieces,
        **{key: binding[key] for key in ("occupies", "requires", "excludes", "mirror_safe")}}
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(output, workspace.json_bytes(spec).decode("utf-8"))
    report = {"kind": "parts-repair-plan", "schema_version": 1, "part": contract.reference(pack),
        "motion": index["motion"], "radius": radius, "changed": changed, "unresolved": unresolved,
        "attempts": attempts, "body_and_motion": "preserve", "activation": "not-applied",
        "patched_samples": len(patched), "art_approved": False, "release_ready": False}
    atomic_write_text(output.with_suffix(".repair.json"), workspace.json_bytes(report).decode("utf-8"))
    owner = candidate_root if patched else (root / index["documents"][part]).parent
    return {"proposal": str(output), "source_root": str(owner), "changed_samples": len(changed),
            "unresolved_samples": len(unresolved), "report": str(output.with_suffix(".repair.json")), "release_ready": False}


def add_repair_arguments(parser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--part", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--radius", type=int, default=2)
    parser.add_argument("--patch-gaps", action="store_true", help="also propose at most 12 explicitly uncovered local pixels; raster pieces only")


def run_repair(**kwargs):
    return workspace._report(lambda: repair_plan(**kwargs))


def add_arguments(parser: argparse.ArgumentParser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)


def run(**kwargs):
    return workspace._report(lambda: import_fitting(**kwargs))
