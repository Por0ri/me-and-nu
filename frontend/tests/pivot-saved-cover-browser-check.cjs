// Real service adapters and components, with loopback HTTP responses intercepted
// in the browser. No backend, persistent writes or library installation.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const { createApiFixture } = require('./helpers/pivot-api-fixture.cjs');
const base = process.argv[2] || 'http://localhost:3196';
const apiOrigin = 'http://localhost:8180';
const out = process.argv[3] || path.join(require('node:os').tmpdir(), 'pivot-saved-cover');
assert.equal(new URL(base).hostname, 'localhost'); assert.equal(new URL(base).protocol, 'http:');
const near = (a, b, label) => assert.ok(Math.abs(a - b) < 1, `${label}: ${a} / ${b}`);
const results = [];
(async () => {
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch({ channel: process.env.PIVOT_BROWSER_CHANNEL || 'msedge', headless: true });
  try {
    for (const width of [320, 375, 390]) {
      const context = await browser.newContext({ viewport: { width, height: 844 }, reducedMotion: 'reduce', serviceWorkers: 'block' });
      const page = await context.newPage(); page.setDefaultTimeout(12000);
      const fixture = createApiFixture({ apiOrigin, frontendOrigin: base }); fixture.rotate('active');
      let imageMode = 'loading', releaseImage;
      const unexpected = [], errors = [], protectedCalls = [];
      const covers = [base + '/pivot/home/chrome/mascot.png', '/api/v1/contents/42/slides/1/image?topicId=7', null, base + '/broken-cover.png'];
      for (const id of [43, 44]) fixture.contents.push({ id, title: `이미지 예외 콘텐츠 ${id}`, summary: '요약', body: '본문' });
      fixture.contents.forEach(c => fixture.state.saved.add(c.id));
      page.on('pageerror', e => errors.push(e.message));
      await context.route('**/*', async route => {
        const request = route.request(), url = new URL(request.url());
        const headers = { 'access-control-allow-origin': base, 'access-control-allow-credentials': 'true' };
        if (url.origin === apiOrigin) {
          if (url.pathname === '/api/v1/contents/42/slides/1/image') {
            protectedCalls.push({ method: request.method(), query: url.search, mode: imageMode });
            if (imageMode === 'loading') await new Promise(resolve => { releaseImage = resolve; });
            return route.fulfill({ status: imageMode === 'error' ? 404 : 200, headers, contentType: 'image/png',
              body: imageMode === 'error' ? '' : fs.readFileSync(path.join(__dirname, '../public/pivot/home/slide-1.png')) }).catch(() => {});
          }
          const response = fixture.dispatch(url.href, { method: request.method(), headers: request.headers(), body: request.postData() });
          if (url.pathname === '/api/v1/users/me/bookmarks') response.body.items.forEach((item, i) => { item.imageUrl = covers[i]; });
          if (response.body?.code === 'FIXTURE_ROUTE_NOT_FOUND') unexpected.push(request.method() + ' ' + url.pathname);
          return route.fulfill({ status: response.status, headers: { ...headers, ...response.headers }, contentType: 'application/json', body: JSON.stringify(response.body) });
        }
        if (url.origin !== base) { unexpected.push(url.href); return route.abort(); }
        if (url.pathname === '/broken-cover.png') return route.fulfill({ status: 200, contentType: 'image/png', body: 'invalid-pixels' });
        return route.continue();
      });
      try {
        await page.goto(base + '/pivot/home?view=saved');
        const saved = page.locator('[data-saved-state="ready"]:visible'); await saved.waitFor();
        await page.waitForFunction(() => document.activeElement === document.querySelector('[data-saved-state] h1'));
        const card = id => saved.locator(`[data-saved-content="${id}"]`);
        const overlay = id => card(id).locator('[data-saved-title]').locator('..');
        const shown = async id => {
          const b = await card(id).boundingBox(), t = await overlay(id).boundingBox();
          assert.ok(t.width > 1 && t.y >= b.y && t.y + t.height <= b.y + b.height, 'Title stays inside thumbnail');
          await assert.doesNotReject(() => saved.getByRole('button', { name: fixture.contents.find(c => c.id === id).title + ' 자세히 보기', exact: true }).waitFor());
        };
        await card(41).locator('img').evaluate(e => e.decode());
        await card(44).getByText('이미지 준비 중', { exact: true }).waitFor();
        await card(42).locator('[data-carousel-image-state="loading"]').waitFor();
        for (const id of [41, 42, 43, 44]) await shown(id);
        const geometry = await saved.locator('[data-saved-content]').evaluateAll(items => items.map(e => {
          const r = e.getBoundingClientRect(), t = e.firstElementChild.getBoundingClientRect();
          return { x: r.x, y: r.y, width: r.width, height: r.height, imageHeight: t.height, titleCount: e.querySelectorAll('[data-saved-title]').length, children: e.children.length };
        }));
        for (const g of geometry) {
          near(g.width, (width - 79) / 2, 'Two-column width'); near(g.height, g.width * 184.661 / 148, 'Thumbnail card height');
          near(g.height, g.imageHeight, 'No extra title row'); assert.equal(g.titleCount, 1); assert.equal(g.children, 1);
        }
        if (width === 375) near(geometry[0].width, 148, 'Figma width');
        near(geometry[1].x - geometry[0].x - geometry[0].width, 8, 'Column gap');
        await page.screenshot({ path: path.join(out, `loading-${width}.png`), fullPage: true });
        assert.ok(releaseImage); imageMode = 'ready'; releaseImage();
        await card(42).locator('[data-carousel-image-state="ready"]').waitFor();
        const protectedTitle = await overlay(42).boundingBox();
        near(protectedTitle.width, 1, 'Successful protected cover hides duplicate title');
        assert.equal(await overlay(42).evaluate(e => getComputedStyle(e).clipPath), 'inset(50%)');
        await page.screenshot({ path: path.join(out, `covers-${width}.png`), fullPage: true });
        for (const id of [41, 42, 43, 44]) {
          await card(id).click();
          await page.locator(`[data-detail-content="${id}"]:visible`).waitFor();
          const query = new URL(page.url()).searchParams;
          assert.equal(query.get('contentId'), String(id)); assert.equal(query.get('topicId'), '7'); assert.equal(query.get('from'), 'saved');
          await page.locator('[data-detail-state]:visible').getByRole('button', { name: '뒤로가기', exact: true }).click();
          await saved.waitFor();
        }
        imageMode = 'error'; await page.reload(); await saved.waitFor();
        await card(42).locator('[data-carousel-image-state="error"]').waitFor(); await shown(42);
        assert.equal(await card(42).locator('button').count(), 0, 'No nested retry button');
        await page.screenshot({ path: path.join(out, `protected-failure-${width}.png`), fullPage: true });
        await card(42).click(); await page.locator('[data-detail-content="42"]:visible').waitFor();
        assert.deepEqual(unexpected, []); assert.deepEqual(errors, []);
        results.push({ width, status: 'pass', browser: browser.version(), geometry, protectedCalls,
          checks: ['text-free overlay', 'missing/broken image identity', 'protected loading/ready/error', 'no title duplication', 'accessible names', 'all four DETAIL targets'] });
        console.log('PASS Saved covers ' + width);
      } catch (error) {
        results.push({ width, status: 'fail', error: error.stack, unexpected, errors });
        await page.screenshot({ path: path.join(out, `failure-${width}.png`) }); console.error(error);
      } finally { releaseImage?.(); await context.close(); }
    }
  } finally { await browser.close(); fs.writeFileSync(path.join(out, 'results.json'), JSON.stringify(results, null, 2)); }
  if (results.some(r => r.status === 'fail')) process.exitCode = 1;
})().catch(e => { console.error(e); process.exitCode = 1; });
