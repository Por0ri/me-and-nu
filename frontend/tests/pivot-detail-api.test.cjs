const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const root = path.resolve(__dirname, '..');
const active = { authenticated: true, sessionState: 'active', onboardingCompleted: true, user: { id: 12, accountType: 'user' }, csrfToken: 'fixture' };
const anonymous = { authenticated: false, sessionState: 'anonymous', onboardingCompleted: false, user: null, csrfToken: 'fixture' };
const target = { postId: '301', contentId: '301', topicId: '9' };
// Required deployed OpenAPI fields only. In particular, channel/practicalInfo/share.text are absent.
const payload = (fields = {}) => ({ contentId: 301, topicId: 9, title: '실제 응답 제목', productionType: 'human',
  sourceUrl: 'https://source.example.test/article', publisher: '응답 출처', publishedAt: null,
  share: { url: 'https://source.example.test/article' }, ...fields });
const json = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const plain = value => JSON.parse(JSON.stringify(value));

function environment({ session = active, href = 'https://test.me-nu.kr/pivot/home', env = {}, respond, overrides = {} } = {}) {
  const requests = [], events = [], cache = new Map();
  let currentSession = session;
  const window = new EventTarget(); window.location = new URL(href);
  window.addEventListener('api:unauthorized', () => events.push('unauthorized'));
  function load(file) {
    if (cache.has(file)) return cache.get(file).exports;
    const module = { exports: {} }; cache.set(file, module);
    const output = ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), { compilerOptions: {
      module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
    } }).outputText;
    vm.runInNewContext(output, { module, exports: module.exports, URL, URLSearchParams, Headers, Response, Event,
      AbortController, structuredClone, window,
      process: { env: { NEXT_PUBLIC_API_BASE_URL: 'https://api.test.me-nu.kr', NEXT_PUBLIC_PIVOT_ENABLE_TEST_API: 'true', ...env } },
      async fetch(url, init) { requests.push({ url, init }); if (!respond) throw new Error('Unexpected fetch'); return respond(url, init, requests.length); },
      require(name) {
        if (Object.hasOwn(overrides, name)) return overrides[name];
        if (name === 'react' || name === 'react/jsx-runtime') return require(name);
        if (name.endsWith('.module.css')) return { __esModule: true, default: new Proxy({}, { get: (_, key) => String(key) }) };
        const base = name.startsWith('@/') ? name.slice(2) : name.startsWith('.') ? path.posix.join(path.posix.dirname(file), name) : null;
        assert.ok(base, 'Unexpected import: ' + name);
        const next = [base + '.ts', base + '.tsx'].find(candidate => fs.existsSync(path.join(root, candidate)));
        assert.ok(next, name); return load(next);
      },
    }, { filename: file });
    return module.exports;
  }
  const source = load('lib/pivot/detail-service.ts').createServiceDetailSource(() => currentSession);
  return { source, load, requests, events, window, setSession(value) { currentSession = value; },
    controller() { const states = []; return { states, ...load('lib/pivot/detail.ts').createDetailRequestController(source, state => states.push(state)) }; },
    render(state) { return renderToStaticMarkup(React.createElement(load('components/pivot/detail-screen.tsx').PivotDetailScreen, { state, demo: false, onBack() {}, onRetry() {} })); },
  };
}

test('Active Detail GET uses exact IDs, cookies, shared AbortSignal and no CSRF write or extra read', async () => {
  const e = environment({ respond: () => json(payload({ displayMode: 'full_body', body: '첫 문단\n\n둘째 문단', imageUrl: 'https://images.example.test/cover.png' })) });
  const c = e.controller(); await c.load(target);
  assert.deepEqual(c.states.map(s => s.status), ['loading', 'ready']);
  assert.equal(e.requests.length, 1);
  const { url, init } = e.requests[0];
  assert.equal(url, 'https://api.test.me-nu.kr/api/v1/contents/301?topicId=9');
  assert.equal(init.method ?? 'GET', 'GET'); assert.equal(init.credentials, 'include'); assert.equal(init.cache, 'no-store');
  assert.equal(init.headers.has('X-CSRF-Token'), false); assert.equal(init.body, undefined); assert.ok(init.signal instanceof AbortSignal);
  assert.deepEqual(e.events, []);
  const article = c.states.at(-1).article;
  assert.equal(article.origin, 'service'); assert.equal(article.work, undefined); assert.equal(article.topicId, '9');
  assert.deepEqual(plain(article.sections), [{ paragraphs: ['첫 문단', '둘째 문단'] }]);
  const html = e.render(c.states.at(-1));
  assert.match(html, /<h1[^>]*>실제 응답 제목<\/h1>/); assert.match(html, /첫 문단/); assert.match(html, /둘째 문단/);
  assert.match(html, /src="https:\/\/images.example.test\/cover.png"/); assert.match(html, /참고 출처 보기/);
  assert.doesNotMatch(html, /하이큐|undefined|Demo 선택|더 볼래요/);
});

