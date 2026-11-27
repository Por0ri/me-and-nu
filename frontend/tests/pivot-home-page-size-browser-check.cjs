// Run against a production build configured with the two loopback origins below.
// All API responses and images are local synthetic data; remote requests are blocked.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const { createApiFixture } = require('./helpers/pivot-api-fixture.cjs');
const { installPageScroll } = require('./helpers/pivot-page-scroll.cjs');
const base = process.argv[2] || 'http://localhost:3196';
const apiOrigin = process.env.PIVOT_FIXTURE_API_ORIGIN || 'http://localhost:8196';
const output = process.argv[3] || path.join(require('node:os').tmpdir(), 'pivot-home-page20');
for (const origin of [base, apiOrigin]) {
  assert.equal(new URL(origin).protocol, 'http:');
  assert.ok(['localhost', '127.0.0.1'].includes(new URL(origin).hostname));
}
const results = [];
const png = fs.readFileSync(path.join(__dirname, '../public/pivot/home/slide-1.png'));
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

async function gesture(page, track, dx, dy) {
  const box = await track.boundingBox();
  const x = box.x + box.width * .8, y = Math.max(170, box.y + 150);
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y, id: 1 }] });
  for (let i = 1; i <= 12; i++) {
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: x + dx * i / 12, y: y + dy * i / 12, id: 1 }] });
    await delay(20);
  }
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await cdp.detach();
}

