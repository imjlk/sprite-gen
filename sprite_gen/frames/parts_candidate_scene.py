# SPDX-License-Identifier: Apache-2.0
"""A candidate on the active body, resolved before any pack is published."""

from copy import deepcopy
from pathlib import Path
import tempfile
from PIL import Image
from sprite_gen._deps import np
from sprite_gen.compose import compose_parts, parts_resolve
from sprite_gen.frames import parts_segments
from sprite_gen.spec import (
    parts_assets as assets,
    parts_contract as contract,
    parts_workspace as workspace,
)


def scene(
    root,
    index,
    registry,
    config,
    pose,
    image,
    placement,
    *,
    virtual=False,
    required=None,
    independent_layers=None,
):
    motion = registry[index["motion"]["id"]]
    rig = registry[index["rig"]["id"]]
    found = next(
        (c, s) for c in motion["clips"] for s in c["samples"] if s["pose_id"] == pose
    )
    clip, sample = found
    raster = Image.new("RGBA", image.size, (255, 255, 255, 255)) if virtual else image
    if virtual:
        # Candidate ink AND missing required pixels need independent ownership.
        # Do not propagate labels through an unrelated empty 1K+ canvas.
        region = np.asarray(image.convert("RGBA"))[..., 3] > 0
        if required is not None:
            region |= required
        raster.putalpha(Image.fromarray(region.astype("uint8") * 255))
    images = {config["slot"]: raster}
    nodes = {config["slot"]: config["node"]}
    if independent_layers:
        images = {}
        nodes = {}
        owned_required = np.zeros((image.height, image.width), dtype=bool)
        for layer in independent_layers:
            part = layer['image'].copy()
            if virtual:
                region = np.asarray(part)[..., 3] > 0
                if required is not None:
                    a,b,x,y = layer['target']
                    inside = np.zeros(region.shape, dtype=bool)
                    inside[b:y,a:x] = True
                    region |= required & inside
                    owned_required |= required & inside
                part = Image.new('RGBA', image.size, (255,255,255,255))
                part.putalpha(Image.fromarray(region.astype('uint8')*255))
            images[layer['id']] = part
            nodes[layer['id']] = layer['node']
        if virtual and required is not None and (required & ~owned_required).any():
            raise contract.PartsContractError('required pixels have no independent target owner; correct the authored targets')
    if config["pieces"]:
        with tempfile.TemporaryDirectory(prefix="parts-candidate-scene-") as temporary:
            base = Path(temporary)
            raster.save(base / "raw.png")
            spec = {
                "motion": index["motion"],
                "pieces": [
                    {
                        "id": config["slot"],
                        "node": config["node"],
                        "pose_map": {
                            pose: {
                                **placement,
                                "image": {
                                    "file": "raw.png",
                                    "sha256": assets.sha256(
                                        (base / "raw.png").read_bytes()
                                    ),
                                },
                            }
                        },
                    }
                ],
            }
            (base / "source.json").write_bytes(workspace.json_bytes(spec))
            split = parts_segments.split_piece(
                base / "source.json",
                root / config["segments"],
                base / "split",
                piece=config["slot"],
                source_root=base,
                into=[(p["id"], p["node"], p["segments"]) for p in config["pieces"]],
                by_component=False if virtual else config["by_component"],
            )
            document = workspace.decode_json(Path(split["spec"]).read_bytes())
            images = {
                p["id"]: Image.open(
                    Path(split["source_root"]) / p["pose_map"][pose]["image"]["file"]
                ).convert("RGBA")
                for p in document["pieces"]
            }
            nodes = {p["id"]: p["node"] for p in document["pieces"]}
    pieces = []
    references = {}
    configured = {piece["id"]: piece for piece in config["pieces"]}
    # Independent candidates freeze their replacement semantics. Inspection and
    # pack construction must remove the same body nodes, without a paint mask.
    configured.update({layer["id"]: layer for layer in independent_layers or []})
    for name, raster in images.items():
        ref = {
            "file": f"candidate/{name}.png",
            "sha256": assets.sha256(assets.png_bytes(raster)),
        }
        references[ref["file"]] = raster
        mapping = {
            c["id"]: {
                s["id"]: contract._placement(
                    {
                        "image": ref,
                        "pivot": placement["pivot"],
                        "target_socket": placement["target_socket"],
                        "offset": placement.get("offset", [0, 0]),
                        "visible": s["pose_id"] == pose
                        and raster.getbbox() is not None,
                    },
                    "candidate placement",
                )
                for s in c["samples"]
            }
            for c in motion["clips"]
        }
        pieces.append(
            {
                "id": name,
                "slot": config["slot"],
                "node": nodes[name],
                "mode": config["mode"],
                "replaces": configured.get(name, {}).get("replaces", []),
                "provides": configured.get(name, {}).get("replaces", []),
                "sample_map": mapping,
            }
        )
    pack = contract.seal(
        {
            "kind": "parts-pack",
            "schema_version": 1,
            "id": config["part_id"],
            "design_id": config["design_id"],
            "role": "part",
            "bindings": [
                {
                    "id": config["part_id"] + ".candidate",
                    "rig": index["rig"],
                    "motion": index["motion"],
                    "view": rig["view"],
                    "occupies": [config["slot"]],
                    "pieces": pieces,
                }
            ],
        }
    )
    catalog = deepcopy(registry[index["catalog"]["id"]])
    catalog.pop("digest")
    for slot in catalog["slots"]:
        slot["options"] = [ref for ref in slot["options"] if ref["id"] != pack["id"]]
        if slot["id"] == config["slot"]:
            slot["options"].append(contract.reference(pack))
        if any(
            ref["id"] == pack["id"] for ref in contract._selected_refs(slot["default"])
        ):
            slot["default"] = None
    catalog["presets"] = []
    catalog = contract.seal(catalog)
    docs = [
        d
        for d in registry.values()
        if d["kind"]
        not in {
            "parts-review",
            "parts-build-lock",
            "parts-appearance",
            "parts-fitting",
            "parts-stability",
        }
        and d["id"] not in (pack["id"], catalog["id"])
    ] + [pack, catalog]
    appearance = contract.seal(
        {
            "kind": "parts-appearance",
            "schema_version": 1,
            "id": "candidate.inspection",
            "rig": index["rig"],
            "motion": index["motion"],
            "catalog": contract.reference(catalog),
            "body": index["body"],
            "states": [clip["state"]],
            "selections": {config["slot"]: contract.reference(pack)},
        }
    )
    plan = parts_resolve.resolve(appearance, docs, complete=False)
    frame = next(
        s
        for c in plan["clips"]
        if c["id"] == clip["id"]
        for s in c["samples"]
        if s["id"] == sample["id"]
    )
    original = compose_parts.AssetLoader(root, index)

    def load(document, ref):
        return (
            references[ref["file"]].copy()
            if document == contract.reference(pack)
            else original(document, ref)
        )

    return plan, frame, load


def visibility(root, index, registry, config, pose, image, placement, *, required=None, independent_layers=None):
    plan, frame, load = scene(
        root,
        index,
        registry,
        config,
        pose,
        image,
        placement,
        virtual=True,
        required=required,
        independent_layers=independent_layers,
    )
    visible = np.zeros((image.height, image.width), dtype=bool)
    for entry in frame["draw"]:
        raster = load(entry["pack"], entry["placement"]["image"])
        canvas = Image.new("RGBA", image.size)
        canvas.alpha_composite(raster, tuple(entry["offset"]))
        ink = np.asarray(canvas)[..., 3] > 0
        if entry["role"] == "part":
            visible |= ink
        else:
            # A translucent body pixel still reveals the underlying garment.
            visible &= ~(np.asarray(canvas)[..., 3] == 255)
    return visible
