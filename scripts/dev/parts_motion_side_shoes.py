# SPDX-License-Identifier: Apache-2.0
"""Register a generated shoe illustration on existing motion landmarks.

Native artwork owns perspective; uniform registration owns scale and rotation.
The canonical UV sole/entry are explicit authoring references, never a 3D fit.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter
from scripts.dev.parts_full_motion import foot_matrix, head_box, similarity
from sprite_gen.curate import parts_motion
from sprite_gen.frames import parts_production as p
from sprite_gen.frames.parts_segments import _components
from sprite_gen.spec import parts_assets as a, parts_workspace as w


def author(assembly, master, revision, registration=None):
    root,index,reg,_=parts_motion._active(assembly);folder=root/'inputs'/revision
    if folder.exists():raise ValueError('use a fresh artwork revision')
    registration=registration or master.with_suffix('.registration.json')
    observed=json.loads(registration.read_text())
    assert a.sha256(master.read_bytes())==observed['source_sha256'],'shoe annotation belongs to another native image'
    native=Image.open(master).convert('RGBA')
    assert list(native.size)==observed['source_size'],'shoe annotation canvas differs'
    folder.mkdir();array=np.array(native)
    main=max(_components(array[...,3]>=16),key=lambda m:int(m.sum()))
    keep=np.array(Image.fromarray(main).filter(ImageFilter.MaxFilter(5)))
    array[~keep]=0;clean=Image.fromarray(array)
    # Observed on this exact generated native; never guessed from another image.
    # Uniform registration into the existing authored UV convention keeps the
    # ankle bridge and outsole checks meaningful without squashing the product.
    heel=np.array(observed['sole_heel']);toe=np.array(observed['sole_toe'])
    assert toe[0]>heel[0] and toe[1]==heel[1],'native needs a right-facing horizontal attachment baseline'
    assert observed['canonical_heel']==[120,795] and observed['canonical_toe']==[1430,795],'unsupported ankle/sole UV convention'
    scale=1310/float(toe[0]-heel[0])
    matrix=np.eye(3);matrix[:2,:2]*=scale;matrix[:2,2]=np.array([120,795])-heel*scale
    inverse=np.linalg.inv(matrix)
    canonical=clean.convert('RGBa').transform((1536,1024),Image.Transform.AFFINE,tuple(inverse[:2].ravel()),Image.Resampling.BICUBIC).convert('RGBA')
    canonical.save(folder/'shoe-side-canonical.png')
    geometry=w.read_json(root,'inputs/geometry.json');state=p._load(root,'full-motion.sneakers');config=deepcopy(state['config'])
    config['prompt']['refs']=[(folder/'shoe-side-canonical.png').relative_to(root).as_posix()]
    config['prompt']['text']='Independent navy/cream low-top sneaker; native illustrated depth, uniformly attached to the source foot. No nonuniform shape warp.'
    lengths={}
    for name in index['states']:
        widths=[head_box(Image.open(root/f'inputs/body/{name}/{int(pose.split(".")[1]):03}.png').convert('RGBA')) for pose in geometry['poses'] if pose.startswith(name+'.')]
        lengths[name]=float(np.median([88*(h[2]-h[0])/240 for h in widths]))
    yy,xx=np.mgrid[:768,:768];coords=np.stack([xx,yy],-1);files={};proof=[]
    for pose,values in geometry['poses'].items():
        name=pose.split('.')[0];allowed=np.zeros((768,768),dtype='uint8')
        for side in ('back','front'):
            q=values[side];q['footwear_length']=lengths[name];transform=foot_matrix(q)
            identity='shoe_'+side;layer=next(l for l in config['independent']['layers'] if l['id']==identity)
            hidden=q.get('foot_fully_occluded',False)
            image=Image.new('RGBA',(768,768)) if hidden else similarity(canonical,transform)
            if side=='back' and not hidden:
                pixels=np.array(image);pixels[...,:3]=np.rint(pixels[...,:3]*.96).astype('uint8');image=Image.fromarray(pixels)
            if hidden:
                layer.setdefault('visible',{})[pose]=False;layer['straight_edges'].pop(pose,None)
            else:
                layer.setdefault('visible',{})[pose]=True
                # A rounded bevel has a curved silhouette. Do not mislabel it
                # as a failed straight outsole or flatten it to satisfy QC.
                if observed.get('sole_profile')=='rounded bevel':
                    layer['straight_edges'].pop(pose,None)
                else:
                    layer['straight_edges'][pose]=[{'id':'rigid-outsole','start':np.rint(transform[:2,:2]@np.array([180,795])+transform[:2,2]).astype(int).tolist(),'end':np.rint(transform[:2,:2]@np.array([1260,795])+transform[:2,2]).astype(int).tolist(),'side':'positive','span':['3/20','4/5'],'max_deviation':'2'}]
            inv=np.linalg.inv(transform);uv=coords@inv[:2,:2].T+inv[:2,2]
            leg=np.array(Image.open(root/f'inputs/body-segments/leg_{side}/{pose}.png').getchannel('A'))
            lip=(uv[...,0]<410)&(uv[...,1]>345)&(uv[...,1]<465)
            mask=Image.fromarray(255-np.rint(leg.astype(float)*lip).astype('uint8'));maskfile=folder/f'{pose}.{side}.keep.png';mask.save(maskfile)
            layer.setdefault('masks',{})[pose]={'target_node':'foot_'+side,'image':{'file':maskfile.relative_to(root).as_posix(),'sha256':a.sha256(maskfile.read_bytes())},'space':'rig-canvas','channel':'luma'}
            alpha=np.array(image.getchannel('A'));occupied=np.argwhere(alpha>=16)
            if len(occupied):
                low=occupied.min(0);high=occupied.max(0)+1;layer['targets'][pose]=[max(0,int(low[1])-4),max(0,int(low[0])-4),min(768,int(high[1])+4),min(768,int(high[0])+4)]
            file=folder/f'{pose}.{identity}.png';image.save(file);files[pose,identity]=file;layer['guides'][pose]=[file.relative_to(root).as_posix()]
            layer['landmarks'][pose]={k:q[k] for k in ('ankle','heel','toe')};layer['landmarks'][pose]['shoe_entry']=np.rint(transform[:2,:2]@np.array([410,438])+transform[:2,2]).astype(int).tolist()
            allowed=np.maximum(allowed,np.array(image.getchannel('A').filter(ImageFilter.MaxFilter(5))))
            proof.append({'pose':pose,'side':side,'matrix':transform.tolist(),'fixed_product_length':lengths[name],'hidden':hidden,'far_material_multiplier':.96 if side=='back' else 1})
        file=folder/f'{pose}.allowed.png';Image.fromarray(allowed).save(file);config['pose_checks'][pose]['allowed']=file.relative_to(root).as_posix()
    (root/'inputs/geometry.json').write_bytes(w.json_bytes(geometry))
    file=folder/'config.json';file.write_bytes(w.json_bytes(config));p.configure(assembly,'full-motion.sneakers',file)
    batch=p.sheet(assembly,'full-motion.sneakers',poses=list(geometry['poses']))
    p.slice_batch(assembly,'full-motion.sneakers',batch=batch['batch'],pages=[(Path(str(n)),files[j['pose'],j['layer']]) for n,j in enumerate(batch['pages'])],registered_canvas=True)
    record={'provider':'built-in-imagegen','native_sha256':a.sha256(master.read_bytes()),'canonical_sha256':a.sha256((folder/'shoe-side-canonical.png').read_bytes()),'native_to_canonical':matrix.tolist(),'native_sole_landmarks':{'heel':heel.tolist(),'toe':toe.tolist()},'registration_sha256':a.sha256(registration.read_bytes()),'cleanup':'largest alpha>=16 component plus 2px fringe','fixed_lengths':lengths,'poses':proof,'perspective':observed.get('perspective','generated orthographic lateral illustration; no nonuniform warp'),'sole_profile':observed.get('sole_profile','straight lateral'),'art_approved':False,'appearance_clips_baked':0}
    (folder/'authoring.json').write_bytes(w.json_bytes(record))
    return {'frames':len(geometry['poses']),'shoe_layers':len(proof),'build':'pending','art_approved':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assembly',type=Path,required=True);parser.add_argument('--master',type=Path,required=True);parser.add_argument('--revision',required=True)
    parser.add_argument('--registration',type=Path)
    print(json.dumps(author(**vars(parser.parse_args()))))
