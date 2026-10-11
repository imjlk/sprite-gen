# SPDX-License-Identifier: Apache-2.0
"""Author fitted sock reference rasters on the qualified four-pose body.

The body ownership/alpha and explicit ankle geometry define a tight garment's
construction guides. RGB is new cloth, sampled from an AI material reference;
body colour is never copied. These are procedural curation drafts, not native
AI pose generation or approved art. No runtime mask or cloth rig is involved.
"""

import argparse
from collections import deque
from copy import deepcopy
from pathlib import Path
import shutil

import numpy as np
from PIL import Image, ImageFilter
from sprite_gen.curate import parts_motion
from sprite_gen.frames import parts_production as p
from sprite_gen.spec import parts_assets as a, parts_workspace as w

FOOTWEAR_TAG = 'footwear.closed-foot'


def attached_cuff(alpha, foot_alpha):
    """Keep distal calf components touching the semantic foot, including AA.

    A folded thigh can cross the cuff's projection plane. It is not hosiery;
    its disconnected raster component must not survive the anatomical crop.
    """
    neighbours=np.asarray(Image.fromarray(foot_alpha.astype('uint8')).filter(ImageFilter.MaxFilter(3)))>=16
    remaining=set(zip(*np.nonzero(alpha>=16)));kept=np.zeros(alpha.shape,dtype='uint8');dropped=0
    while remaining:
        first=remaining.pop();component=[first];queue=deque([first]);touches=bool(neighbours[first])
        while queue:
            y,x=queue.popleft()
            for dy in (-1,0,1):
                for dx in (-1,0,1):
                    point=(y+dy,x+dx)
                    if point in remaining:
                        remaining.remove(point);component.append(point);queue.append(point)
                        touches |= bool(neighbours[point])
        if touches:
            for point in component: kept[point]=255
        else: dropped+=len(component)
    assert kept.any(),'no cuff attached to the semantic foot'
    keep=np.asarray(Image.fromarray(kept).filter(ImageFilter.MaxFilter(3)))>0
    return np.where(keep,alpha,0).astype('uint8'),dropped


