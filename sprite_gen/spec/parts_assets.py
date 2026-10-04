# SPDX-License-Identifier: Apache-2.0
"""Bounded library IO and exact-time adapters over the existing asset decoder."""
from __future__ import annotations

from decimal import Decimal
from fractions import Fraction
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile

from PIL import Image

from sprite_gen.spec import assets, parts_contract as contract, parts_workspace as workspace
from sprite_gen.spec.runio import publish_guard

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 256 * 1024 * 1024
MAX_FILES = 4096
MAX_FRAMES = 2048
MAX_FRAME_PIXELS = 4_194_304
MAX_TOTAL_PIXELS = 67_108_864


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_path(root: Path, relative: str) -> Path:
    """Keep portable paths below one root; v1 library paths have no symlinks."""
    contract._relative_file(relative, "asset.file")
    root = Path(root).resolve()
    current = root
    for component in relative.split("/"):
        current /= component
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise contract.PartsContractError(f"asset.file: symlinks are not allowed under the library root: {relative}")
    if not current.resolve().is_relative_to(root):
        raise contract.PartsContractError(f"asset.file escapes allowed root: {relative}")
    return current


def read_file(root: Path, relative: str, *, limit=MAX_FILE_BYTES) -> bytes:
    path = safe_path(root, relative)
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    directory_fd = None
    try:
        if os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW"):
            directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            directory_fd = os.open(Path(root).resolve(), directory_flags)
            components = relative.split("/")
            for component in components[:-1]:
                next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
                os.close(directory_fd)
                directory_fd = next_fd
            fd = os.open(components[-1], flags | os.O_NOFOLLOW, dir_fd=directory_fd)
        else:  # Windows: resolve/junction checks above, then bounded regular-file IO.
            fd = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise contract.PartsContractError("asset.file must be a regular file")
            if info.st_size > limit:
                raise contract.PartsContractError(f"asset.file exceeds the {limit} byte limit")
            data = stream.read(limit + 1)
            if len(data) > limit:
                raise contract.PartsContractError(f"asset.file exceeds the {limit} byte limit")
            return data
    finally:
        if directory_fd is not None:
            os.close(directory_fd)


class SourceReader:
    """One byte snapshot shared across clips, including images and descriptors."""

    def __init__(self, root: Path, *, captured: dict[str, bytes] | None = None):
        self.root = Path(root).resolve()
        self.data: dict[str, bytes] = dict(captured or {})
        self.total_bytes = sum(map(len, self.data.values()))
        if len(self.data) > MAX_FILES or self.total_bytes > MAX_SNAPSHOT_BYTES:
            raise contract.PartsContractError("captured source exceeds snapshot limits")
        for name in self.data:
            contract._relative_file(name, "captured.file")

    def read(self, relative: str, expected: str | None = None) -> bytes:
        if relative not in self.data:
            if len(self.data) >= MAX_FILES:
                raise contract.PartsContractError("source snapshot exceeds the file count limit")
            file_limit = workspace.MAX_JSON_BYTES if relative.lower().endswith(".json") else MAX_FILE_BYTES
            data = read_file(self.root, relative,
                             limit=min(file_limit, MAX_FILE_BYTES, MAX_SNAPSHOT_BYTES - self.total_bytes))
            self.total_bytes += len(data)
            self.data[relative] = data
        data = self.data[relative]
        if expected is not None and sha256(data) != expected:
            raise contract.PartsContractError(f"source hash mismatch: {relative}")
        return data


def png_bytes(image: Image.Image) -> bytes:
    output = BytesIO()
    image.convert("RGBA").save(output, format="PNG", compress_level=9)
    return output.getvalue()


def image_bytes(data: bytes) -> Image.Image:
    # Shared header/pixel/animated-image gate and RGBA decoder, no second loader.
    try:
        return assets._decode_image(data)
    except Image.DecompressionBombError as exc:
        raise contract.PartsContractError("source image exceeds the pixel limit before decode") from exc


def _check_header(data, pixel_limit, size=None):
    try:
        with Image.open(BytesIO(data)) as header:
            if header.width * header.height > pixel_limit:
                raise contract.PartsContractError("source image exceeds the pixel limit before decode")
            if size is not None and header.size != tuple(size):
                raise contract.PartsContractError("source canvas differs from rig canvas; automatic resizing is disabled")
    except Image.DecompressionBombError as exc:
        raise contract.PartsContractError("source image exceeds the pixel limit before decode") from exc


def load_image(reader: SourceReader, reference: dict, *, size=None) -> Image.Image:
    data = reader.read(reference["file"], reference["sha256"])
    _check_header(data, min(MAX_FRAME_PIXELS, assets.MAX_SOURCE_PIXELS), size)
    return image_bytes(data)


