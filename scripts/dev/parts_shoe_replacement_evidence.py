# SPDX-License-Identifier: Apache-2.0
"""Save inspectable evidence for the qualified four-pose footwear pilot."""

import argparse
from pathlib import Path
import shutil

from PIL import Image, ImageDraw
from sprite_gen.curate import parts_motion
from sprite_gen.frames import parts_production as p, parts_vision as vision
from sprite_gen.frames.parts_independent import reference_view
from sprite_gen.spec import parts_workspace as w


def capture(out, evidence):
    root, index, registry, _ = parts_motion._active(out / 'library/assembly.json')
    state = p._load(root, 'qualified.sneakers')
    evidence.mkdir(parents=True, exist_ok=True)
    board = Image.new('RGB', (1440,1200), '#e7e7e7')
    drawing = ImageDraw.Draw(board)
    status = {'body':index['body'], 'motion':index['motion'], 'part':state['built']['part'],
              'art_approved':False, 'release_ready':False, 'scope':'four non-looping reference poses only', 'poses':{}}
    for n in range(4):
        pose = f'run.{n}'
        row = state['tracks']['main']['poses'][pose]
        record = p.candidate_record(root,state,pose,row['selected'])
        base = root / row['selected']
        actual = vision._render(root,index,registry,state['config'],pose,record,base)
        retained = vision._render(root,index,registry,state['config'],pose,record,base,body_only=True)
        actual.save(evidence / (pose+'.actual.png'))
        retained.save(evidence / (pose+'.retained-body.png'))
        shutil.copy2(root / f'inputs/body/{n:03}.png', evidence / (pose+'.body.png'))
        shutil.copy2(base / 'candidate.json', evidence / (pose+'.candidate.json'))
        for j,layer in enumerate(record['independent_layers']):
            ref = record['images'][layer['image_key']]
            Image.open(base / ref['file']).save(evidence / f"{pose}.{layer['id']}.png")
            x1,y1,x2,y2 = layer['target']
            roi = (x1-24,y1-30,x2+30,y2+24)
            for k,(label,image) in enumerate([('retained body',retained),('actual shoe',actual)]):
                crop = reference_view(image.crop(roi))
                factor = min(350/crop.width,260/crop.height)
                crop = crop.resize((round(crop.width*factor),round(crop.height*factor)),Image.Resampling.LANCZOS)
                x,y = (j*2+k)*360,n*300
                board.paste(crop,(x,y+30))
                drawing.text((x+5,y+5),pose+' '+layer['id']+' '+label,fill='black')
        report = vision.current(root,index,state,pose)
        status['poses'][pose] = {'candidate':row['selected'], 'decision':p.effective_decision(root,index,state,pose),
            'stored_decision':row['decision'], 'check':row['check'], 'vision_current':bool(report and report['current']),
            'vision_status':report['status'] if report else None}
        if report:
            shutil.copy2(root / row['vision']['file'],evidence / (pose+'.vision.json'))
            # Report paths are immutable; preserve the exact board the model saw.
            input_ref = report['input']
            input_dir = (root / input_ref).parent
            for name in ('board.png','input.json','prompt.txt'):
                if (input_dir/name).is_file():
                    shutil.copy2(input_dir/name,evidence / (pose+'.vision-'+name))
    board.save(evidence / 'attachment-comparison.png')
    (evidence / 'current-state.json').write_bytes(w.json_bytes(status))
    for source,name in [
        (root/'inputs/shoe-replacement-r12/native-master.png','native-master.png'),
        (out/'shoe-repair/run.3-angle-rework/native-inside-angle-r2.png','native-run.3-view.png'),
        (root/'inputs/shoe-replacement-r12/geometry.json','geometry.json'),
        (root/'inputs/shoe-replacement-r12/authoring.json','body-ownership-authoring.json'),
        (root/'inputs/shoe-volume-reference-r2/volume.json','allowed-volume.json'),
        (out/'shoe-repair/old-candidate-straight-edge-audit.json','old-candidate-edge-audit.json'),
        (out/'runtime-verification.json','runtime-verification.json')]:
        shutil.copy2(source,evidence/name)
    (evidence/'production-config.json').write_bytes(w.json_bytes(state['config']))
    return {'evidence':str(evidence),'art_approved':False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--evidence',type=Path,required=True)
    print(capture(**vars(parser.parse_args())))
