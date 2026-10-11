# SPDX-License-Identifier: Apache-2.0
"""Register native independent-hair retouches and explicit shoe opening masks.

Preserves body pixels, distant hair art, raw sources and old candidates. Native
hair editing produces intrinsic alpha; footwear owns its selective collar mask.
The programmatic contour helpers retain the rejected experiment for inspection,
not the current production method. None of these steps grants visual approval.
"""

import argparse
from collections import deque
from copy import deepcopy
from pathlib import Path
import shutil

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from sprite_gen.curate import parts_motion, parts_appearance
from sprite_gen.frames import parts_production as p
from sprite_gen.spec import parts_assets as a, parts_contract as c, parts_workspace as w

EARS = {
    'run.0': {'body':[344,273],'old':[338,264],'radius':[26,31]},
    'run.1': {'body':[346,275],'old':[342,255],'radius':[27,31]},
    'run.2': {'body':[350,246],'old':[344,239],'radius':[27,31]},
    'run.3': {'body':[346,258],'old':[341,248],'radius':[27,31]},
}


def register_ear(image, ear):
    """Move the original notch and strand texture together; no colour filling.

    The bounded source-space displacement is baked into an independent draft
    asset. Pixels outside the authored neighbourhood stay byte-identical. The
    renderer does not warp art, and no body RGB is sampled.
    """
    raw=np.asarray(image); yy,xx=np.mgrid[:image.height,:image.width]
    cx,cy=ear['body']; dx,dy=np.array(ear['body'])-np.array(ear['old'])
    radius=np.sqrt(((xx-cx)/43)**2+((yy-cy)/46)**2)
    weight=np.clip((1.7-radius)/.7,0,1)
    weight=weight*weight*(3-2*weight)
    sx=np.clip(xx-dx*weight,0,image.width-1);sy=np.clip(yy-dy*weight,0,image.height-1)
    ix=np.floor(sx).astype(int);iy=np.floor(sy).astype(int)
    jx=np.minimum(ix+1,image.width-1);jy=np.minimum(iy+1,image.height-1)
    tx=(sx-ix)[...,None];ty=(sy-iy)[...,None]
    premult=raw.astype(float);premult[...,:3]*=premult[...,3:4]/255
    sampled=(premult[iy,ix]*(1-tx)+premult[iy,jx]*tx)*(1-ty)+(premult[jy,ix]*(1-tx)+premult[jy,jx]*tx)*ty
    sampled[...,:3]=np.divide(sampled[...,:3]*255,sampled[...,3:4],out=np.zeros_like(sampled[...,:3]),where=sampled[...,3:4]>0)
    repaired=np.clip(np.rint(sampled),0,255).astype('uint8')
    repaired[weight==0]=raw[weight==0]
    return Image.fromarray(repaired),int(np.any(repaired!=raw,axis=-1).sum())


