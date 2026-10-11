# SPDX-License-Identifier: Apache-2.0
"""Author a footwear replacement pilot from a native master and explicit geometry.

The raster edits are uniform rotation/scale/translation during asset authoring,
not runtime rigging. Foot/leg ownership changes preserve every original body
RGBA pixel. This script prepares candidates and criteria; it never approves art.
"""

import argparse
from copy import deepcopy
import math
from pathlib import Path
import shutil

import numpy as np
from PIL import Image, ImageFilter
from sprite_gen.curate import parts_motion
from sprite_gen.frames import parts_import, parts_production as p
from sprite_gen.spec import parts_assets as a, parts_workspace as w


def author(out, master, geometry, revision):
    assembly = out / 'library/assembly.json'
    root, index, registry, _ = parts_motion._active(assembly)
    params = w.decode_json(geometry.read_bytes())
    source = root / params['body_source']
    destination = root / 'inputs' / ('body-pieces-' + revision)
    folder = root / 'inputs' / ('shoe-replacement-' + revision)
    if destination.exists() or folder.exists():
        raise ValueError('use a fresh revision; existing art and evidence are preserved')
    folder.mkdir(parents=True)
    shutil.copy2(master, folder / 'native-master.png')
    shutil.copy2(geometry, folder / 'geometry.json')
    original = Image.open(master).convert('RGBA')
    spec = deepcopy(w.read_json(source, 'import.json'))
    config = deepcopy(p._load(root, 'qualified.sneakers')['config'])
    config['prompt'] = {'text': 'Independent low-top navy canvas sneakers with a rigid thin cream outsole, one navy welt stripe, cream toe cap and laces. Foot nodes are replaced when worn. Match the retained ankle interface; do not reproduce flexible bare-foot curves in the sole.',
                        'refs': [(folder / 'native-master.png').relative_to(root).as_posix()]}
    updates, transfers, criteria = {}, [], {}
    center = np.array(params['source_collar'], dtype=float)
    for layer in config['independent']['layers']:
        foot = 'foot_back' if layer['id'] == 'shoe_back' else 'foot_front'
        leg = 'leg_back' if layer['id'] == 'shoe_back' else 'leg_front'
        layer.update(replaces=[foot], body_nodes=[foot, leg], guides={}, landmarks={}, source_bounds={}, straight_edges={})
        for pose, values in params['layers'][layer['id']].items():
            angle = math.radians(values['angle'])
            scale = values['scale']
            matrix = scale * np.array([[math.cos(angle), -math.sin(angle)], [math.sin(angle), math.cos(angle)]])
            shift = np.array(values['collar']) - matrix @ center
            inverse = np.linalg.inv(matrix)
            reverse = -inverse @ shift
            # Pillow affine minification alone point-samples fine stitches.
            # Prefilter the native source, project at 4x, then reduce premultiplied
            # alpha once so the asset matches the body's smooth illustration.
            filtered = original.convert('RGBa').resize((original.width//2, original.height//2), Image.Resampling.LANCZOS)
            raster = filtered.transform((3072, 3072), Image.Transform.AFFINE,
                (*(inverse[0]/8), reverse[0]/2, *(inverse[1]/8), reverse[1]/2), Image.Resampling.BILINEAR)
            raster = raster.resize((768,768), Image.Resampling.LANCZOS).convert('RGBA')
            path = folder / f'{pose}.{layer["id"]}.png'
            raster.save(path)
            # Output references have native coordinates; the import must not
            # rescale a corrected independent asset a second time.
            py, px = np.nonzero(np.asarray(raster)[...,3] >= 16)
            # Match the existing registration crop (four native pixels of
            # padding) exactly: scale=1 and the authored position is retained.
            layer['targets'][pose] = [max(0,int(px.min())-4), max(0,int(py.min())-4),
                min(768,int(px.max())+5), min(768,int(py.max())+5)]
            layer['source_bounds'][pose] = [0, 0, 768, 768]
            layer['guides'][pose] = [path.relative_to(root).as_posix()]
            start, end = [matrix @ np.array(point) + shift for point in params['source_sole_axis']]
            start, end = [np.rint(point).astype(int).tolist() for point in (start, end)]
            layer['straight_edges'][pose] = [{'id': 'outsole', 'start': start, 'end': end,
                'side': 'positive', 'span': ['3/20', '4/5'], 'max_deviation': '2'}]
            layer['landmarks'][pose] = {'ankle': values['collar'], 'heel': start, 'toe': end}
            leg_pixels = np.array(Image.open(source / leg / f'{pose}.png').convert('RGBA'))
            foot_pixels = np.array(Image.open(source / foot / f'{pose}.png').convert('RGBA'))
            assert not np.any((leg_pixels[..., 3] > 0) & (foot_pixels[..., 3] > 0)), 'ownership must be exclusive'
            union = np.where((foot_pixels[..., 3] > 0)[..., None], foot_pixels, leg_pixels)
            yy, xx = np.mgrid[:768, :768]
            coordinates = np.stack([xx, yy], axis=-1)
            source_y = (coordinates @ inverse[1]) + reverse[1]
            source_x = (coordinates @ inverse[0]) + reverse[0]
            distance = (source_y - params['source_cut_y']) * scale
            # A single tilted plane can incorrectly keep upward-pointing toes
            # as leg pixels. Include distal toes above that plane while keeping
            # the proximal calf outside this distal authoring zone.
            right, top = params['source_distal_cut']
            # Delete distal toes above the tilted plane, but do not trim the
            # calf on the heel side to imitate a rectangular collar corridor.
            is_foot = (distance > 0) | ((source_y > top) & (source_x > right))
            x1,y1,x2,y2 = values['body_region']
            local = (xx >= x1) & (xx < x2) & (yy >= y1) & (yy < y2)
            is_foot = np.where(local, is_foot, foot_pixels[...,3] > 0)
            new_foot = np.where(is_foot[..., None], union, 0).astype('uint8')
            new_leg = np.where((~is_foot)[..., None], union, 0).astype('uint8')
            assert np.array_equal(np.where((new_foot[..., 3] > 0)[..., None], new_foot, new_leg), union)
            updates[foot, pose], updates[leg, pose] = Image.fromarray(new_foot), Image.fromarray(new_leg)
            transfers.append({'pose': pose, 'foot': foot, 'leg': leg,
                'changed_ownership_pixels': int(((new_foot[..., 3] > 0) != (foot_pixels[..., 3] > 0)).sum()),
                'body_pixels_changed': 0})
            masks = criteria.setdefault(pose, {'required': np.zeros((768,768), bool),
                'allowed': np.zeros((768,768), bool), 'protected': np.zeros((768,768), bool)})
            # The covered cut seam belongs to the retained ankle, rather than
            # all deleted flexible toes/heel. Requiring the latter deforms soles.
            seam = Image.fromarray((new_foot[...,3] >= 16).astype('uint8')*255).filter(ImageFilter.MaxFilter(5))
            masks['required'] |= (new_leg[...,3] >= 16) & (np.asarray(seam) >= 16)
            masks['allowed'] |= np.asarray(raster.getchannel('A').filter(ImageFilter.MaxFilter(9))) >= 16
            # The tongue legitimately rises above the collar. Protect the
            # proximal calf beyond that authored product envelope instead.
            masks['protected'] |= (new_leg[...,3] >= 16) & (source_y < params['source_guard_y'])
    for piece in spec['pieces']:
        node = piece['node']
        for pose, placement in piece['pose_map'].items():
            image = updates.get((node, pose))
            if image is None:
                image = Image.open(source / placement['image']['file']).convert('RGBA')
            file = destination / node / f'{pose}.png'
            file.parent.mkdir(parents=True, exist_ok=True)
            image.save(file)
            placement['image'] = {'file': file.relative_to(destination).as_posix(), 'sha256': a.sha256(file.read_bytes())}
    (destination / 'import.json').write_bytes(w.json_bytes(spec))
    parts_import.import_pack(assembly, destination / 'import.json', destination)
    root, index, registry, _ = parts_motion._active(assembly)
    frames, _ = p._render_alone(root, index, registry, None, ['run'])
    for n in range(4):
        assert np.array_equal(np.asarray(frames[f'run.{n}']), np.asarray(Image.open(root / f'inputs/body/{n:03}.png').convert('RGBA')))
    for pose, masks in criteria.items():
        config['pose_checks'][pose] = {'occluded': None}
        for name, values in masks.items():
            file = folder / f'{pose}.{name}.png'
            Image.fromarray(values.astype('uint8') * 255).save(file)
            config['pose_checks'][pose][name] = file.relative_to(root).as_posix()
    (folder / 'config.json').write_bytes(w.json_bytes(config))
    proof = {'status': 'unreviewed-authoring', 'master_sha256': a.sha256(master.read_bytes()),
        'geometry_sha256': a.sha256(geometry.read_bytes()), 'body_pixel_identical': 4,
        'body': index['body'], 'motion': index['motion'], 'ownership_transfers': transfers,
        'runtime_masks': 0, 'runtime_rigging': False, 'appearance_clips_generated': 0,
        'source_overwrite': False, 'art_approved': False,
        'criteria_scope': 'retained ankle cut seam, permitted shoe footprint and explicitly rigid outsole edge; does not prove art quality'}
    (folder / 'authoring.json').write_bytes(w.json_bytes(proof))
    return {'config': str(folder / 'config.json'), **proof}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--master', type=Path, required=True)
    parser.add_argument('--geometry', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    print(author(**vars(parser.parse_args())))
