# SPDX-License-Identifier: Apache-2.0
"""A character built from its skeleton: every joint and anchor is a number, never detected.

A body type is a set of proportions. Its standing skeleton is drawn as a guide and the image model
draws the base mannequin on it; few-frame cycles (idle, walk, run) are posed from the same numbers
and drawn the same way. Every part slot (hair, top, bottoms, shoes) goes the body's way: a design is
drawn once by the model alone on the base, cut by the bones its slot follows and posed on every frame
with the body's own joint turns - no detection, no per-frame redraw. What shows is the runtime's layer
order: the body and each part are cut into pieces by bone, each piece at its body node.

    base      the standing skeleton -> guide -> the base mannequin (base.png, palette, body.json)
    calibrate the template's foot (heel, toe, sole) measured on the base drawing; cycles re-posed
    frames    a cycle (idle 3, walk 6, run 6) -> guide sheet -> drawn frames (the stick route, optional)
    puppet    the base cut along its skeleton, posed per frame (joints closed), mended by the model
    part      a slot's design (expression, hair, upper, lower, footwear): drawn once alone on the base,
              registered, cut by the slot's bones and posed on every frame exactly as the body is
    mend      a posed garment redrawn clean by the model per cycle, put back where the skeleton put it
    dress     every frame in every look, one sheet
    library   the cycles as a frozen motion with the skeleton's sockets
    segments  the body cut into far arm/leg, root, near leg/arm by bone, as the body pack
    packs     each part design as a pack, one piece per body node of its slot
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
    # Four heads tall: the head a quarter of the height (half the share of a Maple-style chibi head).
    # Stands with the feet a stride apart, so a pair of shoes drawn on the base splits one per foot.
    "slim4": {
        "prompt": "a cute young girl base mannequin with gently stylized proportions: a round head about one quarter "
                  "of the total height (four heads tall - NOT a big chibi head), a slender body with narrow shoulders, "
                  "slim arms and legs, small hands and feet",
        "ground": 116, "head": {"center": [64, 32], "radius": 12, "ear": [-2, 1]}, "neck": [64, 45], "shoulder": [64, 48],
        "hip": [64, 75], "torso": {"top": 11, "bottom": 12}, "thigh": 20, "shin": 18,
        "heel": [-3, 3], "toe": [9, 3], "upper_arm": 13, "forearm": 12, "width": {"thigh": 6, "shin": 5, "arm": 4},
        "stand": {"near": (16, 4, -12), "far": (-16, 4, 20), "arms": {"near": (22, 15), "far": (-32, 10)}},
    },
}
# Two heads tall, MapleStory-like: a big head in three-quarter view (the face, where the expression part
# goes, turned toward the viewer), a small body walking right with short stubby limbs.
BODIES["maple"] = {
    "prompt": "a cute MapleStory-style character base mannequin, two heads tall: a big round head as tall as the whole "
              "body below it, a small short body, short stubby arms and legs, small round hands and feet",
    "view": "MapleStory-style side-scroller view facing right (the big head in three-quarter view turned toward the "
            "viewer, the face on its right half; the body and legs in three-quarter side view)",
    "style": " MapleStory character art: clean dark brown outline, soft cel shading, rounded shapes.",
    "ground": 116, "head": {"center": [64, 44], "radius": 21, "ear": [-9, 4]}, "neck": [64, 65], "shoulder": [64, 67],
    "hip": [64, 89], "torso": {"top": 14, "bottom": 15}, "thigh": 12, "shin": 11,
    "heel": [-3, 3], "toe": [7, 3], "upper_arm": 9, "forearm": 8, "width": {"thigh": 7, "shin": 6, "arm": 5},
    "foot_limit": 25,
    "stand": {"near": (20, 4, -16), "far": (-20, 4, 24), "arms": {"near": (20, 15), "far": (-40, 10)}},
}
# The Maple body for costumes: three-quarter, so a top has a front to show, and each limb hangs from its own
# hip or shoulder - the near arm at the back edge of the torso, the far arm at the front edge, the legs side by
# side - so sleeves, torso cloth and trouser legs barely overlap and each reads on its own.
BODIES["maple34"] = dict(
    BODIES["maple"],
    prompt="a cute MapleStory-style character base mannequin, two heads tall: a big round head as tall as the whole "
           "body below it, a small short body with its chest turned toward the viewer, short stubby arms hanging a "
           "little away from the body (the near arm at the back of the torso, the far arm at the front of the chest), "
           "short legs side by side and slightly apart, small round hands and feet, both feet pointing right",
    view="MapleStory-style three-quarter view facing right (the head and the chest turned toward the viewer, the face "
         "on the head's right half)",
    head={"center": [64, 44], "radius": 21, "ear": [-11, 4]}, torso={"top": 17, "bottom": 16},
    offsets={"shoulder": {"near": -8, "far": 8}, "hip": {"near": -3, "far": 3}},
    stand={"near": (-8, 0, 8), "far": (10, 0, -10), "arms": {"near": (-10, 10), "far": (16, 12)}},  # straight knees
)
BODIES["maple34h"] = dict(
    BODIES["maple34"],
    prompt=BODIES["maple34"]["prompt"].replace("short stubby arms", "short slim arms ending in round stubby "
                                                "mitten-like hands with no fingers,").replace(", small round hands and feet", ", small round feet"),
    forearm=7, width={"thigh": 7, "shin": 6, "arm": 4}, hand={"length": 6, "radius": 3},
)
# Head shapes for review (the circle read as a ball): both wider than tall, as Maple heads are.
_HEAD_WORDS = ("a big MapleStory-style head that fills the grey head shape exactly: {shape} - NOT a perfect circle, "
               "NOT a ball")
BODIES["maple_a"] = dict(
    BODIES["maple34h"],
    # The ear a third of the way in from the back, at eye level, inside the outline: on the outline (where the
    # model drew it for [-12, 4]) it read as a bump on the back of the head, and long hair had no room behind it.
    head={"center": [64, 44], "radius": 22, "rx": 23, "ry": 20, "jaw": 0.6, "chin": 2, "ear": [-13, 7],
          "ear_size": (1.5, 2.5)},
    prompt=BODIES["maple34h"]["prompt"].replace("a big round head", _HEAD_WORDS.format(
        shape="wider than tall, a soft rounded-square shape with full cheeks and a gently squared jaw, the chin a "
              "little toward the face")),
)
BODIES["maple_b"] = dict(
    BODIES["maple34h"],
    head={"center": [64, 44], "radius": 22, "rx": 23, "ry": 19, "jaw": 0.25, "chin": 1, "ear": [-12, 4]},
    prompt=BODIES["maple34h"]["prompt"].replace("a big round head", _HEAD_WORDS.format(
        shape="a wide soft oval, wider than tall, with plump round cheeks that make the lower half a little fuller "
              "than the top")),
)
SIDE = "pure side view facing right"
VIEW = {"text": SIDE, "style": ""}  # the character's own view and style, read from body.json by _out
# Standing for the base: both feet and both hands in sight.
STAND = {"near": (10, 4, 0), "far": (-10, 4, 0), "arms": {"near": (22, 15), "far": (-32, 10)}}


def _stand(body):
    return body.get("stand", STAND)


def stand_pose(body):
    s = _stand(body)
    return skeleton(body, tuple(s["near"]), tuple(s["far"]), {k: tuple(v) for k, v in s["arms"].items()})
# Few-frame cycles, near leg: thigh swing from vertical (deg, forward +), knee bend, foot pitch (toe up +).
# The far leg runs half a cycle later; arms swing against their legs. `lift` raises the hip (flight, breath).
CYCLES = {
    # Breathing over planted feet: the upper body sinks a pixel and rises (held half a second each way), moved
    # from the base itself - no model redraw, so nothing but the breath changes between frames.
    "idle": {"stands": True, "legs": [(10, 4, 0)]*4, "far": [(-10, 4, 0)]*4, "arm_swing": 0.0, "arm": (22, 15), "elbow": 12,
             "lift": [0]*4, "sink": [0, 0, 1, 1], "rigid": True, "verb": "standing and breathing", "ms": 250},
    "walk": {"legs": [(22, 0, 12), (8, 10, 0), (-10, 4, -6), (-22, 22, -28), (-4, 40, -10), (16, 22, 4)],
             "arm_swing": 0.7, "elbow": 12, "lift": [0]*6, "verb": "walking", "ms": 130},
    # Both strides alike: contact, the stance knee bending under the body (lowest), push-off into the air
    # (highest) - the hip goes 90.5 -> 91.2 -> 89.7 and back, a pixel or two a frame (it lifted only one
    # stride, and the straight-legged midstance was the highest frame, so the run jolted).
    "run": {"legs": [(30, 14, 6), (0, 55, 0), (-28, 30, -30), (-6, 95, -20), (30, 80, -6), (40, 24, 6)],
            "arm_swing": 1.0, "elbow": 70, "lift": [0, 0, 2, 0, 0, 2], "verb": "running", "ms": 90},
}


def _at(pose, joint, side):
    """A side's own hip or shoulder (poses from before three-quarter bodies have only the middle one)."""
    return pose.get(joint, {}).get(side, pose[joint[:-1]])


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
    if "foot_limit" in body:  # Maple style: the feet stay near level whatever the leg does
        foot = float(np.clip(foot, -body["foot_limit"], body["foot_limit"]))
    return {"knee": knee.tolist(), "ankle": ankle.tolist(), "heel": (ankle + _turn(body["heel"], foot)).tolist(),
            "toe": (ankle + _turn(body["toe"], foot)).tolist(), "foot_turn": float(foot)}


def _arm(shoulder, swing, elbow, body):
    down = np.array([0.0, 1.0])
    el = np.array(shoulder) + _turn(down, -swing)*body["upper_arm"]
    along = _turn(down, -(swing + elbow))
    wrist = el + along*body["forearm"]
    arm = {"elbow": el.tolist(), "wrist": wrist.tolist()}
    if "hand" in body:  # a round hand past the wrist, its own bone (so gloves can replace it as shoes do feet)
        arm["hand"] = (wrist + along*body["hand"]["length"]).tolist()
    return arm


def skeleton(body, near, far, arms, lift=0, sink=0):
    """One pose: legs from the hip, the hip set so the lower foot stands on the ground (then `lift`, which
    raises the whole figure, feet too). `sink` lowers only the upper body - head, torso and arms - by whole
    pixels over planted legs: a breath."""
    probe = [_leg([0.0, 0.0], p, body) for p in (near, far)]
    lowest = max(max(p["heel"][1], p["toe"][1]) for p in probe)
    hip = [body["hip"][0], body["ground"] - lowest - lift]
    shift = hip[1] - body["hip"][1] + sink
    shoulder = [body["shoulder"][0], body["shoulder"][1] + shift]
    # A three-quarter body has width: each leg hangs from its own hip, each arm from its own shoulder.
    off = body.get("offsets", {})
    hips = {side: [hip[0] + off.get("hip", {}).get(side, 0), hip[1]] for side in ("near", "far")}
    shoulders = {side: [shoulder[0] + off.get("shoulder", {}).get(side, 0), shoulder[1]] for side in ("near", "far")}
    return {"hip": hip, "shift": shift, "sink": sink, "shoulder": shoulder, "hips": hips, "shoulders": shoulders,
            "head": {"center": [body["head"]["center"][0], body["head"]["center"][1] + shift], "radius": body["head"]["radius"]},
            "neck": [body["neck"][0], body["neck"][1] + shift],
            "legs": {side: _leg(hips[side], legs, body) for side, legs in (("near", near), ("far", far))},
            "arms": {side: _arm(shoulders[side], *arms[side], body) for side in ("near", "far")}}


def draw_guide(body, pose, upper=None):
    """Sticks on the key: far limbs, then head and torso (or the base's own, moved), then near limbs."""
    image = Image.new("RGBA", (CANVAS, CANVAS))
    draw = ImageDraw.Draw(image)
    w = body["width"]

    def leg(side, color):
        L = pose["legs"][side]
        draw.line([tuple(_at(pose, "hips", side)), tuple(L["knee"])], fill=(*color, 255), width=w["thigh"])
        draw.line([tuple(L["knee"]), tuple(L["ankle"])], fill=(*color, 255), width=w["shin"])
        draw.polygon([tuple(L["ankle"]), tuple(L["heel"]), tuple(L["toe"])], fill=(*color, 255))

    def arm(side, color):
        A = pose["arms"][side]
        draw.line([tuple(_at(pose, "shoulders", side)), tuple(A["elbow"]), tuple(A["wrist"])], fill=(*color, 255), width=w["arm"])
        if "hand" in A:
            r = body["hand"]["radius"]
            cx, cy = (np.array(A["wrist"]) + np.array(A["hand"])) / 2
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(*color, 255))

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
        _draw_head(draw, body["head"], cx, cy, r)
        ex, ey = cx + body["head"]["ear"][0], cy + body["head"]["ear"][1]
        ew, eh = body["head"].get("ear_size", (2, 3))
        draw.ellipse((ex - ew, ey - eh, ex + ew, ey + eh), fill=(*EAR, 255))
    else:
        image.alpha_composite(upper, (0, int(round(pose["shift"]))))
    leg("near", NEAR)
    arm("near", NEAR_ARM)
    draw.line([(0, body["ground"] + 1), (CANVAS, body["ground"] + 1)], fill=(*GROUND, 255), width=1)
    return image


