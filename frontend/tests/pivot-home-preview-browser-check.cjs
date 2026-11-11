// Local API mocks only. No request to Checking or another remote service is allowed.
// Start Next with NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8195 and
// NEXT_PUBLIC_PIVOT_ENABLE_LOCAL_API=true. Uses the existing Agent text snapshot;
// slide boxes are contract fixtures, NOT evidence that production supplies those layouts.
// Run from frontend after approval. Screenshots/JSON may only go to the dedicated
// OS temporary folder pivot-home-preview-v2. No existing profile or cookies are used.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const os = require('node:os');
const { chromium } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
const base = process.argv[2] || 'http://127.0.0.1:3195';
const output = path.resolve(process.argv[3] || path.join(os.tmpdir(), 'pivot-home-preview-v2'));
const local = url => url.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(url.hostname);
const expectedRoot = path.resolve(__dirname, '..');
assert.equal(fs.realpathSync(process.cwd()), fs.realpathSync(expectedRoot), 'Run from the designated frontend directory');
assert.equal(base, 'http://127.0.0.1:3195', 'Only the dedicated local frontend is allowed');
const apiOrigin = 'http://127.0.0.1:8195';
const artifactRoots = [path.resolve(os.tmpdir(), 'pivot-home-preview-v2')];
const within = (parent, child) => { const relative = path.relative(parent, child); return relative === '' || (!relative.startsWith('..') && !path.isAbsolute(relative)); };
assert.ok(artifactRoots.some(parent => within(parent, output)), 'Output must stay in the dedicated artifact or temporary directory');
// Reject existing symlink/junction ancestors before creating directories or files.
for (let current = output; current !== path.dirname(current); current = path.dirname(current)) {
  if (fs.existsSync(current)) assert.equal(fs.lstatSync(current).isSymbolicLink(), false, 'No redirected output directories');
}
const artifact = name => {
  assert.equal(path.basename(name), name, 'Artifact names cannot contain paths');
  const file = path.join(output, name);
  if (fs.existsSync(file)) assert.equal(fs.lstatSync(file).isSymbolicLink(), false, 'No redirected output files');
  return file;
};
// Newer checkouts omit the old Agent export; allow the same read-only snapshot
// to be supplied from the isolated test artifacts without changing service data.
const exported = JSON.parse(fs.readFileSync(process.env.PIVOT_CONTENT_FIXTURE || path.join(root, '../agent/contents/contents.json'), 'utf8'));
const longest = key => [...exported].sort((a, b) => b[key].length - a[key].length)[0];
const originals = [longest('title'), longest('body'), exported[0]];
const boxes = [
  { x: .06, y: .67, width: .88, height: .29 }, // API integration fixture
  { x: .06, y: .12, width: .42, height: .76 }, // left contract boundary
  { x: .52, y: .12, width: .42, height: .76 }, // right contract boundary
];
const fullBox = { x: 0, y: 0, width: 1, height: 1 };
const items = originals.map((original, index) => {
  const id = 41 + index;
  return { id, original, title: original.title, body: original.body, summary: original.summary,
    slides: Array.from({ length: index ? 3 : 10 }, (_, i) => ({ page: i + 1, role: i ? 'body' : 'cover',
      eyebrow: original.subtopic, titleLines: i === 1 ? [original.title, '긴 제목을 읽는 흐름과 말줄임을 확인하는 추가 제목'] : i === 2 ? ['짧은 제목'] : [original.title],
      bodySentences: i === 1 ? ['기존 상세 본문에 없는 두 번째 슬라이드의 원문입니다.', '여러 슬라이드에서 같은 문장을 반복하는 검증입니다.']
        : i === 2 ? ['짧은 요약입니다.'] : i === 3 ? ['여러 슬라이드에서 같은 문장을 반복하는 검증입니다.']
        : original.body.split(/\n\s*\n/).slice(0, 4), width: 1308, height: 1632,
      imageUrl: `/api/v1/contents/${id}/slides/${i + 1}/image?topicId=7`,
      layout: { template: 'split', fitMode: 'contain_blur', backgroundStyle: 'blur', imageBox: fullBox,
        copyBox: boxes[index], textOverlayBox: boxes[index], imageScale: 1, imageOffsetY: 0,
        contrastMode: 'scrim_required', blurBackground: false },
    })) };
});
// Figma copy is a local layout fixture only; never replace service article text.
const figmaTitle = ['사이버펑크: 엣지러너 2', '화면은 번쩍, 평은 갈린다'];
Object.assign(items[0].slides[8], { eyebrow: '리뷰 큐레이션', titleLines: figmaTitle,
  bodySentences: ['리뷰어들이 짚은 매력과 아쉬움,\n팬 시선으로 골라봤어.'] });
Object.assign(items[0].slides[9], { eyebrow: '리뷰 큐레이션', titleLines: figmaTitle,
  bodySentences: ['한 리뷰어는 로만을 그나마 멀쩡한 인물로 봤고, 다른 리뷰어는 그의 행동에 답답함을 느꼈다.'] });
