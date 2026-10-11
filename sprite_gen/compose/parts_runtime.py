# SPDX-License-Identifier: Apache-2.0
"""Independent sprite layers and attachment tracks on a shared motion clock.

No atlas, completed clip, appearance file or workspace write is required. This
Layer sampling is independent of any viewer and does not decode or composite
images. A display adapter keeps the part textures and applies the sampled anchor
and local offset. The CPU frame renderer remains an explicit inspection adapter.
"""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction

from sprite_gen.compose import compose_parts, parts_resolve
from sprite_gen.spec import parts_contract as contract

LAYER_POLICY = "parts.relative-layer-tween.v1"


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
        """Explicit CPU inspection rendering; independent layers use layers()."""
        _, sample, clock = self.current()
        image, evidence = compose_parts.render_sample(self.plan, sample, self.load)
        return image, {**clock, "appearance": contract.reference(self.appearance),
            "motion": self.appearance["motion"], "render_policy": self.plan["render_policy"],
            "evidence": evidence, "release_ready": False}

    def layers(self, *, interpolate=True):
        """Sample persistent layers; retain textures and tween attachment points.

        Held pieces keep their one source image. Raster pieces select the
        current shared body/pose sample; the image itself is never blended.
        Placement is socket position + local origin (-pivot + offset).
        Fractions remain exact until a target engine displays the scene.
        """
        if not isinstance(interpolate, bool):
            raise contract.PartsContractError("layer interpolation must be boolean")
        clip, sample, clock = self.current()
        index = clock["sample_index"]
        next_index = (index+1) % len(clip["samples"]) if clip["loop"] else min(index+1,len(clip["samples"])-1)
        following = clip["samples"][next_index]
        weight = Fraction(clock["sample_tick"]) / sample["duration_ticks"] if interpolate else Fraction(0)
        later = {entry["id"]:entry for entry in following["draw"]}
        names = {}
        for entry in sample["draw"]:
            key = ("body" if entry["role"]=="body" else entry["slot"])+"."+entry["piece_id"]
            names[key] = names.get(key,0)+1
        layers, masks = [], []
        for order,entry in enumerate(sample["draw"]):
            placement = entry["placement"]
            next_entry = later.get(entry["id"])
            anchor = [Fraction(v) for v in entry["socket_position"]]
            local = [Fraction(d-p) for d,p in zip(placement["offset"],placement["pivot"])]
            tweened = False
            if next_entry is not None and next_entry["placement"]["target_socket"]==placement["target_socket"]:
                anchor = [a+(Fraction(b)-a)*weight for a,b in zip(anchor,next_entry["socket_position"])]
                # Local pivots belonging to a different raster are not tweened:
                # the current texture retains its own geometry until the swap.
                if placement["image"]==next_entry["placement"]["image"]:
                    next_local = [d-p for d,p in zip(next_entry["placement"]["offset"],next_entry["placement"]["pivot"])]
                    local = [a+(Fraction(b)-a)*weight for a,b in zip(local,next_local)]
                tweened = bool(weight)
            position = [a+b for a,b in zip(anchor,local)]
            key = ("body" if entry["role"]=="body" else entry["slot"])+"."+entry["piece_id"]
            layer = {"id":key if names[key]==1 else entry["id"],"render_id":entry["id"],
                "order":order,"slot":entry["slot"],"node":entry["node"],"provides":entry["provides"],
                "mode":entry["mode"],"pack":entry["pack"],"image":placement["image"],
                "attach_to":placement["target_socket"],"anchor":[str(v) for v in anchor],
                "local_origin":[str(v) for v in local],"position":[str(v) for v in position],
                "tweened":tweened,"allow_clip":placement["allow_clip"],
                "palette_regions":{**entry["palette_regions"],**placement["palette_regions"]}}
            layers.append(layer)
            mask = placement["mask"]
            if mask is not None and (not mask.get('optional_target',False) or any(mask['target_node'] in e['provides'] for e in sample['draw'])):
                masks.append({**deepcopy(mask),"pack":entry["pack"],"issuer":layer["id"],
                    "origin":[str(v) for v in position] if mask["space"]=="part-local" else ["0","0"]})
        return deepcopy({**clock,"appearance":contract.reference(self.appearance),"motion":self.appearance["motion"],
            "canvas":deepcopy(self.plan["canvas"]),"mirror":self.plan["mirror"],"palette":deepcopy(self.plan["palette"]),
            "layer_policy":LAYER_POLICY,"sample_fraction":str(weight),"next_sample_id":following["id"],
            "layers":layers,"masks":masks,"hidden":sample["hidden"],"release_ready":False})
