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
    prompt = (assembly.parent / made["prompt"]).read_text()
    assert "Erase the body" not in prompt and "whole figure" in prompt  # drawn dressed, cut afterwards
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
    assert mapping["sample.0"]["image"] == mapping["sample.2"]["image"] and built["missing"] == {}
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
    assert redo["poses"] == ["walk.02"] and not (assembly.parent / redo["pages"][0]["art"]).exists()
    draw_batch(assembly, redo)
    production.slice_batch(assembly, "test.badge")
    state = json.loads((assembly.parent / "productions/test.badge/production.json").read_text())
    assert len(state["tracks"]["main"]["poses"]["walk.02"]["candidates"]) == 2 and state["tracks"]["main"]["poses"]["walk.02"]["decision"] == "unverified"
    assert state["tracks"]["main"]["poses"]["walk.01"]["decision"] == "approved"
    with pytest.raises(contract.PartsContractError, match="selection changed"):
        production.review(assembly, "test.badge", reviewer="tester", approve=["walk.02"])
    production.build(assembly, "test.badge")
    production.review(assembly, "test.badge", reviewer="tester", reject=["walk.02"], note="still too low")
    again = production.sheet(assembly, "test.badge")  # the same pose again is a new batch, not the old drawing
    assert again["poses"] == ["walk.02"] and again["batch"] != redo["batch"]
    assert not (assembly.parent / again["pages"][0]["art"]).exists()


def test_a_pose_failing_a_check_is_drawn_again_unless_someone_approved_it():
    error = {"status": "error", "findings": ["alignment 0.4: the drawn figure does not sit on the body; redraw"]}
    rows = {"walk.01": {"selected": "a", "decision": "unverified", "check": error},
            "walk.02": {"selected": "b", "decision": "approved", "check": error},
            "walk.03": {"selected": "c", "decision": "unverified", "check": {"status": "warning"}}}
    raster = {"config": {"mode": "raster"}, "tracks": {"main": {"poses": rows}}}
    assert production._todo(raster, None, list(rows)) == ["walk.01"]
    held = {"config": {"mode": "held", "reference_pose": "walk.01"}, "tracks": {"main": {"poses": rows}}}
    assert production._todo(held, None, list(rows)) == ["walk.01"]


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
    region = Image.open(assembly.parent / state["tracks"]["main"]["poses"]["walk.01"]["selected"] / "mask.png").copy()
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


