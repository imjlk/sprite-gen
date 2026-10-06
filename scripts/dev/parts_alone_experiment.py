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


def _stage_rig_by_drawing(base):
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


# --- foot template rig: the base pose's bare foot found on every pose's body -------------------------------
ANGLES = tuple(range(-180, 180, 10))
FOOT_SCALES = (0.9, 1.0, 1.1, 1.2, 1.3)
SKIN_DISTANCE = 48


def _xcorr(field, kernel):
    """sum over the kernel of field[y+u, x+v] * kernel[u, v], for every placement inside the field."""
    H, W = field.shape
    h, w = kernel.shape
    F = np.fft.rfft2(field, s=(H, W))
    K = np.fft.rfft2(kernel, s=(H, W))
    return np.fft.irfft2(F * np.conj(K), s=(H, W))[:H-h+1, :W-w+1]


def _transform_mask(mask, turn, scale, origin):
    """mask turned by `turn` degrees and scaled about `origin`, on a canvas big enough to hold it,
    with the canvas position of `origin` returned."""
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return mask[:1, :1], (0, 0)
    phi = np.radians(turn)
    pts = np.stack([xs - origin[0], ys - origin[1]], 1).astype(float)
    rot = np.array([[np.cos(phi), -np.sin(phi)], [np.sin(phi), np.cos(phi)]]) * scale
    span = int(np.ceil(np.abs(pts).max() * scale * 1.5)) + 3
    size = 2*span + 1
    out = np.zeros((size, size), dtype=bool)
    # every output pixel takes the input pixel it comes from (nearest), so no holes appear
    oy, ox = np.mgrid[0:size, 0:size]
    inv = np.linalg.inv(rot)
    src = np.stack([ox - span, oy - span], -1).reshape(-1, 2) @ inv.T
    sx = np.round(src[:, 0] + origin[0]).astype(int)
    sy = np.round(src[:, 1] + origin[1]).astype(int)
    ok = (sx >= 0) & (sy >= 0) & (sx < mask.shape[1]) & (sy < mask.shape[0])
    flat = np.zeros(size*size, dtype=bool)
    flat[ok] = mask[sy[ok], sx[ok]]
    return flat.reshape(size, size), (span, span)


class FootRig:
    """The base pose's bare foot (the body under its shoe) and that shoe, placed by one transform per foot."""

    def __init__(self, base_body_rgba, base_shoe):
        rgba = np.asarray(base_body_rgba)
        body = rgba[..., 3] > 0
        self.foot = base_shoe & body
        self.shoe = base_shoe
        self.origin = _middle(self.foot)
        self.skin = rgba[..., :3][self.foot & (rgba[..., :3].astype(int) @ np.array([299, 587, 114]) >= 120*1000)]
        # Around the foot's own outline the body must end - except where the leg goes on up.
        self.ring = _grow(self.foot, 1) & ~self.foot & ~body

    def _skin_map(self, rgba):
        rgb = rgba[..., :3].astype(int)
        palette = np.unique(self.skin, axis=0).astype(int)
        near = np.abs(rgb[:, :, None, :] - palette[None, None]).max(-1).min(-1) <= SKIN_DISTANCE
        return (rgba[..., 3] > 0) & near

    def find(self, body_rgba, window, exclude=None, from_hip=None, keep=4, angles=None, scales=None):
        """The best transform of the base foot onto this body inside `window` (a bool mask): (score, turn, scale, at).

        A foot ends its leg: a placement with body farther from the hip right past it (the leg going
        on beyond, as on a shin) is rescored down."""
        rgba = np.asarray(body_rgba)
        opaque = (rgba[..., 3] > 0).astype(float)
        skin = self._skin_map(rgba).astype(float)
        if exclude is not None:
            skin = skin * ~exclude
        ys, xs = np.nonzero(window)
        y0, y1, x0, x1 = max(0, ys.min()-12), min(rgba.shape[0], ys.max()+13), max(0, xs.min()-12), min(rgba.shape[1], xs.max()+13)
        skin_w, opaque_w = skin[y0:y1, x0:x1], opaque[y0:y1, x0:x1]
        empty_w = 1.0 - opaque_w
        best = None
        for turn in (angles or ANGLES):
            for scale in (scales or FOOT_SCALES):
                foot, (oy, ox) = _transform_mask(self.foot, turn, scale, self.origin)
                ring, _ = _transform_mask(self.ring, turn, scale, self.origin)
                n = max(foot.shape[0], ring.shape[0])
                kf = np.zeros((n, n)); kf[:foot.shape[0], :foot.shape[1]] = foot
                kr = np.zeros((n, n)); kr[:ring.shape[0], :ring.shape[1]] = ring
                if n > skin_w.shape[0] or n > skin_w.shape[1]:
                    continue
                area = kf.sum()
                score = (_xcorr(skin_w, kf) - 2.0*_xcorr(empty_w, kf) + 1.0*_xcorr(empty_w, kr) - 1.0*_xcorr(opaque_w, kr)) / max(area, 1)
                flat = np.argsort(score, axis=None)[::-1][:keep*8]
                taken = 0
                for index in flat:
                    u, v = np.unravel_index(index, score.shape)
                    at = (x0 + v + ox, y0 + u + oy)  # canvas position of the foot's origin
                    if not window[min(max(int(at[1]), 0), window.shape[0]-1), min(max(int(at[0]), 0), window.shape[1]-1)]:
                        continue
                    value = float(score[u, v])
                    if from_hip is not None:
                        placed = self.place(self.foot, turn, scale, at, window.shape)
                        reach = from_hip[placed & (from_hip >= 0)]
                        if reach.size:
                            beyond = _grow(placed, 4) & ~placed & (from_hip > reach.max() + 1)
                            value -= 3.0 * beyond.sum() / max(area, 1)
                    if best is None or value > best[0]:
                        best = (value, turn, scale, at)
                    taken += 1
                    if taken >= keep:
                        break
        return best

    def place(self, mask, turn, scale, at, shape):
        """`mask` (in the base pose's frame) moved by a found transform onto a canvas of `shape`."""
        moved, (oy, ox) = _transform_mask(mask, turn, scale, self.origin)
        out = np.zeros(shape, dtype=bool)
        ys, xs = np.nonzero(moved)
        ty, tx = ys - oy + int(round(at[1])), xs - ox + int(round(at[0]))
        ok = (ty >= 0) & (tx >= 0) & (ty < shape[0]) & (tx < shape[1])
        out[ty[ok], tx[ok]] = True
        return out


# --- feet marked by the model: toe, heel and ankle points per foot --------------------------------------
MARKS = {"near": {"toe": (255, 0, 0), "heel": (0, 0, 255), "ankle": (0, 255, 0)},
         "far": {"toe": (128, 0, 0), "heel": (0, 0, 128), "ankle": (0, 128, 0)}}
