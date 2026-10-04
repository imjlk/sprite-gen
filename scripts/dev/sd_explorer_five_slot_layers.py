# SPDX-License-Identifier: Apache-2.0
"""Five-slot paper-doll demo: intact video body plus independent pose artwork.

The offline importer calibrates the artist's pose drawings once. Playback only
selects those immutable pictures and translates them on the shared body clock.
No body reconstruction, material projection, limb rotation or appearance bake.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from fractions import Fraction
from itertools import product
import json
from pathlib import Path
import shutil

from PIL import Image, ImageDraw

from sprite_gen._deps import np
from sprite_gen.compose.parts_runtime import PartsPlayer
from sprite_gen.curate import parts_appearance, parts_motion
from sprite_gen.frames import parts_import
from sprite_gen.spec import parts_assets as assets, parts_contract as contract, parts_workspace as workspace


SLOTS = ('hair', 'expression', 'upper', 'lower', 'footwear')


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(workspace.json_bytes(value))
    return path


def landmarks(image, torso):
    """Measure two 2D reference points without changing the intact body."""
    rgba = np.asarray(image.convert('RGBA'))
    # Use the full shorts area for X, not a thin top row that can pick up a hand
    # outline. The frozen torso point supplies the stable vertical reference.
    rgb = rgba[..., :3].astype(np.int16)
    rows = np.indices(rgba.shape[:2])[0]
    gray = ((rgba[..., 3] > 0) & (rows >= 87) & (rows < 97)
            & (rgb[..., 0] >= 50) & (rgb[..., 0] <= 120)
            & (rgb.max(axis=2)-rgb.min(axis=2) <= 15))
    _, x = np.where(gray)
    if not len(x):
        raise ValueError('waist reference is missing')
    waist = [round(float(np.median(x))), torso[1]+18]
    upper_area = ((rgba[..., 3] > 0) & (rows >= 65) & (rows < 88)
                  & (rgb[..., 0] > 120) & (rgb[..., 0] < 225)
                  & (rgb.max(axis=2)-rgb.min(axis=2) < 18))
    _, upper_x = np.where(upper_area)
    if not len(upper_x):
        raise ValueError('upper garment reference is missing')
    upper = [round((int(upper_x.min())+int(upper_x.max()))/2), torso[1]]
    y, x = np.where(rgba[98:, :, 3] > 0)
    if not len(x):
        raise ValueError('feet reference is missing')
    feet = [round((int(x.min())+int(x.max()))/2), image.getbbox()[3]+1]
    return {'waist': waist, 'feet': feet, 'upper': upper}


def feature(image, waist):
    """Pose comparison only; the source raster remains unmodified."""
    mask = Image.fromarray((np.asarray(image)[..., 3] > 0).astype(np.uint8)*255)
    return np.asarray(mask.crop((waist[0]-38, waist[1]+5, waist[0]+38, waist[1]+30))) > 0


def calibrate(base, source):
    config = workspace.decode_json((base / 'pose-art.json').read_bytes())
    guide = workspace.decode_json((base / config['guide']).read_bytes())
    records = guide['records']
    source_index, source_registry = workspace.read_workspace(source / 'library/assembly.json')
    source_motion = source_registry[source_index['motion']['id']]
    torso_tracks = {clip['state']: [s['sockets']['torso']['position'] for s in clip['samples']]
                    for clip in source_motion['clips']}
    poses = []
    for row in records:
        number = int(row['sample_id'].split('.')[1])
        image = Image.open(source / 'motion-source' / row['state'] / f'{number:03d}.png').convert('RGBA')
        points = landmarks(image, torso_tracks[row['state']][number])
        poses.append({**row, 'points': points, 'feature': feature(image, points['waist'])})
    fitted = {}
    evidence = []
    for design in config['designs']:
        path = base / design['source']
        if assets.sha256(path.read_bytes()) != design['source_sha256']:
            raise ValueError('pose artwork source changed')
        atlas = Image.open(path).convert('RGBA').resize((288, 288), Image.Resampling.NEAREST)
        atlas.putalpha(atlas.getchannel('A').point(lambda a: 255 if a >= 128 else 0))
        fitted[design['id']] = {}
        for pose in poses:
            cell = pose['cell']
            image = atlas.crop((cell % 4*72, cell // 4*72, cell % 4*72+72, cell // 4*72+72))
            bbox = image.getbbox()
            if bbox is None:
                raise ValueError('pose artwork cell is empty')
            cropped = image.crop(bbox)
            sx, sy = design['asset_scale']
            cropped = cropped.resize((round(cropped.width*sx), round(cropped.height*sy)), Image.Resampling.NEAREST)
            points = pose['points']
            if design['slot'] == 'lower':
                alpha = np.asarray(cropped)[..., 3] > 0
                y, x = np.where(alpha[:min(3, cropped.height)])
                placement = [points['waist'][0]-round(float(np.median(x))), points['waist'][1]]
                anchor = 'waist'
            else:
                placement = [points['feet'][0]-round(cropped.width/2), points['feet'][1]-cropped.height]
                anchor = 'feet'
            canvas = Image.new('RGBA', (128, 128))
            canvas.alpha_composite(cropped, tuple(placement))
            output = base / 'parts-source' / design['id'] / f'{cell:02d}.png'
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(assets.png_bytes(canvas))
            fitted[design['id']][cell] = {
                'image': {'file': output.relative_to(base).as_posix(), 'sha256': assets.sha256(output.read_bytes())},
                'reference_anchor': points[anchor], 'anchor': anchor,
            }
            evidence.append({'design': design['id'], 'cell': cell, 'state': pose['state'],
                             'sample_id': pose['sample_id'], 'source_bbox_in_crop': list(bbox),
                             'asset_scale': design['asset_scale'], 'reference_placement': placement,
                             'image': fitted[design['id']][cell]['image']})
    write(base / 'asset-fitting.json', {'kind': 'sd-pose-art-fitting', 'schema_version': 1,
                                      'reference_crop': guide['reference_crop'], 'drawings': evidence,
                                      'operation': 'one-time artwork import alignment; playback translates only'})
    matches = {}
    for state in ('idle', 'walk', 'run'):
        candidates = [p for p in poses if p['state'] == state]
        matches[state] = []
        for number, path in enumerate(sorted((source / 'motion-source' / state).glob('*.png'))):
            image = Image.open(path).convert('RGBA')
            points = landmarks(image, torso_tracks[state][number])
            current = feature(image, points['waist'])
            pose = min(candidates, key=lambda p: (int(np.count_nonzero(current != p['feature'])), p['cell']))
            matches[state].append({'cell': pose['cell'], 'source_pose_sample': pose['sample_id'],
                                   'difference_pixels': int(np.count_nonzero(current != pose['feature'])),
                                   'points': points})
    write(base / 'pose-matches.json', {**matches, 'policy': 'nearest source-body lower silhouette per state; translation only'})
    return config, fitted, matches


def initialize(base, source):
    library = base / 'library'
    if library.exists():
        raise ValueError('destination library must be new')
    workspace.initialize(library, 'human.sd.v1')
    assembly = library / 'assembly.json'
    index, registry = workspace.read_workspace(assembly)
    old_index, old_registry = workspace.read_workspace(source / 'library/assembly.json')
    old_rig = old_registry[old_index['rig']['id']]
    topology = deepcopy(old_registry[old_rig['topology']['id']])
    topology.pop('digest')
    topology['id'] = 'sd.wardrobe.layers.topology'
    for node in ('lower_clothing', 'footwear'):
        topology['nodes'].append({'id': node, 'parent': 'root', 'required': False})
    for slot in topology['slots']:
        if slot['id'] == 'eyes':
            slot['id'] = 'expression'
    topology['slots'].extend({'id': slot, 'nodes': ['root'], 'sockets': ['root'],
                             'optional': True, 'empty_policy': 'no-piece'} for slot in ('lower', 'footwear'))
    topology = contract.seal(topology)
    rig = deepcopy(old_rig)
    rig.pop('digest')
    rig.update(id='sd.wardrobe.layers.rig', topology=contract.reference(topology))
    rig = contract.seal(rig)
    catalog = deepcopy(old_registry[old_index['catalog']['id']])
    catalog.pop('digest')
    catalog.update(id='sd.wardrobe.layers.catalog', topology=contract.reference(topology), rig=contract.reference(rig))
    for slot in catalog['slots']:
        slot.update(options=[], default=None)
        if slot['id'] == 'eyes':
            slot.update(id='expression', label='표정')
        if slot['id'] == 'upper':
            slot['label'] = '상의'
    catalog['slots'].extend({'id': slot, 'group': 'clothing', 'label': label, 'options': [], 'default': None}
                            for slot, label in (('lower', '하의'), ('footwear', '신발')))
    catalog['slots'].sort(key=lambda s: SLOTS.index(s['id']))
    catalog = contract.seal(catalog)
    index.update(documents={topology['id']: 'profiles/topology.json', rig['id']: 'profiles/rig.json', catalog['id']: 'catalog.json'},
                 rig=contract.reference(rig), catalog=contract.reference(catalog))
    for doc, filename in ((topology, 'profiles/topology.json'), (rig, 'profiles/rig.json'), (catalog, 'catalog.json')):
        write(library / filename, doc)
    write(assembly, index)
    return assembly, old_index, old_registry


def build(base, source, wardrobe):
    config, fitted, matches = calibrate(base, source)
    assembly, old_index, old_registry = initialize(base, source)
    index, registry = workspace.read_workspace(assembly)
    request = workspace.decode_json((source / 'motion-import.json').read_bytes())
    request.update(id='sd.wardrobe.video.motion', rig=index['rig'])
    imported = parts_motion.import_motion(assembly, write(base / 'motion-import.json', request), source / 'motion-source')
    annotations = workspace.decode_json((assembly.parent / imported['annotation_template']).read_bytes())
    old_motion = old_registry[old_index['motion']['id']]
    for clip in old_motion['clips']:
        for sample in clip['samples']:
            target = annotations['clips'][clip['id']][sample['id']]
            assert target['image_sha256'] == sample['image']['sha256']
            target['sockets'] = deepcopy(sample['sockets'])
    parts_motion.freeze_motion(assembly, imported['candidate'], write(base / 'attachment-tracks.json', annotations))
    index, registry = workspace.read_workspace(assembly)
    motion = registry[index['motion']['id']]
    for old, new in zip(old_motion['clips'], motion['clips']):
        assert all(old[key] == new[key] for key in ('id', 'state', 'timebase', 'loop', 'samples'))
    shutil.copytree(source / 'motion-source', base / 'motion-source')
    shutil.copytree(wardrobe / 'parts-source', base / 'parts-source', dirs_exist_ok=True)
    # Calibrate these two held drawings once to cover the source garment.
    # The immutable body and the runtime layer renderer are untouched.
    upper_images = {}
    upper_fitting = []
    for variant, filename in (('a', 'shirt.png'), ('b', 'sd.simple.shirt.b.png')):
        original_path = base / 'parts-source' / filename
        original = Image.open(original_path).convert('RGBA')
        bbox = original.getbbox()
        drawing = original.crop(bbox).resize((22, bbox[3]-bbox[1]), Image.Resampling.NEAREST)
        canvas = Image.new('RGBA', (128, 128))
        canvas.alpha_composite(drawing, (53, bbox[1]))
        output = base / 'parts-source' / f'sd.wardrobe.upper.{variant}.png'
        output.write_bytes(assets.png_bytes(canvas))
        upper_images[variant] = {'file': output.relative_to(base).as_posix(), 'sha256': assets.sha256(output.read_bytes())}
        upper_fitting.append({'variant': variant, 'source': original_path.relative_to(base).as_posix(),
            'source_sha256': assets.sha256(original_path.read_bytes()), 'source_bbox': list(bbox),
            'import_size': [22, bbox[3]-bbox[1]], 'placement': [53, bbox[1]], 'image': upper_images[variant]})
    write(base / 'upper-asset-fitting.json', {'kind': 'sd-held-upper-fitting', 'schema_version': 1,
        'operation': 'one-time nearest-neighbor artwork import sizing; immutable held texture at playback',
        'drawings': upper_fitting})
    choices = {s: [] for s in SLOTS}
    body = workspace.decode_json((source / 'import/body.json').read_bytes())
    body.update(id='sd.wardrobe.body', binding_id='sd.wardrobe.body.binding', rig=index['rig'], motion=index['motion'])
    parts_import.import_pack(assembly, write(base / 'import/body.json', body), base)
    for slot, name in (('expression', 'eyes'), ('hair', 'hair'), ('upper', 'shirt')):
        for variant in ('a', 'b'):
            template = source / 'import' / f'{name}.json' if variant == 'a' else wardrobe / 'import' / f'sd.simple.{name}.b.json'
            spec = workspace.decode_json(template.read_bytes())
            part_id = f'sd.wardrobe.{slot}.{variant}'
            spec.update(id=part_id, binding_id=part_id+'.binding', rig=index['rig'], motion=index['motion'], occupies=[slot])
            for piece in spec['pieces']:
                piece['slot'] = slot
                for clip_id, samples in piece['sample_map'].items():
                    state = next(c['state'] for c in motion['clips'] if c['id'] == clip_id)
                    for sample_id, placement in samples.items():
                        if slot == 'upper':
                            placement['after'] = ['root', 'lower_clothing', 'footwear']
                            placement['image'] = upper_images[variant]
                            number = int(sample_id.split('.')[1])
                            sample = next(c for c in motion['clips'] if c['id'] == clip_id)['samples'][number]
                            # The waist can move away from the chest in a run.
                            # Keep torso Y and center the held garment on the
                            # corresponding intact source garment, by translation.
                            placement.setdefault('offset', [0, 0])[0] += matches[state][number]['points']['upper'][0]-sample['sockets']['torso']['position'][0]
            choices[slot].append(parts_import.import_pack(assembly, write(base / 'import' / (part_id+'.json'), spec), base)['part'])
    for design in config['designs']:
        slot, part_id = design['slot'], design['id']
        provided = 'lower_clothing' if slot == 'lower' else 'footwear'
        mapping = {}
        for clip in motion['clips']:
            mapping[clip['id']] = {}
            for sample, match in zip(clip['samples'], matches[clip['state']]):
                entry = fitted[part_id][match['cell']]
                delta = [a-b for a, b in zip(match['points'][entry['anchor']], entry['reference_anchor'])]
                mapping[clip['id']][sample['id']] = {'image': entry['image'], 'format': 'full-canvas',
                    'pivot': [0, 0], 'target_socket': 'root', 'offset': delta,
                    'after': ['root'] if slot == 'lower' else ['root', 'lower_clothing']}
        spec = {'kind': 'parts-pack-import', 'schema_version': 1, 'id': part_id,
                'design_id': design['design_id'], 'role': 'part', 'body_mode': 'segmented',
                'style': registry[index['rig']['id']]['style'], 'rig': index['rig'], 'motion': index['motion'],
                'view': registry[index['rig']['id']]['view'], 'binding_id': part_id+'.binding', 'occupies': [slot],
                'pieces': [{'id': slot, 'slot': slot, 'node': 'root', 'provides': [provided],
                            'replaces': [], 'mode': 'raster', 'sample_map': mapping}]}
        choices[slot].append(parts_import.import_pack(assembly, write(base / 'import' / (part_id+'.json'), spec), base)['part'])
    assert all(len(v) == 2 for v in choices.values())
    selection = {'kind': 'parts-appearance-selection', 'schema_version': 1, 'id': 'sd.five.slot.layers',
                 'selections': {s: refs[0] for s, refs in choices.items()}}
    parts_appearance.create(assembly, write(base / 'selection.json', selection))
    write(base / 'choices.json', choices)
    write(base / 'body-motion-origin.json', {'kind': 'sd-five-slot-body-provenance', 'schema_version': 1,
        'previous_rig': old_index['rig'], 'previous_motion': old_index['motion'], 'rig': index['rig'], 'motion': index['motion'],
        'change': 'five slot declarations; full body images, socket tracks, sample order and timing unchanged',
        'body_split': False, 'body_replacements': 0, 'appearance_clips_baked': 0,
        'runtime_texture_warps': 0, 'art_approved': False, 'release_ready': False})
    return assembly


def check(base):
    assembly = base / 'library/assembly.json'
    p = PartsPlayer.from_workspace(assembly)
    index, registry = workspace.read_workspace(assembly)
    motion = registry[index['motion']['id']]
    choices = workspace.decode_json((base / 'choices.json').read_bytes())
    saved = {f.relative_to(assembly.parent).as_posix(): assets.sha256(f.read_bytes())
             for f in assembly.parent.rglob('*') if f.is_file()}
    sheet = Image.new('RGBA', (8*256, 12*272), (239, 240, 234, 255))
    text = ImageDraw.Draw(sheet)
    evaluations = 0
    appearance_hashes = {state: set() for state in index['states']}
    for column, refs in enumerate(product(*choices.values())):
        p.set_selection(dict(zip(choices, refs)))
        for state_row, state in enumerate(index['states']):
            p.set_state(state)
            clip = next(c for c in motion['clips'] if c['state'] == state)
            for number, sample in enumerate(clip['samples']):
                p.seek(Fraction(number, 24))
                scene = p.layers(interpolate=False)
                body = scene['layers'][0]
                assert body['slot'] is None and body['position'] == ['0', '0']
                assert body['image']['sha256'] == sample['image']['sha256']
                assert len(scene['layers']) == 6
                image, evidence = p.frame()
                assert all(d['clipped_pixels'] == 0 for d in evidence['evidence']['draw'])
                if number == 0:
                    appearance_hashes[state].add(assets.sha256(image.tobytes()))
                    x, y = column % 8*256, (state_row*4+column//8)*272
                    sheet.alpha_composite(image.resize((256, 256), Image.Resampling.NEAREST), (x, y+16))
                    text.text((x+4, y), f'{state} appearance {column:02d}', fill=(20, 20, 20, 255))
                evaluations += 1
    assert all(len(hashes) == 32 for hashes in appearance_hashes.values())
    # Swap and empty each slot mid-sample; body and exact clock remain intact.
    p.set_state('run')
    p.seek(Fraction(241, 240))
    clock = p.current()[2]
    original = deepcopy(p.appearance['selections'])
    body_ref = p.layers()['layers'][0]['image']
    for slot, refs in choices.items():
        for ref in (None, *refs):
            p.set_selection({slot: ref})
            assert p.current()[2] == clock and p.layers()['layers'][0]['image'] == body_ref
        p.set_selection({slot: original[slot]})
    p.set_selection({s: None for s in choices})
    assert len(p.layers()['layers']) == 1
    assert saved == {f.relative_to(assembly.parent).as_posix(): assets.sha256(f.read_bytes())
                     for f in assembly.parent.rglob('*') if f.is_file()}
    sheet.save(base / '32-appearances-review.png')
    result = {'slots': list(choices), 'designs_per_slot': {s: len(v) for s, v in choices.items()},
              'appearances': 32, 'states': index['states'], 'state_cases': 96,
              'sample_evaluations': evaluations, 'whole_body_preserved': True, 'swap_clock_preserved': True,
              'distinct_appearances_per_state': {state: len(hashes) for state, hashes in appearance_hashes.items()},
              'appearance_clips_baked': 0, 'runtime_texture_warps': 0,
              'art_approved': False, 'release_ready': False}
    write(base / 'validation.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True, type=Path)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--wardrobe', type=Path)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if not args.check:
        if args.source is None or args.wardrobe is None:
            parser.error('--source and --wardrobe are required to create a new library')
        build(args.base, args.source, args.wardrobe)
    print(json.dumps(check(args.base), indent=2))
