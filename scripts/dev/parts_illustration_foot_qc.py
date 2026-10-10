# SPDX-License-Identifier: Apache-2.0
"""Explicit closed-foot design coverage for unapproved native motion candidates.

The guide comes from authored body ownership, and itself still needs visual
review. It neither masks the rendered body nor silently fills missing art.
"""

from pathlib import Path
from copy import deepcopy
import argparse
import shutil
import numpy as np
from scripts.dev.parts_illustration_motion import validate_sources
from PIL import Image
from sprite_gen.spec import parts_workspace as w
from sprite_gen.frames import parts_production as p
from sprite_gen.compose import export_parts


def check_feet(source_art, out):
    validate_sources(source_art, out)
    root = out / "library"
    assembly = root / "assembly.json"
    index, registry = w.read_workspace(assembly)
    motion = registry[index["motion"]["id"]]
    results = {}
    for name in ("footwear.sneakers", "socks.crew"):
        identity = "illustration.motion." + name
        state = p._load(root, identity)
        config = deepcopy(state["config"])
        checks = {}
        for clip in motion["clips"]:
            for sample in clip["samples"]:
                required = Image.new("L", (768, 768))

                for node in ("foot_back", "foot_front"):
                    im = Image.open(
                        root
                        / f"inputs/body-segments/{node}/{clip['id']}/{sample['id']}.png"
                    ).convert("RGBA")
                    required = Image.fromarray(
                        np.maximum(np.asarray(required), np.asarray(im.getchannel("A")))
                    )
                path = (
                    root
                    / f"inputs/closed-foot-interfaces/{name}/{sample['pose_id']}.png"
                )
                path.parent.mkdir(parents=True, exist_ok=True)
                required.save(path)
                checks[sample["pose_id"]] = {
                    "required": path.relative_to(root).as_posix()
                }
        config["pose_checks"] = checks
        material = root / f"inputs/parts/{name}/design-source.png"
        source = source_art / "library"
        old = p._load(source, "illustration." + name)
        row = old["tracks"]["main"]["poses"]["fit.01"]
        record = p.candidate_record(source, old, "fit.01", row["selected"])

        shutil.copy2(
            source / row["selected"] / record["images"]["part"]["file"], material
        )
        description = (
            "Cream ribbed crew socks with navy cuff stripes, including covered heels and toes"
            if name.startswith("socks")
            else "Navy and cream closed-toe ankle sneakers, including heel and sole"
        )
        config["prompt"] = {
            "text": description
            + ". Draw ONLY these independent parts on the exact body pose and canvas coordinates. Match design-source reference material and the required foot coverage guide. Correct toe angle and sole silhouette for this exact frame. Never draw the body or a finished character.",
            "refs": [material.relative_to(root).as_posix()],
        }
        cfg = root / f"inputs/closed-foot-interfaces/{name}/config.json"
        cfg.write_bytes(w.json_bytes(config))
        p.configure(assembly, identity, cfg)
        index, registry = w.read_workspace(assembly)
        p.adopt(assembly, identity, root / index["documents"][identity])
        checked = p.check(assembly, identity)
        built = p.build(assembly, identity)
        guide = p.sheet(assembly, identity, poses=sorted(checks))
        results[name] = {
            "check": checked,
            "build": built,
            "generation_reference": guide["reference"],
            "coverage_rule": "closed-foot designs only; required foot pixels from authored body ownership",
            "body_ownership_visually_approved": False,
            "art_approved": False,
        }
    (root.parent / "closed-foot-qc.json").write_bytes(w.json_bytes(results))
    export_parts.export(
        assembly,
        root.parent / "export-checked",
        interpolation="frame",
        title="실제 walk/run · 위치 검증 후보 · 닫힌 신발/양말 덮임 검사 · 아트 미승인",
    )
    print({n: r["check"]["summary"] for n, r in results.items()})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    check_feet(args.source.absolute(), args.out.absolute())
