# SPDX-License-Identifier: Apache-2.0
from copy import deepcopy
import json
from pathlib import Path

from PIL import Image
import pytest

from compose.test_parts_pipeline import populated_workspace
from parts_helpers import part_spec
from sprite_gen.curate import parts_appearance, parts_motion, parts_review
from sprite_gen.frames import parts_import
from sprite_gen.qa import inspect_parts, parts_fit
from sprite_gen.spec import parts_assets as assets, parts_contract as contract, parts_workspace as workspace


def fitted_workspace(tmp_path, *, max_size=(5, 5), mask=False):
    assembly, selection, source = populated_workspace(tmp_path)
    Image.new("RGBA", (5, 5), "blue").save(source / "piece.png")
    spec = part_spec(tmp_path / "hair.json", assembly, source, identity="hair.a")
    imported = parts_import.import_pack(assembly, spec, source)
    selection.write_bytes(workspace.json_bytes({"kind": "parts-appearance-selection", "schema_version": 1,
        "id": "hero", "selections": {"hair": imported["part"]}}))
    parts_appearance.create(assembly, selection)
    index, registry = workspace.read_workspace(assembly)
    motion = registry[index["motion"]["id"]]
    entries = [{"id": f"hair.{sample['id']}", "part": imported["part"], "binding": "test.binding",
        "piece": "piece", "clip": clip["id"], "sample": sample["id"],
        "rules": {"max_size": list(max_size), "bounds": [60, 60, 72, 72],
                  "required_cover": [[63, 63, 65, 65]]}}
        for clip in motion["clips"] for sample in clip["samples"]]
    if mask:
        image = Image.new("L", (128, 128))
        image.putpixel((64, 64), 255)
        image.save(source / "required.png")
        for entry in entries:
            entry["required"] = {"image": {"file": "required.png", "sha256": assets.sha256((source / "required.png").read_bytes())}, "channel": "luma"}
    request = {"kind": "parts-fitting-import", "schema_version": 1, "id": "hero.fitting",
        "rig": index["rig"], "motion": index["motion"], "entries": entries}
    path = tmp_path / "fitting.json"
    path.write_bytes(workspace.json_bytes(request))
    return assembly, path, source, selection


def collect(assembly):
    root, index, registry, _ = parts_motion._active(assembly)
    return inspect_parts.collect(root, index, registry)


def test_measured_fit_failures_reach_public_qa_and_block_review(tmp_path):
    assembly, spec, source, _ = fitted_workspace(tmp_path, max_size=(4, 4))
    parts_fit.import_fitting(assembly, spec, source)
    _, _, qa, payloads = collect(assembly)
    assert qa["status"] == "error" and qa["fitting"]["failed"] == 3
    fitting = json.loads(payloads["fitting.json"])
    assert all(any(c["id"] == "size" and c["status"] == "error" for c in r["checks"]) for r in fitting["results"])
    assert fitting["art_approved"] is False and qa["release_ready"] is False
    with pytest.raises(contract.PartsContractError, match="QA errors"):
        parts_review.prepare(assembly)


def test_fit_pass_and_luma_masks_keep_visual_quality_unverified(tmp_path):
    assembly, spec, source, _ = fitted_workspace(tmp_path, mask=True)
    result = parts_fit.import_fitting(assembly, spec, source)
    _, _, qa, payloads = collect(assembly)
    assert qa["fitting"]["status"] == "passed" and qa["fitting"]["checked"] == 3
    assert qa["status"] == "unverified" and qa["release_ready"] is False
    assert result["fitting"] in qa["inputs"]
    assert any(c["id"] == "style" and c["status"] == "unverified" for c in qa["checks"])
    fitting = json.loads(payloads["fitting.json"])
    assert all(next(c for c in r["checks"] if c["id"] == "coverage")["value"] == 0 for r in fitting["results"])
    assert result == parts_fit.import_fitting(assembly, spec, source)


def test_active_fitting_requires_every_visible_piece_sample(tmp_path):
    assembly, spec, source, _ = fitted_workspace(tmp_path)
    request = json.loads(spec.read_bytes())
    request["entries"] = request["entries"][:1]
    spec.write_bytes(workspace.json_bytes(request))
    parts_fit.import_fitting(assembly, spec, source)
    _, _, qa, payloads = collect(assembly)
    assert qa["fitting"]["failed"] == 2
    assert sum(r["checks"][0]["id"] == "missing-interface" for r in json.loads(payloads["fitting.json"])["results"]) == 2


