# SPDX-License-Identifier: Apache-2.0
"""Author per-pose sole-view reference sketches; never final artwork or runtime masks."""

from pathlib import Path
from copy import deepcopy
import argparse
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from sprite_gen.curate import parts_motion
from sprite_gen.frames import parts_production as p
from sprite_gen.spec import parts_workspace as w


def prepare(out):
    assembly = out / "library/assembly.json"
    root, index, registry, _ = parts_motion._active(assembly)
    state = p._load(root, "qualified.sneakers")
    config = deepcopy(state["config"])
    layer = next(x for x in config["independent"]["layers"] if x["id"] == "shoe_back")
    folder = root / "inputs/sole-templates"
    folder.mkdir(exist_ok=True)
    for n in (0, 2, 3):
        pose = f"run.{n}"
        body = Image.open(root / "inputs/body" / f"{n:03}.png").convert("RGBA")
        box = layer["targets"][pose]
        region = Image.new("L", body.size)
        ImageDraw.Draw(region).rectangle(tuple(box), fill=255)
        alpha = Image.fromarray(
            np.minimum(np.asarray(region), np.asarray(body.getchannel("A")))
        ).filter(ImageFilter.MaxFilter(5))
        inner = alpha.filter(ImageFilter.MinFilter(9))
        rgba = np.zeros((768, 768, 4), dtype=np.uint8)
        rgba[:, :, :3] = (94, 65, 46)
        rgba[:, :, 3] = np.asarray(alpha)
        proxy = Image.fromarray(rgba)
        plate = Image.new("RGBA", body.size, (221, 207, 174, 0))
        plate.putalpha(inner)
        proxy.alpha_composite(plate)
        # The body enters the opening at the RIGHT edge; the visible broad face
        # is the outsole. Lines are artist guidance, not final generated pixels.
        bounds = alpha.getbbox()
        x1, y1, x2, y2 = bounds
        draw = ImageDraw.Draw(proxy)
        for y in range(y1 + 10, y2 - 6, 10):
            draw.line((x1 + 7, y, x2 - 10, y + 3), fill=(170, 155, 126, 180), width=2)
        proxy.putalpha(alpha)
        cropped = proxy.crop(bounds)
        path = folder / f"{pose}.sole-template.png"
        cropped.save(path)
        layer["guides"][pose] = [path.relative_to(root).as_posix()]
        layer["targets"][pose] = list(bounds)
        guide = Image.new("RGBA", (700, 350), (231, 231, 231, 255))
        for k, image in enumerate((body.crop(tuple(box)), cropped)):
            tile = Image.new("RGBA", image.size, (231, 231, 231, 255))
            tile.alpha_composite(image)
            tile.thumbnail((330, 320))
            guide.alpha_composite(tile, (350 * k, 24))
        ImageDraw.Draw(guide).text(
            (8, 8),
            f"{pose}: unchanged body | AUTHOR DRAFT - sole face, collar RIGHT",
            fill="black",
        )
        guide.convert("RGB").save(folder / f"{pose}.preview.jpg")
        allowed = Image.open(root / config["pose_checks"][pose]["allowed"]).convert("L")
        ImageDraw.Draw(allowed).rectangle(bounds, fill=255)
        path = folder / f"{pose}.allowed.png"
        allowed.save(path)
        config["pose_checks"][pose]["allowed"] = path.relative_to(root).as_posix()
    path = root / "inputs/footwear-sole-template-config.json"
    path.write_bytes(w.json_bytes(config))
    p.configure(assembly, "qualified.sneakers", path)
    p.request_rework(
        assembly,
        "qualified.sneakers",
        ["run.0", "run.2", "run.3"],
        "ONLY shoe_back: the camera sees the UNDERSIDE/OUTSOLE of this bent rear foot. The authored sole template is the REQUIRED pose silhouette. Render the broad cream rubber sole face with subtle tread, navy upper/rim visible around the edges, and the collar opening on the RIGHT at the leg entry. NO laces or dorsal lace panel facing the camera. Keep every contour of the template and its toe/heel/collar proportions. Use the original soft illustrated navy/cream sneaker material and brown outline. This is a pose-specific closed sneaker viewed from BELOW, not a generic standing shoe. No skin, leg, guides or text; transparent isolated artwork. Preserve shoe_front exactly.",
    )
    return p.sheet(
        assembly,
        "qualified.sneakers",
        poses=["run.0", "run.2", "run.3"],
        layers=["shoe_back"],
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    print(prepare(parser.parse_args().out.absolute()))
