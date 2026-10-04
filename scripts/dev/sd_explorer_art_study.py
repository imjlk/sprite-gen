# SPDX-License-Identifier: Apache-2.0
"""Experimental pixel-art alignment study on a 64-grid, exported at 128.

This is not the user's selected SD character or a production artwork source.
It is retained only to reproduce the first-art-sheet coordinate experiment.

This draws new artwork; it does not edit or extract the earlier AI candidates.
The pose authoring happens before motion freeze. The parts renderer receives
only reviewed integer-positioned raster samples, never this skeleton or math.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

from PIL import Image, ImageDraw

COLORS = {
    "ink": (37, 30, 33), "skin": (230, 185, 145), "skin_dark": (180, 126, 88),
    "skin_light": (250, 216, 174), "hair": (70, 45, 30), "hair_dark": (45, 29, 27),
    "hair_light": (109, 69, 41), "shirt": (220, 219, 207), "shirt_dark": (158, 168, 164),
    "teal": (50, 100, 105), "teal_dark": (30, 64, 72), "teal_light": (90, 145, 140),
    "orange": (190, 95, 45), "orange_dark": (125, 57, 38), "orange_light": (231, 143, 73),
    "lower": (61, 74, 87), "lower_dark": (39, 45, 61), "lower_light": (92, 109, 121),
    "shoe": (48, 52, 60), "shoe_light": (91, 98, 103), "sole": (208, 202, 178),
    "leather": (100, 65, 40), "leather_dark": (65, 44, 35), "leather_light": (148, 101, 53),
    "olive": (127, 138, 79), "olive_dark": (75, 86, 53), "olive_light": (169, 177, 109),
    "metal": (200, 189, 153), "eye": (70, 100, 65), "white": (239, 239, 226),
}
CHANNEL_COLORS = {"skin": "skin", "hair": "hair", "fabric_primary": "teal",
    "fabric_secondary": "orange", "leather": "leather", "eyes": "eye",
    "lower_primary": "lower", "shoe_primary": "shoe", "undershirt": "shirt", "equipment": "olive"}
STATES = {"idle": (4, 250), "walk": (8, 125), "run": (8, 100)}


@dataclass
class Pose:
    state: str
    index: int
    bob: int
    lean: int
    ankles: dict
    shoulders: dict
    elbows: dict
    wrists: dict
    hips: dict
    knees: dict

    @property
    def head(self):
        return (self.lean, self.bob)

    @property
    def sockets(self):
        x, y = self.head
        values = {"root": (33 + self.lean, 43 + self.bob), "head": (33 + x, 18 + y),
                  "face": (40 + x, 20 + y), "waist": (33 + self.lean, 43 + self.bob),
                  "back": (27 + self.lean, 33 + self.bob)}
        for side in ("l", "r"):
            values["shoulder_" + side] = self.shoulders[side]
            values["wrist_" + side] = self.wrists[side]
            ax, ay = self.ankles[side]
            values["foot_" + side] = (ax + 4, ay + 4)
        return {name: [2 * v[0], 2 * v[1]] for name, v in values.items()}


def pose(state, index):
    if state == "idle":
        bob, lean = [0, 0, -1, 0][index], 0
        ankles = {"l": (29, 58), "r": (37, 58)}
        wrists = {"l": (25, 44 + bob), "r": (40, 45 + bob)}
        elbows = {"l": (25, 37 + bob), "r": (41, 37 + bob)}
    elif state == "walk":
        bob, lean = [0, -1, 0, -1, 0, -1, 0, -1][index], 1
        steps = [(42, 58), (38, 58), (34, 58), (30, 58), (27, 56), (28, 53), (34, 53), (40, 55)]
        ankles = {"r": steps[index], "l": steps[(index + 4) % 8]}
        near = [(43, 43), (42, 44), (38, 45), (34, 44), (28, 43), (28, 42), (32, 41), (39, 42)]
        far = [(22, 43), (23, 42), (26, 41), (31, 42), (35, 43), (34, 44), (30, 45), (25, 44)]
        wrists = {"r": (near[index][0], near[index][1] + bob), "l": (far[index][0], far[index][1] + bob)}
        elbows = {side: (round((x + (38 if side == "r" else 29)) / 2) + (2 if side == "r" else -2), 37 + bob)
                  for side, (x, _) in wrists.items()}
    else:
        bob, lean = [-2, -1, 0, -1, -3, -1, 0, -1][index], 2
        steps = [(45, 57), (41, 58), (34, 58), (25, 57), (21, 52), (28, 49), (37, 50), (44, 53)]
        ankles = {"r": steps[index], "l": steps[(index + 4) % 8]}
        near = [(43, 29), (46, 31), (46, 35), (43, 37), (33, 39), (29, 34), (32, 29), (39, 27)]
        far = [(24, 39), (21, 36), (20, 31), (24, 28), (30, 29), (34, 32), (33, 36), (29, 39)]
        wrists = {"r": near[index], "l": far[index]}
        elbows = {"r": [(40, 33), (43, 34), (42, 39), (39, 41), (30, 35), (27, 31), (30, 27), (36, 25)][index],
                  "l": [(25, 35), (23, 32), (24, 28), (27, 26), (32, 27), (34, 29), (31, 34), (27, 38)][index]}
    shoulders = {"l": (28 + lean, 30 + bob), "r": (37 + lean, 30 + bob)}
    hips = {"l": (30 + lean, 43 + bob), "r": (36 + lean, 43 + bob)}
    knees = {}
    for side in ("l", "r"):
        hx, hy = hips[side]
        ax, ay = ankles[side]
        knees[side] = (round((hx + ax) / 2) + (2 if ay < 56 else 0), round((hy + ay) / 2))
    return Pose(state, index, bob, lean, ankles, shoulders, elbows, wrists, hips, knees)


def canvas():
    return Image.new("RGBA", (64, 64))


def polygon(image, points, color, *, offset=(0, 0), outline=True):
    x, y = offset
    points = [(px + x, py + y) for px, py in points]
    draw = ImageDraw.Draw(image)
    draw.polygon(points, fill=COLORS[color])
    if outline: draw.line(points + [points[0]], fill=COLORS["ink"], width=1)


def rect(image, box, color, *, offset=(0, 0)):
    x, y = offset
    a, b, c, d = box
    ImageDraw.Draw(image).rectangle((a + x, b + y, c + x, d + y), fill=COLORS[color])


def stroke(image, points, color, width=4, shadow=None):
    draw = ImageDraw.Draw(image)
    draw.line(points, fill=COLORS["ink"], width=width + 2, joint="curve")
    for x, y in points:
        r = (width + 1) // 2
        draw.ellipse((x - r, y - r, x + r, y + r), fill=COLORS["ink"])
    draw.line(points, fill=COLORS[color], width=width, joint="curve")
    if shadow:
        draw.line([(x - 1, y) for x, y in points], fill=COLORS[shadow], width=1)


def head(p):
    image = canvas()
    polygon(image, [(26, 11), (36, 9), (41, 13), (42, 19), (44, 20), (44, 22),
                    (41, 23), (39, 26), (30, 26), (26, 23), (24, 18)], "skin", offset=p.head)
    polygon(image, [(26, 16), (29, 16), (29, 22), (32, 25), (38, 25), (37, 26),
                    (30, 25), (26, 22)], "skin_dark", offset=p.head, outline=False)
    rect(image, (36, 13, 39, 14), "skin_light", offset=p.head)
    polygon(image, [(26, 18), (28, 17), (29, 19), (28, 21), (26, 21)], "skin", offset=p.head)
    rect(image, (27, 19, 27, 20), "skin_dark", offset=p.head)
    return image


def hair(p, variant, layer):
    image = canvas()
    if layer == "back":
        points = ([(25, 10), (30, 8), (35, 9), (30, 15), (28, 23), (26, 25), (23, 21), (23, 15)] if variant == "a"
                  else [(25, 9), (32, 8), (36, 10), (29, 17), (28, 29), (24, 30), (21, 27), (21, 15)])
        polygon(image, points, "hair_dark", offset=p.head)
        if variant == "b": rect(image, (23, 20, 24, 27), "hair", offset=p.head)
    elif variant == "a":
        polygon(image, [(24, 11), (26, 8), (30, 8), (31, 6), (33, 8), (36, 7), (37, 9),
                        (40, 10), (42, 12), (40, 15), (37, 14), (36, 12), (31, 15), (29, 18), (25, 17)], "hair", offset=p.head)
        polygon(image, [(27, 11), (31, 9), (35, 10), (30, 12)], "hair_light", offset=p.head, outline=False)
        rect(image, (26, 15, 28, 17), "hair_dark", offset=p.head)
    else:
        polygon(image, [(22, 14), (24, 10), (28, 8), (35, 8), (40, 10), (42, 13), (40, 17),
                        (38, 16), (37, 12), (34, 14), (30, 16), (29, 22), (26, 23), (24, 21)], "hair", offset=p.head)
        polygon(image, [(25, 13), (27, 11), (33, 10), (36, 11), (29, 13)], "hair_light", offset=p.head, outline=False)
        rect(image, (27, 18, 28, 21), "hair_dark", offset=p.head)
    return image


def face(p, variant, feature):
    image = canvas()
    # Far eye/brow are intentionally invisible in this fixed right side view.
    if feature.endswith("_l"): return image
    if feature == "eye_r":
        if p.state == "idle" and p.index == 2:
            rect(image, (40, 18, 42, 18), "ink", offset=p.head)
        else:
            rect(image, (40, 16 if variant == "a" else 17, 42, 19), "ink", offset=p.head)
            rect(image, (40, 17, 41, 18), "white", offset=p.head)
            rect(image, (42, 17, 42, 18), "eye", offset=p.head)
    elif feature == "brow_r":
        polygon(image, [(39, 14), (42, 14), (42, 15), (39, 15)] if variant == "a"
                else [(39, 14), (40, 13), (42, 14)], "hair_dark", offset=p.head, outline=False)
    elif feature == "mouth":
        if variant == "a": rect(image, (41, 23, 43, 23), "ink", offset=p.head)
        else:
            ImageDraw.Draw(image).line([(40 + p.lean, 22 + p.bob), (41 + p.lean, 23 + p.bob),
                                       (43 + p.lean, 22 + p.bob)], fill=COLORS["ink"], width=1)
    return image


def torso(p, variant=None):
    image = canvas()
    off = p.head
    polygon(image, [(30, 25), (35, 25), (35, 30), (30, 30)], "skin", offset=off)
    rect(image, (30, 26, 31, 29), "skin_dark", offset=off)
    polygon(image, [(29, 28), (36, 28), (39, 31), (39, 43), (27, 43), (26, 32)], "shirt", offset=off)
    rect(image, (28, 33, 29, 41), "shirt_dark", offset=off)
    if variant is None:
        ImageDraw.Draw(image).line([(29 + p.lean, 29 + p.bob), (31 + p.lean, 32 + p.bob),
                                   (35 + p.lean, 32 + p.bob), (37 + p.lean, 29 + p.bob)], fill=COLORS["shirt_dark"])
        return image
    primary = "teal" if variant == "a" else "orange"
    dark, light = primary + "_dark", primary + "_light"
    polygon(image, [(28, 28), (30, 27), (33, 31), (36, 27), (38, 28), (41, 32), (40, 44), (26, 44), (25, 33)], primary, offset=off)
    rect(image, (27, 34, 28, 42), dark, offset=off)
    rect(image, (29, 32, 30, 42), light, offset=off)
    rect(image, (35, 31, 35, 43), "ink", offset=off)
    if variant == "a":
        polygon(image, [(29, 28), (31, 32), (33, 31)], "teal_light", offset=off)
        polygon(image, [(36, 28), (36, 32), (33, 31)], "teal_light", offset=off)
        rect(image, (30, 38, 32, 39), "teal_dark", offset=off)
        rect(image, (35, 34, 35, 34), "metal", offset=off)
    else:
        polygon(image, [(29, 27), (31, 26), (33, 30), (36, 26), (38, 27), (36, 32), (33, 30), (30, 32)], light, offset=off)
        rect(image, (30, 35, 33, 38), dark, offset=off)
        rect(image, (37, 35, 39, 38), dark, offset=off)
        rect(image, (30, 35, 33, 35), light, offset=off)
        for y in (33, 38, 42): rect(image, (35, y, 35, y), "metal", offset=off)
        rect(image, (29, 43, 39, 43), "leather", offset=off)
    return image


def arm(p, side, variant=None):
    image = canvas()
    shoulder, elbow, wrist = p.shoulders[side], p.elbows[side], p.wrists[side]
    stroke(image, [shoulder, elbow, wrist], "skin", 4, "skin_dark")
    if variant is not None:
        primary = "teal" if variant == "a" else "orange"
        if variant == "a":
            end = (round((shoulder[0] + elbow[0]) / 2), round((shoulder[1] + elbow[1]) / 2))
            stroke(image, [shoulder, end], primary, 5, primary + "_dark")
        else:
            stroke(image, [shoulder, elbow, wrist], primary, 5, primary + "_dark")
            x, y = wrist
            rect(image, (x - 2, y - 1, x + 2, y), "leather")
    return image


def hand(p, side, glove=False):
    image = canvas()
    x, y = p.wrists[side]
    polygon(image, [(x - 2, y), (x + 1, y), (x + 2, y + 2), (x + 1, y + 4), (x - 2, y + 3)], "skin")
    rect(image, (x - 1, y + 2, x - 1, y + 3), "skin_dark")
    if glove:
        polygon(image, [(x - 2, y), (x + 1, y), (x + 2, y + 2), (x - 2, y + 2)], "leather")
        rect(image, (x - 1, y, x + 1, y), "leather_light")
    return image


def leg(p, side, variant=None):
    image = canvas()
    hip, knee, ankle = p.hips[side], p.knees[side], p.ankles[side]
    stroke(image, [hip, knee, ankle], "skin", 5, "skin_dark")
    if variant is not None:
        end = (round((hip[0] + knee[0]) / 2), round((hip[1] + knee[1]) / 2)) if variant == "a" else ankle
        stroke(image, [hip, end] if variant == "a" else [hip, knee, ankle], "lower", 6, "lower_dark")
        hx, hy = hip
        rect(image, (hx - 2, hy, hx + 2, hy + 1), "lower_light")
        if side == "r": rect(image, (hx + 1, hy + 2, hx + 2, hy + 4), "lower_dark")
    return image


def foot(p, side, variant=None):
    image = canvas()
    x, y = p.ankles[side]
    if variant is None:
        polygon(image, [(x - 2, y), (x + 2, y), (x + 3, y + 1), (x + 6, y + 1),
                        (x + 7, y + 2), (x + 7, y + 3), (x - 3, y + 3), (x - 3, y + 1)], "skin")
        rect(image, (x - 2, y + 2, x + 2, y + 2), "skin_dark")
    else:
        base = "shoe" if variant == "a" else "leather"
        if variant == "b":
            polygon(image, [(x - 3, y - 4), (x + 2, y - 4), (x + 3, y + 1),
                            (x + 7, y + 1), (x + 8, y + 3), (x - 3, y + 3)], base)
            rect(image, (x - 2, y - 3, x + 1, y - 3), "leather_light")
            rect(image, (x - 2, y - 1, x - 1, y + 1), "leather_dark")
            rect(image, (x + 1, y, x + 3, y), "metal")
        else:
            polygon(image, [(x - 3, y), (x + 2, y), (x + 4, y + 1), (x + 7, y + 1),
                            (x + 8, y + 3), (x - 3, y + 3)], base)
            rect(image, (x + 1, y + 1, x + 4, y + 1), "shoe_light")
            rect(image, (x + 2, y, x + 3, y), "white")
        rect(image, (x - 2, y + 2, x + 7, y + 2), "sole")
    return image


def cap(p, layer):
    image = canvas()
    if layer == "back":
        polygon(image, [(22, 11), (23, 8), (27, 6), (36, 6), (41, 9), (42, 12), (25, 13)], "olive", offset=p.head)
        rect(image, (24, 10, 27, 11), "olive_dark", offset=p.head)
    else:
        polygon(image, [(26, 11), (40, 10), (45, 10), (48, 12), (48, 13), (41, 13), (38, 12), (26, 13)], "olive", offset=p.head)
        rect(image, (32, 9, 34, 10), "metal", offset=p.head)
        rect(image, (42, 12, 46, 12), "olive_dark", offset=p.head)
    return image


def hair_mask(p):
    image = Image.new("RGBA", (64, 64), "white")
    x, y = p.head
    ImageDraw.Draw(image).polygon([(21 + x, 5 + y), (38 + x, 5 + y), (43 + x, 10 + y),
                                  (42 + x, 13 + y), (23 + x, 13 + y)], fill="black")
    return image


def backpack(p, layer):
    image = canvas()
    if layer == "back":
        polygon(image, [(23, 29), (28, 29), (29, 33), (28, 42), (22, 42), (21, 39), (21, 32)], "olive", offset=p.head)
        rect(image, (22, 34, 24, 39), "olive_dark", offset=p.head)
        rect(image, (22, 31, 26, 31), "olive_light", offset=p.head)
        rect(image, (23, 37, 25, 37), "metal", offset=p.head)
    else:
        draw = ImageDraw.Draw(image)
        draw.line([(37 + p.lean, 29 + p.bob), (35 + p.lean, 36 + p.bob), (34 + p.lean, 43 + p.bob)], fill=COLORS["ink"], width=3)
        draw.line([(37 + p.lean, 29 + p.bob), (35 + p.lean, 36 + p.bob), (34 + p.lean, 43 + p.bob)], fill=COLORS["leather"], width=1)
        rect(image, (34, 38, 36, 39), "metal", offset=p.head)
    return image


def exported(image):
    return image.resize((128, 128), Image.Resampling.NEAREST)


def body(p):
    result = {"root": canvas(), "head": head(p), "torso": torso(p)}
    for side in ("l", "r"):
        result["arm_" + side] = arm(p, side)
        result["hand_" + side] = hand(p, side)
        result["leg_" + side] = leg(p, side, "a")
        result["foot_" + side] = foot(p, side)
    for layer in ("front", "back"): result["hair_" + layer] = hair(p, "a", layer)
    for feature in ("eye_l", "eye_r", "brow_l", "brow_r", "mouth"): result[feature] = face(p, "a", feature)
    return result


DRAW_ORDER = ("root", "hair_back", "arm_l", "hand_l", "leg_l", "foot_l", "leg_r", "foot_r",
              "torso", "head", "eye_l", "eye_r", "brow_l", "brow_r", "mouth", "hair_front", "arm_r", "hand_r")


def composed(p, choices="aaaaa", *, extras=()):
    hair_v, face_v, upper_v, lower_v, shoe_v = choices
    pieces = body(p)
    pieces.update({"torso": torso(p, upper_v), "hair_front": hair(p, hair_v, "front"), "hair_back": hair(p, hair_v, "back")})
    for side in ("l", "r"):
        pieces["arm_" + side] = arm(p, side, upper_v)
        pieces["leg_" + side] = leg(p, side, lower_v)
        pieces["foot_" + side] = foot(p, side, shoe_v)
        pieces["hand_" + side] = hand(p, side, "gloves" in extras)
    for feature in ("eye_l", "eye_r", "brow_l", "brow_r", "mouth"): pieces[feature] = face(p, face_v, feature)
    result = canvas()
    if "backpack" in extras: result.alpha_composite(backpack(p, "back"))
    for name in DRAW_ORDER:
        result.alpha_composite(pieces[name])
        if name == "torso" and "backpack" in extras: result.alpha_composite(backpack(p, "strap"))
        if name == "hair_front" and "cap" in extras:
            result.alpha_composite(cap(p, "back"))
            result.alpha_composite(cap(p, "front"))
    return exported(result)
