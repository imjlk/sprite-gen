# SPDX-License-Identifier: Apache-2.0
"""`transition` beside `video-chain` on one join between two Pip clips (docs/transition.md "transition and video-chain").

The join is `jump:13` (Pip standing, the landed hold of `tests/fixtures/rife-cuts/pip-jump`) into `run:7` (a full
stride of `pip-run`): two states, both on the floor, a pose change of 3.15 steps on `video-chain`'s join reading.
Two frames between, four ways, every one through the CLI in a subprocess as a user runs it:

| row | command |
|---|---|
| `strip` | `transition --method strip` (the defaults; `--ground auto` is `shared` here, both ends on the floor) |
| `video` | `transition --method video` (every fault of the judge refuses, the default) |
| `chain-held` | `video-chain --piece A=jump:10..13 --piece B=run:7..10 --bridge 2 --refuse held` |
| `chain` | the same with every fault refusing (the default) |

**The two clips are drawn at different scales.** The run's figure is about 1.37x the jump's (the square root of the
ratio of their median opaque areas; their median heights differ 1.27x, a standing frame 498 px against a stride
696 px). Taken as they are, every frame between grows from A's size to B's, which reads as a zoom. `build_states`
scales the clip drawn larger down to the other's scale (`SCALE_BY`: the square root of the median opaque area ratio,
measured, not set) before anything runs, so both verbs take the same, normalized frames; `summary.json` records the
factor (`states.scale`).

The transition rows call the provider (`--provider`, grok by default: one image or one clip a try, billed per call
where the login does not cover it) `--tries` times each (default 3), since the provider draws anew on every call; the
pages show a delivered try with no fault kept, the one whose lowest step IoU (consecutive frames' silhouette IoU) is
highest, and the summary says which. The chain rows run once (RIFE gives the same frames every run). `--no-provider` runs the chain rows only,
`--render-only` draws the pages again from an earlier `--out-dir`, and `--resume` runs again only what made nothing.
RIFE must be installed for the chain rows (`sprite-gen rife install`).

    .venv/bin/python scripts/dev/transition_chain_demo.py --out-dir <a new directory> [--tries 3] [--jobs 4]

Writes `<out-dir>/states/` (the normalized frames), `<out-dir>/runs/<row>/[try-<k>/]` (each verb's own folder and
report), `<out-dir>/summary.json`, and `<out-dir>/showcase/transition-vs-chain.gif` (A, the two frames between, B
playing, three rows) and `transition-vs-chain.png` (every row's four frames), each under 1 MB.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "rife-cuts"
CLIPS = ("pip-jump", "pip-run")
JOIN = ("pip-jump", 13, "pip-run", 7)  # from clip, from frame, to clip, to frame (0-based)
WHAT = "jump:13 (standing) -> run:7 (full stride), both on the floor"
ROWS = ("strip", "video", "chain-held", "chain")
TRANSITION_ROWS = ("strip", "video")
TITLES = {"strip": "transition --method strip", "video": "transition --method video",
          "chain-held": "video-chain --refuse held", "chain": "video-chain (default)"}
DESCRIPTION = ("chibi wizard kid Pip: big indigo pointed hood with a yellow bell tip, orange scarf, cream tunic, "
               "brown satchel and boots, side view facing right")
MARGIN = (12, 13)  # px added to the larger normalized clip's width and height: the canvas of the comparison
FPS = 24
NAME = "transition-vs-chain"


def _alpha_area(im: Image.Image) -> int:
    return sum(1 for v in im.getchannel("A").tobytes() if v)


def scale_by(frames: dict[str, list[Path]]) -> dict[str, float]:
    """Each clip's scale factor onto the smallest-drawn clip: the square root of the ratio of median opaque areas
    (a figure's area goes with the square of its scale, and the median over a clip evens out its poses)."""
    medians = {}
    for clip, paths in frames.items():
        areas = sorted(_alpha_area(Image.open(p).convert("RGBA")) for p in paths)
        medians[clip] = areas[len(areas) // 2]
    small = min(medians.values())
    return {clip: math.sqrt(small / m) for clip, m in medians.items()}


def build_states(out: Path) -> tuple[dict[str, Path], dict]:
    """Every fixture frame scaled to one drawing scale (`scale_by`), on one canvas, bottom-aligned and centred, as keyed
    PNGs `frame-0001.png` on."""
    frames = {c: sorted((FIXTURES / c).glob("*.webp")) for c in CLIPS}
    scale = scale_by(frames)
    sized = {c: [(round(w * scale[c]), round(h * scale[c])) for w, h in (Image.open(p).size for p in frames[c])]
             for c in CLIPS}
    w = max(s[0] for c in CLIPS for s in sized[c]) + MARGIN[0]
    h = max(s[1] for c in CLIPS for s in sized[c]) + MARGIN[1]
    dirs = {}
    for clip in CLIPS:
        keyed = out / "states" / clip / "keyed"
        keyed.mkdir(parents=True, exist_ok=True)
        for k, (path, size) in enumerate(zip(frames[clip], sized[clip])):
            im = Image.open(path).convert("RGBA")
            if size != im.size:
                im = im.resize(size, Image.Resampling.LANCZOS)
            canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            canvas.alpha_composite(im, ((w - im.width) // 2, h - im.height))
            canvas.save(keyed / f"frame-{k + 1:04d}.png")
        dirs[clip] = keyed
    return dirs, {"scale": {c: round(s, 4) for c, s in scale.items()}, "canvas": [w, h]}


def command(row: str, states: dict[str, Path], run: Path, provider: str, n: int) -> list[str]:
    a, fa, b, fb = JOIN
    if row.startswith("chain"):
        args = ["video-chain", "--piece", f"A={states[a]}:{fa - 3}:{fa}", "--piece", f"B={states[b]}:{fb}:{fb + 3}",
                "--bridge", str(n), "--key", "green", "--fps", str(FPS), "--out-dir", str(run)]
        return args + (["--refuse", "held"] if row == "chain-held" else [])
    return ["transition", "--from-file", str(states[a] / f"frame-{fa + 1:04d}.png"),
            "--to-file", str(states[b] / f"frame-{fb + 1:04d}.png"), "--n", str(n), "--provider", provider,
            "--description", DESCRIPTION, "--out-dir", str(run), "--fps", str(FPS), "--include-ends", "--force",
            "--method", row]


def run_dirs(row: str, runs: Path, tries: int) -> list[Path]:
    """Where a row's runs go: one folder for a chain row, `try-<k>` for each try of a transition row."""
    return [runs / row] if row.startswith("chain") else [runs / row / f"try-{k}" for k in range(1, tries + 1)]


def stands(row: str, run: Path) -> bool:
    """An earlier run that made something: a report whose status is not error."""
    report = run / ("chain.report.json" if row.startswith("chain") else "transition.json")
    try:
        return json.loads(report.read_text(encoding="utf-8")).get("status") != "error"
    except (OSError, ValueError):
        return False


def execute(row: str, run: Path, states: dict[str, Path], provider: str, n: int) -> dict:
    run.mkdir(parents=True, exist_ok=True)
    began = time.monotonic()
    proc = subprocess.run([sys.executable, "-m", "sprite_gen.cli", *command(row, states, run, provider, n)],
                          capture_output=True, text=True)
    (run / "log.txt").write_text(proc.stdout + proc.stderr, encoding="utf-8")
    return {"exit": proc.returncode, "seconds": round(time.monotonic() - began, 1)}


def read_run(row: str, run: Path) -> dict:
    """The numbers a run's report gives, for the summary and the pages."""
    if row.startswith("chain"):
        rep = json.loads((run / "chain.report.json").read_text(encoding="utf-8"))
        bridges = rep.get("chain", {}).get("bridges", [])
        return {"status": rep["status"], "refuse": rep["refuse"],
                "join_steps": next((j.get("steps") for j in rep.get("joins", [])), None),
                "bridge_faults": [sorted({f for t in b["refused"] for f in t["faults"]}) for b in bridges],
                "bridge_made": [b["judged"] is not None for b in bridges],
                "kept_faults": [b.get("kept_faults", []) for b in bridges], "error": rep.get("error")}
    rep = json.loads((run / "transition.json").read_text(encoding="utf-8"))
    em = rep.get("endpoint_match", {})
    return {"status": rep["status"], "endpoint_iou": [em.get("iou_from"), em.get("iou_to")],
            "scale_agree": em.get("scale_agree"), "frame_scale": rep.get("frame_scale", {}).get("drawn_vs_endpoints"),
            "heights": rep.get("keyframe_heights", [None])[:1] + rep.get("heights", []) + rep.get("keyframe_heights", [None])[1:],
            "step_iou": rep.get("step_iou"), "drawn_step_iou": rep.get("drawn_step_iou"), "ground": rep.get("ground"),
            "refuse": rep.get("refuse"), "judge_faults": [r["faults"] for r in rep.get("judge", [])],
            "kept_faults": [r.get("kept_faults", []) for r in rep.get("judge", [])],
            "shown_again": (rep.get("read") or {}).get("unlisted"),
            "warnings": rep.get("warnings", []), "error": rep.get("error")}


def delivered(numbers: dict) -> bool:
    return numbers.get("status") in ("ok", "passed")


def frames_of(row: str, run: Path) -> tuple[list[Image.Image] | None, bool]:
    """A, the two frames between, B as the run delivered them, or as it left them refused; None where none were made."""
    if row.startswith("chain"):
        keyed = run / "keyed"
        folder, refused = (keyed, False) if keyed.is_dir() else (run / "keyed.refused", True)
        files = [folder / f"frame-{k:04d}.png" for k in (4, 5, 6, 7)]
    else:
        folder, refused = (run, False) if (run / "between-0.png").is_file() else (run / "refused", True)
        files = [folder / n for n in ("from.png", "between-0.png", "between-1.png", "to.png")]
    if not all(f.is_file() for f in files):
        return None, True
    return [Image.open(f).convert("RGBA") for f in files], refused


def figure_heights(frames: list[Image.Image]) -> list[int]:
    return [b[3] - b[1] for b in (f.getchannel("A").getbbox() for f in frames)]


def summarise(runs: Path, tries: int, timing: dict) -> dict:
    out: dict = {}
    for row in ROWS:
        dirs = [d for d in run_dirs(row, runs, tries) if (d / "log.txt").is_file()]
        if not dirs:
            continue
        rows = []
        for d in dirs:
            numbers = {"run": str(d.relative_to(runs)), **timing.get(str(d.relative_to(runs)), {}), **read_run(row, d)}
            frames, _ = frames_of(row, d)
            if frames:
                numbers["figure_heights"] = figure_heights(frames)
            rows.append(numbers)
        ok = [k for k, r in enumerate(rows) if delivered(r)]
        # the page shows a delivered try with no fault kept, the evenest of those (highest lowest step IoU); else the
        # evenest delivered try; else the first try
        clean = [k for k in ok if not any(rows[k].get("kept_faults") or [])] or ok
        shown = max(clean, key=lambda k: min(rows[k].get("step_iou") or [1.0])) if clean else 0
        out[row] = {"delivered": len(ok), "of": len(rows), "shown": shown, "tries": rows}
    return out


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    try:
        return ImageFont.truetype(f"/usr/share/fonts/truetype/dejavu/{name}", size)
    except OSError:
        return ImageFont.load_default()


def note(row: str, entry: dict) -> str:
    numbers = entry["tries"][entry["shown"]]
    count = f"try {entry['shown'] + 1}, {entry['delivered']}/{entry['of']} delivered; " if entry["of"] > 1 else ""
    if row.startswith("chain"):
        made = "made" if all(numbers["bridge_made"]) else "refused: " + ", ".join(
            "+".join(f) for f in numbers["bridge_faults"] if f)
        kept = sorted({f for k in numbers["kept_faults"] for f in k})
        return (f"{numbers['status']}; join {numbers['join_steps']} steps; frames between {made}"
                + (f"; kept {'+'.join(kept)}" if kept else ""))
    if not delivered(numbers):
        return count + "refused: " + (numbers["error"] or "").split("refused — ")[-1][:90]
    kept = sorted({f for k in numbers["kept_faults"] for f in k})
    return (count + f"ends IoU {numbers['endpoint_iou'][0]}/{numbers['endpoint_iou'][1]}; steps {numbers['step_iou']}"
            + (f"; judged {'+'.join(kept)}" if kept else ""))


def _panel(im: Image.Image, zoom: float, size: tuple[int, int], refused: bool) -> Image.Image:
    """The frame at `zoom`, its canvas bottom on the panel's, a grey line on the floor (the canvas bottom)."""
    small = im.resize((max(1, round(im.width * zoom)), max(1, round(im.height * zoom))), Image.Resampling.LANCZOS)
    panel = Image.new("RGBA", size, (250, 228, 228, 255) if refused else (238, 238, 238, 255))
    ImageDraw.Draw(panel).line([(0, size[1] - 1), (size[0], size[1] - 1)], fill=(150, 150, 150, 255), width=1)
    panel.alpha_composite(small, ((size[0] - small.width) // 2, size[1] - small.height))
    return panel


def _empty(size: tuple[int, int]) -> Image.Image:
    """The panel of a row that delivered no frames (every try refused before frames were made)."""
    panel = Image.new("RGBA", size, (250, 228, 228, 255))
    ImageDraw.Draw(panel).text((10, size[1] // 2 - 14), "refused:\nno frames delivered", font=_font(12, True),
                               fill=(150, 0, 0))
    return panel


def render(runs: Path, summary: dict, tries: int, show: Path) -> list[Path]:
    """The GIF (three rows playing) and the sheet (every row's four frames), one zoom for every frame."""
    rows = {}
    for row in ROWS:
        entry = summary[row]
        frames, refused = frames_of(row, run_dirs(row, runs, tries)[entry["shown"]])
        # the canvas rows above every figure are empty; crop them so the figures fill the panel (one crop per row)
        if frames:
            top = min(f.getchannel("A").getbbox()[1] for f in frames)
            frames = [f.crop((0, max(0, top - 8), f.width, f.height)) for f in frames]
        rows[row] = (frames, refused, entry)
    tallest = max(f.height for fr, _, _ in rows.values() if fr for f in fr)
    widest = max(f.width for fr, _, _ in rows.values() if fr for f in fr)
    ph = 230
    zoom = ph / tallest
    pw = round(widest * zoom) + 8
    caption = ["A", "between 1/2", "between 2/2", "B"]
    head = WHAT
    lab = 230
    sheet = Image.new("RGBA", (lab + 4 * (pw + 4), 40 + len(ROWS) * (ph + 6)), "white")
    d = ImageDraw.Draw(sheet)
    d.text((8, 8), head, font=_font(15, True), fill="black")
    for k, c in enumerate(caption):
        d.text((lab + k * (pw + 4) + 4, 26), c, font=_font(11, True), fill=(150, 0, 0) if k in (1, 2) else (0, 100, 0))
    for i, row in enumerate(ROWS):
        frames, refused, entry = rows[row]
        y = 40 + i * (ph + 6)
        d.text((8, y + 4), TITLES[row], font=_font(12, True), fill=(0, 0, 120))
        for j, line in enumerate(_wrap(note(row, entry), 34)[:11]):
            d.text((8, y + 22 + j * 14), line, font=_font(10), fill=(150, 0, 0) if refused else (50, 50, 50))
        if not frames:
            sheet.alpha_composite(_empty((pw, ph)), (lab, y))
        for k, im in enumerate(frames or []):
            sheet.alpha_composite(_panel(im, zoom, (pw, ph), refused), (lab + k * (pw + 4), y))
    out_png = show / f"{NAME}.png"
    sheet.convert("RGB").quantize(colors=128).save(out_png, optimize=True)
    shown = [r for r in ROWS if r != "chain"]
    pages = []
    for k in range(4):
        page = Image.new("RGBA", (len(shown) * (pw + 6) + 6, ph + 90), "white")
        d = ImageDraw.Draw(page)
        d.text((6, 4), head, font=_font(12, True), fill="black")
        for i, row in enumerate(shown):
            frames, refused, entry = rows[row]
            x = 6 + i * (pw + 6)
            d.text((x, 22), TITLES[row], font=_font(10, True), fill=(0, 0, 120))
            page.alpha_composite(_panel(frames[k], zoom, (pw, ph), refused) if frames else _empty((pw, ph)), (x, 38))
            d.text((x + 4, 40), caption[k] + (" (refused)" if refused else ""), font=_font(10, True),
                   fill=(150, 0, 0) if k in (1, 2) or refused else (0, 100, 0))
            for j, line in enumerate(_wrap(note(row, entry), 36)[:4]):
                d.text((x, 38 + ph + 2 + j * 12), line, font=_font(9), fill=(60, 60, 60))
        pages.append(page.convert("RGB").quantize(colors=96))
    out_gif = show / f"{NAME}.gif"
    pages[0].save(out_gif, save_all=True, append_images=pages[1:], duration=[700, 350, 350, 700], loop=0,
                  optimize=True)
    return [out_gif, out_png]


def _wrap(text: str, width: int) -> list[str]:
    lines, line = [], ""
    for word in text.split(" "):
        if line and len(line) + len(word) + 1 > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    return lines + [line]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--provider", default="grok", help="the transition rows' provider (default grok)")
    parser.add_argument("--n", type=int, default=2, help="frames between (default 2)")
    parser.add_argument("--tries", type=int, default=3, help="tries of each transition row (default 3)")
    parser.add_argument("--jobs", type=int, default=4, help="runs at once (default 4)")
    parser.add_argument("--no-provider", action="store_true", help="run the chain rows only")
    parser.add_argument("--render-only", action="store_true", help="draw the pages again from an earlier --out-dir")
    parser.add_argument("--resume", action="store_true",
                        help="in an earlier --out-dir, run again only what made nothing (no report, or status error: "
                             "an expired login, a timeout); a delivered or refused try stands")
    args = parser.parse_args(argv)
    out = args.out_dir.resolve()
    states_path = out / "states.json"
    if args.render_only or args.resume:
        states = {c: out / "states" / c / "keyed" for c in CLIPS}
        built = json.loads(states_path.read_text()) if states_path.is_file() else {}
    else:
        states, built = build_states(out)
        states_path.write_text(json.dumps(built, indent=2) + "\n")
    runs = out / "runs"
    timing_path = out / "timing.json"
    timing = json.loads(timing_path.read_text()) if timing_path.is_file() else {}
    if not args.render_only:
        todo = [(row, d) for row in ROWS if not (args.no_provider and row in TRANSITION_ROWS)
                for d in run_dirs(row, runs, args.tries) if not (args.resume and stands(row, d))]
        with ThreadPoolExecutor(max(1, args.jobs)) as pool:
            for (row, d), result in zip(todo, pool.map(lambda t: execute(t[0], t[1], states, args.provider, args.n),
                                                       todo)):
                timing[str(d.relative_to(runs))] = result
                print(f"{d.relative_to(runs)}: exit {result['exit']} in {result['seconds']} s", file=sys.stderr)
        timing_path.write_text(json.dumps(timing, indent=2) + "\n")
    summary = summarise(runs, args.tries, timing)
    show = out / "showcase"
    show.mkdir(exist_ok=True)
    written = render(runs, summary, args.tries, show) if all(r in summary for r in ROWS) else []
    (out / "summary.json").write_text(json.dumps({"join": WHAT, "provider": args.provider, "n": args.n,
                                                  "tries": args.tries, "states": built, "rows": summary},
                                                 indent=2) + "\n")
    big = [p.name for p in written if p.stat().st_size >= 1_000_000]
    print(json.dumps({"summary": str(out / "summary.json"), "showcase": [str(p) for p in written], "over_1mb": big}))
    return 1 if big else 0


if __name__ == "__main__":
    raise SystemExit(main())
