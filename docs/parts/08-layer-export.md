# Independent layer authoring and export

This workflow keeps one intact body and independent custom PNG layers on a
shared motion timeline. Contributors supply pose mappings and native-canvas fit
criteria, inspect failures, and export a portable browser preview. An appearance
change selects different layers without making a new video or appearance clip.
Geometry checks and draft exports never approve visual quality.

## Register fitting criteria

Import and freeze a motion, import the body and parts, then create an appearance
using the existing [motion](02-motion-snapshots.md), [part](03-part-import.md) and
[composition](04-composition.md) commands. Fit criteria name exact imported
references, not approximate body types or inferred anatomy.

```sh
sprite-gen parts-fit-import --assembly ./library/assembly.json \
  --spec ./fit.json --source-root ./fit-source
sprite-gen parts-inspect --assembly ./library/assembly.json
```

A fit import contains one entry for each visible piece and shared sample. The
following is an authoring example; replace its references with actual digests,
coordinates and mask hashes from your own rig. Repeat entries for all samples.

```json
{
  "kind": "parts-fitting-import",
  "schema_version": 1,
  "id": "character.fitting",
  "rig": {"id": "character.rig", "digest": "<rig digest>"},
  "motion": {"id": "character.motion", "digest": "<frozen motion digest>"},
  "entries": [{
    "id": "badge.move.0",
    "part": {"id": "badge.a", "digest": "<part digest>"},
    "binding": "badge.binding",
    "piece": "badge",
    "clip": "move.side",
    "sample": "sample.0",
    "rules": {"max_size": [12, 12], "bounds": [0, 0, 32, 32]},
    "required": {
      "image": {"file": "required.png", "sha256": "<mask hash>"},
      "channel": "luma"
    }
  }]
}
```

`required` measures covered pixels, `protected` measures unwanted overlap, and
`allowed` bounds the part silhouette. Masks use the rig canvas and explicitly
select `luma` or `alpha`; they are not resized. Half-open rectangles can also
declare `required_cover`, `keep_clear` and an optional forbidden `gap_region`.
Size and placement are always measured. Undeclared anatomy and negative space
remain outside the claim. With any fitting policy installed, a visible part
sample missing an entry is an error; without a policy the result is unverified.

Fitting reports enter the same QA evidence and review gate as composition.
Failed fitting blocks review. Part or motion changes detach stale interfaces;
criteria changes invalidate decisions. Inspect and rebind the exact revised
part before requesting a new [explicit review](05-review-and-qa.md).

## Propose bounded corrections

```sh
sprite-gen parts-repair-plan --assembly ./library/assembly.json \
  --part badge.a --radius 2 --patch-gaps --out ./repair/badge.json
```

The command writes an import proposal, a `.repair.json` history and, when pixels
change, a source folder. It does not activate the proposal. Translation searches
at most three pixels and three correction attempts. Optional gap repair proposes
at most twelve missing local pixels on a raster piece, using nearby existing
ink and the original criteria. It preserves picture size, body, motion and
previously passing fits. Large gaps, unrepairable cases and mirrored raster edits
remain unresolved. Inspect the returned history and source images first.

Use the returned `source_root` when importing an accepted proposal with
`parts-import`. Update the appearance selection and fitting references to the
new part digest, import the revised criteria, and rerun `parts-inspect`. This
activation path remains separate from art approval. Correction providers may
supply a newly drawn part through ordinary import; the runtime calls no provider.

## Measure stable source regions

Use `parts-stability-import` when a region is intended to retain its shape and
colors across a shared motion. This example declares a rigid head region;
author different regions and budgets for your own body or non-humanoid asset.
Do not mark moving limbs as rigid.

```json
{
  "kind": "parts-stability-import", "schema_version": 1,
  "id": "character.source-stability",
  "rig": {"id": "character.rig", "digest": "<rig digest>"},
  "motion": {"id": "character.motion", "digest": "<motion digest>"},
  "regions": [{
    "id": "head.move", "label": "Head source",
    "part": {"id": "character.body", "digest": "<body digest>"},
    "binding": "body.binding", "piece": "body", "socket": "head",
    "bounds": [-20, 0, 20, 28],
    "reference": {"clip": "move.side", "sample": "sample.0"},
    "clips": ["move.side"], "max_alpha_pixels": 48,
    "max_rgb_pixels": 160, "rgb_threshold": 24, "min_alpha": 128
  }]
}
```

These example numbers are authored budgets, not universal quality thresholds.
The checker aligns by native integer attachment coordinates and reads the
unmasked source before appearance palette processing. It reports alpha XOR and
RGB differences only where both pixels are opaque. A missing or hidden
reference, nonempty-region failure, stale digest or exceeded budget is an
error. Hidden samples can be explicitly skipped and remain unverified.

```sh
sprite-gen parts-stability-import --assembly ./library/assembly.json \
  --spec ./stability.json
sprite-gen parts-inspect --assembly ./library/assembly.json
```

Review/build gates verify `stability.json` and captured declaration bytes.
Replacing a referenced part or motion detaches stale criteria; editing criteria
invalidates reviews. Region stability and overall animation/art approval are
separate facts.

## Export shared tracks and PNG layers

```sh
sprite-gen parts-export --assembly ./library/assembly.json \
  --out ./layer-export --title "My character" --interpolation frame
sprite-gen parts-play --assembly ./library/assembly.json \
  --port 8766 --title "My character" --interpolation frame
```

The export pointer `preview.json` names an immutable snapshot containing
`manifest.json`, hash-addressed `textures/*.png` and a self-contained
`preview.html`. Open the HTML directly to inspect it without a server. The
manifest describes shared clips, exact sample mappings, palette masks and
per-part fitting and authored source stability evidence. It contains zero baked appearance clips and remains
`release_ready=false`; `parts-build` is the separate explicit baked export path.

The local viewer fetches `/api/library` once, caches PNGs and runs the common
clock in the browser. Each part retains its own canvas. Pause and frame steps
inspect the shared time; body-only comparison and selections preserve it. The current
sample displays its measured errors; the library count covers all selectable
part samples. `/standalone` serves the embedded preview and `/api/export`
downloads its ZIP. Neither playback nor download writes the assembly.

The first portable adapter supports one body pack (flat, or segmented into node
pieces), overlays, one choice per slot, variable sample durations, loop or
clamped clips, ordering with the topology's draw table, cover rules, palette
regions, self masks and explicit mirroring. The manifest carries the table as
`layers`, each pack's `covers` and each piece's `cover_rule`; the browser applies
them the same way as `parts_resolve` (a parity test runs both). Body
replacement, slot precedence and multiple choices are explicit adapter errors. A mask may target another layer's node (a hat hiding the hair it covers); the player applies it to every layer providing that node, as the Python renderer does.
The broader Python runtime retains those capabilities, and the local viewer
falls back to `/api/layers` for those profiles. A generic 32px badge fixture
verifies that the adapter has no SD or humanoid anatomy assumptions.

Use `frame` for layers authored against discrete body poses. `linear` is
available for authored attachment interpolation; it does not blend images or
create missing pose art. Another body needs its own body revision, fit masks
and bindings. The layer library preserves reusable designs and the common
clock within a rig; it does not claim automatic body retargeting.
