const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const root = path.join(__dirname, '..');
const modules = new Map();
function load(file) {
  if (modules.has(file)) return modules.get(file).exports;
  const mod = { exports: {} }; modules.set(file, mod);
  const output = ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
  } }).outputText;
  vm.runInNewContext(output, { module: mod, exports: mod.exports, AbortController, DOMException, URL, URLSearchParams,
    process: { env: {} }, require(name) {
      if (name === 'react' || name === 'react/jsx-runtime') return require(name);
      if (name.endsWith('.css')) return { default: new Proxy({}, { get: (_, key) => String(key) }), __esModule: true };
      const relative = name.startsWith('@/') ? name.slice(2) : path.join(path.dirname(file), name);
      if (name.endsWith('.json')) return JSON.parse(fs.readFileSync(path.join(root, relative), 'utf8'));
      return load(relative + (fs.existsSync(path.join(root, relative + '.tsx')) ? '.tsx' : '.ts'));
    },
  });
  return mod.exports;
}
const { managedInterest, loadManagedInterest } = load('lib/pivot/interest-manage.ts');
const { ApiError } = load('lib/api.ts');
const { InterestScreen } = load('components/pivot/onboarding-screens.tsx');
const session = (id = 7) => ({ authenticated: true, sessionState: 'active', onboardingCompleted: true,
  user: { id, accountType: 'user' }, csrfToken: 'fixture' });
const topic = (changes = {}) => ({ id: 83, code: 'anime', name: '애니메이션', status: 'active', deletedAt: null, subtopicIds: [], ...changes });
const payload = (...topics) => ({ topics });
const signal = () => new AbortController().signal;
const allow = () => {};
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };

test('Only an exactly active, undeleted anime subscription is selected; no first-topic fallback', () => {
  assert.equal(managedInterest(payload(topic())).status, 'ready');
  assert.equal(managedInterest(payload(topic({ id: 2, code: 'movie' }), topic())).status, 'ready');
  for (const value of [payload(), payload(topic({ code: 'movie' })), payload(topic({ code: 'animation' })),
    payload(topic({ code: 'Anime' })), payload(topic({ status: 'deleted' })), payload(topic({ status: 'inactive' })),
    payload(topic({ deletedAt: '2026-11-06T00:00:00Z' })),
    payload(topic({ status: 'deleted' }), topic({ id: 2, code: 'movie' }))]) {
    assert.equal(managedInterest(value).status, 'empty');
  }
});

test('Malformed responses cannot assert an active subscription, even after a valid row', () => {
  for (const value of [null, [], {}, { topics: null }, payload(null), payload(topic({ id: 0 })),
    payload(topic({ id: '83' })), payload(topic({ code: null })), payload(topic({ status: null })),
    payload(topic({ deletedAt: undefined })), payload(topic({ deletedAt: false })), payload(topic(), {})]) {
    assert.throws(() => managedInterest(value), /Invalid interest response/);
  }
});

test('The adapter reuses getMyTopics with its cancellation signal and returns no personal payload', async () => {
  const owner = session(), requestSignal = signal(), calls = [];
  const result = await loadManagedInterest(() => owner, requestSignal, { async getMyTopics(options, init) {
    calls.push({ options, init }); return payload(topic());
  } }, allow);
  assert.equal(JSON.stringify(result), '{"status":"ready"}');
  assert.equal(JSON.stringify(calls[0].options), '{}');
  assert.equal(calls[0].init.signal, requestSignal);
  assert.equal(calls.length, 1);
});

test('Inactive sessions and an unavailable API cannot start a read', async () => {
  let calls = 0;
  const api = { async getMyTopics() { calls++; return payload(topic()); } };
  for (const owner of [null, { ...session(), authenticated: false }, { ...session(), onboardingCompleted: false },
    { ...session(), user: { id: 7, accountType: 'creator' } }, session(0)]) {
    await assert.rejects(loadManagedInterest(() => owner, signal(), api, allow), { name: 'AbortError' });
  }
  const owner = session();
  await assert.rejects(loadManagedInterest(() => owner, signal(), api, () => { throw new Error('disconnected'); }), /disconnected/);
  assert.equal(calls, 0);
});

test('Cancellation prevents the request or discards a late response even if transport ignores abort', async () => {
  const owner = session(), abort = new AbortController(); let calls = 0;
  abort.abort();
  await assert.rejects(loadManagedInterest(() => owner, abort.signal, { async getMyTopics() { calls++; } }, allow), { name: 'AbortError' });
  assert.equal(calls, 0);
  const pending = deferred(), laterAbort = new AbortController();
  const loading = loadManagedInterest(() => owner, laterAbort.signal, { getMyTopics: () => pending.promise }, allow);
  laterAbort.abort(); pending.resolve(payload(topic()));
  await assert.rejects(loading, { name: 'AbortError' });
});

