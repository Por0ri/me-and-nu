// Isolated, synthetic HTTPS E regression. Never run against an operating service.
// Required: SAFARI_LAB_ACCESS_FILE (URL/Username/Password lines).
// Optional: PIVOT_PLAYWRIGHT_MODULE, SAFARI_LAB_WIDTHS=375,390,1280.
// Usage: node tests/pivot-home-windowing-browser-check.cjs [output-directory]
// This measures DOM/resource lifetime in Windows Chrome, not iPhone Safari memory.
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');

const origin = 'https://safari-check.me-nu.kr';
const host = new URL(origin).hostname;
const expected = Array.from({ length: 123 }, (_, index) => String(1000 - index));
const output = process.argv[2] || path.join(os.tmpdir(), 'pivot-home-windowing-E');
const widths = (process.env.SAFARI_LAB_WIDTHS || '375,390,1280').split(',').map(Number);
assert.ok(widths.length && widths.every(width => [375, 390, 1280].includes(width)) && new Set(widths).size === widths.length);
assert.ok(process.env.SAFARI_LAB_ACCESS_FILE, 'SAFARI_LAB_ACCESS_FILE must name the private test-host access file');
const access = Object.fromEntries(fs.readFileSync(process.env.SAFARI_LAB_ACCESS_FILE, 'utf8').split(/\r?\n/)
  .filter(line => line.includes(': ')).map(line => { const split = line.indexOf(': '); return [line.slice(0, split), line.slice(split + 2)]; }));
assert.ok(access.URL === origin && access.Username === 'safari' && /^[A-Za-z0-9_-]{32,80}$/.test(access.Password || ''), 'Private access file does not match the isolated test host');
const basic = Buffer.from(`${access.Username}:${access.Password}`).toString('base64');
const sensitive = new Set([access.Password, basic, `Basic ${basic}`, `${access.Username}:${access.Password}`]);
function redact(value) {
  if (typeof value === 'string') {
    for (const secret of sensitive) value = value.split(secret).join('[REDACTED]');
    return value.replace(/__Host-lab4tls_session=[^;\s"']+/g, '__Host-lab4tls_session=[REDACTED]');
  }
  if (Array.isArray(value)) return value.map(redact);
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, redact(item)]));
  return value;
}

// Observability only: keep counts and URL strings, never image Blobs or DOM nodes.
// The host's image instrumentation may wrap these functions after this init script.
function installLifetimeMetrics() {
  const live = new Set();
  let created = 0, revoked = 0, peak = 0, sequence = 0, targetSequence = 0;
  const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL);
  URL.createObjectURL = function (...args) {
    const url = create(...args); live.add(url); created += 1; peak = Math.max(peak, live.size); return url;
  };
  URL.revokeObjectURL = function (url) { if (live.delete(url)) revoked += 1; return revoke(url); };
  const targets = new WeakMap(), active = new Map();
  const NativeObserver = window.IntersectionObserver;
  if (NativeObserver) window.IntersectionObserver = class extends NativeObserver {
    constructor(...args) { super(...args); this.lifetimeId = ++sequence; }
    observe(target) {
      super.observe(target);
      if (!targets.has(target)) targets.set(target, ++targetSequence);
      if (!active.has(this.lifetimeId)) active.set(this.lifetimeId, new Set());
      active.get(this.lifetimeId).add(targets.get(target));
    }
    unobserve(target) {
      super.unobserve(target);
      const entries = active.get(this.lifetimeId); entries?.delete(targets.get(target));
      if (!entries?.size) active.delete(this.lifetimeId);
    }
    disconnect() { super.disconnect(); active.delete(this.lifetimeId); }
  };
  window.__windowingLifetime = () => ({ created, revoked, live: live.size, peak,
    activeObservers: active.size, observedTargets: [...active.values()].reduce((sum, entries) => sum + entries.size, 0) });
  window.__windowingCsp = [];
  document.addEventListener('securitypolicyviolation', event => window.__windowingCsp.push({ directive: event.effectiveDirective, uri: event.blockedURI }));
}

const cases = [], network = [];
let completed = false, browserVersion;

