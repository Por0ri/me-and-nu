// Visual geometry and authored pose checks, in addition to H2-A touch regressions.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const { enterDemo, settled } = require('./pivot-home-browser-check.cjs');
const base = process.argv[2] || 'http://127.0.0.1:3100';
const out = process.argv[3] || path.join(require('node:os').tmpdir(), 'pivot-h2b');
fs.mkdirSync(out, { recursive: true });
const results = [];
async function rect(locator) {
  return locator.evaluate(e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e); return {
    x: r.x, y: r.y, width: r.width, height: r.height, radius: s.borderRadius, opacity: Number(s.opacity), fontSize: s.fontSize,
  }; });
}
const near = (actual, expected, message) => assert.ok(Math.abs(actual - expected) < 1, `${message}: ${actual} vs ${expected}`);
async function images(page) {
  await page.evaluate(async () => {
    await document.fonts.ready;
    await Promise.all([...document.images].map(image => {
      // Default/eager SSR images need no mutation before hydration.
      if (image.loading === 'lazy') image.loading = 'eager';
      return image.decode().catch(() => {});
    }));
  });
  assert.deepEqual(await page.locator('main img').evaluateAll(items => items.filter(i => !i.complete || !i.naturalWidth).map(i => i.src)), []);
}
(async () => {
  const browser = await chromium.launch({ headless: true, channel: process.env.PIVOT_BROWSER_CHANNEL || 'msedge' });
  try {
    for (const viewport of [{ width: 375, height: 812 }, { width: 390, height: 844 }]) {
      for (const motion of ['no-preference', 'reduce']) {
        const context = await browser.newContext({ viewport, isMobile: true, hasTouch: true, reducedMotion: motion });
        const page = await context.newPage(); page.setDefaultTimeout(10000);
        const api = [], external = [], errors = [];
        await context.route('**/*', async route => {
          const url = new URL(route.request().url());
          if (url.pathname.startsWith('/api/')) { api.push(url.href); await route.abort(); return; }
          if (url.origin !== new URL(base).origin) { external.push(url.href); await route.abort(); return; }
          await route.continue();
        });
        page.on('pageerror', e => errors.push(e.message));
        page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
        const name = `${viewport.width}-${motion}`;
        try {
          let samples = [], circle, poseGeometry;
          // Production preview does not expose /pivot/preview. The dedicated Splash
          // suite covers WebGPU, poses and timing through the real /pivot route.
          if (process.argv[4] !== 'home-only') {
          await page.goto(base + '/pivot/preview?screen=splash'); await images(page);
          const poses = page.locator('[data-splash-character-state]'); assert.equal(await poses.count(), 2);
          const animations = await poses.evaluateAll(items => items.map(e => ({ count: e.getAnimations().length,
            timing: e.getAnimations()[0]?.effect.getTiming() })));
          for (const time of [0, 799, 800, 1599, 1600]) {
            await poses.evaluateAll((items, time) => {
              for (const e of items) for (const animation of e.getAnimations()) { animation.pause(); animation.currentTime = time; }
            }, time);
            const opacity = await poses.evaluateAll(items => items.map(e => Number(getComputedStyle(e).opacity)));
            const expected = motion === 'reduce' || time < 800 || time >= 1600 ? [1, 0] : [0, 1];
            assert.deepEqual(opacity, expected, `Pose at ${time}ms`); samples.push({ time, opacity });
            if (time === 0 || time === 800) await page.screenshot({ path: path.join(out, `splash-${name}-${time}.png`), style: 'nextjs-portal { visibility: hidden !important; }' });
          }
          assert.deepEqual(animations.map(a => a.count), motion === 'reduce' ? [0, 0] : [1, 1]);
          if (motion !== 'reduce') for (const a of animations) { assert.equal(a.timing.duration, 1600); assert.equal(a.timing.iterations, Infinity); }
          circle = await rect(page.locator('[data-splash-background]'));
          near(circle.width, 375, 'Circle width'); near(circle.height, 375, 'Circle height');
          assert.ok(['static', 'initializing', 'animated', 'fallback'].includes(await page.locator('[data-splash-background]').getAttribute('data-splash-background')));
          assert.equal(await page.locator('canvas').count(), 1, 'Canvas is backed by a static fallback');
          assert.equal(await page.locator('img[src*="imgWifi"], img[src*="imgOutline"], img[src*="imgBatteryEnd"]').count(), 0);
          poseGeometry = [await rect(poses.nth(0)), await rect(poses.nth(1))];
          near(poseGeometry[0].height, 172, 'Rest crop'); near(poseGeometry[1].height, 207, 'Raised crop');
          }
          await enterDemo(page); await images(page);
          for (const track of await page.locator('[data-home-track]').all()) await settled(page, track);
          const logo = await rect(page.getByRole('img', { name: 'me;nu', exact: true }));
          const mascot = await rect(page.locator('[data-home-header] div[aria-hidden="true"]'));
          const search = await rect(page.getByRole('searchbox').locator('..'));
          const first = page.locator('[data-home-post]').first(), second = page.locator('[data-home-post]').nth(1);
          const card = await rect(first), nextCard = await rect(second);
          const detail = await rect(first.locator('[data-home-detail] > span'));
          const share = await rect(first.getByRole('button', { name: '공유', exact: true }).locator('img'));
          const navigation = await rect(page.getByRole('navigation', { name: '주요 메뉴' }));
          const ai = await rect(page.getByRole('button', { name: 'AI 콘텐츠 탐색', exact: true }));
          near(logo.x, 24, 'Logo x'); near(logo.width, 103, 'Logo width'); near(logo.height, 20.5862, 'Logo height');
          near(mascot.width, 68, 'Mascot width'); near(mascot.height, 36, 'Mascot height');
          near(search.width, viewport.width - 48, 'Search width'); near(search.height, 36, 'Search height');
          near(card.width, viewport.width - 48, 'Card width'); near(card.height / card.width, 408 / 327, 'Card ratio');
          near(nextCard.y - card.y - card.height, 24, 'Feed gap'); assert.equal(card.radius, '8px');
          near(detail.width, 68, 'Detail face width'); near(detail.height, 24, 'Detail face height');
          near(card.x + card.width - detail.x - detail.width, 12, 'Detail right inset');
          near(card.y + card.height - detail.y - detail.height, 12, 'Detail bottom inset');
          near(share.width, 28, 'Share icon'); near(navigation.width, 200, 'Navigation width'); near(navigation.height, 40, 'Navigation height');
          near(ai.width, 40, 'AI width'); near(ai.height, 40, 'AI height');
          if (viewport.width === 375) {
            // Figma's fake OS strip is 45px. Our truthful demo disclosure is 44px.
            near(logo.y, 54.70703125 - 1, 'Logo y'); near(search.y, 93 - 1, 'Search y'); near(card.y, 145 - 1, 'Feed y'); near(card.height, 408, 'Card height');
          }
          assert.match(await page.getByLabel('시연용 데모 안내').innerText(), /실제 추천 아님/);
          assert.deepEqual(await page.getByRole('navigation', { name: '주요 메뉴' }).getByRole('button').allTextContents(), ['HOME', 'SAVE', 'MY']);
          assert.equal(await page.locator('main a').count(), 0);
          assert.equal(await page.getByRole('dialog').count(), 0, 'AI-open state is not merged into Home');
          assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
          await page.screenshot({ path: path.join(out, `home-${name}.png`), style: 'nextjs-portal { visibility: hidden !important; }' });
          await first.getByRole('button', { name: '좋아요', exact: true }).focus(); await page.keyboard.press('Enter');
          assert.equal(await first.getByRole('button', { name: '좋아요', exact: true }).getAttribute('aria-pressed'), null);
          assert.equal(await first.getByText('좋아요 기능은 준비 중이에요. 아직 반영되지 않아요.', { exact: true }).isVisible(), true);
          await page.getByRole('button', { name: 'AI 콘텐츠 탐색', exact: true }).focus(); await page.keyboard.press('Enter');
          assert.match(await page.getByText('AI 콘텐츠 탐색은 준비 중이에요.', { exact: true }).innerText(), /준비 중/);
          assert.equal(await page.getByRole('dialog').count(), 0);
          assert.deepEqual(api, []); assert.deepEqual(external, []); assert.deepEqual(errors, []);
          results.push({ name, status: 'pass', viewport, motion, browser: browser.version(), samples, circle, poseGeometry,
            home: { logo, mascot, search, card, nextCard, detail, share, navigation, ai }, apiRequests: 0, externalRequests: 0, errors });
          console.log('PASS H2-B ' + name);
        } catch (error) {
          results.push({ name, status: 'fail', error: error.message, api, external, errors });
          await page.screenshot({ path: path.join(out, name + '-failure.png') }).catch(() => {});
          console.error('FAIL H2-B ' + name + ': ' + error.stack);
        } finally { await context.close(); }
      }
    }
  } finally { await browser.close(); fs.writeFileSync(path.join(out, 'visual-results.json'), JSON.stringify(results, null, 2)); }
  if (results.some(r => r.status !== 'pass')) process.exitCode = 1;
})().catch(error => { console.error(error); process.exitCode = 1; });
