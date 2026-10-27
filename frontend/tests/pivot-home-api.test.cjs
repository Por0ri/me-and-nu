const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

const root = path.resolve(__dirname, '..');
const active = { authenticated: true, sessionState: 'active', onboardingCompleted: true, user: { id: 12, accountType: 'user' }, csrfToken: 'fixture-csrf' };
const anonymous = { authenticated: false, sessionState: 'anonymous', onboardingCompleted: false, user: null, csrfToken: 'fixture-csrf' };
const topic = (id = 1, fields = {}) => ({ id, code: 'fixture', name: '테스트 관심사', subtopicIds: [], status: 'active', deletedAt: null, ...fields });
// Contract-shaped fixtures only: titles/images are not asserted to be live BE data.
const content = (id, fields = {}) => ({
  kind: 'content', id, topicId: 1, recommendationId: null, title: `테스트 콘텐츠 ${id}`,
  productionType: 'human', summary: `테스트 요약 ${id}`, imageUrl: null,
  sourceName: '테스트 출처', sourceUrl: 'https://example.test/article', publishedAt: null,
  saved: false, savedItemId: null, recommendationReason: null, seen: false, isPromotional: false,
  notices: [], myReaction: { liked: false, preference: null }, ...fields,
});
const feed = (contents = [content(1, { imageUrl: 'https://images.example.test/1.png' }), content(2)], fields = {}) => ({
  selectedTopicId: 1, sections: [{ id: 'recommended', type: 'recommended', title: '추천 콘텐츠', subtopicId: null, contents }],
  nextCursor: null, emptyReason: null, ...fields,
});
const json = (payload, status = 200) => new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } });
const plain = value => JSON.parse(JSON.stringify(value));
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
const tick = () => new Promise(resolve => setImmediate(resolve));

// Actual API client, Origin/session gates, Home adapter/controller and UI, with no network access.
function environment({ session = active, href = 'https://test.me-nu.kr/pivot/home', env = {}, respond, overrides = {} } = {}) {
  const requests = [], events = [], cache = new Map();
  let currentSession = session;
  const window = new EventTarget();
  window.location = new URL(href);
  window.addEventListener('api:unauthorized', () => events.push('unauthorized'));
  function load(relative) {
    if (cache.has(relative)) return cache.get(relative).exports;
    const module = { exports: {} };
    cache.set(relative, module);
    const code = ts.transpileModule(fs.readFileSync(path.join(root, relative), 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
    }).outputText;
    vm.runInNewContext(code, { module, exports: module.exports, URL, URLSearchParams, Headers, Response, Event, Error,
      AbortController, structuredClone, window,
      process: { env: { NEXT_PUBLIC_API_BASE_URL: 'https://api.test.me-nu.kr', NEXT_PUBLIC_PIVOT_ENABLE_TEST_API: 'true', ...env } },
      async fetch(url, init) {
        requests.push({ url, init });
        if (!respond) throw new Error('Unexpected fetch');
        return respond(url, init, requests.length);
      },
      require(name) {
        if (name in overrides) return overrides[name];
        if (name === 'react') return React;
        if (name === 'react/jsx-runtime') return require(name);
        if (name.endsWith('.module.css')) return { __esModule: true, default: new Proxy({}, { get: (_, key) => String(key) }) };
        const base = name.startsWith('@/') ? name.slice(2) : name.startsWith('.') ? path.posix.join(path.posix.dirname(relative), name) : null;
        if (!base) throw new Error('Unexpected import: ' + name);
        const file = [base + '.ts', base + '.tsx'].find(file => fs.existsSync(path.join(root, file)));
        assert.ok(file, name);
        return load(file);
      },
    }, { filename: relative });
    return module.exports;
  }
  const source = load('lib/pivot/home-service.ts').createServiceHomeSource(() => currentSession);
  function controller() {
    const states = [];
    return { states, ...load('lib/pivot/home.ts').createHomeRequestController(source, state => states.push(state)) };
  }
  function render(state) {
    return renderToStaticMarkup(React.createElement(load('components/pivot/home-screen.tsx').PivotHomeScreen, {
      state, demo: false, query: '', onQueryChange() {}, onRetry() {},
    }));
  }
  return { source, load, controller, render, requests, events, window, setSession(value) { currentSession = value; } };
}
const success = url => json(url.endsWith('/me/topics') ? { topics: [topic()] } : feed());

test('No authenticated active session means zero Topic/feed requests', async () => {
  for (const session of [null, anonymous, { ...anonymous, authenticated: true, sessionState: 'onboarding_pending' },
    { ...active, sessionState: 'restoration_pending' }, { ...active, authenticated: false },
    { ...active, user: null }, { ...active, csrfToken: '' }]) {
    const e = environment({ session, respond: success }), c = e.controller();
    await c.load();
    assert.deepEqual(c.states.map(s => s.status), ['loading', 'error']);
    assert.equal(c.states.at(-1).reason, 'unauthorized');
    assert.equal(e.requests.length, 0);
  }
});

