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


def test_part_only_uses_frozen_interfaces_instead_of_whole_body_overlap(tmp_path):
    assembly = setup(tmp_path)
    config = json.loads((tmp_path / "badge.json").read_text())
    config["cut"]["method"] = "part-only"
    config["prompt"]["text"] = "Draw only the badge."
    for name in ("required", "protected", "allowed"):
        region = Image.new("L", (128, 128), 255 if name == "allowed" else 0)
        region.putpixel((110, 110) if name == "required" else (36, 42), 0 if name == "allowed" else 255)
        region.save(assembly.parent / f"{name}.png")
    config["pose_checks"] = {"walk.01": {n: f"{n}.png" for n in ("required", "protected", "allowed")}}
    (tmp_path / "direct.json").write_text(json.dumps(config))
    production.configure(assembly, "test.badge", tmp_path / "direct.json")
    made = production.sheet(assembly, "test.badge")
    prompt = (assembly.parent / made["prompt"]).read_text()
    assert "Draw ONLY the requested part" in prompt and "whole figure" not in prompt
    for page in made["pages"]:
        guide = json.loads((assembly.parent / page["guide"]).read_text())
        drawn_sheet(guide, assembly.parent / page["art"])
    sliced = production.slice_batch(assembly, "test.badge")
    candidate = assembly.parent / sliced["candidates"][0]["candidate"]
    assert Image.open(candidate / "part.png").getpixel((36, 42)) == (200, 40, 40, 255)
    # Changing the author's input masks cannot rewrite a candidate's checks.
    for name in ("required", "protected", "allowed"):
        Image.new("L", (128, 128), 255 if name == "allowed" else 0).save(assembly.parent / f"{name}.png")
    checked = production.check(assembly, "test.badge")
    assert checked["summary"]["error"] == 1
    state = production._load(assembly.parent, "test.badge")
    metrics = state["tracks"]["main"]["poses"]["walk.01"]["check"]["metrics"]
    assert metrics["alignment"] is None
    assert (metrics["required_pixels"], metrics["protected_pixels"], metrics["allowed_pixels"]) == (1, 1, 1)
    production.build(assembly, "test.badge")
    with pytest.raises(contract.PartsContractError, match="fix errors"):
        production.review(assembly, "test.badge", reviewer="tester", approve=["walk.01"])
    Image.new("L", (128, 128)).save(candidate / "qa_required.png")
    with pytest.raises(contract.PartsContractError, match="changed required mask"):
        production.check(assembly, "test.badge")


def test_provider_failures_survive_reexecution_and_dry_run(tmp_path, monkeypatch):
    assembly = setup(tmp_path)
    production.sheet(assembly, "test.badge")
    monkeypatch.setattr(production.subprocess, "run", lambda *a, **kw:
                        production.subprocess.CompletedProcess(a[0], 1, stderr="provider refused"))
    for _ in range(2):
        assert production.draw(assembly, "test.badge")["pages"][0]["status"] == "failed"
    path = assembly.parent / "productions/test.badge/production.json"
    before = path.read_bytes()
    production.draw(assembly, "test.badge", dry_run=True)
    assert path.read_bytes() == before
    attempts = json.loads(before)["tracks"]["main"]["batches"][-1]["attempts"]
    assert len(attempts) == 2 and all(a["error"] == "provider refused" for a in attempts)


