# SPDX-License-Identifier: Apache-2.0
"""Observe the actual joint attachment board without granting part approval."""

import argparse
import json
from pathlib import Path
import shutil

from sprite_gen.curate import parts_motion
from sprite_gen.gen.facing_vision import codex_inspect
from sprite_gen.spec import parts_assets as a, parts_workspace as w

PROMPT = """Inspect this actual rendered parts contact sheet critically. It has FOUR
independently authored reference poses, not a full animation. Each row is run.0,
run.1, run.2, run.3. Columns: original bare body ear reference; old hair/body
alignment; current independently edited hair rendered on the fixed body;
current sock+shoe on back ankle; current sock+shoe on front ankle. ONLY current
columns 3-5 are under review. Compare the current ear outline to the bare-body
reference. Look for clipped ear contours, wrong notch placement, empty skin
halos, smeared strands, hard cut seams, calf/sock attachment, cuff tilt, open shoe
collar seams, floating shoes or discontinuities. Do not accept defects merely
because they are improved. Distinguish an artifact from the body's own ear line
by comparing the reference. Do not invent motion quality from four stills.
Return JSON with "poses" mapping each pose to {"hair":"pass|rework|unknown",
"socks_shoes":"pass|rework|unknown", "issues":[concrete visible observations]};
"overall":"pass|rework|unknown"; and "limits" array. This is an observational
attachment audit only, never curator approval or release readiness. Output no
Markdown. No tools or code needed."""


def inspect(out, evidence, revision):
    _, index, registry, _ = parts_motion._active(out/'library/assembly.json')
    state = json.loads((evidence/'current-state.json').read_text())
    assert state['appearance'] == index['appearance'], 'stale capture: appearance'
    assert state['body'] == index['body'] and state['motion'] == index['motion'], 'stale body/motion'
    for name, ref in state['parts'].items():
        assert ref['digest'] == registry[name]['digest'], 'stale capture: '+name
    history = evidence/'vision-history'/revision
    if history.exists():
        raise ValueError('use a fresh audit revision; earlier calls are preserved')
    history.mkdir(parents=True)
    board = evidence/'joint-visual-audit.png'
    shutil.copy2(board,history/board.name)
    (history/'joint-vision-prompt.txt').write_text(PROMPT,encoding='utf-8')
    report = {'scope':'attachment contact audit only; not part approval or full motion QC',
              'input_sha256':a.sha256(board.read_bytes()),'prompt_sha256':a.sha256(PROMPT.encode()),
              'appearance':state['appearance'],'body':state['body'],'motion':state['motion'],
              'parts':state['parts'],'art_approved':False,'release_ready':False}
    try:
        raw, metadata = codex_inspect(history/board.name,history,prompt=PROMPT)
        (history/'joint-vision-answer.txt').write_text(raw,encoding='utf-8')
        report.update(transport=metadata,response_sha256=a.sha256(raw.encode()),status='completed')
        try:
            report['observation']=json.loads(raw)
        except json.JSONDecodeError:
            report['status']='invalid-json'
    except (Exception,SystemExit) as exc:
        report.update(status='failed',error=str(exc))
    (history/'joint-vision-report.json').write_bytes(w.json_bytes(report))
    for name in ('joint-vision-report.json','joint-vision-prompt.txt','joint-vision-answer.txt'):
        if (history/name).exists():
            shutil.copy2(history/name,evidence/name)
        elif (evidence/name).exists():
            # A failed current call must not display an older successful answer.
            (evidence/name).unlink()
    return report


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--revision',required=True)
    print(json.dumps(inspect(**vars(parser.parse_args())),ensure_ascii=False))
