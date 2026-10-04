# SPDX-License-Identifier: Apache-2.0
"""Parts opt-in must not change a pre-feature run's request, JSON or pixels."""
import hashlib
import json
from pathlib import Path

from PIL import Image

from conftest import run_script


def test_parts_feature_preserves_the_pinned_non_layer_pipeline(fixture_run_dir):
    baseline = json.loads((Path(__file__).parents[1] / "fixtures/parts/legacy-baseline.json").read_text())
    for script in ["extract_sprite_row_frames.py", "compose_sprite_atlas.py"]:
        result = run_script(script, "--run-dir", str(fixture_run_dir))
        assert result.returncode == 0, result.stdout + result.stderr
    assert hashlib.sha256((fixture_run_dir / "sprite-request.json").read_bytes()).hexdigest() == baseline["request_sha256"]
    assert json.loads((fixture_run_dir / "manifest.json").read_text()) == baseline["manifest"]
    with Image.open(fixture_run_dir / "sprite-sheet-alpha.png") as image:
        assert list(image.size) == baseline["size"]
        assert hashlib.sha256(image.convert("RGBA").tobytes()).hexdigest() == baseline["rgba_sha256"]
    assert not (fixture_run_dir / "assembly.json").exists()
    assert not (fixture_run_dir / "parts").exists()


def test_parts_feature_preserves_the_pinned_layer_pipeline(tmp_path):
    from compose.test_layer_compose import _build_run
    from sprite_gen.compose import compose_layers

    baseline = json.loads((Path(__file__).parents[1] / "fixtures/parts/legacy-layer-baseline.json").read_text())
    run = _build_run(tmp_path)
    result = run_script("compose_layers.py", "--run-dir", str(run))
    assert result.returncode == 0, result.stdout + result.stderr
    folder = run / compose_layers.LAYERS_DIRNAME
    assert hashlib.sha256((run / "sprite-request.json").read_bytes()).hexdigest() == baseline["request_sha256"]
    assert json.loads((folder / "walk_with_can.manifest.json").read_text()) == baseline["manifest"]
    assert json.loads((folder / "layers.report.json").read_text()) == baseline["report"]
    with Image.open(folder / "walk_with_can.png") as image:
        assert list(image.size) == baseline["size"]
        assert hashlib.sha256(image.convert("RGBA").tobytes()).hexdigest() == baseline["rgba_sha256"]
    assert not (run / "assembly.json").exists()
