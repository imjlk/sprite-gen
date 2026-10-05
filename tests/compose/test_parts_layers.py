# SPDX-License-Identifier: Apache-2.0
"""Draw table (zmap), cover rules (vslot) and the optional fields that carry them."""
from copy import deepcopy

import pytest

from parts_helpers import composition_fixture
from sprite_gen.compose import parts_resolve
from sprite_gen.spec import parts_contract as contract
from test_parts_compose import mutate


def body_order(docs):
    plan = parts_resolve.resolve(docs[-1], docs)
    return [entry["node"] for entry in plan["clips"][0]["samples"][0]["draw"]]


def test_optional_fields_leave_older_documents_and_digests_unchanged():
    docs, _ = composition_fixture()
    topology, body = docs[0], docs[3]
    assert "layers" not in topology
    assert all("pose_map" not in p and "cover_rule" not in p for p in body["bindings"][0]["pieces"])
    assert "covers" not in body["bindings"][0]
    assert contract.parse(deepcopy(topology))["digest"] == topology["digest"]
    with pytest.raises(contract.PartsContractError, match="layers"):
        contract.seal({**{k: v for k, v in topology.items() if k != "digest"}, "layers": ["root", "nope"]})


def test_draw_table_orders_open_entries_and_explicit_constraints_still_win():
    docs, _ = composition_fixture()
    nodes = [n["id"] for n in docs[0]["nodes"]]
    assert body_order(docs) == sorted(nodes)  # no table: IDs alone
    table = list(reversed(nodes))
    mutate(docs, docs[0]["id"], lambda d: d.update(layers=table))
    assert body_order(docs) == table
    # An explicit constraint outranks the table: the root raster drawn after the head.
    def after_head(pack):
        piece = next(p for p in pack["bindings"][0]["pieces"] if p["id"] == "root")
        for mapping in piece["sample_map"].values():
            for placement in mapping.values():
                placement["after"] = ["head"]
    mutate(docs, "fixture.body", after_head)
    order = body_order(docs)
    assert order.index("root") > order.index("head")
    assert [n for n in order if n != "root"] == [n for n in table if n != "root"]


def test_unlisted_nodes_draw_after_the_table():
    docs, _ = composition_fixture()
    mutate(docs, docs[0]["id"], lambda d: d.update(layers=["torso", "root"]))
    order = body_order(docs)
    assert order[:2] == ["torso", "root"] and order[2:] == sorted(order[2:])


def test_cover_rule_shows_one_piece_per_covering_state():
    docs, _ = composition_fixture()
    def covered_variants(pack):
        piece = next(p for p in pack["bindings"][0]["pieces"] if p["id"] == "hair_front")
        capped = {**deepcopy(piece), "id": "hair_capped", "cover_rule": {"tag": "hat", "show": "covered"}}
        piece["cover_rule"] = {"tag": "hat", "show": "uncovered"}
        pack["bindings"][0]["pieces"].append(capped)
    mutate(docs, "fixture.body", covered_variants)
    plan = parts_resolve.resolve(docs[-1], docs)
    sample = plan["clips"][0]["samples"][0]
    ids = [e["id"] for e in sample["draw"]]
    assert "fixture.body.hair_front" in ids and "fixture.body.hair_capped" not in ids
    assert sample["suppressed"]["fixture.body.hair_capped"] == "cover:hat" and "covers" not in plan
    mutate(docs, "fixture.body", lambda pack: pack["bindings"][0].update(covers=["hat"]))
    plan = parts_resolve.resolve(docs[-1], docs)
    sample = plan["clips"][0]["samples"][0]
    ids = [e["id"] for e in sample["draw"]]
    assert "fixture.body.hair_capped" in ids and "fixture.body.hair_front" not in ids
    assert plan["covers"] == {"hat": ["fixture.body"]}


def test_pose_map_must_be_expanded_to_every_sample_of_its_pose():
    docs, _ = composition_fixture()
    motion = docs[2]
    def posed(value):
        for clip in value["clips"]:
            for sample in clip["samples"]:
                sample["pose_id"] = "stand" if sample["id"] != "sample.1" else "step"
    mutate(docs, motion["id"], posed)
    def draw_by_pose(pack):
        piece = pack["bindings"][0]["pieces"][0]
        template = next(iter(next(iter(piece["sample_map"].values())).values()))
        piece["pose_map"] = {"stand": deepcopy(template)}
    mutate(docs, "fixture.body", draw_by_pose)
    contract.validate_bundle(docs)  # every sample is in sample_map already
    def drop_one(pack):
        piece = pack["bindings"][0]["pieces"][0]
        del next(iter(piece["sample_map"].values()))["sample.0"]
    with pytest.raises(contract.PartsContractError, match="not expanded"):
        mutate(docs, "fixture.body", drop_one)
        contract.validate_bundle(docs)
    def unknown_pose(pack):
        pack["bindings"][0]["pieces"][0]["pose_map"]["jump"] = deepcopy(pack["bindings"][0]["pieces"][0]["pose_map"]["stand"])
    docs, _ = composition_fixture()
    mutate(docs, docs[2]["id"], posed)
    mutate(docs, "fixture.body", draw_by_pose)
    mutate(docs, "fixture.body", unknown_pose)
    with pytest.raises(contract.PartsContractError, match="pose_map"):
        contract.validate_bundle(docs)
