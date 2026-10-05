# SPDX-License-Identifier: Apache-2.0
"""Recheck a regenerated hair layer and activate only a passing local revision.

The provider adapter is a supplied imagegen output, not an embedded network
client. The generator's output is imported once into a manually calibrated
44x38 region. No body, other part, motion, or runtime scale is modified.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from dataclasses import asdict
from fractions import Fraction
from pathlib import Path
import json
import shutil

from PIL import Image, ImageDraw

from sprite_gen.compose.parts_runtime import PartsPlayer
from sprite_gen.curate import parts_appearance
from sprite_gen.frames import parts_import
from sprite_gen.qa import parts_fitting
from sprite_gen.spec import parts_assets as assets, parts_contract as contract, parts_workspace as workspace

PART_ID = "sd.wardrobe.hair.a"
RULES = parts_fitting.FitRules((128, 128), (44, 39), (41, 23, 88, 65), (30, 12, 98, 64),
    required_cover=((53, 31, 67, 34),), keep_clear=((72, 48, 80, 60), (52, 55, 54, 58)))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(workspace.json_bytes(value))


def tree_hashes(folder):
    return {p.relative_to(folder).as_posix(): assets.sha256(p.read_bytes()) for p in folder.rglob("*") if p.is_file()}


def evaluate(player, candidate):
    part = assets.image_bytes(candidate.image)
    registry = {doc["id"]: doc for doc in player.documents}
    motion = registry[player.appearance["motion"]["id"]]
    samples = []
    for clip in motion["clips"]:
        for sample in clip["samples"]:
            body = player.load(motion, sample["image"])
            delta = tuple(a-b for a, b in zip(sample["sockets"]["head"]["position"], (64, 30)))
            report = parts_fitting.measure(body, part, RULES,
                offset=tuple(a+b for a, b in zip(delta, candidate.offset)), reference_delta=delta)
            samples.append({"clip": clip["id"], "sample": sample["id"], **report})
    names = [check["id"] for check in samples[0]["checks"]]
    checks = [{"id": name, "status": "error" if any(next(c for c in s["checks"] if c["id"] == name)["status"] == "error" for s in samples) else "passed",
               "failed_samples": sum(next(c for c in s["checks"] if c["id"] == name)["status"] == "error" for s in samples),
               "repair_action": next(c for c in samples[0]["checks"] if c["id"] == name)["repair_action"]} for name in names]
    return {"status": "error" if any(c["status"] == "error" for c in checks) else "passed", "checks": checks,
            "samples": samples, "samples_checked": len(samples), "motion": player.appearance["motion"],
            "art_approved": False, "release_ready": False}


def build(source, base, raw_hair):
    if (base/"library").exists():
        raise ValueError("use a new destination; existing revision is preserved")
    saved = tree_hashes(source/"library")
    old = PartsPlayer.from_workspace(source/"library/assembly.json")
    index, registry = workspace.read_workspace(source/"library/assembly.json")
    pack = registry[PART_ID]
    binding = next(b for b in pack["bindings"] if b["id"] == PART_ID+".binding")
    piece = binding["pieces"][0]
    placement = next(iter(next(iter(piece["sample_map"].values())).values()))
    assert piece["mode"] == "held" and piece["replaces"] == []
    assert all(p["pivot"] == [64, 30] and p["target_socket"] == "head" and p["offset"] == [2, -1]
               and p["image"] == placement["image"] for mapping in piece["sample_map"].values() for p in mapping.values())
    initial = parts_fitting.FitCandidate(PART_ID, assets.png_bytes(old.load(pack, placement["image"])), (2, -1))
    base.mkdir(parents=True, exist_ok=True)
    raw = Image.open(raw_hair).convert("RGBA")
    raw.putalpha(raw.getchannel("A").point(lambda a: 255 if a >= 128 else 0))
    bounds = raw.getbbox()
    if bounds is None:
        raise ValueError("regenerated hair is empty")
    # Explicit one-time asset import calibration. Runtime has no scale field.
    picture = Image.new("RGBA", (128, 128))
    picture.alpha_composite(raw.crop(bounds).resize((44, 38), Image.Resampling.NEAREST), (42, 25))
    replacement = parts_fitting.FitCandidate(PART_ID, assets.png_bytes(picture), action="regenerate-part-and-calibrate-import")

    def correct(candidate, failure, attempt):
        # Redraw feedback is handled by the image provider/author before this
        # recipe. Never retry a video or another clothing/face selection.
        if attempt == 1:
            return replacement
        # A bounded attachment nudge is useful only for a pure position error.
        if {c["id"] for c in failure["checks"] if c["status"] == "error"} == {"position"}:
            return parts_fitting.FitCandidate(PART_ID, replacement.image, (1 if attempt == 2 else -1, 0), "adjust-binding-offset")
        return None

    accepted, history = parts_fitting.retry(initial, lambda c: evaluate(old, c), correct)
    write(base/"fit-repair.json", history)
    write(base/"fit-rules.json", {"kind": "authored-sd-hair-fit-rules", "schema_version": 1,
        "part_id": PART_ID, "reference_anchor": [64, 30], "reference_sample": "idle.right/sample.0",
        "motion": index["motion"], "rules": asdict(RULES),
        "calibration": "manual, for this SD body and short hairstyle only; not automatic anatomy or retargeting"})
    if accepted is None:
        raise ValueError("fitting repair failed; old library remains active and no new revision was activated")
    library = base/"library"
    shutil.copytree(source/"library", library, ignore=shutil.ignore_patterns("qa"))
    assembly = library/"assembly.json"
    asset_path = base/"parts-source/hair-fitted-a.png"
    asset_path.parent.mkdir(parents=True, exist_ok=True)
    asset_path.write_bytes(accepted.image)
    spec = workspace.decode_json((source/"import"/(PART_ID+".json")).read_bytes())
    for item in spec["pieces"]:
        for mapping in item["sample_map"].values():
            for entry in mapping.values():
                entry.update(image={"file": "parts-source/hair-fitted-a.png", "sha256": assets.sha256(accepted.image)}, offset=list(accepted.offset))
    spec_path = base/"import"/(PART_ID+".json")
    write(spec_path, spec)
    imported = parts_import.import_pack(assembly, spec_path, base)
    selection = {k: deepcopy(old.appearance[k]) for k in ("id", "selections", "presets", "palette", "mirror")}
    selection.update(kind="parts-appearance-selection", schema_version=1)
    selection["selections"]["hair"] = imported["part"]
    write(base/"selection.json", selection)
    parts_appearance.create(assembly, base/"selection.json")
    choices = workspace.decode_json((source/"choices.json").read_bytes())
    choices["hair"][0] = imported["part"]
    write(base/"choices.json", choices)
    next_index, next_registry = workspace.read_workspace(assembly)
    for key in ("body", "rig", "motion", "states"):
        assert next_index[key] == index[key]
    for identity, document in registry.items():
        if document["kind"] == "parts-pack" and identity != PART_ID:
            assert next_registry[identity] == document
    assert tree_hashes(source/"library") == saved
    write(base/"asset-calibration.json", {"raw_image_sha256": assets.sha256(raw_hair.read_bytes()),
        "raw_alpha_bbox": list(bounds), "target_bbox": [42, 25, 86, 63], "operation": "one-time nearest-neighbor import",
        "previous_part": contract.reference(pack), "corrected_part": imported["part"],
        "body_motion_and_other_parts_preserved": True, "runtime_scale": False, "art_approved": False, "release_ready": False})
    return assembly


def check(source, base):
    old = PartsPlayer.from_workspace(source/"library/assembly.json")
    new = PartsPlayer.from_workspace(base/"library/assembly.json")
    old.seek(Fraction(17, 24))
    new.seek(Fraction(17, 24))
    initial_clock = new.layers()["elapsed_seconds"]
    saved = tree_hashes(base/"library")
    choices = workspace.decode_json((base/"choices.json").read_bytes())
    count = 0
    failures = Counter()
    for state in ("idle", "walk", "run"):
        old.set_state(state)
        new.set_state(state)
        clip = new.current()[0]
        for number, sample in enumerate(clip["samples"]):
            for player in (old, new):
                player.seek(Fraction(number, 24))
            before, after = old.layers(interpolate=False), new.layers(interpolate=False)
            def body(scene):
                return next(layer for layer in scene["layers"] if layer["slot"] is None)
            assert body(before) == body(after)
            assert before["clip_tick"] == after["clip_tick"]
            assert [x for x in before["layers"] if x["slot"] != "hair"] == [x for x in after["layers"] if x["slot"] != "hair"]
            image, evidence = new.frame()
            failures["clipped_pixels"] += sum(row["clipped_pixels"] for row in evidence["evidence"]["draw"])
            count += 1
    assert failures["clipped_pixels"] == 0
    new.seek(Fraction(17, 24))
    for option in (choices["hair"][1], None, choices["hair"][0]):
        new.set_selection({"hair": option})
        assert new.layers()["elapsed_seconds"] == initial_clock
    assert tree_hashes(base/"library") == saved
    sheet = Image.new("RGBA", (3*128, 2*146), (239, 240, 234, 255))
    draw = ImageDraw.Draw(sheet)
    for row, player in enumerate((old, new)):
        for col, state in enumerate(("idle", "walk", "run")):
            player.set_state(state)
            player.seek(Fraction(0 if state == "idle" else 48 if state == "walk" else 70, 24))
            image, _ = player.frame()
            sheet.alpha_composite(image, (col*128, row*146+18))
            draw.text((col*128+3, row*146+3), ("before" if row == 0 else "corrected")+" "+state, fill=(20, 20, 20, 255))
    sheet.resize((768, 584), Image.Resampling.NEAREST).save(base/"before-after-fit.png")
    report = {"states": 3, "sample_evaluations": count, "body_motion_other_layers_preserved": True,
        "swap_clock_preserved": True, "library_bytes_preserved": True, "clipped_pixels": 0,
        "appearance_clips_baked": 0, "runtime_texture_warps": 0, "art_approved": False, "release_ready": False}
    write(base/"validation.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--base", required=True, type=Path)
    parser.add_argument("--raw-hair", required=True, type=Path)
    args = parser.parse_args()
    build(args.source, args.base, args.raw_hair)
    print(json.dumps(check(args.source, args.base), indent=2))
