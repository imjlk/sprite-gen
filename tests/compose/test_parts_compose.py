# SPDX-License-Identifier: Apache-2.0
from copy import deepcopy
from itertools import product

from PIL import Image
import pytest

from parts_helpers import composition_fixture
from sprite_gen.compose import compose_parts as renderer, parts_resolve
from sprite_gen.spec import parts_assets as assets, parts_contract as contract


def mutate(docs, identity, callback):
    old = next(d for d in docs if d["id"] == identity)
    changed = deepcopy(old)
    changed.pop("digest")
    callback(changed)
    changed = contract.seal(changed)
    # Recursively update references and digest downstream in declaration order.
    replacements = {identity: (contract.reference(old), contract.reference(changed))}
    def swap(value):
        if isinstance(value, dict):
            if set(value) == {"id", "digest"} and value["id"] in replacements:
                return replacements[value["id"]][1]
            return {k: swap(v) for k, v in value.items()}
        if isinstance(value, list): return [swap(v) for v in value]
        return value
    result = []
    for doc in docs:
        if doc["id"] == identity:
            result.append(changed)
        else:
            raw = swap({k: v for k, v in doc.items() if k != "digest"})
            new = contract.seal(raw)
            if new["digest"] != doc["digest"]:
                replacements[doc["id"]] = (contract.reference(doc), contract.reference(new))
            result.append(new)
    docs[:] = result


def add_part(docs, images, identity, slot, node, *, provides=None, replaces=None, color="red", pieces=None):
    rig, motion = docs[1:3]
    image = Image.new("RGBA", (3, 3), color)
    ref = {"file": "part.png", "sha256": assets.sha256(assets.png_bytes(image))}
    images[(identity, ref["file"])] = image
    socket = next(s["id"] for s in docs[0]["sockets"] if s["id"] in next(s for s in docs[0]["slots"] if s["id"] == slot)["sockets"])
    base = next((p for p in docs[3]["bindings"][0]["pieces"] if p["node"] == node), docs[3]["bindings"][0]["pieces"][0])
    piece = {"id": "part", "slot": slot, "node": node, "mode": "held",
       "provides": [node] if provides is None else provides, "replaces": [node] if replaces is None else replaces,
       "sample_map": {c["id"]: {s["id"]: {"image": ref, "pivot": [1, 1], "target_socket": socket,
         "offset": deepcopy(base["sample_map"][c["id"]][s["id"]]["offset"])} for s in c["samples"]} for c in motion["clips"]}}
    pack = contract.seal({"kind": "parts-pack", "schema_version": 1, "id": identity, "design_id": identity,
       "bindings": [{"id": "binding", "rig": contract.reference(rig), "motion": contract.reference(motion),
       "view": rig["view"], "occupies": [slot], "pieces": pieces or [piece]}]})
    docs.insert(-2, pack)
    mutate(docs, docs[-2]["id"], lambda d: next(s for s in d["slots"] if s["id"] == slot)["options"].append(contract.reference(pack)))
    mutate(docs, docs[-1]["id"], lambda d: d["selections"].update({slot: contract.reference(pack)}))
    return pack["id"]


def plan(docs):
    return parts_resolve.resolve(docs[-1], docs)


def render(docs, images, clip=0, sample=0):
    resolved = plan(docs)
    return renderer.render_sample(resolved, resolved["clips"][clip]["samples"][sample], lambda pack, ref: images[(pack["id"], ref["file"])].copy())


@pytest.mark.parametrize("preset", ["human.sd.v1", "human.tall.v1", "canine.medium.v1", "blob.fixture.v1", "asymmetric.six-limb.v1"])
def test_all_body_shapes_use_same_timeline_and_preserve_variable_repeats(preset):
    docs, images = composition_fixture(preset)
    resolved = plan(docs)
    before = deepcopy(docs)
    payloads, clips = renderer.render_plan(resolved, lambda pack, ref: images[(pack["id"], ref["file"])].copy())
    assert [c["state"] for c in clips] == ["idle", "walk", "run"]
    assert [[s["duration_ticks"] for s in c["samples"]] for c in clips] == [[250, 125, 250]] * 3
    assert clips[0]["samples"][0]["image"] == clips[0]["samples"][2]["image"]
    assert len(clips[0]["samples"]) == 3 and len(payloads) == 2
    assert docs == before
    assert assets.image_bytes(next(iter(payloads.values()))).size == (docs[1]["canvas"]["width"], docs[1]["canvas"]["height"])


