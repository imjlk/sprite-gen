# SPDX-License-Identifier: Apache-2.0
"""An explicit source-pose paper doll; no nearest-pose runtime or body split.

The source is an intact video body. Selected poses have matching garment
drawings and authored shoe envelopes. Artwork sizing and part-only coverage
masks are import preparation, never a body edit or runtime texture warp.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from fractions import Fraction
from itertools import product
from pathlib import Path
import json
import subprocess

from PIL import Image, ImageDraw, ImageFilter

from sprite_gen._deps import np
from sprite_gen.compose.parts_runtime import PartsPlayer
from sprite_gen.compose import compose_parts
from sprite_gen.qa import parts_fitting
from sprite_gen.curate import parts_appearance, parts_motion
from sprite_gen.frames import parts_import
from sprite_gen.spec import parts_assets as assets, parts_contract as contract, parts_workspace as workspace

SLOTS = ("hair", "expression", "upper", "lower", "footwear")
CADENCE = {"idle": 1, "walk": 8, "run": 12}


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(workspace.json_bytes(data))


def components(image, *, pixels=False):
    remaining = set(zip(*np.where(np.asarray(image)[..., 3] >= 128)))
    output = []
    while remaining:
        seed = remaining.pop()
        pending, points = [seed], [seed]
        while pending:
            y, x = pending.pop()
            for point in ((y-1, x), (y+1, x), (y, x-1), (y, x+1)):
                if point in remaining:
                    remaining.remove(point)
                    points.append(point)
                    pending.append(point)
        if len(points) > 8:
            y, x = zip(*points)
            bounds = (int(min(x)), int(min(y)), int(max(x)+1), int(max(y)+1))
            output.append((bounds, points) if pixels else bounds)
    return sorted(output)


def source_poses(source, guide):
    player = PartsPlayer.from_workspace(source/"library/assembly.json")
    registry = {d["id"]: d for d in player.documents}
    motion = registry[player.appearance["motion"]["id"]]
    if guide["source_motion"] != player.appearance["motion"]:
        raise ValueError("pose guide differs from source motion")
    rows = []
    for row in guide["records"]:
        clip = next(c for c in motion["clips"] if c["state"] == row["state"])
        sample = next(s for s in clip["samples"] if s["id"] == row["source_sample"])
        assert row["source_image"] == sample["image"] and row["sockets"] == sample["sockets"]
        rows.append({**row, "body": player.load(motion, sample["image"])})
    return player, rows


def foot_region(target, body_skin, *, back_air=False):
    x0, y0, x1, y1 = target
    required = np.zeros((128, 128), dtype=bool)
    required[y0:y1, x0:x1] = body_skin[y0:y1, x0:x1]
    # An ankle opening may leave skin visible. A backwards lifted shoe opens
    # on its right edge; other drawings open at their upper edge.
    if back_air:
        required[y0:y1, max(x0, x1-3):x1] = False
    else:
        required[y0:min(y1, y0+2), x0:x1] = False
    return required


def foot_fit(drawing, target, body_skin, *, back_air=False):
    """Bounded one-time shoe fit inside an authored envelope, not a foot solver."""
    x0, y0, x1, y1 = target
    required = foot_region(target, body_skin, back_air=back_air)
    best = None
    for dw, dh, dx, dy in product(range(6), range(5), range(-3, 3), range(-3, 2)):
        width, height = x1-x0+dw, y1-y0+dh
        fitted = drawing.resize((width, height), Image.Resampling.NEAREST)
        canvas = Image.new("RGBA", (128, 128))
        canvas.alpha_composite(fitted, (x0+dx, y0+dy))
        alpha = np.asarray(canvas)[..., 3] >= 128
        missed = int((required & ~alpha).sum())
        outside = alpha.copy()
        outside[max(0, y0-1):min(128, y1+1), max(0, x0-1):min(128, x1+1)] = False
        score = (missed, int(outside.sum()), dw+dh+abs(dx)+abs(dy))
        if best is None or score < best[0]:
            best = (score, canvas, {"envelope": target, "import_size": [width, height],
                                   "placement": [x0+dx, y0+dy], "uncovered_declared_skin_pixels": missed})
    return best[1], best[2]


def garment_fit(drawing, target, required):
    """Fit this pose's authored garment once; playback raster stays fixed."""
    x0, y0, x1, y1 = target
    best = None
    for widths, heights in (((0, 2, 4, 6, 8, 12), (0, 2, 4, 6)), (range(13), range(7))):
        if best is not None and best[0][0] == 0:
            break
        for dw, dh, dx, dy in product(widths, heights, range(-6, 4), range(-3, 3)):
            fitted = drawing.resize((x1-x0+dw, y1-y0+dh), Image.Resampling.NEAREST)
            canvas = Image.new("RGBA", (128, 128))
            canvas.alpha_composite(fitted, (x0+dx, y0+dy))
            alpha = np.asarray(canvas)[..., 3] >= 128
            score = (int((required & ~alpha).sum()), dw+dh+abs(dx)+abs(dy), int((alpha & ~required).sum()))
            if best is None or score < best[0]:
                best = (score, canvas, {"import_size": list(fitted.size), "placement": [x0+dx, y0+dy],
                                       "uncovered_declared_garment_pixels": score[0]})
    return best[1], best[2]


