# SPDX-License-Identifier: Apache-2.0
"""Freeze revised anatomical annotations without changing body art pixels."""

from copy import deepcopy
from pathlib import Path
import argparse

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from parts_qualified_demo import REGIONS
from sprite_gen.curate import parts_motion
from sprite_gen.frames import parts_import, parts_production as p, parts_part_edit
from sprite_gen.spec import parts_assets as a, parts_workspace as w

# Explicit heel/toe guard regions on the normalized body; ankles are exposed.
GUARDS = [
    {
        "foot_back": [[119, 573, 142, 610], [123, 616, 158, 657]],
        "foot_front": [[445, 650, 471, 677], [503, 630, 549, 663]],
    },
    {
        "foot_back": [[304, 663, 332, 686], [364, 662, 394, 687]],
        "foot_front": [[398, 560, 421, 595], [434, 577, 468, 610]],
    },
    {
        "foot_back": [[156, 448, 186, 476], [126, 480, 157, 511]],
        "foot_front": [[491, 553, 525, 578], [551, 532, 584, 567]],
    },
    {
        "foot_back": [[185, 514, 212, 540], [155, 538, 183, 568]],
        "foot_front": [[474, 642, 500, 666], [523, 622, 556, 650]],
    },
]


def refine(out):
    root = out / "library"
    assembly = root / "assembly.json"
    folder = root / "inputs/body-pieces-r2"
    if folder.exists():
        raise ValueError("annotation revision already frozen")
    spec = w.read_json(root, "inputs/body-pieces/import.json")
    transforms = w.read_json(root, "inputs/body-authoring.json")["transforms"]
    priority = [
        "foot_front",
        "foot_back",
        "hand_front",
        "hand_back",
        "head",
        "arm_front",
        "arm_back",
        "waist",
        "leg_front",
        "leg_back",
    ]
    nodes = [x["node"] for x in spec["pieces"]]
    maps = {node: {} for node in nodes}
    heads = {}
    feet = {}
    for n, transform in enumerate(transforms):
        pose = f"run.{n}"
        image = Image.open(root / "inputs/body" / f"{n:03}.png").convert("RGBA")
        pixels = np.asarray(image)
        owners = np.full((768, 768), nodes.index("torso"), dtype=np.uint8)
        tx, ty = transform["translation"]
        for node in reversed(priority):
            left, up, right, down = REGIONS[n][node]
            owners[up + ty : down + ty, left + tx : right + tx] = nodes.index(node)
        for node in nodes:
            arr = pixels.copy()
            arr[owners != nodes.index(node)] = 0
            im = Image.fromarray(arr)
            path = folder / node / (pose + ".png")
            path.parent.mkdir(parents=True, exist_ok=True)
            im.save(path)
            maps[node][pose] = {
                "image": {
                    "file": path.relative_to(folder).as_posix(),
                    "sha256": a.sha256(path.read_bytes()),
                },
                "format": "full-canvas",
                "pivot": [0, 0],
                "target_socket": "root",
                "visible": bool(im.getbbox()),
            }
            if node == "head":
                heads[pose] = im.getbbox()
            if node.startswith("foot_"):
                feet[pose, node] = im
    spec = deepcopy(spec)
    for piece in spec["pieces"]:
        piece["pose_map"] = maps[piece["node"]]
    path = folder / "import.json"
    path.write_bytes(w.json_bytes(spec))
    parts_import.import_pack(assembly, path, folder)
    _, index, registry, _ = parts_motion._active(assembly)
    rendered, _ = p._render_alone(root, index, registry, None, ["run"])
    assert all(
        np.array_equal(
            np.asarray(rendered[f"run.{n}"]),
            np.asarray(Image.open(root / "inputs/body" / f"{n:03}.png")),
        )
        for n in range(4)
    )
    (folder / "annotations.json").write_bytes(
        w.json_bytes(
            {
                "regions": REGIONS,
                "priority": priority,
                "guards": GUARDS,
                "body_pixel_identical": 4,
                "reason": "Complete forefoot upper contour in run.2/run.3; assign hand pixels ahead of head; heel/toe QA excludes ankles.",
            }
        )
    )
    for identity in ["qualified.hair", "qualified.sneakers"]:
        state = p._load(root, identity)
        cfg = state["config"]
        slot = cfg["slot"]
        for n in range(4):
            pose = f"run.{n}"
            required = Image.new("L", (768, 768))
            allowed = Image.new("L", (768, 768))
            protected = Image.new("L", (768, 768))
            for layer in cfg["independent"]["layers"]:
                if slot == "hair":
                    left, up, right, down = heads[pose]
                    layer["targets"][pose] = [
                        left - 8,
                        up - 12,
                        right + 8,
                        up + round((down - up) * 0.82),
                    ]
                    original = Image.open(
                        root / "inputs/body" / f"{n:03}.png"
                    ).getchannel("A")
                    r = Image.new("L", (768, 768))
                    ImageDraw.Draw(r).rectangle(
                        (left + 35, up + 6, right - 35, up + 18), fill=255
                    )
                    required = Image.fromarray(
                        np.minimum(np.asarray(r), np.asarray(original))
                    )
                    ImageDraw.Draw(protected).rectangle(
                        (
                            left + round((right - left) * 0.7),
                            up + round((down - up) * 0.6),
                            right,
                            down,
                        ),
                        fill=255,
                    )
                else:
                    node = layer["body_nodes"][0]
                    im = feet[pose, node]
                    bb = im.getbbox()
                    layer["targets"][pose] = [
                        bb[0] - 10,
                        bb[1] - 10,
                        bb[2] + 10,
                        bb[3] + 10,
                    ]
                    region = Image.new("L", (768, 768))
                    for box in GUARDS[n][node]:
                        ImageDraw.Draw(region).rectangle(box, fill=255)
                    core = np.minimum(
                        np.asarray(region),
                        np.asarray(im.getchannel("A").filter(ImageFilter.MinFilter(9))),
                    )
                    required = Image.fromarray(np.maximum(np.asarray(required), core))
                ImageDraw.Draw(allowed).rectangle(
                    tuple(layer["targets"][pose]), fill=255
                )
            cfg["pose_checks"][pose] = {}
            for name, im in [
                ("required", required),
                ("allowed", allowed),
                ("protected", protected),
            ]:
                path = root / "inputs/criteria-r2" / f"{slot}.{pose}.{name}.png"
                path.parent.mkdir(parents=True, exist_ok=True)
                im.save(path)
                cfg["pose_checks"][pose][name] = path.relative_to(root).as_posix()
        if slot == "hair":
            cfg["prompt"] = {
                "text": "A FULL short chestnut hair cap for the referenced SIDE-PROFILE bald head. Cover the crown AND the rounded BACK of the scalp all the way down behind the ear, taper to the nape. No shaved/bald side or exposed rear scalp. Preserve the exact rounded skull silhouette; curved side-swept fringe, cream highlights and dark brown outline. Keep the ear notch and face unobstructed. Draw ONLY the complete hair layer, no skin, head, face, ear or neck pixels. Match exact head profile, not a frontal forelock icon.",
                "refs": [],
            }
        file = root / "inputs" / f"{slot}-config-r2.json"
        file.write_bytes(w.json_bytes(cfg))
        p.configure(assembly, identity, file)
        if slot == "footwear":
            for n in range(4):
                pose = f"run.{n}"
                result = parts_part_edit.edit(
                    assembly, identity, pose, [{"type": "translate", "dx": 0, "dy": 0}]
                )
                p.select_candidate(
                    assembly, identity, pose, result["preview_candidate"]
                )
            print(p.check(assembly, identity))
            print(p.build(assembly, identity))
    return {
        "body_pixel_identical": 4,
        "anatomical_annotations": str(folder / "annotations.json"),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(refine(args.out.absolute()))
