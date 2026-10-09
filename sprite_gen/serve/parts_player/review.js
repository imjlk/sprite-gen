const $ = id => document.getElementById(id);
let token = new URLSearchParams(location.hash.slice(1)).get('token') || '';
const state = {part:null, data:null, pose:null, candidates:[], candidate:null, busy:false, revision:0};
const log = text => { $('log').textContent = text + '\n' + $('log').textContent; };
const node = (tag, text, className='') => { const el=document.createElement(tag); el.textContent=text; el.className=className; return el; };
const url = (pose, kind, candidate) => '/api/render/' + encodeURIComponent(state.part) + '/' + encodeURIComponent(pose) + '/' + kind + '.png' + (candidate ? '?candidate='+encodeURIComponent(candidate) : '');
async function get(path){ const r=await fetch(path); const j=await r.json(); if(!r.ok) throw new Error(j.error); return j; }
async function act(action, body={}){
 if(!token) throw new Error('읽기 전용입니다. 서버가 출력한 토큰 주소로 여세요.');
 const r=await fetch('/api/act/'+encodeURIComponent(state.part)+'/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-Review-Token':token},body:JSON.stringify(body)});
 const j=await r.json(); if(!r.ok) throw new Error(j.error); return j;
}
function controls(){
 document.querySelectorAll('[data-act],#select-candidate,#approve,#reject,#redo').forEach(b=>b.disabled=state.busy || !token);
 $('part').disabled=state.busy;
 const row=state.data?.poses.find(p=>p.pose===state.pose);
 const c=state.candidate;
 $('select-candidate').disabled ||= !c || c.selected;
 $('redo').disabled ||= !row;
 $('approve').disabled ||= !c?.selected || !state.data?.built_current || !['passed','warning'].includes(row?.check?.status);
 $('reject').disabled ||= !c?.selected || !state.data?.built_current;
 $('access').textContent=state.busy ? '작업 중 · 다른 후보를 둘러볼 수 있습니다' : token ? '검수·큐레이션 가능' : '읽기 전용';
}
function grid(){
 const data=state.data; if(!data) return;
 const selected=state.pose, filter=$('filter').value, view=$('view').value;
 $('summary').textContent=data.poses.length+'포즈 · 오류 '+data.poses.filter(p=>p.check?.status==='error').length+' · 승인 '+data.poses.filter(p=>p.decision==='approved').length+' · '+(data.built_current?'빌드 최신':'선택 변경 · 빌드 필요');
 $('view-note').textContent=view==='body' ? (data.built_current?'현재 빌드의 실제 레이어 순서로 재생합니다.':'선택 후보를 참조 위에 놓은 배치 미리보기입니다. 레이어 검수는 빌드 후 진행하세요.') : view==='errors' ? '덮임 누락(녹색) · 보호 침범(빨강) · 허용 영역 밖(파랑). 오류 위치만 표시합니다.' : '모든 후보는 같은 캔버스 좌표와 배율로 비교합니다.';
 $('grid').replaceChildren();
 for(const p of data.poses){
  if(filter==='error' && p.check?.status!=='error' || filter==='unverified' && p.decision==='approved') continue;
  const b=node('button','','pose'+(p.pose===selected?' active':'')); b.setAttribute('aria-label',p.pose+' 포즈');
  if(p.selected){ const img=document.createElement('img');img.alt=p.pose;img.loading='lazy';img.src=url(p.pose,view);img.onerror=()=>{img.hidden=true;b.prepend(node('span','그림을 읽지 못했습니다','muted'));}; b.append(img); }
  else b.append(node('p','그림 없음'));
  const row=node('div','','row');row.append(node('b',p.pose),node('span',p.check?.status||'unchecked','status '+(p.check?.status||'')));b.append(row,node('small',p.decision||'unverified','muted'));
  b.onclick=()=>inspect(p.pose).catch(e=>log(e.message));$('grid').append(b);
 }
 const ref=[...data.batches].reverse().find(b=>b.reference);
 $('reference-download').hidden=!ref;
 if(ref) $('reference-download').href='/api/reference/'+encodeURIComponent(state.part)+'/'+encodeURIComponent(ref.id)+'.zip';
}
function showCandidate(c){
 state.candidate=c;
 const row=state.data.poses.find(p=>p.pose===state.pose);
 $('pose-title').textContent=state.pose;
 $('pose-status').textContent=(c.selected?'현재 선택':'비교 중인 이전 후보')+' · 개별 검사 '+c.status+' · 후보 '+c.id.slice(0,10);
 $('reference-image').hidden=!c.images.includes('under');
 if(c.images.includes('under')) $('reference-image').src=url(state.pose,'reference',c.id);
 $('part-image').src=url(state.pose,'part',c.id);
 $('part-image').hidden=false;
 $('findings').replaceChildren(...c.findings.map(f=>node('li',f)));
 const missing=['qa_required','qa_protected','qa_allowed'].filter(n=>!c.images.includes(n));
 $('candidate-note').textContent=(missing.length?'검사 지도가 없는 후보입니다. 수치만으로 새 후보와 우열을 정하지 마세요. ':'')+(c.reference?'생성 기준 묶음이 고정된 후보입니다.':'이전 버전 후보: 원본과 출처는 보존됐지만 생성 전 기준 묶음은 없습니다.');
 $('evidence').textContent=JSON.stringify({placement:c.placement,method:c.method,metrics:c.metrics,source:c.source,generation_reference:c.reference},null,2);
 $('note').value=row.note||'';
 $('candidates').querySelectorAll('button').forEach(b=>b.classList.toggle('active',b.dataset.id===c.id));
 controls();
}
async function inspect(pose){
 const revision=++state.revision, part=state.part;
 state.pose=pose;grid();
 const data=await get('/api/candidates/'+encodeURIComponent(part)+'/'+encodeURIComponent(pose));
 if(revision!==state.revision || part!==state.part) return;
 state.candidates=data.candidates;$('candidates').replaceChildren();
 for(const [i,c] of data.candidates.entries()){
  const b=node('button','','candidate');b.dataset.id=c.id;b.setAttribute('aria-label','후보 '+(data.candidates.length-i)+(c.selected?' 현재 선택':''));
  const img=document.createElement('img');img.alt='독립 파츠 후보 '+(data.candidates.length-i);img.src=url(pose,'part',c.id);
  b.append(img,node('small','후보 '+(data.candidates.length-i)+(c.selected?' · 선택됨':'')),node('small',c.status,'status '+c.status));b.onclick=()=>showCandidate(c);$('candidates').append(b);
 }
 if(data.candidates.length) showCandidate(data.candidates.find(c=>c.selected)||data.candidates[0]);
 else{state.candidate=null;$('pose-title').textContent=pose+' · 그림 없음';$('pose-status').textContent='이 포즈의 참조 시트를 만들고 독립 파츠를 생성하세요.';$('part-image').removeAttribute('src');$('part-image').hidden=true;$('reference-image').removeAttribute('src');$('reference-image').hidden=true;$('findings').replaceChildren();$('evidence').textContent='';$('candidate-note').textContent='등록된 후보가 없습니다.';$('note').value='';controls();}
}
async function loadPart(){
 state.data=await get('/api/part/'+encodeURIComponent(state.part));
 if(!state.data.poses.some(p=>p.pose===state.pose)) state.pose=state.data.poses.find(p=>p.check?.status==='error')?.pose||state.data.poses[0]?.pose;
 grid();if(state.pose) await inspect(state.pose);else controls();
}
async function loadList(){
 const data=await get('/api/status');$('part').replaceChildren(...data.productions.map(p=>{const o=node('option',p.part+' · '+p.state);o.value=p.part;return o;}));
 state.part=state.part||data.productions[0]?.part;$('part').value=state.part;
 if(state.part) await loadPart();else $('summary').textContent='제작 기록이 없습니다.';
}
async function run(action,body={}){
 state.busy=true;controls();log(action+' 실행 중…');
 try{const result=await act(action,body);log(JSON.stringify(result,null,2));await loadList();}
 catch(e){log(action+' 실패: '+e.message);}
 finally{state.busy=false;controls();}
}
$('part').onchange=()=>{state.part=$('part').value;state.pose=null;state.revision++;loadPart().catch(e=>log(e.message));};
$('view').onchange=grid;$('filter').onchange=grid;
$('select-candidate').onclick=()=>run('select',{pose:state.pose,candidate:state.candidate.path});
$('redo').onclick=()=>run('sheet',{poses:[state.pose]});
for(const [id,key] of [['approve','approve'],['reject','reject']]) $(id).onclick=()=>{
 const reviewer=$('reviewer').value.trim();if(!reviewer){log('검수자를 입력하세요.');$('reviewer').focus();return;}
 run('review',{reviewer,[key]:[state.pose],note:$('note').value});
};
document.querySelectorAll('[data-act]').forEach(b=>b.onclick=()=>run(b.dataset.act));
controls();loadList().catch(e=>log(e.message));
window.addEventListener('hashchange',()=>{token=new URLSearchParams(location.hash.slice(1)).get('token')||'';controls();});
