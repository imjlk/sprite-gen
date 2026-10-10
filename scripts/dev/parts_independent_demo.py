# SPDX-License-Identifier: Apache-2.0
"""Prepare a small real-motion workspace for the core independent-layer route.

This is an explicitly non-looping reference window, not a newly generated or
silently shortened full run cycle. Original body pixels and source samples stay
recorded. Every subsequent production operation uses public core commands.
"""

from copy import deepcopy
from pathlib import Path
import argparse
import shutil
import numpy as np
from PIL import Image, ImageDraw

from sprite_gen.compose import compose_parts
from sprite_gen.curate import parts_motion
from sprite_gen.frames import parts_import, parts_production as p
from sprite_gen.spec import parts_assets as a, parts_contract as c, parts_workspace as w


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(w.json_bytes(value))


def prepare(out, source, pilot):
    root = out / "library"
    assembly = root / "assembly.json"
    if root.exists():
        raise ValueError(
            "use a new demo directory; existing sources and candidates are preserved"
        )
    oldindex, old = w.read_workspace(source / "assembly.json")
    rig = old[oldindex["rig"]["id"]]
    top = old[rig["topology"]["id"]]
    cat = deepcopy(old[oldindex["catalog"]["id"]])
    cat.pop("digest")
    cat["presets"] = []
    for slot in cat["slots"]:
        slot.update(options=[], default=None)
    cat = c.seal(cat)
    docs = {top["id"]: top, rig["id"]: rig, cat["id"]: cat}
    index = {
        "kind": "parts-workspace",
        "schema_version": 1,
        "id": "independent.production.demo",
        "documents": {
            top["id"]: "profiles/topology.json",
            rig["id"]: "profiles/rig.json",
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
    for name, file in index["documents"].items():
        put(root / file, docs[name])
    put(assembly, index)
    motion = old[oldindex["motion"]["id"]]
    clip = next(clip for clip in motion["clips"] if clip["state"] == "run")
    samples = clip["samples"][9:17]
    load = compose_parts.AssetLoader(source, oldindex)
    frames = []
    provenance = []
    inputs = root / "inputs/body"
    inputs.mkdir(parents=True)
    for n, sample in enumerate(samples):
        image = load(c.reference(motion), sample["image"]).convert("RGBA")
        image.save(inputs / f"{n:03}.png")
        frames.append(
            {"id": sample["pose_id"], "file": f"{n:03}.png", "duration": 1 / 24}
        )
        provenance.append(
            {
                "pose": sample["pose_id"],
                "original_sample": sample["id"],
                "source_frame": sample["source_frame_id"],
                "source_image": sample["image"],
                "duration_ticks": sample["duration_ticks"],
                "timebase": clip["timebase"],
            }
        )
    put(
        inputs / "frames.json",
        {"kind": "sprite-gen-asset", "version": 1, "frames": frames},
    )
    request = {
        "kind": "parts-motion-import",
        "schema_version": 1,
        "id": "illustration.production.reference-window",
        "rig": index["rig"],
        "clips": [
            {
                "id": "run.reference-window",
                "state": "run",
                "file": "frames.json",
                "timebase": 24000,
                "fps": "24",
                "loop": False,
                "source_mode": "external",
                "expected_files": {
                    file.name: a.sha256(file.read_bytes())
                    for file in inputs.iterdir()
                    if file.is_file()
                },
            }
        ],
    }
    put(root / "inputs/motion.json", request)
    imported = parts_motion.import_motion(assembly, root / "inputs/motion.json", inputs)
    annfile = root / imported["annotation_template"]
    annotations = w.decode_json(annfile.read_bytes())
    for n, row in enumerate(annotations["clips"]["run.reference-window"].values()):
        row.update(
            pose_id=samples[n]["pose_id"], sockets=deepcopy(samples[n]["sockets"])
        )
    put(annfile, annotations)
    parts_motion.freeze_motion(assembly, imported["candidate"], annfile)
    active, _ = w.read_workspace(assembly)
    body = old[oldindex["body"][0]["id"]]
    binding = body["bindings"][0]
    pieces = []
    head_bounds = {}
    for piece in binding["pieces"]:
        mapping = {}
        for sample in samples:
            placement = piece["sample_map"][clip["id"]][sample["id"]]
            raster = load(c.reference(body), placement["image"]).convert("RGBA")
            position = [
                s - p + o
                for s, p, o in zip(
                    sample["sockets"][placement["target_socket"]]["position"],
                    placement["pivot"],
                    placement["offset"],
                )
            ]
            full = Image.new("RGBA", (rig["canvas"]["width"], rig["canvas"]["height"]))
            full.paste(raster, tuple(position))
            file = (
                root / "inputs/body-pieces" / piece["id"] / (sample["pose_id"] + ".png")
            )
            file.parent.mkdir(parents=True, exist_ok=True)
            full.save(file)
            mapping[sample["pose_id"]] = {
                "image": {
                    "file": str(file.relative_to(root / "inputs/body-pieces")),
                    "sha256": a.sha256(file.read_bytes()),
                },
                "format": "full-canvas",
                "pivot": [0, 0],
                "target_socket": "root",
                "visible": placement["visible"],
            }
            if piece["node"] == "head":
                head_bounds[sample["pose_id"]] = list(full.getbbox())
        pieces.append(
            {
                "id": piece["id"],
                "node": piece["node"],
                "slot": None,
                "provides": piece["provides"],
                "mode": "raster",
                "pose_map": mapping,
            }
        )
    spec = {
        "kind": "parts-pack-import",
        "schema_version": 1,
        "id": "illustration.production.body",
        "design_id": "illustration.production.body",
        "role": "body",
        "body_mode": "segmented",
        "style": rig["style"],
        "rig": active["rig"],
        "motion": active["motion"],
        "view": rig["view"],
        "binding_id": "illustration.production.body.binding",
        "states": ["run"],
        "occupies": [],
        "pieces": pieces,
    }
    put(root / "inputs/body-pieces/import.json", spec)
    parts_import.import_pack(
        assembly, root / "inputs/body-pieces/import.json", root / "inputs/body-pieces"
    )
    registration = w.decode_json((pilot / "registration.json").read_bytes())
    report = w.decode_json((pilot / "registration-report.json").read_bytes())
    shoe_layers = []
    for node in ("shoe_back", "shoe_front"):
        targets = {}
        source_bounds = {}
        for pose, row in registration.items():
            placement = next(x for x in row["pieces"] if x["node"] == node)
            left, up, right, down = placement["bounds"]
            targets[pose] = [
                128 + round(left / 2),
                384 + round(up / 2),
                128 + round(right / 2),
                384 + round(down / 2),
            ]
            source_bounds[pose] = next(
                x for x in report["poses"][pose]["pieces"] if x["node"] == node
            )["source_bounds"]
        shoe_layers.append(
            {
                "id": node,
                "node": node,
                "targets": targets,
                "source_bounds": source_bounds,
                "fit": "cover",
                "margin": "1.04",
            }
        )
    cfg = {
        "kind": "parts-production-config",
        "schema_version": 1,
        "part_id": "illustration.production.sneakers",
        "design_id": "navy-cream-sneakers",
        "slot": "footwear",
        "node": "shoe_front",
        "socket": "root",
        "states": ["run"],
        "prompt": {
            "text": "Navy and cream sneakers. Lace-up fronts, cream soles, navy side panels. Match the exact foot pose."
        },
        "independent": {"layers": shoe_layers},
        "pose_checks": {},
    }
    # Frozen toe/heel criteria from the prior source-specific annotation remain QA
    # evidence, never runtime masks. Their source is declared in provenance.
    for pose in registration:
        cfg["pose_checks"][pose] = {}
        for name in ("required", "allowed", "protected"):
            file = root / "inputs/shoe-criteria" / f"{pose}.{name}.png"
            file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(
                source / f"inputs/ai-shoes-pilot/criteria/{pose}.{name}.png", file
            )
            cfg["pose_checks"][pose][name] = str(file.relative_to(root))
    put(root / "inputs/shoes-config.json", cfg)
    p.init(assembly, root / "inputs/shoes-config.json")
    shoe_batch = p.sheet(assembly, cfg["part_id"], poses=list(registration))
    for job in shoe_batch["pages"]:
        dest = root / job["art"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pilot / registration[job["pose"]]["raw"], dest)
    p.slice_batch(assembly, cfg["part_id"], batch=shoe_batch["batch"])
    p.check(assembly, cfg["part_id"])
    p.build(assembly, cfg["part_id"])
    hair_targets = {}
    hair_checks = {}
    for pose, (left, up, right, down) in head_bounds.items():
        height = down - up
        # A short crop only covers the scalp. Ears and face remain body pixels.
        hair_targets[pose] = [
            left - 6,
            max(0, up - 12),
            right + 6,
            up + round(height * 0.68),
        ]
        required = Image.new("L", (rig["canvas"]["width"], rig["canvas"]["height"]))
        ImageDraw.Draw(required).rectangle(
            (left + 30, up + 8, right - 30, up + 26), fill=255
        )
        n = int(pose.split(".")[-1]) - 10
        body_alpha = np.asarray(Image.open(inputs / f"{n:03}.png").getchannel("A"))
        required = Image.fromarray(np.minimum(np.asarray(required), body_alpha))
        allowed = Image.new("L", required.size)
        ImageDraw.Draw(allowed).rectangle(tuple(hair_targets[pose]), fill=255)
        protected = Image.new("L", required.size)
        ImageDraw.Draw(protected).rectangle(
            (0, down + 8, required.width - 1, required.height - 1), fill=255
        )
        hair_checks[pose] = {}
        for name, image in [
            ("required", required),
            ("allowed", allowed),
            ("protected", protected),
        ]:
            file = root / "inputs/hair-criteria" / f"{pose}.{name}.png"
            file.parent.mkdir(parents=True, exist_ok=True)
            image.save(file)
            hair_checks[pose][name] = str(file.relative_to(root))
    cfg = {
        "kind": "parts-production-config",
        "schema_version": 1,
        "part_id": "illustration.production.hair",
        "design_id": "chestnut-crop",
        "slot": "hair",
        "node": "hair",
        "socket": "root",
        "states": ["run"],
        "prompt": {
            "text": "Short chestnut-brown cropped hair with a small rightward swept fringe, clean dark brown outline and warm cream highlights. Smooth hand-drawn SD illustration matching the reference body. Hair hugs the bald scalp: a round cap, short temple locks, curved forehead opening, no skin/face or neck. Keep the same silhouette and small fringe across poses."
        },
        "independent": {
            "layers": [
                {
                    "id": "hair",
                    "node": "hair",
                    "targets": hair_targets,
                    "fit": "contain",
                    "margin": "1",
                }
            ]
        },
        "pose_checks": hair_checks,
    }
    put(root / "inputs/hair-config.json", cfg)
    p.init(assembly, root / "inputs/hair-config.json")
    hair_batch = p.sheet(assembly, cfg["part_id"], poses=list(hair_targets))
    rebuilt, _ = p._render_alone(root, *w.read_workspace(assembly), None, ["run"])
    assert all(
        np.array_equal(
            np.asarray(rebuilt[s["pose_id"]]),
            np.asarray(load(c.reference(motion), s["image"]).convert("RGBA")),
        )
        for s in samples
    )
    put(
        out / "source-provenance.json",
        {
            "original_motion": oldindex["motion"],
            "original_body": oldindex["body"],
            "source_window": provenance,
            "is_complete_run_cycle": False,
            "loop": False,
            "body_pixel_identical": len(samples),
            "new_video_calls": 0,
            "shoe_native_generations": str(pilot / "generation-latest.json"),
        },
    )
    return {
        "assembly": str(assembly),
        "hair_batch": hair_batch,
        "shoes": "illustration.production.sneakers",
        "body_pixel_identical": 8,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--pilot", required=True, type=Path)
    args = parser.parse_args()
    result = prepare(args.out.absolute(), args.source.absolute(), args.pilot.absolute())
    print(w.json_bytes(result).decode())
