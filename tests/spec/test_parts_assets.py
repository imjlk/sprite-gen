# SPDX-License-Identifier: Apache-2.0
"""Portable bounded IO, shared format decoding and exact source time."""
from fractions import Fraction
import json
import struct
import zlib

from PIL import Image
import pytest

from parts_helpers import external_source, frame, source_request, workspace_at
from sprite_gen.spec import assets, parts_assets as library, parts_contract as contract, parts_workspace as workspace
from sprite_gen.video.loop import build_strip


def rig_for(assembly):
    index, registry = workspace.read_workspace(assembly)
    return registry[index["rig"]["id"]]


def test_external_order_repeated_bitmap_and_nonuniform_duration_are_preserved(tmp_path):
    source = tmp_path / "input"
    external_source(source)
    rig = rig_for(workspace_at(tmp_path / "library"))
    clip, payloads = library.adapt_clip(library.SourceReader(source), source_request(source), rig)
    assert [s["duration_ticks"] for s in clip["samples"]] == [30000, 15000, 30000]
    assert [s["id"] for s in clip["samples"]] == ["sample.0", "sample.1", "sample.2"]
    assert clip["samples"][0]["image"] == clip["samples"][2]["image"]
    assert len(payloads) == 2
    assert clip["source"]["duration_fractions"] == ["1/4", "1/8", "1/4"]
    assert all(s["sockets"] == {} for s in clip["samples"])


def test_24fps_exact_time_and_explicit_rounding_preserve_total_boundaries():
    ticks, errors = library.quantize_durations([Fraction(1, 24)] * 24, 120000)
    assert ticks == [5000] * 24 and errors == ["0"] * 24
    with pytest.raises(contract.PartsContractError, match="exactly representable"):
        library.quantize_durations([Fraction(1, 24)] * 24, 1000)
    ticks, errors = library.quantize_durations([Fraction(1, 24)] * 24, 1000, "round-boundaries")
    assert sum(ticks) == 1000 and set(ticks) == {41, 42}
    assert sum(map(Fraction, errors)) == 0
    with pytest.raises(contract.PartsContractError, match="zero-duration"):
        library.quantize_durations([Fraction(1, 10000)], 1000, "round-boundaries")


def test_decimal_input_is_not_rounded_through_float_seconds(tmp_path):
    source = tmp_path / "input"
    external_source(source, durations=("0.041666666666666667",), order=(0,))
    rig = rig_for(workspace_at(tmp_path / "library"))
    with pytest.raises(contract.PartsContractError, match="exactly representable"):
        library.adapt_clip(library.SourceReader(source), source_request(source), rig)
    clip, _ = library.adapt_clip(library.SourceReader(source), source_request(source, timing_policy="round-boundaries"), rig)
    assert clip["samples"][0]["duration_ticks"] == 5000
    assert clip["source"]["duration_fractions"] == [str(Fraction("0.041666666666666667"))]
    assert clip["source"]["duration_errors_ticks"] != ["0"]


