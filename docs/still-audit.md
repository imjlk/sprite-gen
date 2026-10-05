# Base still audit and authoring guide — sprite-gen reference

> Owns: `sprite-gen still-audit`: judging a base still as pixel art before a clip or a layer is made from it, the remedies it prints, `--fix`, and the authoring guide for a base still that films and layers well · Index: [docs/README.md](README.md)

A clip filmed from a still keeps the still's grid, palette and noise, and so does every
layer drawn over the loop. Image models usually return "pixel art" that is not: thousands
of colors, soft alpha on every edge, blocks that are neither square nor on one grid. The
audit reads one image, changes nothing, and says what to do about each finding.

```bash
sprite-gen still-audit --image base.png --target-height 89 --noise-map audit.png
sprite-gen still-audit --image base.png --target-height 89 --fix base.native.png --colors 32
```

## What it measures

| Check | Passes when | Remedy it prints |
|---|---|---|
| `pixel_scale` | the still is **upscaled** pixel art (a square grid explains it: residual ≤ 0.15) or **native** pixel art (hard alpha and ≤ 64 colors for 90 % of the subject at 1:1) | `--fix`, or regenerate asking for square pixels on one grid |
| `soft_alpha_share` | semi-transparent pixels ≤ 5 % of opaque ones | binarize alpha (`--fix`), or generate on a flat key |
| `speck_share` | stray islands ≤ 0.2 % of covered pixels | keep only the subject (`--fix`) |
| `pitch_anisotropy` | per-axis pixel sizes agree within 8 % | regenerate, or resample per axis first |
| `native_colors` | ≤ 64 colors after reading one color per grid cell | snap to a 24–40 color palette built as ramps (`--fix`) |
| `ramp_steps` | every material covering ≥ 8 % of the art has ≥ 3 light steps | regenerate with 3–5 tone ramps; a palette cannot add steps never drawn |
| `outline_share` | ≥ 75 % of the outer silhouette edge is dark outline ink | close it (`--fix`), or ask for a continuous 1 px outline |

Materials are the art's colors grouped by hue (neutrals by lightness); outline ink
(luma < 60) is left out. A step is a lightness level (8 % bands) holding at least 1 % of
the material. `--noise-map` writes three panels: alpha coverage, the subject, and each
pixel's distance from its grid cell's median — block edges that do not line up show as
stray lines.

The bounds come from judged stills (2026-10-05/06): two raw image-model stills failed
`pixel_scale`, `soft_alpha_share` and `native_colors` (16 000–28 000 colors, blocks of
9.1 × 10.1 px, residual 0.25–0.48); the same art snapped to its own grid passed with
16 colors, a 6 px grid and residual 0; native 1:1 fixtures are recognized as native.
Warnings judge craft, not design.

## `--fix`

`--fix OUT` keeps the subject, fits the drawn grid near `subject height / --target-height`
(±6 %), reads one color per cell (the cell's inner median, majority alpha — a one-cell
outline stays one pixel), snaps to `--colors` (default 32) and closes the outer outline in
the palette's darkest color. It writes the native still and an integer-upscaled
`OUT.xN.png` to film (`--upscale`, default 6), and reports the audit status after the fix.
It cannot add shading that was never drawn: a `ramp_steps` warning survives it.

## Authoring a base still that films and layers well

These came out of rebuilding one SD character end to end (docs/parts); each one removed a
defect that later stages could only patch.

1. **One view, matching the motion prompts.** Side clips are prompted as an exact side
   view; a three-quarter head on a side body films as a twisted figure. Choose the view
   once and keep the still, the clips and every layer in it.
2. **A real grid.** Ask for square pixels with every art pixel an N × N block, then run
   `--fix` anyway: models still drift by a fraction of a block. Film the snapped,
   integer-upscaled copy, never the raw output.
3. **Ramps, not a color count.** Ask for 3–5 tones per material (shadow hue-shifted toward
   cool or warm, one highlight, one light direction). Flat 1–2 tone cel shading reads as
   thin at native size, and no palette size recovers it. A fixed 24–40 color palette is
   still the right tool: snapping filmed frames to it removes the key spill a clip adds
   (green or magenta tints on far limbs) without flattening the drawing.
4. **A continuous 1 px outline** around the silhouette and between overlapping limbs; a
   broken contour is what reads as "messy lines" after downscaling.
5. **A size that holds detail.** Set the native height first (an SD body of ~84–90 px on a
   128 canvas keeps a readable face and hands) and pass it as `--target-height`.
6. **A layering-friendly base** when parts go on top: bald, a blank face (features as their
   own layer), plain undergarments, bare hands and feet. Hair or a face baked into the base
   cannot be swapped and fights every overlay.
7. **No soft alpha, no specks, flat background.** Generate on a flat key or transparent
   background; `--fix` binarizes and keeps the subject.

Run `still-audit` on the snapped still before filming it; for layered parts drawn on the
same base, audit each design the same way so all layers share one grid and one style.
