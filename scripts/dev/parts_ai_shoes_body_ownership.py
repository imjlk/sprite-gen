# SPDX-License-Identifier: Apache-2.0
"""Correct the visible rear foot's ownership; never mask or repaint body pixels.

Contours are authored on frozen body guides. They replace the old interpolated
ankle ownership at crossing poses. Other body regions and all motion samples
remain exact; this is a provisional semantic correction, not an art approval.
"""
from pathlib import Path
from copy import deepcopy
import argparse
import shutil
import numpy as np
from PIL import Image, ImageDraw
from sprite_gen.frames import parts_segments, parts_import
from sprite_gen.curate import parts_appearance
from sprite_gen.spec import parts_workspace as w, parts_contract as c
from sprite_gen.compose.parts_runtime import PartsPlayer
from fractions import Fraction

REAR_FOOT = {
    'run.13': [(397,481),(443,479),(471,524),(530,563),(560,584),(544,606),(493,607),(452,578),(419,540),(395,515)],
    'run.14': [(370,430),(452,421),(451,472),(491,535),(505,566),(491,584),(466,590),(428,562),(397,530),(384,483),(360,460)],
    'run.15': [(337,386),(381,361),(439,395),(447,445),(453,503),(446,536),(428,548),(396,548),(367,512),(353,466)],
    'run.16': [(336,303),(391,291),(413,329),(407,369),(408,439),(393,470),(367,477),(338,456),(330,410),(329,355)],
    'run.17': [(353,220),(381,207),(415,241),(401,278),(398,328),(402,374),(393,394),(373,399),(345,392),(340,350),(341,302),(346,257)],
}


def correct(out):
    root=out/'library';assembly=root/'assembly.json';index,registry=w.read_workspace(assembly)
    before=deepcopy(index['body']);motion=registry[index['motion']['id']]
    original=root/'inputs/body-segments'
    folder=root/'inputs/ai-shoes-body-ownership'
    if folder.exists():
        raise ValueError('ownership experiment already exists; keep the prior revision')
    shutil.copytree(original/'labels',folder/'source-labels')
    record=w.decode_json((original/'segments.json').read_bytes())
    corrected=[]
    for clip in motion['clips']:
        for sample in clip['samples']:
            pose=sample['pose_id']
            if pose not in REAR_FOOT:
                continue
            file=folder/'source-labels'/clip['id']/f"{sample['id']}.png"
            labels=np.array(Image.open(file).convert('RGBA'))
            body=Image.open(root/f"inputs/body/run/{int(sample['id'].split('.')[-1]):03}.png").convert('RGBA')
            area=Image.new('L',body.size)
            ImageDraw.Draw(area).polygon([(128+x//2,384+y//2)for x,y in REAR_FOOT[pose]],fill=255)
            take=(np.array(area)>0)&(np.array(body)[...,3]>0)
            rgb=tuple(int(record['colors']['foot_back'][1:][i:i+2],16)for i in (0,2,4))
            changed=take&np.any(labels[...,:3]!=np.array(rgb),axis=-1)
            labels[take]=[*rgb,255]
            Image.fromarray(labels,'RGBA').save(file)
            corrected.append({'pose':pose,'ownership_pixels_changed':int(changed.sum()),'body_pixels_changed':0})
    colors=folder/'colors.json';colors.write_bytes(w.json_bytes({name:{'color':color,'provides':record['provides'][name]}
        for name,color in record['colors'].items()}))
    cut=parts_segments.segment_body(assembly,folder/'segments',colors=colors,labels=folder/'source-labels',
        body_id=index['body'][0]['id'],design_id=registry[index['body'][0]['id']]['design_id'],tolerance=8)
    parts_import.import_pack(assembly,Path(cut['spec']),Path(cut['source_root']))
    current,docs=w.read_workspace(assembly)
    selection={'kind':'parts-appearance-selection','schema_version':1,'id':'illustration.motion.ai-shoes-pilot',
        'selections':{slot['id']:None for slot in docs[current['catalog']['id']]['slots']}}
    identity='illustration.motion.footwear.ai-pilot'
    selection['selections']['footwear']=c.reference(docs[identity])
    spec=folder/'appearance.json';spec.write_bytes(w.json_bytes(selection));parts_appearance.create(assembly,spec)
    # Verify the BODY still reconstructs the source in every shared sample.
    player=PartsPlayer.from_workspace(assembly)
    for clip in motion['clips']:
        player.set_state(clip['state']);player.set_selection({'footwear':None})
        for n,sample in enumerate(clip['samples']):
            player.seek(Fraction((n*1000+23)//24+1,1000));im,_=player.frame()
            source=Image.open(root/f"inputs/body/{clip['state']}/{n:03}.png").convert('RGBA')
            assert im.tobytes()==source.tobytes(),sample['pose_id']
    after,_=w.read_workspace(assembly)
    evidence={'body_before':before,'body_after':after['body'],'motion_unchanged':after['motion']==index['motion'],
        'body_pixel_identical':43,'runtime_masks':0,'contours_guide_canvas_1024':REAR_FOOT,
        'corrected':corrected,'body_ownership_visually_approved':False}
    (out/'body-ownership-correction.json').write_bytes(w.json_bytes(evidence))
    return evidence


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();print(correct(args.out.absolute()))
