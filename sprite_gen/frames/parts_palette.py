# SPDX-License-Identifier: Apache-2.0
"""Optional shared-palette import bake; geometry, alpha and source stay intact."""
from pathlib import Path

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.effects import recolor
from sprite_gen.spec import parts_assets as assets, parts_contract as contract, parts_workspace as workspace


def normalize(base, image):
    base = Path(base)
    policy_path = base/"palette-policy.json"
    if not policy_path.exists():
        return image, None
    policy = workspace.decode_json(policy_path.read_bytes())
    palette = policy.get("colors")
    if (policy.get("kind") != "parts-shared-native-palette" or not isinstance(palette, list)
        or not 1 <= len(palette) <= 256
        or any(not isinstance(c, list) or len(c) != 3 or any(type(v) is not int or not 0 <= v <= 255 for v in c) for c in palette)):
        raise contract.PartsContractError("invalid shared native palette")
    raw = assets.png_bytes(image)
    # Both source and policy own the cache identity. A revised palette must
    # never return a previous policy's already baked pixels.
    folder = base/"palette-normalization"/assets.sha256(workspace.json_bytes(policy))/assets.sha256(raw)
    target = folder/"shared.png"
    if not target.exists():
        folder.mkdir(parents=True, exist_ok=True)
        source = folder/"source.png"
        source.write_bytes(raw)
        rgba = np.asarray(image)
        colors = np.unique(rgba[rgba[..., 3] >= 128, :3], axis=0)
        if not len(colors):
            raise contract.PartsContractError("shared palette requires visible source art")
        mapping = {recolor.format_hex(color): recolor.format_hex(min(palette,
                   key=lambda p: sum((int(a)-b)**2 for a, b in zip(color, p)))) for color in colors}
        spec = folder/"spec.json"
        spec.write_bytes(workspace.json_bytes({"kind": "sprite-gen-recolor", "version": 1,
            "match": "exact", "tolerance": 0, "variants": [{"name": "shared", "map": mapping}]}))
        recolor.bake(source, spec, folder)
    result = Image.open(target).convert("RGBA")
    if result.size != image.size or not np.array_equal(np.asarray(result)[..., 3], np.asarray(image)[..., 3]):
        raise contract.PartsContractError("palette bake changed source geometry or alpha")
    return result, target
