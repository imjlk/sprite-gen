# SPDX-License-Identifier: Apache-2.0
"""Rebuild the SD explorer from one clean pixel-art base still and parts designed on it.

Stages (each reads the previous stage's files under --base):
  native   raw base still -> its own grid, a fixed palette and a closed outline (design/)
  designs  each part drawn on the base by an image model -> native part rasters on the base pose
  motion   video-set loops of that base -> strict import -> tracked sockets -> freeze -> motion body
  held     hair, faces, hat and backpack hung on their sockets (follow-through on hair and pack)
  sheets   per-garment pose sheets for drawing each garment on every sample
  clothing slice the drawn garment pages with each garment's own palette, then import
Visual approval stays a human review; nothing here approves art.
"""
from __future__ import annotations

import argparse
import json
from fractions import Fraction
from pathlib import Path
import shutil

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.effects import breathe
from sprite_gen.curate import parts_appearance, parts_motion, parts_sockets
from sprite_gen.frames import extract, parts_import, parts_loop_native as native, parts_pose_sheet as sheets, parts_rebind, pixel_grid
from sprite_gen.spec import parts_assets as assets, parts_workspace as workspace

CANVAS, BASELINE, SCALE = 128, 112, 8
# Idle is the base still breathing (effects.breathe): the head stays bit-identical and every
# layer drawn on the still is warped with the same rows, so nothing is redrawn per frame.
BREATHE = {"depth": 0.05, "breaths": 2, "lag": 0.1}
IDLE_FRAMES, IDLE_FPS = 12, 4
HEAD_BOUNDS = [-20, 0, 20, 28]
# Authored per-part extraction: slot, node, socket, draw order, rig-canvas region and the
# body-difference threshold (lower for materials close to skin or the base clothes).
PARTS = {
    "hair.a": ("hair", "head", "head", ["root", "eyes"], [20, 0, 108, 70], 40, "held"),
    "hair.b": ("hair", "head", "head", ["root", "eyes"], [20, 0, 108, 70], 40, "held"),
    "expression.a": ("expression", "head", "head", ["root"], [56, 30, 80, 60], 24, "held"),
    "expression.b": ("expression", "head", "head", ["root"], [56, 30, 80, 60], 24, "held"),
    "headwear": ("headwear", "head", "head", ["root", "hair"], [10, 0, 118, 60], 40, "held"),
    "back_accessory": ("back_accessory", "root", "root", [], [10, 40, 80, 110], 40, "held"),
    "upper.a": ("upper", "torso", "torso", ["root", "lower_clothing", "footwear"], [20, 55, 108, 100], 40, "raster"),
    "upper.b": ("upper", "torso", "torso", ["root", "lower_clothing", "footwear"], [20, 55, 108, 100], 40, "raster"),
    "lower.a": ("lower", "root", "root", ["root"], [20, 78, 108, 110], 20, "raster"),
    "lower.b": ("lower", "root", "root", ["root"], [20, 78, 108, 116], 20, "raster"),
    "footwear.a": ("footwear", "root", "root", ["root", "lower_clothing"], [20, 84, 108, 120], 20, "raster"),
    "footwear.b": ("footwear", "root", "root", ["root", "lower_clothing"], [20, 84, 108, 120], 20, "raster"),
    "gloves": ("gloves", "root", "root", ["root", "clothing"], [20, 55, 108, 110], 20, "raster"),
}
# Face features are small dark ink on the skin: compare without the 1 px slack and keep dark islands.
# The hat is drawn over the fullest hair so it sits on any hair; its band shares the hair's
# browns, so it is compared tightly and only its own (largest) piece is kept, holes filled.
EXTRACT = {"headwear": {"body": "base.hair.b.canvas.png", "radius": 0, "threshold": 12, "largest": True},
           "expression.a": {"radius": 0, "dark_share": 1.01, "minimum": 2, "body_colors": False},
           "expression.b": {"radius": 0, "dark_share": 1.01, "minimum": 2, "body_colors": False}}
PART_COLORS = {}  # per-part palette size overrides (set from --part-colors)
PROVIDES = {"hair": ["hair"], "expression": ["eyes"], "headwear": ["headwear"], "back_accessory": ["back_accessory"],
            "upper": ["clothing"], "lower": ["lower_clothing"], "footwear": ["footwear"], "gloves": ["glove_a", "glove_b"]}
