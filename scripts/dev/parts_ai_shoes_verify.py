# SPDX-License-Identifier: Apache-2.0
"""Verify the real pilot build, preserved body pixels, and portable layer clock."""
from pathlib import Path
from fractions import Fraction
import argparse
import json
import subprocess

import numpy as np
from PIL import Image, ImageDraw
from sprite_gen.compose import export_parts
from sprite_gen.compose.parts_runtime import PartsPlayer
from sprite_gen.curate import parts_motion
from sprite_gen.frames import parts_production
from sprite_gen.spec import parts_contract as contract, parts_workspace as workspace


def verify(out):
    assembly = out / 'library/assembly.json'
    root, index, registry, _ = parts_motion._active(assembly)
    player = PartsPlayer.from_workspace(assembly)
    identity = 'illustration.motion.footwear.ai-pilot'
    poses = [f'run.{n:02}' for n in range(10, 18)]
    rows = parts_production._load(root, identity)['tracks']['main']['poses']
    for pose in poses:
        metrics = rows[pose]['check']['metrics']
        assert all(metrics[k] == 0 for k in ('required_pixels', 'protected_pixels', 'allowed_pixels', 'holes', 'specks')), (pose, metrics)
    assert all(row['decision'] == 'rejected' for pose, row in rows.items() if pose not in poses)
    _, manifest, bundle = export_parts.pack(root, index, registry, interpolation='frame')
    expected, cases, bare_count, masks = [], [], 0, 0
    contact = Image.new('RGB', (1560, 600), (235, 235, 235))
    draw = ImageDraw.Draw(contact)
    motion = registry[index['motion']['id']]
    keys = ('sample_id', 'sample_tick', 'clip_tick', 'elapsed_seconds', 'sample_fraction')
    layer_keys = ('id', 'position', 'anchor', 'local_origin', 'tweened')
    for clip in motion['clips']:
        player.set_state(clip['state'])
        tick = 0
        for n, sample in enumerate(clip['samples']):
            ms = (tick * 1000 + clip['timebase'] - 1) // clip['timebase'] + 1
            player.seek(Fraction(ms, 1000))
            clocks = []
            for selected in ('', identity):
                player.set_selection({'footwear': contract.reference(registry[identity]) if selected else None})
                scene = player.layers(interpolate=False)
                masks += len(scene['masks'])
                clocks.append(tuple(scene[k] for k in keys))
                cases.append({'state': clip['state'], 'time': ms, 'selected': {'footwear': selected}})
                expected.append({k: scene[k] for k in keys} | {'layers': [{k: layer[k] for k in layer_keys} for layer in scene['layers']]})
                image, _ = player.frame()
                if not selected:
                    source = Image.open(root / 'inputs/body' / clip['state'] / f'{n:03}.png').convert('RGBA')
                    assert np.array_equal(np.asarray(image), np.asarray(source)), sample['pose_id']
                    bare_count += 1
                elif sample['pose_id'] in poses:
                    shoes = [l for l in scene['layers'] if l['slot'] == 'footwear']
                    assert {l['node'] for l in shoes} == {'shoe_back', 'shoe_front'}
                    assert len(shoes) == 2
                    i = poses.index(sample['pose_id'])
                    tile = Image.new('RGBA', image.size, (235, 235, 235, 255))
                    tile.alpha_composite(image)
                    xy = ((i % 4) * 390, (i // 4) * 300)
                    contact.paste(tile.crop((180, 450, 570, 730)).convert('RGB'), (xy[0], xy[1] + 20))
                    draw.text(xy, sample['pose_id'], fill=(15, 15, 15))
            assert clocks[0] == clocks[1]
            tick += sample['duration_ticks']
    input_file = out / 'runtime-verification-cases.json'
    input_file.write_bytes(workspace.json_bytes({'bundle': bundle, 'cases': cases}))
    js = """
const fs=require('fs'), {LayerRuntime}=require(process.argv[1]);
const input=JSON.parse(fs.readFileSync(process.argv[2])), runtime=new LayerRuntime(input.bundle);
const keys=['sample_id','sample_tick','clip_tick','elapsed_seconds','sample_fraction'];
process.stdout.write(JSON.stringify(input.cases.map(c=>{
  const s=runtime.scene(c.state,c.time,c.selected);
  return {...Object.fromEntries(keys.map(k=>[k,s[k]])),layers:s.layers.map(l=>
    Object.fromEntries(['id','position','anchor','local_origin','tweened'].map(k=>[k,l[k]])))};
})));
"""
    actual = subprocess.run(['node', '-e', js, str(export_parts.STATIC / 'runtime.js'), str(input_file)], capture_output=True, text=True, check=True)
    assert json.loads(actual.stdout) == expected
    assert masks == 0 and manifest['appearance_clips_baked'] == 0
    frozen = json.loads((out / 'runtime-verification-body-corrected.json').read_text())
    lineage = json.loads((out / 'final-curation-lineage-body-corrected.json').read_text())
    assert index['body'] == lineage['body'] and index['motion'] == lineage['motion']
    assert bare_count == frozen['body_pixel_identical'] == 43
    evidence = {'body_pixel_identical': bare_count, 'python_js_cases_identical': len(cases),
                'motion_reference_unchanged': True, 'body_reference_unchanged_since_ownership_correction': True,
                'runtime_masks': masks, 'appearance_clips_baked': 0, 'produced_poses': poses,
                'unproduced_rejected': len(rows) - len(poses), 'qa': {p: rows[p]['check'] for p in poses},
                'full_cycle': False, 'release_ready': False}
    (out / 'runtime-verification-final.json').write_bytes(workspace.json_bytes(evidence))
    contact.save(out / 'actual-motion-contact-final-fit.jpg', quality=95)
    return evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.out.absolute()) | {'qa': 'see runtime-verification-final.json'}))
