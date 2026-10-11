# SPDX-License-Identifier: Apache-2.0
"""Reject visible foot endpoints outside their exact source body silhouette.

Distance is a necessary registration check, not anatomical identity approval.
Hidden points are explicitly excluded, rather than converted into passes.
"""
import argparse
import json
from pathlib import Path
import numpy as np
from PIL import Image
from sprite_gen.spec import parts_assets as a, parts_workspace as w


def inspect(root, geometry, maximum=8):
    rows = []
    for pose, q in w.read_json(geometry.parent, geometry.name)['poses'].items():
        state, n = pose.split('.')
        file = root/f'inputs/body/{state}/{int(n):03}.png'
        assert a.sha256(file.read_bytes()) == q['body_sha256'], pose
        pixels = np.array(Image.open(file).convert('RGBA'))
        visible = np.argwhere(pixels[..., 3] >= 128)[:, ::-1]
        for side in ('front', 'back'):
            for key in ('heel', 'toe'):
                if q[side].get('foot_fully_occluded') or q[side].get(key+'_visible') is False:
                    rows.append({'pose': pose, 'side': side, 'point': key, 'status': 'hidden'})
                    continue
                point = np.array(q[side][key])
                distance = float(np.sqrt(((visible-point)**2).sum(1).min()))
                rows.append({'pose': pose, 'side': side, 'point': key,
                             'source_sha256': a.sha256(file.read_bytes()),
                             'distance_px': round(distance, 3),
                             'status': 'rework' if distance > maximum else 'within-bound'})
    return {'points': rows, 'maximum_px': maximum,
            'failed': [r for r in rows if r['status'] == 'rework'],
            'art_approved': False, 'anatomical_identity_approved': False}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--geometry', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--maximum', type=float, default=8)
    args = vars(p.parse_args());out = args.pop('out')
    result = inspect(**args);out.write_bytes(w.json_bytes(result))
    print(json.dumps({'points': len(result['points']), 'failed': result['failed']}))
