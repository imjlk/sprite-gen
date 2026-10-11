# SPDX-License-Identifier: Apache-2.0
"""Source-specific RGB palette authoring for warm limb flashes in the SD demo.

This is a declared color edit, not an anatomical detector. It preserves source
alpha, pose, timing, neutral clothing and dark contours; it does not approve art.
"""
import argparse
import json
from pathlib import Path
import shutil

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from sprite_gen.frames.parts_segments import _components
from sprite_gen.spec import parts_assets as a, parts_workspace as w


def palette_pixels(pixels):
    rgb=pixels[...,:3].astype(float)
    red,green,blue=np.moveaxis(rgb,-1,0)
    yy=np.indices(pixels.shape[:2])[0]
    luminance=rgb@np.array([.2126,.7152,.0722])
    neutral=(pixels[...,3]>=128)&((rgb.max(-1)-rgb.min(-1))<40)
    # A small pale hand highlight is not clothing. Only substantial connected
    # neutral garment regions supply the protected edge neighbourhood.
    garment=np.zeros(neutral.shape,dtype=bool)
    for component in _components(neutral):
        if int(component.sum())>=512:garment |= component
    garment_edge=np.asarray(Image.fromarray(garment).filter(ImageFilter.MaxFilter(9)))
    warm=(pixels[...,3]>0)&(yy>350)&(red>95)&(red>green)&((red-green>18)|(green-blue>22))
    warm &= ~garment_edge & (luminance>85)
    shade=np.clip((luminance-190)*.2,-10,8)
    target=np.array([248,199,155])+shade[...,None]
    # Warm pink edge strokes must become brown contours rather than retain
    # their original hue through a partial RGB blend.
    tone=np.clip((luminance-95)/60,0,1)
    shadow=np.array([157,96,61])*(1-tone[...,None])+np.array([241,192,148])*tone[...,None]
    target=np.where((luminance<155)[...,None],shadow,target)
    weight=warm.astype(float)
    result=pixels.copy()
    result[...,:3]=np.rint(rgb*(1-weight[...,None])+np.clip(target,0,255)*weight[...,None]).astype('uint8')
    assert np.array_equal(result[...,3],pixels[...,3])
    assert np.array_equal(result[~warm],pixels[~warm])
    return result,warm


def author(source, out, geometry):
    if out.exists():raise ValueError('use a fresh revision; preserve original body')
    out.mkdir(parents=True)
    geom=json.loads(geometry.read_text());rows=[];patch={}
    for state in ('walk','run'):
        shutil.copytree(source/'inputs/body'/state,out/'registered'/state)
        board=Image.new('RGB',(1200,300*((len(list((source/'inputs/body'/state).glob('[0-9][0-9][0-9].png')))+3)//4)),(235,235,235));draw=ImageDraw.Draw(board)
        for n,file in enumerate(sorted((source/'inputs/body'/state).glob('[0-9][0-9][0-9].png'))):
            before=np.array(Image.open(file).convert('RGBA'));after,mask=palette_pixels(before)
            pose=f'{state}.{n:02}';target=out/'registered'/state/file.name
            Image.fromarray(after).save(target);geom['poses'][pose]['body_sha256']=a.sha256(target.read_bytes())
            changed=np.flatnonzero(np.any(before!=after,axis=-1));patch[pose+'.indices']=changed;patch[pose+'.rgb']=after.reshape(-1,4)[changed,:3]
            rows.append({'pose':pose,'original_sha256':a.sha256(file.read_bytes()),'edited_sha256':a.sha256(target.read_bytes()),'changed_pixels':len(changed),'alpha_equal':True,'outside_declared_warm_region_equal':True})
            roi=Image.fromarray(after).crop((150,390,600,650));roi.thumbnail((295,265));xy=(n%4*300,n//4*300+25);board.paste(roi,xy,roi);draw.text((xy[0]+5,xy[1]-20),pose,fill='black')
        board.save(out/f'{state}-hands.png')
    np.savez_compressed(out/'rgb-patch.npz',**patch)
    (out/'geometry.json').write_bytes(w.json_bytes(geom))
    report={'source':str(source),'palette':[248,199,155],'method':'declared warm-color region below y350, connected neutral garment >=512px plus 4px edge protected; peach interior and brown warm edge palette; dark contours retained','poses':rows,'alpha_changed':False,'timing_changed':False,'patch_sha256':a.sha256((out/'rgb-patch.npz').read_bytes()),'art_approved':False}
    (out/'skin-authoring.json').write_bytes(w.json_bytes(report))
    return {'frames':len(rows),'changed_pixels':sum(row['changed_pixels'] for row in rows),'alpha_changed':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('source','out','geometry'):parser.add_argument('--'+key,type=Path,required=True)
    print(json.dumps(author(**vars(parser.parse_args()))))
