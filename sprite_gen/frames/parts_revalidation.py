# SPDX-License-Identifier: Apache-2.0
"""Frozen per-frame revalidation evidence. Hints never select or approve art."""
from io import BytesIO
from pathlib import Path
import zipfile

from PIL import Image
from sprite_gen._deps import np
from sprite_gen.curate import parts_motion
from sprite_gen.frames import parts_production as production, parts_candidate_scene, parts_part_edit
from sprite_gen.spec import parts_assets as assets, parts_contract as contract, parts_workspace as workspace
from sprite_gen.spec.runio import atomic_write_text


def _folder(root, part, track):
    return root / "revalidation" / contract.content_digest({"part": part, "track": track or production.MAIN})


def _input_paths(root, config):
    paths = [config.get("segments"), *(config.get("prompt") or {}).get("refs", []),
             *(p for checks in config["pose_checks"].values() for p in checks.values())]
    if config.get("segments"):
        segments = workspace.decode_json(assets.SourceReader(root).read(config["segments"]))
        base = Path(config["segments"]).parent / "labels"
        paths.extend((base / clip / (sample + ".png")).as_posix() for clip, sample in segments.get("poses", {}).values())
    return sorted(set(p for p in paths if p))


def _fingerprint(root, index, registry, state, track):
    config = state["config"]
    reader = assets.SourceReader(root)
    return contract.content_digest({
        "config": config, "rig": index["rig"], "motion": index["motion"], "body": index["body"],
        "topology": registry[index["rig"]["id"]]["topology"],
        "selected": {p: r.get("selected") for p, r in production._track(state, track)["poses"].items()},
        "inputs": {p: assets.sha256(reader.read(p)) for p in _input_paths(root, config)},
    })


def latest(assembly, part, *, track=None):
    root, index, registry, _ = parts_motion._active(assembly)
    state = production._load(root, part)
    file = _folder(root, part, track) / "latest.json"
    if not file.exists():
        return None
    ref = workspace.decode_json(file.read_bytes())
    report = read(root, part, ref["id"], track=track)
    report["current"] = report["fingerprint"] == _fingerprint(root, index, registry, state, track)
    return report


def read(root, part, identity, *, track=None):
    if len(identity) != 64 or any(c not in "0123456789abcdef" for c in identity):
        raise contract.PartsContractError("invalid revalidation report id")
    report = workspace.decode_json((_folder(root, part, track) / identity / "report.json").read_bytes())
    if contract.content_digest({k: v for k, v in report.items() if k != "id"}) != identity:
        raise contract.PartsContractError("changed revalidation report")
    return report


def translation_hint(required, ink, *, radius=32):
    """Best *raw* coverage in a bounded integer move; prefer the smallest move.

    FFT correlation avoids searching thousands of full-resolution image pairs.
    Clipping is excluded. The proposed move must subsequently be tested in zmap.
    """
    if not required.any() or not ink.any():
        return None
    height, width = ink.shape
    crop_y, crop_x = np.nonzero(required | ink)
    box = np.s_[crop_y.min():crop_y.max() + 1, crop_x.min():crop_x.max() + 1]
    required_crop, ink_crop = required[box], ink[box]
    ch, cw = ink_crop.shape
    size = (1 << (2 * ch - 2).bit_length(), 1 << (2 * cw - 2).bit_length())
    correlation = np.fft.irfft2(np.fft.rfft2(required_crop, s=size) *
                               np.fft.rfft2(ink_crop[::-1, ::-1], s=size), s=size)
    ys, xs = np.nonzero(ink)
    best = (int((required & ink).sum()), 0, 0)
    for dy in range(max(-radius, -int(ys.min())), min(radius, height - 1 - int(ys.max())) + 1):
        for dx in range(max(-radius, -int(xs.min())), min(radius, width - 1 - int(xs.max())) + 1):
            score = int(round(correlation[ch - 1 + dy, cw - 1 + dx])) if abs(dy) < ch and abs(dx) < cw else 0
            if score > best[0] or score == best[0] and dx * dx + dy * dy < best[1] ** 2 + best[2] ** 2:
                best = score, dx, dy
    return {"dx": best[1], "dy": best[2], "radius": radius, "raw_overlap": best[0]}


