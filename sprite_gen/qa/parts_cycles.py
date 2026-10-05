# SPDX-License-Identifier: Apache-2.0
"""Verify coverage/timing against an explicitly authored source cycle.

This does not infer gait, foot identity or a video's period. The reference
cycle must first be inspected by its author; pixel quality stays unapproved.
"""
from fractions import Fraction

from sprite_gen.spec.parts_contract import PartsContractError


def verify(guide):
    results = []
    for state, cycle in guide.get("cycle", {}).items():
        expected = cycle["source_samples"]
        period, steps = cycle["phase_period"], cycle["steps"]
        source_fps = cycle["source_fps"]
        if (type(period) is not int or type(steps) is not int or period <= 0 or steps < 2
            or period % steps or len(expected) != period
            or any(type(n) is not int or n < 0 for n in expected)
            or expected != list(range(expected[0], expected[0]+period))):
            raise PartsContractError(f"{state}: reference must declare a complete contiguous source cycle")
        selected = [r["source_sample"] for r in guide["records"] if r["state"] == state]
        if selected != [f"sample.{n}" for n in expected]:
            raise PartsContractError(f"{state}: selected samples omit, duplicate or reorder part of the source cycle")
        if type(source_fps) is not int or source_fps <= 0 or guide["cadence"][state] != source_fps:
            raise PartsContractError(f"{state}: source-cycle cadence differs; declare a separate timing revision")
        results.append({"state": state, "source_samples": expected, "steps": steps,
                        "samples_per_step": period//steps, "duration_seconds": str(Fraction(period, source_fps))})
    return {"kind": "authored-source-cycle-coverage", "cycles": results,
            "art_approved": False, "release_ready": False}
