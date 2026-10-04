# SPDX-License-Identifier: Apache-2.0
"""Experimental raster pose study; NOT a video-derived production motion.

Source fitting and limb rasterization are explicit authoring, BEFORE freeze.
This uses manually authored joint positions, not motion extracted from video.
The shipped compositor still performs integer translations only. No appearance
atlas/clip is built: 32 outfits x three states are validation cases in memory.
"""
from __future__ import annotations

import argparse
from fractions import Fraction
from itertools import product
import json
from pathlib import Path

from PIL import Image

from sprite_gen.compose.parts_runtime import PartsPlayer
from sprite_gen.curate import parts_appearance, parts_motion
from sprite_gen.frames import parts_import
from sprite_gen.frames.extract import apply_palette, build_shared_palette
from sprite_gen.spec import parts_assets as assets, parts_contract as contract, parts_workspace as workspace

REPO = Path(__file__).resolve().parents[2]
ART = REPO / "docs/parts/assets"
BASE = "sd-explorer-modular-body-atlas-20261004.png"
SIZE = (128, 128)
STATES = {"idle": (4, 250), "walk": (8, 125), "run": (8, 100)}
SLOTS = {
    "hair": ("hair_back", "hair_front"),
    "expression": ("eye_l", "eye_r", "brow_l", "brow_r", "mouth"),
    "upper": ("torso", "arm_l", "arm_r"),
    "lower": ("leg_l", "leg_r"),
    "footwear": ("foot_l", "foot_r"),
}
# Hand-authored ROIs, fitting dimensions, neutral local anchors. They are not
# anatomy detection, and variants use these same dimensions and joint anchors.
# Feet stop at x=600 because the hair tile extends into the nominal grid cell.
FITS = {
    "head": ((0, 0, 320, 440), (43, 50), (43, 14)),
    "torso": ((320, 0, 630, 440), (26, 29), (57, 60)),
    "arm_l": ((630, 0, 930, 440), (14, 25), None),
    "arm_r": ((930, 0, 1254, 440), (17, 27), None),
    "hand_l": ((0, 440, 320, 730), (8, 9), None),
    "hand_r": ((320, 440, 630, 730), (8, 9), None),
    "leg_l": ((630, 440, 930, 730), (14, 25), None),
    "leg_r": ((930, 440, 1254, 730), (14, 25), None),
    "foot_l": ((0, 730, 320, 1045), (15, 11), None),
    "foot_r": ((320, 730, 600, 1045), (15, 11), None),
    "hair_back": ((600, 730, 930, 1045), (50, 48), (39, 4)),
    "hair_front": ((930, 730, 1254, 1045), (52, 41), (39, 5)),
    "eye_l": ((0, 1045, 320, 1254), (4, 8), (83, 36)),
    "eye_r": ((320, 1045, 630, 1254), (10, 11), (73, 35)),
    "brow_l": ((630, 1045, 785, 1254), (3, 2), (83, 33)),
    "brow_r": ((785, 1045, 930, 1254), (8, 3), (73, 30)),
    "mouth": ((930, 1045, 1254, 1254), (6, 2), (78, 53)),
}
ORDER = ("root", "hair_back", "arm_l", "hand_l", "leg_l", "foot_l", "leg_r", "foot_r",
         "torso", "arm_r", "hand_r", "head", "eye_l", "eye_r", "brow_l", "brow_r", "mouth", "hair_front")
