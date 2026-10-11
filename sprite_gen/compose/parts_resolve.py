# SPDX-License-Identifier: Apache-2.0
"""Filesystem-free resolution of a concrete appearance on one frozen timeline."""
from __future__ import annotations

from copy import deepcopy
import heapq

from sprite_gen.spec import parts_contract as contract

RENDER_POLICY = "parts.integer-rgba.v1"


def _binding(pack, appearance, rig):
    return next(b for b in pack["bindings"] if b["rig"] == appearance["rig"]
                and b["motion"] == appearance["motion"] and b["view"] == rig["view"])


def _precedence(slots):
    result = {name: set(slot["overrides"]) for name, slot in slots.items()}
    parents = {name: [] for name in slots}
    counts = {name: len(targets) for name, targets in result.items()}
    for name, targets in result.items():
        for target in targets:
            parents[target].append(name)
    ready = [name for name, count in counts.items() if count == 0]
    while ready:
        target = ready.pop()
        for name in parents[target]:
            result[name].update(result[target])
            counts[name] -= 1
            if counts[name] == 0:
                ready.append(name)
    return result


def _remove(suppressed, entry, targets, reason):
    affected = set(entry["provides"]) & targets
    if affected and affected != set(entry["provides"]):
        raise contract.PartsContractError(f"partial replacement of indivisible raster {entry['id']}; split its provided nodes")
    if affected:
        suppressed[entry["id"]] = reason


def layer_ranks(topology):
    """Rank of each node in the topology's draw table (zmap); unlisted nodes draw after it."""
    return {node: number for number, node in enumerate(topology.get("layers", []))}


def rank(entry, ranks):
    """An entry's draw-table position: its node, else the first listed node it provides."""
    if entry["node"] in ranks:
        return ranks[entry["node"]]
    return min((ranks[n] for n in entry["provides"] if n in ranks), default=len(ranks))


def _order(active, known, nodes, ranks=None):
    ranks = ranks or {}
    by_id = {entry["id"]: entry for entry in active}
    edges = {name: set() for name in by_id}
    def targets(token):
        if token in nodes:
            # A primary node is an attachment/slot constraint, not a promise
            # that this raster depicts that node. Face accessories attached at
            # head must order after the head provider, not after each other.
            return [e["id"] for e in active if token in e["provides"]]
        matches = [e["id"] for e in known if token in (e["id"], e["piece_id"])]
        if len(matches) != 1:
            label = "unknown" if not matches else "ambiguous"
            raise contract.PartsContractError(f"{label} draw-order target {token}; use a node or qualified pack.piece ID")
        return [name for name in matches if name in by_id]
    for entry in active:
        for token in entry["placement"]["before"]:
            edges[entry["id"]].update(n for n in targets(token) if n != entry["id"])
        for token in entry["placement"]["after"]:
            for name in targets(token):
                if name != entry["id"]:
                    edges[name].add(entry["id"])
    indegree = dict.fromkeys(edges, 0)
    for names in edges.values():
        for name in names:
            indegree[name] += 1
    # Explicit constraints first; among the drawable, the lowest draw-table rank, the body
    # before what is worn on the same layer (a sleeve over its arm), then the ID.
    # Without a draw table the order is the explicit constraints and the IDs alone.
    key = {name: (rank(entry, ranks), bool(ranks) and entry["role"] != "body", name) for name, entry in by_id.items()}
    ready = [key[name] for name, count in indegree.items() if count == 0]
    heapq.heapify(ready)
    ordered = []
    while ready:
        *_, name = heapq.heappop(ready)
        ordered.append(by_id[name])
        for target in sorted(edges[name]):
            indegree[target] -= 1
            if indegree[target] == 0:
                heapq.heappush(ready, key[target])
    if len(ordered) != len(active):
        raise contract.PartsContractError(f"draw-order cycle: {sorted(n for n, v in indegree.items() if v)}")
    return ordered


