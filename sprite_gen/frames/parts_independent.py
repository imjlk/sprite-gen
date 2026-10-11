# SPDX-License-Identifier: Apache-2.0
"""Pose/layer jobs feeding the existing production, review and pack builders.

Each image is an independent layer. Body pixels are references only. No body
difference extraction, segmentation propagation, or appearance video is used.
"""

from copy import deepcopy
from fractions import Fraction
from io import BytesIO
from pathlib import Path
import subprocess
import sys

from PIL import Image, ImageDraw
from sprite_gen._deps import np
from sprite_gen.compose import parts_resolve, compose_parts
from sprite_gen.curate import parts_motion
from sprite_gen.frames import parts_production as p
from sprite_gen.frames import parts_reference
from sprite_gen.spec import (
    parts_assets as assets,
    parts_contract as c,
    parts_workspace as w,
)


def body_ownership_errors(root, index, registry, config, pose, *, layers=None):
    """Required body interfaces must belong to the declared anatomical nodes.

    A stray heel assigned to torso can occlude perfectly good footwear in zmap.
    Detect that authoring defect before asking an image model to repair the part.
    """
    path = config["pose_checks"].get(pose, {}).get("required")
    if not path or not config.get("independent"):
        return {}
    guide = (
        np.asarray(
            Image.open(BytesIO(assets.SourceReader(root).read(path))).convert("L")
        )
        >= 16
    )
    plan = p._part_plan(index, registry, None, config["states"] or index["states"])
    _, first = p._poses(
        registry[index["motion"]["id"]], config["states"] or index["states"]
    )
    clip_id, source = first[pose]
    sample = next(
        s
        for clip in plan["clips"]
        if clip["id"] == clip_id
        for s in clip["samples"]
        if s["id"] == source["id"]
    )
    loader = compose_parts.AssetLoader(root, index)
    body = Image.new("RGBA", (guide.shape[1], guide.shape[0]))
    by_node = {}
    for draw in sample["draw"]:
        if draw["role"] != "body":
            continue
        image = loader(draw["pack"], draw["placement"]["image"])
        body.alpha_composite(image, tuple(draw["offset"]))
        node = by_node.setdefault(draw["node"], Image.new("RGBA", body.size))
        node.alpha_composite(image, tuple(draw["offset"]))
    required = guide & (np.asarray(body)[..., 3] >= 16)
    targets = {item["id"]: item["target"] for item in layers or []}
    errors = {}
    for layer in config["independent"]["layers"]:
        if not layer["body_nodes"]:
            continue
        owned = np.zeros(guide.shape, dtype=bool)
        for node in layer["body_nodes"]:
            if node in by_node:
                owned |= np.asarray(by_node[node])[..., 3] >= 16
        x1, y1, x2, y2 = targets.get(layer["id"], layer["targets"][pose])
        missing = int((required[y1:y2, x1:x2] & ~owned[y1:y2, x1:x2]).sum())
        if missing:
            errors[layer["id"]] = missing
    return errors


def validate(config, index, registry):
    direct = config["independent"]
    if (
        config["role"] != "part"
        or config["mode"] != "raster"
        or config["pieces"]
        or config["variants"]
        or config["segments"]
    ):
        raise c.PartsContractError(
            "independent generation requires a raster part with explicit layers, without segment cuts or variants"
        )
    if config["visibility"] != "zmap":
        raise c.PartsContractError(
            "independent generation uses zmap; author exceptional masks in a separate masked production"
        )
    topology = registry[registry[index["rig"]["id"]]["topology"]["id"]]
    slot = next((s for s in topology["slots"] if s["id"] == config["slot"]), None)
    if slot is None or not direct["layers"]:
        raise c.PartsContractError(
            "independent generation needs a registered slot and at least one layer"
        )
    ids = [layer["id"] for layer in direct["layers"]]
    if len(ids) != len(set(ids)):
        raise c.PartsContractError("independent layer IDs must be unique")
    order, _ = p._poses(
        registry[index["motion"]["id"]], config["states"] or index["states"]
    )
    canvas = registry[index["rig"]["id"]]["canvas"]
    width, height = canvas["width"], canvas["height"]
    for layer in direct["layers"]:
        c._members(
            layer["body_nodes"],
            {n["id"] for n in topology["nodes"]},
            "reference body nodes",
        )
        c._members(
            [layer["node"]],
            slot["nodes"] or [n["id"] for n in topology["nodes"]],
            "independent node",
        )
        c._members(layer["targets"], order, "target poses")
        c._members(layer["source_bounds"], layer["targets"], "source bounds poses")
        c._members(layer["landmarks"], layer["targets"], "landmark poses")
        c._members(layer["guides"], layer["targets"], "guide poses")
        for points in layer["landmarks"].values():
            for name, point in points.items():
                c._id(name, "landmark name")
                if (
                    not 0 <= float(point[0]) < width
                    or not 0 <= float(point[1]) < height
                ):
                    raise c.PartsContractError(
                        "landmarks must lie on the native body canvas"
                    )
        try:
            margin = Fraction(layer["margin"])
        except (ValueError, ZeroDivisionError) as exc:
            raise c.PartsContractError("fit margin must be a rational number") from exc
        if not 1 <= margin <= Fraction(11, 10):
            raise c.PartsContractError("fit margin must be within 1..1.1")
        for box in layer["targets"].values():
            a, b, x, y = box
            if not 0 <= a < x <= canvas["width"] or not 0 <= b < y <= canvas["height"]:
                raise c.PartsContractError(
                    "independent target must be inside the rig canvas"
                )


