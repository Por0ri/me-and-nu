const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
// Use an existing Playwright installation; this script never installs packages.
const { chromium } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const base = process.argv[2] || 'http://127.0.0.1:3100';
const out = process.argv[3] || path.join(require('node:os').tmpdir(), 'pivot-browser-check');
const session = {authenticated:true,sessionState:'onboarding_pending',onboardingCompleted:false,user:null,csrfToken:'local-test-only'};
const policies = [
 {type:'terms',policyVersion:'test-v1',required:true,text:'Local test terms'},
 {type:'privacy',policyVersion:'test-v2',required:true,text:'Local test privacy'},
 {type:'marketing',policyVersion:'test-v3',required:false,text:'Local optional policy'},
];
// Explicit intercepted UI fixtures, independent of the bundled demo catalog.
const onboardingWorks = [
 {entityId:901,name:'별빛 우체국',aliases:[],imageUrl:null,releaseYear:null,mediaFormat:null,selectable:true,unavailableReason:null},
 {entityId:902,name:'구름 정원의 로봇',aliases:[],imageUrl:null,releaseYear:null,mediaFormat:null,selectable:true,unavailableReason:null},
];
const onboardingTags = [
 {tagId:911,kind:'genre',code:'fantasy',label:'판타지',sourceEntityIds:[901]},
 {tagId:912,kind:'genre',code:'adventure',label:'모험',sourceEntityIds:[901,902]},
 {tagId:913,kind:'theme',code:'friendship',label:'우정',sourceEntityIds:[901,902]},
 {tagId:914,kind:'theme',code:'robot',label:'로봇',sourceEntityIds:[902]},
];
const mode = process.argv[4] || 'development';
const motion = process.argv[5] || 'reduce';
const results = [];
fs.mkdirSync(out, {recursive:true});
async function ready(page) {
 await page.evaluate(async () => { await document.fonts.ready; await Promise.all([...document.images].map(i=>{
  // H2-A introduces offscreen lazy slides; load them before a full-page capture.
  if(i.loading==='lazy') i.loading='eager'; return i.decode().catch(()=>{});
 })); });
}
async function snapshot(page, name) {
 await ready(page);
 const visual = await page.evaluate(() => ({
   overflow:document.documentElement.scrollWidth>innerWidth,
   broken:[...document.querySelectorAll('main img')].filter(i=>!i.complete||!i.naturalWidth).map(i=>i.getAttribute('src')),
   heading:document.querySelector('main h1')?.textContent,
 }));
 assert.equal(visual.overflow,false,name+' horizontal overflow');
 assert.deepEqual(visual.broken,[],name+' broken images');
 await page.screenshot({path:path.join(out,name+'.png'),fullPage:true});
 return visual;
}
async function headingFocused(page) {
 await page.waitForFunction(()=>document.activeElement===document.querySelector('main h1'));
 const style = await page.locator('main h1').evaluate(e=>({outline:getComputedStyle(e).outlineStyle,tabindex:e.tabIndex}));
 assert.equal(style.outline,'none'); assert.equal(style.tabindex,-1);
}
async function choose(page, name, value) {
 const input = page.locator('input[name='+name+'][value='+value+']');
 await input.locator('..').tap();
 await page.waitForFunction(({name,value})=>document.querySelector('input[name='+name+']:checked')?.value===value,{name,value});
 assert.equal(await page.locator('input[name='+name+']:checked').count(),1);
}
async function advance(page) {
 const button = page.getByRole('button',{name:'선택 완료',exact:true});
 await button.click();
}
async function chooseWork(page, label) {
 await page.getByRole('searchbox',{name:'작품 검색',exact:true}).fill(label);
 const button=page.getByRole('list',{name:'작품 검색 결과',exact:true}).getByRole('button').filter({hasText:label});
 await button.click();
 await page.waitForFunction(label=>[...document.querySelectorAll('ul[aria-label="작품 검색 결과"] button')].some(b=>b.textContent.includes(label)&&b.getAttribute('aria-pressed')==='true'),label);
}
async function advanceWorks(page) { await page.getByRole('button',{name:'선택한 작품으로 다음',exact:true}).click(); }
async function advanceTags(page) { await page.getByRole('button',{name:'장르·소재 선택 완료',exact:true}).click(); }
async function pauseClock(page) {
 await page.clock.pauseAt(await page.evaluate(()=>Date.now()+100));
}
async function quickResult(page) {
 await page.getByRole('button',{name:'로그인 없이 시연 계속하기'}).click();
 assert.equal(await page.getByRole('checkbox',{name:'모두 동의합니다'}).isChecked(),false);
 await page.getByRole('checkbox',{name:'모두 동의합니다'}).check();
 await page.getByRole('button',{name:'시작하기',exact:true}).click();
 const interest=page.getByRole('checkbox',{name:'애니메이션',exact:true});
 assert.equal(await interest.isChecked(),false); await interest.focus(); await page.keyboard.press('Space');
 await page.getByRole('button',{name:'애니메이션 선택하고 시작하기'}).click();
 assert.equal(await page.getByRole('list',{name:'선택한 작품',exact:true}).locator('li').count(),0);
 await chooseWork(page,'별빛 우체국'); await advanceWorks(page);
 await page.getByRole('checkbox',{name:'우정',exact:true}).check(); await page.getByRole('checkbox',{name:'판타지',exact:true}).check(); await advanceTags(page);
 assert.equal(await page.locator('input[name=consumptionType]:checked').count(),0);
 await choose(page,'consumptionType','world'); await advance(page);
 await page.waitForFunction(()=>!document.querySelector('button[aria-describedby="home-feedback"]').disabled);
}

