const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const root = path.join(__dirname, '..');
const plain = value => JSON.parse(JSON.stringify(value));
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const session = (id = 7) => ({ authenticated: true, sessionState: 'active', onboardingCompleted: true, user: { id, accountType: 'user' }, csrfToken: 'fixture' });
const item = (id = 41, topicId = 83) => ({ contentId: id, topicId, title: `활동 콘텐츠 ${id}`, summary: '응답 요약', imageUrl: null,
  sourceName: '출처', productionType: 'human', publishedAt: null, subtopicNames: [], likedAt: '2026-11-09T01:00:00Z', viewedAt: '2026-11-09T02:00:00Z' });
function runtime(respond = () => ({ items: [item()], nextCursor: null })) {
  const modules = new Map(), calls = [], events = [];
  const browser = new EventTarget(); browser.location = { origin: 'http://localhost:3194' };
  browser.addEventListener('api:unauthorized', () => events.push('401'));
  const context = vm.createContext({ AbortController, URL, URLSearchParams, Headers, Response, Event, window: browser,
    process: { env: { NEXT_PUBLIC_API_BASE_URL: 'http://localhost:8194', NEXT_PUBLIC_PIVOT_ENABLE_LOCAL_API: 'true' } },
    fetch: async (url, init) => {
      calls.push({ url, init });
      const body = await respond(new URL(url), init);
      return body instanceof Response ? body : new Response(JSON.stringify(body), { status: 200 });
    },
  });
  function load(file) {
    file = file.replaceAll('\\', '/');
    if (modules.has(file)) return modules.get(file).exports;
    const mod = { exports: {} }; modules.set(file, mod);
    const js = ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), { compilerOptions: {
      module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
    } }).outputText;
    const requireModule = name => {
      if (name === 'react' || name === 'react/jsx-runtime') return require(name);
      if (name.endsWith('.module.css')) return { default: new Proxy({}, { get: (_, key) => String(key) }), __esModule: true };
      const relative = name.startsWith('@/') ? name.slice(2) : path.join(path.dirname(file), name);
      return load(relative + (fs.existsSync(path.join(root, relative + '.tsx')) ? '.tsx' : '.ts'));
    };
    vm.runInContext(`(function(module,exports,require){${js}\n})`, context)(mod, mod.exports, requireModule);
    return mod.exports;
  }
  return { load, calls, events };
}
function setup(respond) {
  const env = runtime(respond); let current = session();
  const source = env.load('lib/pivot/my-activity-service.ts').createServiceActivitySource(() => current);
  return { ...env, source, change(next) { current = next; }, controller(tab = 'likes') {
    const states = []; const control = env.load('lib/pivot/my-activity.ts').createActivityRequestController(source, tab, state => states.push(state));
    return { ...control, states };
  } };
}
for (const tab of ['likes', 'recent-views']) {
  test(`${tab}: page-one eyebrow stays verbatim and never falls back to source or taxonomy`, async () => {
    const eyebrow = '  HOME 첫 라벨 & <소식>  ';
    const values = [eyebrow, null, undefined, '', '  ', 42, { fake: 'text' }];
    const env = setup(() => ({ items: values.map((eyebrow, i) => ({ ...item(41 + i), eyebrow, subtopicNames: ['액션'] })), nextCursor: null }));
    const control = env.controller(tab); await control.load();
    const cards = control.states.at(-1).page.cards;
    assert.equal(cards[0].eyebrow, eyebrow);
    assert.ok(cards.slice(1).every(card => card.eyebrow === undefined));
    assert.ok(cards.every(card => card.sourceName === '출처' && card.imageHasEmbeddedCopy === undefined));
    const { MyActivityScreen } = env.load('components/pivot/my-activity-screen.tsx');
    const html = renderToStaticMarkup(React.createElement(MyActivityScreen, {
      state: control.states.at(-1), tab, demo: false, onTab() {}, onMy() {}, onHome() {}, onSaved() {}, onViewDetail() {}, onRetry() {}, onMore() {},
    }));
    assert.equal((html.match(/data-saved-eyebrow/g) || []).length, 1);
    assert.match(html, /  HOME 첫 라벨 &amp; &lt;소식&gt;  /);
    assert.doesNotMatch(html, /출처|액션|<소식>/);
  });
  test(`${tab}: real API client maps cards and preserves all Topic identities without savedItemId`, async () => {
    const env = setup(() => ({ items: [item(), item(42, 99)], nextCursor: 'opaque +/=' })); const control = env.controller(tab);
    await control.load(); const state = control.states.at(-1);
    assert.equal(state.status, 'ready'); assert.deepEqual(plain(state.page.cards.map(card => [card.contentId, card.topicId])), [['41', 83], ['42', 99]]);
    assert.equal(state.page.nextCursor, 'opaque +/='); assert.ok(state.page.cards.every(card => !('savedItemId' in card)));
    const { url, init } = env.calls[0]; assert.equal(new URL(url).pathname, `/api/v1/users/me/${tab}`);
    assert.equal(new URL(url).searchParams.get('limit'), '20'); assert.equal(new URL(url).searchParams.has('topicId'), false);
    assert.equal(init.credentials, 'include'); assert.equal(init.cache, 'no-store'); assert.ok(init.signal);
  });
  test(`${tab}: empty is a successful response, not a Mock fallback`, async () => {
    const env = setup(() => ({ items: [], nextCursor: null })); const control = env.controller(tab); await control.load();
    assert.equal(control.states.at(-1).status, 'ready'); assert.equal(control.states.at(-1).page.cards.length, 0);
  });
  test(`${tab}: initial error retries without exposing server messages`, async () => {
    let fail = true; const env = setup(() => fail ? new Response(JSON.stringify({ message: 'private server text' }), { status: 503 }) : { items: [item()], nextCursor: null });
    const control = env.controller(tab); await control.load(); assert.equal(control.states.at(-1).status, 'error');
    assert.doesNotMatch(control.states.at(-1).message, /private/); fail = false; await control.load(); assert.equal(control.states.at(-1).status, 'ready');
  });
}
test('cursor pagination is on demand, single flight, deduplicated and keeps data on retry', async () => {
  const gate = deferred(); let moreCalls = 0, fail = true;
  const env = setup(async url => {
    if (!url.searchParams.has('cursor')) return { items: [item()], nextCursor: 'opaque +/=' };
    assert.equal(url.searchParams.get('cursor'), 'opaque +/='); moreCalls++;
    await gate.promise;
    if (fail) return new Response('{}', { status: 503 });
    return { items: [item(), item(42), item(42)], nextCursor: null };
  });
  const control = env.controller(); await control.load(); assert.equal(env.calls.length, 1);
  const first = control.loadMore(); await control.loadMore(); assert.equal(moreCalls, 1);
  assert.equal(control.states.at(-1).loadingMore, true); gate.resolve(); await first;
  assert.equal(control.states.at(-1).page.cards.length, 1); assert.ok(control.states.at(-1).moreError);
  fail = false; await control.loadMore(); assert.deepEqual(plain(control.states.at(-1).page.cards.map(card => card.contentId)), ['41', '42']);
  await control.loadMore(); assert.equal(moreCalls, 2);
});
test('duplicate-only and empty pages still follow advancing server cursors', async () => {
  const env = setup(url => ({ items: url.searchParams.get('cursor') === 'b' ? [] : [item()], nextCursor: { null: 'a', a: 'b', b: 'c', c: null }[url.searchParams.get('cursor')] }));
  const control = env.controller(); await control.load(); await control.loadMore(); await control.loadMore(); await control.loadMore();
  assert.equal(control.states.at(-1).page.cards.length, 1); assert.equal(control.states.at(-1).page.nextCursor, null);
});
test('repeated/cyclic cursors fail without appending a partial page or looping', async () => {
  for (const cyclic of [false, true]) {
    const env = setup(url => ({ items: [item(url.searchParams.has('cursor') ? 42 : 41)], nextCursor: !url.searchParams.has('cursor') ? 'a' : cyclic && url.searchParams.get('cursor') === 'a' ? 'b' : 'a' }));
    const control = env.controller(); await control.load(); await control.loadMore(); if (cyclic) await control.loadMore();
    assert.ok(control.states.at(-1).moreError); assert.equal(env.calls.length, cyclic ? 3 : 2);
  }
});
test('invalid identity, timestamp, title, response and cursor fail closed', async () => {
  for (const body of [{ items: [item(0)], nextCursor: null }, { items: [{ ...item(), title: '' }], nextCursor: null },
    { items: [{ ...item(), likedAt: null }], nextCursor: null }, { items: [item()], nextCursor: '' }, { items: null, nextCursor: null }]) {
    const env = setup(() => body), control = env.controller(); await control.load(); assert.equal(control.states.at(-1).status, 'error');
  }
});
test('both API wrappers support optional Topic and encode cursors', async () => {
  const env = runtime(); const api = env.load('lib/api.ts');
  for (const method of [api.getMyLikes, api.getMyRecentViews]) await method({ topicId: 83, cursor: 'opaque +/=', limit: 8 });
  for (const call of env.calls) { const params = new URL(call.url).searchParams; assert.equal(params.get('topicId'), '83'); assert.equal(params.get('cursor'), 'opaque +/='); assert.equal(params.get('limit'), '8'); }
});
test('active 401 retains the existing unauthorized event policy', async () => {
  const env = setup(() => new Response('{"code":"AUTH_REQUIRED"}', { status: 401 })); const control = env.controller();
  await control.load(); assert.deepEqual(env.events, ['401']); assert.equal(control.states.at(-1).status, 'error');
});
test('cancelled requests suppress late success, failure and 401 (including session replacement)', async () => {
  for (const outcome of ['success', 'failure', '401']) {
    const gate = deferred(); const env = setup(() => gate.promise), control = env.controller(); const pending = control.load();
    control.cancel(); env.change(session(8)); const before = control.states.length;
    if (outcome === 'failure') gate.reject(new Error('late')); else gate.resolve(outcome === '401' ? new Response('{}', { status: 401 }) : { items: [item()], nextCursor: null });
    await pending; assert.equal(control.states.length, before); assert.deepEqual(env.events, []);
  }
});
test('session object change, logout and user change reject late data and clear retained pages', async () => {
  for (const next of [session(), session(8), null]) {
    const gate = deferred(); const env = setup(url => url.searchParams.has('cursor') ? gate.promise : { items: [item()], nextCursor: 'more' });
    const control = env.controller(); await control.load(); const pending = control.loadMore(); env.change(next);
    gate.resolve({ items: [item(42)], nextCursor: null }); await pending;
    assert.equal(control.states.at(-1).status, 'loading'); assert.equal(control.states.at(-1).page, undefined);
    const count = env.calls.length; await control.load(); assert.equal(env.calls.length, count);
  }
});
test('a newer request supersedes a slow request on the same controller', async () => {
  const gate = deferred(); let count = 0; const env = setup(() => ++count === 1 ? gate.promise : { items: [item(42)], nextCursor: null });
  const control = env.controller(); const first = control.load(); await control.load();
  gate.resolve({ items: [item()], nextCursor: null }); await first;
  assert.equal(control.states.at(-1).page.cards[0].contentId, '42');
});
test('UI shows both exact Figma tabs, distinct empty states, errors and isolated Demo', () => {
  const env = runtime(), { MyActivityScreen } = env.load('components/pivot/my-activity-screen.tsx');
  const render = (state, tab = 'likes', demo = false) => renderToStaticMarkup(React.createElement(MyActivityScreen, {
    state, tab, demo, onTab() {}, onMy() {}, onHome() {}, onSaved() {}, onViewDetail() {}, onRetry() {}, onMore() {},
  }));
  for (const tab of ['likes', 'recent-views']) {
    const empty = render({ status: 'ready', page: { origin: 'service', cards: [], nextCursor: null }, loadingMore: false }, tab);
    assert.equal((empty.match(/role="tab"/g) || []).length, 2); assert.match(empty, /좋아요한 콘텐츠/); assert.match(empty, /최근 본 콘텐츠/);
    assert.match(empty, new RegExp(tab === 'likes' ? '좋아요한 콘텐츠가 아직 없어요' : '최근 30일 동안 본 콘텐츠가 없어요'));
    assert.match(empty, /콘텐츠 보러가기/); assert.match(empty, /aria-current="page"[^>]*><span>MY/);
  }
  assert.match(render({ status: 'loading' }), /불러오고 있어요/);
  assert.match(render({ status: 'error', message: '실패' }), /다시 시도/);
  const filteredPage = render({ status: 'ready', page: { origin: 'service', cards: [], nextCursor: 'next' }, loadingMore: false });
  assert.match(filteredPage, /다음 목록을 확인해주세요/); assert.match(filteredPage, /더 보기/); assert.doesNotMatch(filteredPage, /아직 없어요/);
  const ready = { status: 'ready', page: { origin: 'service', cards: [{ postId: '41', contentId: '41', topicId: 83, title: '<unsafe>' }], nextCursor: null }, loadingMore: false };
  assert.match(render(ready), /&lt;unsafe&gt;/); assert.match(render(ready), /data-activity-content="41"/);
  assert.doesNotMatch(render(ready, 'likes', true), /data-activity-content|&lt;unsafe&gt;/);
  assert.match(render(ready, 'likes', true), /Demo에서는 실제/);
});
