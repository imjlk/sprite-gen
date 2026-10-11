# SPDX-License-Identifier: Apache-2.0
"""Recorded RGB-only correction for yellow skin flashes in this walk corpus."""
import json
from pathlib import Path
import shutil

import numpy as np
from PIL import Image
from scripts.dev.parts_full_motion import boards
from sprite_gen.spec import parts_assets as a, parts_workspace as w


def fit(sequence, proof):
    if proof.exists():raise ValueError('preserve earlier colour corrections')
    proof.mkdir(parents=True);backup=proof/'source';shutil.copytree(sequence,backup)
    yy,xx=np.mgrid[:768,:768];rows=[];patch={}
    for file in sorted(sequence.glob('[0-9][0-9][0-9].png')):
        raw=file.read_bytes();arr=np.array(Image.open(file).convert('RGBA'));alpha=arr[...,3].copy()
        rgb=arr[...,:3].astype('int16');r,g,b=np.moveaxis(rgb,-1,0)
        # The selected body has peach skin and neutral grey clothes. This
        # source-specific threshold selects the visibly yellow limb flashes,
        # not a background matte or an anatomical segmentation rule.
        suspect=(alpha>=16)&(yy>=400)&(r>110)&(g>85)&(r-g<23)&(g-b>35)
        indices=np.flatnonzero(suspect);flat=arr.reshape(-1,4)
        if len(indices):
            red=flat[indices,0].astype('int16')
            flat[indices,1]=np.clip(red-47,0,255).astype('uint8')
            flat[indices,2]=np.clip(red-82,0,255).astype('uint8')
            Image.fromarray(arr).save(file)
        assert np.array_equal(arr[...,3],alpha),'RGB correction changed shape'
        pose='walk.'+file.stem[-2:];patch[pose+'.indices']=indices;patch[pose+'.rgb']=flat[indices,:3]
        rows.append({'pose':pose,'source_sha256':a.sha256(raw),'edited_sha256':a.sha256(file.read_bytes()),'changed_pixels':len(indices),'alpha_changed':False})
    np.savez_compressed(proof/'patch.npz',**patch)
    report={'poses':rows,'patch_sha256':a.sha256((proof/'patch.npz').read_bytes()),
            'method':'source-bound yellow skin RGB correction, red-channel shade preserved; alpha and timing unchanged',
            'source_specific':True,'art_approved':False}
    (proof/'report.json').write_bytes(w.json_bytes(report));boards(sequence,'walk')
    print(json.dumps({'frames':len(rows),'pixels':sum(r['changed_pixels'] for r in rows),'alpha_changed':False}))


if __name__=='__main__':
    fit(Path('assets/parts-motion-natural-20261012/production-r1/registered/walk'),Path('assets/parts-motion-natural-20261012/walk-colour-fit-r1'))
