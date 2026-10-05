# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path

from PIL import Image
import pytest

from compose.test_export_parts import flat_workspace
from sprite_gen.curate import parts_appearance, parts_motion, parts_review
from sprite_gen.frames import parts_import
from sprite_gen.qa import inspect_parts, parts_stability
from sprite_gen.spec import parts_assets as assets, parts_contract as contract, parts_workspace as workspace


def source_workspace(tmp_path, pixel):
    assembly = flat_workspace(tmp_path)
    source = tmp_path / "source"
    base = Image.new("RGBA", (32, 32), "green")
    base.save(source / "body.png")
    changed = base.copy()
    if pixel is not None:
        changed.putpixel(pixel, (0, 0, 0, 0))
    changed.save(source / "changed.png")
    request = json.loads((tmp_path / "body.json").read_bytes())
    request["pieces"][0]["mode"] = "raster"
    for clip_id, mapping in request["pieces"][0]["sample_map"].items():
        for sample_id, row in mapping.items():
            file = "changed.png" if clip_id == "walk" and sample_id == "sample.1" else "body.png"
            row["image"] = {"file": file, "sha256": assets.sha256((source / file).read_bytes())}
    spec = tmp_path / "body-revised.json"
    spec.write_bytes(workspace.json_bytes(request))
    parts_import.import_pack(assembly, spec, source)
    parts_appearance.create(assembly, tmp_path / "selection.json")
    index, registry = workspace.read_workspace(assembly)
    declaration = {"kind": "parts-stability-import", "schema_version": 1, "id": "generic.stability",
        "rig": index["rig"], "motion": index["motion"], "regions": [{"id": "stable.mark", "label": "Fixed region",
            "part": contract.reference(registry["generic.body"]), "binding": "test.binding", "piece": "piece",
            "socket": "root", "bounds": [-2, -2, 2, 2], "reference": {"clip": "walk", "sample": "sample.0"},
            "clips": ["walk"], "max_alpha_pixels": 0, "max_rgb_pixels": 0}]}
    path = tmp_path / "stability.json"
    path.write_bytes(workspace.json_bytes(declaration))
    return assembly, path


def collect(assembly):
    root, index, registry, _ = parts_motion._active(assembly)
    return inspect_parts.collect(root, index, registry)


def test_source_region_errors_enter_exact_qa_and_block_review(tmp_path):
    assembly, spec = source_workspace(tmp_path, (15, 15))
    parts_stability.import_stability(assembly, spec)
    _, _, qa, payloads = collect(assembly)
    report = json.loads(payloads["stability.json"])
    assert report["checked"] == 3 and report["failed"] == 1
    assert report["results"][1]["alpha_pixels"] == 1
    assert qa["status"] == "error" and qa["stability"]["status"] == "error"
    with pytest.raises(contract.PartsContractError):
        parts_review.prepare(assembly)


def test_motion_outside_authored_region_is_not_misclassified_as_jitter(tmp_path):
    assembly, spec = source_workspace(tmp_path, (0, 0))
    parts_stability.import_stability(assembly, spec)
    _, _, qa, payloads = collect(assembly)
    report = json.loads(payloads["stability.json"])
    assert report["failed"] == 0 and report["status"] == "passed"
    assert qa["status"] == "unverified" and not report["art_approved"]
    assert any(c["id"] == "motion" and c["status"] == "unverified" for c in qa["checks"])


def test_criteria_changes_invalidate_a_review_and_captured_bytes_are_verified(tmp_path):
    assembly, spec = source_workspace(tmp_path, None)
    parts_stability.import_stability(assembly, spec)
    parts_review.prepare(assembly)
    request = json.loads(spec.read_bytes()); request["regions"][0]["max_rgb_pixels"] = 1
    spec.write_bytes(workspace.json_bytes(request))
    result = parts_stability.import_stability(assembly, spec)
    assert result["invalidated"]
    index, registry = workspace.read_workspace(assembly)
    doc = registry[index["stability"][0]["id"]]
    file = (assembly.parent / index["documents"][doc["id"]]).parent / doc["source_assets"][0]["file"]
    file.write_bytes(file.read_bytes() + b" ")
    with pytest.raises(contract.PartsContractError):
        collect(assembly)


@pytest.mark.parametrize("change", ["stale-part", "missing-sample", "huge-region", "negative-tolerance"])
def test_invalid_criteria_cannot_change_active_workspace(tmp_path, change):
    assembly, spec = source_workspace(tmp_path, None)
    before = assembly.read_bytes()
    request = json.loads(spec.read_bytes()); region = request["regions"][0]
    if change == "stale-part": region["part"]["digest"] = "0" * 64
    elif change == "missing-sample": region["reference"]["sample"] = "sample.missing"
    elif change == "huge-region": region["bounds"] = [-100, -100, 100, 100]
    else: region["max_alpha_pixels"] = -1
    spec.write_bytes(workspace.json_bytes(request))
    with pytest.raises(contract.PartsContractError): parts_stability.import_stability(assembly, spec)
    assert assembly.read_bytes() == before


def test_pixel_measurement_ignores_transparent_rgb_and_detects_visible_color_changes():
    original = Image.new("RGBA", (3, 3), (20, 30, 40, 255))
    changed = original.copy(); changed.putpixel((1, 1), (70, 30, 40, 255))
    report = parts_stability.measure(original, changed)
    assert report["alpha_pixels"] == 0 and report["rgb_pixels"] == 1
    a = Image.new("RGBA", (3, 3), (255, 0, 0, 0)); b = Image.new("RGBA", (3, 3), (0, 255, 0, 0))
    assert parts_stability.measure(a, b)["rgb_pixels"] == 0
