const $ = id => document.getElementById(id);
const names = {hair:'헤어',expression:'표정',upper:'상의',lower:'하의',footwear:'신발',headwear:'모자',gloves:'장갑',back_accessory:'등 장비',neckwear:'목 장식',eyewear:'안경',bangs:'앞머리',eyes:'눈',brows:'눈썹',mouth:'입',sleeve_l:'왼쪽 소매',sleeve_r:'오른쪽 소매',footwear_l:'왼쪽 신발',footwear_r:'오른쪽 신발'};
const labels = {hair:['숏 헤어','보브 헤어'],expression:['기본','미소'],upper:['틸 재킷','사파리 재킷'],lower:['반바지','긴바지'],footwear:['운동화','앵클 부츠'],bangs:['숏 앞머리','보브 앞머리'],eyes:['기본 눈','미소 눈'],brows:['기본 눈썹','편안한 눈썹'],mouth:['중립','미소'],sleeve_l:['틸 소매','사파리 소매'],sleeve_r:['틸 소매','사파리 소매'],footwear_l:['운동화','앵클 부츠'],footwear_r:['운동화','앵클 부츠']};
const stateNames = {idle:'아이들',walk:'걷기',run:'뛰기'};
let metadata, state, selected={}, playing=true, origin=performance.now(), held=0, dirty=true, revision=0, busy=false, lastSent=0;
const context=$('character').getContext('2d');
context.imageSmoothingEnabled=false;
const elapsed=()=>held+(playing?performance.now()-origin:0);
const duration=()=>Number(metadata.states.find(s=>s.id===state).duration_ms.split('/').reduce((a,b)=>Number(a)/Number(b)));
function invalidate(){revision++;dirty=true;}
function showSelection(){
  document.querySelectorAll('[data-slot]').forEach(b=>b.setAttribute('aria-pressed',String(selected[b.dataset.slot]===b.dataset.option)));
}
function draw(bitmap,frame){
  context.clearRect(0,0,metadata.canvas.width,metadata.canvas.height);context.drawImage(bitmap,0,0);
  if($('sockets').checked){
    context.fillStyle='#34745e';
    for(const socket of Object.values(frame.evidence.sockets)){
      if(socket.visible){const [x,y]=socket.position;context.fillRect(x-1,y-1,3,3);}
    }
  }
  $('layer-count').textContent=`${frame.evidence.draw.length} layers`;
  const current=metadata.states.find(s=>s.id===state);
  $('frame-position').textContent=`${frame.sample_index+1} / ${current.samples}`;
  $('sample-name').textContent=`${frame.clip_id} · ${frame.sample_id}`;
  $('seek').value=String(Math.floor((elapsed()%duration())/duration()*999));
}
async function render(now){
  if(metadata&&!busy&&(dirty||(playing&&now-lastSent>50))){
    busy=true;dirty=false;lastSent=now;
    const snapshot=revision;
    const params=new URLSearchParams({state,time_ms:String(Math.floor(elapsed())),...selected});
    try{
      const response=await fetch(`/api/frame?${params}`);
      const frame=await response.json();
      if(!response.ok)throw new Error(frame.error||'미리보기를 가져오지 못했습니다.');
      const blob=await (await fetch(frame.image)).blob();
      const bitmap=await createImageBitmap(blob);
      if(snapshot===revision){
        draw(bitmap,frame);$('error').textContent='';
      }else dirty=true;
      bitmap.close();
    }catch(error){$('error').textContent=error.message;playing=false;}
    finally{busy=false;}
  }
  requestAnimationFrame(render);
}
function setPlaying(value){held=elapsed();origin=performance.now();playing=value;$('play').textContent=playing?'Ⅱ':'▶';$('play').setAttribute('aria-label',playing?'일시정지':'재생');invalidate();}
$('play').addEventListener('click',()=>setPlaying(!playing));
$('seek').addEventListener('input',()=>{setPlaying(false);held=Number($('seek').value)/999*duration();invalidate();});
$('sockets').addEventListener('change',invalidate);
$('shuffle').addEventListener('click',()=>{for(const slot of metadata.slots){selected[slot.id]=slot.options[Math.floor(Math.random()*slot.options.length)].id;}showSelection();$('selection-status').textContent='파츠 교체 · 재생 시점 유지';invalidate();});
async function boot(){
  try{
    const response=await fetch('/api/metadata');if(!response.ok)throw new Error('파츠 정보를 가져오지 못했습니다.');
    metadata=await response.json();state=metadata.states[0].id;
    $('character').width=metadata.canvas.width;$('character').height=metadata.canvas.height;
    document.querySelector('.stage-caption span').textContent=`${metadata.canvas.width} × ${metadata.canvas.height}`;
    const stage=document.querySelector('.stage');
    new ResizeObserver(()=>{const scale=Math.max(1,Math.floor(Math.min(stage.clientWidth/metadata.canvas.width,stage.clientHeight/metadata.canvas.height)));$('character').style.width=`${metadata.canvas.width*scale}px`;$('character').style.height=`${metadata.canvas.height*scale}px`;}).observe(stage);
    for(const slot of metadata.slots){
      selected[slot.id]=slot.selected;
      const section=document.createElement('section');section.className='slot';
      const title=document.createElement('h2');title.textContent=names[slot.id]||slot.label;section.append(title);
      const choices=document.createElement('div');choices.className='choices';
      slot.options.forEach(option=>{
        const button=document.createElement('button');button.type='button';button.dataset.slot=slot.id;button.dataset.option=option.id;
        const index=slot.options.filter(o=>o.id!=='').findIndex(o=>o.id===option.id);
        button.textContent=option.id===''?option.label:labels[slot.id]?.[index]||({headwear:'탐험 모자',gloves:'핑거리스',back_accessory:'백팩',neckwear:'스카프',eyewear:'라운드 안경'}[slot.id]||option.label);
        button.addEventListener('click',()=>{selected[slot.id]=option.id;showSelection();$('selection-status').textContent='파츠 교체 · 재생 시점 유지';invalidate();});choices.append(button);
      });section.append(choices);(slot.id in labels&&['hair','expression','upper','lower','footwear'].includes(slot.id)?$('wardrobe'):$('extras')).append(section);
    }
    for(const item of metadata.states){
      const button=document.createElement('button');button.type='button';button.textContent=stateNames[item.id]||item.id;button.dataset.state=item.id;
      button.setAttribute('aria-pressed',String(item.id===state));
      button.addEventListener('click',()=>{state=item.id;held=0;origin=performance.now();document.querySelectorAll('[data-state]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.state===state)));invalidate();});$('states').append(button);
    }
    showSelection();origin=performance.now();requestAnimationFrame(render);
  }catch(error){$('error').textContent=error.message;}
}
boot();
