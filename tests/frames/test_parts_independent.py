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


@pytest.mark.parametrize('covered', [False, True])
def test_independent_cover_rule_matches_preview_build_and_frozen_history(tmp_path, covered):
    from sprite_gen.curate import parts_motion
    from sprite_gen.compose import compose_parts
    from sprite_gen.frames import parts_candidate_scene, parts_independent
    from sprite_gen.serve.serve_parts_review import ReviewDesk

    assembly, order = setup(tmp_path)
    root, _, _, _ = parts_motion._active(assembly)
    cfg = p._load(root, 'test.direct')['config']
    rule = {'tag': 'footwear', 'show': 'uncovered'}
    cfg['independent']['layers'][1]['cover_rule'] = rule
    cfg['covers'] = ['footwear'] if covered else []
    path = tmp_path / 'cover.json'
    path.write_bytes(w.json_bytes(cfg))
    p.configure(assembly, 'test.direct', path)
    batch = produce(assembly, order, tmp_path)
    doc, _ = parts_independent.read_reference(root, batch['reference'])
    assert all(job.get('cover_rule') == (rule if job['layer'] == 'front' else None) for job in doc['jobs'])
    p.check(assembly, 'test.direct')
    p.build(assembly, 'test.direct')
    root, index, registry, _ = parts_motion._active(assembly)
    state = p._load(root, 'test.direct')
    row = state['tracks']['main']['poses'][order[0]]
    record = p.candidate_record(root, state, order[0], row['selected'])
    base = root / row['selected']
    plan, sample, load = parts_candidate_scene.scene(root, index, registry, state['config'], order[0],
        Image.open(base / 'part.png'), record['placement'], independent_layers=parts_independent.candidate_layers(record, base))
    preview, _ = compose_parts.render_sample(plan, sample, load)
    rendered, built_plan = p._render_alone(root, index, registry, c.reference(registry['test.direct']), index['states'])
    assert np.array_equal(np.asarray(preview), np.asarray(rendered[order[0]]))
    assert ('test.direct.front' in sample['suppressed']) == covered
    assert 'test.direct.back' not in sample['suppressed']
    assert sample['suppressed'] == built_plan['clips'][0]['samples'][0]['suppressed']
    assert not any(draw['placement']['mask'] for draw in sample['draw'])

    cfg['independent']['layers'][1]['cover_rule'] = {'tag': 'footwear', 'show': 'covered'}
    path.write_bytes(w.json_bytes(cfg))
    p.configure(assembly, 'test.direct', path)
    state = p._load(root, 'test.direct')
    assert not p._current(state, registry, order)
    assert ReviewDesk(assembly).candidates('test.direct', order[0])['candidates'][0]['status'] == 'stale'
    with pytest.raises(c.PartsContractError, match='independent layers changed'):
        p.build(assembly, 'test.direct')


def test_binding_cover_change_invalidates_current_production_build(tmp_path):
    assembly, order = setup(tmp_path)
    produce(assembly, order, tmp_path)
    p.check(assembly, 'test.direct')
    p.build(assembly, 'test.direct')
    from sprite_gen.curate import parts_motion
    root, _, registry, _ = parts_motion._active(assembly)
    state = p._load(root, 'test.direct')
    assert p._current(state, registry, order)
    cfg = state['config']
    cfg['covers'] = ['footwear']
    path = tmp_path / 'cover-binding.json'
    path.write_bytes(w.json_bytes(cfg))
    p.configure(assembly, 'test.direct', path)
    state = p._load(root, 'test.direct')
    assert not p._current(state, registry, order)
    assert all(row['check'] is None for row in state['tracks']['main']['poses'].values())


