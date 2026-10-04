# SPDX-License-Identifier: Apache-2.0
"""Import a real Grok video-derived SD motion candidate, without freezing it.

Requires the inspected v2 videos/frames/loops in --source. All final motion
pixels come from those clips. No procedural pose, skeleton or new drawing is
used. A common scale/translation and palette are finalized BEFORE import.
"""
from __future__ import annotations

import argparse
from fractions import Fraction
import json
from pathlib import Path
from shutil import copyfile

from PIL import Image

from sprite_gen.compose.compose_layers import _clipped_pixels
from sprite_gen.curate import parts_motion
from sprite_gen.frames.extract import apply_palette, build_shared_palette
from sprite_gen.gen.video import verify_mp4
from sprite_gen.qa.inspect_parts import toolchain
from sprite_gen.spec import parts_assets as assets, parts_workspace as workspace
from sprite_gen.util.resample import resize_cell

STATES = ("idle", "walk", "run")
SELECTED_SHA = "462c5f330daba54007d2b5d5327d4a04bf54571962bec5de2632f569344012c1"
CANVAS = (128,128)


def write(path, data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(workspace.json_bytes(data))
    return path


def prepare(source, output, *, walk_loop="walk-v2-loop", palette_file=None):
    source,output=source.absolute(),output.absolute()
    if output.exists() and any(output.iterdir()):
        raise ValueError("output requires a new or empty directory")
    selected=(source/"selected-sd-reference.png").read_bytes()
    if assets.sha256(selected)!=SELECTED_SHA:
        raise ValueError("video reference differs from the user's selected SD design")
    source_canvas=(source/"selected-sd-green-canvas.png").read_bytes()
    output.mkdir(parents=True,exist_ok=True)
    library=output/"library"
    workspace.initialize(library,"human.sd.v1")
    assembly=library/"assembly.json"
    index,_=workspace.read_workspace(assembly)
    images,records,clips=[],[],[]
    first=Image.open(source/"idle-v2-frames/keyed/frame-0001.png").convert("RGBA")
    reference_box=first.getchannel("A").point(lambda v:255 if v>=8 else 0).getbbox()
    if reference_box is None:
        raise ValueError("first video frame has no body")
    standing_height=reference_box[3]-reference_box[1]
    scale=Fraction(112,standing_height)
    scaled_size=tuple(round(v*scale) for v in first.size)
    actual_x,actual_y=Fraction(scaled_size[0],first.width),Fraction(scaled_size[1],first.height)
    offset=(64-round(Fraction(reference_box[0]+reference_box[2],2)*actual_x),
            120-round(reference_box[3]*actual_y))
    origin=output/"video-origin"
    origin.mkdir()
    if Path(walk_loop).name != walk_loop or walk_loop in {".",".."}:
        raise ValueError("walk loop must be one directory under --source")
    for filename in ("selected-sd-reference.png","selected-sd-green-canvas.png","green-canvas.report.json"):
        copyfile(source/filename,origin/filename)
    for state in STATES:
        loop=source/("idle-v2-pinned-loop" if state=="idle" else walk_loop if state=="walk" else "run-v2-loop")
        loop_report=json.loads((loop/f"{state}.loop.report.json").read_bytes())
        strip_meta=json.loads((loop/f"{state}.strip.json").read_bytes())
        frames_report=json.loads((source/f"{state}-v2-frames/frames.report.json").read_bytes())
        if loop_report.get("status")!="passed" or frames_report.get("edge_contacts"):
            raise ValueError(f"{state}: source extraction/loop gate has not passed")
        fps=Fraction(str(frames_report["fps"]))
        if fps!=24:
            raise ValueError(f"{state}: unexpected source frame rate")
        count,length=strip_meta["frames"],loop_report["cycle"]["length"]
        rate=Fraction(count)*fps/length
        if rate.denominator!=1:
            raise ValueError("this recipe requires an integer finalized sample rate")
        chosen=list(range(length)) if count==length else [round(k*length/count) for k in range(count)]
        rows=[]
        replacements=set((loop_report.get("jump_repair") or {}).get("replaced",[]))
        for number,ordinal in enumerate(chosen):
            source_frame=loop/"cycle"/f"frame-{ordinal:03d}.png"
            rgba=Image.open(source_frame).convert("RGBA")
            if rgba.size!=first.size:
                raise ValueError("native video canvas differs between states")
            scaled=resize_cell(rgba,scaled_size)
            if _clipped_pixels(scaled,offset,CANVAS):
                raise ValueError(f"{state}/{number}: common projection clips artwork")
            image=Image.new("RGBA",CANVAS)
            image.alpha_composite(scaled,offset)
            images.append(image)
            rows.append({"number":number,"cycle_ordinal":ordinal,
                "source_video_ordinal":loop_report["cycle"]["start"]+ordinal,
                "pixel_origin":"RIFE midpoint of video neighbours" if ordinal in replacements else "extracted video frame",
                "source_frame_sha256":assets.sha256(source_frame.read_bytes())})
        clips.append({"state":state,"rate":int(rate),"rows":rows})
        for suffix in ("v2.mp4","v2.video-report.json","v2.prompt.txt"):
            copyfile(source/f"{state}.{suffix}",origin/f"{state}.{suffix}")
        copyfile(loop/f"{state}.loop.report.json",origin/f"{state}.loop.report.json")
        copyfile(loop/f"{state}.strip.json",origin/f"{state}.strip.json")
        copyfile(source/f"{state}-v2-frames/frames.report.json",origin/f"{state}.frames.report.json")
        video_bytes=(origin/f"{state}.v2.mp4").read_bytes()
        verify_mp4(video_bytes)
        records.append({"state":state,"origin":"real Grok Imagine video, chroma extraction and verified loop",
            "mp4_sha256":assets.sha256(video_bytes),"native_fps":str(fps),"source_cycle_start":loop_report["cycle"]["start"],
            "source_cycle_length":length,"finalized_samples":count,"finalized_rate":str(rate),
            "cycle_kind":loop_report["cycle"]["kind"],"seam_ratio":loop_report.get("resampled_seam_ratio"),
            "jump_repair":loop_report.get("jump_repair"),
            "jolt_warning":(loop_report.get("jolt") or {}).get("warnings",[]),"frames":rows})
    if palette_file is None:
        palette=build_shared_palette(images,64)
        palette_source=None
    else:
        palette_data=Path(palette_file).read_bytes()
        palette=json.loads(palette_data)["colors"]
        if not isinstance(palette,list) or not 1 <= len(palette) <= 256 or any(
            not isinstance(color,list) or len(color)!=3 or any(
                type(value) is not int or not 0 <= value <= 255 for value in color) for color in palette):
            raise ValueError("palette requires 1..256 explicit RGB colors")
        (origin/"customization-palette.json").write_bytes(palette_data)
        palette_source={"file":"customization-palette.json","sha256":assets.sha256(palette_data)}
    inputs=output/"motion-source"
    requests=[]
    cursor=0
    for clip in clips:
        state,rate=clip["state"],clip["rate"]
        folder=inputs/state
        folder.mkdir(parents=True)
        expected,entries={},[]
        for row in clip["rows"]:
            image=apply_palette(images[cursor],palette)
            cursor+=1
            filename=f"{row['number']:03d}.png"
            encoded=assets.png_bytes(image)
            (folder/filename).write_bytes(encoded)
            expected[f"{state}/{filename}"]=assets.sha256(encoded)
            entries.append({"id":f"video.{state}.{row['source_video_ordinal']}","file":filename,
                "duration":float(Fraction(1,rate))})
        descriptor=write(folder/"motion.json",{"kind":"sprite-gen-asset","version":1,"loop":True,"frames":entries,
            "origin":"video-derived finalized pixels; explicit common projection and palette"})
        expected[f"{state}/motion.json"]=assets.sha256(descriptor.read_bytes())
        requests.append({"id":state+".right","state":state,"file":state+"/motion.json","source_state":None,
            "timebase":120000,"loop":True,"fps":str(rate),"timing_policy":"exact","source_mode":"external","expected_files":expected})
    spec=write(output/"motion-import.json",{"kind":"parts-motion-import","schema_version":1,
        "id":"sd.explorer.video.motion","rig":index["rig"],"clips":requests})
    imported=parts_motion.import_motion(assembly,spec,inputs)
    preview=Image.new("RGBA",(8*128,3*128))
    for row,clip in enumerate(clips):
        for column in range(8):
            number=min(len(clip["rows"])-1,round(column*len(clip["rows"])/8))
            image=Image.open(inputs/clip["state"]/f"{number:03d}.png").convert("RGBA")
            preview.alpha_composite(image,(column*128,row*128))
    (output/"actual-video-motion-review.png").write_bytes(assets.png_bytes(preview))
    report={"kind":"sd-video-motion-candidate","schema_version":1,"source_origin":"real video",
        "selected_reference_sha256":SELECTED_SHA,"source_canvas_sha256":assets.sha256(source_canvas),
        "common_projection":{"reference_bbox":list(reference_box),"standing_height":standing_height,
            "target_body_height":112,"native_canvas":list(first.size),"scaled_canvas":list(scaled_size),
            "offset":list(offset),"resample":"common premultiplied RGBA resize_cell"},
        "palette":palette,"palette_size":len(palette),"palette_source":palette_source,
        "alpha":"binary, same pinned palette across the three states",
        "clips":records,"samples":len(images),"candidate":imported["candidate"],"annotation_template":imported["annotation_template"],
        "manual_pose_generator_used":False,"appearance_clips_baked":0,"frozen":False,"release_ready":False,
        "remaining":"verify per-sample sockets and body segmentation, then fit actual part variants on this exact motion",
        "recipe_sha256":assets.sha256(Path(__file__).read_bytes()),"toolchain":toolchain()}
    write(output/"motion-candidate-provenance.json",report)
    return {"candidate":str(library/imported["candidate"]),"annotations":str(library/imported["annotation_template"]),
        "states":list(STATES),"samples":len(images),"frozen":False,"appearance_clips_baked":0}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",type=Path,required=True)
    parser.add_argument("--out",type=Path,required=True)
    parser.add_argument("--walk-loop",default="walk-v2-loop",help="inspected walk loop directory, relative to --source")
    parser.add_argument("--palette",type=Path,help="explicit shared customization RGB palette JSON")
    args=parser.parse_args()
    print(json.dumps(prepare(args.source,args.out,walk_loop=args.walk_loop,palette_file=args.palette),ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