def test_a_held_part_with_a_covered_variant_is_drawn_once_per_track(tmp_path):
    from parts_helpers import part_spec
    assembly = setup(tmp_path)
    source = tmp_path / "hat-source"
    source.mkdir()
    Image.new("RGBA", (8, 4), (120, 90, 40, 255)).save(source / "piece.png")
    hat = json.loads(part_spec(tmp_path / "hat.json", assembly, source, slot="headwear", node="head", socket="head",
                               identity="test.hat", pivot=(4, 4)).read_text())
    hat["covers"] = ["headwear"]
    (tmp_path / "hat.json").write_text(json.dumps(hat))
    parts_import.import_pack(assembly, tmp_path / "hat.json", source)
    config = json.loads((tmp_path / "badge.json").read_text())
    config.update(part_id="test.hair", mode="held", reference_pose="walk.01",
                  cover_rule={"tag": "headwear", "show": "uncovered"},
                  variants=[{"id": "capped", "cover_rule": {"tag": "headwear", "show": "covered"}, "wearing": ["test.hat"]}])
    (tmp_path / "hair.json").write_text(json.dumps(config))
    production.init(assembly, tmp_path / "hair.json")
    made = production.sheet(assembly, "test.hair")
    assert made["poses"] == ["walk.01"]
    draw_batch(assembly, made)
    production.slice_batch(assembly, "test.hair")
    capped = production.sheet(assembly, "test.hair", track="capped")
    guide = json.loads((assembly.parent / capped["pages"][0]["guide"]).read_text())
    assert guide["wearing"][0]["id"] == "test.hat"
    draw_batch(assembly, capped)
    production.slice_batch(assembly, "test.hair", track="capped")
    state = json.loads((assembly.parent / "productions/test.hair/production.json").read_text())
    for track in ("main", "capped"):
        rows = state["tracks"][track]["poses"]
        assert rows["walk.01"]["selected"] == rows["walk.02"]["selected"]  # one drawing for every pose
    production.check(assembly, "test.hair")
    production.build(assembly, "test.hair")
    _, registry = workspace.read_workspace(assembly)
    pieces = {p["id"]: p for p in registry["test.hair"]["bindings"][0]["pieces"]}
    assert set(pieces) == {"hair", "capped"} and pieces["capped"]["cover_rule"]["show"] == "covered"
    assert all(p["mode"] == "held" for p in pieces.values())
    Image.new("RGBA", (8, 4), (60, 90, 140, 255)).save(source / "piece.png")  # the hat redrawn
    hat = json.loads(part_spec(tmp_path / "hat.json", assembly, source, slot="headwear", node="head", socket="head",
                               identity="test.hat", pivot=(4, 4)).read_text())
    hat["covers"] = ["headwear"]
    (tmp_path / "hat.json").write_text(json.dumps(hat))
    parts_import.import_pack(assembly, tmp_path / "hat.json", source)
    checked = production.check(assembly, "test.hair", track="capped")
    assert checked["summary"]["capped"]["error"] == 2 and "test.hat as it was before" in checked["findings"]["capped:walk.01"][0]
    redo = production.sheet(assembly, "test.hair", track="capped")
    assert redo["poses"] == ["walk.01"]
    draw_batch(assembly, redo)
    production.slice_batch(assembly, "test.hair", track="capped")
    assert production.check(assembly, "test.hair", track="capped")["summary"]["capped"]["error"] == 0
    production.build(assembly, "test.hair")

    production.review(assembly, "test.hair", reviewer="tester", approve=["all"])
    reviewed = production.review(assembly, "test.hair", reviewer="tester", approve=["all"], track="capped")
    assert reviewed["status"] == "approved" and reviewed["approved"] == 4
    status = {row["part"]: row for row in production.status(assembly, "test.hair")["productions"]}
    assert set(status) == {"test.hair", "test.hair@capped"} and status["test.hair@capped"]["state"] == "approved"


def test_a_body_production_paints_labels_and_builds_a_segmented_body(tmp_path):
    assembly, _ = posed_workspace(tmp_path)
    config = {"kind": "parts-production-config", "schema_version": 1, "part_id": "test.body", "design_id": "body",
              "role": "body", "sheet": {"crop": [32, 32, 96, 96], "scale": 2, "page_size": None, "aspect": None},
              "cut": {"tolerance": 48},
              "body": {"colors": {"leg_r": {"color": "#0000ff", "provides": ["foot_r"]},
                                  "root": {"color": "#ff0000", "provides": ["torso", "head", "arm_l", "hand_l", "leg_l",
                                                                          "foot_l", "arm_r", "hand_r"]}}}}
    (tmp_path / "body.json").write_text(json.dumps(config))
    production.init(assembly, tmp_path / "body.json")
    made = production.sheet(assembly, "test.body")
    root = assembly.parent
    for page in made["pages"]:
        guide = json.loads((root / page["guide"]).read_text())
        image = Image.open(root / page["sheet"]).convert("RGBA")
        from PIL import ImageDraw
        draw = ImageDraw.Draw(image)
        s = guide["scale"]
        for cell in guide["cells"]:
            x, y = cell["origin"]
            draw.rectangle((x, y, x+40*s-1, y+20*s-1), fill=(255, 0, 0, 255))   # torso: top of the crop
            draw.rectangle((x, y+50*s, x+40*s-1, y+64*s-1), fill=(0, 0, 255, 255))  # leg: bottom of the crop
        image.save(root / page["art"])
    sliced = production.slice_batch(assembly, "test.body")
    assert {c["pose"] for c in sliced["candidates"]} == {"walk.01", "walk.02"}
    production.check(assembly, "test.body")
    built = production.build(assembly, "test.body")
    assert built["role"] == "body" and (root / built["segments"]).exists() and built["unprovided_required_nodes"] == []
    index, registry = workspace.read_workspace(assembly)
    assert registry["test.body"]["body_mode"] == "segmented" and contract.reference(registry["test.body"]) in index["body"]
    reviewed = production.review(assembly, "test.body", reviewer="tester", approve=["all"])
    assert reviewed["status"] == "approved"
