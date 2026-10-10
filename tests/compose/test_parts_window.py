# SPDX-License-Identifier: Apache-2.0
import json
import shutil
import subprocess
import pytest
from compose.test_export_parts import flat_workspace
from sprite_gen.compose import export_parts
from sprite_gen.curate import parts_motion


@pytest.mark.parametrize('timebase', [8, 24])
def test_window_keeps_original_samples_at_fractional_boundaries_and_outfit_swaps(tmp_path, timebase):
    if not shutil.which('node'):
        pytest.skip('Node is required for the browser sampler')
    assembly = flat_workspace(tmp_path)
    root, index, registry, _ = parts_motion._active(assembly)
    _, _, bundle = export_parts.pack(root, index, registry, interpolation='frame')
    # The same variable-duration samples, including fractional milliseconds.
    clip = next(c for c in bundle['manifest']['clips'] if c['state']=='walk')
    clip['timebase'] = timebase
    for sample, ticks in zip(clip['samples'], (2,1,2,3)):
        sample['duration_ticks'] = ticks
    file = tmp_path/'input.json'
    file.write_text(json.dumps(bundle))
    code = """
const {LayerRuntime}=require(process.argv[1]), bundle=JSON.parse(require('fs').readFileSync(process.argv[2]));
const r=new LayerRuntime(bundle), before=JSON.stringify(r.data.clips), w=r.sampleWindow('walk',2,3);
const samples=[];
for(let ms=w.start;ms<w.end;ms++){
  const a=r.scene('walk',ms,{badge:'badge.a'}), b=r.scene('walk',ms,{badge:'badge.b'});
  if(a.sample_id!==b.sample_id||a.sample_tick!==b.sample_tick)throw Error('swap changed clock');
  samples.push(a.sample_id);
}
const rejects=[[0,3],[2,1],[1,5],[1.5,3]].map(([a,b])=>{try{r.sampleWindow('walk',a,b);return false;}catch(e){return true;}});
process.stdout.write(JSON.stringify({w,samples:[...new Set(samples)],unchanged:before===JSON.stringify(r.data.clips),rejects}));
"""
    result = subprocess.run(['node','-e',code,str(export_parts.STATIC/'runtime.js'),str(file)],
                            capture_output=True,text=True,check=True)
    actual = json.loads(result.stdout)
    assert actual['samples'] == [clip['samples'][i]['id'] for i in (1,2)]
    assert actual['unchanged'] and all(actual['rejects'])
    assert actual['w']['start'] == (250 if timebase==8 else 84)
    assert actual['w']['end'] == (625 if timebase==8 else 209)
