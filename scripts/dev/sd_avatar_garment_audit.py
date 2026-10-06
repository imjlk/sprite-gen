"""Per pose: garment pixels the model drew vs what the layered composite shows, by covering layer."""
import json, sys
from pathlib import Path
from collections import Counter
from PIL import Image
from sprite_gen._deps import np
from sprite_gen.frames import parts_pose_sheet as sh, parts_segments as seg_mod
from sprite_gen.spec import parts_workspace as ws
B=Path("assets/sd-explorer-avatar-20261006"); A=B/"library/assembly.json"
name=sys.argv[1]; slot=name.split(".")[0]
idx,reg=ws.read_workspace(A); m=reg[idx["motion"]["id"]]; clips={x["id"]:x for x in m["clips"]}
topo=reg[reg[idx["rig"]["id"]]["topology"]["id"]]; layers=topo["layers"]
split=json.load(open(B/f"split/{name}/import.json"))
body_nodes=["arm_back","leg_back","root","leg_front","arm_front"]
entries=[("body",n,layers.index(n),0) for n in body_nodes]+[("part",p["id"],layers.index(p["node"]),1) for p in split["pieces"]]
entries.sort(key=lambda e:(e[2],e[3],e[1]))
def truth_mask(red):
    t=np.asarray(red)
    mask=(t[...,3]>0)&(t[...,0]>150)&(t[...,1]<90) if slot=="upper" else (t[...,3]>0)&(t[...,2]>t[...,0])&(t[...,2]>90)
    keep=np.zeros_like(mask)
    for comp in seg_mod._components(mask):
        if comp.sum()>=12: keep|=comp
    return keep
total=Counter(); per={}
for page in sorted((B/"sheets/clothing").glob("page-*/pose-sheet.json")):
    art=B/"drawn"/f"{name}.p{page.parent.name.split('-')[1]}.png"
    for e,s,red,sheet in sh.drawn_cells([(page,art)],idx,clips,80):
        pose=s["pose_id"]; x0,y0,x1,y1=sheet["crop"]
        truth=np.zeros((128,128),bool); truth[y0:y1,x0:x1]=truth_mask(red)
        top=np.full((128,128),"",dtype=object); have=np.zeros((128,128),bool)
        for kind,pid,_,_ in entries:
            f=B/f"body/{pid}/{e['clip']}/{e['sample']}.png" if kind=="body" else B/f"split/{name}"/next(p for p in split["pieces"] if p["id"]==pid)["pose_map"][pose]["image"]["file"]
            a=np.asarray(Image.open(f).convert("RGBA"))[...,3]>0
            top[a]=f"{kind}.{pid}"
            if kind=="part": have|=a
        miss=truth&~np.char.startswith(top.astype(str),"part.")
        c=Counter(); c["dropped (in no piece)"]=int((miss&~have).sum())
        c.update(Counter(top[miss&have]))
        total.update(c); per[pose]=sum(c.values())
print(name,"truth-missing by cause:",total.most_common())
print("worst poses:",sorted(per.items(),key=lambda kv:-kv[1])[:6])
