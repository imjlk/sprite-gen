# SPDX-License-Identifier: Apache-2.0
"""Replay the recorded common-body RGB repair on its exact source sequence.

This is a source-specific authoring patch, not a general color detector. The
original engine frames, alpha and timing remain available for comparison.
"""
import argparse
import json
from pathlib import Path
import shutil

import numpy as np
from PIL import Image
from sprite_gen.spec.parts_assets import sha256


def replay(source, patch, out):
    if out.exists():
        raise ValueError('preserve prior registered sequences; use a fresh output')
    proof = json.loads((patch/'body-colour-repair.json').read_text())
    data_file = patch/'body-rgb-patch.npz'
    manifest = json.loads((patch/'body-rgb-patch.json').read_text())
    assert sha256(data_file.read_bytes()) == manifest['patch_sha256']
    shutil.copytree(source, out)
    with np.load(data_file, allow_pickle=False) as data:
        for row in proof['poses']:
            state, n = row['pose'].split('.')
            file = out/state/f'{int(n):03}.png'
            assert sha256(file.read_bytes()) == row['original_sha256'], row['pose']
            image = np.array(Image.open(file).convert('RGBA'))
            alpha = image[..., 3].copy()
            flat = image.reshape((-1, 4))
            flat[data[row['pose']+'.indices'], :3] = data[row['pose']+'.rgb']
            assert np.array_equal(image[..., 3], alpha)
            Image.fromarray(image).save(file)
            assert sha256(file.read_bytes()) == row['edited_sha256'], row['pose']
    (out/'body-rgb-patch.json').write_bytes((patch/'body-rgb-patch.json').read_bytes())
    return {'frames': len(proof['poses']), 'alpha_changed': False,
            'source_specific': True, 'art_approved': False}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--patch', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    print(json.dumps(replay(**vars(p.parse_args()))))