JOINTS = {
    "arm_l": ((11, 2), (7, 12), (3, 23)),
    "arm_r": ((3, 2), (8, 13), (13, 25)),
    "leg_l": ((9, 2), (6, 12), (4, 23)),
    "leg_r": ((5, 2), (6, 12), (7, 23)),
}
BODY_NODES = tuple(FITS)
EQUIPMENT = {
    "headwear": ("hat",), "back_accessory": ("backpack",),
    "gloves": ("glove_l", "glove_r"), "neckwear": ("scarf",), "eyewear": ("glasses",),
}
SUBSLOTS = {
    "bangs": ("hair",("hair_front",)),
    "eyes": ("expression",("eye_l","eye_r")),
    "brows": ("expression",("brow_l","brow_r")),
    "mouth": ("expression",("mouth",)),
    "sleeve_l": ("upper",("arm_l",)), "sleeve_r": ("upper",("arm_r",)),
    "footwear_l": ("footwear",("foot_l",)), "footwear_r": ("footwear",("foot_r",)),
}
FITS.update({
    "hat": ((0,0,627,490),(58,31),(36,0)),
    "backpack": ((627,0,1254,490),(22,34),(40,60)),
    "glove_l": ((0,490,627,860),(8,9),None),
    "glove_r": ((627,490,1254,860),(8,9),None),
    "scarf": ((0,860,627,1254),(20,12),(61,62)),
    "glasses": ((627,860,1254,1254),(20,12),(69,33)),
})
OVERLAYS = {
    "hat": ("head","head",list(ORDER),[]),
    "backpack": ("torso","back",[],list(ORDER)),
    "glove_l": ("hand_l","wrist_l",list(ORDER[:ORDER.index("hand_l")+1]),list(ORDER[ORDER.index("hand_l")+1:])),
    "glove_r": ("hand_r","wrist_r",list(ORDER[:ORDER.index("hand_r")+1]),list(ORDER[ORDER.index("hand_r")+1:])),
    "scarf": ("torso","head",["torso"],["head"]),
    "glasses": ("head","face",["head","eye_l","eye_r","brow_l","brow_r"],["hair_front"]),
}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(workspace.json_bytes(value))
    return path


def fit_sources(base):
    fitted, records = {}, []
    requests = [("body", BASE, BODY_NODES)]
    for slot, nodes in SLOTS.items():
        for variant in ("a", "b"):
            name = BASE if variant == "a" and slot in {"hair", "expression", "lower"} else f"sd-explorer-{slot}.{variant}-atlas-20261004.png"
            requests.append((f"{slot}.{variant}", name, nodes))
    requests.extend((f"{slot}.a","sd-explorer-equipment-atlas-20261004.png",nodes) for slot,nodes in EQUIPMENT.items())
    for identity, filename, nodes in requests:
        data = assets.read_file(ART, filename)
        image = assets.load_image(assets.SourceReader(ART), {"file": filename, "sha256": assets.sha256(data)}, size=(1254, 1254))
        fitted[identity] = {}
        for node in nodes:
            roi, size, _ = FITS[node]
            crop = image.crop(roi)
            bbox = crop.getchannel("A").point(lambda a: 255 if a >= 128 else 0).getbbox()
            if bbox is None:
                raise ValueError(f"empty authored ROI: {identity}/{node}")
            value = crop.crop(bbox).resize(size, Image.Resampling.NEAREST)
            fitted[identity][node] = value
            records.append({"pack": identity, "piece": node, "source": filename, "sha256": assets.sha256(data),
                "roi": list(roi), "source_bbox": [bbox[0]+roi[0], bbox[1]+roi[1], bbox[2]+roi[0], bbox[3]+roi[1]],
                "fit_size": list(size), "resample": "nearest", "alpha_threshold": 128})
    palette = build_shared_palette([im for group in fitted.values() for im in group.values()], 64)
    for group in fitted.values():
        for node, image in group.items():
            group[node] = apply_palette(image, palette)
    for slot,(parent,nodes) in SUBSLOTS.items():
        for variant in ("a","b"):
            fitted[f"{slot}.{variant}"]={node:fitted[f"{parent}.{variant}"][node].copy() for node in nodes}
    write(base / "source-fitting.json", {"kind": "sd-source-fitting", "schema_version": 1,
        "selected_design": "sd-explorer-body-candidate-v2-20261004.png", "generator": "built-in image_gen",
        "unrelated_study_used": False, "records": records, "palette": palette,
        "pose_policy": "explicit two-segment affine nearest rasterization before freeze; no runtime retargeting",
        "joint_anchors": JOINTS, "release_ready": False})
    return fitted


def knee(hip, ankle):
    # An authored forward knee, not inferred anatomy. Round all final positions
    # before generating either the body raster or any matching clothing raster.
    hx, hy = hip
    ax, ay = ankle
    return (round((hx+ax)/2) + (4 if ay < 104 else 1), round((hy+ay)/2))


