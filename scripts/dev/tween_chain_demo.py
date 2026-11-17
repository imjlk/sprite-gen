# SPDX-License-Identifier: Apache-2.0
"""`tween` beside `video-chain` on three joins between two Pip clips (docs/tween.md "tween and video-chain").

The frames are the repository's own: `tests/fixtures/rife-cuts/pip-jump` (29 keyed frames) and `pip-run` (26), put on
one canvas (bottom-aligned, centred, the larger of the two sizes and a margin), as `video-chain` takes pieces. Each
join is a last frame of one clip into a first frame of the other, at three pose distances:

- `t1` jump:28 -> run:0, a take-off 130 px off the floor into a stride (the nearest, 2.2 steps on `video-chain`'s
  join reading);
- `t3` jump:13 -> run:7, standing into a full stride (3.2 steps);
- `t2` run:25 -> jump:0, a stride into a fall 164 px off the floor (the farthest, 4.6 steps).

For each, two frames between, five ways, every one through the CLI in a subprocess as a user runs it:

| row | command |
|---|---|
| `strip` | `tween --method strip` (`--ground shared`, the default) |
| `strip-source` | `tween --method strip --ground source` |
| `video` | `tween --method video` (every fault of the judge refuses, the default) |
| `chain-held` | `video-chain --piece A=..:F-3:F --piece B=..:T:T+3 --bridge 2 --refuse held` |
| `chain` | the same with every fault refusing (the default) |

The tween rows call the provider (`--provider`, grok by default: one image or one clip each, billed per call where
the login does not cover it); `--no-provider` runs the chain rows only, and `--render-only` draws the pages again from
an earlier `--out-dir`. RIFE must be installed for the chain rows (`sprite-gen rife install`).

    .venv/bin/python scripts/dev/tween_chain_demo.py --out-dir <a new directory> [--jobs 3]

Writes `<out-dir>/runs/<join>/<row>/` (each verb's own folder and report), `<out-dir>/summary.json` (each row's exit
code, seconds and the numbers its report gives), and `<out-dir>/showcase/`: `tween-vs-chain-<join>.gif` (four rows
playing A, the two frames between, B) and `tween-vs-chain-<join>.png` (all five rows' frames on one sheet), each under
1 MB. The provider draws anew on every run, so the tween rows' numbers are one run's, not a fixed result.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "rife-cuts"
CLIPS = ("pip-jump", "pip-run")
JOINS = {  # name: (from clip, from frame, to clip, to frame, what it is)
    "t1": ("pip-jump", 28, "pip-run", 0, "jump:28 (take-off, 130 px up) -> run:0"),
    "t3": ("pip-jump", 13, "pip-run", 7, "jump:13 (standing) -> run:7 (full stride)"),
    "t2": ("pip-run", 25, "pip-jump", 0, "run:25 (stride) -> jump:0 (falling, 164 px up)"),
}
ROWS = ("strip", "strip-source", "video", "chain-held", "chain")
TITLES = {"strip": "tween strip", "strip-source": "tween strip --ground source", "video": "tween video",
          "chain-held": "video-chain --refuse held", "chain": "video-chain (default)"}
DESCRIPTION = ("chibi wizard kid Pip: big indigo pointed hood with a yellow bell tip, orange scarf, cream tunic, "
               "brown satchel and boots, side view facing right")
MARGIN = (12, 13)  # px added to the larger clip's width and height: the canvas of the comparison
FPS = 24


def build_states(out: Path) -> dict[str, Path]:
    """Every fixture frame on one canvas, bottom-aligned and centred, as keyed PNGs `frame-0001.png` on."""
    frames = {c: sorted((FIXTURES / c).glob("*.webp")) for c in CLIPS}
    sizes = [Image.open(p).size for c in CLIPS for p in frames[c][:1]]
    w, h = max(s[0] for s in sizes) + MARGIN[0], max(s[1] for s in sizes) + MARGIN[1]
    dirs = {}
    for clip in CLIPS:
        keyed = out / "states" / clip / "keyed"
        keyed.mkdir(parents=True, exist_ok=True)
        for k, path in enumerate(frames[clip]):
            im = Image.open(path).convert("RGBA")
            canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            canvas.alpha_composite(im, ((w - im.width) // 2, h - im.height))
            canvas.save(keyed / f"frame-{k + 1:04d}.png")
        dirs[clip] = keyed
    return dirs


def command(row: str, join: str, states: dict[str, Path], run: Path, provider: str, n: int) -> list[str]:
    a, fa, b, fb, _ = JOINS[join]
    if row.startswith("chain"):
        args = ["video-chain", "--piece", f"A={states[a]}:{fa - 3}:{fa}", "--piece", f"B={states[b]}:{fb}:{fb + 3}",
                "--bridge", str(n), "--key", "green", "--fps", str(FPS), "--out-dir", str(run)]
        return args + (["--refuse", "held"] if row == "chain-held" else [])
    args = ["tween", "--from-file", str(states[a] / f"frame-{fa + 1:04d}.png"),
            "--to-file", str(states[b] / f"frame-{fb + 1:04d}.png"), "--n", str(n), "--provider", provider,
            "--description", DESCRIPTION, "--out-dir", str(run), "--fps", str(FPS), "--include-ends", "--force",
            "--method", "video" if row == "video" else "strip"]
    return args + (["--ground", "source"] if row == "strip-source" else [])


def execute(row: str, join: str, states: dict[str, Path], runs: Path, provider: str, n: int) -> dict:
    run = runs / join / row
    run.mkdir(parents=True, exist_ok=True)
    began = time.monotonic()
    proc = subprocess.run([sys.executable, "-m", "sprite_gen.cli", *command(row, join, states, run, provider, n)],
                          capture_output=True, text=True)
    (run / "log.txt").write_text(proc.stdout + proc.stderr, encoding="utf-8")
    return {"exit": proc.returncode, "seconds": round(time.monotonic() - began, 1)}


def read_row(row: str, run: Path) -> dict:
    """The numbers a row's report gives, for the summary and the pages."""
    if row.startswith("chain"):
        rep = json.loads((run / "chain.report.json").read_text(encoding="utf-8"))
        bridges = rep.get("chain", {}).get("bridges", [])
        return {"status": rep["status"], "refuse": rep["refuse"],
                "join_steps": next((j.get("steps") for j in rep.get("joins", [])), None),
                "bridge_faults": [sorted({f for t in b["refused"] for f in t["faults"]}) for b in bridges],
                "bridge_made": [b["judged"] is not None for b in bridges],
                "kept_faults": [b.get("kept_faults", []) for b in bridges], "error": rep.get("error")}
    rep = json.loads((run / "tween.json").read_text(encoding="utf-8"))
    em = rep.get("endpoint_match", {})
    return {"status": rep["status"], "endpoint_iou": [em.get("iou_from"), em.get("iou_to")],
            "scale_agree": em.get("scale_agree"), "frame_scale": rep.get("frame_scale", {}).get("drawn_vs_endpoints"),
            "step_iou": rep.get("step_iou"), "drawn_step_iou": rep.get("drawn_step_iou"),
            "lift_px": rep.get("lift_px"), "refuse": rep.get("refuse"),
            "judge_faults": [r["faults"] for r in rep.get("judge", [])],
            "kept_faults": [r.get("kept_faults", []) for r in rep.get("judge", [])],
            "shown_again": (rep.get("read") or {}).get("unlisted"), "warnings": len(rep.get("warnings", [])),
            "error": rep.get("error")}


