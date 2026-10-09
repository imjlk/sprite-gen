# SPDX-License-Identifier: Apache-2.0
"""Deterministic, immutable generation inputs attached to a production batch."""
from copy import deepcopy
from io import BytesIO
from pathlib import Path

from PIL import Image
from sprite_gen._deps import np
from sprite_gen.compose import compose_parts
from sprite_gen.frames import parts_pose_sheet as sheets
from sprite_gen.spec import parts_assets as assets, parts_contract as contract, parts_workspace as workspace


COLORS = {"required": (45, 160, 95), "protected": (220, 65, 65),
          "allowed": (60, 130, 230), "occluded": (185, 90, 205)}


def overlay(body, masks):
    result = body.convert("RGBA").copy()
    for name, color in COLORS.items():
        if name in masks:
            ink = np.zeros((body.height, body.width, 4), dtype=np.uint8)
            ink[..., :3] = color
            ink[..., 3] = np.where(np.asarray(masks[name].convert("L")) > 0, 65, 0)
            result.alpha_composite(Image.fromarray(ink))
    return result


def freeze(root, folder, index, registry, config, settings, pages, prompt=None):
    """Keep the exact body, pose, interfaces, design references and cut policy used."""
    payloads = {}
    def keep(file, data):
        payloads[file] = data
        return {"file": file, "sha256": assets.sha256(data)}
    motion = registry[index["motion"]["id"]]
    rig = registry[index["rig"]["id"]]
    topology = registry[rig["topology"]["id"]]
    load = compose_parts.AssetLoader(root, index)
    samples = {(c["id"], s["id"]): s for c in motion["clips"] for s in c["samples"]}
    poses = {}
    segments = workspace.decode_json(assets.read_file(root, config["segments"])) if config["segments"] else None
    if segments and segments["motion"] != index["motion"]:
        raise contract.PartsContractError("generation layer guide uses a different motion")
    layer_pages = []
    for page in pages:
        guide_file = root / page["guide"]
        guide = workspace.decode_json(guide_file.read_bytes())
        sheet_image = Image.open(root / page["sheet"])
        layer_page = Image.new("RGBA", sheet_image.size, (235, 235, 235, 255)) if segments else None
        for cell in guide["cells"]:
            sample = samples[cell["clip"], cell["sample"]]
            pose = sample["pose_id"]
            under = sheets._under(guide, cell, sample, motion, index, registry, topology, load, sheet_image=sheet_image)
            masks = {}
            checks = {}
            for name, file in config["pose_checks"].get(pose, {}).items():
                if file:
                    mask = Image.open(BytesIO(assets.read_file(root, file))).convert("L")
                    if mask.size != under.size:
                        raise contract.PartsContractError(f"{pose}: {name} mask must match the rig canvas")
                    masks[name] = mask
                    checks[name] = keep(f"poses/{pose}/{name}.png", assets.png_bytes(mask))
            poses[pose] = {"clip": cell["clip"], "sample": cell["sample"],
                           "body_sha256": cell["body_sha256"], "sockets": sample["sockets"],
                           "placement": {"target_socket": config["socket"], "pivot": sample["sockets"][config["socket"]]["position"]},
                           "reference": keep(f"poses/{pose}/reference.png", assets.png_bytes(under)),
                           "overlay": keep(f"poses/{pose}/overlay.png", assets.png_bytes(overlay(under, masks))),
                           "checks": checks}
            if segments:
                source_clip, source_sample = segments["poses"][pose]
                source = Path(config["segments"]).parent
                label = Image.open(BytesIO(assets.read_file(root, (source / "labels" / source_clip / f"{source_sample}.png").as_posix()))).convert("RGBA")
                poses[pose]["body_layers"] = {"labels": keep(f"poses/{pose}/body-layers.png", assets.png_bytes(label)),
                    "colors": segments["colors"], "provides": segments["provides"], "zmap": topology["layers"]}
                crop = guide["crop"];scale = guide["scale"]
                layer_page.alpha_composite(label.crop(tuple(crop)).resize(((crop[2]-crop[0])*scale,(crop[3]-crop[1])*scale),Image.Resampling.NEAREST),tuple(cell["origin"]))
        layer_pages.append(layer_page)
    design = []
    for number, file in enumerate((settings["prompt"] or {}).get("refs", [])):
        design.append({"source": file, "image": keep(f"design/{number}{Path(file).suffix}", assets.read_file(root, file))})
    cut = deepcopy(settings["cut"])
    cut_files = {}
    for name, files in (("palette", [cut["palette"]] if cut["palette"] else []), ("against", cut["against"])):
        cut_files[name] = [keep(f"cut/{name}.{n}.json", assets.read_file(root, file)) for n, file in enumerate(files)]
    frozen_pages = [{"guide": keep(f"pages/{n}/pose-sheet.json", assets.read_file(root, p["guide"])),
                     "sheet": keep(f"pages/{n}/pose-sheet.png", assets.read_file(root, p["sheet"])),
                     "art": p["art"]} for n, p in enumerate(pages)]
    for number, layer_page in enumerate(layer_pages):
        if layer_page is not None:
            frozen_pages[number]["layers"] = keep(f"pages/{number}/body-layers.png", assets.png_bytes(layer_page))
    document = {"kind": "parts-generation-reference", "schema_version": 1,
                "part": config["part_id"], "rig": index["rig"], "motion": index["motion"], "body": index["body"],
                "canvas": rig["canvas"], "style": rig["style"], "poses": poses, "design": design,
                "pages": frozen_pages, "prompt": keep("prompt.txt", prompt.read_bytes()) if prompt else None,
                "contract": {"mode": config["mode"], "cut": cut, "sheet": config["sheet"],
                             "pieces": config["pieces"], "node": settings["node"], "qa": config["qa"], "visibility": config["visibility"]}, "cut_files": cut_files,
                "profiles": {name: keep(f"{name}.json", workspace.json_bytes(value))
                             for name, value in (("rig", rig), ("topology", topology), ("motion", motion))},
                "art_approved": False}
    document["digest"] = contract.content_digest(document)
    encoded = workspace.json_bytes(document)
    payloads["reference.json"] = encoded
    saved = assets.publish_object(folder, "references", "inputs", document["digest"], payloads)
    return {"file": (saved / "reference.json").relative_to(root).as_posix(), "sha256": assets.sha256(encoded)}


def read(root, ref):
    data = assets.read_file(root, ref["file"])
    if assets.sha256(data) != ref["sha256"]:
        raise contract.PartsContractError("generation reference changed")
    document = workspace.decode_json(data)
    if contract.content_digest({k: v for k, v in document.items() if k != "digest"}) != document["digest"]:
        raise contract.PartsContractError("generation reference digest changed")
    return document, Path(ref["file"]).parent


def cut_policy(document, base):
    cut = deepcopy(document["contract"]["cut"])
    files = document["cut_files"]
    cut["palette"] = (base / files["palette"][0]["file"]).as_posix() if files["palette"] else None
    cut["against"] = [(base / ref["file"]).as_posix() for ref in files["against"]]
    return cut


def files(value):
    if isinstance(value, dict):
        if set(value) == {"file", "sha256"}:
            yield value
        else:
            for child in value.values():
                yield from files(child)
    elif isinstance(value, list):
        for child in value:
            yield from files(child)


def verify(root, document, base):
    reader = assets.SourceReader(root / base)
    for ref in files(document):
        reader.read(ref["file"], ref["sha256"])
    return reader
