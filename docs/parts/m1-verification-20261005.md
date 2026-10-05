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

The [current repair](sd-explorer-motion-repair-20261005.md) replaces nearest-pose
clothing with direct source-pose drawings and holds attachments with the body
frame (`parts-play --interpolation frame`). The 20 selected body PNGs retain
their exact original bytes; motion sampling/cadence and binding digests change.
The three shared states serve 32 appearances without Cartesian clip generation.
Eight-slot fit evidence has 274 visible piece/pose checks and retains five
1–2-pixel tank-top failures plus three 1–4-pixel footwear coverage failures. Browser playing and all-empty/mixed swaps
preserve the selected body and clock. The current regression is **227 passed in
31.12 seconds**; art and release remain unapproved.
