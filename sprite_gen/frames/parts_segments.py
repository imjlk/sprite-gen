# SPDX-License-Identifier: Apache-2.0
"""Cut the body into node pieces by painted labels, and split a garment along them.

A segmented body is the motion's own rasters cut into nodes (back arm, back leg,
torso and head, front leg, front arm), so a garment piece can sit between them in
the draw table instead of being trimmed to what one flat body leaves visible.

Labels are flat colors painted over each pose, one color per node: either files
(`<pose_id>.png` or `<clip>/<sample>.png`, full rig canvas) or a drawn pose sheet
(`parts-pose-sheet` pages, often an image model asked to recolor each limb). A
label only seeds its region; every body pixel takes the nearest painted label, so
a loosely painted sheet still cuts along the body's own pixels. A garment is split
the same way: each of its pixels goes to the piece of the body segment under or
nearest to it. Both write ordinary `parts-pack-import` specs; nothing is imported
or approved here.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path

from sprite_gen._deps import np
from PIL import Image

from sprite_gen.compose import compose_parts
from sprite_gen.curate import parts_motion
from sprite_gen.frames import parts_pose_sheet
from sprite_gen.spec import parts_assets as assets, parts_contract as contract, parts_workspace as workspace
from sprite_gen.spec.runio import atomic_write_text

SEGMENT_POLICY = "parts.body-segments.v1"
COMPONENT_MAJORITY = 0.75  # share of the covered body a piece needs to take a whole stroke
_NEIGHBOURS = ((-1, 0), (0, -1), (0, 1), (1, 0), (-1, -1), (-1, 1), (1, -1), (1, 1))


def _hex(node, value):
    text = value.strip().lstrip("#") if isinstance(value, str) else ""
    if len(text) != 6 or any(c not in "0123456789abcdefABCDEF" for c in text):
        raise contract.PartsContractError(f"{node}: expected #rrggbb")
    return tuple(int(text[i:i+2], 16) for i in (0, 2, 4))


def read_colors(path):
    """{node: "#rrggbb" | {"color": "#rrggbb", "provides": [nodes]}} in back-to-front order.

    One label is one body piece on its node; `provides` lets it stand for several nodes
    (a front arm drawn with its hand), which then cannot be replaced one at a time.
    Returns ({node: rgb}, {node: provided nodes}).
    """
    path = Path(path).absolute()
    raw = workspace.decode_json(assets.read_file(path.parent, path.name, limit=workspace.MAX_JSON_BYTES))
    if not isinstance(raw, dict) or not raw:
        raise contract.PartsContractError("label colors must be a non-empty {node: '#rrggbb'} object")
    colors, provides = {}, {}
    for node, value in raw.items():
        contract._id(node, "colors")
        if isinstance(value, dict):
            if set(value) - {"color", "provides"} or "color" not in value:
                raise contract.PartsContractError(f"{node}: expected {{color, provides}}")
            colors[node] = _hex(node, value["color"])
            extra = contract._ids(value.get("provides", []), f"colors.{node}.provides")
        else:
            colors[node], extra = _hex(node, value), []
        provides[node] = [node, *(n for n in extra if n != node)]
    if len(set(colors.values())) != len(colors):
        raise contract.PartsContractError("each node needs its own label color")
    claimed = [n for nodes in provides.values() for n in nodes]
    if len(set(claimed)) != len(claimed):
        raise contract.PartsContractError("a node is provided by two labels")
    return colors, provides


def _shifted(labels, dy, dx):
    """labels moved so each pixel sees its neighbour at (dy, dx); -1 past the edge."""
    height, width = labels.shape
    out = np.full_like(labels, -1)
    out[max(0, -dy):height-max(0, dy), max(0, -dx):width-max(0, dx)] = \
        labels[max(0, dy):height+min(0, dy), max(0, dx):width+min(0, dx)]
    return out


def nearest_fill(labels, need, *, rounds=512):
    """Give every pixel in `need` the label of the nearest labeled pixel (8-connected rings).

    Ties go to the first neighbour in a fixed order, so the cut is deterministic.
    """
    labels = labels.copy()
    for _ in range(rounds):
        if not (need & (labels < 0)).any():
            break
        grown = labels.copy()
        for dy, dx in _NEIGHBOURS:
            shifted = _shifted(labels, dy, dx)
            take = (grown < 0) & (shifted >= 0)
            grown[take] = shifted[take]
        if (grown == labels).all():
            break
        labels = grown
    return labels


def label_image(painted, body, colors, tolerance):
    """Per-pixel node index (-1 outside the body) and how many body pixels were painted."""
    paint = np.asarray(painted.convert("RGBA")).astype(np.int16)
    alpha = np.asarray(body.getchannel("A")) > 0
    seeds = np.full(alpha.shape, -1, dtype=np.int16)
    best = np.full(alpha.shape, tolerance + 1, dtype=np.int16)
    for number, color in enumerate(colors.values()):
        distance = np.max(np.abs(paint[..., :3] - np.array(color, dtype=np.int16)), axis=-1)
        hit = (paint[..., 3] >= 128) & (distance < best)
        seeds[hit] = number
        best[hit] = distance[hit]
    if not (seeds >= 0).any():
        raise contract.PartsContractError("a label image has no pixel near any label color")
    painted_share = int(np.count_nonzero(alpha & (seeds >= 0)))
    labels = nearest_fill(seeds, alpha)
    labels[~alpha] = -1
    return labels, painted_share


def swap_by_shading(labels, body, nodes, pairs):
    """Swap a far/near label pair when the "near" one covers the darker body pixels.

    A side view shades the far limb darker. Painters, and image models more often, mix up
    which arm or leg is near; the shading of the body under the labels settles it.
    """
    swapped = []
    pixels = np.asarray(body.convert("RGBA")).astype(float)
    luma = pixels[..., :3] @ np.array([0.299, 0.587, 0.114])
    for far, near in pairs:
        a, b = nodes.index(far), nodes.index(near)
        on_far, on_near = labels == a, labels == b
        if on_far.sum() >= 4 and on_near.sum() >= 4 and luma[on_near].mean() < luma[on_far].mean():
            labels[on_far], labels[on_near] = b, a
            swapped.append(f"{far}/{near}")
    return swapped


def _labels_image(labels, colors):
    palette = np.array([(*c, 255) for c in colors.values()] + [(0, 0, 0, 0)], dtype=np.uint8)
    return Image.fromarray(palette[np.where(labels >= 0, labels, len(colors))], "RGBA")


def _painted(labels_dir, pages, index, motion, tolerance):
    """{(clip, sample) or pose_id: painted label image on the rig canvas}."""
    found = {}
    if labels_dir is not None:
        folder = Path(labels_dir).absolute()
        for clip in motion["clips"]:
            for sample in clip["samples"]:
                for key, name in (((clip["id"], sample["id"]), f"{clip['id']}/{sample['id']}.png"),
                                  (sample["pose_id"], f"{sample['pose_id']}.png")):
                    if key is not None and key not in found and (folder / name).is_file():
                        found[key] = Image.open(folder / name).convert("RGBA")
    if pages:
        clips = {clip["id"]: clip for clip in motion["clips"]}
        for entry, sample, reduced, sheet in parts_pose_sheet.drawn_cells(pages, index, clips, tolerance):
            native = Image.new("RGBA", tuple(sheet["canvas"]))
            native.alpha_composite(reduced, tuple(sheet["crop"][:2]))
            found[entry["clip"], entry["sample"]] = native
            if sample["pose_id"] is not None:
                found.setdefault(sample["pose_id"], native)
    return found


def segment_body(assembly, out, *, colors, body_id, labels=None, pages=(), design_id=None, binding_id=None,
                 tolerance=40, key_tolerance=48, by_pose=None, near_lighter=()):
    root, index, registry, _ = parts_motion._active(assembly)
    motion = registry[index["motion"]["id"]]
    if motion["freeze"] is None:
        raise contract.PartsContractError("a segmented body needs a frozen active motion")
    if labels is None and not pages:
        raise contract.PartsContractError("give painted labels: --labels DIR or --page GUIDE=ART")
    rig = registry[index["rig"]["id"]]
    topology = registry[rig["topology"]["id"]]
    colors, provides = read_colors(colors)
    for far, near in near_lighter:
        contract._members([far, near], colors, "near_lighter")
    contract._members([n for nodes in provides.values() for n in nodes], {n["id"] for n in topology["nodes"]}, "colors")
    root_node = next(n["id"] for n in topology["nodes"] if n["parent"] is None)
    root_socket = next((s["id"] for s in topology["sockets"] if s["node"] == root_node), None)
    if root_socket is None:
        raise contract.PartsContractError("the topology root has no socket to hang the body on")
    required = {n["id"] for n in topology["nodes"] if n["required"]}
    if not 0 <= tolerance <= 255:
        raise contract.PartsContractError("--tolerance takes 0..255")
    size = (rig["canvas"]["width"], rig["canvas"]["height"])
    painted = _painted(labels, pages, index, motion, key_tolerance)
    samples = [(clip, sample) for clip in motion["clips"] if clip["state"] in index["states"] for sample in clip["samples"]]
    if by_pose is None:
        by_pose = all(sample["pose_id"] is not None for _, sample in samples)
    load = compose_parts.AssetLoader(root, index)
    out = Path(out).absolute()
    cut, report = {}, []
    for clip, sample in samples:
        key = sample["pose_id"] if by_pose else (clip["id"], sample["id"])
        if by_pose and key in cut:
            cut[clip["id"], sample["id"]] = cut[key]
            continue
        paint = painted.get((clip["id"], sample["id"])) or painted.get(sample["pose_id"])
        if paint is None:
            raise contract.PartsContractError(f"no painted labels for {clip['id']}/{sample['id']} (pose {sample['pose_id']})")
        if paint.size != size:
            raise contract.PartsContractError(f"label image for {clip['id']}/{sample['id']} is not the rig canvas {size}")
        body = load(motion, sample["image"])
        labels, painted_pixels = label_image(paint, body, colors, tolerance)
        swapped = swap_by_shading(labels, body, list(colors), near_lighter)
        pixels = np.asarray(body)
        files = {}
        for number, node in enumerate(colors):
            piece = np.where((labels == number)[..., None], pixels, 0).astype(np.uint8)
            image = Image.fromarray(piece, "RGBA")
            data = assets.png_bytes(image)
            name = f"{node}/{clip['id']}/{sample['id']}.png"
            (out / name).parent.mkdir(parents=True, exist_ok=True)
            (out / name).write_bytes(data)
            files[node] = ({"file": name, "sha256": assets.sha256(data)}, int(np.count_nonzero(piece[..., 3])))
        label_file = out / "labels" / clip["id"] / f"{sample['id']}.png"
        label_file.parent.mkdir(parents=True, exist_ok=True)
        label_file.write_bytes(assets.png_bytes(_labels_image(labels, colors)))
        area = int(np.count_nonzero(pixels[..., 3]))
        cut[key] = cut[clip["id"], sample["id"]] = (files, list(sample["sockets"][root_socket]["position"]))
        report.append({"clip": clip["id"], "sample": sample["id"], "pose": sample["pose_id"],
                       "pixels": {node: count for node, (_, count) in files.items()},
                       "painted_share": round(painted_pixels / area, 3) if area else 0.0, "swapped": swapped})
    # Label maps for every sample, also the ones that share a pose, for garment splitting.
    for clip, sample in samples:
        target = out / "labels" / clip["id"] / f"{sample['id']}.png"
        if not target.exists():
            first = next(r for r in report if r["pose"] == sample["pose_id"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((out / "labels" / first["clip"] / f"{first['sample']}.png").read_bytes())
    pieces = []
    for node in colors:
        piece = {"id": node, "slot": None, "node": node, "mode": "raster", "provides": provides[node]}
        if by_pose:
            piece["pose_map"] = {}
            for clip, sample in samples:
                if sample["pose_id"] not in piece["pose_map"]:
                    files, pivot = cut[sample["pose_id"]]
                    piece["pose_map"][sample["pose_id"]] = {
                        "image": files[node][0], "format": "full-canvas", "pivot": pivot,
                        "target_socket": root_socket, "visible": files[node][1] > 0}
        else:
            piece["sample_map"] = {}
            for clip, sample in samples:
                files, pivot = cut[clip["id"], sample["id"]]
                piece["sample_map"].setdefault(clip["id"], {})[sample["id"]] = {
                    "image": files[node][0], "format": "full-canvas", "pivot": pivot,
                    "target_socket": root_socket, "visible": files[node][1] > 0}
        pieces.append(piece)
    spec = {"kind": "parts-pack-import", "schema_version": 1, "id": body_id, "design_id": design_id or body_id,
            "role": "body", "body_mode": "segmented", "style": rig["style"], "rig": index["rig"],
            "motion": index["motion"], "view": rig["view"], "binding_id": binding_id or body_id + ".binding",
            "occupies": [], "pieces": pieces}
    atomic_write_text(out / "import.json", workspace.json_bytes(spec).decode("utf-8"))
    poses = {}
    for row in report:
        if row["pose"] is not None:
            poses.setdefault(row["pose"], [row["clip"], row["sample"]])
    record = {"kind": "parts-body-segments", "schema_version": 1, "policy": SEGMENT_POLICY, "motion": index["motion"],
              "colors": {node: "#%02x%02x%02x" % color for node, color in colors.items()},
              "provides": provides, "by_pose": by_pose,
              "poses": poses}
    atomic_write_text(out / "segments.json", workspace.json_bytes(record).decode("utf-8"))
    weak = [f"{r['clip']}/{r['sample']}" for r in report if r["painted_share"] < 0.5]
    return {"policy": SEGMENT_POLICY, "spec": str(out / "import.json"), "source_root": str(out),
            "labels": str(out / "labels"), "segments": str(out / "segments.json"), "by_pose": by_pose,
            "nodes": list(colors), "unprovided_required_nodes": sorted(required - {n for v in provides.values() for n in v}),
            "cut": len(report), "loosely_painted": weak, "results": report, "status": "draft", "release_ready": False}


def _into(value):
    piece, sep, rest = value.partition(":")
    node, eq, segments = rest.partition("=")
    names = [s for s in segments.split(",") if s]
    if not sep or not eq or not piece or not node or not names:
        raise argparse.ArgumentTypeError("expected PIECE:NODE=SEGMENT[,SEGMENT...] (SEGMENT * takes the rest)")
    return piece, node, names


def _components(mask):
    """8-connected components of a boolean mask, as boolean masks."""
    from collections import deque
    seen = np.zeros(mask.shape, dtype=bool)
    height, width = mask.shape
    for y, x in zip(*np.nonzero(mask)):
        if seen[y, x]:
            continue
        found, queue = [], deque([(y, x)])
        seen[y, x] = True
        while queue:
            cy, cx = queue.popleft()
            found.append((cy, cx))
            for dy, dx in _NEIGHBOURS:
                ny, nx = cy+dy, cx+dx
                if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    queue.append((ny, nx))
        component = np.zeros(mask.shape, dtype=bool)
        component[tuple(np.array(found).T)] = True
        yield component


def split_piece(spec, segments, out, *, piece, into, source_root=None, part_id=None, by_component=False):
    """Split one raster piece of an import spec into pieces by the body segment under each pixel.

    `by_component` assigns each connected stroke whole, to the piece owning at least 3/4 of the
    body it covers: for separate objects such as gloves and shoes, whose rim overlaps another
    body piece. A stroke with no clear owner (two boots touching) is cut pixel by pixel.
    """
    spec_path = Path(spec).absolute()
    request = workspace.decode_json(assets.read_file(spec_path.parent, spec_path.name, limit=workspace.MAX_JSON_BYTES))
    source = Path(source_root).absolute() if source_root is not None else spec_path.parent
    seg_path = Path(segments).absolute()
    record = workspace.decode_json(assets.read_file(seg_path.parent, seg_path.name, limit=workspace.MAX_JSON_BYTES))
    if record.get("kind") != "parts-body-segments":
        raise contract.PartsContractError("--segments takes the segments.json written by parts-body-segments")
    if record["motion"] != request.get("motion"):
        raise contract.PartsContractError("the segments were cut for a different motion revision")
    order = list(record["colors"])
    labels_root = seg_path.parent / "labels"
    target = next((p for p in request.get("pieces", []) if p.get("id") == piece), None)
    if target is None:
        raise contract.PartsContractError(f"the spec has no piece {piece}")
    assignment, rest = {}, None
    for name, node, names in into:
        for segment in names:
            if segment == "*":
                rest = name
            elif segment not in order:
                raise contract.PartsContractError(f"unknown body segment {segment}; known: {order}")
            elif segment in assignment:
                raise contract.PartsContractError(f"segment {segment} is given to two pieces")
            else:
                assignment[segment] = name
    nodes = {name: node for name, node, _ in into}
    if len(nodes) != len(into):
        raise contract.PartsContractError("each split piece is named once")
    color_index = {tuple(int(v.lstrip("#")[i:i+2], 16) for i in (0, 2, 4)): n for n, v in enumerate(record["colors"].values())}
    out = Path(out).absolute()
    counts = {name: 0 for name in nodes}

    def labels_for(clip_id, sample_id):
        image = np.asarray(Image.open(labels_root / clip_id / f"{sample_id}.png").convert("RGBA"))
        labels = np.full(image.shape[:2], -1, dtype=np.int16)
        for color, number in color_index.items():
            labels[(image[..., 3] > 0) & np.all(image[..., :3] == color, axis=-1)] = number
        return labels

    def split(placement, clip_id, sample_id, where):
        if placement.get("format") != "full-canvas":
            raise contract.PartsContractError(f"{where}: only full-canvas drawings can be split by the body under them")
        pixels = np.asarray(Image.open(source / placement["image"]["file"]).convert("RGBA"))
        ink = pixels[..., 3] > 0
        under = labels_for(clip_id, sample_id)
        labels = nearest_fill(under, ink)
        taken = [order.index(s) for s in assignment]
        stray = ink & (labels >= 0) & ~np.isin(labels, taken)
        if rest is None and stray.any():
            missed = sorted({order[v] for v in np.unique(labels[stray])})
            raise contract.PartsContractError(f"{where}: pixels fall on segments no piece takes: {missed} (add one, or *)")
        names = list(nodes)
        owner = np.full(ink.shape, -1, dtype=np.int16)
        for number, name in enumerate(names):
            mask = np.isin(labels, [order.index(s) for s, owner_name in assignment.items() if owner_name == name])
            if name == rest:
                mask |= stray
            owner[mask & ink] = number
        if by_component:
            # A glove or a shoe is one drawn object: the piece owning most of a connected
            # stroke takes all of it, so its rim over another body piece is not cut away.
            # Only the body it covers votes: a boot follows the foot it is on, not the leg
            # its outline happens to be nearest to. Two objects drawn touching (both boots in a
            # crossing step) have no clear owner and keep the pixel-by-pixel cut.
            for component in _components(ink):
                voters = component & (under >= 0) if (component & (under >= 0)).any() else component
                votes = np.bincount(owner[voters][owner[voters] >= 0], minlength=len(names))
                if votes.any() and votes.max() >= COMPONENT_MAJORITY * votes.sum():
                    owner[component] = int(votes.argmax())
        result = {}
        for number, name in enumerate(names):
            part = np.where((owner == number)[..., None], pixels, 0).astype(np.uint8)
            data = assets.png_bytes(Image.fromarray(part, "RGBA"))
            file = f"{name}/{where}.png"
            (out / file).parent.mkdir(parents=True, exist_ok=True)
            (out / file).write_bytes(data)
            count = int(np.count_nonzero(part[..., 3]))
            counts[name] += count
            result[name] = {**deepcopy(placement), "image": {"file": file, "sha256": assets.sha256(data)},
                            "visible": placement.get("visible", True) and count > 0}
        return result

    split_pieces = {name: {**{k: deepcopy(v) for k, v in target.items() if k not in ("sample_map", "pose_map")},
                           "id": name, "node": node, "provides": [], "replaces": []}
                    for name, node in nodes.items()}
    if rest is not None:
        split_pieces[rest].update(provides=deepcopy(target.get("provides", [])), replaces=deepcopy(target.get("replaces", [])))
    for clip_id, mapping in target.get("sample_map", {}).items():
        for sample_id, placement in mapping.items():
            for name, value in split(placement, clip_id, sample_id, f"{clip_id}/{sample_id}").items():
                split_pieces[name].setdefault("sample_map", {}).setdefault(clip_id, {})[sample_id] = value
    for pose_id, placement in target.get("pose_map", {}).items():
        # A pose's labels come from the sample its body was cut on; all its samples share the raster.
        if pose_id not in record.get("poses", {}):
            raise contract.PartsContractError(f"no cut sample shows pose {pose_id}")
        clip_id, sample_id = record["poses"][pose_id]
        for name, value in split(placement, clip_id, sample_id, f"pose-{pose_id}").items():
            split_pieces[name].setdefault("pose_map", {})[pose_id] = value
    written = {id(placement["image"]) for item in split_pieces.values()
               for mapping in item.get("sample_map", {}).values() for placement in mapping.values()}
    written.update(id(placement["image"]) for item in split_pieces.values()
                   for placement in item.get("pose_map", {}).values())
    result = deepcopy(request)
    position = next(n for n, item in enumerate(result["pieces"]) if item.get("id") == piece)
    result["pieces"][position:position+1] = list(split_pieces.values())
    if part_id is not None:
        result["id"] = part_id
    # Every other file the spec names (other pieces, masks, palette regions) travels with it.
    for item in result["pieces"]:
        for asset in _assets(item):
            if id(asset) in written:
                continue
            copied = out / "kept" / asset["file"]
            copied.parent.mkdir(parents=True, exist_ok=True)
            copied.write_bytes((source / asset["file"]).read_bytes())
            asset["file"] = f"kept/{asset['file']}"
    atomic_write_text(out / "import.json", workspace.json_bytes(result).decode("utf-8"))
    return {"spec": str(out / "import.json"), "source_root": str(out), "piece": piece,
            "pieces": {name: {"node": nodes[name], "pixels": counts[name]} for name in nodes},
            "status": "draft", "release_ready": False}


def _assets(value):
    """Every {file, sha256} reference inside a spec value, as the live dicts."""
    if isinstance(value, dict):
        if set(value) == {"file", "sha256"}:
            yield value
        else:
            for item in value.values():
                yield from _assets(item)
    elif isinstance(value, list):
        for item in value:
            yield from _assets(item)


def _pair(value):
    far, sep, near = value.partition(":")
    if not sep or not far or not near:
        raise argparse.ArgumentTypeError("expected FAR:NEAR")
    return far, near


def add_segment_arguments(parser: argparse.ArgumentParser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--colors", required=True, type=Path,
                        help='JSON {node: "#rrggbb" or {color, provides}} for each body segment, back to front')
    parser.add_argument("--labels", type=Path, help="folder of painted label images: <pose_id>.png or <clip>/<sample>.png")
    parser.add_argument("--page", action="append", default=[], dest="pages", type=parts_pose_sheet._page_pair,
                        help="GUIDE=ART a painted pose sheet page (repeatable)")
    parser.add_argument("--body-id", required=True)
    parser.add_argument("--design-id")
    parser.add_argument("--binding-id")
    parser.add_argument("--tolerance", type=int, default=40, help="max channel distance from a label color (default 40)")
    parser.add_argument("--key-tolerance", type=int, default=48, help="sheet key distance when reading pages (default 48)")
    parser.add_argument("--near-lighter", action="append", default=[], type=_pair,
                        help="FAR:NEAR label pair swapped when the near label lies on darker body pixels (repeatable)")
    parser.add_argument("--by-sample", dest="by_pose", action="store_const", const=False,
                        help="cut every sample even when poses repeat (default: by pose when every sample has one)")


def add_split_arguments(parser: argparse.ArgumentParser):
    parser.add_argument("--spec", required=True, type=Path, help="parts-pack-import spec holding the piece to split")
    parser.add_argument("--source-root", type=Path, help="folder the spec's images are relative to (default: its folder)")
    parser.add_argument("--segments", required=True, type=Path, help="segments.json from parts-body-segments")
    parser.add_argument("--piece", required=True, help="piece of the spec to split")
    parser.add_argument("--into", action="append", required=True, type=_into,
                        help="PIECE:NODE=SEGMENT[,SEGMENT] a new piece on NODE takes pixels over those body segments; * takes the rest")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--part-id", help="rename the part in the written spec")
    parser.add_argument("--by-component", action="store_true",
                        help="give each connected stroke whole to the piece owning most of it (gloves, shoes)")


def run_segment(**kwargs):
    return workspace._report(lambda: segment_body(**kwargs))


def run_split(**kwargs):
    return workspace._report(lambda: split_piece(**kwargs))
