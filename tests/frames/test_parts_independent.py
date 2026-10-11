# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
import pytest
from parts_helpers import posed_workspace
from sprite_gen.frames import (
    parts_import,
    parts_production as p,
    parts_rebind,
    parts_part_edit,
    parts_vision,
    parts_scaffold,
)
from sprite_gen.spec import parts_contract as c, parts_workspace as w


def setup(tmp_path):
    assembly, _ = posed_workspace(tmp_path)
    body = parts_rebind.body_spec(assembly, tmp_path / "body.json", body_id="test.body")
    parts_import.import_pack(
        assembly, tmp_path / "body.json", Path(body["source_root"])
    )
    _, index, registry, _ = __import__(
        "sprite_gen.curate.parts_motion", fromlist=["_active"]
    )._active(assembly)
    order, _ = p._poses(registry[index["motion"]["id"]], index["states"])
    cfg = {
        "kind": "parts-production-config",
        "schema_version": 1,
        "part_id": "test.direct",
        "design_id": "direct",
        "slot": "hair",
        "node": "hair_front",
        "socket": "head",
        "prompt": {"text": "Two independent red and blue hair layers"},
        "independent": {
            "layers": [
                {
                    "id": "back",
                    "node": "hair_back",
                    "targets": {pose: [30, 20, 60, 50] for pose in order},
                },
                {
                    "id": "front",
                    "node": "hair_front",
                    "targets": {pose: [35, 25, 65, 55] for pose in order},
                },
            ]
        },
    }
    file = tmp_path / "config.json"
    file.write_text(json.dumps(cfg))
    p.init(assembly, file)
    return assembly, order


def produce(assembly, order, tmp_path):
    batch = p.sheet(assembly, "test.direct", poses=order)
    paths = []
    for i, job in enumerate(batch["pages"]):
        im = Image.new("RGBA", (128, 128))
        ImageDraw.Draw(im).rectangle(
            (20, 20, 106, 106),
            fill=(255, 20, 30, 255) if job["layer"] == "back" else (20, 30, 255, 255),
        )
        path = tmp_path / f"art-{i}.png"
        im.save(path)
        paths.append((Path(str(i)), path))
    result = p.slice_batch(assembly, "test.direct", batch=batch["batch"], pages=paths)
    assert result["sliced"] == len(order)
    return batch


def answer(status="pass"):
    return json.dumps(
        {
            "status": status,
            "checks": {
                key: {
                    "result": "pass" if status == "pass" else "fail",
                    "reason": "actual visual observation",
                }
                for key in parts_vision.CATEGORIES
            },
            "summary": "시각 관찰",
        }
    )


def test_vision_closeups_keep_distant_piece_out_of_each_joint_crop():
    under = Image.new("RGBA", (768, 768))
    back = Image.new("RGBA", under.size)
    front = Image.new("RGBA", under.size)
    ImageDraw.Draw(back).rectangle((105, 455, 145, 490), fill="red")
    ImageDraw.Draw(front).rectangle((570, 630, 610, 665), fill="blue")
    layers = [
        {"id": "back", "target": [100, 450, 150, 500]},
        {"id": "front", "target": [565, 625, 615, 675]},
    ]
    panels, crops = parts_vision.layer_closeups(
        {"under": under, "piece.back": back, "piece.front": front}, under, layers
    )
    assert len(panels) == 6
    assert crops["back"] == [68, 402, 182, 532]
    assert crops["front"] == [533, 577, 647, 707]
    assert all(image.size == (114, 130) for _, image in panels)
    assert crops["back"][2] < crops["front"][0]


def test_review_attachment_views_use_same_native_crop_without_vision_call(tmp_path):
    from io import BytesIO
    from sprite_gen.serve.serve_parts_review import ReviewDesk

    assembly, order = setup(tmp_path)
    produce(assembly, order, tmp_path)
    desk = ReviewDesk(assembly)
    images = [Image.open(BytesIO(desk.render("test.direct", order[0], kind, piece="back")))
              for kind in ("attachment-body", "attachment-part", "attachment-composite")]
    assert len({im.size for im in images}) == 1
    assert images[1].getbbox()
    assert not p._load(assembly.parent, "test.direct")["tracks"]["main"]["poses"][order[0]].get("vision")
    with pytest.raises(c.PartsContractError, match="known independent"):
        desk.render("test.direct", order[0], "attachment-composite", piece="unknown")


