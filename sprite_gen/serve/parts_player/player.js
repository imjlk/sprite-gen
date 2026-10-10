const $ = id => document.getElementById(id);
const names = {socks:'양말',hair:'헤어',expression:'표정',upper:'상의',lower:'하의',footwear:'신발',headwear:'모자',gloves:'장갑',back_accessory:'등 장비',neckwear:'목 장식',eyewear:'안경',bangs:'앞머리',eyes:'눈',brows:'눈썹',mouth:'입',sleeve_l:'왼쪽 소매',sleeve_r:'오른쪽 소매',footwear_l:'왼쪽 신발',footwear_r:'오른쪽 신발'};
const labels = {hair:['숏 헤어','보브 헤어'],expression:['기본','미소'],upper:['틸 재킷','사파리 재킷'],lower:['반바지','긴바지'],footwear:['운동화','앵클 부츠'],bangs:['숏 앞머리','보브 앞머리'],eyes:['기본 눈','미소 눈'],brows:['기본 눈썹','편안한 눈썹'],mouth:['중립','미소'],sleeve_l:['틸 소매','사파리 소매'],sleeve_r:['틸 소매','사파리 소매'],footwear_l:['운동화','앵클 부츠'],footwear_r:['운동화','앵클 부츠']};
const optionLabels = {'illustration.socks.crew':'크루 양말','illustration.hair.bob':'보브 헤어','illustration.eyes.open':'눈','illustration.upper.cream':'크림 셔츠','illustration.lower.navy':'네이비 반바지','illustration.footwear.sneakers':'스니커즈','sd.simple.shirt':'탱크탑','sd.simple.shirt.b':'포켓 베스트','sd.simple.hair.b':'단발','sd.simple.eyes.b':'웃는 눈'};
Object.assign(optionLabels, {'sd.wardrobe.hair.a':'숏 헤어','sd.wardrobe.hair.b':'단발','sd.wardrobe.expression.a':'기본','sd.wardrobe.expression.b':'눈 미소','sd.wardrobe.upper.a':'탱크탑','sd.wardrobe.upper.b':'포켓 베스트','sd.wardrobe.lower.a':'카고 반바지','sd.wardrobe.lower.b':'긴바지','sd.wardrobe.footwear.a':'운동화','sd.wardrobe.footwear.b':'앵클 부츠'});
Object.assign(optionLabels, {'sd.equipment.headwear':'사파리 모자','sd.equipment.gloves':'가죽 장갑','sd.equipment.back_accessory':'탐험 백팩'});
const stateNames = {idle:'아이들',walk:'걷기',run:'뛰기',fit:'정적 피팅'};
let metadata, state, selected={}, playing=true, origin=performance.now(), held=0, dirty=true, revision=0, busy=false, lastSent=0, runtime=null, lastScene=null;
const layerNodes=new Map(), textureCache=new Map();
let scale=4;
let focusWindow=null, playbackSpeed=1;
const requestQuery=new URLSearchParams(location.search);
Object.assign(optionLabels, {'illustration.motion.footwear.ai-pilot':'AI 신발 · 8프레임', 'illustration.motion.footwear.sneakers':'정지 그림 변형 후보'});
const activeWindow=()=>focusWindow?.state===state?focusWindow:null;
function showWindow(){const window=activeWindow();$('window-note').hidden=!window;$('clear-window').hidden=!window;
  $('window-note').textContent=window?`검수 구간 ${window.first+1}–${window.last+1} · 공통 모션의 일부 구간 · 전체 동작 검증 전`:'';}
