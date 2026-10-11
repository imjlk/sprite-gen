# SPDX-License-Identifier: Apache-2.0
"""Fill source ankle interfaces above the native shoe sole, preserving cuffs.

This corpus uses one rigid shoe master. UV 795 is its authored outsole and UV
700 is safely behind its forefoot. A source-foot bridge remains visible above
that outsole when the closed shoe hides the separate sock-foot layer.
"""
import argparse
from copy import deepcopy
from pathlib import Path
import json

import numpy as np
from PIL import Image, ImageFilter
from scripts.dev.parts_full_motion import foot_matrix,head_box
from sprite_gen.curate import parts_motion,parts_appearance
from sprite_gen.frames import parts_production as p
from sprite_gen.spec import parts_assets as a,parts_contract as c,parts_workspace as w


def bridge(assembly,revision,defer_build=False,include_lower_leg=False):
    root,index,reg,_=parts_motion._active(assembly);folder=root/'inputs'/revision
    if folder.exists():raise ValueError('preserve prior bridges')
    folder.mkdir();state=p._load(root,'full-motion.socks');config=deepcopy(state['config'])
    geometry=w.read_json(root,'inputs/geometry.json')['poses'];yy,xx=np.mgrid[:768,:768];coords=np.stack([xx,yy],-1)
    files={};proof=[];changed_poses=[]
    for pose,row in state['tracks']['main']['poses'].items():
        record=p.candidate_record(root,state,pose,row['selected'])
        pieces={l['id']:Image.open(root/row['selected']/record['images'][l['image_key']]['file']).convert('RGBA') for l in record['independent_layers']}
        state_name,n=pose.split('.');body=Image.open(root/f'inputs/body/{state_name}/{int(n):03}.png').convert('RGBA');head=head_box(body)
        changed=False
        for side in ['back','front']:
            points=geometry[pose][side];ankle=np.array(points['ankle']);axis=np.array(points.get('cuff_axis_point',points['knee']))-ankle;axis=axis/np.linalg.norm(axis)
            matrix=foot_matrix(points,length=88*(head[2]-head[0])/240);inv=np.linalg.inv(matrix);uv=coords@inv[:2,:2].T+inv[:2,2]
            foot=np.array(Image.open(root/f'inputs/body-segments/foot_{side}/{pose}.png').getchannel('A'))
            required=(uv[...,0]>points.get('sock_bridge_u_min',-500))&(uv[...,0]<700)&(uv[...,1]<785)&(((coords-ankle)**2).sum(-1)<70**2)&((coords-ankle)@axis>-45)
            if points.get('cuff_fully_occluded') or points.get('foot_fully_occluded'):required[:]=False
            leg=np.array(Image.open(root/f'inputs/body-segments/leg_{side}/{pose}.png').getchannel('A'))
            along=(coords-ankle)@axis
            across=(coords-ankle)@np.array([-axis[1],axis[0]])
            lower=np.where((along<=0)&(np.abs(across)<=points.get('cuff_half_width',30)),leg,0).astype('uint8') if include_lower_leg else np.zeros_like(leg)
            top=points.get('cuff_top_height')
            upper=np.where((along>=0)&(along<=top)&(np.abs(across)<=points.get('cuff_half_width',30)),leg,0).astype('uint8') if top is not None else np.zeros_like(leg)
            if top is not None:
                required &= along<=top+.5
            added=np.where(required,np.maximum(np.maximum(foot,lower),upper),0).astype('uint8');identity='sock_'+side+'_cuff';old=np.array(pieces[identity])
            # Shoe placement can change while the body stays the same. Retain
            # the authored calf, but rebuild its foot-sourced bridge against
            # the CURRENT sole instead of accumulating former bridge pixels.
            retained=np.minimum(old[...,3],leg);alpha=np.maximum(retained,added)
            edge=(alpha.astype(float)-np.array(Image.fromarray(alpha).filter(ImageFilter.MinFilter(3))))/255
            rgb=np.array([245.,239.,221.])*(1-edge[...,None])+np.array([151,138,113])*edge[...,None]
            new=np.dstack([np.rint(rgb).astype('uint8'),alpha]);kept=retained>0;new[kept,:3]=old[kept,:3];new[alpha==0]=0
            changed |= not np.array_equal(new,old)
            pieces[identity]=Image.fromarray(new)
            guide=folder/f'{pose}.{side}.ankle-required.png';Image.fromarray(added).save(guide)
            proof.append({'pose':pose,'side':side,'added_pixels':int((alpha>old[...,3]).sum()),'required':guide.relative_to(root).as_posix(),
                          'include_lower_leg':include_lower_leg,'source_leg_sha256':a.sha256((root/f'inputs/body-segments/leg_{side}/{pose}.png').read_bytes()),
                          'source_foot_sha256':a.sha256((root/f'inputs/body-segments/foot_{side}/{pose}.png').read_bytes()),'hidden':bool(not required.any())})
        if not changed:
            continue
        changed_poses.append(pose)
        required=np.zeros((768,768),dtype='uint8');allowed=required.copy()
        for l in config['independent']['layers']:
            im=pieces[l['id']];file=folder/f'{pose}.{l["id"]}.png';im.save(file);files[pose,l['id']]=file
            aa=np.array(im.getchannel('A'));points=np.argwhere(aa>=16)
            if len(points):
                lo=points.min(0);hi=points.max(0)+1;l['targets'][pose]=[max(0,int(lo[1])-4),max(0,int(lo[0])-4),min(768,int(hi[1])+4),min(768,int(hi[0])+4)]
            l['guides'][pose]=[file.relative_to(root).as_posix()]
            required=np.maximum(required,np.where(aa>=240,255,0).astype('uint8'));allowed=np.maximum(allowed,np.array(im.getchannel('A').filter(ImageFilter.MaxFilter(5))))
        for k,mask in [('required',required),('allowed',allowed)]:
            file=folder/f'{pose}.{k}.png';Image.fromarray(mask).save(file);config['pose_checks'][pose][k]=file.relative_to(root).as_posix()
    (folder/'authoring.json').write_bytes(w.json_bytes({'poses':proof,'method':'retain existing cuffs; add own source-foot and optional distal-calf interface strictly above rigid shoe sole','art_approved':False}))
    if not changed_poses:
        (root/'inputs/ankle-continuity-active.json').write_bytes(w.json_bytes({
            'authoring':(folder/'authoring.json').relative_to(root).as_posix(),
            'sha256':a.sha256((folder/'authoring.json').read_bytes())}))
        return {'poses_changed':0,'interfaces_checked':len(proof),'art_approved':False}
    file=folder/'config.json';file.write_bytes(w.json_bytes(config));p.configure(assembly,'full-motion.socks',file)
    batch=p.sheet(assembly,'full-motion.socks',poses=changed_poses,layers=['sock_back_cuff','sock_front_cuff'])
    pages=[(Path(str(n)),files[j['pose'],j['layer']]) for n,j in enumerate(batch['pages']) if not j.get('reuse')]
    p.slice_batch(assembly,'full-motion.socks',batch=batch['batch'],pages=pages,registered_canvas=True)
    if defer_build:
        (root/'inputs/ankle-continuity-active.json').write_bytes(w.json_bytes({
            'authoring':(folder/'authoring.json').relative_to(root).as_posix(),
            'sha256':a.sha256((folder/'authoring.json').read_bytes())}))
        return {'poses_changed':len(changed_poses),'interfaces_checked':len(proof),'build':'pending','art_approved':False}
    p.check(assembly,'full-motion.socks');p.build(assembly,'full-motion.socks')
    _,index,reg,_=parts_motion._active(assembly);selection={'kind':'parts-appearance-selection','schema_version':1,'id':'full-motion.example','selections':{
        slot:c.reference(reg['full-motion.'+name]) for slot,name in [('hair','hair'),('footwear','sneakers'),('socks','socks')]}}
    file=folder/'appearance.json';file.write_bytes(w.json_bytes(selection));parts_appearance.create(assembly,file)
    (root/'inputs/ankle-continuity-active.json').write_bytes(w.json_bytes({
        'authoring':(folder/'authoring.json').relative_to(root).as_posix(),
        'sha256':a.sha256((folder/'authoring.json').read_bytes())}))
    return {'poses_changed':len(changed_poses),'interfaces_checked':len(proof),'art_approved':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--assembly',type=Path,required=True);parser.add_argument('--revision',required=True);parser.add_argument('--defer-build',action='store_true')
    parser.add_argument('--include-lower-leg',action='store_true')
    print(json.dumps(bridge(**vars(parser.parse_args()))))
