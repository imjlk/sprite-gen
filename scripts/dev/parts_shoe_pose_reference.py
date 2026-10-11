# SPDX-License-Identifier: Apache-2.0
"""Author a shoe pose reference from explicit source/target heel and toe points.

This writes construction evidence only. It neither changes the motion nor
publishes a produced part. A native pose-specific drawing still needs review.
"""

import argparse
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image
from sprite_gen.curate import parts_motion
from sprite_gen.frames import parts_production as p
from sprite_gen.frames.parts_independent import reference_view
from sprite_gen.spec import parts_assets as assets, parts_workspace as w


def point(value):
    values = [float(x) for x in value.split(',')]
    if len(values) != 2 or not all(math.isfinite(x) for x in values):
        raise argparse.ArgumentTypeError('a point needs finite x,y coordinates')
    return values


def prepare(assembly, part, source_pose, target_pose, layer, source_heel,
            source_toe, target_heel, target_toe, out, source_collar=None, target_collar=None):
    if out.exists():
        raise ValueError('use a new output directory; existing reference art is preserved')
    root, index, registry, _ = parts_motion._active(assembly)
    state = p._load(root, part)
    row = state['tracks']['main']['poses'][source_pose]
    record = p.candidate_record(root, state, source_pose, row['selected'])
    ref = record['images']['piece.' + layer]
    source_bytes = assets.SourceReader(root / row['selected']).read(ref['file'], ref['sha256'])
    from io import BytesIO
    source = Image.open(BytesIO(source_bytes)).convert('RGBA')
    body, _ = p._render_alone(root, index, registry, None, p._states(state, index))
    body = body[target_pose]
    h, t, H, T = map(np.array, (source_heel, source_toe, target_heel, target_toe))
    a, b = t - h, T - H
    if min(np.linalg.norm(a), np.linalg.norm(b)) < 1:
        raise ValueError('heel and toe must define a nonzero axis')
    scale = float(np.linalg.norm(b) / np.linalg.norm(a))
    angle = math.atan2(b[1], b[0]) - math.atan2(a[1], a[0])
    matrix = scale * np.array([[math.cos(angle), -math.sin(angle)],
                               [math.sin(angle), math.cos(angle)]])
    shift = H - matrix @ h
    if (source_collar is None) != (target_collar is None):
        raise ValueError('provide both collar witness points, or neither')
    collar = None
    if source_collar is not None:
        predicted = matrix @ np.array(source_collar) + shift
        collar = {'source': source_collar, 'target': target_collar,
                  'projected': predicted.tolist(),
                  'residual_pixels': float(np.linalg.norm(predicted-np.array(target_collar))),
                  'status': 'curator must inspect; not an approval metric'}
    inverse = np.linalg.inv(matrix)
    reverse_shift = -inverse @ shift
    coefficients = (*inverse[0], reverse_shift[0], *inverse[1], reverse_shift[1])
    image = source.convert('RGBa').transform(body.size, Image.Transform.AFFINE,
                                            coefficients, Image.Resampling.BILINEAR).convert('RGBA')
    if not image.getbbox():
        raise ValueError('the projected reference is outside the body canvas')
    assert np.allclose(matrix @ h + shift, H) and np.allclose(matrix @ t + shift, T)
    out.mkdir(parents=True)
    image.save(out / 'reference-full-canvas.png')
    bounds = image.getbbox()
    view = reference_view(image.crop(bounds))
    factor = min(12, 1280 / max(view.size))
    view.resize((round(view.width * factor), round(view.height * factor)),
                Image.Resampling.BILINEAR).save(out / 'reference-view.png')
    actual = body.copy()
    actual.alpha_composite(image)
    roi = (max(0, bounds[0]-32), max(0, bounds[1]-32),
           min(body.width, bounds[2]+48), min(body.height, bounds[3]+32))
    proof = reference_view(actual).crop(roi)
    factor = min(6, 1280 / max(proof.size))
    proof.resize((round(proof.width*factor), round(proof.height*factor)),
                 Image.Resampling.BILINEAR).save(out / 'on-body-view.png')
    metadata = {
        'status': 'author-reference-only', 'source_candidate': row['selected'],
        'source_rgba': ref, 'source_pose': source_pose, 'target_pose': target_pose,
        'layer': layer, 'rig': index['rig'], 'motion': index['motion'], 'body': index['body'],
        'source_points': {'heel': source_heel, 'toe': source_toe},
        'target_points': {'heel': target_heel, 'toe': target_toe},
        'rotation_clockwise_degrees': math.degrees(angle), 'uniform_scale': scale,
        'reference_bounds': list(bounds), 'source_overwrite': False,
        'collar_witness': collar,
        'runtime_rigging': False, 'appearance_clip_generated': False, 'art_approved': False,
        'limitation': 'two points do not prove collar entry, viewing plane or body coverage',
    }
    (out / 'reference.json').write_bytes(w.json_bytes(metadata))
    return metadata


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assembly', type=Path, required=True)
    parser.add_argument('--part', required=True)
    parser.add_argument('--source-pose', required=True)
    parser.add_argument('--target-pose', required=True)
    parser.add_argument('--layer', required=True)
    for field in ('source-heel', 'source-toe', 'target-heel', 'target-toe'):
        parser.add_argument('--'+field, type=point, required=True)
    parser.add_argument('--source-collar', type=point)
    parser.add_argument('--target-collar', type=point)
    parser.add_argument('--out', type=Path, required=True)
    print(json.dumps(prepare(**vars(parser.parse_args()))))
