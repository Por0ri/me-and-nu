// Existing Playwright + Edge only. No forced clicks, keyboard substitutes or external calls.
// Fourth argument "before" records the same pointer obstruction on an unchanged baseline.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const base = process.argv[2] || 'http://127.0.0.1:3112';
const out = process.argv[3] || path.join(require('node:os').tmpdir(), 'pivot-notice');
const before = process.argv[4] === 'before';
fs.mkdirSync(out, { recursive: true });
const results = [];
const pending = scope => scope.locator('[role="status"][class*="pendingNotice"]');
const home = page => page.locator('[data-home-state="ready"]');
const my = page => page.locator('[data-my-state="ready"]');
const saved = page => page.locator('[data-saved-state="ready"]');
const button = (scope, name) => scope.getByRole('button', { name, exact: true });
async function ready(page, scope) {
  await scope.waitFor({ state: 'visible' });
  await page.evaluate(() => document.fonts.ready);
  await page.waitForFunction(() => document.activeElement?.matches('main h1'));
}
async function open(page, view) {
  await page.goto(base + '/pivot/demo?view=' + view);
  await ready(page, view === 'home' ? home(page) : my(page));
}
async function hit(target) {
  return target.evaluate(e => {
    const r = e.getBoundingClientRect();
    return [[.5,.5],[.2,.5],[.8,.5],[.5,.2],[.5,.8]].map(([dx,dy]) => {
      const x = r.x + r.width * dx, y = r.y + r.height * dy, top = document.elementFromPoint(x,y);
      return { x, y, target: e.contains(top), hit: top?.closest('button')?.getAttribute('aria-label') || top?.textContent };
    });
  });
}
async function pointer(page, target, evidence, label, allowBlocked = false) {
  const points = await hit(target);
  evidence.clicks.push({ label, points });
  if (!allowBlocked) assert.ok(points.every(p => p.target), label + ': ' + JSON.stringify(points));
  await page.mouse.click(points[0].x, points[0].y);
  return points;
}
async function place(page, target, y) {
  await target.evaluate((e, y) => {
    const r = e.getBoundingClientRect();
    scrollTo({ top: scrollY + r.y + r.height / 2 - y, behavior: 'instant' });
  }, y);
  await page.waitForTimeout(80);
}
async function noticeEvidence(scope) {
  return scope.locator('[role="status"]').evaluateAll(items => items.filter(e => e.getBoundingClientRect().width > 1).map(e => {
    const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    const range = document.createRange(); range.selectNodeContents(e);
    return { text: e.textContent, rect: r.toJSON(), position: s.position, pointerEvents: s.pointerEvents,
      role: e.getAttribute('role'), ariaLive: e.getAttribute('aria-live'),
      clipped: e.scrollWidth > e.clientWidth || e.scrollHeight > e.clientHeight,
      textInside: [...range.getClientRects()].every(t => t.left >= r.left && t.right <= r.right + 1 && t.top >= r.top && t.bottom <= r.bottom + 1) };
  }));
}
async function layout(page, scope, evidence, label) {
  const notices = await noticeEvidence(scope);
  assert.ok(notices.length > 0);
  for (const n of notices) {
    assert.equal(n.clipped, false, label); assert.equal(n.textInside, true, label);
    assert.ok(n.rect.left >= 0 && n.rect.right <= page.viewportSize().width, label);
    if (n.position === 'fixed') {
      assert.ok(n.rect.top >= 44 && n.rect.bottom < page.viewportSize().height - 60, label);
      assert.equal(n.pointerEvents, 'none', label);
    }
  }
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  evidence.notices.push({ label, notices });
}
async function screenshot(page, name) {
  await page.screenshot({ path: path.join(out, name + '.png'), style: 'nextjs-portal { visibility: hidden !important; }' });
}
async function waitSlide(post, index) {
  await post.locator('[data-home-position]').filter({ hasText: `${index + 1} /` }).waitFor();
  await post.locator('[data-home-track]').evaluate(async e => {
    let last = -1, count = 0;
    for (let i = 0; i < 100; i++) {
      await new Promise(r => setTimeout(r, 30));
      count = Math.abs(e.scrollLeft - last) < .1 ? count + 1 : 0;
      if (count >= 4) return;
      last = e.scrollLeft;
    }
    throw new Error('Carousel did not settle');
  });
}
async function touch(page, cdp, track, x, y, dx, dy) {
  assert.ok(await track.evaluate((e, p) => e.contains(document.elementFromPoint(p.x,p.y)), { x,y }), 'Touch must reach carousel through notice');
  const state = () => track.evaluate(e => ({ x:e.scrollLeft, index:Math.round(e.scrollLeft/e.clientWidth), y:scrollY }));
  const start = await state();
  const points = (x,y) => [{x,y,id:1,radiusX:2,radiusY:2,force:1}];
  await cdp.send('Input.dispatchTouchEvent', { type:'touchStart', touchPoints:points(x,y) });
  for (let i = 1; i <= 16; i++) {
    await cdp.send('Input.dispatchTouchEvent', { type:'touchMove', touchPoints:points(x+dx*i/16,y+dy*i/16) });
    await page.waitForTimeout(16);
  }
  await page.waitForTimeout(80);
  await cdp.send('Input.dispatchTouchEvent', { type:'touchEnd', touchPoints:[] });
  await page.waitForTimeout(650);
  return { start, end:await state() };
}
async function baseline(page, evidence) {
  await open(page,'home');
  await pointer(page,button(home(page),'알림'),evidence,'Home alert');
  evidence.header = await noticeEvidence(home(page));
  const detail = home(page).locator('[data-home-detail]').first();
  await pointer(page,detail,evidence,'Initial Detail under header notice',true);
  evidence.initialDetailOpened = new URL(page.url()).searchParams.get('view') === 'detail';
  await open(page,'home');
  await pointer(page,button(home(page),'알림'),evidence,'Home alert');
  const toast = await pending(home(page)).boundingBox(), mark = home(page).locator('[data-demo-bookmark]').nth(1);
  await place(page,mark,toast.y+toast.height/2);
  const old = await mark.getAttribute('aria-pressed');
  await pointer(page,mark,evidence,'Scrolled bookmark under header notice',true);
  evidence.bookmarkToggled = old !== await mark.getAttribute('aria-pressed');
  await screenshot(page,evidence.name+'-home');
  await pointer(page,button(home(page),'AI 콘텐츠 탐색'),evidence,'AI');
  evidence.stackedHome = await noticeEvidence(home(page));
  await open(page,'my');
  await pointer(page,button(my(page),'프로필 수정'),evidence,'Profile');
  await pointer(page,button(my(page),'문의 및 신고'),evidence,'Menu under profile notice',true);
  evidence.menuUpdated = (await pending(my(page)).innerText()).includes('문의');
  await pointer(page,button(my(page),'알림'),evidence,'My alert after menu');
  evidence.stackedMy = await noticeEvidence(my(page));
  await screenshot(page,evidence.name+'-my');
}
async function verify(page, context, evidence) {
  await open(page,'home');
  await pointer(page,button(home(page),'알림'),evidence,'Home alert');
  assert.equal(await pending(home(page)).count(),1);
  await layout(page,home(page),evidence,'Header');
  const post = home(page).locator('[data-home-post]').first();
  await pointer(page,button(post,'2번 슬라이드 보기'),evidence,'Indicator through header notice');
  await waitSlide(post,1);
  await pointer(page,post.locator('[data-home-detail]'),evidence,'Detail through header notice');
  await ready(page,page.locator('[data-detail-state="ready"]'));
  await pointer(page,button(page.locator('[data-detail-state]'),'뒤로가기'),evidence,'Detail back');
  await home(page).waitFor({state:'visible'});
  await waitSlide(post,1);
  await pointer(page,button(home(page),'AI 콘텐츠 탐색'),evidence,'AI after alert');
  assert.equal(await pending(home(page)).count(),1);
  assert.match(await pending(home(page)).innerText(),/AI 콘텐츠/);
  await layout(page,home(page),evidence,'AI replaces header');
  // Every card control can be deliberately placed underneath the fixed notice.
  const second = home(page).locator('[data-home-post]').nth(1);
  for (const label of ['공유','좋아요','저장','다음 슬라이드']) {
    await pointer(page,button(home(page),'AI 콘텐츠 탐색'),evidence,'AI replaces previous card feedback');
    assert.equal(await home(page).getByRole('status').count(),1);
    const target = button(second,label), n = await pending(home(page)).boundingBox();
    await place(page,target,n.y+n.height/2);
    const old = label === '저장' ? await target.getAttribute('aria-pressed') : null;
    await pointer(page,target,evidence,`Scrolled ${label} through AI notice`);
    if (label === '다음 슬라이드') await waitSlide(second,1);
    else if (label === '저장') {
      assert.notEqual(await target.getAttribute('aria-pressed'),old);
      await pointer(page,target,evidence,'Restore bookmark through notice');
      assert.equal(await target.getAttribute('aria-pressed'),old);
    } else assert.match(await second.getByRole('status').innerText(),new RegExp(label+' 기능은 준비 중'));
    assert.equal(await home(page).getByRole('status').count(),1,'Card feedback and chrome feedback must not stack');
  }
  // Read the long Demo disclosure fully; it still uses the existing card-local status.
  await place(page,second,page.viewportSize().height/2);
  await pointer(page,button(second,'저장'),evidence,'Remove Demo bookmark');
  assert.equal(await button(second,'저장').getAttribute('aria-pressed'),'false');
  assert.equal(await pending(home(page)).count(),0);
  assert.equal(await home(page).getByRole('status').count(),1);
  await layout(page,second,evidence,'Long Demo save disclosure');
  const contentId = await button(second,'저장').getAttribute('data-demo-bookmark');
  // Reproduce the scroll position where the old card status and fixed toast overlapped.
  await place(page,second.getByRole('status'),page.viewportSize().height-98);
  await layout(page,home(page),evidence,'Card status alone at former fixed-toast overlap');
  await pointer(page,button(home(page),'AI 콘텐츠 탐색'),evidence,'AI replaces card status at same location');
  assert.equal(await second.getByRole('status').count(),0);
  assert.equal(await home(page).getByRole('status').count(),1);
  await place(page,second,page.viewportSize().height/2);
  const scrollBefore = await page.evaluate(() => scrollY);
  await screenshot(page,evidence.name+'-home');
  await pointer(page,button(home(page),'SAVE'),evidence,'Home to Saved during notices');
  await ready(page,saved(page));
  assert.equal(await saved(page).locator(`[data-saved-content="${contentId}"]`).count(),0);
  await pointer(page,button(saved(page),'알림'),evidence,'Saved alert');
  await pointer(page,button(saved(page),'MY'),evidence,'Saved to My during notice');
  await ready(page,my(page));
  await pointer(page,button(my(page),'프로필 수정'),evidence,'Profile');
  await pointer(page,button(my(page),'알림'),evidence,'My alert replaces profile');
  assert.equal(await pending(my(page)).count(),1);
  assert.equal(await pending(my(page)).innerText(),'알림 기능은 준비 중이에요.');
  await pointer(page,button(my(page),'프로필 수정'),evidence,'Profile replaces alert');
  assert.equal(await pending(my(page)).count(),1);
  for (const label of ['내 활동','관심분야 관리','문의 및 신고','이용약관','개인정보처리방침']) {
    const target = button(my(page),label);
    // Keep the 320px repro at its natural position for the first obstructed menu.
    const b = await target.boundingBox();
    if (b.y < 44 || b.y+b.height > page.viewportSize().height-65) await place(page,target,page.viewportSize().height/2);
    await pointer(page,target,evidence,'My '+label);
    assert.match(await pending(my(page)).innerText(),new RegExp(label));
    assert.equal(await pending(my(page)).count(),1);
    await layout(page,my(page),evidence,label);
  }
  const toggle = my(page).getByRole('switch',{name:'알림 허용',exact:true});
  await place(page,toggle,page.viewportSize().height/2);
  await pointer(page,toggle,evidence,'My notification switch during notice');
  assert.equal(await toggle.getAttribute('aria-checked'),'false');
  await screenshot(page,evidence.name+'-my');
  await pointer(page,button(my(page),'SAVE'),evidence,'Scrolled My to Saved during notice');
  await ready(page,saved(page));
  await pointer(page,button(saved(page),'HOME'),evidence,'Saved to Home');
  await home(page).waitFor({state:'visible'});
  await page.waitForFunction(y => Math.abs(scrollY-y)<1,scrollBefore);
  assert.equal(await button(second,'저장').getAttribute('aria-pressed'),'false');
  await pointer(page,button(second,'저장'),evidence,'Re-add Demo bookmark');
  await pointer(page,button(home(page),'SAVE'),evidence,'Saved sync after re-add');
  await ready(page,saved(page));
  assert.equal(await saved(page).locator(`[data-saved-content="${contentId}"]`).count(),1);
  await pointer(page,button(saved(page),'HOME'),evidence,'Home restored again');
  await home(page).waitFor({state:'visible'});
  await page.waitForFunction(y => Math.abs(scrollY-y)<1,scrollBefore);
  // Start real touch gestures within the fixed status rectangle, over the carousel.
  const track = second.locator('[data-home-track]');
  await pointer(page,button(home(page),'HOME'),evidence,'Home scroll to top during notice');
  await page.waitForFunction(() => scrollY === 0);
  await pointer(page,button(home(page),'알림'),evidence,'Header after AI');
  assert.equal(await pending(home(page)).innerText(),'알림 기능은 준비 중이에요.');
  const n = await pending(home(page)).boundingBox(), y = n.y+n.height/2;
  await place(page,track,y);
  const b = await track.boundingBox();
  const cdp = await context.newCDPSession(page);
  const horizontal = await touch(page,cdp,track,b.x+b.width*.78,y,-b.width*.58,0);
  assert.ok(horizontal.end.index > horizontal.start.index,JSON.stringify(horizontal));
  assert.ok(Math.abs(horizontal.end.y-horizontal.start.y)<3);
  await place(page,track,y);
  const vertical = await touch(page,cdp,track,b.x+b.width*.45,y,0,-110);
  assert.ok(vertical.end.y>vertical.start.y+30,JSON.stringify(vertical));
  assert.equal(vertical.end.index,vertical.start.index);
  evidence.touch = {horizontal,vertical};
  await pointer(page,button(home(page),'MY'),evidence,'Home to My after scrolling');
  await ready(page,my(page));
  await pointer(page,button(my(page),'프로필 수정'),evidence,'My notice before Home');
  await pointer(page,button(my(page),'HOME'),evidence,'My to Home during notice');
  await home(page).waitFor({state:'visible'});
  // Service contains no mock content, but shares the same non-blocking chrome.
  await page.goto(base+'/pivot/home');
  const service = page.locator('[data-home-state="unconfigured"]');
  await service.waitFor({state:'visible'});
  assert.equal(await service.locator('[data-home-post], [data-demo-bookmark]').count(),0);
  await pointer(page,button(service,'알림'),evidence,'Service alert');
  await pointer(page,button(service,'AI 콘텐츠 탐색'),evidence,'Service AI');
  assert.equal(await pending(service).count(),1);
  await pointer(page,button(service,'MY'),evidence,'Service My during notice');
  const serviceMy = page.locator('[data-my-state="unconfigured"]');
  await serviceMy.waitFor({state:'visible'});
  assert.equal(await serviceMy.getByRole('switch').count(),0);
  await pointer(page,button(serviceMy,'알림'),evidence,'Service My alert');
  await pointer(page,button(serviceMy,'SAVE'),evidence,'Service Saved during notice');
  await page.locator('[data-saved-state="unconfigured"]').waitFor({state:'visible'});
}
(async () => {
  const browser = await chromium.launch({headless:true,channel:process.env.PIVOT_BROWSER_CHANNEL || 'msedge'});
  try {
    for (const viewport of [{width:320,height:568},{width:375,height:812},{width:390,height:844}]) for (const reducedMotion of ['no-preference','reduce']) {
      const context = await browser.newContext({viewport,isMobile:true,hasTouch:true,reducedMotion,serviceWorkers:'block'});
      const page = await context.newPage(); page.setDefaultTimeout(8000);
      const evidence = {name:`${viewport.width}-${reducedMotion}`,viewport,reducedMotion,browser:browser.version(),clicks:[],notices:[],api:[],external:[],errors:[]};
      await context.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.pathname === '/favicon.ico') return route.fulfill({status:204});
        if (url.pathname.startsWith('/api/')) { evidence.api.push(url.pathname); return route.abort(); }
        if (url.origin !== new URL(base).origin) { evidence.external.push(url.origin); return route.abort(); }
        return route.continue();
      });
      page.on('pageerror', e => evidence.errors.push(e.message));
      page.on('console', e => { if (e.type()==='error') evidence.errors.push(e.text()); });
      try {
        if (before) await baseline(page,evidence); else await verify(page,context,evidence);
        assert.deepEqual(evidence.api,[]); assert.deepEqual(evidence.external,[]); assert.deepEqual(evidence.errors,[]);
        evidence.status = before ? 'baseline' : 'pass'; console.log(evidence.status.toUpperCase(),evidence.name);
      } catch (e) {
        evidence.status='fail'; evidence.error=e.stack; console.error('FAIL',evidence.name,e.stack);
        await screenshot(page,evidence.name+'-failure').catch(()=>{});
      } finally { results.push(evidence); await context.close(); }
    }
  } finally { await browser.close(); fs.writeFileSync(path.join(out,'results.json'),JSON.stringify(results,null,2)); }
  if (results.some(r=>r.status==='fail')) process.exitCode=1;
})().catch(e=>{console.error(e);process.exitCode=1;});
