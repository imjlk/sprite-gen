# SPDX-License-Identifier: Apache-2.0
"""A character built from its skeleton: every joint and anchor is a number, never detected.

A body type is a set of proportions. Its standing skeleton is drawn as a guide and the image model
draws the base mannequin on it; few-frame cycles (idle, walk, run) are posed from the same numbers
and drawn the same way. Parts take their anchors (sole, toe direction, ankle) straight from the
skeleton: a design is drawn once on the base, moved onto every frame's feet and cleaned by the model.

    base     the standing skeleton -> guide -> the base mannequin (design/base.png, palette, skeleton)
    check    how well a drawing follows its skeleton (limb sticks covered by body pixels)
    frames   a cycle (idle 3, walk 6, run 6) -> guide sheet -> drawn frames
    shoes    a shoe design on every frame of a cycle from the skeleton's feet
    show     bare and shod, side by side, per cycle
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parent))
from sprite_gen._deps import np  # noqa: E402
from sprite_gen.frames import parts_pose_sheet as sheets, pixel_grid  # noqa: E402

CANVAS = 128
KEY = (255, 0, 255)
CROP = (16, 8, 112, 124)
COLORS = 28
NEAR, FAR, NEAR_ARM, FAR_ARM = (255, 140, 0), (40, 110, 255), (230, 40, 40), (150, 60, 200)
HEAD, TORSO, GROUND, EAR = (200, 200, 200), (150, 150, 150), (0, 200, 120), (90, 90, 90)

BODIES = {
    "slim": {
        "prompt": "a cute slim chibi (super-deformed) girl base mannequin: a big round head about half the total "
                  "height, a slender small body with narrow shoulders, thin short arms and legs, small hands and feet",
        "ground": 112, "head": {"center": [64, 37], "radius": 16, "ear": [-3, 2]}, "neck": [64, 54], "shoulder": [64, 58],
        "hip": [64, 76], "torso": {"top": 9, "bottom": 11}, "thigh": 17, "shin": 16,
        "heel": [-3, 3], "toe": [8, 3],  # from the ankle, standing
        "upper_arm": 10, "forearm": 9, "width": {"thigh": 5, "shin": 4, "arm": 3},
    },
}
# Standing for the base: both feet and both hands in sight.
STAND = {"near": (10, 4, 0), "far": (-10, 4, 0), "arms": {"near": (22, 15), "far": (-32, 10)}}
# Few-frame cycles, near leg: thigh swing from vertical (deg, forward +), knee bend, foot pitch (toe up +).
# The far leg runs half a cycle later; arms swing against their legs. `lift` raises the hip (flight, breath).
CYCLES = {
    "idle": {"legs": [(10, 4, 0)]*3, "far": [(-10, 4, 0)]*3, "arm_swing": 0.0, "arm": (22, 15), "elbow": 12,
             "lift": [0, -1, 0], "verb": "standing and breathing", "ms": 260},
    "walk": {"legs": [(22, 0, 12), (8, 10, 0), (-10, 4, -6), (-22, 22, -28), (-4, 40, -10), (16, 22, 4)],
             "arm_swing": 0.7, "elbow": 12, "lift": [0]*6, "verb": "walking", "ms": 130},
    "run": {"legs": [(30, 14, 6), (0, 26, 0), (-28, 30, -30), (-6, 95, -20), (30, 80, -6), (40, 24, 6)],
            "arm_swing": 1.0, "elbow": 70, "lift": [0, 0, 0, 3, 0, 3], "verb": "running", "ms": 90},
}
DESIGNS = {
    "sneakers": "red canvas high-top sneakers with white rubber soles, white toe caps and white laces on both feet",
    "boots": "tan-brown leather ankle boots with laces and dark soles",
}


def _turn(vector, degrees):
    """(x, y), y down, turned clockwise on screen by `degrees`."""
    a = np.radians(degrees)
    x, y = vector
    return np.array([x*np.cos(a) - y*np.sin(a), x*np.sin(a) + y*np.cos(a)])


def _leg(hip, pose, body):
    thigh_deg, knee_deg, pitch = pose
    down = np.array([0.0, 1.0])
    knee = np.array(hip) + _turn(down, -thigh_deg)*body["thigh"]
    ankle = knee + _turn(down, -(thigh_deg - knee_deg))*body["shin"]
    foot = -(thigh_deg - knee_deg) - pitch
    return {"knee": knee.tolist(), "ankle": ankle.tolist(), "heel": (ankle + _turn(body["heel"], foot)).tolist(),
            "toe": (ankle + _turn(body["toe"], foot)).tolist(), "foot_turn": float(foot)}


def _arm(shoulder, swing, elbow, body):
    down = np.array([0.0, 1.0])
    el = np.array(shoulder) + _turn(down, -swing)*body["upper_arm"]
    wrist = el + _turn(down, -(swing + elbow))*body["forearm"]
    return {"elbow": el.tolist(), "wrist": wrist.tolist()}


def skeleton(body, near, far, arms, lift=0):
    """One pose: legs from the hip, the hip set so the lower foot stands on the ground (then `lift`)."""
    probe = [_leg([0.0, 0.0], p, body) for p in (near, far)]
    lowest = max(max(p["heel"][1], p["toe"][1]) for p in probe)
    hip = [body["hip"][0], body["ground"] - lowest - lift]
    shift = hip[1] - body["hip"][1]
    shoulder = [body["shoulder"][0], body["shoulder"][1] + shift]
    return {"hip": hip, "shift": shift, "shoulder": shoulder,
            "head": {"center": [body["head"]["center"][0], body["head"]["center"][1] + shift], "radius": body["head"]["radius"]},
            "neck": [body["neck"][0], body["neck"][1] + shift],
            "legs": {"near": _leg(hip, near, body), "far": _leg(hip, far, body)},
            "arms": {side: _arm(shoulder, *arms[side], body) for side in ("near", "far")}}


def draw_guide(body, pose, upper=None):
    """Sticks on the key: far limbs, then head and torso (or the base's own, moved), then near limbs."""
    image = Image.new("RGBA", (CANVAS, CANVAS))
    draw = ImageDraw.Draw(image)
    w = body["width"]

    def leg(side, color):
        L = pose["legs"][side]
        draw.line([tuple(pose["hip"]), tuple(L["knee"])], fill=(*color, 255), width=w["thigh"])
        draw.line([tuple(L["knee"]), tuple(L["ankle"])], fill=(*color, 255), width=w["shin"])
        draw.polygon([tuple(L["ankle"]), tuple(L["heel"]), tuple(L["toe"])], fill=(*color, 255))

    def arm(side, color):
        A = pose["arms"][side]
        draw.line([tuple(pose["shoulder"]), tuple(A["elbow"]), tuple(A["wrist"])], fill=(*color, 255), width=w["arm"])

    arm("far", FAR_ARM)
    leg("far", FAR)
    if upper is None:
        cx, cy = pose["head"]["center"]
        r = pose["head"]["radius"]
        top, bottom = body["torso"]["top"], body["torso"]["bottom"]
        sx, sy = pose["shoulder"]
        hx, hy = pose["hip"]
        draw.polygon([(sx - top/2, sy - 3), (sx + top/2, sy - 3), (hx + bottom/2, hy + 2), (hx - bottom/2, hy + 2)],
                     fill=(*TORSO, 255))
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(*HEAD, 255))
        ex, ey = cx + body["head"]["ear"][0], cy + body["head"]["ear"][1]
        draw.ellipse((ex - 2, ey - 3, ex + 2, ey + 3), fill=(*EAR, 255))
    else:
        image.alpha_composite(upper, (0, int(round(pose["shift"]))))
    leg("near", NEAR)
    arm("near", NEAR_ARM)
    draw.line([(0, body["ground"] + 1), (CANVAS, body["ground"] + 1)], fill=(*GROUND, 255), width=1)
    return image


