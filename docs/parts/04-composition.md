# Shared-timeline parts composition

> Owns: PR04 resolver, appearance selection and raster preview · Index: [docs/README.md](../README.md)

`parts-appearance` pins the current body, rig, frozen motion, catalog, requested
states and explicit selections. `parts-preview` uses the same pure resolver and
RGBA renderer that later QA and baked export consume. Preview is always
`unverified`; successful composition grants no visual or motion approval.

```sh
sprite-gen parts-appearance --assembly ./library/assembly.json --spec ./selection.json
sprite-gen parts-preview --assembly ./library/assembly.json
```

Selection authoring example:

```json
{
  "kind": "parts-appearance-selection",
  "schema_version": 1,
  "id": "explorer",
  "selections": {"hair": {"id": "hair.a", "digest": "<exact imported digest>"}},
  "palette": {"fabric_primary": [40, 100, 110]},
  "mirror": false
}
```

The example digest is a placeholder. Defaults, named presets, then explicit
selections determine the chosen packs. An empty required slot can use its
`body-default` only when the body explicitly provides every declared slot node.
All selected pieces need mappings for every requested sample, including held
pieces and invisible samples. Timing and repeated sample IDs come only from the
frozen motion; parts never run separate timelines.

The default customization workflow uses `compose.parts_runtime.PartsPlayer.layers()`:
keep the base body and each selected part as independent sprite layers, seek the
one shared motion clock, and apply each layer's attachment point plus local
origin. The sampler does not decode or composite an image. Exact linear
interpolation uses the current sample's duration and wraps to the first sample
only for looping clips. Held parts retain one texture; pose sprites change on
the shared sample boundary. Textures themselves are not blended. A held part's
local offset can also interpolate; a different raster keeps its own pivot until
the texture changes. Visibility and draw order follow the sampled pose.

`layers()` returns stable layer IDs, separate image references, exact Fraction
strings for `anchor`, `local_origin` and `position`, palette regions and masks.
The target engine caches those textures and retains layer objects. Mirroring is
an explicit scene transform; anatomical IDs retain their meaning. The method
works without a browser, viewer, renderer, image loader or file writer.

`set_selection` and `set_appearance` preserve state and exact elapsed time. They
require the same rig/motion and validate before mutation. `frame()` is the
explicit CPU inspection adapter. `parts-preview` snapshots and `parts-build`
are explicitly requested inspection/export tools. The 32 × 3 count is a
validation space, not a default collection of 96 clips.

`sprite-gen parts-play --assembly path/to/library/assembly.json --port 8766`
opens the same runtime in a read-only local HTTP player at `127.0.0.1`. It needs
a frozen motion and an active appearance; an imported candidate alone cannot
be played as a fitted parts demo. Pause/seek, slot selections, optional equipment
and independent subslots use the shared clock. `/api/layers` serves separate,
hash-addressed texture URLs and interpolated placements. The browser retains a
canvas for each layer; it never requests a completed character PNG. Texture
pixels change only at a pose or selection boundary. `/api/frame` is an explicit
CPU comparison adapter. The server bounds its selection cache and refuses
unknown or duplicate parameters. HTTP playback never writes an assembly or
bakes an appearance. The viewer is an optional inspection client, not a runtime
dependency.

