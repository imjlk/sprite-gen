# SPDX-License-Identifier: Apache-2.0
"""Real CLI and wheel entry points for data-defined parts workspaces."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from sprite_gen.spec import parts_contract as contract, parts_workspace as workspace


@pytest.mark.parametrize("preset", workspace.preset_names())
def test_each_preset_initializes_the_same_portable_contract(tmp_path, preset):
    root = tmp_path / preset
    workspace.initialize(root, preset)
    index, registry = workspace.read_workspace(root / "assembly.json")
    assert len(registry) == 3
    assert index["motion"] is None and index["appearance"] is None
    assert registry[index["rig"]["id"]]["view"] == "right"
    with pytest.raises(contract.PartsContractError, match="no appearance"):
        workspace.read_workspace(root / "assembly.json", complete=True)


def test_sd_and_tall_share_topology_but_have_distinct_geometry():
    sd, _ = workspace.initial_documents("human.sd.v1")
    tall, _ = workspace.initial_documents("human.tall.v1")
    assert sd[0] == tall[0]
    assert sd[1]["digest"] != tall[1]["digest"]
    assert sd[1]["canvas"] != tall[1]["canvas"]
    assert {"hair", "expression", "upper", "lower", "footwear"} <= {s["id"] for s in sd[2]["slots"]}
    assert {"bangs", "eyes", "mouth", "sleeve_l", "headwear", "gloves", "back_accessory"} <= {s["id"] for s in sd[2]["slots"]}


def test_animal_and_no_leg_profiles_do_not_inherit_human_clothing():
    canine, _ = workspace.initial_documents("canine.medium.v1")
    nodes = {n["id"] for n in canine[0]["nodes"] if n["contact"]}
    assert nodes == {"foot_fl", "foot_fr", "foot_bl", "foot_br"}
    assert {"ears", "tail", "coat", "pawwear"} <= {s["id"] for s in canine[2]["slots"]}
    blob, states = workspace.initial_documents("blob.fixture.v1")
    assert not any(n["contact"] for n in blob[0]["nodes"])
    assert "crawl" in states
    for docs in [canine, blob]:
        assert {"upper", "lower", "footwear"}.isdisjoint(s["id"] for s in docs[2]["slots"])


def test_asymmetric_six_limb_profile_has_no_assumed_pairs():
    docs, _ = workspace.initial_documents("asymmetric.six-limb.v1")
    assert len([n for n in docs[0]["nodes"] if n["id"].startswith("limb_")]) == 6
    assert all(n["symmetric_with"] is None for n in docs[0]["nodes"])


def test_root_location_does_not_change_portable_manifests(tmp_path):
    roots = [tmp_path / "a", tmp_path / "nested" / "b"]
    for root in roots:
        workspace.initialize(root, "human.sd.v1")
    for filename in ["assembly.json", "profiles/rig.json", "profiles/topology.json", "catalog.json"]:
        assert (roots[0] / filename).read_bytes() == (roots[1] / filename).read_bytes()


def test_existing_data_is_preserved(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    sentinel = root / "sprite-request.json"
    sentinel.write_bytes(b"original run")
    with pytest.raises(contract.PartsContractError, match="new or empty"):
        workspace.initialize(root, "human.sd.v1")
    assert sentinel.read_bytes() == b"original run"
    assert list(root.iterdir()) == [sentinel]


@pytest.mark.parametrize("escape", ["../outside.json", "/tmp/outside.json", "link.json"])
def test_library_path_escapes_fail_before_read(tmp_path, escape):
    root = tmp_path / "library"
    workspace.initialize(root, "human.sd.v1")
    (root / "link.json").symlink_to(tmp_path / "outside.json")
    index = json.loads((root / "assembly.json").read_text())
    index["documents"][index["rig"]["id"]] = escape
    (root / "assembly.json").write_text(json.dumps(index))
    with pytest.raises(contract.PartsContractError, match="root|relative|traversal"):
        workspace.read_workspace(root / "assembly.json")


def test_duplicate_json_fields_and_large_descriptors_are_rejected():
    with pytest.raises(contract.PartsContractError, match="duplicate"):
        workspace.decode_json(b'{"id":"a", "id":"b"}')
    with pytest.raises(contract.PartsContractError, match="limit"):
        workspace.decode_json(b" " * (workspace.MAX_JSON_BYTES + 1))


def test_cli_can_create_and_check_a_workspace_and_refuses_false_completeness(tmp_path):
    root = tmp_path / "hero"
    prefix = [sys.executable, "-m", "sprite_gen.cli"]
    created = subprocess.run([*prefix, "parts-init", "--dir", str(root)], capture_output=True, text=True)
    assert created.returncode == 0, created.stderr + created.stdout
    assert json.loads(created.stdout)["status"] == "draft"
    checked = subprocess.run([*prefix, "parts-validate", "--assembly", str(root / "assembly.json")], capture_output=True, text=True)
    assert checked.returncode == 0, checked.stderr + checked.stdout
    assert json.loads(checked.stdout)["release_ready"] is False
    full = subprocess.run([*prefix, "parts-validate", "--assembly", str(root / "assembly.json"), "--complete"], capture_output=True, text=True)
    assert full.returncode == 2
    assert "no appearance" in full.stdout


def test_different_catalog_topology_is_rejected(tmp_path):
    root = tmp_path / "hero"
    workspace.initialize(root, "human.sd.v1")
    index, registry = workspace.read_workspace(root / "assembly.json")
    catalog = registry[index["catalog"]["id"]]
    catalog.pop("digest")
    catalog["topology"]["digest"] = "a" * 64
    catalog = contract.seal(catalog)
    (root / "catalog.json").write_bytes(workspace.json_bytes(catalog))
    index["catalog"] = contract.reference(catalog)
    (root / "assembly.json").write_bytes(workspace.json_bytes(index))
    with pytest.raises(contract.PartsContractError, match="digest"):
        workspace.read_workspace(root / "assembly.json")


@pytest.mark.parametrize("value", [b'{"v":NaN}', b'{"v":Infinity}', b'[' * 2000 + b']' * 2000],
                         ids=["nan", "infinity", "deeply-nested"])
def test_nonfinite_or_overly_deep_json_is_a_contract_error(value):
    with pytest.raises(contract.PartsContractError, match="JSON"):
        workspace.decode_json(value)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="platform has no FIFO")
def test_fifo_is_rejected_without_blocking(tmp_path):
    os.mkfifo(tmp_path / "pipe.json")
    with pytest.raises(contract.PartsContractError, match="regular file"):
        workspace.read_json(tmp_path, "pipe.json")


@pytest.mark.parametrize("preset", workspace.preset_names())
def test_every_body_profile_accepts_full_multi_piece_sample_bindings(preset):
    docs, states = workspace.initial_documents(preset)
    topology, rig, catalog = docs
    motion = contract.seal({
        "kind": "parts-motion", "schema_version": 1, "id": "fixture.motion",
        "rig": contract.reference(rig), "clips": [{
            "id": state + ".right", "state": state, "view": rig["view"],
            "timebase": 120000, "loop": True, "samples": [{
                "id": "sample.0", "image": {"file": "frames/body.png", "sha256": "a" * 64},
                "duration_ticks": 5000, "sockets": {socket["id"]: {
                    "position": [rig["canvas"]["width"] // 2, rig["canvas"]["height"] // 2],
                    "visible": True, "confidence": "unverified"} for socket in topology["sockets"]},
            }],
        } for state in states],
    })
    pieces = []
    for slot in topology["slots"]:
        for i, node in enumerate(slot["nodes"] or ["root"]):
            pieces.append({
                "id": f"{slot['id']}.{i}", "slot": slot["id"], "node": node, "mode": "held",
                "sample_map": {clip["id"]: {"sample.0": {
                    "image": {"file": "parts/shared.png", "sha256": "b" * 64}, "pivot": [0, 0],
                    "target_socket": (slot["sockets"] or [topology["sockets"][0]["id"]])[0],
                }} for clip in motion["clips"]},
            })
    pack = contract.seal({
        "kind": "parts-pack", "schema_version": 1, "id": "fixture.parts", "design_id": "test-design",
        "bindings": [{"id": "fixture.binding", "rig": contract.reference(rig),
                      "motion": contract.reference(motion), "view": rig["view"],
                      "occupies": [slot["id"] for slot in topology["slots"]], "pieces": pieces}],
    })
    catalog.pop("digest")
    for slot in catalog["slots"]:
        slot.update(options=[contract.reference(pack)], default=contract.reference(pack))
    catalog = contract.seal(catalog)
    appearance = contract.seal({
        "kind": "parts-appearance", "schema_version": 1, "id": "fixture.appearance",
        "rig": contract.reference(rig), "motion": contract.reference(motion),
        "catalog": contract.reference(catalog), "states": states,
    })
    contract.validate_bundle([topology, rig, motion, pack, catalog, appearance], complete=True)
