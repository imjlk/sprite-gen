# SPDX-License-Identifier: Apache-2.0
"""Import authored SD redraws into one flat body and reusable overlay library.

Generation is separate from this recipe. Native sizing and integer placement
are import calibration; neither is performed during playback. Rejected source
grids remain outside the active library.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path
import json
import shutil
from fractions import Fraction

from PIL import Image, ImageFilter

from sprite_gen._deps import np
from sprite_gen.curate import parts_motion, parts_sockets
from sprite_gen.frames import parts_import, parts_palette
from sprite_gen.qa import inspect_parts, parts_stability, parts_fit
from sprite_gen.spec import parts_assets as assets, parts_contract as contract, parts_workspace as workspace
import sd_explorer_contribution_demo as contribution
import sd_explorer_matched_layers as matched

write = contribution.write
HEAD_TRACK = {"bounds": [-20, 0, 20, 28], "radius": 3, "alpha_weight": 3, "min_alpha": 128, "rgb_threshold": 24}


def prepare(base, baseline):
    if not (base / "library").exists():
        shutil.copytree(baseline / "library", base / "library")
        for folder in ("parts-source", "authored-interfaces", "raw-footwear-templates"):
            shutil.copytree(baseline / folder, base / folder)
        for name in ("selection.json", "pose-guide.json", "pose-layer-calibration.json", "part-fit-evidence.json", "palette-policy.json"):
            if not (base / name).exists():
                shutil.copy2(baseline / name, base / name)


def reference(base, path):
    return {"file": path.relative_to(base).as_posix(), "sha256": assets.sha256(path.read_bytes())}


def canonical(image):
    pixels = np.array(image.convert("RGBA"), copy=True)
    pixels[..., 3] = np.where(pixels[..., 3] >= 128, 255, 0)
    pixels[pixels[..., 3] == 0] = 0
    return Image.fromarray(pixels)


def reimport(base, original, changes=None, *, owner=None):
    assembly = base / "library/assembly.json"
    index, registry = workspace.read_workspace(assembly)
    binding = original["bindings"][0]
    pieces = deepcopy(binding["pieces"])
    owner = owner or (assembly.parent / index["documents"][original["id"]]).parent
    reader = assets.SourceReader(owner)

    def capture(ref):
        data = reader.read(ref["file"], ref["sha256"])
        path = base / "captured-art" / (assets.sha256(data) + ".png")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return reference(base, path)

    for piece in pieces:
        piece["palette_regions"] = {key: capture(ref) for key, ref in piece["palette_regions"].items()}
        for clip, mapping in piece["sample_map"].items():
            for sample, placement in mapping.items():
                placement.pop("source", None)
                placement.update(format="local", crop_offset=[0, 0])
                placement["image"] = capture(placement["image"])
                placement["palette_regions"] = {key: capture(ref) for key, ref in placement["palette_regions"].items()}
                if placement["mask"] is not None:
                    placement["mask"]["image"] = capture(placement["mask"]["image"])
                if changes is not None:
                    changes(piece, clip, sample, placement)
    spec = {"kind": "parts-pack-import", "schema_version": 1, "id": original["id"],
        "design_id": original["design_id"], "role": original["role"], "body_mode": original["body_mode"],
        "style": registry[index["rig"]["id"]]["style"], "rig": index["rig"], "motion": index["motion"],
        "view": binding["view"], "binding_id": binding["id"], "pieces": pieces,
        **{key: binding[key] for key in ("occupies", "requires", "excludes", "mirror_safe")}}
    path = base / "import" / (original["id"] + ".json")
    write(path, spec)
    return parts_import.import_pack(assembly, path, base)


def parts(base):
    assembly = base / "library/assembly.json"
    index, registry = workspace.read_workspace(assembly)
    for variant in "ab":
        path = base / "raw-art" / f"hair.{variant}.native-selected.png"
        # One authored import footprint, shared unchanged by all 28 poses.
        box = (42, 25, 86, 63) if variant == "a" else (40, 23, 89, 65)
        raw = canonical(Image.open(base / "raw-art" / f"hair.{variant}.png"))
        glyph = raw.crop(raw.getbbox()).resize((box[2]-box[0], box[3]-box[1]), Image.Resampling.NEAREST)
        native = Image.new("RGBA", (128, 128)); native.alpha_composite(glyph, box[:2])
        native, _ = parts_palette.normalize(base / "native-policy/hair", native)
        path.write_bytes(assets.png_bytes(canonical(native)))
        reimport(base, registry[f"sd.wardrobe.hair.{variant}"],
                 lambda piece, clip, sample, placement: placement.update(image=reference(base, path),
                    offset=[0, -1 if variant == "b" else 0]))
    shutil.copy2(base / "raw-art/footwear.b.png", base / "raw-footwear-templates/footwear.b.png")
    calibration = workspace.decode_json((base / "pose-layer-calibration.json").read_bytes())
    guide = workspace.decode_json((base / "pose-guide.json").read_bytes())
    templates = matched.load_footwear_templates(base, calibration, "b")
    for identity, drawing in templates.items():
        image, _ = parts_palette.normalize(base / "native-policy/boots", canonical(drawing))
        image = canonical(image)
        templates[identity] = image
        (base / "footwear-templates/b" / (identity + ".png")).write_bytes(assets.png_bytes(image))
    replacements, evidence, counters = {}, [], {}
    for row in guide["records"]:
        cell, state = row["cell"], row["state"]
        sample = f"sample.{counters.get(state, 0)}"
        counters[state] = counters.get(state, 0) + 1
        folder = base / "parts-source/sd.matched.footwear.b"
        required = np.asarray(Image.open(folder / f"{cell:02d}.required.png")) >= 128
        allowed = np.asarray(Image.open(folder / f"{cell:02d}.allowed.png")) >= 128
        drawings, origins = [], []
        for declaration in calibration["footwear_placements"][str(cell)]:
            if declaration["visible"]:
                drawing = templates[declaration["template"]]
                x, y = declaration["sole_anchor"]
                drawings.append(drawing); origins.append([x-drawing.width//2, y-drawing.height])
        layer, positions = matched.place_footwear_pair(drawings, origins, required, allowed)
        path = base / "revised-boots" / state / (sample + ".png")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(assets.png_bytes(canonical(layer)))
        replacements[state + ".right", sample] = reference(base, path)
        evidence.append({"state": state, "sample": sample, "positions": positions,
                         "per_pose_resize": False, "runtime_mask": False})
    _, registry = workspace.read_workspace(assembly)
    reimport(base, registry["sd.wardrobe.footwear.b"], lambda piece, clip, sample, placement:
             placement.update(image=replacements[clip, sample], mask=None))
    contribution.refresh_selection(base)
    contribution.register_interfaces(base)
    write(base / "polish-parts-evidence.json", {"hair_colors": 3, "boot_colors": 5,
        "boot_templates": 6, "instances": evidence, "body_changed": False,
        "appearance_clips_baked": 0, "new_body_videos": 0, "art_approved": False, "release_ready": False})


def body(base):
    """Use the selected run redraw; retain the exact idle/walk silhouettes.

    Walking/idle redraw candidates changed native pose geometry and are rejected.
    Those existing sources receive the same explicit nine-color import policy.
    All registration is authored data, without modifying body pixels at runtime.
    """
    assembly = base / "library/assembly.json"
    old_index, old_registry = workspace.read_workspace(assembly)
    old_motion = old_registry[old_index["motion"]["id"]]
    loader_root = (assembly.parent / old_index["documents"][old_motion["id"]]).parent
    old_reader = assets.SourceReader(loader_root)
    old_packs = [d for d in old_registry.values() if d["kind"] == "parts-pack"]
    owners = {d["id"]: (assembly.parent / old_index["documents"][d["id"]]).parent for d in old_packs}
    sheet = canonical(Image.open(base / "raw-art/body.run.candidate-2.png").resize((512, 512), Image.Resampling.NEAREST))
    sheet, _ = parts_palette.normalize(base / "native-policy/body", sheet)
    sheet = canonical(sheet)
    (base / "raw-art/body.run.native-selected.png").write_bytes(assets.png_bytes(sheet))
    clips, body_refs, frames_by_clip, registration = [], {}, {}, []
    for clip in old_motion["clips"]:
        state = clip["state"]
        frames, expected, samples = [], {}, []
        head_reference = None
        for n, previous in enumerate(clip["samples"]):
            before = assets.load_image(old_reader, previous["image"])
            if state == "run":
                image = sheet.crop((n%4*128, n//4*128, n%4*128+128, n//4*128+128))
            else:
                image, _ = parts_palette.normalize(base / "native-policy/body", before)
            image = canonical(image)
            sockets = deepcopy(previous["sockets"])
            # Explicit SD authoring ROI: top 28px of the bald head, matched to
            # each clip's first pose. Only the integer attachment point moves
            # (within 3px of the previous pose); body pixels are never translated or repainted.
            if head_reference is None:
                a = np.asarray(image)
                top = int(np.where(a[..., 3] >= 128)[0].min())
                box = Image.fromarray(((a[top:top+28, :, 3] >= 128)*255).astype(np.uint8)).getbbox()
                head_seed = [(box[0]+box[2])//2, top] if state == "run" else sockets["head"]["position"]
                head_reference = image.crop((head_seed[0]-20, head_seed[1], head_seed[0]+20, head_seed[1]+28))
            head_seed = parts_sockets.match(head_reference, image, head_seed, HEAD_TRACK)["position"]
            sockets["head"]["position"] = head_seed
            path = base / "motion-source" / state / f"{n:03d}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(assets.png_bytes(image))
            body_refs[clip["id"], previous["id"]] = reference(base, path)
            expected[f"{state}/{n:03d}.png"] = assets.sha256(path.read_bytes())
            frames.append({"id": f"redraw.{state}.{n}", "file": f"{n:03d}.png",
                           "duration": previous["duration_ticks"] / clip["timebase"]})
            samples.append(sockets)
            old_alpha, alpha = np.asarray(before)[..., 3] >= 128, np.asarray(image)[..., 3] >= 128
            registration.append({"state": state, "sample": previous["id"],
                "source_pose": previous["source_frame_id"], "source_sha256": previous["image"]["sha256"],
                "image_sha256": expected[f"{state}/{n:03d}.png"],
                "bbox_before": before.getbbox(), "bbox_after": image.getbbox(),
                "alpha_iou": float(np.count_nonzero(old_alpha & alpha)/np.count_nonzero(old_alpha | alpha)),
                "head_before": previous["sockets"]["head"]["position"], "head_after": sockets["head"]["position"],
                "geometry_unchanged": bool(np.array_equal(old_alpha, alpha))})
        descriptor = base / "motion-source" / state / "motion.json"
        write(descriptor, {"kind": "sprite-gen-asset", "version": 1, "frames": frames})
        expected[f"{state}/motion.json"] = assets.sha256(descriptor.read_bytes())
        clips.append({"id": clip["id"], "state": state, "file": f"{state}/motion.json", "loop": clip["loop"],
            "timebase": clip["timebase"], "fps": str(Fraction(clip["timebase"], clip["samples"][0]["duration_ticks"])),
            "timing_policy": "exact", "expected_files": expected})
        frames_by_clip[clip["id"]] = samples
    request = base / "motion-import.json"
    write(request, {"kind": "parts-motion-import", "schema_version": 1, "id": old_motion["id"], "rig": old_index["rig"], "clips": clips})
    imported = parts_motion.import_motion(assembly, request, base / "motion-source")
    annotation = workspace.decode_json((assembly.parent / imported["annotation_template"]).read_bytes())
    for clip_id, samples in frames_by_clip.items():
        for n, sockets in enumerate(samples):
            annotation["clips"][clip_id][f"sample.{n}"]["sockets"] = sockets
    write(base / "attachment-tracks.json", annotation)
    parts_motion.freeze_motion(assembly, imported["candidate"], base / "attachment-tracks.json")
    for original in sorted(old_packs, key=lambda d: (d["role"] != "body", d["id"])):
        def change(piece, clip, sample, placement):
            if original["role"] == "body":
                placement.update(image=body_refs[clip, sample])
        reimport(base, original, change, owner=owners[original["id"]])
    contribution.refresh_selection(base)
    contribution.register_interfaces(base)
    write(base / "body-redraw-evidence.json", {"source_motion": old_index["motion"],
        "registration": registration, "run_source": "built-in imagegen redraw of original sixteen poses",
        "idle_walk_source": "existing intact silhouettes; same nine-color native import policy",
        "discarded": ["body.run.candidate-1", "body.walk.candidate-1", "body.idle.candidate-1"],
        "timing_preserved": True, "body_split": False, "runtime_texture_warps": 0,
        "appearance_clips_baked": 0, "new_body_videos": 0, "art_approved": False, "release_ready": False})


def interfaces(base):
    """Re-author interfaces from declared SD material/region inputs.

    This helper belongs to the demo authoring recipe, not generic QA. Exact
    material colors are restricted by previously authored regions. The required
    and protected inputs remain inspectable, rather than inferred at playback.
    """
    assembly = base / "library/assembly.json"
    index, registry = workspace.read_workspace(assembly)
    motion = registry[index["motion"]["id"]]
    owner = (assembly.parent / index["documents"][motion["id"]]).parent
    reader = assets.SourceReader(owner)
    baseline_interfaces = base / "interfaces-before-redraw"
    if not baseline_interfaces.exists():
        shutil.copytree(base / "parts-source", baseline_interfaces)
    guide = workspace.decode_json((base / "pose-guide.json").read_bytes())
    calibration = workspace.decode_json((base / "pose-layer-calibration.json").read_bytes())
    records = iter(guide["records"])

    def mask(path):
        image = Image.open(path)
        return np.asarray(image.getchannel("A") if image.mode == "RGBA" else image) >= 128

    def dilate(array, size=3):
        return np.asarray(Image.fromarray(array.astype(np.uint8)*255).filter(ImageFilter.MaxFilter(size))) >= 128

    def save_mask(path, array, rgba=False):
        a = Image.fromarray(array.astype(np.uint8)*255)
        image = Image.new("RGBA", (128, 128), (255, 255, 255, 0)) if rgba else a
        if rgba:
            image.putalpha(a)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(assets.png_bytes(image) if rgba else _luma_bytes(image))

    for clip in motion["clips"]:
        for sample in clip["samples"]:
            cell = next(records)["cell"]
            image = assets.load_image(reader, sample["image"])
            rgba = np.asarray(image)
            occupied = rgba[..., 3] >= 128

            def material(colors):
                return occupied & np.any([np.all(rgba[..., :3] == c, axis=-1) for c in colors], axis=0)

            skin = material([[249, 190, 129], [219, 158, 113], [133, 75, 44]])
            shirt = material([[172, 172, 175], [215, 213, 211]])
            shorts = material([[50, 54, 55], [89, 88, 88]])
            for variant in "ab":
                for slot, colors in (("upper", shirt), ("lower", shorts)):
                    before = baseline_interfaces / f"sd.matched.{slot}.{variant}"
                    folder = base / "parts-source" / f"sd.matched.{slot}.{variant}"
                    old_required = mask(before / f"{cell:02d}.required.png")
                    old_protected = mask(before / f"{cell:02d}.protected.png")
                    protected = dilate(old_protected) & occupied
                    # Keep the hand/shoulder override as authored exclusion.
                    overrides = [item for item in calibration.get("fit_interface_overrides", [])
                                 if item["slot"] == slot and item["cell"] == cell]
                    for item in overrides:
                        for x0, y0, x1, y1 in item["protected_regions"]:
                            protected[y0:y1, x0:x1] = occupied[y0:y1, x0:x1]
                    if slot == "lower" and variant == "b":
                        required = dilate(old_required) & skin & ~protected
                    else:
                        required = dilate(old_required) & colors & ~protected
                    # Restrict the part to the new intact body surface; protected
                    # pixels exclude authored moving arms/hands. No body mask.
                    allowed = (occupied if slot == "upper" else dilate(mask(before / f"{cell:02d}.mask.png"))) & occupied & ~protected
                    if slot == "lower" and variant == "b":
                        for x0, y0, x1, y1 in calibration["feet_boxes"][cell]:
                            allowed[y0:y1, x0:x1] = False
                            required[y0:y1, x0:x1] = False
                    save_mask(folder / f"{cell:02d}.required.png", required)
                    save_mask(folder / f"{cell:02d}.protected.png", protected)
                    save_mask(folder / f"{cell:02d}.mask.png", allowed, rgba=True)
                folder = base / "parts-source" / f"sd.matched.footwear.{variant}"
                required = np.zeros((128, 128), dtype=bool)
                for box, declaration in zip(calibration["feet_boxes"][cell], calibration["footwear_placements"][str(cell)]):
                    if declaration["visible"]:
                        required |= matched.foot_region(box, skin, back_air=declaration.get("back_air", False))
                save_mask(folder / f"{cell:02d}.required.png", required)
    for identity in (f"sd.wardrobe.{slot}.{variant}" for slot in ("upper", "lower") for variant in "ab"):
        _, registry = workspace.read_workspace(assembly)
        original = registry[identity]
        variant = identity[-1]
        slot = identity.split(".")[2]
        cells = {(clip["id"], sample["id"]): n for n, (clip, sample) in enumerate(
            (clip, sample) for clip in motion["clips"] for sample in clip["samples"])}

        def changes(piece, clip, sample, placement):
            if placement["mask"] is not None:
                path = base / "parts-source" / f"sd.matched.{slot}.{variant}" / f"{cells[clip, sample]:02d}.mask.png"
                placement["mask"]["image"] = reference(base, path)
        reimport(base, original, changes)
    contribution.refresh_selection(base)
    contribution.register_interfaces(base)


def _luma_bytes(image):
    from io import BytesIO
    output = BytesIO(); image.save(output, format="PNG")
    return output.getvalue()


def repair(base):
    assembly = base / "library/assembly.json"
    proposals = []
    for slot in ("upper", "lower"):
        for variant in "ab":
            identity = f"sd.wardrobe.{slot}.{variant}"
            path = base / "repair-plans" / (identity + ".json")
            proposal = parts_fit.repair_plan(assembly, identity, path, radius=3, patch_gaps=True)
            proposals.append(proposal)
            print(json.dumps({"part": identity, **proposal}), flush=True)
    for proposal in proposals:
        if proposal["changed_samples"]:
            parts_import.import_pack(assembly, Path(proposal["proposal"]), Path(proposal["source_root"]))
    contribution.refresh_selection(base)
    contribution.register_interfaces(base)
    write(base / "polish-repair-summary.json", {"proposals": proposals, "art_approved": False, "release_ready": False})


def footwear(base):
    """Fit each foot independently while reusing the six exact angle glyphs."""
    assembly = base / "library/assembly.json"
    calibration = workspace.decode_json((base / "pose-layer-calibration.json").read_bytes())
    guide = workspace.decode_json((base / "pose-guide.json").read_bytes())
    evidence = []
    for variant in "ab":
        templates = matched.load_footwear_templates(base, calibration, variant)
        for identity, drawing in templates.items():
            if identity in calibration.get("footwear_template_flips", {}).get(variant, []):
                drawing = drawing.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            policy = base / "native-policy/boots" if variant == "b" else base
            normalized, _ = parts_palette.normalize(policy, canonical(drawing))
            templates[identity] = canonical(normalized)
            path = base / "footwear-templates" / variant / (identity + ".png")
            path.write_bytes(assets.png_bytes(templates[identity]))
        counters, replacements = {}, {}
        for row in guide["records"]:
            state, cell = row["state"], row["cell"]
            sample = f"sample.{counters.get(state, 0)}"; counters[state] = counters.get(state, 0) + 1
            folder = base / "parts-source" / f"sd.matched.footwear.{variant}"
            required = np.asarray(Image.open(folder / f"{cell:02d}.required.png")) >= 128
            allowed = np.asarray(Image.open(folder / f"{cell:02d}.allowed.png")) >= 128
            drawings, origins = [], []
            visible = [d for d in calibration["footwear_placements"][str(cell)] if d["visible"]]
            for declaration in visible:
                drawing = templates[declaration["template"]]
                x, y = declaration["sole_anchor"]
                drawings.append(drawing); origins.append([x-drawing.width//2, y-drawing.height])
            layer, positions = matched.place_footwear_pair(drawings, origins, required, allowed)
            alpha = np.asarray(layer)[..., 3] >= 128
            path = base / "footwear-placement" / variant / state / (sample + ".png")
            path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(assets.png_bytes(canonical(layer)))
            replacements[state + ".right", sample] = reference(base, path)
            evidence.append({"variant": variant, "state": state, "sample": sample, "positions": positions,
                "glyphs": [reference(base, base / "footwear-templates" / variant / (d["template"] + ".png")) for d in visible],
                "uncovered": int((required & ~alpha).sum()), "outside": int((alpha & ~allowed).sum()),
                "per_pose_resize": False, "per_pose_pixel_patch": False, "runtime_mask": False})
        _, registry = workspace.read_workspace(assembly)
        reimport(base, registry[f"sd.wardrobe.footwear.{variant}"], lambda piece, clip, sample, placement:
            placement.update(image=replacements[clip, sample], offset=[0, 0], mask=None))
    contribution.refresh_selection(base); contribution.register_interfaces(base)
    write(base / "footwear-reuse-evidence.json", {"instances": evidence, "angle_glyphs_per_variant": 6,
        "per_pose_resize": False, "per_pose_pixel_patch": False, "art_approved": False, "release_ready": False})


def stability(base, baseline):
    assembly = base / "library/assembly.json"
    index, registry = workspace.read_workspace(assembly)
    motion = registry[index["motion"]["id"]]
    body_pack = registry[index["body"][0]["id"]]
    declaration = {"kind": "parts-stability-import", "schema_version": 1, "id": "sd.source.stability",
        "rig": index["rig"], "motion": index["motion"], "regions": [{
            "id": "head."+clip["state"], "label": "정렬한 머리 원본", "part": contract.reference(body_pack),
            "binding": body_pack["bindings"][0]["id"], "piece": "body", "socket": "head", "bounds": [-20, 0, 20, 28],
            "reference": {"clip": clip["id"], "sample": clip["samples"][0]["id"]}, "clips": [clip["id"]],
            "max_alpha_pixels": 48, "max_rgb_pixels": 160, "rgb_threshold": 24, "min_alpha": 128
        } for clip in motion["clips"]]}
    path = base / "stability-import.json"; write(path, declaration)
    parts_stability.import_stability(assembly, path)
    root, index, registry, _ = parts_motion._active(assembly)
    _, _, qa, payloads = inspect_parts.collect(root, index, registry)
    after = workspace.decode_json(payloads["stability.json"])
    old_index, old_registry = workspace.read_workspace(baseline / "library/assembly.json")
    old_body = old_registry[old_index["body"][0]["id"]]
    old_motion = old_registry[old_index["motion"]["id"]]
    old_root = (baseline / "library" / old_index["documents"][old_body["id"]]).parent
    reader = assets.SourceReader(old_root)
    before = []
    for clip in old_motion["clips"]:
        ref = None
        for sample in clip["samples"]:
            placement = old_body["bindings"][0]["pieces"][0]["sample_map"][clip["id"]][sample["id"]]
            image = assets.load_image(reader, placement["image"])
            x, y = sample["sockets"]["head"]["position"]
            crop = image.crop((x-20, y, x+20, y+28))
            if ref is None:
                ref = crop
            before.append({"clip": clip["id"], "sample": sample["id"], **parts_stability.measure(ref, crop)})
    write(base / "stability-comparison.json", {"kind": "sd-source-stability-comparison", "schema_version": 1,
        "baseline_motion": old_index["motion"], "motion": index["motion"], "criteria": declaration["regions"],
        "before": before, "after": after, "scope": "40x28 head ROI, integer native attachment alignment; each clip reference is its first pose",
        "art_approved": False, "release_ready": False})
    return qa["stability"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, type=Path)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--body", action="store_true")
    parser.add_argument("--interfaces", action="store_true")
    parser.add_argument("--repair", action="store_true")
    parser.add_argument("--stability", action="store_true")
    parser.add_argument("--footwear", action="store_true")
    args = parser.parse_args()
    prepare(args.base, args.baseline)
    if args.footwear:
        footwear(args.base)
    elif args.stability:
        stability(args.base, args.baseline)
    elif args.repair:
        repair(args.base)
    elif args.interfaces:
        interfaces(args.base)
    elif args.body:
        body(args.base)
    else:
        parts(args.base)
    print(json.dumps(inspect_parts.inspect(args.base / "library/assembly.json"), ensure_ascii=False))
