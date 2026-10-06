# Parts QA and exact revision review

> Owns: PR05 measurements, evidence and approval decisions · Index: [docs/README.md](../README.md)

`parts-inspect` verifies declarations, coverage and input bytes, renders through
the shared PR04 engine, and records bounds, clipping and exact sample timing.
Checks distinguish `error`, `warning`, `unverified` and `passed`. Authored
part fitting declarations add measured coverage, protection and silhouette
checks to this same QA and approval path. Visual fitting,
occlusion, animation, contact truth and style stay unverified until explicitly
reviewed. A socket array or a successful render is not animation approval.

```sh
sprite-gen parts-inspect --assembly ./library/assembly.json
sprite-gen parts-review --assembly ./library/assembly.json
sprite-gen parts-approve --assembly ./library/assembly.json \
  --review-hash <candidate-digest> --checks ./checks.json --reviewer <name>
```

Before review, `parts-coverage` shows the part × clip × pose matrix to fill:
for each body and catalog piece, covered/total samples per clip, distinct
drawings, samples filled from a `pose_map`, poses never drawn and gaps in the
requested states (`--markdown FILE` writes the table). It measures what exists;
it approves nothing. Check it before adding a pose: every pose-drawn part needs a
drawing for the new pose.

```sh
sprite-gen parts-coverage --assembly ./library/assembly.json --markdown coverage.md
```

Part productions ([10-production.md](10-production.md)) add a part review that
does not depend on any appearance: `parts-part-review` renders the built pack on
the body alone, records a decision per pose (`pose.<id>` checks) and seals a
`parts-review` with scope `part` and `evidence.appearance: null`. It survives
imports of other parts and goes when its own pack, body or motion changes;
only appearance reviews are dropped on every pack change.

The decision file explicitly contains all five checks:

```json
{"fitting":"approved","occlusion":"approved","motion":"approved","contact":"approved","style":"approved"}
```

This is a decision input, not a default filled by the tool. Inspect the preview
and QA evidence before authoring it. `--decision rejected` needs at least one
rejected check. Optional `--note` describes the review's limits; technical
synthetic fixture approval must not be represented as real art acceptance.

Each candidate snapshots the plan, rendered samples, preview and QA report,
plus contact annotations when supplied. Its declaration binds the subject,
concrete appearance, dependency digest closure, masks, regions, actual source
bytes, render/QA policies and toolchain. Toolchain includes Python, Pillow and
NumPy versions and a hash of the relevant implementation files, including the
exporter. Decisions recompute evidence and reject modified bytes, policies,
timing, palette, geometry, requested states and stale assembly revisions.
An active candidate hash is required; old history is never silently reused.
Evidence files and decisions are immutable and retained after invalidation.

`--scope part --subject <pack-id>` and `--scope binding --subject <pack-id>
--binding <binding-id>` record narrower reviews with the current appearance as
context. They do not approve a complete combination. M1 uses conservative
dependency invalidation; adding a catalog option can require another review.
Replacing a motion/body/part or named appearance detaches affected consumers.

Optional contact annotations pin the exact motion and declare profile contact
nodes, sockets, ordered stance samples and per-sample game travel:

```json
{
  "kind":"parts-contact-annotations","schema_version":1,
  "motion":{"id":"explorer.motion","digest":"<exact frozen digest>"},
  "clips":{"walk.right":{
    "world_offsets":{"sample.0":[0,0],"sample.1":[1,0]},
    "stances":[{"node":"foot_l","socket":"foot_l",
      "samples":["sample.0","sample.1"],"max_slip_px":1}]
  }}
}
```

`--contacts` is available on inspect/review. World position is declared travel
plus the reflected or original socket coordinate. Drift beyond the supplied
pixel tolerance is an error. This measures declared socket stability; it cannot
prove pixel contact, foot identity or the truth of a stance. Uncovered nodes or
clips remain unverified. There is no lowest-silhouette-pixel foot inference or
automatic in-place walking failure. Profiles without contact nodes impose no
humanoid foot requirement.

With `--contacts`, QA and review also snapshot `preview.json.world_travel` and
`world-travel/<clip>/frames/<sample>.png`. Each frame translates the **complete
rendered character** by the supplied world offset. Native pixels, sample IDs,
ticks, loop flags and anatomical IDs remain unchanged. A contact sheet selects
up to twelve ordered samples and marks supplied stance points; these markers
are separate from the exact world-frame PNGs. Missing travel samples report
partial coverage. Travel is never inferred from feet, and repeated-cycle travel
accumulation must be supplied by the game rather than guessed from one cycle.

World-preview allocation obeys the existing per-image and total pixel budgets.
The preview metadata, generated image hashes and contact annotations participate
in the review evidence. Modifying a world image blocks approval. This evidence
format uses `parts.evidence.v4`, so an older policy decision cannot approve it.

Structural/measurement errors block review. Warnings and unverified facts remain
visible in the evidence even after an explicit decision. Release requires a
current approved appearance review with all five decisions, and reruns the
same evidence verification immediately before export. Local review records are
content-addressed decisions, not cryptographic reviewer signatures.

## Layer fitting and bounded corrections