def test_provider_receives_coverage_reference_not_just_saved_qa_files(tmp_path):
    assembly, order = setup(tmp_path)
    root = assembly.parent
    state = p._load(root, "test.direct")
    required = Image.new("L", (128, 128))
    ImageDraw.Draw(required).rectangle((35, 25, 40, 30), fill=255)
    required.save(root / "required.png")
    cfg = state["config"]
    cfg["pose_checks"] = {order[0]: {"required": "required.png"}}
    path = tmp_path / "with-coverage.json"
    path.write_bytes(w.json_bytes(cfg))
    p.configure(assembly, "test.direct", path)
    batch = p.sheet(assembly, "test.direct", poses=[order[0]])
    from sprite_gen.frames import parts_independent
    document, folder = parts_independent.read_reference(root, batch["reference"])
    for job in document["jobs"]:
        refs = [file for file in job["refs"] if file.endswith(".interfaces.view.png")]
        assert len(refs) == 1
        assert (root / folder / refs[0]).is_file()
        assert "GREEN required coverage" in (root / folder / job["prompt"]).read_text()


def test_reference_view_cannot_expose_hidden_rgb_as_false_geometry():
    from sprite_gen.frames.parts_independent import reference_view

    a = Image.new("RGBA", (40, 30), (255, 0, 0, 0))
    b = Image.new("RGBA", (40, 30), (0, 255, 0, 0))
    for image in (a, b):
        ImageDraw.Draw(image).rectangle((10, 10, 20, 20), fill=(0, 0, 255, 255))
    before = np.array(a).copy()
    av, bv = reference_view(a), reference_view(b)
    assert av.mode == "RGB" and np.array_equal(np.asarray(av), np.asarray(bv))
    assert av.getpixel((0, 0)) == (231, 231, 231)
    assert av.getpixel((15, 15)) == (0, 0, 255)
    assert np.array_equal(np.asarray(a), before)


def test_failed_vision_preserves_explicit_curator_rejection(tmp_path):
    assembly, order = setup(tmp_path)
    produce(assembly, order, tmp_path)
    p.check(assembly, "test.direct")
    p.build(assembly, "test.direct")
    p.review(assembly, "test.direct", reject=[order[0]], reviewer="curator", note="Wrong ankle perspective")
    parts_vision.inspect(assembly, "test.direct", order[0],
                         transport=lambda *args, **kwargs: (answer("rework"), {"transport": "test-double"}))
    row = p._load(assembly.parent, "test.direct")["tracks"]["main"]["poses"][order[0]]
    assert (row["decision"], row["reviewer"], row["note"]) == ("rejected", "curator", "Wrong ankle perspective")


def test_reference_budget_combines_geometry_without_dropping_coverage(tmp_path):
    assembly, order = setup(tmp_path)
    produce(assembly, order, tmp_path)
    root = assembly.parent
    cfg = p._load(root, "test.direct")["config"]
    Image.new("L", (128, 128), 255).save(root / "coverage.png")
    for name in ("design1.png", "design2.png", "design3.png"):
        Image.new("RGBA", (32, 32), "red").save(root / name)
    cfg["prompt"]["refs"] = ["design1.png", "design2.png", "design3.png"]
    cfg["pose_checks"] = {order[0]: {"required": "coverage.png"}}
    cfg["independent"]["reference_previous"] = True
    path = tmp_path / "budget.json"
    path.write_bytes(w.json_bytes(cfg))
    p.configure(assembly, "test.direct", path)
    batch = p.sheet(assembly, "test.direct", poses=[order[0]])
    from sprite_gen.frames import parts_independent
    document, folder = parts_independent.read_reference(root, batch["reference"])
    for job in document["jobs"]:
        assert len(job["refs"]) == 5
        assert job["refs"][0].endswith(".geometry.png")
        assert Image.open(root / folder / job["refs"][0]).size == (1280, 640)
        assert f"{order[0]}/{job['layer']}.interfaces.png" in document["files"]