def test_new_fit_revision_invalidates_approval_and_tampered_mask_is_rejected(tmp_path):
    assembly, spec, source, _ = fitted_workspace(tmp_path, mask=True)
    parts_fit.import_fitting(assembly, spec, source)
    review = parts_review.prepare(assembly)
    checks = tmp_path / "approval.json"
    checks.write_bytes(workspace.json_bytes({name: "approved" for name in inspect_parts.REVIEW_CHECKS}))
    parts_review.decide(assembly, review["review_hash"], checks, "synthetic-test")
    request = json.loads(spec.read_bytes())
    request["entries"][0]["rules"]["max_size"] = [6, 6]
    spec.write_bytes(workspace.json_bytes(request))
    imported = parts_fit.import_fitting(assembly, spec, source)
    assert imported["invalidated"]
    index, registry = workspace.read_workspace(assembly)
    doc = registry[imported["fitting"]["id"]]
    mask = doc["entries"][0]["required"]["image"]
    owner = (assembly.parent / index["documents"][doc["id"]]).parent
    (owner / mask["file"]).write_bytes(b"bad pixels")
    with pytest.raises(contract.PartsContractError, match="hash mismatch"):
        collect(assembly)


def test_replacing_part_detaches_fit_interfaces_and_old_review(tmp_path):
    assembly, spec, source, selection = fitted_workspace(tmp_path)
    parts_fit.import_fitting(assembly, spec, source)
    parts_review.prepare(assembly)
    Image.new("RGBA", (5, 5), "red").save(source / "piece.png")
    request = part_spec(tmp_path / "hair-v2.json", assembly, source, identity="hair.a")
    imported = parts_import.import_pack(assembly, request, source)
    index, registry = workspace.read_workspace(assembly)
    assert index["fitting"] == [] and index["appearance"] is None
    assert not any(d["kind"] in {"parts-fitting", "parts-review"} for d in registry.values())
    data = json.loads(selection.read_bytes())
    data["selections"]["hair"] = imported["part"]
    selection.write_bytes(workspace.json_bytes(data))
    parts_appearance.create(assembly, selection)
    assert collect(assembly)[2]["fitting"]["status"] == "unverified"


@pytest.mark.parametrize("change", ["stale-part", "wrong-sample", "outside-bounds", "negative-tolerance"])
def test_invalid_fit_declaration_preserves_active_workspace(tmp_path, change):
    assembly, spec, source, _ = fitted_workspace(tmp_path)
    before = assembly.read_bytes()
    request = json.loads(spec.read_bytes())
    row = request["entries"][0]
    if change == "stale-part":
        row["part"]["digest"] = "0" * 64
    elif change == "wrong-sample":
        row["sample"] = "sample.missing"
    elif change == "outside-bounds":
        row["rules"]["bounds"] = [0, 0, 129, 129]
    else:
        row["rules"]["max_uncovered_pixels"] = -1
    spec.write_bytes(workspace.json_bytes(request))
    with pytest.raises(contract.PartsContractError):
        parts_fit.import_fitting(assembly, spec, source)
    assert assembly.read_bytes() == before


def test_gap_repair_proposes_only_the_failed_raster_pixels_and_rechecks_original_rules(tmp_path):
    assembly, fit_spec, source, selection = fitted_workspace(tmp_path)
    before_index, _ = workspace.read_workspace(assembly)
    image = Image.new("RGBA", (5, 5), "blue")
    image.putpixel((2, 2), (0, 0, 0, 0))
    image.save(source / "piece.png")
    request = json.loads((tmp_path / "hair.json").read_bytes())
    request["pieces"][0]["mode"] = "raster"
    for mapping in request["pieces"][0]["sample_map"].values():
        for row in mapping.values():
            row["image"]["sha256"] = assets.sha256((source / "piece.png").read_bytes())
    (tmp_path / "hair.json").write_bytes(workspace.json_bytes(request))
    imported = parts_import.import_pack(assembly, tmp_path / "hair.json", source)
    data = json.loads(selection.read_bytes()); data["selections"]["hair"] = imported["part"]
    selection.write_bytes(workspace.json_bytes(data)); parts_appearance.create(assembly, selection)
    fitting = json.loads(fit_spec.read_bytes())
    for row in fitting["entries"]: row["part"] = imported["part"]
    fit_spec.write_bytes(workspace.json_bytes(fitting)); parts_fit.import_fitting(assembly, fit_spec, source)
    assert collect(assembly)[2]["fitting"]["failed"] == 3
    before = assembly.read_bytes()
    result = parts_fit.repair_plan(assembly, "hair.a", tmp_path / "proposed.json", radius=0, patch_gaps=True)
    assert assembly.read_bytes() == before
    assert result["changed_samples"] == 3 and result["unresolved_samples"] == 0
    report = json.loads(Path(result["report"]).read_bytes())
    assert report["patched_samples"] == 3 and report["activation"] == "not-applied"
    proposal = json.loads(Path(result["proposal"]).read_bytes())
    ref = proposal["pieces"][0]["sample_map"]["walk.right"]["sample.0"]["image"]
    patched = assets.image_bytes((Path(result["source_root"]) / ref["file"]).read_bytes())
    assert patched.size == image.size and patched.getpixel((2, 2)) == (0, 0, 255, 255)
    assert workspace.read_workspace(assembly)[0]["motion"] == before_index["motion"]