const number=value=>{const [a,b]=String(value).split('/').map(Number);return b===undefined?a:a/b;};
async function texture(ref){
  if(!textureCache.has(ref.id))textureCache.set(ref.id,(async()=>{
    const response=await fetch(ref.url);if(!response.ok)throw new Error('파츠 텍스처를 가져오지 못했습니다.');
    const bitmap=await createImageBitmap(await response.blob());
    const canvas=document.createElement('canvas');canvas.width=bitmap.width;canvas.height=bitmap.height;
    const ctx=canvas.getContext('2d',{willReadFrequently:true});ctx.drawImage(bitmap,0,0);
    const pixels=ctx.getImageData(0,0,bitmap.width,bitmap.height);bitmap.close();
    return {pixels,width:canvas.width,height:canvas.height};
  })());
  return textureCache.get(ref.id);
}
function coverage(asset,index,channel='luma'){
  if(index<0||index>=asset.width*asset.height)return 0;
  const d=asset.pixels.data,k=index*4;
  return channel==='alpha'?d[k+3]:Math.floor((Math.floor((d[k]*19595+d[k+1]*38470+d[k+2]*7471+32768)/65536)*d[k+3]+127)/255);
}
async function draw(scene,snapshot){
  lastScene=scene;
  const bodyOnly=$('body-only').checked;
  if(bodyOnly&&runtime)scene=runtime.scene(scene.state,Math.round(number(scene.elapsed_seconds)*1000),{}, {bodyOnly:true});
  if(bodyOnly){const layers=scene.layers.filter(layer=>layer.slot===null),ids=new Set(layers.map(layer=>layer.id));scene={...scene,layers,masks:scene.masks.filter(mask=>ids.has(mask.issuer))};}
  const refs=scene.layers.flatMap(layer=>[layer.texture,...Object.values(layer.regions)]).concat(scene.masks.map(mask=>mask.texture));
  const loaded=new Map(await Promise.all(refs.map(async ref=>[ref.id,await texture(ref)])));
  // Finish asynchronous reads before committing any DOM changes. A selection
  // or state change during those reads must not display the stale scene.
  if(snapshot!==revision){dirty=true;return false;}
  const active=new Set();
  for(const layer of scene.layers){
    active.add(layer.id);
    let canvas=layerNodes.get(layer.id);
    if(!canvas){canvas=document.createElement('canvas');canvas.className='sprite-layer';canvas.dataset.layer=layer.id;$('character').append(canvas);layerNodes.set(layer.id,canvas);}
    canvas.hidden=false;canvas.style.zIndex=String(layer.order);
    const [x,y]=layer.position.map(number);
    canvas.style.transform=`translate(${x*scale}px,${y*scale}px)`;
    const masks=scene.masks.filter(mask=>layer.provides.includes(mask.target_node));
    const signature=JSON.stringify([layer.texture.id,layer.regions,scene.palette,masks, masks.length?layer.position:[]]);
    if(canvas.dataset.signature!==signature){
      const source=loaded.get(layer.texture.id),regions=Object.entries(layer.regions).sort(([a],[b])=>a.localeCompare(b));
      const data=new Uint8ClampedArray(source.pixels.data);
      const regionAssets=regions.map(([,ref])=>loaded.get(ref.id));
      const maskAssets=masks.map(mask=>loaded.get(mask.texture.id));
      for(const asset of regionAssets)if(asset.width!==source.width||asset.height!==source.height)throw new Error('파츠 색상 영역의 크기가 맞지 않습니다.');
      masks.forEach((mask,m)=>{const issuer=scene.layers.find(item=>item.id===mask.issuer);const expected=mask.space==='part-local'?issuer.texture.size:[scene.canvas.width,scene.canvas.height];if(maskAssets[m].width!==expected[0]||maskAssets[m].height!==expected[1])throw new Error('파츠 마스크의 크기가 맞지 않습니다.');});
      for(let p=0;p<source.width*source.height;p++){
        let occupied=false;
        for(let r=0;r<regions.length;r++){
          const amount=coverage(regionAssets[r],p),target=scene.palette[regions[r][0]];
          if(occupied&&amount>0)throw new Error('파츠 색상 영역이 겹칩니다.');
          occupied=occupied||amount>0;
          for(let c=0;c<3;c++)data[p*4+c]=Math.floor((data[p*4+c]*(255-amount)+target[c]*amount+127)/255);
        }
        for(let m=0;m<masks.length;m++){
          const mask=masks[m],asset=maskAssets[m],origin=mask.origin.map(number);
          const mx=Math.floor(x+p%source.width+.5-origin[0]),my=Math.floor(y+Math.floor(p/source.width)+.5-origin[1]);
          const amount=mx<0||my<0||mx>=asset.width||my>=asset.height?0:coverage(asset,my*asset.width+mx,mask.channel);
          data[p*4+3]=Math.floor((data[p*4+3]*amount+127)/255);
        }
        if(data[p*4+3]===0)data[p*4]=data[p*4+1]=data[p*4+2]=0;
      }
      canvas.width=source.width;canvas.height=source.height;
      canvas.getContext('2d').putImageData(new ImageData(data,source.width,source.height),0,0);
      canvas.dataset.signature=signature;
    }
    canvas.style.width=`${canvas.width*scale}px`;canvas.style.height=`${canvas.height*scale}px`;
  }
  for(const [id,node] of layerNodes)node.hidden=!active.has(id);
  $('character').style.transform=scene.mirror?'scaleX(-1)':'';
  const overlay=$('landmarks'),context=overlay.getContext('2d');
  overlay.width=scene.canvas.width;overlay.height=scene.canvas.height;
  overlay.style.width=`${scene.canvas.width*scale}px`;overlay.style.height=`${scene.canvas.height*scale}px`;
  if($('sockets').checked){
    context.fillStyle='#34745e';
    for(const layer of scene.layers){const [x,y]=layer.anchor.map(number);context.fillRect(x-1,y-1,3,3);}
  }
  $('layer-count').textContent=`${scene.layers.length} layers`;
  const current=metadata.states.find(s=>s.id===state);
  $('frame-position').textContent=`${scene.sample_index+1} / ${current.samples}`;
  $('sample-name').textContent=`${scene.clip_id} · ${scene.sample_id}`;
  const window=activeWindow(),offset=window?.start||0;
  const position=window?elapsed()-offset:current.loop?elapsed()%duration():Math.min(elapsed(),duration());
  $('seek').value=String(Math.floor(position/duration()*999));
  const fit=scene.fit_summary||{status:'unverified',checked:0,failed:0};
  $('fit-status').dataset.status=bodyOnly?'unverified':fit.status;
  const errors=(scene.fits||[]).filter(row=>row.status==='error');
  const terms={coverage:'피복',silhouette:'윤곽',occlusion:'가림',position:'위치',size:'크기',gap:'빈틈',clipping:'잘림','missing-interface':'검사 기준'};
  $('fit-status').textContent=bodyOnly?'몸만 표시 · 파츠 선택과 재생 시점 유지':errors.length?
    errors.map(row=>`${names[row.slot]||row.slot}: ${row.checks.filter(check=>check.status==='error').map(check=>terms[check.id]||check.id).join('·')}`).join(' / '):
    fit.status==='passed'?`현재 포즈 ${fit.checked}파츠 기하 검사 통과 · 승인 결과는 파츠 검수에서 확인`:'현재 조합 검사 기준 미등록 · 파츠 승인 결과는 제작 검수에서 확인';
  const sourceRows=(runtime?.data.stability?.results||[]).filter(row=>row.clip===scene.clip_id&&row.sample===scene.sample_id);
  const sourceErrors=sourceRows.filter(row=>row.status==='error');
  $('source-status').dataset.status=sourceErrors.length?'error':sourceRows.length?'passed':'unverified';
  $('source-status').textContent=sourceRows.length?sourceRows.map(row=>`${row.label}: 윤곽 ${row.alpha_pixels??'—'}px · 색 변화 ${row.rgb_pixels??'—'}px${row.status==='error'?' · 기준 초과':''}`).join(' / '):'원본 안정 영역 검사 기준 미등록';
  return true;
}
const elapsed=()=>{const raw=held+(playing?(performance.now()-origin)*playbackSpeed:0),window=activeWindow();
  return window?window.start+((raw-window.start)%(window.end-window.start)+(window.end-window.start))%(window.end-window.start):raw;};