def test_reference_inputs_are_frozen_before_drawing_and_checks(tmp_path):
    from sprite_gen.frames import parts_reference
    assembly = setup(tmp_path)
    config = json.loads((tmp_path / "badge.json").read_text())
    config["cut"]["method"] = "part-only"
    region = Image.new("L", (128, 128)); region.putpixel((36, 42), 255)
    region.save(assembly.parent / "protected.png")
    Image.new("RGB", (12, 12), "red").save(assembly.parent / "design.png")
    config["pose_checks"] = {"walk.01": {"protected": "protected.png"}}
    config["prompt"]["refs"] = ["design.png"]
    file = tmp_path / "direct.json"; file.write_text(json.dumps(config))
    production.configure(assembly, "test.badge", file)
    made = production.sheet(assembly, "test.badge")
    frozen, base = parts_reference.read(assembly.parent, made["reference"])
    before = (assembly.parent / base / frozen["design"][0]["image"]["file"]).read_bytes()
    Image.new("RGB", (12, 12), "blue").save(assembly.parent / "design.png")
    Image.new("L", (128, 128)).save(assembly.parent / "protected.png")
    # Mutable original sheet and prompt cannot rewrite what the provider sees.
    (assembly.parent / made["prompt"]).write_text("changed")
    planned = production.draw(assembly, "test.badge", dry_run=True)["pages"][0]["command"]
    assert str(assembly.parent / base / frozen["prompt"]["file"]) in planned
    assert (assembly.parent / base / frozen["design"][0]["image"]["file"]).read_bytes() == before
    draw_batch(assembly, made)
    sliced = production.slice_batch(assembly, "test.badge")
    assert production.check(assembly, "test.badge")["summary"]["error"] == 1
    candidate = assembly.parent / sliced["candidates"][0]["candidate"]
    record = json.loads((candidate / "candidate.json").read_text())
    assert record["generation_reference"] == made["reference"]
    assert Image.open(candidate / "qa_protected.png").convert("L").getpixel((36, 42)) == 255
    # A changed pinned image is rejected, rather than silently drawn or cut.
    (assembly.parent / base / frozen["design"][0]["image"]["file"]).write_bytes(b"bad")
    with pytest.raises(contract.PartsContractError):
        production.draw(assembly, "test.badge", dry_run=True)


def test_curation_restores_a_candidate_and_invalidates_only_changed_pose(tmp_path):
    assembly = setup(tmp_path)
    draw_batch(assembly, production.sheet(assembly, "test.badge"))
    first = production.slice_batch(assembly, "test.badge")["candidates"][0]["candidate"]
    production.check(assembly, "test.badge"); production.build(assembly, "test.badge")
    production.review(assembly, "test.badge", reviewer="tester", approve=["all"])
    index, registry = workspace.read_workspace(assembly)
    appearance = contract.seal({"kind": "parts-appearance", "schema_version": 1, "id": "curation.test",
        **{k: index[k] for k in ("rig", "motion", "catalog", "body", "states")},
        "selections": {slot["id"]: contract.reference(registry["test.badge"]) if slot["id"] == "hair" else None
                       for slot in registry[index["catalog"]["id"]]["slots"]}})
    assert production.release_reviews(assembly.parent, registry, appearance)
    region = Image.open(assembly.parent / first / "mask.png").copy()
    region.paste(0, (0, 44, 128, 128)); region.save(tmp_path / "smaller.png")
    second = production.mask(assembly, "test.badge", "walk.01", tmp_path / "smaller.png")["candidate"]
    restored = production.select_candidate(assembly, "test.badge", "walk.01", first)
    assert restored["changed"] and second != first
    rows = production._load(assembly.parent, "test.badge")["tracks"]["main"]["poses"]
    assert rows["walk.01"]["decision"] == "unverified" and rows["walk.01"]["check"] is None
    assert rows["walk.02"]["decision"] == "approved"
    with pytest.raises(contract.PartsContractError, match="current approved production"):
        production.release_reviews(assembly.parent, registry, appearance)
    assert not production.select_candidate(assembly, "test.badge", "walk.01", first)["changed"]
    with pytest.raises(contract.PartsContractError, match="unknown candidate"):
        production.select_candidate(assembly, "test.badge", "walk.02", first)
    Image.new("RGBA", (128, 128)).save(assembly.parent / first / "part.png")
    with pytest.raises(contract.PartsContractError, match="image changed"):
        production.select_candidate(assembly, "test.badge", "walk.01", first)


