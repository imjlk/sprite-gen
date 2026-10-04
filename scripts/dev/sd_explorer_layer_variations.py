# SPDX-License-Identifier: Apache-2.0
"""Import new overlay designs over the unchanged, frozen SD body motion.

Artwork is fitted once to the shared 128px reference canvas. Every option then
uses the existing head/torso position track, with no body cuts or pose warps.
This recipe deliberately does not manufacture missing pants or shoe poses.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from fractions import Fraction
from itertools import product
import json
from pathlib import Path
import shutil

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.compose.parts_runtime import PartsPlayer
from sprite_gen.curate import parts_appearance
from sprite_gen.frames import parts_import
from sprite_gen.spec import parts_assets as assets, parts_workspace as workspace


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(workspace.json_bytes(value))
    return path


def fit(base, entry):
    raw = base / entry['source']
    if assets.sha256(raw.read_bytes()) != entry['source_sha256']:
        raise ValueError('source artwork changed before import')
    image = Image.open(raw).convert('RGBA')
    crop = image.crop(tuple(entry['source_bbox']))
    x, y, right, bottom = entry['reference_box']
    crop = crop.resize((right-x, bottom-y), Image.Resampling.NEAREST)
    crop.putalpha(crop.getchannel('A').point(lambda a: 255 if a >= 128 else 0))
    canvas = Image.new('RGBA', (128, 128))
    canvas.alpha_composite(crop, (x, y))
    path = base / 'parts-source' / (entry['id'] + '.png')
    path.write_bytes(assets.png_bytes(canvas))
    return path


def pose_guides(base, source):
    """Choose distinct real leg poses, preserving every full body image."""
    poses = [('idle', 0)]
    for state, count in (('walk', 8), ('run', 7)):
        files = sorted((source / 'motion-source' / state).glob('*.png'))
        features = []
        for path in files:
            rgba = np.asarray(Image.open(path).convert('RGBA'))
            features.append((rgba[86:116, 25:103, 3] > 0).ravel())
        vectors = np.array(features)
        chosen = [0]
        while len(chosen) < count:
            distances = np.min(np.count_nonzero(
                vectors[:, None, :] != vectors[chosen][None, :, :], axis=2), axis=1)
            distances[chosen] = -1
            chosen.append(int(np.argmax(distances)))
        poses.extend((state, number) for number in sorted(chosen))
    sheet = Image.new('RGBA', (1024, 1024))
    records = []
    folder = base / 'pose-guides'
    folder.mkdir(exist_ok=True)
    for cell, (state, number) in enumerate(poses):
        path = source / 'motion-source' / state / f'{number:03d}.png'
        image = Image.open(path).convert('RGBA')
        position = [cell % 4 * 256, cell // 4 * 256]
        sheet.alpha_composite(image.resize((256, 256), Image.Resampling.NEAREST), tuple(position))
        records.append({'cell': cell, 'state': state, 'sample_id': f'sample.{number}',
                        'body_sha256': assets.sha256(path.read_bytes()), 'cell_origin': position})
    sheet.save(folder / 'lower-footwear-poses-16.png')
    guide = {
        'schema_version': 1, 'grid': [4, 4], 'cell_size': [256, 256],
        'engine_canvas': [128, 128], 'global_scale': 2, 'body_images_unchanged': True,
        'selection': 'farthest lower-body alpha silhouette differences; avoids gait-period aliasing',
        'records': records, 'purpose': 'artwork pose reference; not an animation atlas or accepted binding',
    }
    write(folder / 'lower-footwear-poses-16.json', guide)
    cropped = Image.new('RGBA', (1024, 1024))
    for cell, (state, number) in enumerate(poses):
        image = Image.open(source / 'motion-source' / state / f'{number:03d}.png').convert('RGBA')
        # This is a generation reference only. The full immutable body raster
        # stays untouched, and the explicit offset restores crop coordinates.
        image = image.crop((28, 68, 100, 140)).resize((256, 256), Image.Resampling.NEAREST)
        cropped.alpha_composite(image, (cell % 4 * 256, cell // 4 * 256))
    cropped.save(folder / 'lower-footwear-crop-poses-16-v2.png')
    write(folder / 'lower-footwear-crop-poses-16-v2.json', {
        **guide, 'cell_source_canvas': [72, 72], 'reference_crop': [28, 68, 100, 140],
        'restore_offset': [28, 68], 'global_scale': '32/9',
    })


def build(base, source):
    assembly = base / 'library/assembly.json'
    if assembly.parent.exists():
        raise ValueError('destination library must be new; use --check for verification')
    original_index, _ = workspace.read_workspace(source / 'library/assembly.json')
    shutil.copytree(source / 'library', assembly.parent)
    shutil.copytree(source / 'parts-source', base / 'parts-source')
    config = workspace.decode_json((base / 'variants-art.json').read_bytes())
    selection = workspace.decode_json((source / 'selection.json').read_bytes())
    choices = {slot: [ref] for slot, ref in selection['selections'].items()}
    for entry in config['variants']:
        image = fit(base, entry)
        spec = workspace.decode_json((source / 'import' / entry['template']).read_bytes())
        spec.update(id=entry['id'], design_id=entry['design_id'], binding_id=entry['id']+'.binding')
        for piece in spec['pieces']:
            for samples in piece['sample_map'].values():
                for placement in samples.values():
                    placement['image'] = {'file': image.relative_to(base).as_posix(),
                                          'sha256': assets.sha256(image.read_bytes())}
                    placement['offset'] = entry['offset'][:]
        ref = parts_import.import_pack(assembly, write(base / 'import' / (entry['id']+'.json'), spec), base)['part']
        choices[entry['slot']].append(ref)
    selection['id'] = 'sd.layer.wardrobe'
    parts_appearance.create(assembly, write(base / 'selection.json', selection))
    index, _ = workspace.read_workspace(assembly)
    assert index['body'] == original_index['body'] and index['motion'] == original_index['motion']
    write(base / 'choices.json', choices)
    write(base / 'body-motion-origin.json', {
        'source_workspace': source.name,
        'rig': original_index['rig'], 'motion': original_index['motion'], 'body': original_index['body'],
        'states': original_index['states'],
        'body_split': False, 'texture_warps': 0, 'appearance_clips_baked': 0,
        'art_approved': False, 'release_ready': False,
    })
    pose_guides(base, source)
    return assembly


def check(base):
    assembly = base / 'library/assembly.json'
    player = PartsPlayer.from_workspace(assembly)
    index, registry = workspace.read_workspace(assembly)
    motion = registry[index['motion']['id']]
    choices = workspace.decode_json((base / 'choices.json').read_bytes())
    saved = {p.relative_to(assembly.parent).as_posix(): assets.sha256(p.read_bytes())
             for p in assembly.parent.rglob('*') if p.is_file()}
    sets = list(product(*([None, *refs] for refs in choices.values())))
    combinations = len(sets)
    sheet = Image.new('RGBA', (combinations*128, len(index['states'])*128))
    evaluations = 0
    for column, refs in enumerate(sets):
        player.set_selection(dict(zip(choices, refs)))
        for row, state in enumerate(index['states']):
            player.set_state(state)
            clip = next(c for c in motion['clips'] if c['state'] == state)
            textures = {}
            for number, sample in enumerate(clip['samples']):
                player.seek(Fraction(number, 24))
                scene = player.layers(interpolate=False)
                body = scene['layers'][0]
                assert body['slot'] is None and body['position'] == ['0', '0']
                assert body['image']['sha256'] == sample['image']['sha256']
                for layer in scene['layers'][1:]:
                    assert layer['mode'] == 'held'
                    previous = textures.setdefault(layer['id'], layer['image'])
                    assert previous == layer['image']
                image, evidence = player.frame()
                assert all(d['clipped_pixels'] == 0 for d in evidence['evidence']['draw'])
                if number == 0:
                    sheet.alpha_composite(image, (column*128, row*128))
                evaluations += 1
    assert saved == {p.relative_to(assembly.parent).as_posix(): assets.sha256(p.read_bytes())
                     for p in assembly.parent.rglob('*') if p.is_file()}
    sheet.save(base / 'combination-inspection.png')
    # Inspect actual drawings at three different leg poses, with truthful slot
    # labels independent of JSON key order. Empty choices are checked above.
    from PIL import ImageDraw
    designs = list(product(*choices.values()))
    review = Image.new('RGBA', (len(designs)*256, 3*272), (239, 240, 234, 255))
    text = ImageDraw.Draw(review)
    for column, refs in enumerate(designs):
        selected = dict(zip(choices, refs))
        player.set_selection(selected)
        label = ' '.join(f'{slot}:{choices[slot].index(ref)}' for slot, ref in selected.items())
        for row, (state, number) in enumerate((('idle', 0), ('walk', 40), ('run', 24))):
            player.set_state(state)
            player.seek(Fraction(number, 24))
            image, _ = player.frame()
            review.alpha_composite(image.resize((256, 256), Image.Resampling.NEAREST),
                                   (column*256, row*272+16))
            text.text((column*256+4, row*272), state+' '+label, fill=(20, 20, 20, 255))
    review.save(base / 'variation-art-review.png')
    original = deepcopy(player.appearance['selections'])
    player.set_state('walk')
    player.seek(Fraction(5, 3))
    clock = player.current()[2]
    for slot, refs in choices.items():
        for ref in (None, *refs):
            player.set_selection({slot: ref})
            assert player.current()[2] == clock
        player.set_selection({slot: original[slot]})
    result = {'slots': list(choices), 'designs_per_slot': {s: len(v) for s, v in choices.items()},
              'layer_sets_including_empty': combinations, 'states': index['states'],
              'sample_evaluations': evaluations, 'whole_body_preserved': True,
              'part_textures_held': True, 'swap_clock_preserved': True, 'appearance_clips_baked': 0,
              'remaining_slots': ['lower', 'footwear'], 'art_approved': False, 'release_ready': False}
    write(base / 'validation.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True, type=Path)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--pose-guides', action='store_true')
    args = parser.parse_args()
    if args.pose_guides:
        if args.source is None:
            parser.error('--source is required for pose references')
        pose_guides(args.base, args.source)
    elif not args.check:
        if args.source is None:
            parser.error('--source is required for a new wardrobe workspace')
        build(args.base, args.source)
    if not args.pose_guides:
        print(json.dumps(check(args.base), indent=2))