test('Two-argument getContent remains compatible with existing callers', async () => {
  const e = environment({ respond: () => json(payload()) });
  assert.equal((await e.load('lib/api.ts').getContent(301, 9)).contentId, 301);
  assert.equal(e.requests[0].init.signal, undefined); assert.equal(e.requests.length, 1);
});

test('Inactive or malformed sessions never issue Detail reads', async () => {
  for (const session of [null, anonymous, { ...anonymous, authenticated: true, sessionState: 'onboarding_pending' },
    { ...active, sessionState: 'restoration_pending' }, { ...active, csrfToken: '' }, { ...active, user: null }]) {
    const e = environment({ session }), c = e.controller(); await c.load(target);
    assert.equal(c.states.at(-1).reason, 'unauthorized'); assert.equal(e.requests.length, 0);
  }
});

test('Missing or invalid content/Topic IDs stop at controller and direct source boundaries', async () => {
  const e = environment();
  for (const field of ['contentId', 'topicId']) for (const value of [undefined, '', '0', '-1', '01', '1.2', '1e2', '9007199254740992', 'demo-content-story']) {
    const requested = { ...target, [field]: value }, c = e.controller(); await c.load(requested);
    assert.equal(c.states.at(-1).status, 'not-found');
    assert.equal((await e.source.load(requested, { signal: new AbortController().signal })).reason, 'invalid-request');
  }
  assert.equal(e.requests.length, 0);
});

test('Minimal deployed DTO and null fields keep title, publisher and original link visible', async () => {
  for (const fields of [{}, { body: null, excerpt: null, imageUrl: null, contentType: null },
    { displayMode: 'full_body', body: null, excerpt: null, imageUrl: null }]) {
    const e = environment({ respond: () => json(payload(fields)) }), c = e.controller(); await c.load(target);
    assert.equal(c.states.at(-1).status, 'ready');
    const html = e.render(c.states.at(-1));
    assert.match(html, /실제 응답 제목/); assert.match(html, /출처: 응답 출처/);
    assert.match(html, /대표 이미지 준비 중/); assert.match(html, /본문이 제공되지 않았어요/);
    assert.match(html, /href="https:\/\/source.example.test\/article" target="_blank" rel="noopener noreferrer"/);
    assert.doesNotMatch(html, /data-detail-state="empty"|하이큐|undefined/);
  }
});

test('link_excerpt displays only excerpt and source; full_body can fall back to a provided excerpt', async () => {
  for (const displayMode of ['link_excerpt', undefined]) {
    const e = environment({ respond: () => json(payload({ displayMode, excerpt: '허용된 발췌', body: 'NEVER SHOW FULL BODY' })) });
    const c = e.controller(); await c.load(target); const html = e.render(c.states.at(-1));
    assert.match(html, /허용된 발췌/); assert.match(html, /원문 보기/); assert.doesNotMatch(html, /NEVER SHOW FULL BODY/);
  }
  const e = environment({ respond: () => json(payload({ displayMode: 'full_body', body: null, excerpt: '제공된 발췌' })) });
  const c = e.controller(); await c.load(target); assert.match(e.render(c.states.at(-1)), /제공된 발췌/);
});

