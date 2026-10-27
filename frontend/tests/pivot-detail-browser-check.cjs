// Reuses existing Playwright and Edge. No package installation or backend calls.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const base = process.argv[2] || 'http://127.0.0.1:3100';
const out = process.argv[3] || path.join(require('node:os').tmpdir(), 'pivot-detail');
const results = [];
fs.mkdirSync(out, { recursive: true });
const story = '/pivot/demo?view=detail&postId=demo-home-story&contentId=demo-content-story';
async function detailReady(page, state = 'ready') {
  await page.locator(`[data-detail-state="${state}"]`).waitFor();
  await page.waitForFunction(() => document.activeElement === document.querySelector('[data-detail-state] h1'));
}
async function capture(page, name) {
  await page.evaluate(async () => { await document.fonts.ready; await Promise.all([...document.querySelectorAll('[data-detail-state] img')].map(i => i.decode().catch(() => {}))); });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'No horizontal overflow');
  await page.screenshot({ path: path.join(out, name + '.png'), fullPage: true, style: 'nextjs-portal { visibility: hidden !important; }' });
}
(async () => {
  const browser = await chromium.launch({ headless: true, channel: process.env.PIVOT_BROWSER_CHANNEL || 'msedge' });
  try {
    for (const viewport of [{ width: 375, height: 812 }, { width: 390, height: 844 }]) {
      for (const reducedMotion of ['no-preference', 'reduce']) {
        const context = await browser.newContext({ viewport, isMobile: true, hasTouch: true, reducedMotion });
        const page = await context.newPage(); page.setDefaultTimeout(12000);
        const api = [], external = [], errors = [];
        await context.route('**/*', async route => {
          const url = new URL(route.request().url());
          if (url.pathname.startsWith('/api/')) { api.push(url.href); await route.abort(); return; }
          if (url.origin !== new URL(base).origin) { external.push(url.href); await route.abort(); return; }
          await route.continue();
        });
        page.on('pageerror', e => errors.push(e.message));
        page.on('console', msg => { if (msg.type() === 'error') errors.push(msg.text()); });
        const name = `${viewport.width}-${reducedMotion}`;
        try {
          await page.goto(base + '/pivot/demo?view=home');
          const home = page.locator('[data-home-state="ready"]'); await home.waitFor();
          await page.getByRole('searchbox').fill('하이큐');
          const tracks = home.locator('[data-home-track]');
          // Independent positions, including a post below the fold.
          await tracks.nth(0).evaluate(e => e.scrollTo({ left: e.clientWidth * 2, behavior: 'instant' }));
          await tracks.nth(1).evaluate(e => e.scrollTo({ left: e.clientWidth, behavior: 'instant' }));
          const button = home.locator('[data-home-detail="demo-content-discovery"]');
          await button.focus(); await button.scrollIntoViewIfNeeded();
          const before = await page.evaluate(() => ({ y: scrollY, tracks: [...document.querySelectorAll('[data-home-track]')].map(e => Math.round(e.scrollLeft / e.clientWidth)) }));
          const title = await button.getAttribute('aria-label');
          await page.keyboard.press('Enter'); await detailReady(page);
          assert.match(page.url(), /contentId=demo-content-discovery/);
          assert.equal(await home.isVisible(), false);
          const h1 = await page.locator('[data-detail-state] h1').innerText(); assert.ok(title.includes(h1.split(' · ')[1]));
          const cover = page.locator('[data-detail-content] > img');
          assert.equal(await cover.getAttribute('src'), '/pivot/home/slide-1.png');
          assert.equal(await page.evaluate(() => scrollY), 0);
          await capture(page, 'detail-' + name);
          const geometry = await cover.boundingBox();
          assert.ok(Math.abs(geometry.x - 24) < 1); assert.ok(Math.abs(geometry.width - (viewport.width - 48)) < 1);
          assert.ok(Math.abs(geometry.height - geometry.width * 408 / 327) < 1);
          assert.equal(await page.locator('[data-detail-state] header img').evaluateAll(items => items.every(image => image.complete && image.naturalWidth > 0)), true);
          const icons = await page.locator('[data-detail-state] header img').evaluateAll(items => items.map(image => {
            const rect = image.getBoundingClientRect(); return { x: rect.x, width: rect.width, height: rect.height };
          }));
          assert.deepEqual(icons.map(icon => [icon.width, icon.height]), [[24, 24], [28, 28], [24, 24], [24, 24]]);
          assert.ok(Math.abs(icons[1].x - (viewport.width - 122)) < 1, 'Share matches the Figma slot');
          const screen = page.locator('[data-detail-state]');
          await screen.getByRole('button', { name: 'AI 질문 안내' }).click();
          await screen.getByRole('button', { name: '이 콘텐츠에서 어떤 점이 궁금하세요?' }).waitFor();
          await capture(page, 'detail-ai-' + name);
          await screen.getByRole('button', { name: 'AI 질문 안내' }).click();
          await screen.getByRole('button', { name: '더 볼래요', exact: true }).click();
          assert.equal(await screen.getByRole('button', { name: '더 볼래요', exact: true }).getAttribute('aria-pressed'), 'true');
          await screen.getByRole('button', { name: '그만 볼래요', exact: true }).click();
          assert.equal(await screen.getByRole('button', { name: '더 볼래요', exact: true }).getAttribute('aria-pressed'), 'false');
          assert.equal(await screen.getByRole('button', { name: '그만 볼래요', exact: true }).getAttribute('aria-pressed'), 'true');
          await screen.getByRole('button', { name: '그만 볼래요', exact: true }).click();
          assert.equal(await screen.getByRole('button', { name: '그만 볼래요', exact: true }).getAttribute('aria-pressed'), 'false');
          assert.ok(await page.evaluate(() => scrollY > 100), 'Body scrolls vertically');
          await screen.getByRole('button', { name: '뒤로가기', exact: true }).click();
          await page.waitForFunction(id => document.activeElement?.dataset.homeDetail === id, 'demo-content-discovery');
          const after = await page.evaluate(() => ({ y: scrollY, tracks: [...document.querySelectorAll('[data-home-track]')].map(e => Math.round(e.scrollLeft / e.clientWidth)) }));
          assert.ok(Math.abs(after.y - before.y) < 2, `Restored scroll ${after.y} / ${before.y}`); assert.deepEqual(after.tracks, before.tracks);
          assert.equal(await page.getByRole('searchbox').inputValue(), '하이큐');
          // Browser Forward / Back also restore positions and reset local feedback.
          await page.goForward(); await detailReady(page);
          assert.equal(await page.getByRole('button', { name: '더 볼래요', exact: true }).getAttribute('aria-pressed'), 'false');
          await page.reload(); await detailReady(page);
          await page.locator('[data-detail-state]').getByRole('button', { name: 'HOME', exact: true }).click();
          await page.waitForFunction(id => document.activeElement?.dataset.homeDetail === id, 'demo-content-discovery');
          assert.equal(await page.getByRole('searchbox').inputValue(), '하이큐');
          assert.deepEqual(await tracks.evaluateAll(items => items.map(e => Math.round(e.scrollLeft / e.clientWidth))), before.tracks);
          assert.ok(Math.abs(await page.evaluate(() => scrollY) - before.y) < 2);
          await page.reload(); await home.waitFor();
          await page.waitForFunction(id => document.activeElement?.dataset.homeDetail === id, 'demo-content-discovery');
          // Every canonical ID, fresh direct URL, refresh and safe HOME fallback.
          for (const id of ['story', 'discovery', 'reading']) {
            await page.goto(`${base}/pivot/demo?view=detail&postId=demo-home-${id}&contentId=demo-content-${id}`);
            await detailReady(page); assert.equal(await page.locator('[data-detail-content]').getAttribute('data-detail-content'), 'demo-content-' + id);
            await page.reload(); await detailReady(page);
            await page.locator('[data-detail-state]').getByRole('button', { name: 'HOME', exact: true }).click();
            await home.waitFor({ state: 'visible' }); assert.match(page.url(), /view=home/);
            await page.waitForFunction(() => document.activeElement === document.querySelector('[data-home-state] h1'));
          }
          for (const suffix of ['&contentId=missing&postId=demo-home-story', '&contentId=demo-content-story&postId=demo-home-reading', '&contentId=%3Cscript%3E&postId=demo-home-story', '']) {
            await page.goto(base + '/pivot/demo?view=detail' + suffix); await detailReady(page, 'not-found');
          }
          await capture(page, 'detail-not-found-' + name);
          for (const state of ['empty', 'error']) {
            await page.goto(base + story + '&detailState=' + state); await detailReady(page, state); await capture(page, 'detail-' + state + '-' + name);
            if (state === 'error') {
              await page.getByRole('button', { name: '다시 시도', exact: true }).click();
              await page.locator('[data-detail-state="error"]').waitFor();
              assert.equal(await page.getByRole('button', { name: '다시 시도', exact: true }).evaluate(e => e === document.activeElement), true);
            }
          }
          await page.goto(base + story); await detailReady(page);
          await capture(page, 'detail-story-' + name);
          assert.ok(await page.evaluate(() => document.documentElement.scrollHeight > innerHeight * 1.5), 'Long article extends beyond a viewport');
          await page.evaluate(() => window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'instant' }));
          const feedback = await page.getByRole('button', { name: '더 볼래요', exact: true }).boundingBox();
          assert.ok(feedback.y + feedback.height < viewport.height - 60, 'Feedback is reachable above the bottom controls');
          // Deliberately corrupt an image response without a network/404 error.
          await context.route('**/pivot/home/slide-1.png', route => route.fulfill({ status: 200, contentType: 'image/png', body: 'invalid-image' }));
          await page.reload(); await page.getByRole('img', { name: '대표 이미지 준비 중' }).waitFor();
          await capture(page, 'detail-missing-image-' + name);
          await context.unroute('**/pivot/home/slide-1.png');
          await page.goto(base + '/pivot/home?view=detail&postId=demo-home-story&contentId=demo-content-story&detailState=empty');
          await detailReady(page, 'not-found');
          const serviceUrl = base + '/pivot/home?view=detail&postId=301&contentId=301&topicId=9';
          await page.goto(serviceUrl);
          await detailReady(page, 'unconfigured');
          await page.reload(); await detailReady(page, 'unconfigured');
          assert.equal(new URL(page.url()).searchParams.get('topicId'), '9');
          await page.locator('[data-detail-state]').getByRole('button', { name: 'MY', exact: true }).click();
          await page.locator('[data-my-state="unconfigured"]').waitFor();
          assert.equal(new URL(page.url()).searchParams.get('topicId'), '9');
          await page.locator('[data-my-state]').getByRole('button', { name: 'SAVE', exact: true }).click();
          await page.locator('[data-saved-state="unconfigured"]').waitFor();
          assert.equal(new URL(page.url()).searchParams.get('topicId'), '9');
          await page.reload(); await page.locator('[data-saved-state="unconfigured"]').waitFor();
          await page.goBack(); await page.locator('[data-my-state="unconfigured"]').waitFor();
          await page.goBack(); await page.locator('[data-detail-state="unconfigured"]').waitFor({ state: 'visible' });
          assert.equal(new URL(page.url()).searchParams.get('topicId'), '9');
          await page.goForward(); await page.locator('[data-my-state="unconfigured"]').waitFor();
          assert.equal(new URL(page.url()).searchParams.get('topicId'), '9');
          for (const query of ['postId=301&contentId=301', 'postId=301&contentId=0&topicId=9', 'postId=301&contentId=301&topicId=9007199254740992']) {
            await page.goto(base + '/pivot/home?view=detail&' + query); await detailReady(page, 'not-found');
          }
          await page.goto(serviceUrl); await detailReady(page, 'unconfigured');
          assert.equal(await page.locator('[data-detail-content]').count(), 0);
          assert.equal(await page.getByRole('button', { name: '더 볼래요' }).count(), 0);
          assert.equal(await page.locator('body').innerText().then(text => /시연용|하이큐/.test(text)), false);
          assert.deepEqual(api, []); assert.deepEqual(external, []); assert.deepEqual(errors, []);
          results.push({ name, status: 'pass', before, after, geometry, api, external, errors }); console.log('PASS Detail ' + name);
        } catch (error) {
          results.push({ name, status: 'fail', error: error.stack, api, external, errors });
          await page.screenshot({ path: path.join(out, name + '-failure.png') }).catch(() => {}); console.error('FAIL Detail ' + name + ': ' + error.stack);
        } finally { await context.close(); }
      }
    }
  } finally { await browser.close(); fs.writeFileSync(path.join(out, 'results.json'), JSON.stringify(results, null, 2)); }
  if (results.some(r => r.status !== 'pass')) process.exitCode = 1;
})().catch(error => { console.error(error); process.exitCode = 1; });
