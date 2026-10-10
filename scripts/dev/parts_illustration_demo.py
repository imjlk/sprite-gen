# SPDX-License-Identifier: Apache-2.0
"""Native-resolution static fitting experiment, using the public parts APIs.

Body ownership and fitting boxes are authored for this generated reference.
This does not manufacture a walk/run cycle or approve the artwork.
"""

from pathlib import Path
import argparse
import shutil
from fractions import Fraction
from PIL import Image, ImageDraw
from sprite_gen._deps import np
from sprite_gen.spec import (
    parts_assets as assets,
    parts_contract as contract,
    parts_workspace as w,
)
from sprite_gen.curate import parts_motion, parts_appearance
from sprite_gen.frames import (
    parts_segments,
    parts_import,
    parts_production as p,
    parts_part_edit,
    parts_body_refine,
)
from sprite_gen.compose import export_parts
from sprite_gen.compose.parts_runtime import PartsPlayer

PROFILE_SOURCE_RGBA_SHA256 = (
    "b19173216301d1d1db52cff2cb70239aa0fccf357a719fb593160e3374b26cd4"
)

NODES = [
    "root",
    "leg_back",
    "foot_back",
    "sock_back",
    "shoe_back",
    "arm_back",
    "hand_back",
    "waist",
    "torso",
    "head",
    "eyes",
    "hair",
    "leg_front",
    "foot_front",
    "sock_front",
    "shoe_front",
    "arm_front",
    "hand_front",
]
SLOTS = {
    "hair": ["hair"],
    "eyes": ["eyes"],
    "upper": ["torso", "arm_back", "arm_front"],
    "lower": ["waist", "leg_back", "leg_front"],
    "footwear": ["foot_back", "foot_front", "shoe_back", "shoe_front"],
    "socks": ["sock_back", "sock_front", "foot_back", "foot_front"],
}
SOCKETS = {
    "root": [628, 1198],
    "head": [622, 200],
    "face": [666, 264],
    "torso": [636, 525],
    "waist": [641, 654],
    "foot_back": [724, 1139],
    "foot_front": [507, 1165],
}
PARTS = {
    "socks.crew": ("socks", "sock_front", "foot_front", [428, 960, 852, 1240]),
    "hair.bob": ("hair", "hair", "head", [439, 0, 814, 358]),
    "eyes.open": ("eyes", "eyes", "face", [583, 232, 753, 295]),
    "upper.cream": ("upper", "torso", "torso", [447, 385, 807, 701]),
    "lower.navy": ("lower", "waist", "waist", [494, 633, 792, 903]),
    "footwear.sneakers": (
        "footwear",
        "foot_front",
        "foot_front",
        [432, 1072, 854, 1228],
    ),
}


def write(file, value):
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(w.json_bytes(value))


def authored_labels(body):
    rgba = np.asarray(body)
    ink = rgba[..., 3] > 0
    yy, xx = np.indices(ink.shape)
    masks = {
        name: np.zeros_like(ink)
        for name in NODES
        if name
        not in ("hair", "eyes", "sock_back", "sock_front", "shoe_back", "shoe_front")
    }
    # Explicit reference-specific regions, not anatomy inferred from RGB.
    masks["head"] = ink & (yy < 399)

    def polygon(points):
        image = Image.new("1", body.size)
        ImageDraw.Draw(image).polygon(points, fill=1)
        return np.asarray(image)

    masks["arm_front"] = ink & polygon(
        [
            (482, 405),
            (545, 411),
            (575, 470),
            (552, 533),
            (515, 601),
            (478, 669),
            (451, 711),
            (399, 711),
            (420, 644),
            (449, 559),
            (462, 471),
        ]
    )
    masks["arm_back"] = ink & polygon(
        [
            (723, 407),
            (773, 417),
            (786, 497),
            (816, 576),
            (846, 645),
            (862, 711),
            (812, 720),
            (782, 656),
            (747, 605),
            (730, 535),
        ]
    )
    masks["hand_front"] = ink & (yy >= 697) & (yy < 830) & (xx < 488)
    masks["hand_back"] = ink & (yy >= 697) & (yy < 830) & (xx > 780)
    masks["waist"] = ink & (yy >= 650) & (yy < 757)
    masks["leg_front"] = ink & (yy >= 757) & (xx < 641)
    masks["leg_back"] = ink & (yy >= 757) & (xx >= 641)
    masks["foot_front"] = masks["leg_front"] & (yy >= 1111)
    masks["foot_back"] = masks["leg_back"] & (yy >= 1091)
    # Hand/foot pixels win over their parent limbs; torso owns the remainder.
    order = [
        "head",
        "hand_front",
        "hand_back",
        "arm_front",
        "arm_back",
        "foot_front",
        "foot_back",
        "leg_front",
        "leg_back",
        "waist",
    ]
    owned = np.zeros_like(ink)
    for name in order:
        masks[name] &= ~owned
        owned |= masks[name]
    masks["torso"] = ink & ~owned
    colors = {name: f"#{(i + 1) * 7919:06x}" for i, name in enumerate(masks)}
    labels = np.zeros_like(rgba)
    for name, region in masks.items():
        labels[region] = (*p._rgb(colors[name]), 255)
    return Image.fromarray(labels), colors, masks