def frames_of(row: str, run: Path) -> tuple[list[Image.Image] | None, bool]:
    """A, the two frames between, B as the row delivered them, or as it left them refused; None where none were made."""
    if row.startswith("chain"):
        keyed = run / "keyed"
        folder, refused = (keyed, False) if keyed.is_dir() else (run / "keyed.refused", True)
        files = [folder / f"frame-{k:04d}.png" for k in (4, 5, 6, 7)]
    else:
        folder, refused = (run, False) if (run / "tween-0.png").is_file() else (run / "refused", True)
        files = [folder / n for n in ("from.png", "tween-0.png", "tween-1.png", "to.png")]
    if not all(f.is_file() for f in files):
        return None, True
    return [Image.open(f).convert("RGBA") for f in files], refused


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    try:
        return ImageFont.truetype(f"/usr/share/fonts/truetype/dejavu/{name}", size)
    except OSError:
        return ImageFont.load_default()


def note(row: str, numbers: dict) -> str:
    if row.startswith("chain"):
        made = "made" if all(numbers["bridge_made"]) else "refused: " + ", ".join(
            "+".join(f) for f in numbers["bridge_faults"] if f)
        return f"{numbers['status']}; join {numbers['join_steps']} steps; frames between {made}"
    if numbers["status"] != "ok":
        return "refused: " + (numbers["error"] or "").split("refused — ")[-1][:90]
    kept = sorted({f for k in numbers["kept_faults"] for f in k})
    return (f"ok; ends IoU {numbers['endpoint_iou'][0]}/{numbers['endpoint_iou'][1]}; steps {numbers['step_iou']}"
            + (f"; judged {'+'.join(kept)}" if kept else ""))


