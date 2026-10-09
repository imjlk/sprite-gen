# SPDX-License-Identifier: Apache-2.0
import json
import threading
import time
from copy import deepcopy
from http.server import ThreadingHTTPServer
import urllib.request
import urllib.error

import pytest
from PIL import Image
from test_parts_production import setup, draw_batch
from sprite_gen.frames import parts_production as production, parts_interfaces, parts_quality
from sprite_gen.serve.serve_parts_review import ReviewDesk, handler_for
from sprite_gen.spec import parts_contract as contract, parts_workspace as workspace


def ready(tmp_path):
    assembly = setup(tmp_path)
    draw_batch(assembly, production.sheet(assembly, 'test.badge'))
    production.slice_batch(assembly, 'test.badge')
    production.check(assembly, 'test.badge')
    production.build(assembly, 'test.badge')
    production.review(assembly, 'test.badge', reviewer='fixture', approve=['all'])
    return assembly


def test_passed_approved_pose_can_be_sent_back_without_replacing_old_candidate(tmp_path):
    assembly = ready(tmp_path)
    before = production._load(assembly.parent, 'test.badge')['tracks']['main']['poses']
    request = production.request_rework(assembly, 'test.badge', ['walk.01'], 'raise the badge by one pixel')
    rows = production._load(assembly.parent, 'test.badge')['tracks']['main']['poses']
    assert rows['walk.01']['selected'] == before['walk.01']['selected']
    assert rows['walk.01']['decision'] == 'unverified'
    assert rows['walk.02']['decision'] == 'approved'
    assert request['draw_poses'] == ['walk.01']
    with pytest.raises(contract.PartsContractError, match='rework'):
        production.review(assembly, 'test.badge', reviewer='fixture', approve=['walk.01'])
    made = production.sheet(assembly, 'test.badge')
    assert made['poses'] == ['walk.01']
    assert 'raise the badge by one pixel' in (assembly.parent / made['prompt']).read_text()
    for bad in ('walk.01', [1], [], ['unknown']):
        with pytest.raises(contract.PartsContractError):
            production.request_rework(assembly, 'test.badge', bad, 'fix')
    with pytest.raises(contract.PartsContractError):
        production.request_rework(assembly, 'test.badge', ['walk.01'], ' ')


def wait_job(desk, identity):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        job = desk.job(identity)
        if job['state'] != 'running':
            return job
        time.sleep(.02)
    pytest.fail('job did not finish')


def test_job_runs_one_batch_and_retains_failures_for_explicit_retry(tmp_path, monkeypatch):
    assembly = ready(tmp_path)
    desk = ReviewDesk(assembly)
    original_draw = production.draw
    gate = threading.Event()
    entered = threading.Event()
    def failing(*a, **kw):
        entered.set(); gate.wait(5)
        return {'pages': [{'status': 'failed', 'error': 'fixture provider refused'}]}
    monkeypatch.setattr(production, 'draw', failing)
    before = desk.part('test.badge')['poses'][0]['selected']
    job = desk.start_rework('test.badge', {'poses':['walk.01'], 'note':'raise the badge'})
    assert entered.wait(5)
    with pytest.raises(contract.PartsContractError, match='running'):
        desk.act('test.badge', 'review', {'reviewer':'fixture','approve':['walk.01']})
    gate.set()
    failed = wait_job(desk, job['id'])
    assert failed['state'] == 'failed' and 'slice' not in failed['results']
    assert desk.part('test.badge')['poses'][0]['selected'] == before
    assert ReviewDesk(assembly).job(job['id'])['error'] == failed['error']
    def drawing(assembly, part, *, batch, track):
        assert batch
        state = production._load(assembly.parent, part)
        entry = production._batch(production._track(state, track), batch)
        draw_batch(assembly, entry)
        return original_draw(assembly, part, batch=batch, track=track)
    monkeypatch.setattr(production, 'draw', drawing)
    done = wait_job(desk, desk.start_rework('test.badge', {'poses':['walk.01'], 'note':'raise the badge'})['id'])
    assert done['state'] == 'completed'
    assert list(done['results']) == ['sheet','draw','slice','check','build']
    assert done['results']['slice']['candidates'][0]['pose'] == 'walk.01'
    assert desk.part('test.badge')['poses'][0]['decision'] == 'unverified'
    assert desk.part('test.badge')['poses'][1]['decision'] == 'approved'
    assert desk.job(job['id'])['state'] == 'failed'
    assert ReviewDesk(assembly).job(done['id'])['state'] == 'completed'


