# SPDX-License-Identifier: Apache-2.0
"""Register the rigid native product on the source heel instead of ankle drift."""
import argparse
from copy import deepcopy
import json
from pathlib import Path

import numpy as np
from PIL import Image,ImageFilter
from scripts.dev.parts_full_motion import foot_matrix,head_box,similarity
from sprite_gen.curate import parts_motion,parts_appearance
from sprite_gen.frames import parts_production as p
from sprite_gen.spec import parts_assets as a,parts_contract as c,parts_workspace as w


def fit(assembly,revision,poses=None,defer_build=False):
    root,index,reg,_=parts_motion._active(assembly);folder=root/'inputs'/revision
    if folder.exists():raise ValueError('preserve prior product placement')
    folder.mkdir();state=p._load(root,'full-motion.sneakers');config=deepcopy(state['config'])
    geometry=w.read_json(root,'inputs/geometry.json');(folder/'geometry-before.json').write_bytes(w.json_bytes(geometry))
    master_file=root/config['prompt']['refs'][0];master=Image.open(master_file).convert('RGBA')
    yy,xx=np.mgrid[:768,:768];coords=np.stack([xx,yy],-1);files={};proof=[]
    selected=poses or list(geometry['poses'])
    for pose in selected:
        values=geometry['poses'][pose]
        state_name,n=pose.split('.');body=Image.open(root/f'inputs/body/{state_name}/{int(n):03}.png').convert('RGBA');head=head_box(body)
        assert a.sha256((root/f'inputs/body/{state_name}/{int(n):03}.png').read_bytes())==values['body_sha256']
        allowed=np.zeros((768,768),dtype='uint8')
        for side in ['back','front']:
            points=values[side];points['footwear_anchor']='heel';matrix=foot_matrix(points,length=88*(head[2]-head[0])/240)
            identity='shoe_'+side;layer=next(l for l in config['independent']['layers'] if l['id']==identity)
            hidden=points.get('foot_fully_occluded',False)
            image=Image.new('RGBA',(768,768)) if hidden else similarity(master,matrix)
            if hidden:
                layer.setdefault('visible',{})[pose]=False;layer['straight_edges'].pop(pose,None)
            else:
                layer['straight_edges'][pose]=[{'id':'rigid-outsole','start':np.rint(matrix[:2,:2]@np.array([180,795])+matrix[:2,2]).astype(int).tolist(),
                    'end':np.rint(matrix[:2,:2]@np.array([1260,795])+matrix[:2,2]).astype(int).tolist(),'side':'positive','span':['3/20','4/5'],'max_deviation':'2'}]
            inv=np.linalg.inv(matrix);uv=coords@inv[:2,:2].T+inv[:2,2]
            leg=np.array(Image.open(root/f'inputs/body-segments/leg_{side}/{pose}.png').getchannel('A'))
            lip=(uv[...,0]<410)&(uv[...,1]>345)&(uv[...,1]<465)
            mask=Image.fromarray(255-np.rint(leg.astype(float)*lip).astype('uint8'));maskfile=folder/f'{pose}.{side}.keep.png';mask.save(maskfile)
            layer.setdefault('masks',{})[pose]={'target_node':'foot_'+side,'image':{'file':maskfile.relative_to(root).as_posix(),'sha256':a.sha256(maskfile.read_bytes())},'space':'rig-canvas','channel':'luma'}
            alpha=np.array(image.getchannel('A'));pixels=np.argwhere(alpha>=16)
            if len(pixels):
                lo=pixels.min(0);hi=pixels.max(0)+1;layer['targets'][pose]=[max(0,int(lo[1])-4),max(0,int(lo[0])-4),min(768,int(hi[1])+4),min(768,int(hi[0])+4)]
            file=folder/f'{pose}.{identity}.png';image.save(file);files[pose,identity]=file;layer['guides'][pose]=[file.relative_to(root).as_posix()]
            allowed=np.maximum(allowed,np.array(image.getchannel('A').filter(ImageFilter.MaxFilter(5))))
            heel=matrix[:2,:2]@np.array([120,795])+matrix[:2,2]
            if not points.get('footwear_entry_weight',0):
                assert np.allclose(heel,points['heel']),pose
            layer['landmarks'][pose]={k:points[k] for k in ['ankle','heel','toe']}
            layer['landmarks'][pose]['shoe_entry']=np.rint(matrix[:2,:2]@np.array([410,438])+matrix[:2,2]).astype(int).tolist()
            proof.append({'pose':pose,'side':side,'matrix':matrix.tolist(),'heel_registration_error':float(np.linalg.norm(heel-points['heel'])),'hidden':hidden,'uniform_scale':float(np.linalg.norm(matrix[:2,0]))})
        file=folder/f'{pose}.allowed.png';Image.fromarray(allowed).save(file);config['pose_checks'][pose]['allowed']=file.relative_to(root).as_posix()
    (root/'inputs/geometry.json').write_bytes(w.json_bytes(geometry))
    (folder/'authoring.json').write_bytes(w.json_bytes({'poses':proof,'shoe_master_sha256':a.sha256(master_file.read_bytes()),
        'method':'uniform scale/rotation with source heel reference and bounded ankle-entry adjustment; no three-point deformation','art_approved':False}))
    file=folder/'config.json';file.write_bytes(w.json_bytes(config));p.configure(assembly,'full-motion.sneakers',file)
    batch=p.sheet(assembly,'full-motion.sneakers',poses=selected)
    p.slice_batch(assembly,'full-motion.sneakers',batch=batch['batch'],pages=[(Path(str(n)),files[j['pose'],j['layer']]) for n,j in enumerate(batch['pages'])],registered_canvas=True)
    if defer_build:
        return {'poses':len(selected),'heels':len(proof),'build':'pending','art_approved':False}
    p.check(assembly,'full-motion.sneakers');p.build(assembly,'full-motion.sneakers')
    _,index,reg,_=parts_motion._active(assembly);selection={'kind':'parts-appearance-selection','schema_version':1,'id':'full-motion.example','selections':{
        slot:c.reference(reg['full-motion.'+name]) for slot,name in [('hair','hair'),('footwear','sneakers'),('socks','socks')]}}
    file=folder/'appearance.json';file.write_bytes(w.json_bytes(selection));parts_appearance.create(assembly,file)
    return {'poses':len(selected),'heels':len(proof),'art_approved':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--assembly',type=Path,required=True);parser.add_argument('--revision',required=True)
    parser.add_argument('--poses',nargs='+');parser.add_argument('--defer-build',action='store_true')
    print(json.dumps(fit(**vars(parser.parse_args()))))
