const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const React = require('react');
const root = path.resolve(__dirname, '..');
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const flush = () => new Promise(resolve => setImmediate(resolve));

function runtime(fetch = () => { throw Error('Unexpected HTTP request'); }) {
  let now = 0, id = 0;
  const timers = new Map(), cache = new Map(), redirects = [], events = [];
  const instances = new Map(), contexts = new Map(), effects = [];
  let hooks = [], cursor = 0;
  const same = (a, b) => a && b && a.length === b.length && a.every((value, i) => Object.is(value, b[i]));
  const react = { ...React,
    useState(initial) { const slot = hooks[cursor++] ??= { value: typeof initial === 'function' ? initial() : initial };
      return [slot.value, value => { slot.value = typeof value === 'function' ? value(slot.value) : value; }]; },
    useRef(initial) { return hooks[cursor++] ??= { current: initial }; },
    useReducer(reduce, initial, initialize) { const [value, set] = react.useState(() => initialize ? initialize(initial) : initial); return [value, action => set(old => reduce(old, action))]; },
    useCallback(fn, deps) { const i = cursor++; if (!same(hooks[i]?.deps, deps)) hooks[i] = { value: fn, deps }; return hooks[i].value; },
    useContext(context) { return contexts.get(context); },
    useEffect(setup, deps) { const i = cursor++, old = hooks[i]; if (same(old?.deps, deps)) return;
      const slot = hooks[i] = { deps, setup }; effects.push(() => { old?.cleanup?.(); slot.cleanup = setup(); }); },
  };
  const browser = new EventTarget();
  browser.location = { origin: 'https://checking.me-nu.kr', search: '', assign: url => redirects.push(url) };
  browser.addEventListener('api:unauthorized', event => events.push(event.type));
  const router = { replace: url => redirects.push(url) };
  const context = vm.createContext({ AbortController, Headers, Response, URL, URLSearchParams, Event, DOMException,
    queueMicrotask, fetch, process: { env: { NEXT_PUBLIC_API_BASE_URL: 'https://api.checking.me-nu.kr' } },
    window: browser,
    setTimeout(fn, ms) { const key = ++id; timers.set(key, { at: now + ms, fn }); return key; },
    clearTimeout(key) { timers.delete(key); },
  });
  function load(relative) {
    const file = path.resolve(root, relative);
    if (cache.has(file)) return cache.get(file).exports;
    const mod = { exports: {} }; cache.set(file, mod);
    const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
    }).outputText;
    vm.runInContext(`(function(require,module,exports){${code}\n})`, context)(name => {
      if (name === 'react') return react;
      if (name === 'react/jsx-runtime') return require(name);
      if (name === 'next/navigation') return { usePathname: () => '/pivot/login', useRouter: () => router };
      if (name.endsWith('.css')) return { default: new Proxy({}, { get: (_, key) => key }), __esModule: true };
      const target = name.startsWith('@/') ? path.join(root, name.slice(2)) : path.resolve(path.dirname(file), name);
      return load(target + (fs.existsSync(target + '.tsx') ? '.tsx' : '.ts'));
    }, mod, mod.exports);
    return mod.exports;
  }
  return { load, redirects, events, timers, browser,
    draw(component, props = {}) { hooks = instances.get(component) ?? []; instances.set(component, hooks); cursor = 0;
      const view = component(props); if (view?.props?.value) contexts.set(view.type, view.props.value);
      effects.splice(0).forEach(effect => effect()); return view; },
    unmount(component) { for (const hook of instances.get(component) ?? []) hook?.cleanup?.(); instances.delete(component); },
    async tick(ms = 15000) {
    now += ms;
    for (const [key, timer] of [...timers]) if (timer.at <= now) { timers.delete(key); timer.fn(); }
    await flush();
  } };
}
const target = { postId: '41', contentId: '41', topicId: 7 };
const views = [
  ['DETAIL', 'detail', 'createDetailRequestController', { status: 'ready', article: { ...target, origin: 'service', title: 'fixture', sections: [{ paragraphs: ['body'] }] } }],
  ['SAVE', 'saved', 'createSavedRequestController', { status: 'ready', saved: { origin: 'service', cards: [{ ...target, title: 'fixture' }] } }],
  ['MY', 'my-page', 'createMyPageRequestController', { status: 'ready', profile: { origin: 'service', nickname: 'fixture', maskedEmail: 'f***@example.com' } }],
];
for (const [name, file, factory, ready] of views) {
  test(`${name}: permanent pending aborts at 15 seconds, then retry succeeds without a late overwrite`, async () => {
    const env = runtime(), pending = deferred(), states = []; let signal, calls = 0;
    const source = { kind: 'service', load(...args) { signal = args.at(-1).signal; return ++calls === 1 ? pending.promise : Promise.resolve(ready); } };
    const controller = env.load(`lib/pivot/${file}.ts`)[factory](source, state => states.push(state));
    const old = controller.load(target);
    await env.tick(14999); assert.equal(states.at(-1).status, 'loading');
    await env.tick(1); assert.equal(states.at(-1).status, 'error'); assert.equal(signal.aborted, true);
    await controller.load(target); assert.equal(states.at(-1).status, 'ready');
    const count = states.length; pending.resolve(ready); await old;
    assert.equal(states.length, count); assert.equal(env.timers.size, 0);
  });
  test(`${name}: normal response and network error settle; explicit retry clears the error`, async () => {
    const env = runtime(), states = []; let fail = true;
    const source = { kind: 'service', async load() { if (fail) throw Error('fixture failure'); return ready; } };
    const controller = env.load(`lib/pivot/${file}.ts`)[factory](source, state => states.push(state));
    await controller.load(target); assert.equal(states.at(-1).status, 'error');
    fail = false; await controller.load(target); assert.equal(states.at(-1).status, 'ready');
    await env.tick(); assert.equal(states.at(-1).status, 'ready'); assert.equal(env.timers.size, 0);
  });
  for (const failure of [false, true]) test(`${name}: leaving the old account/screen aborts and ignores late ${failure ? 'failure' : 'success'}`, async () => {
    const env = runtime(), pending = deferred(), states = []; let signal;
    const make = env.load(`lib/pivot/${file}.ts`)[factory];
    const old = make({ kind: 'service', load(...args) { signal = args.at(-1).signal; return pending.promise; } }, state => states.push(state));
    const request = old.load(target); old.cancel();
    assert.equal(signal.aborted, true);
    await make({ kind: 'service', async load() { return ready; } }, state => states.push(state)).load(target);
    const count = states.length;
    if (failure) pending.reject(Error('late')); else pending.resolve(ready);
    await request; await env.tick();
    assert.equal(states.length, count); assert.equal(states.at(-1).status, 'ready');
  });
}