def test_occlusion_preserves_raw_part_and_builds_a_visible_layer(tmp_path):
    assembly = setup(tmp_path)
    config = json.loads((tmp_path / "badge.json").read_text())
    config["cut"]["method"] = "part-only"
    config["visibility"] = "mask"
    region = Image.new("L", (128, 128))
    region.putpixel((36, 42), 255)
    region.save(assembly.parent / "occluded.png")
    config["pose_checks"] = {"walk.01": {name: "occluded.png" for name in
                                        ("occluded", "protected", "required")}}
    file = tmp_path / "direct.json"
    file.write_text(json.dumps(config))
    production.configure(assembly, "test.badge", file)
    made = production.sheet(assembly, "test.badge")
    for page in made["pages"]:
        guide = json.loads((assembly.parent / page["guide"]).read_text())
        drawn_sheet(guide, assembly.parent / page["art"])
    sliced = production.slice_batch(assembly, "test.badge")
    candidate = assembly.parent / sliced["candidates"][0]["candidate"]
    raw = Image.open(candidate / "part.png").convert("RGBA")
    visible = Image.open(candidate / "render_part.png").convert("RGBA")
    assert raw.getpixel((36, 42)) == (200, 40, 40, 255)
    assert visible.getpixel((36, 42))[3] == 0
    assert visible.getpixel((37, 42)) == raw.getpixel((37, 42))
    assert production.check(assembly, "test.badge")["summary"]["error"] == 0
    metrics = production._load(assembly.parent, "test.badge")["tracks"]["main"]["poses"]["walk.01"]["check"]["metrics"]
    assert metrics["occluded_pixels"] == 1 and metrics["dropped"] == 0
    production.build(assembly, "test.badge")
    _, registry = workspace.read_workspace(assembly)
    placement = registry["test.badge"]["bindings"][0]["pieces"][0]["sample_map"]["walk.right"]["sample.0"]
    assert placement["mask"] is None  # Runtime masks target body providers, not this layer.
    from sprite_gen.spec import parts_assets
    assert placement["image"]["sha256"] == parts_assets.sha256((candidate / "render_part.png").read_bytes())
    manual = production.mask(assembly, "test.badge", "walk.01", candidate / "mask.png")
    new_candidate = assembly.parent / manual["candidate"]
    assert Image.open(new_candidate / "render_part.png").getpixel((36, 42))[3] == 0
    assert (new_candidate / "qa_required.png").read_bytes() == (candidate / "qa_required.png").read_bytes()
    # Candidate-local pinning also detects a changed occlusion guide.
    Image.new("L", (128, 128)).save(new_candidate / "render_mask.png")
    with pytest.raises(contract.PartsContractError, match="changed occlusion mask"):
        production.check(assembly, "test.badge")


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
    assert "sent back at review: badge too low." in (assembly.parent / redo["prompt"]).read_text()
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


def test_an_excluded_zone_is_left_empty_in_every_candidate(tmp_path):
    assembly = setup(tmp_path)
    value = json.loads((tmp_path / "badge.json").read_text())
    value["cut"]["exclude"] = [[0, 0, 128, 128]]
    (tmp_path / "v2.json").write_text(json.dumps(value))
    production.configure(assembly, "test.badge", tmp_path / "v2.json")
    draw_batch(assembly, production.sheet(assembly, "test.badge"))
    sliced = production.slice_batch(assembly, "test.badge")
    record = json.loads((assembly.parent / sliced["candidates"][0]["candidate"] / "candidate.json").read_text())
    assert record["pixels"] == 0


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
    from sprite_gen.serve.serve_parts_review import ReviewDesk
    desk = ReviewDesk(assembly)
    candidates = desk.candidates("test.copy", "walk.01")["candidates"]
    assert "under" in candidates[0]["images"]
    assert desk.render("test.copy", "walk.01", "body-raw")
    assert desk.render("test.copy", "walk.01", "interfaces")


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
        import zipfile
        from io import BytesIO
        reference_zip = urllib.request.urlopen(base + "/api/reference/test.badge/" + sheet["batch"] + ".zip").read()
        with zipfile.ZipFile(BytesIO(reference_zip)) as archive:
            manifest = json.loads(archive.read("reference.json"))
            assert list(manifest["poses"]) == ["walk.02"]
            assert archive.read(manifest["poses"]["walk.02"]["reference"]["file"]).startswith(b"\x89PNG")
        candidates = json.loads(urllib.request.urlopen(base + "/api/candidates/test.badge/walk.01").read())
        assert len(candidates["candidates"]) == 1 and candidates["candidates"][0]["selected"]
        for name, mime in (("review.js", "javascript"), ("review.css", "css")):
            assert mime in urllib.request.urlopen(base + "/" + name).headers["Content-Type"]
    finally:
        server.shutdown()


