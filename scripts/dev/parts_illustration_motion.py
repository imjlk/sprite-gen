# SPDX-License-Identifier: Apache-2.0
"""Actual video motion + unapproved source-art registration candidates.

Never simulates body motion, generates appearance clips, or approves geometry.
The skeleton knots are authored for the two hash-bound clips, not a general
pose detector. Clothing registrations are references for frame-specific art.
"""

from pathlib import Path
from copy import deepcopy
import argparse
from PIL import Image, ImageDraw
from sprite_gen._deps import np
from sprite_gen.spec import parts_assets as a, parts_contract as c, parts_workspace as w
from sprite_gen.curate import parts_motion, parts_appearance
from sprite_gen.frames import parts_segments, parts_import, parts_production as p
from sprite_gen.compose import export_parts
from scripts.dev.parts_illustration_demo import PARTS, PROFILE_SOURCE_RGBA_SHA256, write

# Each knot: near/far knees, ankles, toes, wrists, elbows. Coordinates are
# authored on the engine's cycle canvases and remain visibly unverified.
KNOTS = {
    "walk": {
        0: [
            [302, 494],
            [444, 484],
            [281, 630],
            [433, 625],
            [310, 667],
            [492, 649],
            [328, 421],
            [428, 362],
            [296, 348],
            [424, 320],
        ],
        3: [
            [276, 500],
            [436, 487],
            [247, 628],
            [480, 624],
            [267, 661],
            [546, 629],
            [330, 418],
            [438, 412],
            [297, 348],
            [431, 348],
        ],
        6: [
            [293, 494],
            [407, 512],
            [273, 591],
            [417, 650],
            [267, 646],
            [481, 664],
            [313, 416],
            [444, 428],
            [292, 340],
            [438, 351],
        ],
        9: [
            [350, 496],
            [358, 493],
            [371, 633],
            [334, 630],
            [409, 662],
            [360, 658],
            [258, 418],
            [492, 420],
            [282, 340],
            [464, 348],
        ],
        12: [
            [373, 500],
            [344, 528],
            [402, 634],
            [321, 625],
            [465, 646],
            [365, 648],
            [224, 414],
            [526, 408],
            [265, 340],
            [478, 344],
        ],
        15: [
            [381, 525],
            [325, 517],
            [380, 655],
            [278, 595],
            [443, 673],
            [331, 642],
            [195, 412],
            [532, 410],
            [253, 338],
            [480, 343],
        ],
        18: [
            [346, 530],
            [400, 518],
            [354, 655],
            [361, 582],
            [394, 675],
            [347, 635],
            [253, 413],
            [518, 421],
            [279, 342],
            [473, 351],
        ],
        21: [
            [288, 529],
            [452, 490],
            [286, 651],
            [403, 606],
            [326, 673],
            [416, 649],
            [282, 420],
            [449, 406],
            [286, 341],
            [441, 346],
        ],
    },
    "run": {
        0: [
            [300, 500],
            [450, 443],
            [244, 600],
            [420, 555],
            [263, 650],
            [463, 588],
            [342, 363],
            [455, 409],
            [294, 334],
            [438, 340],
        ],
        3: [
            [315, 482],
            [416, 518],
            [280, 524],
            [416, 661],
            [263, 558],
            [482, 672],
            [327, 417],
            [467, 423],
            [282, 347],
            [461, 369],
        ],
        5: [
            [393, 465],
            [413, 504],
            [355, 610],
            [350, 534],
            [402, 665],
            [330, 578],
            [252, 414],
            [534, 363],
            [276, 343],
            [485, 336],
        ],
        8: [
            [316, 474],
            [461, 475],
            [252, 526],
            [482, 605],
            [243, 604],
            [544, 613],
            [359, 305],
            [465, 399],
            [285, 306],
            [436, 348],
        ],
        10: [
            [356, 494],
            [446, 528],
            [302, 541],
            [436, 660],
            [289, 584],
            [492, 672],
            [281, 430],
            [514, 403],
            [278, 355],
            [463, 354],
        ],
        13: [
            [395, 454],
            [379, 523],
            [349, 584],
            [371, 580],
            [337, 638],
            [376, 587],
            [225, 377],
            [528, 250],
            [288, 302],
            [467, 316],
        ],
        15: [
            [388, 478],
            [388, 543],
            [356, 561],
            [403, 620],
            [349, 609],
            [456, 625],
            [201, 365],
            [498, 239],
            [254, 299],
            [464, 304],
        ],
        18: [
            [318, 541],
            [445, 503],
            [310, 657],
            [414, 546],
            [342, 675],
            [373, 573],
            [262, 421],
            [531, 383],
            [275, 355],
            [475, 357],
        ],
    },
}


