# SPDX-License-Identifier: Apache-2.0
"""Small deterministic inputs for the parts pipeline; no generation providers."""
from copy import deepcopy
import json
from pathlib import Path

from PIL import Image, ImageDraw

from sprite_gen.spec import parts_assets as assets, parts_contract as contract, parts_workspace as workspace


def workspace_at(path, *, preset="human.sd.v1", size=None):
    workspace.initialize(path, preset)
    index, registry = workspace.read_workspace(path / "assembly.json")
    if size is not None:
        rig = registry[index["rig"]["id"]]
        rig.pop("digest")
        rig["canvas"] = {"width": size[0], "height": size[1]}
        rig = contract.seal(rig)
        catalog = registry[index["catalog"]["id"]]
        catalog.pop("digest")
        catalog["rig"] = contract.reference(rig)
        catalog = contract.seal(catalog)
        index.update(rig=contract.reference(rig), catalog=contract.reference(catalog))
        (path / "profiles/rig.json").write_bytes(workspace.json_bytes(rig))
        (path / "catalog.json").write_bytes(workspace.json_bytes(catalog))
        (path / "assembly.json").write_bytes(workspace.json_bytes(index))
    return path / "assembly.json"


def frame(size=(128, 128), phase=0):
    image = Image.new("RGBA", size)
    draw = ImageDraw.Draw(image)
    draw.rectangle((size[0] // 4 + phase, size[1] // 4, size[0] // 2 + phase, size[1] - 1), fill=(50, 100, 80, 255))
    return image


def external_source(root, *, size=(128, 128), durations=("0.25", "0.125", "0.25"), order=(0, 1, 0)):
    root.mkdir(parents=True, exist_ok=True)
    for phase in sorted(set(order)):
        frame(size, phase).save(root / f"pose-{phase}.png")
    # Write decimal lexemes exactly, including precision the float API discards.
    entries = [f'{{"file":"pose-{pose}.png","duration":{duration},"id":"source.{i}"}}'
               for i, (pose, duration) in enumerate(zip(order, durations))]
    (root / "motion.json").write_text('{"kind":"sprite-gen-asset","version":1,"frames":[' + ','.join(entries) + ']}')
    return "motion.json"


def source_request(root, filename="motion.json", **overrides):
    request = {"id": "walk.right", "state": "walk", "file": filename, "source_state": None,
               "timebase": 120000, "loop": None, "fps": None, "timing_policy": "exact",
               "source_mode": "external", "expected_files": {
                   p.relative_to(root).as_posix(): assets.sha256(p.read_bytes())
                   for p in sorted(root.rglob("*")) if p.is_file()}}
    request.update(overrides)
    return request


def import_spec(path, assembly, clips, identity="test.motion"):
    index, _ = workspace.read_workspace(assembly)
    value = {"kind": "parts-motion-import", "schema_version": 1, "id": identity,
             "rig": index["rig"], "clips": clips}
    path.write_bytes(workspace.json_bytes(value))
    return path


def verified_annotations(root, result, *, point=(64, 64)):
    path = root / result["annotation_template"]
    value = json.loads(path.read_text())
    for clip in value["clips"].values():
        for sample in clip.values():
            for socket in sample["sockets"].values():
                socket.update(position=list(point), visible=True, confidence="verified")
    path.write_bytes(workspace.json_bytes(value))
    return path


def tree_hashes(root):
    return {p.relative_to(root).as_posix(): assets.sha256(p.read_bytes()) for p in root.rglob("*") if p.is_file()}


def frozen_workspace(base, *, preset="human.sd.v1"):
    from sprite_gen.curate import parts_motion
    base.mkdir(parents=True, exist_ok=True)
    root, source = base / "library", base / "motion-source"
    assembly = workspace_at(root, preset=preset)
    index, registry = workspace.read_workspace(assembly)
    rig = registry[index["rig"]["id"]]
    size = (rig["canvas"]["width"], rig["canvas"]["height"])
    index["states"] = ["walk"]
    assembly.write_bytes(workspace.json_bytes(index))
    external_source(source, size=size)
    spec = import_spec(base / "motion-import.json", assembly, [source_request(source)])
    imported = parts_motion.import_motion(assembly, spec, source)
    annotations = verified_annotations(root, imported, point=(size[0] // 2, size[1] // 2))
    parts_motion.freeze_motion(assembly, imported["candidate"], annotations)
    return assembly


def part_spec(path, assembly, source_root, *, slot="hair", node="hair_front", socket="head",
              identity="test.part", role="part", format="local", pivot=(2, 2), crop_offset=(0, 0),
              provides=(), replaces=(), body_mode="segmented"):
    index, registry = workspace.read_workspace(assembly)
    motion = registry[index["motion"]["id"]]
    rig = registry[index["rig"]["id"]]
    ref = {"file": "piece.png", "sha256": assets.sha256((source_root / "piece.png").read_bytes())}
    value = {"kind": "parts-pack-import", "schema_version": 1, "id": identity, "design_id": "test-design",
             "role": role, "body_mode": body_mode, "style": rig["style"], "rig": index["rig"], "motion": index["motion"],
             "view": rig["view"], "binding_id": "test.binding", "occupies": [slot] if slot is not None else [],
             "pieces": [{"id": "piece", "slot": slot, "node": node, "provides": list(provides), "replaces": list(replaces),
                         "mode": "held", "sample_map": {clip["id"]: {sample["id"]: {
                             "image": ref, "format": format, "crop_offset": list(crop_offset), "pivot": list(pivot), "target_socket": socket
                         } for sample in clip["samples"]} for clip in motion["clips"]}}]}
    path.write_bytes(workspace.json_bytes(value))
    return path


def composition_fixture(preset="human.sd.v1", *, states=("idle", "walk", "run")):
    """Sealed synthetic declarations + actual tiny rasters; not reviewed artwork."""
    docs, _ = workspace.initial_documents(preset)
    topology, rig, catalog = docs
    center = [rig["canvas"]["width"] // 2, rig["canvas"]["height"] // 2]
    motion = contract.seal({"kind": "parts-motion", "schema_version": 1, "id": "fixture.motion",
        "rig": contract.reference(rig), "freeze": {"candidate_digest": "a" * 64,
        "annotations": {"file": "synthetic-annotations.json", "sha256": "b" * 64},
        "source_assets": [], "encoding_policy": "rgba-png.v1"}, "clips": [
        {"id": state + ".right", "state": state, "view": rig["view"], "timebase": 1000,
         "loop": state != "pulse", "samples": [{"id": f"sample.{i}",
           "image": {"file": "synthetic-source.png", "sha256": "c" * 64}, "duration_ticks": t,
           "sockets": {s["id"]: {"position": [center[0] + (i % 2), center[1]],
             "visible": True, "confidence": "verified"} for s in topology["sockets"]}}
          for i, t in enumerate([250, 125, 250])]} for state in states]})
    images = {}
    pieces = []
    for i, node in enumerate(topology["nodes"]):
        name = node["id"]
        image = Image.new("RGBA", (3, 3), ((i * 17) % 256, (i * 31) % 256, (i * 43) % 256, 255))
        if name == "root": image = Image.new("RGBA", (1, 1))
        data = assets.png_bytes(image)
        ref = {"file": name + ".png", "sha256": assets.sha256(data)}
        images[("fixture.body", ref["file"])] = image
        pieces.append({"id": name, "slot": None, "node": name, "mode": "held", "provides": [name],
           "sample_map": {c["id"]: {s["id"]: {"image": ref, "pivot": [1, 1], "target_socket": "root",
             "offset": [(i % 6) * 6 - 18, (i // 6) * 6 - 12]} for s in c["samples"]} for c in motion["clips"]}})
    body = contract.seal({"kind": "parts-pack", "schema_version": 1, "id": "fixture.body", "design_id": "synthetic",
        "role": "body", "bindings": [{"id": "fixture.body.binding", "rig": contract.reference(rig),
        "motion": contract.reference(motion), "view": rig["view"], "occupies": [], "pieces": pieces}]})
    appearance = contract.seal({"kind": "parts-appearance", "schema_version": 1, "id": "fixture.appearance",
       "rig": contract.reference(rig), "motion": contract.reference(motion), "catalog": contract.reference(catalog),
       "states": list(states), "body": [contract.reference(body)]})
    return [topology, rig, motion, body, catalog, appearance], images
