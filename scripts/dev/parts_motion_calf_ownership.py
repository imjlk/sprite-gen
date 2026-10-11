# SPDX-License-Identifier: Apache-2.0
"""Move an explicitly annotated calf contour out of a replaceable foot node.

This edits segmentation ownership, never the common body drawing or a runtime
mask. Rebind independent candidates after publishing the changed body.
"""
import argparse
import json
from pathlib import Path
import shutil

import numpy as np
from PIL import Image
from sprite_gen.frames import parts_import
from sprite_gen.spec import parts_assets as a, parts_workspace as w


def transfer(source, destination, boundary, cut_y=None, right=None, y_range=None):
    source, destination = source.copy(), destination.copy()
    assert source.shape == destination.shape and source.shape[2] == 4
    yy, xx = np.mgrid[:source.shape[0], :source.shape[1]]
    edge = np.interp(yy, [point[1] for point in boundary], [point[0] for point in boundary])
    region = xx >= edge-1
    if cut_y is not None:
        region |= yy >= cut_y
    if right is not None:
        region &= xx <= np.interp(yy, [p[1] for p in right], [p[0] for p in right])
    if y_range is not None:
        region &= (yy >= y_range[0]) & (yy <= y_range[1])
    moved = (source[..., 3] > 0) & region
    assert not np.any(moved & (destination[..., 3] > 0)), 'ownership overlaps'
    destination[moved] = source[moved]
    source[moved] = 0
    return source, destination, moved


def publish(assembly, annotation, revision):
    root = assembly.parent
    q = json.loads(annotation.read_text())
    folder = root/'inputs'/revision
    if folder.exists():
        raise ValueError('preserve the earlier ownership correction')
    folder.mkdir()
    specfile = root/'inputs/body-segments/import.json'
    spec = w.read_json(root, specfile.relative_to(root).as_posix())
    source = root/f'inputs/body-segments/{q["from"]}/{q["pose"]}.png'
    destination = root/f'inputs/body-segments/{q["to"]}/{q["pose"]}.png'
    assert a.sha256(source.read_bytes()) == q['source_sha256'], 'annotation belongs to another source'
    assert a.sha256(destination.read_bytes()) == q['destination_sha256']
    before = [np.array(Image.open(p).convert('RGBA')) for p in (source, destination)]
    after = transfer(*before, q['boundary_points'], q.get('cut_y'),
                     q.get('right_boundary_points'), q.get('y_range'))
    for file, pixels in zip((source, destination), after[:2]):
        shutil.copy2(file, folder/(file.parent.name+'.before.png'))
        Image.fromarray(pixels).save(file)
    state, frame = q['pose'].split('.')
    body = np.array(Image.open(root/f'inputs/body/{state}/{int(frame):03}.png').convert('RGBA'))
    merged = Image.new('RGBA', (body.shape[1], body.shape[0]))
    for piece in spec['pieces']:
        ref = piece['pose_map'][q['pose']]['image']
        file = specfile.parent/ref['file']
        ref['sha256'] = a.sha256(file.read_bytes())
        merged.alpha_composite(Image.open(file).convert('RGBA'))
    assert np.array_equal(np.array(merged), body), 'ownership changed the body drawing'
    guardfile = root/f'inputs/body-segments/{q["pose"]}.protected-calf.png'
    prior = np.array(Image.open(guardfile).convert('L')) if guardfile.exists() else np.zeros(body.shape[:2], dtype='uint8')
    guard = np.maximum(prior, np.where(after[2] & (body[..., 3] >= 128), 255, 0).astype('uint8'))
    Image.fromarray(guard).save(guardfile)
    specfile.write_bytes(w.json_bytes(spec))
    parts_import.import_pack(assembly, specfile, specfile.parent)
    result = {**q, 'moved_pixels': int(after[2].sum()), 'body_rgba_identical': True,
              'protected_calf_sha256': a.sha256(guardfile.read_bytes()),
              'protected_opaque_calf_pixels': int((guard >= 128).sum()),
              'art_approved': False, 'independent_candidates_need_rebinding': True}
    (folder/'result.json').write_bytes(w.json_bytes(result))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assembly', type=Path, required=True)
    parser.add_argument('--annotation', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    print(json.dumps(publish(**vars(parser.parse_args()))))
