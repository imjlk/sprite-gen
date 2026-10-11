# SPDX-License-Identifier: Apache-2.0
"""Repair two source-bound calf ownership holes without changing body RGBA.

The protected polygons are authored for this exact motion corpus. They are not
an automatic anatomy detector. Original body/parts revisions remain available.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from sprite_gen.curate import parts_motion, parts_appearance
from sprite_gen.frames import parts_import, parts_production as p
from sprite_gen.spec import parts_assets as a, parts_contract as c, parts_workspace as w


def repair(source, out, revision='crossing-r2', regions=None):
    if out.exists():
        raise ValueError('preserve previous workspaces; use a fresh output')
    shutil.copytree(source/'library',out/'library')
    assembly=out/'library/assembly.json'
    root,index,reg,_=parts_motion._active(assembly)
    folder=root/f'inputs/body-segments-{revision}'
    shutil.copytree(root/'inputs/body-segments',folder)
    spec=json.loads((folder/'import.json').read_text())
    polygons=regions or {
        'run.03':[[280,545],[344,545],[349,569],[342,583],[327,600],[310,621],[291,645],[245,645],[265,592]],
        'run.15':[[280,530],[354,530],[355,554],[347,571],[334,590],[314,614],[292,635],[230,635],[265,581]],
    }
    proof=[];moved_by_pose={}
    for pose,polygon in polygons.items():
        leg_node='leg_front' if regions else 'leg_back'
        guard=Image.new('1',(768,768));ImageDraw.Draw(guard).polygon([tuple(q) for q in polygon],fill=1)
        footfile=folder/'foot_front'/f'{pose}.png';legfile=folder/leg_node/f'{pose}.png'
        foot=np.array(Image.open(footfile).convert('RGBA'));leg=np.array(Image.open(legfile).convert('RGBA'))
        moved=np.asarray(guard)&((leg if regions else foot)[...,3]>0)
        moved_by_pose[pose]=moved
        if regions:
            foot[moved]=leg[moved];leg[moved]=0
        else:
            leg[moved]=foot[moved];foot[moved]=0
        Image.fromarray(foot).save(footfile);Image.fromarray(leg).save(legfile)
        required=np.where(moved,leg[...,3],0).astype('uint8')
        previous=folder/f'{pose}.protected-calf.png'
        if previous.exists():required=np.maximum(required,np.array(Image.open(previous).convert('L')))
        required=Image.fromarray(required)
        required.save(folder/f'{pose}.protected-calf.png')
        for node in ['foot_front',leg_node]:
            layer=next(s for s in spec['pieces'] if s['id']==node)
            layer['pose_map'][pose]['image']['sha256']=a.sha256((folder/node/f'{pose}.png').read_bytes())
        state,n=pose.split('.');body=Image.open(root/f'inputs/body/{state}/{int(n):03}.png').convert('RGBA')
        merged=Image.new('RGBA',body.size)
        for piece in spec['pieces']:merged.alpha_composite(Image.open(folder/piece['id']/f'{pose}.png').convert('RGBA'))
        assert np.array_equal(np.asarray(merged),np.asarray(body)),pose
        proof.append({'pose':pose,'polygon':polygon,'moved_pixels':int(moved.sum()),
                      'source_body_sha256':a.sha256((root/f'inputs/body/{state}/{int(n):03}.png').read_bytes()),
                      'body_rgba_changed':False,'from':leg_node if regions else 'foot_front','to':'foot_front' if regions else leg_node,
                      'protected_calf':f'{pose}.protected-calf.png'})
    (folder/'import.json').write_bytes(w.json_bytes(spec))
    imported=parts_import.import_pack(assembly,folder/'import.json',folder)
    # Future source-bound authoring must use the corrected segmentation too.
    backup=root/f'inputs/body-segments-before-{revision}'
    (root/'inputs/body-segments').rename(backup)
    shutil.copytree(folder,root/'inputs/body-segments')
    (folder/'repair.json').write_bytes(w.json_bytes({'poses':proof,'body':imported['part'],'art_approved':False}))
    state=p._load(root,'full-motion.socks');config=deepcopy(state['config']);art={}
    edits=root/f'inputs/{revision}-sock-feet';edits.mkdir()
    for pose in polygons:
        row=state['tracks']['main']['poses'][pose];old=p.candidate_record(root,state,pose,row['selected'])
        pieces={l['id']:Image.open(root/row['selected']/old['images'][l['image_key']]['file']).convert('RGBA') for l in old['independent_layers']}
        alpha=np.array(Image.open(folder/'foot_front'/f'{pose}.png').getchannel('A'))
        rgb=np.broadcast_to(np.array([245.,239.,221.]),(*alpha.shape,3)).copy()
        edge=(alpha.astype(float)-np.asarray(Image.fromarray(alpha).filter(ImageFilter.MinFilter(3))))/255
        rgb=rgb*(1-edge[...,None])+np.array([151,138,113])*edge[...,None]
        arr=np.dstack([np.rint(rgb).astype('uint8'),alpha]);arr[alpha==0]=0
        pieces['sock_front_foot']=Image.fromarray(arr)
        if regions:
            cuff=np.array(pieces['sock_front_cuff'])
            cuff[moved_by_pose[pose]]=0
            pieces['sock_front_cuff']=Image.fromarray(cuff)
        required=np.zeros(alpha.shape,dtype='uint8');allowed=required.copy()
        for l in config['independent']['layers']:
            im=pieces[l['id']];file=edits/f'{pose}.{l["id"]}.png';im.save(file);art[pose,l['id']]=file
            aa=np.array(im.getchannel('A'));points=np.argwhere(aa>=16);lo=points.min(0);hi=points.max(0)+1
            l['targets'][pose]=[max(0,int(lo[1])-4),max(0,int(lo[0])-4),min(768,int(hi[1])+4),min(768,int(hi[0])+4)]
            l['guides'][pose]=[file.relative_to(root).as_posix()]
            required=np.maximum(required,np.where(aa>=240,255,0).astype('uint8'))
            allowed=np.maximum(allowed,np.array(im.getchannel('A').filter(ImageFilter.MaxFilter(5))))
        for k,mask in [('required',required),('allowed',allowed)]:
            file=edits/f'{pose}.{k}.png';Image.fromarray(mask).save(file);config['pose_checks'][pose][k]=file.relative_to(root).as_posix()
    file=edits/'config.json';file.write_bytes(w.json_bytes(config));p.configure(assembly,'full-motion.socks',file)
    p.request_rework(assembly,'full-motion.socks',list(polygons),'교차 종아리가 발 교체로 지워지던 body 소속을 교정했다. 양말 발도 수정한 foot alpha로 다시 제작한다. body RGB는 복사하지 않는다.')
    batch=p.sheet(assembly,'full-motion.socks',poses=list(polygons),layers=['sock_front_foot','sock_front_cuff'] if regions else ['sock_front_foot'])
    pages=[(Path(str(n)),art[j['pose'],j['layer']]) for n,j in enumerate(batch['pages']) if not j.get('reuse')]
    p.slice_batch(assembly,'full-motion.socks',batch=batch['batch'],pages=pages,registered_canvas=True)
    for name in ['hair','sneakers','socks']:
        p.check(assembly,'full-motion.'+name);p.build(assembly,'full-motion.'+name)
    _,index,reg,_=parts_motion._active(assembly)
    selection={'kind':'parts-appearance-selection','schema_version':1,'id':'full-motion.example','selections':{
        slot:c.reference(reg['full-motion.'+name]) for slot,name in [('hair','hair'),('footwear','sneakers'),('socks','socks')]}}
    file=edits/'appearance.json';file.write_bytes(w.json_bytes(selection));parts_appearance.create(assembly,file)
    return {'body':imported['part'],'poses':list(polygons),'art_approved':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True);parser.add_argument('--out',type=Path,required=True);parser.add_argument('--revision',default='crossing-r2');parser.add_argument('--regions',type=Path)
    args=vars(parser.parse_args())
    if args['regions']:args['regions']=json.loads(args['regions'].read_text())
    print(json.dumps(repair(**args)))