MARK_PROMPT = (
    "Image 1 is a pixel-art pose sheet: each grid cell shows one chibi character's lower legs and bare feet in a "
    "different walking or running pose, pure side view facing right, on a flat pure magenta #FF00FF background.\n\n"
    "On every figure mark each foot with three solid square dots, each exactly one art pixel (one 8x8 block): "
    "on the NEAR foot (the lighter leg, in front) a pure red #FF0000 dot on the tip of the toes, a pure blue #0000FF "
    "dot on the back of the heel where it meets the sole, and a pure green #00FF00 dot at the ankle, where the foot "
    "meets the shin; on the FAR foot (the darker leg, behind) the same three points in dark red #800000, dark blue "
    "#000080 and dark green #008000. Put a dot even where a far foot is partly hidden, at where that point is. Change "
    "nothing else: keep every figure, the grid, the image size and the flat #FF00FF background exactly as they are.")


FOOT_SPAN = (4.0, 18.0)  # art px between a heel dot and its toe dot


def _feet_from_dots(dots):
    """Pair toe and heel dots into feet by distance (the model's near/far colors are not reliable),
    and give each foot its nearest ankle dot."""
    import itertools
    toes, heels, ankles = dots["toe"], dots["heel"], dots["ankle"]
    best, pairs = None, []
    for k in range(min(len(toes), len(heels), 2), 0, -1):
        for toe_set in itertools.permutations(range(len(toes)), k):
            for heel_set in itertools.combinations(range(len(heels)), k):
                spans = [np.hypot(toes[t][0] - heels[h][0], toes[t][1] - heels[h][1]) for t, h in zip(toe_set, heel_set)]
                if all(FOOT_SPAN[0] <= d <= FOOT_SPAN[1] for d in spans):
                    cost = sum(spans)
                    if best is None or cost < best:
                        best, pairs = cost, list(zip(toe_set, heel_set))
        if pairs:
            break
    feet = []
    for t, h in pairs:
        foot = {"toe": toes[t], "heel": heels[h]}
        mid = ((toes[t][0] + heels[h][0])/2, (toes[t][1] + heels[h][1])/2)
        if ankles:
            foot["ankle"] = min(ankles, key=lambda a: np.hypot(a[0] - mid[0], a[1] - mid[1]))
        feet.append(foot)
    return feet


def stage_marks(base, step="all"):
    """Ask the model to mark toe, heel and ankle on every foot of the bare pose sheets, then read the dots."""
    root, assembly, out = _paths(base)
    plan = json.loads((out / "plan.json").read_text())
    folder = out / "marks"
    folder.mkdir(exist_ok=True)
    if step in ("all", "draw"):
        procs = []
        for n, page in enumerate(plan["pages"]["b"], 1):
            target = folder / f"page-{n}.png"
            (folder / "prompt.txt").write_text(MARK_PROMPT)
            if target.exists():
                continue
            procs.append((target, subprocess.Popen(
                [".venv/bin/sprite-gen", "gen", "--provider", "codex", "--model", "gpt-5.6-sol", "--prompt-file",
                 str(folder / "prompt.txt"), "--out", str(target), "--ref", str(Path(page["folder"]) / "sheet.png")],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)))
        for target, proc in procs:
            _, err = proc.communicate()
            if proc.returncode:
                raise SystemExit(f"{target}: {err.decode()[-300:]}")
    marks = {}
    width, height = CROP[2]-CROP[0], CROP[3]-CROP[1]
    for n, page in enumerate(plan["pages"]["b"], 1):
        art = folder / f"page-{n}.png"
        if not art.exists():
            continue
        image = np.asarray(Image.open(art).convert("RGB")).astype(int)
        ratio = image.shape[1] / Image.open(Path(page["folder"]) / "sheet.png").width
        for pose, (x, y) in zip(page["poses"], page["origins"]):
            x0, y0 = int(round(x*ratio)), int(round(y*ratio))
            cell = image[y0:y0 + int(round(height*SCALE*ratio)), x0:x0 + int(round(width*SCALE*ratio))]
            block = SCALE*ratio
            dots = {name: [] for name in ("toe", "heel", "ankle")}
            for name in dots:
                for side in MARKS:
                    near = np.abs(cell - np.array(MARKS[side][name])).max(-1) <= 40
                    for blob in _components(near, int(block*block*0.3)):
                        ys, xs = np.nonzero(blob)
                        # canvas coordinates of the dot's middle (one art pixel = block drawn pixels)
                        dots[name].append([CROP[0] + float(xs.mean())/block - 0.5, CROP[1] + float(ys.mean())/block - 0.5])
            marks[pose] = _feet_from_dots(dots)
    (out / "marks.json").write_text(json.dumps(marks, indent=1))
    return {"poses": len(marks), "two_feet": sum(1 for v in marks.values() if len(v) == 2),
            "one_foot": [p for p, v in marks.items() if len(v) == 1], "none": [p for p, v in marks.items() if not v]}


# --- the foot rig manifest: computed (rig), optionally vision-found, hand-adjustable -----------------------
RIG_FILE = "foot-rig.json"
AGREE = 0.4  # IoU of the feet placed by rig and by vision below which they disagree
EXPOSED_PX = 3  # bare foot pixels showing outside a shoe that make it fail
DIRECTION_IOU = 0.45  # a shoe overlapping its expected shoe less than this is turned or shaped wrong


def _vision_transform(rig, base_foot, foot):
    """The transform taking the base pose's marked heel->toe onto this foot's marked heel->toe."""
    bh, bt = np.array(base_foot["heel"]), np.array(base_foot["toe"])
    ph, pt = np.array(foot["heel"]), np.array(foot["toe"])
    vb, vp = bt - bh, pt - ph
    scale = float(np.linalg.norm(vp) / max(np.linalg.norm(vb), 1e-6))
    turn = float(np.degrees(np.arctan2(vp[1], vp[0]) - np.arctan2(vb[1], vb[0])))
    turn = (turn + 180) % 360 - 180
    phi = np.radians(turn)
    rot = np.array([[np.cos(phi), -np.sin(phi)], [np.sin(phi), np.cos(phi)]]) * scale
    at = ph + rot @ (np.array(rig.origin) - bh)
    return {"turn": round(turn, 1), "scale": round(float(np.clip(scale, 0.7, 1.6)), 3), "at": [round(float(v), 2) for v in at]}


def _foot_rig(base):
    root, assembly, out = _paths(base)
    character = character_recipe.config(base)
    rows = production._load(root, _experiment_part(character["prefix"], "b"))["tracks"]["main"]["poses"]
    first = rows[BASE_POSE]["candidates"][0]
    base_shoe = _components(np.asarray(Image.open(root / first / "drawn.png"))[..., 3] > 0)[0]
    return FootRig(Image.open(root / first / "under.png").convert("RGBA"), base_shoe), rows, root, out