def prepare(out, *, expected_body_rgba=PROFILE_SOURCE_RGBA_SHA256):
    root = out / "library"
    assembly = root / "assembly.json"
    if root.exists():
        raise ValueError("keep this experiment; use another output directory")
    body = Image.open(out / "art/body.png").convert("RGBA")
    width, height = body.size
    if body.size != (1254, 1254):
        raise ValueError("review and author ownership for this new body canvas")
    if assets.sha256(body.tobytes()) != expected_body_rgba:
        raise ValueError(
            "body differs from the authored ownership profile; author a new profile for this source"
        )
    w.initialize(root, "human.sd.v1")
    topology = contract.seal(
        {
            "kind": "parts-topology",
            "schema_version": 1,
            "id": "illustration.sd.topology",
            "nodes": [
                {
                    "id": name,
                    "parent": None if name == "root" else "root",
                    "required": name == "root",
                }
                for name in NODES
            ],
            "sockets": [
                {
                    "id": name,
                    "node": name if name in NODES else "head",
                    "required": True,
                }
                for name in SOCKETS
            ],
            "slots": [
                {"id": slot, "nodes": nodes, "sockets": list(SOCKETS), "optional": True}
                for slot, nodes in SLOTS.items()
            ],
            "layers": NODES,
        }
    )
    rig = contract.seal(
        {
            "kind": "parts-rig",
            "schema_version": 1,
            "id": "illustration.sd.rig",
            "topology": contract.reference(topology),
            "view": "front_threequarter",
            "style": "illustration.cel.v1",
            "canvas": {"width": width, "height": height},
            "sampling": "smooth",
        }
    )
    catalog = contract.seal(
        {
            "kind": "parts-catalog",
            "schema_version": 1,
            "id": "illustration.sd.catalog",
            "rig": contract.reference(rig),
            "topology": contract.reference(topology),
            "groups": [{"id": "parts", "label": "고해상도 파츠"}],
            "slots": [{"id": slot, "group": "parts", "label": slot} for slot in SLOTS],
        }
    )
    index = {
        "kind": "parts-workspace",
        "schema_version": 1,
        "id": "illustration.sd.workspace",
        "documents": {
            topology["id"]: "profiles/topology.json",
            rig["id"]: "profiles/rig.json",
            catalog["id"]: "catalog.json",
        },
        "rig": contract.reference(rig),
        "catalog": contract.reference(catalog),
        "states": ["fit"],
    }
    for document in (topology, rig, catalog):
        write(root / index["documents"][document["id"]], document)
    write(assembly, index)
    source = root / "inputs/body"
    source.mkdir(parents=True)
    shutil.copy2(out / "art/body.png", source / "body.png")
    write(
        source / "frame.json",
        {
            "kind": "sprite-gen-asset",
            "version": 1,
            "frames": [{"id": "reference.0", "file": "body.png", "duration": 1}],
        },
    )
    spec = root / "inputs/body-motion.json"
    write(
        spec,
        {
            "kind": "parts-motion-import",
            "schema_version": 1,
            "id": "illustration.sd.reference",
            "rig": contract.reference(rig),
            "clips": [
                {
                    "id": "fit.front_threequarter",
                    "state": "fit",
                    "file": "frame.json",
                    "timebase": 1000,
                    "loop": False,
                    "source_mode": "external",
                    "expected_files": {
                        f.name: assets.sha256(f.read_bytes()) for f in source.iterdir()
                    },
                }
            ],
        },
    )
    imported = parts_motion.import_motion(assembly, spec, source)
    annotations = root / imported["annotation_template"]
    draft = w.decode_json(annotations.read_bytes())
    for clip in draft["clips"].values():
        for sample in clip.values():
            sample["pose_id"] = "fit.01"
            for name, socket in sample["sockets"].items():
                socket.update(
                    position=SOCKETS[name], visible=True, confidence="verified"
                )
    write(annotations, draft)
    parts_motion.freeze_motion(assembly, imported["candidate"], annotations)
    labels, colors, _ = authored_labels(body)
    labels_dir = root / "inputs/labels"
    labels_dir.mkdir()
    labels.save(labels_dir / "fit.01.png")
    colors_path = root / "inputs/body-colors.json"
    write(colors_path, colors)
    segmented = parts_segments.segment_body(
        assembly,
        root / "inputs/body-segments",
        colors=colors_path,
        labels=labels_dir,
        body_id="illustration.sd.body",
        tolerance=0,
    )
    parts_import.import_pack(
        assembly, Path(segmented["spec"]), Path(segmented["source_root"])
    )
    index, registry = w.read_workspace(assembly)
    rendered = p._render_alone(root, index, registry, None, ["fit"])[0]["fit.01"]
    assert rendered.tobytes() == body.tobytes(), (
        "body layer reconstruction changed source RGBA"
    )
    write(
        out / "reference-lock.json",
        {
            "rig": index["rig"],
            "motion": index["motion"],
            "body": index["body"],
            "body_rgba_sha256": assets.sha256(body.tobytes()),
            "native_size": list(body.size),
            "body_samples_pixel_identical": 1,
            "animation_validated": False,
            "release_ready": False,
            "ownership": "authored polygons for this reference only",
            "sampling": "smooth",
        },
    )
    return segmented