const duration=()=>{const window=activeWindow();return window?window.end-window.start:number(metadata.states.find(s=>s.id===state).duration_ms);};
function invalidate(){revision++;dirty=true;}
function showSelection(){
  document.querySelectorAll('[data-slot]').forEach(b=>b.setAttribute('aria-pressed',String(selected[b.dataset.slot]===b.dataset.option)));
}
async function render(now){
  if(metadata&&!busy&&(dirty||(playing&&(runtime||now-lastSent>33)))){
    busy=true;dirty=false;lastSent=now;
    const snapshot=revision;
    const params=new URLSearchParams({state,time_ms:String(Math.floor(elapsed())),...selected});
    try{
      let frame;
      if(runtime)frame=runtime.scene(state,Math.floor(elapsed()),selected);
      else{const response=await fetch(`/api/layers?${params}`);frame=await response.json();if(!response.ok)throw new Error(frame.error||'미리보기를 가져오지 못했습니다.');}
      if(snapshot===revision){
        if(await draw(frame,snapshot))$('error').textContent='';
      }else dirty=true;
    }catch(error){$('error').textContent=error.message;playing=false;}
    finally{busy=false;}
  }
  requestAnimationFrame(render);
}
function setPlaying(value){held=elapsed();origin=performance.now();playing=value;$('play').textContent=playing?'Ⅱ':'▶';$('play').setAttribute('aria-label',playing?'일시정지':'재생');invalidate();}
$('play').addEventListener('click',()=>setPlaying(!playing));
$('seek').addEventListener('input',()=>{setPlaying(false);const window=activeWindow();held=(window?.start||0)+Number($('seek').value)/999*(window?duration()-1:duration());invalidate();});
$('speed').addEventListener('change',()=>{held=elapsed();origin=performance.now();playbackSpeed=Number($('speed').value);invalidate();});
$('clear-window').addEventListener('click',()=>{held=elapsed();origin=performance.now();focusWindow=null;showWindow();invalidate();});
$('sockets').addEventListener('change',invalidate);
$('body-only').addEventListener('change',invalidate);
function step(direction){
  setPlaying(false);
  const clip=metadata.states.find(item=>item.id===state),current=lastScene?.sample_index||0;
  const window=activeWindow(),first=window?.first||0,last=window?.last??clip.samples-1,count=last-first+1;
  const index=window||clip.loop?first+((current-first+direction)%count+count)%count:Math.max(first,Math.min(last,current+direction));
  held=Math.ceil(number(clip.boundaries_ms[index]));invalidate();
}
$('previous').addEventListener('click',()=>step(-1));$('next').addEventListener('click',()=>step(1));
$('find-issue').addEventListener('click',()=>{
  if(!runtime)return;
  const issue=Object.values(runtime.data.fitting).find(row=>row.status==='error');if(!issue)return;
  setPlaying(false);focusWindow=null;state=issue.state;showWindow();selected[issue.slot]=issue.part.id;showSelection();
  document.querySelectorAll('[data-state]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.state===state)));
  const clip=runtime.data.clips.find(item=>item.id===issue.clip),index=clip.samples.findIndex(sample=>sample.id===issue.sample);
  held=Math.ceil(number(metadata.states.find(item=>item.id===state).boundaries_ms[index]));invalidate();
});
$('find-source-issue').addEventListener('click',()=>{
  const issue=(runtime?.data.stability?.results||[]).find(row=>row.status==='error');if(!issue)return;
  setPlaying(false);focusWindow=null;state=issue.state;showWindow();$('body-only').checked=true;
  document.querySelectorAll('[data-state]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.state===state)));
  const clip=runtime.data.clips.find(item=>item.id===issue.clip),index=clip.samples.findIndex(sample=>sample.id===issue.sample);
  held=Math.ceil(number(metadata.states.find(item=>item.id===state).boundaries_ms[index]));invalidate();
});
$('shuffle').addEventListener('click',()=>{for(const slot of metadata.slots){selected[slot.id]=slot.options[Math.floor(Math.random()*slot.options.length)].id;}showSelection();$('selection-status').textContent=metadata.states.every(item=>item.samples===1)?'정적 파츠 피팅 · 동작 검증 전':'파츠 교체 · 재생 시점 유지';invalidate();});
async function boot(){
  try{
    const embedded=document.getElementById('parts-data');
    if(embedded){runtime=new LayerRuntime(JSON.parse(embedded.textContent));$('export').hidden=true;$('standalone').hidden=true;}
    else{
      const library=await fetch('/api/library');
      if(library.ok)runtime=new LayerRuntime(await library.json());
    }
    if(runtime){metadata=runtime.metadata;$('playback-mode').textContent='브라우저 직접 재생';
      const summary=metadata.fit_summary;$('library-status').textContent=summary.status==='unverified'?'코디 검사 기준 미등록':`전체 파츠 ${summary.checked}건 · 오류 ${summary.failed}건`;
      $('find-issue').disabled=summary.failed===0;
      $('find-source-issue').disabled=!runtime.data.stability?.failed;
    }else{
      const response=await fetch('/api/metadata');if(!response.ok)throw new Error('파츠 정보를 가져오지 못했습니다.');metadata=await response.json();
      $('export').hidden=true;$('standalone').hidden=true;$('playback-mode').textContent='서버 레이어 재생';
    }
    state=metadata.states.some(item=>item.id===requestQuery.get('state'))?requestQuery.get('state'):metadata.states[0].id;
    if(runtime&&(requestQuery.has('from')||requestQuery.has('to'))){focusWindow=runtime.sampleWindow(state,Number(requestQuery.get('from')),Number(requestQuery.get('to')));held=focusWindow.start;}
    const speed=requestQuery.get('speed');if(['0.25','0.5','1','2'].includes(speed)){playbackSpeed=Number(speed);$('speed').value=speed;}
    showWindow();$('brand').textContent=metadata.title||metadata.id;document.title=`${metadata.title||metadata.id} · Parts Lab`;
    $('standalone').href='/standalone'+location.search;
    document.documentElement.dataset.sampling=metadata.sampling||'nearest';
    if(metadata.states.every(item=>item.samples===1)){document.querySelector('.aside-note').textContent='기준 포즈에서 파츠별 착용 상태를 비교합니다.';playing=false;$('play').textContent='▶';$('play').setAttribute('aria-label','재생');$('selection-status').textContent='정적 파츠 피팅 · 동작 검증 전';}
    $('shared-states').textContent=`공통 ${metadata.states.map(item=>stateNames[item.id]||item.id).join(' / ')}`;
    document.querySelector('.stage-caption span').textContent=`${metadata.canvas.width} × ${metadata.canvas.height}`;
    const stage=document.querySelector('.stage');
    new ResizeObserver(()=>{const fit=Math.min(stage.clientWidth/metadata.canvas.width,stage.clientHeight/metadata.canvas.height);scale=metadata.sampling==='smooth'?Math.max(.01,fit):Math.max(1,Math.floor(fit));$('character').style.width=`${metadata.canvas.width*scale}px`;$('character').style.height=`${metadata.canvas.height*scale}px`;invalidate();}).observe(stage);
    for(const slot of metadata.slots){
      selected[slot.id]=slot.selected;
      const section=document.createElement('section');section.className='slot';
      const title=document.createElement('h2');title.textContent=/[가-힣]/.test(slot.label)?slot.label:names[slot.id]||slot.label;section.append(title);
      const choices=document.createElement('div');choices.className='choices';
      slot.options.forEach(option=>{
        const button=document.createElement('button');button.type='button';button.dataset.slot=slot.id;button.dataset.option=option.id;
        const index=slot.options.filter(o=>o.id!=='').findIndex(o=>o.id===option.id);
        button.textContent=slot.options.length===2&&slot.options.some(o=>o.id==='')?(option.id?'켜기':'끄기'):option.id===''?option.label:optionLabels[option.id]||option.label;
        button.addEventListener('click',()=>{selected[slot.id]=option.id;showSelection();$('selection-status').textContent=metadata.states.every(item=>item.samples===1)?'정적 파츠 피팅 · 동작 검증 전':'파츠 교체 · 재생 시점 유지';invalidate();});choices.append(button);
      });section.append(choices);(['hair','expression','eyes','upper','lower','footwear','socks'].includes(slot.id)?$('wardrobe'):$('extras')).append(section);
    }
    $('advanced').hidden=$('extras').children.length===0;
    for(const item of metadata.states){
      const button=document.createElement('button');button.type='button';button.textContent=stateNames[item.id]||item.id;button.dataset.state=item.id;
      button.setAttribute('aria-pressed',String(item.id===state));
      button.addEventListener('click',()=>{state=item.id;held=activeWindow()?.start||0;origin=performance.now();showWindow();document.querySelectorAll('[data-state]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.state===state)));invalidate();});$('states').append(button);
    }
    showSelection();
    if(runtime){$('sample-name').textContent='파츠 이미지 준비 중…';await Promise.all(Object.values(runtime.textures).map(texture));}
    origin=performance.now();requestAnimationFrame(render);
  }catch(error){$('error').textContent=error.message;}
}
boot();
