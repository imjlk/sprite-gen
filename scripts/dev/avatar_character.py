# SPDX-License-Identifier: Apache-2.0
"""A new SD character from nothing but a character config, through the production workflow.

    base      the base still: image model -> still-audit-style snap to its grid and palette
    video     walk and run loops (video-set, image-to-video)
    library   the SD avatar topology, the loops as native frames, sockets, pose IDs, frozen motion
    init      productions: the body (labels) and every design taken from the SD explorer
    produce   for each production: check -> sheet -> draw -> slice -> check -> build; later runs draw
              only what is missing, rejected, failing a check or drawn over a changed worn part
    recut     apply changed cut settings (init rewrites the configs) to the drawn pages, no drawing
    appearance, showcase, status

The config is <base>/character.json; the ones used so far are in avatar_characters/ (copy one).

Designs are shared with the SD explorer by design_id: each new body draws the same hoodie,
hair or hat again for its own poses, with the explorer's design image as the reference.
Approval stays a human review in the desk (parts-review-serve).
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
import json
from pathlib import Path
import shutil
import subprocess
import sys

from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
import sd_avatar_process as avatar  # noqa: E402
import sd_explorer_rebuild as rebuild  # noqa: E402

from sprite_gen._deps import np  # noqa: E402
from sprite_gen.compose.parts_runtime import PartsPlayer  # noqa: E402
from sprite_gen.curate import parts_appearance, parts_motion, parts_poses, parts_sockets  # noqa: E402
from sprite_gen.effects import breathe  # noqa: E402
from sprite_gen.frames import parts_loop_native as native, parts_production as production  # noqa: E402
from sprite_gen.spec import parts_contract as contract, parts_workspace as workspace  # noqa: E402

EXPLORER = Path("assets/sd-explorer-avatar-20261006")
HELD_PROMPT = """Edit image 1, a pixel-art sprite of a chibi character in pure side view facing right on a flat pure magenta #FF00FF background. Image 2 shows another chibi character wearing {what}.

Give the character in image 1 exactly that {what}: the same shape, colors, shading and pixel style as image 2, fitted to this character's own head size and shape.{fit} Change nothing else: the whole character from head to feet stays in the picture at the same position and size, with the same pose, body, skin color, clothes and outline; do not recolor or reshade the skin. Keep the image size and the flat #FF00FF background. """
WHAT = {"hair": "hair", "expression": "face (eye, eyebrow and mouth only)", "headwear": "hat"}
FIT = {"hair": " The hair covers the whole top and back of the skull, so no bald scalp shows. The eye, eyebrow and"
               " mouth already drawn on the face stay fully visible: bangs end above the eyebrow.",
       "expression": " Draw only the eye, eyebrow and mouth on the existing face.",
       "headwear": " The hat sits over the hair already drawn, wide enough that no hair pokes through its crown and"
                   " no bald scalp shows; the brim sits above the eyebrow so the eye stays fully visible."}


CAPPED_KEEP = (" Below the brim the hairstyle keeps its own length and shape from image 2: a ponytail, braid or long"
               " hair still hangs down the back exactly as long as without the cap.")


def config(base):
    return json.loads((base / "character.json").read_text())


def write(path, value):
    return rebuild.write(path, value)


def stage_base(base):
    """Draw the base still and snap it to its own grid, palette and outline (design/base.*)."""
    character = config(base)
    design = base / "design"
    design.mkdir(parents=True, exist_ok=True)
    raw = design / "base.raw.png"
    if not raw.exists():
        prompt = base / "prompts/base.txt"
        prompt.parent.mkdir(parents=True, exist_ok=True)
        prompt.write_text(character["body_prompt"])
        done = subprocess.run([".venv/bin/sprite-gen", "gen", "--provider", "codex", "--model", "gpt-5.6-sol",
                               "--prompt-file", str(prompt), "--out", str(raw), "--report", str(raw.with_suffix(".report.json"))],
                              capture_output=True, text=True)
        if done.returncode:
            raise RuntimeError(done.stderr[-400:])
    result = rebuild.stage_native(base, character["target_height"], character.get("colors", 28))
    write(design / "stage-native.json", result)
    return result