`parts-fit-import` registers native-canvas interfaces as a sealed `parts-fitting`
document. Each row pins the exact part, binding, clip and sample. The workspace
indexes these under `fitting`; QA verifies both normalized masks and captured
source bytes. Once a fitting policy is installed, any visible part sample with
no interface is an error. A workspace with no policy remains unverified.
`fitting.json` is included in preview/review evidence, and fitting errors block
review and release through the existing gates. Changing a fit policy invalidates
reviews; replacing its part or motion detaches the stale declaration.

```sh
sprite-gen parts-fit-import --assembly ./library/assembly.json \
  --spec ./fit.json --source-root ./fit-source
sprite-gen parts-inspect --assembly ./library/assembly.json
sprite-gen parts-repair-plan --assembly ./library/assembly.json \
  --part garment.a --radius 2 --patch-gaps --out ./repair/garment.json
```

The repair command produces an import proposal and a `.repair.json` report;
it neither activates a part nor approves art. Translation is limited to three
pixels and the shared three-retry ceiling. `--patch-gaps` can propose up to
twelve missing local pixels for a raster piece, using nearby existing part ink.
It requires authored coverage, honors declared protection and silhouette masks,
keeps the same size, and rechecks the original rules and other passing parts.
Large gaps, mirrored raster edits, missing criteria and unrepairable fits stay
unresolved. The body and motion are untouched. Inspect the proposal, import it
with `parts-import`, rebind the fitting declaration to the new part revision,
then run QA again. See [layer authoring and export](08-layer-export.md).

## Authored source stability

`parts-stability-import --assembly ./library/assembly.json --spec ./stability.json`
registers exact part/binding/piece references and stable source regions. Each
region names a socket, a relative half-open rectangle, a reference clip/sample,
clips to measure, and explicit alpha/RGB pixel budgets. Integer attachment
alignment excludes authored position movement. Moving limbs and deliberate
shape changes must remain outside a declared stable region.

The checker reads the unmasked piece PNG before appearance palettes or other
overlays. It counts opaque alpha differences and visible RGB differences above
the declared threshold, ignoring invisible RGB. `stability.json` enters the
same QA/review/build evidence. Source failures block review; missing criteria
remain unverified. Source/policy revisions invalidate stale evidence. This is a
measurement of declared regions, never automatic approval of anatomy, motion,
palette, or style. The viewer displays current-pose deltas and can seek to a
source failure independently of part-fitting failures.

`sprite_gen.qa.parts_fitting` measures an authored layer interface: maximum
picture size, allowed placement, required coverage, protected pixels,
clipping and declared newly enclosed transparent gaps. Rectangles follow the existing
attachment delta. Preexisting body holes are reported separately; they never
request a redraw of the hair. The engine does not infer anatomy, fill holes,
resize pictures during playback or approve visual quality from these numbers.

The same checker accepts native-canvas `L` or `RGBA` authored pixel masks for
`required_mask`, `protected_mask` and `allowed_mask`. Clothing can require leg
coverage, protect front arms and restrict its silhouette for each exact body
pose. These masks are already in current-frame coordinates; no anatomy or pose
matching is inferred and no mask is resized. `gap_region=None` leaves seam gaps
unmeasured when no forbidden-gap region has been authored, such as intentional
negative space around backpack straps. Passing other measurements does not
claim those undeclared interfaces are verified.

The [native-parts repair](sd-explorer-native-parts-20261005.md) runs the shared
checker across every visible installed piece/pose in eight slots. Its evidence
retains upper/lower clothing coverage failures rather than promoting them to approval.
Frame-synchronized rendering is checked separately from geometric fitting.

`sprite_gen.frames.parts_palette.normalize` optionally uses an authored shared
palette and the existing exact recolor bake. Source image bytes remain saved;
size and alpha must match after baking. Its cache is keyed by both source and
palette policy so revising the policy cannot reuse stale pixels. RGB changes
require new motion/pack digests, even when geometry and timing are unchanged.
The SD recipe uses the same 128px canvas and fixed 48-colour palette for the
body and part artwork. This reduces colour noise; it does not repair a source
video's changing outlines or prove a part's anatomy/style.

`sprite_gen.qa.parts_cycles.verify` compares a recipe's selected samples and
cadence with its explicitly authored complete source cycle. Missing, repeated
or reordered samples and an undeclared slowdown fail before import. The author
must inspect both step phases and declare the reference; this checker does not
infer anatomy, find a video's period or grant art approval. The SD run reference
contains source samples 60–75 at 24fps. The earlier eight-frame half stride was
rejected despite passing layer/geometry checks.

`retry(initial, evaluate, correct, max_retries=3)` evaluates the initial asset
and each corrected revision. The correction adapter receives the failed checks
and changes only that part. Offset errors can change the binding; oversized art
can be calibrated once on import; mismatched openings need a redrawn part.
Three retries is the ceiling. Repeated RGBA pixels and placement stop even when
PNG metadata differs. Exhaustion, absent corrections and provider errors never
activate a failed candidate or silently reuse an approval. Automatic network
retries are a separate provider concern.

The [SD hair repair recipe](../../scripts/dev/sd_explorer_hair_repair.py) consumes
a supplied imagegen output, checks all 288 native samples, and imports a new
hair revision only after every authored fitting check passes. It preserves the
body, other parts, frozen motion and clock. The provider call runs in the
authoring workflow; this recipe does not contain a live Grok/imagegen client.
The fitted short cap is 44×38 instead of 56×49. `fit-repair.json` retains the
failed and corrected measurements; `art_approved` and `release_ready` stay false.
