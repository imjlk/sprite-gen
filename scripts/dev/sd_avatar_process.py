# SPDX-License-Identifier: Apache-2.0
"""Rebuild the HQ SD explorer by the pose-first avatar process (docs/parts/09-avatar-process.md).

Reuses the HQ base (assets/sd-explorer-hq-20261006): its native loops, sockets, designs and
the garments already drawn on every pose. What changes is the structure:

  library   Maple-style topology (body pieces, garment nodes, a draw table) -> motion with pose IDs
  labels    pose sheets, one cell per pose, for painting the body pieces
  segments  painted labels -> segmented body pack
  garments  HQ garment drawings -> pose_map pieces -> split into torso/sleeve, waist/legs, feet, hands
  held      hair (full and capped by cover rules), faces, a hat that covers, back accessory
  appearance, coverage, export
Visual approval stays a human review; nothing here approves art.
"""
from __future__ import annotations

import argparse
import json
from fractions import Fraction
from pathlib import Path
import shutil
import sys

from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
import sd_explorer_rebuild as rebuild  # noqa: E402

from sprite_gen._deps import np  # noqa: E402
from sprite_gen.compose import export_parts  # noqa: E402
from sprite_gen.curate import parts_appearance, parts_motion, parts_poses  # noqa: E402
from sprite_gen.frames import parts_import, parts_pose_sheet as sheets, parts_rebind, parts_segments  # noqa: E402
from sprite_gen.spec import parts_assets as assets, parts_contract as contract, parts_workspace as workspace  # noqa: E402

HQ = Path("assets/sd-explorer-hq-20261006")
BACK, FRONT = "back", "front"
# Back to front. Body pieces and what is worn on them share a node; on one node the body draws first.
LAYERS = ["back_accessory", "arm_back", "hand_back", "leg_back", "foot_back", "root", "waist", "torso",
          "eyes", "hair", "headwear", "leg_front", "foot_front", "arm_front", "hand_front"]
SEGMENTS = {  # label colors painted over each pose; key = body piece node
    "arm_back": {"color": "#00a000", "provides": ["hand_back"]},
    "leg_back": {"color": "#00ffff", "provides": ["foot_back"]},
    "root": {"color": "#ffff00", "provides": []},
    "leg_front": {"color": "#ff8000", "provides": ["foot_front"]},
    "arm_front": {"color": "#ffffff", "provides": ["hand_front"]},
}
SLOTS = {  # slot: (group, label, nodes, sockets)
    "hair": ("head", "헤어", ["hair"], ["head"]),
    "expression": ("face", "표정", ["eyes"], ["head"]),
    "upper": ("clothing", "상의", ["torso", "arm_back", "arm_front"], ["torso"]),
    "lower": ("clothing", "하의", ["waist", "leg_back", "leg_front"], ["root"]),
    "footwear": ("clothing", "신발", ["foot_back", "foot_front"], ["root"]),
    "headwear": ("equipment", "모자", ["headwear"], ["head"]),
    "gloves": ("equipment", "장갑", ["hand_back", "hand_front"], ["root"]),
    "back_accessory": ("equipment", "등 장비", ["back_accessory"], ["root"]),
}
# Garment drawn on every pose -> pieces by the body piece under each pixel (PIECE:NODE=SEGMENTS).
SPLITS = {
    "upper": [("sleeve_back", "arm_back", ["arm_back"]), ("torso", "torso", ["*"]),
              ("sleeve_front", "arm_front", ["arm_front"])],
    "lower": [("leg_back", "leg_back", ["leg_back"]), ("waist", "waist", ["*"]), ("leg_front", "leg_front", ["leg_front"])],
    "footwear": [("shoe_back", "foot_back", ["*"]), ("shoe_front", "foot_front", ["leg_front", "arm_front"])],
    "gloves": [("glove_back", "hand_back", ["*"]), ("glove_front", "hand_front", ["arm_front", "leg_front"])],
}
GARMENTS = ["upper.a", "upper.b", "lower.a", "lower.b", "footwear.a", "footwear.b", "gloves"]
LABEL_PROMPT = """Edit image 1. Image 1 is a pixel-art pose sheet: copies of one bald chibi character in pure side view facing right, in different idle, walking and running poses, each in its own grid cell on a flat pure magenta #FF00FF background.

Turn every figure into a flat color label map. Fill every pixel of each figure, including its outline pixels, with exactly one of these five flat colors and nothing else:
- the far arm, the one partly hidden behind the torso, with its hand: green #00A000
- the far leg, the one partly hidden behind the other leg, with its foot: cyan #00FFFF
- the head, neck, torso, shorts and hips: yellow #FFFF00
- the near leg, drawn in front, with its foot: orange #FF8000
- the near arm, drawn in front of the torso, with its hand: white #FFFFFF
Keep every figure's exact silhouette, size and position in its cell; where the near arm overlaps the torso the overlap is white, where the near leg overlaps the far leg it is orange. No outlines, no shading, no other colors, no text. Keep the same grid, image size and the flat #FF00FF background."""