def stage_video(base):
    character = config(base)
    log = base / "video-set.log"
    command = [".venv/bin/sprite-gen", "video-set", "--base", f"side={base / 'design/base-side.png'}", "--facing", "right",
               "--states", "walk,run", "--out-dir", str(base / "video"), "--character", character["character"],
               "--anchor", "motion-auto", "--concurrency", "2", "--key", "green"]
    with log.open("w") as out:
        done = subprocess.run(command, stdout=out, stderr=subprocess.STDOUT)
    return {"exit": done.returncode, "log": str(log), "table": (base / "video/table.md").read_text()
            if (base / "video/table.md").exists() else None}


def stage_library(base):
    """The SD avatar topology for this body; idle (breathing), walk and run frozen with pose IDs."""
    character = config(base)
    prefix = character["prefix"]
    root = base / "library"
    if root.exists():
        raise ValueError(f"{root} exists")
    root.mkdir(parents=True)
    nodes = [{"id": "root"}] + [{"id": n, "parent": "root", "required": False} for n in avatar.LAYERS if n != "root"]
    seal = avatar.seal_write
    topology = seal(root, "profiles/topology.json", {"kind": "parts-topology", "schema_version": 1,
        "id": f"{prefix}.topology", "nodes": nodes,
        "sockets": [{"id": s, "node": "root", "required": True} for s in ("root", "head", "torso")],
        "slots": [{"id": slot, "nodes": n, "sockets": s, "optional": True, "empty_policy": "no-piece"}
                  for slot, (_, _, n, s) in avatar.SLOTS.items()], "layers": avatar.LAYERS})
    rig = seal(root, "profiles/rig.json", {"kind": "parts-rig", "schema_version": 1, "id": f"{prefix}.rig",
        "topology": contract.reference(topology), "view": "right", "style": "pixel.v1",
        "canvas": {"width": rebuild.CANVAS, "height": rebuild.CANVAS}, "default_body_ids": [f"{prefix}.body"]})
    explorer_index, explorer = workspace.read_workspace(EXPLORER / "library/assembly.json")
    groups = explorer[explorer_index["catalog"]["id"]]
    catalog = seal(root, "catalog.json", {"kind": "parts-catalog", "schema_version": 1, "id": f"{prefix}.catalog",
        "rig": contract.reference(rig), "topology": contract.reference(topology), "groups": groups["groups"],
        "slots": [{"id": slot, "group": group, "label": label} for slot, (group, label, _, _) in avatar.SLOTS.items()],
        "palette_channels": groups["palette_channels"]})
    assembly = root / "assembly.json"
    assembly.write_bytes(workspace.json_bytes({"kind": "parts-workspace", "schema_version": 1, "id": f"{prefix}.workspace",
        "documents": {topology["id"]: "profiles/topology.json", rig["id"]: "profiles/rig.json", catalog["id"]: "catalog.json"},
        "rig": contract.reference(rig), "catalog": contract.reference(catalog), "states": ["idle", "walk", "run"]}))
    still = Image.open(base / "design/base.canvas.png").convert("RGBA")
    idle = base / "idle-breathe"
    idle.mkdir(parents=True, exist_ok=True)
    frames, _ = breathe.bake_breathe_sequence([still]*rebuild.IDLE_FRAMES, rebuild.BREATHE)
    for n, frame in enumerate(frames):
        frame.save(idle / f"frame-{n:03d}.png")
    built = native.build(assembly, base / "native", [("idle", idle, str(rebuild.IDLE_FPS)),
        ("walk", base / "video/side-walk/loop"), ("run", base / "video/side-run/loop")],
        body_height=json.loads((base / "design/stage-native.json").read_text())["native_height"], baseline=rebuild.BASELINE,
        palette=base / "design/base.palette.json", motion_id=f"{prefix}.motion", resample="lattice", outline="darkest")
    imported = parts_motion.import_motion(assembly, Path(built["spec"]), Path(built["source_root"]), strict_motion=True)
    candidate = workspace.decode_json((root / imported["candidate"]).read_bytes())
    first = candidate["motion"]["clips"][0]["samples"][0]
    frame = Image.open((root / imported["candidate"]).parent / first["image"]["file"]).convert("RGBA")
    sockets = rebuild.base_sockets(still)
    found = {}
    for name, (position, bounds) in sockets.items():
        x, y = position
        template = still.crop((x+bounds[0], y+bounds[1], x+bounds[2], y+bounds[3]))
        found[name] = parts_sockets.match(template, frame, position, {"bounds": bounds, "radius": 8, "alpha_weight": 3,
                                                                     "min_alpha": 128, "rgb_threshold": 24})["position"]
    clip = candidate["motion"]["clips"][0]
    ref = {"clip": clip["id"], "sample": first["id"]}
    tracks = [{"socket": "root", "mode": "constant", "reference": {**ref, "position": [0, 0]}}]
    for c in candidate["motion"]["clips"]:
        cref = {"clip": c["id"], "sample": c["samples"][0]["id"]}
        # Each clip tracks from its own first frame, located from the idle reference.
        cframe = Image.open((root / imported["candidate"]).parent / c["samples"][0]["image"]["file"]).convert("RGBA")
        for name in ("head",):
            x, y = found[name]
            bounds = sockets[name][1]
            template = frame.crop((x+bounds[0], y+bounds[1], x+bounds[2], y+bounds[3]))
            start = parts_sockets.match(template, cframe, found[name], {"bounds": bounds, "radius": 10, "alpha_weight": 3,
                                                                       "min_alpha": 128, "rgb_threshold": 24})["position"]
            tracks.append({"socket": name, "clips": [c["id"]], "reference": {**cref, "position": start}, "bounds": bounds,
                           "radius": 8, "max_cost": 600})
    # The torso moves with the head in a side-view chibi; its own small region is hidden by
    # swinging arms often enough to drag a template track onto the limbs.
    tracks.append({"socket": "torso", "mode": "relative", "to": "head", "reference": {**ref, "position": found["torso"]}})
    spec = write(base / "socket-track.json", {"kind": "parts-socket-track", "schema_version": 1, "tracks": tracks})
    tracked = parts_sockets.track_sockets(assembly, imported["candidate"], root / imported["annotation_template"], spec,
                                          base / "annotations.json", accept=True, preview=base / "socket-track.png")
    poses = parts_poses.assign(assembly, base / "annotations.json")
    frozen = parts_motion.freeze_motion(assembly, imported["candidate"], base / "annotations.json")
    return {"motion": frozen["motion"], "poses": poses["poses"], "unverified_sockets": tracked["unverified_sockets"],
            "quality": [{k: r.get(k) for k in ("clip", "status", "over")} for r in imported["motion_quality"]],
            "samples": built["clips"]}


