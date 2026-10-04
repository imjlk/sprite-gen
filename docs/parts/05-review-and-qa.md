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

Structural/measurement errors block review. Warnings and unverified facts remain
visible in the evidence even after an explicit decision. Release requires a
current approved appearance review with all five decisions, and reruns the
same evidence verification immediately before export. Local review records are
content-addressed decisions, not cryptographic reviewer signatures.
