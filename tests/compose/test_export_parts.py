# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path
import shutil
import subprocess

from PIL import Image
import pytest

from parts_helpers import external_source, import_spec, source_request, verified_annotations, part_spec, tree_hashes
from sprite_gen.compose import export_parts, compose_parts
from sprite_gen.compose.parts_runtime import PartsPlayer
from sprite_gen.curate import parts_motion, parts_appearance
from sprite_gen.frames import parts_import
from sprite_gen.spec import parts_contract as contract, parts_workspace as workspace


def flat_workspace(tmp_path, *, states=("walk", "pulse"), layered=False, body_mode="flat"):
    """A one-node body with badge choices; `layered` adds a draw table and a cap that covers the badge."""
    root = tmp_path / "library"
    workspace.initialize(root, "human.sd.v1")
    slots = [{"id": "badge", "nodes": ["badge"], "sockets": ["root"], "optional": True}]
    if layered:
        slots.append({"id": "cap", "nodes": ["badge"], "sockets": ["root"], "optional": True})
    topology = contract.seal({"kind": "parts-topology", "schema_version": 1, "id": "generic.topology",
        "nodes": [{"id": "root"}, {"id": "badge", "parent": "root", "required": False}],
        "sockets": [{"id": "root", "node": "root", "required": True}], "slots": slots,
        **({"layers": ["badge", "root"]} if layered else {})})
    rig = contract.seal({"kind": "parts-rig", "schema_version": 1, "id": "generic.rig",
        "topology": contract.reference(topology), "view": "right", "style": "fixture", "canvas": {"width": 32, "height": 32}})
    catalog = contract.seal({"kind": "parts-catalog", "schema_version": 1, "id": "generic.catalog",
        "rig": contract.reference(rig), "topology": contract.reference(topology), "groups": [{"id": "gear", "label": "장비"}],
        "slots": [{"id": "badge", "group": "gear", "label": "장식"}] +
                 ([{"id": "cap", "group": "gear", "label": "모자"}] if layered else [])})
    docs = {topology["id"]: "profiles/topology.json", rig["id"]: "profiles/rig.json", catalog["id"]: "catalog.json"}
    for doc in (topology, rig, catalog):
        (root / docs[doc["id"]]).write_bytes(workspace.json_bytes(doc))
    assembly = root / "assembly.json"
    assembly.write_bytes(workspace.json_bytes({"kind": "parts-workspace", "schema_version": 1, "id": "generic.workspace",
        "documents": docs, "rig": contract.reference(rig), "catalog": contract.reference(catalog), "states": list(states)}))
    source = tmp_path / "source"
    external_source(source, size=(32, 32))
    spec = import_spec(tmp_path / "motion.json", assembly, [source_request(source, id=state, state=state, loop=state=="walk") for state in ("walk", "pulse")])
    result = parts_motion.import_motion(assembly, spec, source)
    parts_motion.freeze_motion(assembly, result["candidate"], verified_annotations(root, result, point=(16, 16)))
    Image.new("RGBA", (32, 32), "green").save(source / "piece.png")
    spec = part_spec(tmp_path / "body.json", assembly, source, slot=None, node="root", socket="root", role="body",
                     identity="generic.body", provides=("root",), body_mode=body_mode, pivot=(16, 16), format="full-canvas")
    parts_import.import_pack(assembly, spec, source)
    for suffix, color in (("a", "red"), ("b", "blue")) + ((("c", "white"),) if layered else ()):
        Image.new("RGBA", (3, 3), color).save(source / "piece.png")
        slot = "cap" if suffix == "c" else "badge"
        spec = part_spec(tmp_path / (suffix + ".json"), assembly, source, slot=slot, node="badge", socket="root",
                         identity=f"{slot}.{suffix}", provides=() if layered else ("badge",), pivot=(1, 1))
        data = json.loads(spec.read_bytes())
        for mapping in data["pieces"][0]["sample_map"].values():
            for i, row in enumerate(mapping.values()):
                row["offset"] = [i, 0]
                if not layered:
                    row["after"] = ["root"]
        if layered and suffix == "c":
            data["covers"] = ["cap"]
        elif layered and suffix == "a":
            # The badge has a plain and a capped drawing; the cap decides which one shows.
            capped = json.loads(json.dumps(data["pieces"][0]))
            for mapping in capped["sample_map"].values():
                for row in mapping.values():
                    row["offset"] = [row["offset"][0], 2]
            data["pieces"] = [{**data["pieces"][0], "cover_rule": {"tag": "cap", "show": "uncovered"}},
                              {**capped, "id": "capped", "cover_rule": {"tag": "cap", "show": "covered"}}]
        spec.write_bytes(workspace.json_bytes(data))
        parts_import.import_pack(assembly, spec, source)
    index, registry = workspace.read_workspace(assembly)
    selection = tmp_path / "selection.json"
    selection.write_bytes(workspace.json_bytes({"kind": "parts-appearance-selection", "schema_version": 1,
        "id": "generic.character", "selections": {"badge": contract.reference(registry["badge.a"])}}))
    parts_appearance.create(assembly, selection)
    return assembly