def test_independent_images_retain_overlapping_back_layer_and_require_actual_vision(
    tmp_path,
):
    assembly, order = setup(tmp_path)
    produce(assembly, order, tmp_path)
    root = assembly.parent
    state = p._load(root, "test.direct")
    record = p.candidate_record(
        root, state, order[0], state["tracks"]["main"]["poses"][order[0]]["selected"]
    )
    back = Image.open(
        root
        / state["tracks"]["main"]["poses"][order[0]]["selected"]
        / record["images"]["piece.back"]["file"]
    )
    assert np.asarray(back)[..., 0].max() == 255
    assert record["independent_layers"][0]["node"] == "hair_back"
    p.check(assembly, "test.direct")
    p.build(assembly, "test.direct")
    with pytest.raises(c.PartsContractError, match="vision inspection"):
        p.review(assembly, "test.direct", approve=[order[0]], reviewer="tester")
    result = parts_vision.inspect(
        assembly,
        "test.direct",
        order[0],
        transport=lambda *args, **kwargs: (answer(), {"transport": "test-double"}),
    )
    assert result["status"] == "pass" and not result["art_approved"]
    p.review(assembly, "test.direct", approve=[order[0]], reviewer="tester")
    state = p._load(root, "test.direct")
    row = state["tracks"]["main"]["poses"][order[0]]
    before = row["selected"]
    edited = parts_part_edit.edit(
        assembly,
        "test.direct",
        order[0],
        [{"type": "translate", "piece": "front", "dx": 2}],
    )
    original = p.candidate_record(root, state, order[0], before)
    new = p.candidate_record(
        root, p._load(root, "test.direct"), order[0], edited["preview_candidate"]
    )
    assert original["images"]["piece.back"] == new["images"]["piece.back"]
    assert original["images"]["piece.front"] != new["images"]["piece.front"]
    p.select_candidate(assembly, "test.direct", order[0], edited["preview_candidate"])
    p.check(assembly, "test.direct")
    p.build(assembly, "test.direct")
    with pytest.raises(c.PartsContractError, match="vision inspection"):
        p.review(assembly, "test.direct", approve=[order[0]], reviewer="tester")


def test_missing_layer_never_selects_partial_candidate_and_references_are_frozen(
    tmp_path,
):
    assembly, order = setup(tmp_path)
    batch = p.sheet(assembly, "test.direct", poses=[order[0]])
    im = Image.new("RGBA", (32, 32))
    ImageDraw.Draw(im).rectangle((4, 4, 28, 28), fill="red")
    path = tmp_path / "one.png"
    im.save(path)
    result = p.slice_batch(
        assembly, "test.direct", batch=batch["batch"], pages=[(0, path)]
    )
    assert result["sliced"] == 0 and result["missing"] == [order[0]]
    assert not p._load(assembly.parent, "test.direct")["tracks"]["main"]["poses"]
    plan = p.draw(assembly, "test.direct", dry_run=True)
    assert all(
        "--transparent" in row["command"] and "codex" in row["command"]
        for row in plan["pages"]
    )
    assert len(plan["pages"]) == 2


@pytest.mark.parametrize(
    "reply",
    [answer().replace('"status": "pass"', '"status": "uncertain"'), "{}", "not JSON"],
)
def test_invalid_or_contradictory_vision_never_passes(reply):
    with pytest.raises(c.PartsContractError):
        parts_vision.validate_answer(reply)


def test_scaffold_derives_draft_bounds_from_owned_body_without_claiming_coverage(
    tmp_path,
):
    assembly, order = setup(tmp_path)
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Independent blue cape")
    output = tmp_path / "draft.json"
    result = parts_scaffold.scaffold(
        assembly,
        "test.cape",
        "hair",
        ["cape:hair_front:root"],
        prompt,
        output,
        padding=4,
    )
    config = w.decode_json(output.read_bytes())
    assert set(config["independent"]["layers"][0]["targets"]) == set(order)
    assert config["independent"]["require_vision"] is True
    assert config["pose_checks"] == {}
    assert result["status"] == "draft"