test('Different-user and same-user session replacements discard the previous success and failure', async () => {
  for (const replacement of [null, session(8), session(7)]) {
    for (const fail of [false, true]) {
      let owner = session(); const pending = deferred();
      const loading = loadManagedInterest(() => owner, signal(), { getMyTopics: () => pending.promise }, allow);
      owner = replacement;
      if (fail) pending.reject(new ApiError('old session', 401, 'AUTH_REQUIRED'));
      else pending.resolve(payload(topic()));
      await assert.rejects(loading, { name: 'AbortError' });
    }
  }
});

test('A failed request can retry, while a current 401 retains its authentication status', async () => {
  const owner = session(); let fail = true;
  const api = { async getMyTopics() { if (fail) throw new Error('offline'); return payload(); } };
  await assert.rejects(loadManagedInterest(() => owner, signal(), api, allow), /offline/);
  fail = false;
  assert.equal((await loadManagedInterest(() => owner, signal(), api, allow)).status, 'empty');
  const unauthorized = new ApiError('expired', 401, 'AUTH_REQUIRED');
  await assert.rejects(loadManagedInterest(() => owner, signal(), { async getMyTopics() { throw unauthorized; } }, allow), error => error === unauthorized);
});

const props = selected => ({ selected, onToggle() {}, onNext() {}, busy: false });
const render = props => renderToStaticMarkup(React.createElement(InterestScreen, props));
test('Management shows selected or unsubscribed from data, with no subscription controls in either state', () => {
  for (const selected of [true, false]) {
    const html = render({ ...props(selected), manage: { onBack() {} } });
    assert.match(html, /aria-label="뒤로가기"/);
    assert.match(html, /추가 예정/);
    assert.doesNotMatch(html, /type="checkbox"|애니메이션 선택하고 시작하기/);
    if (selected) {
      assert.match(html, /class="interestCard selected fixedCard"/);
      assert.match(html, /선택중/); assert.match(html, /이미 애니메이션을 탐색중입니다/);
      assert.doesNotMatch(html, /미가입/);
    } else {
      assert.doesNotMatch(html, /선택중|이미 애니메이션|interestCard selected/);
      assert.match(html, /미가입/); assert.match(html, /가입된 애니메이션 관심 분야가 없어요/);
    }
  }
});

test('Onboarding retains its interactive selection, artwork and start button in both states', () => {
  for (const selected of [true, false]) {
    const html = render(props(selected));
    assert.match(html, /type="checkbox"/);
    assert.match(html, /분야를 선택한 뒤 취향을 고를 수 있어요/);
    assert.match(html, /애니메이션 선택하고 시작하기/);
    assert.match(html, selected ? /선택 완료/ : /선택 가능/);
    assert.doesNotMatch(html, /선택중|미가입|aria-label="뒤로가기"/);
  }
});

// Exercise the route's state, effect cleanup and handlers without starting a server.
// Layout cleanups run before passive effects, as on a committed session change.
function routeHarness(initialSession = session()) {
  let context = { status: 'ready', session: initialSession }, cursor = 0;
  const hooks = [], effects = [], layouts = [], requests = [], routes = [];
  const router = { replace: href => routes.push(href) };
  const refresh = () => { context = { status: 'loading', session: null }; };
  const useEffect = queue => (setup, deps) => {
    const index = cursor++, previous = hooks[index];
    if (previous && deps.every((dep, i) => Object.is(dep, previous.deps[i]))) return;
    queue.push(() => { previous?.cleanup?.(); hooks[index] = { deps, cleanup: setup() }; });
  };
  const react = { ...React,
    useRef(initial) { return hooks[cursor++] ??= { current: initial }; },
    useState(initial) {
      const hook = hooks[cursor++] ??= { value: initial };
      return [hook.value, next => { hook.value = typeof next === 'function' ? next(hook.value) : next; }];
    },
    useEffect: useEffect(effects), useLayoutEffect: useEffect(layouts),
  };
  const mod = { exports: {} };
  const code = ts.transpileModule(fs.readFileSync(path.join(root, 'components/pivot/interest-manage-route.tsx'), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
  }).outputText;
  const dependencies = {
    react, 'react/jsx-runtime': require('react/jsx-runtime'),
    'next/link': { __esModule: true, default: props => React.createElement('a', props) },
    'next/navigation': { useRouter: () => router },
    '@/lib/api': { ApiError }, '@/lib/pivot/services': load('lib/pivot/services.ts'),
    '@/lib/pivot/interest-manage': { loadManagedInterest: (readSession, requestSignal) => loadManagedInterest(readSession, requestSignal, {
      getMyTopics() { const pending = deferred(); requests.push({ ...pending, signal: requestSignal }); return pending.promise; },
    }, allow) },
    './pivot-session': { usePivotSession: () => ({ state: context, refresh }) },
    './onboarding-screens': { InterestScreen }, './pivot-ui': load('components/pivot/pivot-ui.tsx'),
    './pivot.module.css': { __esModule: true, default: {} },
  };
  vm.runInNewContext(code, { module: mod, exports: mod.exports, AbortController, require(name) {
    assert.ok(name in dependencies, 'Known route dependency: ' + name); return dependencies[name];
  } });
  return { requests, routes,
    setSession(value, status = 'ready') { context = { status, session: value }; },
    draw() {
      cursor = 0;
      const tree = mod.exports.InterestManageRoute();
      for (const queue of [layouts, effects]) for (const run of queue.splice(0)) run();
      return tree;
    },
    unmount() { for (const hook of hooks) hook.cleanup?.(); },
  };
}
const nextTurn = () => new Promise(resolve => setImmediate(resolve));
const descendants = node => !node || typeof node !== 'object' ? [] : [node, ...React.Children.toArray(node.props?.children).flatMap(descendants)];
const markup = harness => renderToStaticMarkup(harness.draw());

