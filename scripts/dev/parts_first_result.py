# SPDX-License-Identifier: Apache-2.0
"""Two independent tops on an unchanged frozen walk, through public production APIs.

The input library is copied, never edited. Generation is part-only; there is no
body subtraction, pose warping, appearance clip generation or automatic recentering.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
from collections import deque
from fractions import Fraction

from PIL import Image, ImageDraw

from sprite_gen._deps import np
from sprite_gen.compose import compose_parts, export_parts
from sprite_gen.compose.parts_runtime import PartsPlayer
from sprite_gen.curate import parts_appearance
from sprite_gen.frames import parts_production as production
from sprite_gen.spec import parts_assets as assets, parts_contract as contract, parts_workspace as workspace

DESIGNS = {
    "teal": ("demo.upper.teal", "a teal sleeveless tank top with a narrow pale cream neckline and hem, no sleeves"),
    "red": ("demo.upper.red", "a red pullover hoodie with long sleeves, ribbed cuffs and hem, a front pocket and the hood down behind the neck"),
}
FIRST = ["walk.01", "walk.03", "walk.05", "walk.08", "walk.10", "walk.13"]


def dilate(mask, count):
    padded = np.pad(mask, count)
    out = np.zeros_like(mask)
    h, w = mask.shape
    for dy in range(2*count+1):
        for dx in range(2*count+1):
            out |= padded[dy:dy+h, dx:dx+w]
    return out


def hand_tip(arm):
    from sprite_gen.frames.parts_segments import _components
    components = list(_components(arm))
    if not components:
        return arm
    arm = max(components, key=lambda c: int(c.sum()))
    distance = np.full(arm.shape, -1)
    ys, xs = np.nonzero(arm)
    if not len(ys):
        return arm
    seeds = arm & (np.indices(arm.shape)[0] <= ys.min()+1)
    queue = deque(zip(*np.nonzero(seeds)))
    distance[seeds] = 0
    while queue:
        y, x = queue.popleft()
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0), (-1, -1), (-1, 1), (1, -1), (1, 1)):
            ny, nx = y+dy, x+dx
            if 0 <= ny < arm.shape[0] and 0 <= nx < arm.shape[1] and arm[ny, nx] and distance[ny, nx] < 0:
                distance[ny, nx] = distance[y, x]+1
                queue.append((ny, nx))
    return arm & (distance >= 0) & (distance >= distance.max()-1)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def prepare(source, out, teal_ref):
    root = out / "library"
    if root.exists():
        raise ValueError(f"{root} exists; keep this experiment and use a new output directory")
    shutil.copytree(source / "library", root, ignore=shutil.ignore_patterns("productions", "builds", "reviews"))
    shutil.copytree(source / "library/productions/sd.mina.body/build/segments", root / "inputs/body-segments")
    assembly = root / "assembly.json"
    index = workspace.decode_json(assembly.read_bytes())
    index["states"] = ["walk"]  # Scope only; the original motion snapshot and all its samples remain unchanged.
    write(assembly, index)
    _, registry = workspace.read_workspace(assembly)
    motion = registry[index["motion"]["id"]]
    clip = next(c for c in motion["clips"] if c["state"] == "walk")
    load = compose_parts.AssetLoader(root, index)
    checks = {}
    source_images = {}
    for sample in clip["samples"]:
        image = load(motion, sample["image"]).convert("RGBA")
        arr = np.asarray(image)
        torso_y = sample["sockets"]["torso"]["position"][1]
        yy, xx = np.indices(arr.shape[:2])
        opaque = arr[..., 3] > 0
        # Authored material/zone policy for this SD fixture, not a rule in the generic engine.
        gray = np.ptp(arr[..., :3].astype(int), axis=-1) < 25
        required = opaque & gray & (arr[..., :3].mean(-1) > 105) & (yy >= torso_y - 10) & (yy <= torso_y + 14)
        torso_x = sample["sockets"]["torso"]["position"][0]
        pieces = {n: np.asarray(Image.open(root / "inputs/body-segments" / n / clip["id"] /
                    f"{sample['id']}.png").convert("RGBA"))[..., 3] > 0
                  for n in ("root", "arm_front", "arm_back", "leg_front", "leg_back")}
        torso = pieces["root"] & (yy >= torso_y-13) & (yy <= torso_y+14)
        allowed = dilate(torso | pieces["arm_front"] | pieces["arm_back"], 3)
        hood = (xx >= torso_x-16) & (xx <= torso_x+2) & (yy >= torso_y-18) & (yy <= torso_y-8)
        allowed |= hood
        protected = (pieces["root"] & (yy < torso_y-15) & (xx >= torso_x-3)) | \
                    hand_tip(pieces["arm_front"]) | hand_tip(pieces["arm_back"]) | \
                    ((pieces["leg_front"] | pieces["leg_back"]) & (yy > torso_y+17))
        occluded = (pieces["root"] & (yy < torso_y-13)) | hand_tip(pieces["arm_front"]) | hand_tip(pieces["arm_back"])
        paths = {}
        for name, region in (("required", required), ("protected", protected), ("allowed", allowed), ("occluded", occluded)):
            path = root / "inputs/interfaces" / sample["pose_id"] / f"{name}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.fromarray((region * 255).astype("uint8")).save(path)
            paths[name] = path.relative_to(root).as_posix()
        checks[sample["pose_id"]] = paths
        source_images[sample["pose_id"]] = sample["image"]["sha256"]
    refs = {"red": root / "inputs/design/upper.c.raw.png", "teal": root / "inputs/design/teal-reference.png"}
    shutil.copy2(teal_ref, refs["teal"])
    for name, (part, design) in DESIGNS.items():
        config = {"kind": "parts-production-config", "schema_version": 1, "part_id": part,
                  "design_id": part, "slot": "upper", "node": "torso", "socket": "torso", "states": ["walk"],
                  "mode": "raster", "draw": {"provider": "codex", "model": "gpt-5.6-sol"},
                  "sheet": {"crop": [32, 52, 100, 100], "scale": 8, "columns": 3, "page_size": 9, "aspect": "3:2"},
                  "cut": {"method": "part-only", "body_threshold": None, "near_body": None, "min_island": 0,
                          "outline": False, "fill_holes": 0, "close_body": 0},
                  "pieces": [{"id": "sleeve_back", "node": "arm_back", "segments": ["arm_back"]},
                             {"id": "torso", "node": "torso", "segments": ["*"]},
                             {"id": "sleeve_front", "node": "arm_front", "segments": ["arm_front"]}],
                  "segments": "inputs/body-segments/segments.json", "pose_checks": checks,
                  "prompt": {"refs": [refs[name].relative_to(root).as_posix()], "text":
                    f"Image 1 is the fixed layout reference: cropped walking poses of ONE tiny chibi sprite, "
                    f"facing right. Image 2 is the design reference. Draw ONLY {design} for the character in each cell. "
                    "Match the collar, shoulders, torso, waist and wrists in image 1 EXACTLY, including the near/far arm "
                    "occlusion. The design in image 2 is for garment shape/colors only, never copy its body or pose. "
                    "Do not include any head, face, skin, hands, legs, shorts or feet in the output. Show only the clothing "
                    "at its original reference coordinates. Everything else, including empty cells, is flat pure magenta "
                    "#FF00FF. Keep all garment pixels on the exact grid of image 1: each art pixel is an 8x8 square. "
                    "Use a crisp 1-art-pixel outline and 3-4 shading colors per material. No anti-aliasing, blurry edges, "
                    "dithering, tiny speckles, labels or guides. Keep the same design and size throughout the poses. "}}
        file = out / "configs" / f"{name}.json"
        write(file, config)
        production.init(assembly, file)
    lock = {"source": str(source), "upstream": "v2.43.0", "motion": index["motion"], "body": index["body"],
            "walk_images": source_images, "part_ids": [p for p, _ in DESIGNS.values()], "first_poses": FIRST,
            "manual_art_edits": [], "appearance_clips_baked": 0, "release_ready": False}
    write(out / "source-lock.json", lock)
    return lock


def sheet(out, name, extend=False):
    assembly = out / "library/assembly.json"
    made = production.sheet(assembly, DESIGNS[name][0], poses=None if extend else FIRST)
    write(out / f"{name}-{'extend' if extend else 'first'}-batch.json", made)
    return made


def finish(out):
    assembly = out / "library/assembly.json"
    results = {}
    for name, (part, _) in DESIGNS.items():
        results[name] = {"check": production.check(assembly, part), "build": production.build(assembly, part)}
        production.review(assembly, part, contact_sheet=out / f"{name}-review.png")
    index, registry = workspace.read_workspace(assembly)
    catalog = registry[index["catalog"]["id"]]
    spec = {"kind": "parts-appearance-selection", "schema_version": 1, "id": "demo.first-result",
            "selections": {s["id"]: None for s in catalog["slots"]}}
    spec["selections"]["upper"] = contract.reference(registry[DESIGNS["teal"][0]])
    write(out / "selection.json", spec)
    parts_appearance.create(assembly, out / "selection.json")
    export = export_parts.export(assembly, out / "export", title="독립 상의 두 세트 · 동일한 걷기", interpolation="frame")
    lock = json.loads((out / "source-lock.json").read_text())
    index, registry = workspace.read_workspace(assembly)
    motion = registry[index["motion"]["id"]]
    unchanged = index["motion"] == lock["motion"] and index["body"] == lock["body"]
    assert unchanged
    frames = [production._render_alone(assembly.parent, index, registry, None, ["walk"])[0]]
    frames.extend(production._render_alone(assembly.parent, index, registry,
                   contract.reference(registry[part]), ["walk"])[0] for part, _ in DESIGNS.values())
    clip = next(c for c in motion["clips"] if c["state"] == "walk")
    load = compose_parts.AssetLoader(assembly.parent, index)
    player = PartsPlayer.from_workspace(assembly, state="walk")
    start = 0
    swaps = 0
    for sample in clip["samples"]:
        original = np.asarray(load(motion, sample["image"]).convert("RGBA"))
        rendered = np.asarray(frames[0][sample["pose_id"]])
        visible = original[..., 3] > 0
        assert np.array_equal(original[..., 3], rendered[..., 3])
        assert np.array_equal(original[visible], rendered[visible])
        at = Fraction(2*start+sample["duration_ticks"], 2*clip["timebase"])
        player.seek(at)
        for n, (part, _) in enumerate(DESIGNS.values(), 1):
            player.set_selection({"upper": contract.reference(registry[part])})
            layer_sample = player.layers(interpolate=False)
            assert player.elapsed_seconds == at and layer_sample["sample_id"] == sample["id"]
            assert layer_sample["motion"] == lock["motion"]
            image, _ = player.frame()
            assert np.array_equal(np.asarray(image), np.asarray(frames[n][sample["pose_id"]]))
            swaps += 1
        start += sample["duration_ticks"]
    comparison = []
    for sample in clip["samples"]:
        canvas = Image.new("RGB", (612, 316), (40, 44, 55))
        painter = ImageDraw.Draw(canvas)
        for n, (label, images) in enumerate(zip(("BODY ONLY", "TEAL TOP", "RED HOODIE"), frames)):
            sprite = images[sample["pose_id"]].crop((32, 24, 100, 120)).resize((204, 288), Image.Resampling.NEAREST)
            canvas.paste(sprite, (n*204, 28), sprite)
            painter.text((n*204+8, 8), label + " / " + sample["pose_id"], fill="white")
        comparison.append(canvas)
    comparison[0].save(out / "comparison.png")
    # Inspection-only loop. Exported runtime assets retain independent layers and
    # the exact original tick timeline, without baked appearance clips.
    duration = [max(10, round(Fraction(s["duration_ticks"]*100, clip["timebase"]))*10)
                for s in clip["samples"]]
    comparison[0].save(out / "comparison.gif", save_all=True, append_images=comparison[1:],
                       duration=duration, loop=0, disposal=2)
    wall = Image.new("RGB", (612*2, 316*7))
    for n, image in enumerate(comparison):
        wall.paste(image, (n%2*612, n//2*316))
    wall.save(out / "comparison-all.png")
    results.update(source_unchanged=unchanged, export=export, status=production.status(assembly),
                   appearance_clips_baked=0, release_ready=False,
                   comparison={"still": str(out / "comparison.png"), "inspection_loop": str(out / "comparison.gif")},
                   verification={"body_samples_pixel_identical": len(clip["samples"]),
                                 "same_clock_swaps": swaps, "motion_digest": motion["digest"]})
    write(out / "result.json", results)
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("prepare", "sheet", "extend", "draw", "slice", "finish"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--teal-ref", type=Path)
    parser.add_argument("--design", choices=DESIGNS)
    args = parser.parse_args()
    if args.stage == "prepare":
        result = prepare(args.source, args.out, args.teal_ref)
    elif args.stage in ("sheet", "extend"):
        result = sheet(args.out, args.design, args.stage == "extend")
    elif args.stage == "draw":
        result = production.draw(args.out / "library/assembly.json", DESIGNS[args.design][0], provider="codex")
    elif args.stage == "slice":
        result = production.slice_batch(args.out / "library/assembly.json", DESIGNS[args.design][0])
    else:
        result = finish(args.out)
    print(json.dumps(result, ensure_ascii=False))