test('Reads active Topics before feed with cookies, no cache, shared abort signal and no write', async () => {
  const e = environment({ respond: success }), c = e.controller();
  await c.load();
  assert.deepEqual(e.requests.map(r => r.url), ['https://api.test.me-nu.kr/api/v1/me/topics', 'https://api.test.me-nu.kr/api/v1/topics/1/feed']);
  for (const { init } of e.requests) {
    assert.equal(init.method ?? 'GET', 'GET');
    assert.equal(init.credentials, 'include');
    assert.equal(init.cache, 'no-store');
    assert.equal(init.headers.has('X-CSRF-Token'), false);
    assert.equal(init.body, undefined);
    assert.equal(init.signal, e.requests[0].init.signal);
  }
  assert.deepEqual(c.states.map(s => s.status), ['loading', 'ready']);
  const home = plain(c.states.at(-1).home);
  assert.equal(home.origin, 'service');
  assert.deepEqual(home.cards.map(c => [c.id, c.detailContentId]), [['1', '1'], ['2', '2']]);
  assert.deepEqual(home.cards.map(c => c.title), ['테스트 콘텐츠 1', '테스트 콘텐츠 2']);
  assert.deepEqual(home.cards.map(c => c.slides.length), [1, 1]);
  assert.deepEqual(home.cards[0].slides[0], { id: '1-cover', order: 1, description: '테스트 요약 1', image: { src: 'https://images.example.test/1.png', alt: '테스트 콘텐츠 1' } });
  assert.equal(home.cards[1].slides[0].image, undefined);
  const html = e.render(c.states.at(-1));
  assert.equal((html.match(/<article/g) || []).length, 2);
  assert.match(html, /data-home-detail="1"/);
  assert.match(html, /data-home-detail="2"/);
  assert.match(html, /이미지 준비 중/);
  assert.doesNotMatch(html, /시연용|demo-home/);
});

test('Uses first active non-deleted Topic in BE order, never a hard-coded 1', async () => {
  const e = environment({ respond: url => json(url.endsWith('/me/topics')
    ? { topics: [topic(1, { status: 'deleted' }), topic(3, { deletedAt: '2026-10-01' }), topic(9), topic(4)] }
    : feed([content(40, { topicId: 9 })], { selectedTopicId: 9 })) });
  const c = e.controller(); await c.load();
  assert.match(e.requests[1].url, /\/topics\/9\/feed$/);
  assert.equal(c.states.at(-1).home.cards[0].detailContentId, '40');
});

test('Missing image/summary stays missing; sections preserve order and duplicate content IDs render once', () => {
  const e = environment(), map = e.load('lib/pivot/home-service.ts').homeFromFeed;
  const response = feed([], { sections: [
    { contents: [content(2, { imageUrl: '', summary: null }), content(1, { imageUrl: undefined, summary: undefined })] },
    { contents: [content(2), content(3, { imageUrl: '   ' })] },
  ] });
  const before = plain(response), home = map(response, 1);
  assert.deepEqual(plain(response), before);
  assert.deepEqual(plain(home.cards.map(c => c.id)), ['2', '1', '3']);
  assert.ok(home.cards.every(c => c.slides.length === 1 && !c.slides[0].image));
  assert.equal(home.cards[0].slides[0].description, '');
  assert.equal(home.cards[1].slides[0].description, '');
});

for (const [name, topics, payload, count] of [
  ['no Topics', [], feed(), 1], ['only deleted Topics', [topic(1, { status: 'deleted' })], feed(), 1],
  ['empty sections', [topic()], feed([], { sections: [] }), 2], ['empty contents', [topic()], feed([]), 2],
]) test(name + ' renders the empty state', async () => {
  const e = environment({ respond: url => json(url.endsWith('/me/topics') ? { topics } : payload) }), c = e.controller();
  await c.load();
  assert.deepEqual(c.states.map(s => s.status), ['loading', 'empty']);
  assert.equal(e.requests.length, count);
  assert.match(e.render(c.states.at(-1)), /아직 표시할 콘텐츠/);
});