test('Route presents loading, server-backed selection and MY PAGE back navigation', async () => {
  const route = routeHarness();
  const loading = markup(route);
  assert.match(loading, /관심 분야를 불러오고 있어요/);
  assert.match(loading, /aria-busy="true"/);
  assert.match(loading, /href="\/pivot\/home\?view=my"/);
  assert.doesNotMatch(loading, /선택중/);
  route.requests[0].resolve(payload(topic())); await nextTurn();
  const screen = route.draw();
  assert.match(renderToStaticMarkup(screen), /선택중/);
  screen.props.manage.onBack();
  assert.deepEqual(route.routes, ['/pivot/home?view=my']);
  route.unmount();
});

test('Route shows empty, deleted and non-anime results as unsubscribed', async () => {
  for (const value of [payload(), payload(topic({ status: 'deleted' })), payload(topic({ deletedAt: '2026-11-06' })), payload(topic({ code: 'movie' }))]) {
    const route = routeHarness(); route.draw();
    route.requests[0].resolve(value); await nextTurn();
    const html = markup(route);
    assert.match(html, /미가입/); assert.doesNotMatch(html, /선택중/);
    route.unmount();
  }
});

test('Route errors are generic and retry clears the error before reading again', async () => {
  const route = routeHarness(); route.draw();
  route.requests[0].reject(new Error('private-server-detail')); await nextTurn();
  const tree = route.draw(), html = renderToStaticMarkup(tree);
  assert.match(html, /role="alert"/); assert.doesNotMatch(html, /private-server-detail|선택중/);
  const retry = descendants(tree).find(node => node.type === 'button');
  assert.equal(retry.props.children, '다시 시도'); retry.props.onClick();
  assert.match(markup(route), /관심 분야를 불러오고 있어요/);
  assert.equal(route.requests.length, 2);
  assert.equal(route.requests[0].signal.aborted, true);
  route.requests[1].resolve(payload(topic())); await nextTurn();
  assert.match(markup(route), /선택중/); route.unmount();
});

test('Current 401 and session expiry return to login without showing selected', async () => {
  const route = routeHarness(); route.draw();
  route.requests[0].reject(new ApiError('expired', 401, 'AUTH_REQUIRED')); await nextTurn();
  assert.deepEqual(route.routes, ['/pivot/login']);
  route.setSession(null);
  assert.doesNotMatch(markup(route), /선택중/);
  assert.equal(route.routes.at(-1), '/pivot/login');
  assert.equal(route.requests.length, 1); route.unmount();
});

test('Route hides prior selection on different-user and same-user replacement, cancelling stale success and 401', async () => {
  for (const replacement of [session(8), session(7)]) {
    const route = routeHarness(); route.draw();
    route.requests[0].resolve(payload(topic())); await nextTurn();
    assert.match(markup(route), /선택중/);
    route.setSession(replacement);
    assert.match(markup(route), /관심 분야를 불러오고 있어요/);
    assert.equal(route.requests[0].signal.aborted, true);
    const old = route.requests[1];
    route.setSession(session(9));
    assert.doesNotMatch(markup(route), /선택중/);
    assert.equal(old.signal.aborted, true);
    route.requests[2].resolve(payload()); await nextTurn();
    old.resolve(payload(topic())); await nextTurn();
    assert.match(markup(route), /미가입/);
    route.setSession(session(10)); route.draw();
    const old401 = route.requests[3];
    route.setSession(session(11)); route.draw();
    route.requests[4].resolve(payload()); await nextTurn();
    old401.reject(new ApiError('old', 401, 'AUTH_REQUIRED')); await nextTurn();
    assert.match(markup(route), /미가입/);
    assert.deepEqual(route.routes, []); route.unmount();
  }
});

test('Leaving during loading aborts the read and prevents a late 401 redirect', async () => {
  const route = routeHarness();
  const back = descendants(route.draw()).find(node => node.props?.href === '/pivot/home?view=my');
  assert.ok(back);
  route.unmount();
  assert.equal(route.requests[0].signal.aborted, true);
  route.requests[0].reject(new ApiError('old', 401, 'AUTH_REQUIRED')); await nextTurn();
  assert.deepEqual(route.routes, []);
});
