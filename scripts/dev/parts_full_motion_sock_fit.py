# SPDX-License-Identifier: Apache-2.0
"""Round source-bound ankle cuffs with explicit, local calf KEEP masks.

Bare body bytes stay intact. Only the listed sock/ankle interfaces are changed;
unselected layers and all other poses keep their immutable candidates.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter
from sprite_gen.curate import parts_motion, parts_appearance
from sprite_gen.frames import parts_production as p
from sprite_gen.spec import parts_assets as a, parts_contract as c, parts_workspace as w


def fit(assembly, revision, rounded_tube=False, ankle_bridge=False, poses=None, annotated_axis=False, corridor=None, exact_anatomy=False):
    root,index,reg,_=parts_motion._active(assembly)
    folder=root/'inputs'/revision
    if folder.exists():raise ValueError('preserve prior rework')
    folder.mkdir()
    state=p._load(root,'full-motion.socks');config=deepcopy(state['config'])
    geometry=w.read_json(root,'inputs/geometry.json')['poses']
    targets={'run.02':['front'],'run.03':['back'],'run.10':['back'],'run.13':['front'],
        'run.18':['back'],'run.19':['back'],'run.20':['back'],'run.21':['back'],'walk.08':['front'],
        'walk.14':['front'],'walk.15':['front'],'walk.16':['front'],'walk.17':['back','front'],'walk.19':['back']}
    if poses:
        # Further audits can correct only named interfaces instead of repeating
        # a previous whole batch. Explicit side names avoid changing its mate.
        targets={}
        for value in poses:
            pose,side=value.rsplit(':',1)
            if pose not in geometry or side not in ('back','front'):
                raise ValueError('pose must be an existing FRAME:back|front')
            targets.setdefault(pose,[]).append(side)
    yy,xx=np.mgrid[:768,:768];coords=np.stack([xx,yy],-1)
    artwork={};proof=[]
    for pose,sides in targets.items():
        row=state['tracks']['main']['poses'][pose];old=p.candidate_record(root,state,pose,row['selected'])
        pieces={piece['id']:Image.open(root/row['selected']/old['images'][piece['image_key']]['file']).convert('RGBA') for piece in old['independent_layers']}
        for side in sides:
            points=geometry[pose][side];ankle=np.array(points['ankle']);knee=np.array(points.get('cuff_axis_point',points['knee']));axis=(knee-ankle)/np.linalg.norm(knee-ankle)
            along=(coords-ankle)@axis;across=(coords-ankle)@np.array([-axis[1],axis[0]])
            leg_file=root/f'inputs/body-segments/leg_{side}/{pose}.png'
            leg=np.asarray(Image.open(leg_file).getchannel('A'))
            if ankle_bridge:
                near=(leg>=128)&(along>=4)&(along<18)&(np.abs(across)<45)
                upstream=(leg>=128)&(along>=24)&(along<42)&(np.abs(across)<45)
                assert near.any() and upstream.any(),f'{pose}/{side}: annotate the local calf tangent'
                if not annotated_axis:
                    axis=coords[upstream].mean(0)-coords[near].mean(0);axis/=np.linalg.norm(axis)
                along=(coords-ankle)@axis;across=(coords-ankle)@np.array([-axis[1],axis[0]])
                foot=np.asarray(Image.open(root/f'inputs/body-segments/foot_{side}/{pose}.png').getchannel('A'))
                from scripts.dev.parts_full_motion import foot_matrix,head_box
                source_state,source_n=pose.split('.')
                body=Image.open(root/f'inputs/body/{source_state}/{int(source_n):03}.png').convert('RGBA')
                head=head_box(body);matrix=foot_matrix(points,length=88*(head[2]-head[0])/240);inv=np.linalg.inv(matrix)
                uv=coords@inv[:2,:2].T+inv[:2,2]
                # Proximal fabric belongs at the shoe entry, never under its
                # rigid outsole or along the bare foot's longer toe silhouette.
                bridge=(uv[...,0]>160)&(uv[...,0]<700)&(uv[...,1]<600)
                foot=np.where(bridge,foot,0).astype('uint8')
                anatomy=np.maximum(leg,foot)
            else:anatomy=leg
            smooth=np.minimum(anatomy,np.asarray(Image.fromarray(anatomy).filter(ImageFilter.MinFilter(7)).filter(ImageFilter.MaxFilter(7))))
            if exact_anatomy:
                smooth=anatomy
            local=(((coords-ankle)**2).sum(-1)<70**2)&(np.abs(across)<48)&(along>(-22 if ankle_bridge else -12))&(along<32)
            if corridor:
                local &= (across>=corridor[0])&(across<=corridor[1])
            cuff=np.rint(smooth.astype(float)*np.clip((28-along)/2,0,1)*local).astype('uint8')
            if rounded_tube:
                upstream=(leg>=128)&(along>=14)&(along<=28)&(np.abs(across)<45)
                lo,hi=np.quantile(across[upstream],[.05,.95]);center=(lo+hi)/2
                width=np.clip((hi-lo)/2,12,25)
                side_width=width-np.maximum(0,along-20)*.55
                sleeve=np.clip(side_width-np.abs(across-center),0,1)
                cuff=np.rint(cuff.astype(float)*sleeve).astype('uint8')
            if not exact_anatomy:
                cuff=np.minimum(cuff,np.asarray(Image.fromarray(cuff).filter(ImageFilter.MinFilter(5)).filter(ImageFilter.MaxFilter(5))))
            assert np.all(cuff<=anatomy)
            # This explicit mask targets only this retained body calf, not the
            # body as a whole. Below the cuff top, keep only its smooth sleeve
            # contour so a removed acute tip cannot expose a tan skin spur.
            kept=leg.copy();kept[local]=np.minimum(leg[local],smooth[local])
            cover=local&(along<(26 if rounded_tube else 24));kept[cover]=np.minimum(kept[cover],cuff[cover])
            if ankle_bridge:kept=leg.copy() # bridge continuity comes from independent proximal-foot fabric
            keep=np.full(leg.shape,255,dtype='uint8');nonzero=leg>0
            keep[nonzero]=np.minimum(255,np.rint(kept[nonzero].astype(float)*255/leg[nonzero])).astype('uint8')
            mask_file=folder/f'{pose}.{side}.calf-keep.png';Image.fromarray(keep).save(mask_file)
            rgb=np.broadcast_to(np.array([245.,239.,221.]),(*cuff.shape,3)).copy()
            edge=(cuff.astype(float)-np.asarray(Image.fromarray(cuff).filter(ImageFilter.MinFilter(3))))/255
            rgb=rgb*(1-edge[...,None])+np.array([151,138,113])*edge[...,None]
            rgb-=np.clip(1-np.abs(along-26)/1.2,0,1)[...,None]*(cuff>0)[...,None]*20
            arr=np.dstack([np.clip(np.rint(rgb),0,255).astype('uint8'),cuff]);arr[cuff==0]=0
            identity='sock_'+side+'_cuff';pieces[identity]=Image.fromarray(arr)
            layer=next(item for item in config['independent']['layers'] if item['id']==identity)
            layer.setdefault('masks',{})[pose]={'target_node':'leg_'+side,'space':'rig-canvas','channel':'luma',
                'image':{'file':mask_file.relative_to(root).as_posix(),'sha256':a.sha256(mask_file.read_bytes())}}
            proof.append({'pose':pose,'side':side,'body_source_sha256':a.sha256(leg_file.read_bytes()),
                'conditional_calf_pixels_affected':int(((leg>=16)&(keep<255)).sum()),'cuff_outside_source_body_pixels':0,
                'body_source_changed':False,'whole_body_mask':False,'includes_proximal_foot':ankle_bridge,
                'axis_source':'annotated calf' if annotated_axis else 'local source-alpha tangent',
                'corridor':corridor,'exact_anatomy':exact_anatomy,'axis':axis.tolist()})
        required=np.zeros((768,768),dtype='uint8');allowed=required.copy()
        for layer in config['independent']['layers']:
            identity=layer['id'];image=pieces[identity];file=folder/f'{pose}.{identity}.png';image.save(file);artwork[pose,identity]=file
            alpha=np.asarray(image.getchannel('A'));points=np.argwhere(alpha>=16);low=points.min(0);high=points.max(0)+1
            layer['targets'][pose]=[max(0,int(low[1])-4),max(0,int(low[0])-4),min(768,int(high[1])+4),min(768,int(high[0])+4)]
            layer['guides'][pose]=[file.relative_to(root).as_posix()]
            required=np.maximum(required,np.where(alpha>=240,255,0).astype('uint8'))
            allowed=np.maximum(allowed,np.asarray(image.getchannel('A').filter(ImageFilter.MaxFilter(5))))
        for key,alpha in [('required',required),('allowed',allowed)]:
            file=folder/f'{pose}.{key}.png';Image.fromarray(alpha).save(file);config['pose_checks'][pose][key]=file.relative_to(root).as_posix()
    method=('source-bounded calf/proximal-foot bridge restricted to shoe-entry UV; all-KEEP calf masks'
            if ankle_bridge else 'source-bounded cuff contour; sparse calf KEEP masks')
    (folder/'authoring.json').write_bytes(w.json_bytes({'poses':proof,'art_approved':False,'method':method+'; original body/shoe bytes retained'}))
    reason=('전 프레임 관찰에서 남은 접합부를 같은 쪽 calf/proximal-foot alpha와 신발 입구 범위로 교정한다. body를 자르는 픽셀은 0이다.'
            if ankle_bridge else '전 프레임 관찰에서 남은 발목 경계를 지정 calf KEEP 마스크와 cuff intrinsic alpha로 맞춘다.')
    p.request_rework(assembly,'full-motion.socks',list(targets),reason+' 다른 포즈와 발 레이어는 보존한다.')
    file=folder/'config.json';file.write_bytes(w.json_bytes(config));p.configure(assembly,'full-motion.socks',file)
    batch=p.sheet(assembly,'full-motion.socks',poses=list(targets),layers=['sock_back_cuff','sock_front_cuff'])
    pages=[(Path(str(n)),artwork[job['pose'],job['layer']]) for n,job in enumerate(batch['pages']) if not job.get('reuse')]
    sliced=p.slice_batch(assembly,'full-motion.socks',batch=batch['batch'],pages=pages,registered_canvas=True)
    checked=p.check(assembly,'full-motion.socks');p.build(assembly,'full-motion.socks')
    _,index,reg,_=parts_motion._active(assembly)
    selection={'kind':'parts-appearance-selection','schema_version':1,'id':'full-motion.example','selections':{
        slot:c.reference(reg['full-motion.'+name]) for slot,name in [('hair','hair'),('footwear','sneakers'),('socks','socks')]}}
    file=folder/'appearance.json';file.write_bytes(w.json_bytes(selection));parts_appearance.create(assembly,file)
    (folder/'result.json').write_bytes(w.json_bytes({'sliced':sliced,'checked':checked,'art_approved':False}))
    return {'poses':list(targets),'art_approved':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--assembly',type=Path,required=True);parser.add_argument('--revision',required=True);parser.add_argument('--rounded-tube',action='store_true');parser.add_argument('--ankle-bridge',action='store_true');parser.add_argument('--poses',nargs='+');parser.add_argument('--annotated-axis',action='store_true');parser.add_argument('--corridor',nargs=2,type=float);parser.add_argument('--exact-anatomy',action='store_true')
    print(json.dumps(fit(**vars(parser.parse_args()))))
