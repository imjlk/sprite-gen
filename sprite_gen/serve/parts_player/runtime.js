/* Independent overlay sampler. Texture pixels are never warped or baked per outfit. */
class LayerRuntime {
  constructor(bundle) {
    this.data = bundle.manifest;
    if (this.data.kind !== 'parts-layer-library' || this.data.schema_version !== 1) throw new Error('지원하지 않는 파츠 패키지입니다.');
    this.textures = Object.fromEntries(Object.entries(this.data.textures).map(([id,ref]) => [id,{...ref,url:bundle.textures[id]}]));
    this.metadata = {id:this.data.appearance.id,title:this.data.title,canvas:this.data.canvas,slots:this.data.slots,
      sampling:this.data.sampling||'nearest',
      interpolation:this.data.interpolation,fit_summary:this.data.fit_summary,mode:'independent-layers',release_ready:false,
      states:this.data.clips.map(clip=>({id:clip.state,samples:clip.samples.length,loop:clip.loop,
        duration_ms:clip.samples.reduce((n,s)=>n+s.duration_ticks,0)*1000/clip.timebase,
        boundaries_ms:clip.samples.map((_,i)=>clip.samples.slice(0,i).reduce((n,s)=>n+s.duration_ticks,0)*1000/clip.timebase)}))};
  }
  fraction(n,d=1n) {
    const gcd=(a,b)=>b===0n?a:gcd(b,a%b), g=gcd(n<0n?-n:n,d);
    n/=g;d/=g;return d===1n?String(n):`${n}/${d}`;
  }
  scene(state,timeMs,selections={},options={}) {
    if(!Number.isSafeInteger(timeMs)||timeMs<0)throw new Error('재생 시간이 올바르지 않습니다.');
    const data=this.data,clip=data.clips.find(c=>c.state===state);
    if(!clip)throw new Error('지원하지 않는 동작입니다.');
    const chosen={...data.defaults,...selections}, ids=new Set(data.body), occupied=new Map();
    for(const slot of options.bodyOnly?[]:data.slots){
      const id=chosen[slot.id];
      if(!slot.options.some(option=>option.id===id))throw new Error(`선택할 수 없는 파츠: ${slot.label}`);
      if(id){ids.add(id);for(const name of data.packs[id].occupies){if(occupied.has(name)&&occupied.get(name)!==id)throw new Error('함께 착용할 수 없는 파츠입니다.');occupied.set(name,id);}}
    }
    for(const id of ids){const pack=data.packs[id];if(pack.requires.some(s=>!occupied.has(s))||pack.excludes.some(s=>occupied.has(s)))throw new Error('필요한 파츠 또는 착용 조건이 맞지 않습니다.');}
    const total=clip.samples.reduce((n,s)=>n+BigInt(s.duration_ticks),0n), elapsed=BigInt(timeMs)*BigInt(clip.timebase);
    const position=clip.loop?elapsed%(total*1000n):(elapsed<(total-1n)*1000n?elapsed:(total-1n)*1000n);
    let start=0n,index=0;
    while(index<clip.samples.length-1&&position>=start+BigInt(clip.samples[index].duration_ticks)*1000n){start+=BigInt(clip.samples[index].duration_ticks)*1000n;index++;}
    const sample=clip.samples[index],next=clip.samples[(index+1)%clip.samples.length]||sample;
    const following=clip.loop?next:clip.samples[Math.min(index+1,clip.samples.length-1)];
    const denominator=BigInt(sample.duration_ticks)*1000n, numerator=data.interpolation==='linear'?position-start:0n;
    const known=[],active=[],hidden=[],covered=[],tags=new Set();
    for(const id of ids)for(const tag of data.packs[id].covers||[])tags.add(tag);
    // Node replacement is declared by a selected design, before pose visibility,
    // matching parts_resolve. Never erase only part of an indivisible body raster.
    const eligible=[];
    for(const id of [...ids].sort())for(const piece of data.packs[id].pieces){
      const rule=piece.cover_rule;
      if(!rule||tags.has(rule.tag)===(rule.show==='covered'))eligible.push({pack:data.packs[id],piece});
    }
    const targetsToReplace=new Set(eligible.filter(e=>e.pack.role!=='body').flatMap(e=>e.piece.replaces||[]));
    const bodyNodes=new Set(eligible.filter(e=>e.pack.role==='body').flatMap(e=>e.piece.provides));
    for(const target of targetsToReplace)if(!bodyNodes.has(target))throw new Error('교체할 바디 영역이 없습니다.');
    if(targetsToReplace.size&&data.body.some(id=>data.packs[id].body_mode==='flat'))throw new Error('바디 영역 교체는 분리된 바디가 필요합니다.');
    const replaced=new Set(),providers=new Map();
    for(const e of eligible){
      const affected=e.pack.role==='body'?e.piece.provides.filter(n=>targetsToReplace.has(n)):[];
      if(affected.length){
        if(affected.length!==e.piece.provides.length)throw new Error('분리되지 않은 바디의 일부만 숨길 수 없습니다.');
        replaced.add(`${e.pack.id}.${e.piece.id}`);continue;
      }
      for(const node of e.piece.provides){const previous=providers.get(node);if(previous&&previous!==e.pack.id)throw new Error('바디 영역을 제공하는 파츠가 겹칩니다.');providers.set(node,e.pack.id);}
    }
    if(!data.body.some(id=>data.packs[id].body_mode==='flat'))for(const node of data.required_nodes||[])if(!providers.has(node))throw new Error('필수 바디 영역이 누락됐습니다.');
    for(const id of [...ids].sort())for(const piece of data.packs[id].pieces){
      const placement=piece.sample_map[clip.id][sample.id],socket=sample.sockets[placement.target_socket];
      const entry={id:`${id}.${piece.id}`,pack:data.packs[id],piece,placement};known.push(entry);
      if(replaced.has(entry.id))continue;
      const rule=piece.cover_rule;
      if(rule&&tags.has(rule.tag)!==(rule.show==='covered')){covered.push(entry.id);continue;}
      if(!placement.visible||socket&&!socket.visible){hidden.push(entry.id);continue;}
      if(!socket||socket.confidence!=='verified')throw new Error('파츠 기준점이 검수되지 않았습니다.');
      active.push({...entry,socket});
    }
    const edges=new Map(active.map(e=>[e.id,new Set()]));
    const targets=token=>{
      if(data.nodes.includes(token))return active.filter(e=>e.piece.provides.includes(token)).map(e=>e.id);
      const matches=known.filter(e=>e.id===token||e.piece.id===token);
      if(matches.length!==1)throw new Error('파츠 그리기 순서가 모호합니다.');
      return edges.has(matches[0].id)?[matches[0].id]:[];
    };
    for(const e of active){for(const token of e.placement.before)for(const id of targets(token))if(id!==e.id)edges.get(e.id).add(id);for(const token of e.placement.after)for(const id of targets(token))if(id!==e.id)edges.get(id).add(e.id);}
    const counts=new Map(active.map(e=>[e.id,0]));for(const values of edges.values())for(const id of values)counts.set(id,counts.get(id)+1);
    // Explicit constraints first; among the drawable, the lowest draw-table rank, the body before
    // what is worn on the same layer, then the ID (parts_resolve._order).
    const table=data.layers||[],rank=e=>{const at=table.indexOf(e.piece.node);if(at>=0)return at;
      const found=e.piece.provides.map(n=>table.indexOf(n)).filter(n=>n>=0);return found.length?Math.min(...found):table.length;};
    const ranks=new Map(active.map(e=>[e.id,[rank(e),table.length&&e.pack.role!=='body'?1:0]]));
    const byKey=(a,b)=>ranks.get(a)[0]-ranks.get(b)[0]||ranks.get(a)[1]-ranks.get(b)[1]||(a<b?-1:a>b?1:0);
    const ready=[...counts].filter(([,n])=>n===0).map(([id])=>id).sort(byKey),ordered=[];
    while(ready.length){const id=ready.shift();ordered.push(active.find(e=>e.id===id));for(const target of edges.get(id)){counts.set(target,counts.get(target)-1);if(counts.get(target)===0){ready.push(target);ready.sort(byKey);}}}
    if(ordered.length!==active.length)throw new Error('파츠 그리기 순서에 순환이 있습니다.');
    const names=new Map();for(const e of ordered){const id=`${e.pack.role==='body'?'body':e.piece.slot}.${e.piece.id}`;names.set(id,(names.get(id)||0)+1);}
    const masks=[],fits=[];
    const layers=ordered.map((e,order)=>{
      const p=e.placement,later=e.piece.sample_map[clip.id][following.id],nextSocket=following.sockets[later.target_socket];
      const anchor=e.socket.position.map(BigInt),local=p.offset.map((v,i)=>BigInt(v-p.pivot[i]));
      let tweened=false;
      if(later.visible&&nextSocket?.visible&&later.target_socket===p.target_socket&&numerator>0n){
        for(let i=0;i<2;i++){anchor[i]=anchor[i]*denominator+(BigInt(nextSocket.position[i])-anchor[i])*numerator;
          local[i]=local[i]*denominator+(JSON.stringify(later.image_key)===JSON.stringify(p.image_key)?BigInt(later.offset[i]-later.pivot[i]-p.offset[i]+p.pivot[i])*numerator:0n);}
        tweened=true;
      }else{for(let i=0;i<2;i++){anchor[i]*=denominator;local[i]*=denominator;}}
      const position=anchor.map((v,i)=>this.fraction(v+local[i],denominator));
      const stable=`${e.pack.role==='body'?'body':e.piece.slot}.${e.piece.id}`,id=names.get(stable)===1?stable:e.id;
      if(p.mask)masks.push({...p.mask,texture:this.textures[p.mask.texture],issuer:id,
        origin:p.mask.space==='part-local'?position:['0','0']});
      if(e.pack.role!=='body'){
        const fit=data.fitting[[e.pack.id,e.piece.id,clip.id,sample.id].join('|')];if(fit)fits.push(fit);
      }
      return {id,render_id:e.id,order,slot:e.piece.slot,node:e.piece.node,provides:e.piece.provides,mode:e.piece.mode,
        pack:e.pack.reference,image:{file:this.textures[p.texture].file,sha256:p.texture},texture:this.textures[p.texture],
        anchor:anchor.map(v=>this.fraction(v,denominator)),local_origin:local.map(v=>this.fraction(v,denominator)),position,tweened,
        attach_to:p.target_socket,allow_clip:p.allow_clip,regions:Object.fromEntries(Object.entries({...e.piece.palette_regions,...p.palette_regions}).map(([name,id])=>[name,this.textures[id]]))};
    });
    return {state,clip_id:clip.id,sample_id:sample.id,sample_index:index,sample_tick:this.fraction(position-start,1000n),
      clip_tick:this.fraction(position,1000n),elapsed_seconds:this.fraction(BigInt(timeMs),1000n),timebase:clip.timebase,loop:clip.loop,
      motion:data.motion,canvas:data.canvas,palette:data.palette,mirror:data.mirror,layers,masks,hidden,covered:covered.sort(),replaced:[...replaced].sort(),
      sample_fraction:this.fraction(numerator,denominator),next_sample_id:following.id,
      fit_summary:{status:fits.some(f=>f.status==='error')?'error':Object.keys(data.fitting).length?'passed':'unverified',
        checked:fits.length,failed:fits.filter(f=>f.status==='error').length,art_approved:false},fits,release_ready:false};
  }
}
if(typeof module!=='undefined')module.exports={LayerRuntime};
