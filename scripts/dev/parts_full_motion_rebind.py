# SPDX-License-Identifier: Apache-2.0
"""Adopt unchanged independent PNGs against current body generation references.

Creates fresh immutable batches/candidates; never rewrites old provenance or
copies approval. Pixel equality is verified for every adopted layer.
"""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image
from sprite_gen.curate import parts_motion, parts_appearance
from sprite_gen.frames import parts_production as p
from sprite_gen.spec import parts_contract as c, parts_workspace as w


def rebind(assembly,revision):
    root,index,reg,_=parts_motion._active(assembly)
    folder=root/'inputs'/revision
    if folder.exists():raise ValueError('preserve prior adoption revisions')
    folder.mkdir();proof=[]
    for name in ['hair','sneakers','socks']:
        identity='full-motion.'+name;state=p._load(root,identity);files={};before={}
        for pose,row in state['tracks']['main']['poses'].items():
            old=p.candidate_record(root,state,pose,row['selected'])
            for layer in old['independent_layers']:
                ref=old['images'][layer['image_key']];files[pose,layer['id']]=root/row['selected']/ref['file']
                before[pose,layer['id']]=np.array(Image.open(files[pose,layer['id']]).convert('RGBA'))
        batch=p.sheet(assembly,identity,poses=list(state['tracks']['main']['poses']))
        pages=[(Path(str(n)),files[j['pose'],j['layer']]) for n,j in enumerate(batch['pages'])]
        p.slice_batch(assembly,identity,batch=batch['batch'],pages=pages,registered_canvas=True)
        current=p._load(root,identity)
        for pose,row in current['tracks']['main']['poses'].items():
            record=p.candidate_record(root,current,pose,row['selected'])
            for layer in record['independent_layers']:
                ref=record['images'][layer['image_key']];now=np.array(Image.open(root/row['selected']/ref['file']).convert('RGBA'))
                assert np.array_equal(now,before[pose,layer['id']]),(pose,layer['id'])
        checked=p.check(assembly,identity);p.build(assembly,identity)
        proof.append({'part':identity,'poses':len(state['tracks']['main']['poses']),
                      'layers_pixel_identical':len(before),'generation_calls':0,'art_approved':False,
                      'current_body':index['body'],'batch':batch['batch']})
        print(json.dumps(proof[-1]),flush=True)
    _,index,reg,_=parts_motion._active(assembly)
    selection={'kind':'parts-appearance-selection','schema_version':1,'id':'full-motion.example','selections':{
        slot:c.reference(reg['full-motion.'+name]) for slot,name in [('hair','hair'),('footwear','sneakers'),('socks','socks')]}}
    file=folder/'appearance.json';file.write_bytes(w.json_bytes(selection));parts_appearance.create(assembly,file)
    result={'parts':proof,'art_approved':False,'approval_copied':False};(folder/'result.json').write_bytes(w.json_bytes(result))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--assembly',type=Path,required=True);parser.add_argument('--revision',required=True)
    rebind(**vars(parser.parse_args()))