def _face_region(still):
    alpha = np.asarray(still)[..., 3] > 0
    top = int(np.where(alpha.any(axis=1))[0].min())
    cols = np.where(alpha[top:top+40].any(axis=0))[0]
    return [int(cols.min() + 0.4*(cols.max()-cols.min())), top+6, int(cols.max())+4, top+40]


def _without_skin(source, still, out, distance=24, share=0.7):
    """A held part's palette without the skin tones it was read off with.

    Design palettes come from a whole character, so a face or hair palette holds skin
    shades; a repainted cheek then passes the palette test and is cut as part of the face or
    the hair. Colors close to the body's dominant colors (its skin) are left out; a hair
    highlight that close still snaps to the nearest hair color.
    """
    pixels = np.asarray(still)
    counts = {}
    rgb = pixels[pixels[..., 3] > 0][:, :3]
    for color in map(tuple, rgb[rgb.astype(np.int32) @ np.array([299, 587, 114]) >= 60*1000].tolist()):  # not outline ink
        counts[color] = counts.get(color, 0) + 1
    dominant, total, seen = [], sum(counts.values()), 0
    for color, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        dominant.append(color)
        seen += n
        if seen >= share*total:
            break
    dominant = np.array(dominant)
    colors = [c for c in json.loads(source.read_text())["colors"]
              if np.abs(np.array(c)[None] - dominant).max(-1).min() > distance]
    write(out, {"colors": colors})