def stage_rig(base, vision=False):
    """Every foot's transform, from the rig (the base foot matched on the body) and, with `vision`, from
    the model's toe/heel marks; written to the manifest, keeping entries a person locked."""
    rig, rows, root, out = _foot_rig(base)
    path = out / RIG_FILE
    old = json.loads(path.read_text()) if path.exists() else {"poses": {}}
    marks = json.loads((out / "marks.json").read_text()) if vision and (out / "marks.json").exists() else {}
    base_marks = (marks.get(BASE_POSE) or [None])[0]
    manifest = {"kind": "foot-rig", "base": {"pose": BASE_POSE, "origin": list(rig.origin)},
                "how": "turn (deg) and scale of the base pose's foot and shoe about origin, moved to at; set locked: "
                       "true on a foot to keep a hand-made value; nudge [dx, dy] moves the shoe drawn on that foot "
                       "(stage apply)", "poses": {}}
    for pose, row in rows.items():
        body = Image.open(root / row["candidates"][0] / "under.png").convert("RGBA")
        opaque = np.asarray(body)[..., 3] > 0
        hip = _from_hip(opaque)
        located = np.asarray(Image.open(root / row["candidates"][0] / "drawn.png"))[..., 3] > 0
        feet, taken = [], np.zeros_like(opaque)
        for guide in _components(located):
            count = 2 if guide.sum() > 1.6*rig.shoe.sum() else 1
            for _ in range(count):
                found = rig.find(body, _grow(guide, 10), exclude=taken, from_hip=hip)
                if found is None:
                    continue
                score, turn, scale, at = found
                feet.append({"rig": {"turn": turn, "scale": scale, "at": [round(float(v), 2) for v in at], "score": round(score, 3)}})
                taken |= _grow(rig.place(rig.foot, turn, scale, at, opaque.shape), 1)
        if base_marks:
            for foot in marks.get(pose, []):
                seen = _vision_transform(rig, base_marks, foot)
                # The marks are coarse: the rig refines them near where they put the foot.
                prior = np.zeros_like(opaque)
                cx, cy = int(round(seen["at"][0])), int(round(seen["at"][1]))
                prior[max(0, cy-6):cy+7, max(0, cx-6):cx+7] = True
                refined = rig.find(body, prior, from_hip=hip, angles=tuple(int(seen["turn"]) + d for d in range(-30, 31, 10)),
                                   scales=tuple(round(seen["scale"]*k, 2) for k in (0.9, 1.0, 1.1)))
                if refined is not None:
                    seen = {"turn": refined[1], "scale": refined[2], "at": [round(float(v), 2) for v in refined[3]],
                            "score": round(refined[0], 3), "from": "marks refined by the rig"}
                placed = rig.place(rig.foot, seen["turn"], seen["scale"], seen["at"], opaque.shape)
                best, overlap = None, 0.0
                for entry in feet:
                    if "rig" not in entry:
                        continue
                    r = entry["rig"]
                    other = rig.place(rig.foot, r["turn"], r["scale"], r["at"], opaque.shape)
                    iou = (placed & other).sum() / max(1, (placed | other).sum())
                    if iou > overlap or best is None and _middle_distance(placed, other) < 8:
                        best, overlap = entry, max(overlap, iou)
                if best is not None and "vision" not in best:
                    best.update(vision=seen, agree=round(float(overlap), 2))
                else:
                    feet.append({"vision": seen, "agree": 0.0})
        for n, entry in enumerate(feet):
            pick = "rig" if "rig" in entry and (not vision or entry.get("agree", 1) >= AGREE or "vision" not in entry) else "vision"
            entry.update(id=f"foot{n+1}", source=pick, locked=False, **{k: entry[pick][k] for k in ("turn", "scale", "at")})
            if vision and "vision" in entry and "rig" in entry and entry.get("agree", 0) < AGREE:
                entry["flag"] = "rig and vision disagree: check by eye"
        kept = [f for f in old["poses"].get(pose, {}).get("feet", []) if f.get("locked")]
        for lock in kept:  # a person's value wins over whatever was computed for that foot
            feet = [f for f in feet if f["id"] != lock["id"]] + [lock]
        manifest["poses"][pose] = {"feet": sorted(feet, key=lambda f: f["id"])}
    path.write_text(json.dumps(manifest, indent=1))
    flagged = {p: [f["id"] for f in v["feet"] if f.get("flag")] for p, v in manifest["poses"].items()}
    return {"manifest": str(path), "feet": sum(len(v["feet"]) for v in manifest["poses"].values()),
            "from_vision": sum(1 for v in manifest["poses"].values() for f in v["feet"] if f["source"] == "vision"),
            "flagged": {p: f for p, f in flagged.items() if f}}


def _middle_distance(a, b):
    if not a.any() or not b.any():
        return 1e9
    (ax, ay), (bx, by) = _middle(a), _middle(b)
    return float(np.hypot(ax - bx, ay - by))


def stage_check(base):
    """Shoes against the manifest: size, bare foot showing, and direction (overlap with the expected shoe)."""
    rig, rows, root, out = _foot_rig(base)
    manifest = json.loads((out / RIG_FILE).read_text())
    report, expected = {"poses": {}}, {}
    for pose, row in rows.items():
        body = np.asarray(Image.open(root / row["selected"] / "under.png").convert("RGBA"))
        opaque = body[..., 3] > 0
        shoes = np.asarray(Image.open(root / row["selected"] / "part.png"))[..., 3] > 0
        skin = rig._skin_map(body)
        want, entries = np.zeros_like(opaque), []
        for foot in manifest["poses"].get(pose, {}).get("feet", []):
            expect = rig.place(rig.shoe, foot["turn"], foot["scale"], foot["at"], opaque.shape)
            bare = rig.place(rig.foot, foot["turn"], foot["scale"], foot["at"], opaque.shape)
            want |= expect
            mine = _components(shoes & _grow(expect, 3))
            mine = mine[0] if mine else np.zeros_like(opaque)
            ratio = mine.sum() / max(1, expect.sum())
            exposed = int((bare & skin & ~shoes).sum())
            iou = float((mine & expect).sum() / max(1, (mine | expect).sum()))
            problems = [k for k, bad in (("size", not SIZE_TOLERANCE[0] <= ratio <= SIZE_TOLERANCE[1]),
                                         ("bare foot", exposed >= EXPOSED_PX), ("direction", iou < DIRECTION_IOU)) if bad]
            entries.append({"id": foot["id"], "source": foot["source"], "ratio": round(float(ratio), 2), "exposed": exposed,
                            "iou": round(iou, 2), "problems": problems, "size_ok": not problems})
        report["poses"][pose] = entries
        expected[pose] = want
    np.savez_compressed(out / "expected.npz", **expected)
    (out / "rig.json").write_text(json.dumps(report, indent=1))  # what the redo stage reads
    _rig_sheet(base, manifest, report)
    return {p: [f"{e['id']}: {', '.join(e['problems'])}" for e in v if e["problems"]] for p, v in report["poses"].items()
            if any(e["problems"] for e in v)}


