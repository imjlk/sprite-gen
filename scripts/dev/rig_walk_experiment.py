# SPDX-License-Identifier: Apache-2.0
"""Rig first: a walk built from a skeleton, so every anchor (sole, toe direction, ankle) is known.

No joint is placed by hand. The base still is measured (ground, heel, toe, ankle, hip, shoulder)
and a generic 8-frame walk is posed with those proportions; the support foot is put on the ground by
computing the hip height. The body is drawn by the image model on the skeleton; shoes come from the
skeleton's feet: the base shoe moved onto each foot, then cleaned by the model.

    skeleton  measure the base still, pose the walk, write skeleton.json and the guide sheet
    body      draw the body on the guide (one call), cut the cells to native frames
    shoes     a shoe design on every frame from the skeleton: transfer + clean (one call per design)
    show      bare / sneakers / boots, side by side, as a GIF
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parent))
import sd_avatar_process as avatar  # noqa: E402
from sprite_gen._deps import np  # noqa: E402
from sprite_gen.frames import parts_pose_sheet as sheets, pixel_grid  # noqa: E402

CANVAS = 128
CROP = (16, 12, 112, 124)  # the whole figure
SCALE, GUTTER, COLUMNS = 4, 16, 4
KEY = (255, 0, 255)
FRAMES = 8
# A generic walk, near leg: thigh swing from vertical (deg, forward +), knee bend (deg), foot pitch (deg, toe up +).
# The far leg runs the same cycle half a cycle later; arms swing against their legs.
WALK = [(22, 0, 12), (14, 14, 0), (2, 6, 0), (-12, 4, -6), (-22, 22, -28), (-8, 40, -12), (10, 36, 0), (22, 10, 6)]
ARM_SWING = 0.7
ELBOW = 12
# A run: knees fold far more, the arms bend and swing harder, and two frames leave the ground.
RUN = [(28, 14, 6), (6, 26, 0), (-20, 12, -24), (-30, 50, -30), (-6, 95, -20), (28, 80, -6), (42, 34, 4), (34, 14, 8)]
CYCLES = {"walk": {"legs": WALK, "arm_swing": ARM_SWING, "elbow": ELBOW, "flight": [0]*8, "verb": "walking"},
          "run": {"legs": RUN, "arm_swing": 1.0, "elbow": 70, "flight": [0, 0, 0, 3, 0, 0, 0, 3], "verb": "running"}}
for _name, _cycle in CYCLES.items():
    _cycle["name"] = _name
CYCLE = CYCLES["walk"]
NEAR, FAR, NEAR_ARM, FAR_ARM = (255, 140, 0), (40, 110, 255), (230, 40, 40), (150, 60, 200)
GROUND = (0, 200, 120)


def measure(still):
    """Joints of a standing side-view still, from its silhouette alone."""
    rgba = np.asarray(still)
    body = rgba[..., 3] > 0
    rgb = rgba[..., :3].astype(int)
    luma = rgb @ np.array([299, 587, 114]) // 1000
    ys, xs = np.nonzero(body)
    ground = int(ys.max())
    width = {y: (int(np.nonzero(body[y])[0].min()), int(np.nonzero(body[y])[0].max())) for y in range(ys.min(), ground + 1)
             if body[y].any()}
    bottom = [y for y in range(ground - 1, ground + 1)]
    heel = min(width[y][0] for y in bottom)
    toe = max(width[y][1] for y in bottom)
    shorts = body & (np.abs(rgb[..., 0] - rgb[..., 2]) < 20) & (luma >= 55) & (luma < 140)
    hem = int(np.nonzero(shorts.any(axis=1))[0].max())
    legs = [width[y][1] - width[y][0] + 1 for y in range(hem + 2, ground - 8)]
    leg_w = float(np.median(legs))
    ankle_y = next(y for y in range(hem + 2, ground + 1) if width[y][1] - width[y][0] + 1 > 1.5*leg_w)
    leg_x = float(np.mean([(width[y][0] + width[y][1])/2 for y in range(hem + 2, ankle_y)]))
    leg_len = ankle_y - hem
    hip = (leg_x, hem - 0.25*leg_len)
    head_top = int(ys.min())
    head_rows = [y for y in width if y < hem]
    neck = min((y for y in head_rows if y > head_top + 10), key=lambda y: width[y][1] - width[y][0])
    shoulder = ((width[neck + 3][0] + width[neck + 3][1])/2, neck + 3)
    return {"ground": ground, "heel": [heel, ground], "toe": [toe, ground], "ankle": [leg_x, float(ankle_y)],
            "hip": [hip[0], hip[1]], "knee": [leg_x, hip[1] + 0.5*(ankle_y - hip[1])], "hem": hem, "neck": neck,
            "shoulder": list(shoulder), "thigh": 0.5*(ankle_y - hip[1]), "shin": 0.5*(ankle_y - hip[1]),
            "upper_arm": 0.45*(hem - shoulder[1]), "forearm": 0.45*(hem - shoulder[1]), "leg_width": leg_w}


def _turn(vector, degrees):
    """Turn a (x, y) vector, y down, clockwise by `degrees` as seen on screen."""
    a = np.radians(degrees)
    x, y = vector
    return np.array([x*np.cos(a) - y*np.sin(a), x*np.sin(a) + y*np.cos(a)])


def _leg(hip, thigh_deg, knee_deg, pitch_deg, base):
    """Knee, ankle, heel and toe of a leg, forward kinematics from the hip."""
    down = np.array([0.0, 1.0])
    thigh = _turn(down, -thigh_deg) * base["thigh"]
    shin = _turn(down, -(thigh_deg - knee_deg)) * base["shin"]
    knee = np.array(hip) + thigh
    ankle = knee + shin
    foot_turn = -(thigh_deg - knee_deg) - pitch_deg
    heel = ankle + _turn(np.array(base["heel"]) - base["ankle"], foot_turn)
    toe = ankle + _turn(np.array(base["toe"]) - base["ankle"], foot_turn)
    return {"knee": knee.tolist(), "ankle": ankle.tolist(), "heel": heel.tolist(), "toe": toe.tolist(),
            "foot_turn": float(foot_turn)}


def pose_walk(base):
    """Eight frames: legs from the cycle, the hip lowered or raised so the lower foot stands on the ground."""
    frames = []
    for n in range(FRAMES):
        near = CYCLE["legs"][n]
        far = CYCLE["legs"][(n + FRAMES//2) % FRAMES]
        probe = {k: _leg([0.0, 0.0], *v, base) for k, v in (("near", near), ("far", far))}
        lowest = max(max(p["heel"][1], p["toe"][1]) for p in probe.values())
        hip = [base["hip"][0], base["ground"] - lowest - CYCLE["flight"][n]]
        legs = {k: _leg(hip, *v, base) for k, v in (("near", near), ("far", far))}
        lift = hip[1] - base["hip"][1]
        shoulder = [base["shoulder"][0], base["shoulder"][1] + lift]
        arms = {}
        for side, leg_cycle in (("near", far), ("far", near)):  # an arm swings with the opposite leg
            swing = CYCLE["arm_swing"] * leg_cycle[0]
            elbow = np.array(shoulder) + _turn(np.array([0.0, 1.0]), -swing) * base["upper_arm"]
            wrist = elbow + _turn(np.array([0.0, 1.0]), -(swing + CYCLE["elbow"])) * base["forearm"]
            arms[side] = {"elbow": elbow.tolist(), "wrist": wrist.tolist()}
        frames.append({"frame": n, "hip": hip, "lift": lift, "shoulder": shoulder, "legs": legs, "arms": arms})
    return frames


def guide(still, base, frame):
    """The still's head and torso moved with the hip, limbs as colored sticks, the ground line."""
    rgba = np.asarray(still)
    upper = rgba.copy()
    cut = int(base["hem"]) - 2
    upper[cut:] = 0
    # the still's own hanging arm stays: only legs are redrawn from it; arms are given as sticks too
    out = Image.new("RGBA", (CANVAS, CANVAS))
    out.alpha_composite(Image.fromarray(upper), (0, int(round(frame["lift"]))))
    draw = ImageDraw.Draw(out)
    width = max(2, int(round(base["leg_width"])))
    for side, color in (("far", FAR), ("near", NEAR)):
        leg = frame["legs"][side]
        draw.line([tuple(frame["hip"]), tuple(leg["knee"]), tuple(leg["ankle"])], fill=(*color, 255), width=width)
        draw.polygon([tuple(leg["ankle"]), tuple(leg["heel"]), tuple(leg["toe"])], fill=(*color, 255))
    for side, color in (("far", FAR_ARM), ("near", NEAR_ARM)):
        arm = frame["arms"][side]
        draw.line([tuple(frame["shoulder"]), tuple(arm["elbow"]), tuple(arm["wrist"])], fill=(*color, 255), width=2)
    draw.line([(0, base["ground"] + 1), (CANVAS, base["ground"] + 1)], fill=(*GROUND, 255), width=1)
    return out