async function checkLoadingGeometry(page,phase) {
 const geometry=await phase.evaluate(node=>{
  const image=node.querySelector('img:not([hidden])'),rect=image.getBoundingClientRect(),box=image.parentElement.getBoundingClientRect();
  return {phase:Number(node.getAttribute('data-demo-loading-phase')),width:rect.width,height:rect.height,top:rect.top-box.top,boxWidth:box.width,boxHeight:box.height,naturalWidth:image.naturalWidth,naturalHeight:image.naturalHeight,fit:getComputedStyle(image).objectFit};
 });
 const scale=(await page.evaluate(()=>innerHeight))<=700?.8:1;
 const cover=geometry.phase===2;
 assert.ok(Math.abs(geometry.boxWidth-227.7*scale)<.1);
 assert.ok(Math.abs(geometry.boxHeight-252.9*scale)<.1);
 assert.ok(Math.abs(geometry.width-227.7*scale)<.1);
 assert.ok(Math.abs(geometry.height-252.9*(cover?1:.9004)*scale)<.1);
 assert.ok(Math.abs(geometry.top-252.9*(cover?0:.0498)*scale)<.1);
 assert.equal(geometry.fit,cover?'cover':'fill');
 assert.equal(geometry.naturalWidth,process.env.PIVOT_ORIGINAL_LOADING_DIR?1254:512);
 assert.equal(geometry.naturalHeight,process.env.PIVOT_ORIGINAL_LOADING_DIR?1254:512);
 if(!cover) assert.ok(Math.abs(geometry.width/geometry.height-1)<.001);
}
async function reselectFlow(page,stem) {
 assert.equal(await page.getByRole('button',{name:'이전 단계'}).count(),0,'Result has no ordinary Back');
 const reselect=page.getByRole('button',{name:'다시 선택하기',exact:true});
 // Navigation deliberately locks for 200ms after each transition.
 await page.waitForFunction(()=>!document.querySelector('button').disabled && [...document.querySelectorAll('button')].some(b=>b.textContent==='다시 선택하기'&&!b.disabled));
 await reselect.focus();await page.keyboard.press('Enter');
 await page.getByRole('searchbox',{name:'작품 검색',exact:true}).waitFor();await headingFocused(page);
 assert.equal(await page.getByRole('list',{name:'선택한 작품',exact:true}).locator('li').count(),0);
 assert.equal(await page.getByLabel('선택 답변 요약').count(),0);
 assert.equal(await page.getByRole('button',{name:'선택한 작품으로 다음',exact:true}).isDisabled(),true);
 await page.getByRole('button',{name:'이전 단계'}).click();
 assert.equal(await page.getByRole('checkbox',{name:'애니메이션',exact:true}).isChecked(),true);
 assert.equal(await page.getByRole('button',{name:'이전 단계'}).count(),0,'Interest has no ordinary Back');
 await page.getByRole('button',{name:'애니메이션 선택하고 시작하기'}).click();
 await page.getByRole('searchbox',{name:'작품 검색',exact:true}).waitFor();
 assert.equal(await page.getByRole('list',{name:'선택한 작품',exact:true}).locator('li').count(),0);
 await chooseWork(page,'구름 정원의 로봇');await advanceWorks(page);await headingFocused(page);
 await page.getByRole('checkbox',{name:'로봇',exact:true}).waitFor();
 assert.equal(await page.locator('fieldset input[type=checkbox]:checked').count(),0);
 assert.equal(await page.getByRole('button',{name:'장르·소재 선택 완료',exact:true}).isDisabled(),true);
 await page.getByRole('checkbox',{name:'로봇',exact:true}).check();await page.getByRole('checkbox',{name:'모험',exact:true}).check();await advanceTags(page);await headingFocused(page);
 assert.equal(await page.locator('input[name=consumptionType]:checked').count(),0);
 assert.equal(await page.getByRole('button',{name:'선택 완료',exact:true}).isDisabled(),true);
 const input=page.locator('input[name=consumptionType][value=goods]');await input.focus();await page.keyboard.press('Space');
 assert.equal(await input.locator('..').evaluate(e=>getComputedStyle(e).outlineWidth),'3px');
 await advance(page);await headingFocused(page);
 const summary=await page.getByLabel('선택 답변 요약').innerText();
 assert.match(summary,/굿즈 구매 정보/);assert.match(summary,/구름 정원의 로봇/);assert.match(summary,/장르·소재[\s\S]*로봇/);
 assert.doesNotMatch(summary,/관련 OST|별빛 우체국/);
 await snapshot(page,stem+'-reselected-result');
}