test('HTML and Markdown payloads remain escaped text without a renderer or invented metadata', async () => {
  for (const contentType of ['html', 'markdown', 'article', null]) {
    const e = environment({ respond: () => json(payload({ contentType, displayMode: 'full_body', title: '<script>title</script>',
      body: '<img src=x onerror=alert(1)>\n# Heading\n[bad](javascript:alert(1))', publisher: '<b>publisher</b>' })) });
    const c = e.controller(); await c.load(target); const html = e.render(c.states.at(-1));
    assert.match(html, /&lt;script&gt;/); assert.match(html, /&lt;img/); assert.match(html, /&lt;b&gt;publisher/); assert.match(html, /# Heading/);
    assert.doesNotMatch(html, /<script|<img src="x"|href="javascript:|<b>publisher/);
  }
});

test('Unsafe source/image URLs are absent; HTTP(S) links are safe new-window links', async () => {
  for (const sourceUrl of ['', 'javascript:alert(1)', 'data:text/html,bad', '//example.test', '/article', 'ftp://example.test',
    'https://user:secret@example.test', 'https://example.test/\narticle', 'https:\\evil.test', ' https://example.test']) {
    const e = environment({ respond: () => json(payload({ sourceUrl, imageUrl: sourceUrl })) }), c = e.controller(); await c.load(target);
    assert.equal(c.states.at(-1).status, 'ready');
    const html = e.render(c.states.at(-1)); assert.doesNotMatch(html, /<a\s|data-detail-state="empty"/); assert.match(html, /대표 이미지 준비 중/);
  }
  for (const sourceUrl of ['http://source.example.test/a?x=1&y=2', 'https://source.example.test/article']) {
    const e = environment({ respond: () => json(payload({ sourceUrl })) }), c = e.controller(); await c.load(target);
    assert.match(e.render(c.states.at(-1)), /target="_blank" rel="noopener noreferrer"/);
  }
});

test('Malformed DTOs and mismatched identities fail closed without exposing payloads', async () => {
  for (const response of [null, [], {}, ...[
    { contentId: '301' }, { contentId: 302 }, { topicId: 1 }, { topicId: '9' }, { title: null }, { title: ' ' },
    { publisher: null }, { sourceUrl: null }, { publishedAt: undefined }, { publishedAt: {} }, { productionType: 'unknown' }, { productionType: ['ai'] },
    { displayMode: 'future_mode' }, { body: {} }, { excerpt: [] }, { imageUrl: false }, { contentType: 1 },
    { share: undefined }, { share: null }, { share: { url: 1 } }, { share: { url: 'https://example.test', text: [] } },
  ].map(fields => payload(fields))]) {
    const e = environment({ respond: () => json(response) }), c = e.controller(); await c.load(target);
    assert.equal(c.states.at(-1).reason, 'invalid-response'); assert.doesNotMatch(e.render(c.states.at(-1)), /data-detail-content/);
  }
});

for (const [status, code, reason, message] of [
  [401, 'AUTH_REQUIRED', 'unauthorized', '로그인이 만료'],
  [403, 'ONBOARDING_REQUIRED', 'onboarding-required', '가입 완료 상태'],
  [403, 'FORBIDDEN', 'forbidden', '권한이 없어요'],
  [404, 'CONTENT_NOT_FOUND', 'content-not-found', '공개되지 않은'],
  [404, 'TOPIC_NOT_FOUND', 'topic-not-found', '관심사를 찾지'],
  [422, 'VALIDATION_ERROR', 'invalid-request', '콘텐츠 주소'],
  [500, 'INTERNAL_ERROR', 'server', '잠시 후'], [503, 'UNAVAILABLE', 'server', '잠시 후'],
  [0, 'NETWORK', 'network', '네트워크 연결'],
]) test('Detail error: ' + reason + ' / ' + status, async () => {
  const e = environment({ respond() { if (!status) throw new TypeError('private secret'); return json({ code, message: 'private secret', requestId: 'private request id' }, status); } });
  const c = e.controller(); await c.load(target);
  assert.deepEqual(c.states.map(s => s.status), ['loading', 'error']); assert.equal(c.states.at(-1).reason, reason);
  const html = e.render(c.states.at(-1)); assert.match(html, new RegExp(message)); assert.match(html, /role="alert"/); assert.match(html, /다시 시도/);
  assert.doesNotMatch(html, /private|data-detail-content|하이큐/); assert.equal(e.requests.length, 1);
  assert.deepEqual(e.events, status === 401 ? ['unauthorized'] : []);
});

test('Explicit retry can recover, with no automatic retry loop', async () => {
  let fail = true; const e = environment({ respond: () => fail ? json({ code: 'FAIL' }, 500) : json(payload()) }), c = e.controller();
  await c.load(target); assert.equal(e.requests.length, 1); fail = false; await c.load(target);
  assert.deepEqual(c.states.map(s => s.status), ['loading', 'error', 'loading', 'ready']); assert.equal(e.requests.length, 2);
});

test('Demo, Preview, Gesture Spike and disallowed API environments issue zero requests', async () => {
  for (const config of [
    { href: 'https://test.me-nu.kr/pivot/demo' }, { href: 'https://test.me-nu.kr/pivot/preview' },
    { href: 'https://test.me-nu.kr/pivot/home/gesture-spike' }, { href: 'https://test.me-nu.kr/pivot/home/gesture-spike/nested' },
    { href: 'https://other.vercel.app/pivot/home' }, { env: { NEXT_PUBLIC_PIVOT_ENABLE_TEST_API: 'false' } },
    { env: { NEXT_PUBLIC_API_BASE_URL: 'https://api.me-nu.kr' } },
  ]) {
    const e = environment(config), c = e.controller(); await c.load(target);
    assert.equal(c.states.at(-1).status, 'unconfigured'); assert.equal(e.requests.length, 0);
  }
});

test('Pre-aborted reads never reach fetch', async () => {
  const e = environment(), abort = new AbortController(); abort.abort();
  await assert.rejects(e.source.load(target, { signal: abort.signal }), { name: 'AbortError' });
  assert.equal(e.requests.length, 0);
});

for (const late of [200, 401, 500, 'network']) test('Cancelled late ' + late + ' cannot replace new Topic data or invalidate its session', async () => {
  const pending = deferred();
  const next = { ...target, topicId: '20' };
  const e = environment({ respond: (url, init, n) => n === 1 ? pending.promise : json(payload({ topicId: 20 })) }), c = e.controller();
  const old = c.load(target); await c.load(next);
  assert.equal(e.requests[0].init.signal.aborted, true);
  if (late === 'network') pending.reject(new TypeError('late')); else pending.resolve(json(late === 200 ? payload() : { code: 'STALE' }, late));
  await old;
  assert.deepEqual(c.states.map(s => s.status), ['loading', 'loading', 'ready']); assert.equal(c.states.at(-1).article.topicId, '20');
  assert.equal(e.requests[1].url, 'https://api.test.me-nu.kr/api/v1/contents/301?topicId=20'); assert.deepEqual(e.events, []);
});

test('Cancellation while parsing a 401 response suppresses the unauthorized event', async () => {
  const body = deferred(), e = environment({ respond: () => ({ status: 401, ok: false, json: () => body.promise }) }), c = e.controller();
  const running = c.load(target); await new Promise(resolve => setImmediate(resolve)); c.cancel();
  body.resolve({ code: 'AUTH_REQUIRED' }); await running;
  assert.deepEqual(c.states.map(s => s.status), ['loading']); assert.deepEqual(e.events, []);
});

test('Session loss/account change and route isolation during a read never publish the old article', async () => {
  for (const change of ['logout', 'account', 'isolation']) {
    const pending = deferred(), e = environment({ respond: () => pending.promise }), c = e.controller(); const running = c.load(target);
    if (change === 'isolation') e.window.location = new URL('https://test.me-nu.kr/pivot/demo');
    else e.setSession(change === 'logout' ? anonymous : { ...active, user: { ...active.user, id: 99 } });
    pending.resolve(json(payload())); await running;
    assert.equal(c.states.at(-1).status, change === 'isolation' ? 'unconfigured' : 'error');
    assert.doesNotMatch(e.render(c.states.at(-1)), /data-detail-content/);
  }
});

test('Service bookmark buttons remain disconnected even when the response says bookmarked', async () => {
  const e = environment({ respond: () => json(payload({ myReaction: { bookmarked: true } })) }), c = e.controller(); await c.load(target);
  const html = e.render(c.states.at(-1));
  assert.match(html, /aria-label="저장" aria-disabled="true"/); assert.doesNotMatch(html, /data-demo-bookmark=|aria-pressed=/);
  assert.equal(e.requests.length, 1); assert.equal(e.requests[0].init.method ?? 'GET', 'GET');
});

test('HomeRoute injects stable paired services only for active sessions; card click reaches Detail GET', async () => {
  let state = { status: 'ready', session: active }, memo;
  const Experience = () => null;
  const react = { ...React, useEffect() {}, useId: () => 'fixture', useRef: value => ({ current: value }), useState: value => [value, () => {}],
    useMemo(factory, deps) { if (!memo || deps.some((v, i) => v !== memo.deps[i])) memo = { deps, value: factory() }; return memo.value; } };
  const e = environment({ respond(url) {
    if (url.endsWith('/me/topics')) return json({ topics: [{ id: 9, status: 'active', deletedAt: null }] });
    if (url.endsWith('/topics/9/feed')) return json({ selectedTopicId: 9, sections: [{ contents: [
      { kind: 'content', id: 301, topicId: 9, title: '실제 응답 제목', summary: '피드 요약', imageUrl: null, savedItemId: 777, recommendationId: 888 },
    ] }] });
    return json(payload({ displayMode: 'full_body', body: '실제 응답 본문' }));
  }, overrides: { react, './pivot-session': { usePivotSession: () => ({ state, refresh() {} }) },
    'next/navigation': { useRouter: () => ({ replace() {} }) }, 'next/link': { __esModule: true, default: () => null },
    './pivot-ui': { PivotFrame: () => null, PivotBrand: () => null }, './home-detail-experience': { HomeDetailExperience: Experience },
  } });
  const { HomeRoute } = e.load('components/pivot/home-route.tsx');
  const draw = () => { const tree = HomeRoute(); return tree.props.children?.type === Experience ? tree.props.children.props : null; };
  const first = draw(); state = { ...state }; const again = draw();
  assert.equal(first.homeSource, again.homeSource); assert.equal(first.detailSource, again.detailSource); assert.equal(e.requests.length, 0);
  const home = await first.homeSource.load({ signal: new AbortController().signal });
  let clicked;
  const tree = e.load('components/pivot/home-screen.tsx').HomeCard({ card: home.home.cards[0], demo: false, onViewDetail(t) { clicked = plain(t); } });
  const nodes = node => !node || typeof node !== 'object' ? [] : [node, ...React.Children.toArray(node.props?.children).flatMap(nodes)];
  nodes(tree).find(n => n.props?.['data-home-detail']).props.onClick(); assert.deepEqual(clicked, target);
  const href = e.load('lib/pivot/detail-navigation.ts').detailHref('/pivot/home', clicked), params = new URL(href, 'https://test.me-nu.kr').searchParams;
  const states = [];
  await e.load('lib/pivot/detail.ts').createDetailRequestController(first.detailSource, s => states.push(s))
    .load({ postId: params.get('postId'), contentId: params.get('contentId'), topicId: params.get('topicId') });
  assert.match(e.render(states.at(-1)), /실제 응답 본문/);
  assert.deepEqual(e.requests.map(r => new URL(r.url).pathname), ['/api/v1/me/topics', '/api/v1/topics/9/feed', '/api/v1/contents/301']);
  state = { status: 'ready', session: { ...active, user: { ...active.user, id: 99 } } };
  assert.notEqual(draw().detailSource, first.detailSource);
  for (const status of ['loading', 'error']) { state = { status, session: active }; assert.equal(draw(), null); }
  state = { status: 'ready', session: anonymous }; assert.equal(draw(), null);
  state = { status: 'disconnected', session: null };
  assert.equal(draw().detailSource, e.load('lib/pivot/detail-service.ts').pendingDetailSource);
});

test('HomeDetailExperience never mounts a mismatched Service/Demo data source', async () => {
  for (const kind of ['demo', 'service']) {
    let selected, calls = 0;
    const e = environment({ overrides: {
      'next/navigation': { useSearchParams: () => new URLSearchParams('view=detail&postId=301&contentId=301&topicId=9') },
      './home-controller': { HomeController: () => null }, './my-page-controller': { MyPageController: () => null },
      './saved-controller': { SavedController: () => null },
      './detail-controller': { DetailController({ source }) { selected = source; return null; } },
    } });
    const wrong = { kind: kind === 'service' ? 'demo' : 'service', async load() { calls++; return { status: 'ready' }; } };
    const { HomeDetailExperience } = e.load('components/pivot/home-detail-experience.tsx');
    renderToStaticMarkup(React.createElement(HomeDetailExperience, { homeSource: { kind }, detailSource: wrong }));
    assert.equal(selected.kind, kind); assert.notEqual(selected, wrong);
    assert.equal((await selected.load(target, { signal: new AbortController().signal })).status, 'unconfigured');
    assert.equal(calls, 0); assert.equal(e.requests.length, 0);
  }
});