def test_vision_context_changes_when_interface_bytes_change_at_the_same_path(tmp_path):
    from sprite_gen.curate import parts_motion

    assembly, order = setup(tmp_path)
    produce(assembly, order, tmp_path)
    root = assembly.parent
    state = p._load(root, "test.direct")
    image = Image.new("L", (128, 128))
    image.save(root / "guard.png")
    state["config"]["pose_checks"] = {order[0]: {"protected": "guard.png"}}
    file = tmp_path / "guard-config.json"
    file.write_bytes(w.json_bytes(state["config"]))
    p.configure(assembly, "test.direct", file)
    parts_vision.inspect(
        assembly,
        "test.direct",
        order[0],
        transport=lambda *args, **kwargs: (answer(), {"transport": "test-double"}),
    )
    _, index, _, _ = parts_motion._active(assembly)
    assert parts_vision.current(root, index, p._load(root, "test.direct"), order[0])[
        "current"
    ]
    Image.new("L", (128, 128), 255).save(root / "guard.png")
    assert not parts_vision.current(
        root, index, p._load(root, "test.direct"), order[0]
    )["current"]


def test_redraw_one_layer_preserves_other_pixels_and_native_output_ignores_import_crop(
    tmp_path,
):
    from sprite_gen.spec import parts_assets as assets

    assembly, order = setup(tmp_path)
    produce(assembly, order, tmp_path)
    root = assembly.parent
    state = p._load(root, "test.direct")
    prior = p.candidate_record(
        root, state, order[0], state["tracks"]["main"]["poses"][order[0]]["selected"]
    )
    state["config"]["independent"]["layers"][1]["source_bounds"] = {
        order[0]: [0, 0, 4, 4]
    }
    f = tmp_path / "crop-config.json"
    f.write_bytes(w.json_bytes(state["config"]))
    p.configure(assembly, "test.direct", f)
    batch = p.sheet(assembly, "test.direct", poses=[order[0]], layers=["front"])
    assert batch["pages"][0]["reuse"]
    image = Image.new("RGBA", (64, 64))
    ImageDraw.Draw(image).rectangle((20, 20, 50, 50), fill="green")
    output = root / batch["pages"][1]["art"]
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output)
    state = p._load(root, "test.direct")
    entry = p._batch(state["tracks"]["main"], batch["batch"])
    entry["attempts"] = [
        {
            "art": batch["pages"][1]["art"],
            "status": "drawn",
            "image_sha256": assets.sha256(output.read_bytes()),
        }
    ]
    p._save(root, state)
    result = p.slice_batch(assembly, "test.direct", batch=batch["batch"])
    assert result["sliced"] == 1
    state = p._load(root, "test.direct")
    candidate = p.candidate_record(
        root, state, order[0], state["tracks"]["main"]["poses"][order[0]]["selected"]
    )
    assert candidate["images"]["piece.back"] == prior["images"]["piece.back"]
    assert candidate["method"]["registration"]["front"]["source_bounds"] is None
    assert candidate["images"]["piece.front"] != prior["images"]["piece.front"]


def test_external_redraw_cannot_silently_target_a_preserved_layer(tmp_path):
    assembly, order = setup(tmp_path)
    produce(assembly, order, tmp_path)
    root = assembly.parent
    batch = p.sheet(assembly, "test.direct", poses=[order[0]], layers=["front"])
    before = p._load(root, "test.direct")
    image = tmp_path / "external-front.png"
    art = Image.new("RGBA", (64, 64))
    ImageDraw.Draw(art).rectangle((16, 16, 48, 48), fill="green")
    art.save(image)
    with pytest.raises(c.PartsContractError, match="preserved layers.*editable indexes: 1"):
        p.slice_batch(
            assembly, "test.direct", batch=batch["batch"], pages=[(Path("0"), image)]
        )
    assert p._load(root, "test.direct") == before
    result = p.slice_batch(
        assembly, "test.direct", batch=batch["batch"], pages=[(Path("1"), image)]
    )
    assert result["sliced"] == 1
    after = p._load(root, "test.direct")
    old = before["tracks"]["main"]["poses"][order[0]]["selected"]
    new = after["tracks"]["main"]["poses"][order[0]]["selected"]
    assert new != old
    old_record = p.candidate_record(root, before, order[0], old)
    new_record = p.candidate_record(root, after, order[0], new)
    assert new_record["images"]["piece.back"] == old_record["images"]["piece.back"]
    assert new_record["images"]["piece.front"] != old_record["images"]["piece.front"]