def authored_checks(root, name, body):
    """Conservative native fitting interfaces, separate from render masks.

    These assert central coverage/allowed location only, not art approval.
    """
    _, _, masks = authored_labels(body)
    yy, xx = np.indices((body.height, body.width))
    slot, _, _, bounds = PARTS[name]
    left, top, right, bottom = bounds
    allowed = (
        (xx >= max(0, left - 32))
        & (xx < min(body.width, right + 32))
        & (yy >= max(0, top - 32))
        & (yy < min(body.height, bottom + 32))
    )
    required = np.zeros_like(allowed)
    protected = np.zeros_like(allowed)
    if slot == "upper":
        required = masks["torso"] & (xx >= 589) & (xx < 701) & (yy >= 466) & (yy < 625)
        protected = masks["hand_front"] | masks["hand_back"]
    elif slot == "lower":
        required = masks["waist"] & (xx >= 560) & (xx < 722) & (yy >= 692) & (yy < 730)
    elif slot == "footwear":
        # Both foot cores must be covered; a lost shoe cannot disappear from QA.
        feet = masks["foot_front"] | masks["foot_back"]
        from PIL import ImageFilter

        required = (
            np.asarray(
                Image.fromarray(feet.astype("uint8") * 255).filter(
                    ImageFilter.MinFilter(13)
                )
            )
            > 0
        )
    elif slot == "socks":
        # Authored ankle/calf coverage, independent of a shoe or sock drawing.
        front = masks["leg_front"] & (yy >= 1030) & (yy < 1105)
        back = masks["leg_back"] & (yy >= 1020) & (yy < 1085)
        from PIL import ImageFilter

        required = (
            np.asarray(
                Image.fromarray((front | back).astype("uint8") * 255).filter(
                    ImageFilter.MinFilter(7)
                )
            )
            > 0
        )
    elif slot == "hair":
        protected = (xx >= 610) & (xx < 748) & (yy >= 302) & (yy < 355)
    elif slot == "eyes":
        protected = (yy >= 350) & (yy < 399) & masks["head"]
    folder = root / "inputs/qa" / name
    folder.mkdir(parents=True, exist_ok=True)
    checks = {}
    for key, mask in (
        ("allowed", allowed),
        ("required", required),
        ("protected", protected),
    ):
        if mask.any():
            file = folder / f"{key}.png"
            Image.fromarray(mask.astype("uint8") * 255).save(file)
            checks[key] = file.relative_to(root).as_posix()
    return {"fit.01": checks}