def _sheet(cells, scale, columns, crop=CROP, gutter=16):
    width, height = (crop[2]-crop[0])*scale, (crop[3]-crop[1])*scale
    rows = -(-len(cells)//columns)
    sheet = Image.new("RGBA", (gutter + columns*(width+gutter), gutter + rows*(height+gutter)), (*KEY, 255))
    origins = []
    for n, cell in enumerate(cells):
        origin = (gutter + n % columns*(width+gutter), gutter + n//columns*(height+gutter))
        sheet.alpha_composite(cell.crop(crop).resize((width, height), Image.Resampling.NEAREST), origin)
        origins.append(origin)
    return sheet, origins


def _cells(drawn_path, sheet_path, origins, scale, palette=None, crop=CROP):
    drawn = Image.open(drawn_path).convert("RGBA")
    ratio = drawn.width / Image.open(sheet_path).width
    width, height = crop[2]-crop[0], crop[3]-crop[1]
    out = []
    for x, y in origins:
        box = tuple(int(round(v*ratio)) for v in (x, y, x + width*scale, y + height*scale))
        native = sheets.reduce_cell(drawn.crop(box), scale*ratio, KEY, 80, size=(width, height))
        full = Image.new("RGBA", (CANVAS, CANVAS))
        full.alpha_composite(native, crop[:2])
        out.append(pixel_grid.snap(full, palette) if palette is not None else full)
    return out


def _onto_guide(image, guide, reach=4):
    """Shift a native drawing by whole pixels onto its guide's silhouette (the cut back can be a pixel or
    two off where the model resampled); the skeleton's numbers stay right."""
    alpha = np.asarray(image)[..., 3] > 0
    target = np.asarray(guide)[..., 3] > 0
    best = max(((dx, dy) for dy in range(-reach, reach+1) for dx in range(-reach, reach+1)),
               key=lambda d: (int((np.roll(np.roll(alpha, d[1], 0), d[0], 1) & target).sum())
                              - int((np.roll(np.roll(alpha, d[1], 0), d[0], 1) & ~target).sum()), -abs(d[0]) - abs(d[1])))
    return Image.fromarray(np.roll(np.roll(np.asarray(image), best[1], 0), best[0], 1)), best


def _clean_native(drawn_path, sheet_path, origin, scale, crop=CROP, colors=None, palette=None):
    """One drawn cell back to native pixels where the guide put it: the drawing's own grid found
    (models resample their output), one pixel read per block, a palette and a closed outline."""
    from sprite_gen.frames import extract
    drawn = Image.open(drawn_path).convert("RGBA")
    ratio = drawn.width / Image.open(sheet_path).width
    width, height = crop[2]-crop[0], crop[3]-crop[1]
    x, y = origin
    box = tuple(int(round(v*ratio)) for v in (x, y, x + width*scale, y + height*scale))
    cell = np.asarray(drawn.crop(box)).copy()
    key = (np.abs(cell[..., :3].astype(int) - np.array(KEY)).max(-1) <= 60)
    cell[key | (cell[..., 3] < 128)] = 0
    raw = Image.fromarray(cell)
    bbox = raw.getbbox()
    if bbox is None:
        return Image.new("RGBA", (CANVAS, CANVAS)), palette
    pitch = pixel_grid.find_pitch([raw], scale*ratio, spread=0.06, steps=25)
    art, _ = pixel_grid.lattice_reduce(raw, pitch)
    if palette is None:
        palette = np.array(extract.build_shared_palette([art], colors or COLORS))
    dark = min(palette.tolist(), key=lambda c: 299*c[0] + 587*c[1] + 114*c[2])
    art = pixel_grid.silhouette_outline(pixel_grid.snap(art, palette), [int(v) for v in dark])
    art = art.crop(art.getbbox())
    full = Image.new("RGBA", (CANVAS, CANVAS))
    full.alpha_composite(art, (int(round(crop[0] + bbox[0]/pitch)), int(round(crop[1] + bbox[1]/pitch))))
    return full, palette


def _draw(prompt_text, folder, sheet, refs, name):
    (folder / f"{name}-prompt.txt").write_text(prompt_text)
    out = folder / f"{name}.png"
    if out.exists():
        return out
    command = [".venv/bin/sprite-gen", "gen", "--provider", "codex", "--model", "gpt-5.6-sol", "--prompt-file",
               str(folder / f"{name}-prompt.txt"), "--out", str(out), "--ref", str(sheet)]
    for ref in refs:
        command += ["--ref", str(ref)]
    done = subprocess.run(command, capture_output=True, text=True)
    if done.returncode:
        raise SystemExit(done.stderr[-400:])
    return out


STYLE = ("Strict pixel art: perfectly square pixels on one consistent grid (each art pixel an {px}x{px} block), a crisp "
         "continuous 1-pixel dark outline, slightly lighter colored outline where a limb overlaps the body; one soft light "
         "from the upper left, a 4-step ramp per material with hue-shifted shadows (skin shadows warm red-brown, cloth "
         "shadows cool blue-gray), crisp clusters, no dithering noise, no anti-aliasing, no gradients, no blur, no text.")
BASE_PROMPT = (
    "Image 1 is a layout guide for a high-quality pixel-art game sprite, pure side view facing right, on a flat pure "
    "magenta #FF00FF background: the light grey circle is the head, the grey shape the torso, the orange stick and "
    "foot the NEAR leg (in front), the blue ones the FAR leg (behind), the red stick the near arm, the purple stick the "
    "far arm, the green line the ground.\n\n"
    "Draw on exactly this guide {who}, standing. The head fills the grey circle, the face toward the right; its one "
    "visible ear sits exactly on the small dark grey mark - a little behind the middle of the head, level with where "
    "the eyes would be, never down by the jaw or neck. Completely bald, a blank face: NO eyes, NO eyebrows, NO mouth, "
    "NO hair (features are separate layers); the torso fills the grey "
    "shape; every limb lies along its stick - thigh, knee, shin, ankle and foot exactly where the stick and foot shape "
    "put them, both feet flat on the green line; each arm along its stick with a small hand at its end. The near limbs "
    "are in front and lit; the far limbs are behind, a little darker, separated from the near ones by an outline where "
    "they overlap. It wears only a plain light-gray sleeveless undershirt and plain dark charcoal shorts, barefoot. No "
    "accessories, no patterns. Remove every guide color: nothing of the sticks, shapes or the green line stays. "
    "{style} Keep the image size; everything else stays flat #FF00FF.")
FRAMES_PROMPT = (
    "Image 1 is a pose guide sheet for a pixel-art game sprite, one {verb} pose per grid cell, pure side view facing "
    "right, on a flat pure magenta #FF00FF background. In each cell the character's head and torso are already drawn; "
    "its limbs are colored sticks: orange stick and foot = NEAR leg (in front), blue = FAR leg (behind), red = near arm, "
    "purple = far arm; the green line is the ground. Image 2 is the character standing.\n\n"
    "In every cell draw that exact character in that pose: keep the head and torso exactly as they are, draw each leg "
    "and arm along its stick - knee, ankle and foot exactly where the stick and foot shape put them, a standing foot "
    "flat on the green line, a small hand at each arm's end. Far limbs behind, a little darker. Same proportions, skin, "
    "shorts, outline and shading as image 2. Remove every stick, foot shape and the green line. {style} Keep every "
    "figure exactly in its own cell; empty space stays flat #FF00FF. Keep this {w}x{h} image and its grid exactly.")


def _out(base_dir):
    folder = base_dir / "rig"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def stage_base(base_dir, body_name):
    body = BODIES[body_name]
    out = _out(base_dir)
    pose = skeleton(body, STAND["near"], STAND["far"], STAND["arms"])
    guide = draw_guide(body, pose)
    sheet, origins = _sheet([guide], 8, 1, gutter=0)
    sheet.save(out / "base-guide.png")
    drawn = _draw(BASE_PROMPT.format(who=body["prompt"], style=STYLE.format(px=8)), out, out / "base-guide.png", [], "base-drawn")
    base, palette = _clean_native(drawn, out / "base-guide.png", origins[0], 8)
    base, _ = _onto_guide(base, draw_guide(body, pose))
    colors = [list(map(int, c)) for c in palette.tolist()]
    base.save(out / "base.png")
    (out / "base.palette.json").write_text(json.dumps({"colors": colors}))
    (out / "body.json").write_text(json.dumps({"type": body_name, "body": body, "stand": STAND, "pose": pose}, indent=1))
    return {"base": str(out / "base.png"), "follows": follows(base, body, pose)}


def follows(image, body, pose):
    """Share of each limb stick's middle line that lands on body pixels (1.0 = drawn where the skeleton says)."""
    alpha = np.asarray(image)[..., 3] > 0
    result = {}
    for side in ("near", "far"):
        L = pose["legs"][side]
        A = pose["arms"][side]
        for name, (a, b) in {f"{side}_thigh": (pose["hip"], L["knee"]), f"{side}_shin": (L["knee"], L["ankle"]),
                             f"{side}_foot": (L["heel"], L["toe"]), f"{side}_arm": (pose["shoulder"], A["wrist"])}.items():
            pts = [np.array(a) + (np.array(b) - np.array(a))*t for t in np.linspace(0.15, 0.85, 12)]
            hit = [alpha[int(round(p[1])), int(round(p[0]))] if 0 <= p[0] < CANVAS and 0 <= p[1] < CANVAS else False for p in pts]
            result[name] = round(float(np.mean(hit)), 2)
    return result


def stage_frames(base_dir, cycle_name):
    out = _out(base_dir)
    info = json.loads((out / "body.json").read_text())
    body, cycle = info["body"], CYCLES[cycle_name]
    base = Image.open(out / "base.png").convert("RGBA")
    palette = pixel_grid.read_palette(out / "base.palette.json")
    upper = np.asarray(base).copy()
    upper[int(body["hip"][1]) + 3:] = 0  # head, torso and shorts; the limbs come from the sticks
    upper_img = Image.fromarray(upper)
    n = len(cycle["legs"])
    poses = []
    for t in range(n):
        near = cycle["legs"][t]
        far = cycle["far"][t] if "far" in cycle else cycle["legs"][(t + n//2) % n]
        if "arm" in cycle:
            arms = {"near": cycle["arm"], "far": (-cycle["arm"][0], cycle["arm"][1])}
        else:
            arms = {"near": (cycle["arm_swing"]*far[0], cycle["elbow"]), "far": (cycle["arm_swing"]*near[0], cycle["elbow"])}
        poses.append(skeleton(body, near, far, arms, cycle["lift"][t]))
    folder = out / cycle_name
    folder.mkdir(exist_ok=True)
    sheet, origins = _sheet([draw_guide(body, p, upper_img) for p in poses], 4, 3)
    sheet.save(folder / "guide.png")
    drawn = _draw(FRAMES_PROMPT.format(verb=cycle["verb"], style=STYLE.format(px=4), w=sheet.width, h=sheet.height),
                  folder, folder / "guide.png", [out / "base.png"], "drawn")
    guides = [draw_guide(body, p, upper_img) for p in poses]
    frames = [_onto_guide(_clean_native(drawn, folder / "guide.png", o, 4, palette=palette)[0], g)[0]
              for o, g in zip(origins, guides)]
    report = []
    for t, (frame, pose) in enumerate(zip(frames, poses)):
        frame.save(folder / f"frame-{t}.png")
        report.append(follows(frame, body, pose))
    (folder / "skeleton.json").write_text(json.dumps({"cycle": cycle_name, "poses": poses}, indent=1))
    return {"frames": n, "follows": report}


# --- shoes from the skeleton's feet --------------------------------------------------------------------
FEET = (32, 84, 96, 116)
BASE_SHOE_PROMPT = (
    "Image 1 is a pixel-art close-up of one chibi character's lower legs and bare feet, standing, pure side view facing "
    "right, on a flat pure magenta #FF00FF background. Draw ONLY the shoes those feet wear: {desc}. Each shoe exactly "
    "where its foot is, the sole on the sole, covering every bit of bare foot, and only a little bigger than the foot: "
    "the shoe's top ends just above the ankle bone, never up the shin. Draw nothing else: no legs, no shorts, no skin, "
    "no body outline - every other pixel is flat #FF00FF. {style} Keep the image size.")
CLEAN_PROMPT = (
    "Image 1 is a pixel-art sheet: each grid cell shows a chibi character {verb}, pure side view facing right, on a flat "
    "pure magenta #FF00FF background, wearing rough {desc} already placed exactly where they go, one on each foot. "
    "Image 2 is one clean shoe of the design. In every cell redraw ONLY those shoes as clean, crisp pixel art in exactly "
    "the same place, size and angle: mend broken or doubled outlines and stray pixels, keep the sole along the foot's "
    "sole. One shoe per foot - never add or move one. Draw nothing else: no body, no legs, no skin - every other pixel, "
    "up to the cell edges, is flat #FF00FF. {style} Keep this {w}x{h} image and its grid exactly.")


def _similar(image, src_heel, src_toe, dst_heel, dst_toe):
    src = np.asarray(image)
    a, b = np.array(src_heel, float), np.array(src_toe, float)
    c, d = np.array(dst_heel, float), np.array(dst_toe, float)
    vs, vd = b - a, d - c
    scale = np.linalg.norm(vd) / max(np.linalg.norm(vs), 1e-6)
    phi = np.arctan2(vd[1], vd[0]) - np.arctan2(vs[1], vs[0])
    inv = np.linalg.inv(np.array([[np.cos(phi), -np.sin(phi)], [np.sin(phi), np.cos(phi)]])*scale)
    oy, ox = np.mgrid[0:CANVAS, 0:CANVAS]
    pts = np.stack([ox - c[0], oy - c[1]], -1).reshape(-1, 2) @ inv.T + a
    sx, sy = np.round(pts[:, 0]).astype(int), np.round(pts[:, 1]).astype(int)
    ok = (sx >= 0) & (sy >= 0) & (sx < src.shape[1]) & (sy < src.shape[0])
    out = np.zeros((CANVAS*CANVAS, 4), dtype=np.uint8)
    out[ok] = src[sy[ok], sx[ok]]
    return out.reshape(CANVAS, CANVAS, 4)


def _near_leg(pose, body):
    mask = Image.new("L", (CANVAS, CANVAS))
    L = pose["legs"]["near"]
    ImageDraw.Draw(mask).line([tuple(pose["hip"]), tuple(L["knee"]), tuple(L["ankle"])], fill=255, width=body["width"]["thigh"] + 2)
    return np.asarray(mask) > 0


def _components(mask, minimum=1):
    from sprite_gen.frames.parts_segments import _components as components
    return sorted((c for c in components(mask) if c.sum() >= minimum), key=lambda c: -int(c.sum()))


def stage_shoes(base_dir, cycle_name, design):
    out = _out(base_dir)
    info = json.loads((out / "body.json").read_text())
    body, stand = info["body"], info["pose"]
    folder = out / cycle_name
    poses = json.loads((folder / "skeleton.json").read_text())["poses"]
    work = folder / design
    work.mkdir(exist_ok=True)
    shoe_dir = out / "shoes" / design
    shoe_dir.mkdir(parents=True, exist_ok=True)
    if not (shoe_dir / "base-shoes.png").exists():  # the design on the base: once, for every cycle
        base = Image.open(out / "base.png").convert("RGBA")
        sheet, origins = _sheet([base], 8, 1, crop=FEET, gutter=0)
        sheet.save(shoe_dir / "base-sheet.png")
        drawn = _draw(BASE_SHOE_PROMPT.format(desc=DESIGNS[design], style=STYLE.format(px=8)), shoe_dir,
                      shoe_dir / "base-sheet.png", [], "base-drawn")
        _cells(drawn, shoe_dir / "base-sheet.png", origins, 8, crop=FEET)[0].save(shoe_dir / "base-shoes.png")
    pair = np.asarray(Image.open(shoe_dir / "base-shoes.png").convert("RGBA"))
    # each standing foot's own shoe: the piece over that foot's heel->toe
    singles = {}
    for side in ("near", "far"):
        L = stand["legs"][side]
        mid = ((np.array(L["heel"]) + np.array(L["toe"]))/2).round().astype(int)
        pieces = _components(pair[..., 3] > 0, 6)
        best = min(pieces, key=lambda c: np.min(np.hypot(*(np.stack(np.nonzero(c)[::-1], 1) - mid).T))) if pieces else None
        single = np.where(best[..., None], pair, 0).astype(np.uint8) if best is not None else pair
        singles[side] = (Image.fromarray(single), L)
    cells, wants = [], []
    for t, pose in enumerate(poses):
        frame = Image.open(folder / f"frame-{t}.png").convert("RGBA")
        layers, want = [], []
        for side in ("far", "near"):
            image, L = singles[side]
            dst = pose["legs"][side]
            moved = _similar(image, L["heel"], L["toe"], dst["heel"], dst["toe"])
            layers.append(moved)
            want.append(moved[..., 3] > 0)
        rough = layers[0].copy()
        rough[_near_leg(pose, body)] = 0
        rough = np.where(layers[1][..., 3:4] > 0, layers[1], rough)
        cell = frame.copy()
        cell.alpha_composite(Image.fromarray(rough))
        cells.append(cell)
        wants.append(want)
    sheet, origins = _sheet(cells, 4, 3)
    sheet.save(work / "rough-sheet.png")
    drawn = _draw(CLEAN_PROMPT.format(verb=CYCLES[cycle_name]["verb"], desc=DESIGNS[design], style=STYLE.format(px=4),
                                      w=sheet.width, h=sheet.height), work, work / "rough-sheet.png",
                  [shoe_dir / "base-shoes.png"], "clean")
    cleaned = _cells(drawn, work / "rough-sheet.png", origins, 4)
    report = []
    for t, (pose, image, want) in enumerate(zip(poses, cleaned, wants)):
        rough = want[0] | want[1]
        alpha = np.asarray(image)[..., 3] > 0
        shift = max(((dx, dy) for dy in range(-6, 7) for dx in range(-6, 7)),
                    key=lambda d: (int((np.roll(np.roll(alpha, d[1], 0), d[0], 1) & rough).sum()), -abs(d[0]) - abs(d[1])))
        pixels = np.roll(np.roll(np.asarray(image), shift[1], 0), shift[0], 1).copy()
        alpha = pixels[..., 3] > 0
        keep, kept = np.zeros_like(alpha), []
        for side, mask in zip(("far", "near"), want):
            pieces = [c for c in _components(alpha & ~keep, 4)]
            best = max(pieces, key=lambda c: (c & mask).sum(), default=None)
            if best is not None and (best & mask).sum():
                keep |= best
                kept.append({"side": side, "on_rig": round(float((best & mask).sum() / max(1, mask.sum())), 2)})
        pixels[~keep] = 0
        if kept and kept[0]["side"] == "far":
            pixels[keep & want[0] & ~want[1] & _near_leg(pose, body)] = 0  # behind the near leg
        Image.fromarray(pixels).save(work / f"shoes-{t}.png")
        report.append({"frame": t, "shift": list(shift), "shoes": kept})
    (work / "report.json").write_text(json.dumps(report, indent=1))
    return report


def stage_show(base_dir):
    out = _out(base_dir)
    crop, scale = (24, 8, 104, 122), 4
    w, h = (crop[2]-crop[0])*scale, (crop[3]-crop[1])*scale
    results = {}
    for cycle_name, cycle in CYCLES.items():
        folder = out / cycle_name
        if not (folder / "frame-0.png").exists():
            continue
        looks = ["bare"] + [d for d in DESIGNS if (folder / d / "shoes-0.png").exists()]
        n = len(cycle["legs"])
        images = []
        for t in range(n):
            sheet = Image.new("RGBA", (w*len(looks), h), (236, 236, 236, 255))
            for k, look in enumerate(looks):
                frame = Image.open(folder / f"frame-{t}.png").convert("RGBA")
                if look != "bare":
                    frame.alpha_composite(Image.open(folder / look / f"shoes-{t}.png").convert("RGBA"))
                sheet.alpha_composite(frame.crop(crop).resize((w, h), Image.Resampling.NEAREST), (k*w, 0))
            images.append(sheet)
        images[0].save(out / f"{cycle_name}.gif", save_all=True, append_images=images[1:], duration=cycle["ms"], loop=0)
        strip = Image.new("RGBA", (w*len(looks)*min(n, 3), h*(-(-n//3))), (236, 236, 236, 255))
        for t, image in enumerate(images):
            strip.alpha_composite(image, ((t % 3)*w*len(looks), (t//3)*h))
        strip.save(out / f"{cycle_name}.png")
        results[cycle_name] = looks
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", required=True, type=Path, help="the character's folder")
    parser.add_argument("stage", choices=("base", "frames", "shoes", "show"))
    parser.add_argument("--body", default="slim", choices=tuple(BODIES))
    parser.add_argument("--cycle", default="walk", choices=tuple(CYCLES))
    parser.add_argument("--design", default="sneakers", choices=tuple(DESIGNS))
    args = parser.parse_args()
    if args.stage == "base":
        result = stage_base(args.base, args.body)
    elif args.stage == "frames":
        result = stage_frames(args.base, args.cycle)
    elif args.stage == "shoes":
        result = stage_shoes(args.base, args.cycle, args.design)
    else:
        result = stage_show(args.base)
    print(json.dumps(result, indent=1, default=str))