def write(path, value):
    return rebuild.write(path, value)


def seal_write(root, name, value):
    doc = contract.seal(value)
    (root / name).parent.mkdir(parents=True, exist_ok=True)
    (root / name).write_bytes(workspace.json_bytes(doc))
    return doc


def stage_library(base):
    """New topology/rig/catalog, the HQ loops re-imported, HQ sockets by raster hash, pose IDs, freeze."""
    root = base / "library"
    if root.exists():
        raise ValueError(f"{root} exists; use a new --base")
    root.mkdir(parents=True)
    nodes = [{"id": "root"}] + [{"id": n, "parent": "root", "required": False} for n in LAYERS if n != "root"]
    topology = seal_write(root, "profiles/topology.json", {"kind": "parts-topology", "schema_version": 1,
        "id": "sd.avatar.topology", "nodes": nodes,
        "sockets": [{"id": "root", "node": "root", "required": True}, {"id": "head", "node": "root", "required": True},
                    {"id": "torso", "node": "root", "required": True}],
        "slots": [{"id": slot, "nodes": n, "sockets": s, "optional": True, "empty_policy": "no-piece"}
                  for slot, (_, _, n, s) in SLOTS.items()],
        "layers": LAYERS})
    rig = seal_write(root, "profiles/rig.json", {"kind": "parts-rig", "schema_version": 1, "id": "sd.avatar.rig",
        "topology": contract.reference(topology), "view": "right", "style": "pixel.v1",
        "canvas": {"width": rebuild.CANVAS, "height": rebuild.CANVAS}, "default_body_ids": ["sd.avatar.body"]})
    hq_index, hq_registry = workspace.read_workspace(HQ / "library/assembly.json")
    hq_catalog = hq_registry[hq_index["catalog"]["id"]]
    catalog = seal_write(root, "catalog.json", {"kind": "parts-catalog", "schema_version": 1, "id": "sd.avatar.catalog",
        "rig": contract.reference(rig), "topology": contract.reference(topology), "groups": hq_catalog["groups"],
        "slots": [{"id": slot, "group": group, "label": label} for slot, (group, label, _, _) in SLOTS.items()],
        "palette_channels": hq_catalog["palette_channels"]})
    assembly = root / "assembly.json"
    assembly.write_bytes(workspace.json_bytes({"kind": "parts-workspace", "schema_version": 1, "id": "sd.avatar.workspace",
        "documents": {topology["id"]: "profiles/topology.json", rig["id"]: "profiles/rig.json", catalog["id"]: "catalog.json"},
        "rig": contract.reference(rig), "catalog": contract.reference(catalog), "states": ["idle", "walk", "run"]}))
    spec = json.loads((HQ / "native/motion-import.json").read_text())
    spec.update(id="sd.avatar.motion", rig=contract.reference(rig))
    write(base / "motion-import.json", spec)
    imported = parts_motion.import_motion(assembly, base / "motion-import.json", HQ / "native", strict_motion=True)
    hq = json.loads((HQ / "annotations.tracked.json").read_text())
    sockets = {row["image_sha256"]: row["sockets"] for clip in hq["clips"].values() for row in clip.values()}
    draft = root / imported["annotation_template"]
    value = json.loads(draft.read_text())
    for clip in value["clips"].values():
        for row in clip.values():
            row["sockets"] = sockets[row["image_sha256"]]
    write(base / "annotations.json", value)
    poses = parts_poses.assign(assembly, base / "annotations.json")
    frozen = parts_motion.freeze_motion(assembly, imported["candidate"], base / "annotations.json")
    shutil.copytree(HQ / "design", base / "design", dirs_exist_ok=True)
    return {"motion": frozen["motion"], "poses": poses["poses"], "shared": poses["shared"],
            "quality": [{k: r[k] for k in ("clip", "status")} for r in imported["motion_quality"]]}


def stage_labels(base):
    assembly = base / "library/assembly.json"
    return sheets.build_sheet(assembly, base / "sheets/labels", by_pose=True, scale=4, crop="24,20,104,116",
                              page_size=12, aspect="1:1", part="body label")


def stage_label_gen(base, only=None):
    import subprocess
    prompt = base / "prompts/labels.txt"
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text(LABEL_PROMPT)
    results = []
    for page in sorted((base / "sheets/labels").glob("page-*/pose-sheet.png")):
        number = page.parent.name.split("-")[1]
        out = base / "drawn" / f"labels.p{number}.png"
        if (only and number not in only) or out.exists():
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        cmd = [".venv/bin/sprite-gen", "gen", "--provider", "codex", "--model", "gpt-5.6-sol", "--prompt-file", str(prompt),
               "--out", str(out), "--report", str(out.with_suffix(".report.json")), "--ref", str(page)]
        done = subprocess.run(cmd, capture_output=True, text=True)
        results.append({"page": number, "ok": done.returncode == 0, "err": done.stderr[-300:]})
    return results


