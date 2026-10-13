const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require('playwright');
(async()=>{
const out=process.env.FLYJI_QA_OUTPUT;
if(!out)throw new Error('Set FLYJI_QA_OUTPUT to a writable output folder');
const browser=await chromium.launch({headless:true,channel:process.env.FLYJI_QA_BROWSER||'chrome'});
const page=await browser.newPage({viewport:{width:1100,height:950}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
await page.goto('file://'+(process.env.FLYJI_QA_PREVIEW||'/tmp/flyji-design-preview.html'));await page.addStyleTag({content:'iframe {height:6000px!important}'});const frame=page.frames().find(f=>f!==page.mainFrame());await frame.waitForSelector('.fy-result');
const reports=[];
async function shot(app,name){const height=await app.evaluate(el=>el.offsetHeight);await page.setViewportSize({width:page.viewportSize().width,height:Math.ceil(height+80)});await page.evaluate(()=>window.scrollTo(0,0));await frame.evaluate(()=>window.scrollTo(0,0));await page.waitForTimeout(150);await page.screenshot({path:path.join(out,name),clip:await app.boundingBox()});}
function ensure(v,msg){if(!v)throw new Error(msg);reports.push(msg);console.log('PASS '+msg);}
async function variant(layout){await frame.evaluate(layout=>{document.querySelectorAll('[data-variant]').forEach(s=>s.hidden=s.dataset.layout!==layout)},layout);return frame.locator(`[data-layout="${layout}"].fy-app`);}
for(const layout of ['board','atlas','globe']){
const app=await variant(layout);
ensure(await app.locator('.fy-result').count()===4,layout+': four journey options on 17 Oct');
await app.locator('[name=stops]').selectOption('0');ensure(await app.locator('.fy-result').count()===1,layout+': direct filter leaves one');
await app.locator('[name=stops]').selectOption('1');
await app.locator('.fy-select').filter({hasText:'YYZ → YUL → CDG'}).focus();await page.keyboard.press('Enter');
ensure((await app.locator('.fy-detail').innerText()).includes('1h 15m'),layout+': selection reveals the correct layover');
ensure(await app.locator('.fy-select').filter({hasText:'YYZ → YUL → CDG'}).evaluate(el=>el===document.activeElement),layout+': keyboard selection preserves focus');
await app.locator('[name=cursor]').fill('200');await app.locator('[name=cursor]').dispatchEvent('input');ensure((await app.locator('.fy-selection-status').innerText()).includes('Connection wait · YUL'),layout+': time cursor identifies the Montreal wait');
await app.locator('.fy-step').click();ensure((await app.locator('.fy-selection-status').innerText()).includes('In flight · AC 202'),layout+': next event advances to the onward flight');
await app.locator('.fy-step').click();ensure((await app.locator('.fy-selection-status').innerText()).includes('Arrived · CDG'),layout+': next event advances to arrival');
if(layout!=='board')ensure(await app.locator('.fy-route.selected').count()===2,layout+': map selection contains both connecting legs');
await app.locator('[name=cursor]').fill('270');await app.locator('[name=cursor]').dispatchEvent('input');
ensure((await app.locator('.fy-time-label').innerText()).includes('Oct'),layout+': cursor has an explicit date');
await app.locator('.fy-pin').first().click();await app.locator('.fy-pin').nth(1).click();ensure(await app.locator('.fy-lane').count()===2,layout+': pinned comparison has two lanes');
await app.locator('[name=stops]').selectOption('0');ensure((await app.locator('.fy-pin-status').innerText()).includes('1 outside this query'),layout+': comparison acknowledges a filtered-out pin');await app.locator('[name=stops]').selectOption('1');
await app.locator('[name=origin]').selectOption('YUL');await app.locator('[name=destination]').selectOption('YUL');await app.locator('.fy-search').click();ensure(await app.locator('.fy-error').isVisible(),layout+': identical airports produce a visible validation error');ensure((await app.locator('.fy-count').innerText()).includes('Toronto'),layout+': invalid draft leaves applied results intact');await app.locator('[name=origin]').selectOption('YYZ');await app.locator('[name=destination]').selectOption('CDG');await app.locator('.fy-search').click();
await app.locator('[data-task=network]').click();ensure(await app.locator('.fy-result').count()===7,layout+': network returns seven dated options across four destinations');
await app.locator('[data-task=service]').click();ensure(await app.locator('.fy-result').count()===4,layout+': service view shows four departure records');
await app.locator('details').first().locator('summary').click();await app.locator('[name=cancelled]').check();ensure(await app.locator('.fy-result').count()===5,layout+': cancelled evidence can be inspected explicitly');
await app.locator('[data-task=journey]').click();
await app.locator('[name=day]').fill('2026-10-19');await app.locator('form button').click();ensure(await app.locator('.fy-empty').count()===1,layout+': out-of-coverage query has an empty state');ensure((await app.locator('.fy-empty').innerText()).includes('no demo coverage'),layout+': missing coverage differs from no service');
await app.locator('.fy-recover').click();
await app.locator('[name=day]').fill('2026-10-18');await app.locator('[name=day]').press('Enter');ensure(await app.locator('.fy-result').count()===2,layout+': keyboard Search produces two journeys on another date');
await app.locator('[name=day]').fill('2026-10-17');await app.locator('form button').click();
await app.locator('details').first().locator('summary').click();
if(layout!=='board')ensure(await app.locator('.fy-map .fy-land').count()===1,layout+': basemap is present');
if(layout==='globe'){const before=await app.locator('.fy-map').innerHTML();await app.locator('[name=rotation]').fill('30');await app.locator('[name=rotation]').dispatchEvent('input');ensure(before!==await app.locator('.fy-map').innerHTML(),'globe: rotation changes projection');await app.locator('.fy-recenter').click();}
await shot(app,layout+'-desktop.png');
}
for(const width of [320,390,736,1024]){
await page.setViewportSize({width,height:1000});
for(const layout of ['board','atlas','globe']){
const app=await variant(layout);await page.waitForTimeout(80);
const dimensions=await app.evaluate(el=>({scroll:el.scrollWidth,width:el.clientWidth}));
ensure(dimensions.scroll<=dimensions.width+2,`${layout}: no horizontal overflow at ${width}px`);
const clipping=await app.evaluate(el=>{const outer=el.getBoundingClientRect();return [...el.querySelectorAll('button,select,input,p,h1,h2,h3,.fy-result-meta,.fy-result-duration,.fy-axis span,.fy-lane-label')].filter(n=>{const r=n.getBoundingClientRect();return r.width&&r.height&&(r.left<outer.left-1||r.right>outer.right+1)}).map(n=>n.tagName+': '+n.textContent.slice(0,60));});ensure(clipping.length===0,`${layout}: text and controls fit at ${width}px`);
if(width===390)await shot(app,layout+'-mobile.png');
}
}
for(const scheme of ['light','dark']){await page.emulateMedia({colorScheme:scheme,reducedMotion:'reduce'});for(const layout of ['board','atlas','globe']){const app=await variant(layout);const checks=await app.evaluate(el=>{const rgb=s=>(s.match(/[\d.]+/g)||[]).slice(0,3).map(Number);const lum=s=>rgb(s).map(c=>{c/=255;return c<=.04045?c/12.92:((c+.055)/1.055)**2.4}).reduce((t,v,i)=>t+v*[.2126,.7152,.0722][i],0);const bg=n=>{for(let p=n;p;p=p.parentElement){const c=getComputedStyle(p).backgroundColor;if(c!=='rgba(0, 0, 0, 0)'&&c!=='transparent')return c;}return getComputedStyle(el).backgroundColor;};return ['.fy-app','.fy-subtitle','.fy-result-meta','.fy-select[aria-pressed=true]','.fy-search','.fy-count','.fy-time-label','.fy-map-caption h2','.fy-selection-status'].flatMap(selector=>{const n=selector==='.fy-app'?el:el.querySelector(selector);if(!n||!n.getBoundingClientRect().height)return [];const css=getComputedStyle(n),a=lum(css.color),b=lum(bg(n));return [{selector,ratio:(Math.max(a,b)+.05)/(Math.min(a,b)+.05),size:parseFloat(css.fontSize)}];});});ensure(checks.every(c=>c.ratio>=4.5&&c.size>=11),`${layout}: sampled essential text meets 4.5:1 contrast and 11px at ${scheme}`);}}
await page.setViewportSize({width:1100,height:950});const globe=await variant('globe');await shot(globe,'globe-dark.png');
const touchPage=await browser.newPage({viewport:{width:390,height:1000},hasTouch:true,isMobile:true});await touchPage.goto('file://'+(process.env.FLYJI_QA_PREVIEW||'/tmp/flyji-design-preview.html'));const touchFrame=touchPage.frames().find(f=>f!==touchPage.mainFrame());await touchFrame.waitForSelector('.fy-result');const touchApp=touchFrame.locator('[data-layout=board].fy-app');await touchApp.locator('summary').first().click();const smallTargets=await touchApp.evaluate(el=>[...el.querySelectorAll('button,select,summary')].filter(n=>{const r=n.getBoundingClientRect();return r.height&&r.width&&(r.height<43||r.width<43)}).map(n=>n.tagName+':'+n.textContent.slice(0,40)));ensure(smallTargets.length===0,'Touch: visible board buttons, selects, and summaries have 44px targets');await touchPage.close();
ensure(errors.length===0,'No JavaScript runtime errors');
fs.writeFileSync(path.join(out,'qa-results.json'),JSON.stringify({date:'2026-10-10',checks:reports.length,results:reports,errors},null,2));console.log(JSON.stringify({checks:reports.length,errors,output:out},null,2));await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
