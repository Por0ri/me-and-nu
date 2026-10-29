// Reuse a local Playwright installation; never installs packages or calls a backend.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const base = process.argv[2] || 'http://127.0.0.1:3100';
const out = process.argv[3] || path.join(require('node:os').tmpdir(), 'pivot-home-h2a');
const results = [];
fs.mkdirSync(out, { recursive: true });

async function enterDemo(page, consumptionType = 'world') {
  await page.goto(base + '/pivot/demo');
  await page.getByRole('button', { name: '로그인 없이 시연 계속하기' }).click();
  await page.getByRole('checkbox', { name: '모두 동의합니다' }).check();
  await page.getByRole('button', { name: '시작하기', exact: true }).click();
  await page.getByRole('checkbox', { name: '애니메이션', exact: true }).focus();
  await page.keyboard.press('Space');
  await page.getByRole('button', { name: '애니메이션 선택하고 시작하기' }).click();
  await page.getByRole('searchbox', {name:'작품 검색',exact:true}).fill('별빛');
  await page.getByRole('list', {name:'작품 검색 결과',exact:true}).getByRole('button').filter({hasText:'별빛 우체국'}).click();
  await page.getByRole('button', {name:'선택한 작품으로 다음',exact:true}).click();
  await page.getByRole('checkbox', {name:'우정',exact:true}).check();
  await page.getByRole('checkbox', {name:'판타지',exact:true}).check();
  await page.getByRole('button', {name:'장르·소재 선택 완료',exact:true}).click();
  await page.locator(`input[name=consumptionType][value=${consumptionType}]`).locator('..').tap();
  await page.getByRole('button', { name: '선택 완료', exact: true }).click();
  await page.getByRole('button', { name: '콘텐츠 보러가기' }).click();
  await page.locator('main[data-home-state="ready"]').waitFor();
}
async function position(track) {
  return track.evaluate(e => ({ x: e.scrollLeft, width: e.clientWidth, page: Math.round(e.scrollLeft / e.clientWidth), y: scrollY }));
}
async function settled(page, track) {
  let last, stable = 0;
  for (let i = 0; i < 100; i++) {
    await page.waitForTimeout(50);
    const now = await position(track);
    if (last && Math.abs(now.x - last.x) < .1 && Math.abs(now.y - last.y) < .1 && Math.abs(now.x - now.page * now.width) < 1) stable++;
    else stable = 0;
    if (stable >= 4) return now;
    last = now;
  }
  throw new Error('Native scrolling did not settle: ' + JSON.stringify(last));
}
async function center(page, track) {
  await track.evaluate(e => e.scrollIntoView({ block: 'center', behavior: 'instant' }));
  await settled(page, track);
}
async function key(page, track, value, expected) {
  await track.focus(); await page.keyboard.press(value);
  await page.waitForFunction(({ id, expected }) => {
    const e = document.getElementById(id);
    return Math.abs(e.scrollLeft - expected * e.clientWidth) < 1;
  }, { id: await track.getAttribute('id'), expected });
  await settled(page, track);
}
async function swipe(page, cdp, track, dx, dy, fast = false) {
  const box = await track.boundingBox(), viewport = page.viewportSize();
  const x = box.x + box.width / 2 - dx / 2;
  const y = Math.min(viewport.height - 20, Math.max(20, box.y + box.height / 2 - dy / 2));
  assert.ok(x > 0 && x + dx > 0 && x < viewport.width && x + dx < viewport.width);
  const point = (px, py) => [{ x: px, y: py, id: 1, radiusX: 2, radiusY: 2, force: 1 }];
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: point(x, y) });
  const steps = fast ? 6 : 16, pending = [];
  for (let step = 1; step <= steps; step++) {
    const action = cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: point(x + dx * step / steps, y + dy * step / steps) });
    if (fast) pending.push(action); else await action;
    await page.waitForTimeout(fast ? 8 : 16);
  }
  if (!fast) await page.waitForTimeout(80);
  pending.push(cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] }));
  await Promise.all(pending);
  return settled(page, track);
}
async function indicator(post, expected, count) {
  assert.match(await post.locator('[data-home-position]').innerText(), new RegExp(`${expected + 1} / ${count}$`));
  assert.equal(await post.locator('[aria-current="true"]').getAttribute('aria-label'), `${expected + 1}번 슬라이드 보기`);
}
async function noOverflow(page) {
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  for (const post of await page.locator('[data-home-post]').all()) {
    const box = await post.boundingBox();
    assert.ok(box.x >= 0 && box.x + box.width <= page.viewportSize().width + 1);
  }
}

