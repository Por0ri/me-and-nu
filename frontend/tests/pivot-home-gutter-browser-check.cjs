// Real browser wheel/touch input; loopback Demo only, no backend or dependencies.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const { installPageScroll } = require('./helpers/pivot-page-scroll.cjs');
const base = process.argv[2] || 'http://127.0.0.1:3196';
const out = process.argv[3] || path.join(require('node:os').tmpdir(), 'pivot-home-gutter');
assert.ok(['localhost', '127.0.0.1'].includes(new URL(base).hostname) && new URL(base).protocol === 'http:');
const near = (a, b, label) => assert.ok(Math.abs(a - b) < 1, `${label}: ${a} / ${b}`);
const results = [];
const y = page => page.locator('[data-home-state]').evaluate(e => e.scrollTop);
const scroll = (page, top) => page.locator('[data-home-state]').evaluate(async (e, top) => {
  e.scrollTo({ top, behavior: 'instant' });
  await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
}, top);
async function wheel(page, x, deltaY, deltaX = 0) {
  await page.mouse.move(x, 300); await page.mouse.wheel(deltaX, deltaY); await page.waitForTimeout(250);
}
async function drag(page, dx, dy) {
  const cdp = await page.context().newCDPSession(page);
  const track = page.locator('[data-home-track]').first(), b = await track.boundingBox();
  const x = b.x + b.width * .7, y = b.y + 100;
  const points = (x, y) => [{ x, y, id: 1 }];
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: points(x, y) });
  for (let i = 1; i <= 16; i++) {
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: points(x + dx * i / 16, y + dy * i / 16) });
    await page.waitForTimeout(20);
  }
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await page.waitForTimeout(500); await cdp.detach();
}
(async () => {
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch({ channel: process.env.PIVOT_BROWSER_CHANNEL || 'msedge', headless: true });
  try {
    for (const width of [375, 1280, 1440]) {
      if (process.env.PIVOT_VIEWPORT_WIDTH && width !== Number(process.env.PIVOT_VIEWPORT_WIDTH)) continue;
      const context = await browser.newContext({ viewport: { width, height: width === 375 ? 812 : 900 }, hasTouch: true, reducedMotion: 'reduce', serviceWorkers: 'block' });
      await installPageScroll(context);
      const errors = [], unexpected = [];
      await context.route('**/*', route => {
        const url = new URL(route.request().url());
        if (url.origin !== new URL(base).origin || url.pathname.startsWith('/api/')) { unexpected.push(url.href); return route.abort(); }
        return route.continue();
      });
      const page = await context.newPage(); page.setDefaultTimeout(12000); page.on('pageerror', e => errors.push(e.message));
      try {
        await page.goto(base + '/pivot/demo?view=home');
        const home = page.locator('[data-home-state="ready"]'); await home.waitFor();
        await page.waitForFunction(() => document.activeElement === document.querySelector('[data-home-state] h1'));
        await page.evaluate(() => document.fonts.ready);
        await home.locator('img').evaluateAll(items => Promise.all(items.filter(e => e.complete).map(e => e.decode().catch(() => {}))));
        await page.waitForTimeout(150);
        const geometry = await home.evaluate(e => {
          const rect = e => e.getBoundingClientRect().toJSON();
          return { port: rect(e), card: rect(e.querySelector('[data-home-post]')), header: rect(e.querySelector('header')),
            nav: rect(e.querySelector('nav')), ai: rect(e.querySelector('[data-home-ai]')) };
        });
        near(geometry.port.width, width, 'Native scrollport includes both gutters');
        near(geometry.card.width, Math.min(width, 430) - 48, 'Unchanged card width');
        near(geometry.card.x, (width - Math.min(width, 430)) / 2 + 24, 'Unchanged card inset');
        near(geometry.header.width, Math.min(width, 430), 'Unchanged header width');
        near(geometry.ai.x, width / 2 + Math.min(307, width - 68) / 2 - 48, 'Unchanged AI anchor');
        near(geometry.nav.x, width / 2 - 100, 'Unchanged NAV anchor');
        const samples = [];
        for (const [label, x] of [['left', 20], ['right', width - 20], ['center', width / 2]]) {
          await scroll(page, 200); await wheel(page, x, 120);
          near(await y(page), 320, label + ' wheel scrolls exactly once');
          await wheel(page, x, -30); near(await y(page), 290, label + ' reverse wheel');
          samples.push({ label, down: 320, up: 290 });
        }
        await scroll(page, 200);
        for (let i = 0; i < 8; i++) await wheel(page, 20, 2.5);
        near(await y(page), 220, 'Fractional trackpad deltas');
        // Headless CDP wheel does not invoke browser zoom shortcuts. Check that
        // app code leaves the cancelable Ctrl+wheel event to the browser.
        assert.equal(await home.evaluate(e => e.dispatchEvent(new WheelEvent('wheel', {
          deltaY: 120, ctrlKey: true, bubbles: true, cancelable: true,
        }))), true, 'Ctrl+wheel is not canceled by the app');
        near(await y(page), 220, 'No scripted Ctrl+wheel forwarding');
        await wheel(page, 20, 0, 100); near(await y(page), 220, 'Horizontal gutter input does not scroll vertically');
        await scroll(page, 0);
        const track = home.locator('[data-home-track]').first(), box = await track.boundingBox();
        await page.mouse.move(box.x + 50, box.y + 100); await page.mouse.wheel(160, 0);
        await page.waitForFunction(() => document.querySelector('[data-home-track]').scrollLeft > 0);
        near(await y(page), 0, 'Horizontal carousel wheel preserves vertical position');
        await track.evaluate(e => e.scrollTo({ left: 0, behavior: 'instant' }));
        // A nested scrollable fixture must retain native input ownership.
        await home.evaluate(e => {
          const nested = document.createElement('div'); nested.id = 'nested-scroll-fixture';
          nested.style.cssText = 'position:fixed;top:150px;left:4px;width:80px;height:160px;overflow:auto;z-index:20';
          const inner = document.createElement('div'); inner.style.height = '800px'; nested.append(inner); e.append(nested);
        });
        await wheel(page, 30, 80);
        near(await page.locator('#nested-scroll-fixture').evaluate(e => e.scrollTop), 80, 'Nested scroll owns the wheel');
        near(await y(page), 0, 'Nested wheel does not duplicate into feed');
        await page.locator('#nested-scroll-fixture').evaluate(e => e.remove());
        if (width === 375) {
          await drag(page, -210, 0);
          assert.ok(await track.evaluate(e => e.scrollLeft > 100), 'Mobile horizontal swipe');
          near(await y(page), 0, 'Horizontal swipe does not move HOME vertically');
          await drag(page, 0, -100); assert.ok(await y(page) > 30, 'Mobile vertical drag over carousel');
        }
        const open = home.locator('[data-home-detail]').nth(1);
        await open.scrollIntoViewIfNeeded(); await open.focus();
        const before = await y(page), slideBefore = await track.evaluate(e => e.scrollLeft);
        await open.click();
        const detail = page.locator('[data-detail-state="ready"]:visible'); await detail.waitFor();
        const detailGeometry = await detail.evaluate(e => ({ nav: e.querySelector('nav').getBoundingClientRect().toJSON(), ai: e.querySelector('[aria-label="AI 질문 안내"]').getBoundingClientRect().toJSON() }));
        near(detailGeometry.nav.x, geometry.nav.x, 'HOME/DETAIL NAV alignment');
        near(detailGeometry.ai.x, geometry.ai.x, 'HOME/DETAIL AI alignment');
        const hiddenDetailY = await y(page);
        await wheel(page, 20, 120); near(await y(page), hiddenDetailY, 'Hidden HOME ignores Detail wheel');
        await detail.getByRole('button', { name: '뒤로가기', exact: true }).click(); await home.waitFor();
        await page.waitForFunction(top => Math.abs(document.querySelector('[data-home-state]').scrollTop - top) < 1, before);
        near(await track.evaluate(e => e.scrollLeft), slideBefore, 'Slide position restored');
        for (const [label, selector] of [['SAVE', '[data-saved-state]'], ['MY', '[data-my-state]']]) {
          await home.getByRole('button', { name: label, exact: true }).click();
          const screen = page.locator(selector + ':visible'); await screen.waitFor();
          near(await screen.evaluate(e => e.getBoundingClientRect().width), Math.min(width, 430), label + ' keeps its own width');
          const hiddenY = await y(page);
          await wheel(page, 20, 120); near(await y(page), hiddenY, label + ' does not scroll hidden HOME');
          await screen.getByRole('button', { name: 'HOME', exact: true }).click(); await home.waitFor();
        }
        await scroll(page, 0);
        await home.getByRole('button', { name: '알림', exact: true }).click();
        const inbox = page.getByRole('main', { name: '알림함', exact: true }); await inbox.waitFor();
        const inboxHomeY = await y(page);
        await wheel(page, 20, 120); near(await y(page), inboxHomeY, 'Inbox wheel does not reach HOME');
        await inbox.getByRole('button', { name: '뒤로가기', exact: true }).click(); await home.waitFor();
        // Repeated screen changes must not accumulate wheel handlers.
        await scroll(page, 200); await wheel(page, width - 20, 120); near(await y(page), 320, 'No duplicate input after navigation');
        const anchored = await home.locator('[data-home-ai]').boundingBox();
        near(anchored.x, geometry.ai.x, 'AI remains fixed after scroll'); near(anchored.y, geometry.ai.y, 'AI vertical anchor');
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
        assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
        await page.screenshot({ path: path.join(out, `home-${width}.png`) });
        results.push({ width, status: 'pass', browser: browser.version(), geometry, detailGeometry, samples, before, errors });
        console.log('PASS HOME gutters ' + width);
      } catch (error) {
        results.push({ width, status: 'fail', error: error.stack });
        await page.screenshot({ path: path.join(out, `failure-${width}.png`) }); console.error(error);
      } finally { await context.close(); }
    }
  } finally { await browser.close(); fs.writeFileSync(path.join(out, 'results.json'), JSON.stringify(results, null, 2)); }
  if (results.some(r => r.status === 'fail')) process.exitCode = 1;
})().catch(e => { console.error(e); process.exitCode = 1; });
