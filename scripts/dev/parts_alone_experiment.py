# SPDX-License-Identifier: Apache-2.0
"""Shoes generated alone, two ways, on one character — no body is cut out of anything.

    B  the posed body as reference (aldegad/sprite-gen#21 style): "draw only the shoes these feet wear"
    D  a structure map: a flat silhouette with the near foot painted orange and the far foot blue,
       "draw only the shoes covering the painted feet"

Both use the same pose sheets geometry (feet close-up, 8x blocks) and the same design reference.
The drawing is only normalized (key, grid, palette) and placed by an integer offset that fits the
foot regions; nothing is repaired. Each method becomes its own experimental production so the
real build (pieces by body segment, zmap) and runtime render it.

    prep     foot regions, sheets and prompts for both methods
    draw     one image call per page (codex)
    slice    cells -> native part -> palette -> fit offset -> candidates; build the packs
    compare  metrics and showcase sheets (old cut / B / D, bare legs and jeans)

Usage: avatar character base, e.g. assets/sd-avatar-mina-20261006
"""
from __future__ import annotations

import argparse
from collections import deque
from fractions import Fraction
import json
from pathlib import Path
import subprocess
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parent))
import avatar_character as character_recipe  # noqa: E402
import sd_avatar_process as avatar  # noqa: E402
import sd_explorer_rebuild as rebuild  # noqa: E402
from sprite_gen._deps import np  # noqa: E402
from sprite_gen.compose import compose_parts  # noqa: E402
from sprite_gen.compose.parts_runtime import PartsPlayer  # noqa: E402
from sprite_gen.curate import parts_motion  # noqa: E402
from sprite_gen.frames import parts_pose_sheet as sheets, parts_production as production, pixel_grid  # noqa: E402
from sprite_gen.spec import parts_assets as assets, parts_contract as contract, parts_workspace as workspace  # noqa: E402

CROP = (32, 84, 96, 116)  # the feet close-up of the SD canvas
SCALE, GUTTER, COLUMNS, PER_PAGE = 8, 16, 3, 12
KEY = (255, 0, 255)
NEAR, FAR = (255, 128, 0), (0, 96, 255)
NEAR_LEG, FAR_LEG, BODY = (200, 200, 200), (120, 120, 120), (160, 160, 160)
FOOT_SHARE = 0.3  # the distal share of a leg (by path length from the hip) taken as its foot
FOOT_LENGTH = 0.085  # a foot, toe to ankle along the silhouette, as a share of the body's height
METHODS = ("b", "d")
SHOE = "footwear.c"

PROMPT = {
    "b": ("Image 1 is a pixel-art pose sheet: each grid cell shows one chibi character's lower legs and bare feet in a "
          "different walking or running pose, pure side view facing right, on a flat pure magenta #FF00FF background. "
          "Image 2 shows the exact shoes: {desc}.\n\n"
          "In every cell draw ONLY the pair of shoes those feet wear: each shoe exactly where its foot is, following the "
          "foot's angle, size and position; the far foot's shoe is hidden where the near leg covers it. Draw nothing "
          "else: no legs, no shorts, no skin, no body outline - every other pixel is flat #FF00FF. "),
    "d": ("Image 1 is a layout sheet for a pixel-art sprite: each grid cell shows a flat grey silhouette of one chibi "
          "character's lower legs in a different walking or running pose (pure side view facing right; the near leg "
          "light grey, the far leg dark grey) on a flat pure magenta #FF00FF background. In every cell the near foot "
          "is painted solid orange and the far foot solid blue. Image 2 shows the exact shoes: {desc}.\n\n"
          "In every cell draw ONLY the pair of shoes: the near shoe covering the orange foot, its toe pointing where "
          "the orange foot points, and the far shoe covering the blue foot, hidden where the near leg's grey overlaps "
          "it. Shoes may be a little bigger than the painted feet, as shoes are. Draw nothing else: the grey "
          "silhouette and the orange and blue paint all become flat #FF00FF. "),
}
COMMON = ("Keep the shoes simple and readable for a tiny game sprite: the same clean shoe shape in every cell, colors "
          "from image 2, a crisp 1-pixel dark outline, no texture, no speckles. {style} Keep this {w}x{h} image and its "
          "grid exactly: every shoe stays in its own cell where its feet are in image 1; do not move, mirror, resize, "
          "rearrange or re-space anything.")


def _paths(base):
    root = (base / "library").absolute()
    return root, root / "assembly.json", base / "experiments/shoes-alone"


def _poses(assembly):
    root, index, registry, _ = parts_motion._active(assembly)
    motion = registry[index["motion"]["id"]]
    seen, poses = set(), []
    for clip in motion["clips"]:
        for sample in clip["samples"]:
            if sample["pose_id"] not in seen:
                seen.add(sample["pose_id"])
                poses.append((sample["pose_id"], clip["id"], sample))
    return root, index, registry, motion, poses


def _segment(root, prefix, name, clip, sample):
    path = root / f"productions/{prefix}.body/build/segments/{name}/{clip}/{sample}.png"
    return np.asarray(Image.open(path).convert("RGBA"))[..., 3] > 0