def stage_labels_fix(base):
    """Read the painted pages per pose and swap near/far limb labels the model mixed up.

    The video shades a far limb darker than the near one. The model painted legs right but
    swapped arms, so a pair is swapped when its "near" label covers darker body pixels.
    """
    from sprite_gen.compose import compose_parts
    assembly = base / "library/assembly.json"
    index, registry, motion, _ = _motion(assembly)
    clips = {c["id"]: c for c in motion["clips"]}
    load = compose_parts.AssetLoader(assembly.parent, index)
    rgb = {n: tuple(int(v["color"][i:i+2], 16) for i in (1, 3, 5)) for n, v in SEGMENTS.items()}
    pages = [(g, base / "drawn" / f"labels.p{g.parent.name.split('-')[1]}.png")
             for g in sorted((base / "sheets/labels").glob("page-*/pose-sheet.json"))]
    out = base / "labels"
    out.mkdir(parents=True, exist_ok=True)
    report = {}
    for entry, sample, reduced, sheet in sheets.drawn_cells(pages, index, clips, 80):
        native = Image.new("RGBA", tuple(sheet["canvas"]))
        native.alpha_composite(reduced, tuple(sheet["crop"][:2]))
        paint = np.asarray(native).copy()
        body = np.asarray(load(motion, sample["image"]))
        luma = body[..., :3] @ np.array([0.299, 0.587, 0.114])
        swapped = []
        for far, near in (("arm_back", "arm_front"), ("leg_back", "leg_front")):
            masks = {n: (np.abs(paint[..., :3].astype(int) - rgb[n]).max(-1) <= 60) & (paint[..., 3] > 0) & (body[..., 3] > 0)
                     for n in (far, near)}
            if masks[far].sum() >= 4 and masks[near].sum() >= 4 and luma[masks[near]].mean() < luma[masks[far]].mean():
                paint[masks[far], :3], paint[masks[near], :3] = rgb[near], rgb[far]
                swapped.append(far.split("_")[0])
        Image.fromarray(paint).save(out / f"{sample['pose_id']}.png")
        report[sample["pose_id"]] = swapped
    write(base / "labels-fix.json", report)
    return {"poses": len(report), "swapped": {k: v for k, v in report.items() if v}}


def stage_segments(base):
    assembly = base / "library/assembly.json"
    colors = write(base / "segment-colors.json", SEGMENTS)
    pages = [(g, base / "drawn" / f"labels.p{g.parent.name.split('-')[1]}.png")
             for g in sorted((base / "sheets/labels").glob("page-*/pose-sheet.json"))]
    labels = base / "labels" if (base / "labels").exists() else None
    result = parts_segments.segment_body(assembly, base / "body", colors=colors, labels=labels,
                                         pages=() if labels else pages, body_id="sd.avatar.body",
                                         tolerance=60, key_tolerance=80)
    parts_import.import_pack(assembly, Path(result["spec"]), Path(result["source_root"]))
    write(base / "segments-evidence.json", {k: result[k] for k in ("cut", "loosely_painted", "unprovided_required_nodes", "results")})
    return {k: result[k] for k in ("cut", "loosely_painted", "unprovided_required_nodes")}


def _motion(assembly):
    index, registry = workspace.read_workspace(assembly)
    return index, registry, registry[index["motion"]["id"]], registry[index["rig"]["id"]]


def stage_garments(base, only=None):
    """HQ garments drawn on every sample -> one drawing per pose -> pieces by body piece -> import."""
    assembly = base / "library/assembly.json"
    index, registry, motion, rig = _motion(assembly)
    pose_of = {(c["id"], s["id"]): s["pose_id"] for c in motion["clips"] for s in c["samples"]}
    report = {}
    for name in GARMENTS:
        if only and name not in only:
            continue
        slot = name.split(".")[0]
        source = HQ / "sliced" / name
        hq = json.loads((source / "import.json").read_text())
        pose_map = {}
        for clip_id, mapping in hq["pieces"][0]["sample_map"].items():
            for sample_id, placement in mapping.items():
                pose = pose_of[clip_id, sample_id]
                if pose not in pose_map:
                    pose_map[pose] = {k: placement[k] for k in ("image", "format", "pivot", "target_socket", "visible")}
        redraw = base / "redraw" / name
        if redraw.exists():
            # Poses that failed review were drawn again; their drawings replace the HQ ones.
            target = base / "garments" / name / "src"
            shutil.copytree(source, target, dirs_exist_ok=True)
            for pose in json.loads((redraw / "redraw.json").read_text())["poses"]:
                file = redraw / f"{pose}.png"
                (target / "redraw").mkdir(exist_ok=True)
                shutil.copy2(file, target / "redraw" / file.name)
                pose_map[pose] = {**pose_map[pose], "image": {"file": f"redraw/{file.name}",
                                  "sha256": assets.sha256(file.read_bytes())}, "visible": True}
            source = target
        spec = {"kind": "parts-pack-import", "schema_version": 1, "id": rebuild.PART_ID[name], "design_id": rebuild.PART_ID[name],
                "role": "part", "style": rig["style"], "rig": index["rig"], "motion": index["motion"], "view": rig["view"],
                "binding_id": rebuild.PART_ID[name] + ".binding", "occupies": [slot],
                "pieces": [{"id": slot, "slot": slot, "node": SLOTS[slot][2][0], "mode": "raster", "pose_map": pose_map}]}
        write(base / "garments" / name / "import.json", spec)
        split = parts_segments.split_piece(base / "garments" / name / "import.json", base / "body/segments.json",
                                           base / "split" / name, piece=slot, into=SPLITS[slot], source_root=source)
        parts_import.import_pack(assembly, Path(split["spec"]), Path(split["source_root"]))
        report[name] = {"poses": len(pose_map), "pieces": split["pieces"]}
    write(base / "garments-evidence.json", report)
    return report