@pytest.mark.parametrize('has_provider', [True,False])
def test_independent_authored_mask_freezes_and_matches_real_render(tmp_path, has_provider):
    from sprite_gen.curate import parts_motion
    from sprite_gen.frames import parts_independent,parts_candidate_scene
    from sprite_gen.compose import compose_parts
    from sprite_gen.spec import parts_assets as assets
    from sprite_gen.serve.serve_parts_review import ReviewDesk
    assembly,order=setup(tmp_path)
    root,index,registry,_=parts_motion._active(assembly)
    mask=Image.new('RGBA',(128,128),'white')
    ImageDraw.Draw(mask).rectangle((38,25,45,35),fill='black')
    ImageDraw.Draw(mask).rectangle((31,21,34,24),fill='black')
    file=root/'mask.png';mask.save(file)
    spec={'image':{'file':file.name,'sha256':assets.sha256(file.read_bytes())},'target_node':'hair_back','space':'rig-canvas','channel':'luma','optional_target':not has_provider}
    cfg=p._load(root,'test.direct')['config'];layer=cfg['independent']['layers'][0]
    layer['provides']=['hair_back'] if has_provider else []
    layer['masks']={pose:spec for pose in order}
    config=root/'masked.json';config.write_bytes(w.json_bytes(cfg));p.configure(assembly,'test.direct',config)
    batch=produce(assembly,order,tmp_path)
    doc,base=parts_independent.read_reference(root,batch['reference'])
    assert all(job['mask']['image']['sha256']==spec['image']['sha256'] for job in doc['jobs'] if job['layer']=='back')
    p.check(assembly,'test.direct');p.build(assembly,'test.direct')
    root,index,registry,_=parts_motion._active(assembly);state=p._load(root,'test.direct')
    row=state['tracks']['main']['poses'][order[0]];record=p.candidate_record(root,state,order[0],row['selected']);base=root/row['selected']
    plan,sample,load=parts_candidate_scene.scene(root,index,registry,state['config'],order[0],Image.open(base/'part.png'),record['placement'],independent_layers=parts_independent.candidate_layers(record,base))
    pixel=compose_parts.render_sample(plan,sample,load,only_ids=['test.direct.back'])[0].getpixel((40,30))
    assert pixel[3]==(0 if has_provider else 255)
    actual=compose_parts.render_sample(plan,sample,load)[0]
    built,_=p._render_alone(root,index,registry,c.reference(registry['test.direct']),index['states'])
    assert np.array_equal(np.asarray(actual),np.asarray(built[order[0]]))
    built_piece=next(piece for piece in registry['test.direct']['bindings'][0]['pieces'] if piece['id']=='back')
    built_mask=built_piece['sample_map'][plan['clips'][0]['id']][sample['id']]['mask']
    assert built_mask['optional_target'] is (not has_provider)
    # Curation views must distinguish actual masking, raw zmap, and part only.
    from io import BytesIO
    desk=ReviewDesk(assembly)
    rendered=Image.open(BytesIO(desk.render('test.direct',order[0],'body'))).convert('RGBA')
    unmasked=Image.open(BytesIO(desk.render('test.direct',order[0],'body-zraw'))).convert('RGBA')
    visible=Image.open(BytesIO(desk.render('test.direct',order[0],'visible'))).convert('RGBA')
    at=(32*4,22*4)
    assert (rendered.getpixel(at)!=unmasked.getpixel(at)) is has_provider
    assert visible.getpixel(at)[3]==(0 if has_provider else 255)
    # Changing the authoring mask is a new candidate/review, not reinterpretation.
    mask.putpixel((40,30),(255,255,255,255));mask.save(file)
    for value in layer['masks'].values(): value['image']['sha256']=assets.sha256(file.read_bytes())
    config.write_bytes(w.json_bytes(cfg));p.configure(assembly,'test.direct',config)
    assert ReviewDesk(assembly).candidates('test.direct',order[0])['candidates'][0]['status']=='stale'
    with pytest.raises(c.PartsContractError,match='independent layers changed'):p.build(assembly,'test.direct')


