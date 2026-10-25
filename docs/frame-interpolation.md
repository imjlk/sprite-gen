# AI Frame Interpolation (in-betweens)

> Owns: Generative in-betweens for sprite frames, recorded as a take · Index: [docs/README.md](README.md)

`sprite_gen/effects/interpolate.py` (`scripts/interpolate_frames.py`) makes an **in-between
frame** between two frames of one state — a half-closed eyelid between open/closed
idle frames, a mid-swing arm between windup and strike.

## Doctrine position

"AI touches raw generation only" applies here too: interpolation is a raw-stage AI
step. The output is never a final frame — it is recorded as a **take**
(`raw/<state>.takes/<label>.png` + `states.<state>.takes` in `sprite-request.json`),
and logical frames are always baked by the deterministic extraction path (chroma
removal → components → grid snap → shared palette). The curator orders the new frame
in the play sequence like any other take frame.

## Backend: generative (codex / grok) — flow-based VFI retired

The backend is the engine's own generation layer (`sprite_gen.gen`): the two aligned
frames go in as reference images and the model **draws** the in-between pose.
Default provider `codex` (GPT `image_gen`); `--provider grok` selects xAI Imagine.

Flow-based VFI (RIFE) was retired (maintainer, 2026-07-17). Rationale, measured on a
real 3-way comparison (hero down_action arm swing, sheet `tween-3way-compare.png`
in the tray): VFI interpolates *motion* photometrically, so appearance
changes come out as a blurry cross-fade; generative in-betweens drew the mid-pose in
clean discrete pixels. codex preserved identity best; grok drifted; RIFE smeared.

## Auth prerequisites (applies to the curator Tween button too)

Generation always runs through the **server machine's image provider** — the browser/webview
never sees or carries any credential. The Tween button POSTs to the local curation
server, which uses the shared image providers (Codex subprocess or direct Grok API) with their own
machine-local credentials.

- Required on the machine running the curation server / CLI:
  - `codex` CLI installed and logged in (ChatGPT OAuth) for `--provider codex`
  - a valid Grok login or `XAI_API_KEY` for `--provider grok` (see [gen](gen.md))
- Setup and provider topology: [`docs/gen.md`](gen.md).
- Not authenticated / required Codex CLI missing → the provider fails loudly and the message
  surfaces as-is (CLI stderr / curator status bar). No silent fallback between
  providers.

## Usage

```bash
$SPRITE_GEN_ROOT/.venv/bin/python $SPRITE_GEN_ROOT/scripts/interpolate_frames.py \
  --run-dir <run> --state down_idle --between 1 2 \
  [--provider codex|grok] [--t 0.5] [--label blink_mid] [--extract]
```

- `--between A B` — frame indices on the state's primary strip (component order).
- `--provider` — generation backend; default `codex` (3-way comparison winner).
- `--t` — interpolation time inside (0, 1); default 0.5. Non-midpoint values are
  expressed in the prompt ("closer to A/B").
- `--label` — take label; default `tween_<A>_<B>_t<t>`. Re-running the same label
  overwrites the same take file and does not duplicate the `takes` entry (idempotent).
- `--extract` — run a **full-batch** re-extraction afterwards. Partial extraction is
  deliberately not offered: the run-wide shared palette is built per extraction batch
  (CHANGELOG v1.56.17 known limitation), so a single-state extract would shade that row
  from a different palette.

In the curator, the row-header **Tween** button does the same thing: open the
popover, click two cards to pick the pair (blue border), choose GPT/Grok, Generate.
While the follow-up full-batch re-extraction runs, `/api/run` reports busy (503)
instead of a manifest-consistency error.

## Cross-state in-betweens (`sprite-gen tween`)

`interpolate` works inside one state's primary strip, one frame per call. Chained states
(`attack -> idle` recovery, `skill -> idle`, an `idle -> attack` wind-up) need frames between
two frames of *different* states, often several, and often across a large height change.
`sprite-gen tween` (`sprite_gen/effects/tween.py`, `scripts/tween_frames.py`) covers that.

Clips that a video source films whole keep their existing paths: `video-stop` for walk/run
stops, one-shot cuts for recovery inside one clip, `video --image --last-frame` for pinned
clips. `tween` is for joining frames that already exist.