def stage_held(base, capped=()):
    """Hair (full and, where drawn, capped), faces, a covering hat and the back accessory on their sockets."""
    assembly = base / "library/assembly.json"
    index, registry, motion, rig = _motion(assembly)
    still = Image.open(base / "design/base.canvas.png").convert("RGBA")
    pivots = {name: value[0] for name, value in rebuild.base_sockets(still).items()}
    nodes = {"hair": "hair", "expression": "eyes", "headwear": "headwear", "back_accessory": "back_accessory"}
    report = {}
    for name in ("hair.a", "hair.b", "hair.c", "expression.a", "expression.b", "expression.c", "headwear", "back_accessory"):
        slot = rebuild.PARTS[name][0]
        drawings = [(slot, f"{name}.native.png", None)]
        if slot == "hair":
            drawings = [(slot, f"{name}.native.png", {"tag": "headwear", "show": "uncovered"})]
            if (base / "design" / f"{name}.capped.native.png").exists():
                drawings.append(("capped", f"{name}.capped.native.png", {"tag": "headwear", "show": "covered"}))
        pieces = []
        for piece_id, file, rule in drawings:
            ref = {"file": file, "sha256": assets.sha256((base / "design" / file).read_bytes())}
            sample_map = {}
            for clip in motion["clips"]:
                socket = "head" if slot != "back_accessory" else "torso"
                positions = [s["sockets"][socket]["position"] for s in clip["samples"]]
                drag = parts_rebind.lag_offsets(positions, Fraction(1, 2), 1) if slot in {"hair", "back_accessory"} else None
                for n, sample in enumerate(clip["samples"]):
                    if slot == "back_accessory":
                        # Hung on root, following the torso's move from its place on the still.
                        pivot, target = [0, 0], "root"
                        offset = [a-b for a, b in zip(sample["sockets"]["torso"]["position"], pivots["torso"])]
                    else:
                        pivot, target, offset = pivots["head"], "head", [0, 0]
                    if drag:
                        offset = [o+d for o, d in zip(offset, drag[n])]
                    sample_map.setdefault(clip["id"], {})[sample["id"]] = {
                        "image": ref, "format": "full-canvas", "pivot": pivot, "target_socket": target, "offset": offset}
            piece = {"id": piece_id, "slot": slot, "node": nodes[slot], "mode": "held", "sample_map": sample_map}
            if rule:
                piece["cover_rule"] = rule
            pieces.append(piece)
        spec = {"kind": "parts-pack-import", "schema_version": 1, "id": rebuild.PART_ID[name], "design_id": rebuild.PART_ID[name],
                "role": "part", "style": rig["style"], "rig": index["rig"], "motion": index["motion"], "view": rig["view"],
                "binding_id": rebuild.PART_ID[name] + ".binding", "occupies": [slot], "pieces": pieces}
        if slot == "headwear":
            spec["covers"] = ["headwear"]
        write(base / "import" / f"{name}.json", spec)
        parts_import.import_pack(assembly, base / "import" / f"{name}.json", base / "design")
        report[name] = [p["id"] for p in pieces]
    return report


CAPPED_PROMPT = """Edit image 1. Image 1 is a pixel-art chibi character in pure side view facing right, wearing a cap, on a flat pure magenta #FF00FF background. Image 2 is the same character without the cap, showing its hair.

Give the character in image 1 exactly the hair of image 2 (same colors, shading and strand style) as it looks pressed under the cap: no hair above, on or through the cap; the hair only shows below the brim and the cap's lower edge, at the side of the head, over the ear, at the nape and a little over the forehead, following the head's shape. Keep the cap exactly as it is, unchanged, drawn over the hair. Change nothing else: keep the head, face, body, outline, position, size and the flat #FF00FF background exactly as they are. Crisp hard-edged pixel art on the same grid as image 1 (each art pixel an 8x8 block), a 1-pixel dark outline, no anti-aliasing, no blur, no text."""


def _big(image, path):
    sheet = Image.new("RGBA", (rebuild.CANVAS*rebuild.SCALE,)*2, (255, 0, 255, 255))
    sheet.alpha_composite(image.resize(sheet.size, Image.Resampling.NEAREST))
    sheet.save(path)
    return path


