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


def scene(root, index, registry, config, pose, image, placement, *, virtual=False):
    motion = registry[index["motion"]["id"]]
    rig = registry[index["rig"]["id"]]
    found = next(
        (c, s) for c in motion["clips"] for s in c["samples"] if s["pose_id"] == pose
    )
    clip, sample = found
    raster = Image.new("RGBA", image.size, (255, 255, 255, 255)) if virtual else image
    images = {config["slot"]: raster}
    nodes = {config["slot"]: config["node"]}
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


def visibility(root, index, registry, config, pose, image, placement):
    plan, frame, load = scene(
        root, index, registry, config, pose, image, placement, virtual=True
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