def author(out, material, *, revision='socks-r4', length_px=32):
    assembly = out / 'library/assembly.json'
    root, index, registry, _ = parts_motion._active(assembly)
    directory = root / 'inputs' / revision
    if directory.exists():
        raise ValueError('use a fresh revision; existing art and criteria are retained')
    directory.mkdir(parents=True)
    shutil.copy2(material, directory / 'native-material.png')
    source = root / 'inputs/body-pieces-r12'
    geometry = w.read_json(root, 'inputs/shoe-replacement-r12/geometry.json')
    cloth = np.asarray(Image.open(material).convert('RGB').filter(ImageFilter.GaussianBlur(3)).resize((256,256),Image.Resampling.LANCZOS)).astype(float)
    mean = cloth.mean(axis=(0,1))
    yy, xx = np.mgrid[:768,:768]
    coords = np.stack([xx,yy],axis=-1)
    layers, rasters, criteria, measured = [], {}, {}, []
    for side in ('back','front'):
        cuff = {'id':f'sock_{side}_cuff','node':f'sock_{side}','targets':{},'source_bounds':{},'guides':{},
                'body_nodes':[f'leg_{side}',f'foot_{side}'],'provides':[f'sock_{side}']}
        foot = {'id':f'sock_{side}_foot','node':f'sock_{side}','targets':{},'source_bounds':{},'guides':{},
                'body_nodes':[f'leg_{side}',f'foot_{side}'],'replaces':[f'foot_{side}'],
                'cover_rule':{'tag':FOOTWEAR_TAG,'show':'uncovered'}}
        for pose, values in geometry['layers']['shoe_'+side].items():
            # The geometry is frozen authoring input, not inferred at playback.
            theta = np.deg2rad(values['angle'])
            matrix = values['scale']*np.array([[np.cos(theta),-np.sin(theta)],[np.sin(theta),np.cos(theta)]])
            shift = np.array(values['collar'])-matrix@np.array(geometry['source_collar'])
            uv = (coords-shift)@np.linalg.inv(matrix).T
            distance = (uv[...,1]-geometry['source_cut_y'])*values['scale']
            leg_alpha = np.asarray(Image.open(source/f'leg_{side}'/f'{pose}.png').getchannel('A')).astype(float)
            foot_alpha = np.asarray(Image.open(source/f'foot_{side}'/f'{pose}.png').getchannel('A')).astype(float)
            x1,y1,x2,y2 = values['body_region']
            local = (xx>=x1)&(xx<x2)&(yy>=y1)&(yy<y2)
            proximal = (leg_alpha>=128)&local&(distance>=-35)&(distance<=-25)
            distal = (leg_alpha>=128)&local&(distance>=-5)&(distance<=0)
            assert proximal.any() and distal.any(), (pose,side,'annotated calf samples missing')
            upper = coords[proximal].mean(axis=0)
            lower = coords[distal].mean(axis=0)
            axis = (upper-lower)/np.linalg.norm(upper-lower)
            across = np.array([-axis[1],axis[0]])
            along = (coords-lower)@axis
            cross = (coords-lower)@across
            # Cuff and ribs follow the calf, not the independently bent foot.
            # The semantic leg raster already owns only this limb. Clipping to
            # the rectangular measurement ROI creates a kink on bent calves.
            cuff_alpha = np.rint(leg_alpha*np.clip(length_px-along+.5,0,1)).astype('uint8')
            cuff_alpha,dropped = attached_cuff(cuff_alpha,foot_alpha)
            foot_alpha = foot_alpha.astype('uint8')
            assert not np.any((cuff_alpha>0)&(foot_alpha>0)), 'split layers must have exclusive ownership'
            alpha = np.maximum(cuff_alpha,foot_alpha)
            # Albedo follows the annotated ankle basis. Clamp contrast to keep
            # the tiny illustration calm rather than making noisy knit pixels.
            u = np.floor(cross*8).astype(int)%256
            v = np.floor(along*8).astype(int)%256
            albedo = np.clip((cloth[v,u]-mean)*.15,-3,3)*(cuff_alpha>0)[...,None]
            rgb = np.broadcast_to(np.array([245,239,221.]),(*alpha.shape,3)).copy()+albedo
            eroded = np.asarray(Image.fromarray(alpha).filter(ImageFilter.MinFilter(3))).astype(float)
            edge = np.clip((alpha.astype(float)-eroded)/255,0,1)
            edge = np.asarray(Image.fromarray(np.rint(edge*255).astype('uint8')).filter(ImageFilter.GaussianBlur(.4)))/255
            rgb = rgb*(1-edge[...,None])+np.array([151,138,113])*edge[...,None]
            hem = np.clip(1-np.abs(along-length_px+1)/1.1,0,1)*(cuff_alpha>0)
            rgb -= hem[...,None]*34
            rgb = np.clip(np.rint(rgb),0,255).astype('uint8')
            for layer,opacity in ((cuff,cuff_alpha),(foot,foot_alpha)):
                pixels = np.dstack([rgb,opacity]); pixels[opacity==0]=0
                file = directory/f"{pose}.{layer['id']}.png"
                Image.fromarray(pixels).save(file)
                py,px = np.nonzero(opacity>=16)
                assert len(px), (pose,layer['id'],'empty sock reference')
                layer['targets'][pose] = [max(0,int(px.min())-4),max(0,int(py.min())-4),min(768,int(px.max())+5),min(768,int(py.max())+5)]
                layer['source_bounds'][pose] = [0,0,768,768]
                layer['guides'][pose] = [file.relative_to(root).as_posix()]
                rasters[pose,layer['id']] = file
            checks = criteria.setdefault(pose,{'required':np.zeros((768,768),bool),'allowed':np.zeros((768,768),bool),'protected':np.zeros((768,768),bool)})
            checks['required'] |= alpha>=240
            checks['allowed'] |= np.asarray(Image.fromarray(alpha).filter(ImageFilter.MaxFilter(3)))>=1
            checks['protected'] |= (leg_alpha>=16)&local&(along > length_px+3)
            measured.append({'pose':pose,'side':side,'calf_axis':axis.tolist(),'calf_samples':[lower.tolist(),upper.tolist()],
                             'cuff_pixels':int((cuff_alpha>=16).sum()),'foot_pixels':int((foot_alpha>=16).sum()),
                             'detached_projection_pixels_removed':dropped})
        layers += [cuff,foot]
    cfg = {'kind':'parts-production-config','schema_version':1,'part_id':'qualified.socks','design_id':'cream-rib-knit-socks',
           'slot':'socks','node':'sock_front','socket':'root','states':['run'],'visibility':'zmap',
           'prompt':{'text':'Short pale cream cotton socks, tightly fitted to the exact calf, ankle and foot silhouette. The plain foot has soft thin fabric; only the cuff has restrained fine ribs following the calf length. A clearly drawn thin cuff hem follows the calf cross-section, not the bent foot axis. No heavy stripes, skin pixels, logos or shoe. Cuff and foot are separate layer pieces; footwear hides only the foot piece. Procedural pose images are construction drafts, not final visual approvals.',
                     'refs':[(directory/'native-material.png').relative_to(root).as_posix()]},
           'independent':{'layers':layers,'require_vision':True,'reference_previous':False},'pose_checks':{}}
    for pose,checks in criteria.items():
        cfg['pose_checks'][pose] = {}
        for name,mask in checks.items():
            file = directory/f'{pose}.{name}.png'
            Image.fromarray(mask.astype('uint8')*255).save(file)
            cfg['pose_checks'][pose][name] = file.relative_to(root).as_posix()
    config = directory/'config.json'; config.write_bytes(w.json_bytes(cfg))
    if (root/'productions/qualified.socks/production.json').exists():
        p.configure(assembly,'qualified.socks',config)
    else:
        p.init(assembly,config)
    batch = p.sheet(assembly,'qualified.socks',poses=['run.0','run.1','run.2','run.3'])
    pages = [(Path(str(n)),rasters[job['pose'],job['layer']]) for n,job in enumerate(batch['pages'])]
    p.slice_batch(assembly,'qualified.socks',batch=batch['batch'],pages=pages,registered_canvas=True)
    checks = p.check(assembly,'qualified.socks')
    p.build(assembly,'qualified.socks')
    # Cover tags already belong to the engine; the production simply authors
    # this shoe's tag. Shoe pixels and body geometry are unchanged.
    shoe = deepcopy(p._load(root,'qualified.sneakers')['config'])
    shoe['covers'] = sorted(set(shoe['covers'])|{FOOTWEAR_TAG})
    path = directory/'footwear-config.json'; path.write_bytes(w.json_bytes(shoe))
    p.configure(assembly,'qualified.sneakers',path)
    p.check(assembly,'qualified.sneakers')
    p.build(assembly,'qualified.sneakers')
    proof = {'kind':'procedural-sock-reference-authoring','body':index['body'],'motion':index['motion'],
             'material_sha256':a.sha256(material.read_bytes()),'cuff_length_px':length_px,'layers':measured,
             'sock_source_images':16,'body_rgb_copied':False,'body_modified':False,'runtime_masks':0,
             'runtime_rigging':False,'appearance_clips_generated':0,'art_approved':False,'checks':checks}
    (directory/'authoring.json').write_bytes(w.json_bytes(proof))
    return {'assembly':str(assembly),'config':str(config),**proof}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--material',type=Path,required=True)
    parser.add_argument('--revision',default='socks-r4')
    parser.add_argument('--length-px',type=int,default=32)
    print(author(**vars(parser.parse_args())))
