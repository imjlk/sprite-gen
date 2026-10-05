# SPDX-License-Identifier: Apache-2.0
"""Upgrade a copied SD checkpoint through public fit/import/repair/export APIs.

The authored interfaces come from the checkpoint's saved native masks and
calibration. This recipe contains no anatomy detector or new body video.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path
import json
import shutil
import subprocess
import tempfile
from fractions import Fraction
from itertools import product

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.curate import parts_appearance, parts_motion
from sprite_gen.compose import compose_parts, export_parts
from sprite_gen.compose.parts_runtime import PartsPlayer
from sprite_gen.frames import parts_import, parts_palette
from sprite_gen.qa import parts_fit, inspect_parts
from sprite_gen.spec import parts_assets as assets, parts_contract as contract, parts_workspace as workspace
import sd_explorer_matched_layers as recipe


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(workspace.json_bytes(value))


def register_interfaces(base):
    assembly = base / "library/assembly.json"
    index, registry = workspace.read_workspace(assembly)
    evidence = workspace.decode_json((base / "part-fit-evidence.json").read_bytes())
    calibration = workspace.decode_json((base / "pose-layer-calibration.json").read_bytes())
    entries = []
    for number, row in enumerate(evidence["results"]):
        pack = registry[row["part"]["id"]]
        binding = next(b for b in pack["bindings"] if b["motion"] == index["motion"])
        checks = {check["id"]: check for check in row["checks"]}
        entry = {"id": f"entry.{number}", "part": contract.reference(pack), "binding": binding["id"],
            "piece": row["piece"], "clip": row["state"] + ".right", "sample": row["sample"],
            "rules": {"max_size": checks["size"]["limit"], "bounds": checks["position"]["limit"],
                      "gap_region": [30, 12, 98, 64] if row["slot"] == "hair" else None}}
        if row["slot"] in {"upper", "lower", "footwear"}:
            variant = pack["id"].rsplit(".", 1)[-1]
            folder = base / "parts-source" / f"sd.matched.{row['slot']}.{variant}"
            masks = {"required": ("required", "luma")}
            if row["slot"] != "footwear":
                masks.update(protected=("protected", "luma"), allowed=("mask", "alpha"))
            elif (folder / f"{row['cell']:02d}.allowed.png").exists():
                masks["allowed"] = ("allowed", "luma")
            for key, (suffix, channel) in masks.items():
                path = folder / f"{row['cell']:02d}.{suffix}.png"
                overrides = [item for item in calibration.get("fit_interface_overrides", [])
                             if item["slot"] == row["slot"] and item["cell"] == row["cell"]]
                if overrides:
                    annotated = Image.open(path).copy()
                    for item in overrides:
                        for x0, y0, x1, y1 in item["protected_regions"]:
                            value = 255 if key == "protected" else 0
                            if annotated.mode == "RGBA":
                                pixels = np.array(annotated, copy=True)
                                pixels[y0:y1, x0:x1, 3] = value
                                annotated = Image.fromarray(pixels)
                            else:
                                annotated.paste(value, (x0, y0, x1, y1))
                    path = base / "authored-interfaces" / f"{pack['id']}.{row['cell']:02d}.{key}.png"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    annotated.save(path)
                entry[key] = {"image": {"file": path.relative_to(base).as_posix(), "sha256": assets.sha256(path.read_bytes())},
                              "channel": channel}
        entries.append(entry)
    request = {"kind": "parts-fitting-import", "schema_version": 1, "id": "sd.authored.fitting",
               "rig": index["rig"], "motion": index["motion"], "entries": entries}
    path = base / "fitting-import.json"
    write(path, request)
    return parts_fit.import_fitting(assembly, path, base)


def refresh_selection(base):
    assembly = base / "library/assembly.json"
    index, registry = workspace.read_workspace(assembly)
    selections = workspace.decode_json((base / "selection.json").read_bytes())
    for slot, value in selections["selections"].items():
        if value is not None:
            selections["selections"][slot] = contract.reference(registry[value["id"]])
    write(base / "selection.json", selections)
    return parts_appearance.create(assembly, base / "selection.json")


def apply_authored_masks(base):
    """Round-trip corrected clothing interfaces through normal part import."""
    assembly = base / "library/assembly.json"
    index, registry = workspace.read_workspace(assembly)
    fitting = workspace.decode_json((base / "fitting-import.json").read_bytes())
    for identity in ("sd.wardrobe.upper.a", "sd.wardrobe.upper.b"):
        original = registry[identity]
        binding = original["bindings"][0]
        owner = (assembly.parent / index["documents"][identity]).parent
        reader = assets.SourceReader(owner)
        source = base / "mask-revisions" / identity
        pieces = deepcopy(binding["pieces"])

        def copy_asset(reference):
            target = source / reference["file"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(reader.read(reference["file"], reference["sha256"]))

        for piece in pieces:
            for reference in piece["palette_regions"].values():
                copy_asset(reference)
            for clip_id, mapping in piece["sample_map"].items():
                for sample_id, placement in mapping.items():
                    copy_asset(placement["image"])
                    for reference in placement["palette_regions"].values():
                        copy_asset(reference)
                    if placement["mask"] is not None:
                        copy_asset(placement["mask"]["image"])
                        interface = next(row for row in fitting["entries"] if row["part"]["id"] == identity
                                         and row["piece"] == piece["id"] and row["clip"] == clip_id and row["sample"] == sample_id)
                        declared = interface["allowed"]["image"]
                        if declared["file"].startswith("authored-interfaces/"):
                            data = (base / declared["file"]).read_bytes()
                            filename = f"authored/{assets.sha256(data)}.png"
                            target = source / filename
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_bytes(data)
                            placement["mask"]["image"] = {"file": filename, "sha256": assets.sha256(data)}
                    placement.pop("source", None)
                    placement.update(format="local", crop_offset=[0, 0])
        spec = {"kind": "parts-pack-import", "schema_version": 1, "id": identity, "design_id": original["design_id"],
                "role": "part", "style": registry[index["rig"]["id"]]["style"], "rig": index["rig"], "motion": index["motion"],
                "view": binding["view"], "binding_id": binding["id"], "pieces": pieces,
                **{key: binding[key] for key in ("occupies", "requires", "excludes", "mirror_safe")}}
        path = base / "mask-revisions" / (identity + ".json")
        write(path, spec)
        parts_import.import_pack(assembly, path, source)
    refresh_selection(base)
    register_interfaces(base)


def sneakers(base):
    assembly = base / "library/assembly.json"
    index, registry = workspace.read_workspace(assembly)
    original = registry["sd.wardrobe.footwear.a"]
    binding = original["bindings"][0]
    calibration = workspace.decode_json((base / "pose-layer-calibration.json").read_bytes())
    guide = workspace.decode_json((base / "pose-guide.json").read_bytes())
    shutil.copy2(base / "raw-footwear-templates/footwear.a.png", base / "raw-footwear-templates/footwear.a-before.png")
    shutil.copy2(base / "raw-footwear-templates/footwear.a-revised.png", base / "raw-footwear-templates/footwear.a.png")
    templates = recipe.load_footwear_templates(base, calibration, "a")
    positions, evidence = {}, []
    state_index = {}
    for row in guide["records"]:
        cell, state = row["cell"], row["state"]
        sample = f"sample.{state_index.get(state, 0)}"
        state_index[state] = state_index.get(state, 0) + 1
        source = base / "parts-source/sd.matched.footwear.a"
        required = np.asarray(Image.open(source / f"{cell:02d}.required.png")) >= 128
        allowed = np.asarray(Image.open(source / f"{cell:02d}.allowed.png")) >= 128
        drawings, origins = [], []
        declarations = calibration["footwear_placements"][str(cell)]
        for number, declaration in enumerate(declarations):
            if declaration["visible"]:
                drawing = templates[declaration["template"]]
                anchor = declaration["sole_anchor"]
                drawings.append(drawing)
                origins.append([anchor[0]-drawing.width//2, anchor[1]-drawing.height])
        layer, shifts = recipe.place_footwear_pair(drawings, origins, required, allowed)
        normalized, _ = parts_palette.normalize(base, layer)
        filename = f"revised-sneakers/{state}/{sample}.png"
        target = base / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(assets.png_bytes(normalized))
        positions[state + ".right", sample] = {"file": filename, "sha256": assets.sha256(target.read_bytes())}
        evidence.append({"state": state, "sample": sample, "positions": shifts,
                         "per_pose_resize": False, "runtime_mask": False})
    pieces = deepcopy(binding["pieces"])
    for piece in pieces:
        for clip, mapping in piece["sample_map"].items():
            for sample, placement in mapping.items():
                placement.pop("source", None)
                placement["image"] = positions[clip, sample]
                placement.update(format="full-canvas", crop_offset=[0, 0], mask=None)
    spec = {"kind": "parts-pack-import", "schema_version": 1, "id": original["id"],
        "design_id": original["design_id"], "role": "part", "style": registry[index["rig"]["id"]]["style"],
        "rig": index["rig"], "motion": index["motion"], "view": binding["view"],
        "binding_id": binding["id"], "occupies": binding["occupies"], "pieces": pieces}
    path = base / "sneakers-import.json"
    write(path, spec)
    imported = parts_import.import_pack(assembly, path, base)
    write(base / "sneakers-evidence.json", {"part": imported["part"], "templates": 6,
          "instances": evidence, "new_body_clips": 0, "appearance_clips_baked": 0, "art_approved": False})
    refresh_selection(base)
    register_interfaces(base)
    return imported


def repair(base):
    assembly = base / "library/assembly.json"
    proposals = []
    for identity in ("sd.wardrobe.upper.a", "sd.wardrobe.upper.b", "sd.wardrobe.lower.a", "sd.wardrobe.lower.b"):
        result = parts_fit.repair_plan(assembly, identity, base / "repair-plans" / (identity + ".json"), radius=3, patch_gaps=True)
        proposals.append(result)
    for proposal in proposals:
        if proposal["changed_samples"]:
            parts_import.import_pack(assembly, Path(proposal["proposal"]), Path(proposal["source_root"]))
    refresh_selection(base)
    register_interfaces(base)
    write(base / "repair-summary.json", {"proposals": proposals, "body_and_motion": "preserve",
          "art_approved": False, "release_ready": False})
    return proposals


def verify_runtime(base, baseline, *, preserve_body=True):
    """Compare every basic choice/pose with the independent browser sampler."""
    assembly = base / "library/assembly.json"
    root, index, registry, _ = parts_motion._active(assembly)
    _, manifest, bundle = export_parts.pack(root, index, registry, title="SD Explorer")
    old_index, old_registry = workspace.read_workspace(baseline / "library/assembly.json")
    body = registry[manifest["body"][0]]
    if preserve_body:
        assert index["motion"] == old_index["motion"]
        assert contract.reference(body) == contract.reference(old_registry[body["id"]])
    else:
        old_motion = old_registry[old_index["motion"]["id"]]
        motion = registry[index["motion"]["id"]]
        assert len(motion["clips"]) == len(old_motion["clips"])
        for clip, previous in zip(motion["clips"], old_motion["clips"]):
            assert all(clip[key] == previous[key] for key in ("id", "state", "view", "timebase", "loop"))
            assert [(s["id"], s["duration_ticks"]) for s in clip["samples"]] == [(s["id"], s["duration_ticks"]) for s in previous["samples"]]
        assert body["body_mode"] == "flat" and len(body["bindings"][0]["pieces"]) == 1
    player = PartsPlayer.from_workspace(assembly)
    load = compose_parts.AssetLoader(root, index)
    texture_hashes = {}
    cases, expected = [], []
    keys = ("sample_id", "sample_tick", "clip_tick", "elapsed_seconds", "sample_fraction")
    layer_keys = ("id", "position", "anchor", "local_origin", "tweened")
    for choices in product("ab", repeat=5):
        selected = {slot: f"sd.wardrobe.{slot}.{choice}" for slot, choice in
                    zip(("hair", "expression", "upper", "lower", "footwear"), choices)}
        player.set_selection({slot: contract.reference(registry[identity]) for slot, identity in selected.items()})
        for clip in manifest["clips"]:
            ticks = 0
            for sample in clip["samples"]:
                milliseconds = (ticks * 1000 + clip["timebase"] - 1) // clip["timebase"]
                player.set_state(clip["state"])
                player.seek(Fraction(milliseconds, 1000))
                scene = player.layers(interpolate=False)
                intact_body = next(layer for layer in scene["layers"] if layer["pack"]["id"] == body["id"])
                assert intact_body["position"] == ["0", "0"]
                assert intact_body["image"]["sha256"] == next(c for c in registry[index["motion"]["id"]]["clips"] if c["id"] == clip["id"])["samples"][clip["samples"].index(sample)]["image"]["sha256"]
                layers = []
                for layer in scene["layers"]:
                    ref = layer["image"]
                    key = (layer["pack"]["id"], ref["file"], ref["sha256"])
                    if key not in texture_hashes:
                        texture_hashes[key] = assets.sha256(assets.png_bytes(load(layer["pack"], ref)))
                    layers.append({**{key: layer[key] for key in layer_keys}, "texture": texture_hashes[key]})
                expected.append({**{key: scene[key] for key in keys}, "layers": layers})
                cases.append({"state": clip["state"], "time": milliseconds, "selected": selected})
                ticks += sample["duration_ticks"]
    code = """const fs=require('fs');const {LayerRuntime}=require(process.argv[1]);