def pose(state, index):
    if state == "idle":
        bob, lean = (0, 0, -1, 0)[index], 0
        feet = {"l": (58, 108), "r": (78, 108)}
        wrists = {"l": (50, 88+bob), "r": (91, 89+bob)}
        elbows = {"l": (54, 77+bob), "r": (85, 78+bob)}
    elif state == "walk":
        bob, lean = (0, -1, 0, -1, 0, -1, 0, -1)[index], 1
        steps = ((85,108),(80,108),(75,108),(69,108),(64,105),(65,99),(74,98),(82,102))
        feet = {"r": steps[index], "l": steps[(index+4)%8]}
        near = ((91,88),(90,89),(85,90),(78,89),(73,87),(74,85),(81,84),(88,85))
        far = ((50,86),(51,84),(56,83),(63,85),(68,87),(65,89),(59,90),(53,88))
        wrists = {"r": near[index], "l": far[index]}
        elbows = {side: (round((point[0]+(81 if side=="r" else 59))/2), 78+bob) for side,point in wrists.items()}
    else:
        bob, lean = (-2,-1,0,-1,-3,-1,0,-1)[index], 3
        steps = ((91,106),(84,108),(74,108),(62,106),(57,100),(66,94),(79,95),(88,100))
        feet = {"r": steps[index], "l": steps[(index+4)%8]}
        wrists = {"r": ((92,65),(96,68),(94,74),(89,79),(78,81),(72,75),(76,66),(86,63))[index],
                  "l": ((51,82),(46,77),(46,69),(52,64),(60,65),(67,71),(66,78),(58,83))[index]}
        elbows = {"r": ((91,76),(93,79),(91,85),(84,86),(72,77),(70,69),(74,62),(82,65))[index],
                  "l": ((54,77),(49,74),(51,65),(55,62),(62,65),(64,71),(62,78),(56,79))[index]}
    shoulders = {"l": (58+lean,67+bob), "r": (80+lean,66+bob)}
    hips = {"l": (63+lean,85+bob), "r": (76+lean,85+bob)}
    sockets = {"root": (69+lean,85+bob), "waist": (69+lean,85+bob),
        "head": (66+lean,40+bob), "face": (66+lean,40+bob), "back": (58+lean,74+bob)}
    for side in ("l", "r"):
        sockets.update({f"shoulder_{side}": shoulders[side], f"wrist_{side}": wrists[side], f"foot_{side}": feet[side]})
    return {"bob": bob, "lean": lean, "shoulders": shoulders, "elbows": elbows, "wrists": wrists,
        "hips": hips, "knees": {s:knee(hips[s],feet[s]) for s in ("l","r")}, "feet": feet, "sockets": sockets}


def warp_fragment(image, box, source_a, source_b, target_a, target_b):
    sx, sy = source_b[0]-source_a[0], source_b[1]-source_a[1]
    tx, ty = target_b[0]-target_a[0], target_b[1]-target_a[1]
    length = sx*sx+sy*sy
    p, q = (tx*sx+ty*sy)/length, (ty*sx-tx*sy)/length
    det = p*p+q*q
    if det == 0:
        raise ValueError("collapsed authored limb")
    ax, ay = source_a[0]-box[0], source_a[1]-box[1]
    matrix = (p/det, q/det, ax-(p*target_a[0]+q*target_a[1])/det,
        -q/det, p/det, ay+(q*target_a[0]-p*target_a[1])/det)
    return image.crop(box).transform(SIZE, Image.Transform.AFFINE, matrix, Image.Resampling.NEAREST)