def _draw_head(draw, head, cx, cy, r):
    """The guide's head: a circle, or a shaped head - wider than tall (`rx`, `ry`), with a lower part that is
    `jaw` square (0 round, 1 square corners) and pushed `chin` pixels toward the face."""
    if "rx" not in head:
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(*HEAD, 255))
        return
    rx, ry = head["rx"], head["ry"]
    draw.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=(*HEAD, 255))
    jaw, chin = head.get("jaw", 0), head.get("chin", 0)
    if jaw:  # the lower half as a rounded box: cheeks and a jaw instead of a ball's underside
        w = rx*(0.75 + 0.2*jaw)
        corner = max(int(round(ry*(1 - jaw)*0.9)), 3)
        draw.rounded_rectangle((cx - w + chin, cy, cx + w + chin, cy + ry*0.95), radius=corner, fill=(*HEAD, 255))


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


def _clean_native(drawn_path, sheet_path, origin, scale, crop=CROP, colors=None, palette=None, expect=None,
                  outline=True):
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
    art = pixel_grid.snap(art, palette)
    if outline:
        art = pixel_grid.silhouette_outline(art, [int(v) for v in dark])
    art = art.crop(art.getbbox())
    full = Image.new("RGBA", (CANVAS, CANVAS))
    full.alpha_composite(art, (int(round(crop[0] + bbox[0]/pitch)), int(round(crop[1] + bbox[1]/pitch))))
    return full, palette


def _draw(prompt_text, folder, sheet, refs, name):
    prompt_text = prompt_text.replace(SIDE, VIEW["text"]).replace(" no text.", " no text." + VIEW["style"])
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
    "the eyes would be, never down by the jaw or neck, and well inside the head's outline: the head's back edge runs "
    "on behind the ear, the ear never sits on that edge or sticks out past it. Completely bald, a blank face: NO eyes, NO eyebrows, NO mouth, "
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


def _out(base_dir, body=None):
    folder = base_dir / "rig"
    folder.mkdir(parents=True, exist_ok=True)
    if body is None and (folder / "body.json").exists():
        body = json.loads((folder / "body.json").read_text())["body"]
    if body is not None:
        VIEW.update(text=body.get("view", SIDE), style=body.get("style", ""))
    return folder


def stage_base(base_dir, body_name):
    body = BODIES[body_name]
    out = _out(base_dir, body)
    pose = stand_pose(body)
    guide = draw_guide(body, pose)
    sheet, origins = _sheet([guide], 8, 1, gutter=0)
    sheet.save(out / "base-guide.png")
    drawn = _draw(BASE_PROMPT.format(who=body["prompt"], style=STYLE.format(px=8)), out, out / "base-guide.png", [], "base-drawn")
    base, palette = _clean_native(drawn, out / "base-guide.png", origins[0], 8)
    base, _ = _onto_guide(base, draw_guide(body, pose))
    colors = [list(map(int, c)) for c in palette.tolist()]
    base.save(out / "base.png")
    (out / "base.palette.json").write_text(json.dumps({"colors": colors}))
    (out / "body.json").write_text(json.dumps({"type": body_name, "body": body, "stand": _stand(body), "pose": pose}, indent=1))
    return {"base": str(out / "base.png"), "follows": follows(base, body, pose)}


def follows(image, body, pose):
    """Share of each limb stick's middle line that lands on body pixels (1.0 = drawn where the skeleton says)."""
    alpha = np.asarray(image)[..., 3] > 0
    result = {}
    for side in ("near", "far"):
        L = pose["legs"][side]
        A = pose["arms"][side]
        for name, (a, b) in {f"{side}_thigh": (_at(pose, "hips", side), L["knee"]), f"{side}_shin": (L["knee"], L["ankle"]),
                             f"{side}_foot": (L["heel"], L["toe"]), f"{side}_arm": (_at(pose, "shoulders", side), A["wrist"])}.items():
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
        if cycle.get("stands") and "stand" in body:  # a body with its own stand idles in it
            s = body["stand"]
            poses.append(skeleton(body, tuple(s["near"]), tuple(s["far"]), {k: tuple(v) for k, v in s["arms"].items()},
                                  cycle["lift"][t], cycle.get("sink", [0]*n)[t]))
            continue
        if "arm" in cycle:
            arms = {"near": cycle["arm"], "far": (-cycle["arm"][0], cycle["arm"][1])}
        else:
            arms = {"near": (cycle["arm_swing"]*far[0], cycle["elbow"]), "far": (cycle["arm_swing"]*near[0], cycle["elbow"])}
        poses.append(skeleton(body, near, far, arms, cycle["lift"][t]))
    return poses


EDIT_PROMPT = (
    "Edit image 1, a pixel-art game sprite of one character, pure side view facing right, on a flat pure magenta "
    "#FF00FF background. Change only these things and keep every other pixel exactly as it is:\n{changes}\n{style} "
    "Keep the image size; everything else stays flat #FF00FF.")
EDIT_EAR = ("- The ear: erase the current ear completely (paint that spot as the bald head's own skin and shading, the "
            "head's outline running on smoothly) and draw one small ear inside the thin cyan ring instead - inside the "
            "head's outline, never on its edge; remove the cyan ring.")
EDIT_LEGS = ("- The legs: redraw both legs smooth and slim - clean, continuous outlines from the shorts down to the "
             "ankles, no bumps or notches at the knees; each leg tapers evenly: the thigh the thickest part, the calf "
             "a little thinner than the thigh (never thicker), the ankle thinnest - at the same length, place and "
             "pose, the same skin colors and the far leg still a little darker.")


EDIT_LEG_GUIDE = ("- The legs: redraw both legs to fill exactly the thin cyan outlines - each thigh the thickest "
                  "part, the calf thinner than the thigh, tapering evenly to the ankle - with smooth continuous "
                  "outlines, no bumps or notches; the same skin colors, the far leg still a little darker; remove "
                  "the cyan lines.")
EDITS = {"ear": EDIT_EAR, "legs": EDIT_LEGS, "leg_shape": EDIT_LEG_GUIDE}
LEG_TAPER = {"hip": 10, "knee": 7, "ankle": 4}  # outer widths, outline included


def _leg_shape(stand, side, taper=LEG_TAPER):
    """A leg's wanted silhouette: a quad from the hip to the knee and from the knee to the ankle, tapering."""
    from PIL import ImageDraw
    image = Image.new("L", (CANVAS, CANVAS))
    draw = ImageDraw.Draw(image)
    L = stand["legs"][side]
    points = [(np.array(_at(stand, "hips", side), float), taper["hip"]), (np.array(L["knee"], float), taper["knee"]),
              (np.array(L["ankle"], float), taper["ankle"])]
    for (a, wa), (b, wb) in zip(points, points[1:]):
        u = (b - a) / max(np.hypot(*(b - a)), 1e-6)
        n = np.array([-u[1], u[0]])
        draw.polygon([tuple(a + n*wa/2), tuple(b + n*wb/2), tuple(b - n*wb/2), tuple(a - n*wa/2)], fill=255)
        draw.ellipse((b[0] - wb/2, b[1] - wb/2, b[0] + wb/2, b[1] + wb/2), fill=255)
    return np.asarray(image) > 0


def stage_edit(base_dir, what):
    """The base edited by the model instead of drawn again (a new drawing changes the whole character): `ear`
    moves the ear to the template's ear mark, `legs` makes the legs smooth and tapered. Only the region an edit
    is about is taken from it (the back of the head; the legs below the shorts) - everything else stays the
    base's own pixels. Each edit starts from the current base, which is kept aside first."""
    out = _out(base_dir)
    info = json.loads((out / "body.json").read_text())
    body, stand = info["body"], info["pose"]
    base = Image.open(out / "base.png").convert("RGBA")
    k = len(list(out.glob("base-before-edit*.png")))
    base.save(out / f"base-before-edit-{k}.png")
    palette = pixel_grid.read_palette(out / "base.palette.json")
    guide = np.asarray(base).copy()
    if "ear" in what:
        ear = _ear_mask(body, stand)
        guide[_dilate(ear, 1) & ~ear] = (*CYAN, 255)
    if "leg_shape" in what:  # the wanted legs outlined, below the shorts
        rows = np.mgrid[0:CANVAS, 0:CANVAS][0] >= stand["hip"][1] + 3
        for side in ("far", "near"):
            shape = _leg_shape(stand, side)
            guide[_dilate(shape, 1) & ~shape & rows] = (*CYAN, 255)
    name = "-".join(what)
    sheet, origins = _sheet([Image.fromarray(guide)], 8, 1, gutter=0)
    sheet.save(out / f"edit-{name}-sheet.png")
    drawn = _draw(EDIT_PROMPT.format(changes="\n".join(EDITS[w] for w in what), style=STYLE.format(px=8)), out,
                  out / f"edit-{name}-sheet.png", [], f"edit-{name}-drawn")
    box = base.getbbox()
    edited, _ = _clean_native(drawn, out / f"edit-{name}-sheet.png", origins[0], 8, palette=palette,
                              expect=(box[2] - box[0], box[3] - box[1]))
    edited, shift = _onto_guide(edited, base)
    ys, xs = np.mgrid[0:CANVAS, 0:CANVAS]
    take = np.zeros((CANVAS, CANVAS), bool)
    if "ear" in what:
        take |= (ys <= stand["neck"][1]) & (xs < stand["head"]["center"][0] - 3)
    if "legs" in what or "leg_shape" in what:
        take |= ys >= stand["hip"][1] + 3
    merged = np.asarray(base).copy()
    merged[take] = np.asarray(edited)[take]
    Image.fromarray(merged).save(out / "base.png")
    return {"edit": what, "shift": list(shift), "taken": int(take.sum())}


def stage_calibrate(base_dir):
    """The template's foot measured on the base drawing: the model draws the foot longer and lower than
    the numbers asked, so heel, toe and sole become the drawn foot's own (rearmost, frontmost, lowest
    pixel, in the foot's frame); the ground moves with the sole so the base keeps its hip; every cycle's
    skeleton is posed again. Re-run the puppet after this."""
    out = _out(base_dir)
    info = json.loads((out / "body.json").read_text())
    body = info.get("template", info["body"])  # always from the numbers the base was drawn on
    stand = stand_pose(body)
    base = np.asarray(Image.open(out / "base.png").convert("RGBA"))
    L = stand["legs"]["near"]
    ys, xs = np.nonzero(_foot_mask(_owners(base, body, stand), stand, "near"))
    local = np.array([_turn(p, -L["foot_turn"]) for p in np.stack([xs, ys], 1) - np.array(L["ankle"])])
    sole = float(local[:, 1].max())
    calibrated = dict(body, heel=[round(float(local[:, 0].min()), 2), round(sole, 2)],
                      toe=[round(float(local[:, 0].max()), 2), round(sole, 2)])
    probe = stand_pose(calibrated)
    calibrated["ground"] = int(round(body["ground"] - (probe["hip"][1] - stand["hip"][1])))
    pose = stand_pose(calibrated)
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
        bones[f"{side}_thigh"] = (_at(pose, "hips", side), L["knee"], layer + (0.2 if side == "far" else 0.5))
        bones[f"{side}_shin"] = (L["knee"], L["ankle"], layer + 0.1)
        bones[f"{side}_foot"] = (L["ankle"], L["toe"], layer + 0.15)
        bones[f"{side}_upper_arm"] = (_at(pose, "shoulders", side), A["elbow"], layer + (-0.5 if side == "far" else 1.0))
        bones[f"{side}_forearm"] = (A["elbow"], A["wrist"], layer + (-0.4 if side == "far" else 1.1))
        if "hand" in A:
            bones[f"{side}_hand"] = (A["wrist"], A["hand"], layer + (-0.35 if side == "far" else 1.2))
    return bones


def _seg_distance(px, py, a, b):
    a, b = np.array(a, float), np.array(b, float)
    d = b - a
    t = np.clip(((px - a[0])*d[0] + (py - a[1])*d[1]) / max(float(d @ d), 1e-6), 0, 1)
    return np.hypot(px - (a[0] + t*d[0]), py - (a[1] + t*d[1]))


