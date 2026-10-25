# SPDX-License-Identifier: Apache-2.0
"""Asset-pipeline layer over `sprite-gen tween`: fixed frames, engine metadata, batches, sheets.

`make_tween` returns in-betweens on a canvas sized to that one transition. A game pipeline
needs more than that, and this module adds it without touching the drawing itself:

* **fixed frames** (`frame_size`, `pivot`, `fit`): every frame of every boundary on one
  canvas size with the foot anchor on one pixel, so frames from different transitions and
  from the base sheet are interchangeable at runtime. A set that does not fit is refused
  (`fit=error`) or shrunk uniformly about the anchor (`fit=shrink`); never clipped.
* **metadata** (`tween.json`, schema `sprite-gen-tween/2`): per-frame file, role, size,
  pivot and duration, plus status, so an engine or atlas packer reads one file.
* **sheets** (`export=sheet`): `sheet.png` + `sheet.json` with one row per boundary, in the
  same `animation.rows` / `frame_layout.rows` shape `compose` writes to `manifest.json`.
* **batches** (`--manifest`): many boundaries in one run; each lands in `<out>/<id>/`,
  `batch.json` summarises them, and one failure does not stop the rest.
* **resumable**: a boundary whose `tween.json` says `ok` for the same input hash (endpoint
  bytes + every option that changes the output) is skipped; `--force` redoes it.

Exit codes: 0 all ok (or cached), 1 provider/runtime error, 2 usage error, 3 refused.
In a batch the most severe code wins: error (1) over refused (3) over usage (2) over ok (0)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen.effects import tween as T

SCHEMA = "sprite-gen-tween/2"
CACHE_VERSION = 1
FIELDS = {  # boundary option -> default
    "from_file": None, "to_file": None, "cell": None, "n": 2, "method": "strip", "provider": "grok",
    "anchor": "foot", "margin": T.DEFAULT_MARGIN, "min_endpoint_iou": T.DEFAULT_MIN_ENDPOINT_IOU,
    "description": None, "chroma": "magenta:#FF00FF", "refs": [], "frame_size": None,
    "pivot": "bottom-center", "fit": "error", "resample": "lanczos", "fps": 12.0, "durations_ms": None,
    "include_ends": False, "export": "none", "loop": False,
}
_HASHED = [k for k in FIELDS if k not in ("export",)]


# ---------------------------------------------------------------- config

def _size(value: Any, what: str) -> tuple[int, int] | None:
    if value in (None, ""):
        return None
    if isinstance(value, (list, tuple)) and len(value) == 2:
        w, h = int(value[0]), int(value[1])
    else:
        try:
            w, h = (int(v) for v in str(value).lower().split("x"))
        except ValueError:
            raise T.TweenUsage(f"tween: {what} must be WxH, got {value!r}") from None
    if w <= 0 or h <= 0:
        raise T.TweenUsage(f"tween: {what} must be positive, got {value!r}")
    return w, h


def _durations(value: Any) -> list[int] | None:
    if value in (None, ""):
        return None
    items = value if isinstance(value, list) else str(value).split(",")
    try:
        out = [int(v) for v in items]
    except ValueError:
        raise T.TweenUsage(f"tween: durations_ms must be integers, got {value!r}") from None
    if any(d <= 0 for d in out):
        raise T.TweenUsage("tween: durations_ms must be positive")
    return out


def boundary_from_args(args) -> dict[str, Any]:
    cfg = {k: getattr(args, k, v) for k, v in FIELDS.items() if k != "refs"}
    cfg["refs"] = [str(p) for p in (args.ref or [])]
    cfg["id"] = args.label or "tween"
    return normalise(cfg, Path.cwd())


def normalise(cfg: dict[str, Any], base: Path) -> dict[str, Any]:
    unknown = set(cfg) - set(FIELDS) - {"id"}
    if unknown:
        raise T.TweenUsage(f"tween: unknown boundary option(s) {sorted(unknown)}")
    out = {**FIELDS, **cfg}
    bid = str(out.get("id") or "")
    if not bid or "/" in bid or "\\" in bid or bid.startswith("."):
        raise T.TweenUsage(f"tween: boundary id must be a filesystem-safe name, got {bid!r}")
    out["id"] = bid
    for key in ("from_file", "to_file"):
        if not out[key]:
            raise T.TweenUsage(f"tween: boundary {bid!r} needs {key}")
        spec = str(out[key])
        head, sep, tail = spec.rpartition(":")
        path, suffix = (head, sep + tail) if sep and tail.lstrip("-").isdigit() else (spec, "")
        out[key] = str(base / path if not Path(path).is_absolute() else Path(path)) + suffix
    out["refs"] = [str(base / r if not Path(r).is_absolute() else Path(r)) for r in out["refs"]]
    out["cell"] = _size(out["cell"], "cell")
    out["frame_size"] = _size(out["frame_size"], "frame_size")
    out["durations_ms"] = _durations(out["durations_ms"])
    if out["fit"] not in ("error", "shrink"):
        raise T.TweenUsage(f"tween: fit must be error or shrink, got {out['fit']!r}")
    if out["resample"] not in ("lanczos", "nearest"):
        raise T.TweenUsage(f"tween: resample must be lanczos or nearest, got {out['resample']!r}")
    if out["export"] not in ("none", "sheet"):
        raise T.TweenUsage(f"tween: export must be none or sheet, got {out['export']!r}")
    if float(out["fps"]) <= 0:
        raise T.TweenUsage("tween: fps must be positive")
    return out


def _pivot(spec: str, size: tuple[int, int]) -> tuple[int, int]:
    w, h = size
    if spec in (None, "", "bottom-center"):
        return w // 2, h
    try:
        x, y = (int(v) for v in str(spec).split(","))
    except ValueError:
        raise T.TweenUsage(f"tween: pivot must be bottom-center or X,Y, got {spec!r}") from None
    if not (0 <= x <= w and 0 <= y <= h):
        raise T.TweenUsage(f"tween: pivot {spec} lies outside the {w}x{h} frame")
    return x, y


def input_hash(cfg: dict[str, Any]) -> str:
    h = hashlib.sha256(f"tween-cache/{CACHE_VERSION}".encode())
    h.update(json.dumps({k: cfg[k] for k in _HASHED}, sort_keys=True, default=str).encode())
    for spec in (cfg["from_file"], cfg["to_file"], *cfg["refs"]):
        head, sep, tail = spec.rpartition(":")
        path = Path(head if sep and tail.lstrip("-").isdigit() else spec)
        h.update(path.read_bytes() if path.is_file() else b"<missing>")
    return h.hexdigest()[:16]


# ---------------------------------------------------------------- frames

def fit_frames(canvases: list[Image.Image], anchor: tuple[int, int], size: tuple[int, int],
               pivot: tuple[int, int], fit: str, resample: str) -> tuple[list[Image.Image], float]:
    """Move every canvas (one shared anchor) onto a `size` frame with the anchor on `pivot`.

    Returns the frames and the uniform scale applied (1.0 unless `fit=shrink` had to shrink)."""
    union = None
    for c in canvases:
        box = c.getchannel("A").point(lambda v: 255 if v >= T.ALPHA_MIN else 0).getbbox()
        if box:
            union = box if union is None else (min(union[0], box[0]), min(union[1], box[1]),
                                               max(union[2], box[2]), max(union[3], box[3]))
    if union is None:
        raise T.TweenRefused("tween: every frame is empty")
    ax, ay = anchor
    need = (ax - union[0], union[2] - ax, ay - union[1], union[3] - ay)   # left, right, up, down
    room = (pivot[0], size[0] - pivot[0], pivot[1], size[1] - pivot[1])
    ratios = [r / n for n, r in zip(need, room) if n > 0]
    scale = min([1.0, *ratios])
    if scale < 1.0 and fit != "shrink":
        raise T.TweenRefused(
            f"tween: refused — the frames need {need[0] + need[1]}x{need[2] + need[3]} px around the anchor "
            f"(left {need[0]}, right {need[1]}, up {need[2]}, down {need[3]}) but --frame-size "
            f"{size[0]}x{size[1]} with pivot {pivot[0]},{pivot[1]} leaves {room}; enlarge the frame, move the "
            "pivot, or pass --fit shrink")
    filt = Image.Resampling.NEAREST if resample == "nearest" else Image.Resampling.LANCZOS
    out = []
    for c in canvases:
        crop = c.crop(union)
        ox, oy = ax - union[0], ay - union[1]
        if scale < 1.0:
            crop = crop.resize((max(1, round(crop.width * scale)), max(1, round(crop.height * scale))), filt)
            ox, oy = ox * scale, oy * scale
        frame = Image.new("RGBA", size, (0, 0, 0, 0))
        frame.alpha_composite(crop, (int(round(pivot[0] - ox)), int(round(pivot[1] - oy))))
        out.append(frame)
    return out, scale


def sheet(rows: list[tuple[str, list[Image.Image], list[int], bool]], size: tuple[int, int],
          pivot: tuple[int, int]) -> tuple[Image.Image, dict[str, Any]]:
    """One row per boundary, cells of `size`; metadata in the `compose` manifest row shape."""
    cols = max(len(f) for _, f, _, _ in rows)
    w, h = size
    atlas = Image.new("RGBA", (cols * w, len(rows) * h), (0, 0, 0, 0))
    animation, layout = {}, {}
    for r, (rid, frames, durations, loop) in enumerate(rows):
        rects = []
        for c, f in enumerate(frames):
            atlas.alpha_composite(f, (c * w, r * h))
            rects.append({"x": c * w, "y": r * h, "w": w, "h": h})
        layout[rid] = rects
        animation[rid] = {"row": r, "frames": len(frames), "durations_ms": durations, "loop": loop}
    meta = {"kind": "sprite-gen-tween-sheet", "schema": SCHEMA, "image": "sheet.png",
            "size": [atlas.width, atlas.height], "cell": {"width": w, "height": h},
            "pivot": {"x": pivot[0], "y": pivot[1], "nx": round(pivot[0] / w, 4), "ny": round(pivot[1] / h, 4)},
            "animation": {"rows": animation}, "frame_layout": {"rows": layout}}
    return atlas, meta


# ---------------------------------------------------------------- one boundary

def _write_json(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _cached(out_dir: Path, digest: str) -> dict[str, Any] | None:
    try:
        old = json.loads((out_dir / "tween.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if old.get("status") != "ok" or old.get("input_hash") != digest:
        return None
    if not all((out_dir / f["file"]).is_file() for f in old.get("frames", [])):
        return None
    return old


def run_boundary(cfg: dict[str, Any], out_dir: Path, *, force: bool = False,
                 generator: T.Generator | None = None, video_runner: T.VideoRunner | None = None
                 ) -> dict[str, Any]:
    """Run one boundary into `out_dir`. Never raises for refusals or provider errors: returns
    {id, status: ok|cached|refused|error, exit, out_dir, report}. Usage errors raise TweenUsage."""
    out_dir.mkdir(parents=True, exist_ok=True)
    digest = input_hash(cfg)
    if not force:
        old = _cached(out_dir, digest)
        if old is not None:
            print(f"[tween] {cfg['id']}: up to date ({digest}), skipped")
            return {"id": cfg["id"], "status": "cached", "exit": T.EXIT_OK, "out_dir": str(out_dir), "report": old}
    name, _, hexv = str(cfg["chroma"]).partition(":")
    try:
        rgb = tuple(int(hexv.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        raise T.TweenUsage(f"tween: chroma must be name:#RRGGBB, got {cfg['chroma']!r}") from None
    a = T.endpoint_from_file(cfg["from_file"], cfg["cell"], rgb)
    b = T.endpoint_from_file(cfg["to_file"], cfg["cell"], rgb)
    base = {"kind": "sprite-gen-tween", "schema": SCHEMA, "id": cfg["id"], "input_hash": digest,
            "from": cfg["from_file"], "to": cfg["to_file"], "n": cfg["n"], "method": cfg["method"],
            "provider": cfg["provider"]}

    def failed(status: str, code: int, exc: BaseException) -> dict[str, Any]:
        try:
            rep = json.loads((out_dir / "tween.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            rep = {}
        rep = {**rep, **base, "status": status, "error": rep.get("error") or f"{type(exc).__name__}: {exc}"}
        _write_json(out_dir / "tween.json", rep)
        print(f"[tween] {cfg['id']}: {status}: {rep['error']}")
        return {"id": cfg["id"], "status": status, "exit": code, "out_dir": str(out_dir), "report": rep}

    try:
        tweens, report = T.make_tween(
            a, b, int(cfg["n"]), workdir=out_dir, method=cfg["method"], provider=cfg["provider"],
            anchor=cfg["anchor"], margin=float(cfg["margin"]), min_endpoint_iou=float(cfg["min_endpoint_iou"]),
            description=cfg["description"], chroma=(name, hexv, rgb), generator=generator,
            video_runner=video_runner, extra_refs=[Path(r) for r in cfg["refs"]])
    except T.TweenUsage:
        raise
    except T.TweenRefused as exc:
        return failed("refused", T.EXIT_REFUSED, exc)
    except Exception as exc:  # noqa: BLE001 - provider/runtime failure is recorded per boundary
        return failed("error", T.EXIT_ERROR, exc)
    ends = report.pop("_ends")
    canvases = [ends[0], *tweens, ends[1]]
    anchor = tuple(report["anchor_px"])
    scale = 1.0
    size = tuple(report["canvas"])
    pivot = anchor
    if cfg["frame_size"]:
        size = cfg["frame_size"]
        pivot = _pivot(cfg["pivot"], size)
        try:
            canvases, scale = fit_frames(canvases, anchor, size, pivot, cfg["fit"], cfg["resample"])
        except T.TweenRefused as exc:
            _write_json(out_dir / "tween.json", {**report, **base, "status": "refused", "error": str(exc)})
            return failed("refused", T.EXIT_REFUSED, exc)
    names = ["from.png", *[f"tween-{k}.png" for k in range(len(tweens))], "to.png"]
    roles = ["from", *["tween"] * len(tweens), "to"]
    for im, fname in zip(canvases, names):
        im.save(out_dir / fname)
    listed = list(range(len(names))) if cfg["include_ends"] else list(range(1, len(names) - 1))
    durations = cfg["durations_ms"] or [max(1, round(1000.0 / float(cfg["fps"])))] * len(listed)
    if len(durations) != len(listed):
        raise T.TweenUsage(f"tween: {cfg['id']}: durations_ms has {len(durations)} values for "
                           f"{len(listed)} frames")
    frames = [{"index": j, "file": names[i], "role": roles[i], "w": size[0], "h": size[1],
               "pivot": [pivot[0], pivot[1]], "duration_ms": d} for j, (i, d) in enumerate(zip(listed, durations))]
    report.update(base)
    report.update({
        "status": "ok", "frame_size": [size[0], size[1]], "fixed_frame": bool(cfg["frame_size"]),
        "pivot": {"x": pivot[0], "y": pivot[1], "nx": round(pivot[0] / size[0], 4), "ny": round(pivot[1] / size[1], 4)},
        "fit_scale": round(scale, 4), "fps": float(cfg["fps"]), "loop": bool(cfg["loop"]),
        "include_ends": bool(cfg["include_ends"]), "frames": frames,
        "transition": {"from_state": cfg["from_file"], "to_state": cfg["to_file"], "n": cfg["n"]},
    })
    if scale < 1.0:
        report.setdefault("warnings", []).append(f"set shrunk to {scale:.3f}x to fit {size[0]}x{size[1]}")
    _write_json(out_dir / "tween.json", report)
    print(f"[tween] {cfg['id']}: {len(tweens)} in-betweens written to {out_dir}")
    return {"id": cfg["id"], "status": "ok", "exit": T.EXIT_OK, "out_dir": str(out_dir), "report": report}


def _sheet_rows(results: list[dict[str, Any]]) -> tuple[list, tuple[int, int], tuple[int, int]]:
    ok = [r for r in results if r["status"] in ("ok", "cached")]
    sizes = {tuple(r["report"]["frame_size"]) for r in ok}
    pivots = {(r["report"]["pivot"]["x"], r["report"]["pivot"]["y"]) for r in ok}
    if len(sizes) != 1 or len(pivots) != 1:
        raise T.TweenUsage("tween: export=sheet across boundaries needs one --frame-size and --pivot for all")
    rows = []
    for r in ok:
        d = Path(r["out_dir"])
        fr = r["report"]["frames"]
        rows.append((r["id"], [Image.open(d / f["file"]).convert("RGBA") for f in fr],
                     [f["duration_ms"] for f in fr], bool(r["report"].get("loop"))))
    return rows, sizes.pop(), pivots.pop()


def write_sheet(results: list[dict[str, Any]], out_dir: Path) -> Path | None:
    if not any(r["status"] in ("ok", "cached") for r in results):
        return None
    rows, size, pivot = _sheet_rows(results)
    atlas, meta = sheet(rows, size, pivot)
    atlas.save(out_dir / "sheet.png")
    _write_json(out_dir / "sheet.json", meta)
    return out_dir / "sheet.png"


def exit_code(results: list[dict[str, Any]]) -> int:
    codes = {r["exit"] for r in results}
    for code in (T.EXIT_ERROR, T.EXIT_REFUSED, T.EXIT_USAGE):
        if code in codes:
            return code
    return T.EXIT_OK


def run_single(cfg: dict[str, Any], out_dir: Path, *, force: bool = False, **kw) -> int:
    result = run_boundary(cfg, out_dir, force=force, **kw)
    if cfg["export"] == "sheet" and result["status"] in ("ok", "cached"):
        write_sheet([result], out_dir)
    return exit_code([result])


# ---------------------------------------------------------------- manifest

def load_manifest(path: Path) -> list[dict[str, Any]]:
    """`{"defaults": {...}, "boundaries": [{"id", "from_file", "to_file", ...}, ...]}`.
    Relative paths resolve against the manifest's folder; ids must be unique."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise T.TweenUsage(f"tween: cannot read manifest {path}: {exc}") from None
    if not isinstance(data, dict) or not isinstance(data.get("boundaries"), list) or not data["boundaries"]:
        raise T.TweenUsage("tween: manifest needs a non-empty \"boundaries\" list")
    defaults = data.get("defaults", {})
    base = Path(path).resolve().parent
    out, seen = [], set()
    for entry in data["boundaries"]:
        cfg = normalise({**defaults, **entry}, base)
        if cfg["id"] in seen:
            raise T.TweenUsage(f"tween: duplicate boundary id {cfg['id']!r} in manifest")
        seen.add(cfg["id"])
        out.append(cfg)
    exports = {c["export"] for c in out}
    if "sheet" in exports and (any(c["frame_size"] is None for c in out)
                               or len({c["frame_size"] for c in out}) != 1):
        raise T.TweenUsage("tween: export=sheet in a manifest needs one frame_size shared by every boundary")
    return out