def test_export_contains_shared_textures_and_an_offline_preview_without_baked_outfits(tmp_path):
    assembly = flat_workspace(tmp_path)
    before = tree_hashes(assembly.parent)
    first = export_parts.export(assembly, tmp_path / "export", title="Generic character")
    assert first == export_parts.export(assembly, tmp_path / "export", title="Generic character")
    folder = Path(first["directory"])
    manifest = json.loads((folder / "manifest.json").read_bytes())
    assert len(manifest["clips"]) == 2 and len(manifest["packs"]) == 3
    assert len(manifest["textures"]) == 3
    assert manifest["appearance_clips_baked"] == 0 and manifest["release_ready"] is False
    assert manifest["fit_summary"]["status"] == "unverified"
    assert not any(p.name == "atlas.png" or p.suffix in {".mp4", ".gif"} for p in folder.rglob("*"))
    html = (folder / "preview.html").read_text()
    assert 'id="parts-data"' in html and "data:image/png;base64," in html
    assert '<script src="/runtime.js">' not in html and 'href="/player.css"' not in html
    assert tree_hashes(assembly.parent) == before
    relocated = tmp_path / "relocated"
    shutil.copytree(assembly.parent, relocated)
    second = export_parts.export(relocated / "assembly.json", tmp_path / "other-export", title="Generic character")
    assert tree_hashes(Path(second["directory"])) == tree_hashes(folder)


