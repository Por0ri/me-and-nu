// Existing Playwright + Edge only. No dependency installation or real API calls.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const base = process.argv[2] || 'http://127.0.0.1:3100';
const out = process.argv[3] || path.join(require('node:os').tmpdir(), 'pivot-saved');
const mode = process.argv[4] || 'development';
const results = []; fs.mkdirSync(out, { recursive: true });
const demoOnlySavedCopy = /Demo|시연용|하이큐|미뉴|아직 저장한|저장한 콘텐츠가 아직 없어요|콘텐츠 보러가기/;
const near = (a, b, label) => assert.ok(Math.abs(a - b) < 1, `${label}: ${a} / ${b}`);
const homeSnapshot = page => page.evaluate(() => ({ y: scrollY, query: document.querySelector('input[type=search]')?.value,
  tracks: [...document.querySelectorAll('[data-home-track]')].map(e => Math.round(e.scrollLeft / e.clientWidth)) }));
const storage = page => page.evaluate(() => ({ local: { ...localStorage }, session: { ...sessionStorage }, cookie: document.cookie }));
async function savedReady(page, state = 'ready') {
  await page.locator(`[data-saved-state="${state}"]`).waitFor({ state: 'visible' });
}
async function detailReady(page, state = 'ready') {
  await page.locator(`[data-detail-state="${state}"]`).waitFor({ state: 'visible' });
  await page.waitForFunction(() => document.activeElement === document.querySelector('[data-detail-state] h1'));
}
async function restoredHome(page, before) {
  await page.locator('[data-home-state="ready"]').waitFor({ state: 'visible' });
  await page.waitForFunction(({ y, query, tracks }) => Math.abs(scrollY - y) < 2
    && document.querySelector('input[type=search]').value === query
    && [...document.querySelectorAll('[data-home-track]')].every((e, i) => Math.round(e.scrollLeft / e.clientWidth) === tracks[i]), before);
  assert.deepEqual(await homeSnapshot(page), before);
}
async function capture(page, name) {
  await page.evaluate(async () => { await document.fonts.ready; await Promise.all([...document.querySelectorAll('[data-saved-state] img')].map(e => e.decode().catch(() => {}))); });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({ path: path.join(out, name + '.png'), fullPage: true, style: 'nextjs-portal { visibility: hidden !important; }' });
}
async function toggleHomeBookmark(button) {
  const page = button.page(), url = page.url(), pressed = await button.getAttribute('aria-pressed');
  const contentId = await button.getAttribute('data-demo-bookmark');
  // Exercise the same unforced pointer path at every width, including 320px.
  await button.click();
  assert.equal(page.url(), url, 'Home bookmark pointer must not open Detail');
  assert.equal(await page.locator('[data-detail-state]:visible').count(), 0);
  await page.waitForFunction(({ contentId, expected }) => [...document.querySelectorAll('[data-home-state] [data-demo-bookmark]')]
    .find(e => e.dataset.demoBookmark === contentId)?.getAttribute('aria-pressed') === expected,
    { contentId, expected: pressed === 'true' ? 'false' : 'true' });
  return { input: 'pointer', contentId, before: pressed, after: await button.getAttribute('aria-pressed'), detailOpened: false };
}
async function production(page, viewport, api, external, errors) {
  const blocked = [];
  for (const route of ['/pivot/demo?view=saved', '/pivot/demo?view=detail&from=saved&postId=demo-home-story&contentId=demo-content-story']) {
    const response = await page.goto(base + route); assert.equal(response.status(), 404);
    assert.equal(await page.locator('[data-saved-state], [data-demo-bookmark]').count(), 0);
    assert.doesNotMatch(await page.locator('body').innerText(), demoOnlySavedCopy);
    blocked.push({ route, status: 404 });
  }
  await page.goto(base + '/pivot/home?view=saved'); await savedReady(page, 'unconfigured');
  assert.doesNotMatch(await page.locator('body').innerText(), demoOnlySavedCopy);
  await capture(page, 'production-service-' + viewport.width);
  assert.deepEqual(api, []); assert.deepEqual(external, []);
  assert.ok(errors.every(e => /Failed to load resource: the server responded with a status of 404 \(Not Found\)/.test(e)));
  return { blocked, expected404Console: errors };
}
(async () => {
  const browser = await chromium.launch({ headless: true, channel: process.env.PIVOT_BROWSER_CHANNEL || 'msedge' });
  try {
    for (const viewport of [{ width: 320, height: 568 }, { width: 360, height: 800 }, { width: 375, height: 812 }, { width: 390, height: 844 }]) for (const reducedMotion of ['no-preference', 'reduce']) {
      const context = await browser.newContext({ viewport, isMobile: true, hasTouch: true, reducedMotion });
      const page = await context.newPage(); page.setDefaultTimeout(12000);
      const api = [], external = [], errors = [], homeBookmarkClicks = [];
      await context.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.pathname === '/favicon.ico') { await route.fulfill({ status: 204 }); return; }
        if (url.pathname.startsWith('/api/')) { api.push(url.href); await route.abort(); return; }
        if (url.origin !== new URL(base).origin) { external.push(url.href); await route.abort(); return; }
        await route.continue();
      });
      page.on('pageerror', error => errors.push(error.message));
      page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
      const name = `${viewport.width}-${reducedMotion}`;
      try {
        if (mode === 'production') {
          const evidence = await production(page, viewport, api, external, errors);
          results.push({ name, status: 'pass', mode, ...evidence }); console.log('PASS Saved production ' + name); continue;
        }
        await page.goto(base + '/pivot/demo?view=saved'); await savedReady(page);
        await page.waitForFunction(() => document.activeElement === document.querySelector('[data-saved-state] h1'));
        const saved = page.locator('[data-saved-state]');
        const cards = saved.locator('[data-saved-content]'); assert.equal(await cards.count(), 3);
        const initialStorage = await storage(page);
        await capture(page, 'saved-' + name);
        const geometry = await cards.evaluateAll(items => items.map(e => {
          const r = e.getBoundingClientRect(); const image = e.querySelector('img');
          return { postId: e.dataset.savedPost, contentId: e.dataset.savedContent, title: e.querySelector('span').textContent.replace(/ 자세히 보기$/, ''),
            x: r.x, y: r.y, width: r.width, height: r.height, radius: getComputedStyle(e).borderRadius,
            src: image?.getAttribute('src'), loaded: image?.complete && image.naturalWidth > 0 };
        }));
        const panel = await saved.getByRole('list', { name: '저장한 콘텐츠 목록' }).evaluate(e => {
          const r = e.getBoundingClientRect(), s = getComputedStyle(e);
          return { x: r.x, y: r.y, width: r.width, background: s.backgroundColor, radius: s.borderRadius, shadow: s.boxShadow };
        });
        const expectedWidth = (viewport.width - 79) / 2;
        near(panel.x, 24, 'Panel inset'); near(panel.y, 92, 'Panel top'); near(panel.width, viewport.width - 48, 'Panel width');
        assert.equal(panel.background, 'rgb(255, 255, 255)'); assert.equal(panel.radius, '8px'); assert.notEqual(panel.shadow, 'none');
        near(geometry[0].x, 35.5, 'Centered first column'); near(geometry[0].y, 104, 'Disclosure + header + gap + panel padding');
        near(geometry[0].width, expectedWidth, 'Card width'); near(geometry[0].height, expectedWidth * 184.661 / 148, 'Card height');
        if (viewport.width === 375) assert.equal(geometry[0].width, 148);
        near(geometry[1].x - geometry[0].x - geometry[0].width, 8, 'Column gap');
        near(geometry[2].y - geometry[0].y - geometry[0].height, 12, 'Row gap');
        assert.equal(geometry[0].radius, '3.621px'); assert.ok(geometry.every(c => c.loaded));
        assert.equal(await cards.locator('span:not([class*="srOnly"])').count(), 0, 'No duplicate visible text on precomposed images');
        assert.equal(await saved.getByRole('button', { name: 'SAVE', exact: true }).getAttribute('aria-current'), 'page');
        const chrome = await saved.locator('header img').evaluateAll(items => items.map(e => { const r = e.getBoundingClientRect(); return { src: e.getAttribute('src'), width: r.width, height: r.height, loaded: e.complete && e.naturalWidth > 0 }; }));
        assert.equal(chrome.length, 2); assert.equal(chrome[0].width, 103); assert.deepEqual([chrome[1].width, chrome[1].height], [24, 24]); assert.ok(chrome.every(i => i.loaded));
        for (const [index, card] of geometry.entries()) {
          const button = saved.locator(`[data-saved-content="${card.contentId}"]`);
          await button.focus(); await page.keyboard.press('Enter'); await detailReady(page);
          const query = new URL(page.url()).searchParams;
          assert.equal(query.get('postId'), card.postId); assert.equal(query.get('contentId'), card.contentId); assert.equal(query.get('from'), 'saved');
          assert.equal(await page.locator('[data-detail-content]').getAttribute('data-detail-content'), card.contentId);
          assert.ok((await page.locator('[data-detail-state] h1').innerText()).includes(card.title));
          assert.equal(await page.locator('[data-detail-content] > img').getAttribute('src'), card.src);
          const detail = page.locator('[data-detail-state]');
          assert.equal(await detail.getByRole('button', { name: 'SAVE', exact: true }).getAttribute('aria-current'), 'page');
          if (index === 0) await detail.getByRole('button', { name: '뒤로가기', exact: true }).click();
          else if (index === 1) await page.goBack();
          else await detail.getByRole('button', { name: 'SAVE', exact: true }).click();
          await savedReady(page);
          await page.waitForFunction(id => document.activeElement?.dataset.savedContent === id, card.contentId);
        }
        // Refresh a Detail opened from Saved, then return to its source list.
        await cards.first().click(); await detailReady(page); await page.reload(); await detailReady(page);
        await page.locator('[data-detail-state]').getByRole('button', { name: '뒤로가기', exact: true }).click(); await savedReady(page);
        const removed = geometry[1];
        await saved.locator(`[data-saved-content="${removed.contentId}"]`).click(); await detailReady(page);
        const detail = page.locator('[data-detail-state]'); const detailBookmark = detail.getByRole('button', { name: '저장', exact: true });
        assert.equal(await detailBookmark.getAttribute('aria-pressed'), 'true'); await detailBookmark.click();
        assert.equal(await detailBookmark.getAttribute('aria-pressed'), 'false');
        await detail.getByRole('status').filter({ hasText: 'Demo 저장 목록에서 제외' }).waitFor();
        await detail.getByRole('button', { name: '뒤로가기', exact: true }).click(); await savedReady(page);
        await page.waitForFunction(() => document.querySelectorAll('[data-saved-content]').length === 2);
        await page.waitForFunction(() => document.activeElement === document.querySelector('[data-saved-state] h1'));
        assert.equal(await saved.locator(`[data-saved-content="${removed.contentId}"]`).count(), 0);
        await saved.getByRole('button', { name: 'HOME', exact: true }).click();
        const home = page.locator('[data-home-state="ready"]'); await home.waitFor({ state: 'visible' });
        await page.waitForFunction(() => document.activeElement === document.querySelector('[data-home-state] h1'));
        for (const card of geometry) {
          const post = home.locator(`[data-home-post="${card.postId}"]`);
          assert.equal(await post.locator('h3').innerText(), card.title);
          assert.equal(await post.locator('[data-home-track] img').first().getAttribute('src'), card.src);
        }
        const bookmark = home.locator(`[data-demo-bookmark="${removed.contentId}"]`);
        assert.equal(await bookmark.getAttribute('aria-pressed'), 'false');
        await home.getByRole('searchbox').fill('Saved 왕복 보존');
        const tracks = home.locator('[data-home-track]');
        await tracks.nth(0).evaluate(e => e.scrollTo({ left: e.clientWidth * 2, behavior: 'instant' }));
        await tracks.nth(1).evaluate(e => e.scrollTo({ left: e.clientWidth, behavior: 'instant' }));
        await page.evaluate(() => scrollTo({ top: 554, behavior: 'instant' }));
        await page.waitForFunction(() => document.querySelector('[data-home-position]').textContent.includes('3 / 5'));
        const before = await homeSnapshot(page);
        homeBookmarkClicks.push(await toggleHomeBookmark(bookmark)); assert.equal(await bookmark.getAttribute('aria-pressed'), 'true');
        assert.deepEqual(await homeSnapshot(page), before, 'Bookmark mutation does not remount or scroll Home');
        await home.getByRole('button', { name: 'SAVE', exact: true }).click(); await savedReady(page);
        assert.equal(await cards.count(), 3);
        await saved.getByRole('button', { name: 'MY', exact: true }).click(); await page.locator('[data-my-state="ready"]').waitFor();
        await page.locator('[data-my-state]').getByRole('button', { name: 'SAVE', exact: true }).click(); await savedReady(page);
        assert.equal(await cards.count(), 3);
        await saved.getByRole('button', { name: 'HOME', exact: true }).click(); await restoredHome(page, before);
        assert.equal(await home.getByRole('button', { name: 'SAVE', exact: true }).evaluate(e => e === document.activeElement), true);
        await home.getByRole('button', { name: 'SAVE', exact: true }).click(); await savedReady(page);
        await page.goBack(); await restoredHome(page, before); await page.goForward(); await savedReady(page);
        await page.reload(); await savedReady(page);
        await saved.getByRole('button', { name: 'HOME', exact: true }).click(); await restoredHome(page, before);
        const homeDetail = home.locator('[data-home-detail="demo-content-discovery"]');
        await homeDetail.focus(); await homeDetail.scrollIntoViewIfNeeded(); const beforeDetail = await homeSnapshot(page);
        await page.keyboard.press('Enter'); await detailReady(page);
        const more = detail.getByRole('button', { name: '더 볼래요', exact: true }); await more.click(); const detailY = await page.evaluate(() => scrollY);
        await detail.getByRole('button', { name: 'SAVE', exact: true }).click(); await savedReady(page);
        await page.goBack(); await detail.waitFor({ state: 'visible' });
        await page.waitForFunction(y => Math.abs(scrollY - y) < 2, detailY); assert.equal(await more.getAttribute('aria-pressed'), 'true');
        await detail.getByRole('button', { name: 'HOME', exact: true }).click(); await restoredHome(page, beforeDetail);
        // Empty list is driven by actual Demo bookmark choices, not a query flag.
        for (const card of geometry) { const control = home.locator(`[data-demo-bookmark="${card.contentId}"]`); if (await control.getAttribute('aria-pressed') === 'true') homeBookmarkClicks.push(await toggleHomeBookmark(control)); }
        await home.getByRole('button', { name: 'SAVE', exact: true }).click(); await savedReady(page, 'empty');
        assert.equal(await cards.count(), 0); await saved.getByText('저장한 콘텐츠가 아직 없어요', { exact: true }).waitFor(); await capture(page, 'saved-empty-' + name);
        const emptyGeometry = await saved.getByRole('region', { name: '저장 목록 상태' }).evaluate(e => {
          const rect = node => { const r = node.getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height }; };
          const button = e.querySelector('button'), image = e.querySelector('img'), clip = image.parentElement;
          return { panel: rect(e), button: rect(button), clip: rect(clip), image: rect(image), src: image.getAttribute('src'),
            loaded: image.complete && image.naturalWidth > 0, clipOverflow: getComputedStyle(clip).overflow,
            buttonBackground: getComputedStyle(button).backgroundColor, buttonFontSize: getComputedStyle(button).fontSize };
        });
        near(emptyGeometry.panel.y, 104, 'Empty panel top'); near(emptyGeometry.panel.width, viewport.width - 48, 'Empty panel width');
        near(emptyGeometry.panel.height, 199.419, 'Empty panel height');
        near(emptyGeometry.button.width, 222, 'Empty CTA width'); near(emptyGeometry.button.height, 35.303, 'Empty CTA height');
        near(emptyGeometry.clip.width, 146.25, 'Mascot clip width'); near(emptyGeometry.clip.height, 78.116, 'Mascot clip height');
        near(emptyGeometry.image.width, 146.25 * .9778, 'Mascot image width'); near(emptyGeometry.image.height, 78.116 * 1.5389, 'Mascot image height');
        near(emptyGeometry.image.y - emptyGeometry.clip.y, -78.116 * .5389, 'Mascot crop offset');
        assert.equal(emptyGeometry.src, '/pivot/home/chrome/mascot.png'); assert.ok(emptyGeometry.loaded);
        assert.equal(emptyGeometry.clipOverflow, 'hidden'); assert.equal(emptyGeometry.buttonBackground, 'rgb(112, 67, 180)');
        assert.equal(emptyGeometry.buttonFontSize, '10.862px');
        assert.equal(await saved.getByRole('button', { name: 'SAVE', exact: true }).getAttribute('aria-current'), 'page');
        await saved.getByRole('button', { name: '콘텐츠 보러가기', exact: true }).click(); await home.waitFor({ state: 'visible' });
        await page.waitForFunction(() => document.activeElement?.matches('[data-home-state] [data-saved-navigation]'));
        await page.evaluate(() => scrollTo({ top: 0, behavior: 'instant' }));
        homeBookmarkClicks.push(await toggleHomeBookmark(home.locator('[data-demo-bookmark="demo-content-story"]')));
        await home.getByRole('button', { name: 'SAVE', exact: true }).click(); await savedReady(page); assert.equal(await cards.count(), 1);
        assert.deepEqual(await storage(page), initialStorage, 'Demo bookmark choices never write persistent storage or cookies');
        await page.reload(); await savedReady(page); assert.equal(await cards.count(), 3, 'Reload restores the disclosed example list');
        // Layout-only stress fixture. The app's three-item Demo source and identities remain unchanged.
        await saved.getByRole('list', { name: '저장한 콘텐츠 목록' }).evaluate(list => {
          const originals = [...list.children];
          for (let i = 0; i < 27; i++) {
            const clone = originals[i % originals.length].cloneNode(true);
            clone.dataset.layoutFixture = 'true';
            clone.querySelectorAll('[id]').forEach(e => e.removeAttribute('id'));
            const button = clone.querySelector('button'); button.removeAttribute('aria-labelledby'); button.disabled = true;
            button.removeAttribute('data-saved-content'); button.removeAttribute('data-saved-post');
            list.append(clone);
          }
        });
        await page.evaluate(() => scrollTo({ top: document.documentElement.scrollHeight, behavior: 'instant' }));
        const longListLayout = await page.evaluate(() => {
          const list = document.querySelector('[aria-label="저장한 콘텐츠 목록"]');
          const last = list.lastElementChild.getBoundingClientRect();
          const nav = document.querySelector('[data-saved-state] nav').getBoundingClientRect();
          return { fixture: 'DOM-only 30-card layout; no source or API changes', count: list.children.length,
            scrollY, lastBottom: last.bottom, navigationTop: nav.top, navigationBottom: nav.bottom, viewportHeight: innerHeight,
            horizontalOverflow: document.documentElement.scrollWidth > innerWidth };
        });
        assert.equal(longListLayout.count, 30); assert.ok(longListLayout.scrollY > viewport.height);
        assert.ok(longListLayout.lastBottom < longListLayout.navigationTop); assert.ok(longListLayout.navigationBottom <= viewport.height);
        assert.equal(longListLayout.horizontalOverflow, false);
        await page.screenshot({ path: path.join(out, 'saved-long-bottom-' + name + '.png'), style: 'nextjs-portal { visibility: hidden !important; }' });
        await saved.getByRole('button', { name: 'HOME', exact: true }).click(); await home.waitFor({ state: 'visible' });
        await page.goto(base + '/pivot/demo?view=saved'); await savedReady(page); assert.equal(await cards.count(), 3);
        await context.route('**/pivot/home/slide-1.png', route => route.fulfill({ status: 200, contentType: 'image/png', body: 'broken-image' }));
        await page.reload(); await savedReady(page);
        await saved.getByText('이미지 준비 중', { exact: true }).first().waitFor();
        assert.equal(await saved.getByText('이미지 준비 중', { exact: true }).count(), 2); await capture(page, 'saved-image-failure-' + name);
        await cards.first().click(); await detailReady(page);
        assert.equal(await page.locator('[data-detail-content]').getAttribute('data-detail-content'), geometry[0].contentId);
        await detail.getByRole('button', { name: '뒤로가기', exact: true }).click(); await savedReady(page);
        await context.unroute('**/pivot/home/slide-1.png');
        // Fresh direct Detail has no trusted return snapshot; use the local Saved fallback.
        await page.goto(base + '/pivot/demo?view=detail&from=saved&postId=demo-home-reading&contentId=demo-content-reading'); await detailReady(page);
        await detail.getByRole('button', { name: '뒤로가기', exact: true }).click(); await savedReady(page);
        await page.goto(base + '/pivot/home?view=saved&savedState=empty'); await savedReady(page, 'unconfigured');
        assert.equal(await cards.count(), 0); assert.equal(await page.locator('[data-demo-bookmark]').count(), 0);
        assert.doesNotMatch(await page.locator('body').innerText(), demoOnlySavedCopy); await capture(page, 'saved-service-' + name);
        await page.reload(); await savedReady(page, 'unconfigured');
        await saved.getByRole('button', { name: 'MY', exact: true }).click(); await page.locator('[data-my-state="unconfigured"]').waitFor();
        await page.locator('[data-my-state]').getByRole('button', { name: 'HOME', exact: true }).click(); await page.locator('[data-home-state="unconfigured"]').waitFor({ state: 'visible' });
        await page.locator('[data-home-state]').getByRole('button', { name: 'SAVE', exact: true }).click(); await savedReady(page, 'unconfigured');
        await page.goto(base + '/pivot/home?view=detail&from=saved&postId=301&contentId=301&topicId=9'); await detailReady(page, 'unconfigured');
        assert.equal(await detail.getByRole('button', { name: '저장', exact: true }).getAttribute('aria-disabled'), 'true');
        await detail.getByRole('button', { name: '뒤로가기', exact: true }).click(); await savedReady(page, 'unconfigured');
        assert.deepEqual(api, []); assert.deepEqual(external, []); assert.deepEqual(errors, []);
        results.push({ name, status: 'pass', browser: browser.version(),
          homeBookmarkInput: 'pointer at every width; no Detail misnavigation', homeBookmarkClicks,
          panel, geometry, emptyGeometry, longListLayout, chrome, before, beforeDetail, detailY, api, external, errors });
        console.log('PASS Saved ' + name);
      } catch (error) {
        results.push({ name, status: 'fail', error: error.stack, api, external, errors });
        await page.screenshot({ path: path.join(out, name + '-failure.png') }).catch(() => {}); console.error('FAIL Saved ' + name + ': ' + error.stack);
      } finally { await context.close(); }
    }
  } finally { await browser.close(); fs.writeFileSync(path.join(out, 'results.json'), JSON.stringify(results, null, 2)); }
  if (results.some(r => r.status !== 'pass')) process.exitCode = 1;
})().catch(error => { console.error(error); process.exitCode = 1; });
