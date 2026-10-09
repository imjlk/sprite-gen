# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path
from copy import deepcopy
from PIL import Image
import pytest
from test_parts_production import setup
from test_parts_pose_sheet import drawn_sheet
from sprite_gen.frames import (
    parts_production as p,
    parts_part_edit as edit,
    parts_body_refine as refine,
)
from sprite_gen.spec import parts_workspace as w, parts_contract as contract
from sprite_gen.curate import parts_motion


def ready(tmp_path, mask=False):
    assembly = setup(tmp_path)
    cfg = json.loads((tmp_path / "badge.json").read_text())
    cfg["cut"]["method"] = "part-only"
    Image.new("L", (128, 128), 255).save(assembly.parent / "occluded.png")
    cfg["pose_checks"] = {"walk.01": {"occluded": "occluded.png"}}
    if mask:
        cfg["visibility"] = "mask"
    path = tmp_path / "direct.json"
    path.write_text(json.dumps(cfg))
    p.configure(assembly, "test.badge", path)
    made = p.sheet(assembly, "test.badge")
    for page in made["pages"]:
        drawn_sheet(
            json.loads((assembly.parent / page["guide"]).read_text()),
            assembly.parent / page["art"],
        )
    p.slice_batch(assembly, "test.badge")
    p.check(assembly, "test.badge")
    p.build(assembly, "test.badge")
    return assembly


def test_default_build_keeps_raw_pixels_and_mask_is_explicit(tmp_path):
    assembly = ready(tmp_path)
    root, index, registry, _ = parts_motion._active(assembly)
    state = p._load(root, "test.badge")
    assert (
        state["config"]["visibility"] == "zmap"
        and state["built"]["visibility"] == "zmap"
    )
    placement = registry["test.badge"]["bindings"][0]["pieces"][0]["sample_map"][
        "walk.right"
    ]["sample.0"]
    image = p.compose_parts.AssetLoader(root, index)(
        registry["test.badge"], placement["image"]
    )
    assert image.getpixel((36, 42)) == (200, 40, 40, 255)
    cfg = state["config"]
    cfg["visibility"] = "mask"
    path = tmp_path / "mask.json"
    path.write_text(json.dumps(cfg))
    p.configure(assembly, "test.badge", path)
    assert not p.status(assembly)["productions"][0]["built_current"]
    p.build(assembly, "test.badge")
    root, index, registry, _ = parts_motion._active(assembly)
    placement = registry["test.badge"]["bindings"][0]["pieces"][0]["sample_map"][
        "walk.right"
    ]["sample.0"]
    assert (
        p.compose_parts.AssetLoader(root, index)(
            registry["test.badge"], placement["image"]
        ).getbbox()
        is None
    )


def test_programmatic_edit_is_unselected_precise_and_restorable(tmp_path):
    assembly = ready(tmp_path)
    state = p._load(assembly.parent, "test.badge")
    before = state["tracks"]["main"]["poses"]["walk.01"]["selected"]
    erased = edit.edit(
        assembly,
        "test.badge",
        "walk.01",
        [{"type": "erase", "bounds": [36, 42, 37, 43]}],
    )
    assert (
        erased["changed_pixels"] == 1
        and erased["provider_calls"] == 0
        and not erased["selected"]
    )
    assert (
        p._load(assembly.parent, "test.badge")["tracks"]["main"]["poses"]["walk.01"][
            "selected"
        ]
        == before
    )
    im = Image.open(assembly.parent / erased["preview_candidate"] / "part.png")
    assert im.getpixel((36, 42))[3] == 0 and im.getpixel((37, 42)) == (200, 40, 40, 255)
    restored = edit.edit(
        assembly,
        "test.badge",
        "walk.01",
        [{"type": "restore-source", "bounds": [36, 42, 37, 43]}],
        candidate=erased["preview_candidate"],
    )
    assert Image.open(
        assembly.parent / restored["preview_candidate"] / "part.png"
    ).getpixel((36, 42)) == (200, 40, 40, 255)
    p.select_candidate(assembly, "test.badge", "walk.01", erased["preview_candidate"])
    p.check(assembly, "test.badge")
    p.build(assembly, "test.badge")
    assert (
        p._load(assembly.parent, "test.badge")["tracks"]["main"]["poses"]["walk.01"][
            "decision"
        ]
        == "unverified"
    )


