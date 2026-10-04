# SPDX-License-Identifier: Apache-2.0
"""Whole body frame + eye, hair and shirt overlays. No body decomposition.

Assets in parts-source/ are aligned once to the base reference. Playback keeps
the body raster intact, picks its shared video frame, and translates the other
three images by head/torso offsets. No replacement, masks, material projection,
limb solver, per-frame texture deformation or appearance clip baking.
"""
from copy import deepcopy
from fractions import Fraction
from itertools import product
import argparse
import json
from pathlib import Path

from PIL import Image
from sprite_gen._deps import np
from sprite_gen.compose.parts_runtime import PartsPlayer
from sprite_gen.curate import parts_appearance, parts_motion
from sprite_gen.frames import parts_import
from sprite_gen.spec import parts_assets as assets, parts_contract as contract, parts_workspace as workspace

PARTS={'eyes':('eyes','head'),'hair':('hair','head'),'upper':('shirt','torso')}


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(workspace.json_bytes(value));return path


def tracks(image):
    """Read two attachment positions; this never alters or splits the image."""
    a=np.asarray(image);mask=a[...,3]>0;mask[64:]=False
    best=[];seen=np.zeros(mask.shape,dtype=bool)
    for y,x in zip(*np.where(mask)):
        if seen[y,x]:continue
        stack=[(int(y),int(x))];seen[y,x]=True;component=[]
        while stack:
            cy,cx=stack.pop();component.append((cy,cx))
            for ny,nx in ((cy-1,cx),(cy+1,cx),(cy,cx-1),(cy,cx+1)):
                if 0<=ny<128 and 0<=nx<128 and mask[ny,nx] and not seen[ny,nx]:
                    seen[ny,nx]=True;stack.append((ny,nx))
        if len(component)>len(best):best=component
    if not best:raise ValueError('head attachment cannot be located')
    hy,hx=zip(*best);head=[round((min(hx)+max(hx))/2),min(hy)]
    rgb=a[...,:3].astype(np.int16);Y=np.indices(mask.shape)[0]
    gray=(a[...,3]>0)&(Y>=65)&(Y<96)&(rgb[...,0]>140)&(rgb.max(axis=2)-rgb.min(axis=2)<28)
    y,x=np.where(gray)
    if not len(x):raise ValueError('shirt attachment cannot be located')
    return {'root':[0,0],'head':head,'torso':[round((int(x.min())+int(x.max()))/2),int(y.min())]}


