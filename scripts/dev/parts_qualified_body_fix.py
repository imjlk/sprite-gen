# SPDX-License-Identifier: Apache-2.0
"""Correct a proven orphan heel in authored body ownership, retaining every body pixel."""

from pathlib import Path
from copy import deepcopy
import argparse
import numpy as np
from PIL import Image
from sprite_gen.curate import parts_motion
from sprite_gen.frames import (
    parts_import,
    parts_production as p,
    parts_independent as independent,
)
from sprite_gen.spec import parts_assets as a, parts_workspace as w


def repair(out):
    assembly = out / "library/assembly.json"
    root, index, registry, _ = parts_motion._active(assembly)
    spec = deepcopy(w.read_json(root, "inputs/body-pieces-r2/import.json"))
    source = root / "inputs/body-pieces-r2"
    folder = root / "inputs/body-pieces-r3"
    if folder.exists():
        raise ValueError("body revision already frozen")
    torso = np.array(Image.open(source / "torso/run.3.png").convert("RGBA"))
    foot = np.array(Image.open(source / "foot_back/run.3.png").convert("RGBA"))
    region = np.zeros(torso.shape[:2], dtype=bool)
    region[466:578, 145:238] = True
    misplaced = region & (torso[..., 3] > 0)
    assert not np.any(misplaced & (foot[..., 3] > 0))
    foot[misplaced] = torso[misplaced]
    torso[misplaced] = 0
    for piece in spec["pieces"]:
        node = piece["node"]
        for pose, placement in piece["pose_map"].items():
            image = Image.open(source / placement["image"]["file"]).convert("RGBA")
            if pose == "run.3" and node in ("torso", "foot_back"):
                image = Image.fromarray(torso if node == "torso" else foot)
            path = folder / node / (pose + ".png")
            path.parent.mkdir(parents=True, exist_ok=True)
            image.save(path)
            placement["image"] = {
                "file": path.relative_to(folder).as_posix(),
                "sha256": a.sha256(path.read_bytes()),
            }
    (folder / "import.json").write_bytes(w.json_bytes(spec))
    parts_import.import_pack(assembly, folder / "import.json", folder)
    root, index, registry, _ = parts_motion._active(assembly)
    frames, _ = p._render_alone(root, index, registry, None, index["states"])
    assert all(
        np.array_equal(
            np.asarray(frames[f"run.{n}"]),
            np.asarray(
                Image.open(root / "inputs/body" / f"{n:03}.png").convert("RGBA")
            ),
        )
        for n in range(4)
    )
    errors = {}
    for identity in ["qualified.hair", "qualified.sneakers"]:
        state = p._load(root, identity)
        errors[identity] = {
            pose: independent.body_ownership_errors(
                root, index, registry, state["config"], pose
            )
            for pose in state["tracks"]["main"]["poses"]
        }
        assert not any(errors[identity].values())
        p.check(assembly, identity)
        p.build(assembly, identity)
    proof = {
        "body_pixel_identical": 4,
        "moved_pixels": int(misplaced.sum()),
        "source": "inputs/body-pieces-r2",
        "region": [145, 466, 238, 578],
        "from": "torso",
        "to": "foot_back",
        "pose": "run.3",
        "body_ownership_errors": errors,
        "approved": False,
    }
    (folder / "ownership-repair.json").write_bytes(w.json_bytes(proof))
    return proof


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    print(repair(parser.parse_args().out.absolute()))