def test_independent_replacement_uses_same_body_suppression_in_preview_and_build(tmp_path):
    from copy import deepcopy
    from sprite_gen.curate import parts_motion
    from sprite_gen.compose import compose_parts
    from sprite_gen.frames import parts_candidate_scene, parts_independent
    from sprite_gen.spec import parts_assets as assets

    assembly, order = setup(tmp_path)
    root, index, registry, _ = parts_motion._active(assembly)
    body = parts_rebind.body_spec(assembly, tmp_path / "segmented.json", body_id="test.body")
    spec = json.loads((tmp_path / "segmented.json").read_text())
    spec["body_mode"] = "segmented"
    topology = registry[registry[index["rig"]["id"]]["topology"]["id"]]
    spec["pieces"][0]["provides"] = [node["id"] for node in topology["nodes"] if node["required"] and node["id"] != "head"]
    head = deepcopy(spec["pieces"][0])
    head.update(id="head", node="head", provides=["head"])
    image = Image.new("RGBA", (128, 128))
    ImageDraw.Draw(image).rectangle((20, 10, 29, 19), fill="green")
    file = Path(body["source_root"]) / "head.png"
    image.save(file)
    for samples in head["sample_map"].values():
        for placement in samples.values():
            placement["image"] = {"file": file.name, "sha256": assets.sha256(file.read_bytes())}
    spec["pieces"].append(head)
    (tmp_path / "segmented.json").write_bytes(w.json_bytes(spec))
    parts_import.import_pack(assembly, tmp_path / "segmented.json", Path(body["source_root"]))
    state = p._load(root, "test.direct")
    cfg = state["config"]
    cfg["independent"]["layers"][1]["replaces"] = ["head"]
    path = tmp_path / "replace.json"
    path.write_bytes(w.json_bytes(cfg))
    p.configure(assembly, "test.direct", path)
    batch = produce(assembly, order, tmp_path)
    document, _ = parts_independent.read_reference(root, batch["reference"])
    assert all(job["replaces"] == (["head"] if job["layer"] == "front" else []) for job in document["jobs"])
    p.check(assembly, "test.direct")
    p.build(assembly, "test.direct")
    root, index, registry, _ = parts_motion._active(assembly)
    state = p._load(root, "test.direct")
    row = state["tracks"]["main"]["poses"][order[0]]
    record = p.candidate_record(root, state, order[0], row["selected"])
    part = Image.open(root / row["selected"] / record["images"]["part"]["file"]).convert("RGBA")
    plan, sample, load = parts_candidate_scene.scene(root, index, registry, state["config"], order[0], part,
        record["placement"], independent_layers=parts_independent.candidate_layers(record, root / row["selected"]))
    preview, _ = compose_parts.render_sample(plan, sample, load)
    built, built_plan = p._render_alone(root, index, registry, c.reference(registry["test.direct"]), index["states"])
    bare, bare_plan = p._render_alone(root, index, registry, None, index["states"])
    assert np.array_equal(np.asarray(preview), np.asarray(built[order[0]]))
    assert preview.getpixel((25, 15))[3] == 0 and bare[order[0]].getpixel((25, 15)) == (0, 128, 0, 255)
    assert "test.body.head" in sample["suppressed"]
    assert "test.body.head" not in bare_plan["clips"][0]["samples"][0]["suppressed"]
    assert not any(draw["placement"]["mask"] for draw in sample["draw"])
    pieces = registry["test.direct"]["bindings"][0]["pieces"]
    assert next(piece for piece in pieces if piece["id"] == "front")["replaces"] == ["head"]

    # A later config must not silently reinterpret an already frozen candidate.
    cfg["independent"]["layers"][1]["replaces"] = []
    path.write_bytes(w.json_bytes(cfg))
    p.configure(assembly, "test.direct", path)
    from sprite_gen.serve.serve_parts_review import ReviewDesk
    stale = ReviewDesk(assembly).candidates('test.direct', order[0])['candidates'][0]
    assert stale['status'] == 'stale' and stale['compatible'] is False
    with pytest.raises(c.PartsContractError, match="independent layers changed"):
        p.build(assembly, "test.direct")


def test_independent_replacement_refuses_a_flat_body_and_missing_nodes(tmp_path):
    assembly, _ = setup(tmp_path)
    cfg = p._load(assembly.parent, "test.direct")["config"]
    cfg["independent"]["layers"][0]["replaces"] = ["root"]
    path = tmp_path / "replace.json"
    path.write_bytes(w.json_bytes(cfg))
    with pytest.raises(c.PartsContractError, match="segmented body"):
        p.configure(assembly, "test.direct", path)
    cfg["independent"]["layers"][0]["replaces"] = ["head"]
    path.write_bytes(w.json_bytes(cfg))
    with pytest.raises(c.PartsContractError, match="replacement body nodes"):
        p.configure(assembly, "test.direct", path)


