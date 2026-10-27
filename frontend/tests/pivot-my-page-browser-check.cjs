// Uses the existing Playwright + Edge installation. Never installs packages.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const base = process.argv[2] || 'http://127.0.0.1:3100';
const out = process.argv[3] || path.join(require('node:os').tmpdir(), 'pivot-my-page');
const mode = process.argv[4] || 'development';
const results = [];
fs.mkdirSync(out, { recursive: true });
const snapshot = page => page.evaluate(() => ({ y: scrollY, query: document.querySelector('input[type=search]')?.value,
  tracks: [...document.querySelectorAll('[data-home-track]')].map(e => Math.round(e.scrollLeft / e.clientWidth)) }));
const storage = page => page.evaluate(() => ({ local: { ...localStorage }, session: { ...sessionStorage }, cookie: document.cookie }));
async function myReady(page) {
  await page.locator('[data-my-state="ready"]').waitFor();
  await page.waitForFunction(() => document.activeElement === document.querySelector('[data-my-state] h1'));
}
async function restored(page, before) {
  await page.locator('[data-home-state="ready"]').waitFor({ state: 'visible' });
  await page.waitForFunction(({ y, query, tracks }) => Math.abs(scrollY - y) < 2
    && document.querySelector('input[type=search]').value === query
    && [...document.querySelectorAll('[data-home-track]')].every((e, i) => Math.round(e.scrollLeft / e.clientWidth) === tracks[i]), before);
  assert.deepEqual(await snapshot(page), before);
}
async function capture(page, name) {
  await page.evaluate(async () => { await document.fonts.ready; await Promise.all([...document.querySelectorAll('[data-my-state] img')].map(e => e.decode())); });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'No horizontal overflow');
  await page.screenshot({ path: path.join(out, name + '.png'), fullPage: true, style: 'nextjs-portal { visibility: hidden !important; }' });
}
async function verifyProduction(browser) {
  for (const viewport of [{ width: 375, height: 812 }, { width: 390, height: 844 }]) {
    const context = await browser.newContext({ viewport, isMobile: true, hasTouch: true });
    const page = await context.newPage(); const api = [], external = [], pageErrors = [], consoleErrors = [], checks = [];
    await context.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/favicon.ico') { await route.fulfill({ status: 204 }); return; }
      if (url.pathname.startsWith('/api/')) { api.push(url.href); await route.abort(); return; }
      if (url.origin !== new URL(base).origin) { external.push(url.href); await route.abort(); return; }
      await route.continue();
    });
    page.on('pageerror', error => pageErrors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') consoleErrors.push(message.text()); });
    try {
      for (const route of ['/pivot/demo?view=my', '/pivot/demo?view=my&postId=demo-home-story&contentId=demo-content-story', '/pivot/preview?screen=result']) {
        const response = await page.goto(base + route);
        assert.equal(response.status(), 404);
        assert.equal(await page.locator('[data-my-state], [aria-label="시연용 데모 안내"]').count(), 0);
        checks.push({ route, httpStatus: 404, mockProfile: false });
      }
      await page.goto(base + '/pivot/home?view=my');
      await page.locator('[data-my-state="unconfigured"]').waitFor();
      assert.doesNotMatch(await page.locator('body').innerText(), /미뉴|example.com|시연용|회원탈퇴/);
      assert.equal(await page.getByRole('switch').count(), 0);
      await capture(page, 'production-my-service-' + viewport.width);
      await page.reload(); await page.locator('[data-my-state="unconfigured"]').waitFor();
      await page.getByRole('button', { name: 'HOME', exact: true }).click();
      await page.locator('[data-home-state="unconfigured"]').waitFor({ state: 'visible' });
      await page.getByRole('button', { name: 'MY', exact: true }).click();
      await page.locator('[data-my-state="unconfigured"]').waitFor();
      assert.deepEqual(api, []); assert.deepEqual(external, []); assert.deepEqual(pageErrors, []);
      // A deliberately blocked document produces an expected HTTP 404 console
      // entry. Keep those distinct from unexpected JS/runtime failures.
      assert.ok(consoleErrors.every(error => /Failed to load resource: the server responded with a status of 404 \(Not Found\)/.test(error)));
      results.push({ name: 'production-' + viewport.width, status: 'pass', checks, service: 'unconfigured', expected404Console: consoleErrors, api, external, pageErrors });
      console.log('PASS My Page production ' + viewport.width);
    } finally { await context.close(); }
  }
}
(async () => {
  const browser = await chromium.launch({ headless: true, channel: process.env.PIVOT_BROWSER_CHANNEL || 'msedge' });
  try {
    if (mode === 'production') { await verifyProduction(browser); return; }
    for (const viewport of [{ width: 375, height: 812 }, { width: 390, height: 844 }]) for (const reducedMotion of ['no-preference', 'reduce']) {
      const context = await browser.newContext({ viewport, isMobile: true, hasTouch: true, reducedMotion });
      const page = await context.newPage(); page.setDefaultTimeout(12000);
      const api = [], external = [], errors = [];
      await context.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.pathname === '/favicon.ico') { await route.fulfill({ status: 204 }); return; }
        if (url.pathname.startsWith('/api/')) { api.push(url.href); await route.abort(); return; }
        if (url.origin !== new URL(base).origin) { external.push(url.href); await route.abort(); return; }
        await route.continue();
      });
      await context.addInitScript(() => {
        window.__notificationRequests = 0;
        if (window.Notification) Notification.requestPermission = async () => { window.__notificationRequests++; return 'denied'; };
      });
      page.on('pageerror', e => errors.push(e.message));
      page.on('console', msg => { if (msg.type() === 'error') errors.push(msg.text()); });
      const name = `${viewport.width}-${reducedMotion}`;
      try {
        await page.goto(base + '/pivot/demo?view=my'); await myReady(page);
        const my = page.locator('[data-my-state]');
        assert.match(await my.innerText(), /미뉴님/); assert.match(await my.innerText(), /demo\*\*\*@example.com/);
        assert.equal(await my.getByRole('button', { name: 'MY', exact: true }).getAttribute('aria-current'), 'page');
        assert.equal(await my.getByRole('button', { name: 'HOME', exact: true }).getAttribute('aria-current'), null);
        assert.match(await page.getByRole('complementary', { name: '시연용 데모 안내' }).innerText(), /가상 프로필.*서버 저장 없음/);
        await capture(page, 'my-' + name);
        const profile = await my.getByRole('region', { name: '시연용 가상 프로필' }).boundingBox();
        assert.equal(profile.x, 24); assert.equal(profile.y, 136); assert.equal(profile.width, viewport.width - 48); assert.equal(profile.height, 68);
        const geometries = await my.locator('img').evaluateAll(items => items.map(e => {
          const r = e.getBoundingClientRect(); return { src: e.getAttribute('src'), width: r.width, height: r.height, loaded: e.complete && e.naturalWidth > 0 };
        }));
        assert.equal(geometries.length, 12);
        for (const image of geometries) {
          assert.equal(image.loaded, true, image.src);
          if (image.src.includes('chevron')) assert.deepEqual([image.width, image.height], [16, 24]);
          if (image.src.includes('switch')) assert.deepEqual([image.width, image.height], [33, 18]);
        }
        const allow = my.getByRole('switch', { name: '알림 허용', exact: true });
        const night = my.getByRole('switch', { name: '21시 이후 알림 허용', exact: true });
        assert.equal(await allow.getAttribute('aria-checked'), 'true'); assert.equal(await night.getAttribute('aria-checked'), 'false');
        const untouched = await storage(page);
        await allow.click(); await night.focus(); await page.keyboard.press('Space');
        assert.equal(await allow.getAttribute('aria-checked'), 'false'); assert.equal(await night.getAttribute('aria-checked'), 'true');
        await capture(page, 'my-toggles-' + name);
        for (const label of ['프로필 수정', '내 활동', '관심분야 관리', '문의 및 신고', '이용약관', '개인정보처리방침']) {
          await my.getByRole('button', { name: label, exact: true }).click();
          await my.getByRole('status').filter({ hasText: label }).waitFor();
          assert.match(await my.getByRole('status').innerText(), /준비 중/);
          assert.equal(new URL(page.url()).search, '?view=my');
        }
        // SAVE now opens the Saved Slice; return through browser Back.
        await my.getByRole('button', { name: 'SAVE', exact: true }).click();
        await page.locator('[data-saved-state="ready"]').waitFor();
        assert.equal(new URL(page.url()).searchParams.get('view'), 'saved');
        await page.goBack(); await myReady(page);
        const logout = my.getByRole('button', { name: '로그아웃', exact: true });
        await logout.click();
        const dialog = page.getByRole('dialog'); await dialog.waitFor();
        assert.match(await dialog.innerText(), /실제 로그인 세션이 없어 로그아웃을 수행하지 않아요/);
        assert.equal(await dialog.getByRole('button', { name: '취소' }).evaluate(e => e === document.activeElement), true);
        await my.getByRole('button', { name: 'HOME', exact: true }).evaluate(e => e.focus());
        assert.equal(await dialog.evaluate(e => e.matches(':modal') && e.contains(document.activeElement)), true, 'Native modal makes background controls inert');
        await page.keyboard.press('Tab');
        assert.equal(await dialog.getByRole('button', { name: '확인' }).evaluate(e => e === document.activeElement), true, 'Both dialog actions are keyboard accessible');
        await capture(page, 'my-logout-' + name);
        await page.keyboard.press('Escape'); await dialog.waitFor({ state: 'detached' });
        assert.equal(await logout.evaluate(e => e === document.activeElement), true, 'Escape restores trigger focus');
        await logout.click(); await dialog.getByRole('button', { name: '확인' }).click();
        await dialog.waitFor({ state: 'detached' });
        const withdraw = my.getByRole('button', { name: '회원탈퇴', exact: true });
        await withdraw.click(); assert.match(await dialog.innerText(), /계정 삭제를 요청하거나 개인정보를 삭제하지 않아요/);
        await capture(page, 'my-withdrawal-' + name);
        await dialog.getByRole('button', { name: '취소' }).click();
        await withdraw.click(); await dialog.getByRole('button', { name: '확인' }).click(); await dialog.waitFor({ state: 'detached' });
        assert.deepEqual(await storage(page), untouched);
        assert.equal(await page.evaluate(() => window.__notificationRequests), 0);
        await page.reload(); await myReady(page);
        assert.equal(await allow.getAttribute('aria-checked'), 'true'); assert.equal(await night.getAttribute('aria-checked'), 'false');
        await my.getByRole('button', { name: 'HOME', exact: true }).click();
        const home = page.locator('[data-home-state="ready"]'); await home.waitFor({ state: 'visible' });
        assert.match(page.url(), /view=home/);
        await home.getByRole('searchbox').fill('MY 왕복 보존');
        const tracks = home.locator('[data-home-track]');
        await tracks.nth(0).evaluate(e => e.scrollTo({ left: e.clientWidth * 2, behavior: 'instant' }));
        await tracks.nth(1).evaluate(e => e.scrollTo({ left: e.clientWidth, behavior: 'instant' }));
        await page.evaluate(() => scrollTo({ top: 554, behavior: 'instant' }));
        await page.waitForFunction(() => document.querySelector('[data-home-post] [data-home-position]').textContent.includes('3 / 5'));
        const before = await snapshot(page);
        await home.getByRole('button', { name: 'MY', exact: true }).click(); await myReady(page);
        assert.equal(await page.evaluate(() => scrollY), 0);
        await allow.click();
        await my.getByRole('button', { name: 'HOME', exact: true }).click(); await restored(page, before);
        assert.equal(await home.getByRole('button', { name: 'MY', exact: true }).evaluate(e => e === document.activeElement), true);
        await page.goForward(); await myReady(page); assert.equal(await allow.getAttribute('aria-checked'), 'true');
        await page.goBack(); await restored(page, before);
        await home.getByRole('button', { name: 'MY', exact: true }).click(); await myReady(page);
        await page.reload(); await myReady(page);
        await my.getByRole('button', { name: 'HOME', exact: true }).click(); await restored(page, before);
        // HOME reload retains its history snapshot without a persistent settings store.
        await page.reload(); await restored(page, before);
        const detailButton = home.locator('[data-home-detail="demo-content-discovery"]');
        await detailButton.focus(); await detailButton.scrollIntoViewIfNeeded();
        const beforeDetail = await snapshot(page);
        await page.keyboard.press('Enter');
        const detail = page.locator('[data-detail-state="ready"]'); await detail.waitFor({ state: 'visible' });
        const more = detail.getByRole('button', { name: '더 볼래요', exact: true });
        await more.click();
        const detailY = await page.evaluate(() => scrollY);
        await detail.getByRole('button', { name: 'MY', exact: true }).click(); await myReady(page);
        assert.equal(await detail.isVisible(), false);
        await page.goBack(); await detail.waitFor({ state: 'visible' });
        await page.waitForFunction(y => Math.abs(scrollY - y) < 2, detailY);
        assert.equal(await more.getAttribute('aria-pressed'), 'true', 'MY does not reset mounted Detail feedback');
        await page.goForward(); await myReady(page);
        await my.getByRole('button', { name: 'HOME', exact: true }).click(); await restored(page, beforeDetail);
        const freshDetail = home.locator('[data-home-detail="demo-content-reading"]');
        await freshDetail.focus(); await freshDetail.scrollIntoViewIfNeeded(); await page.keyboard.press('Enter');
        await detail.waitFor({ state: 'visible' });
        await page.waitForFunction(() => document.activeElement === document.querySelector('[data-detail-state] h1'));
        assert.equal(await page.evaluate(() => scrollY), 0, 'A new article does not inherit the previous Detail scroll');
        assert.equal(await more.getAttribute('aria-pressed'), 'false');
        await detail.getByRole('button', { name: 'HOME', exact: true }).click();
        await home.waitFor({ state: 'visible' });
        await page.goto(base + '/pivot/home?view=my&detailState=error');
        await page.locator('[data-my-state="unconfigured"]').waitFor();
        assert.equal(await my.getByRole('switch').count(), 0);
        assert.doesNotMatch(await page.locator('body').innerText(), /시연용|미뉴|example.com|회원탈퇴|하이큐/);
        await capture(page, 'my-service-' + name);
        await page.reload(); await page.locator('[data-my-state="unconfigured"]').waitFor();
        await my.getByRole('button', { name: 'HOME', exact: true }).click();
        await page.locator('[data-home-state="unconfigured"]').waitFor({ state: 'visible' });
        await page.locator('[data-home-state]').getByRole('button', { name: 'MY', exact: true }).click();
        await page.locator('[data-my-state="unconfigured"]').waitFor();
        assert.deepEqual(api, []); assert.deepEqual(external, []); assert.deepEqual(errors, []);
        results.push({ name, status: 'pass', browser: browser.version(), profile, geometries, before, beforeDetail, detailY, api, external, errors });
        console.log('PASS My Page ' + name);
      } catch (error) {
        results.push({ name, status: 'fail', error: error.stack, api, external, errors });
        await page.screenshot({ path: path.join(out, name + '-failure.png') }).catch(() => {});
        console.error('FAIL My Page ' + name + ': ' + error.stack);
      } finally { await context.close(); }
    }
  } finally { await browser.close(); fs.writeFileSync(path.join(out, 'results.json'), JSON.stringify(results, null, 2)); }
  if (results.some(r => r.status !== 'pass')) process.exitCode = 1;
})().catch(error => { console.error(error); process.exitCode = 1; });
