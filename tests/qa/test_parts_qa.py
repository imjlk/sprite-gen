# SPDX-License-Identifier: Apache-2.0
from copy import deepcopy

import pytest

from parts_helpers import composition_fixture
from sprite_gen.qa import inspect_parts
from sprite_gen.spec import parts_contract as contract


def annotations(docs):
    motion = docs[2]
    return {"kind": "parts-contact-annotations", "schema_version": 1, "motion": contract.reference(motion),
        "clips": {"walk.right": {"world_offsets": {"sample.0": [0, 0], "sample.1": [-1, 0], "sample.2": [0, 0]},
            "stances": [{"node": "foot_l", "socket": "foot_l", "samples": ["sample.0", "sample.1", "sample.2"], "max_slip_px": 0}]}}}


def test_declared_world_travel_cancels_in_place_contact_motion_without_silhouette_guess():
    docs, _ = composition_fixture()
    checks = inspect_parts._contact_checks(annotations(docs), docs[2], docs[0], False, 128)
    assert checks[0]["status"] == "passed" and checks[0]["max_drift_px"] == 0
    assert checks[-1]["status"] == "unverified"  # Other feet/clips were not annotated.
    data = annotations(docs)
    data["clips"]["walk.right"]["world_offsets"]["sample.1"] = [0, 0]
    assert inspect_parts._contact_checks(data, docs[2], docs[0], False, 128)[0]["status"] == "error"


@pytest.mark.parametrize("change", ["node", "socket", "motion", "order", "travel", "tolerance"])
def test_contact_annotation_errors_do_not_become_passed(change):
    docs, _ = composition_fixture()
    data = annotations(docs)
    stance = data["clips"]["walk.right"]["stances"][0]
    if change == "node": stance["node"] = "head"
    elif change == "socket": stance["socket"] = "head"
    elif change == "motion": data["motion"]["digest"] = "a" * 64
    elif change == "order": stance["samples"].reverse()
    elif change == "travel": data["clips"]["walk.right"]["world_offsets"].pop("sample.1")
    else: stance["max_slip_px"] = -1
    with pytest.raises(contract.PartsContractError):
        inspect_parts._contact_checks(data, docs[2], docs[0], False, 128)


def test_blob_does_not_acquire_humanoid_contact_requirements():
    docs, _ = composition_fixture("blob.fixture.v1", states=("pulse",))
    assert inspect_parts._contact_checks(None, docs[2], docs[0], False, 96)[0]["status"] == "passed"
