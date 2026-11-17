# SPDX-License-Identifier: Apache-2.0
"""`tween` beside `video-chain` on four joins between frames of two Pip clips (docs/tween.md "tween and video-chain").

The frames are the repository's own: `tests/fixtures/rife-cuts/pip-jump` (29 keyed frames: rising 0-4 to an apex 208 px
off the floor, falling 5-9, landed 10-25, taking off 26-28) and `pip-run` (26), put on one canvas (bottom-aligned,
centred, the larger of the two sizes and a margin), as `video-chain` takes pieces. Each join is a frame of one clip
into a frame of the other (or of the same clip, `t4`), at three pose distances and two kinds of height:

- `t1` jump:28 -> run:0, a take-off 130 px off the floor into a stride on it (2.2 steps on `video-chain`'s join reading);
- `t2` run:25 -> jump:0, a stride on the floor into a rise 164 px off it (4.6 steps);
- `t3` jump:13 -> run:7, standing into a full stride, both on the floor (3.2 steps): the control, no height to keep;
- `t4` jump:6 -> jump:12, falling 182 px off the floor into the landed crouch. The clip's own frames 8 and 10 are a
  third and two thirds of the way, so they are the frames to compare against (the `truth` row and `truth` numbers).

For each, two frames between, five ways, every one through the CLI in a subprocess as a user runs it:

| row | command |
|---|---|
| `strip-shared` | `tween --method strip --ground shared` (every foot on one ground row) |
| `strip` | `tween --method strip` (`--ground auto`, the default: the height put back where one end is off the floor) |
| `strip-places` | `t4` only: the same with `--places runs/t4/places.json`, the places of the clip's own frames 8 and 10 (each one's height off the floor, and its anchor's distance from the line between the ends'): what a caller who has the motion gives |
| `video` | `tween --method video` (`--ground auto`; every fault of the judge refuses, the default) |
| `chain-held` | `video-chain --piece A=..:F-3:F --piece B=..:T:T+3 --bridge 2 --refuse held` |
| `chain` | the same with every fault refusing (the default) |

The tween rows call the provider (`--provider`, grok by default: one image or one clip a try, billed per call where the
login does not cover it) `--tries` times each (default 3), since the provider draws anew on every call: the summary
counts the tries delivered and the pages show the first delivered (or the first refused). The chain rows run once
(RIFE gives the same frames every run). `--no-provider` runs the chain rows only, and `--render-only` draws the pages
again from an earlier `--out-dir`. RIFE must be installed for the chain rows (`sprite-gen rife install`).

    .venv/bin/python scripts/dev/tween_chain_demo.py --out-dir <a new directory> [--tries 3] [--jobs 5]

Writes `<out-dir>/runs/<join>/<row>/[try-<k>/]` (each verb's own folder and report), `<out-dir>/summary.json` (each
try's exit code, seconds and the numbers its report gives, each row's frames' height off the floor, `rise_px`, and on
`t4` each frame's silhouette IoU with the clip's own frame, its height error and its anchor's error off the line between
the ends'), and `<out-dir>/showcase/`:
`tween-vs-chain-<join>.gif` (four rows playing A, the two frames between, B, `t4` with the clip's own frames beside
them; a grey line on the lower end's floor) and `tween-vs-chain-<join>.png` (every row on one sheet), each under 1 MB.
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
    "t1": ("pip-jump", 28, "pip-run", 0, "jump:28 (take-off, 130 px up) -> run:0 (on the floor)"),
    "t2": ("pip-run", 25, "pip-jump", 0, "run:25 (on the floor) -> jump:0 (rising, 164 px up)"),
    "t3": ("pip-jump", 13, "pip-run", 7, "jump:13 (standing) -> run:7 (full stride), both on the floor"),
    "t4": ("pip-jump", 6, "pip-jump", 12, "jump:6 (falling, 182 px up) -> jump:12 (landed); the clip's 8 and 10 between"),
}
TRUTH = {"t4": ("pip-jump", (6, 8, 10, 12))}  # the clip's own frames at A, 1/3, 2/3, B
ROWS = ("strip-shared", "strip", "video", "chain-held", "chain")
TWEEN_ROWS = ("strip-shared", "strip", "strip-places", "video")
PLACES_FILE = "places.json"  # runs/<join>/places.json: the clip's own frames' places, for the strip-places row
TITLES = {"strip-shared": "tween strip --ground shared", "strip": "tween strip (--ground auto)",
          "video": "tween video (--ground auto)", "chain-held": "video-chain --refuse held",
          "chain": "video-chain (default)", "truth": "the clip's own frames",
          "strip-places": "tween strip --places"}
DESCRIPTION = ("chibi wizard kid Pip: big indigo pointed hood with a yellow bell tip, orange scarf, cream tunic, "
               "brown satchel and boots, side view facing right")
MARGIN = (12, 13)  # px added to the larger clip's width and height: the canvas of the comparison
FPS = 24


def rows_of(join: str) -> tuple[str, ...]:
    """The rows of a join: where the clip's own frames are known, a strip placed where they are, after the strip."""
    return ROWS[:2] + ("strip-places",) + ROWS[2:] if join in TRUTH else ROWS


