# SPDX-License-Identifier: Apache-2.0
"""Import the preserved four-pose AI body sheet and authored ownership profile.

Regions below are explicit demo annotations, not anatomical inference. Every
assigned pixel is copied from the same frozen body, with no drawing or warping.
"""

from copy import deepcopy
from pathlib import Path
import argparse
import shutil

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from sprite_gen.curate import parts_motion
from sprite_gen.frames import parts_import, parts_production as p, parts_scaffold
from sprite_gen.spec import parts_assets as a, parts_contract as c, parts_workspace as w

REGIONS = [
    {
        "head": [220, 0, 540, 265],
        "foot_back": [100, 475, 177, 597],
        "foot_front": [440, 539, 563, 617],
        "hand_back": [186, 333, 252, 386],
        "hand_front": [466, 269, 525, 332],
        "arm_back": [184, 268, 345, 376],
        "arm_front": [357, 269, 508, 354],
        "waist": [264, 355, 367, 447],
        "leg_back": [154, 410, 322, 540],
        "leg_front": [326, 423, 492, 570],
    },
    {
        "head": [175, 0, 440, 265],
        "foot_back": [221, 562, 337, 620],
        "foot_front": [317, 451, 411, 541],
        "hand_back": [141, 333, 198, 394],
        "hand_front": [402, 252, 475, 321],
        "arm_back": [145, 266, 305, 374],
        "arm_front": [295, 260, 455, 340],
        "waist": [220, 351, 339, 438],
        "leg_back": [215, 418, 310, 575],
        "leg_front": [308, 348, 420, 510],
    },
    {
        "head": [220, 0, 510, 259],
        "foot_back": [78, 361, 181, 456],
        "foot_front": [440, 448, 586, 536],
        "hand_back": [182, 267, 244, 334],
        "hand_front": [456, 252, 524, 315],
        "arm_back": [186, 240, 335, 318],
        "arm_front": [350, 250, 506, 325],
        "waist": [263, 325, 380, 396],
        "leg_back": [134, 380, 329, 514],
        "leg_front": [330, 333, 545, 493],
    },
    {
        "head": [190, 0, 490, 265],
        "foot_back": [94, 429, 176, 517],
        "foot_front": [418, 516, 580, 612],
        "hand_back": [186, 307, 254, 369],
        "hand_front": [429, 282, 503, 335],
        "arm_back": [185, 251, 301, 352],
        "arm_front": [287, 268, 486, 346],
        "waist": [226, 354, 365, 445],
        "leg_back": [125, 416, 308, 530],
        "leg_front": [302, 423, 493, 577],
    },
]


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(w.json_bytes(value))