def test_translate_recolor_and_invalid_edits(tmp_path):
    assembly = ready(tmp_path)
    result = edit.edit(
        assembly,
        "test.badge",
        "walk.01",
        [
            {"type": "translate", "dx": 1},
            {"type": "recolor", "from_color": "#c82828", "to_color": "#00ff00"},
        ],
    )
    im = Image.open(assembly.parent / result["preview_candidate"] / "part.png")
    assert im.getpixel((37, 42)) == (0, 255, 0, 255) and im.getpixel((36, 42))[3] == 0
    for op in (
        {"type": "erase"},
        {"type": "restore-source"},
        {"type": "translate", "dx": 128},
        {"type": "translate", "dx": 0.5},
        {"type": "erase", "bounds": [0, 0, 500, 500]},
        {"type": "translate"},
    ):
        with pytest.raises(contract.PartsContractError):
            edit.edit(assembly, "test.badge", "walk.01", [op])


def test_refinement_rejects_incomplete_author_labels_before_changing_workspace(
    tmp_path,
):
    assembly = ready(tmp_path)
    index, registry = w.read_workspace(assembly)
    rig = registry[index["rig"]["id"]]
    topology = deepcopy(registry[rig["topology"]["id"]])
    topology.pop("digest")
    path = tmp_path / "topo.json"
    path.write_text(json.dumps(topology))
    colors = tmp_path / "colors.json"
    colors.write_text(json.dumps({"root": "#ff0000"}))
    folder = tmp_path / "labels"
    folder.mkdir()
    for pose in ("walk.01", "walk.02"):
        Image.new("RGBA", (128, 128)).save(folder / (pose + ".png"))
    before = assembly.read_bytes()
    with pytest.raises(contract.PartsContractError, match="cover each source pixel"):
        refine.refine(
            assembly,
            topology=path,
            labels=folder,
            colors=colors,
            out=tmp_path / "out",
            body_id="test.body",
        )
    assert assembly.read_bytes() == before


