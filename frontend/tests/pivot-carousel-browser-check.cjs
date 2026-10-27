// Real API check against an explicitly prepared disposable localhost database.
// No provider bypass, remote hosts, production DB access, or fixture API server.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const base = process.argv[2] || 'http://localhost:3331';
const output = path.resolve(process.argv[3] || 'out/carousel-e2e');
const api = process.env.PIVOT_API_ORIGIN || 'http://localhost:8211';
for (const origin of [base, api]) {
  const url = new URL(origin);
  assert.equal(url.protocol, 'http:');
  assert.equal(url.hostname, 'localhost', 'only disposable localhost services are supported');
}
const sessions = JSON.parse(fs.readFileSync(path.join(output, 'sessions.json'), 'utf8'));
const fixtures = JSON.parse(fs.readFileSync(path.join(output, 'fixture-ids.json'), 'utf8'));
const ids = Object.fromEntries(fixtures.items.map(item => [Number(item.request_id.split('-').at(-1)), String(item.content_id)]));
const results = [];
const errors = [];

async function until(predicate, message) {
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    if (await predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 75));
  }
  throw new Error(message);
}
async function slideIndex(track) {
  return track.evaluate(el => Math.round(el.scrollLeft / el.clientWidth));
}
async function atSlide(track, index) {
  return track.evaluate((el, expected) => el.clientWidth > 0 && Math.abs(el.scrollLeft - expected * el.clientWidth) < 1, index);
}
async function key(page, track, value, index) {
  await track.focus();
  await page.keyboard.press(value);
  await until(async () => await atSlide(track, index), `carousel ${value} settles at ${index}`);
}
async function swipe(page, track) {
  await track.scrollIntoViewIfNeeded();
  const box = await track.boundingBox();
  const x = box.x + box.width * .85;
  const y = Math.min(page.viewportSize().height - 80, Math.max(80, box.y + box.height * .4));
  const cdp = await page.context().newCDPSession(page);
  const point = px => [{ x: px, y, id: 1, radiusX: 2, radiusY: 2, force: 1 }];
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: point(x) });
  for (let i = 1; i <= 14; i++) {
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: point(x - box.width * .7 * i / 14) });
    await page.waitForTimeout(18);
  }
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await until(async () => await atSlide(track, 1), 'native touch swipe settles at second slide');
  await cdp.detach();
}
async function screenshot(page, name) {
  await page.evaluate(() => document.fonts.ready);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'no horizontal document overflow');
  // Full-page capture can resize Chromium's viewport and re-snap nested tracks.
  // Keep the user's viewport fixed while testing navigation restoration.
  await page.screenshot({ path: path.join(output, name) });
}
async function newContext(browser, viewport, index = 0) {
  const context = await browser.newContext({ viewport, reducedMotion: 'reduce', isMobile: viewport.width < 600, hasTouch: true });
  context.setDefaultTimeout(15000);
  context.setDefaultNavigationTimeout(60000);
  await context.addCookies([{ name: sessions[index].name, value: sessions[index].value, domain: 'localhost', path: '/', httpOnly: true, sameSite: 'Lax' }]);
  return context;
}

