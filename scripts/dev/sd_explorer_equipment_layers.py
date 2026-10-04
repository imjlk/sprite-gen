# SPDX-License-Identifier: Apache-2.0
"""Extend the intact SD paper doll with optional equipment and arm occlusion.

Foreground arm coverage limits the clothing layer, never the body layer. Gloves
use pose drawings at calibrated visible extremities; no limb rig or
texture deformation is introduced. The visible-hand estimates are not anatomical
left/right annotations or contact approval.
"""
from __future__ import annotations

import argparse
from collections import deque
from copy import deepcopy
from fractions import Fraction
from itertools import product
from pathlib import Path
import json
import shutil

from PIL import Image, ImageDraw, ImageFilter

from sprite_gen._deps import np
from sprite_gen.compose.parts_runtime import PartsPlayer
from sprite_gen.curate import parts_appearance, parts_motion
from sprite_gen.frames import parts_import
from sprite_gen.spec import parts_assets as assets, parts_contract as contract, parts_workspace as workspace
from sd_explorer_five_slot_layers import landmarks, write


EXTRAS = ('headwear', 'gloves', 'back_accessory')


def arm_coverage(image):
    rgba = np.asarray(image.convert('RGBA'))
    rgb = rgba[..., :3].astype(np.int16)
    y = np.indices(rgba.shape[:2])[0]
    visible_skin = ((rgba[..., 3] > 0) & (y >= 74) & (y < 96)
                    & (rgb[..., 0] > 120) & (rgb[..., 1] > 55)
                    & (rgb[..., 0]-rgb[..., 1] > 20)
                    & (rgb.max(axis=2)-rgb.min(axis=2) > 45))
    # Include the immediately adjacent source outline in the foreground matte.
    expanded = Image.fromarray(visible_skin.astype(np.uint8)*255).filter(ImageFilter.MaxFilter(3))
    coverage = Image.fromarray(255-np.asarray(expanded)).convert('RGBA')
    return coverage, visible_skin


def visible_hands(skin):
    remaining = set(zip(*np.where(skin)))
    components = []
    while remaining:
        seed = min(remaining)
        remaining.remove(seed)
        component, pending = {seed}, [seed]
        while pending:
            y, x = pending.pop()
            for dy, dx in product((-1, 0, 1), repeat=2):
                point = (y+dy, x+dx)
                if point in remaining:
                    remaining.remove(point)
                    component.add(point)
                    pending.append(point)
        if len(component) >= 5:
            components.append(component)
    # A torso-occluded hand is not invented when its source pixels are absent.
    components = sorted(sorted(components, key=len, reverse=True)[:2],
                        key=lambda c: min(x for _, x in c))
    hands = []
    for component in components:
        seed = min(component)
        distances, pending = {seed: 0}, deque([seed])
        while pending:
            y, x = pending.popleft()
            for dy, dx in product((-1, 0, 1), repeat=2):
                point = (y+dy, x+dx)
                if point in component and point not in distances:
                    distances[point] = distances[(y, x)]+1
                    pending.append(point)
        end = max(distances.values())
        tip = [point for point, distance in distances.items() if distance >= end-3]
        hands.append([round(float(np.mean([x for _, x in tip]))),
                      round(float(np.mean([y for y, _ in tip])))])
    return hands


