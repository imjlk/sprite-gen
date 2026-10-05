# SPDX-License-Identifier: Apache-2.0
"""Rebuild the SD explorer on a motion cut by the video pipeline, using only public parts steps.

Inputs: video-set loops for walk/run, the previous library (idle frames, held parts and
its rig), and authored numbers below. Steps: loops -> native frames -> strict import ->
socket tracking -> freeze -> motion body -> held parts rebound (with follow-through) ->
optional pose-sheet clothing -> appearance. Visual approval stays a human review.
"""
from __future__ import annotations

import argparse
import json
import shutil
from fractions import Fraction
from pathlib import Path

from PIL import Image

from sprite_gen.curate import parts_appearance, parts_motion, parts_sockets
from sprite_gen.frames import parts_import, parts_loop_native, parts_pose_sheet, parts_rebind
from sprite_gen.spec import parts_workspace as workspace

# Authored SD numbers: the body's standing height and ground row on the 128 canvas, and
# the attachment points of the previous library's idle sample.0, where its held parts fit.
BODY_HEIGHT, BASELINE = 83, 112
HEAD, TORSO = [64, 30], [64, 69]
HELD = {
    "sd.wardrobe.hair.a": {"secondary": [("hair", "head", Fraction(1, 2), 1)]},
    "sd.wardrobe.hair.b": {"secondary": [("hair", "head", Fraction(1, 2), 1)]},
    "sd.wardrobe.expression.a": {},
    "sd.wardrobe.expression.b": {},
    "sd.equipment.headwear": {},
    "sd.equipment.back_accessory": {"follow": [("back_accessory", "torso", TORSO)],
                                    "secondary": [("back_accessory", "torso", Fraction(1, 2), 1)]},
}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1))
    return path


def motion(base, baseline, video, palette):
    library = base / "library"
    if library.exists():
        raise ValueError(f"{library} exists; use a new --base")
    shutil.copytree(baseline / "library", library)
    assembly = library / "assembly.json"
    index, registry = workspace.read_workspace(baseline / "library/assembly.json")
    old = registry[index["motion"]["id"]]
    root = (baseline / "library" / index["documents"][old["id"]]).parent
    idle = base / "idle-source"
    idle.mkdir(parents=True, exist_ok=True)
    clip = next(c for c in old["clips"] if c["state"] == "idle")
    for n, sample in enumerate(clip["samples"]):
        Image.open(root / sample["image"]["file"]).save(idle / f"frame-{n:03d}.png")
    fps = Fraction(clip["timebase"], clip["samples"][0]["duration_ticks"])
    native = parts_loop_native.build(assembly, base / "native",
        [("idle", idle, str(fps)), ("walk", video / "side-walk/loop"), ("run", video / "side-run/loop")],
        body_height=BODY_HEIGHT, baseline=BASELINE, palette=palette, motion_id=old["id"], every=[("walk", 2)],
        resample="lattice", outline="darkest")
    imported = parts_motion.import_motion(assembly, Path(native["spec"]), Path(native["source_root"]), strict_motion=True)
    ref = {"clip": "idle.right", "sample": "sample.0"}
    tracks = [{"socket": "head", "reference": {**ref, "position": HEAD}, "bounds": [-20, 0, 20, 28], "radius": 8, "max_cost": 400},
              {"socket": "torso", "reference": {**ref, "position": TORSO}, "bounds": [-7, -8, 7, 8], "radius": 8, "max_cost": 400},
              {"socket": "root", "mode": "constant", "reference": {**ref, "position": [0, 0]}}]
    spec = write(base / "socket-track.json", {"kind": "parts-socket-track", "schema_version": 1, "tracks": tracks})
    tracked = parts_sockets.track_sockets(assembly, imported["candidate"], library / imported["annotation_template"],
                                          spec, base / "annotations.tracked.json", accept=True, preview=base / "socket-track.png")
    if tracked["unverified_sockets"]:
        raise ValueError(f"socket proposals need review: {tracked['unverified_examples']}")
    parts_motion.freeze_motion(assembly, imported["candidate"], base / "annotations.tracked.json")
    body = parts_rebind.body_spec(assembly, base / "import/body.json", body_id="sd.equipment.body")
    parts_import.import_pack(assembly, Path(body["spec"]), Path(body["source_root"]))
    for part, options in HELD.items():
        spec = parts_rebind.rebind_spec(assembly, baseline / "library" / index["documents"][part],
                                        base / "import" / (part + ".json"), **options)
        parts_import.import_pack(assembly, Path(spec["spec"]), Path(spec["source_root"]))
    write(base / "motion-evidence.json", {"native": native, "quality": imported["motion_quality"],
        "sockets": tracked["results"], "art_approved": False, "release_ready": False})
    return assembly


def clothing(base, plan):
    """Slice each drawn clothing sheet named in plan.json and import it."""
    assembly = base / "library/assembly.json"
    for item in json.loads(Path(plan).read_text())["parts"]:
        pages = [(base / p["guide"], base / p["art"]) for p in item["pages"]]
        sliced = parts_pose_sheet.slice_sheet(assembly, None, None, base / "sliced" / item["id"], pages=pages,
            part_id=item["id"], slot=item["slot"], node=item["node"], socket=item.get("socket", "root"),
            piece=item.get("piece", item["slot"]), body_threshold=item.get("body_threshold"),
            outline=item.get("outline", False), palette=base / item["palette"] if item.get("palette") else None,
            classes=base / item["classes"] if item.get("classes") else None, min_island=item.get("min_island", 0),
            near_body=item.get("near_body"), region=item.get("region"), tolerance=item.get("tolerance", 48))
        spec = json.loads(Path(sliced["spec"]).read_text())
        for piece in spec["pieces"]:
            piece["provides"] = item.get("provides", [])
            for samples in piece["sample_map"].values():
                for placement in samples.values():
                    placement["after"] = item.get("after", ["root"])
        Path(sliced["spec"]).write_text(json.dumps(spec))
        parts_import.import_pack(assembly, Path(sliced["spec"]), Path(sliced["source_root"]))
        print(item["id"], {k: sliced[k] for k in ("samples", "drawn", "empty", "missing_requested_samples")})


def appearance(base, selection):
    assembly = base / "library/assembly.json"
    index, registry = workspace.read_workspace(assembly)
    catalog = registry[index["catalog"]["id"]]
    options = {slot["id"]: {o["id"]: o for o in slot["options"]} for slot in catalog["slots"]}
    chosen = {slot: (options[slot][part] if part else None) for slot, part in selection.items()}
    spec = write(base / "selection.json", {"kind": "parts-appearance-selection", "schema_version": 1,
        "id": "sd.matched.layers", "selections": {slot: None for slot in options} | chosen})
    return parts_appearance.create(assembly, spec)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, type=Path)
    parser.add_argument("--baseline", type=Path, help="previous run folder with library/ (motion step)")
    parser.add_argument("--video", type=Path, help="video-set out-dir with side-walk/ and side-run/ (motion step)")
    parser.add_argument("--palette", type=Path, help="body palette JSON (motion step)")
    parser.add_argument("--clothing", type=Path, help="plan.json of drawn clothing pages to slice and import")
    parser.add_argument("--select", type=Path, help="JSON {slot: part id or null} for the appearance")
    args = parser.parse_args()
    if args.baseline:
        motion(args.base, args.baseline, args.video, args.palette)
    if args.clothing:
        clothing(args.base, args.clothing)
    if args.select:
        print(appearance(args.base, json.loads(args.select.read_text())))