def interpolate_knots(state, n, count):
    keys = sorted(KNOTS[state])
    left = max((k for k in keys if k <= n), default=keys[-1])
    right = next((k for k in keys if k > n), keys[0] + count)
    end = KNOTS[state][right % count]
    start = KNOTS[state][left]
    return np.array(start) + (np.array(end) - np.array(start)) * (
        (n - left) / (right - left)
    )


def head_box(body):
    rgba = np.asarray(body)
    yy, xx = np.indices(rgba.shape[:2])
    ink = (rgba[..., 3] > 128) & (yy < 225) & (xx > 210) & (xx < 510)
    ys, xs = np.nonzero(ink)
    if not len(xs):
        raise ValueError("head registration has no source pixels")
    return [int(xs.min()), int(ys.min()), int(xs.max() + 1), int(ys.max() + 1)]


def labels(body, points, head):
    colors = {
        name: f"#{(i + 1) * 7919:06x}"
        for i, name in enumerate(
            [
                "root",
                "leg_back",
                "foot_back",
                "arm_back",
                "hand_back",
                "waist",
                "torso",
                "head",
                "leg_front",
                "foot_front",
                "arm_front",
                "hand_front",
            ]
        )
    }
    im = Image.new("RGBA", body.size)
    d = ImageDraw.Draw(im)

    def rgb(name):
        return tuple(bytes.fromhex(colors[name][1:])) + (255,)

    def line(name, pts, width):
        d.line([tuple(map(round, q)) for q in pts], fill=rgb(name), width=width)

    def ball(name, pt, r):
        x, y = pt
        d.ellipse(
            (round(x - r), round(y - r), round(x + r), round(y + r)), fill=rgb(name)
        )

    cx = (head[0] + head[2]) / 2
    shoulder_y = head[3] + 24
    waist_y = 388
    d.rectangle((cx - 28, shoulder_y, cx + 35, waist_y), fill=rgb("torso"))
    d.rectangle((cx - 37, 378, cx + 38, 410), fill=rgb("waist"))
    d.ellipse(tuple(head), fill=rgb("head"))
    for i, side in enumerate(["front", "back"]):
        shoulder = [cx - 51 if i == 0 else cx + 58, shoulder_y + 14]
        line("arm_" + side, [shoulder, points[8 + i], points[6 + i]], 25)
        ball("hand_" + side, points[6 + i], 15)
        hip = [cx - 32 if i == 0 else cx + 35, 420]
        line("leg_" + side, [hip, points[i], points[2 + i]], 27)
        line("foot_" + side, [points[2 + i], points[4 + i]], 21)
    return im, colors


def transform(image, matrix, size=(768, 768)):
    # Premultiplied sampling avoids dark halos; this is art registration, never motion generation.
    inv = np.linalg.inv(matrix)
    return (
        image.convert("RGBa")
        .transform(
            size,
            Image.Transform.AFFINE,
            tuple(inv[:2, :].ravel()),
            Image.Resampling.BICUBIC,
        )
        .convert("RGBA")
    )


def box_matrix(source, target):
    factor = min(
        (target[2] - target[0]) / (source[2] - source[0]),
        (target[3] - target[1]) / (source[3] - source[1]),
    )
    sc = np.array([(source[0] + source[2]) / 2, (source[1] + source[3]) / 2])
    tc = np.array([(target[0] + target[2]) / 2, (target[1] + target[3]) / 2])
    m = np.eye(3)
    m[:2, :2] *= factor
    m[:2, 2] = tc - sc * factor
    return m


