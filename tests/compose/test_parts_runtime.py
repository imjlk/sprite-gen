# SPDX-License-Identifier: Apache-2.0
from copy import deepcopy
from fractions import Fraction
from itertools import product
from pathlib import Path

import pytest

from compose.test_parts_compose import add_part, mutate
from compose.test_parts_pipeline import populated_workspace
from parts_helpers import composition_fixture, frame, tree_hashes
from sprite_gen.compose import compose_parts, parts_resolve
from sprite_gen.compose.parts_runtime import PartsPlayer
from sprite_gen.curate import parts_appearance
from sprite_gen.spec import parts_assets as assets, parts_contract as contract


def player_fixture():
    docs, images = composition_fixture()
    choices = {}
    for slot, node in [("hair", "hair_front"), ("expression", "head"), ("upper", "torso"), ("lower", "leg_l"), ("footwear", "foot_l")]:
        choices[slot] = [add_part(docs, images, f"{slot}.{v}", slot, node, color=color)
                         for v, color in enumerate(["red", "blue"])]
    player = PartsPlayer(docs[-1], docs, lambda pack, ref: images[(pack["id"], ref["file"])].copy())
    refs = {doc["id"]: contract.reference(doc) for doc in docs}
    return player, choices, refs


def test_32_appearances_use_three_shared_clocks_without_baking_any_completed_clip(monkeypatch):
    player, choices, refs = player_fixture()
    def forbidden(*a, **k): pytest.fail("runtime attempted a clip bake or file write")
    monkeypatch.setattr(compose_parts, "render_plan", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    for state in ("idle", "walk", "run"):
        player.set_state(state)
        player.seek(Fraction(3, 10))
        before = player.current()[2]
        hashes = set()
        for bits in product([0, 1], repeat=5):
            player.set_selection({slot: refs[names[bit]] for (slot, names), bit in zip(choices.items(), bits)})
            image, frame = player.frame()
            assert player.current()[2] == before
            assert frame["sample_id"] == "sample.1" and frame["sample_tick"] == "50"
            assert frame["motion"] == player.appearance["motion"]
            hashes.add(frame["evidence"]["rgba_sha256"])
        assert len(hashes) == 32


def test_repeat_pixels_keep_sample_identity_and_variable_duration_boundaries():
    docs, images = composition_fixture()
    player = PartsPlayer(docs[-1], docs, lambda p, a: images[(p["id"], a["file"])] )
    cases = [(Fraction(0), "sample.0"), (Fraction(1, 4), "sample.1"),
             (Fraction(3, 8), "sample.2"), (Fraction(5, 8), "sample.0")]
    for time, sample in cases:
        player.seek(time)
        assert player.current()[2]["sample_id"] == sample
    player.seek(Fraction(1, 8))
    first = player.frame()[1]["evidence"]["rgba_sha256"]
    player.seek(Fraction(1, 2))
    assert player.frame()[1]["evidence"]["rgba_sha256"] == first


def test_rejected_swap_leaves_clock_selection_and_pixels_unchanged():
    player, choices, refs = player_fixture()
    player.set_state("run")
    player.seek(Fraction(1, 3))
    image, before = player.frame()
    with pytest.raises(contract.PartsContractError):
        player.set_selection({"hair": {"id": "missing", "digest": "a" * 64}})
    after_image, after = player.frame()
    assert after == before and after_image.tobytes() == image.tobytes()
    incompatible = {k: deepcopy(v) for k, v in player.appearance.items() if k != "digest"}
    incompatible["motion"] = {"id": "different", "digest": "b" * 64}
    with pytest.raises(contract.PartsContractError, match="same rig"):
        player.set_appearance(contract.seal(incompatible))
    assert player.frame()[1] == before


def test_nonloop_clamps_and_bad_time_is_rejected():
    docs, images = composition_fixture("blob.fixture.v1", states=("pulse",))
    player = PartsPlayer(docs[-1], docs, lambda p, a: images[(p["id"], a["file"])])
    player.seek(50)
    assert player.current()[2]["sample_id"] == "sample.2"
    for time in (True, -1, 0.5, float("inf")):
        with pytest.raises(contract.PartsContractError): player.seek(time)
        with pytest.raises(contract.PartsContractError): player.advance(time)


def test_live_workspace_playback_writes_no_assets_appearances_previews_or_builds(tmp_path):
    assembly, selection, _ = populated_workspace(tmp_path, states=("idle", "walk", "run"))
    parts_appearance.create(assembly, selection)
    before = tree_hashes(assembly.parent)
    player = PartsPlayer.from_workspace(assembly, state="walk")
    player.advance(Fraction(3, 10))
    player.frame()
    player.set_selection({}, palette={"skin": [180, 140, 110]})
    player.frame()
    assert tree_hashes(assembly.parent) == before
    assert not (assembly.parent / "previews").exists()
    assert not (assembly.parent / "builds").exists()


def test_shared_head_attachments_order_after_head_without_ordering_each_other():
    docs, images = composition_fixture()
    for identity, slot in [("cap", "headwear"), ("glasses", "eyewear")]:
        add_part(docs, images, identity, slot, "head", provides=[], replaces=[])
        mutate(docs, identity, lambda d: [p.update(after=["head"]) for m in d["bindings"][0]["pieces"][0]["sample_map"].values() for p in m.values()])
    plan = parts_resolve.resolve(docs[-1], docs)
    names = [e["id"] for e in plan["clips"][0]["samples"][0]["draw"]]
    assert names.index("fixture.body.head") < names.index("cap.part")
    assert names.index("fixture.body.head") < names.index("glasses.part")


def test_held_layers_keep_textures_and_interpolate_shared_relative_anchors_without_rendering(monkeypatch):
    docs, images = composition_fixture()
    for name,color in (("hair.a","red"),("hair.b","blue")):
        add_part(docs,images,name,"hair","hair_front",color=color)
    def anchors(document):
        for clip in document["clips"]:
            for sample,position in zip(clip["samples"],([64,40],[72,44],[64,40])):
                sample["sockets"]["head"]["position"] = position
    mutate(docs,docs[2]["id"],anchors)
    def forbidden(*args,**kwargs):
        pytest.fail("layer sampling decoded, composited, baked or wrote an image")
    player=PartsPlayer(docs[-1],docs,forbidden)
    monkeypatch.setattr(compose_parts,"render_sample",forbidden)
    monkeypatch.setattr(compose_parts,"render_plan",forbidden)
    monkeypatch.setattr(Path,"write_bytes",forbidden)
    monkeypatch.setattr(Path,"write_text",forbidden)
    player.set_state("walk")
    player.seek(Fraction(1,8))
    scene=player.layers()
    hair=next(layer for layer in scene["layers"] if layer["slot"]=="hair")
    assert scene["sample_fraction"]=="1/2"
    assert hair["mode"]=="held" and hair["anchor"]==["68","42"]
    assert hair["attach_to"]=="head"
    assert [Fraction(v) for v in hair["position"]]==[
        Fraction(a)+Fraction(b) for a,b in zip(hair["anchor"],hair["local_origin"])]
    assert "image" not in scene and "rgba_sha256" not in scene
    snapped=next(layer for layer in player.layers(interpolate=False)["layers"] if layer["slot"]=="hair")
    assert snapped["anchor"]==["64","40"]
    refs={d["id"]:contract.reference(d) for d in docs}
    player.set_selection({"hair":refs["hair.a"]})
    changed=player.layers()
    new_hair=next(layer for layer in changed["layers"] if layer["slot"]=="hair")
    assert new_hair["id"]==hair["id"] and new_hair["image"]!=hair["image"]
    assert new_hair["anchor"]==hair["anchor"] and new_hair["position"]==hair["position"]
    assert changed["motion"]==scene["motion"] and changed["elapsed_seconds"]==scene["elapsed_seconds"]


@pytest.mark.parametrize("preset",["human.sd.v1","human.tall.v1","canine.medium.v1","blob.fixture.v1","asymmetric.six-limb.v1"])
def test_independent_layer_sampling_keeps_all_body_profiles_and_wraps_one_clock(preset):
    docs,_=composition_fixture(preset)
    player=PartsPlayer(docs[-1],docs,lambda *args:pytest.fail("layers need no image loader"))
    player.seek(Fraction(1,2))
    scene=player.layers()
    assert scene["sample_id"]=="sample.2" and scene["next_sample_id"]=="sample.0"
    assert scene["sample_fraction"]=="1/2"
    assert len({layer["id"] for layer in scene["layers"]})==len(scene["layers"])
    assert [layer["order"] for layer in scene["layers"]]==list(range(len(scene["layers"])))
    assert all(layer["attach_to"] in player.current()[1]["sockets"] for layer in scene["layers"])


def test_layer_sampling_nonloop_holds_last_pose_and_visibility_is_not_faded():
    docs,_=composition_fixture("blob.fixture.v1",states=("pulse",))
    player=PartsPlayer(docs[-1],docs,lambda *args:pytest.fail("layers need no renderer"))
    player.seek(50)
    scene=player.layers()
    assert scene["sample_id"]==scene["next_sample_id"]=="sample.2"
    assert all(layer["anchor"]==[str(v) for v in player.current()[1]["sockets"][layer["attach_to"]]["position"]] for layer in scene["layers"])
    with pytest.raises(contract.PartsContractError,match="must be boolean"):
        player.layers(interpolate="yes")


def test_layer_scene_is_detached_from_the_compiled_plan():
    docs,_=composition_fixture()
    player=PartsPlayer(docs[-1],docs,lambda *args:pytest.fail("layers need no renderer"))
    before=player.layers()
    scene=player.layers()
    scene["layers"][0]["image"]["sha256"]="a"*64
    scene["layers"][0]["provides"].clear()
    scene["hidden"].append({"id":"corrupted"})
    assert player.layers()==before


def test_whole_base_body_remains_one_unchanged_layer_when_eye_hair_and_clothes_toggle():
    docs,images=composition_fixture()
    whole=frame();whole_ref={'file':'whole-base.png','sha256':assets.sha256(assets.png_bytes(whole))}
    mutate(docs,docs[0]['id'],lambda d:[s.update(optional=True) for s in d['slots']])
    def flat(document):
        document['body_mode']='flat'
        root=document['bindings'][0]['pieces'][0]
        document['bindings'][0]['pieces']=[root]
        for clip in docs[2]['clips']:
            for sample in clip['samples']:
                placement=root['sample_map'][clip['id']][sample['id']]
                placement.update(image=whole_ref,pivot=sample['sockets']['root']['position'][:],offset=[0,0])
    mutate(docs,docs[3]['id'],flat)
    body_ref=docs[3]['bindings'][0]['pieces'][0]['sample_map']['idle.right']['sample.0']['image']
    images[('fixture.body',body_ref['file'])]=whole
    for name,slot,node in [('eyes','eyes','eye_r'),('hair','hair','hair_front'),('shirt','upper','torso')]:
        add_part(docs,images,name,slot,node,provides=[],replaces=[])
        mutate(docs,name,lambda d:[p.update(after=['root']) for m in d['bindings'][0]['pieces'][0]['sample_map'].values() for p in m.values()])
    refs={d['id']:contract.reference(d) for d in docs}
    player=PartsPlayer(docs[-1],docs,lambda p,a:images[(p['id'],a['file'])].copy())
    player.seek(Fraction(1,8));before=player.current()[2];pixels=whole.tobytes()
    for bits in product((False,True),repeat=3):
        player.set_selection({s:refs[n] if enabled else None for s,n,enabled in zip(('eyes','hair','upper'),('eyes','hair','shirt'),bits)})
        scene=player.layers();base=next(l for l in scene['layers'] if l['slot'] is None)
        assert len([l for l in scene['layers'] if l['slot'] is None])==1
        assert base['image']==body_ref and base['position']==['0','0']
        assert len(scene['layers'])==1+sum(bits)
        assert player.current()[2]==before
        assert images[('fixture.body',body_ref['file'])].tobytes()==pixels