async function run(browser, width) {
  const context = await browser.newContext({ viewport: { width, height: width < 600 ? 844 : 1000 },
    isMobile: width < 600, hasTouch: width < 600, reducedMotion: 'reduce', serviceWorkers: 'block',
    ignoreHTTPSErrors: false, httpCredentials: { username: access.Username, password: access.Password, origin } });
  const external = [], errors = [], consoleErrors = [], feeds = [], pendingFeeds = [], checks = [], samples = [], debateFixtures = [];
  let step = 'initial';
  await context.route('**/*', route => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== origin) { external.push(request.url()); return route.abort('blockedbyclient'); }
    // The isolated HOME harness has no debate endpoint. Fulfill only the exact
    // empty live-list contract; the actual Next route and navigation stay real.
    if (url.pathname === '/api/v1/debates') {
      const query = [...url.searchParams.entries()].sort(([left], [right]) => left.localeCompare(right));
      const expectedQuery = [['limit', '50'], ['status', 'open'], ['topicId', '7']];
      if (request.method() !== 'GET' || JSON.stringify(query) !== JSON.stringify(expectedQuery)) {
        errors.push(`Unexpected debate fixture request: ${request.method()} ${url.pathname}${url.search}`);
        return route.abort('blockedbyclient');
      }
      debateFixtures.push({ method: request.method(), path: url.pathname, query: Object.fromEntries(query) });
      return route.fulfill({ status: 200, contentType: 'application/json', headers: { 'Cache-Control': 'no-store' },
        body: JSON.stringify({ items: [], nextCursor: null }) });
    }
    return route.continue();
  });
  await context.addInitScript(installLifetimeMetrics);
  const page = await context.newPage(); page.setDefaultTimeout(20000);
  page.on('pageerror', error => errors.push(redact(error.message)));
  page.on('console', message => { if (message.type() === 'error') consoleErrors.push(redact(message.text())); });
  page.on('request', request => network.push({ width, step, method: request.method(), url: request.url(), type: request.resourceType() }));
  page.on('response', response => {
    const url = new URL(response.url());
    if (url.pathname === '/api/v1/topics/7/feed') pendingFeeds.push((async () => {
      assert.equal(response.status(), 200, 'synthetic feed status');
      const body = await response.json();
      feeds.push({ limit: Number(url.searchParams.get('limit')), cursor: url.searchParams.get('cursor'),
        nextCursor: body.nextCursor, ids: body.sections.flatMap(section => section.contents.map(card => String(card.id))) });
    })().catch(error => errors.push(redact(error.message))));
  });
  const home = page.locator('[data-home-state]');
  const slots = page.locator('[data-home-feed-slot]');
  const card = id => page.locator(`[data-home-post="${id}"]`);
  const slot = id => page.locator(`[data-home-feed-slot="${id}"]`);
  const slotIds = () => slots.evaluateAll(elements => elements.map(element => element.dataset.homeFeedSlot));
  const blur = () => page.evaluate(() => document.activeElement instanceof HTMLElement && document.activeElement.blur());
  const feedRequestCount = () => network.filter(request => request.width === width
    && new URL(request.url).pathname === '/api/v1/topics/7/feed').length;
  async function settleVisible() {
    await page.waitForFunction(() => {
      const root = document.querySelector('[data-home-state]');
      if (!root || root.clientHeight === 0) return false;
      const viewport = root.getBoundingClientRect();
      return [...root.querySelectorAll('[data-home-feed-slot]')].every(element => {
        const box = element.getBoundingClientRect();
        return box.bottom <= viewport.top + 1 || box.top >= viewport.bottom - 1 || !!element.querySelector('[data-home-post]');
      });
    });
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  }
  async function moveTo(id) {
    await slot(id).evaluate(element => {
      const root = element.closest('[data-home-state]');
      root.scrollTo({ top: root.scrollTop + element.getBoundingClientRect().top - root.getBoundingClientRect().top - 110, behavior: 'instant' });
    });
    await settleVisible();
    await card(id).waitFor({ state: 'visible' });
  }
  async function sample(label, expectedCount = 123) {
    await settleVisible();
    const state = await home.evaluate(root => {
      const nodes = [...root.querySelectorAll('[data-home-feed-slot]')];
      const cards = [...root.querySelectorAll('[data-home-post]')];
      return { slots: nodes.length, ids: nodes.map(element => element.dataset.homeFeedSlot),
        cards: cards.length, mountedIds: cards.map(element => element.dataset.homePost),
        slides: root.querySelectorAll('[data-home-post] [data-carousel-slide]').length,
        elements: root.querySelectorAll('*').length, top: root.scrollTop, scrollHeight: root.scrollHeight,
        heights: nodes.map(element => element.getBoundingClientRect().height),
        gaps: nodes.slice(1).map((element, index) => element.getBoundingClientRect().top - nodes[index].getBoundingClientRect().bottom),
        markersAgree: nodes.every(element => (element.dataset.homeFeedMounted === 'true') === !!element.querySelector('[data-home-post]')),
        positionsAgree: nodes.every((element, index) => Number(element.getAttribute('aria-posinset')) === index + 1 && Number(element.getAttribute('aria-setsize')) === nodes.length),
        lifetime: window.__windowingLifetime() };
    });
    assert.equal(state.slots, expectedCount, `${label}: retained slots`);
    assert.deepEqual(state.ids, expected.slice(0, expectedCount), `${label}: retained order`);
    assert.ok(state.cards > 0 && state.cards <= 8, `${label}: mounted cards must stay bounded (${state.cards})`);
    assert.ok(state.slides <= 80 && state.slides === state.cards * 10, `${label}: bounded 10-slide cards`);
    assert.ok(state.elements < 2500, `${label}: HOME DOM stays below 2500 elements (${state.elements})`);
    assert.equal(new Set(state.mountedIds).size, state.cards, `${label}: unique mounted cards`);
    assert.ok(state.markersAgree && state.positionsAgree, `${label}: slot identity/accessibility markers`);
    assert.ok(state.heights.every(height => height > 100 && Math.abs(height - state.heights[0]) < 1), `${label}: empty slots reserve card height`);
    assert.ok(state.gaps.every(gap => Math.abs(gap - 24) < 1), `${label}: no collapsed or overlapping card slots`);
    assert.ok(state.lifetime.live <= state.cards * 3 + 3, `${label}: live image URLs do not accumulate (${state.lifetime.live})`);
    assert.ok(state.lifetime.activeObservers <= state.cards * 12 + 12, `${label}: active observers do not accumulate (${state.lifetime.activeObservers})`);
    const { ids, heights, gaps, ...compact } = state;
    samples.push({ label, ...compact }); return state;
  }
  async function expectSlide(id, index) {
    await page.waitForFunction(({ id, index }) => {
      const track = document.querySelector(`[data-home-post="${id}"] [data-home-track]`);
      const position = document.querySelector(`[data-home-post="${id}"] [data-home-position]`);
      return track?.clientWidth > 0 && Math.abs(track.scrollLeft / track.clientWidth - index) < .02 && position?.textContent.includes(`${index + 1} / 10`);
    }, { id, index });
  }
  async function readyImage(id, pageNumber = 1) {
    await page.waitForFunction(({ id, pageNumber }) => {
      const image = document.querySelector(`[data-home-post="${id}"] [data-carousel-slide="${pageNumber}"] img`);
      return image?.complete && image.naturalWidth === 1308 && image.naturalHeight === 1632;
    }, { id, pageNumber });
  }
  try {
    const response = await page.goto(`${origin}/__lab/start/E?slides=10`, { waitUntil: 'domcontentloaded' });
    assert.equal(response.status(), 200);
    await page.locator('[data-home-state="ready"]').waitFor();
    await page.locator('[data-home-entry-loading]').waitFor({ state: 'detached' });
    await readyImage(expected[0]);
    assert.equal(await page.evaluate(() => window.__LAB_VARIANT__), 'E');
    assert.equal(await page.evaluate(() => window.__labImages.disabled), false);
    const policy = response.headers()['content-security-policy'] || '';
    assert.ok(policy.includes("connect-src 'self'") && policy.includes("img-src 'self' blob: data:"));
    assert.equal(response.headers()['access-control-allow-origin'], undefined);
    const cookie = (await context.cookies(origin)).find(item => item.name === '__Host-lab4tls_session');
    assert.ok(cookie && cookie.secure && cookie.httpOnly && cookie.sameSite === 'Lax' && cookie.path === '/' && cookie.domain === host);
    sensitive.add(cookie.value);
    assert.equal(await page.evaluate(() => isSecureContext && !document.cookie.includes('__Host-lab4tls_session')), true);
    await Promise.all(pendingFeeds);
    assert.deepEqual(feeds.map(feed => ({ limit: feed.limit, cursor: feed.cursor })), [{ limit: 20, cursor: null }], 'only first 20 requested on entry');
    await sample('initial', 20);
    await page.screenshot({ path: path.join(output, `initial-E-${width}.png`) });
    checks.push('trusted HTTPS, isolated E session, first image decoded, one initial 20-card response');

    const firstTrack = card(expected[0]).locator('[data-home-track]');
    await firstTrack.focus(); await firstTrack.press('ArrowRight'); await expectSlide(expected[0], 1);
    await firstTrack.press('ArrowRight'); await expectSlide(expected[0], 2);
    await readyImage(expected[0], 3);
    await blur();
    step = 'pagination';
    while (await page.locator('[data-home-pagination] button').count()) {
      const before = await slots.count();
      await page.locator('[data-home-pagination] button').click();
      await page.waitForFunction(count => document.querySelectorAll('[data-home-feed-slot]').length > count, before);
      await sample(`pagination-${await slots.count()}`, await slots.count());
    }
    await Promise.all(pendingFeeds);
    assert.deepEqual(feeds.flatMap(feed => feed.ids), expected);
    assert.ok(feeds.every(feed => feed.limit === 20));
    assert.equal(feeds.length, 7);
    assert.equal(new Set(feeds.map(feed => feed.cursor)).size, 7);
    assert.deepEqual(await slotIds(), expected);
    await page.waitForFunction(id => !document.querySelector(`[data-home-post="${id}"]`), expected[0]);
    await moveTo(expected[0]); await expectSlide(expected[0], 2);
    checks.push('all 123 IDs retained in order without duplicate pages; offscreen card unmount; third slide survives remount');

    step = 'traversal';
    const visits = width === 375 ? expected : expected.filter((_, index) => index % 20 === 0 || index === 122);
    for (const id of visits) { await moveTo(id); await sample(`visit-${id}`); }
    for (const id of [expected[0], expected[122], expected[15], expected[100], expected[60], expected[0]]) {
      await moveTo(id); await sample(`jump-${id}`);
    }
    // Rapid reversals within frames exercise observer delivery after several scrolls.
    await page.evaluate(async () => {
      const root = document.querySelector('[data-home-state]');
      for (const fraction of [1, 0, .8, .2, 1, 0]) {
        root.scrollTo({ top: (root.scrollHeight - root.clientHeight) * fraction, behavior: 'instant' });
        await new Promise(resolve => requestAnimationFrame(resolve));
      }
    });
    await sample('rapid-reversals');
    await readyImage(expected[0], 3);
    const lifetime = await page.evaluate(() => window.__windowingLifetime());
    assert.ok(lifetime.created > 0 && lifetime.revoked > 0 && lifetime.created > lifetime.live, 'unmounted image URLs were revoked');
    checks.push(`${visits.length} visited cards plus long jumps/rapid reversals; card, slide, DOM, image URL and observer bounds`);

    step = 'navigation';
    const targetId = expected[20];
    await moveTo(targetId);
    const detailButton = card(targetId).locator('[data-home-detail]');
    await detailButton.scrollIntoViewIfNeeded(); await detailButton.focus();
    const beforeDetail = await sample('before-detail');
    await detailButton.click();
    const detail = page.locator('[data-detail-state="ready"]:visible'); await detail.waitFor();
    // Hidden persistent HOME must retain the opener so restoration can focus it.
    assert.equal(await card(targetId).count(), 1);
    assert.deepEqual(await slotIds(), expected);
    assert.ok(await home.locator('[data-home-post]').count() <= 8);
    await detail.getByRole('button', { name: '저장', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('[data-detail-state="ready"] button[aria-label="저장"]')?.getAttribute('aria-pressed') === 'true');
    await detail.getByRole('button', { name: '뒤로가기', exact: true }).click();
    await page.waitForFunction(({ top, id }) => Math.abs(document.querySelector('[data-home-state]').scrollTop - top) < 2
      && document.activeElement === document.querySelector(`[data-home-post="${id}"] [data-home-detail]`), { top: beforeDetail.top, id: targetId });
    assert.equal(await card(targetId).locator('[data-service-bookmark]').getAttribute('aria-pressed'), 'true');
    await sample('detail-return');
    await home.getByRole('button', { name: 'SAVE', exact: true }).click();
    await page.locator('[data-saved-state="ready"]:visible').waitFor();
    assert.equal(await page.locator('[data-saved-content]').count(), 1);
    await page.getByRole('button', { name: 'HOME', exact: true }).click();
    await settleVisible();
    await home.getByRole('button', { name: 'MY', exact: true }).click();
    await page.locator('[data-my-state="ready"]:visible').waitFor();
    await page.getByRole('button', { name: 'HOME', exact: true }).click();
    await sample('save-my-return');
    await Promise.all(pendingFeeds); assert.equal(feeds.length, 7, 'navigation retains loaded pages');
    checks.push('hidden HOME opener retained; DETAIL return restores exact scroll/focus; saved state and SAVE/MY navigation');

    step = 'talk-route-return';
    const rememberedId = expected[80], deepId = expected[100];
    await blur(); await moveTo(rememberedId);
    const rememberedTrack = card(rememberedId).locator('[data-home-track]');
    await rememberedTrack.focus(); await rememberedTrack.press('Home'); await expectSlide(rememberedId, 0);
    for (let index = 1; index <= 4; index++) { await rememberedTrack.press('ArrowRight'); await expectSlide(rememberedId, index); }
    await blur(); await moveTo(deepId);
    const deepTrack = card(deepId).locator('[data-home-track]');
    await deepTrack.focus(); await deepTrack.press('Home'); await expectSlide(deepId, 0);
    for (let index = 1; index <= 2; index++) { await deepTrack.press('ArrowRight'); await expectSlide(deepId, index); }
    await page.waitForFunction(id => !document.querySelector(`[data-home-post="${id}"]`), rememberedId);
    const beforeTalk = await sample('before-talk-deep');
    const requestsBeforeTalk = feedRequestCount();
    const talk = page.locator('main[aria-label="토론방"]');
    async function enterTalk(from) {
      await from.getByRole('button', { name: 'TALK', exact: true }).click();
      await page.waitForURL(url => url.pathname === '/pivot/debate');
      await talk.getByRole('button', { name: '주제 투표 보러가기', exact: true }).waitFor();
      await home.waitFor({ state: 'detached' });
      assert.equal(feedRequestCount(), requestsBeforeTalk, 'leaving HOME does not fetch another feed');
    }
    await enterTalk(home);
    await talk.getByRole('button', { name: 'HOME', exact: true }).click();
    await page.waitForURL(url => url.pathname === '/pivot/home' && !url.searchParams.has('view'));
    await home.waitFor({ state: 'visible' });
    await page.waitForFunction(top => Math.abs(document.querySelector('[data-home-state]').scrollTop - top) < 2
      && document.activeElement?.matches('[data-talk-navigation]'), beforeTalk.top);
    await expectSlide(deepId, 2);
    await sample('talk-to-home-cached-feed');
    assert.equal(await page.locator('[data-home-entry-loading]').count(), 0, 'TALK return skips first-entry loading');
    assert.equal(feedRequestCount(), requestsBeforeTalk, 'TALK return reuses all loaded pages');
    await moveTo(rememberedId); await expectSlide(rememberedId, 4);
    await sample('talk-restores-previously-unmounted-slide');
    await moveTo(expected[0]); await expectSlide(expected[0], 2);
    checks.push('real HOME→TALK→HOME route unmount/remount retains all 123 IDs, deep scroll, TALK focus and multiple offscreen slide indices without another feed request');

    step = 'detail-talk-history-return';
    await blur(); await moveTo(deepId); await expectSlide(deepId, 2);
    const deepDetailButton = card(deepId).locator('[data-home-detail]');
    await deepDetailButton.scrollIntoViewIfNeeded(); await deepDetailButton.focus();
    const beforeDetailTalk = await sample('before-detail-talk');
    await deepDetailButton.click(); await detail.waitFor();
    const detailUrl = page.url();
    await enterTalk(detail);
    await page.goBack();
    await page.waitForURL(detailUrl); await detail.waitFor();
    assert.deepEqual(await slotIds(), expected, 'history return to DETAIL keeps the complete cached feed');
    assert.equal(feedRequestCount(), requestsBeforeTalk, 'history return to DETAIL reuses loaded pages');
    await detail.getByRole('button', { name: '뒤로가기', exact: true }).click();
    await page.waitForFunction(({ top, id }) => Math.abs(document.querySelector('[data-home-state]').scrollTop - top) < 2
      && document.activeElement === document.querySelector(`[data-home-post="${id}"] [data-home-detail]`), { top: beforeDetailTalk.top, id: deepId });
    await expectSlide(deepId, 2); await sample('detail-talk-history-home-return');
    await blur(); await moveTo(rememberedId); await expectSlide(rememberedId, 4);
    await sample('detail-talk-retains-offscreen-index');
    await Promise.all(pendingFeeds);
    assert.equal(feedRequestCount(), requestsBeforeTalk);
    assert.equal(feeds.length, 7);
    assert.ok(debateFixtures.length >= 2, 'both real TALK visits received only the empty synthetic list');
    checks.push('DETAIL→TALK→browser back→DETAIL→HOME restores the opener focus, exact scroll and cached slide positions with bounded cards and no feed refetch');

    step = 'hidden-home-resize';
    const rotatedId = expected[100];
    await blur(); await moveTo(rotatedId);
    const rotatedButton = card(rotatedId).locator('[data-home-detail]');
    await rotatedButton.scrollIntoViewIfNeeded(); await rotatedButton.focus();
    const rotatedTop = await home.evaluate(element => element.scrollTop);
    await rotatedButton.click(); await detail.waitFor();
    await page.setViewportSize({ width: width < 600 ? 844 : 375, height: width < 600 ? width : 844 });
    // Let IO record hidden targets as non-intersecting before HOME is revealed.
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await detail.getByRole('button', { name: '뒤로가기', exact: true }).click();
    await page.waitForFunction(({ top, id }) => Math.abs(document.querySelector('[data-home-state]').scrollTop - top) < 2
      && document.activeElement === document.querySelector(`[data-home-post="${id}"] [data-home-detail]`), { top: rotatedTop, id: rotatedId });
    await sample('hidden-detail-resize-return');
    await page.waitForFunction(id => {
      const root = document.querySelector('[data-home-state]'), viewport = root.getBoundingClientRect();
      return [...root.querySelectorAll('[data-home-post]')].every(element => {
        const box = element.getBoundingClientRect();
        return element.dataset.homePost === id || (box.bottom >= viewport.top - 400 && box.top <= viewport.bottom + 400);
      });
    }, rotatedId);
    await page.setViewportSize({ width, height: width < 600 ? 844 : 1000 });
    await blur(); await moveTo(expected[0]);
    await sample('hidden-resize-original-viewport');
    checks.push('resize while DETAIL hides HOME reconciles the visible window; only the opener may remain offscreen; scroll/focus restored');

    step = 'keyboard';
    await blur(); await moveTo(expected[0]);
    const keyboardId = expected[90];
    await page.locator(`[data-home-card-activate="${keyboardId}"]`).waitFor({ state: 'attached' });
    // Focus is the browser's keyboard entry operation; scrolling must not be a prerequisite.
    await page.locator(`[data-home-card-activate="${keyboardId}"]`).focus();
    await page.waitForFunction(id => {
      const element = document.querySelector(`[data-home-post="${id}"]`);
      return element?.contains(document.activeElement) && document.activeElement?.matches('[data-home-track], [data-home-detail]');
    }, keyboardId);
    await card(keyboardId).locator('[data-home-track]').press('ArrowRight'); await expectSlide(keyboardId, 1);
    await sample('keyboard-offscreen-entry');
    checks.push('offscreen card keyboard entry mounts card and retains focus in its controls');

    step = 'resize';
    await blur(); await moveTo(expected[0]); await expectSlide(expected[0], 2);
    await page.setViewportSize({ width: width < 600 ? 844 : 900, height: width < 600 ? width : 700 });
    await settleVisible(); await moveTo(expected[0]); await expectSlide(expected[0], 2);
    await sample('landscape-or-resize');
    await page.setViewportSize({ width, height: width < 600 ? 844 : 1000 });
    await moveTo(expected[0]); await expectSlide(expected[0], 2);
    await sample('original-viewport-restored');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    assert.equal(await page.locator('[data-home-entry-loading]').count(), 0);
    await page.screenshot({ path: path.join(output, `final-E-${width}.png`) });
    checks.push('viewport/orientation dimensions preserve slide index and non-collapsing slots without horizontal overflow');

    assert.deepEqual(await page.evaluate(() => window.__windowingCsp), []);
    assert.deepEqual(external, []); assert.deepEqual(errors, []); assert.deepEqual(consoleErrors, []);
    const imageMetrics = await page.evaluate(() => structuredClone(window.__labImages));
    assert.ok(imageMetrics.native.responseBlob > 0 && imageMetrics.native.objectUrl > 0 && imageMetrics.native.decode > 0);
    await Promise.all(pendingFeeds); assert.equal(feedRequestCount(), 7, 'entire run retains the original seven feed pages');
    cases.push({ width, status: 'pass', checks, feeds, samples, imageMetrics, debateFixtures, external, errors, consoleErrors });
    console.log(`PASS hosted E ${width}: ${checks.join('; ')}`);
  } catch (error) {
    const diagnostic = await page.evaluate(() => ({ url: location.pathname + location.search,
      focused: document.activeElement?.outerHTML?.slice(0, 700), lifetime: window.__windowingLifetime?.(),
      mounted: [...document.querySelectorAll('[data-home-post]')].map(element => element.dataset.homePost),
      slots: document.querySelectorAll('[data-home-feed-slot]').length,
      top: document.querySelector('[data-home-state]')?.scrollTop, csp: window.__windowingCsp })).catch(() => null);
    cases.push({ width, status: 'fail', step, error: redact(error.stack || error.message), checks, samples, feeds, debateFixtures, external, errors, consoleErrors, diagnostic });
    await page.screenshot({ path: path.join(output, `failure-E-${width}.png`) }).catch(() => {});
    throw error;
  } finally { await context.close(); }
}