def _sheet(cells):
    width, height = (CROP[2]-CROP[0])*SCALE, (CROP[3]-CROP[1])*SCALE
    rows = -(-len(cells)//COLUMNS)
    sheet = Image.new("RGBA", (GUTTER + COLUMNS*(width+GUTTER), GUTTER + rows*(height+GUTTER)), (*KEY, 255))
    origins = []
    for n, cell in enumerate(cells):
        origin = (GUTTER + n % COLUMNS*(width+GUTTER), GUTTER + n//COLUMNS*(height+GUTTER))
        sheet.alpha_composite(cell.crop(CROP).resize((width, height), Image.Resampling.NEAREST), origin)
        origins.append(origin)
    return sheet, origins


def _cells(drawn_path, sheet_path, origins, palette):
    drawn = Image.open(drawn_path).convert("RGBA")
    ratio = drawn.width / Image.open(sheet_path).width
    width, height = CROP[2]-CROP[0], CROP[3]-CROP[1]
    out = []
    for x, y in origins:
        box = tuple(int(round(v*ratio)) for v in (x, y, x + width*SCALE, y + height*SCALE))
        native = sheets.reduce_cell(drawn.crop(box), SCALE*ratio, KEY, 80, size=(width, height))
        full = Image.new("RGBA", (CANVAS, CANVAS))
        full.alpha_composite(native, CROP[:2])
        out.append(pixel_grid.snap(full, palette) if palette is not None else full)
    return out


def _draw(prompt, sheet, refs, out):
    command = [".venv/bin/sprite-gen", "gen", "--provider", "codex", "--model", "gpt-5.6-sol", "--prompt-file", str(prompt),
               "--out", str(out), "--ref", str(sheet)]
    for ref in refs:
        command += ["--ref", str(ref)]
    done = subprocess.run(command, capture_output=True, text=True)
    if done.returncode:
        raise SystemExit(done.stderr[-400:])


BODY_PROMPT = (
    "Image 1 is a pose guide sheet for a pixel-art game sprite, one {verb} pose per grid cell, pure side view facing "
    "right, on a flat pure magenta #FF00FF background. In each cell the character's head and torso are already drawn; "
    "its limbs are given as colored sticks: the orange stick and orange foot are the NEAR leg (in front), the blue ones "
    "the FAR leg (behind), the red stick the near arm, the purple stick the far arm; the green line is the ground. "
    "Image 2 is the character: a bald chibi mannequin in a light-gray sleeveless undershirt and dark charcoal shorts, "
    "barefoot.\n\n"
    "In every cell draw that exact character in that pose: keep the head and torso exactly as they are, and draw each "
    "leg and arm along its stick - thigh, knee, shin, ankle and foot exactly where the stick and foot shape put them, "
    "the foot's sole on the edge of its colored foot shape, a standing foot flat on the green line. The far leg and "
    "far arm are behind, a little darker. Same proportions, skin, shorts, outline and shading as image 2. Remove "
    "every stick, foot shape and the green line: nothing of the guide colors stays. Crisp hard-edged pixel art on the "
    "same grid as image 1 (each art pixel a 4x4 block), a 1-pixel dark outline, no anti-aliasing, no blur, no text. "
    "Keep every figure exactly in its own cell where it is; empty space stays flat #FF00FF. Keep this {w}x{h} image "
    "and its grid exactly.")


def stage_skeleton(base_dir):
    still = Image.open(base_dir / "design/base.canvas.png").convert("RGBA")
    base = measure(still)
    frames = pose_walk(base)
    out = base_dir / f"experiments/rig-{CYCLE['name']}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "skeleton.json").write_text(json.dumps({"base": base, "frames": frames, "cycle": CYCLE}, indent=1))
    sheet, origins = _sheet([guide(still, base, f) for f in frames])
    sheet.save(out / "guide.png")
    (out / "body-prompt.txt").write_text(BODY_PROMPT.format(w=sheet.width, h=sheet.height, verb=CYCLE["verb"]))
    (out / "origins.json").write_text(json.dumps(origins))
    return {"base": {k: base[k] for k in ("ground", "heel", "toe", "ankle", "hip", "shoulder", "thigh")}, "frames": len(frames)}


def stage_body(base_dir):
    out = base_dir / f"experiments/rig-{CYCLE['name']}"
    if not (out / "body.png").exists():
        _draw(out / "body-prompt.txt", out / "guide.png", [base_dir / "design/base.canvas.png"], out / "body.png")
    palette = pixel_grid.read_palette(base_dir / "design/base.palette.json")
    frames = _cells(out / "body.png", out / "guide.png", json.loads((out / "origins.json").read_text()), palette)
    (out / "frames").mkdir(exist_ok=True)
    for n, frame in enumerate(frames):
        frame.save(out / "frames" / f"body-{n}.png")
    return {"frames": len(frames)}


FEET_CROP = (32, 84, 96, 116)
DESIGNS = {
    "sneakers": "red canvas high-top sneakers with white rubber soles, white toe caps and white laces on both feet",
    "boots": "tan-brown leather ankle boots with laces and dark soles",
}
BASE_SHOE_PROMPT = (
    "Image 1 is a pixel-art close-up of one chibi character's lower legs and bare feet, standing, pure side view facing "
    "right, on a flat pure magenta #FF00FF background. Draw ONLY the shoes those feet wear: {desc}. Each shoe exactly "
    "where its foot is, the sole on the sole, covering every bit of bare foot, and only a little bigger than the foot: the "
    "shoe's top ends just above the ankle bone, never up the shin. Draw nothing else: no legs, no shorts, "
    "no skin, no body outline - every other pixel is flat #FF00FF. Simple and readable for a tiny game sprite: a crisp "
    "1-pixel dark outline, 3-4 tone shading lit from the upper left, crisp hard-edged pixel art on the same grid as "
    "image 1 (each art pixel an 8x8 block), no anti-aliasing, no blur, no text. Keep the image size.")
CLEAN_PROMPT = (
    "Image 1 is a pixel-art sheet: each grid cell shows a chibi character {verb}, pure side view facing right, on a "
    "flat pure magenta #FF00FF background, wearing rough {desc} already placed exactly where they go, one on each foot. "
    "In every cell redraw ONLY those two shoes as clean, crisp pixel art in exactly the same place, size and angle: "
    "mend broken or doubled outlines and stray pixels, keep the sole along the foot's sole. Exactly two shoes per cell, "
    "one per foot - never add or move one. Draw nothing else: no body, no legs, no skin - every other pixel, up to the "
    "cell edges, is flat #FF00FF. Crisp hard-edged pixel art on the same grid as image 1 (each art pixel a 4x4 block), "
    "a 1-pixel dark outline, no anti-aliasing, no blur, no text. Keep this {w}x{h} image and its grid exactly.")


def _similar_rgba(image, src_heel, src_toe, dst_heel, dst_toe):
    """`image` moved so its heel->toe lands on dst heel->toe (turn, scale, shift), nearest pixel."""
    src = np.asarray(image)
    a, b = np.array(src_heel, float), np.array(src_toe, float)
    c, d = np.array(dst_heel, float), np.array(dst_toe, float)
    vs, vd = b - a, d - c
    scale = np.linalg.norm(vd) / max(np.linalg.norm(vs), 1e-6)
    phi = np.arctan2(vd[1], vd[0]) - np.arctan2(vs[1], vs[0])
    rot = np.array([[np.cos(phi), -np.sin(phi)], [np.sin(phi), np.cos(phi)]]) * scale
    inv = np.linalg.inv(rot)
    oy, ox = np.mgrid[0:CANVAS, 0:CANVAS]
    pts = np.stack([ox - c[0], oy - c[1]], -1).reshape(-1, 2) @ inv.T + a
    sx, sy = np.round(pts[:, 0]).astype(int), np.round(pts[:, 1]).astype(int)
    ok = (sx >= 0) & (sy >= 0) & (sx < src.shape[1]) & (sy < src.shape[0])
    out = np.zeros((CANVAS*CANVAS, 4), dtype=np.uint8)
    out[ok] = src[sy[ok], sx[ok]]
    return out.reshape(CANVAS, CANVAS, 4)


def _one_shoe(image):
    """The front shoe of a standing pair: the piece holding the rightmost toe (the far one hides behind it)."""
    from sprite_gen.frames.parts_segments import _components
    pixels = np.asarray(image).copy()
    pieces = sorted(_components(pixels[..., 3] > 0), key=lambda c: -int(c.sum()))
    if len(pieces) > 1:
        keep = max(pieces[:2], key=lambda c: np.nonzero(c)[1].max())
        pixels[~keep] = 0
    return Image.fromarray(pixels)


def _near_leg(frame, base):
    mask = Image.new("L", (CANVAS, CANVAS))
    draw = ImageDraw.Draw(mask)
    leg = frame["legs"]["near"]
    width = int(round(base["leg_width"])) + 2
    draw.line([tuple(frame["hip"]), tuple(leg["knee"]), tuple(leg["ankle"])], fill=255, width=width)
    return np.asarray(mask) > 0


def stage_shoes(base_dir, design):
    """One design on every frame from the skeleton's feet: the base shoe on the still, moved heel->toe onto each
    foot (far first, near over it), then cleaned by the model; each foot keeps the one piece on its shoe."""
    from sprite_gen.frames.parts_segments import _components
    out = base_dir / f"experiments/rig-{CYCLE['name']}"
    sk = json.loads((out / "skeleton.json").read_text())
    base = sk["base"]
    work = out / design
    work.mkdir(exist_ok=True)
    still = Image.open(base_dir / "design/base.canvas.png").convert("RGBA")
    if not (work / "base-shoe.png").exists():
        width, height = FEET_CROP[2]-FEET_CROP[0], FEET_CROP[3]-FEET_CROP[1]
        sheet = Image.new("RGBA", (width*8, height*8), (*KEY, 255))
        sheet.alpha_composite(still.crop(FEET_CROP).resize((width*8, height*8), Image.Resampling.NEAREST))
        sheet.save(work / "base-sheet.png")
        (work / "base-prompt.txt").write_text(BASE_SHOE_PROMPT.format(desc=DESIGNS[design]))
        _draw(work / "base-prompt.txt", work / "base-sheet.png", [], work / "base-drawn.png")
        drawn = Image.open(work / "base-drawn.png").convert("RGBA")
        native = sheets.reduce_cell(drawn, drawn.width/width, KEY, 80, size=(width, height))
        full = Image.new("RGBA", (CANVAS, CANVAS))
        full.alpha_composite(native, FEET_CROP[:2])
        _one_shoe(full).save(work / "base-shoe.png")
    shoe = Image.open(work / "base-shoe.png").convert("RGBA")
    cells, expected = [], []
    for n, frame in enumerate(sk["frames"]):
        body = Image.open(out / "frames" / f"body-{n}.png").convert("RGBA")
        layers, want = [], []
        for side in ("far", "near"):
            leg = frame["legs"][side]
            moved = _similar_rgba(shoe, base["heel"], base["toe"], leg["heel"], leg["toe"])
            layers.append(moved)
            want.append(moved[..., 3] > 0)
        canvas = layers[0].copy()
        canvas[_near_leg(frame, base)] = 0  # the near leg passes in front of the far shoe
        canvas = np.where(layers[1][..., 3:4] > 0, layers[1], canvas)
        Image.fromarray(canvas).save(work / f"rough-{n}.png")
        cell = body.copy()
        cell.alpha_composite(Image.fromarray(canvas))
        cells.append(cell)
        expected.append(want)
    sheet, origins = _sheet(cells)
    sheet.save(work / "rough-sheet.png")
    (work / "clean-prompt.txt").write_text(CLEAN_PROMPT.format(desc=DESIGNS[design], w=sheet.width, h=sheet.height, verb=CYCLE["verb"]))
    if not (work / "clean.png").exists():
        _draw(work / "clean-prompt.txt", work / "rough-sheet.png", [work / "base-shoe.png"], work / "clean.png")
    cleaned = _cells(work / "clean.png", work / "rough-sheet.png", origins, None)
    report = []
    for n, (frame, image, want) in enumerate(zip(sk["frames"], cleaned, expected)):
        # The model may move a whole row of cells a few pixels: put each cell back on its rough shoes first.
        pixels = np.asarray(image)
        rough = want[0] | want[1]
        alpha = pixels[..., 3] > 0
        shift = max(((dx, dy) for dy in range(-6, 7) for dx in range(-6, 7)),
                    key=lambda d: (int((np.roll(np.roll(alpha, d[1], 0), d[0], 1) & rough).sum()), -abs(d[0]) - abs(d[1])))
        pixels = np.roll(np.roll(pixels, shift[1], 0), shift[0], 1).copy()
        alpha = pixels[..., 3] > 0
        keep, kept = np.zeros_like(alpha), []
        for side, mask in zip(("far", "near"), want):
            pieces = [c for c in _components(alpha & ~keep) if c.sum() >= 4]
            best = max(pieces, key=lambda c: (c & mask).sum(), default=None)
            if best is not None and (best & mask).sum():
                keep |= best
                kept.append({"side": side, "px": int(best.sum()), "on_rig": round(float((best & mask).sum() / max(1, mask.sum())), 2)})
        pixels[~keep] = 0
        far_part = np.zeros_like(alpha)
        if kept and kept[0]["side"] == "far":
            far_part = keep & want[0] & _near_leg(frame, base)
        pixels[far_part] = 0  # still behind the near leg
        Image.fromarray(pixels).save(work / f"shoes-{n}.png")
        report.append({"frame": n, "shoes": len(kept), "shift": list(shift), "pieces": kept})
    (work / "report.json").write_text(json.dumps(report, indent=1))
    return report


def stage_show(base_dir):
    out = base_dir / f"experiments/rig-{CYCLE['name']}"
    looks = ["bare"] + [d for d in DESIGNS if (out / d / "shoes-0.png").exists()]
    crop, scale = (24, 14, 104, 122), 4
    w, h = (crop[2]-crop[0])*scale, (crop[3]-crop[1])*scale
    images = []
    for n in range(FRAMES):
        sheet = Image.new("RGBA", (w*len(looks), h), (236, 236, 236, 255))
        for k, look in enumerate(looks):
            frame = Image.open(out / "frames" / f"body-{n}.png").convert("RGBA")
            if look != "bare":
                frame.alpha_composite(Image.open(out / look / f"shoes-{n}.png").convert("RGBA"))
            sheet.alpha_composite(frame.crop(crop).resize((w, h), Image.Resampling.NEAREST), (k*w, 0))
        images.append(sheet)
    images[0].save(out / f"{CYCLE['name']}.gif", save_all=True, append_images=images[1:], duration=110 if CYCLE["name"] == "walk" else 80, loop=0)
    strip = Image.new("RGBA", (w*len(looks)//2*FRAMES//2, h*2), (236, 236, 236, 255))
    grid = Image.new("RGBA", (w*FRAMES//2*len(looks), h*2), (236, 236, 236, 255))
    for n, image in enumerate(images):
        grid.alpha_composite(image, ((n % (FRAMES//2))*w*len(looks), (n//(FRAMES//2))*h))
    grid.save(out / f"{CYCLE['name']}.png")
    return {"looks": looks, "gif": str(out / f"{CYCLE['name']}.gif")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", required=True, type=Path)
    parser.add_argument("stage", choices=("skeleton", "body", "shoes", "show"))
    parser.add_argument("--design", default="sneakers", choices=tuple(DESIGNS))
    parser.add_argument("--cycle", default="walk", choices=tuple(CYCLES))
    args = parser.parse_args()
    CYCLE = CYCLES[args.cycle]
    if args.stage == "shoes":
        result = stage_shoes(args.base, args.design)
    else:
        result = {"skeleton": stage_skeleton, "body": stage_body, "show": stage_show}[args.stage](args.base)
    print(json.dumps(result, indent=1, default=str))