STEPS = ((-1, 0), (1, 0), (0, -1), (0, 1))


def _grow(mask, times=1):
    out = mask.copy()
    for _ in range(times):
        grown = out.copy()
        for dy, dx in STEPS:
            grown |= np.roll(np.roll(out, dy, 0), dx, 1)
        out = grown
    return out


def foot_region(leg, hip):
    """The leg's distal share by path length from where it meets the hip: its foot, however the leg bends."""
    seeds = leg & _grow(hip)
    if not seeds.any():
        rows = np.where(leg.any(axis=1))[0]
        seeds = leg & (np.arange(leg.shape[0])[:, None] <= rows.min() + 1)
    distance = np.full(leg.shape, -1)
    queue = deque()
    for y, x in zip(*np.nonzero(seeds)):
        distance[y, x] = 0
        queue.append((y, x))
    while queue:
        y, x = queue.popleft()
        for dy, dx in STEPS:
            ny, nx = y+dy, x+dx
            if 0 <= ny < leg.shape[0] and 0 <= nx < leg.shape[1] and leg[ny, nx] and distance[ny, nx] < 0:
                distance[ny, nx] = distance[y, x] + 1
                queue.append((ny, nx))
    reach = distance.max()
    return leg & (distance >= reach*(1 - FOOT_SHARE)) if reach > 0 else leg


def _geodesic(mask, seeds):
    distance = np.full(mask.shape, -1)
    queue = deque()
    for y, x in zip(*np.nonzero(seeds & mask)):
        distance[y, x] = 0
        queue.append((y, x))
    while queue:
        y, x = queue.popleft()
        for dy, dx in STEPS:
            ny, nx = y+dy, x+dx
            if 0 <= ny < mask.shape[0] and 0 <= nx < mask.shape[1] and mask[ny, nx] and distance[ny, nx] < 0:
                distance[ny, nx] = distance[y, x] + 1
                queue.append((ny, nx))
    return distance


def _regions(root, prefix, clip, sample, body):
    """Feet from the silhouette itself: the two ends of the lower body farthest from the hip along it,
    each taken up to FOOT_SHARE of that leg's length; the lighter one is the near foot. The painted
    leg labels only shade the legs in the map (they mix near and far in places)."""
    pixels = np.asarray(body)
    silhouette = pixels[..., 3] > 0
    near_leg, far_leg = (_segment(root, prefix, n, clip, sample["id"]) for n in ("leg_front", "leg_back"))
    legs = near_leg | far_leg
    hip_row = int(np.where(legs.any(axis=1))[0].min()) if legs.any() else int(np.where(silhouette.any(axis=1))[0].max()) - 30
    lower = silhouette & (np.arange(silhouette.shape[0])[:, None] >= hip_row)
    from_hip = _geodesic(lower, lower & (np.arange(silhouette.shape[0])[:, None] == hip_row))
    reach = from_hip.max()
    rows = np.where(silhouette.any(axis=1))[0]
    foot_length = max(4, round(FOOT_LENGTH*(rows.max() - rows.min() + 1)))
    first = np.unravel_index(np.argmax(from_hip), from_hip.shape)
    from_first = _geodesic(lower, np.eye(1, lower.size, first[0]*lower.shape[1] + first[1], dtype=bool).reshape(lower.shape))
    apart = (from_first > 0.6*reach) & (from_hip > 0.6*reach)
    feet = []
    for end in (first, np.unravel_index(np.argmax(np.where(apart, from_hip, -1)), from_hip.shape) if apart.any() else None):
        if end is None:
            continue
        seed = np.zeros_like(lower)
        seed[end] = True
        from_end = _geodesic(lower, seed)
        feet.append(lower & (from_end >= 0) & (from_end <= foot_length))
    if len(feet) == 2:
        feet[1] &= ~feet[0]
    luma = pixels[..., :3].astype(np.int32) @ np.array([299, 587, 114])
    lit = luma >= 60*1000

    def nearness(foot):
        votes = (foot & near_leg).sum() - (foot & far_leg).sum()
        if abs(votes) >= 0.4*max(1, foot.sum()):  # the labels agree clearly here
            return float(votes)
        return float(luma[foot & lit].mean()) / 1000 if (foot & lit).any() else 0.0  # else the near foot is the lit one
    feet.sort(key=lambda f: -nearness(f))
    empty = np.zeros_like(lower)
    return {"near_leg": near_leg, "far_leg": far_leg, "near_foot": feet[0] if feet else empty,
            "far_foot": feet[1] if len(feet) > 1 else empty, "body": silhouette}


def _cell_image(method, body, regions):
    if method == "b":
        return body
    rgba = np.zeros((*regions["body"].shape, 4), dtype=np.uint8)
    for name, color in (("body", BODY), ("far_leg", FAR_LEG), ("near_leg", NEAR_LEG), ("far_foot", FAR), ("near_foot", NEAR)):
        rgba[regions[name]] = (*color, 255)
    return Image.fromarray(rgba)