def fitting_operations(name):
    slot, _, _, bounds = PARTS[name]
    operations = [{"type": "fit-alpha-box", "bounds": bounds}]
    if slot == "footwear":
        operations = [
            {
                "type": "fit-alpha-box",
                "source_bounds": [0, 900, 620, 1254],
                "bounds": [425, 1060, 583, 1230],
            },
            {
                "type": "fit-alpha-box",
                "source_bounds": [620, 900, 1254, 1254],
                "bounds": [650, 1068, 856, 1219],
            },
        ]
    if slot == "socks":
        operations = [
            {
                "type": "fit-alpha-box",
                "source_bounds": [0, 0, 620, 1254],
                "bounds": [432, 967, 574, 1230],
            },
            {
                "type": "fit-alpha-box",
                "source_bounds": [620, 0, 1254, 1254],
                "bounds": [640, 963, 851, 1215],
            },
        ]
    if slot == "socks":
        operations.append(
            {
                "type": "fill-small-holes",
                "bounds": [428, 950, 858, 1240],
                "max_pixels": 12,
            }
        )
    return operations


def part_config(out, name):
    root = out / "library"
    slot, node, socket, _ = PARTS[name]
    identity = "illustration." + name
    config = {
        "kind": "parts-production-config",
        "schema_version": 1,
        "part_id": identity,
        "design_id": identity,
        "slot": slot,
        "node": node,
        "socket": socket,
        "states": ["fit"],
        "mode": "raster",
        "visibility": "zmap",
        "sheet": {
            "scale": 1,
            "gutter": 0,
            "columns": 1,
            "page_size": 1,
            "aspect": "1:1",
        },
        "cut": {
            "method": "part-only",
            "min_island": 0,
            "outline": False,
            "fill_holes": 0,
            "close_body": 0,
        },
        "segments": "inputs/body-segments/segments.json",
        "pose_checks": authored_checks(
            root, name, Image.open(out / "art/body.png").convert("RGBA")
        ),
        "prompt": {
            "text": f"Draw only the {slot} as a smooth high-resolution illustration on the fixed pose. Preserve native canvas coordinates, continuous fabric and smooth alpha. Never draw the body or impose a pixel grid.",
            "refs": ["inputs/body/body.png"],
        },
    }
    if slot == "upper":
        config["pieces"] = [
            {
                "id": "sleeve_back",
                "node": "arm_back",
                "segments": ["arm_back", "hand_back"],
            },
            {"id": "torso", "node": "torso", "segments": ["*"]},
            {
                "id": "sleeve_front",
                "node": "arm_front",
                "segments": ["arm_front", "hand_front"],
            },
        ]
    if slot == "lower":
        config["pieces"] = [
            {"id": "back", "node": "leg_back", "segments": ["leg_back", "foot_back"]},
            {
                "id": "front",
                "node": "leg_front",
                "segments": ["leg_front", "foot_front"],
            },
            {"id": "waist", "node": "waist", "segments": ["*"]},
        ]
    if slot in ("footwear", "socks"):
        config["pieces"] = [
            {
                "id": "back",
                "node": "sock_back" if slot == "socks" else "shoe_back",
                "segments": ["leg_back", "foot_back"],
            },
            {
                "id": "front",
                "node": "sock_front" if slot == "socks" else "shoe_front",
                "segments": ["*"],
            },
        ]
    if slot == "socks":
        # This foot-covering design opts in. Calf-only socks can omit replaces.
        config["pieces"][0]["replaces"] = ["foot_back"]
        config["pieces"][1]["replaces"] = ["foot_front"]
    return config


