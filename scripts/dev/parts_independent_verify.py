# SPDX-License-Identifier: Apache-2.0
"""Verify the demo's part combinations against body pixels and the portable runtime."""

import argparse
from fractions import Fraction
import json
from itertools import product
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
        if 'qualified.socks' in registry:
            ids['socks'] = 'qualified.socks'
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
    sock_cuff_cases = sock_foot_cases = sock_foot_hidden_cases = 0
    replaced = {node for binding in registry[ids['footwear']]['bindings'] for piece in binding['pieces'] for node in piece['replaces']}
    foot_ids = {body['id']+'.'+piece['id'] for body in index['body'] for binding in registry[body['id']]['bindings']
                for piece in binding['pieces'] if set(piece['provides']) & replaced}
    art_checks = []
    for n, sample in enumerate(clip["samples"]):
        pose = sample["pose_id"]
        for slot, identity in ids.items():
            state = rows[slot]
            row = state["tracks"]["main"]["poses"][pose]
            check = row.get('check') or {}
            metrics = check.get('metrics',{})
            art_checks.append({"part": identity, "pose": pose,
                               "decision": p.effective_decision(root,index,state,pose), "stored_decision": row['decision'], "check": check.get('status','unchecked'),
                               "missing": metrics.get('required_pixels')})
            if require_approval:
                assert check.get('status') in ('passed','warning'), (identity, pose, metrics)
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
        for choices in product((False, True), repeat=len(ids)):
            enabled = dict(zip(ids, choices))
            selected = {slot: ids[slot] if enabled[slot] else '' for slot in ids}
            shoes, socks = enabled['footwear'], enabled.get('socks', False)
            player.set_selection(
                {
                    slot: c.reference(registry[id]) if id else None
                    for slot, id in selected.items()
                }
            )
            scene = player.layers(interpolate=False)
            if foot_ids:
                visible_ids = {layer['render_id'] for layer in scene['layers']}
                if shoes or socks:
                    assert not visible_ids & foot_ids, (pose, 'covered foot still rendered')
                    covered_suppressed += 1
                else:
                    assert foot_ids <= visible_ids, (pose, 'bare foot failed to return')
                    uncovered_restored += 1
            if 'socks' in ids:
                draw_ids = {layer['render_id'] for layer in scene['layers']}
                cuffs = {ids['socks']+'.sock_'+side+'_cuff' for side in ('back','front')}
                feet = {ids['socks']+'.sock_'+side+'_foot' for side in ('back','front')}
                assert (cuffs <= draw_ids) if socks else not (cuffs & draw_ids), (pose, 'sock cuff visibility')
                if socks:
                    sock_cuff_cases += 1
                    if shoes:
                        assert not (feet & draw_ids), (pose, 'sock feet escape the shoe')
                        sock_foot_hidden_cases += 1
                    else:
                        assert feet <= draw_ids, (pose, 'sock feet missing without shoe')
                        sock_foot_cases += 1
                else:
                    assert not feet & draw_ids
            assert scene["sample_id"] == sample["id"]
            masks += len(scene["masks"])
            clocks.append(tuple(scene[k] for k in keys))
            cases.append({"state": "run", "time": ms, "selected": selected})
            expected.append(
                {k: scene[k] for k in keys}
                | {"layers": [{k: l[k] for k in layer_keys} for l in scene["layers"]],
                   "masks":[{**{k:m[k] for k in ('target_node','space','channel','origin')},'texture_sha256':m['image']['sha256']} for m in scene['masks']]}
            )
            image, _ = player.frame()
            if not any(choices):
                source = Image.open(root / "inputs/body" / f"{n:03}.png").convert(
                    "RGBA"
                )
                assert np.array_equal(np.asarray(image), np.asarray(source)), pose
                bare_count += 1
            if all(choices):
                layers = [l for l in scene["layers"] if l["slot"] in ids]
                assert {l['slot'] for l in layers} == set(ids)
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
['id','position','anchor','local_origin','tweened'].map(k=>[k,l[k]]))),masks:s.masks.map(m=>({...Object.fromEntries(
['target_node','space','channel','origin'].map(k=>[k,m[k]])),texture_sha256:m.texture.id}))};})));
"""
    actual = subprocess.run(
        ["node", "-e", js, str(export_parts.STATIC / "runtime.js"), str(file)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(actual.stdout) == expected
    assert manifest["appearance_clips_baked"] == 0
    proof = {
        "body_pixel_identical": bare_count,
        "python_js_cases_identical": len(cases),
        "covered_foot_layers_suppressed_cases": covered_suppressed,
        "bare_foot_layers_restored_cases": uncovered_restored,
        "sock_cuff_visible_cases": sock_cuff_cases,
        "sock_foot_visible_without_shoes_cases": sock_foot_cases,
        "sock_foot_hidden_inside_shoes_cases": sock_foot_hidden_cases,
        "motion": index["motion"],
        "parts": ids,
        "masks": masks,
        "mask_policy":'explicit per-pose attachment masks' if masks else 'no masks',
        "appearance_clips_baked": 0,
        "loop": False,
        "approved": require_approval,
        "verification_scope": "runtime invariants; artwork quality is separate",
        "art_checks": art_checks,
        "release_ready": False,
    }
    (out / "runtime-verification.json").write_bytes(w.json_bytes(proof))
    contact.save(out / "actual-two-part-contact.jpg", quality=95)
    contact.save(out / "actual-parts-contact.png")
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
