# SPDX-License-Identifier: Apache-2.0
from io import BytesIO
import zipfile

from PIL import Image
import pytest
from sprite_gen._deps import np
from test_parts_production import setup
from test_parts_pose_sheet import drawn_sheet
from sprite_gen.frames import parts_production as production, parts_revalidation as revalidation, parts_part_edit
from sprite_gen.serve.serve_parts_review import ReviewDesk
from sprite_gen.spec import parts_contract as contract, parts_workspace as workspace


def test_translation_search_matches_brute_force_without_clipping():
    ink = np.zeros((19, 23), dtype=bool)
    ink[2:6, 3:8] = True
    required = np.zeros_like(ink)
    required[7:11, 9:14] = True
    hint = revalidation.translation_hint(required, ink, radius=8)
    assert (hint["dx"], hint["dy"], hint["raw_overlap"]) == (6, 5, 20)
    image = Image.fromarray(ink.astype("uint8") * 255).convert("RGBA")
    image.putalpha(Image.fromarray(ink.astype("uint8") * 255))
    moved = parts_part_edit.translate_image(image, hint["dx"], hint["dy"])
    assert int((required & (np.asarray(moved)[..., 3] > 0)).sum()) == hint["raw_overlap"]
    assert revalidation.translation_hint(ink, ink)["dx"] == 0
    assert revalidation.translation_hint(required, np.zeros_like(ink)) is None
    edge = np.zeros_like(ink)
    edge[:, 0] = True
    assert revalidation.translation_hint(required, edge)["dx"] >= 0


def ready(tmp_path):
    assembly = setup(tmp_path)
    config = workspace.decode_json((tmp_path / "badge.json").read_bytes())
    config["cut"]["method"] = "part-only"
    mask = Image.new("L", (128, 128))
    mask.paste(255, (50, 60, 56, 64))
    mask.save(assembly.parent / "required.png")
    config["pose_checks"] = {"walk.01": {"required": "required.png"}}
    file = tmp_path / "direct.json"
    file.write_bytes(workspace.json_bytes(config))
    production.configure(assembly, "test.badge", file)
    for page in production.sheet(assembly, "test.badge")["pages"]:
        drawn_sheet(workspace.decode_json((assembly.parent / page["guide"]).read_bytes()), assembly.parent / page["art"])
    production.slice_batch(assembly, "test.badge")
    return assembly


def test_report_freezes_errors_without_selection_or_approval_and_invalidates_after_edit(tmp_path):
    assembly = ready(tmp_path)
    before = production._load(assembly.parent, "test.badge")
    report = ReviewDesk(assembly).act("test.badge", "revalidate", {})
    row = next(r for r in report["frames"] if r["pose"] == "walk.01")
    assert row["coverage"]["missing"] > 0
    assert row["coverage"]["required_visible"] == 24
    assert row["metrics"]["required_pixels"] == row["coverage"]["missing"]
    assert report["art_approved"] is False and report["provider_calls"] == 0
    assert before == production._load(assembly.parent, "test.badge")
    assert revalidation.latest(assembly, "test.badge")["current"]
    assert revalidation.prepare(assembly, "test.badge")["id"] == report["id"]
    archive = revalidation.archive(assembly, "test.badge", report["id"])
    with zipfile.ZipFile(BytesIO(archive)) as z:
        assert "walk.01/errors.png" in z.namelist()
        assert "walk.01/active-body.png" in z.namelist()
        assert "motion.json" in z.namelist()
        assert np.asarray(Image.open(BytesIO(z.read("walk.01/errors.png"))))[..., 3].sum() > 0
    selected = before["tracks"]["main"]["poses"]["walk.01"]["selected"]
    edited = parts_part_edit.edit(assembly, "test.badge", "walk.01", [{"type": "translate", "dx": 1, "dy": 0}])
    assert revalidation.latest(assembly, "test.badge")["current"]  # a draft leaves the active candidate intact
    production.select_candidate(assembly, "test.badge", "walk.01", edited["preview_candidate"])
    assert not revalidation.latest(assembly, "test.badge")["current"]
    production.select_candidate(assembly, "test.badge", "walk.01", selected)
    assert revalidation.latest(assembly, "test.badge")["current"]
    Image.new("L", (128, 128)).save(assembly.parent / "required.png")
    assert not revalidation.latest(assembly, "test.badge")["current"]
    frozen = revalidation.archive(assembly, "test.badge", report["id"])
    assert frozen == archive  # frozen guides survive mutable input changes
    changed = revalidation.prepare(assembly, "test.badge")
    changed_row = next(r for r in changed["frames"] if r["pose"] == "walk.01")
    assert changed_row["current_guides_match_frozen"] == {"required": False}
    assert changed_row["next_step"] == "refresh-reference"
    assert not changed_row.get("translation", {}).get("usable", False)


def test_archive_detects_tampered_evidence_and_old_reports_cannot_traverse_paths(tmp_path):
    assembly = ready(tmp_path)
    report = revalidation.prepare(assembly, "test.badge")
    folder = revalidation._folder(assembly.parent, "test.badge", None) / report["id"]
    (folder / "walk.01/errors.png").write_bytes(b"changed")
    with pytest.raises(contract.PartsContractError, match="digest|sha|changed|hash"):
        revalidation.archive(assembly, "test.badge", report["id"])
    with pytest.raises(contract.PartsContractError, match="invalid"):
        revalidation.archive(assembly, "test.badge", "../latest")


def test_fingerprint_includes_segment_label_pixels_not_only_segment_json(tmp_path):
    assembly = ready(tmp_path)
    state = production._load(assembly.parent, "test.badge")
    index, registry = workspace.read_workspace(assembly)
    state["config"]["segments"] = "segments/segments.json"
    folder = assembly.parent / "segments"
    labels = folder / "labels/walk.right"
    labels.mkdir(parents=True)
    (folder / "segments.json").write_bytes(workspace.json_bytes({"poses": {"walk.01": ["walk.right", "sample.0"]}}))
    Image.new("RGBA", (128, 128)).save(labels / "sample.0.png")
    first = revalidation._fingerprint(assembly.parent, index, registry, state, None)
    Image.new("RGBA", (128, 128), (255, 0, 0, 255)).save(labels / "sample.0.png")
    assert revalidation._fingerprint(assembly.parent, index, registry, state, None) != first