test('shared session bootstrap expires and can retry without accepting an old CSRF token', async () => {
  const pending = deferred(), requests = [];
  const env = runtime(async (url, init) => {
    requests.push({ url, init });
    if (requests.length === 1) return pending.promise;
    return Response.json({ csrfToken: 'new-token' });
  });
  const api = env.load('lib/api.ts');
  const first = api.getSession(), duplicate = api.getSession();
  const outcome = Promise.allSettled([first, duplicate]);
  await env.tick();
  assert.equal(requests[0].init.signal?.aborted, true);
  assert.equal(requests.length, 1);
  assert.ok((await outcome).every(result => result.status === 'rejected'));
  await api.getSession();
  pending.resolve(Response.json({ csrfToken: 'old-token' })); await flush();
  await api.apiRequest('/fixture-write', { method: 'POST' });
  assert.equal(requests.at(-1).init.headers.get('X-CSRF-Token'), 'new-token');
});

test('OAuth preparation cannot remain pending forever or redirect after its deadline', async () => {
  const pending = deferred(), requests = [];
  const env = runtime(async (url, init) => { requests.push({ url, init }); return pending.promise; });
  let settled = false;
  const request = env.load('lib/pivot/services.ts').startSnsLogin('kakao').catch(() => { settled = true; });
  await env.tick();
  assert.equal(settled, true);
  assert.equal(requests[0].init.signal?.aborted, true);
  assert.equal(requests.length, 1, 'no POST after the session deadline');
  pending.resolve(Response.json({ csrfToken: 'late' })); await request; await flush();
  assert.deepEqual(env.redirects, []);
});

test('one shared subscriber can leave without aborting another; the final subscriber cancels the transport', async () => {
  const pending = deferred(); let signal, calls = 0;
  const env = runtime(async (_, init) => { calls++; signal = init.signal; return pending.promise; });
  const api = env.load('lib/api.ts'), a = new AbortController(), b = new AbortController();
  const first = api.getSession({ signal: a.signal, shared: true });
  const second = api.getSession({ signal: b.signal, shared: true });
  const results = Promise.allSettled([first, second]);
  a.abort(); await flush(); assert.equal(signal.aborted, false); assert.equal(calls, 1);
  b.abort(); await results; await flush();
  assert.equal(signal.aborted, true); assert.equal(env.timers.size, 0);
});