def test_nonlooping_reference_does_not_compare_last_pose_as_first_neighbour():
    motion = {
        "clips": [
            {
                "state": "run",
                "loop": False,
                "samples": [{"pose_id": str(n)} for n in range(3)],
            }
        ]
    }
    assert p._neighbours(motion, ["run"]) == {"0": {"1"}, "1": {"0", "2"}, "2": {"1"}}
    motion["clips"][0]["loop"] = True
    assert p._neighbours(motion, ["run"])["0"] == {"1", "2"}


def test_replacing_neighbour_invalidates_temporal_vision(tmp_path):
    from sprite_gen.curate import parts_motion

    assembly, order = setup(tmp_path)
    produce(assembly, order, tmp_path)
    root = assembly.parent
    parts_vision.inspect(
        assembly,
        "test.direct",
        order[0],
        transport=lambda *args, **kwargs: (answer(), {"transport": "test-double"}),
    )
    _, index, _, _ = parts_motion._active(assembly)
    assert parts_vision.current(root, index, p._load(root, "test.direct"), order[0])[
        "current"
    ]
    result = parts_part_edit.edit(
        assembly,
        "test.direct",
        order[1],
        [{"type": "translate", "piece": "front", "dx": 1}],
    )
    p.select_candidate(assembly, "test.direct", order[1], result["preview_candidate"])
    assert not parts_vision.current(
        root, index, p._load(root, "test.direct"), order[0]
    )["current"]


def test_authored_landmarks_are_frozen_geometry_guides_and_not_runtime_sockets(
    tmp_path,
):
    assembly, order = setup(tmp_path)
    root = assembly.parent
    state = p._load(root, "test.direct")
    state["config"]["independent"]["layers"][0]["landmarks"] = {
        order[0]: {"heel": [34, 35], "toe": [55, 39]}
    }
    f = tmp_path / "landmarks.json"
    f.write_bytes(w.json_bytes(state["config"]))
    p.configure(assembly, "test.direct", f)
    batch = p.sheet(assembly, "test.direct", poses=[order[0]])
    job = batch["pages"][0]
    assert any(path.endswith(".landmarks.view.png") for path in job["refs"])
    from sprite_gen.frames import parts_independent

    doc, base = parts_independent.read_reference(root, batch["reference"])
    assert "Authored body landmarks" in (root / base / job["prompt"]).read_text()
    assert doc["context"]["rig"] == w.read_workspace(assembly)[0]["rig"]


def test_changed_qa_requires_new_candidate_without_redrawing_source_pixels(tmp_path):
    assembly, order = setup(tmp_path)
    produce(assembly, order, tmp_path)
    root = assembly.parent
    state = p._load(root, "test.direct")
    pose = order[0]
    old = p.candidate_record(
        root, state, pose, state["tracks"]["main"]["poses"][pose]["selected"]
    )
    guard = Image.new("L", (128, 128))
    ImageDraw.Draw(guard).rectangle((40, 30, 44, 34), fill=255)
    guard.save(root / "required.png")
    state["config"]["pose_checks"] = {pose: {"required": "required.png"}}
    f = tmp_path / "new-guides.json"
    f.write_bytes(w.json_bytes(state["config"]))
    p.configure(assembly, "test.direct", f)
    p.check(assembly, "test.direct")
    assert (
        p._load(root, "test.direct")["tracks"]["main"]["poses"][pose]["check"]["status"]
        == "error"
    )
    result = parts_part_edit.edit(
        assembly, "test.direct", pose, [{"type": "translate", "dx": 0, "dy": 0}]
    )
    assert result["qa_revalidated"] and result["changed_pixels"] == 0
    p.select_candidate(assembly, "test.direct", pose, result["preview_candidate"])
    p.check(assembly, "test.direct")
    state = p._load(root, "test.direct")
    new = p.candidate_record(root, state, pose, result["preview_candidate"])
    assert old["images"]["piece.back"] == new["images"]["piece.back"]
    assert (
        state["tracks"]["main"]["poses"][pose]["check"]["metrics"]["required_pixels"]
        == 0
    )