PART_ID = {name: ("sd.equipment." if name in {"headwear", "back_accessory", "gloves"} else "sd.wardrobe.") + name for name in PARTS}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1))
    return path


def darkest(colors):
    return [int(v) for v in min(colors, key=lambda c: 299*c[0]+587*c[1]+114*c[2])]


def stage_native(base, target_height, colors=16):
    """Snap the raw base still to its drawn grid, a 16-color palette and a closed outline."""
    raw = Image.open(base / "design/base.raw.png").convert("RGBA")
    pixels = np.asarray(raw).copy()
    pixels[pixels[..., 3] < 128] = 0
    raw = Image.fromarray(pixels)
    box = raw.getbbox()
    pitch = native.loop_pitch([raw], (box[3]-box[1])/target_height, spread=0.06, steps=25)
    art, _ = native.lattice_reduce(raw, pitch)
    palette = extract.build_shared_palette([art], colors)
    art = native.silhouette_outline(native.snap(art, np.array(palette)), darkest(palette))
    art = art.crop(art.getbbox())
    canvas = Image.new("RGBA", (CANVAS, CANVAS))
    canvas.alpha_composite(art, (CANVAS//2-art.width//2, BASELINE+1-art.height))
    canvas.save(base / "design/base.canvas.png")
    write(base / "design/base.palette.json", {"kind": "parts-shared-native-palette", "colors": [list(map(int, c)) for c in palette]})
    sheet = Image.new("RGBA", (CANVAS*SCALE,)*2, (255, 0, 255, 255))
    sheet.alpha_composite(canvas.resize((CANVAS*SCALE,)*2, Image.Resampling.NEAREST))
    sheet.save(base / "design/base-8x.png")
    box = canvas.getbbox()
    crop = canvas.crop((box[0]-8, box[1]-8, box[2]+8, box[3]+8))
    big = crop.resize((crop.width*6, crop.height*6), Image.Resampling.NEAREST)
    square = Image.new("RGBA", (max(big.size),)*2)
    square.alpha_composite(big, ((square.width-big.width)//2, square.height-big.height))
    square.save(base / "design/base-side.png")
    return {"pitch": round(pitch, 3), "native_height": art.height, "palette": len(palette)}


def below_brim_cleanup(strict, loose):
    """Keep the strict hat only down to the brim the loose extraction finds, column by column.

    A tight body difference keeps a hat band that shares the hair's browns but also the
    face lines the redraw moved; a loose one loses the band but keeps the brim clean. In
    each column the lowest loose pixel is the brim; strict pixels below it are debris.
    """
    rgba = np.asarray(strict).copy()
    brim = np.asarray(loose)[..., 3] > 0
    for x in range(rgba.shape[1]):
        rows = np.where(brim[:, x])[0]
        if rows.size:
            rgba[rows.max()+1:, x] = 0
        else:
            rgba[:, x] = 0
    return Image.fromarray(rgba)


def pixel_grid_components(image):
    """8-connected opaque component sizes of an image."""
    from sprite_gen.qa.still_audit import _components
    return _components(np.asarray(image)[..., 3] > 0)


def extract_design(base, name, *, palette_size=8):
    palette_size = PART_COLORS.get(name, palette_size)
    """One drawn design -> the part alone on the base pose, in its own small palette."""
    slot, node, socket, after, region, threshold, mode = PARTS[name]
    body = Image.open(base / "design" / EXTRACT.get(name, {}).get("body", "base.canvas.png")).convert("RGBA")
    raw = Image.open(base / "design" / f"{name}.raw.png").convert("RGBA")
    pitch = SCALE * raw.width / (CANVAS*SCALE)
    dressed = sheets.reduce_cell(raw, pitch, [255, 0, 255], 80, size=(CANVAS, CANVAS))
    rules = EXTRACT.get(name, {})
    part = sheets.subtract_body(dressed, body, rules.get("threshold", threshold), radius=rules.get("radius", 1))
    part = sheets.drop_islands(part, rules.get("minimum", 6), body=body if rules.get("body_colors", True) else None,
                               dark_share=rules.get("dark_share", 0.8))
    part = sheets.keep_outline(part, dressed)
    if slot == "expression":
        # The face is the front of the head: from 40 % of the head's width to just past it,
        # so the ear and the back of the head never enter a face layer.
        alpha = np.asarray(body)[..., 3] > 0
        top = int(np.where(alpha.any(axis=1))[0].min())
        cols = np.where(alpha[top:top+40].any(axis=0))[0]
        left = int(cols.min() + 0.4*(cols.max()-cols.min()))
        region = [left, top+8, int(cols.max())+4, top+38]
    kept = Image.new("RGBA", part.size)
    kept.paste(part.crop(tuple(region)), tuple(region[:2]))
    if rules.get("largest"):
        sizes = pixel_grid_components(kept)
        if sizes:
            kept = sheets.drop_islands(kept, max(sizes)//3, dark_share=1.01)
        kept, _ = pixel_grid.fill_small_holes(kept, 12)
        loose = sheets.drop_islands(sheets.subtract_body(dressed, body, threshold, radius=1), 6, dark_share=1.01)
        kept = below_brim_cleanup(kept, loose)
    if kept.getbbox() is None:
        raise ValueError(f"{name}: nothing left after removing the body")
    colors = extract.build_shared_palette([kept], palette_size)
    kept = native.snap(kept, np.array(colors))
    kept.save(base / "design" / f"{name}.native.png")
    write(base / "design" / f"{name}.palette.json", {"colors": [list(map(int, c)) for c in colors]})
    return {"part": name, "pixels": int((np.asarray(kept)[..., 3] > 0).sum()), "colors": len(colors)}


def stage_motion(base, baseline_library):
    """Loops of the base -> native frames -> strict import -> sockets -> freeze -> motion body."""
    library = base / "library"
    if library.exists():
        raise ValueError(f"{library} exists; use a new --base")
    shutil.copytree(baseline_library, library)
    assembly = library / "assembly.json"
    index, registry = workspace.read_workspace(assembly)
    old = registry[index["motion"]["id"]]
    video = base / "video"
    still = Image.open(base / "design/base.canvas.png").convert("RGBA")
    idle = base / "idle-breathe"
    idle.mkdir(parents=True, exist_ok=True)
    frames, phases = breathe.bake_breathe_sequence([still]*IDLE_FRAMES, BREATHE)
    for n, frame in enumerate(frames):
        frame.save(idle / f"frame-{n:03d}.png")
    built = native.build(assembly, base / "native",
        [("idle", idle, str(IDLE_FPS)), ("walk", video / "side-walk/loop"), ("run", video / "side-run/loop")],
        body_height=json.loads((base / "design/stage-native.json").read_text())["native_height"], baseline=BASELINE,
        palette=base / "design/base.palette.json", motion_id=old["id"], every=[("walk", 2)], resample="lattice",
        outline="darkest")
    imported = parts_motion.import_motion(assembly, Path(built["spec"]), Path(built["source_root"]), strict_motion=True)
    # The head and torso references are found on the first idle sample by matching the base still.
    candidate = workspace.decode_json((library / imported["candidate"]).read_bytes())
    first = candidate["motion"]["clips"][0]["samples"][0]
    frame = Image.open((library / imported["candidate"]).parent / first["image"]["file"]).convert("RGBA")
    still = Image.open(base / "design/base.canvas.png").convert("RGBA")
    sockets = base_sockets(still)
    found = {}
    for name, (position, bounds) in sockets.items():
        x, y = position
        template = still.crop((x+bounds[0], y+bounds[1], x+bounds[2], y+bounds[3]))
        found[name] = parts_sockets.match(template, frame, position, {"bounds": bounds, "radius": 8, "alpha_weight": 3,
                                                                     "min_alpha": 128, "rgb_threshold": 24})["position"]
    clip = candidate["motion"]["clips"][0]
    ref = {"clip": clip["id"], "sample": first["id"]}
    tracks = [{"socket": "head", "reference": {**ref, "position": found["head"]}, "bounds": sockets["head"][1], "radius": 8, "max_cost": 400},
              {"socket": "torso", "reference": {**ref, "position": found["torso"]}, "bounds": sockets["torso"][1], "radius": 8, "max_cost": 400},
              {"socket": "root", "mode": "constant", "reference": {**ref, "position": [0, 0]}}]
    spec = write(base / "socket-track.json", {"kind": "parts-socket-track", "schema_version": 1, "tracks": tracks})
    tracked = parts_sockets.track_sockets(assembly, imported["candidate"], library / imported["annotation_template"], spec,
                                          base / "annotations.tracked.json", accept=True, preview=base / "socket-track.png")
    if tracked["unverified_sockets"]:
        raise ValueError(f"socket proposals need review: {tracked['unverified_examples']}")
    parts_motion.freeze_motion(assembly, imported["candidate"], base / "annotations.tracked.json")
    body = parts_rebind.body_spec(assembly, base / "import/body.json", body_id="sd.equipment.body")
    parts_import.import_pack(assembly, Path(body["spec"]), Path(body["source_root"]))
    write(base / "motion-evidence.json", {"native": built, "quality": imported["motion_quality"], "breathe": BREATHE,
        "breathe_phases": phases,
        "base_sockets": {k: v[0] for k, v in sockets.items()}, "idle_sockets": found,
        "art_approved": False, "release_ready": False})
    return assembly


def base_sockets(still):
    """Authored attachment points on the base still: head top-centre and mid-torso."""
    alpha = np.asarray(still)[..., 3] > 0
    rows = np.where(alpha.any(axis=1))[0]
    top = int(rows.min())
    head_cols = np.where(alpha[top:top+12].any(axis=0))[0]
    torso_y = top + int(0.55*(rows.max()-top))
    torso_cols = np.where(alpha[torso_y])[0]
    return {"head": ([int(head_cols.mean()), top], HEAD_BOUNDS),
            "torso": ([int(torso_cols.mean()), torso_y], [-7, -8, 7, 8])}


def stage_held(base):
    """Import each held design on its socket; pivot = that socket on the base still."""
    assembly = base / "library/assembly.json"
    index, registry = workspace.read_workspace(assembly)
    motion = registry[index["motion"]["id"]]
    rig = registry[index["rig"]["id"]]
    still = Image.open(base / "design/base.canvas.png").convert("RGBA")
    pivots = {name: value[0] for name, value in base_sockets(still).items()} | {"root": [0, 0]}
    # A tracked socket is the place of the same drawn feature in every sample, so a design drawn
    # on the base still hangs with its pivot at that feature's place on the still.
    for name, (slot, node, socket, after, _, _, mode) in PARTS.items():
        if mode != "held":
            continue
        image = base / "design" / f"{name}.native.png"
        ref = {"file": image.name, "sha256": assets.sha256(image.read_bytes())}
        mask = None
        if slot == "headwear":
            # A hat hides the hair it covers: in each column the hat spans, hair above the
            # hat's lowest pixel is masked out (luma 0), so spikes cannot poke through the crown.
            hat = np.asarray(Image.open(image))[..., 3] > 0
            keep = np.full(hat.shape, 255, dtype=np.uint8)
            for x in np.where(hat.any(axis=0))[0]:
                keep[:int(np.where(hat[:, x])[0].max())+1, x] = 0
            mask_file = base / "design" / f"{name}.hair-mask.png"
            Image.fromarray(keep, "L").save(mask_file)
            mask = {"image": {"file": mask_file.name, "sha256": assets.sha256(mask_file.read_bytes())},
                    "target_node": "hair", "space": "part-local", "channel": "luma"}
        follow = "torso" if name == "back_accessory" else None
        sample_map = {}
        for clip in motion["clips"]:
            positions = [s["sockets"]["head" if socket == "head" else "torso"]["position"] for s in clip["samples"]]
            drag = parts_rebind.lag_offsets(positions, Fraction(1, 2), 1) if name in {"hair.a", "hair.b", "back_accessory"} else None
            for n, sample in enumerate(clip["samples"]):
                if socket == "head":
                    pivot, offset = pivots["head"], [0, 0]
                else:
                    pivot = [0, 0]
                    offset = [a-b for a, b in zip(sample["sockets"]["torso"]["position"], pivots["torso"])] if follow else [0, 0]
                if drag:
                    offset = [o+d for o, d in zip(offset, drag[n])]
                sample_map.setdefault(clip["id"], {})[sample["id"]] = {
                    "image": ref, "format": "full-canvas", "pivot": pivot, "target_socket": socket, "offset": offset,
                    "after": after, "before": ["root"] if name == "back_accessory" else [], "mask": mask}
        if name == "back_accessory":
            for samples in sample_map.values():
                for placement in samples.values():
                    placement["after"] = []
        spec = write(base / "import" / f"{name}.json", {"kind": "parts-pack-import", "schema_version": 1, "id": PART_ID[name],
            "design_id": PART_ID[name], "role": "part", "style": rig["style"], "rig": index["rig"], "motion": index["motion"],
            "view": rig["view"], "binding_id": PART_ID[name] + ".binding", "occupies": [slot],
            "pieces": [{"id": slot, "slot": slot, "node": node, "mode": "held", "provides": PROVIDES[slot], "sample_map": sample_map}]})
        parts_import.import_pack(assembly, spec, base / "design")


def stage_sheets(base):
    """Pose sheets for drawing each raster garment on every walk/run sample, and feet close-ups for shoes."""
    assembly = base / "library/assembly.json"
    made = sheets.build_sheet(assembly, base / "sheets/clothing", clips=["walk.right", "run.right"], crop="20,40,108,120",
                              scale=4, page_size=12, aspect="1:1", part="garment")
    sheets.build_sheet(assembly, base / "sheets/feet", clips=["walk.right", "run.right"], crop="32,84,96,116",
                       scale=8, page_size=12, columns=3, aspect="3:2", part="shoes")
    for name in ("footwear.a", "footwear.b"):
        raw = Image.open(base / "design" / f"{name}.raw.png").convert("RGBA")
        k = raw.width / CANVAS
        crop = raw.crop((int(32*k), int(84*k), int(96*k), int(116*k))).resize((64*8, 32*8), Image.Resampling.NEAREST)
        zoom = Image.new("RGBA", crop.size, (255, 0, 255, 255))
        zoom.alpha_composite(crop)
        zoom.save(base / "design" / f"{name}.zoom.png")
    for name, value in PARTS.items():
        if value[-1] == "raster":
            shutil.copy2(base / "design" / f"{name}.raw.png", base / "garment-refs" / f"{name}.png") if (base / "garment-refs").exists() \
                else ((base / "garment-refs").mkdir(), shutil.copy2(base / "design" / f"{name}.raw.png", base / "garment-refs" / f"{name}.png"))
    return made


def stage_clothing(base, only=None):
    assembly = base / "library/assembly.json"
    pages = sorted((base / "sheets/clothing").glob("page-*/pose-sheet.json"))
    report = {}
    for name, (slot, node, socket, after, region, threshold, mode) in PARTS.items():
        if mode != "raster" or (only and name not in only):
            continue
        feet = sorted((base / "sheets/feet").glob("page-*/pose-sheet.json"))
        if slot == "footwear" and feet and all((base / "drawn" / f"{name}.feet.p{g.parent.name.split('-')[1]}.png").exists() for g in feet):
            # Shoes are drawn on a feet-only close-up (8x) with a curated 4-5 color palette:
            # at full-body scale they were ten pixels wide and came back as speckle.
            drawn = [(g, base / "drawn" / f"{name}.feet.p{g.parent.name.split('-')[1]}.png") for g in feet]
            palette, minimum = base / "design" / f"{name}.palette5.json", 6
            if not palette.exists():
                # A shoe is a few pixels wide: a small palette from its own design keeps it clean.
                colors = extract.build_shared_palette([Image.open(base / "design" / f"{name}.native.png").convert("RGBA")], 6)
                write(palette, {"colors": [list(map(int, c)) for c in colors]})
        else:
            drawn = [(guide, base / "drawn" / f"{name}.p{guide.parent.name.split('-')[1]}.png") for guide in pages]
            palette, minimum = base / "design" / f"{name}.palette.json", 12
        sliced = sheets.slice_sheet(assembly, None, None, base / "sliced" / name, pages=drawn, part_id=PART_ID[name], slot=slot,
            node=node, socket=socket, piece=slot, body_threshold=threshold, min_island=minimum, outline=True, near_body=2,
            region=region, tolerance=80, palette=palette, fill_holes=6, close_body=5)
        spec = json.loads(Path(sliced["spec"]).read_text())
        idle_breathing(base, name, spec, Path(sliced["source_root"]), socket)
        for piece in spec["pieces"]:
            piece["provides"] = PROVIDES[slot]
            for samples in piece["sample_map"].values():
                for placement in samples.values():
                    placement["after"] = after
        Path(sliced["spec"]).write_text(json.dumps(spec))
        parts_import.import_pack(assembly, Path(sliced["spec"]), Path(sliced["source_root"]))
        sizes = [r["pixels"] for r in sliced["results"]]
        report[name] = {"samples": sliced["samples"], "empty": sliced["empty"], "px_min": min(sizes), "px_max": max(sizes),
                        "px_spread": round((max(sizes)-min(sizes))/max(max(sizes), 1), 3),
                        "holes_filled": sum(r["holes_filled"] for r in sliced["results"])}
    previous = json.loads((base / "clothing-evidence.json").read_text()) if (base / "clothing-evidence.json").exists() else {}
    report = previous | report
    write(base / "clothing-evidence.json", report)
    return report


def idle_breathing(base, name, spec, root, socket):
    """Idle samples of a raster garment: its base-pose design warped with the body's breathing."""
    index, registry = workspace.read_workspace(base / "library/assembly.json")
    motion = registry[index["motion"]["id"]]
    clip = next(c for c in motion["clips"] if c["state"] == "idle")
    still = Image.open(base / "design/base.canvas.png").convert("RGBA")
    anatomy = breathe.resolve_anatomy(still, BREATHE)
    phases = breathe.fit_breathe_pattern(len(clip["samples"]), BREATHE)
    design = Image.open(base / "design" / f"{name}.native.png").convert("RGBA")
    dressed = still.copy()
    dressed.alpha_composite(design)
    mapping = {}
    for sample, phase in zip(clip["samples"], phases):
        # The warp follows a frame's own silhouette, so warp the dressed still and keep what
        # differs from the warped body: recomposited, it equals the warped dressed still.
        worn = np.asarray(breathe.phase_frame(dressed, BREATHE, phase, anatomy))
        bare = np.asarray(breathe.phase_frame(still, BREATHE, phase, anatomy))
        warped = Image.fromarray(np.where((worn != bare).any(axis=-1)[..., None], worn, 0).astype(np.uint8))
        filename = f"{clip['id']}/{sample['id']}.png"
        (root / filename).parent.mkdir(parents=True, exist_ok=True)
        data = assets.png_bytes(warped)
        (root / filename).write_bytes(data)
        mapping[sample["id"]] = {"image": {"file": filename, "sha256": assets.sha256(data)}, "format": "full-canvas",
                                 "pivot": list(sample["sockets"][socket]["position"]), "target_socket": socket,
                                 "visible": warped.getbbox() is not None}
    spec["pieces"][0]["sample_map"][clip["id"]] = mapping


def stage_appearance(base, selection):
    assembly = base / "library/assembly.json"
    index, registry = workspace.read_workspace(assembly)
    catalog = registry[index["catalog"]["id"]]
    options = {slot["id"]: {o["id"]: o for o in slot["options"]} for slot in catalog["slots"]}
    chosen = {slot: (options[slot][PART_ID[name]] if name else None) for slot, name in selection.items()}
    spec = write(base / "selection.json", {"kind": "parts-appearance-selection", "schema_version": 1,
        "id": "sd.matched.layers", "selections": {slot: None for slot in options} | chosen})
    return parts_appearance.create(assembly, spec)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, type=Path)
    parser.add_argument("stage", choices=("native", "designs", "motion", "held", "sheets", "clothing", "appearance"))
    parser.add_argument("--target-height", type=int, default=84)
    parser.add_argument("--baseline-library", type=Path, help="a previous library/ whose rig, topology and catalog are reused")
    parser.add_argument("--select", type=Path, help="JSON {slot: part name or null}")
    parser.add_argument("--only", nargs="*", help="clothing: only these part names")
    parser.add_argument("--colors", type=int, default=16, help="native: base palette size")
    parser.add_argument("--part-colors", type=int, help="designs: palette size per part (default 8)")
    args = parser.parse_args()
    if args.part_colors:
        PART_COLORS.update({name: args.part_colors for name in PARTS})
    if args.stage == "native":
        result = stage_native(args.base, args.target_height, args.colors)
        write(args.base / "design/stage-native.json", result)
    elif args.stage == "designs":
        result = [extract_design(args.base, name) for name in PARTS if (args.base / "design" / f"{name}.raw.png").exists()]
    elif args.stage == "motion":
        result = str(stage_motion(args.base, args.baseline_library))
    elif args.stage == "held":
        result = stage_held(args.base)
    elif args.stage == "sheets":
        result = {k: v for k, v in stage_sheets(args.base).items() if k in ("cells", "pages")}
    elif args.stage == "clothing":
        result = stage_clothing(args.base, args.only)
    else:
        result = stage_appearance(args.base, json.loads(args.select.read_text()))
    print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
