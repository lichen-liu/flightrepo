const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),os=require('node:os');
const {chromium}=require('playwright');
(async()=>{
 const out=process.env.FLYJI_QA_OUTPUT;if(!out)throw new Error('Set FLYJI_QA_OUTPUT');fs.mkdirSync(out,{recursive:true});
 const browser=await chromium.launch({headless:true,channel:process.env.FLYJI_QA_BROWSER||'chrome'});
 const page=await browser.newPage({viewport:{width:1100,height:1000}});page.setDefaultTimeout(7000);
 const errors=[],requests=[];page.on('pageerror',e=>{errors.push(e.message);console.error('BROWSER '+e.message);});page.on('request',r=>requests.push(r.url()));
 await page.goto('file://'+(process.env.FLYJI_QA_PREVIEW||'/tmp/flyji-refresh-preview.html'));
 await page.addStyleTag({content:'iframe{height:8000px!important}'});
 const frame=page.frames().find(f=>f!==page.mainFrame());await frame.waitForSelector('.fr-select');
 const reports=[];function ensure(value,message){if(!value)throw new Error(message);reports.push(message);console.log('PASS '+message);}
 async function settle(){await page.waitForTimeout(210);}
 async function variant(direction){await frame.evaluate(direction=>document.querySelectorAll('[data-variant]').forEach(s=>s.hidden=s.dataset.direction!==direction),direction);const app=frame.locator(`.fr-app[data-direction=${direction}]`);await app.waitFor();await settle();return app;}
 async function shot(app,name){const height=await app.evaluate(e=>e.offsetHeight);await page.setViewportSize({width:page.viewportSize().width,height:Math.ceil(height+80)});await page.evaluate(()=>scrollTo(0,0));await frame.evaluate(()=>scrollTo(0,0));await settle();await page.screenshot({path:path.join(out,name),clip:await app.boundingBox()});}
 async function cursor(app,hour){await app.locator('[name=cursor]').evaluate((e,h)=>{e.value=h;e.dispatchEvent(new Event('input',{bubbles:true}));},hour);await settle();}
 async function reset(app){await frame.evaluate(()=>window.dispatchEvent(new CustomEvent('openai:set_globals',{detail:{globals:{widgetState:{privateContent:{refreshVersion:1,query:{day:17,nonstop:false,avoid:'',deadline:'none',selected:'d17-direct',compared:'d17-early-direct',hour:24,airport:null,edge:null,previous:null},cameras:{air:{world:false,zoom:1,dx:0,dy:0,lon:-35,lat:40},canvas:{world:false,zoom:1,dx:0,dy:0,lon:-35,lat:40},orbit:{world:false,zoom:1,dx:0,dy:0,lon:-35,lat:40}}}}}}})));await settle();}
 let app=await variant('air');
 ensure(await app.locator('.fr-geography .fr-land').count()===177,'Airlight: published basemap renders 177 countries');
 ensure(await app.locator('.fr-select').count()===5,'Airlight: five dated Toronto–Paris choices on 17 October');
 ensure((await app.locator('.fr-detail').innerText()).includes('06:00')&&(await app.locator('.fr-detail').innerText()).includes('Sun 18 Oct'),'Local endpoint clocks expose the next-day Paris arrival');
 ensure((await app.locator('.fr-comparison').innerText()).includes('same elapsed duration')&&(await app.locator('.fr-comparison').innerText()).includes('3h earlier arrival'),'Initial comparison exposes equal duration and different arrival instants');
 await app.locator('[data-compare="d17-yul-a+d17-yul-b"]').click();
 ensure((await app.locator('.fr-comparison').innerText()).includes('2h 15m longer'),'Pinning Montréal compares elapsed duration on the same axis');
 ensure(await app.locator('[data-wait]').count()===1,'Comparison lane shows a proportional connection wait');
 await app.locator('[data-select="d17-yul-a+d17-yul-b"]').focus();await page.keyboard.press('Enter');await settle();
 ensure((await app.locator('.fr-detail').innerText()).includes('1h 15m'),'Keyboard selection reveals the actual Montréal connection gap');
 ensure(await app.locator('[data-select="d17-yul-a+d17-yul-b"]').evaluate(e=>e===document.activeElement),'Keyboard selection retains focus on the same dated choice');
 await cursor(app,20.5);
 ensure((await app.locator('.fr-phase').innerText()).includes('Waiting · Montréal'),'Schedule cursor distinguishes a connection wait');
 ensure(await app.locator('.fr-position[data-phase=wait]').count()===1,'Waiting uses a hollow hub marker rather than a flying dot');
 ensure((await app.locator('.fr-clock-pair').innerText()).includes('16:30')&&(await app.locator('.fr-clock-pair').innerText()).includes('22:30'),'One instant displays Toronto and Paris local clocks correctly');
 await app.locator('.fr-event').click();await settle();
 ensure((await app.locator('.fr-phase').innerText()).includes('AC 202')&&await app.locator('.fr-position[data-phase=flight]').count()===1,'Next event changes wait to the dated onward flight');
 await cursor(app,18);ensure((await app.locator('.fr-phase').innerText()).includes('Not departed'),'Cursor before departure does not invent an airborne position');
 await cursor(app,32);ensure((await app.locator('.fr-phase').innerText()).includes('Arrived'),'Cursor after arrival stops the journey at its destination');
 await app.locator('[data-airport=YUL]').click();
 ensure((await app.locator('.fr-map-inspect').innerText()).includes('America/Toronto'),'Airport selection exposes its name and time zone');
 await app.locator('.fr-avoid-airport').click();await settle();
 ensure(await app.locator('.fr-select').count()===4&&(await app.locator('.fr-outside').innerText()).includes('retained'),'Avoiding a selected hub retains its excluded dated journey');
 ensure((await app.locator('.fr-detail').innerText()).includes('AC 101'),'Excluded evidence keeps the original dated flight identity');
 ensure(await app.locator('.fr-undo').evaluate(e=>e===document.activeElement),'Removed airport action transfers keyboard focus to Undo');
 await app.locator('.fr-undo').click();
 ensure(await app.locator('.fr-select').count()===5&&await app.locator('.fr-outside').count()===0,'Undo restores the constraint and selected evidence');
 await app.locator('[name=nonstop]').click();
 ensure(await app.locator('.fr-select').count()===2&&(await app.locator('.fr-outside').innerText()).includes('retained'),'Nonstop constraint preserves an excluded connection selection');
 ensure((await app.locator('[name=day] option[value="17"]').innerText()).includes('2 loaded'),'Departure-day counts reflect the current nonstop constraint');
 await app.locator('.fr-undo').click();await app.locator('[name=deadline]').selectOption('7');
 ensure(await app.locator('.fr-select').count()===3&&(await app.locator('.fr-status').innerText()).includes('18 Oct in Paris'),'07:00 next-day Paris deadline admits three loaded choices');
 await app.locator('[name=deadline]').selectOption('5');
 ensure(await app.locator('.fr-select').count()===1&&(await app.locator('.fr-outside').innerText()).includes('retained'),'Earlier arrival deadline admits the early nonstop and retains excluded evidence');
 await app.locator('.fr-restore').click();ensure(await app.locator('.fr-select').count()===5,'Restore query returns the selected journey to the results');
 await app.locator('[name=day]').selectOption('19');ensure((await app.locator('.fr-empty').innerText()).includes('No matching loaded'),'A covered empty date states no matching loaded journeys');
 await app.locator('[name=day]').selectOption('20');ensure((await app.locator('.fr-status').innerText()).includes('Coverage unknown')&&!(await app.locator('.fr-status').innerText()).includes('0 of'),'Missing coverage is distinct from zero service');
 await app.locator('[name=day]').selectOption('22');ensure((await app.locator('.fr-status').innerText()).includes('partial')&&await app.locator('.fr-select').count()===1,'Partial date labels its one loaded result');
 await reset(app);
 const routePoint=await app.locator('.fr-route[data-edge="YYZ|CDG"]').first().evaluate(p=>{const at=p.getPointAtLength(p.getTotalLength()*.51),screen=new DOMPoint(at.x,at.y).matrixTransform(p.getScreenCTM()),rect=p.closest('svg').getBoundingClientRect();return {x:screen.x-rect.left,y:screen.y-rect.top};});
 await app.locator('.fr-map').click({position:routePoint});await settle();
 ensure(await app.locator('[data-edge-select]').count()===2,'Picking a shared physical leg offers its two complete dated journeys');
 await app.locator('[data-edge-select="d17-early-direct"]').focus();await page.keyboard.press('Enter');await settle();
 ensure((await app.locator('.fr-detail').innerText()).includes('AC 870'),'Shared-leg choice selects the exact earlier dated flight');
 ensure(await app.locator('[data-edge-select="d17-early-direct"]').evaluate(e=>e===document.activeElement),'Shared-leg keyboard selection retains focus');
 app=await variant('canvas');
 ensure(await app.locator('.fr-day').count()===7,'Journey canvas offers seven departure days with coverage');
 ensure((await app.locator('.fr-detail').innerText()).includes('AC 870')&&(await app.locator('.fr-comparison').innerText()).includes('3h later'),'Switching visual direction preserves selection and comparison');
 await app.locator('[data-day="20"]').focus();await page.keyboard.press('Enter');await settle();
 ensure((await app.locator('.fr-empty').innerText()).includes('not evidence of no flights'),'Canvas unknown-day state avoids a false no-service claim');
 ensure(await app.locator('[data-day="20"]').evaluate(e=>e===document.activeElement),'Canvas date changes retain keyboard focus');
 await app.locator('[data-day="17"]').click();await reset(app);
 app=await variant('orbit');
 ensure(await app.locator('.fr-geography .fr-land').count()===177&&await app.locator('.fr-airport').count()>=4,'Orbit studio renders geographic Earth with reachable airport controls');
 const before=await app.locator('.fr-route').first().getAttribute('d');await app.locator('.fr-map-move summary').click();await app.locator('[data-camera=east]').click();await settle();
 ensure(before!==await app.locator('.fr-route').first().getAttribute('d'),'Native camera control rotates the Earth');
 ensure(await app.locator('.fr-select').count()===5&&(await app.locator('.fr-detail').innerText()).includes('AC 872'),'Camera changes preserve the query and selected dated journey');
 await app.locator('[data-camera=fit]').click();await app.locator('[name=longitude]').focus();await page.keyboard.press('ArrowRight');await settle();
 ensure(Number(await app.locator('[name=longitude]').inputValue())===-34,'Earth longitude is adjustable with the keyboard');
 await app.locator('[data-camera=fit]').click();await app.locator('.fr-map-move summary').click();
 await app.locator('.fr-remove-compare').click();ensure((await app.locator('.fr-time h2').innerText()).includes('Time in your journey'),'Clearing comparison gives the one-journey state its own heading');
 ensure(await app.locator('[name=cursor]').evaluate(e=>e===document.activeElement),'Clearing comparison transfers focus to the schedule cursor');
 await reset(app);
 const stable=await app.locator('[name=cursor]').inputValue();await page.waitForTimeout(350);ensure(await app.locator('[name=cursor]').inputValue()===stable,'No autoplay advances the schedule cursor');
 await page.emulateMedia({reducedMotion:'reduce'});await cursor(app,24.5);
 ensure(await app.locator('.fr-position').evaluate(e=>!e.__transition),'Reduced motion disables marker interpolation');
 await frame.evaluate(()=>{window.__flyjiCaptured=null;window.openai.setWidgetState=async s=>{window.__flyjiCaptured=s;};});
 await app.locator('[data-compare="d17-kef-a+d17-kef-b"]').click();await settle();
 const stored=await frame.evaluate(()=>window.__flyjiCaptured);ensure(stored?.modelContent?.compared==='d17-kef-a+d17-kef-b'&&JSON.stringify(stored).length<16384,'Meaningful selection persists with a bounded state snapshot');
 await app.locator('[data-select="d17-yul-a+d17-yul-b"]').focus();await page.keyboard.press('Enter');await settle();
 await frame.evaluate(s=>dispatchEvent(new CustomEvent('openai:set_globals',{detail:{globals:{widgetState:s}}})),stored);await settle();
 ensure((await app.locator('.fr-detail').innerText()).includes('AC 872')&&(await app.locator('.fr-comparison').innerText()).includes('Keflavík'),'Host state restoration recovers dated selection and comparison');
 await reset(app);
 for(const direction of ['air','canvas','orbit']){app=await variant(direction);await shot(app,direction+'-desktop.png');}
 for(const width of [320,390,736,1024]){
  await page.setViewportSize({width,height:1000});const heights=[];
  for(const direction of ['air','canvas','orbit']){
   app=await variant(direction);const layout=await app.evaluate(e=>({width:e.clientWidth,scroll:e.scrollWidth,height:e.offsetHeight}));heights.push(layout.height);
   ensure(layout.scroll<=layout.width+2,`${direction}: no horizontal overflow at ${width}px`);
   const clipped=await app.evaluate(e=>{const outer=e.getBoundingClientRect();return [...e.querySelectorAll('h1,h2,h3,p,small,label,button,input,select,svg text')].filter(n=>{const r=n.getBoundingClientRect();return r.width&&r.height&&(r.left<outer.left-1||r.right>outer.right+1);}).map(n=>n.textContent.slice(0,70));});
   ensure(clipped.length===0,`${direction}: text and controls fit at ${width}px`);
   const airportOverlap=await app.evaluate(e=>{const boxes=[...e.querySelectorAll('.fr-airport')].map(n=>n.getBoundingClientRect());return boxes.some((a,i)=>boxes.slice(i+1).some(b=>a.left<b.right&&a.right>b.left&&a.top<b.bottom&&a.bottom>b.top));});
   ensure(!airportOverlap,`${direction}: airport targets do not overlap at ${width}px`);
   const labelOverlap=await app.locator('.fr-time-chart').evaluate(svg=>{const boxes=[...svg.querySelectorAll('text')].map(n=>n.getBoundingClientRect());return boxes.some((a,i)=>boxes.slice(i+1).some(b=>a.left<b.right&&a.right>b.left&&a.top<b.bottom&&a.bottom>b.top));});
   ensure(!labelOverlap,`${direction}: time-axis labels do not overlap at ${width}px`);
   const svgScale=await app.locator('.fr-map').evaluate(e=>Math.abs(e.viewBox.baseVal.width-e.clientWidth));ensure(svgScale<1,`${direction}: geography is redrawn at its actual width ${width}px`);
   if(width===390)await shot(app,direction+'-mobile.png');
  }
  ensure(Math.max(...heights)-Math.min(...heights)<3,`Alternatives keep a shared responsive stage height at ${width}px`);
 }
 const contrast=[];
 for(const scheme of ['light','dark']){await page.emulateMedia({colorScheme:scheme,reducedMotion:'reduce'});
  for(const direction of ['air','canvas','orbit']){app=await variant(direction);const samples=await app.evaluate(e=>{const lum=s=>(s.match(/[\d.]+/g)||[]).slice(0,3).map(Number).map(c=>{c/=255;return c<=.04045?c/12.92:((c+.055)/1.055)**2.4;}).reduce((sum,c,i)=>sum+c*[.2126,.7152,.0722][i],0);const bg=n=>{for(let p=n;p;p=p.parentElement){const color=getComputedStyle(p).backgroundColor;if(color!=='rgba(0, 0, 0, 0)'&&color!=='transparent')return color;}return getComputedStyle(e).backgroundColor;};return [...e.querySelectorAll('h1,h2,h3,p,small,label,button,summary,.fr-status,.fr-phase,.fr-clock-pair span,.fr-time-chart text')].filter(n=>{const r=n.getBoundingClientRect();return r.width&&r.height&&!n.disabled;}).map(n=>{const css=getComputedStyle(n),a=lum(n.tagName==='text'?css.fill:css.color),b=lum(bg(n));return {label:n.textContent.slice(0,70),ratio:(Math.max(a,b)+.05)/(Math.min(a,b)+.05),size:parseFloat(css.fontSize)};});});contrast.push({scheme,direction,minimum:Math.min(...samples.map(s=>s.ratio)),samples:samples.length});const bad=samples.filter(s=>s.ratio<4.5||s.size<11);if(bad.length)console.error('CONTRAST '+JSON.stringify(bad));ensure(!bad.length,`${direction}: sampled essential text contrast and size pass in ${scheme}`);}
 }
 await page.setViewportSize({width:1100,height:1000});await shot(await variant('orbit'),'orbit-dark.png');
 const touch=await browser.newPage({viewport:{width:390,height:1000},hasTouch:true,isMobile:true});await touch.goto('file://'+(process.env.FLYJI_QA_PREVIEW||'/tmp/flyji-refresh-preview.html'));const tf=touch.frames().find(f=>f!==touch.mainFrame());await tf.waitForSelector('.fr-select');
 for(const direction of ['air','canvas','orbit']){await tf.evaluate(d=>document.querySelectorAll('[data-variant]').forEach(s=>s.hidden=s.dataset.direction!==d),direction);await touch.waitForTimeout(200);const ta=tf.locator(`.fr-app[data-direction=${direction}]`);const small=await ta.evaluate(e=>[...e.querySelectorAll('button,select,input,summary')].filter(n=>{const r=n.getBoundingClientRect();return r.width&&r.height&&!n.disabled&&(r.width<43||r.height<43);}).map(n=>n.textContent.slice(0,45)));if(small.length)console.error(JSON.stringify(small));ensure(!small.length,`${direction}: visible coarse-pointer controls reach 44px`);await ta.locator('[data-airport=YUL]').click();ensure((await ta.locator('.fr-map-inspect').innerText()).includes('Montréal'),`${direction}: touch airport selection has a readable counterpart`);}
 await touch.close();ensure(!errors.length,'No JavaScript runtime errors');
 const sha=p=>crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');const source=path.resolve(process.env.FLYJI_QA_SOURCE||'docs/design/flyji-refresh.html');
 const productRequests=requests.filter(u=>/^https?:/.test(u));
 fs.writeFileSync(path.join(out,'qa-results.json'),JSON.stringify({date:'2026-10-11',checks:reports.length,results:reports,errors,environment:{browser:await browser.version(),os:os.platform()+' '+os.release(),headless:true,widths:[320,390,736,1024],captureWidth:1100,touchWidth:390,audit:'author browser QA; no independent reviewer or participant sessions'},contrast,network:{httpRequests:productRequests,scope:'Rendered wrapper may load its own allowed CDN resources; product geometry, library and fixture are embedded.'},source_bytes:fs.statSync(source).size,source_sha256:sha(source),check_script_sha256:sha(__filename),screenshots:Object.fromEntries(fs.readdirSync(out).filter(n=>n.endsWith('.png')).sort().map(n=>[n,sha(path.join(out,n))]))},null,2)+'\n');console.log(JSON.stringify({checks:reports.length,errors}));await browser.close();
})().catch(e=>{console.error(e);process.exit(1);});
