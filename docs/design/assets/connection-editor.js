// Inserted inside the preview's existing closure; the shared fixture supplies exact records.
const demo=JSON.parse(root.querySelector('#fr-connection-data').textContent);
for(const airport of demo.airports){places[airport.iata]={name:names[airport.iata]||airport.name.replace(' Airport Schiphol','').replace('London Gatwick Airport','London'),full:airport.name,coord:[airport.longitude,airport.latitude],zone:airport.timezone,city:airport.city_id};}
const extraFlights=demo.flights.map(f=>({id:f.provider_id,number:f.flight_number,from:f.origin_iata,to:f.destination_iata,out:(Date.parse(f.departure_scheduled)-base)/3600000,inAt:(Date.parse(f.arrival_scheduled)-base)/3600000,day:17}));
const flightPool=[...flights,...extraFlights.filter(f=>!flights.some(old=>old.id===f.id))];
const editorDefault={index:1,mode:'soon',after:'2026-10-19T02:00',airport:'',allowChange:false,undo:null,notice:'',error:'',updated:[]};
state.editor={...editorDefault,...state.editor};
function connection(previous,flight,window={}){
 const same=previous.to===flight.from,transfer=demo.transfers.find(t=>t.arrival_iata===previous.to&&t.departure_iata===flight.from);
 if(!same&&(!state.editor.allowChange||!transfer||places[previous.to]?.city!==places[flight.from]?.city))return null;
 const minimum=same?.75:Math.max(.75,transfer.minimum_minutes/60),wait=flight.out-previous.inAt;
 if(wait<minimum||(window.max!==undefined&&wait>window.max)||(window.after!==undefined&&flight.out<window.after)||(window.airport&&flight.from!==window.airport))return null;
 return {from:previous.to,to:flight.from,wait,minimum,change:!same};
}
function toHour(value,zone){
 const parts=value.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/);if(!parts)throw Error('Enter a departure date and time.');
 const naive=Date.UTC(...[+parts[1],+parts[2]-1,+parts[3],+parts[4],+parts[5]]),matches=[];
 const fmt=new Intl.DateTimeFormat('sv-SE',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'});
 for(let offset=-14*60;offset<=14*60;offset+=15){const instant=naive-offset*60000;if(fmt.format(new Date(instant)).replace(' ','T')===value)matches.push((instant-base)/3600000);}
 if(matches.length!==1)throw Error(matches.length?'This local time occurs twice. Choose another time.':'This local time does not exist. Choose another time.');
 return matches[0];
}
function editorWindow(previous){
 const edit=state.editor,w={airport:edit.airport};
 if(edit.mode==='soon')w.max=6;
 if(edit.mode==='after')w.after=toHour(edit.after,places[previous.to].zone);
 return w;
}
function continuation(prefix,window){
 const horizon=168,seen=new Set(prefix.flatMap(f=>[f.from,f.to]));
 function tail(previous,remaining,visited){
  if(previous.to==='CDG')return [];
  if(!remaining)return null;
  const candidates=[];
  for(const f of flightPool){if(f.inAt>horizon||visited.has(f.to)||!connection(previous,f))continue;const rest=tail(f,remaining-1,new Set([...visited,f.from,f.to]));if(rest)candidates.push([f,...rest]);}
  return candidates.sort((a,b)=>a.at(-1).inAt-b.at(-1).inAt||a.length-b.length||a.map(f=>f.id).join().localeCompare(b.map(f=>f.id).join()))[0]||null;
 }
 return flightPool.filter(f=>f.inAt<=horizon&&!seen.has(f.to)&&connection(prefix.at(-1),f,window)).map(f=>{const rest=tail(f,3-prefix.length-1,new Set([...seen,f.from,f.to]));return {flight:f,tail:rest||[],complete:rest!==null,connection:connection(prefix.at(-1),f,window)};}).sort((a,b)=>a.flight.out-b.flight.out||a.flight.id.localeCompare(b.flight.id));
}
function editableOptions(){const o=get(state.selected);if(!o)return [];const prefix=o.legs.slice(0,state.editor.index);if(!prefix.length||prefix.at(-1).to==='CDG')return [];return continuation(prefix,editorWindow(prefix.at(-1)));}
function rebuildEdited(model,chosen){
 const old=get(state.selected),prefix=old.legs.slice(0,state.editor.index);let options;
 try{options=continuation(prefix,editorWindow(prefix.at(-1)));}catch(error){state.editor.error=error.message;renderAll();return;}
 const option=chosen?options.find(o=>o.flight.id===chosen):options.find(o=>o.complete);
 const before=old.legs.map(f=>f.id);state.editor.undo={selected:state.selected,edited:state.edited?JSON.parse(JSON.stringify(state.edited)):null,settings:state.editor.pendingSettings||editorSettings()};delete state.editor.pendingSettings;
 state.edited=make([...prefix,...(option?[option.flight,...option.tail]:[])]);state.selected=state.edited.id;state.editor.updated=state.edited.legs.filter(f=>!before.includes(f.id)).map(f=>f.id);state.editor.error='';state.editor.notice=state.edited.legs.at(-1).to==='CDG'?'Later legs updated.':'No complete continuation loaded before 24 Oct.';
 state.airport=null;state.edge=null;renderAll();persist(model.direction);
}
function editorSettings(){return Object.fromEntries(['index','mode','after','airport','allowChange'].map(k=>[k,state.editor[k]]));}
function beforeEditorChange(){state.editor.pendingSettings=editorSettings();}
if(!state.edited&&!saved.connectionVersion){state.edited=make([flightPool.find(f=>f.id==='d17-lhr-a'),flightPool.find(f=>f.id==='lhr-ams-early'),flightPool.find(f=>f.id==='ams-cdg-early')]);state.selected=state.edited.id;state.compared='d17-direct';state.hour=27.5;}
function renderEditor(model){
 const node=model.app.querySelector('.fr-editor'),o=get(state.selected),edit=state.editor;if(!node||!o)return;
 edit.index=Math.max(1,Math.min(edit.index,o.legs.length));
 const previous=o.legs[edit.index-1],canEdit=previous.to!=='CDG'&&edit.index<3;
 let options=[],error=edit.error;
 if(canEdit)try{options=editableOptions();}catch(e){error=e.message;}
 const current=o.legs[edit.index],cityAirports=[previous.to,...(edit.allowChange?demo.transfers.filter(t=>t.arrival_iata===previous.to).map(t=>t.departure_iata):[])];
 if(edit.airport&&!cityAirports.includes(edit.airport))edit.airport='';
 node.innerHTML=`<div class="fr-section-head"><h2>Connections</h2><button type="button" class="fr-editor-undo cursor-interaction" ${edit.undo?'':'disabled'}>Undo leg change</button></div><div class="fr-plan-legs">${o.legs.map((f,i)=>`${i?`<button type="button" class="fr-edit-gap cursor-interaction" data-edit-index="${i}" aria-pressed="${edit.index===i}">${o.legs[i-1].to===f.from?`Connection at ${f.from}`:`Airport change · ${o.legs[i-1].to} → ${f.from}`} · ${duration(f.out-o.legs[i-1].inAt)}<span>Edit next leg</span></button>`:''}<div class="fr-plan-leg" data-plan-leg="${f.id}" ${edit.updated.includes(f.id)?'data-updated="true"':''}><span class="fr-leg-index">${i+1}</span><div><strong>${f.from} → ${f.to}</strong><p>${f.number}${edit.updated.includes(f.id)?' · Updated':''}</p><p>${local(f.out,f.from)}<br>${local(f.inAt,f.to)}</p></div><button type="button" class="cursor-interaction" data-show-leg="${i}">Show leg ${i+1}</button></div>`).join('')}</div>${canEdit?`<fieldset class="fr-next-leg"><legend>Next flight after ${previous.to}</legend><div class="fr-editor-fields"><label>Departure window<select name="connection-window"><option value="soon">Within 6 hours</option><option value="any">Any later flight</option><option value="after">Depart after…</option></select></label><label ${edit.mode==='after'?'':'hidden'}>After · ${places[previous.to].zone}<input type="datetime-local" name="connection-after" value="${edit.after}" min="2026-10-17T00:00" max="2026-10-23T23:59"></label><label class="fr-city-check"><input type="checkbox" name="airport-change" ${edit.allowChange?'checked':''}> Allow airport changes in the same city</label><label>Depart from<select name="connection-airport">${(cityAirports.length>1?'<option value="">Any permitted airport</option>':'')+cityAirports.map(code=>`<option value="${code}">${code} · ${places[code].full}</option>`).join('')}</select></label><label class="fr-onward-field">Next leg<select name="next-flight" ${options.length?'':'disabled'}>${options.length?options.map(x=>`<option value="${x.flight.id}">${x.flight.from} → ${x.flight.to} · ${x.flight.number} · ${local(x.flight.out,x.flight.from)}${x.complete?'':' · no onward route loaded'}</option>`).join(''):'<option>No flights in this window</option>'}</select></label></div><p class="fr-transfer-note">${current&&current.from!==previous.to?'Airport change · '+previous.to+' → '+current.from+' · 3h minimum assumed. Includes ground travel and new check-in.':'45m connection allowance. Later legs update after each selection.'}</p><p class="fr-horizon">Loaded departures through 23 Oct · 3 flight legs maximum</p></fieldset>`:'<p class="fr-horizon">Select a connection above to change its next flight.</p>'}<p class="fr-editor-status" aria-live="polite">${edit.notice}</p><p class="fr-editor-error" role="alert" ${error?'':'hidden'}>${error}</p>`;
 const q=n=>node.querySelector(`[name="${n}"]`);
 if(canEdit){q('connection-window').value=edit.mode;q('connection-airport').value=edit.airport|| (cityAirports.length===1?cityAirports[0]:'');q('next-flight').value=current?.id||options[0]?.flight.id||'';
  q('connection-window').onchange=()=>{beforeEditorChange();edit.mode=q('connection-window').value;rebuildEdited(model);};
  q('connection-after').onchange=()=>{beforeEditorChange();edit.after=q('connection-after').value;rebuildEdited(model);};
  q('connection-airport').onchange=()=>{beforeEditorChange();edit.airport=q('connection-airport').value;rebuildEdited(model);};
  q('next-flight').onchange=()=>rebuildEdited(model,q('next-flight').value);
  q('airport-change').onchange=()=>{beforeEditorChange();edit.allowChange=q('airport-change').checked;edit.index=1;edit.airport='';rebuildEdited(model);};
 }
 for(const b of node.querySelectorAll('[data-edit-index]'))b.onclick=()=>{edit.index=+b.dataset.editIndex;edit.airport='';edit.mode='any';edit.error='';renderAll();model.app.querySelector('[name=next-flight]')?.focus({preventScroll:true});persist(model.direction);};
 for(const b of node.querySelectorAll('[data-show-leg]'))b.onclick=()=>{const f=o.legs[+b.dataset.showLeg],c=d3.geoInterpolate(places[f.from].coord,places[f.to].coord)(.5);Object.assign(model.camera,{world:false,zoom:1,dx:0,dy:0,lon:c[0],lat:c[1]});model.mapKind='globe';model.app.dataset.mobileView='map';for(const n of model.app.querySelectorAll('[data-mobile-view]'))n.setAttribute('aria-pressed',String(n.dataset.mobileView==='map'));state.hour=f.out;renderAll();model.app.querySelector('[data-camera=fit]').focus({preventScroll:true});persist(model.direction);};
 node.querySelector('.fr-editor-undo').onclick=()=>{const history=edit.undo;if(!history)return;state.selected=history.selected;state.edited=history.edited;Object.assign(edit,history.settings,{undo:null,notice:'Leg change undone.',updated:[],error:''});renderAll();model.app.querySelector('[name=next-flight]')?.focus({preventScroll:true});persist(model.direction);};
}
