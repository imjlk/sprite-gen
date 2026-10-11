# SPDX-License-Identifier: Apache-2.0
"""Source-bound full-cycle parts authoring and exhaustive rendered evidence.

The video engine owns extraction and cycle selection. This tool registers its
actual cycle canvases on one parts rig, consumes explicit anatomical annotations,
and bakes independent part images. It never generates outfit-specific clips or
grants art approval. Previous source takes and production revisions stay intact.
"""
import argparse
from copy import deepcopy
from fractions import Fraction
from itertools import product
import json
from pathlib import Path
import shutil
import subprocess

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from sprite_gen.compose import export_parts
from sprite_gen.compose.parts_runtime import PartsPlayer
from sprite_gen.curate import parts_motion, parts_appearance
from sprite_gen.frames import parts_import, parts_production as p
from sprite_gen.spec import parts_assets as a, parts_contract as c, parts_workspace as w


def put(file, value):
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(w.json_bytes(value))


def register_source(out, state, loop, *, engine_strip=False):
    folder = out/'registered'/state
    if folder.exists():
        raise ValueError('preserve the registered source; use a new output')
    folder.mkdir(parents=True)
    files = sorted((loop/'cycle').glob('*.png'))
    report = json.loads((loop.parent/'loop.report.json').read_text())
    assert len(files) == report['cycle']['length'] and len(files) >= 12
    sizes = {Image.open(file).size for file in files}
    assert len(sizes) == 1
    size = sizes.pop(); scale = 768/max(size)
    dims = [round(value*scale) for value in size]
    offset = [(768-value)//2 for value in dims]
    strip = None
    if engine_strip:
        strip_file = next(loop.glob('*.strip.png'))
        meta = json.loads(strip_file.with_suffix('.json').read_text())
        strip = Image.open(strip_file).convert('RGBA')
        assert meta['frames']==len(files) and strip.size==(meta['w']*len(files),meta['h'])
        assert meta['w']<=768 and meta['h']<=720
        first_head=head_box(strip.crop((0,0,meta['w'],meta['h'])))
        offset=[round(384-(first_head[0]+first_head[2])/2),720-meta['h']]
    frames = []
    for n, file in enumerate(files):
        if strip is not None:
            im=strip.crop((n*meta['w'],0,(n+1)*meta['w'],meta['h']))
        else:
            im = Image.open(file).convert('RGBa').resize(dims, Image.Resampling.LANCZOS).convert('RGBA')
        canvas = Image.new('RGBA',(768,768)); canvas.paste(im,tuple(offset))
        target = folder/f'{n:03}.png'; canvas.save(target)
        frames.append({'file':target.name,'state':state,'direction':'right','frame_index':n,
                       'source_frame_id':f'{state}.engine-cycle.{n}',
                       'duration_ticks':1000,'duration':1/24})
    put(folder/'frames.json',{'kind':'sprite-gen-asset','version':1,'frames':frames})
    put(folder/'source.json',{'loop':str(loop),'report_sha256':a.sha256((loop.parent/'loop.report.json').read_bytes()),
        'cycle':report['cycle'],'source_size':size,'scale':scale,'offset':offset,'fps':24,
        'frames':[{'file':f.name,'sha256':a.sha256(f.read_bytes())} for f in files],
        'strip':None if strip is None else {'sha256':a.sha256(strip_file.read_bytes()),'metadata':meta},
        'method':'engine strip pixels placed on rig; no extra resizing' if strip else 'premultiplied uniform canvas registration; no pose simulation', 'art_approved':False})
    boards(folder, state)
    return {'state':state,'frames':len(files),'source_size':size}


def similarity(image, matrix):
    inv=np.linalg.inv(matrix)
    if np.linalg.svd(matrix[:2,:2],compute_uv=False).min()<.25:
        # Point sampling a large source at a tiny shoe scale flickers its edges.
        reduced=image.convert('RGBa').resize((image.width//4,image.height//4),Image.Resampling.LANCZOS)
        raster=reduced.transform((3072,3072),Image.Transform.AFFINE,
            (inv[0,0]/16,inv[0,1]/16,inv[0,2]/4,inv[1,0]/16,inv[1,1]/16,inv[1,2]/4),Image.Resampling.BICUBIC)
        return raster.resize((768,768),Image.Resampling.LANCZOS).convert('RGBA')
    return image.convert('RGBa').transform((768,768),Image.Transform.AFFINE,
        tuple(inv[:2].ravel()),Image.Resampling.BICUBIC).convert('RGBA')


def foot_matrix(points, *, length=88):
    length=points.get('footwear_length',length)
    ankle=np.asarray(points['ankle'],float)
    heel=np.asarray(points.get('footwear_heel',points['heel']),float)
    toe=np.asarray(points.get('footwear_toe',points['toe']),float)
    axis=(toe-heel)/np.linalg.norm(toe-heel);normal=np.array([-axis[1],axis[0]])
    # A video model's bare toes flex and shorten. Never squash the product into
    # that changing triangle. The body's reference size fixes physical length;
    # heel/toe supply sole direction, and the actual ankle fixes the entry.
    matrix=np.eye(3);matrix[:2,:2]=np.stack([axis,normal],axis=1)*(length/1310)
    anchor=points.get('footwear_anchor','ankle')
    if anchor=='heel':
        matrix[:2,2]=heel-matrix[:2,:2]@np.array([120,795])
        weight=points.get('footwear_entry_weight',0)
        assert 0<=weight<=1,'entry fit weight must be bounded'
        entry=matrix[:2,:2]@np.array([410,438])+matrix[:2,2]
        matrix[:2,2]+=(ankle-entry)*weight
    else:
        matrix[:2,2]=ankle-matrix[:2,:2]@np.array([410,438])
    return matrix


def head_box(image):
    alpha=np.asarray(image.getchannel('A'))
    yy,xx=np.nonzero((alpha>=128)&(np.indices(alpha.shape)[0]<390))
    top=int(yy.min())
    left=int(xx[yy<top+180].min());right=int(xx[yy<top+180].max())+1
    width=right-left
    neck=min(390,top+width)
    return [left,top,right,neck]


def line_distance(coords, points):
    answer=np.full(coords.shape[:2],1e9)
    for start,end in zip(points,points[1:]):
        start=np.asarray(start);end=np.asarray(end);axis=end-start
        t=np.clip(((coords-start)@axis)/max(float(axis@axis),1),0,1)
        answer=np.minimum(answer,((coords-start-t[...,None]*axis)**2).sum(axis=-1))
    return answer


def ear_registration(source_body, target_body, matrix):
    """Bounded contour registration for this particular frozen SD source only."""
    from scripts.dev.parts_attachment_author import ear_aperture
    _,_,contour=ear_aperture(source_body,{'body':[344,273],'radius':[26,31]})
    predicted=similarity(contour.convert('RGBA'),matrix).convert('RGB')
    y,x=np.nonzero(np.asarray(predicted)[...,0]>=128)
    assert len(x)>50,'source ear contour lost'
    target=np.asarray(target_body);dark=(target[...,3]>=128)&(target[...,0]<185)&(target[...,1]<125)&(target[...,2]<95)
    scores=[]
    for dy in range(-16,17):
        for dx in range(-12,13):
            hits=dark[np.clip(y+dy,0,767),np.clip(x+dx,0,767)]
            scores.append((float(hits.mean())-.0001*(dx*dx+dy*dy),dx,dy,float(hits.mean())))
    _,dx,dy,score=max(scores)
    assert score>=.40,f'ear source-bound registration uncertain ({score:.2f}); annotate/rework the frame'
    result=matrix.copy();result[:2,2]+=[dx,dy]
    return result,{'offset':[dx,dy],'contour_overlap':round(score,4),'source_bound_search':True}


def prepare_library(out, template, geometry, *, preserve_library=None, preserve_states=(),preserve_poses=(), preserve_ownership=False):
    if preserve_ownership and (not preserve_library or not (preserve_states or preserve_poses)):
        raise ValueError('preserving ownership needs a source library and selected states or poses')
    root=out/'library';assembly=root/'assembly.json'
    if root.exists():raise ValueError('use a fresh library')
    old,reg=w.read_workspace(template/'assembly.json');rig=reg[old['rig']['id']];top=reg[rig['topology']['id']]
    cat=deepcopy(reg[old['catalog']['id']]);cat.pop('digest');cat['presets']=[]
    for slot in cat['slots']:slot.update(options=[],default=None)
    cat=c.seal(cat)
    index={'kind':'parts-workspace','schema_version':1,'id':'full-motion.parts',
        'documents':{rig['id']:'profiles/rig.json',top['id']:'profiles/topology.json',cat['id']:'catalog.json'},
        'rig':c.reference(rig),'catalog':c.reference(cat),'motion':None,'body':[],'appearance':None,
        'states':[s for s in ('walk','run') if (out/'registered'/s).exists()],'fitting':[],'stability':[]}
    for doc in (rig,top,cat):put(root/index['documents'][doc['id']],doc)
    put(assembly,index);shutil.copytree(out/'registered',root/'inputs/body')
    geom=json.loads(geometry.read_text());put(root/'inputs/geometry.json',geom)
    clips=[]
    for state in index['states']:
        folder=root/'inputs/body'/state
        clips.append({'id':state+'.side','state':state,'file':state+'/frames.json','timebase':24000,'fps':'24',
            'loop':True,'source_mode':'external','expected_files':{state+'/'+f.name:a.sha256(f.read_bytes()) for f in folder.glob('[0-9][0-9][0-9].png')}})
        clips[-1]['expected_files'][state+'/frames.json']=a.sha256((folder/'frames.json').read_bytes())
    put(root/'inputs/motion.json',{'kind':'parts-motion-import','schema_version':1,'id':'full-motion.walk-run','rig':index['rig'],'clips':clips})
    result=parts_motion.import_motion(assembly,root/'inputs/motion.json',root/'inputs/body')
    annotations=root/result['annotation_template'];draft=json.loads(annotations.read_text())
    for clip,rows in draft['clips'].items():
        for sample_id,row in rows.items():
            n=int(sample_id.rsplit('.',1)[1])
            row.update(pose_id=f'{clip.split(".")[0]}.{n:02}',sockets={'root':{'position':[0,0],'visible':True,'confidence':'verified'}})
    put(annotations,draft);parts_motion.freeze_motion(assembly,result['candidate'],annotations)
    _,index,reg,_=parts_motion._active(assembly)
    for clip in reg[index['motion']['id']]['clips']:
        assert [sample['pose_id'] for sample in clip['samples']]==[
            f'{clip["state"]}.{n:02}' for n in range(len(clip['samples']))
        ], 'sample IDs must bind poses in numeric timeline order'
    nodes=['root','torso','head','leg_back','foot_back','leg_front','foot_front'];maps={k:{} for k in nodes}
    yy,xx=np.mgrid[:768,:768];coords=np.stack([xx,yy],axis=-1)
    proofs=[]; folder=root/'inputs/body-segments'
    for pose,values in geom['poses'].items():
        state,n=pose.split('.');body=Image.open(root/f'inputs/body/{state}/{int(n):03}.png').convert('RGBA');pixels=np.asarray(body)
        assert a.sha256((root/f'inputs/body/{state}/{int(n):03}.png').read_bytes())==values['body_sha256'],pose
        head=head_box(body); owners=np.ones((768,768),dtype='uint8');owners[yy<head[3]]=nodes.index('head')
        hip=np.array([(head[0]+head[2])/2-12,head[3]+150]);distances=[]
        for side in ('back','front'):
            points=values[side]
            distances.append(line_distance(coords,[hip,points['knee'],points['ankle'],points['heel'],points['toe']]))
        for j,side in enumerate(('back','front')):
            points=values[side];lower=(yy>hip[1]-15)&(distances[j]<=distances[1-j])
            owners[lower]=nodes.index('leg_'+side)
        for side,polygons in values.get('leg_owner_polygons',{}).items():
            for polygon in polygons:
                region=Image.new('1',(768,768));ImageDraw.Draw(region).polygon([tuple(q) for q in polygon],fill=1)
                owners[np.asarray(region)]=nodes.index('leg_'+side)
        for side in ('back','front'):
            points=values[side];lower=owners==nodes.index('leg_'+side)
            knee=np.array(points['knee']);ankle=np.array(points['ankle']);axis=(knee-ankle)/np.linalg.norm(knee-ankle)
            along=(coords-ankle)@axis
            heel=np.array(points['heel']);toe=np.array(points['toe']);sole=(toe-heel)/np.linalg.norm(toe-heel)
            forward=(coords-heel)@sole
            # A flexed heel, as well as a raised toe, may lie above the calf
            # cut plane. Authored sole landmarks identify the entire foot;
            # keeping only the toe tip leaves bare heel fragments at the shoe.
            sole_zone=(forward>-12)&(line_distance(coords,[heel,toe])<22**2)
            toe_zone=(forward>(ankle-heel)@sole+8)&(line_distance(coords,[heel,toe])<45**2)
            distal=lower&((along < -4)|sole_zone|toe_zone)&(((coords-ankle)**2).sum(axis=-1)<130**2)
            owners[distal]=nodes.index('foot_'+side)
        images={}
        for node in nodes:
            arr=np.where((owners==nodes.index(node))[...,None],pixels,0).astype('uint8')
            if preserve_library and (state in preserve_states or pose in preserve_poses):
                preserved=preserve_library/f'inputs/body-segments/{node}/{pose}.png'
                arr=np.asarray(Image.open(preserved).convert('RGBA'))
                if preserve_ownership:
                    # A common-body RGB revision must retain the reviewed
                    # ownership, including manual crossing/calf corrections.
                    assert np.all((arr[...,3] == 0) | (arr[...,3] == pixels[...,3]))
                    arr=np.where((arr[...,3]>0)[...,None],pixels,0).astype('uint8')
            target=folder/node/(pose+'.png');target.parent.mkdir(parents=True,exist_ok=True);im=Image.fromarray(arr);im.save(target);images[node]=im
            maps[node][pose]={'image':{'file':target.relative_to(folder).as_posix(),'sha256':a.sha256(target.read_bytes())},'format':'full-canvas','pivot':[0,0],'target_socket':'root','visible':bool(im.getbbox())}
        merged=Image.new('RGBA',body.size)
        for node in nodes:merged.alpha_composite(images[node])
        assert np.array_equal(np.asarray(merged),pixels),'ownership changed body RGBA'
        opaque=np.argwhere(pixels[...,3]>=128)[:,::-1]
        for side in ('back','front'):
            for landmark in ('heel','toe'):
                if values[side].get('foot_fully_occluded') or values[side].get(landmark+'_visible') is False:
                    continue
                point=np.array(values[side][landmark]);distance=float(np.sqrt(((opaque-point)**2).sum(-1).min()))
                assert distance<=8,(pose,side,landmark,'outside actual source body',round(distance,3))
            if values[side].get('toe_visible') is False:
                continue
            point=np.array(values[side]['toe']);nearest=opaque[((opaque-point)**2).sum(-1).argmin()]
            assert images['foot_'+side].getpixel(tuple(map(int,nearest)))[3]>=128,(pose,side,'toe belongs to a non-foot node')
        proofs.append({'pose':pose,'head':head,'body_pixel_identical':True,'front_back_method':'authored source-bound lower-limb centerlines; head cutoff; upper body retained together'})
    spec={'kind':'parts-pack-import','schema_version':1,'id':'full-motion.body','design_id':'qualified-sd-motion-body','role':'body','body_mode':'segmented',
        'style':rig['style'],'rig':index['rig'],'motion':index['motion'],'view':rig['view'],'binding_id':'full-motion.body.binding','states':index['states'],'occupies':[],
        'pieces':[{'id':node,'node':node,'slot':None,'provides':[node],'mode':'raster','pose_map':maps[node]} for node in nodes]}
    put(folder/'import.json',spec);parts_import.import_pack(assembly,folder/'import.json',folder)
    put(root/'inputs/body-authoring.json',{'poses':proofs,'art_approved':False,'upper_body_subdivision_reviewed':False})
    return {'assembly':str(assembly),'frames':len(proofs)}


def author_parts(out, template, master, *, revision='parts-r1', only=None, defer_check=False,
                 preserve_library=None, preserve_states=(),regenerate_poses=()):
    from scripts.dev.parts_sock_author import attached_cuff
    from sprite_gen.frames.parts_segments import _components
    assembly=out/'library/assembly.json';root,index,reg,_=parts_motion._active(assembly)
    folder=root/'inputs'/revision
    if folder.exists():raise ValueError('preserve old part revision')
    folder.mkdir(parents=True);shutil.copy2(master,folder/'shoe-native.png')
    hair_source=template/'inputs/attachments-r9/run.0.hair.png'
    shutil.copy2(hair_source,folder/'hair-native-retouched.png')
    material=template/'inputs/socks-r4/native-material.png';shutil.copy2(material,folder/'sock-material.png')
    hair=Image.open(hair_source).convert('RGBA');shoe=Image.open(master).convert('RGBA')
    geom=w.read_json(root,'inputs/geometry.json')
    yy,xx=np.mgrid[:768,:768];coords=np.stack([xx,yy],axis=-1)
    layers={'hair':[{'id':'hair','node':'hair','provides':['hair'],'body_nodes':['head']}],
        'sneakers':[{'id':'shoe_'+s,'node':'shoe_'+s,'provides':['foot_'+s],'replaces':['foot_'+s],
                     'body_nodes':['foot_'+s,'leg_'+s]} for s in ('back','front')],
        'socks':[{'id':'sock_'+s+'_'+kind,'node':'sock_'+s,'body_nodes':['leg_'+s,'foot_'+s],
            **({'provides':['sock_'+s]} if kind=='cuff' else {'provides':['foot_'+s],'replaces':['foot_'+s],
                'cover_rule':{'tag':'footwear.closed-foot','show':'uncovered'}})} for s in ('back','front') for kind in ('cuff','foot')]}
    for values in layers.values():
        for layer in values:layer.update(targets={},source_bounds={},guides={},landmarks={},masks={})
    files={};proof=[];checks={name:{} for name in layers}
    preserved_states={name:p._load(preserve_library,'full-motion.'+name) for name in layers} if preserve_library else {}
    for pose,values in geom['poses'].items():
        state,n=pose.split('.');body=Image.open(root/f'inputs/body/{state}/{int(n):03}.png').convert('RGBA')
        preserved={}
        if preserve_library and state in preserve_states:
            for name,old in preserved_states.items():
                row=old['tracks']['main']['poses'][pose]
                record=p.candidate_record(preserve_library,old,pose,row['selected'])
                for layer in record['independent_layers']:
                    ref=record['images'][layer['image_key']]
                    im=Image.open(preserve_library/row['selected']/ref['file']).convert('RGBA')
                    assert im.size==(768,768),(pose,layer['id'],'preserved candidate must use rig canvas')
                    preserved[layer['id']]=im
        head=head_box(body);scale=(head[2]-head[0])/240
        matrix=np.eye(3);matrix[:2,:2]*=scale;matrix[:2,2]=np.array(head[:2])-np.array([257,101])*scale
        # One clean source palette/strand design; source-bound frame registration.
        matrix,ear=ear_registration(Image.open(template/'inputs/body/000.png').convert('RGBA'),body,matrix)
        ear_pivot=np.array([344,273]);fixed=matrix[:2,:2]@ear_pivot+matrix[:2,2]
        matrix[:2,:2]*=1.025;matrix[:2,2]=fixed-matrix[:2,:2]@ear_pivot
        hair_im=similarity(hair,matrix)
        made={'hair':hair_im};source_meta={'head':head,'hair_matrix':matrix.tolist(),'ear_registration':ear,'feet':{}}
        for side in ('back','front'):
            values_side=values[side];matrix=foot_matrix(values_side,length=88*(head[2]-head[0])/240);raster=similarity(shoe,matrix)
            inv=np.linalg.inv(matrix);uv=coords@inv[:2,:2].T+inv[:2,2]
            leg=np.asarray(Image.open(root/f'inputs/body-segments/leg_{side}/{pose}.png').getchannel('A'))
            foot=np.asarray(Image.open(root/f'inputs/body-segments/foot_{side}/{pose}.png').getchannel('A'))
            if values_side.get('foot_fully_occluded'):
                assert foot.max()<16,'declared hidden foot still contains visible source anatomy'
                foot=np.zeros_like(foot)
            knee=np.array(values_side.get('cuff_axis_point',values_side['knee']));ankle=np.array(values_side['ankle']);axis=(knee-ankle)/np.linalg.norm(knee-ankle)
            along=(coords-ankle)@axis;across=(coords-ankle)@np.array([-axis[1],axis[0]])
            local=((coords-ankle)**2).sum(axis=-1)<70**2
            # Overlapping legs can share a connected silhouette. Stay within
            # this calf's annotated corridor; connectivity alone cannot decide.
            corridor=np.clip(values_side.get('cuff_half_width',30)-np.abs(across),0,1)*np.clip(along+10,0,1)
            cuff=np.rint(leg.astype(float)*np.clip(28-along+.5,0,1)*local*corridor).astype('uint8')
            if preserved and pose not in regenerate_poses:
                cuff=np.asarray(preserved['sock_'+side+'_cuff'].getchannel('A'));dropped=0
            elif values_side.get('cuff_fully_occluded'):
                dropped=int((cuff>0).sum());cuff=np.zeros_like(cuff)
            else:
                try:cuff,dropped=attached_cuff(cuff,foot)
                except AssertionError as exc:raise ValueError(f'{pose}/{side}: {exc}') from exc
            alpha=np.maximum(cuff,foot);rgb=np.broadcast_to(np.array([245.,239.,221.]),(*alpha.shape,3)).copy()
            edge=(alpha.astype(float)-np.asarray(Image.fromarray(alpha).filter(ImageFilter.MinFilter(3))))/255
            edge=np.clip(edge,0,1);rgb=rgb*(1-edge[...,None])+np.array([151,138,113])*edge[...,None]
            hem=np.clip(1-np.abs(along-27)/1.1,0,1)*(cuff>0);rgb-=hem[...,None]*27
            ribs=(np.sin(across*np.pi/3)*1.0)*(cuff>0);rgb+=ribs[...,None]
            rgb=np.clip(np.rint(rgb),0,255).astype('uint8')
            for kind,opacity in (('cuff',cuff),('foot',foot)):
                data=np.dstack([rgb,opacity]);data[opacity==0]=0;made['sock_'+side+'_'+kind]=Image.fromarray(data)
                if values_side.get(kind+'_fully_occluded'):
                    next(l for l in layers['socks'] if l['id']=='sock_'+side+'_'+kind).setdefault('visible',{})[pose]=False
            made['shoe_'+side]=raster
            # Only a rear collar lip over a retained leg is opened. No body mask.
            lip=(uv[...,0]<410)&(uv[...,1]>345)&(uv[...,1]<465)
            mask=Image.fromarray(255-np.rint(leg.astype(float)*lip).astype('uint8'))
            maskfile=folder/f'{pose}.shoe_{side}.keep.png';mask.save(maskfile)
            layer=next(l for l in layers['sneakers'] if l['id']=='shoe_'+side)
            layer.setdefault('straight_edges',{})[pose]=[{'id':'rigid-outsole','start':np.rint(matrix[:2,:2]@np.array([180,795])+matrix[:2,2]).astype(int).tolist(),
                'end':np.rint(matrix[:2,:2]@np.array([1260,795])+matrix[:2,2]).astype(int).tolist(),'side':'positive','span':['3/20','4/5'],'max_deviation':'2'}]
            layer['masks'][pose]={'target_node':'foot_'+side,'image':{'file':maskfile.relative_to(root).as_posix(),'sha256':a.sha256(maskfile.read_bytes())},'space':'rig-canvas','channel':'luma'}
            layer['landmarks'][pose]={k:values_side[k] for k in ('ankle','heel','toe')}
            source_meta['feet'][side]={'matrix':matrix.tolist(),'cuff_detached_pixels_removed':dropped,
                'ankle':values_side['ankle'],'calf_axis':axis.tolist(),'sock_cuff_pixels':int((cuff>=16).sum()),
                'sock_foot_pixels':int((foot>=16).sum())}
        if preserved:
            if pose in regenerate_poses:
                preserved={key:image for key,image in preserved.items() if key=='hair'}
            for side in ('back','front'):
                if values[side].get('footwear_entry_weight',0)>0:
                    preserved.pop('shoe_'+side,None)
            made.update(preserved)
            source_meta['preserved_layers_pixel_identical']=sorted(preserved)
            source_meta['preserved_library']=str(preserve_library)
            for name,part_layers in layers.items():
                old_layers={l['id']:l for l in preserved_states[name]['config']['independent']['layers']}
                for layer in part_layers:
                    old=old_layers[layer['id']]
                    if pose in regenerate_poses and layer['id']!='hair':
                        continue
                    if layer['id'].startswith('shoe_') and values[layer['id'].split('_')[1]].get('footwear_entry_weight',0)>0:
                        continue
                    for key in ('straight_edges','landmarks'):
                        if pose in old.get(key,{}):layer.setdefault(key,{})[pose]=deepcopy(old[key][pose])
                    if pose in old.get('visible',{}):layer.setdefault('visible',{})[pose]=old['visible'][pose]
                    mask=old.get('masks',{}).get(pose)
                    if mask:
                        mask=deepcopy(mask);file=folder/f'{pose}.{layer["id"]}.preserved-mask.png'
                        shutil.copy2(preserve_library/mask['image']['file'],file)
                        assert a.sha256(file.read_bytes())==mask['image']['sha256']
                        mask['image']['file']=file.relative_to(root).as_posix();layer['masks'][pose]=mask
        for name,part_layers in layers.items():
            required=np.zeros((768,768),dtype='uint8');allowed=required.copy()
            for layer in part_layers:
                image=made[layer['id']];alpha=np.asarray(image.getchannel('A'))
                points=np.argwhere(alpha>=16)
                if len(points)==0:
                    side=layer['id'].split('_')[1]
                    occluded=(layer['id'].endswith('_cuff') and values[side].get('cuff_fully_occluded') or
                              layer['id'].endswith('_foot') and values[side].get('foot_fully_occluded'))
                    assert occluded,f'empty {pose}/{layer["id"]}: correct body ownership'
                    origin=np.asarray(values[side]['ankle']);low=(origin-40)[::-1];high=(origin+40)[::-1]
                else:low=points.min(axis=0);high=points.max(axis=0)+1
                layer['targets'][pose]=[max(0,int(low[1])-4),max(0,int(low[0])-4),min(768,int(high[1])+4),min(768,int(high[0])+4)]
                file=folder/f'{pose}.{layer["id"]}.png';image.save(file);files[pose,layer['id']]=file
                layer['source_bounds'][pose]=[0,0,768,768];layer['guides'][pose]=[file.relative_to(root).as_posix()]
                allowed=np.maximum(allowed,np.asarray(image.getchannel('A').filter(ImageFilter.MaxFilter(5))))
                if name=='socks':required=np.maximum(required,np.where(alpha>=240,255,0).astype('uint8'))
            if name=='hair':
                # Independently supplied body crown band, not the generated hair silhouette.
                ba=np.asarray(body.getchannel('A'));required[head[1]+4:head[1]+14,head[0]+65:head[2]-65]=ba[head[1]+4:head[1]+14,head[0]+65:head[2]-65]
            checks[name][pose]={}
            for key,mask in (('required',required),('allowed',allowed)):
                f=folder/f'{pose}.{name}.{key}.png';Image.fromarray(mask).save(f);checks[name][pose][key]=f.relative_to(root).as_posix()
        proof.append({'pose':pose,**source_meta})
    authored={'poses':proof,'checks':{},'method':{'hair':'registered native edited source',
        'sneakers':'native simplified master, rigid uniform scale/rotation, ankle entry and source-bound sole direction',
        'socks':'procedural anatomy-bound cotton draft; native material reference, not sampled body RGB'},
        'changed_parts':[],'art_approved':False,'runtime_warp':False,'appearance_specific_clips':0}
    put(folder/'authoring.json',authored)
    summaries={}
    for name,part_layers in layers.items():
        if only and name not in only:continue
        identity='full-motion.'+name
        config={'kind':'parts-production-config','schema_version':1,'part_id':identity,'design_id':identity,'slot':{'sneakers':'footwear','socks':'socks','hair':'hair'}[name],
            'node':part_layers[0]['node'],'socket':'root','states':index['states'],'visibility':'zmap',
            'prompt':{'text':{'hair':'Independent chestnut SD hair; fit this exact head and ear per frame without skin or face.',
                'sneakers':'Independent compact navy/cream SD sneakers; three simple lace crossings, straight rigid rubber sole, heel/toe/ankle fitted to the frame. No body pixels.',
                'socks':'Independent cream socks hugging the exact calf and foot; cuff follows calf direction; no body RGB.'}[name],
                'refs':[(folder/('shoe-native.png' if name=='sneakers' else 'hair-native-retouched.png' if name=='hair' else 'sock-material.png')).relative_to(root).as_posix()]},
            'independent':{'layers':part_layers,'require_vision':True,'reference_previous':False},'pose_checks':checks[name]}
        if name=='sneakers':config['covers']=['footwear.closed-foot']
        f=folder/f'{identity}.config.json';put(f,config)
        if (root/'productions'/identity/'production.json').exists():p.configure(assembly,identity,f)
        else:p.init(assembly,f)
        batch=p.sheet(assembly,identity,poses=list(geom['poses']))
        pages=[(Path(str(n)),files[job['pose'],job['layer']]) for n,job in enumerate(batch['pages'])]
        p.slice_batch(assembly,identity,batch=batch['batch'],pages=pages,registered_canvas=True)
        if defer_check:summaries[name]={'status':'pending'}
        else:summaries[name]=p.check(assembly,identity);p.build(assembly,identity)
    _,index,reg,_=parts_motion._active(assembly)
    if not defer_check:
        selection={'kind':'parts-appearance-selection','schema_version':1,'id':'full-motion.example',
            'selections':{slot:c.reference(reg['full-motion.'+name]) for slot,name in [('hair','hair'),('footwear','sneakers'),('socks','socks')]}}
        put(folder/'appearance.json',selection);parts_appearance.create(assembly,folder/'appearance.json')
    authored.update(checks=summaries,changed_parts=list(summaries));put(folder/'authoring.json',authored)
    return {'parts':list(summaries),'poses':len(proof),'approval':False}


def validate_build(out):
    assembly=out/'library/assembly.json';root,index,reg,_=parts_motion._active(assembly)
    checks={}
    for name in ('hair','sneakers','socks'):
        identity='full-motion.'+name;checks[name]=p.check(assembly,identity);p.build(assembly,identity)
    _,index,reg,_=parts_motion._active(assembly)
    selection={'kind':'parts-appearance-selection','schema_version':1,'id':'full-motion.example',
        'selections':{slot:c.reference(reg['full-motion.'+name]) for slot,name in [('hair','hair'),('footwear','sneakers'),('socks','socks')]}}
    file=root/'inputs/validated-appearance.json';put(file,selection);parts_appearance.create(assembly,file)
    put(root/'inputs/validation.json',checks)
    return {'parts':list(checks),'art_approved':False}


def evidence(out, destination):
    assembly=out/'library/assembly.json';root,index,reg,_=parts_motion._active(assembly)
    if destination.exists():raise ValueError('preserve the previous evidence')
    destination.mkdir(parents=True);(destination/'frames').mkdir()
    player=PartsPlayer.from_workspace(assembly);motion=reg[index['motion']['id']]
    _,manifest,bundle=export_parts.pack(root,index,reg,interpolation='frame')
    ids={'hair':'full-motion.hair','footwear':'full-motion.sneakers','socks':'full-motion.socks'}
    bridge_file=root/'inputs/sole-bounded-bridge-r1/authoring.json'
    if (root/'inputs/ankle-continuity-active.json').exists():
        active=json.loads((root/'inputs/ankle-continuity-active.json').read_text())
        bridge_file=root/active['authoring']
        assert a.sha256(bridge_file.read_bytes())==active['sha256'],'changed ankle continuity authoring'
    bridge_guides=json.loads(bridge_file.read_text())['poses'] if bridge_file.exists() else []
    keys=('sample_id','sample_tick','clip_tick','elapsed_seconds','sample_fraction')
    layer_keys=('id','position','anchor','local_origin','tweened')
    cases=[];expected=[];poses=[];bare_count=0
    for clip in motion['clips']:
        state=clip['state'];player.set_state(state);tick=0
        contact=Image.new('RGB',(1536,384*((len(clip['samples'])+3)//4)),(235,235,235));draw=ImageDraw.Draw(contact)
        for n,sample in enumerate(clip['samples']):
            pose=sample['pose_id'];ms=(tick*1000+clip['timebase']-1)//clip['timebase']+1
            player.seek(Fraction(ms,1000));clocks=[]
            for choices in product((False,True),repeat=3):
                selected={slot:identity if enabled else '' for (slot,identity),enabled in zip(ids.items(),choices)}
                player.set_selection({slot:c.reference(reg[identity]) if enabled else None for (slot,identity),enabled in zip(ids.items(),choices)});scene=player.layers(interpolate=False)
                assert scene['sample_id']==sample['id'];clocks.append(tuple(scene[k] for k in keys))
                expected.append({k:scene[k] for k in keys}|{'layers':[{k:layer[k] for k in layer_keys} for layer in scene['layers']],
                    'masks':[{**{k:m[k] for k in ('target_node','space','channel','origin')},'texture_sha256':m['image']['sha256']} for m in scene['masks']]})
                cases.append({'state':state,'time':ms,'selected':selected})
                body_feet={f'full-motion.body.foot_{side}' for side in ('back','front')
                    if np.asarray(Image.open(root/f'inputs/body-segments/foot_{side}/{pose}.png').getchannel('A')).max()>=16}
                draw_ids={l['render_id'] for l in scene['layers']}
                if choices[1] or choices[2]:assert not body_feet&draw_ids
                else:
                    assert body_feet<=draw_ids
                if choices[2]:
                    geometry=w.read_json(root,'inputs/geometry.json')['poses'][pose]
                    assert {'full-motion.socks.sock_'+s+'_cuff' for s in ('back','front')
                        if not geometry[s].get('cuff_fully_occluded')}<=draw_ids
                    sock_feet={'full-motion.socks.sock_'+s+'_foot' for s in ('back','front')}
                    visible_sock_feet={f'full-motion.socks.sock_{s}_foot' for s in ('back','front')
                        if not geometry[s].get('foot_fully_occluded')}
                    assert not sock_feet&draw_ids if choices[1] else visible_sock_feet<=draw_ids
                if not any(choices):
                    bare,_=player.frame();source=Image.open(root/f'inputs/body/{state}/{n:03}.png').convert('RGBA')
                    assert np.array_equal(np.asarray(bare),np.asarray(source)),pose;bare_count+=1
                if all(choices):
                    actual,_=player.frame();actual.save(destination/'frames'/(pose+'.png'))
                    calf_guard=root/f'inputs/body-segments/{pose}.protected-calf.png'
                    if calf_guard.exists():
                        required=np.asarray(Image.open(calf_guard).convert('L'))>=128
                        missing=int((required&(np.asarray(actual)[...,3]<16)).sum())
                        assert missing==0,(pose,'foot replacement erased protected calf',missing)
                    else:missing=None
                    ankle_missing=0
                    for guide in bridge_guides:
                        if guide['pose']!=pose:continue
                        required=np.asarray(Image.open(root/guide['required']).convert('L'))>=128
                        ankle_missing+=int((required&(np.asarray(actual)[...,3]<16)).sum())
                    assert ankle_missing==0,(pose,'closed-foot layering erased required ankle interface',ankle_missing)
                    tile=actual.resize((384,384),Image.Resampling.LANCZOS);xy=(n%4*384,n//4*384)
                    contact.paste(tile,xy,tile);draw.text((xy[0]+5,xy[1]+5),pose,fill='black')
                    states={identity:p._load(root,identity) for identity in ids.values()}
                    reviews={identity:{'selected':s['tracks']['main']['poses'][pose]['selected'],
                        'decision':p.effective_decision(root,index,s,pose),'check':s['tracks']['main']['poses'][pose].get('check')} for identity,s in states.items()}
                    joints=[]
                    for side in ('back','front'):
                        entries={}
                        for identity,piece in [('full-motion.socks','sock_'+side+'_cuff'),('full-motion.sneakers','shoe_'+side)]:
                            s=states[identity];row=s['tracks']['main']['poses'][pose];record=p.candidate_record(root,s,pose,row['selected']);ref=record['images']['piece.'+piece]
                            entries[piece]=Image.open(root/row['selected']/ref['file']).convert('RGBA')
                        cuff=np.asarray(entries['sock_'+side+'_cuff'])[...,3]
                        leg=np.asarray(Image.open(root/f'inputs/body-segments/leg_{side}/{pose}.png').getchannel('A'))
                        foot=np.asarray(Image.open(root/f'inputs/body-segments/foot_{side}/{pose}.png').getchannel('A'))
                        assert np.all(cuff<=np.maximum(leg,foot)),(pose,side,'cuff floats outside its calf/ankle anatomy')
                        points=w.read_json(root,'inputs/geometry.json')['poses'][pose][side]
                        if points.get('footwear_anchor')=='heel' and bridge_guides:
                            body=Image.open(root/f'inputs/body/{state}/{n:03}.png').convert('RGBA');head=head_box(body)
                            inverse=np.linalg.inv(foot_matrix(points,length=88*(head[2]-head[0])/240))
                            cy,cx=np.mgrid[:768,:768];uv=np.stack([cx,cy],-1)@inverse[:2,:2].T+inverse[:2,2]
                            leaked=int(((cuff>=128)&(leg<128)&(uv[...,1]>=795)).sum())
                            assert leaked==0,(pose,side,'foot-sourced sock material below shoe outsole',leaked)
                        else:leaked=None
                        shoe_alpha=np.asarray(entries['shoe_'+side])[...,3]
                        joints.append({'side':side,'cuff_outside_body_pixels':0,'sock_foot_pixels_below_outsole':leaked,
                                       'cuff_raw_shoe_overlap_pixels':int(((cuff>=128)&(shoe_alpha>=128)).sum())})
                    poses.append({'pose':pose,'body_pixel_identical':True,'reviews':reviews,'joints':joints,
                                  'protected_calf_missing_pixels':missing,'required_ankle_missing_pixels':ankle_missing,
                                  'visual_status':'unreviewed'})
            assert len(set(clocks))==1;tick+=sample['duration_ticks']
        contact.save(destination/(state+'-all-frames.png'))
        for start in range(0,len(clip['samples']),6):
            subset=clip['samples'][start:start+6]
            board=Image.new('RGB',(1560,360*len(subset)),(235,235,235));d=ImageDraw.Draw(board)
            for k,sample in enumerate(subset):
                pose=sample['pose_id'];n=start+k;body=Image.open(root/f'inputs/body/{state}/{n:03}.png').convert('RGBA');actual=Image.open(destination/'frames'/(pose+'.png')).convert('RGBA')
                for column,image in enumerate((body,actual)):
                    im=image.resize((260,260),Image.Resampling.LANCZOS);board.paste(im,(column*260,k*360+35),im)
                geometry=w.read_json(root,'inputs/geometry.json')['poses'][pose]
                for j,side in enumerate(('back','front')):
                    q=geometry[side];pts=np.array([q['ankle'],q['heel'],q['toe']]);low=np.floor(pts.min(0)-50).astype(int);high=np.ceil(pts.max(0)+50).astype(int)
                    for column,image in enumerate((body,actual)):
                        roi=image.crop((*low,*high));roi.thumbnail((250,300));board.paste(roi,(520+j*520+column*260,k*360+35),roi)
                d.text((5,k*360+5),pose+'  body | actual | bare back | actual back | bare front | actual front',fill='black')
            board.save(destination/f'{state}-audit-{start:02}.png')
    # Loop closure is sampled explicitly, not inferred from one still contact.
    wraps=[]
    for clip in motion['clips']:
        player.set_state(clip['state']);player.set_selection({s:c.reference(reg[i]) for s,i in ids.items()});player.seek(Fraction(sum(x['duration_ticks'] for x in clip['samples']),clip['timebase']))
        assert player.current()[1]['id']==clip['samples'][0]['id'];wraps.append(clip['state'])
    inputs=destination/'runtime-cases.json';put(inputs,{'bundle':bundle,'cases':cases})
    js="""const fs=require('fs'),{LayerRuntime}=require(process.argv[1]);const input=JSON.parse(fs.readFileSync(process.argv[2])),r=new LayerRuntime(input.bundle);const keys=['sample_id','sample_tick','clip_tick','elapsed_seconds','sample_fraction'];process.stdout.write(JSON.stringify(input.cases.map(c=>{const s=r.scene(c.state,c.time,c.selected);return {...Object.fromEntries(keys.map(k=>[k,s[k]])),layers:s.layers.map(l=>Object.fromEntries(['id','position','anchor','local_origin','tweened'].map(k=>[k,l[k]]))),masks:s.masks.map(m=>({...Object.fromEntries(['target_node','space','channel','origin'].map(k=>[k,m[k]])),texture_sha256:m.texture.id}))};})));"""
    actual=subprocess.run(['/opt/homebrew/bin/node','-e',js,str(export_parts.STATIC/'runtime.js'),str(inputs)],capture_output=True,text=True,check=True)
    assert json.loads(actual.stdout)==expected
    result={'body':index['body'],'motion':index['motion'],'appearance':index['appearance'],'parts':{i:c.reference(reg[i]) for i in ids.values()},
        'frames':len(poses),'combinations':8,'runtime_cases':len(cases),'python_js_clock_layer_masks_equal':True,
        'bare_body_exact_cases':bare_count,'wrap_checked':wraps,'appearance_clips_baked':manifest['appearance_clips_baked'],
        'art_approved':False,'release_ready':False,'poses':poses,'visual_review_scope':'pending; these technical checks do not certify footwear drawing or full-motion aesthetics'}
    assert result['appearance_clips_baked']==0;put(destination/'evidence.json',result)
    return {k:v for k,v in result.items() if k not in ('poses','parts')}


def boards(folder, state):
    files=sorted(folder.glob('[0-9][0-9][0-9].png'))
    contact=Image.new('RGB',(1536,384*((len(files)+3)//4)),(235,235,235));d=ImageDraw.Draw(contact)
    for n,file in enumerate(files):
        im=Image.open(file).convert('RGBA').resize((384,384),Image.Resampling.LANCZOS)
        contact.paste(im,(n%4*384,n//4*384),im);d.text((n%4*384+8,n//4*384+8),f'{state}.{n:02}',fill='black')
    contact.save(folder/'all-source.png')
    for start in range(0,len(files),6):
        board=Image.new('RGB',(1200,680),(235,235,235));draw=ImageDraw.Draw(board)
        for k,file in enumerate(files[start:start+6]):
            im=Image.open(file).convert('RGBA').crop((200,400,600,720))
            x=k%3*400;y=k//3*340+20;board.paste(im,(x,y),im)
            draw.text((x+5,y-16),f'{state}.{start+k:02}  origin=(200,400)',fill='black')
            for q in range(250,600,50):
                draw.line((x+q-200,y,x+q-200,y+320),fill=(190,195,200));draw.text((x+q-200+2,y+2),str(q),fill=(90,90,90))
            for q in range(450,720,50):
                draw.line((x,y+q-400,x+400,y+q-400),fill=(190,195,200));draw.text((x+2,y+q-400),str(q),fill=(90,90,90))
        board.save(folder/f'feet-{start:02}.png')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--stage',choices=['register','library','parts','build','evidence'],default='register')
    parser.add_argument('--state',choices=['walk','run']); parser.add_argument('--loop',type=Path)
    parser.add_argument('--engine-strip',action='store_true');parser.add_argument('--geometry',type=Path)
    parser.add_argument('--template',type=Path);parser.add_argument('--master',type=Path);parser.add_argument('--revision',default='parts-r1');parser.add_argument('--only',nargs='+')
    parser.add_argument('--defer-check',action='store_true')
    parser.add_argument('--preserve-library',type=Path);parser.add_argument('--preserve-states',nargs='*',default=[])
    parser.add_argument('--preserve-ownership',action='store_true')
    parser.add_argument('--preserve-poses',nargs='*',default=[]);parser.add_argument('--regenerate-poses',nargs='*',default=[])
    parser.add_argument('--evidence',type=Path)
    args=parser.parse_args()
    if args.stage=='register':result=register_source(args.out,args.state,args.loop,engine_strip=args.engine_strip)
    elif args.stage=='library':result=prepare_library(args.out,args.template,args.geometry,preserve_library=args.preserve_library,preserve_states=args.preserve_states,preserve_poses=args.preserve_poses,preserve_ownership=args.preserve_ownership)
    elif args.stage=='parts':result=author_parts(args.out,args.template,args.master,revision=args.revision,only=args.only,defer_check=args.defer_check,preserve_library=args.preserve_library,preserve_states=args.preserve_states,regenerate_poses=args.regenerate_poses)
    elif args.stage=='build':result=validate_build(args.out)
    else:result=evidence(args.out,args.evidence)
    print(json.dumps(result))