def fit_art(base):
    fitted, evidence = {}, []
    requests = [('headwear', None, (35, 13, 97, 43)),
                ('backpack', None, (37, 66, 57, 95))]
    for name, side, target in requests:
        source = base / 'raw-art' / (name+'.png')
        raw = Image.open(source).convert('RGBA')
        # Ignore nearly transparent generation dust before taking the bounds.
        # Otherwise that empty margin can shrink the actual equipment to a dot.
        raw.putalpha(raw.getchannel('A').point(lambda alpha: 255 if alpha >= 128 else 0))
        if side:
            split = raw.width//2
            box = (0, 0, split, raw.height) if side == 'a' else (split, 0, raw.width, raw.height)
            raw = raw.crop(box)
        bbox = raw.getbbox()
        if bbox is None:
            raise ValueError('generated equipment is empty')
        image = raw.crop(bbox).resize((target[2]-target[0], target[3]-target[1]), Image.Resampling.NEAREST)
        image.putalpha(image.getchannel('A').point(lambda alpha: 255 if alpha >= 128 else 0))
        if side is None:
            canvas = Image.new('RGBA', (128, 128))
            canvas.alpha_composite(image, target[:2])
            image = canvas
        key = name if side is None else name+'.'+side
        path = base / 'parts-source/equipment' / (key+'.png')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(assets.png_bytes(image))
        fitted[key] = {'file': path.relative_to(base).as_posix(), 'sha256': assets.sha256(path.read_bytes())}
        evidence.append({'id': key, 'source': source.relative_to(base).as_posix(),
                         'source_sha256': assets.sha256(source.read_bytes()), 'source_bbox': list(bbox),
                         'target_bbox': list(target), 'image': fitted[key]})
    write(base / 'equipment-asset-fitting.json', {'kind': 'sd-equipment-import-fitting', 'schema_version': 1,
        'operation': 'one-time nearest-neighbor artwork sizing; immutable held pictures at playback',
        'assets': evidence})
    return fitted


