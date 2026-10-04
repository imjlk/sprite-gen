# SPDX-License-Identifier: Apache-2.0
"""Live sample composition and attachment swapping on a shared motion clock.

No atlas, completed clip, appearance file or workspace write is required. This
is the native CPU runtime; target-engine GPU/mesh adapters can consume the same
resolved sample draw lists without inventing independent part timelines.
"""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction

from sprite_gen.compose import compose_parts, parts_resolve
from sprite_gen.spec import parts_contract as contract


class PartsPlayer:
    """One character's clock/selection, with shared immutable documents/assets."""

    def __init__(self, appearance, documents, load, *, state=None):
        self.documents = tuple(deepcopy(d) for d in documents)
        self.load = load
        self.appearance = contract.parse(appearance)
        self.plan = parts_resolve.resolve(self.appearance, self.documents)
        self.state = state or self.plan["clips"][0]["state"]
        self.elapsed_seconds = Fraction(0)
        self._clip(self.state)

    @classmethod
    def from_workspace(cls, assembly, *, state=None):
        from sprite_gen.curate import parts_motion
        root, index, registry, _ = parts_motion._active(assembly)
        if index["appearance"] is None:
            raise contract.PartsContractError("runtime requires an active appearance")
        appearance = registry[index["appearance"]["id"]]
        if appearance["states"] != index["states"]:
            raise contract.PartsContractError("runtime requested states differ from appearance")
        return cls(appearance, registry.values(), compose_parts.AssetLoader(root, index), state=state)

    def _clip(self, state):
        matches = [clip for clip in self.plan["clips"] if clip["state"] == state]
        if len(matches) != 1:
            raise contract.PartsContractError(f"runtime state has no active clip: {state}")
        return matches[0]

    def seek(self, seconds):
        if isinstance(seconds, bool) or not isinstance(seconds, (int, Fraction)):
            raise contract.PartsContractError("runtime time must be an integer or exact Fraction of seconds")
        if seconds < 0:
            raise contract.PartsContractError("runtime time cannot be negative")
        self.elapsed_seconds = Fraction(seconds)

    def advance(self, seconds):
        if isinstance(seconds, bool) or not isinstance(seconds, (int, Fraction)) or seconds < 0:
            raise contract.PartsContractError("runtime advance must be a nonnegative integer or exact Fraction")
        self.seek(self.elapsed_seconds + seconds)

    def set_state(self, state, *, restart=True):
        self._clip(state)
        self.state = state
        if restart:
            self.elapsed_seconds = Fraction(0)

    def set_appearance(self, appearance):
        """Validate before mutation; swapping never resets state or elapsed time."""
        appearance = contract.parse(appearance)
        if any(appearance[k] != self.appearance[k] for k in ("rig", "motion")):
            raise contract.PartsContractError("runtime swap requires the same rig and frozen motion")
        plan = parts_resolve.resolve(appearance, self.documents)
        if self.state not in {c["state"] for c in plan["clips"]}:
            raise contract.PartsContractError("runtime swap drops the current state")
        self.appearance, self.plan = appearance, plan

    def set_selection(self, selections, *, palette=None):
        appearance = {k: deepcopy(v) for k, v in self.appearance.items() if k != "digest"}
        appearance["selections"].update(deepcopy(selections))
        if palette is not None:
            appearance["palette"].update(deepcopy(palette))
        self.set_appearance(contract.seal(appearance))

    def current(self):
        clip = self._clip(self.state)
        elapsed = self.elapsed_seconds * clip["timebase"]
        total = sum(s["duration_ticks"] for s in clip["samples"])
        position = elapsed % total if clip["loop"] else min(elapsed, total - 1)
        start = 0
        for index, sample in enumerate(clip["samples"]):
            end = start + sample["duration_ticks"]
            if position < end:
                return clip, sample, {"state": self.state, "clip_id": clip["id"],
                    "sample_id": sample["id"], "sample_index": index, "timebase": clip["timebase"],
                    "elapsed_seconds": str(self.elapsed_seconds), "clip_tick": str(position),
                    "sample_tick": str(position - start), "loop": clip["loop"]}
            start = end
        raise contract.PartsContractError("runtime timeline has no sample")

    def frame(self):
        """Compose only the current sample; caller displays it or sends draw lists."""
        _, sample, clock = self.current()
        image, evidence = compose_parts.render_sample(self.plan, sample, self.load)
        return image, {**clock, "appearance": contract.reference(self.appearance),
            "motion": self.appearance["motion"], "render_policy": self.plan["render_policy"],
            "evidence": evidence, "release_ready": False}
