// Real loopback HTTP -> session/CSRF -> isolated migrated PostgreSQL. No API mocks.
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const { chromium, request } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const base = process.argv[2] || 'http://127.0.0.1:3223', api = 'http://127.0.0.1:8201';
const output = process.argv[3] || path.resolve(__dirname, '../../.preview-pivot/admin-support-actions/browser');
const local = url => ['localhost','127.0.0.1'].includes(url.hostname) && url.protocol === 'http:';
assert.ok(local(new URL(base)));
const key = { 'X-Fixture-Key': 'isolated-support-actions-browser' };
const results = [];
let control;
async function configure(body) { const r = await control.post(`${api}/__support_control`, { headers: key, data: body }); assert.equal(r.status(), 200, await r.text()); }
async function dbState() { const r = await control.get(`${api}/__support_state`, { headers: key }); assert.equal(r.status(), 200); return r.json(); }
async function session(browser, width, role) {
  const context = await browser.newContext({ viewport: {width, height: 1117}, serviceWorkers: 'block' });
  const token = ['owner','other'].includes(role) ? `support-actions-fixture-${role}` : `admin-read-fixture-${role}`;
  await context.addCookies([{ name:'menu_session', value:token, domain:'127.0.0.1', path:'/', httpOnly:true, secure:false, sameSite:'Lax' }]);
  const page = await context.newPage(), errors = [], blocked = [], posts = [], responses = [];
  page.setDefaultTimeout(20000); page.setDefaultNavigationTimeout(60000);
  page.on('pageerror', e => errors.push(e.message));
  page.on('response', r => { const u = new URL(r.url()); if (u.pathname.startsWith('/api/')) responses.push({path:u.pathname,method:r.request().method(),status:r.status()}); });
  page.on('request', req => { if (req.method() === 'POST' && new URL(req.url()).pathname.startsWith('/api/v1/admin/')) posts.push({ path:new URL(req.url()).pathname, body:req.postDataJSON() }); });
  await context.route('**/*', route => { if (!local(new URL(route.request().url()))) { blocked.push(new URL(route.request().url()).hostname); return route.abort(); } return route.continue(); });
  return { context, page, posts, responses, async screenshot(name) {
    await page.evaluate(() => document.fonts.ready);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `${name} document overflow`);
    await page.screenshot({path:path.join(output,`${name}-${width}.png`), fullPage:true, animations:'disabled'});
  }, clean() { assert.deepEqual(errors, []); assert.deepEqual(blocked, []); } };
}
const ready = p => p.locator('[data-report-list="ready"]').waitFor();
async function kind(p, value) { await p.getByRole('combobox',{name:'신고 및 문의 유형',exact:true}).selectOption(value); await ready(p); }
async function select(p, id) { await p.locator(`[data-report-row="${id}"] td:first-child button`).click(); await p.locator(`[data-report-detail="${id}"]`).waitFor(); }
async function submit(p, label, text, confirm) {
  await p.getByRole('button',{name:label,exact:true}).click();
  const dialog = p.getByRole('dialog'); await dialog.waitFor();
  await dialog.locator('textarea').fill(text);
  await dialog.getByRole('button',{name:'내용 확인',exact:true}).click();
  const completed = p.waitForResponse(r => r.request().method() === 'POST' && /\/admin\/(reports|inquiries)\/\d+\/(actions|answer)$/.test(new URL(r.url()).pathname));
  await dialog.getByRole('button',{name:confirm,exact:true}).click();
  await completed;
}
async function flow(browser, width) {
  await configure({reset:true});
  const admin = await session(browser,width,'operator'), p = admin.page;
  try {
    await p.goto(`${base}/admin/reports`); await ready(p); await kind(p,'content'); await select(p,'R-1');
    await submit(p,'검토 시작','합성 검토 사유','검토 시작 확정');
    await p.locator('[data-support-phase="succeeded"]').first().waitFor();
    assert.equal((await dbState()).reportStatus,'reviewing');
    await ready(p); await p.getByRole('button',{name:'해결',exact:true}).waitFor({state:'visible'});
    await submit(p,'해결','합성 해결 사유','해결 확정');
    await p.locator('[data-support-phase="succeeded"]').first().waitFor();
    assert.equal((await dbState()).reportStatus,'resolved');
    await admin.screenshot('report-resolved');
    await kind(p,'bug'); await select(p,'I-2');
    await configure({lose_next_write_response:true});
    const reply = 'SYNTHETIC_PRIVATE_REPLY 답변입니다. <script>window.replyInjected=true</script>';
    await submit(p,'답변 작성',reply,'답변 등록 확정');
    await p.locator('[data-support-phase="uncertain"]').waitFor();
    assert.deepEqual(await dbState(), {auditCount:3,answerCount:1,reportStatus:'resolved',inquiryStatus:'answered',auditContainsAnswerBody:false});
    await kind(p,'content');
    const replayResponse = p.waitForResponse(r => r.request().method() === 'POST' && new URL(r.url()).pathname === '/api/v1/admin/inquiries/2/answer' && r.status() === 200);
    await p.getByRole('button',{name:/같은 요청으로 다시 확인/}).click();
    assert.equal((await (await replayResponse).json()).replayed, true);
    await p.locator('[data-support-phase="succeeded"]').first().waitFor();
    assert.equal((await dbState()).auditCount,3);
    const attempts = admin.posts.filter(x=>x.path.endsWith('/answer'));
    assert.equal(attempts.length,2); assert.deepEqual(attempts[0].body,attempts[1].body);
    await kind(p,'bug'); await select(p,'I-2'); await p.locator('[data-inquiry-answer]').waitFor();
    assert.ok((await p.locator('[data-inquiry-answer]').innerText()).includes(reply));
    assert.equal(await p.evaluate(()=>window.replyInjected),undefined);
    await admin.screenshot('inquiry-answered'); admin.clean();

    const owner = await session(browser,width,'owner');
    try {
      const list = await owner.context.request.get(`${api}/api/v1/inquiries`); assert.equal(list.status(),200); assert.equal((await list.json()).total,3);
      const detail = await owner.context.request.get(`${api}/api/v1/inquiries/2`); assert.equal(detail.status(),200); assert.equal((await detail.json()).answer,reply);
      await owner.page.goto(`${base}/pivot/home`);
      await owner.page.locator('[data-my-navigation]').click();
      await owner.page.getByRole('button',{name:'문의 및 신고',exact:true}).click();
      await owner.page.getByRole('button',{name:'문의 내역 · 답변',exact:true}).click();
      await owner.page.getByRole('button',{name:'문의 2 상세',exact:true}).click();
      const reader = owner.page.getByRole('region',{name:'문의 및 답변 상세',exact:true});
      await reader.getByRole('heading',{name:'운영자 답변',exact:true}).waitFor();
      assert.ok((await reader.innerText()).includes(reply));
      assert.equal(await owner.page.evaluate(()=>window.replyInjected),undefined);
      await owner.screenshot('own-inquiry-answer'); owner.clean();
    } catch (error) {
      console.error(JSON.stringify({width,ownerUrl:owner.page.url(),responses:owner.responses.slice(-15),errors:await owner.page.getByRole('alert').allTextContents()}));
      await owner.page.screenshot({path:path.join(output,`owner-failure-${width}.png`),fullPage:true});
      throw error;
    } finally { await owner.context.close(); }
    const other = await session(browser,width,'other');
    try { const r=await other.context.request.get(`${api}/api/v1/inquiries/2`); assert.equal(r.status(),404); assert.doesNotMatch(await r.text(),/SYNTHETIC_PRIVATE_REPLY/); }
    finally { await other.context.close(); }
    results.push({width,reportLifecycle:true,inquiryPublication:true,lostResponseReplayOnce:true,immutableAuditHasNoAnswerBody:true,ownerRead:true,otherOwner404:true,xssEscaped:true});
  } catch (error) {
    console.error(JSON.stringify({width,responses:admin.responses.slice(-25),notice:await p.locator('[data-support-phase]').allTextContents()}));
    await p.screenshot({path:path.join(output,`failure-${width}.png`),fullPage:true});
    throw error;
  } finally { await admin.context.close(); }
}
async function readonly(browser) {
  await configure({reset:true}); const s = await session(browser,1728,'viewer');
  try { await s.page.goto(`${base}/admin/reports`); await ready(s.page); await kind(s.page,'content'); await select(s.page,'R-1');
    assert.equal(await s.page.getByRole('button',{name:'검토 시작',exact:true}).isDisabled(),true);
    await kind(s.page,'bug'); await select(s.page,'I-2'); assert.equal(await s.page.getByRole('button',{name:'답변 작성',exact:true}).isDisabled(),true);
    assert.equal(s.posts.length,0); s.clean(); results.push({viewerReadonly:true});
  } finally { await s.context.close(); }
}
(async()=>{
  fs.mkdirSync(output,{recursive:true}); control=await request.newContext();
  const browser=await chromium.launch({headless:true,channel:'chrome'});
  try { await flow(browser,1728); await flow(browser,390); await readonly(browser); fs.writeFileSync(path.join(output,'results.json'),JSON.stringify({passed:true,results},null,2)); console.log(JSON.stringify({passed:true,results},null,2)); }
  finally { await configure({reset:true}).catch(()=>{}); await browser.close(); await control.dispose(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