def stage_init(base):
    """The body production and one production per design taken from the SD explorer."""
    character = config(base)
    assembly = base / "library/assembly.json"
    root = assembly.parent
    inputs = root / "inputs/design"
    inputs.mkdir(parents=True, exist_ok=True)
    draw = {"provider": "codex", "model": "gpt-5.6-sol"}
    prefix = character["prefix"]
    made = []
    body = {"kind": "parts-production-config", "schema_version": 1, "part_id": f"{prefix}.body", "design_id": f"{prefix}.body",
            "role": "body", "sheet": {"crop": [16, 8, 112, 124], "scale": 4, "page_size": 12, "aspect": "1:1"},
            "cut": {"tolerance": 80},
            "body": {"colors": avatar.SEGMENTS, "near_lighter": [["arm_back", "arm_front"], ["leg_back", "leg_front"]],
                     "tolerance": 60},
            "prompt": {"text": avatar.LABEL_PROMPT + avatar.KEEP_LAYOUT}, "draw": draw}
    configs = {"body": body}
    garment, shoe, style = avatar._garment_prompts()
    hq = (avatar.HQ / "generate_garments.py").read_text()
    hq_body = eval(hq.split("BODY = ", 1)[1].split("\n}\n", 1)[0] + "\n}")
    hq_shoes = eval(hq.split("SHOES = ", 1)[1].split("}\n", 1)[0] + "}")
    still = Image.open(base / "design/base.canvas.png").convert("RGBA")
    segments = f"productions/{prefix}.body/build/segments/segments.json"
    for slot, name in character["designs"].items():
        part_id = rebuild.PART_ID[name]
        source = EXPLORER / "design"
        reference = f"{name}.zoom.png" if slot == "footwear" else f"{name}.raw.png"
        shutil.copy2(source / reference, inputs / reference)
        palette = f"{name}.palette5.json" if (source / f"{name}.palette5.json").exists() and slot == "footwear" else f"{name}.palette.json"
        shutil.copy2(source / palette, inputs / palette)
        base_config = {"kind": "parts-production-config", "schema_version": 1, "part_id": part_id, "design_id": part_id,
                       "slot": slot, "node": avatar.SLOTS[slot][2][0], "draw": draw}
        if slot in ("hair", "expression", "headwear"):
            region = _face_region(still) if slot == "expression" else rebuild.PARTS[name][4]
            if slot == "expression":  # its crumbs of repainted cheek are skin; hair highlights may be skin-like
                _without_skin(inputs / palette, still, inputs / f"{name}.held-palette.json")
                palette = f"{name}.held-palette.json"
            config_value = {**base_config, "socket": "head", "mode": "held", "reference_pose": "idle.01",
                            "sheet": {"crop": [8, 0, 120, 96], "scale": 4, "page_size": None, "aspect": "1:1"},
                            "cut": {"method": "covering", "region": region, "palette": f"inputs/design/{palette}",
                                    "near_body": None, "close_body": 0, "fill_holes": 0, "keep_palette": 48, "prune": 2},
                            "prompt": {"text": HELD_PROMPT.format(what=WHAT[slot], fit=FIT[slot]) + style.format(px=4),
                                       "refs": [f"inputs/design/{reference}"]}}
            if slot == "expression":
                config_value["cut"].update(inset=1, specks=6)  # not the redrawn profile line, not crumbs
            if slot == "hair":
                config_value["cut"]["rim"] = 3  # hair drawn a little inside the skull
            if slot == "headwear":
                config_value["cut"]["largest"] = 25  # one solid hat, not face lines redrawn under the brim
            if slot != "expression":
                # Drawn over what is under them at runtime: the face (eyes stay clear of bangs and
                # brims), and for the hat the hair it sits on.
                face = rebuild.PART_ID[character["designs"]["expression"]] if "expression" in character["designs"] else None
                under = {"hair": [face], "headwear": [face, rebuild.PART_ID[character["designs"].get("hair", "")] if "hair" in character["designs"] else None]}[slot]
                config_value["wearing"] = [w for w in under if w]
            if slot == "hair":
                config_value["cover_rule"] = {"tag": "headwear", "show": "uncovered"}
                if "headwear" in character["designs"]:
                    config_value["variants"] = [{"id": "capped", "cover_rule": {"tag": "headwear", "show": "covered"},
                                                 "wearing": [w for w in (face, rebuild.PART_ID[character["designs"]["headwear"]]) if w],
                                                 "prompt": {"text": avatar.CAPPED_PROMPT + CAPPED_KEEP,
                                                            "refs": [f"inputs/design/{reference}"]}}]
            if slot == "headwear":
                config_value["covers"] = ["headwear"]
        else:
            feet = slot == "footwear"
            if feet:
                desc = avatar.NEW[name][1] if name in avatar.NEW else hq_shoes[name]
                text = shoe.format(desc=desc) + style.format(px=8)
            else:
                desc, where = (avatar.NEW[name][1], avatar.GARMENT_WORDS[name]) if name in avatar.NEW else hq_body[name]
                text = garment.format(garment=desc, where=where) + style.format(px=4)
            config_value = {**base_config, "socket": rebuild.PARTS[name][2], "mode": "raster",
                            "sheet": {"crop": [int(v) for v in (avatar.FEET_CROP if feet else avatar.CLOTHING_CROP).split(",")],
                                      "scale": 8 if feet else 4, "page_size": 12, "aspect": "3:2" if feet else "1:1",
                                      "columns": 3 if feet else None},
                            "cut": {"method": "difference", "body_threshold": rebuild.PARTS[name][5], "tolerance": 80,
                                    "near_body": avatar.NEAR_BODY.get(name, 3), "min_island": 6 if feet else 12,
                                    "outline": True, "fill_holes": 6, "close_body": 5, "region": None,
                                    "palette": f"inputs/design/{palette}", "rescue": 16},
                            "pieces": [{"id": p, "node": n, "segments": s} for p, n, s in avatar.SPLITS[slot]],
                            "by_component": slot in avatar.WHOLE, "segments": segments,
                            "prompt": {"text": text + avatar.KEEP_LAYOUT, "refs": [f"inputs/design/{reference}"]}}
        configs[name] = config_value
    # A held part drawn over others must not take their pixels the model redrew (eyes under
    # bangs, the hat over capped hair).
    held_palette = {configs[n]["slot"]: configs[n]["cut"]["palette"] for n in configs
                    if n != "body" and configs[n].get("mode") == "held"}
    for name, value in configs.items():
        if value.get("mode") != "held" or value["slot"] == "expression":
            continue
        value["cut"]["against"] = [held_palette[w] for w in ("expression", "hair") if w in held_palette and w != value["slot"]]
        for variant in value.get("variants", []):
            # Under a hat little hair reaches the face; what the model draws there is the face redrawn.
            variant["cut"] = {**value["cut"], "against": [held_palette[w] for w in ("expression", "headwear") if w in held_palette],
                              "exclude": [_face_region(still)]}
    for name, value in configs.items():
        file = write(base / "production-configs" / f"{name}.json", value)
        if not (root / "productions" / value["part_id"] / "production.json").exists():
            production.init(assembly, file)
            made.append(value["part_id"])
    return {"initialised": made, "productions": list(configs)}


