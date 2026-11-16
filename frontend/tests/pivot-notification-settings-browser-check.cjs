// Local frontend + in-memory API fixtures only; never forwards a backend request.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const base = process.argv[2] || 'http://127.0.0.1:3197';
const output = process.argv[3] || path.join(require('node:os').tmpdir(), 'pivot-notification-settings');
const loopback = url => url.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(url.hostname);
assert.ok(loopback(new URL(base)));
fs.mkdirSync(output, { recursive: true });
const results = [];
const until = async predicate => {
  const limit = Date.now() + 15000;
  while (!await predicate()) { if (Date.now() > limit) throw new Error('State wait timed out'); await new Promise(resolve => setTimeout(resolve, 25)); }
};

async function verify(browser, width, demo) {
  const context = await browser.newContext({ viewport: { width, height: 844 }, reducedMotion: 'reduce', serviceWorkers: 'block' });
  const page = await context.newPage(); page.setDefaultTimeout(20000);
  const requests = [], unexpected = [], errors = [];
  let stored = { inAppEnabled: true, nightInAppEnabled: false, version: 1 };
  let getFails = false, patchFails = false, conflict = false, holdPatch = false, releasePatch, sessionId = 7;
  page.on('pageerror', error => errors.push(error.message));
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), method = request.method();
    if (url.pathname.startsWith('/api/')) {
      requests.push({ method, path: url.pathname });
      const reply = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
      if (!demo && loopback(url) && url.pathname === '/api/v1/users/me/notification-settings') {
        if (method === 'GET') return getFails ? reply({ code: 'UNAVAILABLE' }, 503) : reply(stored);
        if (method === 'PATCH') {
          assert.equal(request.headers()['x-csrf-token'], 'fixture-only');
          const body = request.postDataJSON();
          if (holdPatch) await new Promise(resolve => { releasePatch = resolve; });
          if (patchFails) return reply({ code: 'UNAVAILABLE' }, 503);
          if (conflict || body.version !== stored.version) { stored = { ...stored, inAppEnabled: true, version: stored.version + 1 }; return reply({ code: 'NOTIFICATION_SETTINGS_CONFLICT' }, 409); }
          stored = { ...stored, ...body, version: stored.version + 1 }; return reply(stored);
        }
      }
      if (!demo && loopback(url) && method === 'GET') {
        if (url.pathname === '/api/v1/auth/session') return reply({ authenticated: true, sessionState: 'active', onboardingCompleted: true,
          user: { id: sessionId, accountType: 'user' }, csrfToken: 'fixture-only' });
        if (url.pathname === '/api/v1/users/me') return reply({ userId: 7, accountType: 'user', nickname: '알림 검증', email: 'fixture@example.com' });
        if (url.pathname === '/api/v1/me/topics') return reply({ topics: [{ id: 83, code: 'anime', name: '애니메이션', status: 'active', subtopicIds: [], deletedAt: null }] });
        if (url.pathname === '/api/v1/topics/83/feed') return reply({ selectedTopicId: 83, sections: [], nextCursor: null, emptyReason: null });
        if (url.pathname === '/api/v1/users/me/bookmarks') return reply({ items: [], nextCursor: null });
      }
      unexpected.push(method + ' ' + url.pathname); return route.abort();
    }
    if (!loopback(url) || url.origin !== new URL(base).origin || !['GET', 'HEAD'].includes(method)) {
      unexpected.push(method + ' non-frontend request'); return route.abort();
    }
    return route.continue();
  });
  try {
    const url = `${base}/pivot/${demo ? 'demo' : 'home'}?view=my`;
    await page.goto(url); const my = page.locator('[data-my-state="ready"]'); await my.waitFor();
    const section = my.getByRole('region', { name: '알림 설정', exact: true });
    const allow = section.getByRole('switch', { name: '알림 허용', exact: true });
    const night = section.getByRole('switch', { name: '21시 이후 알림 허용', exact: true });
    if (!demo) await until(async () => await allow.getAttribute('aria-disabled') === null);
    await until(() => section.locator('img').evaluateAll(images => images.every(img => img.complete && img.naturalWidth > 0)));
    const geometry = await section.locator('img').evaluateAll(images => images.map(img => {
      const rect = img.getBoundingClientRect(); return { src: img.getAttribute('src'), width: rect.width, height: rect.height, loaded: img.complete && img.naturalWidth > 0 };
    }));
    assert.equal(geometry.length, 2);
    for (const item of geometry) { assert.equal(item.loaded, true); assert.equal(item.width, 33); assert.equal(item.height, 18); }
    assert.equal((await allow.boundingBox()).height, 32); assert.equal((await night.boundingBox()).height, 32);
    assert.equal((await section.boundingBox()).width, width - 48);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    const beforeStorage = await page.evaluate(() => ({ local: { ...localStorage }, session: { ...sessionStorage } }));
    const callsBefore = requests.length;
    if (demo) {
      assert.equal(await allow.getAttribute('aria-checked'), 'true'); assert.equal(await night.getAttribute('aria-checked'), 'false');
      await section.screenshot({ path: path.join(output, `demo-${width}.png`) });
      await allow.focus(); await page.keyboard.press('Enter');
      await night.focus(); await page.keyboard.press('Space');
      assert.equal(await allow.getAttribute('aria-checked'), 'false'); assert.equal(await night.getAttribute('aria-checked'), 'true');
      assert.notEqual(await night.evaluate(el => getComputedStyle(el).outlineStyle), 'none');
      assert.deepEqual(await page.evaluate(() => ({ local: { ...localStorage }, session: { ...sessionStorage } })), beforeStorage);
      assert.equal(requests.length, callsBefore);
      await page.reload(); await my.waitFor(); assert.equal(await allow.getAttribute('aria-checked'), 'true'); assert.equal(await night.getAttribute('aria-checked'), 'false');
      assert.equal(requests.length, 0, 'Demo never accesses API');
    } else {
      await until(async () => await allow.getAttribute('aria-disabled') === null);
      assert.equal(await allow.getAttribute('aria-checked'), 'true'); assert.equal(await night.getAttribute('aria-checked'), 'false');
      assert.match(await section.innerText(), /선택적 알림 수신 제어는 준비 중/);
      const writes = () => requests.filter(request => request.method === 'PATCH').length;
      holdPatch = true;
      await allow.click(); await until(() => !!releasePatch);
      assert.equal(await allow.getAttribute('aria-checked'), 'true', 'no optimistic success');
      assert.match(await section.innerText(), /저장하고 있어요/);
      await night.focus(); await page.keyboard.press('Space'); await page.keyboard.press('Enter'); assert.equal(writes(), 1);
      holdPatch = false; releasePatch();
      await until(async () => await allow.getAttribute('aria-disabled') === null);
      assert.equal(await allow.getAttribute('aria-checked'), 'false'); assert.match(await section.innerText(), /저장했어요/);
      await night.focus(); await page.keyboard.press('Space');
      await until(async () => await night.getAttribute('aria-disabled') === null);
      assert.equal(await night.getAttribute('aria-checked'), 'true');
      await page.reload(); await my.waitFor(); await until(async () => await allow.getAttribute('aria-disabled') === null);
      assert.equal(await allow.getAttribute('aria-checked'), 'false'); assert.equal(await night.getAttribute('aria-checked'), 'true');
      patchFails = true; await allow.click(); await section.getByRole('alert').waitFor();
      assert.match(await section.innerText(), /저장 결과를 확인하지 못했어요/); assert.doesNotMatch(await section.innerText(), /저장했어요/);
      const failedWrites = writes(); patchFails = false;
      await section.getByRole('button', { name: '설정 다시 불러오기' }).click(); await until(async () => await allow.getAttribute('aria-disabled') === null);
      assert.equal(writes(), failedWrites); assert.equal(await allow.getAttribute('aria-checked'), 'false');
      conflict = true; await allow.click(); await section.getByRole('alert').waitFor(); assert.match(await section.innerText(), /다른 화면에서/);
      conflict = false; getFails = true;
      await section.getByRole('button', { name: '설정 다시 불러오기' }).click(); await section.getByRole('alert').waitFor();
      assert.match(await section.innerText(), /불러오지 못했어요/); getFails = false;
      await section.getByRole('button', { name: '설정 다시 불러오기' }).click(); await until(async () => await allow.getAttribute('aria-disabled') === null);
      assert.equal(await allow.getAttribute('aria-checked'), 'true');
      assert.deepEqual(await page.evaluate(() => ({ local: { ...localStorage }, session: { ...sessionStorage } })), beforeStorage);
      await section.screenshot({ path: path.join(output, `service-${width}.png`) });
      const beforeSessionChange = writes(); sessionId = 8; await allow.click();
      await until(async () => (await section.innerText()).includes('로그인 상태가 변경됐어요'));
      assert.equal(writes(), beforeSessionChange); assert.equal(await allow.getAttribute('aria-disabled'), 'true');
      await my.getByRole('button', { name: '회원탈퇴', exact: true }).click();
      await my.getByRole('dialog', { name: '회원탈퇴' }).getByRole('button', { name: '취소' }).click();
    }
    assert.ok(requests.every(request => request.method === 'GET' || request.method === 'PATCH' && request.path.endsWith('/notification-settings')));
    assert.ok(!requests.some(request => /\/notifications(?:\/|$)|withdraw|admin/.test(request.path)));
    assert.deepEqual(unexpected, []); assert.deepEqual(errors, []);
    results.push({ mode: demo ? 'demo' : 'service', width, passed: true, geometry, apiRequests: requests });
  } catch (error) {
    await page.screenshot({ path: path.join(output, `failure-${demo}-${width}.png`), fullPage: true }).catch(() => {});
    results.push({ mode: demo ? 'demo' : 'service', width, passed: false, error: error.message, unexpected, errors });
    throw error;
  } finally { await context.close(); }
}

(async () => {
  const browser = await chromium.launch({ headless: true, channel: process.env.PIVOT_BROWSER_CHANNEL || 'msedge' });
  try { for (const width of [375, 390]) for (const demo of [true, false]) await verify(browser, width, demo); }
  finally { await browser.close(); fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(results, null, 2)); }
  console.log(JSON.stringify(results.map(({ mode, width, passed }) => ({ mode, width, passed }))));
})().catch(error => { console.error(error); process.exitCode = 1; });