def fit_glove_poses(base, observations):
    guide = workspace.decode_json((base/'guides/glove-poses-16.json').read_bytes())
    # Manually calibrated source-pose palm positions. These belong to this SD
    # art fixture, not to the generic renderer or another character's anatomy.
    anchors = [[[52, 89], [80, 85]], [[52, 89], [80, 85]], [[70, 87]],
        [[60, 89], [76, 87]], [[64, 89]], [[55, 90], [77, 87]],
        [[61, 89], [74, 88]], [[53, 90], [80, 85]], [[67, 88]],
        [[52, 89], [80, 85]], [[40, 83], [87, 77]], [[55, 87], [73, 85]],
        [[44, 85], [85, 81]], [[51, 83], [76, 81]], [[61, 88]], [[51, 84], [74, 81]]]
    source = base/'raw-art/glove-poses-16.png'
    atlas = Image.open(source).convert('RGBA').resize((288, 288), Image.Resampling.NEAREST)
    atlas.putalpha(atlas.getchannel('A').point(lambda alpha: 255 if alpha >= 128 else 0))
    drawings = {}
    for cell in range(16):
        art = atlas.crop((cell%4*72, cell//4*72, cell%4*72+72, cell//4*72+72))
        remaining = set(zip(*np.where(np.asarray(art)[..., 3] > 0)))
        pieces = []
        while remaining:
            seed = remaining.pop(); component, pending = {seed}, [seed]
            while pending:
                y, x = pending.pop()
                for dy, dx in product((-1, 0, 1), repeat=2):
                    point = (y+dy, x+dx)
                    if point in remaining:
                        remaining.remove(point); component.add(point); pending.append(point)
            if len(component) > 2:
                ys, xs = zip(*component)
                box = min(xs), min(ys), max(xs)+1, max(ys)+1
                pieces.append((box, art.crop(box)))
        drawings[cell] = [image for _, image in sorted(pieces, key=lambda p: p[0][0])]
    # Touching gloves cannot be separated into unseen drawings by a crop.
    # Reuse explicitly compatible separate drawings from the same design.
    reuse = {6: [(3, 0), (3, 1)], 11: [(3, 0), (2, 0)],
             13: [(9, 0), (2, 0)], 15: [(9, 0), (2, 0)]}
    fitted, evidence = {}, []
    for reference in guide['records']:
        cell = reference['cell']; fitted[cell] = []
        references = reuse.get(cell, [(cell, n) for n in range(len(anchors[cell]))])
        for number, (source_cell, source_piece) in enumerate(references):
            image = drawings[source_cell][source_piece]
            scale = min(1, 8/image.width, 9/image.height)
            image = image.resize((max(1, round(image.width*scale)), max(1, round(image.height*scale))), Image.Resampling.NEAREST)
            path = base/'parts-source/glove-poses'/f'{cell:02d}-{number}.png'
            path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(assets.png_bytes(image))
            fitted[cell].append({'file': path.relative_to(base).as_posix(), 'sha256': assets.sha256(path.read_bytes())})
            evidence.append({'reference_cell': cell, 'source_cell': source_cell, 'source_piece': source_piece,
                'reference_palm': anchors[cell][number], 'source_pose': reference['sample_id'],
                'image': fitted[cell][number]})
    mapping = {}
    for clip_id, samples in observations.items():
        state = clip_id.removesuffix('.right')
        references = [row for row in guide['records'] if row['state'] == state]
        features = {}
        for reference in references:
            sample_number = int(reference['sample_id'].split('.')[1])
            image = Image.open(base/'motion-source'/state/f'{sample_number:03d}.png').convert('RGBA')
            point = samples[reference['sample_id']]['points']['upper']
            feature = Image.fromarray(arm_coverage(image)[1].astype(np.uint8)*255)
            features[reference['cell']] = np.asarray(feature.crop((point[0]-36, point[1]+5, point[0]+36, point[1]+27))) > 0
        mapping[clip_id] = {}
        for sample_id, row in samples.items():
            number = int(sample_id.split('.')[1]); image = Image.open(base/'motion-source'/state/f'{number:03d}.png').convert('RGBA')
            point = row['points']['upper']; feature = Image.fromarray(arm_coverage(image)[1].astype(np.uint8)*255)
            current = np.asarray(feature.crop((point[0]-36, point[1]+5, point[0]+36, point[1]+27))) > 0
            cell = min(features, key=lambda c: (int(np.count_nonzero(current != features[c])), c))
            reference = next(item for item in references if item['cell'] == cell)
            reference_row = samples[reference['sample_id']]
            delta = [a-b for a, b in zip(point, reference_row['points']['upper'])]
            palms = []
            for palm in anchors[cell]:
                position = [a+b for a, b in zip(palm, delta)]
                original_candidates = reference_row['hands']
                original = min(original_candidates, key=lambda p: sum((a-b)**2 for a, b in zip(p, palm)), default=None)
                candidates = row['hands']
                detected = min(candidates, key=lambda p: sum((a-b)**2 for a, b in zip(p, position)), default=None)
                if (original is not None and detected is not None
                        and sum((a-b)**2 for a, b in zip(original, palm)) <= 36
                        and sum((a-b)**2 for a, b in zip(detected, position)) <= 36):
                    position = [a+b-c for a, b, c in zip(detected, palm, original)]
                palms.append(position)
            mapping[clip_id][sample_id] = {'cell': cell, 'palms': palms}
    write(base/'glove-pose-fitting.json', {'kind': 'sd-glove-pose-fitting', 'schema_version': 1,
        'source_sha256': assets.sha256(source.read_bytes()), 'drawings': evidence, 'mapping': mapping,
        'confidence': 'calibrated source references with bounded visible-pixel estimates; visual approval remains separate',
        'runtime_transform': 'image selection and translation only'})
    return fitted, mapping


def build(base, source):
    library = base / 'library'
    if library.exists():
        raise ValueError('destination library must be new')
    old_index, old_registry = workspace.read_workspace(source / 'library/assembly.json')
    old_rig = old_registry[old_index['rig']['id']]
    topology = deepcopy(old_registry[old_rig['topology']['id']])
    topology.pop('digest')
    topology['id'] = 'sd.equipment.layers.topology'
    topology['nodes'].extend({'id': node, 'parent': 'root', 'required': False}
                            for node in ('headwear', 'glove_a', 'glove_b', 'back_accessory'))
    topology['slots'].extend({'id': slot, 'nodes': ['head'] if slot == 'headwear' else ['root'],
        'sockets': ['head'] if slot == 'headwear' else ['root'], 'optional': True,
        'empty_policy': 'no-piece'} for slot in EXTRAS)
    topology = contract.seal(topology)
    rig = deepcopy(old_rig)
    rig.pop('digest')
    rig.update(id='sd.equipment.layers.rig', topology=contract.reference(topology),
               default_body_ids=['sd.equipment.body'])
    rig = contract.seal(rig)
    catalog = deepcopy(old_registry[old_index['catalog']['id']])
    catalog.pop('digest')
    catalog.update(id='sd.equipment.layers.catalog', rig=contract.reference(rig), topology=contract.reference(topology))
    for slot in catalog['slots']:
        slot.update(options=[], default=None)
    if not any(group['id'] == 'equipment' for group in catalog['groups']):
        catalog['groups'].append({'id': 'equipment', 'label': '장비'})
    catalog['slots'].extend({'id': slot, 'group': 'equipment', 'label': label, 'options': [], 'default': None}
                          for slot, label in zip(EXTRAS, ('모자', '장갑', '등 장비')))
    catalog = contract.seal(catalog)
    workspace.initialize(library, 'human.sd.v1')
    assembly = library / 'assembly.json'
    index, _ = workspace.read_workspace(assembly)
    index.update(documents={topology['id']: 'profiles/topology.json', rig['id']: 'profiles/rig.json', catalog['id']: 'catalog.json'},
                 rig=contract.reference(rig), catalog=contract.reference(catalog))
    for document, path in ((topology, 'profiles/topology.json'), (rig, 'profiles/rig.json'), (catalog, 'catalog.json')):
        write(library/path, document)
    write(assembly, index)
    shutil.copytree(source/'parts-source', base/'parts-source', dirs_exist_ok=True)
    shutil.copytree(source/'motion-source', base/'motion-source')
    request = workspace.decode_json((source/'motion-import.json').read_bytes())
    request.update(id='sd.equipment.video.motion', rig=index['rig'])
    imported = parts_motion.import_motion(assembly, write(base/'motion-import.json', request), base/'motion-source')
    annotations = workspace.decode_json((library/imported['annotation_template']).read_bytes())
    old_motion = old_registry[old_index['motion']['id']]
    for clip in old_motion['clips']:
        for sample in clip['samples']:
            annotations['clips'][clip['id']][sample['id']]['sockets'] = deepcopy(sample['sockets'])
    parts_motion.freeze_motion(assembly, imported['candidate'], write(base/'attachment-tracks.json', annotations))
    index, registry = workspace.read_workspace(assembly)
    motion = registry[index['motion']['id']]
    assert all(all(old[key] == new[key] for key in ('id', 'state', 'timebase', 'loop', 'samples'))
               for old, new in zip(old_motion['clips'], motion['clips']))
    observations, masks = {}, {}
    for clip in motion['clips']:
        observations[clip['id']], masks[clip['id']] = {}, {}
        for number, sample in enumerate(clip['samples']):
            image = Image.open(base/'motion-source'/clip['state']/f'{number:03d}.png').convert('RGBA')
            coverage, skin = arm_coverage(image)
            path = base/'parts-source/arm-occlusion'/clip['state']/f'{number:03d}.png'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(assets.png_bytes(coverage))
            masks[clip['id']][sample['id']] = {'image': {'file': path.relative_to(base).as_posix(),
                'sha256': assets.sha256(path.read_bytes())}, 'target_node': 'clothing', 'space': 'rig-canvas', 'channel': 'luma'}
            observations[clip['id']][sample['id']] = {
                'hands': visible_hands(skin), 'points': landmarks(image, sample['sockets']['torso']['position'])}
    write(base/'visible-hand-tracks.json', {'kind': 'sd-visible-extremity-tracks', 'schema_version': 1,
        'clips': observations, 'confidence': 'unverified visible-pixel estimates; not anatomical left/right',
        'body_changed': False})
    body = workspace.decode_json((source/'import/body.json').read_bytes())
    body.update(id='sd.equipment.body', binding_id='sd.equipment.body.binding', rig=index['rig'], motion=index['motion'])
    parts_import.import_pack(assembly, write(base/'import/body.json', body), base)
    choices = workspace.decode_json((source/'choices.json').read_bytes())
    for slot, references in choices.items():
        choices[slot] = []
        for reference in references:
            spec = workspace.decode_json((source/'import'/(reference['id']+'.json')).read_bytes())
            spec.update(rig=index['rig'], motion=index['motion'])
            if slot == 'upper':
                for piece in spec['pieces']:
                    for clip_id, samples in piece['sample_map'].items():
                        for sample_id, placement in samples.items():
                            placement['mask'] = masks[clip_id][sample_id]
            choices[slot].append(parts_import.import_pack(assembly, write(base/'import'/(spec['id']+'.json'), spec), base)['part'])
    fitted = fit_art(base)
    glove_images, glove_mapping = fit_glove_poses(base, observations)
    for slot in EXTRAS:
        identity = 'sd.equipment.'+slot
        pieces = []
        for side in ('a', 'b') if slot == 'gloves' else (None,):
            mapping = {}
            for clip in motion['clips']:
                mapping[clip['id']] = {}
                for sample in clip['samples']:
                    row = observations[clip['id']][sample['id']]
                    key = 'backpack' if slot == 'back_accessory' else slot
                    glove_number = ('a', 'b').index(side) if side else None
                    glove = glove_mapping[clip['id']][sample['id']] if side else None
                    glove_present = side and glove_number < len(glove['palms'])
                    image_ref = (glove_images[glove['cell']][glove_number] if glove_present else glove_images[0][0]) if side else fitted[key]
                    placement = {'image': image_ref, 'format': 'local' if side else 'full-canvas',
                                 'pivot': [0, 0], 'target_socket': 'root', 'offset': [0, 0], 'after': ['root']}
                    if slot == 'headwear':
                        placement.update(pivot=[64, 30], target_socket='head', after=['root', 'hair'])
                    elif slot == 'back_accessory':
                        placement.update(offset=[a-b for a, b in zip(row['points']['upper'], (64, 69))],
                                         before=['root'], after=[])
                    else:
                        if not glove_present:
                            placement['visible'] = False
                        else:
                            width, height = Image.open(base/image_ref['file']).size
                            placement.update(offset=[a-b for a, b in zip(glove['palms'][glove_number], (width//2, height//2))],
                                             after=['root', 'clothing'])
                    mapping[clip['id']][sample['id']] = placement
            pieces.append({'id': side or slot, 'slot': slot, 'node': 'head' if slot == 'headwear' else 'root',
                'provides': ['glove_'+side if side else slot], 'replaces': [], 'mode': 'raster' if side else 'held', 'sample_map': mapping})
        spec = {'kind': 'parts-pack-import', 'schema_version': 1, 'id': identity, 'binding_id': identity+'.binding',
            'design_id': 'explorer.'+slot, 'role': 'part', 'style': rig['style'], 'rig': index['rig'], 'motion': index['motion'],
            'view': rig['view'], 'occupies': [slot], 'pieces': pieces}
        choices[slot] = [None, parts_import.import_pack(assembly, write(base/'import'/(identity+'.json'), spec), base)['part']]
    selection = {'kind': 'parts-appearance-selection', 'schema_version': 1, 'id': 'sd.equipment.layers',
                 'selections': {slot: options[0] for slot, options in choices.items()}}
    parts_appearance.create(assembly, write(base/'selection.json', selection))
    write(base/'choices.json', choices)
    write(base/'body-motion-origin.json', {'kind': 'sd-equipment-body-provenance', 'schema_version': 1,
        'previous_motion': old_index['motion'], 'motion': index['motion'], 'body_frames_and_timing_unchanged': True,
        'body_split': False, 'body_replacements': 0, 'runtime_texture_warps': 0, 'appearance_clips_baked': 0,
        'occlusion_target': 'clothing only; intact root body remains unmasked', 'art_approved': False, 'release_ready': False})
    return assembly


def check(base):
    assembly = base/'library/assembly.json'
    index, registry = workspace.read_workspace(assembly)
    motion = registry[index['motion']['id']]
    choices = workspace.decode_json((base/'choices.json').read_bytes())
    p = PartsPlayer.from_workspace(assembly)
    saved = {f.relative_to(assembly.parent).as_posix(): assets.sha256(f.read_bytes())
             for f in assembly.parent.rglob('*') if f.is_file()}
    evaluations, hashes = 0, {state: set() for state in index['states']}
    sheet = Image.new('RGBA', (8*256, 3*272), (240, 240, 235, 255))
    captions = ImageDraw.Draw(sheet)
    pair_coverage, risk_triples = set(), set()
    names = list(choices)
    for number, references in enumerate(product(*choices.values())):
        selected = dict(zip(choices, references))
        p.seek(Fraction(241, 240)); clock = p.current()[2]
        p.set_selection(selected)
        assert p.current()[2] == clock
        for a, b in product(range(len(names)), repeat=2):
            if a < b:
                pair_coverage.add((names[a], references[a]['id'] if references[a] else None,
                                   names[b], references[b]['id'] if references[b] else None))
        for triple in (('hair', 'headwear', 'expression'), ('upper', 'gloves', 'back_accessory'),
                       ('lower', 'footwear', 'gloves')):
            risk_triples.add(tuple((s, selected[s]['id'] if selected[s] else None) for s in triple))
        for state_index, clip in enumerate(motion['clips']):
            p.set_state(clip['state'])
            for sample_index, sample in enumerate(clip['samples']):
                p.seek(Fraction(sample_index, 24))
                scene = p.layers(interpolate=False)
                body = next(layer for layer in scene['layers'] if layer['slot'] is None)
                assert body['position'] == ['0', '0'] and body['image']['sha256'] == sample['image']['sha256']
                assert all(mask['target_node'] == 'clothing' for mask in scene['masks'])
                if sample_index == 0:
                    image, evidence = p.frame()
                    assert all(d['clipped_pixels'] == 0 for d in evidence['evidence']['draw'])
                    hashes[clip['state']].add(assets.sha256(image.tobytes()))
                    if all(selected[s] == choices[s][0] for s in choices if s not in EXTRAS):
                        bits = sum(int(selected[s] is not None)*(1 << i) for i, s in enumerate(EXTRAS))
                        x, y = bits*256, state_index*272
                        sheet.alpha_composite(image.resize((256, 256), Image.Resampling.NEAREST), (x, y+16))
                        captions.text((x+4, y), f'{clip["state"]} equipment {bits:03b}', fill=(20, 20, 20, 255))
                evaluations += 1
        if number % 32 == 0:
            print(f'checked {number+1}/256 appearances', flush=True)
    p.set_selection({slot: None for slot in choices})
    assert len(p.layers()['layers']) == 1
    assert saved == {f.relative_to(assembly.parent).as_posix(): assets.sha256(f.read_bytes())
                     for f in assembly.parent.rglob('*') if f.is_file()}
    sheet.save(base/'equipment-combinations-review.png')
    result = {'appearances': 256, 'base_appearances': 32, 'shared_motion_clips': 3, 'state_cases': 768,
        'sample_evaluations': evaluations, 'all_selection_pairs': len(pair_coverage), 'risk_triples': len(risk_triples),
        'distinct_rasters_at_sample_zero': {state: len(values) for state, values in hashes.items()},
        'whole_body_preserved': True, 'swap_clock_preserved': True, 'optional_empty_verified': True,
        'appearance_clips_baked': 0, 'runtime_texture_warps': 0, 'art_approved': False, 'release_ready': False,
        'coverage_scope': 'all scene samples; CPU raster bounds at sample zero only; visual review separate'}
    write(base/'validation.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True, type=Path)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if not args.check:
        if args.source is None:
            parser.error('--source is required for a new workspace')
        build(args.base, args.source)
    print(json.dumps(check(args.base), indent=2))