test('Strict Mode cleanup/re-subscription shares one bootstrap cookie issuance', async () => {
  const pending = deferred(); let signal, calls = 0;
  const env = runtime(async (_, init) => { calls++; signal = init.signal; return pending.promise; });
  const api = env.load('lib/api.ts'), a = new AbortController(), b = new AbortController();
  const first = api.getSession({ signal: a.signal, shared: true }).catch(() => {});
  a.abort();
  const second = api.getSession({ signal: b.signal, shared: true });
  await flush(); assert.equal(calls, 1); assert.equal(signal.aborted, false);
  pending.resolve(Response.json({ csrfToken: 'one-cookie' }));
  await first; assert.equal((await second).csrfToken, 'one-cookie');
  assert.equal(env.timers.size, 0);
});

test('fresh and dialog-owned session reads stay independent of shared bootstrap cancellation', async () => {
  const pending = deferred(), requests = [];
  const env = runtime(async (_, init) => { requests.push(init); return pending.promise.then(response => response.clone()); });
  const api = env.load('lib/api.ts'), owner = new AbortController(), dialog = new AbortController();
  const shared = api.getSession({ signal: owner.signal, shared: true }).catch(() => {});
  const fresh = api.getSession({ fresh: true });
  const independent = api.getSession({ signal: dialog.signal });
  owner.abort(); await shared; await flush();
  assert.equal(requests.length, 3); assert.equal(requests[0].signal.aborted, true);
  assert.equal(requests[1].signal, undefined); assert.equal(requests[2].signal, dialog.signal);
  assert.equal(dialog.signal.aborted, false);
  pending.resolve(Response.json({ csrfToken: 'independent' })); await fresh; await independent;
});

test('a timed-out late 401 cannot expire a recovered session', async () => {
  const pending = deferred(); let calls = 0;
  const env = runtime(async () => ++calls === 1 ? pending.promise : Response.json({ csrfToken: 'current' }));
  const api = env.load('lib/api.ts');
  const old = api.getSession().catch(() => {}); await env.tick(); await old;
  await api.getSession(); pending.resolve(Response.json({ code: 'AUTH_REQUIRED' }, { status: 401 })); await flush();
  assert.deepEqual(env.events, []);
});

test('OAuth POST timeout reports uncertainty, sends once, ignores a late URL, and permits an explicit new attempt', async () => {
  const pending = deferred(), requests = [];
  const authorizationUrl = 'https://kauth.kakao.com/oauth/authorize?response_type=code&client_id=fixture&state=fixture&redirect_uri=https%3A%2F%2Fapi.checking.me-nu.kr%2Fapi%2Fv1%2Fauth%2Flogin%2Fkakao%2Fcallback';
  let posts = 0;
  const env = runtime(async (url, init) => {
    requests.push({ url, init });
    if (init.method === 'POST') return ++posts === 1 ? pending.promise : Response.json({ authorizationUrl });
    return Response.json({ csrfToken: 'owner-token' });
  });
  const services = env.load('lib/pivot/services.ts');
  const first = services.startSnsLogin('kakao').catch(error => error);
  await flush(); assert.equal(posts, 1); const oldSignal = requests.at(-1).init.signal;
  await env.tick(); assert.match((await first).message, /완료 여부/); assert.equal(oldSignal.aborted, true);
  assert.equal(posts, 1); assert.deepEqual(env.redirects, []);
  await services.startSnsLogin('kakao'); assert.equal(posts, 2); assert.equal(env.redirects.length, 1);
  pending.resolve(Response.json({ authorizationUrl })); await flush(); assert.equal(env.redirects.length, 1);
  assert.equal(requests.filter(r => r.init.method === 'POST')[0].init.headers.get('X-CSRF-Token'), 'owner-token');
});

test('unrelated GET/POST calls have no deadline or automatic resubmission', async () => {
  const pending = deferred(), requests = [];
  const env = runtime(async (url, init) => { requests.push({ url, init }); return pending.promise; });
  const api = env.load('lib/api.ts'); let settled = 0;
  const reads = [api.apiRequest('/fixture-get'), api.apiRequest('/fixture-post', { method: 'POST', headers: { 'X-CSRF-Token': 'explicit' } })];
  reads.forEach(promise => promise.then(() => settled++));
  await env.tick(60000); assert.equal(settled, 0); assert.equal(env.timers.size, 0); assert.equal(requests.length, 2);
  assert.ok(requests.every(r => !r.init.signal));
  pending.resolve(Response.json({ ok: true })); await Promise.all(reads);
});

const nodes = node => !node || typeof node !== 'object' ? [] : [node, ...React.Children.toArray(node.props?.children).flatMap(nodes)];
function sessionUI(env) {
  const Provider = env.load('components/pivot/pivot-session.tsx').PivotSessionProvider({ children: null }).type;
  const Login = env.load('components/pivot/login-screen.tsx').LoginScreen;
  const draw = () => { const context = env.draw(Provider).props.value; return { context, view: env.draw(Login) }; };
  const button = (view, className) => nodes(view).find(node => node.type === 'button' && node.props.className === className);
  return { Provider, Login, draw, button };
}