def stage_capped_gen(base):
    """Ask the image model for each hair as it looks under the hat (a drawing, not a cut)."""
    import subprocess
    design = base / "design"
    still = Image.open(design / "base.canvas.png").convert("RGBA")
    hatted = still.copy()
    hatted.alpha_composite(Image.open(design / "headwear.native.png").convert("RGBA"))
    hatted.save(design / "base.hat.canvas.png")
    _big(hatted, design / "base.hat-8x.png")
    prompt = base / "prompts/capped.txt"
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text(CAPPED_PROMPT)
    results = []
    for name in ("hair.a", "hair.b", "hair.c"):
        out = design / f"{name}.capped.raw.png"
        if out.exists():
            continue
        haired = still.copy()
        haired.alpha_composite(Image.open(design / f"{name}.native.png").convert("RGBA"))
        ref = _big(haired, design / f"{name}.on-base-8x.png")
        cmd = [".venv/bin/sprite-gen", "gen", "--provider", "codex", "--model", "gpt-5.6-sol", "--prompt-file", str(prompt),
               "--out", str(out), "--report", str(out.with_suffix(".report.json")),
               "--ref", str(design / "base.hat-8x.png"), "--ref", str(ref)]
        done = subprocess.run(cmd, capture_output=True, text=True)
        results.append({"hair": name, "ok": done.returncode == 0, "err": done.stderr[-300:]})
    return results


def stage_capped_extract(base, threshold=40):
    """The drawn capped hair minus the hatted base, kept below the hat, in the hair's own palette."""
    design = base / "design"
    body = Image.open(design / "base.hat.canvas.png").convert("RGBA")
    hat = np.asarray(Image.open(design / "headwear.native.png").convert("RGBA"))[..., 3] > 0
    report = {}
    for name in ("hair.a", "hair.b", "hair.c"):
        raw = Image.open(design / f"{name}.capped.raw.png").convert("RGBA")
        pitch = rebuild.SCALE * raw.width / (rebuild.CANVAS*rebuild.SCALE)
        dressed = sheets.reduce_cell(raw, pitch, [255, 0, 255], 80, size=(rebuild.CANVAS, rebuild.CANVAS))
        part = sheets.subtract_body(dressed, body, threshold, radius=1)
        part = sheets.drop_islands(part, 6, body=body)
        part = sheets.keep_outline(part, dressed)
        pixels = np.asarray(part).copy()
        pixels[hat] = 0  # under the hat nothing shows; the hat draws over the hair anyway
        region = rebuild.PARTS[name][4]
        kept = np.zeros_like(pixels)
        kept[region[1]:region[3], region[0]:region[2]] = pixels[region[1]:region[3], region[0]:region[2]]
        colors = json.loads((design / f"{name}.palette.json").read_text())["colors"]
        art = rebuild.native.snap(Image.fromarray(kept), np.array(colors))
        art.save(design / f"{name}.capped.native.png")
        report[name] = int((np.asarray(art)[..., 3] > 0).sum())
    return report


def contact_sheet(base, selection, out, *, crop=(20, 16, 108, 120), scale=3, columns=8):
    """Every pose once in the given outfit, for human review (a pose per cell, labelled)."""
    from PIL import ImageDraw
    from sprite_gen.compose.parts_runtime import PartsPlayer
    assembly = base / "library/assembly.json"
    index, registry = workspace.read_workspace(assembly)
    player = PartsPlayer.from_workspace(assembly)
    player.set_selection({slot: contract.reference(registry[rebuild.PART_ID[name]]) if name else None
                          for slot, name in selection.items()})
    seen, frames = set(), []
    for clip in player.plan["clips"]:
        player.set_state(clip["state"])
        start = Fraction(0)
        motion = registry[index["motion"]["id"]]
        poses = {s["id"]: s["pose_id"] for c in motion["clips"] if c["id"] == clip["id"] for s in c["samples"]}
        for sample in clip["samples"]:
            if poses[sample["id"]] not in seen:
                seen.add(poses[sample["id"]])
                player.seek(start / clip["timebase"])
                frames.append((poses[sample["id"]], player.frame()[0]))
            start += sample["duration_ticks"]
    w, h = (crop[2]-crop[0])*scale, (crop[3]-crop[1])*scale
    rows = (len(frames)+columns-1)//columns
    sheet = Image.new("RGBA", (w*columns, h*rows), (232, 232, 232, 255))
    draw = ImageDraw.Draw(sheet)
    for n, (pose, frame) in enumerate(frames):
        x, y = n % columns * w, n // columns * h
        sheet.alpha_composite(frame.crop(crop).resize((w, h), Image.Resampling.NEAREST), (x, y))
        draw.text((x+3, y+2), pose, fill=(0, 0, 0, 255))
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return str(out)


