// Existing Playwright + Edge only; loopback fixtures, fresh contexts, no real API.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { installPageScroll } = require('./helpers/pivot-page-scroll.cjs');
const path = require('node:path');
const { chromium } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const { createApiFixture } = require('./helpers/pivot-api-fixture.cjs');
const base = process.argv[2] || 'http://127.0.0.1:3195';
assert.ok(['localhost', '127.0.0.1'].includes(new URL(base).hostname));
const out = process.argv[3] || path.join(require('node:os').tmpdir(), 'pivot-navigation');
fs.mkdirSync(out, { recursive: true });
const results = [];
const near = (a, b, label) => assert.ok(Math.abs(a - b) < 1, `${label}: ${a} / ${b}`);
const overlap = (a, b) => a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
const nav = page => page.locator('main:visible nav[aria-label="주요 메뉴"]');
async function ready(page, screen, state = 'ready') {
  await page.locator(`[data-${screen}-state="${state}"]`).waitFor();
  await page.evaluate(async () => { await document.fonts.ready; });
  await page.waitForFunction(() => !document.querySelector('main[aria-busy="true"]'));
}
async function measure(page, active, { compact = false, inset = 0, ai = false } = {}) {
  const data = await nav(page).evaluate(el => {
    const rect = node => node.getBoundingClientRect().toJSON();
    const hit = (node, x, y) => node.contains(document.elementFromPoint(x, y));
    return { rect: rect(el), screen: rect(el.closest('main')), home: el.closest('main').hasAttribute('data-home-state'),
      overflow: document.documentElement.scrollWidth > innerWidth,
      buttons: [...el.querySelectorAll('button')].map(button => {
        const span = button.querySelector('span'), css = getComputedStyle(span), box = rect(button);
        return { label: button.textContent, active: button.getAttribute('aria-current'), rect: box, span: rect(span),
          font: css.fontFamily, size: css.fontSize, line: css.lineHeight, weight: css.fontWeight, background: css.backgroundColor,
          hits: [.15, .5, .85].map(x => hit(button, box.x + box.width * x, box.y + box.height / 2)) };
      }), inert: !!el.closest('[inert]') };
  });
  const { width, height } = page.viewportSize();
  near(data.rect.width, width <= 340 ? 180 : 200, 'NAV width');
  near(data.rect.height, compact ? 40 : 48, 'NAV height');
  near(data.rect.bottom, height - Math.max(compact ? 20 : 12, inset), 'NAV bottom');
  if (width === 375 && height === 812 && inset === 0) near(data.rect.y, 752, 'Figma y');
  assert.equal(data.overflow, false);
  assert.ok(data.rect.left >= 0 && data.rect.right <= width);
  if (data.home) {
    near(data.rect.x + data.rect.width / 2, width / 2 - (width < 600 ? 30 : 0), 'HOME dock group is centered');
  } else if (!ai || compact || width > 340) near(data.rect.x + data.rect.width / 2, width / 2, 'centered NAV');
  for (const [index, button] of data.buttons.entries()) {
    assert.equal(button.size, '12px'); assert.equal(button.line, '18px'); assert.match(button.font, /PivotPretendard/);
    assert.equal(button.weight, button.label === active ? '600' : '400');
    assert.equal(button.active, button.label === active ? 'page' : null);
    if (button.label === active) assert.equal(button.background, 'rgb(239, 231, 255)');
    assert.ok(button.span.left >= button.rect.left - .1 && button.span.right <= button.rect.right + .1, 'Pill stays inside its target');
    assert.ok(button.rect.top >= data.rect.top && button.rect.bottom <= data.rect.bottom);
    if (index) assert.equal(overlap(data.buttons[index - 1].rect, button.rect), false, 'Disjoint NAV targets');
    if (!compact) assert.ok(button.hits.every(Boolean), `${button.label} hit targets`);
  }
  assert.equal(data.inert, compact);
  if (ai && !compact) {
    const control = page.locator('main:visible').locator('[data-home-ai], button[aria-label="AI 질문 안내"]');
    await page.waitForFunction(() => {
      const main = [...document.querySelectorAll('main')].find(el => el.getBoundingClientRect().width > 0);
      const n = main.querySelector('nav').getBoundingClientRect();
      const a = main.querySelector('[data-home-ai], button[aria-label="AI 질문 안내"]').getBoundingClientRect();
      return !(a.left < n.right && a.right > n.left && a.top < n.bottom && a.bottom > n.top);
    });
    data.ai = await control.evaluate(el => el.getBoundingClientRect().toJSON());
    near(data.ai.width, 48, 'Figma AI width'); near(data.ai.height, 48, 'Figma AI height');
    assert.equal(overlap(data.rect, data.ai), false);
  }
  return data;
}
async function lastContent(page, selector) {
  await page.evaluate(() => window.pivotScrollRoot().scrollTo(0, window.pivotScrollRoot().scrollHeight));
  const last = await page.locator(selector).last().boundingBox();
  const n = await nav(page).boundingBox();
  assert.ok(last.y + last.height <= n.y, `${selector} can scroll clear of NAV`);
  return { contentBottom: last.y + last.height, navTop: n.y };
}
async function expanded(page, inset = 0) {
  const toggle = page.getByRole('button', { name: 'AI 질문 안내', exact: true });
  await toggle.click();
  await page.waitForFunction(() => document.querySelector('button[aria-label="AI 질문 안내"]')?.getAttribute('aria-expanded') === 'true');
  const target = await toggle.boundingBox();
  near(target.width, 40, 'Figma expanded AI width'); near(target.height, 40, 'Figma expanded AI height');
  assert.equal(await toggle.locator('img').getAttribute('src'), '/pivot/detail/ai-expanded.svg');
  const data = await measure(page, 'SAVE', { compact: true, ai: true, inset });
  const prompt = page.getByRole('button', { name: '이 콘텐츠에서 어떤 점이 궁금하세요?', exact: true });
  const bar = await prompt.evaluate(el => el.parentElement.getBoundingClientRect().toJSON());
  const promptTarget = await prompt.boundingBox();
  assert.ok(promptTarget.x + promptTarget.width <= target.x, 'AI prompt and toggle targets do not overlap');
  assert.ok(data.rect.top >= bar.top && data.rect.bottom <= bar.bottom, 'No vertical protrusion');
  assert.ok(data.rect.left >= bar.left && data.rect.right <= bar.right, 'Compact NAV fits inside AI prompt');
  const originalUrl = page.url();
  await nav(page).locator('button').first().evaluate(el => el.focus());
  assert.equal(await nav(page).evaluate(el => el.contains(document.activeElement)), false, 'inert prevents focus');
  await toggle.focus(); await page.keyboard.press('Shift+Tab');
  assert.equal(await prompt.evaluate(el => el === document.activeElement), true, 'Tab skips inert NAV');
  await page.keyboard.press('Tab'); assert.equal(await toggle.evaluate(el => el === document.activeElement), true);
  for (const button of data.buttons) {
    await page.mouse.click(button.rect.x + button.rect.width / 2, button.rect.y + button.rect.height / 2);
    assert.equal(page.url(), originalUrl, 'Covered NAV cannot navigate');
  }
  const notice = page.getByRole('status').filter({ hasText: 'AI 질문 기능은 준비 중이에요.' });
  assert.equal(await notice.evaluate(el => getComputedStyle(el).pointerEvents), 'none', 'AI notice lets input reach content');
  await page.screenshot({ path: path.join(out, `detail-expanded-${page.viewportSize().width}-inset${inset}.png`) });
  await toggle.click();
  assert.equal(await toggle.getAttribute('aria-expanded'), 'false');
  return data;
}
async function run(browser, viewport) {
  const context = await browser.newContext({ viewport, isMobile: viewport.width < 600, hasTouch: viewport.width < 600, serviceWorkers: 'block' });
  await installPageScroll(context);
  const page = await context.newPage(); page.setDefaultTimeout(15000);
  const errors = [], blocked = [], checks = {};
  const fixture = createApiFixture({ apiOrigin: 'http://127.0.0.1:8195', frontendOrigin: base }); fixture.rotate('active');
  let service = false;
  await context.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url());
    if (service && url.origin === 'http://127.0.0.1:8195' && url.pathname.startsWith('/api/')) {
      const reply = fixture.dispatch(url.href, { method: req.method(), headers: req.headers(), body: req.postData() });
      return route.fulfill({ status: reply.status, headers: reply.headers, contentType: 'application/json', body: reply.body === undefined ? undefined : JSON.stringify(reply.body) });
    }
    if (url.origin !== base || url.pathname.startsWith('/api/')) { blocked.push(url.href); return route.abort(); }
    if (url.pathname === '/favicon.ico') return route.fulfill({ status: 204 });
    return route.continue();
  });
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(base + '/pivot/demo?view=home'); await ready(page, 'home');
    checks.home = await measure(page, 'HOME', { ai: true });
    await page.screenshot({ path: path.join(out, `home-${viewport.width}.png`) });
    checks.homeLast = await lastContent(page, '[data-home-post]');
    await nav(page).getByRole('button', { name: 'HOME', exact: true }).click();
    assert.equal(await page.evaluate(() => window.pivotScrollRoot().scrollTop), 0, 'HOME resets the active scroll owner');
    assert.ok(await page.locator('[data-home-state] h1').evaluate(el => el === document.activeElement));
    checks.homeReset = true;
    // Native Tab / Shift+Tab, Enter and Space exercise the actual button handlers.
    await nav(page).getByRole('button', { name: 'HOME', exact: true }).focus();
    await page.keyboard.press('Tab'); assert.equal(await page.evaluate(() => document.activeElement.textContent), 'SAVE');
    await page.keyboard.press('Tab'); assert.equal(await page.evaluate(() => document.activeElement.textContent), 'MY');
    await page.keyboard.press('Shift+Tab'); assert.equal(await page.evaluate(() => document.activeElement.textContent), 'SAVE');
    await page.keyboard.press('Enter'); await ready(page, 'saved');
    checks.saved = await measure(page, 'SAVE');
    checks.savedLast = await lastContent(page, '[data-saved-content]');
    await page.screenshot({ path: path.join(out, `saved-${viewport.width}.png`) });
    await page.locator('[data-saved-content]').first().click(); await ready(page, 'detail');
    checks.detailFromSaved = await measure(page, 'SAVE', { ai: true });
    checks.detailLast = await lastContent(page, '[aria-labelledby="detail-feedback-title"]');
    checks.expanded = await expanded(page);
    checks.collapsed = await measure(page, 'SAVE', { ai: true });
    await page.screenshot({ path: path.join(out, `detail-${viewport.width}.png`) });
    await nav(page).getByRole('button', { name: 'MY', exact: true }).focus();
    await page.keyboard.press('Space'); await ready(page, 'my');
    checks.my = await measure(page, 'MY');
    checks.myLast = await lastContent(page, '#my-demo-help');
    await page.screenshot({ path: path.join(out, `my-${viewport.width}.png`) });
    await nav(page).getByRole('button', { name: 'HOME', exact: true }).click(); await ready(page, 'home');
    for (const control of await page.locator('[data-demo-bookmark]').all()) {
      if (await control.getAttribute('aria-pressed') === 'true') await control.click();
    }
    await nav(page).getByRole('button', { name: 'SAVE', exact: true }).click(); await ready(page, 'saved', 'empty');
    checks.empty = await measure(page, 'SAVE');
    // Real CSS env() override in Chromium; this is emulation, not physical-device evidence.
    const cdp = await context.newCDPSession(page);
    for (const inset of [16, 34]) {
      await cdp.send('Emulation.setSafeAreaInsetsOverride', { insets: { bottom: inset } });
      checks[`safeEmpty${inset}`] = await measure(page, 'SAVE', { inset });
      await nav(page).getByRole('button', { name: 'HOME', exact: true }).click(); await ready(page, 'home');
      checks[`safeHome${inset}`] = await measure(page, 'HOME', { inset, ai: true });
      checks[`safeHomeLast${inset}`] = await lastContent(page, '[data-home-post]');
      const anchor = await page.locator('[data-home-ai]').boundingBox();
      near(anchor.y, viewport.height - Math.max(12, inset) - 48, 'Fixed AI follows safe area');
      await page.locator('[data-demo-bookmark]').first().click();
      await nav(page).getByRole('button', { name: 'SAVE', exact: true }).click(); await ready(page, 'saved');
      checks[`safeSaved${inset}`] = await measure(page, 'SAVE', { inset });
      await page.locator('[data-saved-content]').first().click(); await ready(page, 'detail');
      checks[`safeDetail${inset}`] = await measure(page, 'SAVE', { inset, ai: true });
      checks[`safeDetailLast${inset}`] = await lastContent(page, '[aria-labelledby="detail-feedback-title"]');
      checks[`safeExpanded${inset}`] = await expanded(page, inset);
      await nav(page).getByRole('button', { name: 'MY', exact: true }).click(); await ready(page, 'my');
      checks[`safeMy${inset}`] = await measure(page, 'MY', { inset });
      checks[`safeMyLast${inset}`] = await lastContent(page, '#my-demo-help');
      await nav(page).getByRole('button', { name: 'HOME', exact: true }).click(); await ready(page, 'home');
      await page.locator('[data-demo-bookmark][aria-pressed="true"]').first().click();
      await nav(page).getByRole('button', { name: 'SAVE', exact: true }).click(); await ready(page, 'saved', 'empty');
    }
    await cdp.send('Emulation.setSafeAreaInsetsOverride', { insets: {} });
    service = true;
    fixture.state.failNext = { path: '/api/v1/users/me/bookmarks', status: 503, code: 'FIXTURE_UNAVAILABLE' };
    await page.goto(base + '/pivot/home?view=saved'); await ready(page, 'saved', 'error');
    checks.error = await measure(page, 'SAVE');
    await page.getByRole('button', { name: '다시 시도', exact: true }).click(); await ready(page, 'saved', 'empty');
    checks.recovered = await measure(page, 'SAVE');
    assert.deepEqual(errors, []); assert.deepEqual(blocked, []);
    results.push({ viewport, status: 'pass', checks, errors, blocked });
    console.log(`PASS NAV ${viewport.width}x${viewport.height}: ${Object.keys(checks).length} checks`);
  } catch (error) {
    results.push({ viewport, status: 'fail', error: error.stack, checks, errors, blocked });
    await page.screenshot({ path: path.join(out, `failure-${viewport.width}.png`) });
    console.error(`FAIL NAV ${viewport.width}: ${error.stack}`);
  } finally { await context.close(); }
}
(async () => {
  const browser = await chromium.launch({ headless: true, channel: process.env.PIVOT_BROWSER_CHANNEL || 'msedge' });
  try {
    for (const viewport of [{ width: 320, height: 568 }, { width: 375, height: 812 }, { width: 390, height: 844 },
      { width: 430, height: 932 }, { width: 1280, height: 900 }, { width: 1440, height: 1000 }]) {
      if (!process.env.PIVOT_VIEWPORT_WIDTH || viewport.width === Number(process.env.PIVOT_VIEWPORT_WIDTH)) await run(browser, viewport);
    }
  } finally { await browser.close(); fs.writeFileSync(path.join(out, 'results.json'), JSON.stringify(results, null, 2)); }
  if (results.some(result => result.status === 'fail')) process.exitCode = 1;
})().catch(error => { console.error(error); process.exitCode = 1; });
