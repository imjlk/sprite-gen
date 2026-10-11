# SPDX-License-Identifier: Apache-2.0
"""Render, but do not adopt, rigid shoe entry-registration proposals."""
from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from scripts.dev.parts_full_motion import foot_matrix, head_box, similarity
from sprite_gen.compose import compose_parts
from sprite_gen.compose.parts_runtime import PartsPlayer
from sprite_gen.frames import parts_production as p
from sprite_gen.spec import parts_assets as a, parts_workspace as w


def proposals(assembly, out):
    if out.exists():raise ValueError('preserve previous shoe proposals')
    out.mkdir(parents=True)
    root=assembly.parent;g=w.read_json(root,'inputs/geometry.json')['poses']
    state=p._load(root,'full-motion.sneakers');master=Image.open(root/state['config']['prompt']['refs'][0]).convert('RGBA')
    player=PartsPlayer.from_workspace(assembly);player.set_state('run')
    yy,xx=np.mgrid[:768,:768];coords=np.stack([xx,yy],-1)
    chosen=[('run.02','front'),('run.05','back'),('run.14','front'),('run.17','back')]
    board=Image.new('RGB',(1500,420*len(chosen)),(235,235,235));draw=ImageDraw.Draw(board);proof=[]
    for n,(pose,side) in enumerate(chosen):
        index=int(pose.split('.')[1]);player.seek(Fraction(index,24)+Fraction(1,1000))
        _,sample,_=player.current();body=Image.open(root/f'inputs/body/run/{index:03}.png').convert('RGBA')
        head=head_box(body);q=g[pose][side];original=foot_matrix(q,length=88*(head[2]-head[0])/240)
        entry=original[:2,:2]@np.array([410,438])+original[:2,2];delta=np.array(q['ankle'])-entry
        row={'pose':pose,'side':side,'entry_error':float(np.linalg.norm(delta)),'variants':[]}
        for col,weight in enumerate((0,.5,1)):
            s=deepcopy(sample);images={};matrix=original.copy();matrix[:2,2]+=delta*weight
            shoe=similarity(master,matrix);inverse=np.linalg.inv(matrix);uv=coords@inverse[:2,:2].T+inverse[:2,2]
            leg=np.array(Image.open(root/f'inputs/body-segments/leg_{side}/{pose}.png').getchannel('A'))
            foot=np.array(Image.open(root/f'inputs/body-segments/foot_{side}/{pose}.png').getchannel('A'))
            lip=(uv[...,0]<410)&(uv[...,1]>345)&(uv[...,1]<465)
            mask=Image.fromarray(255-np.rint(leg.astype(float)*lip).astype('uint8'))
            axis=np.array(q.get('cuff_axis_point',q['knee']))-q['ankle'];axis=axis/np.linalg.norm(axis)
            bridge=(uv[...,0]<700)&(uv[...,1]<785)&(((coords-q['ankle'])**2).sum(-1)<70**2)&((coords-q['ankle'])@axis>-45)
            for e in s['draw']:
                if e['piece_id']=='shoe_'+side:
                    images['proto.shoe.png']=shoe;images['proto.mask.png']=mask.convert('RGBA')
                    e['placement']['image']={'file':'proto.shoe.png','sha256':a.sha256(shoe.tobytes())}
                    e['placement']['mask']={'image':{'file':'proto.mask.png','sha256':a.sha256(mask.tobytes())},'target_node':'foot_'+side,'space':'rig-canvas','channel':'luma'}
                    e['offset']=[0,0];e['placement']['pivot']=[0,0];e['placement']['offset']=[0,0]
                elif e['piece_id']=='sock_'+side+'_cuff':
                    old=p._load(root,'full-motion.socks');selected=old['tracks']['main']['poses'][pose]['selected'];record=p.candidate_record(root,old,pose,selected)
                    ref=record['images']['piece.sock_'+side+'_cuff'];arr=np.array(Image.open(root/selected/ref['file']).convert('RGBA'))
                    kept=np.minimum(arr[...,3],leg);added=np.where(bridge,foot,0).astype('uint8');alpha=np.maximum(kept,added)
                    edge=(alpha.astype(float)-np.array(Image.fromarray(alpha).filter(ImageFilter.MinFilter(3))))/255
                    rgb=np.array([245,239,221])*(1-edge[...,None])+np.array([151,138,113])*edge[...,None]
                    rgb[kept>0]=arr[kept>0,:3];made=np.dstack([np.rint(rgb).astype('uint8'),alpha]);made[alpha==0]=0
                    im=Image.fromarray(made);images['proto.cuff.png']=im;e['placement']['image']={'file':'proto.cuff.png','sha256':a.sha256(im.tobytes())};e['offset']=[0,0];e['placement']['pivot']=[0,0];e['placement']['offset']=[0,0]
            im,_=compose_parts.render_sample(player.plan,s,lambda pack,ref:images[ref['file']] if ref['file'] in images else player.load(pack,ref))
            if weight==0:im,_=player.frame()
            im.save(out/f'{pose}.{side}.{weight}.png')
            pts=np.array([q[k] for k in ['ankle','heel','toe']]);low=np.floor(pts.min(0)-60).astype(int);high=np.ceil(pts.max(0)+60).astype(int)
            crop=im.crop((*low,*high));crop.thumbnail((480,350));board.paste(crop,(col*500,n*420+45),crop)
            draw.text((col*500+5,n*420+10),f'{pose} {side} entry weight={weight}',fill='black')
            row['variants'].append({'weight':weight,'matrix':matrix.tolist()})
        proof.append(row)
    board.save(out/'comparison.png');(out/'proposals.json').write_bytes(w.json_bytes({'poses':proof,'adopted':False,'art_approved':False}))


if __name__=='__main__':
    proposals(Path('assets/parts-full-motion-20261011/production-r12/library/assembly.json'),Path('assets/parts-motion-natural-20261012/shoe-entry-proposals-r2'))