def stage_apply(base):
    """Hand adjustments from the manifest: a foot's `nudge` [dx, dy] moves the shoe drawn on that foot by
    whole pixels (a new candidate, nothing redrawn). Nudges already applied are recorded and not repeated."""
    rig, rows, root, out = _foot_rig(base)
    character = character_recipe.config(base)
    assembly = root / "assembly.json"
    path = out / RIG_FILE
    manifest = json.loads(path.read_text())
    part = _experiment_part(character["prefix"], "b")
    state = production._load(root, part)
    track = production._track(state, None)
    done = {}
    for pose, entry in manifest["poses"].items():
        moves = [f for f in entry["feet"] if f.get("nudge") and f.get("nudge") != f.get("applied")]
        if not moves:
            continue
        row = track["poses"][pose]
        base_dir = root / row["selected"]
        pixels = np.asarray(Image.open(base_dir / "part.png").convert("RGBA")).copy()
        shoes = pixels[..., 3] > 0
        for foot in moves:
            expect = rig.place(rig.shoe, foot["turn"], foot["scale"], foot["at"], shoes.shape)
            piece = max(_components(shoes, 1), key=lambda c: (c & _grow(expect, 2)).sum(), default=None)
            if piece is None:
                continue
            dx, dy = foot["nudge"]
            moved = np.zeros_like(pixels)
            moved[piece] = pixels[piece]
            pixels[piece] = 0
            moved = np.roll(np.roll(moved, dy, 0), dx, 1)
            pixels = np.where(moved[..., 3:4] > 0, moved, pixels)
            shoes = pixels[..., 3] > 0
            foot["applied"] = list(foot["nudge"])
        record = json.loads((base_dir / "candidate.json").read_text())
        record = {k: v for k, v in record.items() if k not in ("digest", "images")}
        record["method"] = {**record["method"], "manual": [{"foot": f["id"], "nudge": f["nudge"]} for f in moves]}
        record["pixels"] = int(shoes.sum())
        images = {"drawn": Image.open(base_dir / "drawn.png").convert("RGBA"),
                  "under": Image.open(base_dir / "under.png").convert("RGBA"),
                  "mask": Image.fromarray((shoes*255).astype(np.uint8), "L"), "part": Image.fromarray(pixels)}
        record, location = production._publish_candidate(root, part, record, images)
        production._select(track, [pose], location, keep_approved=False)
        done[pose] = [f["id"] for f in moves]
    production._save(root, state)
    if done:
        production.build(assembly, part, allow_incomplete=True)
    path.write_text(json.dumps(manifest, indent=1))
    return {"nudged": done}


