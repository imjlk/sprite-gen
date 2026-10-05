# M1 implementation and verification, 2026-10-05

This audit follows PR02–06 of the supplied roadmap, the expanded customization
plan, and the user's corrections: one intact video body, independent layers,
three shared motions, and 96 **state cases**, not 96 appearance clips.

## Requirement evidence

| Requirement | Current evidence | Scope |
|---|---|---|
| Native/external row, strip and frame import; exact timing, order, repeated IDs, curation and source preservation | `parts_assets.py`, `parts_motion.py`; assets/motion tests in the 209-test run | Implemented; native finalize and stale/tampered geometry are exercised |
| Frozen raster/source/annotation digests and explicit verified coordinates | Motion freeze tests and the three real SD source videos in the demo archive | Byte/geometry linkage is checked; visual accuracy is separate |
| Full/cropped/local part import, small sizes, alpha holes, masks, palette regions, style/rig/view/coverage and path limits | Part import/contract tests | Implemented; explicit authoring calibration is not automatic retargeting |
| Multiple pieces and SD upper/tall sleeve/canine gear/tail imports | [Current five-profile proof](assets/m1-runtime-proof-20261005.json), [recipe](../../scripts/dev/parts_pipeline_proof.py) | Real raster engineering fixtures; no artwork acceptance |
| Shared timeline, sample visibility/order, overlay/replacement, mirror policy, stable IDs and cycle rejection | Composition/runtime tests; current five-profile runtime matrix | Same generic engine; blob includes open `pulse` state and asymmetric limbs are exercised |
| SD hair/expression/upper/lower/footwear, each two distinct designs | [Runtime archive](assets/sd-explorer-m1-layers-20261005.tar.gz), its validation and browser screenshots | 32 base appearances, three real shared body motions; eyes/brows form the two expressions |
| Optional headwear/gloves/back accessory and risk combinations | Same archive: None/on, 256 selections, 73,728 scene samples, 112 pairs and 24 risk-triple combinations | Structural full-sample coverage; focused visual coverage is recorded separately |
| Intact body and clock preserved while swapping/emptying | Per-sample body image SHA/position checks, clock checks, archive roundtrip, browser all-empty check | No body split/replacement, runtime texture warp or appearance clip bake |
| QA states, explicit approval scopes and dependency invalidation | QA/review tests; real SD QA remains unverified | No automated visual/contact/style approval |
| Game-world travel preview | `parts-inspect --contacts`, `parts.evidence.v2`, [walk preview](assets/sd-explorer-m1-world-walk-20261005.png) and 288 ordered world PNGs | Authored illustrative offsets; no foot inference or stance approval |
| World preview tamper/budget/partial coverage and native pixel/tick preservation | New QA/review tests in the 209-test run | Whole rendered character translates; source pixels and native timeline stay unchanged |
| Approved, deterministic multi-state release; missing/stale approval rejection; dedup/ticks/rects/Aseprite and atomic failure handling | Build/review/pipeline tests, including CLI release; one explicit repeated preview export per profile in the proof | Export is an opt-in engineering path, never wardrobe Cartesian expansion |
| Legacy layer/non-layer preservation | Pinned baseline tests in the same run | Golden RGBA, metadata and source bytes pass |
| Reproducible code, documents and artifacts | Recipes, source/prompt provenance, immutable libraries, hash-checked archives | Local `codex/parts-system`; no remote publication is claimed |

## Commands and results

The M1 regression command covers `tests/spec/test_parts_*.py`,
`tests/curate/test_parts_*.py`, `tests/frames/test_parts_import.py`,
`tests/compose/test_parts_*.py`, `tests/qa/test_parts_qa.py`,
`tests/serve/test_parts_player.py` and `tests/packaging/test_parts_legacy_baseline.py`:
**209 passed in 28.46 seconds**. JavaScript syntax and Python compilation pass.
The broader earlier run remains 3,388 passed / 2 skipped; it is not described as
a fresh full-suite run after the world-preview change.

The [current engine proof](assets/m1-runtime-proof-20261005.json) pins the current
implementation/toolchain hash. Its runtime cases are SD 32, tall 2, canine 4,
blob 2 and asymmetric six-limb 4 appearances. All use common frame/timing
evaluation and preserve input bytes and swap clocks. Each profile explicitly
exports only one concrete selection twice to verify byte-identical results.

## Verification limits

The implementation has structural verification. The user rejected the previous
layered walk/run art, so those structural counts are not successful animation
evidence. Actual art is a review draft: palm estimates, all pose transitions,
anatomical hand identity, style and foot contact are not fully accepted.
Representative rendered/browser checks are not approval of every frame or
every combination. The demo records `art_approved=false` and
`release_ready=false`; release remains gated by a current explicit review.

Tall, canine, blob and six-limb evidence is synthetic engineering data. The
rejected three-arm tall artwork is excluded. Actual wardrobe art for other
body shapes, further face/subslot/palette artwork, generation planning and
resume workflows, engine adapters and art release are subsequent scope; this
M1 result does not claim their completion.

## Follow-up: short-hair fitting repair

The user's subsequent size/gap finding supersedes the earlier `[2,-1]` offset
adjustment. The [fitted revision](assets/sd-explorer-hair-repair-20261005.tar.gz)
redraws only short hair, imports it once at 44×38 rather than 56×49, and checks
288 shared native samples. The old cap fails size/placement throughout and
introduces enclosed head gaps in 240 samples. The corrected cap passes the
authored bounds, coverage, face/ear protection, clipping and gap checks; the
body, frozen motion, other part revisions and swap clock are preserved.

The [part-only bounded retry implementation](../../sprite_gen/qa/parts_fitting.py)
supports at most three corrections, rechecks each candidate, stops repeated
RGBA/placement and keeps failed candidates inactive. The demo adapter consumes
an imagegen output; it has no live generation API client. The follow-up parts
regression is **221 passed in 30.21 seconds**, including twelve fitting/repair
tests. This is geometric verification, not full art acceptance.

## Follow-up: pose-synchronized walk/run repair

The [previous repair](sd-explorer-motion-repair-20261005.md) removed nearest-pose
clothing and part-only interpolation, but its eight-frame run was a half stride
and the user rejected it. Technical checks had not detected the missing swing.
The [current full-cycle correction](sd-explorer-full-run-cycle-20261005.md) uses
source run samples 60–75 at native 24fps and both arm/leg phases, with directly
matched garment/footwear drawings. The 28 selected body PNGs retain their exact
bytes; new motion and binding digests reflect the changed run timeline.
Three shared states serve all appearances. The previous idle/walk RGBA results
are identical across 36 body-only/A/B comparisons. Eight-slot fit evidence has
384 visible piece/pose checks, with 16 coverage failures still recorded.
Browser playback and removal/restoration of five parts preserve body texture,
placement and paused clock. Authored-cycle checks reject missing, duplicated,
reordered samples and implicit timing changes; they do not infer gait quality.
That checkpoint's regression was **369 passed in 43.46 seconds**; its footwear
and visual noise were subsequently rejected. The [current native-parts revision](sd-explorer-native-parts-20261005.md)
uses fixed footwear angle images, fits the joint visible-foot interface and
normalizes body/part RGB to one 48-colour palette on the existing 128px grid.
Body geometry/alpha/timing remain identical; RGB changes and therefore motion
and binding digests change. All 56 footwear state/design checks pass; 11 garment
coverage failures remain. The current regression is **372 passed in 44.08 seconds**.
Source outline jitter and final visual acceptance remain open; art and release
stay unapproved.