@pytest.mark.parametrize("interpolation", ["frame", "linear"])
def test_browser_sampler_matches_python_clock_layers_and_variable_duration_boundaries(tmp_path, interpolation):
    if shutil.which("node") is None:
        pytest.skip("Node.js is required to validate the portable browser adapter")
    assembly = flat_workspace(tmp_path)
    root, index, registry, _ = parts_motion._active(assembly)
    _, _, bundle = export_parts.pack(root, index, registry, interpolation=interpolation)
    player = PartsPlayer.from_workspace(assembly)
    cases, expected = [], []
    for state in ("walk", "pulse"):
        for time in (0, 125, 249, 250, 300, 374, 375, 624, 625, 1000):
            for option in ("badge.a", "badge.b", ""):
                cases.append({"state": state, "time": time, "selected": {"badge": option}})
                player.set_selection({"badge": contract.reference(registry[option]) if option else None})
                player.set_state(state)
                from fractions import Fraction
                player.seek(Fraction(time, 1000))
                scene = player.layers(interpolate=interpolation=="linear")
                expected.append({key: scene[key] for key in ("sample_id", "sample_tick", "clip_tick", "elapsed_seconds", "sample_fraction")} |
                    {"layers": [{key: layer[key] for key in ("id", "position", "anchor", "local_origin", "tweened")} for layer in scene["layers"]]})
    file = tmp_path / "cases.json"
    file.write_bytes(workspace.json_bytes({"bundle": bundle, "cases": cases}))
    code = "const fs=require('fs');const {LayerRuntime}=require(process.argv[1]);const input=JSON.parse(fs.readFileSync(process.argv[2]));const runtime=new LayerRuntime(input.bundle);const keys=['sample_id','sample_tick','clip_tick','elapsed_seconds','sample_fraction'];process.stdout.write(JSON.stringify(input.cases.map(c=>{const s=runtime.scene(c.state,c.time,c.selected);return {...Object.fromEntries(keys.map(k=>[k,s[k]])),layers:s.layers.map(l=>Object.fromEntries(['id','position','anchor','local_origin','tweened'].map(k=>[k,l[k]])))};})));"
    result = subprocess.run(["node", "-e", code, str(export_parts.STATIC / "runtime.js"), str(file)], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == expected


@pytest.mark.parametrize("interpolation", ["frame", "linear"])
def test_browser_sampler_matches_python_draw_table_and_cover_rules(tmp_path, interpolation):
    if shutil.which("node") is None:
        pytest.skip("Node.js is required to validate the portable browser adapter")
    assembly = flat_workspace(tmp_path, layered=True, body_mode="segmented")
    root, index, registry, _ = parts_motion._active(assembly)
    _, manifest, bundle = export_parts.pack(root, index, registry, interpolation=interpolation)
    assert manifest["layers"] == ["badge", "root"] and manifest["packs"]["cap.c"]["covers"] == ["cap"]
    player = PartsPlayer.from_workspace(assembly)
    cases, expected = [], []
    from fractions import Fraction
    for badge in ("badge.a", "badge.b", ""):
        for cap in ("cap.c", ""):
            for time in (0, 300):
                cases.append({"state": "walk", "time": time, "selected": {"badge": badge, "cap": cap}})
                player.set_selection({"badge": contract.reference(registry[badge]) if badge else None,
                                      "cap": contract.reference(registry[cap]) if cap else None})
                player.set_state("walk")
                player.seek(Fraction(time, 1000))
                scene = player.layers(interpolate=interpolation == "linear")
                expected.append({"ids": [layer["id"] for layer in scene["layers"]],
                                 "positions": [layer["position"] for layer in scene["layers"]]})
    file = tmp_path / "cases.json"
    file.write_bytes(workspace.json_bytes({"bundle": bundle, "cases": cases}))
    code = "const fs=require('fs');const {LayerRuntime}=require(process.argv[1]);const input=JSON.parse(fs.readFileSync(process.argv[2]));const runtime=new LayerRuntime(input.bundle);process.stdout.write(JSON.stringify(input.cases.map(c=>{const s=runtime.scene(c.state,c.time,c.selected);return {ids:s.layers.map(l=>l.id),positions:s.layers.map(l=>l.position)};})));"
    result = subprocess.run(["node", "-e", code, str(export_parts.STATIC / "runtime.js"), str(file)], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == expected
    # The draw table puts the badge layer behind the body; the cap swaps the badge's drawing.
    assert expected[0]["ids"] == ["badge.capped", "cap.piece", "body.piece"]
    assert expected[2]["ids"] == ["badge.piece", "body.piece"]


def test_export_rejects_slot_precedence_instead_of_flattening_it(tmp_path):
    from compose.test_parts_pipeline import populated_workspace
    assembly, selection, _ = populated_workspace(tmp_path)
    parts_appearance.create(assembly, selection)
    with pytest.raises(contract.PartsContractError, match="portable overlay adapter"):
        export_parts.export(assembly, tmp_path / "export")
    assert not (tmp_path / "export").exists()


def test_portable_default_state_follows_the_appearance_instead_of_source_import_order(tmp_path):
    assembly = flat_workspace(tmp_path, states=("pulse", "walk"))
    root, index, registry, _ = parts_motion._active(assembly)
    _, manifest, _ = export_parts.pack(root, index, registry)
    player = PartsPlayer.from_workspace(assembly)
    assert manifest["clips"][0]["state"] == player.layers()["state"] == "pulse"
    assert [clip["state"] for clip in manifest["clips"]] == index["states"]


def test_cross_layer_mask_is_exported_for_the_player_to_apply(tmp_path):
    assembly = flat_workspace(tmp_path)
    source = tmp_path / "source"
    Image.new("RGBA", (3, 3), "yellow").save(source / "piece.png")
    Image.new("L", (3, 3), 0).save(source / "mask.png")
    spec = part_spec(tmp_path / "c.json", assembly, source, slot="badge", node="badge", socket="root",
                     identity="badge.c", provides=("badge",), pivot=(1, 1))
    data = json.loads(spec.read_bytes())
    from sprite_gen.spec import parts_assets
    mask = {"image": {"file": "mask.png", "sha256": parts_assets.sha256((source / "mask.png").read_bytes())},
            "target_node": "root", "space": "part-local", "channel": "luma"}
    for mapping in data["pieces"][0]["sample_map"].values():
        for row in mapping.values():
            row.update(after=["root"], mask=mask)
    spec.write_bytes(workspace.json_bytes(data))
    parts_import.import_pack(assembly, spec, source)
    index, registry = workspace.read_workspace(assembly)
    selection = tmp_path / "selection-c.json"
    selection.write_bytes(workspace.json_bytes({"kind": "parts-appearance-selection", "schema_version": 1,
        "id": "generic.character", "selections": {"badge": contract.reference(registry["badge.c"])}}))
    parts_appearance.create(assembly, selection)
    folder = Path(export_parts.export(assembly, tmp_path / "export", title="Masked")["directory"])
    manifest = json.loads((folder / "manifest.json").read_bytes())
    rows = [row for pack in manifest["packs"].values() for piece in pack["pieces"]
            for mapping in piece["sample_map"].values() for row in mapping.values() if row["mask"]]
    assert rows and all(row["mask"]["target_node"] == "root" for row in rows)
    # The Python renderer hides the body under the badge's zero mask.
    image, _ = PartsPlayer.from_workspace(assembly).frame()
    assert image.getpixel((16, 16))[:3] == (255, 255, 0) and image.getpixel((15, 15))[3] == 255
