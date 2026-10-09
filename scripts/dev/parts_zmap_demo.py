# SPDX-License-Identifier: Apache-2.0
"""Authored Mina body refinement fixture; all operations use public parts APIs.

The neckline and geodesic wrist lengths below are explicit fixture choices.
They are not a body detector or a rule for arbitrary body shapes.
"""

import argparse
from pathlib import Path
from collections import deque
import shutil
from PIL import Image, ImageDraw
from sprite_gen._deps import np
from sprite_gen.curate import parts_motion
from sprite_gen.frames import parts_production as p, parts_body_refine
from sprite_gen.compose import compose_parts
from sprite_gen.spec import parts_workspace as w

COLORS = {
    "root": "#ffff00",
    "head": "#ff00ff",
    "torso": "#0088ff",
    "waist": "#aaaa00",
    "arm_back": "#00a000",
    "hand_back": "#880044",
    "arm_front": "#ffffff",
    "hand_front": "#aa00ff",
    "leg_back": "#00ffff",
    "leg_front": "#ff8000",
}


def distal(arm, length):
    distances = np.full(arm.shape, -1, dtype=int)
    yy = np.indices(arm.shape)[0]
    if not arm.any():
        return arm
    top = yy[arm].min()
    seeds = arm & (yy <= top + 1)
    distances[seeds] = 0
    queue = deque(zip(*np.nonzero(seeds)))
    while queue:
        y, x = queue.popleft()
        for dy, dx in (
            (0, 1),
            (0, -1),
            (1, 0),
            (-1, 0),
            (-1, -1),
            (-1, 1),
            (1, -1),
            (1, 1),
        ):
            a, b = y + dy, x + dx
            if (
                0 <= a < arm.shape[0]
                and 0 <= b < arm.shape[1]
                and arm[a, b]
                and distances[a, b] < 0
            ):
                distances[a, b] = distances[y, x] + 1
                queue.append((a, b))
    return arm & (distances >= max(0, distances.max() - length + 1))


