# SPDX-License-Identifier: Apache-2.0
"""Register actual AI-drawn pose art; never synthesize motion from a standing shoe.

The model's framing is not trusted. Each independently drawn shoe is assigned
an explicit, reviewed bounding region on a frozen body ROI. Only rigid uniform
registration is used. Original art, pose guides and all transforms are retained.
This experiment does not approve art or infer body ownership from ankle vectors.
"""
from pathlib import Path
import argparse
import json
import hashlib
import shutil
from copy import deepcopy
import numpy as np
from PIL import Image
from sprite_gen.frames.parts_segments import _components, nearest_fill
from sprite_gen.frames import parts_import, parts_production as production
from sprite_gen.spec import parts_workspace as workspace
from sprite_gen.curate import parts_appearance


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def register(out):
    spec = json.loads((out / 'registration.json').read_text())
    geo = json.loads((out / 'guides/geometry.json').read_text())
    (out / 'registered').mkdir(exist_ok=True)
    report = {'provider': 'codex-native-imagegen', 'motion_reference': geo,
              'art_approved': False, 'mask_rendering': False,
              'standing_art_warped': False, 'poses': {}}
    for pose, row in spec.items():
        src = out / row['raw']
        im = Image.open(src).convert('RGBA')
        alpha = np.asarray(im)[..., 3]
        comps = sorted((c for c in _components(alpha > 16) if c.sum() > 100),
                       key=lambda c: np.nonzero(c)[0].mean())
        if len(comps) != 2:
            raise ValueError(f'{pose}: need two separate drawings, found {len(comps)}')
        labels = np.full(alpha.shape, -1, dtype=np.int16)
        for i, comp in enumerate(comps):
            labels[comp] = i
        labels = nearest_fill(labels, alpha > 0, rounds=16)
        combined = Image.new('RGBA', (768, 768))
        fit = Image.open(out / geo['poses'][pose]['guide']).convert('RGBA')
        details = []
        for i, placement in enumerate(row['pieces']):
            rgba = np.asarray(im).copy()
            rgba[labels != i] = 0
            piece = Image.fromarray(rgba, 'RGBA')
            box = piece.getbbox()
            x, y, right, bottom = placement['bounds']
            scale = max((right-x)/(box[2]-box[0]), (bottom-y)/(box[3]-box[1]))
            # Preserve aspect and painted perspective: no nonuniform fit,
            # limb-vector scale, mask-clipping or generated body extraction.
            cropped = piece.crop(box).convert('RGBa')
            resized = cropped.resize((round(cropped.width*scale), round(cropped.height*scale)),
                                     Image.Resampling.LANCZOS).convert('RGBA')
            angle = placement.get('angle', 0)
            if angle:
                resized = resized.convert('RGBa').rotate(angle, Image.Resampling.BICUBIC,
                                                         expand=True).convert('RGBA')
            dx = round((x+right-resized.width)/2)
            dy = round((y+bottom-resized.height)/2)
            local = Image.new('RGBA', (1024, 1024))
            local.alpha_composite(resized, (dx, dy))
            fit.alpha_composite(local)
            # Known ROI resolution change, identical for every part and pose.
            small = local.convert('RGBa').resize((512, 512), Image.Resampling.LANCZOS).convert('RGBA')
            full = Image.new('RGBA', (768, 768))
            full.paste(small, (geo['roi'][0], geo['roi'][1]))
            file = out / 'registered' / f"{pose}.{placement['node']}.png"
            full.save(file)
            combined.alpha_composite(full)
            details.append({'node': placement['node'], 'source_bounds': box,
                            'target_bounds': placement['bounds'], 'scale': scale,
                            'rotation_degrees': angle,
                            'translation': [dx, dy], 'file': str(file.relative_to(out)),
                            'sha256': digest(file)})
        combined.save(out / 'registered' / f'{pose}.png')
        fit.save(out / 'registered' / f'{pose}.fit.png')
        body = Path(geo['poses'][pose]['body'])
        if digest(body) != geo['poses'][pose]['body_sha256']:
            raise ValueError(f'{pose}: frozen body changed')
        report['poses'][pose] = {'raw': row['raw'], 'raw_sha256': digest(src),
                                'source_size': im.size, 'pieces': details}
    (out / 'registration-report.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


def publish(out, source, *, refresh=False):
    """Separate experiment workspace; original body/motion/outfit is untouched."""
    root = out / 'library'
    if root.exists() and not refresh:
        raise ValueError('pilot library exists; use its curation tool for further edits')
    if not refresh:
        shutil.copytree(source, root)
    assembly = root / 'assembly.json'
    index, registry = workspace.read_workspace(assembly)
    identity = 'illustration.motion.footwear.ai-pilot'
    material = root / 'inputs/ai-shoes-pilot'
    if refresh:
        revision = digest(out / 'registration-report.json')[:16]
        material = material / 'revisions' / revision
    if material.exists():
        if digest(material/'registration-report.json') != digest(out/'registration-report.json'):
            raise ValueError('existing revision does not match the current registration')
        for pose, row in json.loads((out/'registration-report.json').read_text())['poses'].items():
            if digest(material/row['raw']) != row['raw_sha256']:
                raise ValueError(f'{pose}: immutable raw source changed')
            for piece in row['pieces']:
                if digest(material/piece['file']) != piece['sha256']:
                    raise ValueError(f'{pose}: immutable registered source changed')
    else:
        material.mkdir(parents=True)
        for name in ('raw', 'guides', 'registered'):
            shutil.copytree(out / name, material / name)
        for name in ('registration.json', 'registration-report.json'):
            shutil.copy2(out / name, material / name)
        generation = out / 'generation-latest.json'
        shutil.copy2(generation if generation.exists() else out/'generation-complete.json', material/'generation-complete.json')
    art = json.loads((out / 'registration-report.json').read_text())['poses']
    blank = material / 'blank.png'
    Image.new('RGBA', (768, 768)).save(blank)
    motion = registry[index['motion']['id']]
    rig = registry[index['rig']['id']]
    # Keep every shared sample, with explicit empty drawings outside the 8-pose
    # experiment. These never pass part QA or art approval.
    pieces = []
    for node in ('shoe_back', 'shoe_front'):
        mapping = {}
        for clip in motion['clips']:
            for sample in clip['samples']:
                pose = sample['pose_id']
                file = material / 'registered' / f'{pose}.{node}.png' if pose in art else blank
                mapping[pose] = {'image': {'file': str(file.relative_to(material)), 'sha256': digest(file)},
                                 'format': 'full-canvas', 'pivot': [0, 0],
                                 'target_socket': 'root', 'visible': pose in art}
        pieces.append({'id': node, 'slot': 'footwear', 'node': node, 'mode': 'raster',
                       'pose_map': mapping})
    spec = {'kind': 'parts-pack-import', 'schema_version': 1, 'id': identity,
            'design_id': identity, 'role': 'part', 'style': rig['style'], 'rig': index['rig'],
            'motion': index['motion'], 'view': rig['view'], 'binding_id': identity+'.binding',
            'occupies': ['footwear'], 'states': index['states'], 'pieces': pieces}
    file = material / 'import.json'
    file.write_bytes(workspace.json_bytes(spec))
    imported = parts_import.import_pack(assembly, file, material)
    # Freeze explicitly authored shoe ownership, without changing body labels.
    # At crossings the foreground shoe owns its painted overlap.
    segments = deepcopy(json.loads((source / 'inputs/body-segments/segments.json').read_text()))
    segments['colors'] = {'shoe_back': '#245db1', 'shoe_front': '#ce4339'}
    segments['provides'] = {'shoe_back': ['shoe_back'], 'shoe_front': ['shoe_front']}
    segroot = material / 'ownership'
    for clip in motion['clips']:
        for sample in clip['samples']:
            pose = sample['pose_id']
            labels = np.zeros((768, 768, 4), dtype=np.uint8)
            if pose in art:
                for node, color in [('shoe_back', (36,93,177)), ('shoe_front', (206,67,57))]:
                    alpha = np.asarray(Image.open(material / 'registered' / f'{pose}.{node}.png'))[...,3] > 0
                    labels[alpha] = [*color, 255]
            name = segroot / 'labels' / clip['id'] / f"{sample['id']}.png"
            name.parent.mkdir(parents=True, exist_ok=True)
            Image.fromarray(labels, 'RGBA').save(name)
    (segroot / 'segments.json').write_bytes(workspace.json_bytes(segments))
    config = deepcopy(production._load(root, identity)['config'] if refresh else
                      production._load(source, 'illustration.motion.footwear.sneakers')['config'])
    config.update(part_id=identity, design_id=identity, segments=str((segroot/'segments.json').relative_to(root)),
                  pieces=[{'id': node, 'node': node, 'segments': [node], 'replaces': []}
                          for node in ('shoe_back', 'shoe_front')],
                  pose_checks=config['pose_checks'] if refresh else {}, visibility='zmap')
    config['prompt'] = {'text': 'Independent pose-specific AI sneakers. Missing poses have no art; never approve blanks. Preserve exact body and canonical shoe design.',
                        'refs': [str((material/'guides/design.png').relative_to(root))]}
    cfg = material / 'production-config.json'
    cfg.write_bytes(workspace.json_bytes(config))
    if refresh:
        production.configure(assembly, identity, cfg)
    else:
        production.init(assembly, cfg)
    adopted = production.adopt(assembly, identity, root / imported['file'])
    # The candidate IDs pin the part-only source; the lineage pins the native
    # provider outputs and exact registration separately, without forging a
    # core draw result or claiming a CLI provider call occurred.
    state = production._load(root, identity)
    lineage = {'provider_calls': len(json.loads((material/'generation-complete.json').read_text())['calls']),
               'provider': 'codex-native-imagegen', 'poses': {}, 'not_produced': [],
               'body_before': index['body'], 'motion_before': index['motion'], 'visual_approved': False}
    for pose, row in state['tracks']['main']['poses'].items():
        if pose in art:
            lineage['poses'][pose] = {'candidate': row['selected'], **art[pose]}
        else:
            lineage['not_produced'].append(pose)
    (material/'lineage.json').write_bytes(workspace.json_bytes(lineage))
    checked = production.check(assembly, identity)
    # No body-derived occlusion masks, replacement or shape edits.
    production.build(assembly, identity)
    production.review(assembly, identity, reviewer='pilot-import', reject=lineage['not_produced'],
                      note='미제작 포즈: 투명 미리보기 자리표시자. 신규 독립 신발 생성 필요; 승인 불가.')
    index2, registry2 = workspace.read_workspace(assembly)
    selection = {'kind': 'parts-appearance-selection', 'schema_version': 1,
                 'id': 'illustration.motion.ai-shoes-pilot', 'presets': [], 'palette': {}, 'mirror': False,
                 'selections': {slot['id']: None for slot in registry2[index2['catalog']['id']]['slots']}}
    selection['selections']['footwear'] = {'id': identity, 'digest': registry2[identity]['digest']}
    sel = material / 'appearance.json'
    sel.write_bytes(workspace.json_bytes(selection))
    appearance = parts_appearance.create(assembly, sel)
    final, _ = workspace.read_workspace(assembly)
    assert final['motion'] == index['motion'] and final['body'] == index['body']
    result = {'import': imported, 'adopt': adopted, 'check': checked, 'appearance': appearance,
              'ai_poses': sorted(art), 'unproduced_poses': lineage['not_produced'],
              'motion_unchanged': True, 'body_unchanged': True, 'visual_approved': False}
    (out / ('publication-refresh.json' if refresh else 'publication.json')).write_bytes(workspace.json_bytes(result))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--publish-from', type=Path)
    parser.add_argument('--refresh', action='store_true')
    args = parser.parse_args()
    result = register(args.out.absolute())
    if args.publish_from:
        publication = publish(args.out.absolute(), args.publish_from.absolute(), refresh=args.refresh)
        print(json.dumps({'published': publication['ai_poses'], 'check': publication['check']['summary']}))
    else:
        print(json.dumps({'registered': list(result['poses']), 'art_approved': False}))
