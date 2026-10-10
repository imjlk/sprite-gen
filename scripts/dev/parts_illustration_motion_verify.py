# SPDX-License-Identifier: Apache-2.0
"""Verify every video sample and optional layer selection without approving art."""

from pathlib import Path
from fractions import Fraction
import json, subprocess, argparse
from PIL import Image, ImageDraw
from sprite_gen.compose.parts_runtime import PartsPlayer
from sprite_gen.compose import export_parts
from sprite_gen.spec import parts_workspace as w, parts_contract as c


def verify(out):
    root = out / "library"
    assembly = root / "assembly.json"
    index, registry = w.read_workspace(assembly)
    slots = ["hair", "eyes", "upper", "lower", "footwear", "socks"]
    selections = {
        s: next(
            c.reference(v)
            for v in registry.values()
            if v.get("role") == "part" and s in v["bindings"][0]["occupies"]
        )
        for s in slots
    }
    motion = index["motion"]
    cases = []
    expected = []
    summary = {}
    all_frames = {}
    for state, count in [("walk", 23), ("run", 20)]:
        player = PartsPlayer.from_workspace(assembly, state=state)
        for n in range(count):
            # integer browser time inside this exact sample; Python and JS get the same time.
            ms = (n * 1000 + 23) // 24 + 1
            player.seek(Fraction(ms, 1000))
            player.set_selection(selections)
            base = player.layers(interpolate=False)
            assert (
                base["sample_id"] == f"sample.{n}"
                and base["motion"] == motion
                and base["masks"] == []
            )
            image, _ = player.frame()
            all_frames[f"{state}.{n + 1:02d}"] = image
            for disabled in [None, *slots, "all"]:
                current = selections.copy()
                if disabled == "all":
                    current = {s: None for s in slots}
                elif disabled:
                    current[disabled] = None
                player.set_selection(current)
                scene = player.layers(interpolate=False)
                assert (
                    scene["sample_id"] == base["sample_id"]
                    and scene["sample_tick"] == base["sample_tick"]
                    and scene["motion"] == base["motion"]
                    and scene["masks"] == []
                )
                if disabled == "all":
                    bare, _ = player.frame()
                    source = Image.open(
                        root / f"inputs/body/{state}/{n:03d}.png"
                    ).convert("RGBA")
                    assert bare.tobytes() == source.tobytes()
                selection = {s: ref["id"] if ref else "" for s, ref in current.items()}
                cases.append({"state": state, "time": ms, "selection": selection})
                expected.append(
                    [
                        {k: layer[k] for k in ("id", "node", "provides", "position")}
                        for layer in scene["layers"]
                    ]
                )
        summary[state] = {
            "frames": count,
            "swap_cases": count * 8,
            "body_source_pixel_identical": count,
        }
    _, manifest, bundle = export_parts.pack(
        root, index, registry, title="실제 모션 · 미승인 파츠 검증 후보"
    )
    js = "const {LayerRuntime}=require(process.argv[1]);const d=JSON.parse(require('fs').readFileSync(0,'utf8'));const r=new LayerRuntime(d.bundle);process.stdout.write(JSON.stringify(d.cases.map(c=>r.scene(c.state,c.time,c.selection).layers.map(({id,node,provides,position})=>({id,node,provides,position})))));"
    result = subprocess.run(
        ["node", "-e", js, "./sprite_gen/serve/parts_player/runtime.js"],
        input=json.dumps({"bundle": bundle, "cases": cases}),
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(result.stdout) == expected
    report = {
        "motions": summary,
        "python_js_cases_identical": len(cases),
        "runtime_masks": 0,
        "body_motion_unchanged": True,
        "appearance_clips_baked": 0,
        "art_approved": False,
        "renderer_checks_are_not_visual_approval": True,
    }
    (out / "motion-verification.json").write_bytes(w.json_bytes(report))
    board = Image.new("RGB", (1200, 500), "#dddddf")
    draw = ImageDraw.Draw(board)
    for col, pose in enumerate(
        ["walk.01", "walk.07", "walk.13", "run.01", "run.07", "run.13"]
    ):
        im = all_frames[pose].copy()
        im.thumbnail((200, 450))
        board.paste(im, (col * 200, 25), im)
        draw.text((col * 200 + 10, 6), pose, fill="black")
    draw.text(
        (10, 475),
        "ACTUAL VIDEO BODY / SOURCE-ART REGISTRATION CANDIDATES / FOOT FIT REJECTED / NOT APPROVED",
        fill="black",
    )
    board.save(out / "all-parts-motion-review.jpg")
    print(report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    verify(args.out.absolute())