def _reach(body, stand):
    """Bone name -> how far each pixel is outside that bone's limb (negative inside)."""
    ys, xs = np.mgrid[0:CANVAS, 0:CANVAS]
    bones = _bones(stand)
    dist = {name: _seg_distance(xs, ys, a, b) for name, (a, b, _) in bones.items()}
    for side in ("far", "near"):  # a foot is its heel-ankle-toe triangle, not only the ankle->toe line
        L = stand["legs"][side]
        dist[f"{side}_foot"] = np.minimum(dist[f"{side}_foot"], np.minimum(_seg_distance(xs, ys, L["ankle"], L["heel"]),
                                                                           _seg_distance(xs, ys, L["heel"], L["toe"])))
        A = stand["arms"][side]
        if "hand" not in A:  # no hand bone: the hand past the wrist goes with the forearm, not left on the torso
            elbow, wrist = np.array(A["elbow"], float), np.array(A["wrist"], float)
            hand = wrist + (wrist - elbow) / max(np.hypot(*(wrist - elbow)), 1e-6)*body["width"]["arm"]
            dist[f"{side}_forearm"] = _seg_distance(xs, ys, elbow, hand)
    widths = {"thigh": body["width"]["thigh"], "shin": body["width"]["shin"], "foot": 4,
              "upper_arm": body["width"]["arm"], "forearm": body["width"]["arm"] + 1,
              "hand": 2*body.get("hand", {}).get("radius", 2) + 1}
    return {n: d - widths[n.split("_", 1)[1]]/2 for n, d in dist.items()}


def _owners(rgba, body, stand, bones=None, whole=False, torso_only=False):
    """Each pixel's bone name ("" where empty) among `bones` (default every bone and "upper"): the upper
    body (head, torso, shorts) above the hip line, else the nearest bone - the near limbs where they cover
    the far ones. `whole`: each connected stroke goes to the bone most of its pixels would (a shoe is not
    split between the feet). `torso_only`: the upper body claims only the head and the skeleton's torso
    (a part's wide sleeve rim beside it goes with the arm)."""
    rgba = np.asarray(rgba)
    alpha = rgba[..., 3] > 0
    names = list(bones) if bones else list(_bones(stand)) + ["upper"]
    reach = {n: r for n, r in _reach(body, stand).items() if n in names}
    owner = np.full(alpha.shape, "", dtype=object)
    if not reach:
        owner[alpha] = "upper"
        return owner
    ys, xs = np.mgrid[0:CANVAS, 0:CANVAS]
    cx, cy = stand["head"]["center"]
    head = np.hypot(xs - cx, ys - cy) <= stand["head"]["radius"] + 2
    best = {}
    for side in ("near", "far"):
        limbs = [n for n in reach if n.startswith(side)]
        if limbs:
            stack = np.stack([reach[n] for n in limbs])
            best[side] = (np.array(limbs, dtype=object)[stack.argmin(0)], stack.min(0))
    inf = np.full(alpha.shape, np.inf)
    near_r = best["near"][1] if "near" in best else inf
    far_r = best["far"][1] if "far" in best else inf
    take_near = near_r <= 1.5
    take_upper = ~take_near & (head | (ys < stand["hip"][1] + 4) & (far_r > 1.0)) if "upper" in names \
        else np.zeros_like(alpha)
    if torso_only:
        take_upper &= head | _torso_mask(body, stand, grow=3)
    rest = ~take_near & ~take_upper
    if "near" in best:
        owner[take_near] = best["near"][0][take_near]
    owner[take_upper] = "upper"
    far_first = (far_r <= near_r + 2) & ("far" in best)
    if "far" in best:
        owner[rest & far_first] = best["far"][0][rest & far_first]
    if "near" in best:
        owner[rest & ~far_first] = best["near"][0][rest & ~far_first]
    owner[~alpha] = ""
    if whole:  # a stroke goes whole to the bone most of it is on - unless two pieces touch (a pair of shoes
        for c in _components(alpha):  # drawn touching): then each pixel goes to its nearest bone
            names_in, counts = np.unique(owner[c].astype(str), return_counts=True)
            if counts.max() >= 0.75*counts.sum():
                owner[c] = names_in[counts.argmax()]
    return owner


def cut_puppet(base, body, stand, bones=None, whole=False):
    """An image cut into rigid pieces, one per bone plus the upper body (or only `bones`, as a part)."""
    rgba = np.asarray(base)
    owner = _owners(rgba, body, stand, bones, whole, torso_only=bones is not None and len(bones) > 1)
    pieces = {}
    for name in bones or list(_bones(stand)) + ["upper"]:
        mask = owner == name
        if mask.any():
            pieces[name] = np.where(mask[..., None], rgba, 0).astype(np.uint8)
    return pieces


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


def _upsampled(pixels):
    up = np.ascontiguousarray(pixels)
    for _ in range(3):
        up = _scale2x(up)
    return up


def _warp(up, phi, a, a2):
    """A piece turned by `phi` about its point `a` and moved so `a` lands on `a2` - RotSprite style,
    sampled from its 8x Scale2x copy `up`, so turned outlines stay one clean pixel."""
    c, s = np.cos(-phi), np.sin(-phi)
    oy, ox = np.mgrid[0:CANVAS, 0:CANVAS]
    dx, dy = ox + 0.5 - a2[0], oy + 0.5 - a2[1]
    sx = np.floor((c*dx - s*dy + a[0])*8).astype(int)
    sy = np.floor((s*dx + c*dy + a[1])*8).astype(int)
    ok = (sx >= 0) & (sy >= 0) & (sx < up.shape[1]) & (sy < up.shape[0])
    out = np.zeros((CANVAS, CANVAS, 4), dtype=np.uint8)
    out[ok] = up[sy[ok], sx[ok]]
    return out


def _angle(a, b):
    return float(np.arctan2(b[1] - a[1], b[0] - a[0]))


def _motion(stand, pose):
    """Bone -> (turn, source pivot, target pivot): the upper body moves by whole pixels, every limb turns
    about its joint."""
    src, dst = _bones(stand), _bones(pose)
    shift = [0.0, float(round(pose["hip"][1] - stand["hip"][1]) + pose.get("sink", 0) - stand.get("sink", 0))]
    moves = {"upper": (0.0, np.array(stand["hip"], float), np.array(stand["hip"], float) + shift)}
    for n in src:
        moves[n] = (_angle(*dst[n][:2]) - _angle(*src[n][:2]), np.array(src[n][0], float), np.array(dst[n][0], float))
    return moves


# Parent -> child joints: where a bent limb opens a wedge, the child's end near the joint is swept through the
# angles between the two, under both, so the outline closes around the joint instead of leaving a gap.
JOINTS = [(p.format(s=s), c.format(s=s), w) for s in ("far", "near") for p, c, w in (
    ("upper", "{s}_thigh", "thigh"), ("{s}_thigh", "{s}_shin", "shin"), ("{s}_shin", "{s}_foot", "shin"),
    ("{s}_upper_arm", "{s}_forearm", "arm"), ("{s}_forearm", "{s}_hand", "arm"))]  # the shoulder sits inside the torso: a sweep there only fans out behind


def pose_pieces(pieces, body, stand, pose, ground=None):
    """Pieces cut on the standing pose moved onto another pose, back to front, the joints closed. Returns
    the picture and which bone each pixel came from. With `ground`, a foot piece is kept out of the ground."""
    bones, moves = _bones(stand), _motion(stand, pose)
    depth = {n: v[2] for n, v in bones.items()} | {"upper": 1.5}
    ups = {n: _upsampled(p) for n, p in pieces.items()}
    canvas = np.zeros((CANVAS, CANVAS, 4), dtype=np.uint8)
    owner = np.full((CANVAS, CANVAS), "", dtype=object)
    lifts = {}
    for n in sorted(pieces, key=depth.get):
        phi, a, a2 = moves[n]
        layer = _warp(ups[n], phi, a, a2)
        if ground is not None and n.endswith("_foot"):
            rows = np.nonzero(layer[..., 3].any(1))[0]
            lifts[n] = max(int(rows.max()) - ground, 0) if len(rows) else 0
            layer = np.roll(layer, -lifts[n], 0)
        put = layer[..., 3] > 0
        canvas[put], owner[put] = layer[put], n
    ys, xs = np.mgrid[0:CANVAS, 0:CANVAS]
    for parent, child, width in JOINTS:
        if child not in pieces or (parent not in pieces and parent != "upper"):
            continue
        phi_p, phi_c = moves[parent][0], moves[child][0]
        if abs(phi_c - phi_p) < np.radians(4):
            continue
        joint = moves[child][1]
        near_joint = np.hypot(xs + 0.5 - joint[0], ys + 0.5 - joint[1]) <= body["width"][width]/2 + 2
        end = np.where(near_joint[..., None], pieces[child], 0).astype(np.uint8)
        if not end[..., 3].any():
            continue
        up = _upsampled(end)
        steps = int(np.ceil(abs(phi_c - phi_p) / np.radians(8)))
        for phi in np.linspace(phi_p, phi_c, steps + 2)[1:-1]:
            layer = np.roll(_warp(up, phi, joint, moves[child][2]), -lifts.get(child, 0), 0)
            put = (layer[..., 3] > 0) & (canvas[..., 3] == 0)
            canvas[put], owner[put] = layer[put], child
    return canvas, owner


def pose_puppet(pieces, body, stand, pose):
    return Image.fromarray(pose_pieces(pieces, body, stand, pose)[0])


def _keep_head(mended, rough, pose):
    """The head is never bent, so it stays the base's own pixels: the model redraws it a pixel or two off,
    and a held part drawn on the base (hair, hat) then shows a bald ring."""
    zone = np.mgrid[0:CANVAS, 0:CANVAS][0] <= pose["neck"][1]  # rows above the neck (the drawn head outgrows its circle)
    out = np.asarray(mended).copy()
    out[zone] = np.asarray(rough)[zone]
    return Image.fromarray(out)


def stage_puppet(base_dir, cycle_name):
    """Frames from the cut base posed bone by bone, then mended by the model: feet are where the skeleton
    says, by construction."""
    out = _out(base_dir)
    info = json.loads((out / "body.json").read_text())
    body, stand = info["body"], info["pose"]
    base = Image.open(out / "base.png").convert("RGBA")
    palette = pixel_grid.read_palette(out / "base.palette.json")
    folder = out / cycle_name
    folder.mkdir(exist_ok=True)
    poses = cycle_poses(body, CYCLES[cycle_name])
    (folder / "skeleton.json").write_text(json.dumps({"cycle": cycle_name, "poses": poses}, indent=1))
    pieces = cut_puppet(base, body, stand)
    work = folder / "puppet"
    work.mkdir(exist_ok=True)
    roughs = [pose_puppet(pieces, body, stand, p) for p in poses]
    for t, r in enumerate(roughs):
        r.save(work / f"rough-{t}.png")
    if CYCLES[cycle_name].get("rigid"):  # the base moved by whole pixels only: it is its own clean drawing
        for t, r in enumerate(roughs):
            r.save(folder / f"frame-{t}.png")
        for stale in folder.glob("frame-*.png"):
            if int(stale.stem.split("-")[1]) >= len(roughs):
                stale.unlink()
        return [{"frame": t, "rigid": True} for t in range(len(roughs))]
    sheet, origins = _sheet(roughs, 4, 3)
    sheet.save(work / "sheet.png")
    drawn = _draw(PUPPET_PROMPT.format(verb=CYCLES[cycle_name]["verb"], style=STYLE.format(px=4), w=sheet.width,
                                       h=sheet.height), work, work / "sheet.png", [out / "base.png"], "mended")
    report = []
    for t, (o, rough) in enumerate(zip(origins, roughs)):
        box = rough.getbbox()
        mended, _ = _clean_native(drawn, work / "sheet.png", o, 4, palette=palette, expect=(box[2]-box[0], box[3]-box[1]))
        mended, shift = _onto_guide(mended, rough)
        mended = _keep_head(mended, rough, poses[t])
        mended.save(work / f"mended-{t}.png")
        mended.save(folder / f"frame-{t}.png")
        report.append({"frame": t, "shift": list(shift), "follows": follows(mended, body, poses[t])})
    return report


def _components(mask, minimum=1):
    from sprite_gen.frames.parts_segments import _components as components
    return sorted((c for c in components(mask) if c.sum() >= minimum), key=lambda c: -int(c.sum()))


def _body_owner(rgba, body, pose):
    """A body frame's bones, every pixel past the ankle given to the foot (heel and toe tips included), so a
    shoe that replaces the foot hides all of the bare foot."""
    owner = _owners(rgba, body, pose)
    ys, xs = np.mgrid[0:CANVAS, 0:CANVAS]
    for side in ("near", "far"):
        owner[_foot_mask(owner, pose, side)] = f"{side}_foot"
        A = pose["arms"][side]
        if "hand" in A:  # and past the wrist, the hand
            elbow, wrist = np.array(A["elbow"], float), np.array(A["wrist"], float)
            u = (wrist - elbow) / max(np.hypot(*(wrist - elbow)), 1e-6)
            past = (xs + 0.5 - wrist[0])*u[0] + (ys + 0.5 - wrist[1])*u[1] >= 0
            owner[np.isin(owner.astype(str), [f"{side}_forearm", f"{side}_hand"]) & past] = f"{side}_hand"
    return owner


