# SPDX-License-Identifier: Apache-2.0
"""Reproduce M1 on synthetic rasters; never label this as artwork acceptance.

Run from a checkout with .venv/bin/python and a new --out directory. No network,
generation provider or review approval is used. Every output is a preview build.
"""
from __future__ import annotations

import argparse
from itertools import product
import json
from pathlib import Path

from PIL import Image, ImageDraw

from sprite_gen.compose import parts_build
from sprite_gen.curate import parts_motion, parts_appearance
from sprite_gen.frames import parts_import
from sprite_gen.qa import inspect_parts
from sprite_gen.spec import parts_assets as assets, parts_contract as contract, parts_workspace as workspace

PRESETS = ("human.sd.v1", "human.tall.v1", "canine.medium.v1", "blob.fixture.v1", "asymmetric.six-limb.v1")


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(workspace.json_bytes(value))
    return path


def fixture(base, preset):
    library, source = base / "library", base / "sources"
    workspace.initialize(library, preset)
    assembly = library / "assembly.json"
    index, registry = workspace.read_workspace(assembly)
    rig = registry[index["rig"]["id"]]
    topology = registry[rig["topology"]["id"]]
    width, height = rig["canvas"]["width"], rig["canvas"]["height"]
    points = {n["id"]: [width // 2 - 20 + (i % 6) * 8, height // 2 - 12 + (i // 6) * 8]
              for i, n in enumerate(topology["nodes"])}
    colors = {n["id"]: [(i * 43 + 50) % 255, (i * 31 + 70) % 255, (i * 17 + 90) % 255, 255]
              for i, n in enumerate(topology["nodes"])}
    phases = {"idle": [0, 0, 1, 0], "walk": [0, 1, 2, 1, 0, -1, -2, -1], "run": [0, 2, 3, 2, 0, -2, -3, -2]}
    if preset == "blob.fixture.v1":
        phases["pulse"] = [0, 1]
    index["states"] = list(phases)
    write(assembly, index)
    requests = []
    for state, offsets in phases.items():
        folder = source / state
        folder.mkdir(parents=True)
        files = {}
        for phase in sorted(set(offsets)):
            image = Image.new("RGBA", (width, height))
            for name, point in points.items():
                image.alpha_composite(Image.new("RGBA", (3, 3), tuple(colors[name])), (point[0] + phase, point[1]))
            filename = f"pose-{phase}.png"
            image.save(folder / filename)
            files[f"{state}/{filename}"] = assets.sha256((folder / filename).read_bytes())
        duration = {"idle": 0.25, "walk": 0.125, "run": 0.1, "pulse": 0.25}[state]
        descriptor = write(folder / "motion.json", {"kind": "sprite-gen-asset", "version": 1,
            "frames": [{"id": f"source.{i}", "file": f"pose-{phase}.png", "duration": duration} for i, phase in enumerate(offsets)]})
        files[f"{state}/motion.json"] = assets.sha256(descriptor.read_bytes())
        requests.append({"id": f"{state}.right", "state": state, "file": f"{state}/motion.json", "source_state": None,
            "timebase": 120000, "loop": state != "pulse", "fps": None, "timing_policy": "exact", "source_mode": "external", "expected_files": files})
    spec = write(base / "motion-import.json", {"kind": "parts-motion-import", "schema_version": 1,
        "id": "fixture.motion", "rig": index["rig"], "clips": requests})
    imported = parts_motion.import_motion(assembly, spec, source)
    annotations_path = library / imported["annotation_template"]
    annotations = json.loads(annotations_path.read_bytes())
    for clip_id, samples in annotations["clips"].items():
        for sample_id, sample in samples.items():
            phase = phases[clip_id.removesuffix(".right")][int(sample_id.split(".")[-1])]
            for socket in sample["sockets"].values():
                socket.update(position=[width // 2 + phase, height // 2], visible=True, confidence="verified")
    write(annotations_path, annotations)
    parts_motion.freeze_motion(assembly, imported["candidate"], annotations_path)
    index, registry = workspace.read_workspace(assembly)
    motion = registry[index["motion"]["id"]]
    parts_source = base / "parts-source"
    parts_source.mkdir()

    def pack(identity, role, declarations, occupies):
        pieces = []
        for piece_id, slot, node, provided, color, variant in declarations:
            image = Image.new("RGBA", (3, 3), tuple(color))
            if variant:
                image.putpixel((0, 0), (0, 0, 0, 0))
                image.putpixel((2, 2), (0, 0, 0, 0))
            filename = f"{identity}-{piece_id}.png"
            image.save(parts_source / filename)
            reference = {"file": filename, "sha256": assets.sha256((parts_source / filename).read_bytes())}
            socket = "root" if slot is None else next(s for s in topology["slots"] if s["id"] == slot)["sockets"][0]
            position = points[provided]
            pieces.append({"id": piece_id, "slot": slot, "node": node, "provides": [provided],
                "replaces": [] if role == "body" else [provided], "mode": "held", "sample_map": {
                    c["id"]: {s["id"]: {"image": reference, "format": "local", "pivot": [0, 0], "target_socket": socket,
                        "offset": [position[0] - width // 2, position[1] - height // 2]} for s in c["samples"]} for c in motion["clips"]}})
        spec = write(base / f"{identity}-import.json", {"kind": "parts-pack-import", "schema_version": 1,
            "id": identity, "design_id": "synthetic." + identity, "role": role, "style": rig["style"],
            "rig": index["rig"], "motion": index["motion"], "view": rig["view"], "binding_id": identity + ".binding",
            "occupies": occupies, "pieces": pieces})
        return parts_import.import_pack(assembly, spec, parts_source)["part"]

    pack("fixture.body", "body", [(n["id"], None, n["id"], n["id"], colors[n["id"]], 0) for n in topology["nodes"]], [])
    options = {}
    if preset == "human.sd.v1":
        interfaces = {"hair": [("hair_front", "hair_front"), ("hair_back", "hair_back")],
            "expression": [("head", n) for n in ("eye_l", "eye_r", "brow_l", "brow_r", "mouth")],
            "upper": [(n, n) for n in ("torso", "arm_l", "arm_r")],
            "lower": [(n, n) for n in ("leg_l", "leg_r")], "footwear": [(n, n) for n in ("foot_l", "foot_r")]}
        for slot, nodes in interfaces.items():
            options[slot] = [pack(f"{slot}.{variant}", "part", [(provided, slot, node, provided,
                [210, 90, 40, 255] if variant == 0 else [40, 120, 210, 255], variant) for node, provided in nodes], [slot]) for variant in range(2)]
    builds = []
    variants = list(product([0, 1], repeat=5)) if options else [()]
    for bits in variants:
        identity = "fixture." + ("".join(map(str, bits)) if bits else "default")
        selection = write(base / "selection.json", {"kind": "parts-appearance-selection", "schema_version": 1,
            "id": identity, "selections": {slot: options[slot][v] for slot, v in zip(options, bits)}})
        parts_appearance.create(assembly, selection)
        first = parts_build.build(assembly, base / "builds" / identity)
        assert first == parts_build.build(assembly, base / "builds" / identity)
        builds.append({"appearance": identity, "build": first["build"], "clips": first["clips"],
            "samples": first["samples"], "manifest": str(Path(first["manifest"]).relative_to(base))})
    return {"preset": preset, "evidence": "synthetic fixture", "appearances": len(builds),
            "clips": sum(b["clips"] for b in builds), "samples": sum(b["samples"] for b in builds),
            "release_ready": False, "builds": builds}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    output = args.out.absolute()
    if output.exists() and any(output.iterdir()):
        parser.error("--out requires a new or empty directory")
    output.mkdir(parents=True, exist_ok=True)
    report = {"kind": "parts-pipeline-proof", "schema_version": 1, "evidence": "synthetic fixture only",
        "artwork_acceptance": False, "release_ready": False, "toolchain": inspect_parts.toolchain(),
        "rigs": [fixture(output / preset, preset) for preset in PRESETS]}
    write(output / "proof.json", report)
    print(json.dumps({"proof": str(output / "proof.json"), "rigs": len(report["rigs"]),
                      "sd_appearances": report["rigs"][0]["appearances"], "sd_clips": report["rigs"][0]["clips"], "release_ready": False}, indent=2))


if __name__ == "__main__":
    main()