for (const endpoint of ['topics', 'feed']) for (const [status, reason, text] of [
  [401, 'unauthorized', '다시 로그인'], [403, 'forbidden', '접근할 권한'], [500, 'server', '잠시 후'], [0, 'network', '네트워크 연결'],
]) test(`${endpoint}: ${reason} stays distinct and never falls back to Demo`, async () => {
  const e = environment({ respond(url) {
    if (endpoint === 'feed' && url.endsWith('/me/topics')) return json({ topics: [topic()] });
    if (!status) throw new TypeError('Private network error');
    return json({ code: 'FIXTURE_ERROR', message: 'Private BE detail' }, status);
  } }), c = e.controller();
  await c.load();
  assert.deepEqual(c.states.map(s => s.status), ['loading', 'error']);
  assert.equal(c.states.at(-1).reason, reason);
  const html = e.render(c.states.at(-1));
  assert.match(html, new RegExp(text));
  assert.match(html, /role="alert"/);
  assert.doesNotMatch(html, /<article|시연용|Private/);
  assert.deepEqual(e.events, status === 401 ? ['unauthorized'] : []);
  assert.equal(e.requests.length, endpoint === 'topics' ? 1 : 2);
});

test('Malformed or wrong-Topic responses fail closed instead of appearing empty', async () => {
  for (const payload of [null, {}, { selectedTopicId: 9, sections: [] }, feed([], { sections: null }),
    feed([], { sections: [{ contents: null }] }), feed([content('1')]), feed([content(1, { topicId: 9 })]),
    feed([content(1, { title: null })]), feed([content(1, { imageUrl: [] })])]) {
    const e = environment({ respond: url => json(url.endsWith('/me/topics') ? { topics: [topic()] } : payload) }), c = e.controller();
    await c.load(); assert.equal(c.states.at(-1).reason, 'invalid-response');
  }
  for (const payload of [null, {}, { topics: null }, { topics: [null] }, { topics: [topic('1')] }]) {
    const e = environment({ respond: () => json(payload) }), c = e.controller();
    await c.load(); assert.equal(c.states.at(-1).reason, 'invalid-response');
    assert.equal(e.requests.length, 1);
  }
});

test('Demo, preview, disabled API and wrong Origin never fetch, even with an active fixture session', async () => {
  for (const options of [
    { href: 'https://test.me-nu.kr/pivot/demo' }, { href: 'https://test.me-nu.kr/pivot/preview' },
    { href: 'https://test.me-nu.kr/pivot/home/gesture-spike' }, { href: 'https://other.vercel.app/pivot/home' },
    { env: { NEXT_PUBLIC_PIVOT_ENABLE_TEST_API: 'false' } }, { env: { NEXT_PUBLIC_API_BASE_URL: 'https://api.me-nu.kr' } },
  ]) {
    const e = environment(options), c = e.controller(); await c.load();
    assert.equal(c.states.at(-1).status, 'unconfigured');
    const demo = await e.load('lib/pivot/home-mock.ts').mockHomeSource.load({ signal: new AbortController().signal });
    assert.equal(demo.home.origin, 'demo'); assert.equal(demo.home.cards.length, 3);
    assert.equal(e.requests.length, 0);
  }
});

test('Cancellation before loading or during Topics prevents a feed request', async () => {
  const pending = deferred(), e = environment({ respond: () => pending.promise });
  const abort = new AbortController(); abort.abort();
  await assert.rejects(e.source.load({ signal: abort.signal }), { name: 'AbortError' });
  assert.equal(e.requests.length, 0);
  const c = e.controller(), running = c.load();
  c.cancel(); pending.resolve(json({ topics: [topic()] })); await running;
  assert.equal(e.requests[0].init.signal.aborted, true);
  assert.equal(e.requests.length, 1);
  assert.deepEqual(c.states.map(s => s.status), ['loading']);
});

test('Session loss/account change while Topics loads prevents the feed request', async () => {
  for (const session of [null, anonymous, { ...active, user: { ...active.user, id: 99 } }]) {
    const pending = deferred(), e = environment({ respond: () => pending.promise }), c = e.controller();
    const running = c.load(); e.setSession(session); pending.resolve(json({ topics: [topic()] })); await running;
    assert.equal(e.requests.length, 1); assert.equal(c.states.at(-1).reason, 'unauthorized');
  }
});

for (const lateStatus of [200, 401, 403]) test(`Repeated load ignores cancelled late ${lateStatus}, including its session event`, async () => {
  const pending = deferred(); let feeds = 0;
  const e = environment({ respond(url) {
    if (url.endsWith('/me/topics')) return json({ topics: [topic()] });
    return ++feeds === 1 ? pending.promise : json(feed([content(2)]));
  } }), c = e.controller();
  const old = c.load(); await tick();
  assert.equal(e.requests.length, 2);
  await c.load();
  pending.resolve(json(lateStatus === 200 ? feed([content(1)]) : { message: 'stale' }, lateStatus)); await old;
  assert.deepEqual(c.states.map(s => s.status), ['loading', 'loading', 'ready']);
  assert.equal(c.states.at(-1).home.cards[0].id, '2');
  assert.equal(e.requests[1].init.signal.aborted, true);
  assert.deepEqual(e.events, []);
});