def foot_matrix(start, end, target_start, target_end):
    src = np.array(end) - start
    dst = np.array(target_end) - target_start
    scale = np.linalg.norm(dst) / np.linalg.norm(src)
    angle = np.arctan2(dst[1], dst[0]) - np.arctan2(src[1], src[0])
    rot = scale * np.array(
        [[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]]
    )
    m = np.eye(3)
    m[:2, :2] = rot
    m[:2, 2] = target_start - rot @ start
    return m


def validate_sources(source, out):
    body = Image.open(source / "art/body.png").convert("RGBA")
    if a.sha256(body.tobytes()) != PROFILE_SOURCE_RGBA_SHA256:
        raise ValueError("source body changed; author a new registration profile")
    expected = {
        "walk": "f9e3fe250302465e110665e2d09f39181a3fd2318168d483250f366e909ca6bf",
        "run": "2d1f18ae6f8594732dc2e9976e174c9fc0cb5f75976aaee378f0db69aea62e02",
    }
    for state, digest in expected.items():
        if a.sha256((out / (state + ".mp4")).read_bytes()) != digest:
            raise ValueError("new video needs new authored knots")
    cycles = {
        "walk": "3f8f47d0273471755a6053009bac6eb6e69d87f16b59e05b1d559c01d3923792",
        "run": "7fa1f175e0ad3631f310015128e15005f53788a7d663857c51cb3a23c215270a",
    }
    for state, digest in cycles.items():
        files = sorted((out / f"{state}-loop/cycle").glob("*.png"))
        if (
            a.sha256("".join(a.sha256(file.read_bytes()) for file in files).encode())
            != digest
        ):
            raise ValueError("cycle pixels changed; author new landmarks")