For one body pack (flat, or segmented into node pieces) with independent layers, `/api/library` supplies the
shared tracks and texture library once. The browser then samples the common
clock locally, so continuous playback does not wait for a server frame request.
The same client is exported by `parts-export` as `manifest.json`, individual
PNGs and a self-contained `preview.html`. `/standalone` opens that embedded
preview; `/api/export` downloads its portable ZIP. This draft export creates
zero appearance clips and grants no release approval. The first adapter supports
one choice per slot, a segmented body, ordering with the draw table, cover
rules, palettes, masks (including a mask on another layer's node) and explicit
mirror. Body replacement, slot precedence and multiple choices remain supported
by the Python runtime but are explicit capability errors for this adapter; the
local viewer uses the server-layer path for those profiles.
See [layer authoring and export](08-layer-export.md).

The body is an explicit pack. Segmented bodies must provide every required
topology node. `provides` identifies indivisible raster interfaces, and `replaces`
removes all body pieces providing those nodes. A raster supplying several nodes
cannot be partly removed. Cross-pack duplicate providers are errors unless the
topology declares a slot `overrides` relationship. Humanoid presets declare
mouth/eyes/brows over expression, bangs over hair, and side sleeves/footwear over
their parent slots. Only shared provided nodes are suppressed: a mouth override
leaves the expression's eye pieces in place. Cyclic slot precedence is rejected.
Several pieces of one pack can jointly provide one node. Flat body packs support
overlays only; they cannot claim separated limb replacement.

Body and part pieces share one draw list. Per-sample `before`/`after` targets may
be topology nodes, unique piece IDs, or qualified `pack-id.piece-id` names.
Relations to known hidden/replaced pieces become inactive. Unknown or ambiguous
targets and cycles fail. Unconstrained ties use lexical qualified IDs, so authors
must declare meaningful overlaps. Socket visibility and placement visibility
both apply; anatomical IDs retain their meaning through visibility and mirror.

## Draw table and cover rules

A topology may declare `layers`: its nodes from back to front, the draw table
(MapleStory's zmap). Entries left unordered by explicit `before`/`after` draw by
their rank in the table: an entry's own `node`, else the first listed node it
provides; nodes missing from the table draw after it. On one rank the body
draws before what is worn there, so a sleeve on `arm_r` covers the front arm and
a sleeve on `arm_l` stays behind the torso. Remaining ties use the qualified ID.
Explicit constraints still win over the table, and a topology without `layers`
orders exactly as before. The table is per topology; a rig whose view swaps near
and far limbs needs its own topology (or mirror).

```json
"layers": ["arm_l", "hand_l", "leg_l", "foot_l", "hair_back", "root", "torso",
           "leg_r", "foot_r", "head", "eye_l", "eye_r", "brow_l", "brow_r", "mouth",
           "hair_front", "arm_r", "hand_r"]
```

Cover rules replace masks for the common "a hat flattens the hair" case
(MapleStory's vslot). A binding lists the tags it `covers`; a piece with
`"cover_rule": {"tag": "headwear", "show": "uncovered"}` draws only while no
selected binding covers that tag, and `"show": "covered"` only while one does.
A hair pack therefore carries both its full and its capped drawing, and a cap
declares `"covers": ["headwear"]`. The plan records the active covers, and a
piece hidden by a rule appears in `suppressed` as `cover:<tag>`. Rules do not
cut pixels; the capped drawing is authored like any other piece. A cross-layer
mask remains available when a cut, not a different drawing, is wanted.

Node order targets match `provides` interfaces. An accessory's primary `node`
describes its attachment constraint; it does not automatically make the accessory
a provider of that node. Two overlays attached to `head` can both order after
the head raster without ordering after each other. Target a specific overlay by
its qualified piece ID when its relative order matters.

Placement is integer `socket - pivot + offset`, with original crop geometry
already preserved by import. Art clipping fails unless `allow_clip` is explicit;
preview records clipped pixel counts. Masks keep their target raster's alpha,
using the legacy integer `(alpha * mask + 127) // 255` operation. `rig-canvas`
masks start at (0,0); `part-local` masks start at the issuing piece's placement.
They apply to the named target node's providers regardless of drawing order.
Alpha masks read alpha; luma masks multiply grayscale intensity by alpha.
Zero outside the mask means hidden. Multiple target masks multiply.
Only the target node's `provides` interfaces receive its mask; attaching an
eye or accessory to `head` does not make that overlay a head provider.

Palette regions are disjoint coverage masks. Sample regions override a piece's
channel region. v1 blends flat target RGB using integer rounding and preserves
source alpha; it does not preserve shaded material ramps. Region overlap or size
mismatch fails. Transparent output RGB is canonicalized to zero.

Mirror requires both `rig.mirror_allowed` and `binding.mirror_safe` on every
selected pack. Output pixels, sockets, local pivots and placement offsets reflect
together; relative draw order and anatomical IDs are retained. The original plan
and source offset remain available alongside transformed preview metadata.
All presets default to refusing mirror, including the asymmetric fixture.

Objects are published below `appearances/` and `previews/` by content digest.
Appearance activation uses an assembly revision check. Replacing a named
appearance detaches its old consumers/reviews without deleting history.
Asset reads verify hashes under the workspace root and enforce byte/pixel limits.
No source file is rewritten, resized, rotated, recentered or silently retargeted.

Synthetic pixel tests cover five rigs, idle/walk/run, a five-slot 32 appearance /
96 clip matrix, replacements, subslot precedence, body ordering, masks, palette,
visibility, clipping and mirror. These tests validate engine behavior and are
separate from acceptance of the actual explorer artwork and animations.