def _foot_mask(owner, pose, side):
    """A leg's foot: its pixels past the plane through the ankle across the shin (heel and its outline
    included, unlike the nearest-bone split)."""
    L = pose["legs"][side]
    knee, ankle = np.array(L["knee"], float), np.array(L["ankle"], float)
    u = (ankle - knee) / max(np.hypot(*(ankle - knee)), 1e-6)
    ys, xs = np.mgrid[0:CANVAS, 0:CANVAS]
    past = (xs + 0.5 - ankle[0])*u[0] + (ys + 0.5 - ankle[1])*u[1] >= -1
    return np.isin(owner, [f"{side}_shin", f"{side}_foot"]) & past


# --- parts: a design drawn once on the base, cut by the skeleton's bones, posed exactly as the body is ------
# A slot says which bones its design follows, which part of the upper body is its own (`zone`), and which
# body node each piece is drawn at - so the runtime's layer order puts sleeves with arms and shoes with feet.
# Every slot runs the same steps: design (one model call, the part alone on the base) -> register -> cut by
# bone -> pose every frame. Nothing per slot is code.
LIMBS_ARM = ["near_upper_arm", "near_forearm", "far_upper_arm", "far_forearm"]
LIMBS_LEG = ["near_thigh", "near_shin", "far_thigh", "far_shin"]
SLOTS = {
    "hair": {
        "bones": ["upper"], "zone": "head", "core": ["upper"], "core_zone": "crown", "rim": 3, "nodes": {"upper": "hair"}, "margin": 12,
        "reduce": "strands",
        "view": "the head and shoulders", "what": "the hair",
        "fit": "The hair sits on its bald head as the hairstyle does: it hugs the skull outline exactly - no gap of bald "
               "scalp between the hair and the head's outline. The whole face stays open: bangs stop well above where "
               "the eyes go (the middle of the face, on the head's right half), and no hair falls across the cheeks or "
               "the front of the face; side hair stays behind the face, toward the back of the head. Unless told otherwise "
               "below, it may cover the ear.",
        "designs": {"bob": "a chin-length chestnut-brown bob with straight-cut bangs, a 4-step warm brown ramp",
                    "ponytail": "glossy black hair with short side-swept bangs, all the rest pulled back into a ponytail "
                                "tied at the back of the head that hangs down behind it to the nape",
                    # ear-showing styles, to settle where the ear sits
                    "long_ear": {"desc": "long straight honey-blonde hair falling down the back past the shoulders, with "
                                         "soft bangs, tucked behind the ear",
                                 "fit": "The ear stays fully visible: the side hair is tucked behind the ear - it runs "
                                        "from the temple over the top of the ear, then down behind it onto the back - so "
                                        "every pixel of the ear and a thin line of skin around it stay uncovered; the "
                                        "long hair hangs down the back of the head and the back, behind the ear.",
                                 "below": 34, "show_ear": True},
                    "short_ear": {"desc": "a short dark-brown pixie cut with a soft side-swept fringe, cropped above and "
                                          "around the ear",
                                  "fit": "The ear stays fully visible: the hair is cut short above and around the ear and "
                                         "leaves the whole ear uncovered - draw no hair over it.", "show_ear": True}}},
    "expression": {
        "bones": ["upper"], "zone": "head", "nodes": {"upper": "eyes"}, "sheet": True, "margin": 3, "what": "the face",
        "fit": "a MapleStory-style face: big glossy dark-brown eyes (tall ovals, each about a fifth of the head's "
               "width) with a white highlight, short soft brows, a small mouth, a faint pink blush.",
        "designs": {"neutral": "calm, eyes open, small closed mouth",
                    "smile": "happy: a wide open smile, eyes curved up",
                    "blink": "eyes closed as short downward curves, otherwise calm",
                    "surprised": "round wide eyes, a small round open mouth"}},
    "upper": {
        "bones": ["upper"] + LIMBS_ARM, "mend": True,
        "cells": [{"bones": ["upper"], "hide": LIMBS_ARM + ["near_hand", "far_hand"],
                   "ask": "only the top's body, from the neck down to the hips, without sleeves - this body has its "
                          "arms taken away, so draw the torso part whole and closed where the arms would join"},
                  {"bones": ["near_upper_arm", "near_forearm"], "show": ["near_upper_arm", "near_forearm", "near_hand"],
                   "ask": "only the sleeve on this arm, as long as the design's sleeve (nothing if it has none)"}],
        "zone": "chest", "core": ["upper"], "nodes": {"upper": "torso", "near": "arm_front", "far": "arm_back"},
        "margin": 6, "fill": True, "view": "the upper body", "what": "the top",
        "fit": "The top covers the whole torso from the neck down to just over the hips, hiding the undershirt "
               "completely, and its sleeves lie along the arms as the design has them (the near sleeve in front of "
               "the torso, the far sleeve behind it). The hands stay bare: leave them out.",
        "designs": {"tee": "a short-sleeved mustard-yellow crew-neck t-shirt with a small white star on the chest",
                    "sweater": "a long-sleeved cream knit sweater with red horizontal stripes, sleeves to the wrists"}},
    "lower": {
        "bones": ["upper"] + LIMBS_LEG, "mend": True, "zone": "hips", "core": ["upper", "near_thigh", "far_thigh"], "core_zone": "seat", "nodes": {"upper": "waist", "near": "leg_front", "far": "leg_back"},
        "margin": 6, "fill": True, "view": "the lower body", "what": "the bottoms",
        "fit": "The bottoms start at the waist, covering the charcoal shorts completely, and follow each leg as the design "
               "has them (the near leg in front, the far leg behind). The feet stay bare: leave them out.",
        "designs": {"jeans": "long slightly loose blue denim jeans down to the ankles",
                    "shorts": "khaki cargo shorts down to mid-thigh with a brown belt"}},
    "gloves": {
        "bones": ["near_hand", "far_hand"], "core": ["near_hand", "far_hand"], "whole": True,
        "nodes": {"near": "hand_front", "far": "hand_back"}, "replaces": True, "angles": 30, "margin": 5,
        "view": "the arms and round hands", "what": "the two gloves",
        "one": "glove (one round, mitten-like glove, its cuff at the wrist)",
        "fit": "One glove on each hand, exactly where its hand is, covering the whole round hand and the wrist and only a "
               "little bigger than the hand: small, simple, rounded MapleStory-style gloves.",
        "designs": {"mittens": "red knitted mittens with a fluffy white cuff",
                    "leather": "dark brown leather gloves with a tan cuff"}},
    "footwear": {
        "bones": ["near_foot", "far_foot"], "core": ["near_foot", "far_foot"], "whole": True, "nodes": {"near": "foot_front", "far": "foot_back"},
        "replaces": True, "angles": 15, "margin": 5, "view": "the lower legs and bare feet", "what": "the two shoes",
        "one": "shoe (one shoe in side view; level, its toe points right)",
        "fit": "One shoe on each foot, exactly where its foot is, at its angle, the sole on the sole, covering every bit "
               "of bare foot and only a little bigger than the foot: small, simple, rounded MapleStory-style shoes whose "
               "top ends at the ankle, never up the shin.",
        "designs": {"sneakers": "low-top red canvas sneakers with a white rubber sole and white laces",
                    "boots": "short chestnut-brown leather boots ending just above the ankle, a rounded toe, a thick "
                             "dark-brown sole band along the whole bottom, a lighter fold at the top"}},
}
PART_PROMPT = (
    "Image 1 is a pixel-art close-up of {view} of one character in pure side view facing right, on a flat pure magenta "
    "#FF00FF background. Draw ONLY {what} this character wears: {desc}. Draw it exactly where it sits on the body in "
    "image 1, at the same size and grid, as if the body were invisible. {fit} Whatever is on the far side, behind the "
    "body, is a little darker. Draw nothing else: no skin, no face, no head, no hands, no body outline, nothing of the "
    "character's undershirt, shorts or bare feet - every other pixel is flat #FF00FF. {style} Keep the image size.")
REDO_NOTE = (" A first try drew it {why}. The thin cyan line in image 1 outlines the body area {what} must cover: fill "
             "everything inside it (the item may reach past it where the design does) at full body size, and do not "
             "draw the cyan line.")
CYAN = (0, 255, 255)


def _design(slot, design):
    """A slot's design as {"desc", optional "fit" words added to the slot's, optional "below" px of extra reach}."""
    spec = slot["designs"][design]
    return {"desc": spec} if isinstance(spec, str) else spec


def _slot_node(slot, bone):
    return slot["nodes"]["upper" if bone == "upper" else bone.split("_", 1)[0]]


def _zone(slot, stand):
    """Rows of the upper body that belong to a slot."""
    ys = np.mgrid[0:CANVAS, 0:CANVAS][0]
    neck, hip = stand["neck"][1], stand["hip"][1]
    return {"head": ys <= neck, "crown": ys <= stand["head"]["center"][1], "chest": (ys >= neck - 1) & (ys <= hip + 2), "hips": ys >= hip - 5, "seat": (ys >= hip) & (ys <= hip + 6)}.get(
        slot.get("zone"), np.zeros((CANVAS, CANVAS), bool))


def _target(slot, base_owner, stand):
    """The base pixels a slot's design is drawn over."""
    limbs = [b for b in slot["bones"] if b != "upper"]
    target = np.isin(base_owner.astype(str), limbs)
    if "upper" in slot["bones"]:
        target |= (base_owner == "upper") & _zone(slot, stand)
    return target


def _dilate(mask, n=1):
    for _ in range(n):
        m = mask.copy()
        m[1:] |= mask[:-1]; m[:-1] |= mask[1:]; m[:, 1:] |= mask[:, :-1]; m[:, :-1] |= mask[:, 1:]
        mask = m
    return mask


def _register(part, target, body_alpha, reach=8, core=None):
    """A drawn part moved by whole pixels onto the base: most of its target covered, least of the rest of the
    body (the model shifts its output a few pixels). With a `core` (what the part must cover - a hair's crown,
    not the whole head with its face), the core is what counts, and each pixel moved costs 2% of it: the
    model's own placement is usually right, and covering more of a big target (the face) is not better."""
    alpha = np.asarray(part)[..., 3] > 0
    other = body_alpha & ~_dilate(target, 1)
    goal = target if core is None else core
    cost = 0 if core is None else 0.02*int(core.sum())
    def score(d):
        a = np.roll(np.roll(alpha, d[1], 0), d[0], 1)
        return (int((a & goal).sum()) - 0.5*int((a & other).sum()) - cost*(abs(d[0]) + abs(d[1])),
                -abs(d[0]) - abs(d[1]))
    best = max(((dx, dy) for dy in range(-reach, reach + 1) for dx in range(-reach, reach + 1)), key=score)
    return np.roll(np.roll(np.asarray(part), best[1], 0), best[0], 1), best


def _fill_rows(piece, slot, body, stand, base):
    """The torso piece of a garment closed row by row inside the slot's zone: the near sleeve covered part
    of it on the base, and when the arm swings away the body would show through. Each gap takes the nearest
    design pixel of its row (stripes stay level)."""
    out = piece.copy()
    alpha = piece[..., 3] > 0
    zone = _zone(slot, stand)
    under = np.isin(_owners(base, body, stand).astype(str), ["upper", "near_upper_arm", "near_forearm"])
    torso = _torso_mask(body, stand) & under  # never past the drawn torso, nor where the far arm was
    for y in np.nonzero((alpha & zone).any(1))[0]:
        xs = np.nonzero(alpha[y])[0]
        for x in range(CANVAS):
            if not alpha[y, x] and (xs.min() < x < xs.max() or torso[y, x]):
                out[y, x] = piece[y, xs[np.abs(xs - x).argmin()]]
    return out


def _torso_mask(body, stand, grow=2):
    """The skeleton's torso shape (the guide's), a little grown: the body there is torso even where an arm
    hid it on the base."""
    image = Image.new("L", (CANVAS, CANVAS))
    span = body.get("offsets", {}).get("shoulder", {})  # a three-quarter torso reaches its shoulders
    top = max(body["torso"]["top"], abs(span.get("far", 0) - span.get("near", 0)) + body["width"]["arm"]) + grow*2
    bottom = body["torso"]["bottom"] + grow*2
    (sx, sy), (hx, hy) = stand["shoulder"], stand["hip"]
    ImageDraw.Draw(image).polygon([(sx - top/2, sy - 3), (sx + top/2, sy - 3), (hx + bottom/2, hy + 2),
                                   (hx - bottom/2, hy + 2)], fill=255)
    return np.asarray(image) > 0