def import_part(out, name):
    root = out / "library"
    assembly = root / "assembly.json"
    index, registry = w.read_workspace(assembly)
    slot, node, socket, bounds = PARTS[name]
    identity = "illustration." + name
    source = root / "inputs/parts" / name
    source.mkdir(parents=True, exist_ok=True)
    shutil.copy2(out / "art" / f"{name}.png", source / "part.png")
    reference = {
        "file": "part.png",
        "sha256": assets.sha256((source / "part.png").read_bytes()),
    }
    spec = source / "import.json"
    write(
        spec,
        {
            "kind": "parts-pack-import",
            "schema_version": 1,
            "id": identity,
            "design_id": identity,
            "role": "part",
            "style": "illustration.cel.v1",
            "rig": index["rig"],
            "motion": index["motion"],
            "view": "front_threequarter",
            "binding_id": identity + ".binding",
            "occupies": [slot],
            "pieces": [
                {
                    "id": slot,
                    "slot": slot,
                    "node": node,
                    "mode": "raster",
                    "pose_map": {
                        "fit.01": {
                            "image": reference,
                            "format": "full-canvas",
                            "pivot": SOCKETS[socket],
                            "target_socket": socket,
                        }
                    },
                }
            ],
        },
    )
    imported = parts_import.import_pack(assembly, spec, source)
    config = part_config(out, name)
    if (root / "inputs/body-segments-socks").exists():
        config["segments"] = "inputs/body-segments-socks/segments.json"
    file = out / "configs" / f"{name}.json"
    write(file, config)
    if (root / "productions" / identity / "production.json").exists():
        p.configure(assembly, identity, file)
    else:
        p.init(assembly, file)
    index, _ = w.read_workspace(assembly)
    p.adopt(assembly, identity, root / index["documents"][identity])
    operations = fitting_operations(name)
    edited = parts_part_edit.edit(assembly, identity, "fit.01", operations)
    p.select_candidate(assembly, identity, "fit.01", edited["preview_candidate"])
    checked = p.check(assembly, identity)
    built = p.build(assembly, identity)
    guide = p.sheet(assembly, identity, poses=["fit.01"])
    write(
        out / f"{name}.result.json",
        {
            "import": imported,
            "edit": edited,
            "check": checked,
            "build": built,
            "future_generation_reference": guide["reference"],
            "first_generation_reference": "raw/body-magenta.png; see prompts.json",
            "art_approved": False,
        },
    )
    return built


def slice_part(out, name):
    """Ingest a native part-only page generated against frozen QA guides."""
    root = out / "library"
    assembly = root / "assembly.json"
    identity = "illustration." + name
    prepared = w.decode_json((out / f"{name}.generation-reference.json").read_bytes())
    for page in prepared["pages"]:
        shutil.copy2(out / "raw" / f"{name}.png", root / page["art"])
    sliced = p.slice_batch(assembly, identity, batch=prepared["batch"])
    state = p._load(root, identity)
    source = root / state["tracks"]["main"]["poses"]["fit.01"]["selected"]
    shutil.copy2(source / "part.png", out / "art" / f"{name}.png")
    edited = parts_part_edit.edit(
        assembly, identity, "fit.01", fitting_operations(name)
    )
    p.select_candidate(assembly, identity, "fit.01", edited["preview_candidate"])
    checked = p.check(assembly, identity)
    built = p.build(assembly, identity)
    guide = p.sheet(assembly, identity, poses=["fit.01"])
    write(
        out / f"{name}.result.json",
        {
            "slice": sliced,
            "edit": edited,
            "check": checked,
            "build": built,
            "first_generation_reference": prepared["reference"],
            "future_generation_reference": guide["reference"],
            "art_approved": False,
        },
    )
    return built


