# SPDX-License-Identifier: Apache-2.0
"""The opt-in parts contract is generic, strict and filesystem-free."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from sprite_gen.spec import parts_contract as parts


def declarations():
    topology = parts.seal({
        "kind": "parts-topology", "schema_version": 1, "id": "test.topology",
        "nodes": [{"id": "root"}, {"id": "head", "parent": "root"}],
        "sockets": [{"id": "face", "node": "head", "required": True}],
        "slots": [{"id": "expression", "nodes": ["head"], "sockets": ["face"]}],
    })
    rig = parts.seal({
        "kind": "parts-rig", "schema_version": 1, "id": "test.rig",
        "topology": parts.reference(topology), "view": "right", "style": "pixel.v1",
        "canvas": {"width": 128, "height": 128},
    })
    motion = parts.seal({
        "kind": "parts-motion", "schema_version": 1, "id": "test.motion",
        "rig": parts.reference(rig), "clips": [{
            "id": "walk.right", "state": "walk", "view": "right", "timebase": 120000,
            "loop": True, "samples": [{
                "id": f"sample.{i}", "image": {"file": "frames/repeated.png", "sha256": "a" * 64},
                "duration_ticks": duration, "sockets": {
                    "face": {"position": [64, 40], "confidence": "verified", "visible": True}},
            } for i, duration in enumerate([5000, 4000, 5000])],
        }],
    })
    pack = parts.seal({
        "kind": "parts-pack", "schema_version": 1, "id": "face.smile",
        "design_id": "smile", "bindings": [{
            "id": "smile.right", "rig": parts.reference(rig), "motion": parts.reference(motion),
            "view": "right", "occupies": ["expression"], "pieces": [{
                "id": "mouth", "slot": "expression", "node": "head", "mode": "held",
                "sample_map": {"walk.right": {f"sample.{i}": {
                    "image": {"file": "mouth.png", "sha256": "b" * 64},
                    "pivot": [2, 2], "target_socket": "face",
                } for i in range(3)}},
            }],
        }],
    })
    catalog = parts.seal({
        "kind": "parts-catalog", "schema_version": 1, "id": "test.catalog",
        "rig": parts.reference(rig), "topology": parts.reference(topology),
        "groups": [{"id": "face", "label": "Face"}],
        "slots": [{"id": "expression", "group": "face", "label": "Expression",
                   "options": [parts.reference(pack)], "default": parts.reference(pack)}],
        "presets": [{"id": "happy", "label": "Happy",
                     "assignments": {"expression": parts.reference(pack)}}],
    })
    appearance = parts.seal({
        "kind": "parts-appearance", "schema_version": 1, "id": "test.appearance",
        "rig": parts.reference(rig), "motion": parts.reference(motion),
        "catalog": parts.reference(catalog), "states": ["walk"], "presets": ["happy"],
    })
    return [topology, rig, motion, pack, catalog, appearance]


def reseal(document, mutation):
    value = deepcopy(document)
    value.pop("digest")
    mutation(value)
    return parts.seal(value)


def test_repeated_images_have_distinct_samples_and_exact_variable_timing():
    docs = declarations()
    parts.validate_bundle(docs, complete=True)
    samples = docs[2]["clips"][0]["samples"]
    assert len({sample["id"] for sample in samples}) == 3
    assert len({sample["image"]["sha256"] for sample in samples}) == 1
    assert sum(sample["duration_ticks"] for sample in samples) == 14000


def test_parser_does_not_read_files_and_does_not_mutate_inputs(monkeypatch):
    docs = declarations()
    original = deepcopy(docs)
    monkeypatch.setattr(Path, "open", lambda *a, **k: pytest.fail("declaration parsing performed IO"))
    monkeypatch.setattr(Path, "exists", lambda *a: pytest.fail("declaration parsing performed IO"))
    parts.validate_bundle(docs, complete=True)
    assert docs == original


def test_digest_ignores_object_key_order_but_preserves_timeline_order():
    docs = declarations()
    reversed_keys = dict(reversed(list(docs[2].items())))
    assert parts.parse(reversed_keys) == docs[2]
    changed = reseal(docs[2], lambda d: d["clips"][0]["samples"].reverse())
    assert changed["digest"] != docs[2]["digest"]


@pytest.mark.parametrize("mutation,match", [
    (lambda d: d.update(unknown=True), "unknown"),
    (lambda d: d.update(schema_version=True), "schema_version"),
    (lambda d: d.update(schema_version=2), "schema_version"),
    (lambda d: d["nodes"].append({"id": "root"}), "duplicate"),
    (lambda d: d["nodes"][1].update(parent="missing"), "parent"),
    (lambda d: d["nodes"][0].update(parent="head"), "cycle"),
    (lambda d: d["sockets"][0].update(node="missing"), "node"),
    (lambda d: d["slots"][0].update(sockets=["missing"]), "socket"),
])
def test_bad_topology_is_rejected(mutation, match):
    with pytest.raises(parts.PartsContractError, match=match):
        reseal(declarations()[0], mutation)


@pytest.mark.parametrize("value", [True, 0, -1, 1.25, "128"])
def test_canvas_dimensions_are_positive_json_integers(value):
    with pytest.raises(parts.PartsContractError, match="width"):
        reseal(declarations()[1], lambda d: d["canvas"].update(width=value))


@pytest.mark.parametrize("path", ["../outside.png", "/tmp/outside.png", "C:/outside.png", "a\\b.png"])
def test_portable_asset_paths_cannot_escape(path):
    with pytest.raises(parts.PartsContractError, match="file"):
        reseal(declarations()[2], lambda d: d["clips"][0]["samples"][0]["image"].update(file=path))


def test_digest_catches_modified_declarations():
    rig = declarations()[1]
    rig["canvas"]["width"] += 1
    with pytest.raises(parts.PartsContractError, match="digest"):
        parts.parse(rig)


def test_changed_motion_cannot_use_old_part_binding():
    docs = declarations()
    docs[2] = reseal(docs[2], lambda d: d["clips"][0]["samples"][0].update(duration_ticks=1))
    with pytest.raises(parts.PartsContractError, match="digest"):
        parts.validate_bundle(docs)


def test_other_rig_and_missing_state_are_rejected():
    docs = declarations()
    alternate = reseal(docs[1], lambda d: d.update(id="test.tall", canvas={"width": 128, "height": 192}))
    docs.append(alternate)
    docs[5] = reseal(docs[5], lambda d: d.update(rig=parts.reference(alternate)))
    with pytest.raises(parts.PartsContractError, match="rig"):
        parts.validate_bundle(docs, complete=True)
    docs = declarations()
    docs[5] = reseal(docs[5], lambda d: d.update(states=["run"]))
    with pytest.raises(parts.PartsContractError, match="run"):
        parts.validate_bundle(docs, complete=True)


def test_missing_sample_cannot_pass_complete_validation():
    docs = declarations()
    docs[3] = reseal(docs[3], lambda d: d["bindings"][0]["pieces"][0]["sample_map"]["walk.right"].pop("sample.1"))
    pack_ref = parts.reference(docs[3])
    docs[4] = reseal(docs[4], lambda d: (
        d["slots"][0].update(options=[pack_ref], default=pack_ref),
        d["presets"][0].update(assignments={"expression": pack_ref})))
    docs[5] = reseal(docs[5], lambda d: d.update(catalog=parts.reference(docs[4])))
    parts.validate_bundle(docs)
    with pytest.raises(parts.PartsContractError, match="sample.1"):
        parts.validate_bundle(docs, complete=True)


def test_held_mode_still_requires_one_consistent_image():
    with pytest.raises(parts.PartsContractError, match="held"):
        reseal(declarations()[3], lambda d: d["bindings"][0]["pieces"][0]["sample_map"]["walk.right"]["sample.1"]["image"].update(sha256="c" * 64))


def test_legacy_adapter_is_explicit_and_does_not_relax_existing_profiles():
    from sprite_gen.compose.layers import PROFILES
    old = deepcopy(PROFILES)
    for name in PROFILES:
        topology = parts.legacy_topology(name)
        assert {s["id"] for s in topology["sockets"] if s["required"]} == set(PROFILES[name]["required"])
    with pytest.raises(parts.PartsContractError, match="legacy"):
        parts.legacy_topology("six_legs")
    assert PROFILES == old


def test_unusual_state_is_data_not_a_core_enum():
    motion = reseal(declarations()[2], lambda d: d["clips"][0].update(state="gallop"))
    assert motion["clips"][0]["state"] == "gallop"


def test_reference_points_to_immutable_content():
    topology = declarations()[0]
    assert parts.reference(topology) == {"id": topology["id"], "digest": topology["digest"]}
    assert len(topology["digest"]) == 64
    assert json.loads(json.dumps(parts.parse(topology))) == topology


def multi_choice_bundle(max_count):
    old = declarations()
    topology = reseal(old[0], lambda d: d["slots"][0].update(max_count=max_count))
    rig = reseal(old[1], lambda d: d.update(topology=parts.reference(topology)))
    motion = reseal(old[2], lambda d: d.update(rig=parts.reference(rig)))
    pack = reseal(old[3], lambda d: d["bindings"][0].update(rig=parts.reference(rig), motion=parts.reference(motion)))
    second = reseal(pack, lambda d: (d.update(id="face.brows"), d["bindings"][0]["pieces"][0].update(id="brows")))
    refs = [parts.reference(pack), parts.reference(second)]
    catalog = reseal(old[4], lambda d: (
        d.update(rig=parts.reference(rig), topology=parts.reference(topology)),
        d["slots"][0].update(options=refs, default=refs[0]),
        d["presets"][0].update(assignments={"expression": refs[1]})))
    appearance = reseal(old[5], lambda d: d.update(rig=parts.reference(rig), motion=parts.reference(motion),
                                                catalog=parts.reference(catalog), selections={"expression": refs}))
    return [topology, rig, motion, pack, second, catalog, appearance]


def test_cardinality_is_profile_data_and_multi_selection_is_validated():
    parts.validate_bundle(multi_choice_bundle(2), complete=True)
    with pytest.raises(parts.PartsContractError, match="max_count"):
        parts.validate_bundle(multi_choice_bundle(1), complete=True)


def test_presets_then_explicit_override_resolve_without_mutating_motion():
    docs = multi_choice_bundle(2)
    appearance, catalog = docs[-1], docs[-2]
    before = deepcopy(docs)
    selected = parts.resolve_selections(appearance, catalog)
    assert selected["expression"] == appearance["selections"]["expression"]
    without_override = reseal(appearance, lambda d: d.update(selections={}))
    assert parts.resolve_selections(without_override, catalog)["expression"] == catalog["presets"][0]["assignments"]["expression"]
    assert docs == before


def test_join_interfaces_cannot_pass_by_same_rig_alone():
    docs = declarations()
    docs[4] = reseal(docs[4], lambda d: d["slots"][0].update(join_interface="face.v2"))
    docs[5] = reseal(docs[5], lambda d: d.update(catalog=parts.reference(docs[4])))
    with pytest.raises(parts.PartsContractError, match="join interface"):
        parts.validate_bundle(docs, complete=True)


def test_unverified_socket_is_a_declaration_not_an_approval():
    motion = reseal(declarations()[2], lambda d: d["clips"][0]["samples"][0]["sockets"]["face"].update(confidence="unverified"))
    assert parts.parse(motion)["clips"][0]["samples"][0]["sockets"]["face"]["confidence"] == "unverified"


def test_missing_required_socket_is_rejected_without_image_io():
    docs = declarations()[:3]
    docs[2] = reseal(docs[2], lambda d: d["clips"][0]["samples"][0].update(sockets={}))
    with pytest.raises(parts.PartsContractError, match="required_socket"):
        parts.validate_bundle(docs)


def test_catalog_omission_cannot_hide_a_required_topology_slot():
    docs = declarations()
    docs[4] = reseal(docs[4], lambda d: d.update(slots=[], presets=[]))
    docs[5] = reseal(docs[5], lambda d: d.update(catalog=parts.reference(docs[4]), presets=[]))
    parts.validate_bundle(docs)
    with pytest.raises(parts.PartsContractError, match="required slot expression"):
        parts.validate_bundle(docs, complete=True)


def test_piece_cannot_use_a_node_outside_its_slot():
    docs = declarations()
    docs[3] = reseal(docs[3], lambda d: d["bindings"][0]["pieces"][0].update(node="root"))
    with pytest.raises(parts.PartsContractError, match="slot_node"):
        parts.validate_bundle(docs)


def test_visible_piece_needs_a_socket_in_the_target_sample():
    docs = declarations()[:4]
    docs[0] = reseal(docs[0], lambda d: d["sockets"][0].update(required=False))
    docs[1] = reseal(docs[1], lambda d: d.update(topology=parts.reference(docs[0])))
    docs[2] = reseal(docs[2], lambda d: (
        d.update(rig=parts.reference(docs[1])), d["clips"][0]["samples"][0].update(sockets={})))
    docs[3] = reseal(docs[3], lambda d: d["bindings"][0].update(rig=parts.reference(docs[1]), motion=parts.reference(docs[2])))
    with pytest.raises(parts.PartsContractError, match="target_socket"):
        parts.validate_bundle(docs)
    docs[3] = reseal(docs[3], lambda d: d["bindings"][0]["pieces"][0]["sample_map"]["walk.right"]["sample.0"].update(visible=False))
    parts.validate_bundle(docs)


def test_duplicate_document_ids_cannot_choose_an_arbitrary_revision():
    docs = declarations()
    with pytest.raises(parts.PartsContractError, match="duplicate document ID"):
        parts.validate_bundle([*docs, docs[1]])


def test_bundle_order_does_not_change_validation_or_hide_bad_catalog_slots():
    docs = declarations()
    assert parts.validate_bundle(reversed(docs), complete=True) == parts.validate_bundle(docs, complete=True)
    docs[4] = reseal(docs[4], lambda d: (d["slots"][0].update(id="unknown"), d.update(presets=[])))
    docs[5] = reseal(docs[5], lambda d: d.update(catalog=parts.reference(docs[4]), presets=[]))
    with pytest.raises(parts.PartsContractError, match="unknown references"):
        parts.validate_bundle(reversed(docs), complete=True)


@pytest.mark.parametrize("name", ["bad name", "Root", "../root", "root/hand"])
def test_legacy_adapter_checks_custom_landmark_names(name):
    with pytest.raises(parts.PartsContractError, match="landmark"):
        parts.legacy_topology("humanoid_biped", landmarks=[name])