def _far_from_near(pieces, base, body, stand):
    """A far limb piece the base mostly hid (a sleeve behind the torso) rebuilt from its near twin: turned
    from the near bone onto the far one and shaded as the body's far limbs are."""
    bones = _bones(stand)
    rgba = np.asarray(base)
    owner = _owners(rgba, body, stand)
    luma = rgba[..., :3].astype(float) @ np.array([0.299, 0.587, 0.114])
    near_px, far_px = np.isin(owner.astype(str), ["near_thigh", "near_shin"]), np.isin(owner.astype(str), ["far_thigh", "far_shin"])
    shade = float(luma[far_px].mean() / luma[near_px].mean()) if near_px.any() and far_px.any() else 0.8
    rebuilt = []
    for near in [n for n in pieces if n.startswith("near_")]:
        far = "far_" + near[len("near_"):]
        count = lambda n: int((pieces[n][..., 3] > 0).sum()) if n in pieces else 0  # noqa: E731
        if count(near) < 20 or count(far) >= 0.6*count(near):
            continue
        (a, b, _), (a2, b2, _) = bones[near], bones[far]
        moved = _warp(_upsampled(pieces[near]), _angle(a2, b2) - _angle(a, b), np.array(a, float), np.array(a2, float))
        moved[..., :3] = np.clip(moved[..., :3]*shade, 0, 255).astype(np.uint8)
        pieces[far] = moved
        rebuilt.append(far)
    return rebuilt


def _ear_mask(body, stand):
    """The template ear (its guide mark grown by a pixel): what an ear-showing hairstyle keeps clear."""
    ex = stand["head"]["center"][0] + body["head"]["ear"][0]
    ey = stand["head"]["center"][1] + body["head"]["ear"][1]
    ew, eh = body["head"].get("ear_size", (2, 3))
    ys, xs = np.mgrid[0:CANVAS, 0:CANVAS]
    return ((xs + 0.5 - ex) / (ew + 1.5))**2 + ((ys + 0.5 - ey) / (eh + 1.5))**2 <= 1


def _tidy_strands(part, colors=16):
    """A reduced drawing of strands (hair) on a shared palette, gaps of a few pixels between
    strands closed with the nearest strand color (the head showing through a pinhole read as a black fleck
    once outlined). Its outline is closed later, where it meets the background (_outline_against)."""
    from sprite_gen.frames import extract
    part = np.asarray(part).copy()
    alpha = part[..., 3] > 0
    for c in _components(~alpha):
        if c.sum() <= 4 and not (c[0].any() or c[-1].any() or c[:, 0].any() or c[:, -1].any()):
            ys, xs = np.nonzero(c)
            for y, x in zip(ys, xs):
                for dy, dx in ((0, -1), (0, 1), (-1, 0), (1, 0)):
                    if alpha[y + dy, x + dx]:
                        part[y, x] = part[y + dy, x + dx]
                        break
    image = Image.fromarray(part)
    palette = np.array(extract.build_shared_palette([image], colors))
    image = pixel_grid.snap(image, palette)
    snapped = np.asarray(image).copy()
    alpha = snapped[..., 3] > 0
    outer = ~alpha
    for c in _components(~alpha):  # the background reaching the canvas edge is outside; enclosed gaps are not
        if not (c[0].any() or c[-1].any() or c[:, 0].any() or c[:, -1].any()):
            outer &= ~c
    return snapped, palette


def _outline_against(part, body_alpha):
    """A placed part's edge darkened only where it meets the background - not where it lies over the body (the
    hair's edge over the face is the model's own line; a second dark ring there read as a thick band) - and
    only on pixels not already dark."""
    part = part.copy()
    alpha = part[..., 3] > 0
    if not alpha.any():
        return part
    luma = part[..., :3].astype(float) @ np.array([0.299, 0.587, 0.114])
    colors = part[alpha][:, :3].astype(int)
    dark = colors[(colors @ np.array([299, 587, 114])).argmin()]
    background = ~alpha & ~body_alpha
    edge = alpha & _dilate(background, 1) & (luma > np.percentile(luma[alpha], 20))
    part[edge, :3] = dark
    return part


def _drawn_ear(base, body, stand):
    """The ear as the base draws it - its outline and inside, found around the template mark as the pixels that
    are not the head's own skin - so a hair cut round it fits the ear, not the (smaller) template mark."""
    rgba = np.asarray(base)
    ys, xs = np.mgrid[0:CANVAS, 0:CANVAS]
    head = (rgba[..., 3] > 0) & (ys <= stand["neck"][1])
    inside = head & ~_dilate(~head, 1)
    colors = rgba[..., :3].astype(int)
    codes = colors[..., 0]*65536 + colors[..., 1]*256 + colors[..., 2]
    values, counts = np.unique(codes[inside], return_counts=True)
    skin = values[np.argsort(counts)[::-1][:6]]  # the head's commonest tones are its skin and shading
    mark = _ear_mask(body, stand)
    near = _dilate(mark, 4) & inside & ~np.isin(codes, skin)
    parts = [c for c in _components(near) if (c & _dilate(mark, 1)).any()]
    ex = stand["head"]["center"][0] + body["head"]["ear"][0]
    ey = stand["head"]["center"][1] + body["head"]["ear"][1]
    ew, eh = body["head"].get("ear_size", (2, 3))
    roomy = ((xs + 0.5 - ex) / (ew + 2.5))**2 + ((ys + 0.5 - ey) / (eh + 3))**2 <= 1
    ear = (np.any(parts, 0) if parts else mark) | (roomy & inside)
    # fill the ear's inside (skin-colored pixels enclosed by its outline)
    filled = ear.copy()
    for c in _components(~ear & _dilate(ear, 3)):
        if not (c & ~_dilate(ear, 2)).any():
            filled |= c
    return filled | mark


def _clear_ear(part, ear):
    """Hair drawn over the ear taken off it with a pixel of skin round it, the cut edge closed with the hair's
    darkest color - one ear in the opening, not the hair's cut edge crossing the ear's own outline."""
    part = part.copy()
    alpha = part[..., 3] > 0
    if not (alpha & _dilate(ear, 1)).any():
        return part
    colors = part[alpha][:, :3].astype(int)
    dark = colors[(colors @ np.array([299, 587, 114])).argmin()]
    opening = _dilate(ear, 1)
    part[opening] = 0
    luma = part[..., :3].astype(float) @ np.array([0.299, 0.587, 0.114])
    edge = (part[..., 3] > 0) & _dilate(opening, 1) & (luma > float(dark @ np.array([0.299, 0.587, 0.114])) + 30)
    part[edge, :3] = dark
    return part


def _rim(part, base, core, n):
    """A head-covering part grown up to `n` pixels over the bare head next to it: models draw held hair a
    pixel or two inside the skull, and a bald ring shows. New pixels on the head's outline take the part's
    darkest color."""
    part = part.copy()
    alpha = part[..., 3] > 0
    drawn = alpha.copy()
    body = np.asarray(base)[..., 3] > 0
    rim = body & _dilate(~body, 2)  # within two pixels of the head's outline - never down over the forehead
    for _ in range(n):
        grow = _dilate(alpha, 1) & core & rim & ~alpha
        for y, x in zip(*np.nonzero(grow)):
            for dy, dx in ((0, -1), (0, 1), (-1, 0), (1, 0)):
                if 0 <= y + dy < CANVAS and 0 <= x + dx < CANVAS and alpha[y + dy, x + dx]:
                    part[y, x] = part[y + dy, x + dx]
                    break
        alpha |= grow
    colors = part[alpha][:, :3].astype(int)
    dark = colors[(colors @ np.array([299, 587, 114])).argmin()]
    outline = body & _dilate(~body, 1) & alpha & ~drawn  # grown over the head's own outline
    part[outline, :3] = dark
    return part


def _cut_on_body(part, base_owner, body, stand, slot):
    """A single-drawing part cut into bone pieces by the nearest bone (strokes whole for `whole` parts)."""
    return cut_puppet(part, body, stand, slot["bones"], slot.get("whole", False))


CELLS_PROMPT = (
    "Image 1 is a pixel-art sheet of {n} cells, each a close-up of part of one character in pure side view facing right, "
    "on a flat pure magenta #FF00FF background. The character wears {what}: {desc}. {fit} Draw {what} in separate "
    "pieces, one per cell, each exactly where it sits on the body shown in that cell, at the same size and grid, as if "
    "the body were invisible: {cells}. Draw nothing else: no skin, no head, no hands, no body outline, nothing of the "
    "character's undershirt or shorts - every other pixel is flat #FF00FF. {style} Keep every cell exactly in place; "
    "keep this {w}x{h} image and its grid exactly.")


def _design_cells(out, slot, design, folder, base, base_owner, body, stand, crop, scale):
    """A part whose pieces overlap in the side view (a top's body and its sleeve) drawn as separate pieces in
    one call: each cell shows only the body parts that piece sits on (the torso with the arms taken away; the
    arm alone), so the torso cloth is whole and the sleeve is only sleeve. Each cell is registered on its own
    body parts; a cell's pixels belong to its bones."""
    base_pieces = cut_puppet(base, body, stand)
    images, targets = [], []
    for cell in slot["cells"]:
        shown = [n for n in base_pieces if n not in cell.get("hide", [])] if "hide" in cell else cell["show"]
        image = np.zeros((CANVAS, CANVAS, 4), np.uint8)
        for n in sorted(shown, key=lambda n: {**{k: v[2] for k, v in _bones(stand).items()}, "upper": 1.5}[n]):
            put = base_pieces[n][..., 3] > 0
            image[put] = base_pieces[n][put]
        images.append(Image.fromarray(image))
        target = np.isin(base_owner.astype(str), [b for b in cell["bones"] if b != "upper"])
        if "upper" in cell["bones"]:
            target |= ((base_owner == "upper") | _torso_mask(body, stand)) & _zone(slot, stand) & (np.asarray(base)[..., 3] > 0)
        targets.append(target)
    sheet, origins = _sheet(images, scale, len(images), crop=crop)
    sheet.save(folder / "sheet.png")
    cells = "; ".join(f"cell {k + 1}{' (left)' if k == 0 else ''}: {c['ask']}" for k, c in enumerate(slot["cells"]))
    drawn = _draw(CELLS_PROMPT.format(n=len(images), what=slot["what"], desc=_design(slot, design)["desc"], fit=slot["fit"],
                                      cells=cells, style=STYLE.format(px=scale), w=sheet.width, h=sheet.height),
                  folder, folder / "sheet.png", [], "drawn")
    pieces, palettes, shifts = {}, [], []
    combined = np.zeros((CANVAS, CANVAS, 4), np.uint8)
    for cell, origin, image, target in zip(slot["cells"], origins, images, targets):
        art, palette = _clean_native(drawn, folder / "sheet.png", origin, scale, crop=crop, colors=16)
        art = np.asarray(art).copy()
        for c in _components(art[..., 3] > 0):
            if c.sum() < 4:
                art[c] = 0
        art, shift = _register(art, target, np.asarray(image)[..., 3] > 0)
        palettes.append(palette), shifts.append(list(shift))
        bones = cell["bones"]
        owner = _owners(art, body, stand, bones) if len(bones) > 1 else np.where(art[..., 3] > 0, bones[0], "")
        for n in bones:
            if (owner == n).any():
                pieces[n] = np.where((owner == n)[..., None], art, 0).astype(np.uint8)
        put = art[..., 3] > 0
        combined[put] = art[put]
    return combined, pieces, np.unique(np.concatenate(palettes), axis=0), shifts


