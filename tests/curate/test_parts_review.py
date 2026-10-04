# SPDX-License-Identifier: Apache-2.0
from copy import deepcopy
import json

import pytest

from compose.test_parts_pipeline import populated_workspace
from sprite_gen.curate import parts_appearance, parts_review
from sprite_gen.qa import inspect_parts
from sprite_gen.spec import parts_assets as assets, parts_contract as contract, parts_workspace as workspace


def ready(tmp_path, *, states=("walk",)):
    assembly, selection, source = populated_workspace(tmp_path, states=states)
    parts_appearance.create(assembly, selection)
    checks = tmp_path / "checks.json"
    checks.write_bytes(workspace.json_bytes({name: "approved" for name in inspect_parts.REVIEW_CHECKS}))
    return assembly, selection, checks


def test_qa_keeps_visual_and_contact_quality_unverified_and_hashes_source_bytes(tmp_path):
    assembly, _, _ = ready(tmp_path)
    result = inspect_parts.inspect(assembly)
    assert result["status"] == "unverified" and result["release_ready"] is False
    assert next(c for c in result["checks"] if c["id"] == "contact-measurement")["status"] == "unverified"
    qa = workspace.read_json(assembly.parent, result["qa"])
    assert len(qa["asset_hashes"]) >= 5
    assert all(next(c for c in qa["checks"] if c["id"] == name)["status"] == "unverified" for name in inspect_parts.REVIEW_CHECKS)


def test_approval_requires_explicit_checks_and_exact_candidate_hash(tmp_path):
    assembly, _, checks = ready(tmp_path)
    result = parts_review.prepare(assembly)
    incomplete = {"fitting": "approved"}
    checks.write_bytes(workspace.json_bytes(incomplete))
    with pytest.raises(contract.PartsContractError, match="explicitly cover"):
        parts_review.decide(assembly, result["review_hash"], checks, "fixture-test")
    checks.write_bytes(workspace.json_bytes({name: "approved" for name in inspect_parts.REVIEW_CHECKS}))
    approved = parts_review.decide(assembly, result["review_hash"], checks, "fixture-test", note="Synthetic technical fixture; not artwork quality acceptance")
    assert approved["status"] == "approved" and approved["release_ready"] is True
    with pytest.raises(contract.PartsContractError, match="no longer active"):
        parts_review.decide(assembly, result["review_hash"], checks, "fixture-test")
    index, registry = workspace.read_workspace(assembly)
    parts_review.verify(assembly.parent, index, registry, registry[approved["review"]["id"]])


def test_rejected_review_does_not_grant_release_approval(tmp_path):
    assembly, _, checks = ready(tmp_path)
    result = parts_review.prepare(assembly)
    checks.write_bytes(workspace.json_bytes({name: "rejected" if name == "motion" else "approved" for name in inspect_parts.REVIEW_CHECKS}))
    rejected = parts_review.decide(assembly, result["review_hash"], checks, "fixture-test", decision="rejected")
    assert rejected["status"] == "rejected" and rejected["release_ready"] is False


@pytest.mark.parametrize("change", ["palette", "states", "policy", "toolchain", "evidence", "source"])
def test_stale_palette_states_policy_toolchain_or_bytes_cannot_be_approved(tmp_path, monkeypatch, change):
    assembly, selection, checks = ready(tmp_path)
    result = parts_review.prepare(assembly)
    if change == "palette":
        data = json.loads(selection.read_bytes())
        data["palette"] = {"skin": [1, 2, 3]}
        selection.write_bytes(workspace.json_bytes(data))
        parts_appearance.create(assembly, selection)
    elif change == "states":
        data = json.loads(assembly.read_bytes())
        data["states"].append("run")
        assembly.write_bytes(workspace.json_bytes(data))
    elif change == "policy":
        monkeypatch.setattr(inspect_parts, "QA_POLICY", "parts.evidence.v2")
    elif change == "toolchain":
        monkeypatch.setattr(inspect_parts, "toolchain", lambda: {"fake": "changed"})
    elif change == "evidence":
        (assembly.parent / result["file"]).parent.joinpath("preview.json").write_bytes(b"{}")
    elif change == "source":
        index, registry = workspace.read_workspace(assembly)
        motion = registry[index["motion"]["id"]]
        target = (assembly.parent / index["documents"][motion["id"]]).parent / motion["freeze"]["annotations"]["file"]
        target.write_bytes(b"{}")
    with pytest.raises(contract.PartsContractError):
        parts_review.decide(assembly, result["review_hash"], checks, "fixture-test")


@pytest.mark.parametrize("scope", ["part", "binding"])
def test_scopes_are_recorded_and_do_not_approve_entire_appearance(tmp_path, scope):
    assembly, _, checks = ready(tmp_path)
    index, registry = workspace.read_workspace(assembly)
    body = registry[index["body"][0]["id"]]
    result = parts_review.prepare(assembly, scope=scope, subject=body["id"], binding=body["bindings"][0]["id"] if scope == "binding" else None)
    approved = parts_review.decide(assembly, result["review_hash"], checks, "fixture-test")
    assert approved["scope"] == scope and approved["release_ready"] is False


def test_review_activation_rejects_concurrent_assembly_edit(tmp_path, monkeypatch):
    assembly, _, _ = ready(tmp_path)
    original = assets.publish_object
    def concurrent(root, collection, identity, digest, payloads):
        folder = original(root, collection, identity, digest, payloads)
        if collection == "reviews":
            data = json.loads(assembly.read_bytes())
            data["id"] = "concurrent-edit"
            assembly.write_bytes(workspace.json_bytes(data))
        return folder
    monkeypatch.setattr(assets, "publish_object", concurrent)
    with pytest.raises(contract.PartsContractError, match="assembly changed"):
        parts_review.prepare(assembly)
    assert json.loads(assembly.read_bytes())["id"] == "concurrent-edit"
