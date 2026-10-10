# SPDX-License-Identifier: Apache-2.0
import base64
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from io import BytesIO
import json
from pathlib import Path
import threading
from urllib.error import HTTPError
from urllib.request import urlopen

from PIL import Image
import pytest

from compose.test_parts_pipeline import populated_workspace
from parts_helpers import part_spec, tree_hashes
from sprite_gen.compose import compose_parts, parts_build
from sprite_gen.curate import parts_appearance
from sprite_gen.frames import parts_import
from sprite_gen.serve import serve_parts
from sprite_gen.spec import parts_contract as contract
from sprite_gen.spec import parts_workspace as workspace


@pytest.fixture
def session(tmp_path):
    assembly, selection, source = populated_workspace(tmp_path,states=("idle","walk","run"))
    for identity,slot,color in [("hair.a","hair","red"),("hair.b","hair","blue"),("hat","headwear","orange")]:
        Image.new("RGBA",(5,5),color).save(source/"piece.png")
        spec=part_spec(tmp_path/f"{identity}.json",assembly,source,identity=identity,slot=slot,
            node="head" if slot=="headwear" else "hair_front",socket="head")
        parts_import.import_pack(assembly,spec,source)
    parts_appearance.create(assembly,selection)
    return serve_parts.PartsSession(assembly), assembly


def test_http_swaps_do_not_write_or_bake_and_preserve_exact_sample_time(session,monkeypatch):
    player,assembly=session
    before=tree_hashes(assembly.parent)
    def forbidden(*args,**kwargs):
        pytest.fail("live HTTP player attempted to write or bake")
    monkeypatch.setattr(Path,"write_bytes",forbidden)
    monkeypatch.setattr(Path,"write_text",forbidden)
    monkeypatch.setattr(compose_parts,"render_plan",forbidden)
    monkeypatch.setattr(parts_build,"build",forbidden)
    frames=[player.frame({"state":["run"],"time_ms":["300"],"hair":[choice]}) for choice in ("hair.a","hair.b")]
    assert {f["sample_id"] for f in frames}=={"sample.1"}
    assert {f["sample_tick"] for f in frames}=={"6000"}
    assert {f["elapsed_seconds"] for f in frames}=={"3/10"}
    assert len({f["evidence"]["rgba_sha256"] for f in frames})==2
    assert tree_hashes(assembly.parent)==before
    assert not (assembly.parent/"previews").exists() and not (assembly.parent/"builds").exists()
    image=Image.open(BytesIO(base64.b64decode(frames[0]["image"].split(",",1)[1])))
    assert image.size==(128,128)


def test_optional_overlay_can_be_removed_without_resetting_or_masking_head(session):
    player,_=session
    slot=next(s for s in player.metadata["slots"] if s["id"]=="headwear")
    assert slot["options"][0]=={"id":"","label":"없음"}
    common={"state":["walk"],"time_ms":["375"]}
    bare=player.frame({**common,"headwear":[""]})
    hat=player.frame({**common,"headwear":["hat"]})
    again=player.frame({**common,"headwear":[""]})
    assert bare==again and bare["sample_id"]==hat["sample_id"]=="sample.2"
    assert len(hat["evidence"]["draw"])==len(bare["evidence"]["draw"])+1


def test_http_reload_reads_the_new_outfit_without_restarting_the_player(session,tmp_path):
    player,assembly=session
    server=ThreadingHTTPServer(('127.0.0.1',0),serve_parts.handler_for(player))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    try:
        old=json.loads(urlopen(base+'/api/metadata').read())
        index,registry=workspace.read_workspace(assembly)
        spec=tmp_path/'updated-outfit.json'
        spec.write_bytes(workspace.json_bytes({'kind':'parts-appearance-selection','schema_version':1,'id':index['appearance']['id'],'selections':{'hair':contract.reference(registry['hair.a'])}}))
        parts_appearance.create(assembly,spec)
        before=tree_hashes(assembly.parent)
        new=json.loads(urlopen(base+'/api/metadata').read())
        assert new!=old and new['motion']==old['motion']
        assert next(s for s in new['slots'] if s['id']=='hair')['selected']=='hair.a'
        assert tree_hashes(assembly.parent)==before
    finally:
        server.shutdown();server.server_close();thread.join()


