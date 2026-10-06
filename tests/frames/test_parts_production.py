# SPDX-License-Identifier: Apache-2.0
"""A part produced pose by pose: sheet, drawn page, candidates, checks, build, part review, redo."""
import json
from pathlib import Path

from PIL import Image
import pytest

from parts_helpers import posed_workspace
from sprite_gen.frames import parts_import, parts_production as production, parts_rebind
from sprite_gen.spec import parts_contract as contract, parts_workspace as workspace
from test_parts_pose_sheet import drawn_sheet


def setup(tmp_path):
    assembly, _ = posed_workspace(tmp_path)
    body = parts_rebind.body_spec(assembly, tmp_path / "body.json", body_id="test.body")
    parts_import.import_pack(assembly, tmp_path / "body.json", Path(body["source_root"]))
    config = tmp_path / "badge.json"
    config.write_text(json.dumps({"kind": "parts-production-config", "schema_version": 1, "part_id": "test.badge",
        "design_id": "badge", "slot": "hair", "node": "hair_front", "socket": "head",
        "sheet": {"crop": [32, 32, 96, 96], "scale": 2, "page_size": None, "aspect": None},
        "cut": {"method": "difference", "body_threshold": 8, "near_body": None, "min_island": 0, "outline": False,
                "fill_holes": 0, "close_body": 0},
        "prompt": {"text": "Draw the badge on every figure."}}))
    production.init(assembly, config)
    return assembly


def draw_batch(assembly, made):
    root = assembly.parent
    for page in made["pages"]:
        guide = json.loads((root / page["guide"]).read_text())
        body = Image.open(root / page["sheet"]).convert("RGBA")
        drawn_sheet(guide, root / page["art"], body=body)


def test_a_part_goes_from_sheet_to_an_approved_part_review(tmp_path):
    assembly = setup(tmp_path)
    made = production.sheet(assembly, "test.badge")
    assert made["poses"] == ["walk.01", "walk.02"] and made["prompt"]
    assert production.draw(assembly, "test.badge", dry_run=True)["pages"][0]["status"] == "planned"
    draw_batch(assembly, made)
    sliced = production.slice_batch(assembly, "test.badge")
    assert [c["pose"] for c in sliced["candidates"]] == ["walk.01", "walk.02"]
    record = json.loads((assembly.parent / sliced["candidates"][0]["candidate"] / "candidate.json").read_text())
    assert set(record["images"]) == {"drawn", "under", "mask", "part"} and record["pixels"] == 24
    checked = production.check(assembly, "test.badge")
    assert checked["summary"]["error"] == 0 and checked["checked"] == 2
    built = production.build(assembly, "test.badge")
    _, registry = workspace.read_workspace(assembly)
    mapping = registry["test.badge"]["bindings"][0]["pieces"][0]["sample_map"]["walk.right"]
    assert mapping["sample.0"]["image"] == mapping["sample.2"]["image"] and built["poses"] == 2
    status = production.status(assembly)["productions"][0]
    assert status["built_current"] and status["next"] == ["review 2 poses: parts-part-review"]
    with pytest.raises(contract.PartsContractError, match="reviewer"):
        production.review(assembly, "test.badge", approve=["all"])
    reviewed = production.review(assembly, "test.badge", reviewer="tester", approve=["all"],
                                 contact_sheet=tmp_path / "contact.png")
    assert reviewed["status"] == "approved" and reviewed["approved"] == 2 and (tmp_path / "contact.png").exists()
    _, registry = workspace.read_workspace(assembly)
    review = registry["review.part.test.badge"]
    assert review["scope"] == "part" and review["evidence"]["appearance"] is None
    assert review["checks"] == {"pose.walk.01": "approved", "pose.walk.02": "approved"}
    assert production.status(assembly)["productions"][0]["state"] == "approved"


def test_a_rejected_pose_is_the_only_one_drawn_again(tmp_path):
    assembly = setup(tmp_path)
    draw_batch(assembly, production.sheet(assembly, "test.badge"))
    production.slice_batch(assembly, "test.badge")
    production.check(assembly, "test.badge")
    production.build(assembly, "test.badge")
    reviewed = production.review(assembly, "test.badge", reviewer="tester", approve=["walk.01"], reject=["walk.02"],
                                 note="badge too low")
    assert reviewed["status"] == "rejected" and reviewed["rejected"] == ["walk.02"]
    redo = production.sheet(assembly, "test.badge")
    assert redo["poses"] == ["walk.02"]
    draw_batch(assembly, redo)
    production.slice_batch(assembly, "test.badge")
    state = json.loads((assembly.parent / "productions/test.badge/production.json").read_text())
    assert len(state["poses"]["walk.02"]["candidates"]) == 2 and state["poses"]["walk.02"]["decision"] == "unverified"
    assert state["poses"]["walk.01"]["decision"] == "approved"
    with pytest.raises(contract.PartsContractError, match="selection changed"):
        production.review(assembly, "test.badge", reviewer="tester", approve=["walk.02"])


