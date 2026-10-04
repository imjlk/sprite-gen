# Deterministic multi-state baked export

> Owns: PR06 atlas snapshots and release gate · Index: [docs/README.md](../README.md)

```sh
sprite-gen parts-build --assembly ./library/assembly.json \
  --mode preview --out ./library/builds/explorer
sprite-gen parts-build --assembly ./library/assembly.json \
  --mode release --out ./library/builds/explorer
```

Builds consume the same resolved samples as preview and QA. `preview` does not
grant release approval. `release` requires a current approved **appearance**
review covering all five explicit decisions. Part or binding reviews alone are
insufficient. Render/QA/export policy, implementation or dependency version,
source bytes, timing, requested states, mask and palette changes invalidate the
evidence. Release recomputes it before publishing.

Each digest snapshot contains `atlas.png`, `manifest.json`, `aseprite.json`,
`build-lock.json`, `report.json`, plan/preview/QA evidence, normalized frames and
the approved review declaration. Only requested states of one concrete appearance
are baked. The engine does not expand a wardrobe's Cartesian product implicitly.

Atlas cells deduplicate canonical RGBA pixels in first timeline occurrence order.
Every ordered sample keeps its own ID, duration ticks, cell rectangle, sockets,
draw evidence and visibility, including repeated references to one cell.
`frame_layout.rows` contains explicit per-sample rectangles; consumers must not
infer cells from the number of parts or frames. `clips` retains exact timebase,
ticks and loop flags. The pure existing Aseprite mapping reads the same layout
and animation rows without any fabricated legacy run.

Aseprite uses integer milliseconds. Boundaries round half up; duration errors
are recorded as exact rational strings. The native manifest's tick timeline
remains authoritative. Samples that quantize below one millisecond are rejected
instead of silently disappearing. Aseprite tags cannot encode the native
loop flag; consumers needing it read `manifest.json`.

The output folder contains immutable `snapshots/<build-id>/<digest>/` directories.
`preview.json` and `release.json` are separate current pointers. Snapshot writes
complete before a pointer changes, and pointer activation checks the assembly
revision. Failed writes or concurrent input edits retain the previous release.
An unreferenced completed snapshot may remain after a stale activation; it is
safe history. Outputs must be external or under the workspace's `builds/` so
they cannot overwrite input objects. Sources and the assembly stay unchanged.

The lock hashes every output except itself, pins dependency/review references and
records Python/Pillow/NumPy/package versions, implementation digest and policies.
Portable outputs omit absolute paths and timestamps. Identical content under a
different source/output root produces the same snapshot in a fixed toolchain.
CLI result paths are absolute conveniences and are not part of its digest.

Tests cover full import→selection→review→approval→release, multiple states,
variable duration and ordered repeats, dedup metadata, relocated builds, byte
identity, source preservation, stale approvals, failed publication and concurrent
assembly changes. Target engine loading, animation adapter integration and final
art quality are separate verification scopes.