def prepare(source, out):
    root = out / "library"
    assembly = root / "assembly.json"
    if root.exists():
        raise ValueError("preserve this experiment; choose a new output directory")
    validate_sources(source, out)
    oldroot = source / "library"
    oldindex, oldregistry = w.read_workspace(oldroot / "assembly.json")
    w.initialize(root, "human.sd.v1")
    topo = deepcopy(oldregistry[oldregistry[oldindex["rig"]["id"]]["topology"]["id"]])
    topo.pop("digest")
    topo["id"] = "illustration.motion.topology"
    topo["sockets"] = [{"id": "root", "node": "root", "required": True}]
    for slot in topo["slots"]:
        slot["sockets"] = ["root"]
    topo = c.seal(topo)
    rig = deepcopy(oldregistry[oldindex["rig"]["id"]])
    rig.pop("digest")
    rig.update(
        id="illustration.motion.rig",
        canvas={"width": 768, "height": 768},
        topology=c.reference(topo),
    )
    rig = c.seal(rig)
    cat = deepcopy(oldregistry[oldindex["catalog"]["id"]])
    cat.pop("digest")
    cat.update(
        id="illustration.motion.catalog",
        rig=c.reference(rig),
        topology=c.reference(topo),
        presets=[],
    )
    for slot in cat["slots"]:
        slot.update(options=[], default=None)
    cat = c.seal(cat)
    index = {
        "kind": "parts-workspace",
        "schema_version": 1,
        "id": "illustration.motion.workspace",
        "documents": {
            topo["id"]: "profiles/topology.json",
            rig["id"]: "profiles/rig.json",
            cat["id"]: "profiles/catalog.json",
        },
        "rig": c.reference(rig),
        "motion": None,
        "body": [],
        "catalog": c.reference(cat),
        "appearance": None,
        "states": ["walk", "run"],
        "fitting": [],
        "stability": [],
    }
    for name, file in index["documents"].items():
        write(root / file, {topo["id"]: topo, rig["id"]: rig, cat["id"]: cat}[name])
    write(assembly, index)
    clips = []
    poses = {}
    geometry = {}
    counts = {}
    for state in ["walk", "run"]:
        report = w.decode_json(
            (out / f"{state}-loop/loop.loop.report.json").read_bytes()
        )
        # The authored knots are tied to the observed, unmodified cycle, not a new take.
        count = {"walk": 23, "run": 20}[state]
        if (
            report["cycle"]["length"] != count
            or report["cycle"]["start"] != {"walk": 52, "run": 64}[state]
        ):
            raise ValueError("new cycle needs new reviewed knots")
        folder = root / "inputs/body" / state
        folder.mkdir(parents=True)
        files = sorted((out / f"{state}-loop/cycle").glob("*.png"))
        if len(files) != count:
            raise ValueError("cycle changed")
        frames = []
        counts[state] = count
        for n, file in enumerate(files):
            body = Image.new("RGBA", (768, 768))
            body.alpha_composite(Image.open(file).convert("RGBA"))
            pose = f"{state}.{n + 1:02d}"
            body.save(folder / f"{n:03d}.png")
            points = interpolate_knots(state, n, count)
            head = head_box(body)
            poses[pose] = body
            geometry[pose] = {
                "points": points.tolist(),
                "head": head,
                "source": str(file),
                "source_sha256": a.sha256(file.read_bytes()),
            }
            frames.append({"id": pose, "file": f"{n:03d}.png", "duration": 1 / 24})
        write(
            folder / "frames.json",
            {"kind": "sprite-gen-asset", "version": 1, "frames": frames},
        )
        clips.append(
            {
                "id": state + ".front_threequarter",
                "state": state,
                "file": state + "/frames.json",
                "timebase": 24000,
                "fps": "24",
                "loop": True,
                "source_mode": "external",
                "expected_files": {
                    state + "/" + f.name: a.sha256(f.read_bytes())
                    for f in folder.iterdir()
                },
            }
        )
    write(
        root / "inputs/motion.json",
        {
            "kind": "parts-motion-import",
            "schema_version": 1,
            "id": "illustration.motion.actual-video",
            "rig": c.reference(rig),
            "clips": clips,
        },
    )
    imported = parts_motion.import_motion(
        assembly, root / "inputs/motion.json", root / "inputs/body"
    )
    annotations = root / imported["annotation_template"]
    draft = w.decode_json(annotations.read_bytes())
    for clip_id, samples in draft["clips"].items():
        state = clip_id.split(".")[0]
        for sample_id, sample in samples.items():
            n = int(sample_id.split(".")[-1])
            pose = f"{state}.{n + 1:02d}"
            sample["pose_id"] = pose
            for socket in sample["sockets"].values():
                socket.update(position=[0, 0], visible=True, confidence="verified")
    write(annotations, draft)
    parts_motion.freeze_motion(assembly, imported["candidate"], annotations)
    labelsdir = root / "inputs/labels"
    labelsdir.mkdir()
    for pose, body in poses.items():
        im, colors = labels(body, geometry[pose]["points"], geometry[pose]["head"])
        im.save(labelsdir / f"{pose}.png")
    write(root / "inputs/colors.json", colors)
    segmented = parts_segments.segment_body(
        assembly,
        root / "inputs/body-segments",
        colors=root / "inputs/colors.json",
        labels=labelsdir,
        body_id="illustration.motion.body",
        tolerance=0,
    )
    parts_import.import_pack(
        assembly, Path(segmented["spec"]), Path(segmented["source_root"])
    )
    return register_parts(source, out)