def run_manifest(path: Path, out_dir: Path, *, force: bool = False, only: list[str] | None = None,
                 generator: T.Generator | None = None, video_runner: T.VideoRunner | None = None) -> int:
    boundaries = load_manifest(path)
    if only:
        missing = set(only) - {b["id"] for b in boundaries}
        if missing:
            raise T.TweenUsage(f"tween: --only names unknown id(s) {sorted(missing)}")
        boundaries = [b for b in boundaries if b["id"] in only]
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for cfg in boundaries:
        try:
            results.append(run_boundary(cfg, out_dir / cfg["id"], force=force, generator=generator,
                                        video_runner=video_runner))
        except T.TweenUsage as exc:
            print(f"[tween] {cfg['id']}: usage: {exc}")
            results.append({"id": cfg["id"], "status": "usage", "exit": T.EXIT_USAGE,
                            "out_dir": str(out_dir / cfg["id"]), "error": str(exc)})
    sheet_path = None
    if any(c["export"] == "sheet" for c in boundaries):
        sheet_path = write_sheet(results, out_dir)
    code = exit_code(results)
    summary = {"kind": "sprite-gen-tween-batch", "schema": SCHEMA, "manifest": str(path), "exit": code,
               "counts": {s: sum(r["status"] == s for r in results)
                          for s in ("ok", "cached", "refused", "error", "usage")},
               "sheet": sheet_path.name if sheet_path else None,
               "boundaries": [{"id": r["id"], "status": r["status"], "exit": r["exit"],
                               "dir": Path(r["out_dir"]).name,
                               "error": r.get("error") or r.get("report", {}).get("error")} for r in results]}
    _write_json(out_dir / "batch.json", summary)
    print(f"[tween] batch: {summary['counts']} -> {out_dir / 'batch.json'} (exit {code})")
    return code