def read_reference(root, ref):
    data = assets.SourceReader(root).read(ref["file"], ref["sha256"])
    doc = w.decode_json(data)
    if (
        doc.get("kind") != "parts-independent-reference"
        or c.content_digest({k: v for k, v in doc.items() if k != "digest"})
        != doc["digest"]
    ):
        raise c.PartsContractError("changed independent generation reference")
    base = Path(ref["file"]).parent
    reader = assets.SourceReader(root / base)
    for file in doc["files"].values():
        reader.read(file["file"], file["sha256"])
    return doc, base


def prepare(assembly, part, wanted, *, track=None, layers=None):
    root, index, registry, _ = parts_motion._active(assembly)
    state = p._load(root, part)
    config = state["config"]
    rows = p._track(state, track)
    validate(config, index, registry)
    layer_ids = {layer["id"] for layer in config["independent"]["layers"]}
    if layers is not None:
        if not layers:
            raise c.PartsContractError("select at least one layer to redraw")
        c._members(layers, layer_ids, "redraw layers")
    _, first = p._poses(registry[index["motion"]["id"]], p._states(state, index))
    for layer in config["independent"]["layers"]:
        if set(wanted) - layer["targets"].keys():
            raise c.PartsContractError(
                f"{layer['id']}: author target bounds for {sorted(set(wanted) - layer['targets'].keys())}"
            )
    context = {k: index[k] for k in ("rig", "motion", "body")}
    settings = p._settings(config, track)
    notes = p._redo_notes(state, rows, wanted, list(first))
    payloads = {}
    files = {}

    def keep(name, data):
        payloads[name] = data
        files[name] = {"file": name, "sha256": assets.sha256(data)}
        return name

    frames, _ = p._render_alone(root, index, registry, None, p._states(state, index))
    body_plan = p._part_plan(index, registry, None, p._states(state, index))
    body_samples = {
        (clip["id"], sample["id"]): sample
        for clip in body_plan["clips"]
        for sample in clip["samples"]
    }
    body_loader = compose_parts.AssetLoader(root, index)
    design = []
    for n, path in enumerate((settings["prompt"] or {}).get("refs", [])):
        design.append(
            keep(f"design/{n}{Path(path).suffix}", assets.SourceReader(root).read(path))
        )
    jobs = []
    for pose in wanted:
        if errors := body_ownership_errors(root, index, registry, config, pose):
            raise c.PartsContractError(
                f"{pose}: required body pixels belong to other nodes {errors}; repair body ownership before generating parts"
            )
        body = frames[pose]
        body_file = keep(f"{pose}/body.png", assets.png_bytes(body))
        checks = {}
        for name, path in config["pose_checks"].get(pose, {}).items():
            if path:
                mask = Image.open(
                    BytesIO(assets.SourceReader(root).read(path))
                ).convert("L")
                if mask.size != body.size:
                    raise c.PartsContractError(
                        f"{pose}: {name} must match the native canvas"
                    )
                checks[name] = keep(f"{pose}/qa_{name}.png", assets.png_bytes(mask))
        for layer in config["independent"]["layers"]:
            target = layer["targets"][pose]
            a, b, x, y = target
            pad = max(16, round(max(x - a, y - b) * 0.25))
            roi = (
                max(0, a - pad),
                max(0, b - pad),
                min(body.width, x + pad),
                min(body.height, y + pad),
            )
            guide = body.crop(roi)
            factor = min(4, 1024 / max(guide.size))
            guide = (
                guide.convert("RGBa")
                .resize(
                    (round(guide.width * factor), round(guide.height * factor)),
                    Image.Resampling.BILINEAR,
                )
                .convert("RGBA")
            )
            guide_file = keep(
                f"{pose}/{layer['id']}.guide.png", assets.png_bytes(guide)
            )
            interface_files = []
            if checks:
                # Retaining QA files in the batch did not make them visible to
                # the image provider. Send a cropped anatomy/coverage overlay.
                masks = {name: Image.open(BytesIO(payloads[path])).convert("L")
                         for name, path in checks.items() if name in ("required", "protected")}
                if masks:
                    interfaces = parts_reference.overlay(body, masks).crop(roi)
                    interfaces = interfaces.convert("RGBa").resize(guide.size, Image.Resampling.BILINEAR).convert("RGBA")
                    interface_files.append(keep(f"{pose}/{layer['id']}.interfaces.png", assets.png_bytes(interfaces)))
            landmark_files = []
            points = layer["landmarks"].get(pose, {})
            if points:
                marked = guide.copy()
                drawing = ImageDraw.Draw(marked)
                positions = {
                    name: ((float(x) - roi[0]) * factor, (float(y) - roi[1]) * factor)
                    for name, (x, y) in points.items()
                }
                for name, (x, y) in positions.items():
                    drawing.ellipse(
                        (x - 5, y - 5, x + 5, y + 5),
                        fill=(240, 30, 80, 255),
                        outline=(255, 255, 255, 255),
                        width=2,
                    )
                    drawing.text(
                        (x + 7, max(0, y - 16)),
                        name,
                        fill=(240, 30, 80, 255),
                        stroke_width=2,
                        stroke_fill=(255, 255, 255, 255),
                    )
                if "heel" in positions and "toe" in positions:
                    drawing.line(
                        (*positions["heel"], *positions["toe"]),
                        fill=(240, 30, 80, 255),
                        width=3,
                    )
                landmark_files.append(
                    keep(
                        f"{pose}/{layer['id']}.landmarks.png", assets.png_bytes(marked)
                    )
                )
            ownership = []
            if layer["body_nodes"]:
                anatomy = Image.new("RGBA", body.size)
                sample = body_samples[first[pose][0], first[pose][1]["id"]]
                for draw in sample["draw"]:
                    if draw["role"] == "body" and draw["node"] in layer["body_nodes"]:
                        anatomy.alpha_composite(
                            body_loader(draw["pack"], draw["placement"]["image"]),
                            tuple(draw["offset"]),
                        )
                if not anatomy.getbbox():
                    raise c.PartsContractError(
                        f"{pose}/{layer['id']}: reference body nodes have no pixels"
                    )
                anatomy = (
                    anatomy.crop(roi)
                    .convert("RGBa")
                    .resize(guide.size, Image.Resampling.BILINEAR)
                    .convert("RGBA")
                )
                ownership.append(
                    keep(f"{pose}/{layer['id']}.anatomy.png", assets.png_bytes(anatomy))
                )
            shape_guides = [
                keep(
                    f"{pose}/{layer['id']}.shape-{n}.png",
                    assets.SourceReader(root).read(path),
                )
                for n, path in enumerate(layer["guides"].get(pose, []))
            ]
            previous = []
            selected = rows["poses"].get(pose, {}).get("selected")
            if selected and config["independent"]["reference_previous"]:
                prior = p.candidate_record(root, state, pose, selected, track=track)
                key = "raw." + layer["id"]
                if ref := prior["images"].get(key):
                    previous_image = Image.open(
                        BytesIO(
                            assets.SourceReader(root / selected).read(
                                ref["file"], ref["sha256"]
                            )
                        )
                    ).convert("RGBA")
                    previous_bounds = (
                        prior["method"]
                        .get("registration", {})
                        .get(layer["id"], {})
                        .get("source_bounds")
                    )
                    if previous_bounds:
                        previous_image = previous_image.crop(tuple(previous_bounds))
                    previous.append(
                        keep(
                            f"{pose}/{layer['id']}.previous.png",
                            assets.png_bytes(previous_image),
                        )
                    )
            prompt = (
                (settings["prompt"] or {}).get("text", "")
                + "\n\n"
                + f"Generate ONLY the independent {layer['id']} sprite layer for pose {pose}. "
                f"It belongs to render node {layer['node']}. The attached body crop is the exact pose reference. "
                "Match its angle, perspective, attachments, and art style. Draw the complete layer, including portions hidden by foreground limbs. "
                + (
                    "The anatomy reference isolates the exact BODY for this layer; it is geometry guidance, never output. It may appear as a labelled panel in the combined geometry board. Match its toe/heel or attachment direction instead of copying the old part's mistaken perspective. "
                    if ownership
                    else ""
                )
                + "Do not paint a character, skin, face, body, limbs, other garments, labels, guides or a background. "
                "Return one clean isolated asset on genuine transparent alpha, with the whole silhouette visible. "
                "Do not draw a sheet or a grid. Preserve the design from the attached design reference. "
                "Output framing is free; registration uses a single uniform scale and translation. "
                + (
                    "The interface overlay shows GREEN required coverage on the actual body and RED protected areas. Cover the full green contour including heel/toe boundaries; leave red areas free. These colours are inspection guidance, never paint them in the final part. "
                    if interface_files else ""
                )
                + (
                    f"Authored body landmarks: {points}. The marked guide labels the exact attachment/toe/heel direction. These markers are instructions for geometry ONLY; never draw labels or markers in the final asset. Design references determine materials, never override this pose direction. "
                    if points
                    else ""
                )
                + (
                    "The final reference is the previous generated part for this exact pose. Edit that source, preserve its design, and correct the stated defects. "
                    if previous
                    else ""
                )
                + notes
                + (
                    " The attached authored PART TEMPLATE defines this pose's silhouette, collar opening and visible material planes. Preserve that geometry; apply the requested finished illustration style. Template colours and labels are guidance, not output text. Do not substitute a generic shoe viewing angle."
                    if shape_guides
                    else ""
                )
            )
            prompt_file = keep(
                f"{pose}/{layer['id']}.prompt.txt", prompt.encode("utf-8")
            )
            reuse = None
            if layers is not None and layer["id"] not in layers:
                if not selected:
                    raise c.PartsContractError(
                        f"{pose}: other layers need a selected source to preserve"
                    )
                prior = p.candidate_record(root, state, pose, selected, track=track)
                if prior["motion"] != index["motion"]:
                    raise c.PartsContractError(
                        "preserved layer belongs to a stale motion"
                    )
                if not prior.get("independent_reference"):
                    raise c.PartsContractError(
                        "preserved layer needs independent production provenance"
                    )
                prior_reference, _ = read_reference(
                    root, prior["independent_reference"]
                )
                if any(
                    prior_reference["context"][key] != index[key]
                    for key in ("rig", "motion")
                ):
                    raise c.PartsContractError(
                        "preserved layer belongs to a stale rig or motion"
                    )
                old_body = prior["images"]["under"]
                old_pixels = Image.open(
                    BytesIO(
                        assets.SourceReader(root / selected).read(
                            old_body["file"], old_body["sha256"]
                        )
                    )
                ).convert("RGBA")
                if not np.array_equal(np.asarray(old_pixels), np.asarray(body)):
                    raise c.PartsContractError(
                        "preserved layer belongs to a different body image"
                    )
                key = "piece." + layer["id"]
                ref = prior["images"].get(key)
                if not ref:
                    raise c.PartsContractError(
                        f"{pose}: selected candidate lacks {layer['id']}"
                    )
                frozen_piece = keep(
                    f"{pose}/{layer['id']}.preserved.png",
                    assets.SourceReader(root / selected).read(
                        ref["file"], ref["sha256"]
                    ),
                )
                raw_ref = prior["images"].get("raw." + layer["id"], ref)
                frozen_raw = keep(
                    f"{pose}/{layer['id']}.preserved-raw.png",
                    assets.SourceReader(root / selected).read(
                        raw_ref["file"], raw_ref["sha256"]
                    ),
                )
                reuse = {
                    "image": frozen_piece,
                    "raw": frozen_raw,
                    "parent": prior["digest"],
                    "registration": prior["method"]
                    .get("registration", {})
                    .get(layer["id"], {}),
                }
            references = [guide_file, *ownership, *landmark_files, *interface_files,
                          *design, *shape_guides, *previous]
            if len(references) > 5:
                # Native image editing has a limited reference budget. Keep all
                # geometric evidence together instead of asking the child agent
                # to silently drop the body, markers or coverage reference.
                geometry = [("Exact body", guide_file)]
                geometry.extend(("Isolated anatomy", file) for file in ownership)
                geometry.extend(("Body landmarks", file) for file in landmark_files)
                geometry.extend(("Coverage / protection", file) for file in interface_files)
                board = Image.new("RGBA", (1280, 640 * ((len(geometry) + 1) // 2)), (235, 235, 235, 255))
                labels = ImageDraw.Draw(board)
                for n, (label, file) in enumerate(geometry):
                    panel = Image.open(BytesIO(payloads[file])).convert("RGBA")
                    panel.thumbnail((624, 600), Image.Resampling.BILINEAR)
                    x, y = n % 2 * 640, n // 2 * 640
                    board.alpha_composite(panel, (x + (640 - panel.width) // 2, y + 32 + (600 - panel.height) // 2))
                    labels.text((x + 8, y + 8), label, fill=(20, 20, 20))
                geometry_file = keep(f"{pose}/{layer['id']}.geometry.png", assets.png_bytes(board))
                references = [geometry_file, *design, *shape_guides, *previous]
                if len(references) > 5:
                    raise c.PartsContractError("too many independent design/template references; author combined reference boards before generation")
                prompt += " The FIRST reference is a labelled geometry board: exact body, isolated anatomy, landmarks and coverage panels are evidence for this same pose. Use every panel; never discard one to fit a reference limit."
                prompt_file = keep(f"{pose}/{layer['id']}.prompt.txt", prompt.encode("utf-8"))
            jobs.append(
                {
                    "pose": pose,
                    "layer": layer["id"],
                    "node": layer["node"],
                    "target": target,
                    "source_bounds": layer["source_bounds"].get(pose),
                    "fit": layer["fit"],
                    "margin": layer["margin"],
                    "body": body_file,
                    "guide": guide_file,
                    "prompt": prompt_file,
                    "refs": references,
                    "checks": checks,
                    "reuse": reuse,
                }
            )
    doc = {
        "kind": "parts-independent-reference",
        "schema_version": 1,
        "part": part,
        "context": context,
        "config": config,
        "jobs": jobs,
        "files": files,
        "replaces": {
            pose: rows["poses"].get(pose, {}).get("selected") for pose in wanted
        },
        "note": notes,
    }
    doc["digest"] = c.content_digest(doc)
    payloads["reference.json"] = w.json_bytes(doc)
    saved = assets.publish_object(
        p._folder(root, part), "independent-inputs", "inputs", doc["digest"], payloads
    )
    ref = {
        "file": (saved / "reference.json").relative_to(root).as_posix(),
        "sha256": assets.sha256(payloads["reference.json"]),
    }
    batch = "i" + doc["digest"][:12]
    folder = p._folder(root, part) / "batches" / batch
    pages = [
        {
            **job,
            "art": (folder / f"{job['pose']}.{job['layer']}.png")
            .relative_to(root)
            .as_posix(),
        }
        for job in jobs
    ]
    old = next((b for b in rows["batches"] if b["id"] == batch), {})
    entry = {
        "id": batch,
        "layout": "independent",
        "motion": index["motion"],
        "poses": wanted,
        "pages": pages,
        "reference": ref,
        "attempts": old.get("attempts", []),
        "prompt": None,
        "refs": [],
    }
    rows["batches"] = [b for b in rows["batches"] if b["id"] != batch] + [entry]
    p._save(root, state)
    return {
        "part": part,
        "batch": batch,
        "poses": wanted,
        "pages": pages,
        "reference": ref,
        "next": "parts-part-draw (or draw each isolated asset externally), then parts-part-slice",
        "art_approved": False,
        "release_ready": False,
    }


def draw(assembly, part, entry, *, provider, model, dry_run=False, track=None):
    root, _, _, _ = parts_motion._active(assembly)
    state = p._load(root, part)
    doc, base = read_reference(root, entry["reference"])
    results = []
    if [{k: v for k, v in job.items() if k != "art"} for job in entry["pages"]] != doc[
        "jobs"
    ]:
        raise c.PartsContractError("independent job inputs changed after preparation")
    for job in entry["pages"]:
        if job.get("reuse"):
            results.append(
                {"art": job["art"], "status": "preserved", "layer": job["layer"]}
            )
            continue
        output = root / job["art"]
        command = [
            sys.executable,
            "-m",
            "sprite_gen.cli",
            "gen",
            "--provider",
            provider,
            "--prompt-file",
            str(root / base / job["prompt"]),
            "--out",
            str(output),
            "--report",
            str(output.with_suffix(".report.json")),
            "--transparent",
        ]
        if provider == "codex":
            command += ["--alpha-mode", "native"]
        for ref in job["refs"]:
            command += ["--ref", str(root / base / ref)]
        if model:
            command += ["--model", model]
        if dry_run:
            results.append({"art": job["art"], "status": "planned", "command": command})
            continue
        if output.exists():
            results.append({"art": job["art"], "status": "exists"})
            continue
        output.parent.mkdir(parents=True, exist_ok=True)
        done = subprocess.run(command, capture_output=True, text=True)
        result = {
            "art": job["art"],
            "status": "drawn" if done.returncode == 0 else "failed",
            "provider": provider,
            "image_sha256": assets.sha256(output.read_bytes())
            if done.returncode == 0
            else None,
            "error": done.stderr[-400:] if done.returncode else None,
        }
        results.append(result)
        state = p._load(root, part)
        current = p._batch(p._track(state, track), entry["id"])
        current.setdefault("attempts", []).append(result)
        p._save(root, state)
        if done.returncode:
            break
    return {
        "part": part,
        "batch": entry["id"],
        "pages": results,
        "art_approved": False,
        "release_ready": False,
    }


def register(
    image, target, *, source_bounds=None, fit="contain", margin="1", smooth=True
):
    raw = image.convert("RGBA")
    if source_bounds:
        a, b, x, y = source_bounds
        if not 0 <= a < x <= raw.width or not 0 <= b < y <= raw.height:
            raise c.PartsContractError("source bounds exceed the generated image")
        raw = raw.crop(tuple(source_bounds))
    alpha = np.asarray(raw)[..., 3]
    # An opaque full character/background is never silently cut into a part.
    if not (alpha == 0).any() or (alpha > 0).mean() > 0.95:
        raise c.PartsContractError(
            "independent asset needs transparent surroundings; inspect or regenerate it"
        )
    yy, xx = np.nonzero(alpha >= 16)
    if not len(xx):
        raise c.PartsContractError("independent asset has no visible art")
    box = (
        max(0, int(xx.min()) - 4),
        max(0, int(yy.min()) - 4),
        min(raw.width, int(xx.max()) + 5),
        min(raw.height, int(yy.max()) + 5),
    )
    cut = raw.crop(box)
    a, b, x, y = target
    scale = (min if fit == "contain" else max)(
        (x - a) / cut.width, (y - b) / cut.height
    ) * float(Fraction(margin))
    size = (max(1, round(cut.width * scale)), max(1, round(cut.height * scale)))
    cut = (
        cut.convert("RGBa")
        .resize(size, Image.Resampling.BILINEAR if smooth else Image.Resampling.NEAREST)
        .convert("RGBA")
    )
    position = [round((a + x - cut.width) / 2), round((b + y - cut.height) / 2)]
    return (
        cut,
        position,
        {
            "source_bounds": source_bounds,
            "visible_bounds": box,
            "target": target,
            "fit": fit,
            "margin": margin,
            "uniform_scale": scale,
            "position": position,
            "sampling": "premultiplied-bilinear" if smooth else "nearest",
        },
    )


def slice_batch(assembly, part, entry, *, pages=(), track=None):
    root, index, registry, _ = parts_motion._active(assembly)
    state = p._load(root, part)
    rows = p._track(state, track)
    doc, base = read_reference(root, entry["reference"])
    if [{k: v for k, v in job.items() if k != "art"} for job in entry["pages"]] != doc[
        "jobs"
    ]:
        raise c.PartsContractError("independent job inputs changed after preparation")
    if doc["context"] != {k: index[k] for k in ("rig", "motion", "body")}:
        raise c.PartsContractError(
            "body/rig/motion changed; prepare new independent references"
        )
    if any(not str(n).isdigit() for n, _ in pages):
        raise c.PartsContractError("independent image pages use INDEX=PATH")
    supplied = {int(str(n)): path for n, path in pages}
    if set(supplied) - set(range(len(entry["pages"]))):
        raise c.PartsContractError("unknown independent image index")
    rig = registry[index["rig"]["id"]]
    canvas = (rig["canvas"]["width"], rig["canvas"]["height"])
    ranks = parts_resolve.layer_ranks(registry[rig["topology"]["id"]])
    made = []
    missing = []
    _, first = p._poses(registry[index["motion"]["id"]], p._states(state, index))
    for pose in entry["poses"]:
        selected = [(n, j) for n, j in enumerate(entry["pages"]) if j["pose"] == pose]
        if any(
            not j.get("reuse") and not Path(supplied.get(n, root / j["art"])).is_file()
            for n, j in selected
        ):
            missing.append(pose)
            continue
        images = {}
        layers = []
        registration = {}
        sources = []
        for n, job in selected:
            path = Path(supplied.get(n, root / job["art"])).absolute()
            ref = doc["files"][job["reuse"]["image"]] if job.get("reuse") else None
            encoded = (
                assets.SourceReader(root / base).read(ref["file"], ref["sha256"])
                if ref
                else path.read_bytes()
            )
            raw = Image.open(BytesIO(encoded)).convert("RGBA")
            native = assets.publish_object(
                p._folder(root, part),
                "native-art",
                "source",
                assets.sha256(encoded),
                {"source.image": encoded},
            )
            cut, position, details = (
                (
                    raw,
                    [0, 0],
                    {
                        **job["reuse"]["registration"],
                        "preserved_from": job["reuse"]["parent"],
                    },
                )
                if job.get("reuse")
                else register(
                    raw,
                    job["target"],
                    source_bounds=None
                    if any(
                        attempt.get("art") == job["art"]
                        and attempt.get("status") == "drawn"
                        and attempt.get("image_sha256") == assets.sha256(encoded)
                        for attempt in entry.get("attempts", [])
                    )
                    else job["source_bounds"],
                    fit=job["fit"],
                    margin=job["margin"],
                    smooth=rig.get("sampling") == "smooth",
                )
            )
            if (
                position[0] < 0
                or position[1] < 0
                or position[0] + cut.width > canvas[0]
                or position[1] + cut.height > canvas[1]
            ):
                raise c.PartsContractError(
                    f"{pose}/{job['layer']}: registration would clip the generated asset"
                )
            layer = Image.new("RGBA", canvas)
            layer.paste(cut, tuple(position))
            key = "piece." + job["layer"]
            images[key] = layer
            if job.get("reuse"):
                raw_ref = doc["files"][job["reuse"]["raw"]]
                images["raw." + job["layer"]] = Image.open(
                    BytesIO(
                        assets.SourceReader(root / base).read(
                            raw_ref["file"], raw_ref["sha256"]
                        )
                    )
                ).convert("RGBA")
            else:
                images["raw." + job["layer"]] = raw
            layers.append(
                {
                    "id": job["layer"],
                    "node": job["node"],
                    "image_key": key,
                    "target": job["target"],
                }
            )
            registration[job["layer"]] = details
            sources.append(
                {
                    "layer": job["layer"],
                    "sha256": assets.sha256(encoded),
                    "file": str(path),
                    "artifact": {
                        "file": (native / "source.image").relative_to(root).as_posix(),
                        "sha256": assets.sha256(encoded),
                    },
                    "provider_report": w.decode_json(
                        path.with_suffix(".report.json").read_bytes()
                    )
                    if path.with_suffix(".report.json").is_file()
                    else None,
                }
            )
        layers.sort(key=lambda item: (ranks.get(item["node"], 0), item["id"]))
        full = Image.new("RGBA", canvas)
        for item in layers:
            full.alpha_composite(images[item["image_key"]])
        body_ref = doc["files"][selected[0][1]["body"]]
        body = Image.open(
            BytesIO(
                assets.SourceReader(root / base).read(
                    body_ref["file"], body_ref["sha256"]
                )
            )
        ).convert("RGBA")
        images.update(part=full, under=body, mask=full.getchannel("A"))
        for name, key in selected[0][1]["checks"].items():
            ref = doc["files"][key]
            images["qa_" + name] = Image.open(
                BytesIO(
                    assets.SourceReader(root / base).read(ref["file"], ref["sha256"])
                )
            ).convert("L")
        clip, sample = first[pose]
        record = {
            "kind": "parts-production-candidate",
            "schema_version": 1,
            "policy": p.POLICY,
            "part": part,
            "track": track or p.MAIN,
            "pose_id": pose,
            "clip": clip,
            "sample": sample["id"],
            "motion": index["motion"],
            "body_sha256": sample["image"]["sha256"],
            "under_sha256": body_ref["sha256"],
            "wearing": [],
            "method": {"independent": True, "registration": registration},
            "independent_reference": entry["reference"],
            "independent_layers": layers,
            "source": sources,
            "placement": {
                "format": "full-canvas",
                "pivot": list(sample["sockets"][state["config"]["socket"]]["position"]),
                "target_socket": state["config"]["socket"],
                "offset": [0, 0],
            },
            "alignment": None,
            "holes_filled": 0,
            "pixels": int((np.asarray(full)[..., 3] > 0).sum()),
        }
        _, location = p._publish_candidate(root, part, record, images)
        p._select(rows, [pose], location)
        made.append(pose)
    p._save(root, state)
    return {
        "part": part,
        "batch": entry["id"],
        "sliced": len(made),
        "poses": made,
        "missing": missing,
        "art_approved": False,
        "release_ready": False,
    }


def candidate_layers(record, base):
    reader = assets.SourceReader(base)
    return [
        {
            **item,
            "image": Image.open(
                BytesIO(
                    reader.read(
                        record["images"][item["image_key"]]["file"],
                        record["images"][item["image_key"]]["sha256"],
                    )
                )
            ).convert("RGBA"),
        }
        for item in record.get("independent_layers", [])
    ]


def build_pieces(root, part, state, order):
    specs = []
    rows = state["tracks"][p.MAIN]["poses"]
    for layer in state["config"]["independent"]["layers"]:
        mapping = {}
        for pose in order:
            row = rows.get(pose)
            if not row or not row.get("selected"):
                continue
            record = p.candidate_record(root, state, pose, row["selected"])
            if not any(
                item["id"] == layer["id"] and item["node"] == layer["node"]
                for item in record.get("independent_layers", [])
            ):
                raise c.PartsContractError(
                    f"{pose}: independent layers changed; regenerate against the current config"
                )
            ref = record["images"]["piece." + layer["id"]]
            mapping[pose] = {
                **record["placement"],
                "image": {
                    "file": (root / row["selected"] / ref["file"])
                    .relative_to(p._folder(root, part))
                    .as_posix(),
                    "sha256": ref["sha256"],
                },
                "visible": True,
            }
        specs.append(
            {
                "id": layer["id"],
                "slot": state["config"]["slot"],
                "node": layer["node"],
                "mode": "raster",
                "pose_map": mapping,
            }
        )
    return specs


def edit_layers(root, state, row, location, record, images, operations, *, sampling):
    # A deliberate new edit candidate also freezes the currently authored QA
    # guides. The parent retains the former guides and artwork unchanged.
    guides_changed = False
    for name, path in state["config"]["pose_checks"].get(record["pose_id"], {}).items():
        if path:
            guard = Image.open(BytesIO(assets.SourceReader(root).read(path))).convert(
                "L"
            )
            if guard.size != images["part"].size:
                raise c.PartsContractError("QA guide must match the native canvas")
            key = "qa_" + name
            guides_changed |= key not in images or not np.array_equal(
                np.asarray(images[key].convert("L")), np.asarray(guard)
            )
            images[key] = guard
    from sprite_gen.frames.parts_part_edit import apply_operations

    ids = {item["id"] for item in record["independent_layers"]}
    for op in operations:
        if op["piece"] is not None:
            c._members([op["piece"]], ids, "edit piece")
        if op["type"] == "fit-alpha-box" and len(ids) > 1 and op["piece"] is None:
            raise c.PartsContractError(
                "fit one independent piece at a time; give operation.piece"
            )
        if op["type"] == "rotate" and len(ids) > 1 and op["piece"] is None:
            raise c.PartsContractError("rotate one independent piece at a time")
    changed = 0
    holes = 0
    bounds_changed = False
    for item in record["independent_layers"]:
        ops = [
            op for op in operations if op["piece"] is None or op["piece"] == item["id"]
        ]
        if not ops:
            continue
        key = item["image_key"]
        original_key = "original." + item["id"]
        images.setdefault(original_key, images[key].copy())
        single = {"part": images[key].copy(), "drawn": images[original_key].copy()}
        edited, count, filled = apply_operations(
            single,
            {"method": {"cut": {"method": "part-only"}}},
            ops,
            sampling=sampling,
            allow_noop=True,
        )
        images[key] = edited["part"]
        if box := images[key].getbbox():
            target = item["target"]
            expanded = [
                min(target[0], box[0]),
                min(target[1], box[1]),
                max(target[2], box[2]),
                max(target[3], box[3]),
            ]
            bounds_changed |= expanded != target
            item["target"] = expanded
        changed += count
        holes += filled
    if not changed and not guides_changed and not bounds_changed:
        raise c.PartsContractError("operations do not change any source pixels")
    full = Image.new("RGBA", images["part"].size)
    for item in record["independent_layers"]:
        full.alpha_composite(images[item["image_key"]])
    images["part"] = full
    images["mask"] = full.getchannel("A")
    new = {k: deepcopy(v) for k, v in record.items() if k not in ("digest", "images")}
    new["method"]["programmatic_edit"] = {
        "parent": record["digest"],
        "source_candidate": location,
        "operations": operations,
        "changed_pixels": changed,
        "provider_calls": 0,
        "registered_geometry": True,
        "qa_revalidated": guides_changed,
        "review_bounds_updated": bounds_changed,
    }
    new.update(
        pixels=int((np.asarray(full)[..., 3] > 0).sum()),
        holes_filled=record.get("holes_filled", 0) + holes,
    )
    saved, path = p._publish_candidate(root, state["config"]["part_id"], new, images)
    if path not in row["candidates"]:
        row["candidates"].append(path)
    p._save(root, state)
    return {
        "part": state["config"]["part_id"],
        "pose": record["pose_id"],
        "preview_candidate": path,
        "digest": saved["digest"],
        "changed_pixels": changed,
        "selected": False,
        "provider_calls": 0,
        "qa_revalidated": guides_changed,
        "art_approved": False,
    }
