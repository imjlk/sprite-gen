const $ = id => document.getElementById(id);
const readonly = !!document.querySelector('meta[name=review-mode]');
let token = readonly ? '' : document.querySelector('meta[name=review-token]')?.content || new URLSearchParams(location.hash.slice(1)).get('token') || '';
const state = {part:null, data:null, pose:null, candidates:[], candidate:null, busy:false, revision:0,marked:new Set(),drafts:new Map(),activeJob:null};
const draftKey=()=>state.part+'/'+state.pose;
const statusName=s=>({passed:'기계 검사 범위 내 통과',warning:'검사 경고',error:'검사 오류',unchecked:'미검사',partial:'기본 검사만 완료'}[s]||s);
const log = text => { $('log').textContent = text + '\n' + $('log').textContent; };
const node = (tag, text, className='') => { const el=document.createElement(tag); el.textContent=text; el.className=className; return el; };
const url = (pose, kind, candidate) => '/api/render/' + encodeURIComponent(state.part) + '/' + encodeURIComponent(pose) + '/' + kind + '.png' + (candidate ? '?candidate='+encodeURIComponent(candidate) : '');
async function get(path){ const r=await fetch(path); const j=await r.json(); if(!r.ok) throw new Error(j.error); return j; }
async function act(action, body={}){
 if(!token) throw new Error('읽기 전용입니다. 읽기 전용 주소의 ?readonly=1을 제거해 다시 여세요.');
 const r=await fetch('/api/act/'+encodeURIComponent(state.part)+'/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-Review-Token':token},body:JSON.stringify(body)});
 const j=await r.json(); if(!r.ok) throw new Error(j.error); return j;
}
function controls(){
 document.querySelectorAll('[data-act],#select-candidate,#approve,#reject,#redo,#redo-marked,#edit-candidate,#apply-visibility').forEach(b=>b.disabled=state.busy || !token || !state.data);
 $('part').disabled=state.busy;$('visibility').disabled=state.busy||!token;
 const row=state.data?.poses.find(p=>p.pose===state.pose);
 const c=state.candidate;
 $('select-candidate').disabled ||= !c || c.selected || c.compatible===false;$('edit-candidate').disabled ||= !c || c.compatible===false;
 $('redo').disabled ||= !row;
 $('redo-marked').disabled ||= !state.marked.size;
 $('redo-marked').textContent='표시한 '+state.marked.size+'포즈 새 이미지 생성';
 $('approve').disabled ||= !c?.selected || !state.data?.built_current || !['passed','warning'].includes(row?.check?.status);
 $('approve').disabled ||= ['requested','failed'].includes(row?.rework?.status);
 $('approve').disabled ||= ['body-raw','body-zraw'].includes($('view').value);
 $('reject').disabled ||= !c?.selected || !state.data?.built_current;
 $('access').textContent=state.busy ? '재작업 중 · 후보 비교 가능' : token ? '검수·큐레이션 가능' : '읽기 전용';
}
function grid(){
 const data=state.data; if(!data) return;$('visibility').value=data.config.visibility;
 const selected=state.pose, filter=$('filter').value, view=$('view').value;
 $('summary').textContent=data.poses.length+'포즈 · 오류 '+data.poses.filter(p=>p.check?.status==='error').length+' · 승인 '+data.poses.filter(p=>p.decision==='approved').length+' · '+(data.built_current?'빌드 최신':'선택 변경 · 빌드 필요');
 $('view-note').textContent=view==='body' ? (data.built_current?'실제 빌드 · ' + (data.config.visibility==='mask'?'지정 마스크 + zmap':'zmap 기본 · 원본 파츠') + '':'선택 후보를 참조 위에 놓은 배치 미리보기입니다. 레이어 검수는 빌드 후 진행하세요.') : view==='body-raw' ? '생성 당시 참조 위에 원본 part.png를 그대로 덮습니다. 가림 마스크와 zmap을 적용하지 않습니다. 비교용이며 현재 빌드를 바꾸지 않습니다.' : view==='body-zraw' ? '현재 선택의 원본을 같은 조각으로 분리하고 실제 빌드의 zmap 순서로 그립니다. occluded 마스크를 적용하지 않습니다. 현재 선택·빌드가 필요하며, 수치 판정과 승인은 현재 빌드 기준입니다.' : view==='visible' ? 'occluded 마스크로 알파가 지워진 파생 파츠입니다. zmap 순서와는 별개의 처리입니다.' : view==='errors' ? '덮임 누락(녹색) · 보호 침범(빨강) · 허용 영역 밖(파랑). 오류 위치만 표시합니다.' : '모든 후보는 같은 캔버스 좌표와 배율로 비교합니다.';
 $('grid').replaceChildren();
 for(const p of data.poses){
  if(filter==='error' && p.check?.status!=='error' || filter==='unverified' && p.decision==='approved') continue;
  const b=node('button','','pose'+(p.pose===selected?' active':'')); b.setAttribute('aria-label',p.pose+' 포즈');
  if(p.selected){ const img=document.createElement('img');img.alt=p.pose;img.loading='lazy';img.src=url(p.pose,view);img.onerror=()=>{img.hidden=true;b.prepend(node('span','그림을 읽지 못했습니다','muted'));}; b.append(img); }
  else b.append(node('p','그림 없음'));
  const row=node('div','','row');row.append(node('b',p.pose),node('span',statusName(p.check?.status||'unchecked'),'status '+(p.check?.status||'')));b.append(row,node('small',['requested','failed'].includes(p.rework?.status)?(p.rework.status==='failed'?'재작업 실패 · 재시도 가능':'재작업 요청됨'):p.decision==='approved'?'시각 승인됨':'시각 미승인','muted'));
  b.onclick=()=>inspect(p.pose).catch(e=>log(e.message));
  const cell=node('div','','pose-cell'),label=node('label',' 재작업 대상','muted'),check=document.createElement('input');check.type='checkbox';check.checked=state.marked.has(p.pose);check.setAttribute('aria-label','재작업 대상 '+p.pose);check.onchange=()=>{if(check.checked)state.marked.add(p.pose);else state.marked.delete(p.pose);controls();};label.prepend(check);cell.append(b,label);$('grid').append(cell);
 }
 const ref=[...data.batches].reverse().find(b=>b.reference);
 $('reference-download').hidden=!ref;
 if(ref) $('reference-download').href='/api/reference/'+encodeURIComponent(state.part)+'/'+encodeURIComponent(ref.id)+'.zip';
}
function showCandidate(c){
 state.candidate=c;
 const row=state.data.poses.find(p=>p.pose===state.pose);
 $('pose-title').textContent=state.pose;
 $('pose-status').textContent=(c.selected?'현재 선택':c.method?.programmatic_edit?'수정 후보 비교 · 미적용':'이전 후보 비교')+' · '+statusName(c.status)+' · 후보 '+c.id.slice(0,10);
 const view=$('view').value, onBody=['body','body-raw','body-zraw'].includes(view);
 const preview=view==='body'&&!c.selected?'body-raw':onBody&&(view!=='body-zraw'||c.selected&&state.data.built_current)?view:'reference';
 $('reference-image').hidden=!c.images.includes('under')&&preview==='reference';
 $('reference-image').alt=preview==='reference'?'이 후보가 생성될 때 사용한 참조':'선택한 후보를 몸 위에 놓은 비교';
 $('reference-caption').textContent=preview==='body-raw'?'원본 덮기 · 가림 없음':preview==='body-zraw'?'원본 · zmap만':preview==='body'?'실제 빌드 · '+(state.data.config.visibility==='mask'?'마스크+zmap':'zmap 기본'):'생성 당시 참조';
 if(preview!=='reference'||c.images.includes('under'))$('reference-image').src=url(state.pose,preview,preview==='body-zraw'?null:c.id);
 $('part-image').src=url(state.pose,'part',c.id);
 $('part-image').hidden=false;
 $('findings').replaceChildren(...c.findings.map(f=>node('li',f)));
 const tbody=$('criteria').querySelector('tbody');tbody.replaceChildren(...(c.criteria||[]).map(r=>{const tr=document.createElement('tr');tr.append(node('td',r.label),node('td',r.value==null?r.status:r.id==='area_jump'?(r.value*100).toFixed(1)+'% / '+r.limit*100+'%':r.value+'px / '+(r.limit==null?'참고':r.limit+'px')),node('td',statusName(r.status)));return tr;}));
 const missing=['qa_required','qa_protected','qa_allowed'].filter(n=>!c.images.includes(n));
 $('candidate-note').textContent=(missing.length?'검사 지도가 없는 후보입니다. 수치만으로 새 후보와 우열을 정하지 마세요. ':'')+(!c.selected?' 이전 후보의 프레임 전체 검사는 선택 후 전체 검사로 확인하세요. ':'')+(c.reference?'생성 기준 묶음이 고정된 후보입니다.':'이전 버전 후보: 원본과 출처는 보존됐지만 생성 전 기준 묶음은 없습니다.');
 $('evidence').textContent=JSON.stringify({placement:c.placement,method:c.method,metrics:c.metrics,source:c.source,generation_reference:c.reference},null,2);
 $('note').value=state.drafts.get(draftKey())??row.rework?.note??row.note??'';if(!state.busy&&row.rework?.status==='failed')$('job-status').textContent='재작업 실패: '+row.rework.error;
 $('candidates').querySelectorAll('button').forEach(b=>b.classList.toggle('active',b.dataset.id===c.id));
 controls();
}
async function inspect(pose){
 const revision=++state.revision, part=state.part;
 state.pose=pose;if(!state.busy)$('job-status').textContent='';grid();
 const data=await get('/api/candidates/'+encodeURIComponent(part)+'/'+encodeURIComponent(pose));
 if(revision!==state.revision || part!==state.part) return;
 state.candidates=data.candidates;$('candidates').replaceChildren();
 for(const [i,c] of data.candidates.entries()){
  const b=node('button','','candidate');b.dataset.id=c.id;b.setAttribute('aria-label','후보 '+(data.candidates.length-i)+(c.selected?' 현재 선택':''));
  const img=document.createElement('img');img.alt='독립 파츠 후보 '+(data.candidates.length-i);img.src=url(pose,'part',c.id);
  b.append(img,node('small','후보 '+(data.candidates.length-i)+(c.selected?' · 선택됨':'')),node('small',statusName(c.status),'status '+c.status));b.onclick=()=>showCandidate(c);$('candidates').append(b);
 }
 if(data.candidates.length) showCandidate(data.candidates.find(c=>c.selected)||data.candidates[0]);
 else{state.candidate=null;$('pose-title').textContent=pose+' · 그림 없음';$('pose-status').textContent='이 포즈의 참조 시트를 만들고 독립 파츠를 생성하세요.';$('part-image').removeAttribute('src');$('part-image').hidden=true;$('reference-image').removeAttribute('src');$('reference-image').hidden=true;$('findings').replaceChildren();$('evidence').textContent='';$('candidate-note').textContent='등록된 후보가 없습니다.';$('note').value=state.drafts.get(draftKey())??'';$('criteria').querySelector('tbody').replaceChildren();controls();}
}
async function loadPart(){
 state.data=await get('/api/part/'+encodeURIComponent(state.part));
 if(!state.data.poses.some(p=>p.pose===state.pose)) state.pose=state.data.poses.find(p=>p.check?.status==='error')?.pose||state.data.poses[0]?.pose;
 grid();if(state.pose) await inspect(state.pose);else controls();
}
async function loadList(){
 const data=await get('/api/status');$('part').replaceChildren(...data.productions.map(p=>{const o=node('option',p.part+' · '+p.state);o.value=p.part;return o;}));
 state.activeJob=data.active_job;state.part=state.part||data.productions[0]?.part;$('part').value=state.part;
 if(state.part) await loadPart();else $('summary').textContent='제작 기록이 없습니다.';
}
async function run(action,body={}){
 state.busy=true;controls();log(action+' 실행 중…');
 try{const result=await act(action,body);log(JSON.stringify(result,null,2));await loadList();if(result.preview_candidate){const candidate=state.candidates.find(c=>c.path===result.preview_candidate);if(candidate)showCandidate(candidate);$('job-status').textContent='원본 '+result.changed_pixels+'px 수정 후보 생성 · 이미지 생성 호출 '+result.provider_calls+'회 · 적용 대기';}else if(action==='apply'){$('job-status').textContent='후보 적용 · 검사 · 실제 빌드 완료 · 시각 검수 대기';}else if(action==='visibility'){$('job-status').textContent='겹침 처리 변경 · 검사 · 실제 빌드 완료 · 시각 검수 대기';}}
 catch(e){$('job-status').textContent=action+' 실패: '+e.message;log(action+' 실패: '+e.message);}
 finally{state.busy=false;controls();}
}
$('part').onchange=()=>{state.part=$('part').value;state.pose=null;state.marked.clear();state.revision++;loadPart().catch(e=>log(e.message));};
$('view').onchange=()=>{grid();if(state.candidate)showCandidate(state.candidate);};$('filter').onchange=grid;
$('select-candidate').onclick=()=>run('apply',{pose:state.pose,candidate:state.candidate.path});
const stageNames={requested:'요청 저장',sheet:'참조 시트 준비',draw:'독립 파츠 생성',slice:'원본 분리',check:'검사',build:'레이어 빌드',completed:'완료'};
async function followJob(job){
 state.busy=true;controls();
 try{
  while(job.state==='running'){
   $('job-status').textContent=(stageNames[job.step]||job.step)+' · 대상 '+job.poses.join(', ');
   await new Promise(resolve=>setTimeout(resolve,1500));job=await get('/api/jobs/'+job.id);
  }
  $('job-status').textContent=job.state==='completed'?'새 후보 생성·검사·빌드 완료 · 시각 검수 대기':'재작업 실패: '+job.error;
  log(JSON.stringify(job,null,2));localStorage.removeItem('parts-rework-job');await loadList();
 }catch(e){$('job-status').textContent='작업 상태 확인 실패: '+e.message;log(e.message);}
 finally{state.busy=false;controls();}
}
async function rework(poses){
 const note=$('note').value.trim();if(!note){$('job-status').textContent='수정할 내용을 구체적으로 적어 주세요. 검사 통과 포즈도 재작업할 수 있습니다.';$('note').focus();return;}
 state.busy=true;controls();
 try{const job=await act('rework',{poses,note});localStorage.setItem('parts-rework-job',job.id);await followJob(job);}
 catch(e){$('job-status').textContent='재작업 요청 실패: '+e.message;state.busy=false;controls();}
}
$('note').oninput=()=>state.drafts.set(draftKey(),$('note').value);
$('redo').onclick=()=>rework([state.pose]);
$('redo-marked').onclick=()=>rework([...state.marked]);
for(const [id,key] of [['approve','approve'],['reject','reject']]) $(id).onclick=()=>{
 const reviewer=$('reviewer').value.trim();if(!reviewer){log('검수자를 입력하세요.');$('reviewer').focus();return;}
 run('review',{reviewer,[key]:[state.pose],note:$('note').value});
};
document.querySelectorAll('[data-act]').forEach(b=>b.onclick=()=>run(b.dataset.act));
controls();loadList().then(async()=>{const id=localStorage.getItem('parts-rework-job');const job=id?await get('/api/jobs/'+id):state.activeJob;if(job){state.part=job.part;state.pose=job.poses[0];await loadList();await followJob(job);}}).catch(e=>log(e.message));
window.addEventListener('hashchange',()=>{if(!readonly)token=document.querySelector('meta[name=review-token]')?.content||new URLSearchParams(location.hash.slice(1)).get('token')||'';controls();});

$('edit-candidate').onclick=()=>{
 const type=$('edit-type').value,op={type};
 const ids=['left','top','right','bottom'],values=ids.map(id=>$('edit-'+id).value);
 if(values.some(Boolean)){if(values.some(v=>v==='')){log('영역 네 좌표를 모두 입력하세요.');return;}op.bounds=values.map(Number);}
 if(type==='translate'){op.dx=Number($('edit-dx').value);op.dy=Number($('edit-dy').value);}
 if(type==='recolor'){op.from_color=$('edit-from').value;op.to_color=$('edit-to').value;}
 run('edit',{pose:state.pose,candidate:state.candidate.path,operations:[op]});
};
$('apply-visibility').onclick=()=>run('visibility',{visibility:$('visibility').value});