def _time(value, name) -> Fraction:
    if isinstance(value, bool) or not isinstance(value, (int, Decimal, str)):
        raise contract.PartsContractError(f"{name}: expected an exact positive time/rate")
    if isinstance(value, Decimal) and (not value.is_finite() or abs(value.adjusted()) > 12):
        raise contract.PartsContractError(f"{name}: time/rate outside supported precision")
    if isinstance(value, str) and len(value) > 128:
        raise contract.PartsContractError(f"{name}: time/rate expression too long")
    try:
        result = Fraction(value)
    except (ValueError, ZeroDivisionError, OverflowError) as exc:
        raise contract.PartsContractError(f"{name}: invalid rational time/rate") from exc
    if result <= 0:
        raise contract.PartsContractError(f"{name}: expected a positive time/rate")
    return result


def quantize_durations(durations, timebase, policy="exact"):
    contract._positive(timebase, "timebase")
    contract._enum("exact", "round-boundaries")(policy, "timing_policy")
    end, previous = Fraction(0), 0
    ticks, errors = [], []
    for duration in durations:
        exact = duration * timebase
        end += exact
        if policy == "exact":
            if exact.denominator != 1:
                raise contract.PartsContractError("duration is not exactly representable; choose a timebase or explicit round-boundaries policy")
            boundary = int(end)
        else:
            boundary = (2 * end.numerator + end.denominator) // (2 * end.denominator)
        tick = boundary - previous
        if tick <= 0:
            raise contract.PartsContractError("timing conversion produced a zero-duration sample")
        ticks.append(tick)
        errors.append(str(Fraction(tick) - exact))
        previous = boundary
    return ticks, errors


def _plan(data, source: str, state, fps):
    """Read authoritative source timing, never infer it from float seconds."""
    if data is None:
        kind, files, durations, ids, loop = "image", [source], [Fraction(1)], ["image:0"], True
    elif data.get("kind") == "sprite-gen-asset":
        entries = data.get("frames")
        if data.get("version") != 1 or not isinstance(entries, list) or not entries:
            raise contract.PartsContractError("external descriptor requires version 1 and ordered frames")
        kind, files, durations, ids = "external", [], [], []
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise contract.PartsContractError("external frame must be an object")
            filename = contract._relative_file(entry.get("file"), "frame.file")
            files.append((Path(source).parent / filename).as_posix())
            durations.append(_time(entry.get("duration"), "frame.duration"))
            ids.append(str(entry.get("id", f"frame:{i}:{filename}")))
        loop = contract._boolean(data.get("loop", True), "source.loop")
    elif "frame_layout" in data:
        state = assets._choose(data["frame_layout"].get("rows"), state)
        rects = data["frame_layout"]["rows"][state]
        if not isinstance(rects, list) or not rects:
            raise contract.PartsContractError("atlas requires ordered frame rectangles")
        animation = data["animation"]["rows"][state]
        filename = contract._relative_file(data.get("sprite_sheet_alpha"), "atlas.file")
        kind, files = "atlas", [(Path(source).parent / filename).as_posix()]
        if "durations_ms" in animation:
            if not isinstance(animation["durations_ms"], list):
                raise contract.PartsContractError("durations_ms must be an ordered array")
            durations = [_time(value, "duration_ms") / 1000 for value in animation["durations_ms"]]
        else:
            durations = [1 / _time(animation.get("fps"), "fps")] * len(rects)
        ids = [f"atlas:{state}:{i}:" + json.dumps(rect, sort_keys=True, default=str, separators=(",", ":"))
               for i, rect in enumerate(rects)]
        loop = contract._boolean(animation.get("loop", True), "source.loop")
        for rect in rects:
            width = contract._positive(rect.get("w"), "rect.w")
            height = contract._positive(rect.get("h"), "rect.h")
            if width * height > MAX_FRAME_PIXELS:
                raise contract.PartsContractError("atlas frame exceeds the pixel limit")
    elif all(key in data for key in ("frames", "w", "h", "delay_ms")):
        count = contract._positive(data["frames"], "strip.frames")
        kind, files = "strip", [Path(source).with_suffix(".png").as_posix()]
        durations = [_time(data["delay_ms"], "delay_ms") / 1000] * min(count, MAX_FRAMES + 1)
        ids = [f"strip:{i}" for i in range(len(durations))]
        loop = contract._boolean(data.get("loop", True), "source.loop")
    else:
        raise contract.PartsContractError("unsupported asset descriptor")
    if not 0 < len(durations) <= MAX_FRAMES or len(durations) != len(ids):
        raise contract.PartsContractError("source frame count/timing coverage is outside the supported limit")
    if fps is not None:
        durations = [1 / _time(fps, "fps override")] * len(ids)
    return kind, files, durations, ids, state, loop