async function questionFlow(page, stem, enterHome = false, releaseFirstImage) {
 await headingFocused(page);
 assert.equal(await page.getByRole('button',{name:'이전 단계'}).count(),0,'Interest has no ordinary Back');
 assert.equal(await page.getByRole('button',{name:'애니메이션 선택하고 시작하기'}).isDisabled(),true);
 const interest = page.getByRole('checkbox',{name:'애니메이션',exact:true});
 await interest.focus(); await page.keyboard.press('Space');
 assert.equal(await interest.isChecked(),true);
 const cardFocus=await interest.locator('..').evaluate(e=>getComputedStyle(e).outlineWidth);
 assert.equal(cardFocus,'3px');
 await snapshot(page,stem+'-interest');
 await page.getByRole('button',{name:'애니메이션 선택하고 시작하기'}).click();
 await headingFocused(page);
 assert.equal(await page.getByRole('button',{name:'선택한 작품으로 다음',exact:true}).isDisabled(),true);
 assert.equal(await page.getByRole('progressbar').getAttribute('aria-valuemax'),'3');
 await chooseWork(page,'별빛 우체국');
 assert.equal(await page.getByRole('list',{name:'선택한 작품',exact:true}).locator('li').count(),1);
 await snapshot(page,stem+'-q1');
 await advanceWorks(page); await headingFocused(page);
 assert.equal(await page.getByRole('progressbar').getAttribute('aria-valuenow'),'2');
 await page.getByRole('checkbox',{name:'우정',exact:true}).waitFor();
 assert.equal(await page.getByRole('button',{name:'장르·소재 선택 완료',exact:true}).isDisabled(),true);
 await page.getByRole('checkbox',{name:'우정',exact:true}).check();
 assert.equal(await page.getByRole('button',{name:'장르·소재 선택 완료',exact:true}).isDisabled(),true);
 await page.getByRole('checkbox',{name:'판타지',exact:true}).check();
 await snapshot(page,stem+'-q2');
 // Back alone retains selections; changing Q1 requires a new Q2 selection.
 await page.getByRole('button',{name:'이전 단계'}).click();
 assert.equal(await page.getByRole('list',{name:'선택한 작품',exact:true}).locator('li').count(),1);
 await advanceWorks(page);
 await page.getByRole('checkbox',{name:'우정',exact:true}).waitFor();
 assert.equal(await page.getByRole('checkbox',{name:'우정',exact:true}).isChecked(),true);
 await page.getByRole('button',{name:'이전 단계'}).click();
 await chooseWork(page,'구름 정원의 로봇');
 assert.equal(await page.getByRole('list',{name:'선택한 작품',exact:true}).locator('li').count(),2);
 assert.match(await page.locator('main').innerText(),/장르·소재 선택을 초기화/);
 await advanceWorks(page);
 await page.getByRole('checkbox',{name:'모험',exact:true}).waitFor();
 assert.equal(await page.locator('fieldset input[type=checkbox]:checked').count(),0);
 await page.getByRole('checkbox',{name:'모험',exact:true}).check();
 assert.equal(await page.getByRole('button',{name:'장르·소재 선택 완료',exact:true}).isDisabled(),true);
 await page.getByRole('checkbox',{name:'우정',exact:true}).check();await advanceTags(page);await headingFocused(page);
 assert.equal(await page.getByRole('progressbar').getAttribute('aria-valuenow'),'3');
 assert.equal(await page.getByRole('button',{name:'선택 완료',exact:true}).isDisabled(),true);
 await choose(page,'consumptionType','world');await page.keyboard.press('ArrowRight');
 await page.waitForFunction(()=>document.querySelector('input[name=consumptionType]:checked')?.value==='relationships');
 assert.equal(await page.locator('input[name=consumptionType]:checked').count(),1);
 assert.equal(await page.locator('input[name=consumptionType]:checked').locator('..').evaluate(e=>getComputedStyle(e).outlineWidth),'3px');
 await snapshot(page,stem+'-q3');
 await page.getByRole('button',{name:'이전 단계'}).click();
 await page.getByRole('checkbox',{name:'모험',exact:true}).waitFor();
 assert.equal(await page.getByRole('checkbox',{name:'모험',exact:true}).isChecked(),true);
 await advanceTags(page);
 assert.equal(await page.locator('input[name=consumptionType]:checked').inputValue(),'relationships');
 await choose(page,'consumptionType','ost');await advance(page);
 assert.match(await page.getByLabel('선택 답변 요약').innerText(),/관련 OST/);
 assert.match(await page.getByLabel('선택 답변 요약').innerText(),/별빛 우체국/);
 assert.match(await page.getByLabel('선택 답변 요약').innerText(),/구름 정원의 로봇/);
 assert.match(await page.getByLabel('선택 답변 요약').innerText(),/모험/);
 await snapshot(page,stem+'-result');
 await reselectFlow(page,stem);
 if (enterHome) {
  await page.waitForFunction(()=>!document.querySelector('button[aria-describedby="home-feedback"]').disabled);
  await page.clock.install(); await pauseClock(page);
  await page.getByRole('button',{name:'콘텐츠 보러가기'}).evaluate(button=>{button.click();button.click();});
  const phase=page.locator('[data-demo-loading-phase]');
  await phase.waitFor(); await headingFocused(page);
  assert.equal(await phase.getAttribute('data-demo-loading-phase'),'1');
  if(releaseFirstImage) {
   assert.equal(await phase.getAttribute('data-loading-image-state'),'pending');
   assert.equal(await page.getByText('시연용 화면 준비 중',{exact:true}).isVisible(),true);
   await page.clock.runFor(800);
   assert.equal(await phase.getAttribute('data-demo-loading-phase'),'1','Decode delay cannot consume the first 800ms frame');
   assert.equal(await page.locator('main article').count(),0);
   await page.screenshot({path:path.join(out,stem+'-image-pending.png'),fullPage:true});
   releaseFirstImage();
  }
  await page.locator('[data-loading-image-state="ready"]').waitFor();
  await checkLoadingGeometry(page,phase);
  assert.match(await page.getByRole('status').innerText(),/실제 콘텐츠 수집·추천·저장은 진행하지 않/);
  await snapshot(page,stem+'-loading-1');
  await phase.locator('img:not([hidden])').first().screenshot({path:path.join(out,stem+'-crop-1.png')});
  await page.clock.runFor(799); assert.equal(await phase.getAttribute('data-demo-loading-phase'),'1');
  await page.clock.runFor(1); assert.equal(await phase.getAttribute('data-demo-loading-phase'),motion==='reduce'?'1':'2');
  await checkLoadingGeometry(page,phase);
  await snapshot(page,stem+'-loading-2');
  await phase.locator('img:not([hidden])').first().screenshot({path:path.join(out,stem+'-crop-2.png')});
  await page.clock.runFor(800); assert.equal(await phase.getAttribute('data-demo-loading-phase'),motion==='reduce'?'1':'3');
  await checkLoadingGeometry(page,phase);
  await snapshot(page,stem+'-loading-3');
  await phase.locator('img:not([hidden])').first().screenshot({path:path.join(out,stem+'-crop-3.png')});
  await page.clock.runFor(799); assert.equal(await page.locator('main article').count(),0);
  assert.equal(await phase.count(),1,'One loading cycle despite duplicate result clicks');
  await page.clock.runFor(1);
  await page.locator('main[data-home-state="ready"]').waitFor();
  await headingFocused(page);
  assert.equal(await page.locator('main article').count(),3);
  assert.match(await page.getByLabel('시연용 피드 안내').innerText(),/실제 추천 결과가 아니/);
  assert.equal(await page.locator('[data-home-track]').count(),3,'H2-A gives each post a carousel');
  assert.deepEqual(await page.locator('[data-home-post]').evaluateAll(posts=>posts.map(post=>post.querySelectorAll('[data-home-slide]').length)),[5,3,2]);
  assert.equal(await page.locator('[data-home-detail]').count(),3);
  assert.equal(await page.locator('main article a').count(),0,'Pending Detail never links to a missing route');
  const titles = await page.locator('main article h3').allTextContents();
  await page.getByRole('searchbox',{name:'작품 이름',exact:true}).fill('검색하지 않는 입력');
  await page.keyboard.press('Enter');
  assert.deepEqual(await page.locator('main article h3').allTextContents(),titles,'Typing must not filter or recommend');
  assert.equal(new URL(page.url()).pathname,'/pivot/demo');
  await snapshot(page,stem+'-home');
  await page.clock.resume();
  return titles;
 }
 // This visual fixture stops before the real completion write; the separate
 // service integration check covers persisted signup and rotated sessions.
 await page.waitForFunction(()=>[...document.querySelectorAll('button')].some(button=>button.textContent==='가입 완료하고 콘텐츠 보기'&&!button.disabled));
 assert.equal(await page.getByRole('button',{name:'가입 완료하고 콘텐츠 보기',exact:true}).isEnabled(),true);
}
(async()=>{
 const browser=await chromium.launch({headless:true,channel:process.env.PIVOT_BROWSER_CHANNEL||'msedge'});
 try {
  for(const viewport of [{width:375,height:812},{width:320,height:568}]) {
   const context=await browser.newContext({viewport,deviceScaleFactor:Number(process.env.PIVOT_DPR||1),isMobile:true,hasTouch:true,reducedMotion:motion});
   const requests=[],external=[],errors=[];
   let standaloneSession=true;
   let releaseImage;
   let holdImage=true;
   const firstImageGate=new Promise(resolve=>{releaseImage=()=>{holdImage=false;resolve();};});
   await context.route('**/*',async route=>{
    const req=route.request(),url=new URL(req.url());
    if(url.pathname.startsWith('/api/')) {
     const headers={'Access-Control-Allow-Origin':base,'Access-Control-Allow-Credentials':'true','Access-Control-Allow-Headers':'content-type,x-csrf-token','Access-Control-Allow-Methods':'GET,POST,OPTIONS'};
     if(req.method()==='OPTIONS') {await route.fulfill({status:204,headers});return;}
     requests.push({method:req.method(),url:req.url()});
     const currentSession=standaloneSession?{...session,authenticated:false,sessionState:'anonymous',onboardingCompleted:false,user:null}:session;
     let payload=url.pathname==='/api/v1/auth/session'?currentSession:url.pathname==='/api/v1/policies'?{consentItems:policies}:null;
     if(url.pathname==='/api/v1/topics') payload={topics:[{id:900,code:'anime',name:'애니메이션'}]};
     if(url.pathname==='/api/v1/onboarding/works') payload={items:onboardingWorks.filter(work=>work.name.includes((url.searchParams.get('q')||'').trim())),nextCursor:null};
     if(url.pathname==='/api/v1/onboarding/preference-options') {
      const ids=req.postDataJSON().entityIds;
      const tags=onboardingTags.map(tag=>({...tag,sourceEntityIds:tag.sourceEntityIds.filter(id=>ids.includes(id))}));
      payload={entityIds:ids,genres:tags.filter(tag=>tag.kind==='genre'),themes:tags.filter(tag=>tag.kind==='theme'),minSelections:2,minGenreSelections:1,minThemeSelections:1,maxSelections:null,maxSelectionsPerKind:7};
     }
     await route.fulfill({status:payload?200:500,contentType:'application/json',headers,body:JSON.stringify(payload)});return;
    }
    if(url.origin!==new URL(base).origin) {external.push(req.url());await route.abort();return;}
    if(/^\/pivot\/loading\/phase-[123]\.png$/.test(url.pathname)) {
     if(url.pathname.endsWith('phase-1.png')&&holdImage) await firstImageGate;
     if(mode==='image-failure'&&url.pathname.endsWith('phase-1.png')) {
      await route.fulfill({status:200,contentType:'image/png',body:'invalid image fixture'});return;
     }
     if(process.env.PIVOT_ORIGINAL_LOADING_DIR) {
      await route.fulfill({path:path.join(process.env.PIVOT_ORIGINAL_LOADING_DIR,path.basename(url.pathname)),contentType:'image/png'});return;
     }
    }
    await route.continue();
   });
   await context.addInitScript(()=>{
    const keys=['menu:consumer-session','menu:consumer-flow','menu:home-view-mode:v2'];
    const originalGet=Storage.prototype.getItem,originalSet=Storage.prototype.setItem;
    for(const storage of [localStorage,sessionStorage]) for(const key of keys) originalSet.call(storage,key,'h1-preserve:'+key);
    window.__consumerAccess=[];
    window.__consumerIntact=()=>[localStorage,sessionStorage].every(storage=>keys.every(key=>originalGet.call(storage,key)==='h1-preserve:'+key));
    for(const method of ['getItem','setItem','removeItem','clear']) {
     const original=Storage.prototype[method];
     Storage.prototype[method]=function(...args){
      if(method==='clear'||keys.includes(String(args[0]))) window.__consumerAccess.push({method,key:args[0]});
      return original.apply(this,args);
     };
    }
    window.__pivotPushes=[];
    window.__splashTimers=[];
    const timeout=window.setTimeout.bind(window);
    window.setTimeout=function(callback,delay,...args){
     if(delay===3000) window.__splashTimers.push({at:performance.now(),delay});
     return timeout(callback,delay,...args);
    };
    const push=history.pushState.bind(history);
    history.pushState=function(...args){window.__pivotPushes.push(String(args[2]));return push(...args);};
   });
   const page=await context.newPage();page.setDefaultTimeout(10000);
   page.on('pageerror',e=>errors.push(e.message));
   page.on('console',message=>{if(message.type()==='error')errors.push(message.text());});
   const prefix=String(viewport.width);
   if(mode==='blocked-production') {
    const response=await page.goto(base+'/pivot/demo');
    assert.equal(response.status(),404,'Production blocks demo even with opt-in');
    assert.equal(await page.getByLabel('시연용 데모 안내').count(),0);
    const preview=await page.goto(base+'/pivot/preview?screen=result');
    assert.equal(preview.status(),404);
    await page.goto(base+'/pivot/home');
    await page.locator('main[data-home-state="unconfigured"]').waitFor();
    assert.equal(await page.locator('main article').count(),0);
    assert.deepEqual(requests,[]); assert.deepEqual(external,[]); assert.deepEqual(errors,[]);
    results.push({viewport,scenario:'Production demo/preview HTTP 404; service Home isolated',apiRequests:0,status:'pass'});
    await context.close(); continue;
   }
   if(mode==='image-failure') {
    await page.goto(base+'/pivot/demo'); await quickResult(page);
    await page.clock.install(); await pauseClock(page);
    await page.getByRole('button',{name:'콘텐츠 보러가기'}).click();
    await page.locator('[data-loading-image-state="pending"]').waitFor();
    await page.getByRole('button',{name:'처음부터 시연'}).click(); await headingFocused(page);
    releaseImage(); await page.clock.runFor(3400);
    await page.getByRole('button',{name:'로그인 없이 시연 계속하기'}).waitFor();
    await page.clock.runFor(5000);
    assert.equal(await page.locator('main article').count(),0,'Late image failure after unmount cannot open Home');
    await page.clock.resume(); await quickResult(page); await pauseClock(page);
    await page.getByRole('button',{name:'콘텐츠 보러가기'}).click();
    await page.locator('[data-loading-image-state="failed"]').waitFor();
    assert.equal(await page.getByText('이미지 없이 시연을 계속해요',{exact:true}).isVisible(),true);
    await page.clock.runFor(2399); assert.equal(await page.locator('main article').count(),0);
    await page.clock.runFor(1); await page.locator('main[data-home-state="ready"]').waitFor();
    assert.deepEqual(requests,[]); assert.deepEqual(external,[]); assert.deepEqual(errors,[]);
    assert.deepEqual(await page.evaluate(()=>window.__consumerAccess),[]);
    assert.equal(await page.evaluate(()=>window.__consumerIntact()),true);
    results.push({viewport,scenario:'pending-image restart and stale decode cleanup; failed-image fallback reaches Mock Home',apiRequests:0,status:'pass'});
    await context.close(); continue;
   }
   if(mode==='watchdog') {
    await page.goto(base+'/pivot/demo'); await quickResult(page);
    await page.clock.install(); await pauseClock(page);
    await page.evaluate(()=>{
     window.__holdFirstDecode=true; window.__decodeCalls=0; window.__decodeReleases=[]; window.__homeEntries=0;
     window.__releaseDecode=()=>{window.__holdFirstDecode=false;window.__decodeReleases.splice(0).forEach(resolve=>resolve());};
     const decode=HTMLImageElement.prototype.decode;
     HTMLImageElement.prototype.decode=function(){
      const decoded=decode.call(this);
      if(this.getAttribute('src')==='/pivot/loading/phase-1.png'&&window.__holdFirstDecode) {
       window.__decodeCalls++;
       const held=new Promise(resolve=>window.__decodeReleases.push(resolve));
       return Promise.all([decoded,held]).then(()=>{});
      }
      return decoded;
     };
     let wasHome=false;
     new MutationObserver(()=>{
      const isHome=!!document.querySelector('main[data-home-state="ready"]');
      if(isHome&&!wasHome) window.__homeEntries++;
      wasHome=isHome;
     }).observe(document,{childList:true,subtree:true,attributes:true,attributeFilter:['data-home-state']});
    });
    const phase=page.locator('[data-demo-loading-phase]');
    // The first network response remains held past the watchdog deadline.
    await page.getByRole('button',{name:'콘텐츠 보러가기'}).click();
    await phase.waitFor(); await page.clock.runFor(2999);
    assert.equal(await phase.getAttribute('data-loading-image-state'),'pending');
    await page.clock.runFor(1);
    assert.equal(await phase.getAttribute('data-loading-image-state'),'failed');
    assert.equal(await page.getByText('이미지 없이 시연을 계속해요',{exact:true}).isVisible(),true);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await page.screenshot({path:path.join(out,prefix+'-watchdog-fallback.png'),fullPage:true});
    await page.clock.runFor(500);
    const delivered=page.waitForResponse(response=>new URL(response.url()).pathname==='/pivot/loading/phase-1.png');
    releaseImage(); await delivered;
    await page.waitForFunction(()=>document.querySelector('img[src="/pivot/loading/phase-1.png"]')?.complete);
    assert.equal(await phase.getAttribute('data-loading-image-state'),'failed');
    assert.equal(await page.evaluate(()=>window.__decodeCalls),0,'Late load events must not trigger decode after timeout');
    await page.clock.runFor(1899); assert.equal(await page.locator('main article').count(),0);
    await page.clock.runFor(1); await page.locator('main[data-home-state="ready"]').waitFor();
    await page.clock.runFor(10000); assert.equal(await page.evaluate(()=>window.__homeEntries),1);
    // A decoded image whose promise never settles follows the same bounded path.
    await page.getByRole('button',{name:'처음부터 시연'}).click(); await headingFocused(page);
    await page.clock.runFor(3300); await page.clock.resume(); await quickResult(page); await pauseClock(page);
    const decodeBeforeHung=await page.evaluate(()=>window.__decodeCalls);
    await page.getByRole('button',{name:'콘텐츠 보러가기'}).click();
    // Cached images can decode twice under React dev StrictMode.
    await page.waitForFunction(before=>window.__decodeCalls>before,decodeBeforeHung);
    await page.clock.runFor(2999); assert.equal(await phase.getAttribute('data-loading-image-state'),'pending');
    await page.clock.runFor(1); assert.equal(await phase.getAttribute('data-loading-image-state'),'failed');
    await page.clock.runFor(500); await page.evaluate(()=>window.__releaseDecode());
    assert.equal(await phase.getAttribute('data-loading-image-state'),'failed','Late decode cannot reset the timer');
    await page.clock.runFor(1899); assert.equal(await page.locator('main article').count(),0);
    await page.clock.runFor(1); await page.locator('main[data-home-state="ready"]').waitFor();
    await page.clock.runFor(10000); assert.equal(await page.evaluate(()=>window.__homeEntries),2);
    // Restart before the deadline, then deliver the old promise and advance every old timer.
    await page.getByRole('button',{name:'처음부터 시연'}).click(); await headingFocused(page);
    await page.clock.runFor(3300); await page.clock.resume(); await quickResult(page); await pauseClock(page);
    await page.evaluate(()=>{window.__holdFirstDecode=true;});
    const decodeBeforeRestart=await page.evaluate(()=>window.__decodeCalls);
    await page.getByRole('button',{name:'콘텐츠 보러가기'}).click();
    await page.waitForFunction(before=>window.__decodeCalls>before,decodeBeforeRestart);
    await page.clock.runFor(1000); await page.getByRole('button',{name:'처음부터 시연'}).click();
    await headingFocused(page); await page.evaluate(()=>window.__releaseDecode());
    await page.clock.runFor(3300); await page.getByRole('button',{name:'로그인 없이 시연 계속하기'}).waitFor();
    await page.clock.runFor(10000);
    assert.equal(await page.locator('main article').count(),0);
    assert.equal(await page.evaluate(()=>window.__homeEntries),2,'Exactly one Home entry per completed cycle');
    assert.deepEqual(requests,[]); assert.deepEqual(external,[]); assert.deepEqual(errors,[]);
    assert.deepEqual(await page.evaluate(()=>window.__consumerAccess),[]);
    assert.equal(await page.evaluate(()=>window.__consumerIntact()),true);
    results.push({viewport,motion,scenario:'3000ms watchdog for hung image/decode; late callbacks ignored; single Home per cycle; restart cleanup',apiRequests:0,status:'pass'});
    await context.close(); continue;
   }
   // Standalone /pivot must use its router.replace path, independently of DemoFlow.
   await page.goto(base+'/pivot');
   await page.waitForFunction(()=>window.__splashTimers.length>0);
   const splashStart=await page.evaluate(()=>window.__splashTimers.at(-1));
   assert.equal(splashStart.delay,3000);
   await page.waitForFunction(at=>performance.now()>=at+2500,splashStart.at);
   assert.equal(new URL(page.url()).pathname,'/pivot','Standalone Splash must not leave early');
   await page.waitForURL('**/pivot/login');
   assert.ok(await page.evaluate(at=>performance.now()-at>=3000,splashStart.at));
   await page.getByRole('button',{name:'카카오 로그인'}).waitFor();
   const standaloneRequests=requests.splice(0);
   assert.ok(standaloneRequests.every(request=>request.method==='GET'&&new URL(request.url).pathname==='/api/v1/auth/session'));
   results.push({viewport,motion,scenario:'standalone /pivot waits 3000ms then router-replaces to /pivot/login',apiRequests:standaloneRequests,status:'pass'});
   standaloneSession=false;
   await page.goto(base+'/pivot/demo');
   await snapshot(page,prefix+'-demo-splash');
   await page.getByRole('button',{name:'로그인 없이 시연 계속하기'}).waitFor();
   await headingFocused(page);
   await snapshot(page,prefix+'-demo-login');
   await page.keyboard.press('Tab');
   assert.equal(await page.evaluate(()=>document.activeElement?.tagName),'BUTTON');
   assert.equal(await page.evaluate(()=>getComputedStyle(document.activeElement).outlineWidth),'3px');
   await page.getByRole('button',{name:'카카오 로그인'}).click();
   assert.match(await page.locator('#login-feedback').innerText(),/실제 로그인은 진행되지 않/);
   await page.getByRole('button',{name:'네이버 로그인'}).click();
   assert.match(await page.locator('#login-feedback').innerText(),/실제 로그인은 진행되지 않/);
   await page.getByRole('button',{name:'로그인 없이 시연 계속하기'}).click();
   await headingFocused(page);
   assert.equal(await page.getByRole('button',{name:'시작하기',exact:true}).isDisabled(),true);
   await page.getByRole('checkbox',{name:'서비스 설명 및 이용약관',exact:true}).check();
   assert.equal(await page.getByRole('checkbox',{name:'모두 동의합니다'}).evaluate(e=>e.indeterminate),true);
   assert.equal(await page.getByRole('button',{name:'시작하기',exact:true}).isDisabled(),true);
   await page.getByRole('button',{name:'서비스 설명 및 이용약관 내용 보기'}).click();
   await page.getByRole('dialog').waitFor();
   assert.match(await page.getByRole('dialog').innerText(),/demo-only/);
   await page.keyboard.press('Escape');
   assert.equal(await page.getByRole('dialog').count(),0);
   await page.getByRole('checkbox',{name:'개인정보 수집 및 이용동의',exact:true}).check();
   await snapshot(page,prefix+'-demo-consent');
   await page.getByRole('button',{name:'시작하기',exact:true}).evaluate(b=>{b.click();b.click();});
   const homeTitles=await questionFlow(page,prefix+'-demo',true,releaseImage);
   assert.deepEqual(requests,[],'Demo must make zero API requests');
   await page.evaluate(()=>window.dispatchEvent(new Event('api:unauthorized')));
   assert.equal(await page.locator('main[data-home-state="ready"]').count(),1,'Demo Home is isolated from service session events');
   await page.getByRole('button',{name:'처음부터 시연'}).click();
   await headingFocused(page);
   assert.match(await page.locator('main h1').innerText(),/me;nu/);
   await page.keyboard.press('Tab');
   assert.equal(await page.getByRole('button',{name:'로그인 화면으로 건너뛰기'}).evaluate(node=>node===document.activeElement),true);
   assert.equal(await page.evaluate(()=>getComputedStyle(document.activeElement).outlineWidth),'3px');
   await page.getByRole('button',{name:'로그인 없이 시연 계속하기'}).click();
   assert.equal(await page.getByRole('checkbox',{name:'모두 동의합니다'}).isChecked(),false);
   assert.equal(await page.getByRole('button',{name:'시작하기',exact:true}).isDisabled(),true);
   // Already at consent after the first restart; return to login before the shared helper.
   await page.goto(base+'/pivot/demo');
   await quickResult(page); await pauseClock(page);
   await page.getByRole('button',{name:'콘텐츠 보러가기'}).click();
   await page.locator('[data-demo-loading-phase="1"]').waitFor();
   await page.clock.runFor(800);
   await page.getByRole('button',{name:'처음부터 시연'}).click();
   await page.getByRole('heading',{name:'me;nu, 당신의 덕질을 위한 시작'}).waitFor();
   await headingFocused(page);
   await page.clock.runFor(3000);
   if(motion!=='reduce') {
    const animation=await page.locator('main').locator('..').evaluate(node=>getComputedStyle(node).animationName);
    assert.match(animation,/splash-dissolve/);
    await page.clock.runFor(300);
   }
   await page.getByRole('button',{name:'로그인 없이 시연 계속하기'}).waitFor();
   await page.clock.runFor(5000);
   assert.equal(await page.locator('main article').count(),0,'Interrupted loading cannot open Home later');
   await page.clock.resume();
   await quickResult(page);
   await page.getByRole('button',{name:'콘텐츠 보러가기'}).click();
   await page.locator('[data-demo-loading-phase="1"]').waitFor();
   await page.locator('main[data-home-state="ready"]').waitFor();
   assert.equal(await page.getByRole('searchbox',{name:'작품 이름',exact:true}).inputValue(),'','Restart clears Home input');
   assert.deepEqual(await page.locator('main article h3').allTextContents(),homeTitles,'Different answers must not personalize Mock');
   assert.deepEqual(requests,[]);
   assert.deepEqual(await page.evaluate(()=>window.__consumerAccess),[]);
   assert.equal(await page.evaluate(()=>window.__consumerIntact()),true);
   results.push({viewport,motion,scenario:'result → three-phase loading → Home; duplicate clicks, mid-loading restart, fresh cycle, Consumer isolation',apiRequests:requests.length,status:'pass'});
   await page.goto(base+'/pivot/home');
   await page.locator('main[data-home-state="unconfigured"]').waitFor();
   assert.equal(await page.locator('main article').count(),0,'Service must not render Mock cards');
   assert.equal(await page.getByLabel('시연용 피드 안내').count(),0);
   await page.getByRole('searchbox',{name:'작품 이름',exact:true}).fill('서비스 입력');
   await page.keyboard.press('Enter');
   await page.evaluate(()=>window.dispatchEvent(new Event('api:unauthorized')));
   assert.equal(await page.locator('main[data-home-state="unconfigured"]').count(),1);
   await snapshot(page,prefix+'-service-home');
   assert.deepEqual(requests,[],'Disconnected Home makes zero API requests, including session reads');
   assert.deepEqual(await page.evaluate(()=>window.__consumerAccess),[]);
   assert.equal(await page.evaluate(()=>window.__consumerIntact()),true);
   results.push({viewport,scenario:'service Home unconfigured; no Mock fallback; input only; API and Consumer isolation',apiRequests:0,status:'pass'});
   // Every design preview remains reachable and network-isolated.
   if(mode !== 'production' && mode !== 'approved-preview') {
   for(const screen of ['splash','login','consent','interest','q1','q2','q3','result']) {
    await page.goto(base+'/pivot/preview?screen='+screen);
    await page.locator('main h1').waitFor();await snapshot(page,prefix+'-preview-'+screen);
   }
   assert.deepEqual(requests,[]);
   results.push({viewport,scenario:'all eight preview screens',apiRequests:0,status:'pass'});
   }
   if(mode === 'development' || mode === 'approved-preview') {
    await page.goto(base+'/pivot/consent');
    await page.getByRole('heading',{name:'로그인 확인'}).waitFor();
    assert.equal(requests.length,0,'Default development configuration must not read APIs');
    assert.deepEqual(external,[]);assert.deepEqual(errors,[]);
    results.push({viewport,scenario:'API disabled service gate',apiRequests:0,status:'pass'});
    await context.close();continue;
   }
   await page.goto(base+'/pivot/onboarding');
   await page.getByRole('heading',{name:'온보딩 확인'}).waitFor();
   assert.equal(await page.getByRole('searchbox',{name:'작품 검색',exact:true}).count(),0,'Missing consent draft blocks onboarding');
   await page.goto(base+'/pivot/consent');
   await page.getByRole('checkbox',{name:'마케팅 수신 동의',exact:true}).waitFor();
   await page.getByRole('checkbox',{name:'서비스 설명 및 이용약관',exact:true}).check();
   await page.getByRole('checkbox',{name:'개인정보 수집 및 이용동의',exact:true}).check();
   assert.equal(await page.getByRole('checkbox',{name:'마케팅 수신 동의',exact:true}).isChecked(),false);
   assert.equal(await page.getByRole('button',{name:'시작하기',exact:true}).isEnabled(),true);
   await page.getByRole('button',{name:'시작하기',exact:true}).evaluate(b=>{b.click();b.click();});
   await page.waitForURL('**/pivot/onboarding');
   await page.getByRole('textbox',{name:'닉네임',exact:true}).fill('브라우저 검증 회원');
   assert.equal(await page.locator('input[type=date]').count(),0,'Birth-date collection remains deferred');
   await page.getByRole('button',{name:'취향 선택하기',exact:true}).click();
   await questionFlow(page,prefix+'-service');
   const serviceText=await page.locator('main').innerText();
   assert.doesNotMatch(serviceText,/시연|미리보기|피드 연결 대기|새 Home/);
   const pushes=await page.evaluate(()=>window.__pivotPushes.filter(u=>u.includes('/pivot/onboarding')));
   assert.equal(pushes.length,1,'Consent double click must cause one route transition');
   assert.ok(requests.some(r=>r.url.endsWith('/api/v1/auth/session')),'Service control proves local API opt-in is on');
   assert.ok(requests.some(r=>r.url.endsWith('/api/v1/policies')));
   assert.ok(requests.filter(r=>r.method!=='GET').every(r=>r.method==='POST'&&new URL(r.url).pathname==='/api/v1/onboarding/preference-options'),'Only read-only candidate POSTs occur before explicit completion');
   await page.evaluate(()=>window.dispatchEvent(new Event('api:unauthorized')));
   await page.getByRole('heading',{name:'온보딩 확인'}).waitFor();
   assert.equal(await page.getByLabel('선택 답변 요약').count(),0);
   assert.deepEqual(external,[]);assert.deepEqual(errors,[]);
   results.push({viewport,scenario:'service with intercepted local session/policies, consent double click, expiry',apiRequests:requests,status:'pass'});
   await context.close();
  }
 } finally {await browser.close();fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify(results,null,2));}
 console.log(JSON.stringify(results,null,2));
})().catch(e=>{console.error(e);process.exitCode=1;});