def ear_aperture(body, ear):
    """Trace this pilot body's drawn C contour inside its annotated ear ROI.

    This source-specific authoring rule is not a general anatomy detector. The
    threshold and ROI are frozen inputs; missing/ambiguous contours fail rather
    than silently substitute a circular cutout.
    """
    rgb=np.asarray(body.convert('RGB')); yy,xx=np.mgrid[:body.height,:body.width]
    cx,cy=ear['body']; rx,ry=ear['radius']
    # A rectangular authored guard retains the open C's endpoints. An ellipse
    # clipped the lower-right endpoint and left the old hair's dark spur there.
    region=(np.abs(xx-cx)<=rx+4)&(np.abs(yy-cy)<=ry+4)
    stroke=region&(rgb[...,0]<185)&(rgb[...,1]<125)&(rgb[...,2]<95)
    remaining=set(zip(*np.nonzero(stroke))); components=[]
    while remaining:
        first=remaining.pop();component=[first];queue=deque([first])
        while queue:
            y,x=queue.popleft()
            for dy in (-1,0,1):
                for dx in (-1,0,1):
                    point=(y+dy,x+dx)
                    if point in remaining:
                        remaining.remove(point);component.append(point);queue.append(point)
        components.append(component)
    seed=(cx-rx,cy)
    matches=[component for component in components if len(component)>=100 and
             min((x-seed[0])**2+(y-seed[1])**2 for y,x in component)<=36]
    assert len(matches)==1,'annotated ear tangent missing or ambiguous'
    points=sorted((int(x),int(y)) for y,x in matches[0])
    def cross(o,a,b): return (a[0]-o[0])*(b[1]-o[1])-(a[1]-o[1])*(b[0]-o[0])
    lower=[];upper=[]
    for point in points:
        while len(lower)>=2 and cross(lower[-2],lower[-1],point)<=0: lower.pop()
        lower.append(point)
    for point in reversed(points):
        while len(upper)>=2 and cross(upper[-2],upper[-1],point)<=0: upper.pop()
        upper.append(point)
    hull=lower[:-1]+upper[:-1]
    opening=Image.new('L',body.size);ImageDraw.Draw(opening).polygon(hull,fill=255)
    # Exposing skin outside the drawn ear stroke creates a pale ring. Keep the
    # authored opening inside the detected stroke; no silhouette dilation.
    opening=opening.filter(ImageFilter.GaussianBlur(.2))
    contour=Image.new('L',body.size)
    pixels=contour.load()
    for y,x in matches[0]: pixels[int(x),int(y)]=255
    return Image.fromarray(255-np.asarray(opening)),hull,contour


def outline_ear(image, source, mask, contour):
    """Author a narrow hair-side outline using only the original hair palette.

    The body's drawn contour supplies geometry, never its RGB. This removes
    the pale cutout fringe while leaving the actual body ear pixels exposed.
    It is part authoring, not a validation-threshold adjustment.
    """
    original=np.asarray(source); rgb=original[...,:3].astype(float)
    dark=(original[...,3]>=240)&(rgb.mean(axis=-1)<65)
    assert dark.any(),'native hair outline palette missing'
    ink=np.median(rgb[dark],axis=0)
    ring=np.asarray(contour.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.GaussianBlur(.35))).astype(float)/255
    ring*=np.asarray(mask).astype(float)/255
    raw=np.asarray(image).astype(float); old_alpha=raw[...,3]/255
    new_alpha=np.maximum(old_alpha,ring)
    colour=raw[...,:3]*(1-ring[...,None])+ink*ring[...,None]
    repaired=np.dstack([colour,new_alpha*255])
    repaired=np.clip(np.rint(repaired),0,255).astype('uint8')
    return Image.fromarray(repaired),{'hair_palette_rgb':np.rint(ink).astype(int).tolist(),
                                     'outline_pixels':int((ring>.05).sum()),'body_rgb_copied':False}