def adapt_clip(reader: SourceReader, request: dict, rig: dict):
    """Preserve source order/canvas/timing and encode immutable normalized frames."""
    source = request["file"]
    expected = request["expected_files"]
    if source not in expected:
        raise contract.PartsContractError("source descriptor is missing from expected_files")
    used = set()

    def checked_read(path):
        try:
            relative = Path(path).relative_to(reader.root).as_posix()
        except ValueError as exc:
            raise contract.PartsContractError("source file escapes allowed root") from exc
        if relative not in expected:
            raise contract.PartsContractError(f"source file has no expected hash: {relative}")
        used.add(relative)
        return reader.read(relative, expected[relative])

    source_path = safe_path(reader.root, source)
    raw = checked_read(source_path)
    if source_path.suffix.lower() == ".json":
        workspace.decode_json(raw)  # duplicate/nonfinite/depth/size gate
        data = json.loads(raw.decode("utf-8"), parse_float=Decimal)
        if not isinstance(data, dict):
            raise contract.PartsContractError("asset descriptor must be an object")
    else:
        data = None
    try:
        kind, files, durations, identities, source_state, loop = _plan(data, source, request["source_state"], request["fps"])
    except (KeyError, TypeError, AttributeError) as exc:
        raise contract.PartsContractError(f"malformed source descriptor: {exc}") from exc
    if request["source_mode"] == "native-finalized" and kind != "atlas":
        raise contract.PartsContractError("native-finalized source must be a final runtime atlas")
    size = (rig["canvas"]["width"], rig["canvas"]["height"])
    if size[0] * size[1] > MAX_FRAME_PIXELS or size[0] * size[1] * len(durations) > MAX_TOTAL_PIXELS:
        raise contract.PartsContractError("decoded clip exceeds the pixel budget")
    if kind == "atlas" and any((rect["w"], rect["h"]) != size
                                for rect in data["frame_layout"]["rows"][source_state]):
        raise contract.PartsContractError("atlas frame canvas differs from rig canvas; automatic resizing is disabled")
    if kind == "strip" and (data["w"], data["h"]) != size:
        raise contract.PartsContractError("strip frame canvas differs from rig canvas; automatic resizing is disabled")
    for filename in set(files):
        binary = checked_read(safe_path(reader.root, filename))
        frame_input = kind in {"image", "external"}
        _check_header(binary, min(MAX_FRAME_PIXELS, assets.MAX_SOURCE_PIXELS) if frame_input else assets.MAX_SOURCE_PIXELS,
                      size if frame_input else None)
    sequence = assets.load_asset(source_path, state=source_state,
                                 fps=float(_time(request["fps"], "fps")) if request["fps"] is not None else None,
                                 read_bytes=checked_read, decode_json=workspace.decode_json)
    if sequence.size != size or len(sequence.frames) != len(durations):
        raise contract.PartsContractError("source canvas/frame count differs from the selected rig; automatic resizing is disabled")
    if used != set(expected):
        raise contract.PartsContractError(f"expected_files does not match source dependencies: {sorted(set(expected) - used)}")
    ticks, errors = quantize_durations(durations, request["timebase"], request["timing_policy"])
    images, samples = {}, []
    for i, (image, tick, identity) in enumerate(zip(sequence.frames, ticks, identities)):
        encoded = png_bytes(image)
        digest = sha256(encoded)
        filename = f"images/{digest}.png"
        images[filename] = encoded
        samples.append({"id": f"sample.{i}", "image": {"file": filename, "sha256": digest},
                        "duration_ticks": tick, "sockets": {}, "source_frame_id": identity})
    clip = {"id": request["id"], "state": request["state"], "view": rig["view"],
            "timebase": request["timebase"], "loop": loop if request["loop"] is None else request["loop"],
            "samples": samples, "source": {"format": kind, "file": source, "state": source_state,
                "files": {name: expected[name] for name in sorted(used)}, "timing_policy": request["timing_policy"],
                "duration_fractions": [str(value) for value in durations], "duration_errors_ticks": errors,
                "source_mode": request["source_mode"], "fps_override": request["fps"], "loop_override": request["loop"]}}
    return clip, images


def publish_object(root: Path, collection: str, identity: str, digest: str, payloads: dict[str, bytes]) -> Path:
    """Publish one verified immutable folder; never overwrite an existing digest."""
    contract._id(collection, "collection")
    contract._id(identity, "object.id")
    contract._hash(digest, "object.digest")
    if not payloads or len(payloads) > MAX_FILES or sum(map(len, payloads.values())) > MAX_SNAPSHOT_BYTES:
        raise contract.PartsContractError("object exceeds the snapshot limits")
    for filename, data in payloads.items():
        contract._relative_file(filename, "object.file")
        if not isinstance(data, bytes) or len(data) > MAX_FILE_BYTES:
            raise contract.PartsContractError("object payload must be bounded bytes")
        if filename.lower().endswith(".json") and len(data) > workspace.MAX_JSON_BYTES:
            raise contract.PartsContractError("object JSON exceeds the 4 MiB limit")
    root = Path(root).resolve()
    destination = safe_path(root, f"{collection}/{identity}/{digest}")
    staging = Path(tempfile.mkdtemp(prefix=f".{collection}-", dir=root))
    try:
        for filename, data in payloads.items():
            path = staging / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        with publish_guard(root):
            safe_path(root, destination.relative_to(root).as_posix())
            if destination.exists():
                actual = {p.relative_to(destination).as_posix() for p in destination.rglob("*") if p.is_file()}
                if actual != set(payloads):
                    raise contract.PartsContractError("existing immutable object has different files")
                for filename, data in payloads.items():
                    if read_file(destination, filename) != data:
                        raise contract.PartsContractError("existing immutable object is modified")
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                staging.rename(destination)
        return destination
    finally:
        if staging.exists():
            shutil.rmtree(staging)
