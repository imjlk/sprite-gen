# SPDX-License-Identifier: Apache-2.0
"""Source-bound landmark proposals for the new common walk, never approvals."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil

import numpy as np
from PIL import Image, ImageDraw
from sprite_gen.gen.facing_vision import codex_inspect
from sprite_gen.spec import parts_assets as a, parts_workspace as w


PROMPT = '''Inspect the labeled actual SD body frames. Propose anatomical points
for building independent footwear and sock layers. The small image shows the
whole body; the large image is an unscaled crop of that SAME 768x768 body.
Its grid labels are GLOBAL pixel coordinates, not page coordinates. Crop starts
at x=180,y=420 and ends at x=620,y=740. Read those printed coordinates.
For each leg give knee center, ankle joint center, back heel at the sole, and
frontmost toe at the sole. Front/back identify the foreground/background drawing
layers, not the foot's horizontal position: a front leg can move behind the body.
Trace each calf to its own foot; do not assign the other calf to a foot when they
cross. Infer the joint through an overlap only if its trajectory is clear.
Heel and toe specify the actual foot-bottom direction, not the leg direction.
Use numeric [x,y] pairs at the actual anatomy, no boxes. Mark an entirely hidden
foot only when no visible foot silhouette remains; a partly hidden foot is not
entirely hidden. Return only JSON:
{"poses": {"walk.NN": {"front": {"knee":[x,y],"ankle":[x,y],
"heel":[x,y],"toe":[x,y]},"back":{same four points},
"uncertainties":[specific uncertainty or empty list]}}}.
Do not use tools or code. These are proposals for visual validation, NOT approved
anatomy. Required frame IDs: '''


def propose(sequence, previous, out):
    if out.exists():
        raise ValueError('preserve earlier observations; use a fresh directory')
    out.mkdir(parents=True)
    files=sorted(sequence.glob('[0-9][0-9][0-9].png'))
    pages=[]
    for start in range(0,len(files),6):
        selected=files[start:start+6]
        canvas=Image.new('RGB',(1420,390*((len(selected)+1)//2)),(235,235,235))
        draw=ImageDraw.Draw(canvas)
        poses=[]
        for k,file in enumerate(selected):
            pose=f'walk.{int(file.stem):02}';poses.append(pose)
            x=(k%2)*710;y=(k//2)*390
            im=Image.open(file).convert('RGBA');small=im.resize((200,200),Image.Resampling.LANCZOS)
            canvas.paste(small,(x,y+30),small)
            crop=im.crop((180,420,620,740));canvas.paste(crop,(x+230,y+40),crop)
            draw.text((x+5,y+4),pose+'  GLOBAL anatomy coordinates',fill='black')
            for gx in range(200,621,40):
                xx=x+230+gx-180
                draw.line((xx,y+40,xx,y+360),fill=(145,174,192),width=1)
                draw.text((xx+2,y+22),str(gx),fill=(35,75,100))
            for gy in range(440,741,40):
                yy=y+40+gy-420
                draw.line((x+230,yy,x+670,yy),fill=(145,174,192),width=1)
                draw.text((x+202,yy),str(gy),fill=(35,75,100))
        scope=out/f'page-{start:02}';scope.mkdir();canvas.save(scope/'input.png')
        (scope/'prompt.txt').write_text(PROMPT+json.dumps(poses))
        pages.append((scope,poses))
    def call(item):
        scope,poses=item;prompt=(scope/'prompt.txt').read_text()
        report={'input_sha256':a.sha256((scope/'input.png').read_bytes()),
                'prompt_sha256':a.sha256(prompt.encode()),'required_poses':poses,
                'art_approved':False,'source_bound':True}
        try:
            raw,transport=codex_inspect(scope/'input.png',scope,prompt=prompt)
            (scope/'response.txt').write_text(raw);answer=json.loads(raw)
            assert set(answer['poses'])==set(poses),'missing or invented frame'
            for row in answer['poses'].values():
                for side in ('front','back'):
                    for key in ('knee','ankle','heel','toe'):
                        point=row[side][key]
                        assert len(point)==2 and all(isinstance(v,(int,float)) and 0<=v<768 for v in point)
            report.update(status='completed',transport=transport,answer=answer,
                          response_sha256=a.sha256(raw.encode()))
        except (Exception,SystemExit) as exc:
            report.update(status='failed',error=str(exc))
        (scope/'report.json').write_bytes(w.json_bytes(report))
        return report
    with ThreadPoolExecutor(max_workers=2) as pool:
        reports=list(pool.map(call,pages))
    assert all(r['status']=='completed' for r in reports),[(r['required_poses'],r.get('error')) for r in reports if r['status']!='completed']
    geometry=json.loads(previous.read_text())
    geometry['poses']={k:v for k,v in geometry['poses'].items() if k.startswith('run.')}
    for report in reports:
        for pose,row in report['answer']['poses'].items():
            file=sequence/f'{int(pose.split(".")[1]):03}.png'
            for side in ('front','back'):row[side]['footwear_anchor']='heel'
            row['body_sha256']=a.sha256(file.read_bytes())
            row['landmark_source']='vision proposal; still requires source anatomy validation'
            geometry['poses'][pose]=row
    (out/'geometry-proposed.json').write_bytes(w.json_bytes(geometry))
    return {'proposed':len(files),'art_approved':False,'status':'proposal'}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--sequence',type=Path,required=True)
    p.add_argument('--previous',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    print(json.dumps(propose(**vars(p.parse_args()))))