def test_an_explicit_mask_and_adopting_a_pack(tmp_path):
    assembly = setup(tmp_path)
    draw_batch(assembly, production.sheet(assembly, "test.badge"))
    sliced = production.slice_batch(assembly, "test.badge")
    region = Image.open(assembly.parent / sliced["candidates"][0]["candidate"] / "mask.png").copy()
    region.paste(0, (0, 44, 128, 128))  # the badge's top rows only
    region.save(tmp_path / "mask.png")
    masked = production.mask(assembly, "test.badge", "walk.01", tmp_path / "mask.png")
    assert 0 < masked["pixels"] < 24
    production.build(assembly, "test.badge")
    index, _ = workspace.read_workspace(assembly)
    other = tmp_path / "other.json"
    other.write_text((tmp_path / "badge.json").read_text().replace("test.badge", "test.copy"))
    production.init(assembly, other)
    adopted = production.adopt(assembly, "test.copy", assembly.parent / index["documents"]["test.badge"])
    assert adopted["adopted"] == 2 and adopted["missing"] == []


def test_a_part_review_survives_other_parts_and_goes_with_its_own_pack(tmp_path):
    assembly = setup(tmp_path)
    draw_batch(assembly, production.sheet(assembly, "test.badge"))
    production.slice_batch(assembly, "test.badge")
    production.check(assembly, "test.badge")
    production.build(assembly, "test.badge")
    production.review(assembly, "test.badge", reviewer="tester", approve=["all"])
    from parts_helpers import part_spec
    source = tmp_path / "other-source"
    source.mkdir()
    Image.new("RGBA", (4, 4), (10, 10, 200, 255)).save(source / "piece.png")
    parts_import.import_pack(assembly, part_spec(tmp_path / "other.json", assembly, source, slot="eyewear",
                                                 node="head", socket="face", identity="test.glasses"), source)
    _, registry = workspace.read_workspace(assembly)
    assert registry["review.part.test.badge"]["status"] == "approved"
    production.build(assembly, "test.badge")  # same selection: same pack digest, review kept
    _, registry = workspace.read_workspace(assembly)
    assert "review.part.test.badge" in registry
    state = json.loads((assembly.parent / "productions/test.badge/production.json").read_text())
    region = Image.open(assembly.parent / state["poses"]["walk.01"]["selected"] / "mask.png").copy()
    region.paste(0, (0, 44, 128, 128))
    region.save(tmp_path / "mask.png")
    production.mask(assembly, "test.badge", "walk.01", tmp_path / "mask.png")
    production.build(assembly, "test.badge")  # a new drawing: a new pack revision, the old review goes
    _, registry = workspace.read_workspace(assembly)
    assert "review.part.test.badge" not in registry


def test_the_review_desk_needs_its_token_and_records_decisions(tmp_path):
    import threading, urllib.request, urllib.error
    from http.server import ThreadingHTTPServer
    from sprite_gen.serve import serve_parts_review as desk_module
    assembly = setup(tmp_path)
    draw_batch(assembly, production.sheet(assembly, "test.badge"))
    production.slice_batch(assembly, "test.badge")
    production.check(assembly, "test.badge")
    production.build(assembly, "test.badge")
    desk = desk_module.ReviewDesk(assembly)
    server = ThreadingHTTPServer(("127.0.0.1", 0), desk_module.handler_for(desk))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        page = json.loads(urllib.request.urlopen(base + "/api/part/test.badge").read())
        assert [p["pose"] for p in page["poses"]] == ["walk.01", "walk.02"] and page["built_current"]
        png = urllib.request.urlopen(base + "/api/render/test.badge/walk.01/body.png").read()
        assert png.startswith(b"\x89PNG")
        body = json.dumps({"reviewer": "tester", "approve": ["walk.01"], "reject": ["walk.02"], "note": "too low"}).encode()
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(urllib.request.Request(base + "/api/act/test.badge/review", data=body, method="POST"))
        done = json.loads(urllib.request.urlopen(urllib.request.Request(base + "/api/act/test.badge/review", data=body,
                          method="POST", headers={"X-Review-Token": desk.token})).read())
        assert done["rejected"] == ["walk.02"]
        sheet = json.loads(urllib.request.urlopen(urllib.request.Request(base + "/api/act/test.badge/sheet", data=b"{}",
                           method="POST", headers={"X-Review-Token": desk.token})).read())
        assert sheet["poses"] == ["walk.02"]
    finally:
        server.shutdown()


def test_config_changes_apply_to_new_drawings_and_identity_stays_fixed(tmp_path):
    assembly = setup(tmp_path)
    draw_batch(assembly, production.sheet(assembly, "test.badge"))
    production.slice_batch(assembly, "test.badge")
    value = json.loads((tmp_path / "badge.json").read_text())
    value["prompt"]["text"] = "Draw the badge over the trousers."
    (tmp_path / "v2.json").write_text(json.dumps(value))
    assert production.configure(assembly, "test.badge", tmp_path / "v2.json")["changed"] == ["prompt"]
    value["socket"] = "face"
    (tmp_path / "v3.json").write_text(json.dumps(value))
    with pytest.raises(contract.PartsContractError, match="fixed"):
        production.configure(assembly, "test.badge", tmp_path / "v3.json")