def anchor_place(frame: Image.Image) -> tuple[float, float]:
    """A frame's foot anchor on its canvas: (x, its lowest opaque row's height off the canvas floor)."""
    from sprite_gen.effects.tween import foot_x
    box = frame.getchannel("A").getbbox()
    return box[0] + foot_x(frame.crop(box)), float(frame.height - box[3])


def places_from_truth(join: str, states: dict[str, Path]) -> dict:
    """`--places` read off the clip's own frames between: each one's height off the floor (dy, negative up) and its
    anchor's distance from the straight line between the ends' anchors (dx): what a caller who has the clip gives."""
    truth = truth_frames(join, states)
    (xa, _), (xb, _) = anchor_place(truth[0]), anchor_place(truth[-1])
    places = []
    for k, frame in enumerate(truth[1:-1], start=1):
        x, up = anchor_place(frame)
        t = k / (len(truth) - 1)
        places.append({"dx": round(x - (xa + (xb - xa) * t), 1), "dy": -up})
    clip, picks = TRUTH[join]
    return {"from": f"{clip} frames {', '.join(str(p) for p in picks[1:-1])} (the clip's own)", "places": places}


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
    if row == "strip-places":
        args += ["--places", str(run.parents[1] / PLACES_FILE)]
    return args + (["--ground", "shared"] if row == "strip-shared" else [])


def run_dirs(row: str, join: str, runs: Path, tries: int) -> list[Path]:
    """Where a row's runs go: one folder for a chain row, `try-<k>` for each try of a tween row."""
    base = runs / join / row
    return [base] if row.startswith("chain") else [base / f"try-{k}" for k in range(1, tries + 1)]


def stands(row: str, run: Path) -> bool:
    """An earlier run that made something: a report whose status is not error."""
    report = run / ("chain.report.json" if row.startswith("chain") else "tween.json")
    try:
        return json.loads(report.read_text(encoding="utf-8")).get("status") != "error"
    except (OSError, ValueError):
        return False