def design_part(out, slot_name, design):
    """The one model call per design: the part alone, drawn over a close-up of the base, reduced to native
    pixels with its own palette and registered onto the base."""
    slot = SLOTS[slot_name]
    info = json.loads((out / "body.json").read_text())
    body, stand = info["body"], info["pose"]
    folder = out / "parts" / slot_name / design
    folder.mkdir(parents=True, exist_ok=True)
    base = Image.open(out / "base.png").convert("RGBA")
    base_owner = _owners(base, body, stand)
    target = _target(slot, base_owner, stand)
    ys, xs = np.nonzero(target)
    m = slot["margin"]
    spec = _design(slot, design)
    crop = (max(int(xs.min()) - m, 0), max(int(ys.min()) - m, 0), min(int(xs.max()) + m + 1, CANVAS),
            min(int(ys.max()) + m + 1 + spec.get("below", 0), CANVAS))
    scale = int(np.clip(768 // max(crop[2] - crop[0], crop[3] - crop[1]), 4, 16))
    core = _target(dict(slot, bones=slot["core"], zone=slot.get("core_zone", slot.get("zone"))), base_owner, stand)
    prompt = PART_PROMPT.format(view=slot["view"], what=slot["what"], desc=spec["desc"],
                                fit=(slot["fit"] + " " + spec.get("fit", "")).strip(), style=STYLE.format(px=scale))
    ear = _drawn_ear(base, body, stand) if spec.get("show_ear") else _ear_mask(body, stand)
    if spec.get("show_ear"):
        prompt += (" The thin cyan ring in image 1 goes round the ear: leave everything inside it uncovered (the hair "
                   "goes round and behind it), and do not draw the cyan ring.")
    rounds, best = [], None
    if slot.get("cells"):
        part, pieces, palette, shift = _design_cells(out, slot, design, folder, base, base_owner, body, stand, crop, scale)
        covered = float(((part[..., 3] > 0) & core).sum() / max(core.sum(), 1))
        rounds.append({"round": 1, "core": round(covered, 2), "why": ""})
        best = (covered, part, palette, shift)
    for k in range(0 if slot.get("cells") else 3):  # a first drawing, then up to two redraws over the outlined core
        name = "sheet" if k == 0 else f"sheet-{k + 1}"
        guide = np.asarray(base).copy()
        if k > 0:
            guide[_dilate(core, 1) & ~core] = (*CYAN, 255)
        if spec.get("show_ear"):  # a cyan ring round the ear marks it as kept clear
            ring = _dilate(ear, 1) & ~ear
            guide[ring] = (*CYAN, 255)
        image = Image.fromarray(guide)
        if k > 0:
            prompt += REDO_NOTE.format(why=rounds[-1]["why"], what=slot["what"])
        sheet, origins = _sheet([image], scale, 1, crop=crop, gutter=0)
        sheet.save(folder / f"{name}.png")
        drawn = _draw(prompt, folder, folder / f"{name}.png", [], "drawn" if k == 0 else f"drawn-{k + 1}")
        if slot.get("reduce") == "strands":  # read on the drawing's own grid; gaps filled, the outer outline only
            part, _ = _clean_native(drawn, folder / f"{name}.png", origins[0], scale, crop=crop, colors=24,
                                    outline=False)
            part, palette = _tidy_strands(part, colors=24)
        else:
            part, palette = _clean_native(drawn, folder / f"{name}.png", origins[0], scale, crop=crop, colors=16)
        part = np.asarray(part).copy()
        for c in _components(part[..., 3] > 0):
            if c.sum() < 4:  # specks
                part[c] = 0
        part, shift = _register(part, target, np.asarray(base)[..., 3] > 0, core=core)
        covered = float(((part[..., 3] > 0) & core).sum() / max(core.sum(), 1))
        why = "too small, away from the body" if covered < 0.8 else ""
        if not why and slot.get("whole") and slot["bones"][0].endswith("_foot"):  # a shoe against its bare foot
            split = _owners(part, body, stand, slot["bones"], True)
            for bone in slot["bones"]:
                c = split == bone
                if not c.any():
                    why, covered = f"no {bone.replace('_', ' ')} piece", min(covered, 0.79)
                    break
                L = stand["legs"][bone.split("_")[0]]  # the skeleton's foot: labels spill where the feet overlap
                ratio = (np.ptp(np.nonzero(c.any(0))[0]) + 1) / (abs(L["toe"][0] - L["heel"][0]) + 1)
                if not 0.95 <= ratio <= 1.4:
                    why = f"{'too short' if ratio < 0.95 else 'too long'} against the foot ({ratio:.2f}x)"
                    covered = min(covered, 0.79)
        rounds.append({"round": k + 1, "core": round(covered, 2), "why": why})
        if best is None or covered > best[0]:
            best = (covered, part, palette, shift)
        if not why:
            break
    _, part, palette, shift = best  # the round that covered most, not merely the last
    if slot.get("rim"):
        part = _rim(part, base, core, slot["rim"])
    if spec.get("show_ear"):
        part = _clear_ear(part, ear)
    if slot.get("reduce") == "strands":
        part = _outline_against(part, np.asarray(base)[..., 3] > 0)
    if not slot.get("cells"):
        pieces = _cut_on_body(part, base_owner, body, stand, slot)
    if slot.get("fill") and "upper" in pieces:
        pieces["upper"] = _fill_rows(pieces["upper"], slot, body, stand, base)
    rebuilt = _far_from_near(pieces, base, body, stand)
    alpha = part[..., 3] > 0
    body_alpha = np.asarray(base)[..., 3] > 0
    report = {"slot": slot_name, "design": design, "shift": shift if isinstance(shift, list) else list(shift), "rounds": rounds, "far_from_near": rebuilt,
              "covers": round(float((alpha & target).sum() / max(target.sum(), 1)), 2),
              "off_body": round(float((alpha & ~_dilate(body_alpha, 3)).sum() / max(alpha.sum(), 1)), 2),
              "pieces": {n: int((p[..., 3] > 0).sum()) for n, p in pieces.items()}}
    if slot.get("whole"):  # a shoe against its bare foot
        report["length"] = {n: round(float(np.ptp(np.nonzero(p[..., 3].any(0))[0]) + 1) /
                                     max(float(np.ptp(np.nonzero((base_owner == n).any(0))[0]) + 1), 1), 2)
                            for n, p in pieces.items()}
    Image.fromarray(part).save(folder / "design.png")
    for n, p in pieces.items():
        Image.fromarray(p).save(folder / f"piece-{n}.png")
    (folder / "design.json").write_text(json.dumps(dict(report, palette=palette.tolist()), indent=1))
    return report


def _fit(canvas, owner, frame, body, stand, pose, reach=2):
    """Each posed bone piece moved by up to `reach` whole pixels onto that bone in the frame as drawn: the
    model's mended body sits a pixel or two off the skeleton, and a garment that follows only the skeleton
    leaves skin showing at its edges."""
    drawn = _owners(frame, body, pose)
    figure = frame[..., 3] > 0
    depth = {n: v[2] for n, v in _bones(stand).items()} | {"upper": 1.5}
    out = np.zeros_like(canvas)
    out_owner = np.full(owner.shape, "", dtype=object)
    for bone in sorted(set(owner[owner != ""].tolist()), key=depth.get):
        mine = owner == bone
        target = drawn == bone
        def score(d):
            m = np.roll(np.roll(mine, d[1], 0), d[0], 1)
            return int((m & target).sum()) - 0.5*int((m & ~figure).sum()), -abs(d[0]) - abs(d[1])
        dx, dy = max(((dx, dy) for dy in range(-reach, reach + 1) for dx in range(-reach, reach + 1)), key=score) \
            if target.any() and bone != "upper" else (0, 0)  # the upper body is the base's own, already exact
        layer = np.roll(np.roll(np.where(mine[..., None], canvas, 0), dy, 0), dx, 1)
        put = layer[..., 3] > 0
        out[put], out_owner[put] = layer[put], bone
    return out, out_owner


FACE_PROMPT = (
    "Image 1 is a pixel-art sheet: every grid cell shows the same bald, blank-faced head of one character, pure side "
    "view facing right, on a flat pure magenta #FF00FF background. In each cell draw ONLY the face, {fit} The cells, "
    "left to right then top to bottom, show these expressions: {faces}. The face sits on the front of the head turned "
    "toward the viewer, at the same size and place and in the same style in every cell; only the expression changes. "
    "Both eyes are whole and well inside the head's outline - the far eye a little narrower, never touching or cut "
    "by the outline. "
    "Keep the head's dark outline exactly where it is in every cell (it aligns the layers), and turn every skin pixel "
    "of the head into flat #FF00FF: a cell shows only that outline and the face. {style} Keep every cell exactly in "
    "place; keep this {w}x{h} image and its grid exactly.")


def _block_reduce(drawn_path, sheet_path, origin, scale, crop, share=0.12):
    """One drawn cell to native pixels block by block: a block takes the commonest color of its drawn pixels
    once they cover `share` of it. Thin strokes (eye lines, brows) survive, which reading one pixel per
    block loses; the cell's own grid places every pixel, no bounding box."""
    drawn = Image.open(drawn_path).convert("RGBA")
    ratio = drawn.width / Image.open(sheet_path).width
    w, h = crop[2] - crop[0], crop[3] - crop[1]
    step = scale*ratio
    cell = np.asarray(drawn).astype(int)
    key = (np.abs(cell[..., :3] - np.array(KEY)).max(-1) <= 60) | (cell[..., 3] < 128)
    out = np.zeros((CANVAS, CANVAS, 4), np.uint8)
    def box(i, j):
        return (int(round(origin[0]*ratio + i*step)), int(round(origin[1]*ratio + j*step)),
                int(round(origin[0]*ratio + (i + 1)*step)), int(round(origin[1]*ratio + (j + 1)*step)))
    cover = np.zeros((h, w))
    for j in range(h):
        for i in range(w):
            x0, y0, x1, y1 = box(i, j)
            e = key[y0:y1, x0:x1]
            cover[j, i] = (~e).mean() if e.size else 0
    # A thin stroke straddling two blocks covers both a little: only the block it covers most is ink (or a block
    # well covered on its own) - else every eye line and brow came out a pixel thicker.
    padded = np.pad(cover, 1)
    neighbors = np.max([padded[1 + dy:1 + dy + h, 1 + dx:1 + dx + w] for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                        if dy or dx], axis=0)
    inked = (cover >= 0.4) | ((cover >= share) & (cover >= 0.7*neighbors))
    for j in range(h):
        for i in range(w):
            x0, y0, x1, y1 = box(i, j)
            block, empty = cell[y0:y1, x0:x1, :3].reshape(-1, 3), key[y0:y1, x0:x1].reshape(-1)
            if not inked[j, i] or block.size == 0 or not (0 <= crop[1] + j < CANVAS and 0 <= crop[0] + i < CANVAS):
                continue
            ink = block[~empty]
            codes = (ink[:, 0] >> 4)*256 + (ink[:, 1] >> 4)*16 + (ink[:, 2] >> 4)
            values, counts = np.unique(codes, return_counts=True)
            out[crop[1] + j, crop[0] + i] = (*ink[codes == values[counts.argmax()]].mean(0).astype(np.uint8), 255)
    return out


def design_faces(out, slot_name):
    """Every expression of a face slot in ONE model call (one cell each, so they share one hand), each cell
    aligned by the head outline the model was told to keep, then only the face inside the head kept."""
    slot = SLOTS[slot_name]
    info = json.loads((out / "body.json").read_text())
    body, stand = info["body"], info["pose"]
    folder = out / "parts" / slot_name
    folder.mkdir(parents=True, exist_ok=True)
    base = Image.open(out / "base.png").convert("RGBA")
    rgba = np.asarray(base)
    owner = _owners(base, body, stand)
    head = (owner == "upper") & _zone(slot, stand)
    ring = head & _dilate(~head, 1)
    inside = head & ~_dilate(~head, 2)  # the head's skin, for the guard against painted skin
    ys, xs = np.nonzero(head)
    m = slot["margin"]
    crop = (int(xs.min()) - m, int(ys.min()) - m, int(xs.max()) + m + 1, int(ys.max()) + m + 1)
    names = list(slot["designs"])
    scale = int(np.clip(400 // max(crop[2] - crop[0], crop[3] - crop[1]), 4, 12))
    sheet, origins = _sheet([base]*len(names), scale, 2, crop=crop)
    sheet.save(folder / "sheet.png")
    faces = "; ".join(f"{k + 1} {n} ({slot['designs'][n]})" for k, n in enumerate(names))
    drawn = _draw(FACE_PROMPT.format(fit=slot["fit"], faces=faces, style=STYLE.format(px=scale), w=sheet.width,
                                     h=sheet.height), folder, folder / "sheet.png", [], "drawn")
    skin = rgba[inside][:, :3].astype(int)
    report = {}
    for name, origin in zip(names, origins):
        art = _block_reduce(drawn, folder / "sheet.png", origin, scale, crop)
        palette = np.unique(art[art[..., 3] > 0][:, :3], axis=0)
        luma = art[..., :3].astype(float) @ np.array([0.299, 0.587, 0.114])
        dark = (art[..., 3] > 0) & (luma < 90)
        band = _dilate(ring, 1)
        def score(d):
            moved = np.roll(np.roll(dark, d[1], 0), d[0], 1)
            return int((moved & ring).sum()) + int((moved & band).sum()), -abs(d[0]) - abs(d[1])
        d = max(((dx, dy) for dy in range(-5, 6) for dx in range(-5, 6)), key=score)
        art = np.roll(np.roll(art, d[1], 0), d[0], 1)
        # A three-quarter face reaches the outline (the far eye): keep what is on the outline unless it only
        # restates the head's own outline there.
        dark = (art[..., 3] > 0) & (art[..., :3].astype(float) @ np.array([0.299, 0.587, 0.114]) < 90)
        restated = np.zeros_like(dark)
        for c in _components(dark & band):  # the model's copy of the outline: long strokes along it
            if c.sum() >= 12:
                restated |= c
        face = (art[..., 3] > 0) & head & ~restated & ~(band & ~head)
        if face.sum() > 0.35*inside.sum():  # skin painted after all: drop what is the head's own skin
            near_skin = np.abs(art[..., None, :3].astype(int) - skin[None, None]).max(-1).min(-1) <= 24
            face &= ~near_skin
        for c in _components(face):
            if c.sum() < 2:
                face &= ~c
        piece = np.where(face[..., None], art, 0).astype(np.uint8)
        target = folder / name
        target.mkdir(exist_ok=True)
        Image.fromarray(piece).save(target / "design.png")
        Image.fromarray(piece).save(target / "piece-upper.png")
        report[name] = {"slot": slot_name, "design": name, "shift": list(d), "outline_hits": score(d)[0],
                        "ring": int(ring.sum()), "pixels": int(face.sum())}
        (target / "design.json").write_text(json.dumps(dict(report[name], palette=palette.tolist()), indent=1))
    return report


MEND_PROMPT = (
    "Image 1 is a pixel-art sheet: each grid cell shows the same character {verb}, pure side view facing right, on a "
    "flat pure magenta #FF00FF background, wearing {what} that was cut apart and moved onto the pose - so it has seams, "
    "gaps, jagged edges and broken lines at the shoulders, elbows, hips and knees. Image 2 is that character standing "
    "in {what} as designed.\n\nIn every cell redraw ONLY {what}, clean, as one well-drawn piece of clothing on that "
    "pose: close the seams, smooth the jagged edges, keep the design, colors, pattern, folds, outline and shading of "
    "image 2, and keep it exactly where and as big as it is in the cell. Draw nothing else - no skin, no head, no hands, "
    "no feet, no shoes, no body - every other pixel is flat #FF00FF. {style} Keep every figure exactly in its own "
    "cell; keep this {w}x{h} image and its grid exactly.")


def _spread_owner(owner, mask, steps=4):
    """Bone names carried onto new pixels from the nearest named ones."""
    owner = owner.copy()
    for _ in range(steps):
        empty = mask & (owner == "")
        if not empty.any():
            break
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            moved = np.roll(np.roll(owner, dy, 0), dx, 1)
            take = empty & (owner == "") & (moved != "")
            owner[take] = moved[take]
    return owner


def stage_mend(base_dir, slot_name, design):
    """A posed garment redrawn clean by the model, one sheet per cycle (all its frames at once, the body
    under each for context), then put back where the skeleton put it: registered onto the posed layer,
    only pixels on or next to it kept, whatever the model dropped kept from the posed layer, each pixel's
    body node from the posed layer. A frame the model drew badly (under 85% of the posed layer) stays posed."""
    out = _out(base_dir)
    slot = SLOTS[slot_name]
    folder = out / "parts" / slot_name / design
    palette = np.array(json.loads((folder / "design.json").read_text())["palette"])
    standing = Image.open(out / "base.png").convert("RGBA")
    standing.alpha_composite(Image.open(folder / "design.png").convert("RGBA"))
    standing.save(folder / "standing.png")
    nodes = sorted(set(slot["nodes"].values()))
    report = {}
    for cycle_name in CYCLES:
        path = out / cycle_name / "skeleton.json"
        work = out / cycle_name / slot_name / design
        if not path.exists() or not work.exists() or CYCLES[cycle_name].get("rigid"):
            continue  # a rigid cycle's garment is the design moved by whole pixels: nothing to mend
        n = len(json.loads(path.read_text())["poses"])
        roughs, owners, cells = [], [], []
        mend = work / "mend"
        for t in range(n):  # the posed layer: kept aside the first time, so mending again starts from it
            for name in [f"{t}"] + [f"{t}-{node}" for node in nodes]:
                if not (mend / f"rough-{name}.png").exists():
                    mend.mkdir(exist_ok=True)
                    Image.open(work / f"{name}.png").save(mend / f"rough-{name}.png")
            rough = np.asarray(Image.open(mend / f"rough-{t}.png").convert("RGBA"))
            owner = np.full(rough.shape[:2], "", dtype=object)
            for node in nodes:
                owner[np.asarray(Image.open(mend / f"rough-{t}-{node}.png"))[..., 3] > 0] = node
            dressed = Image.open(out / cycle_name / f"frame-{t}.png").convert("RGBA")
            dressed.alpha_composite(Image.fromarray(rough))
            roughs.append(rough), owners.append(owner), cells.append(dressed)
        sheet, origins = _sheet(cells, 4, 3)
        sheet.save(mend / "sheet.png")
        drawn = _draw(MEND_PROMPT.format(verb=CYCLES[cycle_name]["verb"], what=slot["what"], style=STYLE.format(px=4),
                                         w=sheet.width, h=sheet.height), mend, mend / "sheet.png", [folder / "standing.png"],
                      "drawn")
        report[cycle_name] = []
        for t, (origin, rough, owner) in enumerate(zip(origins, roughs, owners)):
            ra = rough[..., 3] > 0
            box = Image.fromarray(rough).getbbox()
            clean, _ = _clean_native(drawn, mend / "sheet.png", origin, 4, palette=palette,
                                     expect=(box[2] - box[0], box[3] - box[1]))
            clean = np.asarray(clean)
            ca = clean[..., 3] > 0
            if ca.any():  # the model moves a whole cell's drawing up to ~10 px: centroids first, then the outline
                (cy, cx), (ry, rx) = np.argwhere(ca).mean(0), np.argwhere(ra).mean(0)
                coarse = (int(round(rx - cx)), int(round(ry - cy)))
                clean = np.roll(np.roll(clean, coarse[1], 0), coarse[0], 1)
            else:
                coarse = (0, 0)
            clean, fine = _onto_guide(Image.fromarray(clean), Image.fromarray(rough), reach=3)
            shift = (coarse[0] + fine[0], coarse[1] + fine[1])
            clean = np.asarray(clean)
            keep = (clean[..., 3] > 0) & _dilate(ra, 1)
            covered = float((keep & ra).sum() / max(ra.sum(), 1))
            if covered < 0.85:
                report[cycle_name].append({"frame": t, "covered": round(covered, 2), "kept": "posed"})
                continue
            image = np.where(keep[..., None], clean, 0).astype(np.uint8)
            dropped = ra & ~_dilate(keep, 1)
            image[dropped] = rough[dropped]
            alpha = image[..., 3] > 0
            node_of = _spread_owner(np.where(alpha, owner, ""), alpha)
            image, node_of = _despeckle(image, node_of)
            Image.fromarray(image).save(work / f"{t}.png")
            for node in nodes:
                Image.fromarray(np.where((node_of == node)[..., None], image, 0).astype(np.uint8)).save(work / f"{t}-{node}.png")
            report[cycle_name].append({"frame": t, "covered": round(covered, 2), "shift": list(shift), "kept": "mended"})
    (folder / "mend.json").write_text(json.dumps(report, indent=1))
    return report


ANGLE_PROMPT = (
    "Image 1 is a pixel-art sheet: each grid cell shows the same {one} on a flat pure magenta #FF00FF background, "
    "turned to a different angle by rotating the pixels - so its outline and details are jagged. Image 2 is that shoe "
    "as designed, on the character's foot.\n\nIn every cell redraw the shoe cleanly at exactly that cell's angle, "
    "place and size: the same design, colors, outline and shading as image 2, crisp one-pixel outline, the sole a "
    "clean band along the bottom of the shoe. Draw only the shoe; every other pixel stays flat #FF00FF. {style} Keep "
    "every cell exactly in place; keep this {w}x{h} image and its grid exactly.")


def _turn_of(pose, stand, bone, master):
    """How far a pose turns `bone` from the standing `master` bone, in degrees."""
    a, b = _bones(pose)[bone][:2]
    a0, b0 = _bones(stand)[master][:2]
    return float(np.degrees(_angle(a, b) - _angle(a0, b0)) + 180) % 360 - 180


def _bone_turns(out, stand, bones, master):
    """Every angle the poses turn the slot's bones to, against the standing master bone."""
    turns = []
    for cycle_name in CYCLES:
        path = out / cycle_name / "skeleton.json"
        if path.exists():
            for pose in json.loads(path.read_text())["poses"]:
                turns += [_turn_of(pose, stand, b, master) for b in bones]
    return turns


def design_angles(out, slot_name, design):
    """A rigid part redrawn by the model at every angle the poses need, `angles` degrees apart (one call,
    one cell per angle, the roughly turned design as the guide): each frame then places the nearest
    drawing by translation only, so nothing is rotated per frame and the small drawing stays crisp."""
    slot = SLOTS[slot_name]
    info = json.loads((out / "body.json").read_text())
    stand = info["pose"]
    folder = out / "parts" / slot_name / design
    work = folder / "angles"
    work.mkdir(exist_ok=True)
    near = slot["bones"][0]  # the near piece is the master; the far one is the same drawing, shaded
    master = np.asarray(Image.open(folder / f"piece-{near}.png").convert("RGBA"))
    palette = np.array(json.loads((folder / "design.json").read_text())["palette"])
    ankle = np.array(_bones(stand)[near][0], float)  # the bone's root (ankle, wrist): every drawing turns about it
    step = slot["angles"]
    bins = sorted({int(round(t / step))*step for t in _bone_turns(out, stand, slot["bones"], near)} | {0})
    up = _upsampled(master)
    roughs = {b: _warp(up, np.radians(b), ankle, ankle) for b in bins}
    boxes = np.array([Image.fromarray(r).getbbox() for r in roughs.values()])
    crop = (max(int(boxes[:, 0].min()) - 3, 0), max(int(boxes[:, 1].min()) - 3, 0),
            min(int(boxes[:, 2].max()) + 3, CANVAS), min(int(boxes[:, 3].max()) + 3, CANVAS))
    scale = int(np.clip(300 // max(crop[2] - crop[0], crop[3] - crop[1]), 6, 14))
    sheet, origins = _sheet([Image.fromarray(roughs[b]) for b in bins], scale, 4, crop=crop)
    sheet.save(work / "sheet.png")
    design_view = Image.open(out / "base.png").convert("RGBA")
    design_view.alpha_composite(Image.open(folder / "design.png").convert("RGBA"))
    box = Image.open(folder / "design.png").getbbox()
    near_box = Image.fromarray(master).getbbox()
    box = (min(box[0], near_box[0]), min(box[1], near_box[1]), max(box[2], near_box[2]), max(box[3], near_box[3]))
    design_view.crop((box[0] - 4, box[1] - 8, box[2] + 4, box[3] + 3)).resize(
        ((box[2] - box[0] + 8)*scale, (box[3] - box[1] + 11)*scale), Image.Resampling.NEAREST).save(work / "design.png")
    drawn = _draw(ANGLE_PROMPT.format(one=slot["one"], style=STYLE.format(px=scale), w=sheet.width, h=sheet.height),
                  work, work / "sheet.png", [work / "design.png"], "drawn")
    dark = [int(v) for v in min(palette.tolist(), key=lambda c: 299*c[0] + 587*c[1] + 114*c[2])]
    report = {}
    for b, origin in zip(bins, origins):
        rough = roughs[b]
        ra = rough[..., 3] > 0
        art = _block_reduce(drawn, work / "sheet.png", origin, scale, crop)
        alpha = art[..., 3] > 0
        parts = _components(alpha, 4)
        alpha = np.any(parts, 0) if parts else alpha
        art = np.where(alpha[..., None], art, 0).astype(np.uint8)
        if alpha.any():
            art = np.asarray(pixel_grid.snap(Image.fromarray(art), palette))
            (cy, cx), (ry, rx) = np.argwhere(alpha).mean(0), np.argwhere(ra).mean(0)
            art = np.roll(np.roll(art, int(round(ry - cy)), 0), int(round(rx - cx)), 1)
            art = np.asarray(_onto_guide(Image.fromarray(art), Image.fromarray(rough), reach=2)[0])
            art = np.asarray(pixel_grid.silhouette_outline(Image.fromarray(art), dark))
        aa = art[..., 3] > 0
        covered = float((aa & ra).sum() / max(ra.sum(), 1))
        spill = float((aa & ~_dilate(ra, 1)).sum() / max(aa.sum(), 1))
        kept = "drawn" if covered >= 0.75 and spill <= 0.25 else "turned"
        Image.fromarray(art if kept == "drawn" else rough).save(work / f"{b}.png")
        report[b] = {"covered": round(covered, 2), "spill": round(spill, 2), "kept": kept}
    (work / "angles.json").write_text(json.dumps({"pivot": ankle.tolist(), "angles": report}, indent=1))
    return report


def _far_shade_of(out):
    """How much darker the base draws a far leg than the near one."""
    info = json.loads((out / "body.json").read_text())
    rgba = np.asarray(Image.open(out / "base.png").convert("RGBA"))
    owner = _owners(rgba, info["body"], info["pose"]).astype(str)
    luma = rgba[..., :3].astype(float) @ np.array([0.299, 0.587, 0.114])
    near, far = np.isin(owner, ["near_thigh", "near_shin"]), np.isin(owner, ["far_thigh", "far_shin"])
    return float(luma[far].mean() / luma[near].mean()) if near.any() and far.any() else 0.8


def pose_angles(out, slot_name, design, pose):
    """A rigid pair (feet, hands) of a pose from the angle drawings: the nearest angle, moved so its root is the
    bone's (no rotation), kept out of the ground; the far one shaded as the far limbs are."""
    folder = out / "parts" / slot_name / design / "angles"
    meta = json.loads((folder / "angles.json").read_text())
    info = json.loads((out / "body.json").read_text())
    stand, ground = info["pose"], info["body"]["ground"]
    pivot = np.array(meta["pivot"])
    bins = np.array(sorted(int(b) for b in meta["angles"]))
    canvas = np.zeros((CANVAS, CANVAS, 4), np.uint8)
    owner = np.full((CANVAS, CANVAS), "", dtype=object)
    shade = _far_shade_of(out)
    bones = SLOTS[slot_name]["bones"]
    for bone in sorted(bones, key=lambda n: not n.startswith("far")):
        side = bone.split("_")[0]
        turn = _turn_of(pose, stand, bone, bones[0])
        b = int(bins[np.abs(bins - turn).argmin()])
        image = np.asarray(Image.open(folder / f"{b}.png").convert("RGBA")).copy()
        dx, dy = (int(v) for v in np.round(np.array(_bones(pose)[bone][0]) - pivot))
        image = np.roll(np.roll(image, dy, 0), dx, 1)
        rows = np.nonzero(image[..., 3].any(1))[0]
        if len(rows) and rows.max() > ground:
            image = np.roll(image, -(int(rows.max()) - ground), 0)
        if side == "far":
            image[..., :3] = np.clip(image[..., :3]*shade, 0, 255).astype(np.uint8)
        put = image[..., 3] > 0
        canvas[put], owner[put] = image[put], bone
    return canvas, owner


def _despeckle(canvas, owner, minimum=6):
    """Islands of a few pixels cut off from the rest of a posed part (a turned piece's stray corner) dropped."""
    alpha = canvas[..., 3] > 0
    for c in _components(alpha):
        if c.sum() < minimum:
            canvas[c], owner[c] = 0, ""
    return canvas, owner


def stage_part(base_dir, slot_name, design):
    """A part on every frame: designed once, its bone pieces posed as the body's are, split by body node."""
    out = _out(base_dir)
    slot = SLOTS[slot_name]
    info = json.loads((out / "body.json").read_text())
    body, stand = info["body"], info["pose"]
    folder = out / "parts" / slot_name / design
    if slot.get("sheet") and not (folder / "design.json").exists():
        design_faces(out, slot_name)
    report = design_part(out, slot_name, design) if not (folder / "design.json").exists() else \
        {k: v for k, v in json.loads((folder / "design.json").read_text()).items() if k != "palette"}
    pieces = {p.stem[len("piece-"):]: np.asarray(Image.open(p).convert("RGBA")) for p in folder.glob("piece-*.png")}
    ground = body["ground"] if slot.get("whole") else None
    if slot.get("angles"):
        if not (folder / "angles" / "angles.json").exists():
            report["angles"] = design_angles(out, slot_name, design)
        else:
            report["angles"] = json.loads((folder / "angles" / "angles.json").read_text())["angles"]
    frames = {}
    for cycle_name in CYCLES:
        path = out / cycle_name / "skeleton.json"
        if not path.exists():
            continue
        work = out / cycle_name / slot_name / design
        work.mkdir(parents=True, exist_ok=True)
        for stale in (work / "mend").glob("rough-*.png"):  # posed again: a mend starts from this pose
            stale.unlink()
        for t, pose in enumerate(json.loads(path.read_text())["poses"]):
            canvas, owner = pose_angles(out, slot_name, design, pose) if slot.get("angles") else \
                pose_pieces(pieces, body, stand, pose, ground)
            frame = np.asarray(Image.open(out / cycle_name / f"frame-{t}.png").convert("RGBA"))
            canvas, owner = _fit(canvas, owner, frame, body, stand, pose)
            canvas[body["ground"] + 1:], owner[body["ground"] + 1:] = 0, ""
            canvas, owner = _despeckle(canvas, owner)
            Image.fromarray(canvas).save(work / f"{t}.png")
            for node in sorted(set(slot["nodes"].values())):
                mask = np.isin(owner.astype(str), [b for b in pieces if _slot_node(slot, b) == node])
                Image.fromarray(np.where(mask[..., None], canvas, 0).astype(np.uint8)).save(work / f"{t}-{node}.png")
        frames[cycle_name] = t + 1
    return dict(report, frames=frames)


def stage_dress(base_dir, looks=None):
    """Every frame dressed in each look (body frame under its parts, back to front by node), one sheet."""
    import sd_avatar_process as avatar
    out = _out(base_dir)
    looks = looks or _looks(out)
    rows = []
    for cycle_name in CYCLES:
        path = out / cycle_name / "skeleton.json"
        if not path.exists():
            continue
        poses = json.loads(path.read_text())["poses"]
        body_info = json.loads((out / "body.json").read_text())["body"]
        for t, pose in enumerate(poses):
            row = []
            for look in looks.values():
                frame = np.asarray(Image.open(out / cycle_name / f"frame-{t}.png").convert("RGBA")).copy()
                layers = []
                for slot_name, design in look.items():
                    for node in set(SLOTS[slot_name]["nodes"].values()):
                        file = out / cycle_name / slot_name / design / f"{t}-{node}.png"
                        if file.exists():
                            layers.append((avatar.LAYERS.index(node), np.asarray(Image.open(file).convert("RGBA"))))
                        if SLOTS[slot_name].get("replaces"):
                            owner = _body_owner(frame, body_info, pose)
                            frame[np.isin(owner.astype(str), SLOTS[slot_name]["bones"])] = 0
                canvas = frame
                body_at = avatar.LAYERS.index("root")
                front = None
                for k, layer in sorted(layers, key=lambda l: l[0]):
                    put = layer[..., 3] > 0
                    if k < body_at:  # a far-side piece: behind the torso and the near limbs, as the runtime has it
                        if front is None:
                            front = ~np.char.startswith(_owners(frame, body_info, pose).astype(str), "far") & \
                                (frame[..., 3] > 0)
                        put &= ~front
                    canvas = np.where(put[..., None], layer, canvas)
                row.append(Image.fromarray(canvas.astype(np.uint8)))
            rows.append(row)
    crop, scale = (24, 4, 104, 124), 3
    w, h = (crop[2]-crop[0])*scale, (crop[3]-crop[1])*scale
    sheet = Image.new("RGBA", (w*len(rows), h*len(looks)), (236, 236, 236, 255))
    for c, row in enumerate(rows):
        for r, image in enumerate(row):
            sheet.alpha_composite(image.crop(crop).resize((w, h), Image.Resampling.NEAREST), (c*w, r*h))
    sheet.save(out / "dressed.png")
    return {"looks": list(looks), "frames": len(rows), "sheet": str(out / "dressed.png")}


def _looks(out):
    """bare, then one full outfit per design index (the first design of every slot, the second, ...)."""
    made = {s: [d for d in slot["designs"] if (out / "parts" / s / d / "design.json").exists()] for s, slot in SLOTS.items()}
    looks = {"bare": {}}
    for k in range(max((len(v) for v in made.values()), default=0)):
        looks[f"look{k + 1}"] = {s: v[k % len(v)] for s, v in made.items() if v}  # short slots cycle
    return looks


# --- body segments: which piece each pixel is, from the skeleton -------------------------------------------
# Body pieces back to front, each a label color; the feet are pieces of their own so footwear can
# replace them (a shoe hides the whole bare foot, toes included, whatever its outline).
SEGMENTS = {"arm_back": "#00a000", "hand_back": "#005000", "leg_back": "#00ffff", "foot_back": "#0000ff",
            "root": "#ffff00", "leg_front": "#ff8000", "foot_front": "#ff0000",
            "arm_front": "#ffffff", "hand_front": "#a0a0a0"}
SEGMENT_OF = {"upper": "root"}  # bone -> body piece node
for _side, _node in (("far", "back"), ("near", "front")):
    SEGMENT_OF |= {f"{_side}_thigh": f"leg_{_node}", f"{_side}_shin": f"leg_{_node}", f"{_side}_foot": f"foot_{_node}",
                   f"{_side}_upper_arm": f"arm_{_node}", f"{_side}_forearm": f"arm_{_node}", f"{_side}_hand": f"hand_{_node}"}


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
            owner = _body_owner(pixels, body, pose)
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
    """Each part design as a pack: one piece per body node of its slot, one full-canvas drawing per sample,
    moved as the motion build moved its clip."""
    from sprite_gen.frames import parts_import
    from sprite_gen.spec import parts_assets as assets, parts_workspace as workspace
    out = _out(base_dir)
    assembly = (out / "library/assembly.json").absolute()
    mapping = json.loads((out / "library-map.json").read_text())
    index, registry = workspace.read_workspace(assembly)
    motion = registry[index["motion"]["id"]]
    rig = registry[index["rig"]["id"]]
    made = {}
    import sd_avatar_process as avatar
    for slot_name, slot in SLOTS.items():
        nodes = sorted(set(slot["nodes"].values()))
        socket = avatar.SLOTS[slot_name][3][0]  # the slot's socket; a full-canvas drawing's pivot is where it is
        for design in slot["designs"]:
            part_id = f"{prefix}.{slot_name}.{design}"
            if part_id in registry or not (out / "parts" / slot_name / design / "design.json").exists():
                continue
            src = out / "packs" / slot_name / design
            src.mkdir(parents=True, exist_ok=True)
            pieces = {node: {} for node in nodes}
            for clip in motion["clips"]:
                state = clip["state"]
                dx, dy = mapping["shifts"][state]
                for k, sample in enumerate(clip["samples"]):
                    t = mapping["starts"][state][k]
                    for node in nodes:
                        file = out / state / slot_name / design / f"{t}-{node}.png"
                        image = np.roll(np.roll(np.asarray(Image.open(file).convert("RGBA")), dy, 0), dx, 1)
                        name = f"{state}-{t}-{node}.png"
                        Image.fromarray(image).save(src / name)
                        ref = {"file": name, "sha256": assets.sha256((src / name).read_bytes())}
                        pieces[node].setdefault(clip["id"], {})[sample["id"]] = {
                            "image": ref, "format": "full-canvas", "pivot": sample["sockets"][socket]["position"],
                            "target_socket": socket, "visible": bool((image[..., 3] > 0).any())}
            replaces = slot.get("replaces", False)
            spec = {"kind": "parts-pack-import", "schema_version": 1, "id": part_id, "design_id": f"{prefix}.{design}",
                    "role": "part", "body_mode": "segmented", "style": rig["style"], "rig": index["rig"],
                    "motion": index["motion"], "view": rig["view"], "binding_id": f"{part_id}.binding",
                    "occupies": [slot_name],
                    "pieces": [{"id": f"{slot_name}_{node}", "slot": slot_name, "node": node, "mode": "raster",
                                **({"provides": [node], "replaces": [node]} if replaces else {}),
                                "sample_map": pieces[node]} for node in nodes]}
            (src / "import.json").write_text(json.dumps(spec, indent=1))
            parts_import.import_pack(assembly, src / "import.json", src)
            made[part_id] = len(nodes)
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
    looks = {name: {s: f"{prefix}.{s}.{d}" for s, d in look.items() if f"{prefix}.{s}.{d}" in registry}
             for name, look in _looks(out).items()}
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
    parser.add_argument("stage", choices=("base", "edit", "calibrate", "frames", "puppet", "part", "mend", "dress", "library", "segments", "packs", "play"))
    parser.add_argument("--prefix", default="sd.rig.slim")
    parser.add_argument("--body", default="slim", choices=tuple(BODIES))
    parser.add_argument("--cycle", default="walk", choices=tuple(CYCLES))
    parser.add_argument("--slot", default="footwear", choices=tuple(SLOTS))
    parser.add_argument("--edit", default="legs", help="what the edit stage changes: ear, legs (comma-separated)")
    parser.add_argument("--design", help="one of the slot's designs (default: every one)")
    args = parser.parse_args()
    if args.stage == "base":
        result = stage_base(args.base, args.body)
    elif args.stage == "frames":
        result = stage_frames(args.base, args.cycle)
    elif args.stage == "puppet":
        result = stage_puppet(args.base, args.cycle)
    elif args.stage == "calibrate":
        result = stage_calibrate(args.base)
    elif args.stage == "edit":
        result = stage_edit(args.base, args.edit.split(","))
    elif args.stage == "part":
        result = [stage_part(args.base, args.slot, d) for d in ([args.design] if args.design else SLOTS[args.slot]["designs"])]
    elif args.stage == "mend":
        result = [stage_mend(args.base, args.slot, d) for d in ([args.design] if args.design else SLOTS[args.slot]["designs"])]
    elif args.stage == "dress":
        result = stage_dress(args.base)
    elif args.stage == "library":
        result = stage_library(args.base, args.prefix)
    elif args.stage == "segments":
        result = stage_segments(args.base, args.prefix)
    elif args.stage == "packs":
        result = stage_packs(args.base, args.prefix)
    else:
        result = stage_play(args.base, args.prefix)
    print(json.dumps(result, indent=1, default=str))
