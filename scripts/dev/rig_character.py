# SPDX-License-Identifier: Apache-2.0
"""A character built from its skeleton: every joint and anchor is a number, never detected.

A body type is a set of proportions. Its standing skeleton is drawn as a guide and the image model
draws the base mannequin on it; few-frame cycles (idle, walk, run) are posed from the same numbers
and drawn the same way. Parts take their anchors (sole, toe direction, ankle) straight from the
skeleton: a design is drawn once by the model, then placed on every frame's feet - no detection,
no per-frame redraw. What shows is the runtime's layer order: the body is cut into pieces by bone.

    base      the standing skeleton -> guide -> the base mannequin (base.png, palette, body.json)
    calibrate the template's foot (heel, toe, sole) measured on the base drawing; cycles re-posed
    frames    a cycle (idle 3, walk 6, run 6) -> guide sheet -> drawn frames
    puppet    the base cut along its skeleton, posed per frame, mended by the model (feet exact)
    shoes     a shoe design (one model call) placed heel->toe on every frame of a cycle
    library   the cycles as a frozen motion with the skeleton's sockets
    segments  the body cut into far arm/leg, root, near leg/arm by bone, as the body pack
    packs     each shoe design as a part (far and near piece)
    play      every look through the runtime, one GIF per state
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


def _clean_native(drawn_path, sheet_path, origin, scale, crop=CROP, colors=None, palette=None, expect=None):
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
    guess = scale*ratio
    if expect is not None:
        # The figure's own height against the height it must have: the true block size, even when the
        # model stretched its output a little differently across and down.
        guess = (bbox[3] - bbox[1]) / max(expect[1], 1)
    pitch = pixel_grid.find_pitch([raw], guess, spread=0.03 if expect is not None else 0.06, steps=25)
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


def cycle_poses(body, cycle):
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
    return poses


def stage_calibrate(base_dir):
    """The template's foot measured on the base drawing: the model draws the foot longer and lower than
    the numbers asked, so heel, toe and sole become the drawn foot's own (rearmost, frontmost, lowest
    pixel, in the foot's frame); the ground moves with the sole so the base keeps its hip; every cycle's
    skeleton is posed again. Re-run the puppet after this."""
    out = _out(base_dir)
    info = json.loads((out / "body.json").read_text())
    body = info.get("template", info["body"])  # always from the numbers the base was drawn on
    stand = skeleton(body, STAND["near"], STAND["far"], STAND["arms"])
    base = np.asarray(Image.open(out / "base.png").convert("RGBA"))
    L = stand["legs"]["near"]
    ys, xs = np.nonzero(_foot_mask(_owners(base, body, stand), stand, "near"))
    local = np.array([_turn(p, -L["foot_turn"]) for p in np.stack([xs, ys], 1) - np.array(L["ankle"])])
    sole = float(local[:, 1].max())
    calibrated = dict(body, heel=[round(float(local[:, 0].min()), 2), round(sole, 2)],
                      toe=[round(float(local[:, 0].max()), 2), round(sole, 2)])
    probe = skeleton(calibrated, STAND["near"], STAND["far"], STAND["arms"])
    calibrated["ground"] = int(round(body["ground"] - (probe["hip"][1] - stand["hip"][1])))
    pose = skeleton(calibrated, STAND["near"], STAND["far"], STAND["arms"])
    (out / "body.json").write_text(json.dumps(dict(info, body=calibrated, pose=pose, template=body), indent=1))
    for cycle_name, cycle in CYCLES.items():
        path = out / cycle_name / "skeleton.json"
        if path.exists():
            path.write_text(json.dumps({"cycle": cycle_name, "poses": cycle_poses(calibrated, cycle)}, indent=1))
    return {"was": {k: body[k] for k in ("heel", "toe", "ground")},
            "now": {k: calibrated[k] for k in ("heel", "toe", "ground")}, "hip": [stand["hip"], pose["hip"]]}


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
    poses = cycle_poses(body, cycle)
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


# --- the puppet: the base cut along its own skeleton and posed bone by bone ------------------------------
PUPPET_PROMPT = (
    "Image 1 is a pixel-art sheet: each grid cell shows the same chibi character {verb}, pure side view facing right, "
    "on a flat pure magenta #FF00FF background, assembled from cut-out body parts already in exactly the right pose. "
    "Image 2 is the character standing, drawn whole.\n\n"
    "In every cell redraw the character as one clean drawing in exactly that pose: keep every limb, hand and foot "
    "exactly where and as big as it is, mend the seams and gaps at the shoulders, elbows, hips and knees, close the "
    "outline, and let the far limbs read as behind (a little darker). Do not move, turn, lengthen or shorten anything. "
    "{style} Keep every figure exactly in its own cell; empty space stays flat #FF00FF. Keep this {w}x{h} image and "
    "its grid exactly.")


def _bones(pose):
    """Name -> (from, to, layer) of every rigid piece of a pose; feet go ankle -> toe."""
    bones = {}
    for side, layer in (("far", 0), ("near", 3)):
        L, A = pose["legs"][side], pose["arms"][side]
        bones[f"{side}_thigh"] = (pose["hip"], L["knee"], layer + (0.2 if side == "far" else 0.5))
        bones[f"{side}_shin"] = (L["knee"], L["ankle"], layer + 0.1)
        bones[f"{side}_foot"] = (L["ankle"], L["toe"], layer + 0.15)
        bones[f"{side}_upper_arm"] = (pose["shoulder"], A["elbow"], layer + (-0.5 if side == "far" else 1.0))
        bones[f"{side}_forearm"] = (A["elbow"], A["wrist"], layer + (-0.4 if side == "far" else 1.1))
    return bones


def _seg_distance(px, py, a, b):
    a, b = np.array(a, float), np.array(b, float)
    d = b - a
    t = np.clip(((px - a[0])*d[0] + (py - a[1])*d[1]) / max(float(d @ d), 1e-6), 0, 1)
    return np.hypot(px - (a[0] + t*d[0]), py - (a[1] + t*d[1]))


def _owners(rgba, body, stand):
    """Each pixel's bone name ("" where empty): the upper body (head, torso, shorts) above the hip line,
    else the nearest bone - the near limbs where they cover the far ones."""
    alpha = rgba[..., 3] > 0
    ys, xs = np.mgrid[0:CANVAS, 0:CANVAS]
    bones = _bones(stand)
    dist = {name: _seg_distance(xs, ys, a, b) for name, (a, b, _) in bones.items()}
    for side in ("far", "near"):  # a foot is its heel-ankle-toe triangle, not only the ankle->toe line
        L = stand["legs"][side]
        dist[f"{side}_foot"] = np.minimum(dist[f"{side}_foot"], np.minimum(_seg_distance(xs, ys, L["ankle"], L["heel"]),
                                                                           _seg_distance(xs, ys, L["heel"], L["toe"])))
    widths = {"thigh": body["width"]["thigh"], "shin": body["width"]["shin"], "foot": 4,
              "upper_arm": body["width"]["arm"], "forearm": body["width"]["arm"] + 1}
    owner = np.full(alpha.shape, "", dtype=object)
    cx, cy = stand["head"]["center"]
    head = np.hypot(xs - cx, ys - cy) <= stand["head"]["radius"] + 2
    near_limbs = [n for n in bones if n.startswith("near")]
    far_limbs = [n for n in bones if n.startswith("far")]
    for y, x in zip(*np.nonzero(alpha)):
        reach = {n: dist[n][y, x] - widths[n.split("_", 1)[1]]/2 for n in bones}
        near_best = min(near_limbs, key=reach.get)
        far_best = min(far_limbs, key=reach.get)
        if reach[near_best] <= 1.5:
            owner[y, x] = near_best
        elif head[y, x] or y < stand["hip"][1] + 4 and reach[far_best] > 1.0:
            owner[y, x] = "upper"
        else:
            owner[y, x] = far_best if reach[far_best] <= reach[near_best] + 2 else near_best
    return owner


def cut_puppet(base, body, stand):
    """The base cut into rigid pieces, one per bone plus the upper body."""
    rgba = np.asarray(base)
    owner = _owners(rgba, body, stand)
    pieces = {}
    for name in list(_bones(stand)) + ["upper"]:
        mask = owner == name
        pieces[name] = np.where(mask[..., None], rgba, 0).astype(np.uint8)
    return pieces


def _rigid(pixels, a, b, a2, b2):
    """Pixels turned about `a` so a->b points along a2->b2, then moved to a2 (no stretching)."""
    a, b, a2, b2 = (np.array(v, float) for v in (a, b, a2, b2))
    phi = np.arctan2(*(b2 - a2)[::-1]) - np.arctan2(*(b - a)[::-1])
    inv = np.array([[np.cos(-phi), -np.sin(-phi)], [np.sin(-phi), np.cos(-phi)]])
    oy, ox = np.mgrid[0:CANVAS, 0:CANVAS]
    pts = np.stack([ox - a2[0], oy - a2[1]], -1).reshape(-1, 2) @ inv.T + a
    sx, sy = np.round(pts[:, 0]).astype(int), np.round(pts[:, 1]).astype(int)
    ok = (sx >= 0) & (sy >= 0) & (sx < CANVAS) & (sy < CANVAS)
    out = np.zeros((CANVAS*CANVAS, 4), dtype=np.uint8)
    out[ok] = pixels[sy[ok], sx[ok]]
    return out.reshape(CANVAS, CANVAS, 4)


def pose_puppet(pieces, stand, pose):
    """The pieces moved onto a pose, back to front."""
    bones, target = _bones(stand), _bones(pose)
    layers = [(bones[n][2], _rigid(pieces[n], bones[n][0], bones[n][1], target[n][0], target[n][1])) for n in bones]
    upper = np.roll(pieces["upper"], int(round(pose["shift"])), 0)
    layers.append((1.5, upper))
    canvas = np.zeros((CANVAS, CANVAS, 4), dtype=np.uint8)
    for _, layer in sorted(layers, key=lambda l: l[0]):
        canvas = np.where(layer[..., 3:4] > 0, layer, canvas)
    return Image.fromarray(canvas)


def stage_puppet(base_dir, cycle_name):
    """Frames from the cut base posed bone by bone, then mended by the model: feet are where the skeleton
    says, by construction."""
    out = _out(base_dir)
    info = json.loads((out / "body.json").read_text())
    body, stand = info["body"], info["pose"]
    base = Image.open(out / "base.png").convert("RGBA")
    palette = pixel_grid.read_palette(out / "base.palette.json")
    folder = out / cycle_name
    poses = json.loads((folder / "skeleton.json").read_text())["poses"]
    pieces = cut_puppet(base, body, stand)
    work = folder / "puppet"
    work.mkdir(exist_ok=True)
    roughs = [pose_puppet(pieces, stand, p) for p in poses]
    for t, r in enumerate(roughs):
        r.save(work / f"rough-{t}.png")
    sheet, origins = _sheet(roughs, 4, 3)
    sheet.save(work / "sheet.png")
    drawn = _draw(PUPPET_PROMPT.format(verb=CYCLES[cycle_name]["verb"], style=STYLE.format(px=4), w=sheet.width,
                                       h=sheet.height), work, work / "sheet.png", [out / "base.png"], "mended")
    report = []
    for t, (o, rough) in enumerate(zip(origins, roughs)):
        box = rough.getbbox()
        mended, _ = _clean_native(drawn, work / "sheet.png", o, 4, palette=palette, expect=(box[2]-box[0], box[3]-box[1]))
        mended, shift = _onto_guide(mended, rough)
        mended.save(work / f"mended-{t}.png")
        mended.save(folder / f"frame-{t}.png")  # until the feet stage puts the skeleton's feet on it
        report.append({"frame": t, "shift": list(shift)})
    return report


# --- shoes: designed once by the model, then placed on every foot by the skeleton -------------------------
BASE_SHOE_PROMPT = (
    "Image 1 is a pixel-art close-up of one chibi character's lower legs and bare feet, mid-stride, pure side view facing "
    "right, on a flat pure magenta #FF00FF background. Draw ONLY the two shoes those feet wear, one on each foot: "
    "{desc}. Each shoe exactly where its foot is, at its angle, the sole on the sole, covering every bit of bare foot, "
    "and only a little bigger than the foot: "
    "the shoe's top ends just above the ankle bone, never up the shin. Draw nothing else: no legs, no shorts, no skin, "
    "no body outline - every other pixel is flat #FF00FF. {style} Keep the image size.")


def _scale2x(rgba):
    """Scale2x (EPX): twice the size, diagonal edges kept sharp instead of blocky."""
    p = np.ascontiguousarray(rgba).view(np.uint32)[..., 0]
    q = np.pad(p, 1, mode="edge")
    b, d, f, h = q[:-2, 1:-1], q[1:-1, :-2], q[1:-1, 2:], q[2:, 1:-1]
    out = np.empty((p.shape[0]*2, p.shape[1]*2), np.uint32)
    out[0::2, 0::2] = np.where((d == b) & (b != f) & (d != h), d, p)
    out[0::2, 1::2] = np.where((b == f) & (b != d) & (f != h), f, p)
    out[1::2, 0::2] = np.where((d == h) & (d != b) & (h != f), d, p)
    out[1::2, 1::2] = np.where((h == f) & (d != h) & (b != f), f, p)
    return out[..., None].view(np.uint8).reshape(out.shape[0], out.shape[1], 4)


def _place(image, src_heel, src_toe, dst_heel, dst_toe):
    """A drawing moved so its heel->toe lies on another heel->toe (turned and scaled, RotSprite style:
    sampled from an 8x Scale2x copy, so turned outlines stay one clean pixel)."""
    up = np.asarray(image.convert("RGBA"))
    for _ in range(3):
        up = _scale2x(up)
    a, b = np.array(src_heel, float), np.array(src_toe, float)
    c, d = np.array(dst_heel, float), np.array(dst_toe, float)
    vs, vd = b - a, d - c
    scale = np.linalg.norm(vd) / max(np.linalg.norm(vs), 1e-6)
    phi = np.arctan2(vd[1], vd[0]) - np.arctan2(vs[1], vs[0])
    inv = np.linalg.inv(np.array([[np.cos(phi), -np.sin(phi)], [np.sin(phi), np.cos(phi)]])*scale)
    oy, ox = np.mgrid[0:CANVAS, 0:CANVAS]
    pts = (np.stack([ox - c[0], oy - c[1]], -1).reshape(-1, 2) @ inv.T + a + 0.5)*8
    sx, sy = np.floor(pts[:, 0]).astype(int), np.floor(pts[:, 1]).astype(int)
    ok = (sx >= 0) & (sy >= 0) & (sx < up.shape[1]) & (sy < up.shape[0])
    out = np.zeros((CANVAS*CANVAS, 4), dtype=np.uint8)
    out[ok] = up[sy[ok], sx[ok]]
    return out.reshape(CANVAS, CANVAS, 4)


def _components(mask, minimum=1):
    from sprite_gen.frames.parts_segments import _components as components
    return sorted((c for c in components(mask) if c.sum() >= minimum), key=lambda c: -int(c.sum()))


def _shoe_pose(out):
    """The frame the shoe is designed on: feet well apart (no overlap to split), the near foot nearest flat."""
    stand = json.loads((out / "body.json").read_text())["pose"]["legs"]["near"]
    def angle(L):
        return np.degrees(np.arctan2(L["toe"][1] - L["heel"][1], L["toe"][0] - L["heel"][0]))
    best = None
    for cycle_name in CYCLES:
        path = out / cycle_name / "skeleton.json"
        if not path.exists():
            continue
        for t, pose in enumerate(json.loads(path.read_text())["poses"]):
            near, far = pose["legs"]["near"], pose["legs"]["far"]
            apart = min(np.hypot(*(np.array(near[a]) - far[b])) for a in ("ankle", "heel", "toe") for b in ("ankle", "heel", "toe"))
            if apart < 12:
                continue
            score = abs(angle(near) - angle(stand))
            if best is None or score < best[0]:
                best = (score, cycle_name, t, pose)
    return best[1:]


def design_shoe(out, design):
    """The one model call per design: the shoes drawn on a frame whose feet stand apart (standing feet
    overlap, and a pair drawn on them can't be split), reduced, and the near foot's shoe kept with that
    foot's heel and toe."""
    info = json.loads((out / "body.json").read_text())
    shoe_dir = out / "shoes" / design
    if (shoe_dir / "base-shoe.png").exists():
        return shoe_dir
    shoe_dir.mkdir(parents=True, exist_ok=True)
    cycle_name, t, pose = _shoe_pose(out)
    frame = Image.open(out / cycle_name / f"frame-{t}.png").convert("RGBA")
    feet = [np.array(pose["legs"][s][k]) for s in ("near", "far") for k in ("ankle", "heel", "toe")]
    x0, x1 = min(p[0] for p in feet) - 10, max(p[0] for p in feet) + 10
    y0 = min(p[1] for p in feet) - 12
    crop = tuple(int(v) for v in (x0, y0, x1, max(y0 + 24, info["body"]["ground"] + 4)))
    sheet, origins = _sheet([frame], 8, 1, crop=crop, gutter=0)
    sheet.save(shoe_dir / "base-sheet.png")
    drawn = _draw(BASE_SHOE_PROMPT.format(desc=DESIGNS[design], style=STYLE.format(px=8)), shoe_dir,
                  shoe_dir / "base-sheet.png", [], "base-drawn")
    pair, palette = _clean_native(drawn, shoe_dir / "base-sheet.png", origins[0], 8, crop=crop, colors=16)
    pair = np.asarray(pair)
    L = pose["legs"]["near"]
    ys, xs = np.mgrid[0:CANVAS, 0:CANVAS]
    on_foot = _seg_distance(xs, ys, L["heel"], L["toe"]) <= 3
    near = max(_components(pair[..., 3] > 0, 6), key=lambda c: (c & on_foot).sum())
    Image.fromarray(np.where(near[..., None], pair, 0).astype(np.uint8)).save(shoe_dir / "base-shoe.png")
    (shoe_dir / "base-shoe.json").write_text(json.dumps({"cycle": cycle_name, "frame": t, "heel": L["heel"], "toe": L["toe"],
                                                         "palette": palette.tolist()}))
    return shoe_dir


def _planted(L, ground, tilt=15):
    """A foot's heel and toe for its shoe: a foot touching the ground at a slight angle stands flat on it
    (heel and toe on the ground line, same length); a heel strike or toe-off keeps its angle."""
    heel, toe = np.array(L["heel"], float), np.array(L["toe"], float)
    v = toe - heel
    if max(heel[1], toe[1]) < ground - 1 or abs(np.degrees(np.arctan2(v[1], abs(v[0])))) > tilt:
        return heel.tolist(), toe.tolist()
    length = np.hypot(*v)
    pivot = heel if heel[1] >= toe[1] else toe  # the end that touches
    heel = np.array([pivot[0] if pivot is heel else pivot[0] - np.sign(v[0])*length, ground])
    return heel.tolist(), [heel[0] + np.sign(v[0])*length, ground]


def _grounded(placed, ground):
    """A placed foot or shoe lifted out of the ground: a turned heel's corner may dip below it."""
    rows = np.nonzero((placed[..., 3] > 0).any(1))[0]
    sink = int(rows.max()) - ground if len(rows) else 0
    return np.roll(placed, -sink, 0) if sink > 0 else placed


def _sole(image, heel, toe):
    """A drawing's own sole: its heel->toe line moved along the foot's normal onto its lowest pixels, so
    a placed foot or shoe stands on the skeleton's sole line (the drawn sole is rarely the template's)."""
    heel, toe = np.array(heel, float), np.array(toe, float)
    u = (toe - heel) / max(np.hypot(*(toe - heel)), 1e-6)
    n = np.array([-u[1], u[0]])  # down, for a foot pointing right
    ys, xs = np.nonzero(np.asarray(image)[..., 3] > 0)
    depth = float(((np.stack([xs, ys], 1) + 0.5 - heel) @ n).max()) - 0.5
    return (heel + depth*n).tolist(), (toe + depth*n).tolist()


def _foot_mask(owner, pose, side):
    """A leg's foot: its pixels past the plane through the ankle across the shin (heel and its outline
    included, unlike the nearest-bone split)."""
    L = pose["legs"][side]
    knee, ankle = np.array(L["knee"], float), np.array(L["ankle"], float)
    u = (ankle - knee) / max(np.hypot(*(ankle - knee)), 1e-6)
    ys, xs = np.mgrid[0:CANVAS, 0:CANVAS]
    past = (xs + 0.5 - ankle[0])*u[0] + (ys + 0.5 - ankle[1])*u[1] >= -1
    return np.isin(owner, [f"{side}_shin", f"{side}_foot"]) & past


def _far_shade(out):
    """How much darker the far limbs are drawn: far shin over near shin brightness on the base."""
    info = json.loads((out / "body.json").read_text())
    base = np.asarray(Image.open(out / "base.png").convert("RGBA"))
    owner = _owners(base, info["body"], info["pose"])
    luma = base[..., :3].astype(float) @ np.array([0.299, 0.587, 0.114])
    return float(luma[owner == "far_shin"].mean() / luma[owner == "near_shin"].mean())


def _shaded(pixels, factor):
    out = np.asarray(pixels).copy()
    out[..., :3] = np.clip(out[..., :3]*factor, 0, 255).astype(np.uint8)
    return out


def stage_shoes(base_dir, cycle_name, design):
    """Every frame's two shoes, no model: the designed shoe moved heel->toe onto each foot of the
    skeleton (the far one shaded as the far limbs are). Which parts show is the runtime's layer order
    (far leg < far shoe < body < near leg < near shoe), not a mask baked in here."""
    from sprite_gen.frames import pixel_grid as grid
    out = _out(base_dir)
    shoe_dir = design_shoe(out, design)
    shoe = Image.open(shoe_dir / "base-shoe.png").convert("RGBA")
    meta = json.loads((shoe_dir / "base-shoe.json").read_text())
    dark = min(meta["palette"], key=lambda c: 299*c[0] + 587*c[1] + 114*c[2])
    src = _sole(shoe, meta["heel"], meta["toe"])
    shade = _far_shade(out)
    ground = json.loads((out / "body.json").read_text())["body"]["ground"]
    folder = out / cycle_name
    work = folder / design
    work.mkdir(exist_ok=True)
    report = []
    for t, pose in enumerate(json.loads((folder / "skeleton.json").read_text())["poses"]):
        both = Image.new("RGBA", (CANVAS, CANVAS))
        for side in ("far", "near"):
            heel, toe = _planted(pose["legs"][side], ground)
            placed = _grounded(_place(shoe, *src, heel, toe), ground)
            placed = np.asarray(grid.silhouette_outline(Image.fromarray(placed), [int(v) for v in dark])).copy()
            if side == "far":
                placed = _shaded(placed, shade)
            image = Image.fromarray(placed)
            image.save(work / f"shoes-{t}-{side}.png")
            both.alpha_composite(image)
        both.save(work / f"shoes-{t}.png")
        report.append({"frame": t, "feet": {s: {"heel": pose["legs"][s]["heel"], "toe": pose["legs"][s]["toe"]}
                                             for s in ("far", "near")}})
    (work / "report.json").write_text(json.dumps(report, indent=1))
    return len(report)


# --- body segments: which piece each pixel is, from the skeleton -------------------------------------------
# Body pieces back to front, each a label color; the feet are pieces of their own so footwear can
# replace them (a shoe hides the whole bare foot, toes included, whatever its outline).
SEGMENTS = {"arm_back": {"color": "#00a000", "provides": ["hand_back"]}, "leg_back": "#00ffff", "foot_back": "#0000ff",
            "root": "#ffff00", "leg_front": "#ff8000", "foot_front": "#ff0000",
            "arm_front": {"color": "#ffffff", "provides": ["hand_front"]}}
SEGMENT_OF = {"upper": "root"}  # bone -> body piece node
for _side, _node in (("far", "back"), ("near", "front")):
    SEGMENT_OF |= {f"{_side}_thigh": f"leg_{_node}", f"{_side}_shin": f"leg_{_node}", f"{_side}_foot": f"foot_{_node}",
                   f"{_side}_upper_arm": f"arm_{_node}", f"{_side}_forearm": f"arm_{_node}"}


def _moved(value, dx, dy):
    """A pose with every point moved (scalars such as the head radius kept)."""
    if isinstance(value, dict):
        return {k: _moved(v, dx, dy) for k, v in value.items()}
    if isinstance(value, list) and len(value) == 2 and all(isinstance(v, (int, float)) for v in value):
        return [value[0] + dx, value[1] + dy]
    return value


def stage_segments(base_dir, prefix):
    """The body as pieces (far arm, far leg and foot, root, near leg and foot, near arm) for every sample, each pixel given
    to its skeleton bone - so the runtime's layer order shows and hides parts by itself."""
    from sprite_gen.compose import compose_parts
    from sprite_gen.frames import parts_import, parts_segments
    from sprite_gen.spec import parts_workspace as workspace
    out = _out(base_dir)
    assembly = (out / "library/assembly.json").absolute()
    index, registry = workspace.read_workspace(assembly)
    motion = registry[index["motion"]["id"]]
    mapping = json.loads((out / "library-map.json").read_text())
    body = json.loads((out / "body.json").read_text())["body"]
    load = compose_parts.AssetLoader(assembly.parent, index)
    folder = out / "segments"
    labels = folder / "labels"
    labels.mkdir(parents=True, exist_ok=True)
    colors = folder / "colors.json"
    colors.write_text(json.dumps(SEGMENTS))
    rgb = {n: tuple(int((v if isinstance(v, str) else v["color"])[i:i+2], 16) for i in (1, 3, 5)) for n, v in SEGMENTS.items()}
    for clip in motion["clips"]:
        state = clip["state"]
        poses = json.loads((out / state / "skeleton.json").read_text())["poses"]
        for k, sample in enumerate(clip["samples"]):
            pose = _moved(poses[mapping["starts"][state][k] % len(poses)], *mapping["shifts"][state])
            pixels = np.asarray(load(motion, sample["image"]).convert("RGBA"))
            owner = _owners(pixels, body, pose)
            paint = np.zeros_like(pixels)
            for bone, node in SEGMENT_OF.items():
                paint[owner == bone] = (*rgb[node], 255)
            Image.fromarray(paint).save(labels / f"{sample['pose_id']}.png")
    result = parts_segments.segment_body(assembly, folder / "body", colors=colors, labels=labels,
                                         body_id=f"{prefix}.body", tolerance=10, key_tolerance=80)
    parts_import.import_pack(assembly, Path(result["spec"]), Path(result["source_root"]))
    return {k: result[k] for k in ("cut", "unprovided_required_nodes")}


# --- the library: frames and their skeleton as a frozen motion --------------------------------------------
SOCKETS = ("root", "head", "torso", "hip", "shoulder", "near_heel", "near_toe", "far_heel", "far_toe")
FPS = {"idle": "4", "walk": "8", "run": "11"}


def _sockets(pose, shift):
    """Every socket of one pose from its skeleton, moved as the motion build moved the clip."""
    dx, dy = shift
    def at(p):
        return [int(round(p[0] + dx)), int(round(p[1] + dy))]
    cx, cy = pose["head"]["center"]
    return {"root": [0, 0], "head": at([cx, cy - pose["head"]["radius"]]),
            "torso": at([(pose["shoulder"][0] + pose["hip"][0])/2, (pose["shoulder"][1] + pose["hip"][1])/2]),
            "hip": at(pose["hip"]), "shoulder": at(pose["shoulder"]),
            "near_heel": at(pose["legs"]["near"]["heel"]), "near_toe": at(pose["legs"]["near"]["toe"]),
            "far_heel": at(pose["legs"]["far"]["heel"]), "far_toe": at(pose["legs"]["far"]["toe"])}


def stage_library(base_dir, prefix):
    """Topology (the SD layers plus the skeleton's anchor sockets), rig and catalog; the cycles imported
    as native frames; every socket written from the skeleton (verified: nothing tracked); pose IDs; frozen."""
    import shutil
    import sd_avatar_process as avatar
    from sprite_gen.curate import parts_motion, parts_poses
    from sprite_gen.frames import parts_loop_native as native
    from sprite_gen.spec import parts_contract as contract, parts_workspace as workspace
    out = _out(base_dir)
    root = out / "library"
    if root.exists():
        raise SystemExit(f"{root} exists")
    root.mkdir(parents=True)
    nodes = [{"id": "root"}] + [{"id": n, "parent": "root", "required": False} for n in avatar.LAYERS if n != "root"]
    seal = avatar.seal_write
    topology = seal(root, "profiles/topology.json", {"kind": "parts-topology", "schema_version": 1,
        "id": f"{prefix}.topology", "nodes": nodes,
        "sockets": [{"id": s, "node": "root", "required": s in ("root", "head", "torso")} for s in SOCKETS],
        "slots": [{"id": slot, "nodes": n, "sockets": s, "optional": True, "empty_policy": "no-piece"}
                  for slot, (_, _, n, s) in avatar.SLOTS.items()], "layers": avatar.LAYERS})
    rig = seal(root, "profiles/rig.json", {"kind": "parts-rig", "schema_version": 1, "id": f"{prefix}.rig",
        "topology": contract.reference(topology), "view": "right", "style": "pixel.v1",
        "canvas": {"width": CANVAS, "height": CANVAS}, "default_body_ids": [f"{prefix}.body"]})
    explorer_index, explorer = workspace.read_workspace(Path("assets/sd-explorer-avatar-20261006/library/assembly.json"))
    groups = explorer[explorer_index["catalog"]["id"]]
    catalog = seal(root, "catalog.json", {"kind": "parts-catalog", "schema_version": 1, "id": f"{prefix}.catalog",
        "rig": contract.reference(rig), "topology": contract.reference(topology), "groups": groups["groups"],
        "slots": [{"id": slot, "group": group, "label": label} for slot, (group, label, _, _) in avatar.SLOTS.items()],
        "palette_channels": groups["palette_channels"]})
    assembly = root / "assembly.json"
    assembly.write_bytes(workspace.json_bytes({"kind": "parts-workspace", "schema_version": 1, "id": f"{prefix}.workspace",
        "documents": {topology["id"]: "profiles/topology.json", rig["id"]: "profiles/rig.json", catalog["id"]: "catalog.json"},
        "rig": contract.reference(rig), "catalog": contract.reference(catalog), "states": list(CYCLES)}))
    loops, skeletons = [], {}
    for name in CYCLES:
        frames = out / name / "loop"
        if frames.exists():
            shutil.rmtree(frames)
        frames.mkdir()
        n = len(json.loads((out / name / "skeleton.json").read_text())["poses"])
        for t in range(n):
            shutil.copy2(out / name / f"frame-{t}.png", frames / f"frame-{t:03d}.png")
        loops.append((name, frames, FPS[name]))
        skeletons[name] = json.loads((out / name / "skeleton.json").read_text())["poses"]
    body = json.loads((out / "body.json").read_text())["body"]
    built = native.build(assembly, out / "native", loops, body_height=body["ground"] - (body["head"]["center"][1] - body["head"]["radius"]),
                         baseline=body["ground"], palette=out / "base.palette.json", motion_id=f"{prefix}.motion",
                         resample="lattice", outline="darkest")
    imported = parts_motion.import_motion(assembly, Path(built["spec"]), Path(built["source_root"]), strict_motion=False)
    candidate = workspace.decode_json((root / imported["candidate"]).read_bytes())
    notes = json.loads((root / imported["annotation_template"]).read_text())
    shifts, starts = {}, {}
    for clip in built["clips"]:
        shifts[clip["state"]] = clip["projection"]["offset"]
        starts[clip["state"]] = [clip["first_source_frame"] + sum(clip["holds"][:k]) for k in range(len(clip["holds"]))]
    for clip in candidate["motion"]["clips"]:
        state = clip["state"]
        poses = skeletons[state]
        for k, sample in enumerate(clip["samples"]):
            pose = poses[starts[state][k] % len(poses)]
            for socket, position in _sockets(pose, shifts[state]).items():
                notes["clips"][clip["id"]][sample["id"]]["sockets"][socket] = {"position": position, "visible": True,
                                                                               "confidence": "verified"}
    path = out / "annotations.json"
    path.write_text(json.dumps(notes, indent=1))
    (out / "library-map.json").write_text(json.dumps({"shifts": shifts, "starts": starts,
        "clips": {c["state"]: c["id"] for c in candidate["motion"]["clips"]}}, indent=1))
    poses = parts_poses.assign(assembly, path)
    frozen = parts_motion.freeze_motion(assembly, imported["candidate"], path)
    return {"motion": frozen["motion"], "poses": poses["poses"], "shifts": shifts,
            "quality": [{k: r.get(k) for k in ("clip", "status", "over")} for r in imported["motion_quality"]]}


def stage_packs(base_dir, prefix):
    """Each shoe design as a part (a near and a far piece,
    one full-canvas drawing per sample, moved as the motion build moved its clip); then an appearance."""
    from sprite_gen.frames import parts_import
    from sprite_gen.spec import parts_assets as assets, parts_workspace as workspace
    out = _out(base_dir)
    assembly = (out / "library/assembly.json").absolute()
    mapping = json.loads((out / "library-map.json").read_text())
    index, registry = workspace.read_workspace(assembly)
    motion = registry[index["motion"]["id"]]
    made = {}
    for design in DESIGNS:
        src = out / "packs" / design
        src.mkdir(parents=True, exist_ok=True)
        pieces = {side: {} for side in ("near", "far")}
        complete = True
        for clip in motion["clips"]:
            state = clip["state"]
            dx, dy = mapping["shifts"][state]
            for k, sample in enumerate(clip["samples"]):
                t = mapping["starts"][state][k]
                for side, node in (("far", "foot_back"), ("near", "foot_front")):
                    file = out / state / design / f"shoes-{t}-{side}.png"
                    if not file.exists():
                        complete = False
                        continue
                    image = np.roll(np.roll(np.asarray(Image.open(file).convert("RGBA")), dy, 0), dx, 1)
                    name = f"{state}-{t}-{side}.png"
                    Image.fromarray(image).save(src / name)
                    ref = {"file": name, "sha256": assets.sha256((src / name).read_bytes())}
                    pieces[side].setdefault(clip["id"], {})[sample["id"]] = {
                        "image": ref, "format": "full-canvas", "pivot": [0, 0], "target_socket": "root",
                        "visible": bool((image[..., 3] > 0).any())}
        if not complete:
            continue
        rig = registry[index["rig"]["id"]]
        part_id = f"{prefix}.shoes.{design}"
        spec = {"kind": "parts-pack-import", "schema_version": 1, "id": part_id, "design_id": f"{prefix}.{design}",
                "role": "part", "body_mode": "segmented", "style": rig["style"], "rig": index["rig"], "motion": index["motion"],
                "view": rig["view"], "binding_id": f"{part_id}.binding", "occupies": ["footwear"],
                "pieces": [{"id": f"shoe_{side}", "slot": "footwear", "node": node, "mode": "raster",
                            "provides": [node], "replaces": [node], "sample_map": pieces[side]} for side, node in (("far", "foot_back"), ("near", "foot_front"))]}
        (src / "import.json").write_text(json.dumps(spec, indent=1))
        parts_import.import_pack(assembly, src / "import.json", src)
        made[design] = part_id
    return made


def stage_play(base_dir, prefix):
    """Through the runtime: an appearance per look (bare, each shoe), every state played to a GIF."""
    from fractions import Fraction
    from sprite_gen.compose.parts_runtime import PartsPlayer
    from sprite_gen.curate import parts_appearance
    from sprite_gen.spec import parts_contract as contract, parts_workspace as workspace
    out = _out(base_dir)
    assembly = (out / "library/assembly.json").absolute()
    index, registry = workspace.read_workspace(assembly)
    catalog = registry[index["catalog"]["id"]]
    looks = {"bare": {}} | {d: {"footwear": f"{prefix}.shoes.{d}"} for d in DESIGNS if f"{prefix}.shoes.{d}" in registry}
    selection = out / "packs/selection.json"
    selection.write_text(json.dumps({"kind": "parts-appearance-selection", "schema_version": 1, "id": f"{prefix}.look",
                                     "selections": {s["id"]: None for s in catalog["slots"]}}))
    parts_appearance.create(assembly, selection)
    crop, scale = (24, 4, 104, 124), 3
    w, h = (crop[2]-crop[0])*scale, (crop[3]-crop[1])*scale
    result = {}
    for state in CYCLES:
        columns = []
        for look in looks.values():
            index, registry = workspace.read_workspace(assembly)
            player = PartsPlayer.from_workspace(assembly)
            player.set_selection({s["id"]: contract.reference(registry[look[s["id"]]]) if s["id"] in look else None
                                  for s in catalog["slots"]})
            player.set_state(state)
            clip = next(c for c in player.plan["clips"] if c["state"] == state)
            frames, start = [], Fraction(0)
            for sample in clip["samples"]:
                player.seek(start / clip["timebase"])
                frames.append((player.frame()[0], sample["duration_ticks"]*1000//clip["timebase"]))
                start += sample["duration_ticks"]
            columns.append(frames)
        images = []
        for n in range(len(columns[0])):
            sheet = Image.new("RGBA", (w*len(columns), h), (236, 236, 236, 255))
            for k, column in enumerate(columns):
                sheet.alpha_composite(column[n][0].crop(crop).resize((w, h), Image.Resampling.NEAREST), (k*w, 0))
            images.append(sheet)
        images[0].save(out / f"play-{state}.gif", save_all=True, append_images=images[1:], duration=[d for _, d in columns[0]], loop=0)
        result[state] = len(images)
    return {"looks": list(looks), "frames": result}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", required=True, type=Path, help="the character's folder")
    parser.add_argument("stage", choices=("base", "calibrate", "frames", "puppet", "shoes", "library", "segments", "packs", "play"))
    parser.add_argument("--prefix", default="sd.rig.slim")
    parser.add_argument("--body", default="slim", choices=tuple(BODIES))
    parser.add_argument("--cycle", default="walk", choices=tuple(CYCLES))
    parser.add_argument("--design", default="sneakers", choices=tuple(DESIGNS))
    args = parser.parse_args()
    if args.stage == "base":
        result = stage_base(args.base, args.body)
    elif args.stage == "frames":
        result = stage_frames(args.base, args.cycle)
    elif args.stage == "puppet":
        result = stage_puppet(args.base, args.cycle)
    elif args.stage == "calibrate":
        result = stage_calibrate(args.base)
    elif args.stage == "shoes":
        result = stage_shoes(args.base, args.cycle, args.design)
    elif args.stage == "library":
        result = stage_library(args.base, args.prefix)
    elif args.stage == "segments":
        result = stage_segments(args.base, args.prefix)
    elif args.stage == "packs":
        result = stage_packs(args.base, args.prefix)
    else:
        result = stage_play(args.base, args.prefix)
    print(json.dumps(result, indent=1, default=str))