def raster(image, node, p, state, index):
    output = Image.new("RGBA", SIZE)
    if node in JOINTS:
        side = node[-1]
        joints = (p["shoulders"][side],p["elbows"][side],p["wrists"][side]) if node.startswith("arm") else (p["hips"][side],p["knees"][side],p["feet"][side])
        a,b,c = JOINTS[node]
        for box,sa,sb,ta,tb in (((0,0,image.width,b[1]+2),a,b,joints[0],joints[1]),
                               ((0,b[1]-2,image.width,image.height),b,c,joints[1],joints[2])):
            output = Image.alpha_composite(output,warp_fragment(image,box,sa,sb,ta,tb))
        return output
    if node.startswith(("hand","glove")):
        point = p["wrists"][node[-1]]
        offset = (point[0]-4,point[1]-1)
    elif node.startswith("foot"):
        point = p["feet"][node[-1]]
        offset = (point[0]-4,point[1]-2)
    else:
        x,y = FITS[node][2]
        offset = (x+p["lean"],y+p["bob"])
        if node.startswith("eye") and state=="idle" and index==2:
            image = image.resize((image.width,2),Image.Resampling.NEAREST)
            offset = (offset[0],offset[1]+image.height+2)
    output.alpha_composite(image,offset)
    return output


def socket_for(node, slot):
    if slot == "hair" or node in {"head","hair_back","hair_front"}:
        return "head"
    if slot == "expression" or node in SLOTS["expression"]:
        return "face"
    if node.startswith("arm"):
        return "shoulder_"+node[-1]
    if node.startswith("hand"):
        return "wrist_"+node[-1]
    if node.startswith("foot"):
        return node
    if slot == "upper":
        return "shoulder_r"
    return "waist" if node.startswith("leg") else "root"


def prepare(base):
    fitted = fit_sources(base)
    library = base / "library"
    workspace.initialize(library, "human.sd.v1")
    assembly = library / "assembly.json"
    idx, docs = workspace.read_workspace(assembly)
    source, prepared = base/"motion-source", base/"parts-source"
    prepared.mkdir()
    poses, body_rasters, requests = {}, {}, []
    for state,(count,milliseconds) in STATES.items():
        folder = source/state
        folder.mkdir(parents=True)
        frames, expected = [], {}
        for i in range(count):
            p = pose(state,i)
            poses[state,i] = p
            images = {node:raster(image,node,p,state,i) for node,image in fitted["body"].items()}
            images["root"] = Image.new("RGBA",SIZE)
            body_rasters[state,i] = images
            image = Image.new("RGBA",SIZE)
            for node in ORDER:
                image = Image.alpha_composite(image,images[node])
            name=f"{i:02}.png"
            encoded=assets.png_bytes(image)
            (folder/name).write_bytes(encoded)
            expected[f"{state}/{name}"] = assets.sha256(encoded)
            frames.append({"id":f"source.{i}","file":name,"duration":milliseconds/1000})
        descriptor=write(folder/"motion.json",{"kind":"sprite-gen-asset","version":1,"frames":frames})
        expected[f"{state}/motion.json"]=assets.sha256(descriptor.read_bytes())
        requests.append({"id":f"{state}.right","state":state,"file":f"{state}/motion.json","source_state":None,
            "timebase":1000,"loop":True,"fps":None,"timing_policy":"exact","source_mode":"external","expected_files":expected})
    request=write(base/"motion-import.json",{"kind":"parts-motion-import","schema_version":1,
        "id":"sd.explorer.motion","rig":idx["rig"],"clips":requests})
    imported=parts_motion.import_motion(assembly,request,source)
    path=library/imported["annotation_template"]
    annotations=json.loads(path.read_bytes())
    for clip, samples in annotations["clips"].items():
        state=clip.removesuffix(".right")
        for ordinal, sample in enumerate(samples.values()):
            positions=poses[state,ordinal]["sockets"]
            for name,socket in sample["sockets"].items():
                socket.update(position=list(positions[name]),visible=True,confidence="verified")
    write(path,annotations)
    parts_motion.freeze_motion(assembly,imported["candidate"],path)
    idx, docs = workspace.read_workspace(assembly)
    rig, motion = docs[idx["rig"]["id"]],docs[idx["motion"]["id"]]
    options={slot:[] for slot in (*SLOTS,*EQUIPMENT,*SUBSLOTS)}
    for identity, group in fitted.items():
        role="body" if identity=="body" else "part"
        slot=None if role=="body" else identity.split(".")[0]
        names=ORDER if role=="body" else tuple(group)
        pieces=[]
        for node in names:
            semantic,socket,after,before=OVERLAYS[node] if node in OVERLAYS else (node,socket_for(node,slot),list(ORDER[:ORDER.index(node)]),[])
            mapping={}
            for clip in motion["clips"]:
                state=clip["state"]
                mapping[clip["id"]]={}
                for i,sample in enumerate(clip["samples"]):
                    p=poses[state,i]
                    image=body_rasters[state,i][node] if role=="body" else raster(group[node],node,p,state,i)
                    bbox=image.getbbox() or (0,0,1,1)
                    encoded=assets.png_bytes(image.crop(bbox))
                    name=assets.sha256(encoded)+".png"
                    (prepared/name).write_bytes(encoded)
                    mapping[clip["id"]][sample["id"]]={"image":{"file":name,"sha256":assets.sha256(encoded)},
                        "format":"cropped","crop_offset":list(bbox[:2]),"pivot":list(p["sockets"][socket]),
                        "target_socket":socket,"after":after,"before":before}
                    if node in SLOTS["expression"]:
                        # A semantic-node mask keeps far facial features inside
                        # the authored head silhouette without masking the head.
                        head_data=assets.png_bytes(body_rasters[state,i]["head"])
                        mask_name=assets.sha256(head_data)+".png"
                        (prepared/mask_name).write_bytes(head_data)
                        mapping[clip["id"]][sample["id"]]["mask"]={"image":{"file":mask_name,"sha256":assets.sha256(head_data)},
                            "target_node":node,"space":"rig-canvas","channel":"alpha"}
            provided=[] if node in OVERLAYS else [node]
            pieces.append({"id":node,"slot":slot,"node":"head" if slot=="expression" else semantic,
                "mode":"raster","provides":provided,"replaces":[] if role=="body" else provided,"sample_map":mapping})
        identity="sd.explorer."+identity
        request=write(base/f"{identity}-import.json",{"kind":"parts-pack-import","schema_version":1,"id":identity,
            "design_id":identity,"role":role,"style":rig["style"],"rig":idx["rig"],"motion":idx["motion"],
            "view":rig["view"],"binding_id":identity+".binding","occupies":[] if slot is None else [slot],"pieces":pieces})
        reference=parts_import.import_pack(assembly,request,prepared)["part"]
        if slot is not None:
            options[slot].append(reference)
    selection=write(base/"selection.json",{"kind":"parts-appearance-selection","schema_version":1,
        "id":"sd.explorer.live","selections":{slot:opts[0] if slot in SLOTS else None for slot,opts in options.items()}})
    parts_appearance.create(assembly,selection)
    return assembly,options