def _produce_one(assembly, part, track=None):
    """check -> sheet -> draw -> slice -> check for one track, when it has poses to draw.

    The first check queues drawings that fail (a hat redrawn under capped hair) for the sheet.
    """
    production.check(assembly, part, track=track)
    try:
        made = production.sheet(assembly, part, track=track)
    except contract.PartsContractError as exc:
        if "every pose has a selected drawing" in str(exc):
            return {"part": part, "track": track, "status": "nothing to draw"}
        raise
    drawn = production.draw(assembly, part, track=track)
    failed = [p for p in drawn["pages"] if p["status"] == "failed"]
    if failed:
        return {"part": part, "track": track, "status": "draw failed", "error": failed[0]["error"]}
    sliced = production.slice_batch(assembly, part, track=track)
    checked = production.check(assembly, part, track=track)
    return {"part": part, "track": track, "status": "drawn", "candidates": len(sliced["candidates"]),
            "summary": checked["summary"], "poses": len(made["poses"])}


def stage_produce(base, only=None):
    """Draw what is missing or rejected, in dependency order, then build.

    The body comes first (its segments cut the garments); the hat before the capped hair.
    """
    character = config(base)
    assembly = base / "library/assembly.json"
    prefix = character["prefix"]
    report = []
    designs = {slot: rebuild.PART_ID[name] for slot, name in character["designs"].items()}
    for file in sorted((base / "production-configs").glob("*.json")):  # what init last wrote applies to new drawings
        production.configure(assembly, json.loads(file.read_text())["part_id"], file)
    # Each held part is drawn over the ones before it: face, hair, hat (then the capped hair).
    waves = [[f"{prefix}.body"], [p for s, p in designs.items() if s not in ("hair", "headwear")],
             [designs[s] for s in ("hair",) if s in designs], [designs[s] for s in ("headwear",) if s in designs]]
    for wave in waves:
        wave = [p for p in wave if not only or p in only]
        with ThreadPoolExecutor(3) as pool:
            report += list(pool.map(lambda part: _produce_one(assembly, part), wave))
        capped_later = designs.get("hair") if "headwear" in designs else None  # the hat is drawn over it first
        for part in wave:
            if not any(r["part"] == part and r["status"] == "draw failed" for r in report):
                production.build(assembly, part, allow_incomplete=part == capped_later)
    hair = designs.get("hair")
    if hair and "headwear" in designs and (not only or hair in only):
        report.append(_produce_one(assembly, hair, track="capped"))
        production.build(assembly, hair)
    write(base / "produce-report.json", report)
    return report


