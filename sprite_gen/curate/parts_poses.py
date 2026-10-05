# SPDX-License-Identifier: Apache-2.0
"""Poses as the unit of drawing: name them, recompose clips from them, report coverage.

A pose is one approved body drawing. Parts drawn per pose (`pose_map`) follow
every sample that shows the pose, so a clip assembled from existing poses
(a held frame, a shifted landing, a slower walk) needs no new part art. These
tools only name, reorder and measure; nothing here is approved for release.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from fractions import Fraction
from pathlib import Path

from PIL import Image

from sprite_gen.compose import parts_resolve
from sprite_gen.curate import parts_motion
from sprite_gen.spec import parts_assets as library, parts_contract as contract, parts_workspace as workspace
from sprite_gen.spec.runio import atomic_write_text


def _named(value):
    where, sep, name = value.partition("=")
    clip, slash, sample = where.rpartition("/")
    if not sep or not slash or not clip or not sample or not name:
        raise argparse.ArgumentTypeError("expected CLIP/SAMPLE=POSE_ID")
    return clip, sample, name


def assign(assembly, annotations, *, out=None, names=()):
    """Give each distinct drawing in an annotation draft one pose_id.

    Precedence: a name given here, a pose_id already in the draft, the pose the
    active motion gave the same raster, then `<clip prefix>.<n>`. Samples that
    share a raster share a pose; two names for one raster are refused.
    """
    _, index, registry, _ = parts_motion._active(assembly)
    path = Path(annotations).absolute()
    raw = parts_motion._input(path)
    draft = parts_motion._ANNOTATIONS({k: v for k, v in raw.items() if k != "digest"}, "annotations")
    clips = draft["clips"]
    chosen = {}

    def claim(digest, pose, why):
        contract._id(pose, why)
        previous = chosen.setdefault(digest, pose)
        if previous != pose:
            raise contract.PartsContractError(f"one raster has two pose IDs: {previous}, {pose} ({why})")

    for clip, sample, name in names:
        if sample not in clips.get(clip, {}):
            raise contract.PartsContractError(f"unknown sample {clip}/{sample}")
        claim(clips[clip][sample]["image_sha256"], name, f"--pose {clip}/{sample}")
    for clip_id, samples in clips.items():
        for sample_id, row in samples.items():
            if row["pose_id"] is not None and row["image_sha256"] not in chosen:
                claim(row["image_sha256"], row["pose_id"], f"{clip_id}/{sample_id}")
    motion = registry.get(index["motion"]["id"]) if index["motion"] is not None else None
    reused = 0
    for clip in (motion or {}).get("clips", []):
        for sample in clip["samples"]:
            if sample["pose_id"] is not None and sample["image"]["sha256"] not in chosen:
                if any(row["image_sha256"] == sample["image"]["sha256"] for rows in clips.values() for row in rows.values()):
                    chosen[sample["image"]["sha256"]] = sample["pose_id"]
                    reused += 1
    taken = set(chosen.values())
    generated = 0
    for clip_id, samples in clips.items():
        prefix, number = clip_id.split(".")[0], 0
        for sample_id in sorted(samples, key=_sample_order):
            digest = samples[sample_id]["image_sha256"]
            if digest in chosen:
                continue
            number += 1
            while f"{prefix}.{number:02d}" in taken:
                number += 1
            chosen[digest] = f"{prefix}.{number:02d}"
            taken.add(chosen[digest])
            generated += 1
    poses = {}
    for clip_id, samples in clips.items():
        for sample_id, row in samples.items():
            row["pose_id"] = chosen[row["image_sha256"]]
            poses.setdefault(row["pose_id"], []).append(f"{clip_id}/{sample_id}")
    target = Path(out).absolute() if out is not None else path
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(target, workspace.json_bytes(draft).decode("utf-8"))
    shared = {pose: where for pose, where in poses.items() if len(where) > 1}
    return {"annotations": str(target), "poses": len(poses), "samples": sum(len(s) for s in clips.values()),
            "named": len(names), "reused_from_motion": reused, "generated": generated,
            "shared": dict(sorted(shared.items())), "status": "draft", "release_ready": False}


def _sample_order(sample_id):
    head, _, tail = sample_id.rpartition(".")
    return (head, int(tail)) if tail.isdigit() else (sample_id, -1)


_STEP = contract._record({"pose": contract._id, "duration_ticks": contract._positive, "offset": contract._point},
                         {"offset": [0, 0]})
_NEW_CLIP = contract._record({"id": contract._id, "state": contract._id, "loop": contract._boolean,
                              "timebase": contract._positive, "samples": contract._list(_STEP, nonempty=True)},
                             {"loop": True, "timebase": 120000})
_COMPOSE = contract._record({"kind": contract._enum("parts-motion-compose"), "schema_version": contract._positive,
                             "id": contract._id, "keep": contract._nullable(contract._ids),
                             "clips": contract._records(_NEW_CLIP)}, {"keep": None, "clips": []})


def _shift(image, offset):
    """Move a canvas raster by whole pixels; a shift that pushes ink off the canvas is refused."""
    if offset == [0, 0]:
        return image
    moved = Image.new("RGBA", image.size, (0, 0, 0, 0))
    moved.paste(image, tuple(offset))
    if moved.getchannel("A").getbbox() is None or sum(moved.getchannel("A").histogram()[1:]) != sum(image.getchannel("A").histogram()[1:]):
        raise contract.PartsContractError(f"offset {offset} pushes the pose off the rig canvas")
    return moved


def compose(assembly, spec, out, *, freeze=True):
    """Write a motion whose new clips reuse existing poses, then import (and freeze) it.

    Kept clips are copied sample for sample with exact ticks. A new sample takes a pose's
    raster and sockets, both moved by `offset`, for `duration_ticks`. Every pose_id is
    preserved, so pose-drawn parts cover the new clips once rebound (`parts-rebind`).
    """
    root, index, registry, _ = parts_motion._active(assembly)
    request = _COMPOSE(parts_motion._input(spec), "compose")
    if request["schema_version"] != 1:
        raise contract.PartsContractError("only schema_version 1 is supported")
    if index["motion"] is None or registry[index["motion"]["id"]]["freeze"] is None:
        raise contract.PartsContractError("composing poses requires a frozen active motion")
    motion = registry[index["motion"]["id"]]
    rig = registry[index["rig"]["id"]]
    size = (rig["canvas"]["width"], rig["canvas"]["height"])
    reader = library.SourceReader((root / index["documents"][motion["id"]]).parent)
    keep = request["keep"] if request["keep"] is not None else [c["id"] for c in motion["clips"]]
    contract._members(keep, {c["id"] for c in motion["clips"]}, "compose.keep")
    poses = {}
    for clip in motion["clips"]:
        for sample in clip["samples"]:
            if sample["pose_id"] is not None:
                poses.setdefault(sample["pose_id"], sample)
    if not poses and request["clips"]:
        raise contract.PartsContractError("the active motion has no pose IDs; run parts-pose-assign before freezing it")
    plans = []
    for clip in motion["clips"]:
        if clip["id"] in keep:
            plans.append((clip["id"], clip["state"], clip["loop"], clip["timebase"],
                          [(s, s["duration_ticks"], [0, 0]) for s in clip["samples"]]))
    for clip in request["clips"]:
        steps = []
        for step in clip["samples"]:
            if step["pose"] not in poses:
                raise contract.PartsContractError(f"{clip['id']}: unknown pose {step['pose']}")
            steps.append((poses[step["pose"]], step["duration_ticks"], step["offset"]))
        plans.append((clip["id"], clip["state"], clip["loop"], clip["timebase"], steps))
    ids = [plan[0] for plan in plans]
    states = [plan[1] for plan in plans]
    if len(set(ids)) != len(ids) or len(set(states)) != len(states):
        raise contract.PartsContractError("composed clip IDs and states must be unique (one clip per state)")
    out = Path(out).absolute()
    clips, annotations, report = [], {}, []
    for clip_id, state, loop, timebase, steps in plans:
        target = out / clip_id
        target.mkdir(parents=True, exist_ok=True)
        entries, expected, rows, exact = [], {}, {}, True
        for number, (sample, ticks, offset) in enumerate(steps):
            image = _shift(library.load_image(reader, sample["image"], size=size), offset)
            data = library.png_bytes(image)
            filename = f"{number:03d}.png"
            (target / filename).write_bytes(data)
            expected[f"{clip_id}/{filename}"] = library.sha256(data)
            duration = Fraction(ticks, timebase)
            exact &= Fraction(repr(float(duration))) == duration
            entries.append({"id": f"{clip_id}.{sample['pose_id'] or sample['id']}.{number}", "file": filename,
                            "duration": float(duration)})
            sockets = deepcopy(sample["sockets"])
            for name, location in sockets.items():
                location["position"] = [v + d for v, d in zip(location["position"], offset)]
                x, y = location["position"]
                if location["visible"] and not (0 <= x <= size[0] and 0 <= y <= size[1]):
                    raise contract.PartsContractError(f"{clip_id}/{number}: socket {name} leaves the rig canvas")
            rows[f"sample.{number}"] = {"image_sha256": library.sha256(data), "sockets": sockets,
                                        "pose_id": sample["pose_id"]}
        descriptor = {"kind": "sprite-gen-asset", "version": 1, "loop": loop, "frames": entries}
        data = workspace.json_bytes(descriptor)
        (target / "motion.json").write_bytes(data)
        expected[f"{clip_id}/motion.json"] = library.sha256(data)
        clips.append({"id": clip_id, "state": state, "file": f"{clip_id}/motion.json", "source_state": None,
                      # A duration with no finite decimal is rounded to the same tick boundaries.
                      "timebase": timebase, "loop": loop, "fps": None,
                      "timing_policy": "exact" if exact else "round-boundaries",
                      "source_mode": "external", "expected_files": expected})
        annotations[clip_id] = rows
        report.append({"clip": clip_id, "state": state, "samples": len(steps), "kept": clip_id in keep,
                       "poses": [sample["pose_id"] for sample, _, _ in steps],
                       "ticks": sum(ticks for _, ticks, _ in steps)})
    import_spec = {"kind": "parts-motion-import", "schema_version": 1, "id": request["id"], "rig": index["rig"], "clips": clips}
    atomic_write_text(out / "motion-import.json", workspace.json_bytes(import_spec).decode("utf-8"))
    imported = parts_motion.import_motion(assembly, out / "motion-import.json", out)
    candidate = workspace.decode_json((root / imported["candidate"]).read_bytes())
    filled = {"kind": "parts-motion-annotations", "schema_version": 1, "id": candidate["id"],
              "candidate_digest": candidate["digest"], "clips": annotations}
    for clip in candidate["motion"]["clips"]:
        for sample in clip["samples"]:
            if annotations[clip["id"]][sample["id"]]["image_sha256"] != sample["image"]["sha256"]:
                raise contract.PartsContractError("imported raster differs from the composed frame")
    atomic_write_text(out / "annotations.json", workspace.json_bytes(filled).decode("utf-8"))
    result = {"spec": str(out / "motion-import.json"), "candidate": imported["candidate"],
              "annotations": str(out / "annotations.json"), "clips": report,
              "motion_quality": imported["motion_quality"], "warnings": imported["warnings"]}
    if freeze:
        frozen = parts_motion.freeze_motion(assembly, imported["candidate"], out / "annotations.json")
        result.update(motion=frozen["motion"], invalidated=frozen["invalidated"],
                      next="rebind each part to the new motion (parts-rebind; pose_map pieces carry by pose)")
    return {**result, "status": "frozen" if freeze else "draft", "release_ready": False}


def coverage(assembly, *, markdown=None):
    """Which piece is drawn for which clip and pose: the part x motion matrix to fill."""
    _, index, registry, _ = parts_motion._active(assembly)
    if index["motion"] is None:
        raise contract.PartsContractError("coverage requires an active motion")
    motion = registry[index["motion"]["id"]]
    rig = registry[index["rig"]["id"]]
    topology = registry[rig["topology"]["id"]]
    ranks = parts_resolve.layer_ranks(topology)
    catalog = registry[index["catalog"]["id"]]
    refs = [*index["body"], *(ref for slot in catalog["slots"] for ref in slot["options"])]
    all_poses = sorted({s["pose_id"] for c in motion["clips"] for s in c["samples"]} - {None})
    unposed = sum(s["pose_id"] is None for c in motion["clips"] for s in c["samples"])
    rows, gaps = [], []
    for ref in dict.fromkeys(r["id"] for r in refs):
        pack = registry[ref]
        for binding in pack["bindings"]:
            if binding["rig"] != index["rig"] or binding["motion"] != index["motion"]:
                continue
            for piece in binding["pieces"]:
                poses = piece.get("pose_map", {})
                row = {"pack": pack["id"], "role": pack["role"], "piece": piece["id"], "slot": piece["slot"],
                       "node": piece["node"], "mode": piece["mode"],
                       "layer": parts_resolve.rank({"node": piece["node"], "provides": piece["provides"]}, ranks)
                                if ranks else None,
                       "cover_rule": piece.get("cover_rule"), "covers": binding.get("covers", []),
                       "poses_drawn": len(poses), "poses_missing": sorted(set(all_poses) - set(poses)) if poses else [],
                       "clips": {}}
                for clip in motion["clips"]:
                    mapping = piece["sample_map"].get(clip["id"], {})
                    covered = [s for s in clip["samples"] if s["id"] in mapping]
                    cell = {"covered": len(covered), "samples": len(clip["samples"]),
                            "visible": sum(mapping[s["id"]]["visible"] for s in covered),
                            "drawings": len({mapping[s["id"]]["image"]["sha256"] for s in covered}),
                            "by_pose": sum(s["pose_id"] in poses for s in covered),
                            "active_state": clip["state"] in index["states"]}
                    row["clips"][clip["id"]] = cell
                    if cell["active_state"] and cell["covered"] < cell["samples"]:
                        gaps.append(f"{pack['id']}.{piece['id']}/{clip['id']}: {cell['samples'] - cell['covered']} samples")
                rows.append(row)
    report = {"motion": index["motion"], "states": index["states"],
              "clips": [{"id": c["id"], "state": c["state"], "samples": len(c["samples"]),
                         "poses": len({s["pose_id"] for s in c["samples"]} - {None})} for c in motion["clips"]],
              "poses": len(all_poses), "unposed_samples": unposed, "layers": topology.get("layers", []),
              "pieces": rows, "gaps": gaps, "status": "complete" if not gaps else "incomplete", "release_ready": False}
    if markdown is not None:
        lines = ["| part.piece | node | layer | mode | " + " | ".join(c["id"] for c in motion["clips"]) + " |",
                 "|---|---|---|---|" + "---|" * len(motion["clips"])]
        for row in rows:
            cells = []
            for clip in motion["clips"]:
                cell = row["clips"][clip["id"]]
                mark = "" if cell["covered"] == cell["samples"] else " ✗"
                cells.append(f"{cell['covered']}/{cell['samples']} ({cell['drawings']}){mark}")
            rule = row["cover_rule"]
            label = f"{row['pack']}.{row['piece']}" + (f" [{rule['show']}:{rule['tag']}]" if rule else "")
            lines.append(f"| {label} | {row['node']} | {'' if row['layer'] is None else row['layer']} | {row['mode']} | "
                         + " | ".join(cells) + " |")
        lines += ["", "Cells: covered/samples (distinct drawings); ✗ marks a gap. "
                  f"{len(all_poses)} poses, {unposed} samples without a pose_id."]
        target = Path(markdown).absolute()
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target, "\n".join(lines) + "\n")
        report["markdown"] = str(target)
    return report


def add_assign_arguments(parser: argparse.ArgumentParser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--annotations", required=True, type=Path, help="parts-motion-annotations draft to fill")
    parser.add_argument("--out", type=Path, help="write here instead of updating the draft in place")
    parser.add_argument("--pose", action="append", type=_named, dest="names", default=[],
                        help="CLIP/SAMPLE=POSE_ID name a drawing (every sample sharing its raster gets the name)")


def add_compose_arguments(parser: argparse.ArgumentParser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--spec", required=True, type=Path, help="parts-motion-compose spec")
    parser.add_argument("--out", required=True, type=Path, help="folder for the composed frames and import spec")
    parser.add_argument("--no-freeze", dest="freeze", action="store_false",
                        help="stop at the imported candidate with filled annotations")


def add_coverage_arguments(parser: argparse.ArgumentParser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--markdown", type=Path, help="also write the matrix as a Markdown table")


def run_assign(**kwargs):
    return workspace._report(lambda: assign(**kwargs))


def run_compose(**kwargs):
    return workspace._report(lambda: compose(**kwargs))


def run_coverage(**kwargs):
    return workspace._report(lambda: coverage(**kwargs))
