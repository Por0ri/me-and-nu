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

async function verify(browser, width, demo) {
  const context = await browser.newContext({ viewport: { width, height: 844 }, reducedMotion: 'reduce', serviceWorkers: 'block' });
  const page = await context.newPage(); page.setDefaultTimeout(20000);
  const requests = [], unexpected = [], errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), method = request.method();
    if (url.pathname.startsWith('/api/')) {
      requests.push({ method, path: url.pathname });
      const reply = body => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
      if (!demo && loopback(url) && method === 'GET') {
        if (url.pathname === '/api/v1/auth/session') return reply({ authenticated: true, sessionState: 'active', onboardingCompleted: true,
          user: { id: 7, accountType: 'user' }, csrfToken: 'fixture-only' });
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
      assert.match(await section.innerText(), /현재 설정값을 확인하거나 변경할 수 없어요/);
      for (const control of [allow, night]) {
        assert.equal(await control.getAttribute('aria-disabled'), 'true');
        assert.equal(await control.getAttribute('aria-describedby'), 'my-notification-status');
        await control.focus(); await page.keyboard.press('Enter'); await page.keyboard.press('Space');
        const rect = await control.boundingBox(); await page.mouse.click(rect.x + rect.width / 2, rect.y + rect.height / 2);
        assert.equal(await control.getAttribute('aria-checked'), 'false');
      }
      assert.equal(requests.length, callsBefore, 'settings interactions cannot issue requests');
      assert.deepEqual(await page.evaluate(() => ({ local: { ...localStorage }, session: { ...sessionStorage } })), beforeStorage);
      assert.doesNotMatch(await section.innerText(), /저장했|저장 완료|저장 성공/);
      await section.screenshot({ path: path.join(output, `service-${width}.png`) });
      await my.getByRole('button', { name: '회원탈퇴', exact: true }).click();
      await page.getByRole('status').filter({ hasText: '회원탈퇴 기능은 준비 중' }).waitFor();
    }
    assert.ok(requests.every(request => request.method === 'GET'));
    assert.ok(!requests.some(request => /notification|withdraw|admin/.test(request.path)));
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
