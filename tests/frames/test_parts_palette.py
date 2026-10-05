# SPDX-License-Identifier: Apache-2.0
import json

import pytest
from PIL import Image

from sprite_gen.frames import parts_palette
from sprite_gen.spec.parts_contract import PartsContractError


def policy(path, colors):
    (path/"palette-policy.json").write_text(json.dumps({"kind": "parts-shared-native-palette", "colors": colors}))


def test_bake_keeps_source_geometry_alpha_and_input_pixels(tmp_path):
    image = Image.new("RGBA", (3, 1))
    image.putdata([(140, 130, 120, 255), (50, 60, 70, 128), (100, 90, 80, 0)])
    before = image.tobytes()
    policy(tmp_path, [[110, 100, 90], [30, 40, 50]])
    output, path = parts_palette.normalize(tmp_path, image)
    assert image.tobytes() == before
    assert output.size == image.size and output.getchannel("A").tobytes() == image.getchannel("A").tobytes()
    assert output.getpixel((0, 0)) == (110, 100, 90, 255)
    assert output.getpixel((1, 0)) == (30, 40, 50, 128)
    assert path.is_file()


def test_revised_palette_cannot_reuse_stale_bake(tmp_path):
    image = Image.new("RGBA", (2, 2), (100, 100, 100, 255))
    policy(tmp_path, [[80, 0, 0]])
    first, old_path = parts_palette.normalize(tmp_path, image)
    policy(tmp_path, [[0, 80, 0]])
    second, new_path = parts_palette.normalize(tmp_path, image)
    assert old_path != new_path and old_path.is_file()
    assert first.getpixel((0, 0)) != second.getpixel((0, 0))
    repeated, path = parts_palette.normalize(tmp_path, image)
    assert path == new_path and repeated.tobytes() == second.tobytes()


def test_missing_policy_keeps_legacy_input_and_invalid_policy_fails(tmp_path):
    image = Image.new("RGBA", (1, 1), (20, 30, 40, 255))
    output, path = parts_palette.normalize(tmp_path, image)
    assert output is image and path is None
    policy(tmp_path, [[300, 0, 0]])
    with pytest.raises(PartsContractError):
        parts_palette.normalize(tmp_path, image)
