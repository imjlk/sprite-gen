# SPDX-License-Identifier: Apache-2.0
"""Verify the real two-part demo against body pixels and the portable runtime."""

import argparse
from fractions import Fraction
import json
from pathlib import Path
import subprocess

import numpy as np
from PIL import Image, ImageDraw
from sprite_gen.compose import export_parts
from sprite_gen.compose.parts_runtime import PartsPlayer
from sprite_gen.curate import parts_motion
from sprite_gen.frames import parts_production as p, parts_vision
from sprite_gen.spec import parts_contract as c, parts_workspace as w


def verify(out, *, require_approval=False):
    assembly = out / "library/assembly.json"
    root, index, registry, _ = parts_motion._active(assembly)
    ids = {
        "hair": "illustration.production.hair",
        "footwear": "illustration.production.sneakers",
    }
    if "qualified.hair" in registry:
        ids = {"hair": "qualified.hair", "footwear": "qualified.sneakers"}
    rows = {slot: p._load(root, identity) for slot, identity in ids.items()}
    motion = registry[index["motion"]["id"]]
    assert len(motion["clips"]) == 1 and not motion["clips"][0]["loop"]
    clip = motion["clips"][0]
    assert len(clip["samples"]) in (4, 8)
    keys = (
        "sample_id",
        "sample_tick",
        "clip_tick",
        "elapsed_seconds",
        "sample_fraction",
    )
    layer_keys = ("id", "position", "anchor", "local_origin", "tweened")
    player = PartsPlayer.from_workspace(assembly)
    player.set_state("run")
    _, manifest, bundle = export_parts.pack(
        root, index, registry, interpolation="frame"
    )
    cases, expected = [], []
    contact = Image.new(
        "RGB", (1536, 384 * ((len(clip["samples"]) + 3) // 4)), (235, 235, 235)
    )
    draw = ImageDraw.Draw(contact)
    bare_count, tick, masks = 0, 0, 0
    covered_suppressed, uncovered_restored = 0, 0
    replaced = {node for binding in registry[ids['footwear']]['bindings'] for piece in binding['pieces'] for node in piece['replaces']}
    foot_ids = {body['id']+'.'+piece['id'] for body in index['body'] for binding in registry[body['id']]['bindings']
                for piece in binding['pieces'] if set(piece['provides']) & replaced}
    art_checks = []
    for n, sample in enumerate(clip["samples"]):
        pose = sample["pose_id"]
        for slot, identity in ids.items():
            state = rows[slot]
            row = state["tracks"]["main"]["poses"][pose]
            metrics = row["check"]["metrics"]
            art_checks.append({"part": identity, "pose": pose,
                               "decision": p.effective_decision(root,index,state,pose), "stored_decision": row['decision'], "check": row["check"]["status"],
                               "missing": metrics["required_pixels"]})
            if require_approval:
                assert row["check"]["status"] != "error", (identity, pose, metrics)
                assert all(metrics[k] == 0 for k in ("required_pixels", "allowed_pixels", "protected_pixels"))
                vision = parts_vision.current(root, index, state, pose)
                assert vision and vision["current"] and vision["status"] == "pass", (
                    identity,
                    pose,
                )
                assert vision["transport"]["transport"] == "codex-exec"
                assert row["decision"] == "approved", (identity, pose, row["decision"])
        ms = (tick * 1000 + clip["timebase"] - 1) // clip["timebase"] + 1
        player.seek(Fraction(ms, 1000))
        clocks = []
        for hair, shoes in ((False, False), (True, False), (False, True), (True, True)):
            selected = {
                "hair": ids["hair"] if hair else "",
                "footwear": ids["footwear"] if shoes else "",
            }
            player.set_selection(
                {
                    slot: c.reference(registry[id]) if id else None
                    for slot, id in selected.items()
                }
            )
            scene = player.layers(interpolate=False)
            if foot_ids:
                visible_ids = {layer['render_id'] for layer in scene['layers']}
                if shoes:
                    assert not visible_ids & foot_ids, (pose, 'covered foot still rendered')
                    covered_suppressed += 1
                else:
                    assert foot_ids <= visible_ids, (pose, 'bare foot failed to return')
                    uncovered_restored += 1
            assert scene["sample_id"] == sample["id"]
            masks += len(scene["masks"])
            clocks.append(tuple(scene[k] for k in keys))
            cases.append({"state": "run", "time": ms, "selected": selected})
            expected.append(
                {k: scene[k] for k in keys}
                | {"layers": [{k: l[k] for k in layer_keys} for l in scene["layers"]]}
            )
            image, _ = player.frame()
            if not hair and not shoes:
                source = Image.open(root / "inputs/body" / f"{n:03}.png").convert(
                    "RGBA"
                )
                assert np.array_equal(np.asarray(image), np.asarray(source)), pose
                bare_count += 1
            if hair and shoes:
                layers = [l for l in scene["layers"] if l["slot"] in ids]
                assert {l["node"] for l in layers} == {
                    "hair",
                    "shoe_back",
                    "shoe_front",
                }
                tile = Image.new("RGBA", image.size, (235, 235, 235, 255))
                tile.alpha_composite(image)
                xy = ((n % 4) * 384, (n // 4) * 384)
                contact.paste(
                    tile.resize((384, 384), Image.Resampling.BILINEAR).convert("RGB"),
                    xy,
                )
                draw.text((xy[0] + 8, xy[1] + 8), pose, fill=(15, 15, 15))
        assert len(set(clocks)) == 1
        tick += sample["duration_ticks"]
    file = out / "runtime-cases.json"
    file.write_bytes(w.json_bytes({"bundle": bundle, "cases": cases}))
    js = """
const fs=require('fs'), {LayerRuntime}=require(process.argv[1]);
const input=JSON.parse(fs.readFileSync(process.argv[2])), runtime=new LayerRuntime(input.bundle);
const keys=['sample_id','sample_tick','clip_tick','elapsed_seconds','sample_fraction'];
process.stdout.write(JSON.stringify(input.cases.map(c=>{const s=runtime.scene(c.state,c.time,c.selected);
return {...Object.fromEntries(keys.map(k=>[k,s[k]])),layers:s.layers.map(l=>Object.fromEntries(
['id','position','anchor','local_origin','tweened'].map(k=>[k,l[k]])))};})));
"""
    actual = subprocess.run(
        ["node", "-e", js, str(export_parts.STATIC / "runtime.js"), str(file)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(actual.stdout) == expected
    assert masks == 0 and manifest["appearance_clips_baked"] == 0
    proof = {
        "body_pixel_identical": bare_count,
        "python_js_cases_identical": len(cases),
        "covered_foot_layers_suppressed_cases": covered_suppressed,
        "bare_foot_layers_restored_cases": uncovered_restored,
        "motion": index["motion"],
        "parts": ids,
        "masks": masks,
        "appearance_clips_baked": 0,
        "loop": False,
        "approved": require_approval,
        "verification_scope": "runtime invariants; artwork quality is separate",
        "art_checks": art_checks,
        "release_ready": False,
    }
    (out / "runtime-verification.json").write_bytes(w.json_bytes(proof))
    contact.save(out / "actual-two-part-contact.jpg", quality=95)
    export_parts.export(
        assembly,
        out / "export",
        title=f"Independent parts · {len(clip['samples'])} source poses",
        interpolation="frame",
    )
    return proof


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--require-approval", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(verify(args.out.absolute(), require_approval=args.require_approval))
    )