def test_layer_api_keeps_separate_textures_and_tweens_without_cpu_composition(session,monkeypatch):
    player,assembly=session
    before=tree_hashes(assembly.parent)
    def forbidden(*args,**kwargs):
        pytest.fail("independent layers attempted CPU composition, file writes or baking")
    monkeypatch.setattr(compose_parts,"render_sample",forbidden)
    monkeypatch.setattr(compose_parts,"render_plan",forbidden)
    monkeypatch.setattr(parts_build,"build",forbidden)
    monkeypatch.setattr(Path,"write_bytes",forbidden)
    monkeypatch.setattr(Path,"write_text",forbidden)
    common={"state":["run"],"time_ms":["300"]}
    first=player.layer_frame({**common,"hair":["hair.a"]})
    changed=player.layer_frame({**common,"hair":["hair.b"]})
    assert player.metadata["mode"]=="independent-layers"
    assert first["sample_tick"]==changed["sample_tick"]=="6000"
    assert first["sample_fraction"]==changed["sample_fraction"]=="2/5"
    assert first["motion"]==changed["motion"]
    assert "image" not in first and "evidence" not in first
    a=next(layer for layer in first["layers"] if layer["slot"]=="hair")
    b=next(layer for layer in changed["layers"] if layer["slot"]=="hair")
    assert a["id"]==b["id"] and a["texture"]["id"]!=b["texture"]["id"]
    assert a["position"]==b["position"] and a["mode"]==b["mode"]=="held"
    body_a={layer["id"]:layer["texture"] for layer in first["layers"] if layer["slot"] is None}
    body_b={layer["id"]:layer["texture"] for layer in changed["layers"] if layer["slot"] is None}
    assert body_a==body_b
    cached=player.texture(a["texture"]["id"])
    assert player.texture(a["texture"]["id"]) is cached
    assert Image.open(BytesIO(cached)).size==tuple(a["texture"]["size"])
    assert tree_hashes(assembly.parent)==before


def test_frame_interpolation_holds_attachments_until_body_pose_changes(session):
    _, assembly = session
    source = assembly.parent.parent / "body-source"
    spec = part_spec(assembly.parent.parent / "moving-hair.json", assembly, source,
                     identity="hair.a", slot="hair", node="hair_front", socket="head")
    request = json.loads(spec.read_bytes())
    for mapping in request["pieces"][0]["sample_map"].values():
        mapping["sample.2"]["offset"] = [4, 0]
    spec.write_text(json.dumps(request))
    parts_import.import_pack(assembly, spec, source)
    parts_appearance.create(assembly, assembly.parent.parent / "selection.json")
    player = serve_parts.PartsSession(assembly, interpolation="frame")
    common = {"state": ["run"], "hair": ["hair.a"]}
    first = player.layer_frame({**common, "time_ms": ["250"]})
    middle = player.layer_frame({**common, "time_ms": ["300"]})
    next_pose = player.layer_frame({**common, "time_ms": ["375"]})
    assert player.metadata["interpolation"] == "frame"
    assert middle["elapsed_seconds"] == "3/10" and middle["sample_tick"] == "6000"
    assert first["sample_id"] == middle["sample_id"] == "sample.1"
    assert next_pose["sample_id"] == "sample.2"
    assert first["layers"] == middle["layers"]
    assert middle["sample_fraction"] == "0" and not any(l["tweened"] for l in middle["layers"])
    hair = lambda scene: next(l for l in scene["layers"] if l["slot"] == "hair")
    assert hair(middle)["position"] != hair(next_pose)["position"]
    linear = serve_parts.PartsSession(assembly).layer_frame({**common, "time_ms": ["300"]})
    assert hair(linear)["position"] != hair(middle)["position"]
    changed = player.layer_frame({"state": ["run"], "time_ms": ["300"], "hair": ["hair.b"]})
    assert hair(changed)["position"] == hair(middle)["position"]
    assert changed["sample_tick"] == middle["sample_tick"]
    assert hair(changed)["texture"] != hair(middle)["texture"]


def test_interpolation_cli_defaults_and_validation(session):
    import argparse
    _, assembly = session
    parser = argparse.ArgumentParser()
    serve_parts.add_arguments(parser)
    assert parser.parse_args(["--assembly", str(assembly)]).interpolation == "linear"
    assert parser.parse_args(["--assembly", str(assembly), "--interpolation", "frame"]).interpolation == "frame"
    with pytest.raises(contract.PartsContractError):
        serve_parts.PartsSession(assembly, interpolation="unknown")


def test_player_accepts_a_catalog_with_unused_optional_slots_omitted(tmp_path):
    from copy import deepcopy
    assembly, selection, _ = populated_workspace(tmp_path)
    index, registry = workspace.read_workspace(assembly)
    catalog = deepcopy(registry[index["catalog"]["id"]])
    catalog.pop("digest")
    catalog["slots"] = [slot for slot in catalog["slots"] if slot["id"] == "hair"]
    catalog["presets"] = []
    catalog = contract.seal(catalog)
    (assembly.parent / index["documents"][catalog["id"]]).write_bytes(workspace.json_bytes(catalog))
    index["catalog"] = contract.reference(catalog)
    assembly.write_bytes(workspace.json_bytes(index))
    parts_appearance.create(assembly, selection)
    workspace.read_workspace(assembly, complete=True)
    player = serve_parts.PartsSession(assembly)
    assert player.frame({"time_ms": ["300"]})["sample_id"] == "sample.1"