def ensure_socks_catalog(assembly):
    root = assembly.absolute().parent
    index, registry = w.read_workspace(assembly)
    catalog = registry[index["catalog"]["id"]]
    if any(slot["id"] == "socks" for slot in catalog["slots"]):
        return
    value = {
        **catalog,
        "slots": [
            *catalog["slots"],
            {"id": "socks", "group": "parts", "label": "양말"},
        ],
    }
    value.pop("digest", None)
    value = contract.seal(value)
    folder = assets.publish_object(
        root,
        "catalogs",
        value["id"],
        value["digest"],
        {"catalog.json": w.json_bytes(value)},
    )
    index["catalog"] = contract.reference(value)
    index["documents"][value["id"]] = (
        (folder / "catalog.json").relative_to(root).as_posix()
    )
    contract.validate_bundle(
        [value if d["id"] == value["id"] else d for d in registry.values()]
    )
    write(assembly, index)


def extend_socks(out):
    """Add independently ordered socks without changing the body artwork."""
    root = out / "library"
    assembly = root / "assembly.json"
    index, registry = w.read_workspace(assembly)
    topology = registry[registry[index["rig"]["id"]]["topology"]["id"]]
    topology = {
        **topology,
        "nodes": [
            {
                "id": name,
                "parent": None if name == "root" else "root",
                "required": name == "root",
            }
            for name in NODES
        ],
        "layers": NODES,
        "slots": [
            {"id": slot, "nodes": nodes, "sockets": list(SOCKETS), "optional": True}
            for slot, nodes in SLOTS.items()
        ],
    }
    topology.pop("digest", None)
    path = out / "topology-with-socks.json"
    write(path, topology)
    labels, colors, _ = authored_labels(
        Image.open(out / "art/body.png").convert("RGBA")
    )
    folder = root / "inputs/labels-socks"
    folder.mkdir(exist_ok=True)
    labels.save(folder / "fit.01.png")
    colors_path = root / "inputs/body-colors-socks.json"
    write(colors_path, colors)
    result = parts_body_refine.refine(
        assembly,
        topology=path,
        labels=folder,
        colors=colors_path,
        out=root / "inputs/body-segments-socks",
        body_id="illustration.sd.body",
    )
    for file in (root / "productions").glob("*/production.json"):
        state = p._load(root, file.parent.name)
        config = state["config"]
        config["segments"] = "inputs/body-segments-socks/segments.json"
        target = (
            out / "configs" / (file.parent.name.removeprefix("illustration.") + ".json")
        )
        if config["slot"] == "footwear":
            config["pieces"] = part_config(out, "footwear.sneakers")["pieces"]
        write(target, config)
        p.configure(assembly, file.parent.name, target)
        if not any(
            row.get("selected")
            for track in state["tracks"].values()
            for row in track["poses"].values()
        ):
            continue
        p.check(assembly, file.parent.name)
        p.build(assembly, file.parent.name)
        p.sheet(assembly, file.parent.name, poses=["fit.01"])
    ensure_socks_catalog(assembly)
    index, _ = w.read_workspace(assembly)
    lock = out / "reference-lock.json"
    old = w.decode_json(lock.read_bytes())
    write(out / "reference-lock.before-socks.json", old)
    write(
        lock,
        {
            **old,
            "rig": index["rig"],
            "motion": index["motion"],
            "body": index["body"],
            "metadata_revision": "separate socks slot; body pixels/timing unchanged",
        },
    )
    return result


def reference(out, name):
    root = out / "library"
    assembly = root / "assembly.json"
    config = part_config(out, name)
    config["segments"] = (
        "inputs/body-segments-socks/segments.json"
        if (root / "inputs/body-segments-socks").exists()
        else config["segments"]
    )
    if name == "socks.crew":
        config["prompt"]["text"] = (
            "Draw ONLY TWO separate cream ribbed crew socks with two narrow navy cuff stripes, on the authored bare ankles/calf regions of this exact body pose. The left near leg faces almost forward; the right far leg is three-quarter side view. Continuous smooth illustration shading; preserve native 1254 canvas coordinates. No shoes, no bare feet/toes, no skin, no body. Sock fabric may extend over the foot under the separately rendered shoe. Leave all other pixels flat magenta. Required mask is ankle/calf coverage, not the shoe silhouette. Cuffs sit above ankle; lower fabric extends slightly under the shoe opening."
        )
    file = out / "configs" / f"{name}.json"
    write(file, config)
    if (root / "productions" / config["part_id"] / "production.json").exists():
        p.configure(assembly, config["part_id"], file)
    else:
        p.init(assembly, file)
    result = p.sheet(assembly, config["part_id"], poses=["fit.01"])
    write(out / f"{name}.generation-reference.json", result)
    return result