# New designs made by the process: held parts drawn once, raster garments drawn per pose.
NEW = {
    "hair.c": ("hair", "long dark auburn-red hair tied in a high ponytail with a teal hair band, the tail hanging behind the head "
               "past the nape, side locks over the ear, chunky clusters with clear highlights"),
    "expression.c": ("expression", "a determined face seen in profile: one large chibi eye with a focused dark green iris and a white "
                     "highlight, a slanted thick eyebrow and a small firm mouth"),
    "upper.c": ("upper", "a red pullover hoodie with long sleeves reaching the wrists, ribbed cuffs and hem, a front pocket and the "
                "hood lying down on the back, replacing the light-gray undershirt"),
    "lower.c": ("lower", "blue denim jeans reaching the ankles with slightly rolled cuffs, stitched seams and a brown belt, "
                "replacing the charcoal shorts"),
    "footwear.c": ("footwear", "red canvas high-top sneakers with white rubber soles, white toe caps and white laces on both feet"),
}
rebuild.PARTS.update({
    "hair.c": ("hair", "head", "head", [], [16, 0, 112, 84], 40, "held"),
    "expression.c": ("expression", "head", "head", [], [56, 30, 80, 60], 24, "held"),
    "upper.c": ("upper", "torso", "torso", [], [14, 40, 114, 104], 40, "raster"),
    "lower.c": ("lower", "root", "root", [], [20, 78, 108, 116], 20, "raster"),
    "footwear.c": ("footwear", "root", "root", [], [20, 84, 108, 120], 20, "raster"),
})
rebuild.EXTRACT.update({"expression.c": {"radius": 0, "dark_share": 1.01, "minimum": 2, "body_colors": False}})
rebuild.PART_ID.update({name: "sd.wardrobe." + name for name in NEW})
GARMENT_WORDS = {"upper.c": "keeping its shorts", "lower.c": "covering both legs completely down to the ankles",
                 "footwear.c": ""}
# A short last page came back re-spaced (8 cells redrawn as 3+3+2), so the layout is spelled out.
KEEP_LAYOUT = (" Keep every figure exactly in its own cell where it is in image 1: do not move, rearrange, re-space or"
               " add figures; empty grid positions stay empty #FF00FF.")
CLOTHING_CROP, FEET_CROP = "20,40,108,120", "32,84,96,116"


def _codex(prompt_text, out, refs, prompt_file):
    import subprocess
    if out.exists():
        return {"out": out.name, "ok": True, "cached": True}
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(prompt_text)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [".venv/bin/sprite-gen", "gen", "--provider", "codex", "--model", "gpt-5.6-sol", "--prompt-file", str(prompt_file),
           "--out", str(out), "--report", str(out.with_suffix(".report.json"))]
    for ref in refs:
        cmd += ["--ref", str(ref)]
    for _ in range(2):
        done = subprocess.run(cmd, capture_output=True, text=True)
        if done.returncode == 0:
            return {"out": out.name, "ok": True}
    return {"out": out.name, "ok": False, "err": done.stderr[-300:]}


def _parallel(jobs):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(3) as pool:
        return list(pool.map(lambda job: _codex(*job), jobs))