def resolve(appearance, documents, *, complete=True):
    """Return serializable sample plans; no IO, image decoding or inferred anatomy.

    A provided node is an indivisible raster interface. Several pieces in the
    same pack may jointly provide it. Cross-pack providers require an explicit
    slot override, and a raster supplying several nodes cannot be partly erased.
    """
    appearance = contract.parse(appearance)
    # Reviews/build locks consume appearances, and cannot validate a proposed
    # replacement before activation has detached those old consumers.
    docs = [d for d in documents if d["id"] != appearance["id"]
            and d["kind"] not in {"parts-review", "parts-build-lock"}] + [appearance]
    registry = contract.validate_bundle(docs)
    # complete=False renders one part on the body alone (a part review) with required slots empty.
    contract._check_appearance(appearance, registry, complete)
    rig = registry[appearance["rig"]["id"]]
    motion = registry[appearance["motion"]["id"]]
    topology = registry[rig["topology"]["id"]]
    catalog = registry[appearance["catalog"]["id"]]
    if not appearance["body"] or motion["freeze"] is None:
        raise contract.PartsContractError("composition requires an explicit body and frozen motion")
    selected = contract.resolve_selections(appearance, catalog)
    packs = {ref["id"]: registry[ref["id"]] for ref in appearance["body"]}
    for selection in selected.values():
        packs.update({ref["id"]: registry[ref["id"]] for ref in contract._selected_refs(selection)})
    bindings = {name: _binding(pack, appearance, rig) for name, pack in packs.items()}
    if appearance["mirror"] and (not rig["mirror_allowed"] or not all(b["mirror_safe"] for b in bindings.values())):
        raise contract.PartsContractError("mirror requires explicit rig permission and mirror-safe bindings for every body/part")
    entries = []
    for name, pack in sorted(packs.items()):
        for piece in bindings[name]["pieces"]:
            entries.append({"id": f"{name}.{piece['id']}", "pack": contract.reference(pack),
                            "binding_id": bindings[name]["id"], "piece_id": piece["id"],
                            "mode": piece["mode"],
                            "role": pack["role"], "slot": piece["slot"], "node": piece["node"],
                            "provides": piece["provides"], "replaces": piece["replaces"],
                            "palette_regions": piece["palette_regions"], "piece": piece})
    if len({e["id"] for e in entries}) != len(entries):
        raise contract.PartsContractError("qualified draw IDs collide; rename a pack/piece")
    flat = [p for p in packs.values() if p["role"] == "body" and p["body_mode"] == "flat"]
    if flat and (len(appearance["body"]) != 1 or any(e["replaces"] for e in entries)):
        raise contract.PartsContractError("flat body supports overlays only; node replacement needs segmented artwork")
    precedence = _precedence({s["id"]: s for s in topology["slots"]})
    suppressed = {}
    # Cover rules (vslot): a binding names the tags it covers; a piece shows only while
    # its tag is covered, or only while it is not.
    covering = {tag: sorted(name for name, b in bindings.items() if tag in b.get("covers", []))
                for b in bindings.values() for tag in b.get("covers", [])}
    for entry in entries:
        rule = entry["piece"].get("cover_rule")
        if rule is not None and (rule["tag"] in covering) != (rule["show"] == "covered"):
            suppressed[entry["id"]] = f"cover:{rule['tag']}"
    body_provided = {n for e in entries if e["role"] == "body" for n in e["provides"]}
    for entry in entries:
        if entry["role"] == "body" or entry["id"] in suppressed:
            continue
        if not set(entry["replaces"]) <= body_provided:
            raise contract.PartsContractError(f"{entry['id']} replaces a node with no body interface")
        for base in entries:
            if base["role"] == "body":
                _remove(suppressed, base, set(entry["replaces"]), entry["id"])
            elif base["pack"] != entry["pack"] and base["slot"] in precedence[entry["slot"]]:
                _remove(suppressed, base, set(entry["provides"]), entry["id"])
    remaining = [e for e in entries if e["id"] not in suppressed]
    providers = {}
    for entry in remaining:
        for node in entry["provides"]:
            previous = providers.setdefault(node, entry["pack"]["id"])
            if previous != entry["pack"]["id"]:
                raise contract.PartsContractError(f"duplicate providers for node {node}: {previous}, {entry['pack']['id']}")
    if not flat:
        missing = {n["id"] for n in topology["nodes"] if n["required"]} - providers.keys()
        if missing:
            raise contract.PartsContractError(f"missing required body nodes: {sorted(missing)}")
    ranks = layer_ranks(topology)
    palette = {c["id"]: c["default"] for c in catalog["palette_channels"]}
    palette.update(appearance["palette"])
    clips = []
    for state in appearance["states"]:
        clip = next(c for c in motion["clips"] if c["state"] == state and c["view"] == rig["view"])
        samples = []
        for sample in clip["samples"]:
            known, active, hidden = [], [], []
            for source in entries:
                entry = {k: deepcopy(v) for k, v in source.items() if k != "piece"}
                entry["placement"] = deepcopy(source["piece"]["sample_map"][clip["id"]][sample["id"]])
                known.append(entry)
                if entry["id"] in suppressed:
                    continue
                placement = entry["placement"]
                socket = sample["sockets"].get(placement["target_socket"])
                if not placement["visible"] or (socket is not None and not socket["visible"]):
                    hidden.append(entry["id"])
                    continue
                if socket is None or socket["confidence"] != "verified":
                    raise contract.PartsContractError(f"{entry['id']}/{sample['id']}: visible placement needs a verified socket")
                entry["offset"] = [t - p + d for t, p, d in zip(socket["position"], placement["pivot"], placement["offset"])]
                entry["socket_position"] = deepcopy(socket["position"])
                contract._members([*entry["palette_regions"], *placement["palette_regions"]], palette, entry["id"])
                active.append(entry)
            ordered = _order(active, known, {n["id"] for n in topology["nodes"]}, ranks)
            for entry in ordered:
                mask = entry["placement"]["mask"]
                if mask is not None and not mask.get('optional_target',False) and not any(mask["target_node"] in e["provides"] for e in remaining):
                    raise contract.PartsContractError(f"{entry['id']}: mask target has no raster provider")
            samples.append({"id": sample["id"], "duration_ticks": sample["duration_ticks"],
                            "sockets": deepcopy(sample["sockets"]), "draw": ordered,
                            "hidden": sorted(hidden), "suppressed": dict(sorted(suppressed.items()))})
        clips.append({"id": clip["id"], "state": state, "view": clip["view"], "timebase": clip["timebase"],
                      "loop": clip["loop"], "samples": samples})
    plan = {"kind": "parts-render-plan", "schema_version": 1, "appearance": contract.reference(appearance),
            "rig": contract.reference(rig), "motion": contract.reference(motion), "topology": contract.reference(topology),
            "catalog": contract.reference(catalog), "canvas": rig["canvas"], "mirror": appearance["mirror"],
            "render_policy": RENDER_POLICY, "palette": palette, "clips": clips}
    if covering:
        plan["covers"] = covering
    plan["digest"] = contract.content_digest(plan)
    return plan
