# AI independent footwear pilot — 2026-10-10

This experiment now contains actual AI-drawn **pose-specific independent shoes**, rather than a standing shoe rotated/scaled along interpolated ankle vectors. It provides a playable first result on eight shared run samples (`run.10–17`). The complete run cycle and production art approval remain outside this pilot.

## Open the result

- Player: <http://127.0.0.1:8786/?state=run&from=10&to=17&speed=0.25>
- Curation: <http://127.0.0.1:8785/?part=illustration.motion.footwear.ai-pilot&pose=run.17>
- Workspace: `assets/parts-ai-shoes-pilot-20261010/library/assembly.json`.
- The player swaps “정지 그림 변형 후보”, “AI 신발 · 8프레임” and no shoes at the same original sample/time. The window loops only a section of the original motion; its endpoints are not a new seamless cycle.

![Actual built independent shoes](assets/ai-shoes-actual-motion-20261010.jpg)

## What was actually generated

The native Codex image generation tool made 12 images: eight pose-specific shoe pairs, plus targeted redraws for `run.12`, `run.13`, `run.15`, and `run.17`. No image-generation API key or alternative provider was used. No new video was filmed. The existing Grok-derived walk/run body clips remain the shared motion.

Each request used the exact body pose ROI plus the navy/cream sneaker design. The ROI was `[128,384,640,768]` on the immutable 768px body canvas, padded to 512px square and enlarged to a 1024px reference. The model did not consistently honor output framing or resolution; raw outputs therefore remain evidence rather than directly usable coordinates.

`parts_ai_shoes_pilot.py` separates the two actual drawings and registers each with authored uniform scale/translation. The current native-art policy requires no rotation and uses a single premultiplied bilinear resize. A uniform 4% fit margin (5% for `run.10` rear shoe) and one-pixel translations for rear shoes in `run.10`/`run.11` cover the unchanged foot-core guides. Legacy resampling remains available to reproduce old candidates. It preserves aspect and the newly drawn perspective. It never uses projected ankle-toe length to scale standing art, repaints a shoe, or extracts clothing from a generated full character. All original provider outputs and prompts are retained. The later small translation proposals were bounded to ±8 body pixels and remained unapproved; proposals based on ambiguous crossing rectangles were discarded in favor of body contour review or an AI redraw.

The source and transforms live in `generation-latest.json`, `registration.json`, `registration-report.json`, and the immutable `inputs/ai-shoes-pilot/revisions/…` directories. Curation candidates retain frozen QA images; `final-curation-lineage-final-fit.json` identifies the final selected candidates, while the body-corrected lineage remains the prior evidence.

[Native outputs, prompts, guides, annotations and evidence](assets/ai-independent-shoes-20261010.sources.zip) are archived as 146 hash-verified files (12.0MB). The body/motion source dependency remains the archive in experiment 16.

## The actual zmap failure and correction

Raw overlays looked much better, but the **real build** still drew bare rear feet above their shoes in `run.14–17`. The old video-body segmentation had assigned those rear-foot pixels to foreground nodes. Repositioning or redrawing a shoe alone could not fix that ordering error.

`parts_ai_shoes_body_ownership.py` applies body-reference contours to the visible rear feet in `run.13–17` and regenerates the segmented body pack. The body pack revision changes, while its reconstructed RGBA stays exactly equal to all 43 original body frames. There is no runtime occlusion mask and no body-node replacement. The existing zmap now draws the correctly owned rear foot below `shoe_back`; actual foreground limbs can still occlude that shoe naturally. This is a provisional semantic annotation for this exact body/motion, not a general detector or an approved anatomical rig.

![Before rear-foot ownership correction](assets/ai-shoes-before-body-ownership-20261010.jpg)

The review server also now keys its built render cache on body, rig, motion, part and state context. A body ownership revision with unchanged full-body pixels/part digest must not reuse an old composite. The cache keeps one context and captures the rendered image under its lock.

## QC and approval remain separate

The new guides check authored **foot-core** regions containing toes/heels against original body alpha, protected upper-body space, and allowed footwear volumes. Crossing contours exclude the other limb. These are explicitly limited criteria; full ankle closure and temporal design consistency are not certified. QA guides are never used to erase runtime body pixels.