// Existing local PNG stands in for media. Cover/body use verbatim Agent text;
// later slides add clearly synthetic short/long/missing/duplicate-text fixtures.
const png = fs.readFileSync(path.join(root, 'public/local-content/licorice-pizza-poster.png'));
const results = []; const geometry = [];
const until = async (fn, message) => {
  const limit = Date.now() + 12000;
  while (!await fn()) { if (Date.now() > limit) throw new Error(message); await new Promise(resolve => setTimeout(resolve, 40)); }
};
const indexOf = track => track.evaluate(el => {
  const index = Math.round(el.scrollLeft / el.clientWidth);
  const active = el.querySelector('[data-carousel-slide][aria-hidden="false"]');
  return Math.abs(el.scrollLeft - index * el.clientWidth) < 1 && Number(active?.getAttribute("data-carousel-slide")) === index + 1 ? index : -1;
});
async function swipe(page, element, dx, dy, fraction = [.6, .45]) {
  await element.scrollIntoViewIfNeeded();
  const b = await element.boundingBox();
  const x = b.x + b.width * fraction[0], y = b.y + b.height * fraction[1];
  const cdp = await page.context().newCDPSession(page);
  const points = n => [{ x: x + dx * n, y: y + dy * n, id: 1, radiusX: 2, radiusY: 2, force: 1 }];
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: points(0) });
  for (let i = 1; i <= 16; i++) {
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: points(i / 16) });
    await page.waitForTimeout(20);
  }
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await cdp.detach();
}
async function mouseDrag(page, track, dx, dy = 0, finish) {
  await track.scrollIntoViewIfNeeded();
  const b = await track.boundingBox(), x = b.x + b.width * (dx < 0 ? .8 : .2), y = b.y + b.height * .3;
  await page.mouse.move(x, y); await page.mouse.down();
  try {
    await page.mouse.move(x + dx, y + dy, { steps: 12 });
    if (finish) await finish();
  } finally { await page.mouse.up(); }
}
async function hit(locator) {
  await locator.scrollIntoViewIfNeeded();
  // Wait for the scroll and collision-avoidance paint, without changing the hit assertion.
  await locator.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const result = await locator.evaluate(el => { const r = el.getBoundingClientRect(); const found = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
    return { hit: el.contains(found), label: el.getAttribute('aria-label'), rect: r.toJSON(), found: found?.outerHTML.slice(0, 250) }; });
  if (!result.hit) console.error('HIT DIAGNOSTIC', result);
  return result.hit;
}
// Measure the interactive face; the exported SVG includes a non-interactive shadow.
async function aiObstructions(ai) {
  return ai.evaluate(el => {
    const button = el.getBoundingClientRect();
    const overlaps = (a, b) => a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
    const controls = [...el.closest('main').querySelectorAll('nav, [data-home-post] button')].filter(control => {
      const rect = control.getBoundingClientRect(), css = getComputedStyle(control);
      return !control.closest('[inert]') && css.visibility === 'visible' && css.pointerEvents !== 'none' &&
        Number(css.opacity) > 0 && rect.width && rect.height && overlaps(button, rect);
    });
    return controls.map(control => control.getAttribute('aria-label') || control.textContent);
  });
}
async function run(browser, viewport) {
  const context = await browser.newContext({ viewport, isMobile: viewport.width < 600, hasTouch: true,
    reducedMotion: 'reduce', serviceWorkers: 'block' });
  const page = await context.newPage(); page.setDefaultTimeout(12000); page.setDefaultNavigationTimeout(60000);
  const calls = [], external = [], unexpected = [], errors = [], checks = [], failures = [];
  const saved = new Set(), liked = new Set(), preferences = new Map();
  let imageFailure = null, imageLoading = null, detailMode = "full_body", detailSlides = null, imageFailureStatus = 200;
  page.on('pageerror', error => errors.push(error.message));
  await context.routeWebSocket('**/*', socket => {
    const url = new URL(socket.url());
    if (url.origin === base.replace('http:', 'ws:') && url.pathname.startsWith('/_next/')) socket.connectToServer();
    else { external.push(url.href); socket.close(); }
  });
  await context.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), method = req.method();
    if (!local(url) || ![base, apiOrigin].includes(url.origin)) { external.push(url.href); return route.abort(); }
    if (url.pathname === '/favicon.ico') return route.fulfill({ status: 204 });
    if (!url.pathname.startsWith('/api/')) {
      if (url.origin === base && ['GET', 'HEAD'].includes(method)) return route.continue();
      unexpected.push(url.href); return route.abort();
    }
    calls.push({ path: url.pathname, method });
    const reply = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: status === 204 ? undefined : JSON.stringify(body) });
    if (method === 'OPTIONS') return reply(undefined, 204);
    if (url.pathname === '/api/v1/auth/session') return reply({ authenticated: true, sessionState: 'active',
      onboardingCompleted: true, user: { id: 12, accountType: 'user' }, csrfToken: 'local-mvp-csrf' });
    if (url.pathname === '/api/v1/me/topics') return reply({ topics: [{ id: 7, code: 'anime', name: '애니메이션', subtopicIds: [], status: 'active', deletedAt: null }] });
    const publication = item => ({ title: item.title, productionType: 'ai', publishedAt: '2026-11-02T00:00:00Z',
      sourceName: item.original.sourceName, publisher: item.original.sourceName, sourceUrl: item.original.sources[0].url,
      notices: [{ code: 'AI_GENERATED', message: 'AI가 생성한 콘텐츠입니다.' }] });
    if (url.pathname === '/api/v1/topics/7/feed') return reply({ selectedTopicId: 7, sections: [{ id: 'recommended', type: 'recommended',
      title: '추천 콘텐츠', subtopicId: null, contents: items.map(item => ({ ...publication(item), kind: 'content', id: item.id, topicId: 7,
        summary: item.summary, imageUrl: item.slides[0].imageUrl, slides: item.slides, saved: saved.has(item.id),
        savedItemId: saved.has(item.id) ? item.id + 100 : null, seen: false, isPromotional: false, recommendationId: null,
        recommendationReason: null, myReaction: { liked: liked.has(item.id), preference: preferences.get(item.id) || null } })) }], nextCursor: null, emptyReason: null });
    if (url.pathname === '/api/v1/users/me/bookmarks') return reply({ items: items.filter(item => saved.has(item.id)).map(item => ({
      savedItemId: item.id + 100, contentId: item.id, topicId: 7, title: item.title, summary: item.summary,
      imageUrl: item.slides[0].imageUrl, sourceName: item.original.sourceName, tags: [], resurfaceEnabled: true })), nextCursor: null });
    if (url.pathname === '/api/v1/users/me/activity-events') return reply({ saved: req.postDataJSON().events.length, duplicated: 0, rejected: 0 });
    const match = url.pathname.match(/^\/api\/v1\/contents\/(\d+)(.*)$/);
    if (match) {
      const id = Number(match[1]), item = items.find(item => item.id === id), action = match[2];
      if (item && /\/slides\/\d+\/image/.test(action)) {
        const deadline = Date.now() + 5000;
        while (imageLoading === id && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 20));
        if (imageFailure === id) return route.fulfill({ status: imageFailureStatus, contentType: 'image/png', body: 'invalid-png' });
        return route.fulfill({ status: 200, contentType: 'image/png', body: png, headers: { 'cache-control': 'private, no-store' } });
      }
      if (item && !action) return reply({ ...publication(item), contentId: id, topicId: 7, displayMode: detailMode, excerpt: item.summary,
        body: item.body, contentType: 'article', imageUrl: item.slides[0].imageUrl, slides: detailSlides || item.slides,
        sources: item.original.sources.map((source, i) => ({ url: source.url, title: source.name, role: 'citation', citationOrder: i + 1 })),
        myReaction: { liked: liked.has(id), preference: preferences.get(id) || null, bookmarked: saved.has(id) },
        subtopicIds: [], practicalInfo: {}, isPromotional: false, share: { url: item.original.sources[0].url, text: item.title }, channel: null });
      if (item && action === '/bookmark') {
        if (method === 'DELETE') { saved.delete(id); return reply(undefined, 204); }
        saved.add(id); return reply({ savedItemId: id + 100, contentId: id, topicId: 7, tags: [], resurfaceEnabled: true, savedAt: '2026-11-02T00:00:00Z' });
      }
      if (item && action === '/like') {
        if (method === 'DELETE') { liked.delete(id); return reply(undefined, 204); }
        liked.add(id); return reply({ contentId: id, liked: true });
      }
      if (item && action === '/preference') {
        if (method === 'DELETE') { preferences.delete(id); return reply(undefined, 204); }
        preferences.set(id, req.postDataJSON().value); return reply({ contentId: id, topicId: 7, value: preferences.get(id) });
      }
    }
    unexpected.push({ path: url.pathname, method }); return route.abort();
  });
  const check = async (name, fn) => {
    try { await fn(); checks.push(name); } catch (error) { failures.push({ name, error: error.message }); console.error('FAIL', viewport.width, name, error.message); }
  };
  const capture = async name => {
    await page.evaluate(() => document.fonts.ready);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'no horizontal page overflow');
    await page.screenshot({ path: artifact(`${viewport.width}-${name}.png`) });
  };
  async function inspectPreview(card, slide, label) {
    const overlay = slide.locator('[data-carousel-overlay]');
    const geometryData = await overlay.evaluate(el => {
      const r = el.getBoundingClientRect(), stage = el.parentElement.getBoundingClientRect();
      const title = el.querySelector('h3'), copy = el.querySelector('div > p');
      const measure = node => { if (!node) return null; const b = node.getBoundingClientRect(), css = getComputedStyle(node);
        return { rect: b.toJSON(), lineHeight: parseFloat(css.lineHeight), font: parseFloat(css.fontSize), clamp: css.webkitLineClamp,
          family: css.fontFamily, weight: css.fontWeight, spacing: css.letterSpacing }; };
      const descendants = [el, ...el.querySelectorAll('*')];
      const movable = descendants.filter(node => { node.scrollTop = 100; const moved = node.scrollTop > 0; node.scrollTop = 0; return moved; }).length;
      return { rect: r.toJSON(), stage: stage.toJSON(), scroll: el.scrollHeight, client: el.clientHeight,
        overflow: getComputedStyle(el).overflowY, movable, title: measure(title), copy: measure(copy) };
    });
    geometry.push({ viewport, label, ...geometryData });
    assert.ok(Math.abs(geometryData.stage.height - geometryData.stage.width * 408 / 327) < 1);
    assert.equal(geometryData.overflow, 'clip'); assert.equal(geometryData.movable, 0);
    assert.ok(geometryData.scroll <= geometryData.client + 1, 'no vertical overflow inside the overlay');
    assert.equal(geometryData.title.font, 18); assert.equal(geometryData.title.lineHeight, 26);
    assert.match(geometryData.title.family, /PivotPretendard/); assert.equal(geometryData.title.weight, '600');
    assert.ok(['normal', '0px'].includes(geometryData.title.spacing));
    assert.equal(geometryData.title.clamp, '3');
    assert.ok(geometryData.title.rect.height <= 3 * 26 + 1 && geometryData.title.rect.height >= 26);
    assert.ok(Math.abs(geometryData.title.rect.height / 26 - Math.round(geometryData.title.rect.height / 26)) < .03, 'title ends at a complete line');
    if (geometryData.copy) {
      assert.equal(geometryData.copy.font, 14); assert.equal(geometryData.copy.lineHeight, 20); assert.equal(geometryData.copy.clamp, '3');
      assert.match(geometryData.copy.family, /PivotPretendard/); assert.equal(geometryData.copy.weight, '400');
      assert.ok(['normal', '0px'].includes(geometryData.copy.spacing));
      assert.ok(geometryData.copy.rect.height <= 60 + 1);
      assert.ok(geometryData.title.rect.bottom <= geometryData.copy.rect.top, 'title and summary do not overlap');
    }
    for (const button of [card.getByRole('button', { name: '저장', exact: true }), card.locator('[data-home-detail]')]) {
      const b = await button.boundingBox(), r = geometryData.rect;
      assert.ok(r.right <= b.x || r.bottom <= b.y || r.x >= b.x + b.width || r.y >= b.y + b.height, 'preview is clear of controls');
      assert.ok(await hit(button), 'control remains reachable');
    }
    await capture(label);
  }
  try {
    await page.goto(base + '/pivot/home');
    const home = page.locator('main[data-home-state="ready"]'); await home.waitFor();
    if (viewport.width >= 600) {
      const card = home.locator('[data-home-post="41"]'), track = card.locator('[data-home-track]');
      const reset = async (index = 0) => {
        await card.getByRole('button', { name: `${index + 1}번 슬라이드 보기`, exact: true }).click();
        await until(async () => await indexOf(track) === index, 'mouse test reset');
      };
      await track.evaluate(el => {
        el.__mouseProbe = { clicks: 0, pointerId: null };
        el.closest('[data-home-post]').addEventListener('click', event => {
          if (el.contains(event.target)) el.__mouseProbe.clicks++;
        });
        el.addEventListener('pointerdown', event => { el.__mouseProbe.pointerId = event.pointerId; });
      });
      const clicks = () => track.evaluate(el => el.__mouseProbe.clicks);
      const clean = () => track.evaluate(el => !el.dataset.dragging && !el.hasPointerCapture(el.__mouseProbe.pointerId));
      await check('service Home mouse: both directions, continuous movement, smooth snap and no card click/detail navigation', async () => {
        await page.emulateMedia({ reducedMotion: 'no-preference' });
        try {
          await reset(); const before = await clicks();
          const width = await track.evaluate(el => el.clientWidth);
          await mouseDrag(page, track, -width * .7, 0, async () => {
            const state = await track.evaluate(el => ({ left: el.scrollLeft, width: el.clientWidth,
              dragging: el.dataset.dragging, snap: getComputedStyle(el).scrollSnapType }));
            assert.equal(state.dragging, 'true'); assert.equal(state.snap, 'none');
            assert.ok(Math.abs(state.left - state.width * .7) < 2, 'track follows the mouse before release');
          });
          await until(async () => await indexOf(track) === 1, 'Home drag advances');
          await mouseDrag(page, track, width * .7);
          await until(async () => await indexOf(track) === 0, 'Home drag returns');
          assert.equal(await clicks(), before); assert.ok(await clean());
          assert.equal(await home.isVisible(), true); assert.equal(new URL(page.url()).pathname, '/pivot/home');
          assert.equal(await track.evaluate(el => getComputedStyle(el).scrollSnapType), 'x mandatory');
          await capture('home-mouse-drag');
        } finally { await page.emulateMedia({ reducedMotion: 'reduce' }); }
      });
      await check('service Home mouse: 5px clicks, 6px drags, vertical/equal diagonal cancellation and fresh click', async () => {
        await reset(); const before = await clicks();
        await mouseDrag(page, track, -5); assert.equal(await clicks(), before + 1); assert.ok(await clean());
        await mouseDrag(page, track, -6, 0, async () => assert.equal(await track.getAttribute('data-dragging'), 'true'));
        await until(async () => await indexOf(track) === 0, 'short drag snaps back');
        assert.equal(await clicks(), before + 1);
        for (const [dx, dy] of [[-4, 60], [-6, 6], [-30, 60]]) {
          await mouseDrag(page, track, dx, dy, async () => assert.ok(await clean()));
          assert.equal(await indexOf(track), 0);
        }
        const after = await clicks(); await mouseDrag(page, track, 0); assert.equal(await clicks(), after + 1);
      });
      await check('service Home mouse: cancel/lost capture cleanup, first/last edges and independent card positions', async () => {
        const width = await track.evaluate(el => el.clientWidth);
        for (const cancel of [true, false]) {
          await reset();
          await mouseDrag(page, track, -width * .7, 0, async () => {
            await track.evaluate((el, cancel) => {
              const pointerId = el.__mouseProbe.pointerId;
              if (cancel) el.dispatchEvent(new PointerEvent('pointercancel', { bubbles: true, pointerType: 'mouse', pointerId }));
              else el.releasePointerCapture(pointerId);
            }, cancel);
            // The browser processes pending capture loss before its next pointer
            // event. Deliver a real move before expecting lostpointercapture.
            const box = await track.boundingBox();
            await page.mouse.move(box.x + box.width * .1 + 1, box.y + box.height * .3);
            await until(clean, 'pointer cleanup');
          });
          await until(async () => await indexOf(track) === 1, 'cancel/lost capture aligns');
        }
        await reset(); await mouseDrag(page, track, width * .6); assert.equal(await indexOf(track), 0);
        await reset(9); await mouseDrag(page, track, -width * .6); assert.equal(await indexOf(track), 9);
        const second = home.locator('[data-home-post="42"] [data-home-track]');
        assert.equal(await indexOf(second), 0);
        await mouseDrag(page, second, -width * .7); await until(async () => await indexOf(second) === 1, 'second card drag');
        assert.equal(await indexOf(track), 9); await reset();
      });
      await check('service Home mouse: nested button/link starts retain their activation and never capture', async () => {
        await reset();
        for (const tag of ['button', 'a']) {
          await track.evaluate((el, tag) => {
            const control = document.createElement(tag); control.dataset.dragControlFixture = '';
            control.innerHTML = '<span>입력 보호 검증</span>';
            control.style.cssText = 'position:absolute;top:100px;left:60px;z-index:10;width:180px;height:48px;background:white';
            if (tag === 'button') control.type = 'button'; else control.href = '#local-drag-fixture';
            control.addEventListener('click', event => { event.preventDefault(); control.dataset.activated = 'true'; });
            el.querySelector('[data-carousel-slide][aria-hidden="false"] figure').append(control);
          }, tag);
          const control = track.locator('[data-drag-control-fixture]');
          try {
            await control.locator('span').click();
            assert.equal(await control.getAttribute('data-activated'), 'true');
            await control.evaluate(el => delete el.dataset.activated);
            await control.scrollIntoViewIfNeeded(); const b = await control.boundingBox();
            await page.mouse.move(b.x + 100, b.y + 24); await page.mouse.down();
            await page.mouse.move(b.x + 40, b.y + 24, { steps: 6 }); assert.ok(await clean()); await page.mouse.up();
            // Native link drag-and-drop may omit click; it must remain outside
            // the carousel gesture, and a subsequent ordinary click must work.
            assert.equal(await indexOf(track), 0);
            await control.locator('span').click(); assert.equal(await control.getAttribute('data-activated'), 'true');
          } finally { await page.mouse.up(); await control.evaluate(el => el.remove()); }
        }
      });
    }
    for (const [n, item] of items.entries()) {
      const card = home.locator(`[data-home-post="${item.id}"]`), track = card.locator('[data-home-track]');
      await card.scrollIntoViewIfNeeded();
      await card.locator('[data-carousel-slide="1"] img[data-carousel-image-state="ready"]').waitFor();
      for (const slideIndex of [0, 1, 2]) await check('layout-' + n + '/slide-' + slideIndex + ': preview size, complete lines and controls', async () => {
        await track.focus(); await page.keyboard.press('Home'); await until(async () => await indexOf(track) === 0, 'reset first');
        for (let i = 0; i < slideIndex; i++) { await page.keyboard.press('ArrowRight'); await until(async () => await indexOf(track) === i + 1, 'next'); }
        const activeSlide = card.locator('[data-carousel-slide="' + (slideIndex + 1) + '"]');
        await activeSlide.locator('img[data-carousel-image-state="ready"]').waitFor();
        await inspectPreview(card, activeSlide, 'layout-' + n + '-slide-' + slideIndex);
        const text = await activeSlide.innerText();
        for (const sentence of item.slides[slideIndex].bodySentences) assert.ok(text.includes(sentence), 'full source text stays in the DOM');
      });
      await check(`layout-${n}: touch swipe and previous button`, async () => {
        await track.focus(); await page.keyboard.press('Home'); await until(async () => await indexOf(track) === 0, 'first slide');
        if (viewport.width < 600) await swipe(page, track, -220, 0, [.8, .7]);
        else await page.keyboard.press('ArrowRight');
        await until(async () => await indexOf(track) === 1, 'horizontal swipe');
        if (viewport.width < 600) {
          await swipe(page, track, 220, 0, [.2, .7]);
          await until(async () => await indexOf(track) === 0, 'right swipe over preview returns first');
          await swipe(page, track, -220, 0, [.8, .7]);
          await until(async () => await indexOf(track) === 1, 'left swipe over preview advances');
        }
        const previous = card.getByRole('button', { name: '이전 슬라이드', exact: true });
        await until(() => previous.isEnabled(), 'previous button is enabled after slide change');
        await page.keyboard.press('Tab'); await previous.focus();
        await until(() => previous.evaluate(el => getComputedStyle(el).opacity === '1'), 'keyboard arrow is visible');
        assert.ok(await hit(previous), 'previous button center receives input over server overlay');
        if (viewport.width < 600) await page.keyboard.press('Enter'); else await previous.click();
        await until(async () => await indexOf(track) === 0, 'previous returns first slide');
      });
    }
    await check('Bottom copy follows the card width, 10px inset and five-stop scrim', async () => {
      const card = home.locator('[data-home-post="41"]'), track = card.locator('[data-home-track]');
      for (const [index, expectedHeight] of [[8, 136], [9, 156]]) {
        await card.getByRole('button', { name: `${index + 1}번 슬라이드 보기`, exact: true }).click();
        await until(async () => await indexOf(track) === index, 'Figma fixture slide');
        const slide = card.locator(`[data-carousel-slide="${index + 1}"]`);
        await slide.locator('img[data-carousel-image-state="ready"]').waitFor();
        await page.evaluate(() => document.fonts.ready);
        const metrics = await slide.evaluate(el => {
          const stage = el.querySelector('figure').getBoundingClientRect(), copy = el.querySelector('figcaption').getBoundingClientRect();
          const scrim = el.querySelector('figure > div[aria-hidden="true"]');
          return { width: copy.width, stageWidth: stage.width, copyHeight: copy.height,
            left: copy.left - stage.left, bottom: stage.bottom - copy.bottom,
            scrimHeight: scrim.getBoundingClientRect().height, gradient: getComputedStyle(scrim).backgroundImage };
        });
        assert.equal(metrics.width, Math.min(227, metrics.stageWidth - 100));
        assert.equal(metrics.left, 10); assert.equal(metrics.bottom, 10);
        assert.equal(metrics.scrimHeight, metrics.copyHeight + 20);
        if (metrics.width === 227) assert.equal(metrics.scrimHeight, expectedHeight);
        for (const stop of ['20.673%', '51.442%', '79.327%']) assert.ok(metrics.gradient.includes(stop));
        geometry.push({ viewport, label: 'figma-' + index, ...metrics });
        await inspectPreview(card, slide, 'figma-' + index);
      }
    });
    await check('touch focus never paints large arrows; keyboard focus still reveals working controls', async () => {
      const card = home.locator('[data-home-post="41"]'), track = card.locator('[data-home-track]');
      await track.focus(); await page.keyboard.press('Home'); await until(async () => await indexOf(track) === 0, 'first slide');
      const next = card.getByRole('button', { name: '다음 슬라이드', exact: true });
      await page.keyboard.press('Tab'); await next.focus();
      await until(() => next.evaluate(el => getComputedStyle(el).opacity === '1'), 'keyboard controls remain visible');
      await page.keyboard.press('Enter'); await until(async () => await indexOf(track) === 1, 'keyboard button works');
      if (viewport.width < 600) {
        await track.scrollIntoViewIfNeeded(); const b = await track.boundingBox();
        await page.touchscreen.tap(b.x + b.width * .5, b.y + b.height * .2);
        await swipe(page, track, -220, 0, [.8, .3]); await until(async () => await indexOf(track) === 2, 'touch reaches third slide');
        for (const name of ['이전 슬라이드', '다음 슬라이드']) {
          await until(() => card.getByRole('button', { name, exact: true }).evaluate(el => getComputedStyle(el).opacity === '0' && getComputedStyle(el).pointerEvents === 'none'), 'touch arrows are hidden and do not intercept swipes');
        }
        // A dot is a genuine focusable touch target: its focus must not expose the arrows either.
        const dot = card.getByRole('button', { name: '1번 슬라이드 보기', exact: true });
        await dot.tap(); await until(async () => await indexOf(track) === 0, 'touch dot works');
        await until(() => next.evaluate(el => getComputedStyle(el).opacity === '0'), 'dot touch does not expose arrows');
        await capture('touch-arrows-hidden');
      }
    });
    await check('vertical touch on preview scrolls the feed, never an inner card', async () => {
      const card = home.locator('[data-home-post="41"]'), track = card.locator('[data-home-track]');
      await track.focus(); await page.keyboard.press('Home'); await until(async () => await indexOf(track) === 0, 'first slide');
      const overlay = card.locator('[data-carousel-overlay]').first();
      await overlay.scrollIntoViewIfNeeded(); const before = await page.evaluate(() => scrollY);
      await swipe(page, overlay, 0, -120, [.5, .8]);
      await until(async () => await page.evaluate(() => scrollY) > before + 40, 'touch beginning on copy scrolls feed');
      assert.equal(await overlay.evaluate(el => el.scrollTop), 0);
      assert.equal(await indexOf(track), 0, 'vertical gesture does not change the slide');
    });
    if (viewport.width < 600) await check('service Home native diagonal touch and momentum settle without mouse capture or JS correction', async () => {
      const card = home.locator('[data-home-post="41"]'), track = card.locator('[data-home-track]');
      await track.focus(); await page.keyboard.press('Home'); await until(async () => await indexOf(track) === 0, 'touch reset');
      assert.equal(await track.evaluate(el => getComputedStyle(el).touchAction), 'auto');
      await swipe(page, track, -220, -25, [.8, .45]);
      await until(async () => await indexOf(track) >= 1, 'horizontal diagonal settles natively');
      const settled = await indexOf(track); await page.waitForTimeout(500);
      assert.equal(await indexOf(track), settled, 'no delayed slide rollback');
      assert.equal(await track.getAttribute('data-dragging'), null);
      await track.scrollIntoViewIfNeeded(); const before = await page.evaluate(() => scrollY);
      await swipe(page, track, -20, -140, [.5, .7]);
      await until(async () => await page.evaluate(() => scrollY) > before + 40, 'vertical diagonal scrolls the feed');
      assert.equal(await indexOf(track), settled); assert.equal(await track.getAttribute('data-dragging'), null);
    });
    await check('Home → Detail → Back/HOME: body, sources, AI, reactions, focus and positions', async () => {
      const card = home.locator('[data-home-post="41"]'), track = card.locator('[data-home-track]');
      await track.focus(); await page.keyboard.press('End'); await until(async () => await indexOf(track) === 9, '10th slide');
      const open = card.locator('[data-home-detail]'); await open.focus(); await open.scrollIntoViewIfNeeded();
      const before = await page.evaluate(() => scrollY);
      await open.click(); const detail = page.locator('main[data-detail-state="ready"]'); await detail.waitFor();
      await until(() => detail.locator('h1').evaluate(el => el === document.activeElement), 'Detail heading focus');
      assert.equal(await detail.locator('[data-content-cover]').count(), 1);
      assert.equal(await detail.locator('[data-detail-track]').count(), 1);
      assert.equal(await detail.locator('[data-carousel-slide]').count(), items[0].slides.length);
      const detailTrack = detail.locator('[data-detail-track]');
      if (viewport.width < 600) {
        await swipe(page, detailTrack, -220, 0, [.8, .3]);
        await until(async () => await indexOf(detailTrack) === 1, 'Detail touch advances');
        await swipe(page, detailTrack, 220, 0, [.2, .3]);
        await until(async () => await indexOf(detailTrack) === 0, 'Detail touch returns');
      }
      await detailTrack.scrollIntoViewIfNeeded();
      const dragBox = await detailTrack.boundingBox(), startX = dragBox.x + dragBox.width * .8, startY = dragBox.y + dragBox.height * .3;
      await page.mouse.move(startX, startY); await page.mouse.down();
      await page.mouse.move(startX - dragBox.width * .7, startY, { steps: 12 }); await page.mouse.up();
      await until(async () => await indexOf(detailTrack) === 1, 'Detail mouse drag advances');
      await detailTrack.focus(); await page.keyboard.press('End');
      await until(async () => await indexOf(detailTrack) === items[0].slides.length - 1, 'Detail reaches actual final slide');
      await page.keyboard.press('Home'); await until(async () => await indexOf(detailTrack) === 0, 'Detail returns to first slide');
      await page.evaluate(() => document.fonts.ready);
      const typography = await detail.evaluate(el => {
        const cover = el.querySelector('[data-carousel-slide="1"]');
        const paragraphs = el.querySelectorAll('article > div:last-child > section:first-child > p');
        const measure = node => {
          const css = getComputedStyle(node), rect = node.getBoundingClientRect();
          return { family: css.fontFamily, size: css.fontSize, weight: css.fontWeight, lineHeight: css.lineHeight,
            spacing: css.letterSpacing, width: rect.width, height: rect.height, textSizeAdjust: css.webkitTextSizeAdjust };
        };
        return { title: measure(cover.querySelector('h3')), description: measure(cover.querySelector('figcaption div > p')),
          body: measure(paragraphs[0]), paragraphGap: paragraphs[1].getBoundingClientRect().top - paragraphs[0].getBoundingClientRect().bottom,
          fontLoaded: document.fonts.check('14px PivotPretendard', '본문 글꼴') };
      });
      for (const [key, size, height, weight] of [['title', '18px', '26px', '600'], ['description', '14px', '20px', '400'], ['body', '16px', '24px', '400']]) {
        assert.equal(typography[key].size, size); assert.equal(typography[key].lineHeight, height); assert.equal(typography[key].weight, weight);
        assert.match(typography[key].family, /PivotPretendard/); assert.ok(['normal', '0px'].includes(typography[key].spacing));
      }
      assert.equal(typography.fontLoaded, true); assert.equal(typography.paragraphGap, 20);
      assert.equal(typography.body.width, Math.min(viewport.width, 430) - 64);
      assert.equal(typography.body.height % 24, 0);
      const cdp = await context.newCDPSession(page); await cdp.send('DOM.enable'); await cdp.send('CSS.enable');
      const { root: dom } = await cdp.send('DOM.getDocument');
      const { nodeId } = await cdp.send('DOM.querySelector', { nodeId: dom.nodeId,
        selector: 'main[data-detail-state="ready"] article > div:last-child > section:first-child > p' });
      const { fonts } = await cdp.send('CSS.getPlatformFontsForNode', { nodeId }); await cdp.detach();
      assert.ok(fonts.length > 0 && fonts.every(font => font.isCustomFont && /Pretendard/.test(font.familyName)), 'actual body glyphs use bundled Pretendard');
      geometry.push({ viewport, label: 'detail-typography', ...typography, fonts });
      for (const paragraph of items[0].body.split(/\n\s*\n/)) assert.ok((await detail.innerText()).includes(paragraph));
      const publication = detail.locator('[data-detail-publication]');
      assert.ok((await publication.innerText()).includes('AI가 생성한 콘텐츠입니다.'));
      assert.equal(await publication.locator('a').count(), new Set(originals[0].sources.map(s => s.url)).size);
      await capture('detail-top');
      const like = detail.getByRole('button', { name: '좋아요', exact: true }), save = detail.getByRole('button', { name: '저장', exact: true });
      await like.click(); await until(async () => await like.getAttribute('aria-pressed') === 'true', 'like reflected');
      await save.click(); await until(async () => await save.getAttribute('aria-pressed') === 'true', 'save reflected');
      const more = detail.getByRole('button', { name: '더 볼래요', exact: true });
      await more.click(); await until(async () => await more.getAttribute('aria-pressed') === 'true', 'positive preference');
      const stop = detail.getByRole('button', { name: '그만 볼래요', exact: true });
      await stop.click(); await until(async () => await stop.getAttribute('aria-pressed') === 'true', 'negative preference');
      await stop.click(); await until(async () => await stop.getAttribute('aria-pressed') === 'false', 'clear preference');
      await page.evaluate(() => scrollTo(0, document.documentElement.scrollHeight));
      assert.ok((await more.boundingBox()).y + (await more.boundingBox()).height < viewport.height - 60);
      await capture('detail-bottom');
      await detail.getByRole('button', { name: '뒤로가기', exact: true }).click(); await home.waitFor();
      await until(() => open.evaluate(el => el === document.activeElement), 'return card focus');
      assert.equal(await indexOf(track), 9); assert.ok(Math.abs(await page.evaluate(() => scrollY) - before) < 2);
      assert.equal(await card.getByRole('button', { name: '좋아요', exact: true }).getAttribute('aria-pressed'), 'true');
      await page.goForward(); await detail.waitFor();
      await detail.getByRole('button', { name: 'HOME', exact: true }).click(); await home.waitFor();
      await until(async () => await indexOf(track) === 9, 'HOME restores slide');
    });
    await check('Saved → Detail → Back/SAVE restores saved card focus', async () => {
      await home.locator('[data-saved-navigation]').click();
      const savedScreen = page.locator('[data-saved-state="ready"]'); await savedScreen.waitFor();
      const card = savedScreen.locator('[data-saved-content="41"]'); await card.click();
      const detail = page.locator('[data-detail-state="ready"]'); await detail.waitFor();
      await detail.getByRole('button', { name: '뒤로가기', exact: true }).click(); await savedScreen.waitFor();
      await until(() => card.evaluate(el => el === document.activeElement), 'saved focus restored');
      await card.click(); await detail.waitFor(); await detail.getByRole('button', { name: 'SAVE', exact: true }).click(); await savedScreen.waitFor();
    });
    for (const item of items) await check('loading preview: layout-' + (item.id - 41), async () => {
      imageLoading = item.id; imageFailure = null;
      try {
        await page.goto(base + '/pivot/home'); await home.waitFor();
        const card = home.locator('[data-home-post="' + item.id + '"]'); await card.scrollIntoViewIfNeeded();
        const first = card.locator('[data-carousel-slide="1"]');
        await first.locator('[data-carousel-image-state="loading"]').waitFor();
        await inspectPreview(card, first, 'image-loading-' + item.id);
        const status = await first.locator('[role="status"]').boundingBox(), preview = await first.locator('[data-carousel-overlay]').boundingBox();
        assert.ok(status.y + status.height <= preview.y, 'loading status and preview are separated');
      } finally { imageLoading = null; }
    });
    for (const item of items) await check(`image decode failure and retry: layout-${item.id - 41}`, async () => {
      imageFailure = item.id; await page.goto(base + '/pivot/home'); await home.waitFor();
      const card = home.locator(`[data-home-post="${item.id}"]`); await card.scrollIntoViewIfNeeded();
      const first = card.locator('[data-carousel-slide="1"]'); await first.locator('[data-carousel-image-state="error"]').waitFor();
      await inspectPreview(card, first, 'image-error-' + item.id);
      const cardBox = await card.boundingBox();
      assert.ok(Math.abs(cardBox.height - cardBox.width * 408 / 327) < 1, 'failure keeps card size');
      const retry = first.getByRole('button', { name: '이미지 다시 시도', exact: true });
      assert.ok(await hit(retry), 'image retry center must not be covered by card copy');
      const overlay = await first.locator('[data-carousel-overlay]').boundingBox();
      for (const element of [first.locator('[data-carousel-image-state="error"] [role="status"]'), retry]) {
        const r = await element.boundingBox();
        assert.ok(r.x + r.width <= overlay.x || overlay.x + overlay.width <= r.x || r.y + r.height <= overlay.y || overlay.y + overlay.height <= r.y,
          'image error message and retry must not overlap readable copy');
      }
      imageFailure = null; await retry.click(); await first.locator('img[data-carousel-image-state="ready"]').waitFor();
      await inspectPreview(card, first, 'image-recovered-' + item.id);
    });
    await check('longest Agent article: complete paragraphs, no fixed footer obstruction', async () => {
      imageFailure = null; await page.goto(base + '/pivot/home'); await home.waitFor();
      await home.locator('[data-home-post="42"] [data-home-detail]').click();
      const detail = page.locator('[data-detail-state="ready"]'); await detail.waitFor();
      for (const paragraph of items[1].body.split(/\n\s*\n/)) assert.ok((await detail.innerText()).includes(paragraph));
      assert.equal(await detail.locator('[data-carousel-slide]').count(), 3, 'short server carousel is not padded to five');
      const track = detail.locator('[data-detail-track]'); await track.focus(); await page.keyboard.press('End');
      await until(async () => await indexOf(track) === 2, 'three-slide detail ends at page three');
      await page.evaluate(() => scrollTo(0, document.documentElement.scrollHeight));
      await capture('longest-detail-bottom');
      assert.ok(await hit(detail.getByRole('button', { name: '더 볼래요', exact: true })));
    });
    await check('Detail cover failure: readable copy, separate retry, recovered cover', async () => {
      imageFailure = 42; await page.reload();
      const detail = page.locator('[data-detail-state="ready"]'); await detail.waitFor();
      const cover = detail.locator('[data-content-cover] [data-carousel-slide="1"]');
      await cover.scrollIntoViewIfNeeded(); // Protected media loads only near the viewport.
      await cover.locator('[data-carousel-image-state="error"]').waitFor();
      const retry = cover.getByRole('button', { name: '이미지 다시 시도', exact: true });
      assert.ok(await hit(retry));
      const copy = cover.locator('[data-carousel-overlay]'), r = await retry.boundingBox(), c = await copy.boundingBox();
      assert.ok(r.y + r.height <= c.y, 'Detail error row is above copy');
      assert.equal(await copy.evaluate(el => { el.scrollTop = 100; return el.scrollTop; }), 0, 'failure copy is not an inner scroller');
      const below = await detail.locator('article > div:last-child').textContent();
      for (const text of items[1].slides[0].bodySentences) assert.ok(below.includes(text), 'failure originals remain below the image');
      await capture('detail-error'); imageFailure = null; await retry.click();
      await cover.locator('img[data-carousel-image-state="ready"]').waitFor();
      await capture('detail-recovered');
    });
    for (const mode of ['full_body', 'link_excerpt']) await check(mode + ': all missing originals, exact deduplication, no empty duplicates', async () => {
      detailMode = mode; imageFailure = null; await page.goto(base + '/pivot/home'); await home.waitFor();
      await home.locator('[data-home-post="41"] [data-home-detail]').click();
      const detail = page.locator('[data-detail-state="ready"]'); await detail.waitFor();
      const extra = detail.locator('[data-detail-slide-text]'); await extra.waitFor();
      const normalize = text => text.replace(/\s+/gu, ' ').trim();
      const visibleArticle = mode === 'full_body' ? items[0].body : items[0].summary;
      const baseKeys = new Set(visibleArticle.split(/\n\s*\n/).map(normalize));
      const cover = items[0].slides[0];
      const coverFits = await detail.locator('[data-carousel-slide="1"] figcaption').evaluate(el => {
        const image = el.parentElement.querySelector('img');
        return image?.complete && image.naturalWidth > 0 && el.scrollHeight <= el.clientHeight && el.scrollWidth <= el.clientWidth;
      });
      if (coverFits) for (const text of [...cover.titleLines, cover.titleLines.join(' '), ...cover.bodySentences]) baseKeys.add(normalize(text));
      const paragraphs = await extra.locator('section > p:not(:first-child)').allTextContents();
      const keys = paragraphs.map(normalize);
      assert.equal(new Set(keys).size, keys.length, 'later slides never repeat a sentence');
      for (const key of keys) assert.equal(baseKeys.has(key), false, 'existing article/cover text is not added again');
      const all = normalize(await detail.innerText());
      for (const slide of items[0].slides) for (const text of [...slide.titleLines, ...slide.bodySentences]) assert.ok(all.includes(normalize(text)), 'every original remains accessible');
      assert.equal(await detail.getByText('원문에서 계속 읽기', { exact: true }).count(), mode === 'link_excerpt' ? 1 : 0);
      assert.equal(await detail.locator('[data-detail-track]').count(), 1);
      await extra.scrollIntoViewIfNeeded(); await capture('detail-' + mode + '-extra');
      assert.ok(await extra.evaluate(el => !['auto', 'scroll', 'hidden'].includes(getComputedStyle(el).overflowY) && el.scrollHeight <= el.clientHeight + 1));
      await detail.getByRole('button', { name: '뒤로가기', exact: true }).click(); await home.waitFor();
    });
    detailMode = 'full_body';
    // Independent Detail fixtures exercise first-slide copy absent from article.body.
    // These strings never replace the Home feed or any server original.
    const normalize = text => text.replace(/\s+/gu, ' ').trim();
    const firstOriginal = '  첫 슬라이드에서만 읽을 수 있는 긴\t원문입니다.\n'.repeat(15) + '마지막 원문까지 보존합니다!';
    const mobileSlides = items[0].slides.slice(0, 3).map((slide, index) => ({ ...slide,
      titleLines: [index === 0 ? '겹치지 않아야 하는 긴 제목 '.repeat(16) : `${index + 1}번 독립 제목`],
      bodySentences: index === 0 ? [firstOriginal] : index === 1
        ? [normalize(firstOriginal), '둘째 슬라이드에만 있는 원문입니다.', '공통 원문입니다.']
        : ['공통 원문입니다.', '셋째 슬라이드의 마지막 원문입니다.'],
      layout: { ...slide.layout, textOverlayBox: fullBox },
    }));
    const enterDetail = async () => {
      await page.goto(base + '/pivot/home'); await home.waitFor();
      await home.locator('[data-home-post="41"] [data-home-detail]').click();
      const detail = page.locator('[data-detail-state="ready"]'); await detail.waitFor();
      return detail;
    };
    const assertOriginals = async (detail, slides) => {
      const below = detail.locator('article > div:last-child');
      const text = normalize(await below.textContent());
      for (const slide of slides) for (const original of [...slide.titleLines, ...slide.bodySentences])
        assert.ok(text.includes(normalize(original)), 'complete originals are readable below, not merely present in clipped DOM');
      const paragraphs = await detail.locator('[data-detail-slide-text] section > p:not(:first-child)').allTextContents();
      assert.equal(new Set(paragraphs.map(normalize)).size, paragraphs.length, 'transcript has no duplicate sentences');
      assert.equal(paragraphs.filter(p => normalize(p) === normalize(firstOriginal)).length, 1);
      assert.ok(paragraphs.includes(firstOriginal), 'display keeps the first original whitespace unchanged');
      const sizes = await detail.locator('[data-detail-slide-text] section > p:not(:first-child)').evaluateAll(nodes => nodes.map(el => {
        const css = getComputedStyle(el); return [css.fontSize, css.lineHeight, css.fontWeight, css.fontFamily];
      }));
      for (const [size, line, weight, font] of sizes) { assert.equal(size, '16px'); assert.equal(line, '24px'); assert.equal(weight, '400'); assert.match(font, /PivotPretendard/); }
    };
    const assertDetailGeometry = async (detail, label) => {
      const result = await detail.locator('[data-carousel-slide][aria-hidden="false"] figcaption').evaluate(el => {
        const r = el.getBoundingClientRect(), stage = el.parentElement.getBoundingClientRect();
        const rect = node => node.getBoundingClientRect().toJSON();
        const buttons = [...el.closest('[data-content-carousel]').querySelectorAll('button')].filter(node => !node.closest('[inert]'));
        const overlaps = node => { const b = node.getBoundingClientRect(); return r.left < b.right && r.right > b.left && r.top < b.bottom && r.bottom > b.top; };
        const moving = [el, ...el.querySelectorAll('*')].some(node => { node.scrollTop = 100; const moved = node.scrollTop !== 0; node.scrollTop = 0; return moved; });
        return { rect: r.toJSON(), stage: stage.toJSON(), overflow: getComputedStyle(el).overflowY, moving,
          overlaps: buttons.filter(overlaps).map(node => node.getAttribute('aria-label') || node.textContent),
          controls: buttons.map(rect), scrollHeight: el.scrollHeight, clientHeight: el.clientHeight };
      });
      geometry.push({ viewport, label, ...result });
      assert.equal(result.overflow, 'clip'); assert.equal(result.moving, false); assert.deepEqual(result.overlaps, []);
      assert.ok(Math.abs(result.stage.height - result.stage.width * 408 / 327) < 1);
      assert.ok(result.rect.bottom <= result.stage.bottom + 1 && result.rect.top >= result.stage.top);
    };
    for (const mode of ['full_body', 'link_excerpt']) await check(`Detail mobile ${mode}: overflow, 1→2→3, all originals and 16/24 transcript`, async () => {
      detailSlides = mobileSlides; detailMode = mode; imageFailure = null;
      const detail = await enterDetail(), track = detail.locator('[data-detail-track]');
      await detail.locator('[data-carousel-slide="1"] img[data-carousel-image-state="ready"]').waitFor();
      await assertOriginals(detail, mobileSlides);
      for (let index = 0; index < 3; index++) {
        if (index) {
          if (viewport.width < 600) await swipe(page, track, -220, 0, [.8, .4]);
          else { await track.focus(); await page.keyboard.press('ArrowRight'); }
          await until(async () => await indexOf(track) === index, 'Detail 1→2→3 native navigation');
        }
        await detail.locator(`[data-carousel-slide="${index + 1}"] img[data-carousel-image-state="ready"]`).waitFor();
        await assertDetailGeometry(detail, `detail-mobile-${mode}-${index + 1}`);
        await capture(`detail-mobile-${mode}-${index + 1}`);
      }
      await track.focus(); await page.keyboard.press('Home'); await until(async () => await indexOf(track) === 0, 'Detail keyboard reset');
      await page.keyboard.press('ArrowRight'); await until(async () => await indexOf(track) === 1, 'Detail keyboard next');
      await page.keyboard.press('ArrowRight'); await until(async () => await indexOf(track) === 2, 'Detail keyboard next twice');
      if (viewport.width < 600) {
        const overlay = detail.locator('[data-carousel-slide="3"] figcaption');
        await overlay.scrollIntoViewIfNeeded(); const before = await page.evaluate(() => scrollY);
        await swipe(page, overlay, 0, -100, [.5, .8]);
        await until(async () => await page.evaluate(() => scrollY) > before + 20, 'Detail copy gesture scrolls the article');
        assert.equal(await overlay.evaluate(el => el.scrollTop), 0);
      }
      const transcript = detail.locator('[data-detail-slide-text]'); await transcript.scrollIntoViewIfNeeded();
      await assertOriginals(detail, mobileSlides); await capture(`detail-mobile-${mode}-originals`);
    });
    await check('Detail mobile: loading, HTTP 404, retry and recovered originals never overlap controls', async () => {
      detailSlides = mobileSlides; detailMode = 'full_body'; imageLoading = 41; imageFailure = null;
      try {
        const detail = await enterDetail();
        const cover = detail.locator('[data-carousel-slide="1"]');
        await cover.locator('[data-carousel-image-state="loading"]').waitFor();
        await assertOriginals(detail, mobileSlides); await assertDetailGeometry(detail, 'detail-mobile-loading');
        const status = await cover.locator('[role="status"]').boundingBox(), overlay = await cover.locator('figcaption').boundingBox();
        assert.ok(status.y + status.height <= overlay.y); await capture('detail-mobile-loading');
        imageLoading = null; await cover.locator('img[data-carousel-image-state="ready"]').waitFor();
        imageFailure = 41; imageFailureStatus = 404; await page.reload(); await detail.waitFor();
        await cover.locator('[data-carousel-image-state="error"]').waitFor();
        await assertOriginals(detail, mobileSlides); await assertDetailGeometry(detail, 'detail-mobile-404');
        const retry = cover.getByRole('button', { name: '이미지 다시 시도', exact: true });
        assert.ok(await hit(retry)); await capture('detail-mobile-404');
        imageFailure = null; await retry.click(); await cover.locator('img[data-carousel-image-state="ready"]').waitFor();
        await assertOriginals(detail, mobileSlides); await assertDetailGeometry(detail, 'detail-mobile-retry');
        await capture('detail-mobile-retry');
      } finally { imageLoading = null; imageFailure = null; imageFailureStatus = 200; }
    });
    await check('Detail mobile: fitting first copy deduplicates only after measurement and reappears after resize', async () => {
      const title = '크기 변경 검증', copy = '짧은 원문도 화면 크기에 따라 줄 수가 바뀝니다. 이 문장 전체를 읽을 수 있는 경우에만 아래 원문에서 제외합니다.';
      detailSlides = mobileSlides.map((slide, index) => index ? slide : { ...slide, titleLines: [title], bodySentences: [copy],
        layout: { ...slide.layout, textOverlayBox: { x: .06, y: .5, width: .88, height: .45 } } });
      const detail = await enterDetail(), firstText = detail.locator('[data-detail-slide-page="1"]');
      await detail.locator('[data-carousel-slide="1"] img[data-carousel-image-state="ready"]').waitFor();
      await until(async () => await firstText.count() === 0, 'fully fitting first caption avoids transcript duplication');
      await page.setViewportSize({ width: 240, height: 812 });
      await firstText.waitFor(); assert.ok((await firstText.textContent()).includes(copy));
      await page.setViewportSize(viewport);
      await until(async () => await firstText.count() === 0, 'resize back rechecks fit and removes duplicate');
      await capture('detail-mobile-fitting');
    });
    detailSlides = null; detailMode = 'full_body';
    await check('fixed AI: the gutter keeps controls clickable and the notice is preserved', async () => {
      imageFailure = null; detailMode = 'full_body'; await page.goto(base + '/pivot/home'); await home.waitFor();
      const ai = home.getByRole('button', { name: 'AI 콘텐츠 탐색', exact: true });
      const card = home.locator('[data-home-post="42"]');
      await card.evaluate(el => el.scrollIntoView({ block: 'end', behavior: 'instant' }));
      assert.deepEqual(await aiObstructions(ai), []);
      for (const button of [card.getByRole('button', { name: '좋아요', exact: true }), card.getByRole('button', { name: '저장', exact: true }), card.locator('[data-home-detail]'), ai]) assert.ok(await hit(button));
      const size = await ai.boundingBox(); assert.equal(size.width, 48); assert.equal(size.height, 48);
      const probes = await ai.evaluate(el => {
        const b = el.getBoundingClientRect(), img = el.querySelector('img');
        const hit = (x, y) => el.contains(document.elementFromPoint(x, y));
        return { face: [[1, 24], [47, 24], [24, 1], [24, 47]].map(([x, y]) => hit(b.x + x, b.y + y)),
          shadow: hit(b.x - 4, b.y + 24), imageWidth: img.naturalWidth, events: getComputedStyle(img).pointerEvents };
      });
      assert.ok(probes.face.every(Boolean)); assert.equal(probes.shadow, false);
      assert.equal(probes.imageWidth, 64); assert.equal(probes.events, 'none');
      const cardBox = await card.boundingBox();
      assert.ok(Math.abs(size.x - cardBox.x - cardBox.width - 8) < 1, 'AI sits outside the feed');
      assert.ok(Math.abs(size.y - (viewport.height - 12 - 48)) < 1, 'AI aligns with the NAV height');
      await capture('fixed-ai-gutter');
      // The existing informational button has aria-disabled=true, so locator.click waits for enabled.
      // Use real input at the verified hit target; keep the visible notice assertion.
      const point = await ai.boundingBox();
      if (viewport.width < 600) await page.touchscreen.tap(point.x + point.width / 2, point.y + point.height / 2);
      else await page.mouse.click(point.x + point.width / 2, point.y + point.height / 2);
      await home.getByText('AI 콘텐츠 탐색은 준비 중이에요.', { exact: true }).waitFor();
      const like = card.getByRole('button', { name: '좋아요', exact: true }), save = card.getByRole('button', { name: '저장', exact: true });
      const likedBefore = await like.getAttribute('aria-pressed'), savedBefore = await save.getAttribute('aria-pressed');
      await like.click(); await until(async () => await like.getAttribute('aria-pressed') !== likedBefore, 'like still works under the dock');
      await save.click(); await until(async () => await save.getAttribute('aria-pressed') !== savedBefore, 'save still works under the dock');
      await card.locator('[data-home-detail]').click();
      const detail = page.locator('[data-detail-state="ready"]'); await detail.waitFor();
      await detail.getByRole('button', { name: '뒤로가기', exact: true }).click(); await home.waitFor();
      assert.deepEqual(await ai.boundingBox(), size, 'Detail return retains the fixed AI coordinates');
      assert.ok(await hit(ai));
    });
    await check('fast vertical scroll and direction changes keep the same AI coordinates', async () => {
      const card = home.locator('[data-home-post="42"]'), ai = home.locator('[data-home-ai]');
      await card.evaluate(el => el.scrollIntoView({ block: 'end', behavior: 'instant' }));
      const positions = await page.evaluate(async () => {
        const ai = document.querySelector('[data-home-ai]'), origin = scrollY, result = [];
        for (const y of [origin - 2, origin + 2, 0, 160, 400, 650, 320, 120, 0, document.documentElement.scrollHeight]) {
          scrollTo(0, y);
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          const { x, y: top } = ai.getBoundingClientRect();
          const css = getComputedStyle(ai);
          result.push({ x, top, scrollY, opacity: css.opacity, visibility: css.visibility });
        }
        return result;
      });
      assert.ok(new Set(positions.map(item => item.scrollY)).size >= 5);
      assert.equal(new Set(positions.map(({ x, top }) => `${x},${top}`)).size, 1);
      assert.ok(positions.every(item => item.opacity === '1' && item.visibility === 'visible'));
      assert.deepEqual(await aiObstructions(ai), []);
      assert.ok(await hit(ai));
    });
    await check('desktop and mobile keyboard Home, End and consecutive arrows reach the intended pages', async () => {
      const card = home.locator('[data-home-post="41"]'), track = card.locator('[data-home-track]');
      await track.focus();
      for (const [key, expected] of [['End', 9], ['Home', 0], ['ArrowRight', 1], ['ArrowRight', 2], ['ArrowLeft', 1], ['Home', 0]]) {
        await page.keyboard.press(key); await until(async () => await indexOf(track) === expected, 'keyboard ' + key + ' reaches ' + expected);
      }
    });
    assert.deepEqual(external, []); assert.deepEqual(unexpected, []); assert.deepEqual(errors, []);
  } catch (error) { failures.push({ name: 'setup/network', error: error.stack }); }
  finally {
    results.push({ viewport, checks, failures, external, unexpected, errors, requestCount: calls.length });
    await context.close();
  }
}
(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: process.env.PIVOT_BROWSER_CHANNEL || 'msedge' });
  try { for (const viewport of [{ width: 375, height: 812 }, { width: 390, height: 844 }, { width: 1440, height: 1000 }]) await run(browser, viewport); }
  finally { await browser.close(); fs.writeFileSync(artifact('results.json'), JSON.stringify({
    geometry, fixtures: items.map(item => ({ id: item.id, agentId: item.original.id, title: item.title, bodyChars: item.body.length })), results }, null, 2)); }
  console.log(JSON.stringify(results.map(({ viewport, checks, failures }) => ({ viewport, passed: checks.length, failures })), null, 2));
  if (results.some(result => result.failures.length)) process.exitCode = 1;
})().catch(error => { console.error(error); process.exitCode = 1; });
