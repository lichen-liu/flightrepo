export const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const ref = flight => ({provider:flight.provider, provider_id:flight.provider_id});
export const sameRef = (a,b) => a?.provider===b?.provider && a?.provider_id===b?.provider_id;
export function duration(minutes) {
  const m=Math.max(0,Math.floor(minutes)), days=Math.floor(m/1440), hours=Math.floor(m%1440/60), rest=m%60;
  return [days && `${days}d`, hours && `${hours}h`, (rest || !m) && `${rest}m`].filter(Boolean).join(' ');
}
export function localDate(instant, zone='UTC') {
  const parts=new Intl.DateTimeFormat('en-CA',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date(instant));
  const get=type=>parts.find(p=>p.type===type).value;
  return `${get('year')}-${get('month')}-${get('day')}`;
}
export function localInput(instant, zone='UTC') {
  const parts=new Intl.DateTimeFormat('en-CA',{timeZone:zone,hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date(instant));
  return `${localDate(instant,zone)}T${parts.find(p=>p.type==='hour').value}:${parts.find(p=>p.type==='minute').value}`;
}
export function stamp(instant, zone='UTC') {
  return new Intl.DateTimeFormat('en-GB',{timeZone:zone,day:'numeric',month:'short',hour:'2-digit',minute:'2-digit',hourCycle:'h23',timeZoneName:'shortOffset'}).format(new Date(instant));
}
export function addDate(date, days) {
  const d=new Date(`${date}T12:00:00Z`);d.setUTCDate(d.getUTCDate()+days);return d.toISOString().slice(0,10);
}
export function queryLink(values) {
  const params=new URLSearchParams();
  for(const [key,value] of Object.entries(values)) if(value!=='' && value!=null) params.set(key,String(value));
  return `${location.origin}/?${params}`;
}
export function errorMessage(error) {
  const detail=error?.detail;
  if(Array.isArray(detail)) return detail.map(x=>`${x.loc?.slice(1).join('.') || 'Input'}: ${x.msg}`).join(' · ');
  return detail?.message || (typeof detail==='string' ? detail : error?.message) || 'Could not read the flight data. Retry the request.';
}

// Latest response wins even when transport cancellation arrives too late.
export class RequestLane {
  generation=0; controller=null;
  cancel(){this.generation++;this.controller?.abort();this.controller=null;}
  async run(url, body, receive, failed, finished=()=>{}) {
    this.cancel();const generation=this.generation;
    const controller=this.controller=new AbortController();
    try {
      const response=await fetch(url,{signal:controller.signal,method:body===undefined?'GET':'POST',
        ...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
      const payload=await response.json();
      if(generation!==this.generation)return;
      if(!response.ok) throw payload;
      await receive(payload);
    } catch(error) {
      if(generation===this.generation && error?.name!=='AbortError') await failed(error);
    } finally {if(generation===this.generation){this.controller=null;finished();}}
  }
}
