# SPDX-License-Identifier: Apache-2.0
"""Draft independent generation bounds from explicit body-node ownership.

Drafts are source-bound authoring aids. They do not certify anatomical labels,
infer clothing from RGB, or create required-coverage masks automatically.
"""

from pathlib import Path
from PIL import Image, ImageDraw
from sprite_gen.compose import compose_parts
from sprite_gen.curate import parts_motion
from sprite_gen.frames import parts_production as p
from sprite_gen.spec import parts_assets as a, parts_contract as c, parts_workspace as w


def scaffold(assembly, part, slot, layers, prompt_file, out, *, padding=8, states=None):
    root, index, registry, _ = parts_motion._active(assembly)
    if not index["body"]:
        raise c.PartsContractError(
            "import a body before scaffolding independent layers"
        )
    rig = registry[index["rig"]["id"]]
    top = registry[rig["topology"]["id"]]
    slots = {s["id"]: s for s in top["slots"]}
    c._members([slot], slots, "slot")
    nodes = {n["id"] for n in top["nodes"]}
    if not 0 <= padding <= max(rig["canvas"].values()):
        raise c.PartsContractError("padding exceeds native canvas")
    specs = []
    for text in layers:
        fields = text.split(":")
        if len(fields) != 3:
            raise c.PartsContractError(
                "layer must be id:render_node:body_node[,body_node]"
            )
        identity, node, source_nodes = fields
        source_nodes = source_nodes.split(",")
        c._id(identity, "layer")
        c._members([node], slots[slot]["nodes"] or nodes, "layer node")
        c._members(source_nodes, nodes, "body source nodes")
        specs.append(
            {"id": identity, "node": node, "body_nodes": source_nodes, "targets": {}}
        )
    if not specs:
        raise c.PartsContractError(
            "choose explicit body nodes for at least one render layer"
        )
    chosen = list(states or index["states"])
    c._members(chosen, index["states"], "states")
    plan = p._part_plan(index, registry, None, chosen)
    load = compose_parts.AssetLoader(root, index)
    payloads = {}
    counts = {}
    motion = registry[index["motion"]["id"]]
    poses = {
        (clip["id"], sample["id"]): sample["pose_id"]
        for clip in motion["clips"]
        for sample in clip["samples"]
    }
    for clip in plan["clips"]:
        for sample in clip["samples"]:
            pose = poses[clip["id"], sample["id"]]
            counts.setdefault(pose, {})
            for layer in specs:
                canvas = Image.new(
                    "RGBA", (rig["canvas"]["width"], rig["canvas"]["height"])
                )
                for entry in sample["draw"]:
                    if entry["role"] == "body" and entry["node"] in layer["body_nodes"]:
                        raster = load(
                            entry["pack"], entry["placement"]["image"]
                        ).convert("RGBA")
                        canvas.alpha_composite(raster, tuple(entry["offset"]))
                box = canvas.getbbox()
                if box is None:
                    raise c.PartsContractError(
                        f"{pose}/{layer['id']}: body nodes are absent; author a different ownership profile"
                    )
                left, up, right, down = box
                target = [
                    max(0, left - padding),
                    max(0, up - padding),
                    min(canvas.width, right + padding),
                    min(canvas.height, down + padding),
                ]
                layer["targets"][pose] = target
                counts[pose][layer["id"]] = box
                board = canvas.copy()
                ImageDraw.Draw(board).rectangle(
                    tuple(target), outline=(60, 130, 230, 255), width=2
                )
                payloads[f"{pose}.{layer['id']}.png"] = a.png_bytes(board)
    prompt = Path(prompt_file).read_text(encoding="utf-8").strip()
    if not prompt:
        raise c.PartsContractError("give a concrete design prompt")
    config = p._CONFIG(
        {
            "kind": "parts-production-config",
            "schema_version": 1,
            "part_id": part,
            "design_id": part,
            "slot": slot,
            "node": specs[0]["node"],
            "socket": next(
                s["id"]
                for s in top["sockets"]
                if s["node"]
                == next(n["id"] for n in top["nodes"] if n["parent"] is None)
            ),
            "states": chosen,
            "prompt": {"text": prompt},
            "independent": {"layers": [layer for layer in specs]},
        },
        "draft",
    )
    evidence = {
        "kind": "parts-scaffold",
        "schema_version": 1,
        "rig": index["rig"],
        "body": index["body"],
        "motion": index["motion"],
        "sources": [
            {k: layer[k] for k in ("id", "node", "body_nodes")} for layer in specs
        ],
        "body_bounds": counts,
        "config": config,
        "required_coverage_authored": False,
        "art_approved": False,
    }
    folder = a.publish_object(
        root,
        "scaffolds",
        part,
        c.content_digest(evidence),
        payloads | {"profile.json": w.json_bytes(evidence)},
    )
    destination = Path(out).absolute()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(w.json_bytes(config))
    return {
        "config": str(destination),
        "profile": str(folder / "profile.json"),
        "poses": len(counts),
        "status": "draft",
        "next": "inspect bounds, author independent targets and optional QA interfaces, then parts-part-init",
        "release_ready": False,
    }


def add_arguments(parser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--part", required=True)
    parser.add_argument("--slot", required=True)
    parser.add_argument("--layer", action="append", dest="layers", required=True)
    parser.add_argument("--prompt-file", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--padding", type=int, default=8)
    parser.add_argument("--state", action="append", dest="states")


def run(**kwargs):
    return w._report(lambda: scaffold(**kwargs))