async function verify(browser, width) {
  const context = await browser.newContext({ viewport: { width, height: width < 600 ? 844 : 1000 },
    isMobile: width < 600, hasTouch: width < 600, reducedMotion: 'reduce', serviceWorkers: 'block' });
  await installPageScroll(context);
  const fixture = createApiFixture({ apiOrigin, frontendOrigin: base }); fixture.rotate('active');
  fixture.contents.splice(0, fixture.contents.length, ...Array.from({ length: 123 }, (_, i) => ({
    id: 1000 - i, title: `합성 카드 ${i + 1}`, summary: '로컬 검증', body: '로컬 검증 본문',
  })));
  const expected = fixture.contents.map(item => String(item.id));
  await context.addCookies([{ name: 'fixture_session', value: fixture.state.cookie, url: apiOrigin }]);
  let fail = true, cycle = true, imageRequests = 0;
  const requests = [], external = [], errors = [], checks = [];
  await context.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url());
    if (![base, apiOrigin].includes(url.origin)) { external.push(url.href); return route.abort(); }
    const headers = { 'access-control-allow-origin': base, 'access-control-allow-credentials': 'true' };
    const reply = (body, status = 200) => route.fulfill({ status, headers, contentType: 'application/json', body: JSON.stringify(body) });
    if (/\/contents\/\d+\/slides\/\d+\/image$/.test(url.pathname)) {
      const first = url.pathname === '/api/v1/contents/1000/slides/1/image';
      const attempt = first ? ++imageRequests : 0;
      if (first && attempt === 1) await delay(1500);
      return route.fulfill({ headers, status: width === 390 && first && attempt === 1 ? 404 : 200, contentType: 'image/png', body: png });
    }
    if (url.pathname.startsWith('/api/')) {
      const response = fixture.dispatch(url.href, { method: req.method(), headers: req.headers(), body: req.postData() });
      if (url.pathname === '/api/v1/topics/7/feed') {
        const limit = Number(url.searchParams.get('limit')), cursor = url.searchParams.get('cursor');
        assert.equal(limit, 20);
        requests.push({ limit, cursor });
        if (cursor === 'after:20' && fail) { fail = false; return reply({ code: 'UNAVAILABLE' }, 503); }
        const offset = cursor ? Number(cursor.replace('after:', '')) : 0;
        const body = response.body;
        body.sections[0].contents = body.sections[0].contents.slice(offset, offset + limit).map(card => ({ ...card,
          slides: [1, 2, 3].map(page => ({ page, role: page === 1 ? 'cover' : 'body', eyebrow: '합성',
            titleLines: [card.title], bodySentences: ['로컬 Mock'], width: 1308, height: 1632,
            imageUrl: `/api/v1/contents/${card.id}/slides/${page}/image?topicId=7`,
            layout: { template: 'cover', fitMode: 'contain', backgroundStyle: 'gradient', imageScale: 1,
              imageOffsetY: 0, contrastMode: 'dark', blurBackground: false,
              imageBox: { x: 0, y: 0, width: 1, height: 1 }, copyBox: { x: 0, y: .6, width: 1, height: .4 },
              textOverlayBox: { x: 0, y: .6, width: 1, height: .4 } },
          })),
        }));
        body.nextCursor = offset + limit < 123 ? `after:${offset + limit}` : null;
        if (cursor === 'after:40' && cycle) { cycle = false; body.nextCursor = 'after:20'; }
        return reply(body);
      }
      if (url.pathname === '/api/v1/users/me') return reply({ userId: 12, nickname: '검증 회원', accountType: 'user', email: null });
      // Auxiliary background requests stay in memory too.
      if (url.pathname === '/api/v1/users/me/notification-settings') return reply({ inAppEnabled: true, nightInAppEnabled: false, version: 1 });
      if (url.pathname.endsWith('/events')) return reply({ accepted: true });
      return route.fulfill({ status: response.status, headers: { ...headers, ...response.headers },
        contentType: 'application/json', body: response.body === undefined ? undefined : JSON.stringify(response.body) });
    }
    if (url.origin !== base) { external.push(url.href); return route.abort(); }
    return route.continue();
  });
  const page = await context.newPage(); page.setDefaultTimeout(15000);
  page.on('pageerror', error => errors.push(error.message));
  const cards = page.locator('[data-home-post]');
  const ids = () => cards.evaluateAll(elements => elements.map(el => el.dataset.homePost));
  const home = page.locator('[data-home-state]');
  try {
    await page.goto(base + '/pivot/home');
    const loader = page.locator('[data-home-entry-loading]');
    await loader.waitFor();
    await page.waitForTimeout(300);
    assert.equal(await loader.count(), 1, 'first image holds entry loading');
    await loader.waitFor({ state: 'detached' });
    assert.deepEqual(await ids(), expected.slice(0, 20));
    assert.deepEqual(requests, [{ limit: 20, cursor: null }], 'no eager later pages');
    if (width === 390) {
      await page.getByRole('button', { name: '이미지 다시 시도', exact: true }).click();
      await page.getByRole('button', { name: '이미지 다시 시도', exact: true }).waitFor({ state: 'detached' });
      await page.waitForFunction(() => document.querySelector('[data-home-post] [data-carousel-slide="1"] img')?.naturalWidth > 0);
      assert.equal(await loader.count(), 0);
      checks.push('first image failure/retry without reopening loader');
    }
    checks.push('first image readiness; exactly 20 initial cards');
    await page.screenshot({ path: path.join(output, `initial-20-${width}.png`) });
    const track = cards.first().locator('[data-carousel-track]');
    if (width < 600) {
      await gesture(page, track, -220, 0);
      await page.waitForFunction(() => document.querySelector('[data-carousel-track]').scrollLeft > 100);
      await gesture(page, track, 0, -230);
      await page.waitForFunction(() => window.pivotScrollRoot().scrollTop > 50);
      checks.push('horizontal touch swipe and vertical touch scroll');
    } else {
      await track.focus(); await page.keyboard.press('ArrowRight');
      await page.waitForFunction(() => document.querySelector('[data-carousel-track]').scrollLeft > 100);
      await page.mouse.wheel(0, 500);
      await page.waitForFunction(() => window.pivotScrollRoot().scrollTop > 50);
      checks.push('desktop carousel keyboard and vertical wheel');
    }
    const more = () => page.locator('[data-home-pagination] button');
    await more().click();
    await page.getByRole('button', { name: '다음 콘텐츠 다시 시도', exact: true }).waitFor();
    assert.deepEqual(await ids(), expected.slice(0, 20));
    await more().click();
    await page.waitForFunction(() => document.querySelectorAll('[data-home-post]').length === 40);
    await more().click();
    await page.getByRole('button', { name: '다음 콘텐츠 다시 시도', exact: true }).waitFor();
    assert.deepEqual(await ids(), expected.slice(0, 40));
    await page.waitForTimeout(200);
    assert.deepEqual(requests.map(r => r.cursor), [null, 'after:20', 'after:20', 'after:40']);
    while (await more().count()) {
      const before = await cards.count();
      await more().click();
      await page.waitForFunction(n => document.querySelectorAll('[data-home-post]').length > n, before);
    }
    assert.deepEqual(await ids(), expected);
    assert.deepEqual(requests.map(r => r.cursor), [null, 'after:20', 'after:20', 'after:40', 'after:40', 'after:60', 'after:80', 'after:100', 'after:120']);
    checks.push('123 cards in server order; failure/retry and cyclic cursor guard');
    const target = cards.nth(20).locator('[data-home-detail]');
    await target.scrollIntoViewIfNeeded(); await target.focus();
    const top = await page.evaluate(() => window.pivotScrollRoot().scrollTop);
    await target.click();
    const detail = page.locator('[data-detail-state="ready"]:visible'); await detail.waitFor();
    await detail.getByRole('button', { name: '저장', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('[data-detail-state="ready"] button[aria-label="저장"]')?.getAttribute('aria-pressed') === 'true');
    await detail.getByRole('button', { name: '뒤로가기', exact: true }).click();
    await page.waitForFunction(t => Math.abs(window.pivotScrollRoot().scrollTop - t) < 2, top);
    assert.equal(await target.evaluate(el => el === document.activeElement), true);
    assert.equal(await cards.nth(20).locator('[data-service-bookmark]').getAttribute('aria-pressed'), 'true');
    checks.push('card 21 DETAIL → HOME restores scroll/focus and saved state');
    await home.getByRole('button', { name: 'SAVE', exact: true }).click();
    await page.locator('[data-saved-state="ready"]:visible').waitFor();
    await page.locator('[data-saved-content]').first().click(); await detail.waitFor();
    await detail.getByRole('button', { name: '저장', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('[data-detail-state="ready"] button[aria-label="저장"]')?.getAttribute('aria-pressed') === 'false');
    await detail.getByRole('button', { name: 'HOME', exact: true }).click();
    assert.equal(await cards.nth(20).locator('[data-service-bookmark]').getAttribute('aria-pressed'), 'false');
    await home.getByRole('button', { name: 'MY', exact: true }).click();
    await page.locator('[data-my-state="ready"]:visible').waitFor();
    await page.getByRole('button', { name: 'HOME', exact: true }).click();
    assert.deepEqual(await ids(), expected);
    assert.equal(requests.length, 9, 'navigation keeps loaded pages');
    assert.equal(await loader.count(), 0);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    checks.push('SAVE/MY navigation; bookmark removal sync; retained pages; no overflow');
    await page.evaluate(() => window.pivotScrollRoot().scrollTo(0, 0));
    await page.waitForFunction(() => document.querySelector('[data-home-post] [data-carousel-slide][aria-hidden="false"] img')?.naturalWidth > 0);
    await page.screenshot({ path: path.join(output, `home-${width}.png`) });
    assert.deepEqual(external, []); assert.deepEqual(errors, []);
    results.push({ width, status: 'pass', checks, requests, external, errors });
    console.log(`PASS HOME page20 ${width}: ${checks.join('; ')}`);
  } catch (error) {
    results.push({ width, status: 'fail', checks, requests, external, errors, error: error.stack });
    await page.screenshot({ path: path.join(output, `failure-${width}.png`) });
    throw error;
  } finally { await context.close(); }
}
(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: process.env.PIVOT_BROWSER_CHANNEL || 'chrome' });
  try {
    for (const width of [375, 390, 1440]) await verify(browser, width);
  } finally { await browser.close(); fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(results, null, 2)); }
})().catch(error => { console.error(error); process.exitCode = 1; });