def execute(row: str, join: str, run: Path, states: dict[str, Path], provider: str, n: int) -> dict:
    run.mkdir(parents=True, exist_ok=True)
    began = time.monotonic()
    proc = subprocess.run([sys.executable, "-m", "sprite_gen.cli", *command(row, join, states, run, provider, n)],
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
    rep = json.loads((run / "tween.json").read_text(encoding="utf-8"))
    em = rep.get("endpoint_match", {})
    return {"status": rep["status"], "endpoint_iou": [em.get("iou_from"), em.get("iou_to")],
            "scale_agree": em.get("scale_agree"), "frame_scale": rep.get("frame_scale", {}).get("drawn_vs_endpoints"),
            "step_iou": rep.get("step_iou"), "drawn_step_iou": rep.get("drawn_step_iou"),
            "ground": rep.get("ground"), "lift_px": rep.get("lift_px"), "refuse": rep.get("refuse"),
            "judge_faults": [r["faults"] for r in rep.get("judge", [])],
            "kept_faults": [r.get("kept_faults", []) for r in rep.get("judge", [])],
            "shown_again": (rep.get("read") or {}).get("unlisted"), "warnings": len(rep.get("warnings", [])),
            "error": rep.get("error")}


def delivered(row: str, numbers: dict) -> bool:
    return numbers.get("status") in ("ok", "passed")


def frames_of(row: str, run: Path) -> tuple[list[Image.Image] | None, bool]:
    """A, the two frames between, B as the run delivered them, or as it left them refused; None where none were made."""
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


def truth_frames(join: str, states: dict[str, Path]) -> list[Image.Image] | None:
    if join not in TRUTH:
        return None
    clip, picks = TRUTH[join]
    return [Image.open(states[clip] / f"frame-{k + 1:04d}.png").convert("RGBA") for k in picks]


def rise_px(frames: list[Image.Image]) -> list[int]:
    """Each frame's lowest opaque row above the lower end's, in px of its own canvas (one canvas a row)."""
    bottoms = [f.getchannel("A").getbbox()[3] for f in frames]
    floor = max(bottoms[0], bottoms[-1])
    return [floor - b for b in bottoms]


def against_truth(frames: list[Image.Image], truth: list[Image.Image]) -> dict:
    """The frames between beside the clip's own: silhouette IoU at the clip frame's height, foot on foot, and the
    height error. A frame drawn at another scale than the clip's is compared at the clip's scale."""
    from sprite_gen.effects.tween import _scaled, _tight, silhouette_iou
    iou = []
    for got, want in zip(frames[1:-1], truth[1:-1]):
        g, w = _tight(got), _tight(want)
        iou.append(round(silhouette_iou(_scaled(g, w.height), w, "foot"), 3))
    got, want = rise_px(frames), rise_px(truth)

    def off_line(fs):  # each frame between's anchor x off the straight line between the ends' anchors
        xs = [anchor_place(f)[0] for f in fs]
        return [xs[k] - (xs[0] + (xs[-1] - xs[0]) * k / (len(xs) - 1)) for k in range(1, len(xs) - 1)]
    return {"iou": iou, "rise_px": want, "rise_error_px": [g - w for g, w in zip(got[1:-1], want[1:-1])],
            "anchor_error_px": [round(g - w, 1) for g, w in zip(off_line(frames), off_line(truth))]}


def summarise(join: str, runs: Path, states: dict[str, Path], tries: int, timing: dict) -> dict:
    out: dict = {}
    truth = truth_frames(join, states)
    if truth:
        out["truth"] = {"rise_px": rise_px(truth)}
    for row in rows_of(join):
        dirs = [d for d in run_dirs(row, join, runs, tries) if (d / "log.txt").is_file()]
        if not dirs:
            continue
        rows = []
        for d in dirs:
            numbers = {**timing.get(join, {}).get(str(d.relative_to(runs)), {}), **read_run(row, d)}
            frames, _ = frames_of(row, d)
            if frames:
                numbers["rise_px"] = rise_px(frames)
                if truth:
                    numbers["truth"] = against_truth(frames, truth)
            rows.append(numbers)
        ok = [k for k, r in enumerate(rows) if delivered(row, r)]
        out[row] = {"delivered": len(ok), "of": len(rows), "shown": ok[0] if ok else 0, "tries": rows}
    return out


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    try:
        return ImageFont.truetype(f"/usr/share/fonts/truetype/dejavu/{name}", size)
    except OSError:
        return ImageFont.load_default()


def note(row: str, entry: dict) -> str:
    if row == "truth":
        return f"heights off the floor {entry['rise_px']} px"
    numbers = entry["tries"][entry["shown"]]
    count = f"{entry['delivered']}/{entry['of']} delivered; " if entry["of"] > 1 else ""
    rise = f"; up {numbers['rise_px']} px" if numbers.get("rise_px") else ""
    if row.startswith("chain"):
        made = "made" if all(numbers["bridge_made"]) else "refused: " + ", ".join(
            "+".join(f) for f in numbers["bridge_faults"] if f)
        return f"{numbers['status']}; join {numbers['join_steps']} steps; frames between {made}{rise}"
    if not delivered(row, numbers):
        return count + "refused: " + (numbers["error"] or "").split("refused — ")[-1][:90]
    kept = sorted({f for k in numbers["kept_faults"] for f in k})
    truth = f"; IoU with the clip's {numbers['truth']['iou']}" if numbers.get("truth") else ""
    return (count + f"ends IoU {numbers['endpoint_iou'][0]}/{numbers['endpoint_iou'][1]}{rise}{truth}"
            + (f"; judged {'+'.join(kept)}" if kept else ""))


def _panel(im: Image.Image, zoom: float, size: tuple[int, int], refused: bool, floor: int | None) -> Image.Image:
    """The frame at `zoom`, its canvas bottom on the panel's, a grey line on the row's floor (`floor`: canvas row)."""
    small = im.resize((max(1, round(im.width * zoom)), max(1, round(im.height * zoom))), Image.Resampling.LANCZOS)
    panel = Image.new("RGBA", size, (250, 228, 228, 255) if refused else (238, 238, 238, 255))
    top = size[1] - small.height
    if floor is not None:
        y = top + round(floor * zoom)
        ImageDraw.Draw(panel).line([(0, y), (size[0], y)], fill=(150, 150, 150, 255), width=1)
    panel.alpha_composite(small, ((size[0] - small.width) // 2, max(0, top)))
    return panel


def render(join: str, runs: Path, summary: dict, states: dict[str, Path], tries: int, show: Path,
           height: int) -> list[Path]:
    """The GIF (four rows playing, and the clip's own frames where there are) and the sheet (every row's four frames)."""
    rows = {}
    for row in rows_of(join):
        entry = summary[join][row]
        frames, refused = frames_of(row, run_dirs(row, join, runs, tries)[entry["shown"]])
        rows[row] = (frames, refused, entry)
    truth = truth_frames(join, states)
    order = list(rows_of(join)) + (["truth"] if truth else [])
    if truth:
        rows["truth"] = (truth, False, summary[join]["truth"])
    zoom = 200 / height
    pw, ph = round(640 * zoom), 236  # wider and taller than the canvas: a video canvas, a hood tip and a height reach past it
    caption = ["A", "between 1/2", "between 2/2", "B"]
    head = f"{join}: {JOINS[join][4]}"

    def floor(frames):
        return max(f.getchannel("A").getbbox()[3] for f in (frames[0], frames[-1])) if frames else None
    lab = 230
    sheet = Image.new("RGBA", (lab + 4 * (pw + 4), 40 + len(order) * (ph + 6)), "white")
    d = ImageDraw.Draw(sheet)
    d.text((8, 8), head, font=_font(15, True), fill="black")
    for k, c in enumerate(caption):
        d.text((lab + k * (pw + 4) + 4, 26), c, font=_font(11, True), fill=(150, 0, 0) if k in (1, 2) else (0, 100, 0))
    for i, row in enumerate(order):
        frames, refused, entry = rows[row]
        y = 40 + i * (ph + 6)
        d.text((8, y + 4), TITLES[row], font=_font(12, True), fill=(0, 0, 120))
        for j, line in enumerate(_wrap(note(row, entry), 34)[:11]):
            d.text((8, y + 22 + j * 14), line, font=_font(10), fill=(150, 0, 0) if refused else (50, 50, 50))
        for k, im in enumerate(frames or []):
            sheet.alpha_composite(_panel(im, zoom, (pw, ph), refused, floor(frames)), (lab + k * (pw + 4), y))
    out_png = show / f"tween-vs-chain-{join}.png"
    sheet.convert("RGB").quantize(colors=128).save(out_png, optimize=True)
    shown = [r for r in order if r != "chain"]
    pages = []
    for k in range(4):
        page = Image.new("RGBA", (len(shown) * (pw + 6) + 6, ph + 90), "white")
        d = ImageDraw.Draw(page)
        d.text((6, 4), head, font=_font(12, True), fill="black")
        for i, row in enumerate(shown):
            frames, refused, entry = rows[row]
            x = 6 + i * (pw + 6)
            d.text((x, 22), TITLES[row], font=_font(10, True), fill=(0, 0, 120))
            if frames:
                page.alpha_composite(_panel(frames[k], zoom, (pw, ph), refused, floor(frames)), (x, 38))
            d.text((x + 4, 40), caption[k] + (" (refused)" if refused else ""), font=_font(10, True),
                   fill=(150, 0, 0) if k in (1, 2) or refused else (0, 100, 0))
            for j, line in enumerate(_wrap(note(row, entry), 30)[:4]):
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
    parser.add_argument("--tries", type=int, default=3, help="tries of each tween row (default 3)")
    parser.add_argument("--only", default=",".join(JOINS), help=f"joins to run, comma separated (default {','.join(JOINS)})")
    parser.add_argument("--jobs", type=int, default=5, help="runs at once (default 5)")
    parser.add_argument("--no-provider", action="store_true", help="run the chain rows only")
    parser.add_argument("--render-only", action="store_true", help="draw the pages again from an earlier --out-dir")
    parser.add_argument("--resume", action="store_true",
                        help="in an earlier --out-dir, run again only what made nothing (no report, or status error: "
                             "an expired login, a timeout); a delivered or refused try stands")
    args = parser.parse_args(argv)
    out = args.out_dir.resolve()
    joins = [j.strip() for j in args.only.split(",") if j.strip()]
    if unknown := [j for j in joins if j not in JOINS]:
        parser.error(f"--only: no join {unknown}; joins are {', '.join(JOINS)}")
    reuse = args.render_only or args.resume
    states = build_states(out) if not reuse else {c: out / "states" / c / "keyed" for c in CLIPS}
    runs = out / "runs"
    timing_path = out / "timing.json"
    timing = json.loads(timing_path.read_text()) if timing_path.is_file() else {}
    if not args.render_only:
        for j in joins:
            if j in TRUTH:
                (runs / j).mkdir(parents=True, exist_ok=True)
                (runs / j / PLACES_FILE).write_text(json.dumps(places_from_truth(j, states), indent=2) + "\n")
        todo = [(row, j, d) for j in joins for row in rows_of(j) if not (args.no_provider and row in TWEEN_ROWS)
                for d in run_dirs(row, j, runs, args.tries) if not (args.resume and stands(row, d))]
        with ThreadPoolExecutor(max(1, args.jobs)) as pool:
            for (row, j, d), result in zip(todo, pool.map(
                    lambda t: execute(t[0], t[1], t[2], states, args.provider, args.n), todo)):
                timing.setdefault(j, {})[str(d.relative_to(runs))] = result
                print(f"{d.relative_to(runs)}: exit {result['exit']} in {result['seconds']} s", file=sys.stderr)
        timing_path.write_text(json.dumps(timing, indent=2) + "\n")
    summary = {j: summarise(j, runs, states, args.tries, timing) for j in joins}
    show = out / "showcase"
    show.mkdir(exist_ok=True)
    height = Image.open(next(iter(states.values())) / "frame-0001.png").height
    written = [p for j in joins if all(r in summary[j] for r in rows_of(j))
               for p in render(j, runs, summary, states, args.tries, show, height)]
    (out / "summary.json").write_text(json.dumps({"provider": args.provider, "n": args.n, "tries": args.tries, "joins": {
        j: {"what": JOINS[j][4], **summary[j]} for j in joins}}, indent=2) + "\n")
    big = [p.name for p in written if p.stat().st_size >= 1_000_000]
    print(json.dumps({"summary": str(out / "summary.json"), "showcase": [str(p) for p in written], "over_1mb": big}))
    return 1 if big else 0


if __name__ == "__main__":
    raise SystemExit(main())
