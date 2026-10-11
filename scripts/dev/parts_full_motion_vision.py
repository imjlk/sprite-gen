# SPDX-License-Identifier: Apache-2.0
"""Live image observations of every actual motion frame, without art approval."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil

from sprite_gen.gen.facing_vision import codex_inspect
from sprite_gen.spec import parts_assets as a, parts_workspace as w

PROMPT='''Critically inspect these consecutive actual layered sprite frames.
Columns: the unchanged bare body reference; actual hair+socks+shoes composition;
enlarged BARE back ankle; enlarged ACTUAL back ankle; enlarged BARE front ankle;
enlarged ACTUAL front ankle. Each labeled row is ONE
real frame. Check ALL specified frame IDs. This is a high-resolution SD illustrated
character, not pixel art. Reject obvious defects rather than accepting improvement.
Hair: ear opening matches body ear, no bald crown, skin halo or displaced hair.
Use the enlarged bare anatomy to distinguish source ankle contours from part
spill; do not claim fabric extends outside the calf without that comparison.
Socks: fitted calf cuff, no detached white triangle, hard clip, fabric on the other
limb or missing ankle bridge. Shoes: consistent navy/cream product volume, rigid
sole, correct toe direction, no needle-thin or crushed shoe, naked toes, floating
footwear, wrong calf entry, cutoff collar or random size/shape changes. Compare
neighboring frames for attachment and design continuity. The neutral grey board background is transparency,
not cream fabric: a missing calf exposing background is a body-ownership defect.
Distinguish such a hole from an opaque sock by the actual bare/composed pair.
Report visible uncertain defects; never declare an unobservable rear shoe approved.
Do not invent speed or gait approval from stills; a biomechanically questionable source pose is a BODY
issue, not something to hide by approving parts. This page contains only the
labeled subset of the active motion's actual frame sequence.
Return strict JSON, no Markdown: {"poses": {FRAME: {"hair":"pass|rework|unknown",
"socks":"pass|rework|unknown","shoes":"pass|rework|unknown","issues":[specific
visible observations]}},"limits":[specific uncertainties]}. No tools or code.
This is an observational audit only. It NEVER grants curator approval or release.
Required frame IDs: '''


def inspect(evidence, *, revision='vision-r1', pages=None):
    state=json.loads((evidence/'evidence.json').read_text())
    history=evidence/revision
    if history.exists():raise ValueError('preserve prior calls; use a new revision')
    history.mkdir()
    def page(file):
        scope=history/file.stem;scope.mkdir();shutil.copy2(file,scope/'input.png')
        name,start=file.stem.split('-audit-');start=int(start)
        count=state.get('motion_pose_counts',{}).get(name) or sum(row['pose'].startswith(name+'.') for row in state['poses'])
        poses=[f'{name}.{n:02}' for n in range(start,min(start+6,count))]
        prompt=PROMPT+json.dumps(poses);(scope/'prompt.txt').write_text(prompt)
        report={'appearance':state['appearance'],'body':state['body'],'motion':state['motion'],'parts':state['parts'],
            'input_sha256':a.sha256(file.read_bytes()),'prompt_sha256':a.sha256(prompt.encode()),'required_poses':poses,
            'art_approved':False,'release_ready':False}
        try:
            raw,metadata=codex_inspect(scope/'input.png',scope,prompt=prompt);(scope/'response.txt').write_text(raw)
            answer=json.loads(raw)
            assert set(answer['poses'])==set(poses),'model omitted or invented frames'
            assert all(row[key] in ('pass','rework','unknown') for row in answer['poses'].values() for key in ('hair','socks','shoes'))
            report.update(status='completed',transport=metadata,response_sha256=a.sha256(raw.encode()),answer=answer)
        except (Exception,SystemExit) as exc:report.update(status='failed',error=str(exc))
        (scope/'report.json').write_bytes(w.json_bytes(report));return report
    files=sorted(evidence.glob('*-audit-*.png'))
    if pages:
        by_name={f.stem:f for f in files}
        if not set(pages)<=set(by_name):raise ValueError('unknown audit page')
        files=[by_name[name] for name in sorted(set(pages))]
    with ThreadPoolExecutor(max_workers=2) as pool:reports=list(pool.map(page,files))
    rows={pose:row for report in reports if report['status']=='completed' for pose,row in report['answer']['poses'].items()}
    expected=sum(len(r['required_poses']) for r in reports)
    result={'status':'completed' if len(rows)==expected else 'incomplete','frames_observed':len(rows),'frames_expected':expected,
        'art_approved':False,'release_ready':False,'poses':rows,'pages':[{'required_poses':r['required_poses'],'status':r['status'],'error':r.get('error')} for r in reports],
        'scope':'actual still-frame attachment and between-frame appearance observations; no timing, gait or human approval'}
    (history/'summary.json').write_bytes(w.json_bytes(result));return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--evidence',type=Path,required=True);p.add_argument('--revision',default='vision-r1');p.add_argument('--pages',nargs='+')
    result=inspect(**vars(p.parse_args()))
    print(json.dumps({k:v for k,v in result.items() if k!='poses'}))