def refit(out, name):
    """Return to immutable original pixels, never repeatedly resample a fit."""
    root = out / "library"
    assembly = root / "assembly.json"
    identity = "illustration." + name
    state = p._load(root, identity)
    original = state["tracks"]["main"]["poses"]["fit.01"]["selected"]
    while True:
        record = w.decode_json((root / original / "candidate.json").read_bytes())
        parent = (
            record.get("method", {})
            .get("programmatic_edit", {})
            .get("source_candidate")
        )
        if parent is None:
            break
        original = parent
    edited = parts_part_edit.edit(
        assembly, identity, "fit.01", fitting_operations(name), candidate=original
    )
    p.select_candidate(assembly, identity, "fit.01", edited["preview_candidate"])
    checked = p.check(assembly, identity)
    built = p.build(assembly, identity)
    guide = p.sheet(assembly, identity, poses=["fit.01"])
    file = out / f"{name}.result.json"
    previous = w.decode_json(file.read_bytes())
    write(
        file,
        {
            **previous,
            "edit": edited,
            "check": checked,
            "build": built,
            "future_generation_reference": guide["reference"],
        },
    )
    return built


def verify(out):
    """Exercise all optional selections without generating appearance clips."""
    from itertools import product

    root = out / "library"
    assembly = root / "assembly.json"
    index, registry = w.read_workspace(assembly)
    lock = w.decode_json((out / "reference-lock.json").read_bytes())
    body = Image.open(out / "art/body.png").convert("RGBA")
    assert assets.sha256(body.tobytes()) == lock["body_rgba_sha256"]
    reconstructed = p._render_alone(root, index, registry, None, ["fit"])[0]["fit.01"]
    assert reconstructed.tobytes() == body.tobytes()
    available = [
        (PARTS[name][0], "illustration." + name)
        for name in PARTS
        if "illustration." + name in registry
    ]
    player = PartsPlayer.from_workspace(assembly, state="fit")
    rows = []
    for flags in product((False, True), repeat=len(available)):
        selected = {
            slot: contract.reference(registry[identity]) if flag else None
            for (slot, identity), flag in zip(available, flags)
        }
        player.set_selection(selected)
        player.seek(Fraction(0))
        scene = player.layers(interpolate=False)
        assert scene["motion"] == index["motion"] and scene["sample_id"] == "sample.0"
        assert scene["masks"] == []
        body_nodes = {
            layer["node"] for layer in scene["layers"] if layer["slot"] is None
        }
        assert ("foot_back" in body_nodes) == (selected.get("socks") is None)
        assert ("foot_front" in body_nodes) == (selected.get("socks") is None)
        assert {
            "head",
            "torso",
            "arm_front",
            "hand_front",
            "leg_front",
            "leg_back",
        } <= body_nodes
        image, _ = player.frame()
        rows.append(
            {
                "selected": [slot for slot, value in selected.items() if value],
                "rgba_sha256": assets.sha256(image.tobytes()),
                "runtime_masks": len(scene["masks"]),
                "sample": scene["sample_id"],
            }
        )
    defaults = {
        slot: contract.reference(registry[identity]) for slot, identity in available
    }
    wall = Image.new("RGB", (1024, 384), (238, 238, 232))
    draw = ImageDraw.Draw(wall)
    for n, (label, sock, shoe) in enumerate(
        (
            ("BARE", False, False),
            ("SOCKS ONLY", True, False),
            ("SHOES ONLY", False, True),
            ("SOCKS + SHOES", True, True),
        )
    ):
        selected = {
            **defaults,
            "socks": defaults.get("socks") if sock else None,
            "footwear": defaults.get("footwear") if shoe else None,
        }
        player.set_selection(selected)
        image, _ = player.frame()
        if sock and not shoe:
            image.save(out / "socks-alone.png")
        preview = image.crop((360, 895, 900, 1254)).resize(
            (256, 170), Image.Resampling.LANCZOS
        )
        wall.paste(preview, (n * 256, 80), preview)
        draw.text((n * 256 + 15, 24), label, fill="black")
    draw.text(
        (15, 315),
        "Independent sock/shoe selections. One shared reference pose; zero appearance clips.",
        fill="black",
    )
    wall.save(out / "socks-independent.png")
    result = {
        "combinations_rendered": len(rows),
        "unique_composite_hashes": len({row["rgba_sha256"] for row in rows}),
        "body_source_rgba_identical": True,
        "runtime_masks": 0,
        "appearance_clips_baked": 0,
        "socks_foot_replacement": "explicit in this full-foot design only",
        "animation_validated": False,
        "combinations": rows,
    }
    write(out / "verification.json", result)
    return result


