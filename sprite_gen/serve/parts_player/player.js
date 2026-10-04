const $ = id => document.getElementById(id);
const names = {hair:'헤어',expression:'표정',upper:'상의',lower:'하의',footwear:'신발',headwear:'모자',gloves:'장갑',back_accessory:'등 장비',neckwear:'목 장식',eyewear:'안경',bangs:'앞머리',eyes:'눈',brows:'눈썹',mouth:'입',sleeve_l:'왼쪽 소매',sleeve_r:'오른쪽 소매',footwear_l:'왼쪽 신발',footwear_r:'오른쪽 신발'};
const labels = {hair:['숏 헤어','보브 헤어'],expression:['기본','미소'],upper:['틸 재킷','사파리 재킷'],lower:['반바지','긴바지'],footwear:['운동화','앵클 부츠'],bangs:['숏 앞머리','보브 앞머리'],eyes:['기본 눈','미소 눈'],brows:['기본 눈썹','편안한 눈썹'],mouth:['중립','미소'],sleeve_l:['틸 소매','사파리 소매'],sleeve_r:['틸 소매','사파리 소매'],footwear_l:['운동화','앵클 부츠'],footwear_r:['운동화','앵클 부츠']};
const optionLabels = {'sd.simple.shirt':'탱크탑','sd.simple.shirt.b':'포켓 베스트','sd.simple.hair.b':'단발','sd.simple.eyes.b':'웃는 눈'};
const stateNames = {idle:'아이들',walk:'걷기',run:'뛰기'};
let metadata, state, selected={}, playing=true, origin=performance.now(), held=0, dirty=true, revision=0, busy=false, lastSent=0;
const layerNodes=new Map(), textureCache=new Map();
let scale=4;
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
  $('seek').value=String(Math.floor((elapsed()%duration())/duration()*999));
  return true;
}
const elapsed=()=>held+(playing?performance.now()-origin:0);
const duration=()=>Number(metadata.states.find(s=>s.id===state).duration_ms.split('/').reduce((a,b)=>Number(a)/Number(b)));
function invalidate(){revision++;dirty=true;}
function showSelection(){
  document.querySelectorAll('[data-slot]').forEach(b=>b.setAttribute('aria-pressed',String(selected[b.dataset.slot]===b.dataset.option)));
}
async function render(now){
  if(metadata&&!busy&&(dirty||(playing&&now-lastSent>33))){
    busy=true;dirty=false;lastSent=now;
    const snapshot=revision;
    const params=new URLSearchParams({state,time_ms:String(Math.floor(elapsed())),...selected});
    try{
      const response=await fetch(`/api/layers?${params}`);
      const frame=await response.json();
      if(!response.ok)throw new Error(frame.error||'미리보기를 가져오지 못했습니다.');
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
$('seek').addEventListener('input',()=>{setPlaying(false);held=Number($('seek').value)/999*duration();invalidate();});
$('sockets').addEventListener('change',invalidate);
$('shuffle').addEventListener('click',()=>{for(const slot of metadata.slots){selected[slot.id]=slot.options[Math.floor(Math.random()*slot.options.length)].id;}showSelection();$('selection-status').textContent='파츠 교체 · 재생 시점 유지';invalidate();});
async function boot(){
  try{
    const response=await fetch('/api/metadata');if(!response.ok)throw new Error('파츠 정보를 가져오지 못했습니다.');
    metadata=await response.json();state=metadata.states[0].id;
    document.querySelector('.stage-caption span').textContent=`${metadata.canvas.width} × ${metadata.canvas.height}`;
    const stage=document.querySelector('.stage');
    new ResizeObserver(()=>{scale=Math.max(1,Math.floor(Math.min(stage.clientWidth/metadata.canvas.width,stage.clientHeight/metadata.canvas.height)));$('character').style.width=`${metadata.canvas.width*scale}px`;$('character').style.height=`${metadata.canvas.height*scale}px`;invalidate();}).observe(stage);
    for(const slot of metadata.slots){
      selected[slot.id]=slot.selected;
      const section=document.createElement('section');section.className='slot';
      const title=document.createElement('h2');title.textContent=/[가-힣]/.test(slot.label)?slot.label:names[slot.id]||slot.label;section.append(title);
      const choices=document.createElement('div');choices.className='choices';
      slot.options.forEach(option=>{
        const button=document.createElement('button');button.type='button';button.dataset.slot=slot.id;button.dataset.option=option.id;
        const index=slot.options.filter(o=>o.id!=='').findIndex(o=>o.id===option.id);
        button.textContent=slot.options.length===2&&slot.options.some(o=>o.id==='')?(option.id?'켜기':'끄기'):option.id===''?option.label:optionLabels[option.id]||labels[slot.id]?.[index]||({headwear:'탐험 모자',gloves:'핑거리스',back_accessory:'백팩',neckwear:'스카프',eyewear:'라운드 안경'}[slot.id]||option.label);
        button.addEventListener('click',()=>{selected[slot.id]=option.id;showSelection();$('selection-status').textContent='파츠 교체 · 재생 시점 유지';invalidate();});choices.append(button);
      });section.append(choices);(['hair','expression','eyes','upper','lower','footwear'].includes(slot.id)?$('wardrobe'):$('extras')).append(section);
    }
    $('advanced').hidden=$('extras').children.length===0;
    for(const item of metadata.states){
      const button=document.createElement('button');button.type='button';button.textContent=stateNames[item.id]||item.id;button.dataset.state=item.id;
      button.setAttribute('aria-pressed',String(item.id===state));
      button.addEventListener('click',()=>{state=item.id;held=0;origin=performance.now();document.querySelectorAll('[data-state]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.state===state)));invalidate();});$('states').append(button);
    }
    showSelection();origin=performance.now();requestAnimationFrame(render);
  }catch(error){$('error').textContent=error.message;}
}
boot();