def prepare_art(base, rows):
    calibration = workspace.decode_json((base/"pose-layer-calibration.json").read_bytes())
    fits = []
    for variant in ("a", "b"):
        sheets = {}
        for slot in ("upper", "lower", "footwear"):
            path = base/"raw-art"/(slot+"."+variant+".png")
            picture = Image.open(path).convert("RGBA").resize((320, 400), Image.Resampling.NEAREST)
            picture.putalpha(picture.getchannel("A").point(lambda a: 255 if a >= 128 else 0))
            sheets[slot] = picture

        def cell_image(slot, cell):
            return sheets[slot].crop((cell % 4*80, cell//4*80, cell % 4*80+80, cell//4*80+80))

        for row in rows:
            cell, body = row["cell"], row["body"]
            rgba = np.asarray(body)
            rgb = rgba[..., :3].astype(np.int16)
            y = np.indices(rgba.shape[:2])[0]
            occupied = rgba[..., 3] >= 128
            skin = (occupied & (rgb[..., 0] > 150) & (rgb[..., 1] > 70)
                    & (rgb[..., 0]-rgb[..., 1] > 20) & (rgb.max(2)-rgb.min(2) > 30))
            gray = rgb.max(2)-rgb.min(2) < 25
            skin_picture = Image.new("RGBA", (128, 128))
            skin_picture.putalpha(Image.fromarray(skin.astype(np.uint8)*255))
            arms = np.zeros((128, 128), dtype=bool)
            for (x0, y0, x1, y1), points in components(skin_picture, pixels=True):
                if y0 < 80 and y1 > 74:
                    ys, xs = zip(*points)
                    arms[ys, xs] = True
            around = np.asarray(Image.fromarray(arms.astype(np.uint8)*255).filter(ImageFilter.MaxFilter(3))) > 0
            arms |= around & occupied & ((rgb.max(2) < 110) | ~gray)
            garment = {"upper": occupied & (y >= 65) & (y < 91) & (rgb[..., 0] > 125) & gray,
                       "lower": occupied & (y >= 87) & (y < 103) & (rgb[..., 0] > 40) & (rgb[..., 0] <= 130) & gray}
            # Long trousers cover the complete legs, including dark skin and
            # the thighs above y=97. Skin-colour thresholds alone missed both.
            leg = occupied & (y >= 91) & ~arms
            for x0, y0, x1, y1 in calibration["feet_boxes"][cell]:
                leg[y0:y1, x0:x1] = False
            if variant == "b":
                garment["lower"] |= leg
            for slot in ("upper", "lower"):
                ys, xs = np.where(garment[slot])
                target = [int(xs.min())-1, int(ys.min())-1, int(xs.max())+2, int(ys.max())+2]
                drawing = cell_image(slot, cell)
                raw_bbox = drawing.getbbox()
                if raw_bbox is None:
                    raise ValueError(f"{slot}.{variant}/{cell} is empty")
                required = garment[slot] & ~arms
                if slot == "lower" and variant == "b":
                    # Existing dark outlines can remain under the new layer;
                    # the visibly exposed skin must all be covered by pants.
                    required &= (rgb[..., 0]-rgb[..., 1] > 20) & (rgb.max(2)-rgb.min(2) > 30)
                layer, fit = garment_fit(drawing.crop(raw_bbox), target, required)
                if slot == "upper":
                    coverage = occupied & ~arms
                else:
                    # A part-only stencil prevents a compact shorts drawing
                    # from flaring over the legs, or trousers painting feet.
                    area = np.asarray(Image.fromarray(garment[slot].astype(np.uint8)*255).filter(ImageFilter.MaxFilter(3))) > 0
                    coverage = area & occupied & ~arms
                    if variant == "b":
                        for x0, y0, x1, y1 in calibration["feet_boxes"][cell]:
                            coverage[y0:y1, x0:x1] = False
                folder = base/"parts-source"/("sd.matched."+slot+"."+variant)
                folder.mkdir(parents=True, exist_ok=True)
                (folder/f"{cell:02d}.png").write_bytes(assets.png_bytes(layer))
                mask = Image.new("RGBA", (128, 128), (255, 255, 255, 0))
                mask.putalpha(Image.fromarray(coverage.astype(np.uint8)*255))
                (folder/f"{cell:02d}.mask.png").write_bytes(assets.png_bytes(mask))
                Image.fromarray(required.astype(np.uint8)*255).save(folder/f"{cell:02d}.required.png")
                Image.fromarray(arms.astype(np.uint8)*255).save(folder/f"{cell:02d}.protected.png")
                fits.append({"slot": slot, "variant": variant, "cell": cell,
                             "raw_bbox": list(raw_bbox), "target_bbox": target, "mask_target": "part only", **fit})
            shoe = Image.new("RGBA", (128, 128))
            shoe_required = np.zeros((128, 128), dtype=bool)
            for number, (source_cell, side) in enumerate(calibration["foot_drawing_sources"][cell]):
                drawing = cell_image("footwear", source_cell)
                objects = components(drawing)
                if len(objects) > 2 or side >= len(objects):
                    raise ValueError(f"shoe drawing {variant}/{source_cell}/{side} is invalid")
                fitted, evidence = foot_fit(drawing.crop(objects[side]), calibration["feet_boxes"][cell][number],
                                            skin, back_air=cell >= 16 and number == 0)
                shoe.alpha_composite(fitted)
                shoe_required |= foot_region(calibration["feet_boxes"][cell][number], skin,
                                             back_air=cell >= 16 and number == 0)
                fits.append({"slot": "footwear", "variant": variant, "cell": cell,
                             "foot": number, "drawing_source": [source_cell, side], **evidence})
            folder = base/"parts-source"/("sd.matched.footwear."+variant)
            folder.mkdir(parents=True, exist_ok=True)
            (folder/f"{cell:02d}.png").write_bytes(assets.png_bytes(shoe))
            Image.fromarray(shoe_required.astype(np.uint8)*255).save(folder/f"{cell:02d}.required.png")
    write(base/"part-fitting.json", {"kind": "explicit-source-pose-part-fitting", "schema_version": 1,
          "drawings": fits, "runtime_warp": False, "body_changed": False,
          "operation": "one-time artwork fitting and explicit part-only coverage; no nearest-pose selection"})


def build(base, source, player, rows):
    library = base/"library"
    if library.exists():
        raise ValueError("destination library must be new")
    old_index, old_registry = workspace.read_workspace(source/"library/assembly.json")
    rig = old_registry[old_index["rig"]["id"]]
    topology = old_registry[rig["topology"]["id"]]
    catalog = deepcopy(old_registry[old_index["catalog"]["id"]])
    catalog.pop("digest")
    catalog["id"] = "sd.matched.layers.catalog"
    for slot in catalog["slots"]:
        slot.update(options=[], default=None)
    catalog = contract.seal(catalog)
    workspace.initialize(library, "human.sd.v1")
    assembly = library/"assembly.json"
    index, _ = workspace.read_workspace(assembly)
    index.update(documents={rig["id"]: "profiles/rig.json", topology["id"]: "profiles/topology.json", catalog["id"]: "catalog.json"},
                 rig=contract.reference(rig), catalog=contract.reference(catalog))
    for doc, path in ((rig, "profiles/rig.json"), (topology, "profiles/topology.json"), (catalog, "catalog.json")):
        write(library/path, doc)
    write(assembly, index)
    clips, by_state = [], {}
    for state in index["states"]:
        selected = [r for r in rows if r["state"] == state]
        by_state[state] = selected
        folder = base/"motion-source"/state
        folder.mkdir(parents=True, exist_ok=True)
        expected, frames = {}, []
        for number, row in enumerate(selected):
            filename = f"{number:03d}.png"
            data = assets.png_bytes(row["body"])
            assert assets.sha256(data) == row["source_image"]["sha256"]
            (folder/filename).write_bytes(data)
            expected[f"{state}/{filename}"] = assets.sha256(data)
            frames.append({"id": row["source_sample"], "file": filename,
                           "duration": 1 / CADENCE[state]})
        path = folder/"motion.json"
        write(path, {"kind": "sprite-gen-asset", "version": 1, "fps": CADENCE[state], "frames": frames})
        expected[f"{state}/motion.json"] = assets.sha256(path.read_bytes())
        clips.append({"id": state+".right", "state": state, "file": state+"/motion.json", "fps": str(CADENCE[state]),
                      "loop": True, "timebase": 120000, "timing_policy": "round-boundaries",
                      "source_mode": "external", "expected_files": expected})
    request = base/"motion-import.json"
    write(request, {"kind": "parts-motion-import", "schema_version": 1, "id": "sd.matched.video.motion", "rig": index["rig"], "clips": clips})
    imported = parts_motion.import_motion(assembly, request, base/"motion-source")
    candidate = workspace.decode_json((library/imported["candidate"]).read_bytes())
    annotation = workspace.decode_json((library/imported["annotation_template"]).read_bytes())
    for clip in candidate["motion"]["clips"]:
        for sample, row in zip(clip["samples"], by_state[clip["state"]]):
            annotation["clips"][clip["id"]][sample["id"]]["sockets"] = deepcopy(row["sockets"])
    write(base/"attachment-tracks.json", annotation)
    parts_motion.freeze_motion(assembly, imported["candidate"], base/"attachment-tracks.json")
    index, registry = workspace.read_workspace(assembly)
    motion = registry[index["motion"]["id"]]

    def image_ref(path):
        return {"file": path.relative_to(base).as_posix(), "sha256": assets.sha256(path.read_bytes())}

    captured = {}
    def capture(owner, reference):
        key = owner["id"], reference["sha256"]
        if key not in captured:
            data = assets.png_bytes(player.load(owner, reference))
            path = base/"captured-art"/(assets.sha256(data)+".png")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            captured[key] = image_ref(path)
        return captured[key]

    def mapping(factory):
        return {clip["id"]: {sample["id"]: factory(clip, row, number)
                for number, (sample, row) in enumerate(zip(clip["samples"], by_state[clip["state"]]))} for clip in motion["clips"]}

    body_id = old_index["body"][0]["id"]
    body = {"kind": "parts-pack-import", "schema_version": 1, "id": body_id, "design_id": "explorer.body",
        "role": "body", "body_mode": "flat", "style": rig["style"], "rig": index["rig"], "motion": index["motion"], "view": rig["view"],
        "binding_id": body_id+".binding", "pieces": [{"id": "body", "slot": None, "node": "root", "provides": ["root"], "replaces": [], "mode": "raster",
        "sample_map": mapping(lambda clip, row, number: {"image": image_ref(base/"motion-source"/clip["state"]/f"{number:03d}.png"),
                    "format": "full-canvas", "pivot": [0, 0], "offset": [0, 0], "target_socket": "root"})}]}
    write(base/"import/body.json", body)
    parts_import.import_pack(assembly, base/"import/body.json", base)
    choices = {}
    for slot in ("hair", "expression", "headwear", "gloves", "back_accessory"):
        options = [d for d in old_registry.values() if d["kind"] == "parts-pack" and d["role"] == "part"
                   and any(p["slot"] == slot for b in d["bindings"] for p in b["pieces"])]
        choices[slot] = [None] if slot not in SLOTS else []
        for original in sorted(options, key=lambda d: d["id"]):
            binding = original["bindings"][0]
            pieces = []
            for piece in binding["pieces"]:
                def placement(clip, row, number):
                    value = deepcopy(piece["sample_map"][clip["id"]][row["source_sample"]])
                    value.pop("source", None)
                    value["format"] = "full-canvas"
                    value["image"] = capture(original, value["image"])
                    # Retain each glove's source-local image and the pivot/
                    # offset already normalized by its original import.
                    if piece["mode"] == "raster" and slot == "gloves":
                        value["format"] = "local"
                    if value["mask"] is not None:
                        value["mask"]["image"] = capture(original, value["mask"]["image"])
                    value["palette_regions"] = {k: capture(original, v) for k, v in value["palette_regions"].items()}
                    return value
                pieces.append({k: deepcopy(piece[k]) for k in ("id", "slot", "node", "provides", "replaces", "mode", "join_interface")})
                pieces[-1].update(sample_map=mapping(placement), palette_regions={k: capture(original, v) for k, v in piece["palette_regions"].items()})
            spec = {"kind": "parts-pack-import", "schema_version": 1, "id": original["id"], "design_id": original["design_id"], "role": "part",
                "style": rig["style"], "rig": index["rig"], "motion": index["motion"], "view": rig["view"], "binding_id": binding["id"], "occupies": [slot], "pieces": pieces}
            path = base/"import"/(spec["id"]+".json")
            write(path, spec)
            choices[slot].append(parts_import.import_pack(assembly, path, base)["part"])
    for slot in ("upper", "lower", "footwear"):
        choices[slot] = []
        for variant in ("a", "b"):
            identity = "sd.wardrobe."+slot+"."+variant
            provided = {"upper": "clothing", "lower": "lower_clothing", "footwear": "footwear"}[slot]
            def placement(clip, row, number):
                folder = base/"parts-source"/("sd.matched."+slot+"."+variant)
                socket = "torso" if slot == "upper" else "root"
                result = {"image": image_ref(folder/f"{row['cell']:02d}.png"), "format": "full-canvas",
                    "pivot": row["sockets"][socket]["position"], "offset": [0, 0], "target_socket": socket,
                    "after": ["root", "lower_clothing", "footwear"] if slot == "upper" else ["root", "lower_clothing"] if slot == "footwear" else ["root"]}
                if slot in ("upper", "lower"):
                    result["mask"] = {"image": image_ref(folder/f"{row['cell']:02d}.mask.png"), "target_node": provided,
                                      "space": "rig-canvas", "channel": "alpha"}
                return result
            spec = {"kind": "parts-pack-import", "schema_version": 1, "id": identity, "design_id": "explorer."+slot+"."+variant,
                    "role": "part", "style": rig["style"], "rig": index["rig"], "motion": index["motion"], "view": rig["view"],
                    "binding_id": identity+".binding", "occupies": [slot], "pieces": [{"id": slot, "slot": slot,
                    "node": "torso" if slot == "upper" else "root",
                    "provides": [provided], "replaces": [], "mode": "raster", "sample_map": mapping(placement)}]}
            path = base/"import"/(identity+".json")
            write(path, spec)
            choices[slot].append(parts_import.import_pack(assembly, path, base)["part"])
    choices = {slot["id"]: choices[slot["id"]] for slot in old_registry[old_index["catalog"]["id"]]["slots"]}
    write(base/"choices.json", choices)
    write(base/"selection.json", {"kind": "parts-appearance-selection", "schema_version": 1, "id": "sd.matched.layers",
                                  "selections": {slot: options[0] for slot, options in choices.items()}})
    parts_appearance.create(assembly, base/"selection.json")
    write(base/"motion-provenance.json", {"source_motion": old_index["motion"], "motion": index["motion"],
        "source_geometry_and_body_pixels_preserved": True, "native_source_cadence": 24, "curated_cadence": CADENCE,
        "sampling": "idle four source poses; walk one 24-source-frame cycle sampled every three; run eight consecutive poses slowed 2x",
        "body_split": False, "runtime_texture_warps": 0, "appearance_clips_baked": 0, "art_approved": False, "release_ready": False})
    return assembly


def check(base, rows):
    player = PartsPlayer.from_workspace(base/"library/assembly.json")
    choices = workspace.decode_json((base/"choices.json").read_bytes())
    by_state = {state: [r for r in rows if r["state"] == state] for state in CADENCE}
    count = 0
    for selections in product(*(choices[slot] for slot in SLOTS)):
        player.seek(Fraction(1, 7))
        before = player.elapsed_seconds
        player.set_selection(dict(zip(SLOTS, selections)))
        assert player.elapsed_seconds == before
        for state in CADENCE:
            player.set_state(state)
            for number, row in enumerate(by_state[state]):
                player.seek(Fraction(number, CADENCE[state]))
                scene = player.layers(interpolate=False)
                body = next(l for l in scene["layers"] if l["slot"] is None)
                assert body["position"] == ["0", "0"] and body["image"]["sha256"] == row["source_image"]["sha256"]
                assert all(m["target_node"] != "root" for m in scene["masks"])
                image, evidence = player.frame()
                assert sum(d["clipped_pixels"] for d in evidence["evidence"]["draw"]) == 0
                count += 1
    for variant in ("a", "b"):
        player.set_selection({slot: choices[slot][0 if variant == "a" else 1] for slot in SLOTS})
        sheet = Image.new("RGBA", (4*128, 5*146), (239, 240, 234, 255))
        draw = ImageDraw.Draw(sheet)
        for row in rows:
            player.set_state(row["state"])
            number = by_state[row["state"]].index(row)
            player.seek(Fraction(number, CADENCE[row["state"]]))
            image, _ = player.frame()
            cell = row["cell"]
            sheet.alpha_composite(image, (cell % 4*128, cell//4*146+18))
            draw.text((cell % 4*128+3, cell//4*146+2), str(cell)+" "+row["state"], fill=(20, 20, 20, 255))
        sheet.resize((1024, 1460), Image.Resampling.NEAREST).save(base/("matched-review-"+variant+".png"))
    report = {"base_appearances": 32, "shared_states": 3, "curated_body_samples": len(rows), "scene_evaluations": count,
              "whole_body_pixels_preserved": True, "clipped_pixels": 0, "appearance_clips_baked": 0,
              "runtime_texture_warps": 0, "art_approved": False, "release_ready": False}
    write(base/"validation.json", report)
    return report


def check_part_fits(base, rows):
    """Every installed part/piece, all poses; geometry never implies art approval."""
    player = PartsPlayer.from_workspace(base/"library/assembly.json")
    choices = workspace.decode_json((base/"choices.json").read_bytes())
    limits = {
        "hair": ((58, 52), (32, 14, 100, 73)),
        "expression": ((30, 24), (43, 36, 94, 72)),
        "upper": ((42, 40), (42, 62, 91, 101)),
        "lower": ((64, 45), (29, 82, 100, 122)),
        "footwear": ((70, 34), (29, 89, 100, 122)),
        "headwear": ((68, 48), (30, 7, 106, 61)),
        "gloves": ((28, 30), (25, 60, 104, 110)),
        "back_accessory": ((40, 40), (30, 58, 99, 103)),
    }
    results, seen = [], set()
    by_state = {state: [r for r in rows if r["state"] == state] for state in CADENCE}
    for variant in (0, 1):
        player.set_selection({s: opts[variant] if s in SLOTS else opts[-1] for s, opts in choices.items()})
        for row in rows:
            player.set_state(row["state"])
            player.seek(Fraction(by_state[row["state"]].index(row), CADENCE[row["state"]]))
            _, sample, clock = player.current()
            for entry in sample["draw"]:
                slot = entry["slot"]
                key = entry["pack"]["id"], entry["piece_id"], row["cell"]
                if slot is None or key in seen:
                    continue
                seen.add(key)
                part, _ = compose_parts.render_sample(player.plan, {**sample, "draw": [entry]}, player.load)
                size, bounds = limits[slot]
                if key[0] == "sd.wardrobe.hair.a":
                    size = (44, 39)
                rules = parts_fitting.FitRules((128, 128), size, bounds,
                    (30, 12, 98, 64) if slot == "hair" else None)
                masks = {}
                if slot in ("upper", "lower", "footwear"):
                    folder = base/"parts-source"/("sd.matched."+slot+"."+("a" if variant == 0 else "b"))
                    names = (("required_mask", "required"),) if slot == "footwear" else (
                        ("required_mask", "required"), ("protected_mask", "protected"), ("allowed_mask", "mask"))
                    masks = {name: Image.open(folder/f"{row['cell']:02d}.{suffix}.png").copy()
                             for name, suffix in names}
                fit = parts_fitting.measure(row["body"], part, rules, **masks)
                results.append({"part": entry["pack"], "piece": entry["piece_id"], "slot": slot,
                                "cell": row["cell"], "state": clock["state"], "sample": clock["sample_id"],
                                "coverage_region": "source-pose mask" if masks else "not declared", **fit})
    failed = [r for r in results if r["status"] == "error"]
    report = {"kind": "parts-all-slot-fit-evidence", "schema_version": 1, "motion": player.appearance["motion"],
              "part_piece_pose_checks": len(results), "slots_checked": list(limits), "failed_checks": len(failed),
              "rework_parts": sorted({r["part"]["id"] for r in failed}), "results": results,
              "calibration": "SD source-specific palette/y-region masks and authored bounds; no general anatomy or retargeting model",
              "art_approved": False, "release_ready": False}
    write(base/"part-fit-evidence.json", report)
    return {k: v for k, v in report.items() if k != "results"}


def review_videos(base):
    """Two side-by-side inspection recordings; never installed as runtime clips."""
    player = PartsPlayer.from_workspace(base/"library/assembly.json")
    choices = workspace.decode_json((base/"choices.json").read_bytes())
    for state in ("walk", "run"):
        folder = base/(state+"-review-frames")
        folder.mkdir(exist_ok=True)
        player.set_state(state)
        for number in range(8):
            player.seek(Fraction(number, CADENCE[state]))
            sheet = Image.new("RGBA", (384, 146), (239, 240, 234, 255))
            draw = ImageDraw.Draw(sheet)
            for column, variant in enumerate((None, 0, 1)):
                player.set_selection({slot: None if variant is None else choices[slot][variant] for slot in SLOTS})
                image, _ = player.frame()
                sheet.alpha_composite(image, (column*128, 18))
                draw.text((column*128+4, 3), ("BODY", "PARTS A", "PARTS B")[column], fill=(20, 20, 20, 255))
            sheet.resize((768, 292), Image.Resampling.NEAREST).save(folder/f"{number:03d}.png")
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-stream_loop", "3",
                        "-framerate", str(CADENCE[state]), "-i", str(folder/"%03d.png"),
                        "-c:v", "libx264", "-r", "24", "-crf", "18", "-pix_fmt", "yuv420p",
                        str(base/(state+"-review.mp4"))], check=True)
    write(base/"review-media.json", {"kind": "parts-inspection-recordings", "runtime_assets": False,
        "motion": player.appearance["motion"], "columns": ["unmodified body", "parts A", "parts B"],
        "states": {s: {"source_fps": CADENCE[s], "samples": 8, "cycles": 4, "video_fps": 24} for s in ("walk", "run")}})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, type=Path)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--review-video", action="store_true", help="Requires ffmpeg; records two inspection videos only.")
    args = parser.parse_args()
    guide = workspace.decode_json((args.base/"body-pose-guide-20.json").read_bytes())
    player, rows = source_poses(args.source, guide)
    prepare_art(args.base, rows)
    build(args.base, args.source, player, rows)
    print(json.dumps(check(args.base, rows), indent=2))
    print(json.dumps(check_part_fits(args.base, rows), indent=2))
    if args.review_video:
        review_videos(args.base)