def test_review_views_show_independent_parts_and_do_not_show_a_stale_build(tmp_path):
    from io import BytesIO
    from sprite_gen.serve.serve_parts_review import ReviewDesk
    assembly = setup(tmp_path)
    draw_batch(assembly, production.sheet(assembly, "test.badge"))
    first = production.slice_batch(assembly, "test.badge")["candidates"][0]["candidate"]
    production.check(assembly, "test.badge"); production.build(assembly, "test.badge")
    mask = Image.open(assembly.parent / first / "mask.png").copy()
    mask.paste(0, (0, 44, 128, 128)); mask.save(tmp_path / "smaller.png")
    second = production.mask(assembly, "test.badge", "walk.01", tmp_path / "smaller.png")["candidate"]
    desk = ReviewDesk(assembly)
    raw = Image.open(assembly.parent / second / "part.png").convert("RGBA")
    shown = Image.open(BytesIO(desk.render("test.badge", "walk.01", "cut")))
    assert shown.tobytes() == raw.resize((512, 512), Image.Resampling.NEAREST).tobytes()
    under = Image.open(assembly.parent / second / "under.png").convert("RGBA")
    under.alpha_composite(raw)
    body = Image.open(BytesIO(desk.render("test.badge", "walk.01", "body")))
    assert body.tobytes() == under.resize((512, 512), Image.Resampling.NEAREST).tobytes()
    listed = desk.candidates("test.badge", "walk.01")["candidates"]
    assert len(listed) == 2 and listed[0]["selected"]
    historical = Image.open(BytesIO(desk.render("test.badge", "walk.01", "part", Path(first).name)))
    assert historical.getpixel((36*4, 44*4))[3] == 255 and shown.getpixel((36*4, 44*4))[3] == 0
    with pytest.raises(contract.PartsContractError, match="unknown candidate"):
        desk.render("test.badge", "walk.01", "part", "../../outside")


def test_review_render_cache_refreshes_for_body_context_without_part_digest_change(tmp_path, monkeypatch):
    from copy import deepcopy
    from sprite_gen.serve.serve_parts_review import ReviewDesk
    assembly = setup(tmp_path)
    draw_batch(assembly, production.sheet(assembly, 'test.badge'))
    production.slice_batch(assembly, 'test.badge')
    production.check(assembly, 'test.badge'); production.build(assembly, 'test.badge')
    desk = ReviewDesk(assembly)
    calls = []
    def frames(*args, **kwargs):
        calls.append(1)
        color = 'red' if len(calls)==1 else 'blue'
        return {pose:Image.new('RGBA',(128,128),color) for pose in ('walk.01','walk.02')},None
    monkeypatch.setattr(production, '_render_alone', frames)
    first = desk.render('test.badge','walk.01','body')
    assert desk.render('test.badge','walk.01','body')==first and len(calls)==1
    # Only ownership/body revision changes, exactly the native-foot failure.
    root,index,registry,sha = desk._state()
    changed = deepcopy(index)
    changed['body'][0]['digest'] = '1'*64
    monkeypatch.setattr(desk, '_state', lambda:(root,changed,registry,sha))
    assert desk.render('test.badge','walk.01','body')!=first and len(calls)==2


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


