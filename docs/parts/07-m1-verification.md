# M1 implementation and verification checkpoint

> Owns: PR00–06 completion evidence and remaining actual-art work · Index: [docs/README.md](../README.md)

2026-10-04: the opt-in M1 engine is implemented on `codex/parts-system`.
The existing generation, extraction, curation and non-parts export paths retain
their legacy behavior. The actual explorer acceptance pack is still incomplete.

User correction, 2026-10-04: the primary demo composes reusable parts at the
current shared motion sample and preserves playback time during selection
changes. The 96 count means appearance/state validation cases, not separately
produced or stored finished clips. Existing synthetic baked proof is export
test evidence, not the default customization workflow. Baked release remains
an explicit optional export. The duplicated-arm tall artwork candidate was
rejected by the user and must not be reused.

The follow-up runtime implementation retains one shared motion clock and
composes only its current sample while swapping attachments. The temporary
`assets/sd-explorer-art-work/first-art-sheet.png` is a separately code-authored
alignment study (`scripts/dev/sd_explorer_art_study.py`), not an extraction,
Grok generation or replacement for the user's accepted SD design candidate.
Actual art production remains based on that accepted SD candidate.

| Stage | Available behavior |
|---|---|
| PR02 | Immutable motion import/freeze, exact timing, final-raster annotation and revision invalidation |
| PR03 | Body/part import, multi-piece bindings, crop/pivot geometry, masks/regions and explicit sample coverage |
| PR04 | Shared timeline, segmented body replacement, subslot override, stable draw order, visibility, masks, palette and guarded mirror |
| PR05 | Measured QA, optional declared contacts/world travel, exact review evidence, explicit approval/rejection and conservative invalidation |
| PR06 | Multi-state baked atlas, ordered dedup, exact tick manifest, Aseprite mapping, build lock and atomic preview/release publication |

The local full suite passed **3354 tests, 2 skipped, 74 existing Pillow warnings**
in 338.42 seconds. Log: `/tmp/sprite-gen-parts-pr04-06-20261004.log`.
After strengthening the same mask-rounding and multi-state release cases and
extending test helpers, the affected compose/import/freeze/review suite passed
**81 tests** in 13.07 seconds. CLI taxonomy and the dependency import gate also
passed. Tests use local Python 3.13.2, Pillow 12.3.0 and NumPy 2.5.3; this does
not claim a Linux/Python 3.14 CI run. The skipped cases require real chroma
stills or a RIFE binary, neither configured locally. Legacy layer/non-layer
request, manifest, report and RGBA golden outputs passed without updates.

Release tests include actual import→freeze→body import→selection→review→explicit
fixture approval→CLI release for idle/walk/run. They also cover relocated roots,
byte identity, stale palette/states/policy/asset evidence, insufficient part-only
approval, failed snapshot writes and concurrent assembly revisions preserving
the prior release pointer. Fixture approval records are explicitly synthetic
technical checks, not artwork approval.

A separate reproducible pipeline proof runs real import/freeze/import/selection
and preview-build calls, including a second identical build for every appearance:

```sh
.venv/bin/python scripts/dev/parts_pipeline_proof.py --out assets/parts-fixture-proof-new
```

The output directory must be new or empty. No provider or network is used. The
script writes preview builds only and never generates approval decisions.
The checked-in [proof metadata](assets/m1-fixture-proof-20261004.json) pins build
digests and toolchain; current local files are under
`assets/parts-fixture-proof-20261004-verified/` (ignored generated artifacts).

| Synthetic rig | Appearances | Clips | Ordered samples |
|---|---:|---:|---:|
| SD humanoid, five basic slots × two shape/color variants | 32 | 96 | 640 |
| Tall humanoid | 1 | 3 | 20 |
| Quadruped | 1 | 3 | 20 |
| Blob, including custom `pulse` | 1 | 4 | 22 |
| Asymmetric six-limb | 1 | 3 | 20 |

These deliberately small synthetic rasters prove engine semantics and do not
depict finished characters or natural walking/running. Their socket annotations
describe fixture alignment, not anatomical or foot-contact truth. QA visual
checks stay unverified, and all these proof builds have `release_ready=false`.
Tall/canine variation import is tested, but a complete actual wardrobe for either
rig has not been authored.

The next work is the actual SD explorer art and playback demonstration. A new
[body candidate](assets/sd-explorer-body-candidate-v2-20261004.png) was generated
with built-in imagegen and saved without modification. Its
[exact prompt and inspection](assets/sd-explorer-body-candidate-v2-20261004.provenance.json)
record two visible arms/legs and the remaining palette/alpha/grid/view issues.
It is not rig-ready, segmented, animated or approved. The first generation had
duplicated limb appearance and unsuitable proportions and was discarded.

Actual acceptance still requires a cleaned body and final motion samples,
segmented hair/face/upper/lower/footwear with two designs each, bindings to the
same frozen 20-sample timeline, actual 96-clip fitting/occlusion/contact review,
and the additional headwear/gloves/back equipment checks. The Codex goal remains
active for that work. No branch push, GitHub PR, deployment or package release
has been performed.
