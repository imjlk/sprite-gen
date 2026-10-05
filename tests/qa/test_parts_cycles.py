# SPDX-License-Identifier: Apache-2.0
from copy import deepcopy

import pytest

from sprite_gen.qa import parts_cycles
from sprite_gen.spec.parts_contract import PartsContractError


def guide():
    return {"records": [{"state": "run", "source_sample": f"sample.{n}"} for n in range(60,76)],
            "cadence": {"run": 24}, "cycle": {"run": {"source_samples": list(range(60,76)),
            "phase_period": 16, "steps": 2, "source_fps": 24}}}


def test_full_two_step_reference_preserves_native_cadence():
    value = guide()
    before = deepcopy(value)
    result = parts_cycles.verify(value)
    assert result["cycles"][0]["samples_per_step"] == 8
    assert result["cycles"][0]["duration_seconds"] == "2/3"
    assert value == before and not result["art_approved"]


@pytest.mark.parametrize("change", ["half", "duplicate", "reorder", "slow"])
def test_half_step_or_wrong_order_or_implicit_slowdown_is_rejected(change):
    value = guide()
    if change == "half": value["records"] = value["records"][:8]
    elif change == "duplicate": value["records"][8] = value["records"][7].copy()
    elif change == "reorder": value["records"][7], value["records"][8] = value["records"][8], value["records"][7]
    else: value["cadence"]["run"] = 12
    with pytest.raises(PartsContractError):
        parts_cycles.verify(value)


def test_declaring_the_half_stride_as_the_full_reference_does_not_hide_missing_phases():
    value = guide()
    value["cycle"]["run"]["source_samples"] = list(range(60,68))
    value["records"] = value["records"][:8]
    with pytest.raises(PartsContractError, match="complete"):
        parts_cycles.verify(value)


def test_missing_authored_cycle_never_creates_an_art_approval():
    result = parts_cycles.verify({"records": []})
    assert result["cycles"] == [] and not result["art_approved"]