def test_parallel_viewers_keep_state_and_time_isolated(session):
    player,_=session
    requests=[{"state":[state],"time_ms":[str(time)]} for _ in range(3)
        for state in ("idle","walk","run") for time in (0,250,375)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        frames=list(pool.map(player.frame,requests))
    for query,frame in zip(requests,frames):
        assert frame["state"]==query["state"][0]
        assert frame["sample_id"]=={"0":"sample.0","250":"sample.1","375":"sample.2"}[query["time_ms"][0]]


@pytest.mark.parametrize("query",[
    {"time_ms":["-1"]},{"time_ms":["0.5"]},{"time_ms":["1"*17]},
    {"time_ms":["0","1"]},{"path":["../secret"]},{"state":["missing"]},
    {"hair":["../../image.png"]},{"hair":["hat"]},
])
def test_invalid_request_cannot_break_next_valid_frame(session,query):
    player,_=session
    before=player.frame({"time_ms":["300"]})
    with pytest.raises(contract.PartsContractError):
        player.frame(query)
    assert player.frame({"time_ms":["300"]})==before


def test_http_routes_return_frames_errors_and_packaged_view_without_writes(session):
    player,assembly=session
    before=tree_hashes(assembly.parent)
    server=ThreadingHTTPServer(("127.0.0.1",0),serve_parts.handler_for(player))
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    url=f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(url+"/api/frame?state=walk&time_ms=300&hair=hair.b") as response:
            assert response.headers["Cache-Control"]=="no-store"
            assert json.load(response)["sample_id"]=="sample.1"
        with urlopen(url+"/") as response:
            assert b"Parts Lab" in response.read()
        with urlopen(url+"/api/layers?state=walk&time_ms=300&hair=hair.b") as response:
            scene=json.load(response)
            assert scene["sample_id"]=="sample.1" and "image" not in scene
        reference=scene["layers"][0]["texture"]
        with urlopen(url+reference["url"]) as response:
            assert response.headers["Content-Type"]=="image/png"
            assert "immutable" in response.headers["Cache-Control"]
            assert Image.open(BytesIO(response.read())).size==tuple(reference["size"])
        with pytest.raises(HTTPError) as missing:
            urlopen(url+"/api/textures/../../assembly.json.png")
        assert missing.value.code==400
        with pytest.raises(HTTPError) as invalid:
            urlopen(url+"/api/frame?time_ms=0&time_ms=1")
        assert invalid.value.code==400
        with pytest.raises(HTTPError) as outside:
            urlopen(url+"/../assembly.json")
        assert outside.value.code==404
        # This segmented fixture uses slot precedence, so the browser must
        # keep its existing server-layer fallback instead of flattening it.
        with pytest.raises(HTTPError) as unsupported:
            urlopen(url+"/api/library")
        assert unsupported.value.code==400
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)
    assert tree_hashes(assembly.parent)==before


def test_http_portable_layers_and_download_match_without_workspace_writes(tmp_path):
    import zipfile
    from compose.test_export_parts import flat_workspace
    assembly = flat_workspace(tmp_path)
    before = tree_hashes(assembly.parent)
    player = serve_parts.PartsSession(assembly, interpolation="frame", title="Generic character")
    server = ThreadingHTTPServer(("127.0.0.1", 0), serve_parts.handler_for(player))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(url + "/api/library") as response:
            bundle = json.load(response)
        manifest = bundle["manifest"]
        assert manifest["title"] == "Generic character"
        assert manifest["appearance_clips_baked"] == 0
        assert len(manifest["clips"]) == 2 and len(manifest["textures"]) == 3
        with urlopen(url + "/standalone") as response:
            html = response.read()
        assert b'id="parts-data"' in html and b'data:image/png;base64,' in html
        with urlopen(url + "/api/export") as response:
            assert response.headers["Content-Type"] == "application/zip"
            assert 'filename="parts-layers.zip"' in response.headers["Content-Disposition"]
            archive = response.read()
        with urlopen(url + "/api/export") as response:
            assert response.read() == archive
        with zipfile.ZipFile(BytesIO(archive)) as zipped:
            assert json.loads(zipped.read("manifest.json")) == manifest
            assert zipped.read("preview.html") == html
            for ref in manifest["textures"].values():
                from sprite_gen.spec import parts_assets
                assert parts_assets.sha256(zipped.read(ref["file"])) == ref["sha256"]
            assert set(zipped.namelist()) == {"manifest.json", "preview.html", *[ref["file"] for ref in manifest["textures"].values()]}
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)
    assert tree_hashes(assembly.parent) == before