def _page(cells):
    width, height = (CROP[2]-CROP[0])*SCALE, (CROP[3]-CROP[1])*SCALE
    rows = -(-PER_PAGE // COLUMNS)
    sheet = Image.new("RGBA", (GUTTER + COLUMNS*(width+GUTTER), GUTTER + rows*(height+GUTTER)), (*KEY, 255))
    origins = []
    for n, image in enumerate(cells):
        origin = (GUTTER + n % COLUMNS*(width+GUTTER), GUTTER + n//COLUMNS*(height+GUTTER))
        sheet.alpha_composite(image.crop(CROP).resize((width, height), Image.Resampling.NEAREST), origin)
        origins.append(origin)
    return sheet, origins


def stage_prep(base):
    character = character_recipe.config(base)
    root, assembly, out = _paths(base)
    _, index, registry, motion, poses = _poses(assembly)
    load = compose_parts.AssetLoader(root, index)
    style = avatar._garment_prompts()[2].format(px=SCALE)
    desc = avatar.NEW[SHOE][1] if SHOE in avatar.NEW else ""
    design = root / f"inputs/design/{SHOE}.zoom.png"
    plan = {"poses": [], "pages": {m: [] for m in METHODS}, "design": str(design)}
    regions_dir = out / "regions"
    regions_dir.mkdir(parents=True, exist_ok=True)
    cells = {m: [] for m in METHODS}
    for pose, clip, sample in poses:
        body = load(motion, sample["image"]).convert("RGBA")
        regions = _regions(root, character["prefix"], clip, sample, body)
        np.savez_compressed(regions_dir / f"{pose}.npz", **regions)
        plan["poses"].append({"pose": pose, "clip": clip, "sample": sample["id"]})
        for method in METHODS:
            cells[method].append(_cell_image(method, body, regions))
    for method in METHODS:
        for start in range(0, len(cells[method]), PER_PAGE):
            folder = out / method / f"page-{start//PER_PAGE + 1}"
            folder.mkdir(parents=True, exist_ok=True)
            sheet, origins = _page(cells[method][start:start+PER_PAGE])
            sheet.save(folder / "sheet.png")
            text = PROMPT[method].format(desc=desc) + COMMON.format(style=style, w=sheet.width, h=sheet.height)
            (folder / "prompt.txt").write_text(text)
            plan["pages"][method].append({"folder": str(folder), "poses": [p["pose"] for p in plan["poses"][start:start+PER_PAGE]],
                                          "origins": origins})
    (out / "plan.json").write_text(json.dumps(plan, indent=1))
    return {"poses": len(plan["poses"]), "pages": {m: len(v) for m, v in plan["pages"].items()}}


def stage_draw(base, only=None):
    _, _, out = _paths(base)
    plan = json.loads((out / "plan.json").read_text())
    jobs = [(m, p) for m in METHODS if not only or m in only for p in plan["pages"][m]]
    procs = []
    for method, page in jobs:
        folder = Path(page["folder"])
        if (folder / "drawn.png").exists():
            continue
        procs.append((folder, subprocess.Popen(
            [".venv/bin/sprite-gen", "gen", "--provider", "codex", "--model", "gpt-5.6-sol", "--prompt-file",
             str(folder / "prompt.txt"), "--out", str(folder / "drawn.png"), "--report", str(folder / "drawn.report.json"),
             "--ref", str(folder / "sheet.png"), "--ref", plan["design"]], stdout=subprocess.PIPE, stderr=subprocess.PIPE)))
    results = []
    for folder, proc in procs:
        _, err = proc.communicate()
        results.append({"page": str(folder), "ok": proc.returncode == 0, "error": err.decode()[-300:] if proc.returncode else None})
    return results


def fit(part, regions, reach=3):
    """The integer offset that best lays the shoes on the feet; nothing is redrawn or cut.

    Covering the feet is rewarded; a shoe pixel off the figure (sunk into the ground, floating
    behind the heel) costs as much, so the drawing's own placement wins unless it is clearly off."""
    alpha = np.asarray(part)[..., 3] > 0
    feet = regions["near_foot"] | regions["far_foot"]
    legs_above = regions["body"] & ~_grow(feet, 2)
    outside = ~_grow(regions["body"], 1)
    best = None
    for dy in range(-reach, reach+1):
        for dx in range(-reach, reach+1):
            moved = np.roll(np.roll(alpha, dy, 0), dx, 1)
            score = (moved & feet).sum() - (moved & outside).sum()
            key = (score, -(abs(dx)+abs(dy)))
            if best is None or key > best[0]:
                best = (key, dx, dy)
    _, dx, dy = best
    moved = np.roll(np.roll(np.asarray(part), dy, 0), dx, 1)
    shown = moved[..., 3] > 0
    return Image.fromarray(moved), {"offset": [dx, dy], "coverage": round(float((shown & feet).sum() / max(1, feet.sum())), 3),
                                    "intrusion": round(float((shown & legs_above).sum() / max(1, shown.sum())), 3),
                                    "pixels": int(shown.sum())}


def _experiment_part(prefix, method):
    return f"{prefix}.exp.shoes-{method}"


def _ensure_production(base, assembly, method):
    character = character_recipe.config(base)
    part = _experiment_part(character["prefix"], method)
    root = assembly.parent
    if (root / "productions" / part / "production.json").exists():
        return part
    source = json.loads((base / f"production-configs/{SHOE}.json").read_text())
    source.update(part_id=part, design_id=f"{part}.design",
                  prompt={"text": PROMPT[method], "refs": source["prompt"]["refs"]})
    file = base / f"experiments/shoes-alone/{method}/config.json"
    file.write_text(json.dumps(source, indent=1))
    production.init(assembly, file)
    return part


def stage_slice(base):
    character = character_recipe.config(base)
    root, assembly, out = _paths(base)
    _, index, registry, motion, poses = _poses(assembly)
    by_pose = {pose: (clip, sample) for pose, clip, sample in poses}
    plan = json.loads((out / "plan.json").read_text())
    palette = pixel_grid.read_palette(root / f"inputs/design/{SHOE}.palette5.json") if (root / f"inputs/design/{SHOE}.palette5.json").exists() \
        else pixel_grid.read_palette(root / f"inputs/design/{SHOE}.palette.json")
    load = compose_parts.AssetLoader(root, index)
    canvas = (registry[index["rig"]["id"]]["canvas"]["width"], registry[index["rig"]["id"]]["canvas"]["height"])
    width, height = CROP[2]-CROP[0], CROP[3]-CROP[1]
    report = {}
    for method in METHODS:
        part = _ensure_production(base, assembly, method)
        state = production._load(root, part)
        rows = production._track(state, None)
        report[method] = {}
        for page in plan["pages"][method]:
            folder = Path(page["folder"])
            art = folder / "drawn.png"
            if not art.exists():
                continue
            drawn = Image.open(art).convert("RGBA")
            sheet = Image.open(folder / "sheet.png")
            ratio = drawn.width / sheet.width
            for pose, origin in zip(page["poses"], page["origins"]):
                x, y = origin
                box = tuple(int(round(v*ratio)) for v in (x, y, x + width*SCALE, y + height*SCALE))
                native = sheets.reduce_cell(drawn.crop(box), SCALE*ratio, KEY, 80, size=(width, height))
                full = Image.new("RGBA", canvas)
                full.alpha_composite(native, CROP[:2])
                full = pixel_grid.snap(full, palette)
                regions = dict(np.load(out / "regions" / f"{pose}.npz"))
                placed, metrics = fit(full, regions)
                clip, sample = by_pose[pose]
                body = load(motion, sample["image"]).convert("RGBA")
                socket = sample["sockets"][state["config"]["socket"]]
                record = {"kind": "parts-production-candidate", "schema_version": 1, "policy": production.POLICY, "part": part,
                          "track": production.MAIN, "pose_id": pose, "clip": clip, "sample": sample["id"],
                          "motion": index["motion"], "body_sha256": sample["image"]["sha256"], "under_sha256": None,
                          "wearing": [], "method": {"generate": f"alone-{method}", "fit": metrics["offset"], "batch": None},
                          "source": {"art_sha256": assets.sha256(art.read_bytes()), "origin": list(origin)},
                          "placement": {"format": "full-canvas", "pivot": list(socket["position"]),
                                        "target_socket": state["config"]["socket"], "offset": [0, 0]},
                          "alignment": None, "holes_filled": 0, "pixels": metrics["pixels"]}
                mask = Image.fromarray(((np.asarray(placed)[..., 3] > 0)*255).astype(np.uint8), "L")
                record, location = production._publish_candidate(root, part, record, {"drawn": full, "under": body,
                                                                                      "mask": mask, "part": placed})
                production._select(rows, [pose], location, keep_approved=False)
                report[method][pose] = metrics
        production._save(root, state)
        production.build(assembly, part, allow_incomplete=True)
    (out / "fit-report.json").write_text(json.dumps(report, indent=1))
    return {m: _summary(v) for m, v in report.items()}


def _summary(rows):
    if not rows:
        return {}
    values = list(rows.values())
    pixels = np.array([v["pixels"] for v in values])
    return {"poses": len(values),
            "offset_mean_px": round(float(np.mean([abs(v["offset"][0]) + abs(v["offset"][1]) for v in values])), 2),
            "offset_max_px": int(max(abs(v["offset"][0]) + abs(v["offset"][1]) for v in values)),
            "coverage_mean": round(float(np.mean([v["coverage"] for v in values])), 3),
            "coverage_min": round(float(min(v["coverage"] for v in values)), 3),
            "intrusion_mean": round(float(np.mean([v["intrusion"] for v in values])), 3),
            "area_cv": round(float(pixels.std() / max(pixels.mean(), 1)), 3)}


def _frames(assembly, selection, state):
    index, registry = workspace.read_workspace(assembly)
    catalog = registry[index["catalog"]["id"]]
    player = PartsPlayer.from_workspace(assembly)
    player.set_selection({s["id"]: contract.reference(registry[selection[s["id"]]]) if selection.get(s["id"]) else None
                          for s in catalog["slots"]})
    player.set_state(state)
    clip = next(c for c in player.plan["clips"] if c["state"] == state)
    frames, start = [], Fraction(0)
    for sample in clip["samples"]:
        player.seek(start / clip["timebase"])
        frames.append((player.frame()[0], sample["duration_ticks"]*1000//clip["timebase"]))
        start += sample["duration_ticks"]
    return frames


def stage_compare(base):
    character = character_recipe.config(base)
    root, assembly, out = _paths(base)
    character_recipe.stage_appearance(base)  # imports drop the appearance; the runtime needs one
    shoes = {"cut (before)": rebuild.PART_ID[SHOE]} | {f"alone {m.upper()}": _experiment_part(character["prefix"], m) for m in METHODS}
    lower = rebuild.PART_ID[character["designs"]["lower"]]
    crop = (24, 56, 104, 124)
    scale = 4
    for state in ("walk", "run", "idle"):
        rows = [[_frames(assembly, {"footwear": shoe, "lower": legs}, state) for legs in (None, lower)] for shoe in shoes.values()]
        width, height = (crop[2]-crop[0])*scale, (crop[3]-crop[1])*scale
        images = []
        for n in range(len(rows[0][0])):
            sheet = Image.new("RGBA", (width*2 + 140, height*len(rows)), (236, 236, 236, 255))
            draw = ImageDraw.Draw(sheet)
            for r, (label, row) in enumerate(zip(shoes, rows)):
                draw.text((6, r*height + 8), label, fill=(30, 30, 30, 255))
                for c, frames in enumerate(row):
                    sheet.alpha_composite(frames[n][0].crop(crop).resize((width, height), Image.Resampling.NEAREST), (140 + c*width, r*height))
            images.append(sheet)
        images[0].save(out / f"compare-{state}.gif", save_all=True, append_images=images[1:], duration=[d for _, d in rows[0][0]], loop=0)
        strip = Image.new("RGBA", (width*2 + 140, height*len(rows)*4), (236, 236, 236, 255))
        for k, n in enumerate(range(0, len(images), max(1, len(images)//4))[:4]):
            strip.alpha_composite(images[n], (0, k*height*len(rows)))
        strip.save(out / f"compare-{state}.png")
    return {"out": str(out)}


# --- foot rig: where each shoe should be, from the base pose's shoe and each pose's foot ----------------
BASE_POSE = "idle.01"
SIZE_TOLERANCE = (0.8, 1.2)  # a shoe's area against the base pose's shoe
MIN_SHOE = 10  # px: smaller pieces are crumbs, not a shoe
BAND = (1, 6)  # px around a shoe where its own leg is read


def _components(mask, minimum=MIN_SHOE):
    from sprite_gen.frames.parts_segments import _components as components
    return sorted((c for c in components(mask) if c.sum() >= minimum), key=lambda c: -int(c.sum()))


def _from_hip(body):
    """Path length from the hip along the body: the leg's own way down, however it bends."""
    rows = np.where(body.any(axis=1))[0]
    hip_row = rows.min() + int(0.55*(rows.max() - rows.min()))  # below the waist of a 2-head body
    lower = body & (np.arange(body.shape[0])[:, None] >= hip_row)
    return _geodesic(lower, lower & (np.arange(body.shape[0])[:, None] == hip_row))


def foot_frame(shoe, body, others, from_hip=None):
    """The ankle (where the leg enters the shoe) and the shin's angle, read off the body around a shoe.

    The leg is the piece of body around the shoe nearest the hip along the body; bare toes
    sticking out of a small shoe are a piece too, farther along."""
    band = body & _grow(shoe, BAND[1]) & ~_grow(shoe, BAND[0]) & ~others
    legs = _components(band, 3)
    if not legs:
        return None
    if from_hip is not None:
        reach = [float(from_hip[c & (from_hip >= 0)].mean()) if (c & (from_hip >= 0)).any() else 1e9 for c in legs]
        leg = legs[int(np.argmin(reach))]
    else:
        leg = legs[0]
    ankle = shoe & _grow(leg, BAND[0] + 1)
    if not ankle.any():
        return None
    ay, ax = (float(v.mean()) for v in np.nonzero(ankle))
    ly, lx = (float(v.mean()) for v in np.nonzero(leg))
    return {"ankle": [ax, ay], "angle": float(np.degrees(np.arctan2(ay - ly, ax - lx)))}  # knee side -> ankle


def _turned(base_shoe, base_frame, turn, at, scale=1.0):
    """The base shoe turned by `turn` degrees and scaled about its ankle, the ankle moved to `at`."""
    image = Image.fromarray((base_shoe*255).astype(np.uint8), "L")
    ax, ay = base_frame["ankle"]
    phi = np.radians(turn)
    c, s = np.cos(phi)/scale, np.sin(phi)/scale
    # output (x, y) -> input: inverse rotation and scale about the new ankle, back onto the base ankle
    a, b, d, e = c, s, -s, c
    matrix = (a, b, ax - a*at[0] - b*at[1], d, e, ay - d*at[0] - e*at[1])
    return np.asarray(image.transform(image.size, Image.Transform.AFFINE, matrix, resample=Image.Resampling.NEAREST)) > 0


SCALES = tuple(round(0.9 + 0.05*i, 2) for i in range(11))  # 0.90 ... 1.40 of the base shoe
FOOT_COVER = 0.92


def _foot(body, near, frame, turn, base_frame):
    """The bare foot a shoe must cover: body pixels near it, past the ankle along the shin."""
    shin = np.radians(base_frame["angle"] + turn)
    ys, xs = np.mgrid[0:body.shape[0], 0:body.shape[1]]
    beyond = (xs - frame["ankle"][0])*np.cos(shin) + (ys - frame["ankle"][1])*np.sin(shin) > 0
    return body & near & beyond


def _scaled(base_shoe, base_frame, turn, at, foot):
    """The smallest scale of the base shoe that covers this pose's bare foot (feet differ by pose)."""
    best = None
    for scale in SCALES:
        mask = _turned(base_shoe, base_frame, turn, at, scale)
        cover = (mask & foot).sum() / max(1, foot.sum())
        if cover >= FOOT_COVER:
            return mask, scale, round(float(cover), 2)
        if best is None or cover > best[2]:
            best = (mask, scale, round(float(cover), 2))
    return best


def _expected(base_shoe, base_frame, shoe, frame, reach=2):
    """The base pose's shoe, at its own size, turned and hung on this ankle to lie along the drawn shoe.

    The drawing gives the angle (the model places and turns shoes well); the base pose gives the
    size. Returns (mask, turn in degrees)."""
    best = None
    for turn in range(-180, 180, 5):
        for dy in range(-reach, reach+1):
            for dx in range(-reach, reach+1):
                at = (frame["ankle"][0] + dx, frame["ankle"][1] + dy)
                mask = _turned(base_shoe, base_frame, turn, at)
                score = (mask & shoe).sum() / max(1, (mask | shoe).sum())
                if best is None or score > best[0]:
                    best = (score, mask, turn)
    return best[1], best[2]


def _placed(base_shoe, scale, turn, center, base_center):
    """The base shoe scaled and turned about its middle, its middle moved to `center`."""
    image = Image.fromarray((base_shoe*255).astype(np.uint8), "L")
    phi = np.radians(turn)
    c, s = np.cos(phi)/scale, np.sin(phi)/scale
    a, b, d, e = c, s, -s, c
    bx, by = base_center
    matrix = (a, b, bx - a*center[0] - b*center[1], d, e, by - d*center[0] - e*center[1])
    return np.asarray(image.transform(image.size, Image.Transform.AFFINE, matrix, resample=Image.Resampling.NEAREST)) > 0


def _middle(mask):
    ys, xs = np.nonzero(mask)
    return float(xs.mean()), float(ys.mean())


def expected_shoe(base_shoe, scale, drawn, reach=3):
    """The base pose's shoe at this foot's scale, turned and placed to lie along the drawn shoe."""
    base_center, (cx, cy) = _middle(base_shoe), _middle(drawn)
    best = None
    for turn in range(-180, 180, 5):
        for dy in range(-reach, reach+1):
            for dx in range(-reach, reach+1):
                mask = _placed(base_shoe, scale, turn, (cx+dx, cy+dy), base_center)
                score = (mask & drawn).sum() / max(1, (mask | drawn).sum())
                if best is None or score > best[0]:
                    best = (score, mask, turn)
    return best[1], best[2]


def stage_rig(base):
    """Per pose, per foot: how big that foot is against the base pose's (the body's feet differ by pose,
    the video draws running feet bigger), the base shoe at that scale laid along the first drawing,
    and the current drawing's size against it."""
    root, assembly, out = _paths(base)
    character = character_recipe.config(base)
    part = _experiment_part(character["prefix"], "b")
    rows = production._load(root, part)["tracks"]["main"]["poses"]
    def mask_of(location, image="part"):
        return np.asarray(Image.open(root / location / f"{image}.png"))[..., 3] > 0
    body = {pose: dict(np.load(out / "regions" / f"{pose}.npz"))["body"] for pose in rows}
    base_mask = mask_of(rows[BASE_POSE]["candidates"][0], "drawn")  # the model's own placement, before any fit
    base_shoe = _components(base_mask)[0]
    # The base foot: the median over the base clip's poses (one pose's first drawing is noisy by ~10 %).
    base_clip = BASE_POSE.split(".")[0]
    feet = []
    for pose, row in rows.items():
        if pose.startswith(base_clip + "."):
            shoes = _components(mask_of(row["candidates"][0], "drawn"))
            feet += [int((s & body[pose]).sum()) for s in shoes if s.sum() <= 1.6*base_shoe.sum()]
    base_foot = int(np.median(feet)) if feet else int((base_shoe & body[BASE_POSE]).sum())
    rig, expected = {"base": {"pose": BASE_POSE, "area": int(base_shoe.sum()), "foot": base_foot}, "poses": {}}, {}
    for pose, row in rows.items():
        located, current_mask = mask_of(row["candidates"][0], "drawn"), mask_of(row["selected"])
        entries, want = [], np.zeros_like(located)
        for guide in _components(located):
            pair = bool(guide.sum() > 1.6*base_shoe.sum())  # two shoes drawn touching
            current = _components(current_mask & _grow(guide, 3))
            current = current[0] if current else np.zeros_like(located)
            entry = {"pair": pair}
            if pair:
                want |= guide
                ratio = current.sum() / max(1, guide.sum())
            else:
                foot = int((guide & body[pose]).sum())  # the bare foot the first drawing had to cover
                scale = float(np.clip(np.sqrt(foot / max(1, base_foot)), 0.85, 1.4))
                expect, turn = expected_shoe(base_shoe, scale, guide)
                want |= expect
                ratio = current.sum() / max(1, expect.sum())
                entry.update(foot=foot, scale=round(scale, 2), turn=turn,
                             iou=round(float((current & expect).sum() / max(1, (current | expect).sum())), 2))
            entry.update(area=int(current.sum()), ratio=round(float(ratio), 2),
                         size_ok=bool(SIZE_TOLERANCE[0] <= ratio <= SIZE_TOLERANCE[1]))
            entries.append(entry)
        rig["poses"][pose] = entries
        expected[pose] = want
    np.savez_compressed(out / "expected.npz", **expected)
    (out / "rig.json").write_text(json.dumps(rig, indent=1))
    off = {p: [e["ratio"] for e in v if not e["size_ok"]] for p, v in rig["poses"].items() if any(not e["size_ok"] for e in v)}
    return {"base": rig["base"], "off_size": off}


GUIDE = (0, 220, 255)
REDO_PROMPT = (
    "Image 1 is a pixel-art pose sheet: each grid cell shows one chibi character's lower legs and bare feet in a "
    "different walking or running pose, pure side view facing right, on a flat pure magenta #FF00FF background. A thin "
    "cyan outline in each cell marks exactly where each shoe goes and how big it is. Image 2 shows the exact shoes: "
    "{desc}.\n\n"
    "In every cell draw ONLY the pair of shoes: each shoe fills its cyan outline exactly - same size, same angle, toe "
    "where the outline's toe is, opening where the leg enters. Draw both shoes whole, even where the other leg passes in "
    "front (the legs are layered over them later). Draw nothing else: no legs, no "
    "shorts, no skin, no body outline, no cyan line, no specks - every other pixel, up to the cell edges, is flat "
    "#FF00FF. ")


def stage_redo(base, step="all", round_=1):
    """Send back the poses whose shoes miss the base size, then redraw only them on a sheet that shows
    where each shoe goes (the rig's expected shoe, outlined), and place the result against it."""
    character = character_recipe.config(base)
    root, assembly, out = _paths(base)
    rig = json.loads((out / "rig.json").read_text())
    expected = dict(np.load(out / "expected.npz"))
    part = _experiment_part(character["prefix"], "b")
    failing = [p for p, v in rig["poses"].items() if any(not e["size_ok"] for e in v)]
    folder = out / ("b-redo" if round_ == 1 else f"b-redo-{round_}")
    folder.mkdir(exist_ok=True)
    if step in ("all", "sheet"):
        note = {p: ", ".join(f"{e['ratio']}x" for e in v if not e["size_ok"]) for p, v in rig["poses"].items() if p in failing}
        production.review(assembly, part, reviewer="claude (rig check)", reject=failing,
                          note="shoe size off the base pose (" + "; ".join(f"{p} {r}" for p, r in note.items()) + "); redraw at the base size")
        _, index, registry, motion, poses = _poses(assembly)
        load = compose_parts.AssetLoader(root, index)
        by_pose = {pose: sample for pose, _, sample in poses}
        cells = []
        for pose in failing:
            body = np.asarray(load(motion, by_pose[pose]["image"]).convert("RGBA")).copy()
            want = expected[pose]
            edge = want & ~np.all([np.roll(want, k, a) for k in (1, -1) for a in (0, 1)], axis=0)
            body[edge] = (*GUIDE, 255)
            cells.append(Image.fromarray(body))
        pages = []
        for start in range(0, len(cells), PER_PAGE):
            page = folder / f"page-{start//PER_PAGE + 1}"
            page.mkdir(exist_ok=True)
            sheet, origins = _page(cells[start:start+PER_PAGE])
            sheet.save(page / "sheet.png")
            style = avatar._garment_prompts()[2].format(px=SCALE)
            (page / "prompt.txt").write_text(REDO_PROMPT.format(desc=avatar.NEW[SHOE][1]) + COMMON.format(style=style, w=sheet.width, h=sheet.height))
            pages.append({"folder": str(page), "poses": failing[start:start+PER_PAGE], "origins": origins})
        (folder / "plan.json").write_text(json.dumps({"pages": pages, "design": str(root / f"inputs/design/{SHOE}.zoom.png")}, indent=1))
    plan = json.loads((folder / "plan.json").read_text())
    if step in ("all", "draw"):
        procs = [(Path(p["folder"]), subprocess.Popen(
            [".venv/bin/sprite-gen", "gen", "--provider", "codex", "--model", "gpt-5.6-sol", "--prompt-file",
             str(Path(p["folder"]) / "prompt.txt"), "--out", str(Path(p["folder"]) / "drawn.png"), "--ref",
             str(Path(p["folder"]) / "sheet.png"), "--ref", plan["design"]], stdout=subprocess.PIPE, stderr=subprocess.PIPE))
                 for p in plan["pages"] if not (Path(p["folder"]) / "drawn.png").exists()]
        for page, proc in procs:
            _, err = proc.communicate()
            if proc.returncode:
                raise SystemExit(f"{page}: {err.decode()[-300:]}")
    if step in ("all", "slice"):
        return _slice_redo(base, plan, expected)
    return {"failing": failing}


def _slice_redo(base, plan, expected):
    character = character_recipe.config(base)
    root, assembly, out = _paths(base)
    _, index, registry, motion, poses = _poses(assembly)
    by_pose = {pose: (clip, sample) for pose, clip, sample in poses}
    load = compose_parts.AssetLoader(root, index)
    palette = pixel_grid.read_palette(root / f"inputs/design/{SHOE}.palette.json")
    canvas = (registry[index["rig"]["id"]]["canvas"]["width"], registry[index["rig"]["id"]]["canvas"]["height"])
    width, height = CROP[2]-CROP[0], CROP[3]-CROP[1]
    part = _experiment_part(character["prefix"], "b")
    state = production._load(root, part)
    rows = production._track(state, None)
    report = {}
    for page in plan["pages"]:
        folder = Path(page["folder"])
        drawn = Image.open(folder / "drawn.png").convert("RGBA")
        ratio = drawn.width / Image.open(folder / "sheet.png").width
        for pose, (x, y) in zip(page["poses"], page["origins"]):
            box = tuple(int(round(v*ratio)) for v in (x, y, x + width*SCALE, y + height*SCALE))
            native = sheets.reduce_cell(drawn.crop(box), SCALE*ratio, KEY, 80, size=(width, height))
            full = Image.new("RGBA", canvas)
            full.alpha_composite(native, CROP[:2])
            full = pixel_grid.snap(full, palette)
            regions = dict(np.load(out / "regions" / f"{pose}.npz"))
            placed, metrics = fit_expected(full, regions, expected[pose])
            clip, sample = by_pose[pose]
            body = load(motion, sample["image"]).convert("RGBA")
            socket = sample["sockets"][state["config"]["socket"]]
            record = {"kind": "parts-production-candidate", "schema_version": 1, "policy": production.POLICY, "part": part,
                      "track": production.MAIN, "pose_id": pose, "clip": clip, "sample": sample["id"],
                      "motion": index["motion"], "body_sha256": sample["image"]["sha256"], "under_sha256": None, "wearing": [],
                      "method": {"generate": "alone-b-rig-guide", "fit": metrics["offset"], "batch": None},
                      "source": {"art_sha256": assets.sha256((folder / "drawn.png").read_bytes()), "origin": [x, y]},
                      "placement": {"format": "full-canvas", "pivot": list(socket["position"]),
                                    "target_socket": state["config"]["socket"], "offset": [0, 0]},
                      "alignment": None, "holes_filled": 0, "pixels": metrics["pixels"]}
            mask = Image.fromarray(((np.asarray(placed)[..., 3] > 0)*255).astype(np.uint8), "L")
            record, location = production._publish_candidate(root, part, record, {"drawn": full, "under": body, "mask": mask, "part": placed})
            production._select(rows, [pose], location, keep_approved=False)
            report[pose] = metrics
    production._save(root, state)
    production.build(assembly, part, allow_incomplete=True)
    (out / "redo-fit.json").write_text(json.dumps(report, indent=1))
    return report


def fit_expected(part, regions, want, reach=3):
    """Place the redrawn shoes on the rig's expected shoes; a pixel sunk into the ground still costs."""
    alpha = np.asarray(part)[..., 3] > 0
    outside = ~_grow(regions["body"], 1)
    loose = ~_grow(want, 1)
    best = None
    for dy in range(-reach, reach+1):
        for dx in range(-reach, reach+1):
            moved = np.roll(np.roll(alpha, dy, 0), dx, 1)
            score = (moved & want).sum() - 0.5*(moved & loose).sum() - (moved & outside & loose).sum()
            key = (score, -(abs(dx)+abs(dy)))
            if best is None or key > best[0]:
                best = (key, dx, dy)
    _, dx, dy = best
    moved = np.roll(np.roll(np.asarray(part), dy, 0), dx, 1)
    shown = moved[..., 3] > 0
    return Image.fromarray(moved), {"offset": [dx, dy], "iou": round(float((shown & want).sum() / max(1, (shown | want).sum())), 3),
                                    "pixels": int(shown.sum())}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", required=True, type=Path)
    parser.add_argument("stage", choices=("prep", "draw", "slice", "compare", "rig", "redo"))
    parser.add_argument("--step", default="all", choices=("all", "sheet", "draw", "slice"))
    parser.add_argument("--round", type=int, default=1)
    parser.add_argument("--only", nargs="*")
    args = parser.parse_args()
    result = {"prep": lambda: stage_prep(args.base), "draw": lambda: stage_draw(args.base, args.only),
              "slice": lambda: stage_slice(args.base), "compare": lambda: stage_compare(args.base),
              "rig": lambda: stage_rig(args.base), "redo": lambda: stage_redo(args.base, args.step, args.round)}[args.stage]()
    print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
