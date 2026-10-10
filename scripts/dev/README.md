# scripts/dev — maintainer experiments, not pipeline stages

These are not `sprite-gen` verbs and the skill never requires them. Each one is a
measurement or a proof that fed a rule now living in the package or its docs:

| Script | What it measures |
|---|---|
| `breathe_mutation_battery.py` | plants each breathe-contract mutation in the source and checks the tests bite (exit 0 = every net bites) |
| `measure_align_sigma.py` | per-frame horizontal jitter σ of `fit.align_x` variants on a source run's raw strips |
| `check_visible_magenta.py` | chroma-leak guard for screenshots (`sprite_gen.frames.check_visible_magenta`) |
| `source_restoration_demo.py` | source restoration end to end on a drawn walker with two melted cells ([loop-comparison](../../docs/loop-comparison.md#restoring-the-active-cut-from-source)): `video-source-manifest`, then `video-loop-repair` and `video-loop-compare` twice (each `improved`) and a third request that changes nothing; exit 0 only when every check holds. Writes the release showcase GIFs and a `summary.json` with no path or time (`--out-dir <new dir>`) |
| `parts_pipeline_proof.py` | five-rig synthetic import/freeze/baked-export regression proof; explicitly not the default runtime customization workflow |
| `sd_explorer_art_study.py` | code-authored coordinate/art study that produced `first-art-sheet.png`; not the selected SD artwork and no Grok/imagegen call |
| `sd_explorer_contribution_demo.py` | takes supplied sneaker art and authored masks through public fit/import/repair APIs; compares all 32 basic choices across 28 shared poses with the browser sampler; creates no new body or appearance clips |
| `sd_explorer_polish.py` | imports supplied hair/boot/run-body redraws, aligns every clip's head attachment with the generic `parts_sockets.match` region matcher, reuses six exact footwear angles, rebinds authored interfaces, and measures aligned source-head stability; no runtime body decomposition or appearance-clip generation |
| `sd_avatar_process.py` | rebuilds the HQ SD explorer by the pose-first process (docs/parts/09): Maple-style topology with a draw table, motion with pose IDs, codex-painted body labels (near/far limbs corrected by shading) cut into a segmented body, HQ garments as pose maps split by body piece, capped hair under a covering hat, redrawing failed poses over the parts they are worn with, five new designs drawn per pose, outfit contact sheets for review, coverage and export |
| `sd_avatar_garment_audit.py` | compares one per-pose garment, composed over the segmented body, with the dressed cells the model drew, and attributes each missing garment pixel to extraction (in no piece) or to the layer drawn over it |
| `parts_first_result.py` | copies an existing frozen SD library, authors fixture-specific interfaces, produces two independent tops through public part production APIs, and exports their unchanged shared walk with review contact sheets; drafts keep failed QA and never receive automatic art approval ([first result](../../docs/parts/11-part-only-production.md)) |

Run them with the project interpreter (`.venv/bin/python scripts/dev/<name>.py`).


`parts_illustration_demo.py` builds and verifies the [native illustration fitting experiment](../../docs/parts/15-illustration-experiment.md)
through the public parts APIs. Its authored ownership profile is bound to one source RGBA hash;
other bodies need their own authored profile. Stages are prepare, import, reference, slice, refit,
extend-socks, finish and verify. It does not generate walk/run, auto-approve art, or call an image provider.
