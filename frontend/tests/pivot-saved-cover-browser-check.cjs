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
const before = process.argv[4] === 'before';
(async () => {
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch({ channel: process.env.PIVOT_BROWSER_CHANNEL || 'msedge', headless: true });
  try {
    for (const width of before ? [375] : [320, 375, 390]) {
      const context = await browser.newContext({ viewport: { width, height: 844 }, reducedMotion: 'reduce', serviceWorkers: 'block' });
      const page = await context.newPage(); page.setDefaultTimeout(12000);
      const fixture = createApiFixture({ apiOrigin, frontendOrigin: base }); fixture.rotate('active');
      const eyebrow = '새로운 시선 · 편집자의 선택';
      const box = { x: 0.06, y: 0.67, width: 0.88, height: 0.29 };
      const slides = [1, 2].map(page => ({ page, role: page === 1 ? 'cover' : 'body', eyebrow: page === 1 ? eyebrow : '두 번째 슬라이드',
        titleLines: ['연결 검증 콘텐츠 하나'], bodySentences: ['공개 요약'], width: 1308, height: 1632,
        imageUrl: `/api/v1/contents/41/slides/${page}/image?topicId=7`,
        layout: { template: 'split', fitMode: 'contain_blur', backgroundStyle: 'blur', imageBox: box, copyBox: box,
          textOverlayBox: box, imageScale: 1, imageOffsetY: 0, contrastMode: 'scrim_required', blurBackground: false } }));
      fixture.contents.find(c => c.id === 42).title = '보호 이미지에서도 제목과 요약을 읽을 수 있는 저장 콘텐츠';
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
          if (/^\/api\/v1\/contents\/41\/slides\/\d+\/image$/.test(url.pathname)) return route.fulfill({ status: 200, headers,
            contentType: 'image/png', body: fs.readFileSync(path.join(__dirname, '../public/pivot/home/chrome/mascot.png')) });
          if (url.pathname === '/api/v1/contents/42/slides/1/image') {
            protectedCalls.push({ method: request.method(), query: url.search, mode: imageMode });
            if (imageMode === 'loading') await new Promise(resolve => { releaseImage = resolve; });
            return route.fulfill({ status: imageMode === 'error' ? 404 : 200, headers, contentType: 'image/png',
              body: imageMode === 'error' ? '' : fs.readFileSync(path.join(__dirname, '../public/pivot/home/chrome/mascot.png')) }).catch(() => {});
          }
          const response = fixture.dispatch(url.href, { method: request.method(), headers: request.headers(), body: request.postData() });
          if (url.pathname === '/api/v1/topics/7/feed') response.body.sections[0].contents.find(item => item.id === 41).slides = slides;
          if (url.pathname === '/api/v1/users/me/bookmarks') response.body.items.forEach((item, i) => {
            item.imageUrl = covers[i];
            item.summary = i < 2 ? '새로운 시선으로 살펴보는 콘텐츠의 요약입니다.' : null;
            item.sourceName = '테스트 매거진';
            if (i < 2) item.eyebrow = eyebrow;
            else if (i === 2) item.eyebrow = null;
            else delete item.eyebrow;
          });
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
        const overlay = id => card(id).locator('[data-saved-title]').locator('xpath=ancestor::span[contains(@class,"savedOverlay")]');
        const measureText = id => card(id).locator('[data-saved-title]').evaluate(e => {
          const r = e.getBoundingClientRect(), range = document.createRange(); range.selectNodeContents(e);
          const glyph = range.getBoundingClientRect();
          const parents = []; for (let n = e; n && n.tagName !== 'BUTTON'; n = n.parentElement) {
            const s = getComputedStyle(n); parents.push({ display: s.display, visibility: s.visibility, opacity: Number(s.opacity), clip: s.clipPath, width: n.clientWidth, height: n.clientHeight });
          }
          return { text: e.textContent, width: r.width, height: r.height, glyphWidth: glyph.width, glyphHeight: glyph.height,
            font: getComputedStyle(e).fontSize, lineHeight: getComputedStyle(e).lineHeight, color: getComputedStyle(e).color,
            clamp: getComputedStyle(e).webkitLineClamp, parents };
        });
        const shown = async id => {
          const b = await card(id).boundingBox(), t = await overlay(id).boundingBox();
          assert.ok(t.width > 1 && t.y >= b.y && t.y + t.height <= b.y + b.height, 'Title stays inside thumbnail');
          const text = await measureText(id);
          assert.ok(text.width > 1 && text.height > 1 && text.glyphWidth > 1 && text.glyphHeight > 1, 'Title has painted text geometry');
          assert.ok(text.parents.every(s => s.display !== 'none' && s.visibility === 'visible' && s.opacity > 0 && s.clip === 'none' && s.width > 1 && s.height > 1), 'Title and every ancestor are visually exposed');
          assert.equal(text.clamp, '2');
          assert.ok(text.height <= 2 * parseFloat(text.lineHeight) + .1, 'Title is limited to two visual lines');
          await assert.doesNotReject(() => saved.getByRole('button', { name: fixture.contents.find(c => c.id === id).title + ' 자세히 보기', exact: true }).waitFor());
          return text;
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
        const protectedTitle = await measureText(42);
        if (before) {
          await page.screenshot({ path: path.join(out, 'before-375.png'), fullPage: true });
          results.push({ width, status: 'baseline', geometry, protectedTitle });
          console.log('CAPTURED before 375'); continue;
        }
        await shown(42);
        for (const id of [41, 42]) {
          assert.equal(await card(id).locator('[data-saved-summary]').innerText(), '새로운 시선으로 살펴보는 콘텐츠의 요약입니다.');
          assert.equal(await card(id).locator('[data-saved-eyebrow]').innerText(), eyebrow);
          for (const selector of ['[data-saved-summary]', '[data-saved-eyebrow]']) {
            const r = await card(id).locator(selector).boundingBox(); assert.ok(r.width > 1 && r.height > 1);
          }
        }
        for (const id of [43, 44]) assert.equal(await card(id).locator('[data-saved-summary], [data-saved-eyebrow]').count(), 0, 'Null or missing metadata is omitted');
        assert.equal(await saved.getByText(/테스트 매거진|출처:/).count(), 0, 'Source attribution never becomes the purple label');
        await page.screenshot({ path: path.join(out, `covers-${width}.png`), fullPage: true });
        await page.goto(base + '/pivot/home');
        const homeEyebrow = page.locator('[data-home-post="41"] [data-content-slide="1"] [data-slide-overlay] p').first();
        await homeEyebrow.waitFor();
        assert.equal(await homeEyebrow.innerText(), eyebrow, 'Same content first slide uses identical HOME and SAVE copy');
        await page.goto(base + '/pivot/home?view=saved'); await saved.waitFor();
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
        await page.goto(base + '/pivot/demo?view=saved');
        const demo = page.locator('[data-saved-state="ready"]:visible'); await demo.waitFor();
        await demo.locator('[data-saved-content] img').evaluateAll(items => Promise.all(items.map(e => e.decode())));
        const demoTitles = await demo.locator('[data-embedded-copy]').evaluateAll(items => items.map(e => {
          const label = e.querySelector('[data-saved-title]'), overlay = e.querySelector('[class*="savedOverlay"]'), s = getComputedStyle(overlay);
          return { title: label.textContent, clip: s.clipPath, width: overlay.clientWidth, height: overlay.clientHeight };
        }));
        assert.equal(demoTitles.length, 3); assert.ok(demoTitles.every(t => t.clip === 'inset(50%)' && t.width === 1 && t.height === 1));
        await page.screenshot({ path: path.join(out, `demo-${width}.png`), fullPage: true });
        const demoIds = await demo.locator('[data-saved-content]').evaluateAll(items => items.map(item => item.dataset.savedContent));
        for (const contentId of demoIds) {
          await demo.locator(`[data-saved-content="${contentId}"]`).click();
          await page.locator(`[data-detail-content="${contentId}"]:visible`).waitFor();
          await page.locator('[data-detail-state]:visible').getByRole('button', { name: '뒤로가기', exact: true }).click();
          await demo.waitFor();
        }
        await context.route('**/pivot/home/slide-1.png', route => route.fulfill({ status: 200, contentType: 'image/png', body: 'invalid-pixels' }));
        await page.reload(); await demo.waitFor();
        const failedDemo = demo.locator('[data-saved-content]').first();
        await failedDemo.getByText('이미지 준비 중', { exact: true }).waitFor();
        const failedDemoLabel = await failedDemo.locator('[data-saved-title]').boundingBox();
        assert.ok(failedDemoLabel.width > 1 && failedDemoLabel.height > 1, 'Failed verified Demo cover reveals its canonical title');
        await context.unroute('**/pivot/home/slide-1.png');
        assert.deepEqual(unexpected, []); assert.deepEqual(errors, []);
        results.push({ width, status: 'pass', browser: browser.version(), geometry, protectedCalls, protectedTitle, demoTitles,
          checks: ['text-free overlay', 'same HOME page-one eyebrow', 'sourceName excluded', 'null/missing eyebrow omitted', 'summary unchanged', 'missing/broken image identity', 'protected loading/ready/error text visible', 'explicit Demo copy only', 'accessible names', 'all four DETAIL targets'] });
        console.log('PASS Saved covers ' + width);
      } catch (error) {
        results.push({ width, status: 'fail', error: error.stack, unexpected, errors });
        await page.screenshot({ path: path.join(out, `failure-${width}.png`) }); console.error(error);
      } finally { releaseImage?.(); await context.close(); }
    }
  } finally { await browser.close(); fs.writeFileSync(path.join(out, 'results.json'), JSON.stringify(results, null, 2)); }
  if (results.some(r => r.status === 'fail')) process.exitCode = 1;
})().catch(e => { console.error(e); process.exitCode = 1; });