def register_parts(source, out):
    """Resume registration on an already imported body; never modify motion."""
    validate_sources(source, out)
    root = out / "library"
    assembly = root / "assembly.json"
    oldroot = source / "library"
    poses = {}
    geometry = {}
    counts = {}
    for state in ("walk", "run"):
        files = sorted((out / f"{state}-loop/cycle").glob("*.png"))
        counts[state] = len(files)
        for n, file in enumerate(files):
            pose = f"{state}.{n + 1:02d}"
            body = Image.open(root / f"inputs/body/{state}/{n:03d}.png").convert("RGBA")
            poses[pose] = body
            geometry[pose] = {
                "points": interpolate_knots(state, n, len(files)).tolist(),
                "head": head_box(body),
                "source": str(file),
                "source_sha256": a.sha256(file.read_bytes()),
            }
    index, registry = w.read_workspace(assembly)
    rig = registry[index["rig"]["id"]]
    bodyframes = p._render_alone(root, index, registry, None, ["walk", "run"])[0]
    assert all(
        bodyframes[pose].tobytes() == body.tobytes() for pose, body in poses.items()
    )
    source_body = Image.open(source / "art/body.png").convert("RGBA")
    # Original reference-specific head ownership from the preserved static body.
    original_head = source_body.crop((0, 0, 1254, 399)).getbbox()
    results = {}
    for name, (slot, node, socket, _) in PARTS.items():
        identity = "illustration.motion." + name
        oldstate = p._load(oldroot, "illustration." + name)
        row = oldstate["tracks"]["main"]["poses"]["fit.01"]
        record = p.candidate_record(oldroot, oldstate, "fit.01", row["selected"])
        original = Image.open(
            oldroot / row["selected"] / record["images"]["part"]["file"]
        ).convert("RGBA")
        folder = root / "inputs/parts" / name
        folder.mkdir(parents=True, exist_ok=True)
        previous = (
            w.decode_json((folder / "import.json").read_bytes())
            if (folder / "import.json").exists()
            else None
        )
        mapping = {}
        operations = {}
        for pose, g in geometry.items():
            head = g["head"]
            cx = (head[0] + head[2]) / 2
            points = np.array(g["points"])
            canvas = Image.new("RGBA", (768, 768))
            matrices = []
            if slot in ["hair", "eyes"]:
                matrices = [box_matrix(original_head, head)]
                canvas = transform(original, matrices[0])
            elif slot in ["upper", "lower"]:
                target = (
                    [cx - 97, head[3] + 9, cx + 91, 407]
                    if slot == "upper"
                    else [cx - 80, 370, cx + 81, 475]
                )
                matrices = [box_matrix(original.getbbox(), target)]
                canvas = transform(original, matrices[0])
            else:
                for i in range(2):
                    cropped = original.copy()
                    roi = (0, 0, 620, 1254) if i == 0 else (620, 0, 1254, 1254)
                    keep = Image.new("RGBA", original.size)
                    keep.paste(cropped.crop(roi), roi[:2])
                    start = np.array([516, 1134]) if i == 0 else np.array([736, 1112])
                    end = np.array([552, 1200]) if i == 0 else np.array([824, 1172])
                    if slot == "socks":
                        # Shaft follows the calf; the foot silhouette still needs pose-specific art.
                        end = start + np.array([0, -170])
                        matrix = foot_matrix(start, end, points[2 + i], points[i])
                    else:
                        matrix = foot_matrix(start, end, points[2 + i], points[4 + i])
                    matrices.append(matrix)
                    canvas.alpha_composite(transform(keep, matrix))
            canvas.save(folder / f"{pose}.png")
            operations[pose] = [m.tolist() for m in matrices]
            mapping[pose] = {
                "image": {
                    "file": f"{pose}.png",
                    "sha256": a.sha256((folder / f"{pose}.png").read_bytes()),
                },
                "format": "full-canvas",
                "pivot": [0, 0],
                "target_socket": "root",
            }
        write(
            folder / "import.json",
            {
                "kind": "parts-pack-import",
                "schema_version": 1,
                "id": identity,
                "design_id": identity,
                "role": "part",
                "style": rig["style"],
                "rig": index["rig"],
                "motion": index["motion"],
                "view": rig["view"],
                "binding_id": identity + ".binding",
                "occupies": [slot],
                "pieces": [
                    {
                        "id": slot,
                        "slot": slot,
                        "node": node,
                        "mode": "raster",
                        "pose_map": mapping,
                    }
                ],
            },
        )
        index, registry = w.read_workspace(assembly)
        existing = (
            p._load(root, identity)
            if (root / "productions" / identity / "production.json").exists()
            else None
        )
        order, _ = p._poses(registry[index["motion"]["id"]], ["walk", "run"])
        reusable = bool(
            existing
            and p._current(existing, registry, order)
            and previous
            and previous["pieces"][0]["pose_map"] == mapping
        )
        if not reusable:
            parts_import.import_pack(assembly, folder / "import.json", folder)
        config = deepcopy(oldstate["config"])
        config.update(
            part_id=identity,
            design_id=identity,
            states=["walk", "run"],
            socket="root",
            pose_checks={},
            segments="inputs/body-segments/segments.json",
            sheet={
                "scale": 1,
                "gutter": 8,
                "columns": 3,
                "crop": [160, 0, 600, 730],
                "page_size": 6,
                "aspect": None,
            },
            prompt={
                "text": "Draw independent frame-specific "
                + slot
                + " against this exact video-body pose. Registration candidates are only geometric references; preserve design while correcting pose, silhouette, support and occlusion. Never draw the body.",
                "refs": [],
            },
        )
        # Replacement needs explicit, reviewed foot ownership; these seeds are unverified.
        for piece in config["pieces"]:
            piece["replaces"] = []
        write(folder / "config.json", config)
        if (root / "productions" / identity / "production.json").exists():
            p.configure(assembly, identity, folder / "config.json")
        else:
            p.init(assembly, folder / "config.json")
        index, registry = w.read_workspace(assembly)
        if reusable:
            checked = {
                "resumed": True,
                "poses": {
                    pose: row.get("check")
                    for pose, row in existing["tracks"]["main"]["poses"].items()
                },
            }
            built = {"part": existing["built"]["part"], "resumed": True}
        else:
            p.adopt(assembly, identity, root / index["documents"][identity])
            checked = p.check(assembly, identity)
            built = p.build(assembly, identity)
        priority = [
            f"{state}.{n + 1:02d}"
            for state, count in counts.items()
            for n in sorted(
                set(
                    [
                        0,
                        count // 6,
                        count // 3,
                        count // 2,
                        count * 2 // 3,
                        count * 5 // 6,
                    ]
                )
            )
        ]
        reference = (
            {"reference": existing["tracks"]["main"]["batches"][-1]["reference"]}
            if reusable
            else p.sheet(assembly, identity, poses=priority)
        )
        results[name] = {
            "check": checked,
            "future_generation_reference": reference["reference"],
            "build": built,
            "source_candidate": row["selected"],
            "source_sha256": record["images"]["part"]["sha256"],
            "registration_matrices": operations,
            "provider_calls": 0,
            "art_approved": False,
        }
    index, registry = w.read_workspace(assembly)
    write(
        out / "selection.json",
        {
            "kind": "parts-appearance-selection",
            "schema_version": 1,
            "id": "illustration.motion.registration",
            "selections": {
                slot: c.reference(registry["illustration.motion." + name])
                for name, (slot, *_) in PARTS.items()
            },
        },
    )
    parts_appearance.create(assembly, out / "selection.json")
    export_parts.export(
        assembly,
        out / "export",
        interpolation="frame",
        title="실제 walk/run · 파츠 위치 검증 후보 · 아트 미승인",
    )
    write(
        out / "registration-provenance.json",
        {
            "kind": "illustration-motion-registration-experiment",
            "body_source": "actual Grok image-to-video, engine video-frames/video-loop",
            "clips": counts,
            "canvas": [768, 768],
            "body_source_reconstruction_exact": True,
            "geometry": geometry,
            "parts": results,
            "source_art": str(source),
            "registration_only": True,
            "per_frame_image_generation": False,
            "appearance_clips_baked": 0,
            "runtime_masks": 0,
            "art_approved": False,
            "release_ready": False,
            "remaining": [
                "authored knots and body ownership need visual approval",
                "clothing/foot silhouettes require frame-specific generation or source editing",
                "validate temporal consistency and exact feet occlusion; geometry QC maps not yet authored",
            ],
        },
    )
    return {
        "assembly": str(assembly),
        "motion_frames": sum(counts.values()),
        "independent_slots": len(PARTS),
        "art_approved": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--register-only", action="store_true")
    args = parser.parse_args()
    print(
        (register_parts if args.register_only else prepare)(
            args.source.absolute(), args.out.absolute()
        )
    )