def test_quadruped_tail_replaces_old_pixels_and_orders_near_far_feet():
    docs, images = composition_fixture("canine.medium.v1")
    add_part(docs, images, "tail.b", "tail", "tail")
    def ordering(d):
        for piece in d["bindings"][0]["pieces"]:
            if piece["node"] == "foot_fl":
                for mapping in piece["sample_map"].values():
                    for p in mapping.values(): p["after"] = ["foot_fr"]
    mutate(docs, "fixture.body", ordering)
    sample = plan(docs)["clips"][0]["samples"][0]
    ids = [e["id"] for e in sample["draw"]]
    assert "fixture.body.tail" not in ids and "tail.b.part" in ids
    assert ids.index("fixture.body.foot_fr") < ids.index("fixture.body.foot_fl")
    output, evidence = render(docs, images)
    tail = next(e for e in evidence["draw"] if e["id"] == "tail.b.part")
    assert output.getpixel(tuple(tail["offset"])) == (255, 0, 0, 255)


def test_body_hands_can_draw_after_sleeve_and_hair_can_surround_body():
    docs, images = composition_fixture()
    add_part(docs, images, "coat", "upper", "arm_r")
    def order(d):
        for piece in d["bindings"][0]["pieces"]:
            for samples in piece["sample_map"].values():
                for p in samples.values():
                    if piece["node"] == "hand_r": p["after"] = ["arm_r"]
                    if piece["node"] == "hair_back": p["before"] = ["head", "torso"]
                    if piece["node"] == "hair_front": p["after"] = ["head"]
    mutate(docs, "fixture.body", order)
    ids = [e["node"] for e in plan(docs)["clips"][0]["samples"][0]["draw"]]
    assert ids.index("arm_r") < ids.index("hand_r")
    assert ids.index("hair_back") < ids.index("head") < ids.index("hair_front")
    render(docs, images)


def test_mouth_override_keeps_expression_eyes():
    docs, images = composition_fixture()
    add_part(docs, images, "expression.a", "expression", "head", provides=["mouth"], replaces=["mouth"])
    first = deepcopy(next(d for d in docs if d["id"] == "expression.a")["bindings"][0]["pieces"][0])
    first.update(id="eyes", provides=["eye_r"], replaces=["eye_r"])
    mutate(docs, "expression.a", lambda d: d["bindings"][0]["pieces"].append(first))
    add_part(docs, images, "mouth.b", "mouth", "mouth")
    ids = [e["id"] for e in plan(docs)["clips"][0]["samples"][0]["draw"]]
    assert "expression.a.part" not in ids and "expression.a.eyes" in ids and "mouth.b.part" in ids


def test_order_cycle_unknown_and_ambiguous_targets_fail():
    docs, _ = composition_fixture()
    def order(d):
        for p in d["bindings"][0]["pieces"][:2]:
            for mapping in p["sample_map"].values():
                for placement in mapping.values(): placement["after"] = ["torso" if p["node"] == "root" else "root"]
    mutate(docs, "fixture.body", order)
    with pytest.raises(contract.PartsContractError, match="cycle"): plan(docs)
    mutate(docs, "fixture.body", lambda d: d["bindings"][0]["pieces"][0]["sample_map"]["idle.right"]["sample.0"].update(after=["typo"]))
    with pytest.raises(contract.PartsContractError, match="unknown draw"): plan(docs)


def test_partial_multinode_replacement_and_cross_pack_conflict_fail():
    docs, images = composition_fixture()
    mutate(docs, "fixture.body", lambda d: d["bindings"][0]["pieces"][2]["provides"].append("mouth"))
    add_part(docs, images, "mouth.b", "mouth", "mouth")
    with pytest.raises(contract.PartsContractError, match="indivisible"): plan(docs)
    docs, images = composition_fixture()
    add_part(docs, images, "hat", "headwear", "head")
    add_part(docs, images, "glasses", "eyewear", "head")
    with pytest.raises(contract.PartsContractError, match="duplicate providers"): plan(docs)


def test_missing_state_or_held_sample_fails_and_visibility_is_explicit():
    docs, images = composition_fixture()
    mutate(docs, "fixture.appearance", lambda d: d["states"].append("jump"))
    with pytest.raises(contract.PartsContractError, match="states"): plan(docs)
    docs, images = composition_fixture()
    mutate(docs, "fixture.body", lambda d: d["bindings"][0]["pieces"][1]["sample_map"]["run.right"].pop("sample.1"))
    with pytest.raises(contract.PartsContractError, match="missing coverage"): plan(docs)
    docs, images = composition_fixture()
    mutate(docs, "fixture.body", lambda d: d["bindings"][0]["pieces"][1]["sample_map"]["run.right"]["sample.1"].update(visible=False))
    _, evidence = render(docs, images, 2, 1)
    assert "fixture.body.torso" in evidence["hidden"]


