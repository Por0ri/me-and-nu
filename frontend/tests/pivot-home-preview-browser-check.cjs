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
const exported = JSON.parse(fs.readFileSync(path.join(root, '../agent/contents/contents.json'), 'utf8'));
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
async function hit(locator) {
  await locator.scrollIntoViewIfNeeded();
  // Wait for the scroll and collision-avoidance paint, without changing the hit assertion.
  await locator.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const result = await locator.evaluate(el => { const r = el.getBoundingClientRect(); const found = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
    return { hit: el.contains(found), label: el.getAttribute('aria-label'), rect: r.toJSON(), found: found?.outerHTML.slice(0, 250) }; });
  if (!result.hit) console.error('HIT DIAGNOSTIC', result);
  return result.hit;
}
// Measure the visible image/button footprint independently of the positioning helper.
async function aiObstructions(ai) {
  return ai.evaluate(el => {
    const button = el.getBoundingClientRect(), image = el.querySelector('img').getBoundingClientRect();
    const anchor = el.parentElement.getBoundingClientRect();
    const actual = { left: Math.min(button.left, image.left), right: Math.max(button.right, image.right),
      top: Math.min(button.top, image.top), bottom: Math.max(button.bottom, image.bottom) };
    const original = { left: actual.left + anchor.left - button.left, right: actual.right + anchor.left - button.left,
      top: actual.top + anchor.top - button.top, bottom: actual.bottom + anchor.top - button.top };
    const overlaps = (a, b) => a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
    const controls = [...document.querySelectorAll('[data-home-post] button')].filter(control => {
      const rect = control.getBoundingClientRect(), css = getComputedStyle(control);
      return !control.closest('[inert]') && css.visibility !== 'hidden' && Number(css.opacity) > 0 && rect.width && rect.height &&
        rect.bottom > 0 && rect.top < innerHeight && overlaps(rect, control.closest('[data-home-post]').getBoundingClientRect());
    });
    const blockers = box => controls.filter(control => overlaps(box, control.getBoundingClientRect()))
      .map(control => control.getAttribute('aria-label') || control.textContent);
    return { original: blockers(original), actual: blockers(actual) };
  });
}
async function run(browser, viewport) {
  const context = await browser.newContext({ viewport, isMobile: viewport.width < 600, hasTouch: true,
    reducedMotion: 'reduce', serviceWorkers: 'block' });
  const page = await context.newPage(); page.setDefaultTimeout(12000); page.setDefaultNavigationTimeout(60000);
  const calls = [], external = [], unexpected = [], errors = [], checks = [], failures = [];
  const saved = new Set(), liked = new Set(), preferences = new Map();
  let imageFailure = null, imageLoading = null, detailMode = "full_body";
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
        if (imageFailure === id) return route.fulfill({ status: 200, contentType: 'image/png', body: 'invalid-png' });
        return route.fulfill({ status: 200, contentType: 'image/png', body: png, headers: { 'cache-control': 'private, no-store' } });
      }
      if (item && !action) return reply({ ...publication(item), contentId: id, topicId: 7, displayMode: detailMode, excerpt: item.summary,
        body: item.body, contentType: 'article', imageUrl: item.slides[0].imageUrl, slides: item.slides,
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
        return { rect: b.toJSON(), lineHeight: parseFloat(css.lineHeight), font: parseFloat(css.fontSize), clamp: css.webkitLineClamp }; };
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
    assert.equal(geometryData.title.clamp, '3');
    assert.ok(geometryData.title.rect.height <= 3 * 26 + 1 && geometryData.title.rect.height >= 26);
    assert.ok(Math.abs(geometryData.title.rect.height / 26 - Math.round(geometryData.title.rect.height / 26)) < .03, 'title ends at a complete line');
    if (geometryData.copy) {
      assert.equal(geometryData.copy.font, 14); assert.equal(geometryData.copy.lineHeight, 20); assert.equal(geometryData.copy.clamp, '2');
      assert.ok(geometryData.copy.rect.height <= 40 + 1);
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
        assert.ok(await hit(previous), 'previous button center receives input over server overlay');
        await previous.click(); await until(async () => await indexOf(track) === 0, 'previous returns first slide');
      });
    }
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
    await check('Home → Detail → Back/HOME: body, sources, AI, reactions, focus and positions', async () => {
      const card = home.locator('[data-home-post="41"]'), track = card.locator('[data-home-track]');
      await track.focus(); await page.keyboard.press('End'); await until(async () => await indexOf(track) === 9, '10th slide');
      const open = card.locator('[data-home-detail]'); await open.focus(); await open.scrollIntoViewIfNeeded();
      const before = await page.evaluate(() => scrollY);
      await open.click(); const detail = page.locator('main[data-detail-state="ready"]'); await detail.waitFor();
      await until(() => detail.locator('h1').evaluate(el => el === document.activeElement), 'Detail heading focus');
      assert.equal(await detail.locator('[data-content-cover]').count(), 1);
      assert.equal(await detail.locator('[data-carousel-track]').count(), 0, 'Detail stays single cover');
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
      await page.evaluate(() => scrollTo(0, document.documentElement.scrollHeight));
      await capture('longest-detail-bottom');
      assert.ok(await hit(detail.getByRole('button', { name: '더 볼래요', exact: true })));
    });
    await check('Detail cover failure: readable copy, separate retry, recovered cover', async () => {
      imageFailure = 42; await page.reload();
      const detail = page.locator('[data-detail-state="ready"]'); await detail.waitFor();
      const cover = detail.locator('[data-content-cover]');
      await cover.scrollIntoViewIfNeeded(); // Protected media loads only near the viewport.
      await cover.locator('[data-carousel-image-state="error"]').waitFor();
      const retry = cover.getByRole('button', { name: '이미지 다시 시도', exact: true });
      assert.ok(await hit(retry));
      const copy = cover.locator('[data-carousel-overlay]'), r = await retry.boundingBox(), c = await copy.boundingBox();
      assert.ok(r.y + r.height <= c.y, 'Detail error row is above copy');
      await copy.focus(); await page.keyboard.press('End');
      await until(() => copy.evaluate(el => el.scrollTop >= el.scrollHeight - el.clientHeight - 2), 'failure copy can be read to end');
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
      for (const text of [...cover.titleLines, cover.titleLines.join(' '), ...cover.bodySentences]) baseKeys.add(normalize(text));
      const paragraphs = await extra.locator('section > p:not(:first-child)').allTextContents();
      const keys = paragraphs.map(normalize);
      assert.equal(new Set(keys).size, keys.length, 'later slides never repeat a sentence');
      for (const key of keys) assert.equal(baseKeys.has(key), false, 'existing article/cover text is not added again');
      const all = normalize(await detail.innerText());
      for (const slide of items[0].slides) for (const text of [...slide.titleLines, ...slide.bodySentences]) assert.ok(all.includes(normalize(text)), 'every original remains accessible');
      assert.equal(await detail.getByText('원문에서 계속 읽기', { exact: true }).count(), mode === 'link_excerpt' ? 1 : 0);
      assert.equal(await detail.locator('[data-carousel-track]').count(), 0);
      await extra.scrollIntoViewIfNeeded(); await capture('detail-' + mode + '-extra');
      assert.ok(await extra.evaluate(el => !['auto', 'scroll', 'hidden'].includes(getComputedStyle(el).overflowY) && el.scrollHeight <= el.clientHeight + 1));
      await detail.getByRole('button', { name: '뒤로가기', exact: true }).click(); await home.waitFor();
    });
    detailMode = 'full_body';
    await check('fixed AI: visible controls stay clickable, default geometry and notice are preserved', async () => {
      imageFailure = null; detailMode = 'full_body'; await page.goto(base + '/pivot/home'); await home.waitFor();
      const ai = home.getByRole('button', { name: 'AI 콘텐츠 탐색', exact: true });
      const card = home.locator('[data-home-post="42"]');
      await card.evaluate(el => el.scrollIntoView({ block: 'end', behavior: 'instant' }));
      await until(async () => await ai.getAttribute('data-ai-displaced') === 'true', 'AI moves only while overlapping visible controls');
      for (const button of [card.getByRole('button', { name: '좋아요', exact: true }), card.getByRole('button', { name: '저장', exact: true }), card.locator('[data-home-detail]'), ai]) assert.ok(await hit(button));
      const size = await ai.boundingBox(); assert.equal(size.width, 40); assert.equal(size.height, 40);
      await capture('fixed-nav-obstruction-resolved');
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
      await page.evaluate(() => scrollTo(0, 0));
      assert.ok((await aiObstructions(ai)).original.length > 0, 'at scrollY=0 the next card still occupies the default AI position');
      await until(async () => await ai.getAttribute('data-ai-displaced') === 'true', 'AI stays displaced while a visible control overlaps its anchor');
      assert.deepEqual((await aiObstructions(ai)).actual, [], 'temporary position clears the visible controls');
      await page.evaluate(() => scrollTo(0, document.documentElement.scrollHeight));
      assert.deepEqual((await aiObstructions(ai)).original, [], 'return is tested only after the actual default-position collision clears');
      await until(async () => await ai.getAttribute('data-ai-displaced') === 'false', 'AI returns after the collision clears and scroll settles');
      assert.deepEqual((await aiObstructions(ai)).actual, []);
      const homeRect = await home.boundingBox(), original = await ai.boundingBox();
      assert.ok(Math.abs(original.x - (homeRect.x + homeRect.width - 34 - 40)) < 1);
      assert.ok(Math.abs(original.y - (viewport.height - 20 - 40)) < 1);
      assert.ok(await hit(ai)); await capture('ai-default-position');
    });
    await check('fast vertical scroll and boundary jitter do not hide or repeatedly move AI', async () => {
      const card = home.locator('[data-home-post="42"]'), ai = home.locator('[data-home-ai]');
      await card.evaluate(el => el.scrollIntoView({ block: 'end', behavior: 'instant' }));
      await until(async () => await ai.getAttribute('data-ai-displaced') === 'true', 'collision shift');
      const positions = await page.evaluate(async () => {
        const ai = document.querySelector('[data-home-ai]'), origin = scrollY, result = [];
        for (let i = 0; i < 18; i++) {
          scrollTo(0, origin + (i % 2 ? 2 : -2));
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          const css = getComputedStyle(ai); result.push({ transform: css.transform, opacity: css.opacity, visibility: css.visibility });
        }
        return result;
      });
      assert.equal(new Set(positions.map(item => item.transform)).size, 1, 'no boundary ping-pong');
      assert.ok(positions.every(item => item.opacity === '1' && item.visibility === 'visible'));
      await page.evaluate(async () => {
        for (const y of [0, 160, 400, 650, 320, 120, 0]) { scrollTo(0, y); await new Promise(resolve => requestAnimationFrame(resolve)); }
      });
      assert.ok((await aiObstructions(ai)).original.length > 0, 'the unchanged rapid-scroll sequence ends at a genuine collision');
      await until(async () => await ai.getAttribute('data-ai-displaced') === 'true', 'rapid scroll preserves avoidance while the anchor is blocked');
      assert.deepEqual((await aiObstructions(ai)).actual, []);
      await page.evaluate(() => scrollTo(0, document.documentElement.scrollHeight));
      assert.deepEqual((await aiObstructions(ai)).original, [], 'clear the collision before requiring the default position');
      await until(async () => await ai.getAttribute('data-ai-displaced') === 'false', 'stable default after rapid scroll');
      assert.deepEqual((await aiObstructions(ai)).actual, []);
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
