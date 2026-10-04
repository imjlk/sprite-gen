# SPDX-License-Identifier: Apache-2.0
from copy import deepcopy
from fractions import Fraction
from itertools import product
from pathlib import Path

import pytest

from compose.test_parts_compose import add_part, mutate
from compose.test_parts_pipeline import populated_workspace
from parts_helpers import composition_fixture, tree_hashes
from sprite_gen.compose import compose_parts, parts_resolve
from sprite_gen.compose.parts_runtime import PartsPlayer
from sprite_gen.curate import parts_appearance
from sprite_gen.spec import parts_contract as contract


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
