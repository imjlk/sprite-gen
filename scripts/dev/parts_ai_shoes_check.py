# SPDX-License-Identifier: Apache-2.0
"""Explicit provisional foot-core coverage, independent of old ankle/foot labels.

Regions select frozen BODY pixels containing heels/toes. They are QA guides,
not runtime masks. An authored ankle boundary and temporal art still need
visual review; a geometry pass never grants approval.
"""
from pathlib import Path
import argparse
import json
import numpy as np
from PIL import Image, ImageDraw
from sprite_gen.frames import parts_production as production
from sprite_gen.spec import parts_workspace as workspace
from sprite_gen.serve.serve_parts_review import ReviewDesk

REGIONS = {
    'run.10': [[243,300,344,394],[580,545,746,630]],
    'run.11': [[285,302,376,401],[508,546,691,633]],
    'run.12': [[335,297,444,410],[444,531,628,623]],
    'run.13': [[370,314,502,412],[393,511,557,605]],
    'run.14': [[408,354,566,421],[358,486,504,586]],
    'run.15': [[448,395,618,445],[340,439,453,550]],
    'run.16': [[466,451,633,510],[327,380,411,472]],
    'run.17': [[449,507,608,584],[338,317,403,394]],
}

# Foot contours traced from BODY references, not the generated shoe alpha.
# The rectangular drafts included the other leg at these crossings.
FRONT_CONTOURS = {
    'run.13': [(375,314),(461,314),(479,363),(498,395),(493,411),(420,408),(389,380)],
    'run.14': [(415,354),(510,354),(539,388),(562,411),(551,419),(450,416),(430,388)],
    'run.15': [(452,395),(535,395),(578,412),(617,425),(606,440),(496,446),(446,416)],
}


def check(out):
    root = out / 'library'
    assembly = root / 'assembly.json'
    identity = 'illustration.motion.footwear.ai-pilot'
    state = production._load(root, identity)
    config = state['config'].copy()
    geometry = json.loads((out / 'guides/geometry.json').read_text())
    registration = json.loads((out / 'registration.json').read_text())
    folder = root / 'inputs/ai-shoes-pilot/criteria'
    folder.mkdir(exist_ok=True)
    checks = {}
    for pose, boxes in REGIONS.items():
        body = Image.open(geometry['poses'][pose]['body']).convert('RGBA')
        required = Image.new('L', body.size)
        draw = ImageDraw.Draw(required)
        for i,(x,y,right,bottom) in enumerate(boxes):
            if i==0 and pose in FRONT_CONTOURS:
                draw.polygon([(128+px//2,384+py//2) for px,py in FRONT_CONTOURS[pose]],fill=255)
            else:
                draw.rectangle((128+x//2,384+y//2,128+right//2-1,384+bottom//2-1), fill=255)
        required = Image.fromarray(np.minimum(np.asarray(required),np.asarray(body)[...,3]))
        allowed = Image.new('L', body.size)
        draw = ImageDraw.Draw(allowed)
        for piece in registration[pose]['pieces']:
            x,y,right,bottom = piece['bounds']
            draw.rectangle((128+(x-35)//2,384+(y-35)//2,128+(right+35)//2,384+(bottom+35)//2), fill=255)
        protected = Image.new('L', body.size)
        ImageDraw.Draw(protected).rectangle((0,0,767,450),fill=255)
        checks[pose] = {}
        for name, im in [('required',required),('allowed',allowed),('protected',protected)]:
            file=folder/f'{pose}.{name}.png';im.save(file)
            checks[pose][name]=str(file.relative_to(root))
    config['pose_checks'] = checks
    cfg=folder/'config.json';cfg.write_bytes(workspace.json_bytes(config))
    production.configure(assembly, identity, cfg)
    index,_=workspace.read_workspace(assembly)
    production.adopt(assembly, identity, root/index['documents'][identity])
    result=production.check(assembly, identity)
    ReviewDesk(assembly).build_preserving_appearance(identity)
    production.review(assembly,identity,reviewer='pilot-import',
                      reject=[pose for pose in state['tracks']['main']['poses'] if pose not in REGIONS],
                      note='미제작 포즈: 투명 미리보기 자리표시자. 신규 독립 신발 생성 필요; 승인 불가.')
    evidence={'source':'agent-authored foot-core regions over immutable body alpha; crossings exclude the other leg',
              'regions_guide_canvas_1024':REGIONS, 'front_contours':FRONT_CONTOURS,
              'whole_ankle_coverage_checked':False,
              'visual_approved':False,'render_masks':0,'check':result}
    (out/'foot-core-qc.json').write_bytes(workspace.json_bytes(evidence))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    result=check(args.out.absolute())
    print(json.dumps(result['summary']))
