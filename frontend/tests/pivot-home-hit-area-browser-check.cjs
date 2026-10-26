// Reuse an installed Playwright + Edge. No package installation or backend calls.
// Optional fourth argument "before" records the unfixed layout without changing it.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const base = process.argv[2] || 'http://127.0.0.1:3104';
const out = process.argv[3] || path.join(require('node:os').tmpdir(), 'pivot-home-hit-area');
const beforeFix = process.argv[4] === 'before';
const near = (actual, expected, label) => assert.ok(Math.abs(actual - expected) <= 1, label + ': ' + actual + ' / ' + expected);
fs.mkdirSync(out, { recursive: true });
const results = [];
const home = page => page.locator('[data-home-state="ready"]');
const snapshot = page => page.evaluate(() => ({ y: scrollY,
  query: document.querySelector('[data-home-state] input')?.value,
  tracks: [...document.querySelectorAll('[data-home-track]')].map(e => Math.round(e.scrollLeft / e.clientWidth)) }));
async function homeReady(page) {
  await home(page).waitFor({ state: 'visible' });
  await page.evaluate(async () => { await document.fonts.ready; });
}
async function restored(page, expected) {
  await homeReady(page);
  await page.waitForFunction(s => Math.abs(scrollY - s.y) < 1
    && document.querySelector('[data-home-state] input').value === s.query
    && [...document.querySelectorAll('[data-home-track]')].every((e, i) => Math.round(e.scrollLeft / e.clientWidth) === s.tracks[i]), expected);
  assert.deepEqual(await snapshot(page), expected);
}
async function detailReady(page) {
  await page.locator('[data-detail-state="ready"]').waitFor({ state: 'visible' });
  await page.waitForFunction(() => document.activeElement === document.querySelector('[data-detail-state] h1'));
}
async function savedReady(page) {
  await page.locator('[data-saved-state="ready"]').waitFor({ state: 'visible' });
}
async function position(track) {
  return track.evaluate(e => ({ x: e.scrollLeft, width: e.clientWidth, index: Math.round(e.scrollLeft / e.clientWidth), y: scrollY }));
}
async function settled(page, track) {
  let last, stable = 0;
  for (let i = 0; i < 100; i++) {
    await page.waitForTimeout(50);
    const now = await position(track);
    stable = last && Math.abs(now.x - last.x) < .1 && Math.abs(now.y - last.y) < .1
      && Math.abs(now.x - now.index * now.width) < 1 ? stable + 1 : 0;
    if (stable >= 4) return now;
    last = now;
  }
  throw new Error('Native scroll did not settle: ' + JSON.stringify(last));
}
async function selectSlide(page, post, index) {
  await post.getByRole('button', { name: `${index + 1}번 슬라이드 보기`, exact: true }).click();
  const track = post.locator('[data-home-track]');
  await page.waitForFunction(({ id, index }) => {
    const e = document.getElementById(id); return Math.abs(e.scrollLeft - index * e.clientWidth) < 1;
  }, { id: await track.getAttribute('id'), index });
  return settled(page, track);
}
async function geometry(post) {
  return post.evaluate(article => {
    const rect = e => e.getBoundingClientRect().toJSON();
    const bookmark = article.querySelector('[data-demo-bookmark]');
    const controls = [...article.querySelectorAll('button')].map(e => ({ label: e.getAttribute('aria-label'),
      disabled: e.disabled, rect: rect(e), position: getComputedStyle(e).position,
      pointerEvents: getComputedStyle(e).pointerEvents, zIndex: getComputedStyle(e).zIndex }));
    const overlaps = [];
    for (let i = 0; i < controls.length; i++) for (let j = i + 1; j < controls.length; j++) {
      const a = controls[i], b = controls[j];
      const width = Math.min(a.rect.right, b.rect.right) - Math.max(a.rect.left, b.rect.left);
      const height = Math.min(a.rect.bottom, b.rect.bottom) - Math.max(a.rect.top, b.rect.top);
      if (width > .01 && height > .01) overlaps.push({ a: a.label, b: b.label, width, height });
    }
    const samples = button => {
      const r = button.getBoundingClientRect();
      // Adjacent reactions share a boundary; keep their probes a full CSS pixel inside.
      // Bookmark and Detail additionally exercise half-pixel coordinates across the gap.
      const edge = button === bookmark || button.hasAttribute('data-home-detail') ? .5 : 1;
      // Preserve the original nine probes; add half-pixel edges and rounded-corner probes.
      return [[r.width / 2, r.height / 2], [1, r.height / 2], [r.width - 1, r.height / 2],
        [r.width / 2, 1], [r.width / 2, r.height - 1], [6, 6], [r.width - 6, 6],
        [6, r.height - 6], [r.width - 6, r.height - 6],
        [edge, r.height / 2], [r.width - edge, r.height / 2], [r.width / 2, edge], [r.width / 2, r.height - edge],
        [3, 3], [r.width - 3, 3], [3, r.height - 3], [r.width - 3, r.height - 3]].map(([dx, dy]) => {
        const x = r.x + dx, y = r.y + dy, hit = document.elementFromPoint(x, y);
        return { x, y, target: hit?.closest('button') === button, bookmark: hit?.closest('button') === bookmark,
          hit: hit?.tagName, label: hit?.closest('button')?.getAttribute('aria-label'), className: hit?.className };
      });
    };
    const points = samples(bookmark);
    const detail = article.querySelector('[data-home-detail]');
    const card = rect(article), detailFace = rect(detail.querySelector('span'));
    const reactions = [...article.querySelectorAll('[role="group"][aria-label$="반응 기능"] button > span')].map(rect);
    const visual = { reactions, detail: detailFace, reactionStart: reactions[0].top - card.top,
      reactionEnd: reactions.at(-1).bottom - card.top, detailStart: detailFace.top - card.top,
      gap: detailFace.top - reactions.at(-1).bottom, hitGap: rect(detail).top - rect(bookmark).bottom };
    const probes = Object.fromEntries([...article.querySelectorAll('button')].map(e => [e.getAttribute('aria-label'), samples(e)]));
    const scope = article.closest('main');
    const chrome = [...scope.querySelectorAll('nav, button[aria-label="AI 콘텐츠 탐색"]'),
      document.querySelector('[aria-label="시연용 데모 안내"]')].filter(Boolean).map(e => ({
        label: e.getAttribute('aria-label'), rect: rect(e), pointerEvents: getComputedStyle(e).pointerEvents,
        zIndex: getComputedStyle(e).zIndex }));
    return { card, scrollY, controls, overlaps, points, probes, visual, chrome };
  });
}
async function toggleAt(page, button, point) {
  const pressed = await button.getAttribute('aria-pressed'), url = page.url(), state = await snapshot(page);
  const id = await button.getAttribute('data-demo-bookmark');
  if (point) await page.mouse.click(point.x, point.y);
  else await button.click();
  await page.waitForFunction(({ id, expected }) => document.querySelector(`[data-home-state] [data-demo-bookmark="${id}"]`)
    ?.getAttribute('aria-pressed') === expected, { id, expected: pressed === 'true' ? 'false' : 'true' });
  assert.equal(page.url(), url, 'Bookmark must not open Detail');
  assert.equal(await page.locator('[data-detail-state="ready"]:visible').count(), 0);
  assert.deepEqual(await snapshot(page), state, 'Bookmark preserves scroll, query and every carousel');
}
async function swipe(page, cdp, track, dx, dy) {
  const b = await track.boundingBox();
  const x = b.x + b.width / 2 - dx / 2, y = b.y + b.height / 2 - dy / 2;
  assert.ok(y > 44 && y + dy > 44 && y < page.viewportSize().height - 64 && y + dy < page.viewportSize().height - 64);
  assert.ok(await track.evaluate((e, p) => e.contains(document.elementFromPoint(p.x, p.y)), { x, y }), 'Touch starts on the slide, outside controls');
  const points = (px, py) => [{ x: px, y: py, id: 1, radiusX: 2, radiusY: 2, force: 1 }];
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: points(x, y) });
  for (let i = 1; i <= 16; i++) {
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: points(x + dx * i / 16, y + dy * i / 16) });
    await page.waitForTimeout(16);
  }
  await page.waitForTimeout(80);
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  return settled(page, track);
}
async function touchChecks(page, post, cdp) {
  const track = post.locator('[data-home-track]');
  // A slow drag must cross half a slide, while starting clear of the right arrow.
  const distance = Math.round((await position(track)).width * .58);
  const center = async () => { await track.evaluate(e => e.scrollIntoView({ block: 'center', behavior: 'instant' })); return settled(page, track); };
  await selectSlide(page, post, 0); const start = await center();
  const horizontal = await swipe(page, cdp, track, -distance, 0);
  assert.ok(horizontal.index > 0, JSON.stringify({ start, horizontal })); assert.ok(Math.abs(horizontal.y - start.y) < 3);
  const verticalStart = await center(), vertical = await swipe(page, cdp, track, 0, -100);
  assert.ok(vertical.y > verticalStart.y + 30); assert.equal(vertical.index, verticalStart.index);
  const diagonalStart = await center(), diagonal = await swipe(page, cdp, track, -25, -100);
  assert.ok(diagonal.y > diagonalStart.y + 30); assert.equal(diagonal.index, diagonalStart.index);
  await selectSlide(page, post, 0); const diagonalXStart = await center();
  const diagonalX = await swipe(page, cdp, track, -distance, -25);
  assert.ok(diagonalX.index > 0); assert.ok(Math.abs(diagonalX.y - diagonalXStart.y) < 10);
  return { start, horizontal, verticalStart, vertical, diagonalStart, diagonal, diagonalXStart, diagonalX };
}
(async () => {
  const browser = await chromium.launch({ headless: true, channel: process.env.PIVOT_BROWSER_CHANNEL || 'msedge' });
  try {
    for (const viewport of [{ width: 320, height: 568 }, { width: 360, height: 800 }, { width: 375, height: 812 }, { width: 390, height: 844 }]) {
      for (const reducedMotion of ['no-preference', 'reduce']) {
        const name = `${viewport.width}-${reducedMotion}`;
        const context = await browser.newContext({ viewport, isMobile: true, hasTouch: true, reducedMotion, serviceWorkers: 'block' });
        const page = await context.newPage(); page.setDefaultTimeout(8000);
        const api = [], external = [], errors = [], evidence = { name, viewport, reducedMotion, browser: browser.version(), cards: [] };
        await context.route('**/*', async route => {
          const url = new URL(route.request().url());
          if (url.pathname === '/favicon.ico') return route.fulfill({ status: 204 });
          if (url.pathname.startsWith('/api/')) { api.push(url.href); return route.abort(); }
          if (url.origin !== new URL(base).origin) { external.push(url.href); return route.abort(); }
          return route.continue();
        });
        page.on('pageerror', e => errors.push(e.message));
        page.on('console', e => { if (e.type() === 'error') errors.push(e.text()); });
        try {
          await page.goto(base + '/pivot/demo?view=home'); await homeReady(page);
          await page.waitForFunction(() => document.activeElement === document.querySelector('[data-home-state] h1'));
          const posts = home(page).locator('[data-home-post]');
          const initialStorage = await page.evaluate(() => ({ local: { ...localStorage }, session: { ...sessionStorage }, cookie: document.cookie }));
          for (const index of [0, 1]) {
            const post = posts.nth(index), bookmark = post.locator('[data-demo-bookmark]');
            await page.evaluate(y => scrollTo({ top: y, behavior: 'instant' }), index ? 554 : 0);
            await settled(page, post.locator('[data-home-track]'));
            const measured = await geometry(post); evidence.cards.push({ index, ...measured });
            if (index === 0) await page.screenshot({ path: path.join(out, name + '.png'), style: 'nextjs-portal { visibility: hidden !important; }' });
            if (beforeFix) {
              try { await bookmark.click({ trial: true, timeout: 1500 }); evidence.cards.at(-1).pointerTrial = 'pass'; }
              catch (e) { evidence.cards.at(-1).pointerTrial = 'blocked'; evidence.cards.at(-1).pointerError = e.message; }
              continue;
            }
            assert.deepEqual(measured.overlaps, [], 'All card button rectangles must be disjoint');
            assert.ok(measured.visual.hitGap >= 4 - .02, 'Bookmark and Detail retain a 4px pointer gap');
            const detailControl = measured.controls.find(c => c.label.endsWith(' 자세히 보기'));
            assert.deepEqual([detailControl.rect.width, detailControl.rect.height], [88, 44]);
            near(measured.card.bottom - measured.visual.detail.bottom, 12, 'Detail face bottom inset');
            if (viewport.width === 375) {
              near(measured.card.width, 327, 'Figma card width'); near(measured.card.height, 408, 'Figma card height');
              near(measured.visual.reactionStart, 262, 'Figma reactions start');
              near(measured.visual.reactionEnd, 360, 'Figma reactions end');
              near(measured.visual.detailStart, 372, 'Figma Detail start');
              assert.ok(Math.abs(measured.visual.gap - 12) < .1, 'Figma visual gap remains 12px');
            }
            assert.ok(measured.points.every(p => p.bookmark), 'Bookmark center, edges and corners must hit the bookmark');
            assert.deepEqual([measured.controls.find(c => c.label === '저장').rect.width, measured.controls.find(c => c.label === '저장').rect.height], [44, 35]);
            await toggleAt(page, bookmark);
            for (const point of (await geometry(post)).points.slice(1)) await toggleAt(page, bookmark, point);
            await toggleAt(page, bookmark); // Restore after every center, edge and corner probe.
            evidence.cards.at(-1).pointerClicks = measured.points.length + 1;
            const url = page.url(), controlClicks = [];
            for (const label of ['공유', '좋아요']) {
              const control = measured.controls.find(c => c.label === label);
              assert.deepEqual([control.rect.width, control.rect.height], [44, 35]);
              for (const point of (await geometry(post)).probes[label]) {
                assert.ok(point.target, label + ' hit target');
                await page.mouse.click(point.x, point.y);
                assert.ok((await post.getByRole('status').innerText()).includes(label + ' 기능은 준비 중'));
                assert.equal(page.url(), url); assert.equal(await home(page).isVisible(), true);
              }
              controlClicks.push({ label, count: measured.probes[label].length });
            }
            // Exercise both 44px arrows at the same center/edge/corner points.
            for (let probe = 0; probe < measured.points.length; probe++) {
              for (const [label, expected] of [['다음 슬라이드', 1], ['이전 슬라이드', 0]]) {
                const current = await geometry(post), control = current.controls.find(c => c.label === label);
                assert.deepEqual([control.rect.width, control.rect.height], [44, 44]);
                const point = current.probes[label][probe]; assert.ok(point.target, label + ' hit target');
                await page.mouse.click(point.x, point.y);
                await settled(page, post.locator('[data-home-track]'));
                assert.equal((await position(post.locator('[data-home-track]'))).index, expected);
                assert.equal(page.url(), url); assert.equal(await home(page).isVisible(), true);
              }
            }
            await selectSlide(page, post, 1);
            const state = await snapshot(page), contentId = await bookmark.getAttribute('data-demo-bookmark');
            const bookmarkState = await bookmark.getAttribute('aria-pressed');
            const detailLabel = detailControl.label;
            for (let probe = 0; probe < measured.points.length; probe++) {
              const point = (await geometry(post)).probes[detailLabel][probe];
              assert.ok(point.target, 'Detail center, edges and corners hit Detail');
              await page.mouse.click(point.x, point.y); await detailReady(page);
              assert.equal(new URL(page.url()).searchParams.get('contentId'), contentId);
              assert.equal(await page.locator('[data-detail-content]').getAttribute('data-detail-content'), contentId);
              await page.locator('[data-detail-state]').getByRole('button', { name: '뒤로가기', exact: true }).click();
              await restored(page, state);
              assert.equal(await bookmark.getAttribute('aria-pressed'), bookmarkState, 'Detail must not toggle bookmark');
            }
            evidence.cards.at(-1).controlClicks = [...controlClicks,
              { label: '다음 슬라이드', count: measured.points.length }, { label: '이전 슬라이드', count: measured.points.length },
              { label: '자세히 보기', count: measured.points.length }];

          }
          if (!beforeFix) {
            await home(page).getByRole('searchbox').fill('Home hit area regression');
            const post = posts.nth(1), bookmark = post.locator('[data-demo-bookmark]');
            await page.evaluate(() => scrollTo({ top: 554, behavior: 'instant' }));
            const state = await snapshot(page), contentId = await bookmark.getAttribute('data-demo-bookmark');
            await toggleAt(page, bookmark); assert.equal(await bookmark.getAttribute('aria-pressed'), 'false');
            await home(page).getByRole('button', { name: 'SAVE', exact: true }).click(); await savedReady(page);
            let saved = page.locator('[data-saved-state]');
            assert.equal(await saved.locator('[data-saved-content]').count(), 2);
            assert.equal(await saved.locator(`[data-saved-content="${contentId}"]`).count(), 0);
            await saved.getByRole('button', { name: 'HOME', exact: true }).click(); await restored(page, state);
            await toggleAt(page, bookmark);
            await home(page).getByRole('button', { name: 'SAVE', exact: true }).click(); await savedReady(page);
            assert.equal(await saved.locator('[data-saved-content]').count(), 3);
            const savedCard = saved.locator(`[data-saved-content="${contentId}"]`);
            await savedCard.click(); await detailReady(page);
            await page.locator('[data-detail-state]').getByRole('button', { name: '저장', exact: true }).click();
            await page.locator('[data-detail-state]').getByRole('button', { name: '뒤로가기', exact: true }).click(); await savedReady(page);
            assert.equal(await saved.locator('[data-saved-content]').count(), 2);
            await saved.getByRole('button', { name: 'HOME', exact: true }).click(); await restored(page, state);
            assert.equal(await bookmark.getAttribute('aria-pressed'), 'false'); await toggleAt(page, bookmark);
            await home(page).getByRole('button', { name: 'MY', exact: true }).click();
            const my = page.locator('[data-my-state="ready"]'); await my.waitFor({ state: 'visible' });
            await my.getByRole('button', { name: 'SAVE', exact: true }).click(); await savedReady(page);
            await saved.getByRole('button', { name: 'HOME', exact: true }).click(); await restored(page, state);
            evidence.restored = state;
            const cdp = await context.newCDPSession(page);
            evidence.touch = [];
            for (const index of [0, 1]) {
              const other = posts.nth(index === 0 ? 1 : 0).locator('[data-home-track]');
              const otherIndex = (await position(other)).index;
              evidence.touch.push({ index, ...await touchChecks(page, posts.nth(index), cdp) });
              assert.equal((await position(other)).index, otherIndex, 'Posts retain independent slide positions');
            }
            // Keyboard is checked separately and never counted as pointer success.
            const track = post.locator('[data-home-track]'); await track.focus(); await page.keyboard.press('Home');
            await settled(page, track); assert.equal((await position(track)).index, 0);
            await page.keyboard.press('ArrowRight'); await settled(page, track); assert.equal((await position(track)).index, 1);
            evidence.css = await track.evaluate(e => { const s = getComputedStyle(e); return { snap: s.scrollSnapType, touch: s.touchAction, y: s.overscrollBehaviorY, motion: s.scrollBehavior }; });
            assert.deepEqual(evidence.css, { snap: 'x mandatory', touch: 'auto', y: 'auto', motion: reducedMotion === 'reduce' ? 'auto' : 'smooth' });
            // Deliberately place controls behind fixed chrome, then restore a usable scroll position.
            // Report these existing overlays separately from a card-internal collision.
            evidence.overlayProbes = [];
            for (const [index, targetY] of [[0, 20], [1, viewport.height - 40], [1, viewport.height / 2]]) {
              const probe = posts.nth(index).locator('[data-demo-bookmark]');
              await probe.evaluate((e, y) => { const r = e.getBoundingClientRect(); scrollTo({ top: scrollY + r.y + r.height / 2 - y, behavior: 'instant' }); }, targetY);
              const measured = await geometry(posts.nth(index));
              evidence.overlayProbes.push({ index, targetY, center: measured.points[0], scrollY: measured.scrollY });
              if (targetY === viewport.height / 2) {
                assert.ok(measured.points.every(p => p.bookmark), 'Scrolling clear of fixed chrome restores the full bookmark target');
                await toggleAt(page, probe); await toggleAt(page, probe);
              }
            }
            assert.deepEqual(await page.evaluate(() => ({ local: { ...localStorage }, session: { ...sessionStorage }, cookie: document.cookie })), initialStorage);
            await page.goto(base + '/pivot/home?view=saved');
            await page.locator('[data-saved-state="unconfigured"]').waitFor({ state: 'visible' });
            assert.equal(await page.locator('[data-demo-bookmark], [data-saved-content]').count(), 0);
            await page.locator('[data-saved-state]').getByRole('button', { name: 'HOME', exact: true }).click();
            await page.locator('[data-home-state="unconfigured"]').waitFor({ state: 'visible' });
            assert.equal(await page.locator('[data-home-post]').count(), 0);
            assert.deepEqual(api, []); assert.deepEqual(external, []); assert.deepEqual(errors, []);
          }
          results.push({ ...evidence, status: beforeFix ? 'baseline' : 'pass', api, external, errors });
          console.log(`${beforeFix ? 'BASELINE' : 'PASS'} ${name}`);
        } catch (e) {
          results.push({ ...evidence, status: 'fail', error: e.stack, api, external, errors });
          await page.screenshot({ path: path.join(out, name + '-failure.png') }).catch(() => {});
          console.error(`FAIL ${name}: ${e.stack}`);
        } finally { await context.close(); }
      }
    }
  } finally { await browser.close(); fs.writeFileSync(path.join(out, 'results.json'), JSON.stringify(results, null, 2)); }
  if (results.some(r => r.status === 'fail')) process.exitCode = 1;
})().catch(e => { console.error(e); process.exitCode = 1; });