const input=JSON.parse(fs.readFileSync(process.argv[2]));const runtime=new LayerRuntime(input.bundle);
const keys=['sample_id','sample_tick','clip_tick','elapsed_seconds','sample_fraction'];
const layerKeys=['id','position','anchor','local_origin','tweened'];
process.stdout.write(JSON.stringify(input.cases.map(c=>{const s=runtime.scene(c.state,c.time,c.selected);
return {...Object.fromEntries(keys.map(k=>[k,s[k]])),layers:s.layers.map(l=>({
...Object.fromEntries(layerKeys.map(k=>[k,l[k]])),texture:l.texture.id}))};})));"""
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "cases.json"
        write(path, {"bundle": bundle, "cases": cases})
        result = subprocess.run(["node", "-e", code, str(export_parts.STATIC / "runtime.js"), str(path)],
                                capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == expected, "browser sampler differs from the Python layer runtime"
    report = {"sampled_scenes": len(cases), "basic_combinations": 32, "body_samples": 28,
        "shared_clips": len(manifest["clips"]), "appearance_clips_baked": 0,
        "browser_matches_python": True, "body_unchanged": contract.reference(body) == contract.reference(old_registry[body["id"]]),
        "motion_unchanged": index["motion"] == old_index["motion"], "timing_unchanged": True,
        "fitting": manifest["fit_summary"], "art_approved": False, "release_ready": False}
    write(base / "runtime-verification.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, type=Path)
    parser.add_argument("--sneakers", action="store_true")
    parser.add_argument("--repair", action="store_true")
    parser.add_argument("--apply-authored-masks", action="store_true")
    parser.add_argument("--verify-runtime", action="store_true")
    parser.add_argument("--redrawn-body", action="store_true", help="verify shared timing and intact body, allowing authored source redraws")
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    register_interfaces(args.base)
    if args.apply_authored_masks:
        apply_authored_masks(args.base)
    if args.sneakers:
        print(json.dumps(sneakers(args.base), ensure_ascii=False))
    if args.repair:
        print(json.dumps(repair(args.base), ensure_ascii=False))
    if args.verify_runtime:
        if args.baseline is None:
            parser.error("--verify-runtime requires --baseline")
        print(json.dumps(verify_runtime(args.base, args.baseline, preserve_body=not args.redrawn_body), ensure_ascii=False))
    print(json.dumps(inspect_parts.inspect(args.base / "library/assembly.json"), ensure_ascii=False))