def _regions(root, index, registry, config, pose, image, record, base):
    reader = assets.SourceReader(base)
    regions = {name: np.asarray(Image.open(BytesIO(reader.read(ref["file"], ref["sha256"]))).convert("L")) > 0
               for name in ("required", "protected", "allowed")
               if (ref := record["images"].get("qa_" + name))}
    if not regions:
        return {}, {}
    ink = np.asarray(image)[..., 3] > 0
    visible = np.ones(ink.shape, dtype=bool)
    if config["visibility"] == "zmap" and config["segments"]:
        visible = parts_candidate_scene.visibility(root, index, registry, config, pose, image,
                                                   record["placement"], required=regions.get("required"))
    elif config["visibility"] == "mask" and "render_mask" in record["images"]:
        ref = record["images"]["render_mask"]
        visible = np.asarray(Image.open(BytesIO(reader.read(ref["file"], ref["sha256"]))).convert("L")) > 0
    if "required" in regions:
        regions["required"] &= visible
    visible_ink = ink & visible
    errors = {name: (region & ~visible_ink if name == "required" else region & visible_ink
                     if name == "protected" else ~region & visible_ink) for name, region in regions.items()}
    return regions, errors


def prepare(assembly, part, *, track=None):
    root, index, registry, _ = parts_motion._active(assembly)
    state = production._load(root, part)
    config = state["config"]
    if config["role"] != "part":
        raise contract.PartsContractError("revalidation targets independent parts")
    if config["mode"] != "raster":
        raise contract.PartsContractError("frame revalidation currently requires raster parts; held parts need follow-placement inspection")
    rows = production._track(state, track)["poses"]
    motion = registry[index["motion"]["id"]]
    order, first = production._poses(motion, production._states(state, index))
    neighbours = {}
    for clip in motion["clips"]:
        if clip["state"] not in production._states(state, index):
            continue
        poses = [s["pose_id"] for s in clip["samples"]]
        for n, pose in enumerate(poses):
            near = neighbours.setdefault(pose, [])
            for other in (n - 1, n + 1):
                if 0 <= other < len(poses) or clip["loop"]:
                    target = poses[other % len(poses)]
                    if target != pose and target not in near:
                        near.append(target)
    context = (motion, index, registry, registry[registry[index["rig"]["id"]]["topology"]["id"]],
               production.compose_parts.AssetLoader(root, index))
    bodies, _ = production._render_alone(root, index, registry, None, production._states(state, index))
    records, files = [], {}
    def put(path, encoded):
        files[path] = encoded
        return {"file": path, "sha256": assets.sha256(encoded)}
    for pose in order:
        row = rows.get(pose, {})
        entry = {"pose": pose, "clip": first[pose][0], "neighbours": list(neighbours.get(pose, ())),
                 "candidate": row.get("selected"), "art_approved": False}
        records.append(entry)
        if not row.get("selected"):
            entry.update(status="missing", priority=4, next_step="create-part")
            continue
        record = production.candidate_record(root, state, pose, row["selected"], track=track)
        base = root / row["selected"]
        entry["candidate_digest"] = record["digest"]
        if record["motion"] != index["motion"]:
            entry.update(status="stale-motion", priority=4, next_step="refresh-reference")
            continue
        metrics, findings = production._measure(root, config, production._settings(config, track), row["selected"], context)
        entry.update(metrics=metrics, findings=[m for _, m in findings],
                     status="error" if any(s == "error" for s, _ in findings) else "warning" if findings else "passed")
        image = Image.open(base / record["images"]["part"]["file"]).convert("RGBA")
        regions, errors = _regions(root, index, registry, config, pose, image, record, base)
        entry["evidence"] = {name: put(f"{pose}/candidate-{name}.png", assets.SourceReader(base).read(ref["file"], ref["sha256"]))
                             for name, ref in record["images"].items()}
        entry["evidence"]["active_body"] = put(f"{pose}/active-body.png", assets.png_bytes(bodies[pose]))
        entry["source_body_matches_active"] = (
            "under" in record["images"] and Image.open(base / record["images"]["under"]["file"]).convert("RGBA").tobytes()
            == bodies[pose].convert("RGBA").tobytes())
        # QA coordinates belong to the candidate; do not resize them to the body.
        colored = np.zeros((image.height, image.width, 4), dtype="uint8")
        for name, error in errors.items():
            colored[error] = {"required": [255, 0, 90, 230], "protected": [255, 80, 0, 230],
                              "allowed": [0, 100, 255, 230]}[name]
        overlay = Image.fromarray(colored)
        entry["evidence"]["errors"] = put(f"{pose}/errors.png", assets.png_bytes(overlay))
        entry["criteria_registered"] = list(regions)
        guide_matches = {}
        for name, path in config["pose_checks"].get(pose, {}).items():
            if path:
                ref = record["images"].get("qa_" + name)
                current = np.asarray(Image.open(BytesIO(assets.SourceReader(root).read(path))).convert("L")) > 0
                frozen = np.asarray(Image.open(base / ref["file"]).convert("L")) > 0 if ref else None
                guide_matches[name] = frozen is not None and np.array_equal(current, frozen)
        entry["current_guides_match_frozen"] = guide_matches
        entry.update(priority=3 if entry["status"] == "error" else 2 if not regions else 1,
                     next_step="audit-guide-and-shape" if regions else "author-placement-guide")
        required = regions.get("required")
        if required is not None:
            total, missing = int(required.sum()), int(errors["required"].sum())
            entry["coverage"] = {"required_visible": total, "missing": missing,
                                  "missing_ratio": missing / total if total else None}
            if missing and total:
                hint = translation_hint(required, np.asarray(image)[..., 3] > 0)
                if hint and (hint["dx"] or hint["dy"]):
                    moved = parts_part_edit.translate_image(image, hint["dx"], hint["dy"])
                    after_regions, after_errors = _regions(root, index, registry, config, pose, moved, record, base)
                    hint.update(after_required=int(after_regions["required"].sum()),
                                after_missing=int(after_errors["required"].sum()),
                                after_forbidden=sum(int(e.sum()) for n, e in after_errors.items() if n != "required"),
                                provisional=True, applied=False)
                    # A move which hides required pixels is not a coverage improvement.
                    hint["usable"] = np.array_equal(after_regions["required"], required) and hint["after_missing"] < missing and hint["after_forbidden"] == 0
                    entry["translation"] = hint
                    if hint["usable"]:
                        entry["next_step"] = "preview-position-then-review-shape" if hint["after_missing"] == 0 else "position-helps-shape-still-unresolved"
                        entry["evidence"]["position_proposal"] = put(f"{pose}/position-proposal.png", assets.png_bytes(moved))
        if any(not matches for matches in guide_matches.values()) or not entry["source_body_matches_active"]:
            entry.update(priority=4, next_step="refresh-reference")
            if entry.get("translation"):
                entry["translation"]["usable"] = False
    for entry in records:
        others = [r["metrics"]["pixels"] for r in records if r["pose"] in entry["neighbours"] and "metrics" in r]
        if others and "metrics" in entry:
            entry["neighbour_area_delta"] = abs(entry["metrics"]["pixels"] - float(np.median(others))) / max(float(np.median(others)), 1)
    records.sort(key=lambda r: (-r["priority"], -(r.get("coverage", {}).get("missing_ratio") or 0),
                                -r.get("neighbour_area_delta", 0), r["pose"]))
    report = {"schema_version": 1, "part": part, "track": track or production.MAIN,
              "fingerprint": _fingerprint(root, index, registry, state, track),
              "motion": index["motion"], "rig": index["rig"], "body": index["body"],
              "frames": records, "art_approved": False, "provider_calls": 0,
              "policy": "Coverage hints move the entire part (both shoes/socks together) within 32px, not each limb. They are not visual verdicts and can worsen one side. Audit ownership guides, design, foot/limb angle and neighbouring frames before approval. No masks or body pixels are changed.",
              "inputs": {"config": put("config.json", workspace.json_bytes(config)),
                         "motion": put("motion.json", workspace.json_bytes(motion))}}
    for i, path in enumerate(_input_paths(root, config)):
        report["inputs"][f"source.{i}"] = put("source/" + path, assets.SourceReader(root).read(path))
    report["id"] = contract.content_digest(report)
    folder = _folder(root, part, track) / report["id"]
    folder.mkdir(parents=True, exist_ok=True)
    for file, encoded in files.items():
        path = folder / file
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_bytes() != encoded:
            raise contract.PartsContractError("changed frozen revalidation evidence")
        if not path.exists():
            path.write_bytes(encoded)
    atomic_write_text(folder / "report.json", workspace.json_bytes(report).decode())
    atomic_write_text(folder.parent / "latest.json", workspace.json_bytes({"id": report["id"]}).decode())
    return {**report, "current": True}


def archive(assembly, part, identity, *, track=None):
    root, _, _, _ = parts_motion._active(assembly)
    report = read(root, part, identity, track=track)
    base = _folder(root, part, track) / identity
    stream = BytesIO()
    reader = assets.SourceReader(base)
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as output:
        def put(file, encoded):
            info = zipfile.ZipInfo(file)
            info.compress_type = zipfile.ZIP_DEFLATED
            output.writestr(info, encoded)
        put("report.json", workspace.json_bytes(report))
        references = list(report["inputs"].values()) + [ref for row in report["frames"] for ref in row.get("evidence", {}).values()]
        for ref in references:
            put(ref["file"], reader.read(ref["file"], ref["sha256"]))
    return stream.getvalue()
