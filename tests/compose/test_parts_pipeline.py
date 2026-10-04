# SPDX-License-Identifier: Apache-2.0
from copy import deepcopy
import json
import subprocess
import sys

from PIL import Image
import pytest

from parts_helpers import frozen_workspace, part_spec, tree_hashes
from sprite_gen.compose import compose_parts
from sprite_gen.curate import parts_appearance
from sprite_gen.frames import parts_import
from sprite_gen.spec import parts_contract as contract, parts_workspace as workspace


def populated_workspace(tmp_path, *, states=("walk",)):
    assembly = frozen_workspace(tmp_path, states=states)
    index, registry = workspace.read_workspace(assembly)
    topology = registry[registry[index["rig"]["id"]]["topology"]["id"]]
    source = tmp_path / "body-source"
    source.mkdir()
    Image.new("RGBA", (3, 3), "green").save(source / "piece.png")
    spec = part_spec(tmp_path / "body.json", assembly, source, role="body", slot=None, node="root", socket="root", provides=["root"], pivot=(1, 1))
    request = json.loads(spec.read_bytes())
    first = request["pieces"][0]
    request["pieces"] = []
    for i, node in enumerate(topology["nodes"]):
        piece = deepcopy(first)
        piece.update(id=node["id"], node=node["id"], provides=[node["id"]])
        for mapping in piece["sample_map"].values():
            for placement in mapping.values(): placement["offset"] = [(i % 6) * 5 - 15, (i // 6) * 5 - 10]
        request["pieces"].append(piece)
    spec.write_bytes(workspace.json_bytes(request))
    parts_import.import_pack(assembly, spec, source)
    selection = tmp_path / "selection.json"
    selection.write_bytes(workspace.json_bytes({"kind": "parts-appearance-selection", "schema_version": 1, "id": "hero"}))
    return assembly, selection, source


def test_cli_selection_preview_determinism_and_read_only_sources(tmp_path):
    assembly, selection, source = populated_workspace(tmp_path)
    before = tree_hashes(source)
    command = [sys.executable, "-m", "sprite_gen.cli"]
    for verb, options in [("parts-appearance", ["--spec", str(selection)]), ("parts-preview", [])]:
        result = subprocess.run([*command, verb, "--assembly", str(assembly), *options], text=True, capture_output=True)
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout)["release_ready"] is False
    first = compose_parts.preview(assembly)
    assert first == compose_parts.preview(assembly)
    report = workspace.read_json(assembly.parent, first["preview"])
    assert [s["duration_ticks"] for s in report["clips"][0]["samples"]] == [30000, 15000, 30000]
    assert tree_hashes(source) == before
    index, registry = workspace.read_workspace(assembly, complete=True)
    piece = registry[index["body"][0]["id"]]["bindings"][0]["pieces"][0]
    ref = piece["sample_map"]["walk.right"]["sample.0"]["image"]
    image_path = (assembly.parent / index["documents"][index["body"][0]["id"]]).parent / ref["file"]
    image_path.write_bytes(b"corrupted")
    with pytest.raises(contract.PartsContractError, match="hash mismatch"):
        compose_parts.preview(assembly)


def test_selection_rejects_incomplete_body_before_activation(tmp_path):
    assembly = frozen_workspace(tmp_path)
    selection = tmp_path / "selection.json"
    selection.write_bytes(workspace.json_bytes({"kind": "parts-appearance-selection", "schema_version": 1, "id": "hero"}))
    before = assembly.read_bytes()
    with pytest.raises(contract.PartsContractError, match="required slot|explicit body"):
        parts_appearance.create(assembly, selection)
    assert assembly.read_bytes() == before