def prepare(out, template):
    root = out / "library"
    if root.exists():
        raise ValueError("prepare a new workspace")
    source = Image.open(out / "body-native-sheet.png").convert("RGBA")
    old, registry = w.read_workspace(template / "assembly.json")
    rig = registry[old["rig"]["id"]]
    top = registry[rig["topology"]["id"]]
    cat = deepcopy(registry[old["catalog"]["id"]])
    cat.pop("digest")
    cat["presets"] = []
    for slot in cat["slots"]:
        slot.update(options=[], default=None)
    cat = c.seal(cat)
    index = {
        "kind": "parts-workspace",
        "schema_version": 1,
        "id": "qualified.parts.demo",
        "documents": {
            rig["id"]: "profiles/rig.json",
            top["id"]: "profiles/topology.json",
            cat["id"]: "catalog.json",
        },
        "rig": c.reference(rig),
        "catalog": c.reference(cat),
        "motion": None,
        "body": [],
        "appearance": None,
        "states": ["run"],
        "fitting": [],
        "stability": [],
    }
    for doc in [rig, top, cat]:
        put(root / index["documents"][doc["id"]], doc)
    assembly = root / "assembly.json"
    put(assembly, index)
    x = source.width // 2
    y = source.height // 2
    cells = [
        (0, 0, x, y),
        (x, 0, source.width, y),
        (0, y, x, source.height),
        (x, y, source.width, source.height),
    ]
    nodes = list(REGIONS[0]) + ["torso", "root"]
    maps = {node: {} for node in nodes}
    frames = []
    transforms = []
    footprint = {}
    heads = {}
    source_root = root / "inputs/body-pieces"
    # Largest alpha-connected body component, then retain its antialias fringe.
    from sprite_gen.frames.parts_segments import _components

    for n, box in enumerate(cells):
        pose = f"run.{n}"
        raw = source.crop(box)
        array = np.asarray(raw).copy()
        alpha = array[..., 3]
        main = max(_components(alpha >= 16), key=lambda mask: int(mask.sum()))
        keep = np.asarray(Image.fromarray(main).filter(ImageFilter.MaxFilter(5)))
        array[~keep] = 0
        array[..., 3][array[..., 3] >= 240] = 255
        raw = Image.fromarray(array)
        head = REGIONS[n]["head"]
        hx = (head[0] + head[2]) / 2
        # Same cell resolution; only integer registration of each authored pose.
        tx = round(384 - hx)
        ty = 76 + [0, 6, -6, 0][n]
        full = Image.new("RGBA", (768, 768))
        full.paste(raw, (tx, ty))
        path = root / "inputs/body" / f"{n:03}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        full.save(path)
        frames.append(
            {
                "file": path.name,
                "state": "run",
                "direction": "right",
                "frame_index": n,
                "source_frame_id": f"generated-cell.{n}",
                "duration_ticks": 2400,
                "duration": 0.1,
            }
        )
        owners = np.full((raw.height, raw.width), nodes.index("torso"), dtype=np.uint8)
        for node, bounds in reversed(list(REGIONS[n].items())):
            left, up, right, down = bounds
            owners[up:down, left:right] = nodes.index(node)
        for node in nodes:
            arr = array.copy()
            arr[owners != nodes.index(node)] = 0
            image = Image.new("RGBA", (768, 768))
            image.paste(Image.fromarray(arr), (tx, ty))
            path = source_root / node / (pose + ".png")
            path.parent.mkdir(parents=True, exist_ok=True)
            image.save(path)
            maps[node][pose] = {
                "image": {
                    "file": path.relative_to(source_root).as_posix(),
                    "sha256": a.sha256(path.read_bytes()),
                },
                "format": "full-canvas",
                "pivot": [0, 0],
                "target_socket": "root",
                "visible": bool(image.getbbox()),
            }
            if node == "head":
                heads[pose] = image.getbbox()
            if node in ("foot_back", "foot_front"):
                footprint[pose, node] = image
        transforms.append(
            {
                "cell": box,
                "translation": [tx, ty],
                "regions": REGIONS[n],
                "cleanup": "largest alpha>=16 component + 2px AA fringe; alpha>=240 made opaque",
            }
        )
    bodydir = root / "inputs/body"
    put(
        bodydir / "frames.json",
        {"kind": "sprite-gen-asset", "version": 1, "frames": frames},
    )
    request = {
        "kind": "parts-motion-import",
        "schema_version": 1,
        "id": "qualified.run.reference",
        "rig": index["rig"],
        "clips": [
            {
                "id": "run.reference",
                "state": "run",
                "file": "frames.json",
                "timebase": 24000,
                "fps": "10",
                "loop": False,
                "source_mode": "external",
                "expected_files": {
                    p.name: a.sha256(p.read_bytes())
                    for p in bodydir.iterdir()
                    if p.is_file()
                },
            }
        ],
    }
    put(root / "inputs/motion.json", request)
    imported = parts_motion.import_motion(
        assembly, root / "inputs/motion.json", bodydir
    )
    file = root / imported["annotation_template"]
    annotations = w.decode_json(file.read_bytes())
    for n, row in enumerate(annotations["clips"]["run.reference"].values()):
        row.update(
            pose_id=f"run.{n}",
            sockets={
                "root": {"position": [0, 0], "visible": True, "confidence": "verified"}
            },
        )
    put(file, annotations)
    parts_motion.freeze_motion(assembly, imported["candidate"], file)
    active, _ = w.read_workspace(assembly)
    spec = {
        "kind": "parts-pack-import",
        "schema_version": 1,
        "id": "qualified.body",
        "design_id": "qualified.body",
        "role": "body",
        "body_mode": "segmented",
        "style": rig["style"],
        "rig": active["rig"],
        "motion": active["motion"],
        "view": rig["view"],
        "binding_id": "qualified.body.binding",
        "states": ["run"],
        "occupies": [],
        "pieces": [
            {
                "id": node,
                "node": node,
                "slot": None,
                "provides": [node],
                "mode": "raster",
                "pose_map": maps[node],
            }
            for node in nodes
        ],
    }
    put(source_root / "import.json", spec)
    parts_import.import_pack(assembly, source_root / "import.json", source_root)
    put(
        root / "inputs/body-authoring.json",
        {
            "raw_sha256": a.sha256((out / "body-native-sheet.png").read_bytes()),
            "transforms": transforms,
            "full_cycle_approved": False,
        },
    )
    shutil.copy2(out / "body-native-sheet.png", root / "inputs/body-native-sheet.png")
    shutil.copy2(out / "body-generation.json", root / "inputs/body-generation.json")
    shutil.copy2(out / "body-prompt.txt", root / "inputs/body-prompt.txt")
    for slot, id, text, layers in [
        (
            "hair",
            "qualified.hair",
            "Short chestnut hair cap, curved side-swept fringe, warm cream highlights, dark brown outline. Match the exact bald head side profile. Only hair, no face, skin or body. Preserve design between poses.",
            ["hair:hair:head"],
        ),
        (
            "footwear",
            "qualified.sneakers",
            "Navy and cream lace-up sneakers, dark navy side panel, cream sole and cream toe cap. Wrap the exact referenced foot SIDE PROFILE: preserve toe direction, heel position and ankle entry. Draw only this ONE shoe, no skin or leg. Do not change the foot perspective or invent a sole-facing view. Smooth hand-drawn SD style. Preserve design between poses.",
            ["shoe_back:shoe_back:foot_back", "shoe_front:shoe_front:foot_front"],
        ),
    ]:
        prompt = root / "inputs" / f"{slot}.txt"
        prompt.write_text(text)
        cfgfile = root / "inputs" / f"{slot}-config.json"
        parts_scaffold.scaffold(assembly, id, slot, layers, prompt, cfgfile, padding=10)
        cfg = w.decode_json(cfgfile.read_bytes())
        cfg["pose_checks"] = {}
        for pose in heads:
            required = Image.new("L", (768, 768))
            allowed = Image.new("L", (768, 768))
            for layer in cfg["independent"]["layers"]:
                target = layer["targets"][pose]
                if slot == "hair":
                    left, up, right, down = heads[pose]
                    target[:] = [
                        left - 8,
                        up - 10,
                        right + 8,
                        up + round((down - up) * 0.68),
                    ]
                    arr = np.asarray(
                        Image.open(
                            root / "inputs/body" / f"{int(pose[-1]):03}.png"
                        ).getchannel("A")
                    )
                    mask = np.zeros_like(arr)
                    mask[up + 6 : up + 18, left + 35 : right - 35] = arr[
                        up + 6 : up + 18, left + 35 : right - 35
                    ]
                    required = Image.fromarray(mask)
                else:
                    im = (
                        footprint[pose, layer["body_nodes"][0]]
                        .getchannel("A")
                        .filter(ImageFilter.MinFilter(9))
                    )
                    arr = np.asarray(im).copy()
                    bb = footprint[pose, layer["body_nodes"][0]].getbbox()
                    arr[: bb[1] + (bb[3] - bb[1]) // 4] = 0
                    required = Image.fromarray(np.maximum(np.asarray(required), arr))
                ImageDraw.Draw(allowed).rectangle(tuple(target), fill=255)
            protected = Image.new("L", (768, 768))
            cfg["pose_checks"][pose] = {}
            for name, image in [
                ("required", required),
                ("allowed", allowed),
                ("protected", protected),
            ]:
                path = root / "inputs/criteria" / f"{slot}.{pose}.{name}.png"
                path.parent.mkdir(parents=True, exist_ok=True)
                image.save(path)
                cfg["pose_checks"][pose][name] = path.relative_to(root).as_posix()
        put(cfgfile, cfg)
        p.init(assembly, cfgfile)
    rendered, _ = p._render_alone(
        root, *parts_motion._active(assembly)[1:3], None, ["run"]
    )
    assert all(
        np.array_equal(
            np.asarray(rendered[f"run.{n}"]),
            np.asarray(Image.open(bodydir / f"{n:03}.png")),
        )
        for n in range(4)
    )
    return {
        "assembly": str(assembly),
        "body_pixel_identical": 4,
        "loop": False,
        "full_cycle_approved": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    args = parser.parse_args()
    print(prepare(args.out.absolute(), args.template.absolute()))