def _rig_sheet(base, manifest, report):
    """Every pose with its expected shoes outlined and its manifest values, for adjusting by hand."""
    rig, rows, root, out = _foot_rig(base)
    tiles = []
    for pose, row in rows.items():
        body = Image.open(root / row["selected"] / "under.png").convert("RGBA")
        image = body.copy()
        image.alpha_composite(Image.open(root / row["selected"] / "part.png").convert("RGBA"))
        pixels = np.asarray(image).copy()
        lines = [pose]
        for foot, check in zip(manifest["poses"][pose]["feet"], report["poses"][pose]):
            expect = rig.place(rig.shoe, foot["turn"], foot["scale"], foot["at"], pixels.shape[:2])
            edge = expect & ~np.all([np.roll(expect, k, a) for k in (1, -1) for a in (0, 1)], axis=0)
            pixels[edge] = (0, 220, 255, 255) if not check["problems"] else (255, 60, 60, 255)
            lines.append(f"{foot['id']} {foot['source'][0]}{'L' if foot.get('locked') else ''} t{foot['turn']:.0f} "
                         f"s{foot['scale']:.2f} @{foot['at'][0]:.0f},{foot['at'][1]:.0f} {'/'.join(check['problems'])}")
        bg = Image.new("RGBA", image.size, (232, 232, 232, 255))
        bg.alpha_composite(Image.fromarray(pixels))
        tile = Image.new("RGB", (300, 230), (255, 255, 255))
        tile.paste(bg.crop((26, 68, 102, 120)).resize((300, 205), Image.Resampling.NEAREST).convert("RGB"), (0, 25))
        draw = ImageDraw.Draw(tile)
        for n, line in enumerate(lines):
            draw.text((3, 1 + n*8) if n == 0 else (3, 25 + 205 - 8*(len(lines) - n)), line, fill=(0, 0, 0))
        tiles.append(tile)
    cols = 6
    sheet = Image.new("RGB", (300*cols, 230*(-(-len(tiles)//cols))), (255, 255, 255))
    for n, tile in enumerate(tiles):
        sheet.paste(tile, ((n % cols)*300, (n//cols)*230))
    sheet.save(out / "foot-rig.png")


# --- judged by a vision model: what numbers miss (bare toes, a shoe the wrong way round) -------------------
JUDGE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["cells"],
    "properties": {"cells": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["cell", "bare_skin_visible", "wrong_direction", "too_big", "too_small", "floating", "ok", "fix"],
        "properties": {"cell": {"type": "integer"}, "bare_skin_visible": {"type": "boolean"},
                       "wrong_direction": {"type": "boolean"}, "too_big": {"type": "boolean"},
                       "too_small": {"type": "boolean"}, "floating": {"type": "boolean"}, "ok": {"type": "boolean"},
                       "fix": {"type": "string"}}}}}}
JUDGE_PROMPT = (
    "You review pixel-art shoes put on a chibi character's feet, pose by pose. Image 1 is the reference: on the left "
    "the standing pose with its shoes (the size and look every shoe should keep), on the right the shoe design. "
    "Image 2 is a grid of numbered cells; each cell shows one pose twice: on the left the bare legs, on the right the "
    "same legs wearing the shoes.\n\n"
    "For every cell judge the shoes against that pose's bare feet (left) and the reference: "
    "bare_skin_visible - any toe, heel or sole of a bare foot still shows outside a shoe; "
    "wrong_direction - a shoe does not follow its foot (toe where the foot's toe is not, sole not under the sole, or "
    "a shoe lying along the shin); too_big / too_small - a shoe is clearly bigger or smaller than its foot needs, or "
    "than the other shoe and the reference allow; floating - a shoe is not on its foot. ok is true only if all of "
    "these are false. fix: one short instruction for redrawing that cell's shoes (empty when ok), naming the near "
    "(front, lighter) or far (back, darker) shoe. Be strict: a few pixels of bare toe showing is not ok.")
JUDGE_PER_CALL = 6


def _panel(body, shod, crop=(24, 60, 104, 124), scale=5):
    width, height = (crop[2]-crop[0])*scale, (crop[3]-crop[1])*scale
    panel = Image.new("RGB", (width*2 + 12, height + 24), (255, 255, 255))
    for n, image in enumerate((body, shod)):
        bg = Image.new("RGBA", image.size, (232, 232, 232, 255))
        bg.alpha_composite(image)
        panel.paste(bg.crop(crop).resize((width, height), Image.Resampling.NEAREST).convert("RGB"), (n*(width + 12), 24))
    return panel


def stage_judge(base, method="b", tag="judge", only=None):
    """A vision model judges every pose's shoes (bare legs beside shod legs) against the reference."""
    import tempfile
    character = character_recipe.config(base)
    root, assembly, out = _paths(base)
    part = _experiment_part(character["prefix"], method)
    rows = production._load(root, part)["tracks"]["main"]["poses"]
    folder = out / tag
    folder.mkdir(exist_ok=True)
    def shod(location):
        body = Image.open(root / location / "under.png").convert("RGBA")
        image = body.copy()
        image.alpha_composite(Image.open(root / location / "part.png").convert("RGBA"))
        return body, image
    reference = Image.new("RGB", (900, 360), (255, 255, 255))
    body, image = shod(rows[BASE_POSE]["selected"])
    reference.paste(_panel(body, image).crop((0, 0, 412, 344)).resize((430, 360)), (0, 0))
    reference.paste(Image.open(root / f"inputs/design/{SHOE}.zoom.png").convert("RGB").resize((450, 225)), (440, 60))
    reference.save(folder / "reference.png")
    (folder / "schema.json").write_text(json.dumps(JUDGE_SCHEMA))
    poses = [p for p in rows if not only or p in only]
    jobs = []
    for start in range(0, len(poses), JUDGE_PER_CALL):
        chunk = poses[start:start+JUDGE_PER_CALL]
        panels = [_panel(*shod(rows[p]["selected"])) for p in chunk]
        w, h = panels[0].size
        grid = Image.new("RGB", (w*2 + 20, h*((len(panels)+1)//2) + 10), (255, 255, 255))
        draw = ImageDraw.Draw(grid)
        for n, panel in enumerate(panels):
            x, y = (n % 2)*(w + 20), (n//2)*h
            grid.paste(panel, (x, y))
            draw.text((x + 6, y + 4), f"cell {n+1}", fill=(0, 0, 0))
        path = folder / f"grid-{start//JUDGE_PER_CALL + 1}.png"
        grid.save(path)
        answer = folder / f"answer-{start//JUDGE_PER_CALL + 1}.json"
        jobs.append((chunk, answer, subprocess.Popen(
            ["codex", "exec", "--skip-git-repo-check", "-m", "gpt-5.6-sol", "-i", str(folder / "reference.png"), "-i", str(path),
             "--output-schema", str(folder / "schema.json"), "-o", str(answer), JUDGE_PROMPT],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)))
    verdicts = {}
    for chunk, answer, proc in jobs:
        _, err = proc.communicate()
        if proc.returncode or not answer.exists():
            raise SystemExit(f"judge failed: {err.decode()[-400:]}")
        cells = json.loads(answer.read_text())["cells"]
        for cell in cells:
            if 1 <= cell["cell"] <= len(chunk):
                verdicts[chunk[cell["cell"] - 1]] = {k: v for k, v in cell.items() if k != "cell"}
    (out / f"{tag}.json").write_text(json.dumps(verdicts, indent=1))
    return {"failing": {p: v["fix"] for p, v in verdicts.items() if not v["ok"]}, "judged": len(verdicts)}


JUDGED_PROMPT = (
    "Image 1 is a pixel-art pose sheet: each grid cell shows one chibi character's lower legs and bare feet in a "
    "different walking or running pose, pure side view facing right, on a flat pure magenta #FF00FF background. "
    "Image 2 shows the exact shoes: {desc}.\n\n"
    "In every cell draw ONLY the pair of shoes those feet wear, each on its own foot: the toe of the shoe at the toes "
    "of that foot, the sole along that foot's sole, the opening where its shin enters, every bit of bare foot covered. "
    "A foot raised behind the body points down and back, so its shoe does too. Draw both shoes whole, even where the "
    "other leg passes in front (the legs are layered over them later), each the size its own foot needs. Draw "
    "nothing else: no legs, no shorts, no skin, no body outline, no specks - every other pixel, up to the cell edges, "
    "is flat #FF00FF.\n\nWhat to fix, cell by cell (reading left to right, top to bottom):\n{notes}\n\n")


def stage_redo_judged(base, step="all", tag="judge", round_=1):
    """Redraw only the poses the vision judge failed, each cell with the judge's own fix."""
    character = character_recipe.config(base)
    root, assembly, out = _paths(base)
    verdicts = json.loads((out / f"{tag}.json").read_text())
    failing = [p for p, v in verdicts.items() if not v["ok"]]
    part = _experiment_part(character["prefix"], "b")
    folder = out / f"b-judged-{round_}"
    folder.mkdir(exist_ok=True)
    if step in ("all", "sheet"):
        production.review(assembly, part, reviewer="claude (vision judge)", reject=failing,
                          note="; ".join(f"{p}: {verdicts[p]['fix']}" for p in failing)[:1800])
        _, index, registry, motion, poses = _poses(assembly)
        load = compose_parts.AssetLoader(root, index)
        by_pose = {pose: sample for pose, _, sample in poses}
        pages = []
        style = avatar._garment_prompts()[2].format(px=SCALE)
        for start in range(0, len(failing), PER_PAGE):
            chunk = failing[start:start+PER_PAGE]
            page = folder / f"page-{start//PER_PAGE + 1}"
            page.mkdir(exist_ok=True)
            sheet, origins = _page([load(motion, by_pose[p]["image"]).convert("RGBA") for p in chunk])
            sheet.save(page / "sheet.png")
            notes = "\n".join(f"cell {n+1} (row {n//COLUMNS + 1}, column {n % COLUMNS + 1}): {verdicts[p]['fix']}"
                              for n, p in enumerate(chunk))
            (page / "prompt.txt").write_text(JUDGED_PROMPT.format(desc=avatar.NEW[SHOE][1], notes=notes)
                                             + COMMON.format(style=style, w=sheet.width, h=sheet.height))
            pages.append({"folder": str(page), "poses": chunk, "origins": origins})
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
        return _slice_pages(base, plan, "alone-b-judged")
    return {"failing": failing}


def _slice_pages(base, plan, how, keep=None):
    """Cells of redrawn pages -> native -> palette -> fit (off-figure pixels cost) -> candidates; rebuild."""
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
            if keep is not None:
                full = keep(pose, full)
            regions = dict(np.load(out / "regions" / f"{pose}.npz"))
            placed, metrics = fit(full, regions)
            clip, sample = by_pose[pose]
            socket = sample["sockets"][state["config"]["socket"]]
            record = {"kind": "parts-production-candidate", "schema_version": 1, "policy": production.POLICY, "part": part,
                      "track": production.MAIN, "pose_id": pose, "clip": clip, "sample": sample["id"],
                      "motion": index["motion"], "body_sha256": sample["image"]["sha256"], "under_sha256": None, "wearing": [],
                      "method": {"generate": how, "fit": metrics["offset"], "batch": None},
                      "source": {"art_sha256": assets.sha256((folder / "drawn.png").read_bytes()), "origin": [x, y]},
                      "placement": {"format": "full-canvas", "pivot": list(socket["position"]),
                                    "target_socket": state["config"]["socket"], "offset": [0, 0]},
                      "alignment": None, "holes_filled": 0, "pixels": metrics["pixels"]}
            mask = Image.fromarray(((np.asarray(placed)[..., 3] > 0)*255).astype(np.uint8), "L")
            body = load(motion, sample["image"]).convert("RGBA")
            record, location = production._publish_candidate(root, part, record, {"drawn": full, "under": body, "mask": mask, "part": placed})
            production._select(rows, [pose], location, keep_approved=False)
            report[pose] = metrics
    production._save(root, state)
    production.build(assembly, part, allow_incomplete=True)
    return report


# --- soles: feet found on the body itself, tracked through each clip ------------------------------------
FOOT_LEN = 9
def _bfs(mask, seeds, limit=None):
    dist = np.full(mask.shape, -1); q = deque()
    for y, x in zip(*np.nonzero(seeds & mask)):
        dist[y, x] = 0; q.append((y, x))
    while q:
        y, x = q.popleft()
        if limit is not None and dist[y, x] >= limit: continue
        for dy, dx in STEPS:
            ny, nx = y+dy, x+dx
            if 0 <= ny < mask.shape[0] and 0 <= nx < mask.shape[1] and mask[ny, nx] and dist[ny, nx] < 0:
                dist[ny, nx] = dist[y, x] + 1; q.append((ny, nx))
    return dist


# ---- clip-level tracking -------------------------------------------------------------------
TOP = 5
HIDDEN = 30.0  # cost of calling a foot hidden in a frame, in px of motion


def leaves(rgba):
    """Candidate toes of one frame: leaves of the skin's distance from the hem, with how far they reach."""
    rgb = rgba[..., :3].astype(int); a = rgba[..., 3] > 0
    luma = rgb @ np.array([299, 587, 114]) // 1000
    skin = a & (rgb[..., 0] > rgb[..., 2] + 30) & (luma >= 90)
    shorts = a & (np.abs(rgb[..., 0] - rgb[..., 2]) < 20) & (luma >= 55) & (luma < 140)
    hem = np.where(shorts.any(axis=1))[0].max() if shorts.any() else 80
    lower = skin & (np.arange(128)[:, None] >= hem - 8)
    seeds = lower & _grow(shorts, 3)
    hx = float(np.nonzero(shorts[hem])[0].mean()) if shorts[hem].any() else 64.0
    for comp in _components(lower, 15):
        if not (comp & seeds).any():  # a leg cut off from the hem by an outline starts nearest the crotch
            ys, xs = np.nonzero(comp); k = np.argmin(np.hypot(xs - hx, ys - hem)); seeds[ys[k], xs[k]] = True
    fh = _bfs(lower, seeds)
    reach = max(fh.max(), 1)
    padded = np.pad(fh, 3, constant_values=-1)
    around = np.max([padded[3+dy:3+dy+128, 3+dx:3+dx+128] for dy in range(-3, 4) for dx in range(-3, 4)], axis=0)
    pts = [(int(y), int(x)) for y, x in zip(*np.nonzero((fh >= 0) & (fh >= around) & (fh >= 0.35*reach)))]
    pts.sort(key=lambda p: -fh[p])
    chosen = []
    for p in pts:  # one per spot
        if all(np.hypot(p[0]-q[0], p[1]-q[1]) >= 4 for q in chosen):
            chosen.append(p)
    return [{"at": (p[1], p[0]), "reach": float(fh[p]/reach), "lit": float(luma[max(0,p[0]-3):p[0]+4, max(0,p[1]-3):p[1]+4][skin[max(0,p[0]-3):p[0]+4, max(0,p[1]-3):p[1]+4]].mean())}
            for p in chosen[:TOP]], (lower, fh)


def track(frames):
    """Two feet through a cyclic clip: per frame a leaf or hidden for each, least motion overall,
    far-reaching leaves preferred, the two never the same spot. Run over the cycle twice, keep the second."""
    n = len(frames)
    seq = frames + frames
    states = []
    for f in seq:
        options = list(range(len(f))) + [None]
        states.append([(a, b) for a in options for b in options if a is None or b is None or a != b])
    def pos(f, i, fallback):
        return np.array(f[i]["at"], float) if i is not None else fallback
    def unary(f, s):
        cost = 0.0
        for i in s:
            cost += HIDDEN if i is None else 8.0*(1 - f[i]["reach"])
        if s[0] is not None and s[1] is not None:
            d = np.hypot(*(np.array(f[s[0]]["at"]) - np.array(f[s[1]]["at"])))
            if d < 1.3*FOOT_LEN:
                cost += 50
        return cost
    # DP with each track's last seen position carried through hidden frames
    best = [{s: (unary(seq[0], s), None, [pos(seq[0], s[0], np.array([60., 105.])), pos(seq[0], s[1], np.array([70., 105.]))]) for s in states[0]}]
    for t in range(1, len(seq)):
        layer = {}
        for s in states[t]:
            u = unary(seq[t], s)
            cand = None
            for prev, (c, _, last) in best[-1].items():
                move = 0.0
                new_last = []
                for k in range(2):
                    if s[k] is None:
                        new_last.append(last[k])
                    else:
                        p = pos(seq[t], s[k], last[k]); move += float(np.hypot(*(p - last[k]))); new_last.append(p)
                total = c + u + move
                if cand is None or total < cand[0]:
                    cand = (total, prev, new_last)
            layer[s] = cand
        best.append(layer)
    s = min(best[-1], key=lambda k: best[-1][k][0])
    path = [s]
    for t in range(len(seq) - 1, 0, -1):
        s = best[t][s][1]; path.append(s)
    path = path[::-1][n:]
    return path


def _foot_geometry(body, end):
    """Heel, toe and sole of the foot at a leg's end, read along the leg: the shin (knee -> ankle) says
    which way the toes point - in a side view facing right, the shin turned a quarter turn back (down for
    a standing foot, down and back for one kicked up behind) - and the sole faces away from the ankle."""
    lower, fh = body
    seed = np.zeros_like(lower)
    seed[end[1], end[0]] = True
    near_end = _bfs(lower, seed, 22)
    depth = fh[end[1], end[0]]
    foot = (near_end >= 0) & (near_end <= 12) & (fh >= depth - 10)
    shin = (near_end >= 0) & (fh < depth - 10) & (fh >= depth - 18)
    ys, xs = np.nonzero(foot)
    pts = np.stack([xs, ys], 1).astype(float)
    c0 = pts.mean(0)
    ankle_px = foot & _grow(shin, 1)
    ankle = np.stack(np.nonzero(ankle_px)[::-1], 1).mean(0) if ankle_px.any() else c0
    if shin.any():
        knee_side = np.stack(np.nonzero(shin)[::-1], 1).mean(0)
        shin_dir = ankle - knee_side
        shin_dir = shin_dir / max(np.linalg.norm(shin_dir), 1e-6)
    else:
        shin_dir = np.array([0.0, 1.0])
    toe_dir = np.array([shin_dir[1], -shin_dir[0]])  # a quarter turn back (y down): (0, 1) -> (1, 0)
    _, vectors = np.linalg.eigh(np.cov((pts - c0).T))
    axis = vectors[:, -1]
    if axis @ toe_dir < 0:
        axis = -axis
    proj = (pts - c0) @ axis
    toe, heel = pts[np.argmax(proj)], pts[np.argmin(proj)]
    normal = np.array([-axis[1], axis[0]])
    if (ankle - c0) @ normal > 0:
        normal = -normal
    return {"toe": toe.tolist(), "heel": heel.tolist(), "normal": normal.tolist(), "foot": foot,
            "shin": shin_dir.tolist(), "ankle": ankle.tolist()}


def _similar(base, foot, origin):
    """turn, scale and where the base origin lands, taking the base heel->toe onto this one."""
    bh, bt = np.array(base["heel"]), np.array(base["toe"])
    ph, pt = np.array(foot["heel"]), np.array(foot["toe"])
    vb, vp = bt - bh, pt - ph
    scale = float(np.clip(np.linalg.norm(vp) / max(np.linalg.norm(vb), 1e-6), 0.85, 1.4))
    turn = float((np.degrees(np.arctan2(vp[1], vp[0]) - np.arctan2(vb[1], vb[0])) + 180) % 360 - 180)
    phi = np.radians(turn)
    rot = np.array([[np.cos(phi), -np.sin(phi)], [np.sin(phi), np.cos(phi)]])
    # the sole must end up on the sole side: the base normal turned should agree with this one
    agrees = float(rot @ np.array(base["normal"]) @ np.array(foot["normal"]))
    at = ph + scale * rot @ (np.array(origin) - bh)
    return {"turn": round(turn, 1), "scale": round(scale, 3), "at": [round(float(v), 2) for v in at], "sole_agrees": round(agrees, 2)}


def stage_soles(base):
    """The foot rig from the body's own feet: per clip the two toes tracked, each foot's heel->toe and
    sole, and the transform of the base foot (and shoe) onto it. Hidden feet are filled from the frames
    around them. Written to the manifest; feet a person locked are kept."""
    rig, rows, root, out = _foot_rig(base)
    _, index, registry, motion, poses = _poses(root / "assembly.json")
    load = compose_parts.AssetLoader(root, index)
    base_rgba = np.asarray(Image.open(root / rows[BASE_POSE]["candidates"][0] / "under.png").convert("RGBA"))
    base_cands, base_body = leaves(base_rgba)
    base_geo = _foot_geometry(base_body, base_cands[0]["at"])
    path = out / RIG_FILE
    old = json.loads(path.read_text()) if path.exists() else {"poses": {}}
    per_pose = {}
    for clip in motion["clips"]:
        images = [np.asarray(load(motion, s["image"]).convert("RGBA")) for s in clip["samples"]]
        found = [leaves(im) for im in images]
        chosen = track([f[0] for f in found])
        tracks = {0: [], 1: []}
        for (cands, body_maps), state, sample in zip(found, chosen, clip["samples"]):
            for k in (0, 1):
                if state[k] is None:
                    tracks[k].append(None)
                    continue
                geo = _foot_geometry(body_maps, cands[state[k]]["at"])
                tracks[k].append({**_similar(base_geo, geo, rig.origin), "lit": cands[state[k]]["lit"]})
        n = len(clip["samples"])
        for k in (0, 1):  # a hidden foot takes the mean of the nearest frames on both sides (the clip loops)
            for t in range(n):
                if tracks[k][t] is not None:
                    continue
                before = next((tracks[k][(t - j) % n] for j in range(1, n) if tracks[k][(t - j) % n] and not tracks[k][(t - j) % n].get("filled")), None)
                after = next((tracks[k][(t + j) % n] for j in range(1, n) if tracks[k][(t + j) % n] and not tracks[k][(t + j) % n].get("filled")), None)
                if before and after:
                    turn = before["turn"] + ((after["turn"] - before["turn"] + 180) % 360 - 180)/2
                    tracks[k][t] = {"turn": round(turn, 1), "scale": round((before["scale"] + after["scale"])/2, 3),
                                    "at": [round((a + b)/2, 2) for a, b in zip(before["at"], after["at"])],
                                    "sole_agrees": None, "lit": 0.0, "filled": True}
        # the near leg is the same leg all clip long: the lit one more often
        lit = [np.mean([f["lit"] for f in tracks[k] if f and not f.get("filled")] or [0]) for k in (0, 1)]
        near = int(np.argmax(lit))
        for t, sample in enumerate(clip["samples"]):
            pose = sample["pose_id"]
            if pose in per_pose:
                continue
            feet = []
            for k, role in ((near, "near"), (1 - near, "far")):
                f = tracks[k][t]
                if f is None:
                    continue
                entry = {"id": f"foot-{role}", "role": role, "source": "filled" if f.get("filled") else "soles",
                         "turn": f["turn"], "scale": f["scale"], "at": f["at"], "locked": False}
                if f.get("sole_agrees") is not None and f["sole_agrees"] < 0:
                    entry["flag"] = "the sole would face the wrong way: check by eye"
                feet.append(entry)
            if clip["id"].startswith("idle"):
                feet = feet[:1]  # standing still, the far foot is behind the near one
            per_pose[pose] = feet
    manifest = {"kind": "foot-rig", "base": {"pose": BASE_POSE, "origin": list(rig.origin), "heel": base_geo["heel"],
                                             "toe": base_geo["toe"]},
                "how": "turn (deg) and scale of the base pose's foot and shoe about origin, moved to at; set locked: "
                       "true on a foot to keep a hand-made value; nudge [dx, dy] moves the shoe drawn on that foot "
                       "(stage apply)", "poses": {}}
    for pose, feet in per_pose.items():
        kept = {f["id"]: f for f in old["poses"].get(pose, {}).get("feet", []) if f.get("locked")}
        manifest["poses"][pose] = {"feet": [kept.get(f["id"], f) for f in feet] + [f for i, f in kept.items() if i not in {g["id"] for g in feet}]}
    path.write_text(json.dumps(manifest, indent=1))
    return {"manifest": str(path), "feet": sum(len(v["feet"]) for v in manifest["poses"].values()),
            "filled": sum(1 for v in manifest["poses"].values() for f in v["feet"] if f["source"] == "filled"),
            "flagged": {p: [f["id"] for f in v["feet"] if f.get("flag")] for p, v in manifest["poses"].items() if any(f.get("flag") for f in v["feet"])}}


def _transform_rgba(image, turn, scale, origin, at, shape):
    """RGBA pixels turned and scaled about `origin` and moved there to `at`, nearest pixel, no holes."""
    src = np.asarray(image)
    phi = np.radians(turn)
    inv = np.linalg.inv(np.array([[np.cos(phi), -np.sin(phi)], [np.sin(phi), np.cos(phi)]]) * scale)
    oy, ox = np.mgrid[0:shape[0], 0:shape[1]]
    pts = np.stack([ox - at[0], oy - at[1]], -1).reshape(-1, 2) @ inv.T
    sx = np.round(pts[:, 0] + origin[0]).astype(int)
    sy = np.round(pts[:, 1] + origin[1]).astype(int)
    ok = (sx >= 0) & (sy >= 0) & (sx < src.shape[1]) & (sy < src.shape[0])
    out = np.zeros((shape[0]*shape[1], 4), dtype=np.uint8)
    out[ok] = src[sy[ok], sx[ok]]
    return out.reshape(shape[0], shape[1], 4)


def stage_transfer(base):
    """Shoes from the rig alone: the base pose's shoe moved onto every foot in the manifest - one shoe a
    foot, never more. Each pose's pair becomes a candidate; the far shoe goes under the near one."""
    rig, rows, root, out = _foot_rig(base)
    character = character_recipe.config(base)
    assembly = root / "assembly.json"
    manifest = json.loads((out / RIG_FILE).read_text())
    first = rows[BASE_POSE]["candidates"][0]
    drawn = np.asarray(Image.open(root / first / "drawn.png").convert("RGBA"))
    base_pixels = np.where(rig.shoe[..., None], drawn, 0).astype(np.uint8)
    part = _experiment_part(character["prefix"], "b")
    state = production._load(root, part)
    track_rows = production._track(state, None)
    _, index, registry, motion, poses = _poses(assembly)
    by_pose = {pose: (clip, sample) for pose, clip, sample in poses}
    for pose, entry in manifest["poses"].items():
        canvas = np.zeros((128, 128, 4), dtype=np.uint8)
        for foot in sorted(entry["feet"], key=lambda f: f["role"] != "far"):  # far first, near on top
            shoe = _transform_rgba(base_pixels, foot["turn"], foot["scale"], rig.origin, foot["at"], canvas.shape[:2])
            canvas = np.where(shoe[..., 3:4] > 0, shoe, canvas)
        clip, sample = by_pose[pose]
        body = Image.open(root / rows[pose]["candidates"][0] / "under.png").convert("RGBA")
        socket = sample["sockets"][state["config"]["socket"]]
        record = {"kind": "parts-production-candidate", "schema_version": 1, "policy": production.POLICY, "part": part,
                  "track": production.MAIN, "pose_id": pose, "clip": clip, "sample": sample["id"],
                  "motion": index["motion"], "body_sha256": sample["image"]["sha256"], "under_sha256": None, "wearing": [],
                  "method": {"generate": "rig-transfer", "feet": [{k: f[k] for k in ("id", "turn", "scale", "at", "source")} for f in entry["feet"]],
                             "batch": None},
                  "source": {"rig": RIG_FILE}, "placement": {"format": "full-canvas", "pivot": list(socket["position"]),
                                                             "target_socket": state["config"]["socket"], "offset": [0, 0]},
                  "alignment": None, "holes_filled": 0, "pixels": int((canvas[..., 3] > 0).sum())}
        image = Image.fromarray(canvas)
        mask = Image.fromarray(((canvas[..., 3] > 0)*255).astype(np.uint8), "L")
        record, location = production._publish_candidate(root, part, record, {"drawn": image, "under": body, "mask": mask, "part": image})
        production._select(track_rows, [pose], location, keep_approved=False)
    production._save(root, state)
    production.build(assembly, part, allow_incomplete=True)
    return {"poses": len(manifest["poses"])}


REFINE_PROMPT = (
    "Image 1 is a pixel-art pose sheet: each grid cell shows one chibi character's lower legs in a different walking "
    "or running pose, pure side view facing right, on a flat pure magenta #FF00FF background, wearing a pair of rough "
    "red sneakers already placed exactly where they go, one on each foot. Image 2 shows the exact shoe design: {desc}."
    "\n\nIn every cell redraw ONLY those two shoes as clean, crisp pixel art in exactly the same place, size and angle: "
    "mend broken or doubled outlines, smooth jagged edges and stray white or dark pixels, keep the toe cap, laces and "
    "sole where they are. Exactly two shoes per cell, one per foot - never add a shoe, never move one to another spot. "
    "Draw nothing else: no legs, no shorts, no skin, no body outline - every other pixel, up to the cell edges, is flat "
    "#FF00FF. ")


def stage_refine(base, step="all"):
    """The model cleans the rig's shoes: same place, size and angle, crisp pixels. Each foot then keeps the
    one redrawn piece lying on its rig shoe, so a pose never has more shoes than feet."""
    rig, rows, root, out = _foot_rig(base)
    character = character_recipe.config(base)
    assembly = root / "assembly.json"
    manifest = json.loads((out / RIG_FILE).read_text())
    folder = out / "refine"
    folder.mkdir(exist_ok=True)
    poses = list(manifest["poses"])
    if step in ("all", "sheet"):
        cells, pages = [], []
        for pose in poses:
            loc = rows[pose]["selected"]
            image = Image.open(root / loc / "under.png").convert("RGBA")
            image.alpha_composite(Image.open(root / loc / "part.png").convert("RGBA"))
            cells.append(image)
        style = avatar._garment_prompts()[2].format(px=SCALE)
        for start in range(0, len(poses), PER_PAGE):
            page = folder / f"page-{start//PER_PAGE + 1}"
            page.mkdir(exist_ok=True)
            sheet, origins = _page(cells[start:start+PER_PAGE])
            sheet.save(page / "sheet.png")
            (page / "prompt.txt").write_text(REFINE_PROMPT.format(desc=avatar.NEW[SHOE][1]) + COMMON.format(style=style, w=sheet.width, h=sheet.height))
            pages.append({"folder": str(page), "poses": poses[start:start+PER_PAGE], "origins": origins})
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
        def one_per_foot(pose, full):
            pixels = np.asarray(full).copy()
            alpha = pixels[..., 3] > 0
            keep = np.zeros_like(alpha)
            for foot in manifest["poses"][pose]["feet"]:
                expect = rig.place(rig.shoe, foot["turn"], foot["scale"], foot["at"], alpha.shape)
                pieces = _components(alpha & ~keep, 4)
                if pieces:
                    best = max(pieces, key=lambda c: (c & _grow(expect, 1)).sum())
                    if (best & _grow(expect, 1)).sum():
                        keep |= best
            pixels[~keep] = 0
            return Image.fromarray(pixels)
        return _slice_pages(base, plan, "rig-transfer-refined", keep=one_per_foot)
    return {"poses": len(poses)}


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
    parser.add_argument("stage", choices=("prep", "draw", "slice", "compare", "rig", "check", "redo", "marks", "judge",
                                          "redo-judged", "apply", "soles", "transfer", "refine"))
    parser.add_argument("--tag", default="judge")
    parser.add_argument("--poses", nargs="*")
    parser.add_argument("--vision", action="store_true", help="rig: also find feet from the model's toe/heel marks")
    parser.add_argument("--step", default="all", choices=("all", "sheet", "draw", "slice"))
    parser.add_argument("--round", type=int, default=1)
    parser.add_argument("--only", nargs="*")
    args = parser.parse_args()
    result = {"prep": lambda: stage_prep(args.base), "draw": lambda: stage_draw(args.base, args.only),
              "slice": lambda: stage_slice(args.base), "compare": lambda: stage_compare(args.base),
              "rig": lambda: stage_rig(args.base, args.vision), "check": lambda: stage_check(args.base), "redo": lambda: stage_redo(args.base, args.step, args.round),
              "marks": lambda: stage_marks(args.base, args.step), "judge": lambda: stage_judge(args.base, tag=args.tag, only=args.poses),
              "redo-judged": lambda: stage_redo_judged(args.base, args.step, args.tag, args.round),
              "apply": lambda: stage_apply(args.base), "soles": lambda: stage_soles(args.base),
              "transfer": lambda: stage_transfer(args.base), "refine": lambda: stage_refine(args.base, args.step)}[args.stage]()
    print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
