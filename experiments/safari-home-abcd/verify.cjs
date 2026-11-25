require('./net-guard.cjs');
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const {root,origin,variants,playwright}=require('./config.cjs');
const {chromium}=require(playwright);
const {request:localRequest}=require('./local-client.cjs');
const certificate=JSON.parse(fs.readFileSync(path.join(root,'results/certificates.json')));
const manifest=JSON.parse(fs.readFileSync(path.join(root,'fixtures/manifest.json')));
const expected=JSON.parse(fs.readFileSync(path.join(root,'fixtures/contents.json'))).map(c=>String(c.id));
const cases=[],network=[],probes=[];
const isImage=url=>/(?:\/slides\/\d+\/image|\/_next\/image|\.(?:png|jpe?g|gif|webp|avif|svg|ico|bmp)(?:[?#]|$))/i.test(url);
function requestStats(rows){
 return {total:rows.length,apiRequests:rows.filter(r=>new URL(r.url).pathname.startsWith('/api/')).length,
 apiJsonRequests:rows.filter(r=>new URL(r.url).pathname.startsWith('/api/')&&!isImage(r.url)).length,
 imageRequests:rows.filter(r=>isImage(r.url)||r.type==='image').length,protectedImageRequests:rows.filter(r=>/\/slides\/\d+\/image/.test(r.url)).length,
 feedRequests:rows.filter(r=>new URL(r.url).pathname==='/api/v1/topics/7/feed').length,
 byUrl:rows.reduce((out,r)=>{const k=r.method+' '+r.url;out[k]=(out[k]||0)+1;return out;},{})};
}
async function run(browser,variant,width){
 const context=await browser.newContext({viewport:{width,height:width<600?844:1000},isMobile:width<600,hasTouch:width<600,reducedMotion:'reduce',serviceWorkers:'block'});
 const external=[],errors=[],consoleErrors=[],feeds=[],responses=[];let phase='normal',step='initial';
 await context.route('**/*',route=>{const r=route.request();if(new URL(r.url()).origin!==origin){external.push(r.url());return route.abort('blockedbyclient');}return route.continue();});
 await context.addInitScript(()=>{window.__labCsp=[];document.addEventListener('securitypolicyviolation',event=>window.__labCsp.push({uri:event.blockedURI,directive:event.effectiveDirective}));});
 const page=await context.newPage();page.setDefaultTimeout(20000);
 page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error'&&phase==='normal')consoleErrors.push(m.text());});
 page.on('request',r=>network.push({variant,width,phase,step,url:r.url(),method:r.method(),type:r.resourceType()}));
 page.on('response',response=>{const u=new URL(response.url());if(u.pathname==='/api/v1/topics/7/feed')responses.push((async()=>{const body=await response.json();feeds.push({url:u.href,limit:Number(u.searchParams.get('limit')),cursor:u.searchParams.get('cursor'),nextCursor:body.nextCursor,ids:body.sections.flatMap(s=>s.contents.map(c=>String(c.id))),bodySha256:crypto.createHash('sha256').update(JSON.stringify(body)).digest('hex')});})());});
 const cards=page.locator('[data-home-post]'),home=page.locator('[data-home-state]');
 const cardIds=()=>cards.evaluateAll(rows=>rows.map(r=>r.dataset.homePost));
 try{
  const homeResponse=await page.goto(origin+'/__lab/start/'+variant+'?slides=10',{waitUntil:'domcontentloaded'});
  await page.locator('[data-home-state="ready"]').waitFor();await page.locator('[data-home-entry-loading]').waitFor({state:'detached'});
  if(variant!=='B')await page.waitForFunction(()=>document.querySelector('[data-home-post] img')?.naturalWidth===1308);
  const policy=homeResponse.headers()['content-security-policy'];
  assert.ok(policy.includes("connect-src 'self'"));assert.ok(policy.includes(variant==='B'?"img-src 'none'":"img-src 'self' blob: data:"));
  assert.equal(homeResponse.headers()['access-control-allow-origin'],undefined);
  const sessionCookie=(await context.cookies(origin)).find(c=>c.name==='__Host-lab4tls_session');
  assert.ok(sessionCookie&&sessionCookie.secure&&sessionCookie.httpOnly&&sessionCookie.sameSite==='Lax'&&sessionCookie.path==='/'&&sessionCookie.domain==='127.0.0.1');
  assert.equal(await page.evaluate(()=>document.cookie.includes('__Host-lab4tls_session')),false);
  assert.equal(await page.evaluate(()=>isSecureContext),true);
  const cookieFlags={secure:sessionCookie.secure,httpOnly:sessionCookie.httpOnly,sameSite:sessionCookie.sameSite,path:sessionCookie.path,domain:sessionCookie.domain};
  await Promise.all(responses);
  assert.equal(feeds.length,1);assert.equal(feeds[0].limit,variants[variant].pageSize);assert.deepEqual(feeds[0].ids,expected.slice(0,variants[variant].pageSize));
  assert.deepEqual(await cardIds(),expected.slice(0,variant==='C'?1:variants[variant].pageSize));
  assert.equal(Number(await page.locator('[data-lab-received-cards]').getAttribute('data-lab-received-cards')),variants[variant].pageSize);
  await page.waitForTimeout(400);
  const initial=await page.evaluate(()=>({cards:document.querySelectorAll('[data-home-post]').length,slides:document.querySelectorAll('[data-home-post] [data-carousel-slide]').length,
    imgElements:document.querySelectorAll('img').length,svgElements:document.querySelectorAll('svg').length,elements:document.querySelectorAll('*').length,
    imagePreloads:document.querySelectorAll('link[rel="preload"][as="image"]').length,metrics:structuredClone(window.__labImages)}));
  if(variant==='B'){assert.equal(initial.imgElements,0);assert.equal(initial.svgElements,0);assert.equal(initial.imagePreloads,0);assert.ok(Object.values(initial.metrics.native).every(n=>n===0));}
  else{assert.ok(initial.metrics.native.responseBlob>0);assert.ok(initial.metrics.native.objectUrl>0);assert.ok(initial.metrics.native.decode>0);}
  if(variant==='C'){
    const imageIds=network.filter(r=>r.variant===variant&&r.width===width).map(r=>r.url.match(/\/contents\/(\d+)\/slides\//)?.[1]).filter(Boolean);
    assert.ok(imageIds.length>0&&imageIds.every(id=>id===expected[0]));
  }
  const initialRequests=requestStats(network.filter(r=>r.variant===variant&&r.width===width&&r.step==='initial'));
  await page.screenshot({path:path.join(root,'results/initial-'+variant+'-'+width+'.png')});step='pagination';
  const firstTrack=cards.first().locator('[data-carousel-track]');await firstTrack.focus();await firstTrack.press('ArrowRight');
  await page.waitForFunction(()=>document.querySelector('[data-home-post] [data-home-position]')?.textContent.includes('2 / 10'));
  // All conditions keep requests/pagination, including C: retained data grows while one card remains mounted.
  while(await page.locator('[data-home-pagination] button').count()){
    const received=Number(await page.locator('[data-lab-received-cards]').getAttribute('data-lab-received-cards'));
    await page.locator('[data-home-pagination] button').click();
    await page.waitForFunction(n=>Number(document.querySelector('[data-lab-received-cards]')?.getAttribute('data-lab-received-cards'))>n,received);
  }
  await Promise.all(responses);assert.deepEqual(feeds.flatMap(f=>f.ids),expected);assert.ok(feeds.every(f=>f.limit===variants[variant].pageSize));
  assert.deepEqual(await cardIds(),variant==='C'?expected.slice(0,1):expected);
  step='navigation';
  const detailButton=cards.first().locator('[data-home-detail]');await detailButton.scrollIntoViewIfNeeded();await detailButton.focus();await detailButton.click();
  const detail=page.locator('[data-detail-state="ready"]:visible');await detail.waitFor();
  await detail.getByRole('button',{name:'저장',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('[data-detail-state="ready"] button[aria-label="저장"]')?.getAttribute('aria-pressed')==='true');
  await detail.getByRole('button',{name:'뒤로가기',exact:true}).click();assert.equal(await detailButton.evaluate(el=>el===document.activeElement),true);
  await home.getByRole('button',{name:'SAVE',exact:true}).click();await page.locator('[data-saved-state="ready"]:visible').waitFor();assert.equal(await page.locator('[data-saved-content]').count(),1);
  await page.getByRole('button',{name:'HOME',exact:true}).click();await home.getByRole('button',{name:'MY',exact:true}).click();await page.locator('[data-my-state="ready"]:visible').waitFor();
  await page.getByRole('button',{name:'HOME',exact:true}).click();assert.deepEqual(await cardIds(),variant==='C'?expected.slice(0,1):expected);
  assert.equal(await cards.first().locator('[data-service-bookmark]').getAttribute('aria-pressed'),'true');
  const finalDom=await page.evaluate(()=>({elements:document.querySelectorAll('*').length,cards:document.querySelectorAll('[data-home-post]').length,imgElements:document.querySelectorAll('img').length}));
  const finalMetrics=await page.evaluate(()=>structuredClone(window.__labImages));
  const normalCsp=await page.evaluate(()=>window.__labCsp);assert.deepEqual(normalCsp,[]);
  const normalRequests=requestStats(network.filter(r=>r.variant===variant&&r.width===width&&r.phase==='normal'));
  const imageRequests=network.filter(r=>r.variant===variant&&r.width===width&&r.phase==='normal'&&(isImage(r.url)||r.type==='image'));
  if(variant==='B'){assert.equal(imageRequests.length,0);assert.ok(Object.values(finalMetrics.native).every(n=>n===0));assert.equal(finalMetrics.imagePreloadNodes,0);}
  assert.deepEqual(errors,[]);assert.deepEqual(consoleErrors,[]);assert.deepEqual(external,[]);
  cases.push({variant,width,mode:variants[variant].mode,status:'pass',initial,initialRequests,normalRequests,finalDom,cookieFlags,csp:policy,normalCsp,feeds,finalCards:await cards.count(),retainedCards:123,normalImageRequests:imageRequests.length,finalMetrics,consoleErrors:[...consoleErrors],pageErrors:[...errors],fixtureSha256:manifest.contentSha256,
    checks:['initial limit and raw feed IDs','all 123 fetched in order without duplicates','rendered card count','horizontal carousel','DETAIL/HOME focus','SAVE/MY','no external requests']});
  console.log('PASS '+variant+' '+width+': request '+feeds[0].ids.length+', initial cards '+initial.cards+', images '+imageRequests.length+', final DOM cards '+await cards.count());
  if(variant==='B'&&width===390){phase='intentional-block-probes';step='probe';
    const result=await page.evaluate(async()=>{window.__labImages.phase='probe';const checks={};
      for(const [name,fn]of Object.entries({fetch:()=>fetch('/api/v1/contents/1000/slides/1/image?topicId=7'),blobRead:()=>new Response('probe').blob(),blob:()=>new Blob(['probe']),objectUrl:()=>URL.createObjectURL({}),decode:()=>document.createElement('img').decode(),image:()=>new Image(),bitmap:()=>createImageBitmap({})}))try{await fn();checks[name]=false;}catch{checks[name]=true;}
      const image=document.createElement('img');image.src='/api/v1/contents/1000/slides/1/image?topicId=7';checks.src=image.getAttribute('src')===null;
      return{checks,metrics:structuredClone(window.__labImages)};});
    assert.ok(Object.values(result.checks).every(Boolean));assert.ok(Object.values(result.metrics.native).every(n=>n===0));probes.push({kind:'B-native-pipeline-blocked',...result});
    const cookie=(await context.cookies(origin)).find(c=>c.name==='__Host-lab4tls_session');
    const refused=await localRequest('/api/v1/contents/1000/slides/1/image?topicId=7',{headers:{Cookie:cookie.name+'='+cookie.value}});assert.equal(refused.status,403);probes.push({kind:'B-server-image-denied',status:403});
    const undefinedApi=await page.evaluate(async()=>{const r=await fetch('/api/v1/not-defined');return{status:r.status,body:await r.json()};});assert.equal(undefinedApi.status,404);probes.push({kind:'undefined-api-no-fallback',...undefinedApi});
    const cspBlocked=await page.evaluate(async()=>{try{await fetch('https://external-probe.invalid/never-connect');return false;}catch{return true;}});assert.equal(cspBlocked,true);await page.waitForFunction(()=>window.__labCsp.some(v=>v.uri==='https://external-probe.invalid/never-connect'&&v.directive==='connect-src'));
    assert.deepEqual(external,[]);probes.push({kind:'external-CSP-block',passed:true,violations:await page.evaluate(()=>window.__labCsp)});
  }
 }catch(error){cases.push({variant,width,status:'fail',error:error.stack,errors,consoleErrors,external,feeds});await page.screenshot({path:path.join(root,'results/failure-'+variant+'-'+width+'.png')});throw error;}
 finally{await context.close();}
}
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome',args:['--ignore-certificate-errors-spki-list='+certificate.server.spkiSha256,'--disable-background-networking','--disable-component-update','--disable-sync','--no-first-run','--disable-default-apps','--disable-domain-reliability','--disable-client-side-phishing-detection','--no-pings','--disable-quic','--proxy-server=http://127.0.0.1:9','--proxy-bypass-list=127.0.0.1']});
 try{
  for(const width of [375,390])for(const variant of Object.keys(variants))await run(browser,variant,width);
  for(const width of [375,390]){
    const abc=cases.filter(r=>r.width===width&&r.variant!=='D');assert.equal(new Set(abc.map(r=>r.feeds[0].bodySha256)).size,1,'A/B/C must receive byte-equivalent feed JSON');
  }
 }finally{await browser.close();fs.writeFileSync(path.join(root,'results/browser-requests.jsonl'),network.map(r=>JSON.stringify(r)).join('\n')+'\n');fs.writeFileSync(path.join(root,'results/verification.json'),JSON.stringify({cases,probes,passed:cases.filter(r=>r.status==='pass').length===8,scope:'Windows Chrome HTTPS isolation. Leaf SPKI exception only; independent TLS chain/SAN verification. No system trust change, iPhone/Safari memory measurement or OS-wide packet capture.'},null,2));}
})().catch(error=>{console.error(error);process.exitCode=1;});