(async () => {
  assert.equal(process.platform, 'win32', 'This runner records Windows Chrome regression evidence');
  fs.mkdirSync(output, { recursive: true });
  let browser;
  try {
    browser = await chromium.launch({ headless: true, channel: 'chrome', args: [
      '--disable-background-networking', '--disable-component-update', '--disable-sync', '--no-first-run',
      '--disable-default-apps', '--disable-domain-reliability', '--disable-client-side-phishing-detection', '--no-pings', '--disable-quic',
      '--proxy-server=http://127.0.0.1:9', '--proxy-bypass-list=safari-check.me-nu.kr;<-loopback>',
    ] });
    browserVersion = browser.version();
    for (const width of widths) await run(browser, width);
    completed = true;
  } finally {
    if (browser) await browser.close();
    fs.writeFileSync(path.join(output, 'requests.jsonl'), network.map(request => JSON.stringify(redact(request))).join('\n') + '\n');
    fs.writeFileSync(path.join(output, 'verification.json'), JSON.stringify(redact({
      passed: completed && cases.length === widths.length && cases.every(result => result.status === 'pass'),
      origin, variant: 'E', widths, browserVersion, cases,
      scope: 'Separate ephemeral Windows headless Chrome contexts on trusted public HTTPS. Synthetic isolated E only; exact-origin credentials; external routes blocked; background proxy sink. TALK uses the actual Next route with only its exact GET live-debate list fulfilled as empty synthetic JSON. No user browser, operating API/DB, TLS bypass or iPhone Safari memory measurement.',
    }), null, 2));
  }
})().catch(error => { console.error(redact(error.stack || error.message)); process.exitCode = 1; });