def test_authored_rigid_edge_rejects_a_bowed_sole_and_missing_art(tmp_path):
    from sprite_gen.frames import parts_independent
    from sprite_gen.spec import parts_assets as assets

    edge = {'id': 'sole', 'start': [10,40], 'end': [100,40], 'side': 'positive',
            'span': ['3/20','4/5'], 'max_deviation': '1'}
    for shape, expected in [('straight','pass'), ('bowed','fail'), ('missing','fail')]:
        image = Image.new('RGBA',(128,64))
        if shape != 'missing':
            for x in range(10,101):
                bottom = 40 if shape == 'straight' else 40+round(7*np.sin((x-10)*np.pi/90))
                ImageDraw.Draw(image).line((x,10,x,bottom),fill='navy')
        file = tmp_path / (shape+'.png')
        image.save(file)
        record = {'images': {'piece.shoe': {'file': file.name, 'sha256': assets.sha256(file.read_bytes())}},
                  'independent_layers': [{'id':'shoe','node':'shoe_front','image_key':'piece.shoe',
                                          'target':[10,10,101,50],'straight_edges':[edge]}]}
        metric = parts_independent.straight_edge_metrics(record,tmp_path)['shoe.sole']
        assert metric['status'] == expected
        if shape == 'missing':
            assert metric['max_deviation'] is None


def test_registered_canvas_import_preserves_curated_coordinates(tmp_path):
    assembly, order = setup(tmp_path)
    produce(assembly,order,tmp_path)
    batch = p.sheet(assembly,'test.direct',poses=[order[0]],layers=['front'])
    image = Image.new('RGBA',(128,128))
    ImageDraw.Draw(image).rectangle((44,31,53,40),fill='blue')
    file = tmp_path/'registered.png'
    image.save(file)
    p.slice_batch(assembly,'test.direct',batch=batch['batch'],pages=[(Path('1'),file)],registered_canvas=True)
    state = p._load(assembly.parent,'test.direct')
    row = state['tracks']['main']['poses'][order[0]]
    record = p.candidate_record(assembly.parent,state,order[0],row['selected'])
    actual = Image.open(assembly.parent/row['selected']/record['images']['piece.front']['file'])
    assert np.array_equal(np.asarray(actual),np.asarray(image))
    assert record['method']['registration']['front']['fit'] == 'native-canvas'
    for size,box in [((64,64),(44,31,53,40)),((128,128),(100,100,110,110))]:
        invalid = Image.new('RGBA',size)
        ImageDraw.Draw(invalid).rectangle(box,fill='blue')
        invalid.save(file)
        with pytest.raises(c.PartsContractError,match='native rig canvas|exceeds its authored target'):
            p.slice_batch(assembly,'test.direct',batch=batch['batch'],pages=[(Path('1'),file)],registered_canvas=True)
        assert p._load(assembly.parent,'test.direct')['tracks']['main']['poses'][order[0]]['selected'] == row['selected']