module.exports = { enterDemo, position, settled, key, center, noOverflow };
if (require.main === module) (async () => {
  const browser = await chromium.launch({ headless: true, channel: process.env.PIVOT_BROWSER_CHANNEL || 'msedge' });
  try {
    for (const viewport of [{ width: 375, height: 812 }, { width: 390, height: 844 }]) {
      for (const motion of ['no-preference', 'reduce']) {
        const context = await browser.newContext({ viewport, isMobile: true, hasTouch: true, reducedMotion: motion, serviceWorkers: 'block' });
        const api = [], external = [], errors = [];
        let failImage = false;
        await context.route('**/*', async route => {
          const url = new URL(route.request().url());
          if (url.pathname.startsWith('/api/')) { api.push(url.href); await route.abort(); return; }
          if (url.origin !== new URL(base).origin) { external.push(url.href); await route.abort(); return; }
          if (failImage && url.pathname === '/pivot/home/slide-2.png') {
            await route.fulfill({ status: 200, contentType: 'image/png', body: 'invalid-image-test' }); return;
          }
          await route.continue();
        });
        const page = await context.newPage(); page.setDefaultTimeout(10000);
        page.on('pageerror', error => errors.push(error.message));
        page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
        const name = `${viewport.width}-${motion}`;
        try {
          await enterDemo(page);
          const posts = page.locator('[data-home-post]'), tracks = page.locator('[data-home-track]');
          assert.equal(await posts.count(), 3);
          assert.deepEqual(await posts.evaluateAll(items => items.map(p => p.querySelectorAll('[data-home-slide]').length)), [5, 3, 2]);
          assert.deepEqual(await posts.evaluateAll(items => items.map(p => p.querySelector('[data-home-detail]').getAttribute('data-home-detail'))),
            ['demo-content-story', 'demo-content-discovery', 'demo-content-reading']);
          const titles = await posts.locator('h3').allTextContents();
          const slideIds = await page.locator('[data-home-slide]').evaluateAll(items => items.map(e => e.dataset.homeSlide));
          assert.deepEqual(slideIds, ['story-cover', 'story-second', 'story-third', 'story-fourth', 'story-last',
            'discovery-cover', 'discovery-next', 'discovery-last', 'reading-cover', 'reading-last']);
          const first = tracks.nth(0), second = tracks.nth(1), third = tracks.nth(2);
          const css = await first.evaluate(e => {
            const s = getComputedStyle(e); return { snap: s.scrollSnapType, touch: s.touchAction, chain: s.overscrollBehaviorY, behavior: s.scrollBehavior };
          });
          assert.deepEqual(css, { snap: 'x mandatory', touch: 'auto', chain: 'auto', behavior: motion === 'reduce' ? 'auto' : 'smooth' });
          await noOverflow(page);
          await page.getByRole('searchbox', { name: '작품 이름', exact: true }).fill('검색 입력 보존');
          await page.keyboard.press('Enter');
          assert.deepEqual(await posts.locator('h3').allTextContents(), titles);
          await key(page, second, 'End', 2); await key(page, third, 'End', 1);
          assert.equal(await posts.nth(1).getByRole('img', { name: '이미지 준비 중', exact: true }).isVisible(), true);
          await key(page, first, 'Home', 0); await center(page, first);
          const cdp = await context.newCDPSession(page);
          const start = await position(first);
          const horizontal = await swipe(page, cdp, first, -190, 0);
          assert.ok(horizontal.page > 0); assert.ok(Math.abs(horizontal.y - start.y) < 3);
          await indicator(posts.nth(0), horizontal.page, 5);
          assert.equal((await position(second)).page, 2); assert.equal((await position(third)).page, 1);
          const vertical = await swipe(page, cdp, first, 0, -150);
          assert.ok(vertical.y > horizontal.y + 40); assert.equal(vertical.page, horizontal.page);
          await center(page, first);
          const beforeDiagonal = await position(first);
          const diagonal = await swipe(page, cdp, first, -30, -150);
          assert.ok(diagonal.y > beforeDiagonal.y + 40); assert.equal(diagonal.page, beforeDiagonal.page);
          await key(page, first, 'Home', 0); await center(page, first);
          const beforeDiagonalX = await position(first);
          const diagonalX = await swipe(page, cdp, first, -190, -30);
          assert.ok(diagonalX.page > 0); assert.ok(Math.abs(diagonalX.y - beforeDiagonalX.y) < 10);
          await key(page, first, 'Home', 0); await center(page, first);
          // Observe a fast flick without imposing or claiming exactly one slide.
          const flick = await swipe(page, cdp, first, -230, 0, true);
          assert.ok(flick.page > 0 && flick.page < 5); await indicator(posts.nth(0), flick.page, 5);
          const afterFlick = await swipe(page, cdp, first, 0, -150);
          assert.ok(afterFlick.y > flick.y + 40); assert.equal(afterFlick.page, flick.page);
          await key(page, first, 'End', 4);
          assert.equal(await posts.nth(0).getByRole('button', { name: '다음 슬라이드', exact: true }).isDisabled(), true);
          await posts.nth(0).getByRole('button', { name: '2번 슬라이드 보기', exact: true }).click();
          await page.waitForFunction(() => Math.abs(document.querySelector('[data-home-track]').scrollLeft - document.querySelector('[data-home-track]').clientWidth) < 1);
          await settled(page, first); await indicator(posts.nth(0), 1, 5);
          await posts.nth(0).getByRole('button', { name: '다음 슬라이드', exact: true }).click();
          await page.waitForFunction(() => Math.abs(document.querySelector('[data-home-track]').scrollLeft - 2 * document.querySelector('[data-home-track]').clientWidth) < 1);
          await settled(page, first); await indicator(posts.nth(0), 2, 5);
          await first.focus(); assert.equal(await first.evaluate(e => getComputedStyle(e).outlineWidth), '3px');
          await page.keyboard.press('Tab'); assert.equal(await page.evaluate(() => document.activeElement?.tagName), 'BUTTON');
          await center(page, second); await swipe(page, cdp, second, 190, 0);
          assert.equal((await position(first)).page, 2); assert.equal((await position(third)).page, 1);
          await key(page, first, 'Home', 0);
          await page.locator('[data-home-detail="demo-content-story"]').click();
          assert.equal(new URL(page.url()).pathname, '/pivot/demo');
          await page.locator('[data-detail-state="ready"]').waitFor();
          assert.equal(await page.locator('[data-detail-content]').getAttribute('data-detail-content'), 'demo-content-story');
          await page.locator('[data-detail-state]').getByRole('button', { name: '뒤로가기', exact: true }).click();
          await page.waitForFunction(() => document.activeElement?.dataset.homeDetail === 'demo-content-story');
          await noOverflow(page);
          const positionsBeforeDecode = await Promise.all([first, second, third].map(async track => (await position(track)).page));
          await page.evaluate(async () => {
            for (const image of document.querySelectorAll('main img')) { image.loading = 'eager'; await image.decode().catch(() => {}); }
          });
          await Promise.all([first, second, third].map(track => settled(page, track)));
          assert.deepEqual(await Promise.all([first, second, third].map(async track => (await position(track)).page)), positionsBeforeDecode,
            'Lazy image decoding preserves the position of every post');
          assert.deepEqual(await page.locator('main img').evaluateAll(items => items.filter(i => !i.complete || !i.naturalWidth).map(i => i.src)), []);
          const geometry = await first.locator('img').first().evaluate(e => {
            const r = e.getBoundingClientRect(); return { width: r.width, height: r.height, fit: getComputedStyle(e).objectFit };
          });
          assert.ok(Math.abs(geometry.width - (viewport.width - 48)) < 1);
          assert.ok(Math.abs(geometry.height / geometry.width - 408 / 327) < .01); assert.equal(geometry.fit, 'cover');
          await page.evaluate(() => scrollTo(0, 0));
          await Promise.all([first, second, third].map(track => settled(page, track)));
          assert.deepEqual(await Promise.all([first, second, third].map(async track => (await position(track)).page)), positionsBeforeDecode,
            'Returning to the feed start preserves every post position');
          await page.screenshot({ path: path.join(out, name + '.png'), fullPage: true });
          // A different full onboarding cycle must still return the same fixed feed.
          await enterDemo(page, 'goods');
          assert.deepEqual(await posts.locator('h3').allTextContents(), titles);
          await indicator(posts.nth(0), 0, 5); await indicator(posts.nth(1), 0, 3); await indicator(posts.nth(2), 0, 2);
          failImage = true; await enterDemo(page);
          await key(page, first, 'ArrowRight', 1);
          assert.equal(await posts.nth(0).getByRole('img', { name: '이미지 준비 중', exact: true }).isVisible(), true);
          await page.screenshot({ path: path.join(out, name + '-image-fallback.png'), fullPage: true });
          await page.goto(base + '/pivot/home');
          await page.locator('main[data-home-state="unconfigured"]').waitFor();
          assert.equal(await page.locator('[data-home-post], [data-home-track]').count(), 0);
          assert.equal(await page.getByLabel('시연용 피드 안내').count(), 0);
          assert.deepEqual(api, []); assert.deepEqual(external, []); assert.deepEqual(errors, []);
          results.push({ name, status: 'pass', viewport, motion, browser: browser.version(), horizontal, vertical, diagonal, diagonalX, flick,
            afterFlick, geometry, apiRequests: 0, externalRequests: 0, pageErrors: 0, detailNavigations: 1 });
          console.log('PASS ' + name + '; observed fast flick ended at slide ' + (flick.page + 1) + '/5 (no one-slide guarantee)');
        } catch (error) {
          results.push({ name, status: 'fail', error: error.message, api, external, errors });
          await page.screenshot({ path: path.join(out, name + '-failure.png'), fullPage: true }).catch(() => {});
          console.error('FAIL ' + name + ': ' + error.stack);
        } finally { await context.close(); }
      }
    }
  } finally { await browser.close(); fs.writeFileSync(path.join(out, 'home-results.json'), JSON.stringify(results, null, 2)); }
  if (results.some(result => result.status !== 'pass')) process.exitCode = 1;
})().catch(error => { console.error(error); process.exitCode = 1; });