| Pose | Required pixels missing | Allowed/protected violation | Status |
|---|---:|---:|---|
| run.10 | 0 | 0 | warning |
| run.11 | 0 | 0 | passed |
| run.12 | 0 | 0 | passed |
| run.13 | 0 | 0 | passed |
| run.14 | 0 | 0 | passed |
| run.15 | 0 | 0 | passed |
| run.16 | 0 | 0 | passed |
| run.17 | 0 | 0 | warning |

Total: **zero missing foot-core pixels, zero allowed/protected violations, zero small alpha holes or fragments in all eight produced poses**. Art decisions remain unverified; these narrow checks do not approve the library. The earlier 72-pixel deficit and apparent alpha holes were investigated against actual source pixels. Most holes were silhouette ringing from low-alpha native cutout edges and two LANCZOS resizes. A positive filter removes this registration artifact without repainting shoes, erasing body pixels, or weakening the fixed criteria. The generic area-jump warning at the window endpoints compares against empty unproduced neighbors; it is not proof of temporal instability or stability. Review the eight produced images in sequence as well as the original body reference.

The other 35 poses have explicit transparent preview placeholders and are **rejected as unproduced**, not accepted parts. Their ownership guides are empty. A new generation for one of these poses requires new ownership annotations. `parts_segments.split_piece` now fails before writing a split import if drawn pixels have no owner; it no longer silently discards such artwork. The review UI displays rejected decisions separately from unverified art.

## Verification

- 43 body frames reconstruct byte-identically after the ownership correction.
- Motion reference/sample IDs/timing are unchanged; shoe swaps preserve every sample.
- Python and browser layer samplers match for 86 actual off/on cases.
- Eight produced samples each have `shoe_back` and `shoe_front` layers; runtime masks and appearance-specific baked clips are both zero.
- 61 focused tests cover production/revalidation, split pixel preservation, body-context render cache invalidation, export/player behavior, and variable/fractional-duration review windows. JS syntax, Ruff F checks and Git whitespace checks pass.
- Live player and curation pages were checked, including original sample IDs, 8-frame stepping/window wrap, 11/13 off/on layers at the same paused sample, and actual zmap rendering. The fit-update archive contains 46 hash-verified files (2.48MB), supplementing the unchanged original source archive. The eight produced poses have no QA errors; endpoint warnings remain visible. Code tests do not approve the art.

## Reproduce / continue

Use the managed worktree `.venv` and the exact body library from experiment 16:

```sh
.venv/bin/python -m scripts.dev.parts_ai_shoes_pilot --out assets/parts-ai-shoes-pilot-20261010 --publish-from assets/parts-illustration-motion-20261010/library
.venv/bin/python -m scripts.dev.parts_ai_shoes_check --out assets/parts-ai-shoes-pilot-20261010
.venv/bin/python -m scripts.dev.parts_ai_shoes_body_ownership --out assets/parts-ai-shoes-pilot-20261010
.venv/bin/python -m scripts.dev.parts_ai_shoes_check --out assets/parts-ai-shoes-pilot-20261010
.venv/bin/sprite-gen parts-play --assembly assets/parts-ai-shoes-pilot-20261010/library/assembly.json --interpolation frame --port 8786
.venv/bin/sprite-gen parts-review-serve --assembly assets/parts-ai-shoes-pilot-20261010/library/assembly.json --port 8785
```

The provider calls are intentionally not replayed by those commands. Restore the archived raw art/prompts/guides and resolve frozen body source paths first. The source archive preserves native outputs, placement history, foot contours, selected-candidate lineage and verification evidence. Restore [the fit update](assets/ai-independent-shoes-20261010.fit-update.zip) over the original source archive to reproduce the final registration, selected-candidate lineage and verification. Then use `--refresh` when republishing an existing pilot; the immutable prior candidate revisions remain available. `parts_ai_shoes_verify.py --out …` checks the actual build against unchanged body pixels and Python/browser sampling. The generated runtime export remains an independent layer library; the development workspace is separate from the previous experiment and the primary checkout.

The first-result Goal is fulfilled by these eight consecutive frames, the real player swap/sequence check and the preserved source/verification artifacts. Further work is a separate expansion: complete the other 35 poses, inspect the complete loop, refine temporal lace/panel identity, validate body ownership outside this window, and add another garment/body type. The original body clip itself retains its original limb-shape and contour variation; the shoes pipeline does not repair those underlying video frames.