def test_position_preview_matches_saved_build_without_mutating_or_editing_other_poses(tmp_path):
    from io import BytesIO
    from parts_helpers import tree_hashes
    from sprite_gen.serve.serve_parts_review import ReviewDesk
    assembly = setup(tmp_path)
    draw_batch(assembly, production.sheet(assembly, "test.badge"))
    production.slice_batch(assembly, "test.badge")
    production.check(assembly, "test.badge")
    production.build(assembly, "test.badge")
    desk = ReviewDesk(assembly)
    from sprite_gen.curate import parts_appearance
    from parts_helpers import part_spec
    index,registry=workspace.read_workspace(assembly)
    support=tmp_path/'outfit-support';support.mkdir();Image.new('RGBA',(1,1)).save(support/'piece.png')
    selected={'hair':contract.reference(registry['test.badge'])}
    for slot,node,socket in [('expression','head','face'),('upper','arm_l','shoulder_l'),('lower','leg_l','waist'),('footwear','foot_l','foot_l')]:
        spec=part_spec(tmp_path/f'{slot}-support.json',assembly,support,identity='support.'+slot,slot=slot,node=node,socket=socket,pivot=(0,0))
        imported=parts_import.import_pack(assembly,spec,support);selected[slot]=imported['part']
    index, registry = workspace.read_workspace(assembly)
    outfit=tmp_path/'outfit.json'
    outfit.write_text(json.dumps({'kind':'parts-appearance-selection','schema_version':1,'id':'test.position-outfit','selections':selected}))
    parts_appearance.create(assembly,outfit)
    meta = desk.part("test.badge")
    assert meta["canvas"] == {"width": 128, "height": 128}
    _, registry = workspace.read_workspace(assembly)
    clip = registry[workspace.read_workspace(assembly)[0]["motion"]["id"]]["clips"][0]
    assert meta["clips"][0]["samples"] == [
        {"pose": s["pose_id"], "duration_ms": s["duration_ticks"] * 1000 / clip["timebase"]}
        for s in clip["samples"]]
    before = tree_hashes(assembly.parent)
    other = desk.render("test.badge", "walk.02", "body")
    preview = desk.render("test.badge", "walk.01", "position", dx=3, dy=-2)
    assert tree_hashes(assembly.parent) == before
    with pytest.raises(contract.PartsContractError, match="clips source"):
        desk.render("test.badge", "walk.01", "position", dx=-128)
    assert tree_hashes(assembly.parent) == before
    edited = desk.act("test.badge", "edit", {"pose": "walk.01", "operations": [{"type": "translate", "dx": 3, "dy": -2}]})
    assert not edited["selected"] and edited["provider_calls"] == 0
    desk.act("test.badge", "apply", {"pose": "walk.01", "candidate": edited["preview_candidate"]})
    built = desk.render("test.badge", "walk.01", "body")
    assert Image.open(BytesIO(preview)).tobytes() == Image.open(BytesIO(built)).tobytes()
    assert desk.render("test.badge", "walk.02", "body") == other
    assert desk.part("test.badge")["poses"][0]["decision"] != "approved"
    index,registry=workspace.read_workspace(assembly)
    assert index['appearance']['id']=='test.position-outfit'
    assert registry['test.position-outfit']['selections']['hair']==contract.reference(registry['test.badge'])


def test_new_required_criterion_cannot_be_bypassed_with_an_older_candidate(tmp_path):
    assembly = setup(tmp_path)
    draw_batch(assembly, production.sheet(assembly, "test.badge"))
    production.slice_batch(assembly, "test.badge")
    production.check(assembly, "test.badge")
    production.build(assembly, "test.badge")
    config=json.loads((tmp_path/'badge.json').read_text())
    Image.new('L',(128,128),0).save(assembly.parent/'required.png')
    config['pose_checks']={'walk.01':{'required':'required.png'}}
    (tmp_path/'current-criteria.json').write_text(json.dumps(config))
    production.configure(assembly,'test.badge',tmp_path/'current-criteria.json')
    # Even a cached passing result cannot bypass the newly declared criterion.
    with pytest.raises(contract.PartsContractError,match='lacks current criteria'):
        production.review(assembly,'test.badge',reviewer='tester',approve=['walk.01'])
    checked=production.check(assembly,'test.badge')
    assert checked['summary']['error']==1


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
    hat["design_id"] = "hat.renamed"  # a new hat pack version that draws the same pixels
    (tmp_path / "hat.json").write_text(json.dumps(hat))
    parts_import.import_pack(assembly, tmp_path / "hat.json", source)
    assert production.check(assembly, "test.hair", track="capped")["summary"]["capped"]["error"] == 0
    Image.new("RGBA", (8, 4), (60, 90, 140, 255)).save(source / "piece.png")  # the hat redrawn
    hat = json.loads(part_spec(tmp_path / "hat.json", assembly, source, slot="headwear", node="head", socket="head",
                               identity="test.hat", pivot=(4, 4)).read_text())
    hat["covers"] = ["headwear"]
    (tmp_path / "hat.json").write_text(json.dumps(hat))
    parts_import.import_pack(assembly, tmp_path / "hat.json", source)
    production.slice_batch(assembly, "test.hair", track="capped")  # cut again over what the sheet showed
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