test('real session provider and LoginScreen recover both buttons and expose retry after bootstrap timeout', async () => {
  const pending = deferred(); let calls = 0, oldSignal;
  const env = runtime(async (_, init) => { if (++calls === 1) { oldSignal = init.signal; return pending.promise; }
    return Response.json({ authenticated: false, sessionState: 'anonymous', user: null, csrfToken: 'retry' }); });
  const ui = sessionUI(env);
  const before = ui.draw(); assert.equal(ui.button(before.view, 'kakao').props.disabled, true);
  await env.tick(); const failed = ui.draw(); assert.equal(failed.context.state.status, 'error'); assert.equal(oldSignal.aborted, true);
  assert.equal(ui.button(failed.view, 'kakao').props.disabled, false); assert.equal(ui.button(failed.view, 'naver').props.disabled, false);
  const retry = nodes(failed.view).find(node => node.type === 'button' && node.props.children === '로그인 상태 다시 확인');
  assert.ok(retry); retry.props.onClick(); ui.draw(); await flush();
  assert.equal(ui.draw().context.state.session.csrfToken, 'retry');
  pending.resolve(Response.json({ csrfToken: 'old' })); await flush();
  assert.equal(ui.draw().context.state.session.csrfToken, 'retry');
  env.unmount(ui.Login); env.unmount(ui.Provider);
});

test('LoginScreen releases a timed-out OAuth lock and ignores the previous attempt after navigation/account change', async () => {
  const pending = deferred(); let postSignal;
  const env = runtime(async (_, init) => {
    if (init.method === 'POST') { postSignal = init.signal; return pending.promise; }
    return Response.json({ authenticated: false, sessionState: 'anonymous', user: null, csrfToken: 'owner' });
  });
  const ui = sessionUI(env); ui.draw(); await flush();
  let state = ui.draw(); const request = ui.button(state.view, 'kakao').props.onClick(); await flush();
  assert.equal(ui.button(ui.draw().view, 'naver').props.disabled, true);
  await env.tick(); await request; state = ui.draw();
  assert.equal(postSignal.aborted, true); assert.equal(ui.button(state.view, 'naver').props.disabled, false);
  assert.ok(nodes(state.view).some(node => node.props?.children?.includes?.('완료 여부')));
  const next = ui.button(state.view, 'naver').props.onClick(); await flush();
  env.unmount(ui.Login); assert.equal(postSignal.aborted, true);
  pending.resolve(Response.json({ authorizationUrl: 'https://invalid.example' })); await next; await flush();
  assert.deepEqual(env.redirects, []); env.unmount(ui.Provider);
});

test('session owner transition cancels bootstrap and prevents late state/CSRF overwrite', async () => {
  const pending = deferred(); let signal;
  const env = runtime(async (_, init) => { signal = init.signal; return pending.promise; });
  const ui = sessionUI(env), initial = ui.draw();
  const active = { authenticated: true, sessionState: 'active', onboardingCompleted: true, user: { id: 2, accountType: 'user' }, csrfToken: 'new-account' };
  initial.context.completeOnboarding(active); await flush(); assert.equal(signal.aborted, true);
  pending.resolve(Response.json({ csrfToken: 'old-account' })); await flush();
  assert.equal(ui.draw().context.state.session, active);
  env.unmount(ui.Login); env.unmount(ui.Provider);
});

for (const action of ['logout', 'login', 'onboard']) test(`${action} cookie transition invalidates an in-flight shared session read`, async () => {
  const pending = deferred(), requests = []; let reads = 0, oldSignal;
  const env = runtime(async (url, init) => {
    requests.push({ url, init });
    if (url.endsWith('/auth/session')) {
      if (++reads === 2) { oldSignal = init.signal; return pending.promise; }
      return Response.json({ csrfToken: reads === 1 ? 'before' : 'after' });
    }
    return Response.json({ user: { id: 2 }, onboardingCompleted: true });
  });
  const api = env.load('lib/api.ts'); await api.getSession();
  const old = api.getSession().catch(() => {});
  await api[action]({ fixture: true });
  assert.equal(oldSignal.aborted, true);
  await api.getSession();
  pending.resolve(Response.json({ csrfToken: 'stale' })); await old; await flush();
  await api.apiRequest('/fixture-write', { method: 'POST' });
  assert.equal(requests.at(-1).init.headers.get('X-CSRF-Token'), 'after');
});