def validate(base, assembly, options):
    player=PartsPlayer.from_workspace(assembly)
    library=assembly.parent
    before={str(p.relative_to(library)):assets.sha256(p.read_bytes()) for p in library.rglob("*") if p.is_file()}
    sheet=Image.new("RGBA",(8*128,4*128))
    study=Image.new("RGBA",(8*128,3*128))
    cases,samples=0,0
    for n,bits in enumerate(product((0,1),repeat=5)):
        selection={slot:options[slot][v] for slot,v in zip(SLOTS,bits)}
        for state in STATES:
            player.set_state(state)
            player.seek(Fraction(3,20))
            old=(player.state,player.elapsed_seconds,player.current()[2]["sample_id"])
            player.set_selection(selection)
            assert old==(player.state,player.elapsed_seconds,player.current()[2]["sample_id"])
            clip=player.current()[0]
            ticks=0
            for i,sample in enumerate(clip["samples"]):
                player.seek(Fraction(ticks,clip["timebase"]))
                image,evidence=player.frame()
                assert all(e["clipped_pixels"]==0 for e in evidence["evidence"]["draw"])
                assert set(image.getchannel("A").tobytes()) <= {0,255}
                if state=="idle" and i==0:
                    sheet.alpha_composite(image,((n%8)*128,(n//8)*128))
                if n==0:
                    study.alpha_composite(image,(i*128,list(STATES).index(state)*128))
                samples+=1
                ticks+=sample["duration_ticks"]
            cases+=1
    equipment_sheet=Image.new("RGBA",(8*128,128))
    equipment_cases,equipment_samples=0,0
    for n,bits in enumerate(product((0,1),repeat=3)):
        selection={slot:options[slot][-1] for slot in SLOTS}
        selection.update({slot:options[slot][0] for slot in EQUIPMENT})
        selection.update({slot:options[slot][0] if enabled else None for slot,enabled in zip(("headwear","gloves","back_accessory"),bits)})
        for state in STATES:
            player.set_state(state)
            player.seek(Fraction(3,20))
            old=player.current()[2]
            player.set_selection(selection)
            assert player.current()[2]==old
            clip=player.current()[0]
            ticks=0
            for i,sample in enumerate(clip["samples"]):
                player.seek(Fraction(ticks,clip["timebase"]))
                image,evidence=player.frame()
                assert all(e["clipped_pixels"]==0 for e in evidence["evidence"]["draw"])
                if state=="idle" and i==0:
                    equipment_sheet.alpha_composite(image,(n*128,0))
                equipment_samples+=1
                ticks+=sample["duration_ticks"]
            equipment_cases+=1
    independent_cases,independent_samples=0,0
    for slot in SUBSLOTS:
        for reference in options[slot]:
            selection={name:None for name in options}
            selection.update({name:refs[-1] for name,refs in options.items() if name in SLOTS})
            selection[slot]=reference
            for state in STATES:
                player.set_state(state)
                player.seek(Fraction(3,20))
                old=player.current()[2]
                player.set_selection(selection)
                assert player.current()[2]==old
                clip=player.current()[0]
                ticks=0
                for sample in clip["samples"]:
                    player.seek(Fraction(ticks,clip["timebase"]))
                    _,evidence=player.frame()
                    assert all(e["clipped_pixels"]==0 for e in evidence["evidence"]["draw"])
                    independent_samples+=1
                    ticks+=sample["duration_ticks"]
                independent_cases+=1
    after={str(p.relative_to(library)):assets.sha256(p.read_bytes()) for p in library.rglob("*") if p.is_file()}
    assert before==after,"live swaps wrote workspace files"
    (base/"wardrobe-review.png").write_bytes(assets.png_bytes(sheet))
    (base/"shared-motion-review.png").write_bytes(assets.png_bytes(study))
    (base/"equipment-review.png").write_bytes(assets.png_bytes(equipment_sheet))
    report={"kind":"sd-explorer-live-demo-validation","schema_version":1,"evidence":"selected SD artwork, source fitting and live CPU composition",
        "motion_origin":"manually authored two-segment affine raster transforms; NOT video-derived",
        "production_motion":False,
        "assembly":"library/assembly.json","motion_states":list(STATES),"shared_motion_clips":3,
        "appearances_tested":32,"validation_cases":cases,"samples_composed_in_memory":samples,
        "equipment_validation_cases":equipment_cases,"equipment_samples_composed_in_memory":equipment_samples,
        "independent_subslot_validation_cases":independent_cases,"independent_subslot_samples_composed_in_memory":independent_samples,
        "independent_subslots":list(SUBSLOTS),
        "equipment_slots":list(EQUIPMENT),"part_packs":sum(map(len,options.values())),"body_packs":1,"appearances_stored":1,"appearance_clips_baked":0,"workspace_changed_by_swaps":False,
        "timeline_preserved_on_swaps":True,"clipped_pixels":0,"options":options,
        "visual_status":"pending fit, occlusion, motion and contact review","artwork_acceptance":False,"release_ready":False}
    write(base/"demo.json",report)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out",type=Path,required=True)
    parser.add_argument("--experimental-raster-motion",action="store_true",help="acknowledge this is a manual pose experiment, not video-derived motion")
    args=parser.parse_args()
    if not args.experimental_raster_motion:
        parser.error("this study requires --experimental-raster-motion; use the video workflow for production motion")
    output=args.out.absolute()
    if output.exists() and any(output.iterdir()):
        parser.error("--out requires a new or empty directory")
    output.mkdir(parents=True,exist_ok=True)
    assembly,options=prepare(output)
    report=validate(output,assembly,options)
    print(json.dumps({"demo":str(output/"demo.json"),"shared_motion_clips":report["shared_motion_clips"],
        "validation_cases":report["validation_cases"],"appearance_clips_baked":0,"release_ready":False},indent=2))


if __name__=="__main__":
    main()