def prepare(source, out, *, resume=False):
    out = Path(out).absolute()
    root = out / "library"
    if root.exists() and not resume:
        raise ValueError("preserve this run; choose an unused output directory")
    if not root.exists():
        shutil.copytree(Path(source) / "library", root)
    assembly = root / "assembly.json"
    _, index, registry, _ = parts_motion._active(assembly)
    topology = dict(registry[registry[index["rig"]["id"]]["topology"]["id"]])
    topology.pop("digest")
    if "head" not in {n["id"] for n in topology["nodes"]}:
        topology["nodes"].append(
            {
                "id": "head",
                "parent": "torso",
                "required": False,
                "symmetric_with": None,
                "contact": False,
            }
        )
        topology["layers"].insert(topology["layers"].index("eyes"), "head")
    for socket in topology["sockets"]:
        if socket["id"] == "head":
            socket["node"] = "head"
    (out / "topology.json").write_bytes(w.json_bytes(topology))
    (out / "colors.json").write_bytes(w.json_bytes(COLORS))
    motion = registry[index["motion"]["id"]]
    loader = compose_parts.AssetLoader(root, index)
    policies = []
    contact = []
    seen = set()
    segments = w.decode_json((root / "inputs/body-segments/segments.json").read_bytes())
    for clip in motion["clips"]:
        for sample in clip["samples"]:
            if sample["pose_id"] in seen:
                continue
            seen.add(sample["pose_id"])
            segment_clip, segment_sample = segments["poses"][sample["pose_id"]]
            under = loader(motion, sample["image"]).convert("RGBA")
            rgba = np.asarray(under)
            parts = {
                n: np.asarray(
                    Image.open(
                        root
                        / "inputs/body-segments"
                        / n
                        / segment_clip
                        / f"{segment_sample}.png"
                    ).convert("RGBA")
                )[..., 3]
                > 0
                for n in ("root", "arm_back", "arm_front", "leg_back", "leg_front")
            }
            yy = np.indices(rgba.shape[:2])[0]
            neck = sample["sockets"]["torso"]["position"][1] - 13
            waist = sample["sockets"]["torso"]["position"][1] + 14
            masks = {
                "head": parts["root"] & (yy < neck),
                "torso": parts["root"] & (yy >= neck) & (yy < waist),
                "waist": parts["root"] & (yy >= waist),
                "root": np.zeros_like(yy, dtype=bool),
                "leg_back": parts["leg_back"],
                "leg_front": parts["leg_front"],
            }
            for side in ("back", "front"):
                # Explicit native-pixel wrist policy for this existing Mina fixture.
                hand = distal(parts["arm_" + side], 5)
                masks["hand_" + side] = hand
                masks["arm_" + side] = parts["arm_" + side] & ~hand
            labels = np.zeros_like(rgba)
            coverage = np.zeros(yy.shape, dtype=int)
            for name, mask in masks.items():
                labels[mask] = (*p._rgb(COLORS[name]), 255)
                coverage += mask
            if not np.array_equal(coverage, (rgba[..., 3] > 0).astype(int)):
                raise ValueError("body ownership has overlap or holes")
            label = Image.fromarray(labels)
            file = out / "labels" / f"{sample['pose_id']}.png"
            file.parent.mkdir(exist_ok=True)
            label.save(file)
            policies.append(
                {
                    "pose": sample["pose_id"],
                    "neck_y": int(neck),
                    "wrist_distal_pixels": 5,
                    "hand_pixels": {
                        s: int(masks["hand_" + s].sum()) for s in ("back", "front")
                    },
                }
            )
            if clip["state"] == "walk":
                tile = Image.new("RGBA", (384, 384), (235, 235, 232, 255))
                tile.alpha_composite(label.resize((384, 384), Image.Resampling.NEAREST))
                ImageDraw.Draw(tile).text((8, 8), sample["pose_id"], fill="black")
                contact.append(tile)
    (out / "body-ownership-policy.json").write_bytes(
        w.json_bytes({"fixture": "Mina", "authored_draft": True, "poses": policies})
    )
    wall = Image.new("RGBA", (384 * 4, 384 * 4), (235, 235, 232, 255))
    for n, tile in enumerate(contact):
        wall.alpha_composite(tile, (n % 4 * 384, n // 4 * 384))
    wall.save(out / "body-ownership.png")
    result = parts_body_refine.refine(
        assembly,
        topology=out / "topology.json",
        labels=out / "labels",
        colors=out / "colors.json",
        out=root / "inputs/body-refined",
        body_id="sd.mina.body",
    )
    for identity in ("demo.upper.red", "demo.upper.teal"):
        state = p._load(root, identity)
        config = state["config"]
        config["visibility"] = "zmap"
        config["segments"] = "inputs/body-refined/segments.json"
        design = (
            "a red long-sleeved pullover hoodie, one simple kangaroo pocket, ribbed cuffs and hem"
            if identity.endswith("red")
            else "a teal sleeveless tank top with a narrow cream neckline and hem"
        )
        config["prompt"]["text"] = (
            f"Image 1 is the fixed pose/coordinate guide. Image 2 defines the garment design. "
            f"Image 3 labels the body layers: head, torso, near/far arms and near/far hands. "
            f"Draw ONLY {design}, independently for every pose at exactly the reference coordinates. "
            "The label colors describe ownership and depth, not fabric colors. Keep a continuous garment "
            "surface at the neckline and wrists; do not punch skin-shaped holes into the clothing. "
            "The body head and hands will cover overlaps at their zmap layers. End the sleeves at the "
            "wrists. Never draw skin, head, hands, legs, shoes or another body. Leave all other pixels "
            "pure magenta #FF00FF, including empty cells. Match the fixed 8x8 art-pixel grid. "
            "Use a crisp one-art-pixel outline and 3-4 shading colors, with no antialiasing, speckles, "
            "dithering or labels. Preserve the same garment design and size across the motion."
        )
        # Sleeve ownership includes its corresponding hand region; body hands
        # then cover it naturally at their independent zmap layer.
        for piece in config["pieces"]:
            if piece["id"] == "sleeve_back":
                piece["segments"] = ["arm_back", "hand_back"]
            if piece["id"] == "sleeve_front":
                piece["segments"] = ["arm_front", "hand_front"]
        path = out / (identity + ".config.json")
        path.write_bytes(w.json_bytes(config))
        p.configure(assembly, identity, path)
        p.build(assembly, identity)
        p.check(assembly, identity)
        made = p.sheet(
            assembly,
            identity,
            poses=[
                s["pose_id"]
                for c in motion["clips"]
                if c["state"] == "walk"
                for s in c["samples"]
            ],
        )
        (out / (identity + ".layer-guides.json")).write_bytes(w.json_bytes(made))
    lock_file = out / "source-lock.json"
    if lock_file.exists():
        lock = w.decode_json(lock_file.read_bytes())
    else:
        lock = w.decode_json((Path(source) / "source-lock.json").read_bytes())
        (out / "source-lock-original.json").write_bytes(w.json_bytes(lock))
    current, _ = w.read_workspace(assembly)
    lock.update(
        body=current["body"],
        motion=current["motion"],
        body_refinement="Authored ownership; 42 RGBA samples and timing preserved; revised metadata digests.",
    )
    lock_file.write_bytes(w.json_bytes(lock))
    (out / "body-refine-result.json").write_bytes(w.json_bytes(result))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    print(prepare(args.source, args.out, resume=args.resume))
