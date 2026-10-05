# SPDX-License-Identifier: Apache-2.0
"""Socket proposals from one authored reference region on final candidate rasters."""
import json
import subprocess
import sys

import pytest
from PIL import Image, ImageDraw

from parts_helpers import import_spec, source_request, workspace_at
from sprite_gen.curate import parts_motion, parts_sockets
from sprite_gen.spec import parts_contract as contract, parts_workspace as workspace

OFFSETS = [(0, 0), (1, -1), (2, 0), (1, 1)]


def head_frame(dx, dy, *, stray=False):
    image = Image.new("RGBA", (128, 128))
    draw = ImageDraw.Draw(image)
    draw.rectangle((44, 60, 84, 120), fill=(40, 90, 70, 255))
    draw.ellipse((48+dx, 20+dy, 80+dx, 52+dy), fill=(230, 180, 140, 255))
    draw.rectangle((70+dx, 32+dy, 73+dx, 35+dy), fill=(20, 20, 20, 255))
    if stray:
        # One loose pixel above the head must not pull the socket upward.
        draw.point((64+dx, 16+dy), fill=(120, 70, 40, 255))
    return image


def setup(tmp_path, *, stray=False):
    root, source = tmp_path / "library", tmp_path / "source"
    assembly = workspace_at(root)
    source.mkdir()
    frames = []
    for n, (dx, dy) in enumerate(OFFSETS):
        head_frame(dx, dy, stray=stray and n == 3).save(source / f"pose-{n}.png")
        frames.append(f'{{"file":"pose-{n}.png","duration":0.125}}')
    (source / "motion.json").write_text('{"kind":"sprite-gen-asset","version":1,"frames":[' + ",".join(frames) + "]}")
    spec = import_spec(tmp_path / "import.json", assembly, [source_request(source)])
    imported = parts_motion.import_motion(assembly, spec, source)
    return root, assembly, imported


def track_spec(path, assembly, **head):
    _, registry = workspace.read_workspace(assembly)
    topology = next(d for d in registry.values() if d["kind"] == "parts-topology")
    tracks = [{"socket": "head", "reference": {"clip": "walk.right", "sample": "sample.0", "position": [64, 20]},
               "bounds": [-17, 0, 17, 34], "radius": 3, **head}]
    tracks += [{"socket": s["id"], "mode": "constant",
                "reference": {"clip": "walk.right", "sample": "sample.0", "position": [64, 120]}}
               for s in topology["sockets"] if s["id"] != "head"]
    path.write_text(json.dumps({"kind": "parts-socket-track", "schema_version": 1, "tracks": tracks}))
    return path


def test_track_recovers_integer_offsets_without_editing_rasters_and_stays_unverified(tmp_path):
    root, assembly, imported = setup(tmp_path, stray=True)
    draft = root / imported["annotation_template"]
    before = draft.read_bytes()
    result = parts_sockets.track_sockets(assembly, imported["candidate"], draft, track_spec(tmp_path / "t.json", assembly),
                                         tmp_path / "ann.json", preview=tmp_path / "preview.png")
    head = [row for row in result["results"] if row["socket"] == "head"]
    assert [row["position"] for row in head] == [[64+dx, 20+dy] for dx, dy in OFFSETS]
    assert all(row["status"] == "proposed" and row["confidence"] == "unverified" for row in head)
    assert draft.read_bytes() == before and (tmp_path / "preview.png").is_file()
    assert result["release_ready"] is False and result["unverified_sockets"] > 0
    with pytest.raises(contract.PartsContractError, match="unverified"):
        parts_motion.freeze_motion(assembly, imported["candidate"], tmp_path / "ann.json")


def test_accept_verifies_only_proposals_within_budget_then_freezes(tmp_path):
    root, assembly, imported = setup(tmp_path)
    draft = root / imported["annotation_template"]
    result = parts_sockets.track_sockets(assembly, imported["candidate"], draft,
                                         track_spec(tmp_path / "t.json", assembly, max_cost=0), tmp_path / "ann.json", accept=True)
    assert result["needs_review"] == 0 and result["unverified_sockets"] == 0
    frozen = parts_motion.freeze_motion(assembly, imported["candidate"], tmp_path / "ann.json")
    _, registry = workspace.read_workspace(assembly)
    samples = registry[frozen["motion"]["id"]]["clips"][0]["samples"]
    assert [s["sockets"]["head"]["position"] for s in samples] == [[64+dx, 20+dy] for dx, dy in OFFSETS]


def test_budget_and_search_edge_leave_samples_for_review(tmp_path):
    root, assembly, imported = setup(tmp_path, stray=True)
    draft = root / imported["annotation_template"]
    result = parts_sockets.track_sockets(assembly, imported["candidate"], draft,
        track_spec(tmp_path / "t.json", assembly, max_cost=0, radius=1), tmp_path / "ann.json", accept=True)
    rows = {row["sample"]: row for row in result["results"] if row["socket"] == "head"}
    assert rows["sample.0"]["confidence"] == "verified"
    # A best match on the search boundary may be cut off; it stays for review.
    assert rows["sample.1"]["at_edge"] and rows["sample.1"]["confidence"] == "unverified"
    with pytest.raises(contract.PartsContractError, match="unverified"):
        parts_motion.freeze_motion(assembly, imported["candidate"], tmp_path / "ann.json")


@pytest.mark.parametrize("mutate,match", [
    (lambda t: t[0].update(socket="tail"), "unknown topology socket"),
    (lambda t: t[0].update(bounds=None), "requires bounds"),
    (lambda t: t[0].update(radius=40), "radius"),
    (lambda t: t[0]["reference"].update(sample="sample.9"), "reference sample is missing"),
    (lambda t: t[0]["reference"].update(position=[2, 2]), "transparent"),
    (lambda t: t.append(dict(t[0])), "tracked twice"),
])
def test_invalid_tracks_are_refused(tmp_path, mutate, match):
    root, assembly, imported = setup(tmp_path)
    path = track_spec(tmp_path / "t.json", assembly)
    value = json.loads(path.read_text())
    mutate(value["tracks"])
    path.write_text(json.dumps(value))
    with pytest.raises(contract.PartsContractError, match=match):
        parts_sockets.track_sockets(assembly, imported["candidate"], root / imported["annotation_template"],
                                    path, tmp_path / "ann.json")
    assert not (tmp_path / "ann.json").exists()


def test_stale_draft_raster_hash_is_refused(tmp_path):
    root, assembly, imported = setup(tmp_path)
    draft = root / imported["annotation_template"]
    value = json.loads(draft.read_text())
    value["clips"]["walk.right"]["sample.1"]["image_sha256"] = "0" * 64
    draft.write_text(json.dumps(value))
    with pytest.raises(contract.PartsContractError, match="raster hash"):
        parts_sockets.track_sockets(assembly, imported["candidate"], draft, track_spec(tmp_path / "t.json", assembly),
                                    tmp_path / "ann.json")


def test_cli_reports_json(tmp_path):
    root, assembly, imported = setup(tmp_path)
    completed = subprocess.run([sys.executable, "-m", "sprite_gen.cli", "parts-socket-track", "--assembly", str(assembly),
        "--candidate", imported["candidate"], "--annotations", str(root / imported["annotation_template"]),
        "--spec", str(track_spec(tmp_path / "t.json", assembly)), "--out", str(tmp_path / "ann.json")],
        capture_output=True, text=True)
    payload = json.loads(completed.stdout)
    assert completed.returncode == 0 and payload["ok"] and payload["tracked"] > 0