def test_mask_targets_another_piece_in_declared_space_with_integer_rounding():
    docs, images = composition_fixture("blob.fixture.v1")
    add_part(docs, images, "ornament", "ornament", "root", provides=[], replaces=[], color=(0, 0, 0, 0))
    mask = Image.new("RGBA", (96, 96), (128, 128, 128, 255))
    ref = {"file": "mask.png", "sha256": assets.sha256(assets.png_bytes(mask))}
    images[("ornament", "mask.png")] = mask
    mutate(docs, "ornament", lambda d: [p.update(mask={"image": ref, "target_node": "eye_l", "space": "rig-canvas"})
         for m in d["bindings"][0]["pieces"][0]["sample_map"].values() for p in m.values()])
    output, evidence = render(docs, images)
    target = next(e for e in evidence["draw"] if e["node"] == "eye_l")
    assert output.getpixel(tuple(target["offset"]))[3] == 128


def test_palette_changes_only_declared_rgb_regions_and_preserves_alpha():
    image = Image.new("RGBA", (2, 1), (20, 40, 60, 127))
    region = Image.new("RGBA", (2, 1), (0, 0, 0, 255))
    region.putpixel((0, 0), (255, 255, 255, 255))
    output = renderer.recolor(image, {"fabric": region}, {"fabric": [200, 100, 50]})
    assert [output.getpixel((x, 0)) for x in range(2)] == [(200, 100, 50, 127), (20, 40, 60, 127)]
    with pytest.raises(contract.PartsContractError, match="overlap"):
        renderer.recolor(image, {"a": region, "b": region}, {"a": [0, 0, 0], "b": [255, 255, 255]})


def test_mirror_reflects_pixels_pivots_sockets_but_keeps_anatomical_ids():
    docs, images = composition_fixture()
    original, evidence = render(docs, images)
    mutate(docs, "fixture.appearance", lambda d: d.update(mirror=True))
    with pytest.raises(contract.PartsContractError, match="mirror"): plan(docs)
    mutate(docs, docs[1]["id"], lambda d: d.update(mirror_allowed=True))
    mutate(docs, "fixture.body", lambda d: d["bindings"][0].update(mirror_safe=True))
    mirrored, reflected = render(docs, images)
    assert mirrored.tobytes() == original.transpose(Image.Transpose.FLIP_LEFT_RIGHT).tobytes()
    hand = next(e for e in evidence["draw"] if e["node"] == "hand_r")
    flipped = next(e for e in reflected["draw"] if e["node"] == "hand_r")
    assert flipped["offset"][0] == 128 - hand["offset"][0] - hand["size"][0]
    assert flipped["socket_position"][0] == 127 - hand["socket_position"][0]


def test_clipping_fails_unless_explicitly_declared_and_counted():
    docs, images = composition_fixture()
    def offset(d):
        for m in d["bindings"][0]["pieces"][1]["sample_map"].values():
            for p in m.values(): p["offset"] = [-65, 0]
    mutate(docs, "fixture.body", offset)
    with pytest.raises(contract.PartsContractError, match="clip"): render(docs, images)
    mutate(docs, "fixture.body", lambda d: [p.update(allow_clip=True) for m in d["bindings"][0]["pieces"][1]["sample_map"].values() for p in m.values()])
    _, evidence = render(docs, images)
    assert next(e for e in evidence["draw"] if e["node"] == "torso")["clipped_pixels"] == 6


def test_sd_five_slots_two_variants_make_32_distinct_appearances_and_96_clips():
    docs, images = composition_fixture()
    slots = [("hair", "hair_front"), ("expression", "head"), ("upper", "torso"), ("lower", "leg_l"), ("footwear", "foot_l")]
    choices = {}
    for slot, node in slots:
        choices[slot] = [add_part(docs, images, f"{slot}.{v}", slot, node, color=color) for v, color in enumerate(["red", "blue"])]
    hashes, total = set(), 0
    for bits in product([0, 1], repeat=5):
        mutate(docs, "fixture.appearance", lambda d: d["selections"].update({slot: contract.reference(next(p for p in docs if p["id"] == choices[slot][bit])) for (slot, _), bit in zip(slots, bits)}))
        resolved = plan(docs)
        payloads, clips = renderer.render_plan(resolved, lambda pack, ref: images[(pack["id"], ref["file"])].copy())
        hashes.add(clips[0]["samples"][0]["rgba_sha256"])
        total += len(clips)
    assert len(hashes) == 32 and total == 96