def build(base):
    library=base/'library'
    workspace.initialize(library,'human.sd.v1');assembly=library/'assembly.json'
    index,registry=workspace.read_workspace(assembly)
    topology=contract.seal({'kind':'parts-topology','schema_version':1,'id':'sd.simple.layers.topology',
        'nodes':[{'id':'root'},{'id':'head','parent':'root','required':False},{'id':'torso','parent':'root','required':False},
                 *[{'id':n,'parent':'root','required':False} for n in ('eyes','hair','clothing')]],
        'sockets':[{'id':n,'node':n,'required':True} for n in ('root','head','torso')],
        'slots':[{'id':s,'nodes':['torso' if s=='upper' else 'head'],'sockets':[anchor],'optional':True,'empty_policy':'no-piece'} for s,(_,anchor) in PARTS.items()]})
    rig=deepcopy(registry[index['rig']['id']]);rig.pop('digest');rig.update(id='sd.simple.layers.rig',topology=contract.reference(topology));rig=contract.seal(rig)
    catalog=deepcopy(registry[index['catalog']['id']]);catalog.pop('digest');catalog.update(id='sd.simple.layers.catalog',topology=contract.reference(topology),rig=contract.reference(rig))
    catalog['slots']=[{**s,'label':{'eyes':'눈','hair':'헤어','upper':'의상'}[s['id']]} for s in catalog['slots'] if s['id'] in PARTS];catalog=contract.seal(catalog)
    index.update(documents={topology['id']:'profiles/topology.json',rig['id']:'profiles/rig.json',catalog['id']:'catalog.json'},rig=contract.reference(rig),catalog=contract.reference(catalog))
    for d,path in ((topology,'profiles/topology.json'),(rig,'profiles/rig.json'),(catalog,'catalog.json')):write(library/path,d)
    write(assembly,index)
    source=base/'motion-source';requests=[];locations={};native=[]
    for state in index['states']:
        report=json.loads((base/f'{state}-loop/{state}.loop.report.json').read_bytes())
        # Preserve the complete body canvas, in the source order, at its exact
        # native cadence. Only the common 720->128 pixel projection is applied.
        files=sorted((base/f'{state}-loop/cycle').glob('*.png'))
        if not files:raise ValueError('loop frames missing')
        folder=source/state;folder.mkdir(parents=True)
        entries=[];expected={};rows=[]
        for i,path in enumerate(files):
            image=Image.open(path).convert('RGBA').resize((128,128),Image.Resampling.NEAREST)
            image.putalpha(image.getchannel('A').point(lambda a:255 if a>=128 else 0))
            filename=f'{i:03d}.png';data=assets.png_bytes(image);(folder/filename).write_bytes(data)
            expected[f'{state}/{filename}']=assets.sha256(data);entries.append({'id':f'video.{i}','file':filename,'duration':1/24})
            rows.append(tracks(image))
        # Decimal duration lexemes cannot represent 1/24 exactly; import the
        # fps descriptor so native rational timing remains exact.
        descriptor={'kind':'sprite-gen-asset','version':1,'fps':24,'frames':entries}
        path=write(folder/'motion.json',descriptor);expected[f'{state}/motion.json']=assets.sha256(path.read_bytes())
        requests.append({'id':state+'.right','state':state,'file':state+'/motion.json','fps':'24','loop':True,'timebase':120000,'source_mode':'external','expected_files':expected})
        locations[state+'.right']=rows;native.append({'state':state,'video_sha256':assets.sha256((base/f'{state}.mp4').read_bytes()),'loop_report':report})
    request=write(base/'motion-import.json',{'kind':'parts-motion-import','schema_version':1,'id':'sd.simple.video.motion','rig':index['rig'],'clips':requests})
    imported=parts_motion.import_motion(assembly,request,source)
    annotations=json.loads((library/imported['annotation_template']).read_bytes())
    candidate=json.loads((library/imported['candidate']).read_bytes())
    for clip in candidate['motion']['clips']:
        for source_sample,positions in zip(clip['samples'],locations[clip['id']]):
            sample=annotations['clips'][clip['id']][source_sample['id']]
            for name,socket in sample['sockets'].items():socket.update(position=positions[name],visible=True,confidence='verified')
    path=write(base/'attachment-tracks.json',annotations)
    parts_motion.freeze_motion(assembly,imported['candidate'],path)
    index,registry=workspace.read_workspace(assembly);motion=registry[index['motion']['id']]
    reference=Image.open(base/'parts-source/body-reference.png').convert('RGBA');origin=tracks(reference)
    refs={}
    for slot in (None,*PARTS):
        name='body' if slot is None else PARTS[slot][0];anchor='root' if slot is None else PARTS[slot][1]
        node='root' if slot is None else 'torso' if slot=='upper' else 'head';provided='clothing' if slot=='upper' else slot
        mapping={}
        for clip in motion['clips']:
            state=clip['state'];mapping[clip['id']]={}
            for i,sample in enumerate(clip['samples']):
                image=source/f'{state}/{i:03d}.png' if slot is None else base/f'parts-source/{name}.png'
                # The body is loaded directly from the full motion image; its
                # coordinates are always (0,0). Parts only follow a delta.
                relative=image.relative_to(base).as_posix()
                mapping[clip['id']][sample['id']]={'image':{'file':relative,'sha256':assets.sha256(image.read_bytes())},'format':'full-canvas','pivot':[0,0] if slot is None else origin[anchor],'target_socket':anchor,
                    'after':[] if slot is None else ['root'] if slot=='eyes' else ['root','eyes'] if slot=='hair' else ['root','eyes','hair']}
        spec={'kind':'parts-pack-import','schema_version':1,'id':'sd.simple.'+name,'design_id':'explorer.'+name,'role':'body' if slot is None else 'part','body_mode':'flat' if slot is None else 'segmented',
            'style':rig['style'],'rig':index['rig'],'motion':index['motion'],'view':rig['view'],'binding_id':'sd.simple.'+name+'.binding','occupies':[] if slot is None else [slot],
            'pieces':[{'id':name,'slot':slot,'node':node,'provides':['root'] if slot is None else [provided],'replaces':[],'mode':'raster' if slot is None else 'held','sample_map':mapping}]}
        refs[slot]=parts_import.import_pack(assembly,write(base/f'import/{name}.json',spec),base)['part']
    selection=write(base/'selection.json',{'kind':'parts-appearance-selection','schema_version':1,'id':'sd.simple.layers','selections':{s:refs[s] for s in PARTS}})
    parts_appearance.create(assembly,selection)
    write(base/'whole-body-provenance.json',{'kind':'sd-simple-layer-demo','motion_origin':'actual Grok bald-base videos','sources':native,'base_body_split':False,'body_replacements':0,'texture_warps':0,'appearance_clips_baked':0,'origin':origin,'selections':{s:refs[s] for s in PARTS},'art_approved':False,'release_ready':False})
    return assembly


def check(base,assembly):
    player=PartsPlayer.from_workspace(assembly);saved={p.relative_to(assembly.parent).as_posix():assets.sha256(p.read_bytes()) for p in assembly.parent.rglob('*') if p.is_file()}
    _,registry=workspace.read_workspace(assembly)
    motion=registry[player.appearance['motion']['id']]
    originals=deepcopy(player.appearance['selections']);count=0;sheet=Image.new('RGBA',(8*128,3*128))
    for n,bits in enumerate(product((False,True),repeat=3)):
        player.set_selection({s:originals[s] if enabled else None for s,enabled in zip(PARTS,bits)})
        for row,state in enumerate(('idle','walk','run')):
            player.set_state(state);clip=player.current()[0]
            for i,sample in enumerate(clip['samples']):
                player.seek(Fraction(i,24));scene=player.layers()
                body=scene['layers'][0]
                assert body['slot'] is None and body['position']==['0','0']
                source_clip=next(c for c in motion['clips'] if c['state']==state)
                assert body['image']['sha256']==source_clip['samples'][i]['image']['sha256']
                assert all(l['mode']=='held' for l in scene['layers'][1:])
                image,evidence=player.frame();assert all(d['clipped_pixels']==0 for d in evidence['evidence']['draw'])
                if i==0:sheet.alpha_composite(image,(n*128,row*128))
                count+=1
    assert saved=={p.relative_to(assembly.parent).as_posix():assets.sha256(p.read_bytes()) for p in assembly.parent.rglob('*') if p.is_file()}
    sheet.save(base/'layer-toggle-inspection.png')
    result={'whole_body_preserved':True,'layer_sets':8,'states':3,'sample_evaluations':count,'appearance_clips_baked':0,'art_approved':False,'release_ready':False};write(base/'validation.json',result);return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--base',required=True,type=Path);args=parser.parse_args()
    print(json.dumps(check(args.base,build(args.base)),indent=2))
