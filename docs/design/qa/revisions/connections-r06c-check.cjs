const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {chromium}=require('playwright');
const sha=p=>crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
(async()=>{
 const out=process.env.FLYJI_QA_OUTPUT;if(!out)throw Error('Set FLYJI_QA_OUTPUT');fs.mkdirSync(out,{recursive:true});
 const browser=await chromium.launch({headless:true,channel:'chrome'}),page=await browser.newPage({viewport:{width:1100,height:1000}});
 const errors=[],reports=[];page.on('pageerror',e=>{errors.push(e.message);console.log('JS ERROR',e.message);});
 page.setDefaultTimeout(6000);await page.emulateMedia({reducedMotion:'reduce'});
 await page.goto('file:///tmp/flyji-connections-preview.html');await page.addStyleTag({content:'iframe{height:8000px!important}'});
 const frame=page.frames().find(f=>f!==page.mainFrame());await frame.waitForSelector('.fr-editor select',{timeout:20000});await frame.waitForSelector('.viz-carousel-controls',{timeout:90000});
 const ensure=(value,message)=>{if(!value)throw Error(message);reports.push(message);console.log('PASS',message);};
 const settle=()=>page.waitForTimeout(120);
 async function variant(direction){const name={air:'Map first',canvas:'Connections first',orbit:'Globe first'}[direction];for(let i=0;i<3&&await frame.locator('.viz-carousel').getAttribute('data-active-variant')!==name;i++){await frame.locator('.viz-carousel-next').focus();await page.keyboard.press('Enter');await settle();}return frame.locator(`.fr-app[data-direction=${direction}]`);}
 const initial=await frame.evaluate(()=>structuredClone(window.openai.widgetState));
 // The initial render is not saved. Capture a real interaction first for the test restore fixture.
 let app=await variant('air');await app.locator('[name=connection-window]').selectOption('any');await settle();
 const fixture=await frame.evaluate(()=>structuredClone(window.openai.widgetState));
 async function reset(){await frame.evaluate(p=>{window.dispatchEvent(new CustomEvent('openai:set_globals',{detail:{globals:{widgetState:p}}}));},fixture);await settle();}
 const ids=async a=>a.locator('[data-plan-leg]').evaluateAll(es=>es.map(e=>e.dataset.planLeg));
 const opts=async a=>a.locator('[name=next-flight] option').evaluateAll(es=>es.map(e=>e.value));
 async function view(a,name){await a.locator(`[data-mobile-view=${name}]`).click();await settle();}
 async function shot(a,name){const h=await a.evaluate(e=>e.offsetHeight);await page.setViewportSize({width:page.viewportSize().width,height:Math.ceil(h+80)});await page.evaluate(()=>scrollTo(0,0));await frame.evaluate(()=>scrollTo(0,0));await settle();await page.screenshot({path:path.join(out,name),clip:await a.boundingBox()});}
 for(const direction of ['air','canvas','orbit']){
  app=await variant(direction);await reset();
  ensure(await app.locator('.fr-geography .fr-land').count()===177,direction+': published world renders 177 countries');
  const headings=await app.locator('h1,h2').allTextContents();
  ensure(headings.includes('Connections')&&headings.includes('Route map')&&headings.includes('Flight schedule')&&!headings.some(h=>/One clock|Two choices|possibilit|perspective/i.test(h)),direction+': plain task headings');
  ensure(JSON.stringify(await ids(app))===JSON.stringify(['d17-lhr-a','lhr-ams-early','ams-cdg-early']),direction+': exact initial three-leg route');
  ensure((await app.locator('.fr-selected-summary').innerText()).includes('3h total connection time'),direction+': all connection gaps included in total');
  await app.locator('[name=next-flight]').focus();await page.keyboard.press('ArrowDown');await page.keyboard.press('Enter');await settle();
  // Choose exact identity rather than depending on native option ordering.
  await app.locator('[name=next-flight]').selectOption('lhr-ams-late');await settle();
  ensure(JSON.stringify(await ids(app))===JSON.stringify(['d17-lhr-a','lhr-ams-late','ams-cdg-late']),direction+': later leg replaces the entire dependent suffix');
  ensure(await app.locator('[name=next-flight]').evaluate(e=>e===document.activeElement),direction+': native next-leg focus retained');
  ensure((await app.locator('[data-plan-leg="ams-cdg-late"]').innerText()).includes('Updated'),direction+': updated suffix identified');
  await app.locator('.fr-editor-undo').click();await settle();
  ensure((await ids(app))[1]==='lhr-ams-early',direction+': Undo restores exact route');
  await app.locator('[data-edit-index="2"]').click();await app.locator('[name=next-flight]').selectOption('ams-cdg-late');await settle();
  ensure(JSON.stringify(await ids(app))===JSON.stringify(['d17-lhr-a','lhr-ams-early','ams-cdg-late']),direction+': editing final leg retains both earlier flights');
  await reset();
  await app.locator('[name=airport-change]').check();await settle();
  ensure((await opts(app)).includes('lgw-ams-early')&&!(await opts(app)).includes('lgw-ams-too-soon'),direction+': same-city choices respect transfer allowance');
  await app.locator('[name=connection-airport]').selectOption('LGW');await settle();
  ensure(JSON.stringify(await ids(app))===JSON.stringify(['d17-lhr-a','lgw-ams-early','ams-cdg-mid']),direction+': LHR → LGW rebuilds Amsterdam onward flight');
  ensure((await app.locator('.fr-transfer-note').innerText()).includes('3h minimum assumed'),direction+': airport transfer assumption visible');
  ensure(await app.locator('.fr-route.transfer').count()===1,direction+': ground change has a distinct dashed geographic link');
  ensure(await app.locator('.fr-ground-key').isVisible(),direction+': airport-change line has a visible legend');
  await app.locator('[name=next-flight]').selectOption('lgw-ams-overnight');await settle();
  ensure((await ids(app)).at(-1)==='ams-cdg-next-day',direction+': overnight selection rebuilds next-day final leg');
  ensure((await app.locator('.fr-plan-legs').innerText()).includes('25h'),direction+': overnight wait displayed at actual duration');
  await app.locator('[name=connection-window]').selectOption('after');await app.locator('[name=connection-after]').fill('2026-10-19T02:00');await app.locator('[name=connection-after]').press('Tab');await settle();
  ensure((await ids(app))[1]==='lgw-ams-overnight',direction+': origin-local departure threshold converts to the right instant');
  await app.locator('[name=connection-after]').fill('2026-10-23T09:00');await app.locator('[name=connection-after]').press('Tab');await settle();
  ensure((await ids(app)).length===1&&(await app.locator('.fr-editor-status').innerText()).includes('No complete'),direction+': no loaded continuation keeps earlier leg and exposes gap');
  ensure((await app.locator('.fr-selected-summary').innerText()).includes('Last arrival · LHR'),direction+': incomplete prefix does not claim arrival in Paris');
  ensure((await app.locator('.fr-comparison').innerText()).includes('Route incomplete'),direction+': incomplete prefix has no misleading final-arrival comparison');
  await reset();await app.locator('[name=next-flight]').selectOption('no-tail');await settle();
  ensure(JSON.stringify(await ids(app))===JSON.stringify(['d17-lhr-a','no-tail']),direction+': explicitly chosen dead end remains selected');
  await reset();await app.locator('[name=deadline]').selectOption('5');await settle();
  ensure(await app.locator('.fr-outside').count()===1,direction+': edited journey excluded by arrival constraint is retained and labeled');
  await reset();await app.locator('[name=connection-window]').selectOption('soon');await app.locator('.fr-editor-undo').click();await settle();
  ensure(await app.locator('[name=connection-window]').inputValue()==='any',direction+': Undo restores the prior window as well as exact flight identities');
  await reset();await app.locator('[data-show-leg="1"]').click();await settle();
  ensure(await app.locator('[data-map-kind=globe]').getAttribute('aria-pressed')==='true'&&Number(await app.locator('[name=cursor]').inputValue())===27,direction+': Show leg orients globe and schedule to that exact leg');
  await app.locator('[data-map-kind=flat]').click();await settle();ensure(await app.locator('[data-map-kind=flat]').getAttribute('aria-pressed')==='true',direction+': flat view remains available');
  await app.locator('[data-map-kind=globe]').click();await settle();ensure(await app.locator('.fr-geography .fr-land').count()===177,direction+': sphere retains published basemap');
  await reset();await shot(app,direction+'-desktop.png');
 }
 for(const width of [320,390,736,1024]){
  await page.setViewportSize({width,height:1000});
  for(const direction of ['air','canvas','orbit']){
   app=await variant(direction);await reset();
   if(width<650)await view(app,'legs');
   ensure(await app.evaluate(e=>e.scrollWidth<=e.clientWidth+1),`${direction} ${width}px: no horizontal overflow`);
   ensure(await app.locator('[name=next-flight]').evaluate(e=>{const r=e.getBoundingClientRect(),p=e.closest('.fr-editor').getBoundingClientRect();return r.left>=p.left&&r.right<=p.right+1;}),`${direction} ${width}px: next-flight control fits`);
   await app.locator('[name=connection-window]').selectOption('after');await settle();
   ensure(await app.locator('[name=connection-after]').evaluate(e=>e.getBoundingClientRect().width<=e.closest('.fr-editor').getBoundingClientRect().width),`${direction} ${width}px: date control fits`);
   await reset();if(width<650)await view(app,'map');
   ensure(await app.locator('.fr-map').evaluate(e=>Math.abs(e.clientWidth-e.viewBox.baseVal.width)<1),`${direction} ${width}px: map projects to its actual width`);
   if(width===390){await shot(app,direction+'-mobile-map.png');await view(app,'legs');await shot(app,direction+'-mobile-legs.png');}
  }
 }
 await page.setViewportSize({width:1100,height:1000});app=await variant('orbit');await reset();await page.emulateMedia({colorScheme:'dark',reducedMotion:'reduce'});await settle();await shot(app,'orbit-dark.png');
 for(const scheme of ['light','dark']){
  await page.emulateMedia({colorScheme:scheme,reducedMotion:'reduce'});
  for(const direction of ['air','canvas','orbit']){
   app=await variant(direction);await reset();
   const contrast=await app.evaluate(e=>{
    const rgb=s=>{const x=document.createElement('span');x.style.color=s;document.body.append(x);const v=getComputedStyle(x).color.match(/[\d.]+/g).slice(0,3).map(Number);x.remove();return v;};
    const lum=c=>c.map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;}).reduce((s,v,i)=>s+v*[.2126,.7152,.0722][i],0);
    const style=getComputedStyle(e),surface=rgb(style.getPropertyValue('--surface')),quiet=rgb(style.getPropertyValue('--quiet')),ink=rgb(style.getPropertyValue('--ink')),ratio=(a,b)=>{const x=lum(a),y=lum(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);};return {quiet:ratio(quiet,surface),ink:ratio(ink,surface)};
   });ensure(contrast.quiet>=4.5&&contrast.ink>=4.5,`${direction} ${scheme}: sampled primary and supporting text contrast >=4.5`);
  }
 }
 // Touch uses a separate page and its own error listener.
 const touch=await browser.newPage({viewport:{width:390,height:1000},hasTouch:true,isMobile:true});touch.on('pageerror',e=>errors.push(e.message));await touch.emulateMedia({reducedMotion:'reduce'});await touch.goto('file:///tmp/flyji-connections-preview.html');await touch.addStyleTag({content:'iframe{height:8000px!important}'});
 const tf=touch.frames().find(f=>f!==touch.mainFrame());await tf.waitForSelector('.fr-editor select',{state:'attached'});await tf.waitForSelector('.viz-carousel-controls',{timeout:90000});
 for(let i=0;i<3&&await tf.locator('.viz-carousel').getAttribute('data-active-variant')!=='Map first';i++)await tf.locator('.viz-carousel-next').tap();
 const ta=tf.locator('.fr-app[data-direction=air]');await ta.locator('[data-mobile-view=legs]').tap();
 const small=await ta.locator('button:visible,select:visible,input[type=datetime-local]:visible').evaluateAll(es=>es.filter(e=>!e.disabled).map(e=>({label:e.getAttribute('name')||e.textContent.trim(),height:e.getBoundingClientRect().height})).filter(e=>e.height<43));ensure(small.length===0,'Touch: visible active controls provide 44px height');
 await ta.locator('[name=airport-change]').check();await ta.locator('[name=connection-window]').selectOption('any');await ta.locator('[name=next-flight]').selectOption('lgw-ams-overnight');ensure((await ids(ta)).at(-1)==='ams-cdg-next-day','Touch: airport-change and overnight editing work without dragging');
 await ta.locator('[data-show-leg="1"]').tap();ensure(await ta.locator('[data-map-kind=globe]').getAttribute('aria-pressed')==='true','Touch: Show leg opens globe without a gesture');
 ensure(errors.length===0,'Zero JavaScript errors across desktop and touch contexts');
 const source='docs/design/flyji-connections.html',captures=Object.fromEntries(fs.readdirSync(out).filter(f=>f.endsWith('.png')).map(f=>[f,sha(path.join(out,f))]));
 fs.writeFileSync(path.join(out,'qa-results.json'),JSON.stringify({date:'2026-10-13',checks:reports.length,results:reports,errors,environment:{browser:await browser.version(),headless:true,widths:[320,390,736,1024,1100],touch:390},source_bytes:fs.statSync(source).size,source_sha256:sha(source),script_sha256:sha(__filename),screenshots:captures,limitation:'Author checks on synthetic fixture; no participant, real-device, screen-reader or live-provider validation.'},null,2)+'\n');
 console.log('TOTAL',reports.length,'CAPTURES',Object.keys(captures).length);await browser.close();
})().catch(e=>{console.error(e);process.exit(1);});
