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
            assert b"SD Explorer" in response.read()
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
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)
    assert tree_hashes(assembly.parent)==before
