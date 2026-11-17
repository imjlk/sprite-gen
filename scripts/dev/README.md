# scripts/dev — maintainer experiments, not pipeline stages

These are not `sprite-gen` verbs and the skill never requires them. Each one is a
measurement or a proof that fed a rule now living in the package or its docs:

| Script | What it measures |
|---|---|
| `breathe_mutation_battery.py` | plants each breathe-contract mutation in the source and checks the tests bite (exit 0 = every net bites) |
| `measure_align_sigma.py` | per-frame horizontal jitter σ of `fit.align_x` variants on a source run's raw strips |
| `check_visible_magenta.py` | chroma-leak guard for screenshots (`sprite_gen.frames.check_visible_magenta`) |
| `source_restoration_demo.py` | source restoration end to end on a drawn walker with two melted cells ([loop-comparison](../../docs/loop-comparison.md#restoring-the-active-cut-from-source)): `video-source-manifest`, then `video-loop-repair` and `video-loop-compare` twice (each `improved`) and a third request that changes nothing; exit 0 only when every check holds. Writes the release showcase GIFs and a `summary.json` with no path or time (`--out-dir <new dir>`) |
| `tween_chain_demo.py` | `tween` beside `video-chain` on four joins between frames of the repository's Pip clips, two with one end off the floor and a landing beside the clip's own frames ([tween](../../docs/tween.md#1-tween-and-video-chain)): two frames between, by `tween --method strip` (`--ground shared` and the default `auto`), `tween --method video` and `video-chain --bridge 2` (`--refuse held` and the default), every one through the CLI, each tween row `--tries` times (default 3); writes each run's folder, a `summary.json` of exit codes, seconds, report numbers, heights off the floor and, on the landing, each frame against the clip's, and the comparison GIFs and sheets (`--out-dir <new dir>`; the tween rows call the provider, `--no-provider` runs the chain rows only, `--resume` runs again only what made nothing) |

Run them with the project interpreter (`.venv/bin/python scripts/dev/<name>.py`).