def _panel(im: Image.Image, zoom: float, size: tuple[int, int], refused: bool) -> Image.Image:
    small = im.resize((max(1, round(im.width * zoom)), max(1, round(im.height * zoom))), Image.Resampling.LANCZOS)
    panel = Image.new("RGBA", size, (250, 228, 228, 255) if refused else (238, 238, 238, 255))
    panel.alpha_composite(small, ((size[0] - small.width) // 2, max(0, size[1] - small.height)))
    return panel


def render(join: str, runs: Path, summary: dict, show: Path, height: int) -> list[Path]:
    """The GIF (four rows playing) and the sheet (five rows, the four frames each) of one join."""
    rows = {row: (*frames_of(row, runs / join / row), summary[join][row]) for row in ROWS}
    zoom = 230 / height
    pw, ph = round(700 * zoom), 230  # wider than the canvas: a video canvas and a hood tip reach past it
    caption = ["A", "between 1/2", "between 2/2", "B"]
    head = f"{join}: {JOINS[join][4]}"
    # the sheet
    lab = 230
    sheet = Image.new("RGBA", (lab + 4 * (pw + 4), 40 + len(ROWS) * (ph + 6)), "white")
    d = ImageDraw.Draw(sheet)
    d.text((8, 8), head, font=_font(15, True), fill="black")
    for k, c in enumerate(caption):
        d.text((lab + k * (pw + 4) + 4, 26), c, font=_font(11, True), fill=(150, 0, 0) if k in (1, 2) else (0, 100, 0))
    for i, row in enumerate(ROWS):
        frames, refused, numbers = rows[row]
        y = 40 + i * (ph + 6)
        d.text((8, y + 4), TITLES[row], font=_font(12, True), fill=(0, 0, 120))
        for j, line in enumerate(_wrap(note(row, numbers), 34)[:9]):
            d.text((8, y + 22 + j * 14), line, font=_font(10), fill=(150, 0, 0) if refused else (50, 50, 50))
        for k, im in enumerate(frames or []):
            sheet.alpha_composite(_panel(im, zoom, (pw, ph), refused), (lab + k * (pw + 4), y))
    out_png = show / f"tween-vs-chain-{join}.png"
    sheet.convert("RGB").quantize(colors=128).save(out_png, optimize=True)
    # the GIF: the four rows a reader compares, A -> between -> B
    shown = [r for r in ROWS if r != "chain"]
    pages = []
    for k in range(4):
        page = Image.new("RGBA", (len(shown) * (pw + 6) + 6, ph + 76), "white")
        d = ImageDraw.Draw(page)
        d.text((6, 4), head, font=_font(12, True), fill="black")
        for i, row in enumerate(shown):
            frames, refused, numbers = rows[row]
            x = 6 + i * (pw + 6)
            d.text((x, 22), TITLES[row], font=_font(10, True), fill=(0, 0, 120))
            if frames:
                page.alpha_composite(_panel(frames[k], zoom, (pw, ph), refused), (x, 38))
            d.text((x + 4, 40), caption[k] + (" (refused)" if refused else ""), font=_font(10, True),
                   fill=(150, 0, 0) if k in (1, 2) or refused else (0, 100, 0))
            for j, line in enumerate(_wrap(note(row, numbers), 30)[:2]):
                d.text((x, 38 + ph + 2 + j * 12), line, font=_font(9), fill=(60, 60, 60))
        pages.append(page.convert("RGB").quantize(colors=96))
    out_gif = show / f"tween-vs-chain-{join}.gif"
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
    parser.add_argument("--provider", default="grok", help="the tween rows' provider (default grok)")
    parser.add_argument("--n", type=int, default=2, help="frames between (default 2)")
    parser.add_argument("--only", default=",".join(JOINS), help="joins to run, comma separated (default t1,t3,t2)")
    parser.add_argument("--jobs", type=int, default=3, help="rows run at once (default 3)")
    parser.add_argument("--no-provider", action="store_true", help="run the chain rows only")
    parser.add_argument("--render-only", action="store_true", help="draw the pages again from an earlier --out-dir")
    args = parser.parse_args(argv)
    out = args.out_dir.resolve()
    joins = [j.strip() for j in args.only.split(",") if j.strip()]
    if unknown := [j for j in joins if j not in JOINS]:
        parser.error(f"--only: no join {unknown}; joins are {', '.join(JOINS)}")
    states = build_states(out) if not args.render_only else {c: out / "states" / c / "keyed" for c in CLIPS}
    runs = out / "runs"
    timing_path = out / "timing.json"
    timing = json.loads(timing_path.read_text()) if timing_path.is_file() else {}
    if not args.render_only:
        todo = [(row, j) for j in joins for row in ROWS if not (args.no_provider and not row.startswith("chain"))]
        with ThreadPoolExecutor(max(1, args.jobs)) as pool:
            for (row, j), result in zip(todo, pool.map(lambda t: execute(*t, states, runs, args.provider, args.n),
                                                       todo)):
                timing.setdefault(j, {})[row] = result
                print(f"{j} {row}: exit {result['exit']} in {result['seconds']} s", file=sys.stderr)
        timing_path.write_text(json.dumps(timing, indent=2) + "\n")
    summary = {j: {row: {**timing.get(j, {}).get(row, {}), **read_row(row, runs / j / row)}
                   for row in ROWS if (runs / j / row).is_dir()} for j in joins}
    show = out / "showcase"
    show.mkdir(exist_ok=True)
    height = Image.open(next(iter(states.values())) / "frame-0001.png").height
    written = [p for j in joins if len(summary[j]) == len(ROWS) for p in render(j, runs, summary, show, height)]
    (out / "summary.json").write_text(json.dumps({"provider": args.provider, "n": args.n, "joins": {
        j: {"what": JOINS[j][4], **summary[j]} for j in joins}}, indent=2) + "\n")
    big = [p.name for p in written if p.stat().st_size >= 1_000_000]
    print(json.dumps({"summary": str(out / "summary.json"), "showcase": [str(p) for p in written], "over_1mb": big}))
    return 1 if big else 0


if __name__ == "__main__":
    raise SystemExit(main())