def stage_recut(base, only=None):
    """Apply the configs `init` wrote to existing productions and cut their drawn pages again (no drawing)."""
    character = config(base)
    assembly = base / "library/assembly.json"
    root = assembly.parent
    report = []
    for file in sorted((base / "production-configs").glob("*.json")):
        part = json.loads(file.read_text())["part_id"]
        if only and part not in only:
            continue
        production.configure(assembly, part, file)
        tracks = json.loads((root / "productions" / part / "production.json").read_text())["tracks"]
        for track in tracks:
            track = None if track == production.MAIN else track
            try:
                sliced = production.slice_batch(assembly, part, track=track)
            except contract.PartsContractError as exc:
                if "has changed since" not in str(exc):
                    raise
                # Drawn over a worn part that is cut differently now: the check queues a redraw.
                report.append({"part": part, "track": track, "status": "stale; produce redraws it",
                               "summary": production.check(assembly, part, track=track)["summary"]})
                continue
            report.append({"part": part, "track": track, "candidates": len(sliced["candidates"]),
                           "summary": production.check(assembly, part, track=track)["summary"]})
        production.build(assembly, part)
    write(base / "recut-report.json", report)
    return report


def stage_appearance(base):
    character = config(base)
    assembly = base / "library/assembly.json"
    index, registry = workspace.read_workspace(assembly)
    catalog = registry[index["catalog"]["id"]]
    options = {slot["id"]: {o["id"]: o for o in slot["options"]} for slot in catalog["slots"]}
    chosen = {slot: options[slot].get(rebuild.PART_ID[name]) for slot, name in character["designs"].items()}
    spec = write(base / "selection.json", {"kind": "parts-appearance-selection", "schema_version": 1,
        "id": f"{character['prefix']}.look", "selections": {slot: None for slot in options} | chosen})
    return parts_appearance.create(assembly, spec)


def stage_showcase(base):
    """Walk and run with the full outfit, without the hat and bare (body only), side by side."""
    character = config(base)
    assembly = base / "library/assembly.json"
    index, registry = workspace.read_workspace(assembly)
    catalog = registry[index["catalog"]["id"]]
    full = {slot: rebuild.PART_ID[name] for slot, name in character["designs"].items()}
    looks = [full, {s: p for s, p in full.items() if s != "headwear"}, {}]
    out = base / "showcase"
    out.mkdir(exist_ok=True)
    for state in ("walk", "run", "idle"):
        columns = []
        for look in looks:
            player = PartsPlayer.from_workspace(assembly)
            player.set_selection({s["id"]: contract.reference(registry[look[s["id"]]]) if s["id"] in look else None
                                  for s in catalog["slots"]})
            player.set_state(state)
            clip = next(c for c in player.plan["clips"] if c["state"] == state)
            frames, start = [], Fraction(0)
            for sample in clip["samples"]:
                player.seek(start / clip["timebase"])
                frames.append((player.frame()[0], sample["duration_ticks"]*1000//clip["timebase"]))
                start += sample["duration_ticks"]
            columns.append(frames)
        images = []
        for n in range(len(columns[0])):
            sheet = Image.new("RGBA", (288*3, 348), (236, 236, 236, 255))
            for i, column in enumerate(columns):
                sheet.alpha_composite(column[n][0].crop((16, 8, 112, 124)).resize((288, 348), Image.Resampling.NEAREST), (i*288, 0))
            images.append(sheet)
        images[0].save(out / f"{state}.gif", save_all=True, append_images=images[1:], duration=[d for _, d in columns[0]], loop=0)
        images[len(images)//3].save(out / f"{state}.png")
    return {"out": str(out)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, type=Path)
    parser.add_argument("stage", choices=("base", "video", "library", "init", "produce", "recut", "appearance", "showcase", "status"))
    parser.add_argument("--only", nargs="*")
    args = parser.parse_args()
    stage = args.stage
    if stage == "base":
        result = stage_base(args.base)
    elif stage == "video":
        result = stage_video(args.base)
    elif stage == "library":
        result = stage_library(args.base)
    elif stage == "init":
        result = stage_init(args.base)
    elif stage == "produce":
        result = stage_produce(args.base, args.only)
    elif stage == "recut":
        result = stage_recut(args.base, args.only)
    elif stage == "appearance":
        result = stage_appearance(args.base)
    elif stage == "showcase":
        result = stage_showcase(args.base)
    else:
        result = production.status(args.base / "library/assembly.json")
    print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