def test_refine_revisions_preserve_pixels_sources_and_zmap_qc(tmp_path):
    from sprite_gen._deps import np
    from sprite_gen.compose import compose_parts
    from sprite_gen.frames import parts_reference

    assembly = ready(tmp_path)
    index, registry = w.read_workspace(assembly)
    original = registry[index["motion"]["id"]]
    topology = deepcopy(registry[registry[index["rig"]["id"]]["topology"]["id"]])
    topology.pop("digest")
    topology["nodes"].append({"id": "neck", "parent": "torso", "required": False})
    names = [node["id"] for node in topology["nodes"]]
    topology["layers"] = [name for name in names if name != "head"] + ["head"]
    palette = {name: f"#{(i + 1) * 7919:06x}" for i, name in enumerate(names)}
    labels = tmp_path / "labels"
    labels.mkdir()
    loader = compose_parts.AssetLoader(assembly.parent, index)
    sources = {}
    for sample in original["clips"][0]["samples"]:
        image = loader(original, sample["image"])
        sources[sample["id"]] = image.tobytes()
        rgba = np.asarray(image)
        yy = np.indices(rgba.shape[:2])[0]
        painted = np.zeros_like(rgba)
        for name, zone in (("head", yy < 50), ("root", yy >= 50)):
            painted[zone & (rgba[..., 3] > 0)] = (*p._rgb(palette[name]), 255)
        Image.fromarray(painted).save(labels / (sample["pose_id"] + ".png"))
    topo = tmp_path / "topology.json"
    topo.write_bytes(w.json_bytes(topology))
    colors = tmp_path / "colors.json"
    colors.write_bytes(w.json_bytes(palette))
    result = refine.refine(
        assembly,
        topology=topo,
        labels=labels,
        colors=colors,
        out=assembly.parent / "refined",
        body_id="test.body",
    )
    assert result["pixel_identical_samples"] == 3 and not result["timing_changed"]
    updated, documents = w.read_workspace(assembly)
    motion = documents[updated["motion"]["id"]]
    assert updated["motion"] != index["motion"]
    assert motion["clips"] == original["clips"]
    for sample in motion["clips"][0]["samples"]:
        assert (
            compose_parts.AssetLoader(assembly.parent, updated)(
                motion, sample["image"]
            ).tobytes()
            == sources[sample["id"]]
        )
    # Migration retains the canonical import snapshots required by offline export.
    from sprite_gen.spec import parts_assets

    for identity, document in documents.items():
        source = assembly.parent / Path(updated["documents"][identity]).parent
        for ref in (document.get("import") or {}).get("source_assets", []):
            parts_assets.SourceReader(source).read(ref["file"], ref["sha256"])
    config = p._load(assembly.parent, "test.badge")["config"]
    config["segments"] = "refined/segments.json"
    file = tmp_path / "config.json"
    file.write_bytes(w.json_bytes(config))
    p.configure(assembly, "test.badge", file)
    made = p.sheet(assembly, "test.badge", poses=["walk.01", "walk.02"])
    state = p._load(assembly.parent, "test.badge")
    frozen, base = parts_reference.read(
        assembly.parent, state["tracks"]["main"]["batches"][-1]["reference"]
    )
    parts_reference.verify(assembly.parent, frozen, base)
    assert "layers" in frozen["pages"][0]
    assert frozen["poses"]["walk.01"]["body_layers"]["zmap"][-1] == "head"
    p.check(assembly, "test.badge")
    metrics = p._load(assembly.parent, "test.badge")["tracks"]["main"]["poses"][
        "walk.01"
    ]["check"]["metrics"]
    assert metrics["inspection_space"] == "zmap-visible"
    assert (
        metrics["occluded_pixels"] == metrics["pixels"]
        and metrics["visible_pixels"] == 0
    )
    p.build(assembly, "test.badge")
    p.review(assembly, "test.badge", reviewer="fixture", approve=["walk.01"])
    # Ownership can change without a topology revision. Previous QC and visual
    # approval must still become stale.
    for pose in ("walk.01", "walk.02"):
        label_file = labels / (pose + ".png")
        painted = Image.open(label_file).convert("RGBA")
        painted.putpixel((40, 48), (*p._rgb(palette["root"]), 255))
        painted.save(label_file)
    refine.refine(
        assembly,
        topology=topo,
        labels=labels,
        colors=colors,
        out=assembly.parent / "refined",
        body_id="test.body",
    )
    row = p._load(assembly.parent, "test.badge")["tracks"]["main"]["poses"]["walk.01"]
    assert row["decision"] == "unverified" and row["check"] is None
    # A missing required pixel outside the body's occlusion must still fail.
    region = Image.new("L", (128, 128))
    region.putpixel((110, 110), 255)
    region.save(assembly.parent / "required.png")
    config["pose_checks"] = {"walk.01": {"required": "required.png"}}
    file.write_bytes(w.json_bytes(config))
    p.configure(assembly, "test.badge", file)
    made = p.sheet(assembly, "test.badge", poses=["walk.01", "walk.02"])
    for page in made["pages"]:
        drawn_sheet(
            json.loads((assembly.parent / page["guide"]).read_text()),
            assembly.parent / page["art"],
        )
    p.slice_batch(assembly, "test.badge")
    p.check(assembly, "test.badge")
    metrics = p._load(assembly.parent, "test.badge")["tracks"]["main"]["poses"][
        "walk.01"
    ]["check"]["metrics"]
    assert metrics["required_pixels"] == 1