```bash
# run mode: endpoints from two states of a run (negative index = from the end)
sprite-gen tween <run> --from attack:-1 --to idle:0 --n 2 [--label attack_recover]
# file mode: endpoints from PNGs, or from cells of a sheet
sprite-gen tween --from-file a.png --to-file sheet.png:0 --cell 448x448 --n 3 --out-dir out/
```

| flag | meaning |
| --- | --- |
| `--n` | in-betweens to draw, 1..6 (default 2), all in one call |
| `--method strip` (default) | one keyframe strip: slot 1 = A, last slot = B, grey onion-skin ghosts of both in the slots between; the provider fills every slot at once |
| `--method video` | films a first-last clip through the existing `video --image --last-frame` path (grok login), samples N+2 frames evenly, same checks |
| `--provider codex\|grok` | the one provider used; no retry on another (No silent fallback). Default `grok` |
| `--anchor foot\|bottom-center` | horizontal anchor: mean x of the bottom 6 % of opaque rows, or bbox centre |
| `--margin` | safe margin around each slot as a fraction of the figure size (default 0.12) |
| `--min-endpoint-iou` | refusal threshold for drawn endpoints (default 0.6) |
| `--ref` | extra identity reference for the strip method (repeatable) |
| `--description` | character text for the prompt (run mode reads `character.description`) |

What it does with the result:

- **Endpoint check.** The drawn slot 1 and last slot are compared with A and B (silhouette
  IoU at the keyframes' height, foot anchors aligned), and the scale the provider drew the
  two ends at must agree (`scale_agree >= 0.85`). Under either threshold the command exits
  non-zero with the numbers and keeps `keyframe-strip.png`, `raw.png` and `tween.json`.
- **Scale and feet.** The provider often draws the strip at its own scale. The scale measured
  on the two drawn ends is interpolated across the slots, each in-between is rescaled by it and
  clamped to the A..B height range, and its foot anchor goes on the shared ground row.
- **Slicing by component.** The drawn strip is cut by connected component (each goes to the
  slot holding its centre), not by fixed columns, so a long prop reaching over a slot border
  stays with its owner instead of showing up as a second prop next door. Components over a
  border are reported as warnings, and so is a source cell whose content touches its own
  cell border (a sign the source sheet was mis-sliced).
- **Pose jump warning.** `step_iou` (consecutive silhouette IoU across A, tweens, B) under
  0.45 is warned: usually a pose that is in neither keyframe.
- **Record.** Run mode writes one take strip on the destination state
  (`raw/<to>.takes/<label>.png`) with `takes[].transition = {from_state, from_index,
  to_state, to_index, n, method, provider, endpoint_match}`; re-running a label overwrites it.
  File mode writes `tween-<k>.png` (RGBA, one canvas size) and `tween.json`.

## Pipeline internals

1. Chroma-remove the primary strip, extract components, tighten to solid bbox.
2. **Registration** (`register_row_frames`) puts both frames on one canvas aligned by
   upper-body alpha overlap — static pixels coincide, so the model reads the pair as
   "same drawing, only the moving part differs".
3. Canvas pads to /32 on the request chroma color; the prompt requires the same flat
   chroma background so the extractor keys it out later.
4. `tween_prompt` composes the prompt deterministically from request truth (character
   description, chroma color, t) and `sprite_gen.gen.generate_image()` runs the provider
   with both frames attached as refs.
5. The generated mid frame is saved as the take raw; extraction re-derives the state
   row (the frame appears labeled `<label>#0`) and pixel-unfake quantization snaps it
   onto the logical grid.

Quality note: judge the result **after** pixel-unfake re-quantization, and touch up
stray pixels with the curator's pixel editor if needed. Identity drift is a reroll
(regenerate the take), not a local repair.

## Testing

`tests/test_frame_interpolation.py` covers the plumbing with an injected stub
interpolator (alignment canvas contract, prompt composition, take write, request
idempotency, loud rejections including unknown providers). Real provider generation
is exercised manually, not in CI.

## Related

- [docs/README.md](README.md) — documentation index