def test_final_video_strip_uses_its_actual_canvas_and_ms_delay(tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    strip, sidecar = build_strip([frame(phase=i) for i in range(3)], cycle_seconds=0.5, kind="one-shot")
    strip.save(source / "walk.strip.png")
    (source / "walk.strip.json").write_text(json.dumps(sidecar))
    rig = rig_for(workspace_at(tmp_path / "library", size=(sidecar["w"], sidecar["h"])))
    clip, _ = library.adapt_clip(library.SourceReader(source), source_request(source, "walk.strip.json", timebase=100000), rig)
    assert clip["loop"] is False
    assert clip["source"]["duration_fractions"] == [str(Fraction(str(sidecar["delay_ms"])) / 1000)] * 3
    assert [s["duration_ticks"] for s in clip["samples"]] == [16667] * 3


@pytest.mark.parametrize("filename", ["../outside.png", "/tmp/outside.png", "C:/outside.png", "a\\b.png"])
def test_descriptor_traversal_is_rejected_before_loading_an_image(tmp_path, filename, monkeypatch):
    source = tmp_path / "input"
    external_source(source)
    path = source / "motion.json"
    data = json.loads(path.read_text())
    data["frames"][0]["file"] = filename
    path.write_text(json.dumps(data))
    rig = rig_for(workspace_at(tmp_path / "library"))
    monkeypatch.setattr(assets, "_decode_image", lambda *args: pytest.fail("unsafe image was decoded"))
    with pytest.raises(contract.PartsContractError, match="relative|traversal"):
        library.adapt_clip(library.SourceReader(source), source_request(source), rig)


def test_symlink_and_file_size_are_rejected_before_read(tmp_path):
    (tmp_path / "inside").mkdir()
    (tmp_path / "outside.bin").write_bytes(b"data")
    (tmp_path / "inside/link.bin").symlink_to(tmp_path / "outside.bin")
    with pytest.raises(contract.PartsContractError, match="symlink"):
        library.read_file(tmp_path / "inside", "link.bin")
    with pytest.raises(contract.PartsContractError, match="byte limit"):
        library.read_file(tmp_path, "outside.bin", limit=3)


def test_pixel_budget_is_checked_before_decode(tmp_path, monkeypatch):
    source = tmp_path / "input"
    external_source(source)
    rig = rig_for(workspace_at(tmp_path / "library"))
    monkeypatch.setattr(library, "MAX_FRAME_PIXELS", 1)
    monkeypatch.setattr(assets, "_decode_image", lambda *args: pytest.fail("oversized image was decoded"))
    with pytest.raises(contract.PartsContractError, match="pixel budget"):
        library.adapt_clip(library.SourceReader(source), source_request(source), rig)


def test_expected_hash_catches_a_source_replaced_during_import(tmp_path, monkeypatch):
    source = tmp_path / "input"
    external_source(source)
    request = source_request(source)
    rig = rig_for(workspace_at(tmp_path / "library"))
    original = library.read_file

    def read_then_replace(root, name, **kwargs):
        result = original(root, name, **kwargs)
        if name == "motion.json":
            Image.new("RGBA", (128, 128), "blue").save(source / "pose-1.png")
        return result

    monkeypatch.setattr(library, "read_file", read_then_replace)
    with pytest.raises(contract.PartsContractError, match="source hash mismatch"):
        library.adapt_clip(library.SourceReader(source), request, rig)


def test_unknown_or_extra_dependencies_cannot_be_declared_as_a_consistent_source(tmp_path):
    source = tmp_path / "input"
    external_source(source)
    rig = rig_for(workspace_at(tmp_path / "library"))
    request = source_request(source)
    request["expected_files"].pop("pose-1.png")
    with pytest.raises(contract.PartsContractError, match="no expected hash"):
        library.adapt_clip(library.SourceReader(source), request, rig)
    request = source_request(source)
    request["expected_files"]["unused.png"] = "a" * 64
    with pytest.raises(contract.PartsContractError, match="does not match source dependencies"):
        library.adapt_clip(library.SourceReader(source), request, rig)


def test_hostile_image_header_is_a_contract_error_before_decode(tmp_path, monkeypatch):
    source = tmp_path / "input"
    external_source(source)
    path = source / "pose-0.png"
    original = path.read_bytes()
    ihdr = original[12:16] + struct.pack(">II", 1_000_000, 1_000_000) + original[24:29]
    path.write_bytes(original[:12] + ihdr + struct.pack(">I", zlib.crc32(ihdr)) + original[33:])
    rig = rig_for(workspace_at(tmp_path / "library"))
    monkeypatch.setattr(assets, "_decode_image", lambda *args: pytest.fail("hostile pixels were decoded"))
    with pytest.raises(contract.PartsContractError, match="pixel limit"):
        library.adapt_clip(library.SourceReader(source), source_request(source), rig)


def test_every_atlas_rectangle_is_checked_before_decode(tmp_path, monkeypatch):
    source = tmp_path / "input"
    source.mkdir()
    frame().save(source / "atlas.png")
    descriptor = {"sprite_sheet_alpha": "atlas.png", "frame_layout": {"rows": {"walk": [
        {"x": 0, "y": 0, "w": 128, "h": 128}, {"x": 0, "y": 0, "w": 1, "h": 1}]}},
        "animation": {"rows": {"walk": {"fps": 8, "loop": True}}}}
    (source / "motion.json").write_text(json.dumps(descriptor))
    rig = rig_for(workspace_at(tmp_path / "library"))
    monkeypatch.setattr(assets, "_decode_image", lambda *args: pytest.fail("wrong canvas was decoded"))
    with pytest.raises(contract.PartsContractError, match="canvas differs"):
        library.adapt_clip(library.SourceReader(source), source_request(source, source_state="walk"), rig)


def test_immutable_objects_reuse_equal_bytes_and_reject_modified_content(tmp_path):
    payloads = {"value.bin": b"stable"}
    digest = library.sha256(b"stable")
    first = library.publish_object(tmp_path, "objects", "test", digest, payloads)
    assert library.publish_object(tmp_path, "objects", "test", digest, payloads) == first
    (first / "value.bin").write_bytes(b"corrupt")
    with pytest.raises(contract.PartsContractError, match="modified"):
        library.publish_object(tmp_path, "objects", "test", digest, payloads)
    assert (first / "value.bin").read_bytes() == b"corrupt"
