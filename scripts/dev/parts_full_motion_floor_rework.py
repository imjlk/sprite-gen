# SPDX-License-Identifier: Apache-2.0
"""Adopt two source-foot lower-contour proposals, preserving other shoe images."""
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


def rework(assembly,proposal,revision):
    root,index,reg,_=parts_motion._active(assembly);folder=root/'inputs'/revision
    if folder.exists():raise ValueError('preserve previous floor proposals')
    folder.mkdir();rows=json.loads(proposal.read_text());state=p._load(root,'full-motion.sneakers');config=deepcopy(state['config'])
    geometry=w.read_json(root,'inputs/geometry.json');(folder/'geometry-before.json').write_bytes(w.json_bytes(geometry))
    master=Image.open(root/config['prompt']['refs'][0]).convert('RGBA');yy,xx=np.mgrid[:768,:768];coords=np.stack([xx,yy],-1);files={}
    for row in rows:
        pose=row['pose'];side=row['side'];points=geometry['poses'][pose][side];points['footwear_heel']=row['heel'];points['footwear_toe']=row['toe']
        source=root/f'inputs/body-segments/foot_{side}/{pose}.png';row['source_foot_sha256']=a.sha256(source.read_bytes())
        state_name,n=pose.split('.');body=Image.open(root/f'inputs/body/{state_name}/{int(n):03}.png').convert('RGBA');head=head_box(body);matrix=foot_matrix(points,length=88*(head[2]-head[0])/240)
        current=state['tracks']['main']['poses'][pose];record=p.candidate_record(root,state,pose,current['selected'])
        pieces={l['id']:Image.open(root/current['selected']/record['images'][l['image_key']]['file']).convert('RGBA') for l in record['independent_layers']}
        identity='shoe_'+side;pieces[identity]=similarity(master,matrix);layer=next(l for l in config['independent']['layers'] if l['id']==identity)
        inv=np.linalg.inv(matrix);uv=coords@inv[:2,:2].T+inv[:2,2];leg=np.array(Image.open(root/f'inputs/body-segments/leg_{side}/{pose}.png').getchannel('A'));lip=(uv[...,0]<410)&(uv[...,1]>345)&(uv[...,1]<465)
        maskfile=folder/f'{pose}.{side}.keep.png';Image.fromarray(255-np.rint(leg.astype(float)*lip).astype('uint8')).save(maskfile)
        layer['masks'][pose]={'target_node':'foot_'+side,'space':'rig-canvas','channel':'luma','image':{'file':maskfile.relative_to(root).as_posix(),'sha256':a.sha256(maskfile.read_bytes())}}
        layer['straight_edges'][pose]=[{'id':'rigid-outsole','start':np.rint(matrix[:2,:2]@np.array([180,795])+matrix[:2,2]).astype(int).tolist(),
            'end':np.rint(matrix[:2,:2]@np.array([1260,795])+matrix[:2,2]).astype(int).tolist(),'side':'positive','span':['3/20','4/5'],'max_deviation':'2'}]
        layer['landmarks'][pose]['shoe_entry']=np.rint(matrix[:2,:2]@np.array([410,438])+matrix[:2,2]).astype(int).tolist()
        row['matrix']=matrix.tolist();allowed=np.zeros((768,768),dtype='uint8')
        for l in config['independent']['layers']:
            im=pieces[l['id']];file=folder/f'{pose}.{l["id"]}.png';im.save(file);files[pose,l['id']]=file;aa=np.array(im.getchannel('A'));pixels=np.argwhere(aa>=16);lo=pixels.min(0);hi=pixels.max(0)+1
            l['targets'][pose]=[max(0,int(lo[1])-4),max(0,int(lo[0])-4),min(768,int(hi[1])+4),min(768,int(hi[0])+4)];l['guides'][pose]=[file.relative_to(root).as_posix()]
            allowed=np.maximum(allowed,np.array(im.getchannel('A').filter(ImageFilter.MaxFilter(5))))
        file=folder/f'{pose}.allowed.png';Image.fromarray(allowed).save(file);config['pose_checks'][pose]['allowed']=file.relative_to(root).as_posix()
    (root/'inputs/geometry.json').write_bytes(w.json_bytes(geometry));(folder/'authoring.json').write_bytes(w.json_bytes({'poses':rows,'source_specific':True,'art_approved':False}))
    file=folder/'config.json';file.write_bytes(w.json_bytes(config));p.configure(assembly,'full-motion.sneakers',file)
    batch=p.sheet(assembly,'full-motion.sneakers',poses=[r['pose'] for r in rows],layers=['shoe_front'])
    pages=[(Path(str(n)),files[j['pose'],j['layer']]) for n,j in enumerate(batch['pages']) if not j.get('reuse')]
    p.slice_batch(assembly,'full-motion.sneakers',batch=batch['batch'],pages=pages,registered_canvas=True);p.check(assembly,'full-motion.sneakers');p.build(assembly,'full-motion.sneakers')
    _,index,reg,_=parts_motion._active(assembly);selection={'kind':'parts-appearance-selection','schema_version':1,'id':'full-motion.example','selections':{
        slot:c.reference(reg['full-motion.'+name]) for slot,name in [('hair','hair'),('footwear','sneakers'),('socks','socks')]}}
    file=folder/'appearance.json';file.write_bytes(w.json_bytes(selection));parts_appearance.create(assembly,file)
    return {'poses':[r['pose'] for r in rows],'art_approved':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--assembly',type=Path,required=True);parser.add_argument('--proposal',type=Path,required=True);parser.add_argument('--revision',required=True)
    print(json.dumps(rework(**vars(parser.parse_args()))))
