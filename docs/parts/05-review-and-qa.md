# Parts QA and exact revision review

> Owns: PR05 measurements, evidence and approval decisions · Index: [docs/README.md](../README.md)

`parts-inspect` verifies declarations, coverage and input bytes, renders through
the shared PR04 engine, and records bounds, clipping and exact sample timing.
Checks distinguish `error`, `warning`, `unverified` and `passed`. Fitting,
occlusion, animation, contact truth and style stay unverified until explicitly
reviewed. A socket array or a successful render is not animation approval.

```sh
sprite-gen parts-inspect --assembly ./library/assembly.json
sprite-gen parts-review --assembly ./library/assembly.json
sprite-gen parts-approve --assembly ./library/assembly.json \
  --review-hash <candidate-digest> --checks ./checks.json --reviewer <name>
```

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
format uses `parts.evidence.v2`, so an older policy decision cannot approve it.

Structural/measurement errors block review. Warnings and unverified facts remain
visible in the evidence even after an explicit decision. Release requires a
current approved appearance review with all five decisions, and reruns the
same evidence verification immediately before export. Local review records are
content-addressed decisions, not cryptographic reviewer signatures.

## Layer fitting and bounded corrections

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

The [walk/run repair](sd-explorer-motion-repair-20261005.md) runs the shared
checker across every visible installed piece/pose in eight slots. Its evidence
retains tank-top and footwear coverage failures rather than promoting them to approval.
Frame-synchronized rendering is checked separately from geometric fitting.

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