def test_next_generation_uses_selected_position_correction_without_forcing_unknown_shape(tmp_path):
    from sprite_gen.frames import parts_independent

    assembly,order = setup(tmp_path)
    produce(assembly,order,tmp_path)
    corrected = parts_part_edit.edit(assembly,'test.direct',order[0],[{'type':'translate','piece':'front','dx':2}])
    p.select_candidate(assembly,'test.direct',order[0],corrected['preview_candidate'])
    p.check(assembly,'test.direct')
    batch = p.sheet(assembly,'test.direct',poses=[order[0]],layers=['front'])
    doc,folder = parts_independent.read_reference(assembly.parent,batch['reference'])
    job = next(job for job in doc['jobs'] if job['layer']=='front')
    assert job['geometry_candidate'] == corrected['digest']
    assert any(ref.endswith('.selected-geometry.view.png') for ref in job['refs'])
    assert Image.open(assembly.parent/folder/job['guide']).size == Image.open(assembly.parent/folder/f"{order[0]}/front.selected-geometry.view.png").size
    # Model uncertainty must not promote an unverified viewing plane as a template.
    uncertain = json.loads(answer())
    uncertain.update(status='uncertain',summary='visible plane needs review')
    uncertain['checks']['shape'] = {'result':'unknown','reason':'the source foot projection is ambiguous'}
    parts_vision.inspect(assembly,'test.direct',order[0],transport=lambda *args,**kwargs:(json.dumps(uncertain),{'transport':'test-double'}))
    batch = p.sheet(assembly,'test.direct',poses=[order[0]],layers=['front'])
    doc,_ = parts_independent.read_reference(assembly.parent,batch['reference'])
    job = next(job for job in doc['jobs'] if job['layer']=='front')
    assert job['geometry_candidate'] == corrected['digest'] and job['shape_review']
    assert not any('.selected-geometry.view.png' in ref or '.shape-' in ref for ref in job['refs'])


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
    p.check(assembly,'test.direct')
    p.build(assembly,'test.direct')
    for target in order:
        parts_vision.inspect(assembly,'test.direct',target,
            transport=lambda *args, **kwargs: (answer(), {"transport": "test-double"}))
    p.review(assembly,'test.direct',approve=['all'],reviewer='tester')
    from sprite_gen.curate import parts_motion

    _, index, registry, _ = parts_motion._active(assembly)
    appearance = c.seal({'kind':'parts-appearance','schema_version':1,'id':'test.appearance',
        **{key:index[key] for key in ('rig','motion','catalog','body','states')},
        'selections':{'hair':c.reference(registry['test.direct'])}})
    assert p.release_reviews(root,registry,appearance,index=index)
    assert parts_vision.current(root, index, p._load(root, "test.direct"), pose)[
        "current"
    ]
    Image.new("RGBA", (128, 128), (120, 30, 40, 255)).save(guide)
    assert not parts_vision.current(root, index, p._load(root, "test.direct"), pose)[
        "current"
    ]
    assert p.status(assembly,'test.direct')['productions'][0]['approved'] == 0
    assert p._load(root,'test.direct')['tracks']['main']['poses'][pose]['decision'] == 'approved'
    from sprite_gen.serve.serve_parts_review import ReviewDesk
    assert ReviewDesk(assembly).part('test.direct')['poses'][0]['decision'] == 'unverified'
    with pytest.raises(c.PartsContractError,match='current approved production'):
        p.release_reviews(root,registry,appearance,index=index)
    from sprite_gen.frames import parts_independent

    # Original generation inputs remain immutable despite changing the source guide.
    parts_independent.read_reference(root, batch["reference"])


def test_completed_rework_is_not_reissued_and_pose_criteria_invalidate_votes(tmp_path):
    assembly,order = setup(tmp_path)
    produce(assembly,order,tmp_path)
    p.request_rework(assembly,'test.direct',[order[0]],'move this failed attachment')
    batch = produce(assembly,[order[0]],tmp_path)
    from sprite_gen.frames import parts_independent
    doc,folder = parts_independent.read_reference(assembly.parent,batch['reference'])
    assert 'move this failed attachment' in (assembly.parent/folder/doc['jobs'][0]['prompt']).read_text()
    done = p.sheet(assembly,'test.direct',poses=[order[0]])
    doc,folder = parts_independent.read_reference(assembly.parent,done['reference'])
    assert 'move this failed attachment' not in (assembly.parent/folder/doc['jobs'][0]['prompt']).read_text()
    p.check(assembly,'test.direct')
    p.build(assembly,'test.direct')
    parts_vision.inspect(assembly,'test.direct',order[0],transport=lambda *a,**k:(answer(),{'transport':'test-double'}))
    p.review(assembly,'test.direct',approve=[order[0]],reviewer='tester')
    cfg = p._load(assembly.parent,'test.direct')['config']
    Image.new('L',(128,128)).save(assembly.parent/'new-allowed.png')
    cfg['pose_checks'] = {order[0]:{'allowed':'new-allowed.png'}}
    file = tmp_path/'new-criteria.json'
    file.write_bytes(w.json_bytes(cfg))
    p.configure(assembly,'test.direct',file)
    assert p._load(assembly.parent,'test.direct')['tracks']['main']['poses'][order[0]]['decision'] == 'unverified'


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