def stage_new_designs(base):
    """Each new design drawn once on the base still (the design reference and palette)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("hq_design", HQ / "design_parts.py")
    template = (HQ / "design_parts.py").read_text().split('TEMPLATE = """', 1)[1].split('"""', 1)[0]
    return _parallel([(template.format(desc=desc), base / "design" / f"{name}.raw.png", [base / "design/base-8x.png"],
                       base / "prompts" / f"{name}.txt") for name, (_, desc) in NEW.items()])


def stage_new_extract(base):
    rebuild.PART_COLORS.update({name: 12 for name in NEW})
    report = [rebuild.extract_design(base, name) for name in NEW]
    raw = Image.open(base / "design/footwear.c.raw.png").convert("RGBA")
    k = raw.width / rebuild.CANVAS
    crop = raw.crop((int(32*k), int(84*k), int(96*k), int(116*k))).resize((64*8, 32*8), Image.Resampling.NEAREST)
    zoom = Image.new("RGBA", crop.size, (255, 0, 255, 255))
    zoom.alpha_composite(crop)
    zoom.save(base / "design/footwear.c.zoom.png")
    return report


def stage_new_sheets(base):
    assembly = base / "library/assembly.json"
    clothing = sheets.build_sheet(assembly, base / "sheets/clothing", by_pose=True, crop=CLOTHING_CROP, scale=4,
                                  page_size=12, aspect="1:1", part="garment")
    feet = sheets.build_sheet(assembly, base / "sheets/feet", by_pose=True, crop=FEET_CROP, scale=8, page_size=12,
                              columns=3, aspect="3:2", part="shoes")
    return {"clothing": clothing["cells"], "feet": feet["cells"]}


def _garment_prompts():
    text = (HQ / "generate_garments.py").read_text()
    style = text.split('STYLE = (', 1)[1].split('")\n', 1)[0]
    style = "".join(part.split('"', 1)[1].rsplit('"', 1)[0] for part in style.split("\n") if '"' in part)
    garment = text.split('GARMENT = """', 1)[1].split('"""', 1)[0]
    shoe = text.split('SHOE = """', 1)[1].split('"""', 1)[0]
    return garment, shoe, style


def stage_new_draw(base, only=None):
    """Each new raster garment drawn on every pose page (dressed sheets, the design as reference)."""
    garment, shoe, style = _garment_prompts()
    jobs = []
    for name, (slot, desc) in NEW.items():
        if rebuild.PARTS[name][-1] != "raster" or (only and name not in only):
            continue
        feet = slot == "footwear"
        folder = base / "sheets" / ("feet" if feet else "clothing")
        for page in sorted(folder.glob("page-*/pose-sheet.png")):
            number = page.parent.name.split("-")[1]
            if feet:
                text = shoe.format(desc=desc) + style.format(px=8)
                refs = [page, base / "design" / f"{name}.zoom.png"]
                out = base / "drawn" / f"{name}.feet.p{number}.png"
            else:
                text = garment.format(garment=desc, where=GARMENT_WORDS[name]) + style.format(px=4) + KEEP_LAYOUT
                refs = [page, base / "design" / f"{name}.raw.png"]
                out = base / "drawn" / f"{name}.p{number}.png"
            jobs.append((text, out, refs, base / "prompts" / f"{out.stem}.txt"))
    return _parallel(jobs)


def stage_new_slice(base, only=None):
    """Slice the drawn pages by pose, split by body piece and import."""
    assembly = base / "library/assembly.json"
    report = {}
    for name in NEW:
        slot, node, socket, _, region, threshold, mode = rebuild.PARTS[name]
        if mode != "raster" or (only and name not in only):
            continue
        feet = slot == "footwear"
        folder = base / "sheets" / ("feet" if feet else "clothing")
        pages = [(g, base / "drawn" / f"{name}{'.feet' if feet else ''}.p{g.parent.name.split('-')[1]}.png")
                 for g in sorted(folder.glob("page-*/pose-sheet.json"))]
        palette = base / "design" / f"{name}.palette.json"
        sliced = sheets.slice_sheet(assembly, None, None, base / "sliced" / name, pages=pages, part_id=rebuild.PART_ID[name],
            slot=slot, node=SLOTS[slot][2][0], socket=socket, piece=slot, body_threshold=threshold,
            min_island=6 if feet else 12, outline=True, near_body=2, region=region, tolerance=80, palette=palette,
            fill_holes=6, close_body=5, by_pose=True)
        split = parts_segments.split_piece(Path(sliced["spec"]), base / "body/segments.json", base / "split" / name,
                                           piece=slot, into=SPLITS[slot])
        parts_import.import_pack(assembly, Path(split["spec"]), Path(split["source_root"]))
        report[name] = {"poses": sliced["poses"], "empty": sliced["empty"], "pieces": split["pieces"]}
    write(base / "new-evidence.json", report)
    return report


REDRAW_SHOE = """Edit image 1, a pixel-art close-up sheet of one chibi character's lower legs in different running poses, pure side view facing right, each pose in its own grid cell on a flat pure magenta #FF00FF background. The legs already wear long dark brown cargo pants down to the ankles; the feet are bare. Image 2 shows the exact boots: {desc}.

Put exactly these boots on both bare feet in every cell, following each foot's angle, size and position. The boot top stops at the ankle, just under the pants hem: never paint over the pants or the legs. Keep the boots simple and readable for a tiny game sprite: the same clean boot shape in every cell, the colors of image 2, a crisp 1-pixel outline, no texture, no speckles. Change nothing else: keep the pants, legs, outline, grid, image size and the flat #FF00FF background exactly as they are. """