def test_faint_alpha_cannot_pass_opaque_required_coverage(tmp_path):
    assembly, order = setup(tmp_path)
    root = assembly.parent
    state = p._load(root, "test.direct")
    guard = Image.new("L", (128, 128))
    ImageDraw.Draw(guard).rectangle((40, 30, 44, 34), fill=255)
    guard.save(root / "required.png")
    state["config"]["pose_checks"] = {order[0]: {"required": "required.png"}}
    f = tmp_path / "opaque-required.json"
    f.write_bytes(w.json_bytes(state["config"]))
    p.configure(assembly, "test.direct", f)
    batch = p.sheet(assembly, "test.direct", poses=order)
    paths = []
    for n, job in enumerate(batch["pages"]):
        im = Image.new("RGBA", (128, 128))
        ImageDraw.Draw(im).rectangle((20, 20, 106, 106), fill=(255, 0, 0, 64))
        file = tmp_path / f"ghost-{n}.png"
        im.save(file)
        paths.append((n, file))
    p.slice_batch(assembly, "test.direct", batch=batch["batch"], pages=paths)
    p.check(assembly, "test.direct")
    check = p._load(root, "test.direct")["tracks"]["main"]["poses"][order[0]]["check"]
    assert check["metrics"]["required_pixels"] == 25 and check["status"] == "error"


def test_body_first_redraw_does_not_condition_on_rejected_previous_shape(tmp_path):
    assembly, order = setup(tmp_path)
    produce(assembly, order, tmp_path)
    batch = p.sheet(assembly, "test.direct", poses=[order[0]])
    assert all(
        not any(".previous." in ref for ref in page["refs"]) for page in batch["pages"]
    )
    state = p._load(assembly.parent, "test.direct")
    state["config"]["independent"]["reference_previous"] = True
    f = tmp_path / "edit-source.json"
    f.write_bytes(w.json_bytes(state["config"]))
    p.configure(assembly, "test.direct", f)
    batch = p.sheet(assembly, "test.direct", poses=[order[0]])
    assert all(
        any(".previous." in ref for ref in page["refs"]) for page in batch["pages"]
    )


def test_resized_layer_registers_required_coverage_outside_old_target(tmp_path):
    assembly, order = setup(tmp_path)
    produce(assembly, order, tmp_path)
    root, pose = assembly.parent, order[0]
    state = p._load(root, "test.direct")
    guard = Image.new("L", (128, 128))
    ImageDraw.Draw(guard).rectangle((68, 35, 70, 37), fill=255)
    guard.save(root / "expanded-required.png")
    state["config"]["pose_checks"] = {pose: {"required": "expanded-required.png"}}
    config = tmp_path / "expanded-config.json"
    config.write_bytes(w.json_bytes(state["config"]))
    p.configure(assembly, "test.direct", config)
    result = parts_part_edit.edit(
        assembly,
        "test.direct",
        pose,
        [{"type": "fit-alpha-box", "piece": "front", "bounds": [35, 25, 75, 65]}],
    )
    p.select_candidate(assembly, "test.direct", pose, result["preview_candidate"])
    p.check(assembly, "test.direct")
    state = p._load(root, "test.direct")
    row = state["tracks"]["main"]["poses"][pose]
    record = p.candidate_record(root, state, pose, row["selected"])
    assert record["method"]["programmatic_edit"]["review_bounds_updated"]
    assert row["check"]["metrics"]["required_pixels"] == 0
    assert row["decision"] == "unverified"