test('A network error can retry into the real service feed', async () => {
  let fail = true;
  const e = environment({ respond(url) { if (fail) throw new TypeError('offline'); return success(url); } }), c = e.controller();
  await c.load(); fail = false; await c.load();
  assert.deepEqual(c.states.map(s => s.status), ['loading', 'error', 'loading', 'ready']);
  assert.equal(c.states.at(-1).home.origin, 'service');
});

test('Current scope fetches only the first page without an automatic request loop', async () => {
  const e = environment({ respond: url => json(url.endsWith('/me/topics') ? { topics: [topic()] } : feed(undefined, { nextCursor: 'another-page' })) });
  const c = e.controller(); await c.load();
  assert.equal(e.requests.length, 2); assert.equal(c.states.at(-1).home.cards.length, 2);
});

test('Service single-slide controls keep IDs for existing Detail navigation and never call Detail API', async () => {
  const e = environment({ respond: success }), c = e.controller(); await c.load();
  const writes = [], targets = [], hooks = []; let cursor = 0;
  const react = { ...React, useId: () => 'fixture-card',
    useRef: () => ({ current: { clientWidth: 327, scrollTo: value => writes.push(value) } }),
    useState(initial) { const index = cursor++; hooks[index] ??= initial; return [hooks[index], value => { hooks[index] = value; }]; },
  };
  const ui = environment({ overrides: { react } });
  const card = c.states.at(-1).home.cards[0];
  const tree = ui.load('components/pivot/home-screen.tsx').HomeCard({ card, demo: false, onViewDetail: target => targets.push(plain(target)) });
  const all = node => !node || typeof node !== 'object' ? [] : [node, ...React.Children.toArray(node.props?.children).flatMap(all)];
  const nodes = all(tree), track = nodes.find(n => n.props?.['data-home-track'] === '');
  assert.equal(nodes.find(n => n.props?.['aria-label'] === '다음 슬라이드').props.disabled, true);
  const target = {};
  track.props.onKeyDown({ key: 'ArrowRight', target, currentTarget: target, preventDefault() {} });
  assert.deepEqual(writes, []);
  track.props.onScroll({ currentTarget: { clientWidth: 327, scrollLeft: 0 } });
  nodes.find(n => n.props?.['data-home-detail'] === '1').props.onClick();
  assert.deepEqual(targets, [{ postId: '1', contentId: '1' }]);
  const href = e.load('lib/pivot/detail-navigation.ts').detailHref('/pivot/home', targets[0]);
  assert.equal(href, '/pivot/home?view=detail&postId=1&contentId=1');
  const detail = await e.load('lib/pivot/detail-service.ts').pendingDetailSource.load({ target: targets[0], signal: new AbortController().signal });
  assert.equal(detail.status, 'unconfigured');
  assert.equal(e.requests.length, 2);
});

test('Home route keeps a stable service across rerenders and replaces it on session change', () => {
  let state = { status: 'ready', session: active }, memo;
  const Experience = () => null;
  const react = { ...React, useEffect() {}, useMemo(factory, dependencies) {
    if (!memo || dependencies.some((value, index) => value !== memo.dependencies[index])) {
      memo = { dependencies, value: factory() };
    }
    return memo.value;
  } };
  const e = environment({ overrides: {
    react, './pivot-session': { usePivotSession: () => ({ state, refresh() {} }) },
    'next/navigation': { useRouter: () => ({ replace() {} }) },
    'next/link': { __esModule: true, default: () => null },
    './pivot-ui': { PivotFrame: () => null, PivotBrand: () => null },
    './home-detail-experience': { HomeDetailExperience: Experience },
  } });
  const { HomeRoute } = e.load('components/pivot/home-route.tsx');
  const renderSource = () => {
    const tree = HomeRoute();
    return tree.props.children?.type === Experience ? tree.props.children.props.homeSource : null;
  };
  const first = renderSource();
  assert.equal(first.kind, 'service');
  state = { ...state };
  assert.equal(renderSource(), first, 'Render must not restart the Home controller request');
  state = { status: 'ready', session: { ...active, user: { ...active.user, id: 99 } } };
  assert.notEqual(renderSource(), first, 'Account change must invalidate the previous feed source');
  for (const status of ['loading', 'error']) {
    state = { status, session: active }; assert.equal(renderSource(), null);
  }
  state = { status: 'ready', session: anonymous }; assert.equal(renderSource(), null);
  state = { status: 'disconnected', session: null };
  assert.equal(renderSource(), e.load('lib/pivot/home-service.ts').pendingHomeSource);
  assert.equal(e.requests.length, 0);
});