def test_root_is_editable_but_wrong_host_origin_and_token_are_rejected(tmp_path):
    desk = ReviewDesk(setup(tmp_path))
    server = ThreadingHTTPServer(('127.0.0.1',0),handler_for(desk))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{server.server_port}'
    try:
        assert desk.token in urllib.request.urlopen(url).read().decode()
        assert desk.token not in urllib.request.urlopen(url+'/?readonly=1').read().decode()
        for headers in ({'Host':'evil.test'}, {'Origin':'http://evil.test','X-Review-Token':desk.token}, {}):
            with pytest.raises(urllib.error.HTTPError) as exc:
                urllib.request.urlopen(urllib.request.Request(url+'/api/act/test.badge/check',data=b'{}',headers=headers))
            assert exc.value.code == 403
    finally:
        server.shutdown();server.server_close()


def test_stable_regions_are_explicit_raw_socket_aligned_and_invalidate_approval(tmp_path):
    assembly = ready(tmp_path)
    state = production._load(assembly.parent,'test.badge')
    config = deepcopy(state['config'])
    config['qa']={'stable_regions':[{'id':'badge','socket':'head','bounds':[-30,-30,0,0],
                                  'reference_pose':'walk.01','max_alpha_pixels':0,'max_rgb_pixels':0,'rgb_threshold':24}]}
    path=tmp_path/'qa.json';path.write_text(json.dumps(config))
    production.configure(assembly,'test.badge',path)
    state=production._load(assembly.parent,'test.badge')
    assert all(row['decision']=='unverified' and row['check'] is None for row in state['tracks']['main']['poses'].values())
    production.check(assembly,'test.badge')
    check=production._load(assembly.parent,'test.badge')['tracks']['main']['poses']['walk.01']['check']
    assert 'stable_regions' in check['metrics'] and check['qa_policy']==config['qa']
    config['qa']['stable_regions'][0]['max_alpha_pixels']=-1
    path.write_text(json.dumps(config))
    with pytest.raises(contract.PartsContractError, match='nonnegative'):
        production.configure(assembly,'test.badge',path)
    # Identical area but altered RGB must be detected; pixels outside the authored region do not matter.
    for pose,color in [('a','red'),('b','blue')]:
        folder=tmp_path/pose;folder.mkdir();Image.new('RGBA',(4,4),color).save(folder/'part.png')
    rows={p:{'selected':p} for p in ('a','b')}
    first={p:('clip',{'sockets':{'head':{'position':[0,0],'visible':True,'confidence':'verified'}}}) for p in rows}
    rule={**config['qa']['stable_regions'][0],'bounds':[0,0,2,2],'reference_pose':'a','max_alpha_pixels':0}
    delta=parts_quality.temporal(tmp_path,rows,'b',[rule],first)[0]
    assert delta['alpha_pixels']==0 and delta['rgb_pixels']==4 and delta['status']=='warning'
    assert parts_quality.temporal(tmp_path,rows,'b',[],first)==[]
    assert next(r for r in parts_quality.criteria({'required_pixels':0}) if r['id']=='allowed_pixels')['status']=='미설정'


def test_held_rework_redraws_configured_reference_and_invalidates_all_poses(tmp_path):
    assembly=setup(tmp_path)
    config=json.loads((tmp_path/'badge.json').read_text());config.update(mode='held',reference_pose='walk.02')
    path=tmp_path/'held.json';path.write_text(json.dumps(config));production.configure(assembly,'test.badge',path)
    request=production.request_rework(assembly,'test.badge',['walk.01'],'simplify the outline')
    assert request['poses']==['walk.01','walk.02'] and request['draw_poses']==['walk.02']
    assert production.sheet(assembly,'test.badge')['poses']==['walk.02']