def finish(out):
    root = out / "library"
    assembly = root / "assembly.json"
    index, registry = w.read_workspace(assembly)
    selected = {slot: None for slot in SLOTS}
    for name in PARTS:
        identity = "illustration." + name
        if identity in registry:
            selected[PARTS[name][0]] = contract.reference(registry[identity])
    selection = out / "selection.json"
    write(
        selection,
        {
            "kind": "parts-appearance-selection",
            "schema_version": 1,
            "id": "illustration.first-fit",
            "selections": selected,
        },
    )
    parts_appearance.create(assembly, selection)
    exported = export_parts.export(
        assembly,
        out / "export",
        interpolation="frame",
        title="고해상도 SD · 정적 파츠 피팅 · 동작 검증 전",
    )
    player = PartsPlayer.from_workspace(assembly, state="fit")
    player.seek(Fraction(0))
    image, _ = player.frame()
    image.save(out / "assembled.png")
    index, registry = w.read_workspace(assembly)
    body = p._render_alone(root, index, registry, None, ["fit"])[0]["fit.01"]
    body.save(out / "body-only.png")
    wall = Image.new("RGB", (1024, 768), (238, 238, 232))
    d = ImageDraw.Draw(wall)
    for n, (label, im) in enumerate(
        (
            ("BODY / NATIVE ILLUSTRATION", body),
            ("INDEPENDENT LAYERS / STATIC FIT", image),
        )
    ):
        preview = im.resize((512, 512), Image.Resampling.LANCZOS)
        wall.paste(preview, (n * 512, 64), preview)
        d.text((n * 512 + 24, 24), label, fill="black")
    d.text(
        (24, 640),
        "Static fitting proof. One reference pose; walk/run not generated or approved.",
        fill="black",
    )
    wall.save(out / "comparison.png")
    lock = w.decode_json((out / "reference-lock.json").read_bytes())
    assert index["motion"] == lock["motion"] and index["body"] == lock["body"]
    result = {
        "export": exported,
        "production": p.status(assembly),
        "body_and_motion_unchanged": True,
        "appearance_clips_baked": 0,
        "static_pose_samples": 1,
        "animation_validated": False,
        "release_ready": False,
    }
    for file in (root / "productions").glob("*/production.json"):
        state = p._load(root, file.parent.name)
        summary = out / (
            file.parent.name.removeprefix("illustration.") + ".result.json"
        )
        if summary.exists():
            previous = w.decode_json(summary.read_bytes())
            write(
                summary,
                {
                    **previous,
                    "current_build": state["built"],
                    "current_check": state["tracks"]["main"]["poses"]["fit.01"][
                        "check"
                    ],
                    "current_motion": index["motion"],
                },
            )
    write(out / "result.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "stage",
        choices=[
            "prepare",
            "import",
            "refit",
            "extend-socks",
            "reference",
            "slice",
            "finish",
            "verify",
        ],
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--part", choices=PARTS)
    args = parser.parse_args()
    args.out = args.out.absolute()
    stages = {
        "prepare": prepare,
        "import": import_part,
        "refit": refit,
        "extend-socks": extend_socks,
        "reference": reference,
        "slice": slice_part,
        "finish": finish,
        "verify": verify,
    }
    if args.stage in ("import", "refit", "reference", "slice"):
        if args.part is None:
            parser.error("this stage requires --part")
        result = stages[args.stage](args.out, args.part)
    else:
        result = stages[args.stage](args.out)
    print(result)
