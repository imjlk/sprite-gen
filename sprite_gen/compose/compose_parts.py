# SPDX-License-Identifier: Apache-2.0
"""Render resolved pieces with integer translation and explicit target masks."""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image

from sprite_gen.compose.compose_layers import apply_alpha_mask, _clipped_pixels
from sprite_gen.compose import parts_resolve
from sprite_gen.curate import parts_motion
from sprite_gen.spec import parts_assets as assets, parts_contract as contract, parts_workspace as workspace


def _coverage(image, channel="luma"):
    if channel == "alpha":
        return image.getchannel("A")
    return apply_alpha_mask(image, image.convert("L")).getchannel("A")


def recolor(image, regions, palette):
    """Flat RGB targets blended by disjoint region coverage; retain source alpha."""
    result = np.array(image, dtype=np.uint32, copy=True)
    occupied = np.zeros((image.height, image.width), dtype=bool)
    for name, region in sorted(regions.items()):
        if region.size != image.size:
            raise contract.PartsContractError("palette region size differs from piece image")
        weights = np.asarray(_coverage(region), dtype=np.uint32)
        if np.any(occupied & (weights > 0)):
            raise contract.PartsContractError("palette regions overlap; split channels or use one channel")
        occupied |= weights > 0
        weight = weights[..., None]
        result[..., :3] = (result[..., :3] * (255 - weight) + np.asarray(palette[name]) * weight + 127) // 255
    return Image.fromarray(result.astype(np.uint8))


class AssetLoader:
    """Verified, bounded, one-read image cache, rooted at each immutable document."""
    def __init__(self, root, index):
        self.root, self.index = Path(root), index
        self.reader, self.images = assets.SourceReader(root), {}
        self.total_pixels = 0

    def relative_asset(self, document, reference):
        parent = Path(self.index["documents"][document["id"]]).parent.as_posix()
        return {**reference, "file": reference["file"] if parent == "." else f"{parent}/{reference['file']}"}

    def __call__(self, pack, reference):
        reference = self.relative_asset(pack, reference)
        key = (reference["file"], reference["sha256"])
        if key not in self.images:
            value = assets.load_image(self.reader, reference)
            self.total_pixels += value.width * value.height
            if self.total_pixels > assets.MAX_TOTAL_PIXELS:
                raise contract.PartsContractError("decoded render inputs exceed pixel budget")
            self.images[key] = value
        return self.images[key].copy()