def test_interfaces_bind_to_body_revision_and_do_not_activate_proposed_config(tmp_path):
    assembly=setup(tmp_path);index,_=workspace.read_workspace(assembly)
    profile={'kind':'parts-interface-profile','schema_version':1,'id':'hair.test','slot':'hair','rig':index['rig'],'body':index['body'],
             'rules':{'required':[{'socket':'head','bounds':[-32,-32,0,0]}],
                      'allowed':[{'socket':'head','bounds':[-34,-34,2,2],'body_only':False}]}}
    path=tmp_path/'profile.json';path.write_text(json.dumps(profile));out=tmp_path/'proposed.json'
    old=production._load(assembly.parent,'test.badge')['config']
    result=parts_interfaces.derive(assembly,'test.badge',path,out)
    assert result['poses']==2 and not result['activated']
    assert production._load(assembly.parent,'test.badge')['config']==old
    config=json.loads(out.read_text())
    assert set(config['pose_checks'])=={'walk.01','walk.02'}
    assert Image.open(assembly.parent/config['pose_checks']['walk.01']['allowed']).convert('L').getbbox()==(30,30,66,66)
    assert parts_interfaces.derive(assembly,'test.badge',path,out)==result
    for key,value in [('slot','footwear'),('body',[]),('rig',{'id':index['rig']['id'],'digest':'0'*64})]:
        wrong={**profile,key:value};path.write_text(json.dumps(wrong))
        with pytest.raises(contract.PartsContractError,match='revision'):
            parts_interfaces.derive(assembly,'test.badge',path,out)


def test_running_job_becomes_explicit_failure_after_restart(tmp_path):
    assembly=setup(tmp_path);desk=ReviewDesk(assembly)
    production.request_rework(assembly,'test.badge',['walk.01'],'raise the badge')
    desk.jobs['interrupted']={'id':'interrupted','part':'test.badge','poses':['walk.01'],
                              'state':'running','step':'draw','results':{}}
    desk.update_job('interrupted')
    restored=ReviewDesk(assembly).job('interrupted')
    assert restored['state']=='failed' and 'restarted' in restored['error']
    assert production._load(assembly.parent,'test.badge')['tracks']['main']['poses']['walk.01']['rework']['status']=='failed'


def test_footwear_interfaces_follow_named_foot_sockets(tmp_path):
    assembly=setup(tmp_path)
    config=json.loads((tmp_path/'badge.json').read_text())
    config.update(slot='footwear',node='foot_l',socket='foot_l')
    path=tmp_path/'shoe.json';path.write_text(json.dumps(config));production.configure(assembly,'test.badge',path)
    index,_=workspace.read_workspace(assembly)
    profile={'kind':'parts-interface-profile','schema_version':1,'id':'footwear.test','slot':'footwear',
             'rig':index['rig'],'body':index['body'],
             'rules':{'allowed':[{'socket':'foot_l','bounds':[-2,-2,4,2],'body_only':False},
                                 {'socket':'foot_r','bounds':[-3,-2,3,2],'body_only':False}]}}
    path=tmp_path/'feet-profile.json';path.write_text(json.dumps(profile));out=tmp_path/'feet-proposed.json'
    result=parts_interfaces.derive(assembly,'test.badge',path,out)
    masks=json.loads(out.read_text())['pose_checks']
    assert result['poses']==2
    assert Image.open(assembly.parent/masks['walk.01']['allowed']).convert('L').getbbox()==(61,62,68,66)
    profile['rules']['allowed'][0]['bounds']=[0,0,10000,2];path.write_text(json.dumps(profile))
    with pytest.raises(contract.PartsContractError,match='canvas'):
        parts_interfaces.derive(assembly,'test.badge',path,out)


def test_marked_passed_poses_are_drawn_together_with_instructions(tmp_path, monkeypatch):
    assembly=ready(tmp_path)
    config=production._load(assembly.parent,'test.badge')['config'];config['sheet']['page_size']=1
    path=tmp_path/'paged.json';path.write_text(json.dumps(config));production.configure(assembly,'test.badge',path)
    original_draw=production.draw
    def drawing(assembly, part, *, batch, track):
        entry=production._batch(production._track(production._load(assembly.parent,part),track),batch)
        assert entry['poses']==['walk.01','walk.02'] and len(entry['pages'])==2
        assert 'simplify both badges' in (assembly.parent/entry['prompt']).read_text()
        draw_batch(assembly,entry)
        return original_draw(assembly,part,batch=batch,track=track)
    monkeypatch.setattr(production,'draw',drawing)
    desk=ReviewDesk(assembly)
    done=wait_job(desk,desk.start_rework('test.badge',{'poses':['walk.01','walk.02'],'note':'simplify both badges'})['id'])
    assert done['state']=='completed' and len(done['results']['slice']['candidates'])==2
    assert all(row['decision']=='unverified' for row in desk.part('test.badge')['poses'])