def stage_redraw(base, name="footwear.b", wearing=("lower.b",), poses=("run.02", "run.03", "run.04", "run.08", "run.09",
                                                                         "run.10", "run.11")):
    """Redraw one part on the poses that failed review, over the parts it is worn with.

    The sheet shows the body already wearing `wearing`, so the model cannot paint the
    shin as a boot shaft; the drawing is cut against that same dressed body.
    """
    from sprite_gen.compose.parts_runtime import PartsPlayer
    from sprite_gen.frames import pixel_grid
    assembly = base / "library/assembly.json"
    index, registry, motion, _ = _motion(assembly)
    player = PartsPlayer.from_workspace(assembly)
    slots = {rebuild.PARTS[w][0]: contract.reference(registry[rebuild.PART_ID[w]]) for w in wearing}
    player.set_selection({slot["id"]: slots.get(slot["id"]) for slot in registry[index["catalog"]["id"]]["slots"]})
    crop, scale, key = [32, 80, 96, 116], 8, (255, 0, 255)
    under = {}
    for clip in motion["clips"]:
        player.set_state(clip["state"])
        start = Fraction(0)
        for sample in clip["samples"]:
            if sample["pose_id"] in poses and sample["pose_id"] not in under:
                player.seek(start / clip["timebase"])
                under[sample["pose_id"]] = (player.frame()[0], sample)
            start += sample["duration_ticks"]
    cw, ch = (crop[2]-crop[0])*scale, (crop[3]-crop[1])*scale
    columns, gutter = 3, 16
    rows = (len(poses)+columns-1)//columns
    size = (gutter + columns*(cw+gutter), gutter + rows*(ch+gutter))
    side = max(size[0], int(size[1]*3/2))
    sheet = Image.new("RGBA", (side, int(side*2/3)), (*key, 255))
    origins = {}
    for n, pose in enumerate(poses):
        x, y = gutter + n % columns*(cw+gutter), gutter + n//columns*(ch+gutter)
        sheet.alpha_composite(under[pose][0].crop(crop).resize((cw, ch), Image.Resampling.NEAREST), (x, y))
        origins[pose] = (x, y)
    folder = base / "redraw" / name
    folder.mkdir(parents=True, exist_ok=True)
    sheet.save(folder / "sheet.png")
    desc = {"footwear.b": "tan-brown leather ankle boots with laces and dark soles"}[name]
    result = _codex(REDRAW_SHOE.format(desc=desc) + _garment_prompts()[2].format(px=8), folder / "drawn.png",
                    [folder / "sheet.png", base / "design" / f"{name}.zoom.png"], base / "prompts" / f"redraw.{name}.txt")
    if not result["ok"]:
        return result
    drawn = Image.open(folder / "drawn.png").convert("RGBA")
    ratio = drawn.width / sheet.width
    colors = np.array(json.loads((base / "design" / f"{name}.palette5.json").read_text())["colors"])
    report = {}
    for pose in poses:
        x, y = origins[pose]
        box = tuple(int(round(v*ratio)) for v in (x, y, x+cw, y+ch))
        cell = sheets.reduce_cell(drawn.crop(box), scale*ratio, list(key), 80, size=(cw//scale, ch//scale))
        dressed_under = under[pose][0].crop(crop)
        part = sheets.subtract_body(cell, dressed_under, 20)
        part = sheets.drop_islands(part, 6, body=dressed_under)
        part = sheets.keep_outline(part, cell)
        part, _ = pixel_grid.fill_small_holes(part, 6)
        part = pixel_grid.snap(part, colors)
        canvas = Image.new("RGBA", (rebuild.CANVAS, rebuild.CANVAS))
        canvas.alpha_composite(part, tuple(crop[:2]))
        canvas.save(folder / f"{pose}.png")
        report[pose] = int((np.asarray(canvas)[..., 3] > 0).sum())
    write(folder / "redraw.json", {"wearing": list(wearing), "poses": report})
    return report


def stage_appearance(base, selection):
    assembly = base / "library/assembly.json"
    index, registry = workspace.read_workspace(assembly)
    catalog = registry[index["catalog"]["id"]]
    options = {slot["id"]: {o["id"]: o for o in slot["options"]} for slot in catalog["slots"]}
    chosen = {slot: (options[slot][rebuild.PART_ID[name]] if name else None) for slot, name in selection.items()}
    spec = write(base / "selection.json", {"kind": "parts-appearance-selection", "schema_version": 1,
        "id": "sd.avatar.layers", "selections": {slot: None for slot in options} | chosen})
    return parts_appearance.create(assembly, spec)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, type=Path)
    parser.add_argument("stage", choices=("library", "labels", "label-gen", "labels-fix", "segments", "garments", "capped-gen",
                                          "capped-extract", "held", "appearance", "redraw", "new-designs", "new-extract",
                                          "new-sheets", "new-draw", "new-slice",
                                          "coverage", "export"))
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--select", type=Path)
    args = parser.parse_args()
    stage = args.stage
    if stage == "library":
        result = stage_library(args.base)
    elif stage == "labels":
        result = {k: v for k, v in stage_labels(args.base).items() if k in ("cells", "pages")}
    elif stage == "label-gen":
        result = stage_label_gen(args.base, args.only)
    elif stage == "labels-fix":
        result = stage_labels_fix(args.base)
    elif stage == "segments":
        result = stage_segments(args.base)
    elif stage == "garments":
        result = stage_garments(args.base, args.only)
    elif stage == "capped-gen":
        result = stage_capped_gen(args.base)
    elif stage == "capped-extract":
        result = stage_capped_extract(args.base)
    elif stage == "redraw":
        result = stage_redraw(args.base)
    elif stage in ("new-designs", "new-extract", "new-sheets"):
        result = {"new-designs": stage_new_designs, "new-extract": stage_new_extract, "new-sheets": stage_new_sheets}[stage](args.base)
    elif stage == "new-draw":
        result = stage_new_draw(args.base, args.only)
    elif stage == "new-slice":
        result = stage_new_slice(args.base, args.only)
    elif stage == "held":
        result = stage_held(args.base)
    elif stage == "appearance":
        result = stage_appearance(args.base, json.loads(args.select.read_text()))
    elif stage == "coverage":
        result = {k: v for k, v in parts_poses.coverage(args.base / "library/assembly.json",
                                                        markdown=args.base / "coverage.md").items() if k != "pieces"}
    else:
        result = export_parts.export(args.base / "library/assembly.json", args.base / "export", title="SD avatar process")
    print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