def render_sample(plan, sample, load):
    """Use the same sample plan for preview, QA and export; return image/evidence."""
    size = (plan["canvas"]["width"], plan["canvas"]["height"])
    if size[0] * size[1] > assets.MAX_FRAME_PIXELS:
        raise contract.PartsContractError("rig canvas exceeds the pixel budget")
    prepared, masks, evidence = {}, [], []
    for entry in sample["draw"]:
        placement, pack = entry["placement"], entry["pack"]
        image = load(pack, placement["image"])
        regions = {**entry["palette_regions"], **placement["palette_regions"]}
        image = recolor(image, {name: load(pack, ref) for name, ref in regions.items()}, plan["palette"])
        prepared[entry["id"]] = image
        mask = placement["mask"]
        if mask is not None:
            value = load(pack, mask["image"])
            expected = image.size if mask["space"] == "part-local" else size
            if value.size != expected:
                raise contract.PartsContractError(f"{entry['id']}: mask size differs from declared space")
            canvas_mask = Image.new("L", size, 0)
            origin = tuple(entry["offset"]) if mask["space"] == "part-local" else (0, 0)
            canvas_mask.paste(_coverage(value, mask["channel"]), origin)
            masks.append((mask["target_node"], canvas_mask))
    output = Image.new("RGBA", size)
    for entry in sample["draw"]:
        image, placement = prepared[entry["id"]], entry["placement"]
        clipped = _clipped_pixels(image, tuple(entry["offset"]), size)
        if clipped and not placement["allow_clip"]:
            raise contract.PartsContractError(f"{entry['id']}/{sample['id']}: {clipped} art pixels clip the rig canvas")
        layer = Image.new("RGBA", size)
        layer.alpha_composite(image, tuple(entry["offset"]))
        for node, mask in masks:
            if entry["node"] == node or node in entry["provides"]:
                layer = apply_alpha_mask(layer, mask)
        output = Image.alpha_composite(output, layer)
        x, y = entry["offset"]
        pivot = placement["pivot"][:]
        socket = entry["socket_position"][:]
        if plan["mirror"]:
            x, pivot[0], socket[0] = size[0] - x - image.width, image.width - 1 - pivot[0], size[0] - 1 - socket[0]
        evidence.append({"id": entry["id"], "node": entry["node"], "provides": entry["provides"],
                         "offset": [x, y], "size": list(image.size), "pivot": pivot, "socket_position": socket,
                         "source_offset": entry["offset"], "clipped_pixels": clipped,
                         "image_flipped": plan["mirror"], "allow_clip": placement["allow_clip"]})
    if plan["mirror"]:
        output = output.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    # Hidden RGB must not influence identity or leak through downstream tools.
    pixels = np.array(output, copy=True)
    pixels[pixels[..., 3] == 0] = 0
    output = Image.fromarray(pixels)
    sockets = {name: {**socket, "position": [size[0] - 1 - socket["position"][0], socket["position"][1]]}
               if plan["mirror"] else socket for name, socket in sample["sockets"].items()}
    return output, {"draw": evidence, "sockets": sockets, "rgba_sha256": assets.sha256(output.tobytes()),
                    "hidden": sample["hidden"], "suppressed": sample["suppressed"]}


def render_plan(plan, load):
    count = sum(len(c["samples"]) for c in plan["clips"])
    pixels = plan["canvas"]["width"] * plan["canvas"]["height"]
    if count > assets.MAX_FRAMES or count * pixels > assets.MAX_TOTAL_PIXELS:
        raise contract.PartsContractError("render plan exceeds the sample/pixel budget")
    payloads, clips = {}, []
    for clip in plan["clips"]:
        samples = []
        for sample in clip["samples"]:
            image, evidence = render_sample(plan, sample, load)
            data = assets.png_bytes(image)
            name = f"frames/{assets.sha256(data)}.png"
            payloads[name] = data
            samples.append({"id": sample["id"], "duration_ticks": sample["duration_ticks"],
                            "image": {"file": name, "sha256": assets.sha256(data)}, **evidence})
        clips.append({k: clip[k] for k in ("id", "state", "view", "timebase", "loop")} | {"samples": samples})
    return payloads, clips


def preview(assembly):
    root, index, registry, _ = parts_motion._active(assembly)
    if index["appearance"] is None:
        raise contract.PartsContractError("no active appearance; run parts-appearance first")
    appearance = registry[index["appearance"]["id"]]
    plan = parts_resolve.resolve(appearance, registry.values())
    payloads, clips = render_plan(plan, AssetLoader(root, index))
    report = {"kind": "parts-preview", "schema_version": 1, "appearance": index["appearance"],
              "plan_digest": plan["digest"], "render_policy": parts_resolve.RENDER_POLICY,
              "canvas": plan["canvas"], "mirror": plan["mirror"], "clips": clips,
              "status": "unverified", "release_ready": False}
    report["digest"] = contract.content_digest(report)
    payloads.update({"preview.json": workspace.json_bytes(report), "plan.json": workspace.json_bytes(plan)})
    folder = assets.publish_object(root, "previews", appearance["id"], report["digest"], payloads)
    return {"preview": (folder / "preview.json").relative_to(root).as_posix(),
            "digest": report["digest"], "appearance": index["appearance"],
            "clips": len(clips), "samples": sum(len(c["samples"]) for c in clips), "status": "unverified", "release_ready": False}


def add_arguments(parser: argparse.ArgumentParser):
    parser.add_argument("--assembly", required=True, type=Path)


def run(*, assembly: Path):
    return workspace._report(lambda: preview(assembly))
