# SPDX-License-Identifier: Apache-2.0
"""Capture the fitted sock pilot's actual on/off renders and review evidence."""

import argparse
from fractions import Fraction
from pathlib import Path
import shutil

import numpy as np
from PIL import Image, ImageDraw
from sprite_gen.compose.parts_runtime import PartsPlayer
from sprite_gen.curate import parts_motion
from sprite_gen.frames import parts_production as p, parts_vision as vision
from sprite_gen.frames.parts_independent import reference_view
from sprite_gen.spec import parts_contract as c, parts_workspace as w


def capture(out, evidence):
    assembly = out/'library/assembly.json'
    root,index,registry,_ = parts_motion._active(assembly)
    player = PartsPlayer.from_workspace(assembly)
    player.set_state('run')
    evidence.mkdir(parents=True,exist_ok=True)
    board = Image.new('RGB',(1440,1200),'#e7e7e7')
    draw = ImageDraw.Draw(board)
    audit = Image.new('RGB',(1280,1040),'#e7e7e7')
    audit_draw = ImageDraw.Draw(audit)
    states = {name:p._load(root,name) for name in ('qualified.socks','qualified.sneakers','qualified.hair')}
    hair_inputs=(root/states['qualified.hair']['config']['independent']['layers'][0]['guides']['run.0'][0]).parent
    proof = {'scope':'four non-looping reference poses; procedural sock construction drafts',
             'body':index['body'],'motion':index['motion'],'appearance':index['appearance'],
             'parts':{name:state['built']['part'] for name,state in states.items()},'poses':{},'runtime_masks_in_saved_render_cases':0,
             'shoe_only_pixels_unchanged':0,'bare_body_pixels_identical':0,'release_ready':False}
    tick = 0
    clip = registry[index['motion']['id']]['clips'][0]
    for n,sample in enumerate(clip['samples']):
        pose = sample['pose_id']
        player.seek(Fraction(tick+1,clip['timebase']))
        images = {}
        for name,socks,shoes in [('bare',False,False),('shoes-only',False,True),('socks-only',True,False),('socks-shoes',True,True)]:
            player.set_selection({'hair':None,'socks':c.reference(registry['qualified.socks']) if socks else None,
                                  'footwear':c.reference(registry['qualified.sneakers']) if shoes else None})
            image,ev = player.frame()
            proof['runtime_masks_in_saved_render_cases'] += len(player.layers(interpolate=False)['masks'])
            images[name] = image
            image.save(evidence/f'{pose}.{name}.png')
        bare = Image.open(root/f'inputs/body/{n:03}.png').convert('RGBA')
        player.set_selection({slot:c.reference(registry[part]) for slot,part in
                              [('hair','qualified.hair'),('socks','qualified.socks'),('footwear','qualified.sneakers')]})
        actual,_ = player.frame()
        actual.save(evidence/f'{pose}.all-parts.png')
        original_hair = Image.open(hair_inputs/f'{pose}.hair.source.png').convert('RGBA')
        before = Image.alpha_composite(bare,original_hair)
        head_roi = (240,170,410,330)
        for col,(label,head) in enumerate([('body head reference',bare),('hair before repair',before),('hair actual layers',actual)]):
            tile = reference_view(head.crop(head_roi)).resize((240,226),Image.Resampling.LANCZOS)
            audit.paste(tile,(col*256,n*260+28))
            audit_draw.text((col*256+4,n*260+5),pose+' '+label,fill='black')
        old = Image.open(Path('docs/parts/assets/shoe-replacement-20261011')/f'{pose}.actual.png').convert('RGBA')
        assert np.array_equal(np.asarray(images['bare']),np.asarray(bare)), pose
        proof['bare_body_pixels_identical'] += 1
        proof['shoe_only_pixels_unchanged'] += int(np.array_equal(np.asarray(images['shoes-only']),np.asarray(old)))
        row = states['qualified.socks']['tracks']['main']['poses'][pose]
        candidate = p.candidate_record(root,states['qualified.socks'],pose,row['selected'])
        base = root/row['selected']
        shoe_row = states['qualified.sneakers']['tracks']['main']['poses'][pose]
        shoe = p.candidate_record(root,states['qualified.sneakers'],pose,shoe_row['selected'])
        seam = []
        for j,side in enumerate(('back','front')):
            cuff_key,foot_key = f'piece.sock_{side}_cuff',f'piece.sock_{side}_foot'
            cuff = Image.open(base/candidate['images'][cuff_key]['file']).convert('RGBA')
            sock_foot = Image.open(base/candidate['images'][foot_key]['file']).convert('RGBA')
            ankle = Image.open(root/f'inputs/body-pieces-r12/leg_{side}/{pose}.png').getchannel('A')
            body_foot = Image.open(root/f'inputs/body-pieces-r12/foot_{side}/{pose}.png').getchannel('A')
            ca,fa = np.asarray(cuff)[...,3],np.asarray(sock_foot)[...,3]
            assert np.all(ca<=np.asarray(ankle)), (pose,side,'cuff floats outside anatomy')
            assert np.array_equal(fa,np.asarray(body_foot)), (pose,side,'sock foot changed anatomy')
            shoe_ref = shoe['images'][f'piece.shoe_{side}']
            shoe_piece = Image.open(root/shoe_row['selected']/shoe_ref['file']).convert('RGBA')
            original_shoe = Image.open(Path('docs/parts/assets/shoe-replacement-20261011')/f'{pose}.shoe_{side}.png').convert('RGBA')
            assert np.array_equal(np.asarray(shoe_piece),np.asarray(original_shoe)), (pose,side,'raw shoe pixels moved')
            overlap = int(((ca>=128)&(np.asarray(shoe_piece)[...,3]>=128)).sum())
            assert overlap>0, (pose,side,'no cuff/footwear attachment overlap')
            seam.append({'side':side,'cuff_outside_anatomy_pixels':0,'sock_foot_alpha_matches_body':True,'raw_shoe_pixels_unchanged':True,'cuff_shoe_overlap_pixels':overlap})
            boxes = [cuff.getbbox(),shoe_piece.getbbox()]
            roi = (min(b[0] for b in boxes)-20,min(b[1] for b in boxes)-20,
                   max(b[2] for b in boxes)+20,max(b[3] for b in boxes)+20)
            for k,name in enumerate(('shoes-only','socks-shoes')):
                tile = reference_view(images[name].crop(roi))
                scale = min(350/tile.width,260/tile.height)
                tile = tile.resize((round(tile.width*scale),round(tile.height*scale)),Image.Resampling.LANCZOS)
                x,y=(j*2+k)*360,n*300
                board.paste(tile,(x,y+30));draw.text((x+5,y+5),f'{pose} {side} {name}',fill='black')
            tile = reference_view(actual.crop(roi))
            scale = min(245/tile.width,225/tile.height)
            tile = tile.resize((round(tile.width*scale),round(tile.height*scale)),Image.Resampling.LANCZOS)
            audit.paste(tile,((3+j)*256,n*260+28))
            audit_draw.text(((3+j)*256+4,n*260+5),pose+' '+side+' actual attachment',fill='black')
        observations = {}
        for name,state in states.items():
            part_row = state['tracks']['main']['poses'][pose]
            current = vision.current(root,index,state,pose)
            observations[name] = {'check':part_row['check'],'decision':p.effective_decision(root,index,state,pose),
                                  'vision_status':current['status'] if current else None,'vision_current':bool(current and current['current'])}
            prefix = pose+'.'+('shoes' if name=='qualified.sneakers' else name.split('.')[-1])
            if current:
                shutil.copy2(root/part_row['vision']['file'],evidence/(prefix+'.vision.json'))
                input_dir=(root/current['input']).parent
                for file in ('board.png','input.json','prompt.txt'):
                    shutil.copy2(input_dir/file,evidence/(prefix+'.vision-'+file))
        proof['poses'][pose] = {'cuff_seams':seam,'reviews':observations,'candidate':row['selected']}
        for layer in candidate['independent_layers']:
            ref = candidate['images'][layer['image_key']]
            shutil.copy2(base/ref['file'],evidence/f"{pose}.{layer['id']}.png")
        shutil.copy2(base/'candidate.json',evidence/(pose+'.candidate.json'))
        for part in ('qualified.hair','qualified.sneakers'):
            part_row = states[part]['tracks']['main']['poses'][pose]
            record = p.candidate_record(root,states[part],pose,part_row['selected'])
            shutil.copy2(root/part_row['selected']/'candidate.json',evidence/(pose+'.'+part.split('.')[-1]+'.candidate.json'))
            for layer in record['independent_layers']:
                for key in [layer['image_key']]+([layer['mask']['image_key']] if layer.get('mask') else []):
                    ref=record['images'][key]
                    shutil.copy2(root/part_row['selected']/ref['file'],evidence/(pose+'.'+key+'.png'))
                shutil.copy2(hair_inputs/f"{pose}.{layer['id']}.source.png",evidence/(pose+'.'+layer['id']+'.source.png'))
        tick += sample['duration_ticks']
    proof['art_approved'] = all(obs['decision']=='approved' for row in proof['poses'].values() for obs in row['reviews'].values())
    board.save(evidence/'attachment-comparison.png')
    audit.save(evidence/'joint-visual-audit.png')
    construction = (root/states['qualified.socks']['config']['prompt']['refs'][0]).parent
    for name in ('config.json','footwear-config.json','authoring.json','native-material.png'):
        shutil.copy2(construction/name,evidence/name)
    for name,state in states.items():
        (evidence/(name+'.current-config.json')).write_bytes(w.json_bytes(state['config']))
    shutil.copy2(hair_inputs/'authoring.json',evidence/'attachment-authoring.json')
    (evidence/'appearance.json').write_bytes(w.json_bytes(registry[index['appearance']['id']]))
    shutil.copy2(out/'socks-work/material-prompt.txt',evidence/'material-prompt.txt')
    shutil.copy2(out/'runtime-verification.json',evidence/'runtime-verification.json')
    shutil.copy2(out/'actual-parts-contact.png',evidence/'three-part-contact.png')
    (evidence/'current-state.json').write_bytes(w.json_bytes(proof))
    return proof


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--evidence',type=Path,required=True)
    result=capture(**vars(parser.parse_args()))
    print({k:v for k,v in result.items() if k!='poses'})
