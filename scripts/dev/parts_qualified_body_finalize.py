# SPDX-License-Identifier: Apache-2.0
"""Finalize explicit foot ownership interfaces while preserving all body RGBA pixels."""

import argparse
from pathlib import Path
from copy import deepcopy
import numpy as np
from PIL import Image
from sprite_gen.curate import parts_motion
from sprite_gen.frames import parts_import, parts_production as p
from sprite_gen.spec import parts_assets as a, parts_workspace as w


def finalize(out):
    A = out / "library/assembly.json"
    r, ix, reg, _ = parts_motion._active(A)
    src = r / "inputs/body-pieces-r3"
    dest = r / "inputs/body-pieces-r4"
    assert not dest.exists()
    spec = deepcopy(w.read_json(r, "inputs/body-pieces-r3/import.json"))
    zones = {
        ("run.0", "foot_front"): [434, 605, 561, 691],
        ("run.2", "foot_front"): [455, 508, 591, 591],
        ("run.2", "foot_back"): [115, 428, 210, 521],
    }
    fixes = {}
    audit = []
    for (pose, node), box in zones.items():
        torso = fixes.setdefault(
            ("torso", pose),
            np.array(Image.open(src / "torso" / f"{pose}.png").convert("RGBA")),
        )
        foot = fixes.setdefault(
            (node, pose),
            np.array(Image.open(src / node / f"{pose}.png").convert("RGBA")),
        )
        region = np.zeros(torso.shape[:2], bool)
        x1, y1, x2, y2 = box
        region[y1:y2, x1:x2] = True
        bad = region & (torso[..., 3] > 0)
        assert not np.any(bad & (foot[..., 3] > 0))
        foot[bad] = torso[bad]
        torso[bad] = 0
        audit.append(
            {"pose": pose, "node": node, "bounds": box, "pixels": int(bad.sum())}
        )
    for piece in spec["pieces"]:
        node = piece["node"]
        for pose, placement in piece["pose_map"].items():
            image = (
                Image.fromarray(fixes[node, pose])
                if (node, pose) in fixes
                else Image.open(src / placement["image"]["file"])
            )
            f = dest / node / f"{pose}.png"
            f.parent.mkdir(parents=True, exist_ok=True)
            image.save(f)
            placement["image"] = {
                "file": f.relative_to(dest).as_posix(),
                "sha256": a.sha256(f.read_bytes()),
            }
    (dest / "import.json").write_bytes(w.json_bytes(spec))
    parts_import.import_pack(A, dest / "import.json", dest)
    r, ix, reg, _ = parts_motion._active(A)
    frames, _ = p._render_alone(r, ix, reg, None, ix["states"])
    assert all(
        np.array_equal(
            np.asarray(frames[f"run.{n}"]),
            np.asarray(Image.open(r / "inputs/body" / f"{n:03}.png").convert("RGBA")),
        )
        for n in range(4)
    )
    (dest / "ownership-audit.json").write_bytes(
        w.json_bytes(
            {
                "body_pixel_identical": 4,
                "transfers": audit,
                "source": "body-pieces-r3",
                "approved": False,
            }
        )
    )
    print(audit)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    finalize(parser.parse_args().out.absolute())