(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.PIVOT_BROWSER_EXECUTABLE
    ? { executablePath: process.env.PIVOT_BROWSER_EXECUTABLE } : { channel: 'chrome' }) });
  try {
    let contextIndex = 0;
    for (const viewport of [{ width: 390, height: 844 }, { width: 1280, height: 900 }]) {
      const context = await newContext(browser, viewport, contextIndex++);
      const page = await context.newPage();
      const imageResponses = [];
      page.on('pageerror', error => errors.push(error.message));
      page.on('response', response => {
        const url = new URL(response.url());
        if (url.origin === api && /\/slides\/\d+\/image$/.test(url.pathname)) imageResponses.push(response);
      });
      await page.goto(`${base}/pivot/home`);
      await page.locator('main[data-home-state="ready"]').waitFor();
      assert.equal(await page.locator('[data-home-post]').count(), 3);
      const three = page.locator(`[data-home-post="${ids[3]}"]`);
      const ten = page.locator(`[data-home-post="${ids[10]}"]`);
      assert.equal(await three.locator('[data-home-slide]').count(), 3);
      assert.equal(await ten.locator('[data-home-slide]').count(), 10);
      await until(async () => await three.locator('img[src^="blob:"]').count() > 0, 'authenticated PNG rendered as blob');
      const track = three.locator('[data-home-track]');
      await key(page, ten.locator('[data-home-track]'), 'End', 9);
      await key(page, ten.locator('[data-home-track]'), 'Home', 0);
      await until(async () => await ten.locator('[data-carousel-slide="1"] img[data-carousel-image-state="ready"]').count() === 1,
        'first image reloads after End then Home');
      await key(page, track, 'Home', 0);
      if (viewport.width < 600) await swipe(page, track);
      else await key(page, track, 'ArrowRight', 1);
      await screenshot(page, `home-${viewport.width}.png`);
      assert.equal(await slideIndex(track), 1, 'expected home position before opening detail');
      await three.locator('[data-home-detail]').click();
      const detail = page.locator('main[data-detail-state="ready"]');
      await detail.waitFor();
      assert.match(await detail.innerText(), /3장 캐러셀의 독립 상세 본문/);
      assert.match(await detail.innerText(), /AI/);
      assert.equal(await detail.locator('a[href="https://example.com/official"]').count(), 1);
      assert.equal(await detail.locator('a[href="https://example.com/supplement"]').count(), 1);
      const cover = detail.locator('[data-content-cover]');
      assert.equal(await cover.count(), 1, 'detail retains one cover above the independent article');
      assert.equal(await cover.locator('[data-carousel-overlay]').count(), 1, 'first-slide text remains on the cover');
      assert.equal(await detail.locator('[data-carousel-track], [data-detail-track], [data-carousel-slide]').count(), 0);
      assert.equal(await detail.getByRole('button', { name: '이전 슬라이드', exact: true }).count(), 0);
      assert.equal(await detail.getByRole('button', { name: '다음 슬라이드', exact: true }).count(), 0);
      await until(async () => await cover.locator('img[src^="blob:"]').count() === 1, 'detail authenticated single-cover PNG');
      const save = detail.locator(`[data-service-bookmark="${ids[3]}"]`);
      if (await save.getAttribute('aria-pressed') === 'true') {
        await save.click();
        await until(async () => await save.getAttribute('aria-pressed') === 'false', 'reset prior synthetic bookmark');
      }
      await save.click();
      await until(async () => await save.getAttribute('aria-pressed') === 'true', 'bookmark persisted through real CSRF API');
      await screenshot(page, `detail-${viewport.width}.png`);
      await detail.getByRole('button', { name: '뒤로가기', exact: true }).click();
      try { await until(async () => await atSlide(track, 1), 'detail Back restores exact home slide position'); }
      catch (error) {
        console.error(JSON.stringify(await page.evaluate(() => ({ url: location.href, snapshot: history.state?.pivotDetailHome,
          tracks: [...document.querySelectorAll('[data-home-post]')].map(post => {
            const el = post.querySelector('[data-home-track]'); return { id: post.dataset.homePost, x: el.scrollLeft, width: el.clientWidth };
          }) })), null, 2));
        throw error;
      }
      assert.equal(await three.locator('[data-service-bookmark]').getAttribute('aria-pressed'), 'true');
      await page.reload();
      await page.locator('main[data-home-state="ready"]').waitFor();
      assert.equal(await three.locator('[data-service-bookmark]').getAttribute('aria-pressed'), 'true');
      await page.locator('main[data-home-state="ready"] [data-saved-navigation]').click();
      const saved = page.locator('main[data-saved-state="ready"]');
      await saved.waitFor();
      const savedCard = saved.locator(`[data-saved-content="${ids[3]}"]`);
      await until(async () => await savedCard.locator('img[src^="blob:"]').count() === 1, 'saved cover uses authenticated image');
      await savedCard.click();
      await detail.waitFor();
      assert.match(await detail.innerText(), /3장 캐러셀의 독립 상세 본문/);
      assert.ok(imageResponses.some(response => response.status() === 200), 'real image API response');
      const response = imageResponses.find(response => response.status() === 200);
      assert.match((await response.allHeaders())['cache-control'], /private.*no-store/);
      assert.match((await response.request().allHeaders()).cookie, new RegExp(sessions[contextIndex-1].name));
      await context.clearCookies();
      await page.reload();
      await page.waitForURL('**/pivot/login');
      assert.equal(await page.locator('[data-home-post]').count(), 0, 'logged out content removed');
      results.push(`${viewport.width}px: home 3/10 slides, legacy content, real cookie PNG, keyboard/touch, detail single cover and sources/body/AI, bookmark persistence and saved cover, Back position, expired session`);
      await context.close();
    }
    const context = await newContext(browser, { width: 390, height: 844 });
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => {
      const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL);
      window.__carouselUrls = { live: new Set(), created: 0, revoked: 0 };
      URL.createObjectURL = blob => { const url = create(blob); window.__carouselUrls.live.add(url); window.__carouselUrls.created++; return url; };
      URL.revokeObjectURL = url => { window.__carouselUrls.live.delete(url); window.__carouselUrls.revoked++; revoke(url); };
    });
    let failed = false, unauthorized = false;
    await context.route(`${api}/api/v1/contents/*/slides/*/image?topicId=1`, async route => {
      const pathname = new URL(route.request().url()).pathname;
      if (!failed && pathname === `/api/v1/contents/${ids[10]}/slides/1/image`) {
        failed = true;
        await route.fulfill({ status: 404, contentType: 'application/json', body: '{"code":"CONTENT_NOT_FOUND"}' });
      } else if (unauthorized) {
        await route.fulfill({ status: 401, contentType: 'application/json', body: '{"code":"AUTH_REQUIRED"}' });
      } else await route.continue();
    });
    await page.goto(`${base}/pivot/home`);
    const ten = page.locator(`[data-home-post="${ids[10]}"]`);
    const first = ten.locator('[data-carousel-slide="1"]');
    await first.locator('[data-carousel-image-state="error"]').waitFor();
    assert.match(await first.innerText(), /현재 제공되지 않는 이미지/);
    await first.getByRole('button', { name: '이미지 다시 시도', exact: true }).click();
    await first.locator('img[data-carousel-image-state="ready"]').waitFor();
    await key(page, ten.locator('[data-home-track]'), 'End', 9);
    await key(page, ten.locator('[data-home-track]'), 'Home', 0);
    await first.locator('img[data-carousel-image-state="ready"]').waitFor();
    assert.equal(await first.locator('[data-carousel-image-state="error"]').count(), 0);
    unauthorized = true;
    await ten.locator('[data-home-track]').focus();
    await page.keyboard.press('End');
    await page.waitForURL('**/pivot/login');
    await until(async () => await page.evaluate(() => window.__carouselUrls.live.size === 0), 'session boundary revokes all protected object URLs');
    assert.equal(await page.locator('img[src^="blob:"]').count(), 0);
    results.push('Image 404 -> explicit retry -> PNG recovery; End/Home re-entry; image 401 -> login and all protected blob URLs revoked');
    await context.close();
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output, 'browser-results.json'), JSON.stringify({ results, errors }, null, 2));
    console.log(JSON.stringify({ results, errors }, null, 2));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