def test_pose_angle_edit_retains_other_layer_and_rejects_clipping(tmp_path):
    assembly, order = setup(tmp_path)
    produce(assembly, order, tmp_path)
    root, pose = assembly.parent, order[0]
    state = p._load(root, "test.direct")
    old = p.candidate_record(
        root, state, pose, state["tracks"]["main"]["poses"][pose]["selected"]
    )
    result = parts_part_edit.edit(
        assembly,
        "test.direct",
        pose,
        [{"type": "rotate", "piece": "front", "angle": 10}],
    )
    new = p.candidate_record(
        root, p._load(root, "test.direct"), pose, result["preview_candidate"]
    )
    assert old["images"]["piece.back"] == new["images"]["piece.back"]
    assert old["images"]["raw.front"] == new["images"]["raw.front"]
    assert old["images"]["piece.front"] != new["images"]["piece.front"]
    assert not result["selected"] and result["provider_calls"] == 0
    with pytest.raises(c.PartsContractError, match="clips"):
        parts_part_edit.edit(
            assembly,
            "test.direct",
            pose,
            [{"type": "rotate", "piece": "front", "angle": 10, "dx": 120}],
        )
    with pytest.raises(c.PartsContractError, match="30 degrees"):
        parts_part_edit.edit(
            assembly,
            "test.direct",
            pose,
            [{"type": "rotate", "piece": "front", "angle": 45}],
        )


def test_pose_template_is_frozen_and_same_path_edits_stale_vision(tmp_path):
    assembly, order = setup(tmp_path)
    root, pose = assembly.parent, order[0]
    guide = root / "part-template.png"
    Image.new("RGBA", (128, 128), (20, 30, 40, 255)).save(guide)
    state = p._load(root, "test.direct")
    state["config"]["independent"]["layers"][0]["guides"] = {pose: [guide.name]}
    file = tmp_path / "template-config.json"
    file.write_bytes(w.json_bytes(state["config"]))
    p.configure(assembly, "test.direct", file)
    batch = produce(assembly, order, tmp_path)
    assert any(".shape-0.view.png" in path for path in batch["pages"][0]["refs"])
    parts_vision.inspect(
        assembly,
        "test.direct",
        pose,
        transport=lambda *args, **kwargs: (answer(), {"transport": "test-double"}),
    )
    from sprite_gen.curate import parts_motion

    _, index, _, _ = parts_motion._active(assembly)
    assert parts_vision.current(root, index, p._load(root, "test.direct"), pose)[
        "current"
    ]
    Image.new("RGBA", (128, 128), (120, 30, 40, 255)).save(guide)
    assert not parts_vision.current(root, index, p._load(root, "test.direct"), pose)[
        "current"
    ]
    from sprite_gen.frames import parts_independent

    # Original generation inputs remain immutable despite changing the source guide.
    parts_independent.read_reference(root, batch["reference"])


def test_wrong_body_ownership_is_blocked_before_generation(tmp_path):
    assembly, order = setup(tmp_path)
    root, pose = assembly.parent, order[0]
    from sprite_gen.curate import parts_motion

    _, index, registry, _ = parts_motion._active(assembly)
    frames, _ = p._render_alone(root, index, registry, None, index["states"])
    y, x = np.argwhere(np.asarray(frames[pose])[..., 3] >= 16)[0]
    guard = Image.new("L", frames[pose].size)
    ImageDraw.Draw(guard).point((int(x), int(y)), fill=255)
    guard.save(root / "body-required.png")
    state = p._load(root, "test.direct")
    cfg = state["config"]
    cfg["pose_checks"] = {pose: {"required": "body-required.png"}}
    layer = cfg["independent"]["layers"][0]
    layer["targets"][pose] = [0, 0, frames[pose].width, frames[pose].height]
    layer["body_nodes"] = ["hair_front"]
    file = tmp_path / "wrong-ownership.json"
    file.write_bytes(w.json_bytes(cfg))
    p.configure(assembly, "test.direct", file)
    with pytest.raises(c.PartsContractError, match="repair body ownership"):
        p.sheet(assembly, "test.direct", poses=[pose])
    plan = p._part_plan(index, registry, None, index["states"])
    layer["body_nodes"] = list(
        {
            draw["node"]
            for clip in plan["clips"]
            for sample in clip["samples"]
            for draw in sample["draw"]
            if draw["role"] == "body"
        }
    )
    file.write_bytes(w.json_bytes(cfg))
    p.configure(assembly, "test.direct", file)
    p.sheet(assembly, "test.direct", poses=[pose])