def native_ear_repair(root, pose, sheet):
    """Register a native part-only retouch, preserving all distant source art."""
    n=int(pose[-1]);tile=sheet.crop(((n%2)*sheet.width//2,(n//2)*sheet.height//2,
                                   (n%2+1)*sheet.width//2,(n//2+1)*sheet.height//2))
    tile=tile.resize((320,320),Image.Resampling.BILINEAR)
    alpha=np.asarray(tile)[...,3]
    cx,cy=EARS[pose]['body'];expected_y=cy-90
    edges=[]
    # Restrict feature matching to the ear's anatomical vertical band; a
    # dangling nape strand below it is not the ear aperture's left tangent.
    for y in range(max(0,expected_y-30),min(320,expected_y+31)):
        line=alpha[y,80:175]
        transitions=np.flatnonzero((line[:-1]>=128)&(line[1:]<128))
        for transition in transitions:
            if np.all(line[transition+1:min(transition+10,len(line))]<128):
                edges.append((int(transition)+81,y));break
    assert edges,'native ear notch missing'
    left=min(x for x,y in edges)
    native_y=float(np.mean([y for x,y in edges if x<=left+1]))
    body=Image.open(root/f'inputs/body/{n:03}.png').convert('RGBA')
    _,_,contour=ear_aperture(body,EARS[pose]);ys,xs=np.nonzero(np.asarray(contour))
    body_left=int(xs.min());body_y=float(np.mean(ys[xs<=body_left+1]))
    dx=body_left+1-(left+215);dy=body_y-(native_y+90)
    assert abs(dx)<=20 and abs(dy)<=25,'native reframe exceeds authored registration range'
    patch=Image.new('RGBA',(768,768));patch.paste(tile,(215,90))
    from sprite_gen.frames.parts_part_edit import translate_image
    patch=translate_image(patch,round(dx),round(dy))
    original=Image.open(root/f'inputs/attachments-r6/{pose}.hair.png').convert('RGBA')
    yy,xx=np.mgrid[:768,:768]
    # The repaired aperture is fully inside this edit box. A feather only at
    # the outer box blends unchanged strand textures, never cuts an ear mask.
    distance=np.minimum.reduce([xx-(cx-44),(cx+36)-xx,yy-(cy-46),(cy+43)-yy])
    weight=np.clip(distance/8,0,1)[...,None]
    a=np.asarray(original).astype(float);b=np.asarray(patch).astype(float)
    a[...,:3]*=a[...,3:4]/255;b[...,:3]*=b[...,3:4]/255
    mixed=a*(1-weight)+b*weight
    mixed[...,:3]=np.divide(mixed[...,:3]*255,mixed[...,3:4],out=np.zeros_like(mixed[...,:3]),where=mixed[...,3:4]>0)
    result=Image.fromarray(np.clip(np.rint(mixed),0,255).astype('uint8'))
    return result,{'native_grid_origin':[215,90],'translation':[round(dx),round(dy)],
                   'source_tangent':[left,native_y],'body_tangent':[body_left,body_y],
                   'notch_target_margin_px':1,
                   'edit_box':[cx-44,cy-46,cx+36,cy+43],'feather_px':8,'runtime_mask':False}


def author(out, *, revision='attachments-r9', native_hair=None):
    assembly=out/'library/assembly.json'
    root,index,registry,_=parts_motion._active(assembly)
    folder=root/'inputs'/revision
    if folder.exists(): raise ValueError('use a fresh revision')
    folder.mkdir(parents=True)
    native_sheet=None
    if native_hair:
        shutil.copy2(native_hair,folder/'native-hair-sheet.png')
        native_sheet=Image.open(native_hair).convert('RGBA')
        assert native_sheet.width==native_sheet.height and native_sheet.width%2==0,'expected 2x2 hair sheet'
    source=w.read_json(root,'inputs/shoe-replacement-r12/geometry.json')
    yy,xx=np.mgrid[:768,:768];coords=np.stack([xx,yy],axis=-1)
    proof={'body_modified':False,'art_approved':False,'source_preserved':True,'poses':{}}
    for name in ('qualified.hair','qualified.sneakers'):
        state=p._load(root,name);cfg=deepcopy(state['config']);pages={}
        for layer in cfg['independent']['layers']:
            layer['masks']={}
            if name=='qualified.hair': layer['provides']=['hair']
            for pose in EARS:
                row=state['tracks']['main']['poses'][pose]
                record=p.candidate_record(root,state,pose,row['selected'])
                ref=record['images']['piece.'+layer['id']]
                image=Image.open(root/row['selected']/ref['file']).convert('RGBA')
                if name=='qualified.hair':
                    # The rejected r2 fill is not an input to the next repair.
                    image=Image.open(root/f"inputs/attachments-r2/{pose}.{layer['id']}.source.png").convert('RGBA')
                image.save(folder/f"{pose}.{layer['id']}.source.png")
                if name=='qualified.hair':
                    ear=EARS[pose]
                    if native_sheet is not None:
                        image,registration=native_ear_repair(root,pose,native_sheet)
                        mask=None
                        proof['poses'].setdefault(pose,{})['ear']={**ear,**registration,
                            'method':'native independent-hair retouch; bounded registered patch; no body RGB'}
                    else:
                        original=image.copy();image,changed=register_ear(image,ear)
                        body=Image.open(root/f'inputs/body/{int(pose[-1]):03}.png').convert('RGBA')
                        mask,hull,contour=ear_aperture(body,ear)
                        image,outline=outline_ear(image,original,mask,contour)
                        target='hair'
                        proof['poses'].setdefault(pose,{})['ear']={**ear,'registered_pixels':changed,
                            'mask_hull':hull,'stroke_rgb_threshold':[185,125,95],'mask_margin_px':0,
                            'hair_side_outline':outline,'method':'programmatic contour draft; rejected native-matte repair experiment'}
                else:
                    side='back' if layer['id']=='shoe_back' else 'front'
                    values=source['layers'][layer['id']][pose]
                    theta=np.deg2rad(values['angle']);scale=values['scale']
                    matrix=scale*np.array([[np.cos(theta),-np.sin(theta)],[np.sin(theta),np.cos(theta)]])
                    shift=np.array(values['collar'])-matrix@np.array(source['source_collar'])
                    uv=(coords-shift)@np.linalg.inv(matrix).T
                    distance=(uv[...,1]-source['source_cut_y'])*scale
                    leg=np.asarray(Image.open(root/f'inputs/body-pieces-r12/leg_{side}/{pose}.png').getchannel('A')).astype(float)
                    # Only the rear lip overlapping the real ankle becomes an
                    # opening. Foreground rim and the lower attachment seam stay.
                    lip=(uv[...,0]<source['source_collar'][0])&(distance<-3)&(distance>-12)
                    aperture=np.rint(leg*lip).astype('uint8')
                    mask=Image.fromarray(255-aperture)
                    target='foot_'+side
                    removed=int(((np.asarray(image)[...,3]>=16)&(aperture>=16)).sum())
                    proof['poses'].setdefault(pose,{})[layer['id']]={'collar_opening_pixels':removed,'target_node':target,'raw_shoe_unchanged':True}
                if mask is not None:
                    mask_path=folder/f"{pose}.{layer['id']}.keep-mask.png"
                    mask.convert('RGBA').save(mask_path)
                    layer['masks'][pose]={'image':{'file':mask_path.relative_to(root).as_posix(),'sha256':a.sha256(mask_path.read_bytes())},'target_node':target,'space':'rig-canvas','channel':'luma'}
                file=folder/f"{pose}.{layer['id']}.png";image.save(file);pages[pose,layer['id']]=file
                py,px=np.nonzero(np.asarray(image)[...,3]>=16)
                layer['targets'][pose]=[max(0,int(px.min())-4),max(0,int(py.min())-4),min(768,int(px.max())+5),min(768,int(py.max())+5)]
                layer['source_bounds'][pose]=[0,0,768,768]
                if name=='qualified.hair': layer['guides'][pose]=[file.relative_to(root).as_posix()]
        path=folder/(name+'.config.json');path.write_bytes(w.json_bytes(cfg));p.configure(assembly,name,path)
        batch=p.sheet(assembly,name,poses=list(EARS))
        p.slice_batch(assembly,name,batch=batch['batch'],pages=[(Path(str(n)),pages[job['pose'],job['layer']]) for n,job in enumerate(batch['pages'])],registered_canvas=True)
        result=p.check(assembly,name);p.build(assembly,name)
        proof[name]={'checks':result,'config':path.relative_to(root).as_posix()}
    _,_,registry,_=parts_motion._active(assembly)
    appearance={'kind':'parts-appearance-selection','schema_version':1,'id':'qualified.example',
                'selections':{slot:c.reference(registry[part]) for slot,part in
                              [('hair','qualified.hair'),('footwear','qualified.sneakers'),('socks','qualified.socks')]}}
    (folder/'appearance.json').write_bytes(w.json_bytes(appearance))
    proof['appearance']=parts_appearance.create(assembly,folder/'appearance.json')['appearance']
    (folder/'authoring.json').write_bytes(w.json_bytes(proof))
    return proof


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--revision',default='attachments-r9')
    parser.add_argument('--native-hair',type=Path,required=True)
    print(author(**vars(parser.parse_args())))
