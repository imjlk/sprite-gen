# SPDX-License-Identifier: Apache-2.0
"""Replace a legacy rectangle with an observed native shoe-opening mask.

Artwork stays pixel-identical. The explicit opening annotation belongs to one
native source and is registered by the same uniform matrix as that artwork.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from scripts.dev.parts_full_motion import foot_matrix, similarity
from scripts.dev.parts_full_motion_rebind import rebind
from sprite_gen.curate import parts_motion
from sprite_gen.frames import parts_production as p
from sprite_gen.spec import parts_assets as a, parts_workspace as w


def remask(assembly, annotation, revision, defer_adoption=False):
    root, _, _, _ = parts_motion._active(assembly)
    observed=json.loads(annotation.read_text())
    source=root/observed['native_source']
    assert a.sha256(source.read_bytes())==observed['native_sha256'], 'opening belongs to another shoe'
    native=Image.open(source)
    assert list(native.size)==observed['native_size'], 'opening canvas differs'
    folder=root/'inputs'/revision
    if folder.exists(): raise ValueError('preserve previous mask revisions')
    folder.mkdir()
    canvas=Image.new('L',native.size)
    ImageDraw.Draw(canvas).polygon([tuple(x) for x in observed['opening_polygon']],fill=255)
    opening=canvas.filter(ImageFilter.GaussianBlur(1.5))
    opening.save(folder/'native-opening.png')
    native_rgba=Image.new('RGBA',native.size,'white');native_rgba.putalpha(opening)
    source_matrix=np.array(observed['native_to_canonical'])
    geometry=w.read_json(root,'inputs/geometry.json')['poses']
    state=p._load(root,'full-motion.sneakers');config=deepcopy(state['config']);proof=[]
    for pose,points in geometry.items():
        for side in ('back','front'):
            matrix=foot_matrix(points[side])
            registered=np.array(similarity(native_rgba,matrix@source_matrix).getchannel('A'))
            leg=np.array(Image.open(root/f'inputs/body-segments/leg_{side}/{pose}.png').getchannel('A'))
            foot=np.array(Image.open(root/f'inputs/body-segments/foot_{side}/{pose}.png').getchannel('A'))
            anatomy=np.maximum(leg,foot)
            keep=255-np.rint(anatomy.astype(float)*registered/255).astype('uint8')
            file=folder/f'{pose}.{side}.keep.png';Image.fromarray(keep).save(file)
            layer=next(x for x in config['independent']['layers'] if x['id']=='shoe_'+side)
            layer['masks'][pose]={'target_node':'foot_'+side,'image':{'file':file.relative_to(root).as_posix(),'sha256':a.sha256(file.read_bytes())},'space':'rig-canvas','channel':'luma'}
            entry=matrix@source_matrix@np.array([*observed['native_entry'],1])
            layer['landmarks'][pose]['shoe_entry']=np.rint(entry[:2]).astype(int).tolist()
            proof.append({'pose':pose,'side':side,'mask_sha256':a.sha256(file.read_bytes()),'actual_entry':entry[:2].tolist(),'source_ankle':points[side]['ankle'],'mask_basis':'observed native opening intersected with own source anatomy'})
    file=folder/'config.json';file.write_bytes(w.json_bytes(config));p.configure(assembly,'full-motion.sneakers',file)
    (folder/'annotation.json').write_bytes(annotation.read_bytes())
    (folder/'authoring.json').write_bytes(w.json_bytes({'source':observed,'poses':proof,'art_approved':False,'image_generation_calls':0}))
    if defer_adoption:return {'mask_interfaces':len(proof),'adoption':'pending','art_approved':False}
    return rebind(assembly,revision+'-adoption',parts=['sneakers'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assembly',type=Path,required=True)
    parser.add_argument('--annotation',type=Path,required=True)
    parser.add_argument('--revision',required=True)
    parser.add_argument('--defer-adoption',action='store_true')
    remask(**vars(parser.parse_args()))
