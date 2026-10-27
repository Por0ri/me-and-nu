const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const React = require('react');
const root = path.resolve(__dirname, '..');
const anonymous = { authenticated: false, sessionState: 'anonymous', onboardingCompleted: false, user: null, csrfToken: 'anonymous-csrf' };
const active = { authenticated: true, sessionState: 'active', onboardingCompleted: true, user: { id: 12, accountType: 'user' }, csrfToken: 'rotated-csrf' };
const endpoints = { kakao: 'https://kauth.kakao.com/oauth/authorize', naver: 'https://nid.naver.com/oauth2.0/authorize' };
const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

// Run the actual API client and Pivot services together; fetch never reaches a network.
function environment({ env = {}, href = 'https://test.me-nu.kr/pivot/login', respond, overrides = {} } = {}) {
  const requests = [], navigations = [], window = new EventTarget();
  window.location = Object.assign(new URL(href), { assign(url) { navigations.push(url); } });
  const cache = new Map();
  const dependencies = { react: React, 'react/jsx-runtime': require('react/jsx-runtime'), ...overrides };
  function load(relative) {
    if (cache.has(relative)) return cache.get(relative).exports;
    const module = { exports: {} };
    cache.set(relative, module);
    const code = ts.transpileModule(fs.readFileSync(path.join(root, relative), 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
    }).outputText;
    vm.runInNewContext(code, { module, exports: module.exports, URL, URLSearchParams, Headers, Response, Event, Error, window,
      process: { env: { NEXT_PUBLIC_API_BASE_URL: 'https://api.test.me-nu.kr', ...env } },
      async fetch(url, init) {
        requests.push({ url, init });
        if (!respond) throw new Error('Unexpected fetch');
        return respond(url, init, requests.length);
      },
      require(name) {
        if (name in dependencies) return dependencies[name];
        const base = name.startsWith('@/') ? name.slice(2) : name.startsWith('.') ? path.posix.join(path.posix.dirname(relative), name) : null;
        if (!base) throw new Error('Unexpected import: ' + name);
        const file = [base, base + '.ts', base + '.tsx'].find(file => fs.existsSync(path.join(root, file)));
        if (!file) throw new Error('Unresolved import: ' + name);
        return load(file);
      },
    }, { filename: relative });
    return module.exports;
  }
  return { load, requests, navigations, window, services: () => load('lib/pivot/services.ts'), api: () => load('lib/api.ts') };
}
const enabled = { NEXT_PUBLIC_PIVOT_ENABLE_TEST_API: 'true' };

test('Test API requires its own exact flag and exact HTTPS FE/API origins', async () => {
  const s = environment().services();
  assert.equal(s.isTestApiAllowed(true, 'https://test.me-nu.kr', 'https://api.test.me-nu.kr'), true);
  assert.equal(s.isTestApiAllowed(true, 'https://test.me-nu.kr', 'https://api.test.me-nu.kr/'), true);
  for (const flag of [undefined, 'false', 'TRUE', '1', ' true ']) {
    const e = environment({ env: { NEXT_PUBLIC_PIVOT_ENABLE_TEST_API: flag, NEXT_PUBLIC_PIVOT_ENABLE_LOCAL_API: 'true' } });
    assert.equal(e.services().canUsePivotApi(), false);
    await assert.rejects(e.services().startSnsLogin('kakao'));
    assert.equal(e.requests.length, 0);
  }
  for (const origin of ['http://test.me-nu.kr', 'https://test.me-nu.kr.evil.test', 'https://me-nu.kr', 'https://other.vercel.app', 'https://test.me-nu.kr:444', 'null', 'https://user:pass@test.me-nu.kr']) {
    assert.equal(s.isTestApiAllowed(true, origin, 'https://api.test.me-nu.kr'), false, origin);
  }
  for (const base of ['http://api.test.me-nu.kr', 'https://api.test.me-nu.kr.evil.test', 'https://api.me-nu.kr', 'https://evil.test', 'https://user:pass@api.test.me-nu.kr', 'https://api.test.me-nu.kr@evil.test', 'https://api.test.me-nu.kr:444', 'https://api.test.me-nu.kr/path', 'https://api.test.me-nu.kr?x=1', 'https://api.test.me-nu.kr#fragment', 'https://*.me-nu.kr', '/api', 'invalid']) {
    const e = environment({ env: { ...enabled, NEXT_PUBLIC_API_BASE_URL: base } });
    assert.equal(e.services().canUsePivotApi(), false, base);
    await assert.rejects(e.services().restorePivotSession());
    await assert.rejects(e.services().startSnsLogin('naver'));
    assert.equal(e.requests.length, 0);
  }
});

test('Demo, preview and Gesture Spike cannot read sessions/policies or start OAuth with both flags enabled', async () => {
  for (const origin of ['https://test.me-nu.kr', 'http://localhost:3000']) {
    for (const route of ['/pivot/demo', '/pivot/demo/child', '/pivot/preview', '/pivot/preview/login', '/pivot/home/gesture-spike']) {
      const e = environment({ href: origin + route, env: { ...enabled, NEXT_PUBLIC_PIVOT_ENABLE_LOCAL_API: 'true', NEXT_PUBLIC_API_BASE_URL: origin.startsWith('http:') ? 'http://localhost:8000' : 'https://api.test.me-nu.kr' } });
      assert.equal(e.services().canUsePivotApi(), false);
      await assert.rejects(e.services().restorePivotSession());
      await assert.rejects(e.services().loadPivotPolicies());
      await assert.rejects(e.services().startSnsLogin('kakao'));
      assert.equal(e.requests.length, 0);
      assert.equal(e.navigations.length, 0);
    }
  }
});

for (const provider of ['kakao', 'naver']) test(provider + ': session then signin POST includes cookie, fresh CSRF and validated navigation', async () => {
  const authorizationUrl = endpoints[provider] + '?response_type=code&client_id=fixture&state=fixture';
  const e = environment({ env: enabled, respond: (url) => json(url.endsWith('/session') ? anonymous : { authorizationUrl }) });
  await e.services().startSnsLogin(provider);
  assert.deepEqual(e.requests.map(r => r.url), ['https://api.test.me-nu.kr/api/v1/auth/session', 'https://api.test.me-nu.kr/api/v1/auth/login/' + provider]);
  for (const { init } of e.requests) {
    assert.equal(init.credentials, 'include');
    assert.equal(init.cache, 'no-store');
    assert.equal(init.redirect, 'error');
  }
  const post = e.requests[1].init;
  assert.equal(post.method, 'POST');
  assert.deepEqual(JSON.parse(post.body), { purpose: 'signin' });
  assert.equal(post.headers.get('X-CSRF-Token'), anonymous.csrfToken);
  assert.equal(post.headers.get('Content-Type'), 'application/json');
  assert.deepEqual(e.navigations, [authorizationUrl]);
});

test('Only the selected provider HTTPS authorization endpoint may receive browser navigation', async () => {
  const e = environment();
  for (const provider of ['kakao', 'naver']) {
    const endpoint = endpoints[provider], url = new URL(endpoint);
    assert.equal(e.services().isSnsAuthorizationUrl(provider, 'https:' + url.host + url.pathname), false);
    assert.equal(e.services().isSnsAuthorizationUrl(provider, endpoint + '?state=fixture'), true);
    for (const value of [null, {}, 123, '', '/relative', 'javascript:alert(1)', endpoint.replace('https:', 'http:'), endpoint.replace(url.host, url.host + '.evil.test'), endpoint.replace(url.host, 'evil.test@' + url.host), endpoint.replace(url.host, url.host + '@evil.test'), endpoint.replace(url.host, url.host + ':444'), url.origin + '/logout', endpoint + '/extra', endpoint + '#fragment', ' ' + endpoint, endpoint + '\n', endpoint.replace('https://', 'https:\\'), endpoints[provider === 'kakao' ? 'naver' : 'kakao']]) {
      assert.equal(e.services().isSnsAuthorizationUrl(provider, value), false, String(value));
    }
  }
  for (const payload of [null, {}, { authorizationUrl: 'https://evil.test' }, { authorizationUrl: endpoints.naver }]) {
    const bad = environment({ env: enabled, respond: url => json(url.endsWith('/session') ? anonymous : payload) });
    await assert.rejects(bad.services().startSnsLogin('kakao'), /안전한 로그인 주소/);
    assert.deepEqual(bad.navigations, []);
  }
  await assert.rejects(e.services().startSnsLogin('google'), /지원하지 않는/);
  assert.equal(e.requests.length, 0);
});

test('Malformed session never starts OAuth; raw errors and server payloads are not exposed', async () => {
  for (const session of [null, {}, { ...anonymous, csrfToken: '' }, { ...active, user: null }, { ...active, user: { id: 12, accountType: 'consumer' } }]) {
    const e = environment({ env: enabled, respond: () => json(session) });
    await assert.rejects(e.services().startSnsLogin('kakao'), /로그인을 시작하지 못/);
    assert.equal(e.requests.length, 1);
    assert.equal(e.navigations.length, 0);
  }
  const e = environment({ env: enabled, respond() { throw new Error('private-provider-payload'); } });
  await assert.rejects(e.services().startSnsLogin('naver'), error => !error.message.includes('private-provider-payload'));
});

for (const status of [401, 403, 429, 503]) test('OAuth HTTP ' + status + ' stops navigation and explicit retry reacquires CSRF', async () => {
  let attempt = 0, fail = true, expired = 0;
  const e = environment({ env: enabled, respond: url => {
    if (url.endsWith('/session')) return json({ ...anonymous, csrfToken: 'csrf-' + ++attempt });
    return fail ? json({ code: status === 403 ? 'CSRF_INVALID' : 'AUTH_ERROR', message: 'private-provider-payload' }, status) : json({ authorizationUrl: endpoints.kakao });
  } });
  e.window.addEventListener(e.api().API_UNAUTHORIZED_EVENT, () => expired++);
  await assert.rejects(e.services().startSnsLogin('kakao'), error => !error.message.includes('private-provider-payload'));
  assert.equal(e.requests.length, 2, 'No automatic POST replay');
  assert.equal(e.navigations.length, 0);
  assert.equal(expired, status === 401 ? 1 : 0);
  fail = false;
  await e.services().startSnsLogin('kakao');
  assert.equal(e.requests[3].init.headers.get('X-CSRF-Token'), 'csrf-2');
  assert.equal(e.navigations.length, 1);
});

test('OAuth return re-reads active user session and replaces in-memory CSRF; 204 remains supported', async () => {
  let current = anonymous;
  const e = environment({ env: enabled, respond: url => url.endsWith('/session') ? json(current) : new Response(null, { status: 204 }) });
  await e.services().restorePivotSession();
  e.window.location.pathname = '/pivot/home';
  current = active;
  const restored = await e.services().restorePivotSession();
  assert.equal(restored.sessionState, 'active');
  assert.equal(restored.user.accountType, 'user');
  assert.equal(restored.csrfToken, active.csrfToken);
  assert.equal(await e.api().logout(), undefined);
  assert.equal(e.requests[2].init.headers.get('X-CSRF-Token'), active.csrfToken);
});

test('Entering a demo route during OAuth preparation cancels subsequent POST/navigation', async () => {
  const pending = deferred();
  const e = environment({ env: enabled, respond: () => pending.promise });
  const login = e.services().startSnsLogin('kakao');
  e.window.location.pathname = '/pivot/demo';
  pending.resolve(json(anonymous));
  await assert.rejects(login);
  assert.equal(e.requests.length, 1);
  assert.equal(e.navigations.length, 0);
});

// Small hook store executes real component effects and handlers without installing test packages.
function hooks() {
  const slots = [];
  let cursor = 0, effects = [];
  const same = (a, b) => a && b && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
  const react = { ...React,
    useState(initial) {
      const i = cursor++;
      slots[i] ??= { value: typeof initial === 'function' ? initial() : initial };
      return [slots[i].value, next => { slots[i].value = typeof next === 'function' ? next(slots[i].value) : next; }];
    },
    useRef(initial) { const i = cursor++; return slots[i] ??= { current: initial }; },
    useReducer(reducer, initial, init) { const [value, set] = react.useState(() => init ? init(initial) : initial); return [value, action => set(old => reducer(old, action))]; },
    useCallback(callback, deps) { const i = cursor++; if (!same(slots[i]?.deps, deps)) slots[i] = { value: callback, deps }; return slots[i].value; },
    useEffect(effect, deps) {
      const i = cursor++;
      if (!same(slots[i]?.deps, deps)) effects.push(() => { slots[i]?.cleanup?.(); slots[i] = { deps, cleanup: effect() }; });
    },
  };
  return { react, render(Component, props = {}) { cursor = 0; effects = []; const tree = Component(props); effects.forEach(run => run()); return tree; }, unmount() { slots.forEach(slot => slot.cleanup?.()); } };
}
function nodes(tree) {
  if (!tree || typeof tree !== 'object') return [];
  return [tree, ...React.Children.toArray(tree.props?.children).flatMap(nodes)];
}

test('Session provider re-reads on Home entry/BFCache, retains auth drafts across consent, and does not loop on renders', async () => {
  const h = hooks();
  let pathname = '/pivot/login', current = anonymous;
  const e = environment({ env: enabled, respond: () => json(current), overrides: { react: h.react, 'next/navigation': { usePathname: () => pathname } } });
  const { PivotSessionProvider } = e.load('components/pivot/pivot-session.tsx');
  const login = PivotSessionProvider({ children: null });
  let tree = h.render(login.type);
  assert.equal(tree.props.value.state.status, 'loading');
  await tick();
  tree = h.render(login.type);
  assert.equal(tree.props.value.state.session.sessionState, 'anonymous');
  tree.props.value.rememberDraft({ consents: [] });
  pathname = '/pivot/consent';
  assert.equal(PivotSessionProvider({}).key, login.key);
  pathname = '/pivot/onboarding';
  assert.equal(PivotSessionProvider({}).key, login.key);
  h.render(login.type); h.render(login.type);
  assert.equal(e.requests.length, 1);
  assert.ok(h.render(login.type).props.value.draft);
  pathname = '/pivot/home'; current = active;
  e.window.location.pathname = pathname;
  const home = PivotSessionProvider({});
  assert.notEqual(home.key, login.key);
  h.unmount();
  // Remount with a fresh hook store, as React does for the changed provider key.
  const homeHooks = hooks();
  const homeEnv = environment({ env: enabled, href: e.window.location.href, respond: () => json(current), overrides: { react: homeHooks.react, 'next/navigation': { usePathname: () => pathname } } });
  const homeProvider = homeEnv.load('components/pivot/pivot-session.tsx').PivotSessionProvider({});
  homeHooks.render(homeProvider.type);
  await tick();
  tree = homeHooks.render(homeProvider.type);
  assert.equal(tree.props.value.state.session.sessionState, 'active');
  current = anonymous;
  homeEnv.window.dispatchEvent(Object.assign(new Event('pageshow'), { persisted: true }));
  assert.equal(homeHooks.render(homeProvider.type).props.value.state.status, 'loading');
  await tick();
  assert.equal(homeHooks.render(homeProvider.type).props.value.state.session.sessionState, 'anonymous');
  assert.equal(homeEnv.requests.length, 2);
  homeHooks.unmount();
});

test('Provider error retries; stale success cannot revive a session cleared by 401', async () => {
  const h = hooks();
  let response = () => Promise.reject(new Error('offline'));
  const e = environment({ env: enabled, respond: () => response(), overrides: { react: h.react, 'next/navigation': { usePathname: () => '/pivot/home' } } });
  const Component = e.load('components/pivot/pivot-session.tsx').PivotSessionProvider({}).type;
  h.render(Component); await tick();
  let tree = h.render(Component);
  assert.equal(tree.props.value.state.status, 'error');
  response = () => json(active);
  tree.props.value.refresh();
  assert.equal(h.render(Component).props.value.state.status, 'loading');
  await tick(); tree = h.render(Component);
  assert.equal(tree.props.value.state.session.sessionState, 'active');
  const stale = deferred(); response = () => stale.promise;
  tree.props.value.rememberDraft({ consents: [] });
  tree.props.value.refresh(); h.render(Component);
  e.window.dispatchEvent(new Event(e.api().API_UNAUTHORIZED_EVENT));
  stale.resolve(json(active)); await tick();
  tree = h.render(Component);
  assert.equal(tree.props.value.state.session, null);
  assert.equal(tree.props.value.draft, null);
  assert.match(tree.props.value.state.message, /만료/);
  h.unmount();
});

test('Refresh invalidates an old response immediately, before the next render/effect', async () => {
  const h = hooks(), old = deferred(), fresh = deferred();
  const e = environment({ env: enabled, respond: (_url, _init, count) => count === 1 ? old.promise : fresh.promise,
    overrides: { react: h.react, 'next/navigation': { usePathname: () => '/pivot/home' } } });
  const Component = e.load('components/pivot/pivot-session.tsx').PivotSessionProvider({}).type;
  const first = h.render(Component);
  first.props.value.refresh();
  old.resolve(json(active)); await tick();
  assert.equal(h.render(Component).props.value.state.status, 'loading');
  fresh.resolve(json(anonymous)); await tick();
  assert.equal(h.render(Component).props.value.state.session.sessionState, 'anonymous');
  h.unmount();
});

test('Isolated providers never mount connected effects or fetch', () => {
  for (const route of ['/pivot/demo', '/pivot/preview/login', '/pivot/home/gesture-spike']) {
    const e = environment({ env: enabled, overrides: { 'next/navigation': { usePathname: () => route } } });
    const tree = e.load('components/pivot/pivot-session.tsx').PivotSessionProvider({});
    assert.equal(tree.props.value.state.status, 'disconnected');
    assert.equal(tree.props.value.state.session, null);
    assert.equal(e.requests.length, 0);
  }
});

function loginHarness({ search = '', props = {}, respond } = {}) {
  const h = hooks(), redirects = [];
  const view = { state: { status: 'ready', session: anonymous } };
  const router = { replace(url) { redirects.push(url); } };
  const e = environment({ env: enabled, href: 'https://test.me-nu.kr/pivot/login' + search, respond,
    overrides: { react: h.react,
      'next/navigation': { useRouter: () => router },
      './pivot-session': { usePivotSession: () => view },
      './pivot-ui': {}, './pivot.module.css': { default: {} },
    },
  });
  const { LoginScreen } = e.load('components/pivot/login-screen.tsx');
  let tree;
  const render = () => { tree = h.render(LoginScreen, props); return tree; };
  render(); render(); // Apply mount feedback, as the browser's next render does.
  return { ...e, redirects, view, render, unmount: h.unmount,
    buttons: () => nodes(tree).filter(n => n.type === 'button' && n.props['aria-describedby'] === 'login-feedback'),
    feedback: () => nodes(tree).find(n => n.props?.id === 'login-feedback'),
    posts: () => e.requests.filter(r => r.init.method === 'POST'),
    pageshow(persisted) { e.window.dispatchEvent(Object.assign(new Event('pageshow'), { persisted })); },
  };
}

for (const [index, provider] of ['kakao', 'naver'].entries()) test(provider + ': rapid clicks produce one OAuth POST and navigation keeps both buttons locked', async () => {
  const post = deferred();
  const ui = loginHarness({ respond: url => url.endsWith('/session') ? json(anonymous) : post.promise });
  const originalButtons = ui.buttons();
  const click = originalButtons[index].props.onClick();
  await originalButtons[index].props.onClick();
  await originalButtons[1 - index].props.onClick();
  await tick(); ui.render();
  assert.equal(ui.posts().length, 1);
  assert.ok(ui.posts()[0].url.endsWith('/' + provider));
  assert.ok(ui.buttons().every(b => b.props.disabled && b.props['aria-busy']));
  ui.pageshow(false); ui.render();
  assert.ok(ui.buttons().every(b => b.props.disabled), 'Ordinary pageshow must not unlock an in-flight request');
  post.resolve(json({ authorizationUrl: endpoints[provider] }));
  await click; ui.render();
  assert.deepEqual(ui.navigations, [endpoints[provider]]);
  assert.ok(ui.buttons().every(b => b.props.disabled && b.props['aria-busy']), 'assign returned, but unloading has not finished');
  assert.match(ui.feedback().props.children, /로그인 페이지로 연결/);
  await originalButtons[index].props.onClick();
  await ui.buttons()[1 - index].props.onClick();
  ui.pageshow(false); ui.render();
  await ui.buttons()[index].props.onClick();
  assert.equal(ui.posts().length, 1);
  assert.equal(ui.requests.length, 2, 'No timer, automatic retry or extra session request');
  assert.ok(ui.buttons().every(b => b.props.disabled));
  ui.unmount();
});

test('Failed OAuth request unlocks both buttons and permits only an explicit retry', async () => {
  let fail = true;
  const ui = loginHarness({ respond: url => url.endsWith('/session') ? json(anonymous)
    : fail ? json({ code: 'CSRF_INVALID', message: 'private-provider-payload' }, 403) : json({ authorizationUrl: endpoints.naver }) });
  await ui.buttons()[0].props.onClick(); ui.render();
  assert.ok(ui.buttons().every(b => !b.props.disabled && !b.props['aria-busy']));
  assert.match(ui.feedback().props.children, /다시 시도/);
  assert.doesNotMatch(ui.feedback().props.children, /private-provider-payload/);
  assert.equal(ui.posts().length, 1);
  assert.equal(ui.navigations.length, 0);
  fail = false;
  await ui.buttons()[1].props.onClick(); ui.render();
  assert.equal(ui.posts().length, 2);
  assert.equal(ui.requests.length, 4);
  assert.deepEqual(ui.navigations, [endpoints.naver]);
  assert.ok(ui.buttons().every(b => b.props.disabled));
  ui.unmount();
});

test('BFCache return clears navigation feedback/lock, respects session loading, and never replays OAuth automatically', async () => {
  const ui = loginHarness({ respond: url => json(url.endsWith('/session') ? anonymous : { authorizationUrl: endpoints.kakao }) });
  await ui.buttons()[0].props.onClick(); ui.render();
  assert.ok(ui.buttons().every(b => b.props.disabled));
  ui.pageshow(true); ui.render();
  assert.ok(ui.buttons().every(b => !b.props.disabled && !b.props['aria-busy']));
  assert.equal(ui.feedback().props.children ?? '', '');
  assert.equal(ui.posts().length, 1);
  ui.view.state = { status: 'loading', session: null }; ui.render();
  assert.ok(ui.buttons().every(b => b.props.disabled));
  await ui.buttons()[0].props.onClick();
  assert.equal(ui.posts().length, 1, 'The provider must finish restoring the session before another login');
  ui.view.state = { status: 'ready', session: anonymous }; ui.render();
  await ui.buttons()[0].props.onClick(); ui.render();
  assert.equal(ui.posts().length, 2);
  assert.ok(ui.buttons().every(b => b.props.disabled));
  ui.unmount();
});

test('A late failure from before BFCache return cannot unlock or overwrite the newer attempt', async () => {
  const old = deferred(), fresh = deferred();
  let posts = 0;
  const ui = loginHarness({ respond: url => url.endsWith('/session') ? json(anonymous) : ++posts === 1 ? old.promise : fresh.promise });
  const firstClick = ui.buttons()[0].props.onClick();
  await tick();
  ui.pageshow(true); ui.render();
  const secondClick = ui.buttons()[1].props.onClick();
  await tick();
  old.reject(new Error('old-failure'));
  await firstClick; ui.render();
  assert.ok(ui.buttons().every(b => b.props.disabled));
  assert.match(ui.feedback().props.children, /로그인 페이지로 연결/);
  fresh.resolve(json({ authorizationUrl: endpoints.naver }));
  await secondClick; ui.render();
  assert.deepEqual(ui.navigations, [endpoints.naver]);
  assert.equal(ui.posts().length, 2);
  assert.ok(ui.buttons().every(b => b.props.disabled));
  ui.unmount();
});

for (const [code, expected] of [
  ['AUTH_CANCELLED', /로그인이 취소/],
  ['AUTH_FAILED', /로그인에 실패/],
  ['AUTH_UNAVAILABLE', /지금은 로그인 서비스를 이용할 수 없/],
  ['AUTH_ACCOUNT_EXISTS', /이미 가입된 계정.*기존에 사용하던 로그인 방법/],
  ['AUTH_ACCOUNT_UNAVAILABLE', /이 계정으로는 로그인할 수 없.*계정 상태/],
]) test('Callback ' + code + ' renders safe Korean feedback on a new document and BFCache return', () => {
  const ui = loginHarness({ search: '?error=' + code + '&error_description=private-provider-payload' });
  assert.match(ui.feedback().props.children, expected);
  assert.doesNotMatch(ui.feedback().props.children, /AUTH_|private-provider-payload/);
  assert.equal(ui.feedback().props.role, 'status');
  assert.equal(ui.feedback().props['aria-live'], 'polite');
  assert.ok(ui.buttons().every(b => !b.props.disabled));
  ui.pageshow(true); ui.render();
  assert.match(ui.feedback().props.children, expected);
  assert.equal(ui.requests.length, 0);
  assert.deepEqual(ui.redirects, []);
  ui.unmount();
});

test('Unknown, empty, malicious and object-property error values use only a fixed generic message', () => {
  for (const error of ['', 'auth_cancelled', 'AUTH_CANCELLED_suffix', 'constructor', '__proto__', 'toString', '<script>alert(1)</script>', 'https://evil.test/?token=private']) {
    const ui = loginHarness({ search: '?error=' + encodeURIComponent(error) + '&error_description=private-provider-payload' });
    assert.equal(ui.feedback().props.children, '로그인을 완료하지 못했어요. 다시 시도해 주세요.');
    assert.ok(ui.buttons().every(b => !b.props.disabled));
    assert.equal(ui.requests.length, 0);
    ui.unmount();
  }
  for (const search of ['', '?error_description=private-provider-payload']) {
    const ui = loginHarness({ search });
    assert.equal(ui.feedback().props.children ?? '', '');
    assert.equal(ui.requests.length, 0);
    ui.unmount();
  }
});

test('Demo and preview ignore callback feedback and BFCache events, keeping SNS clicks isolated', async () => {
  for (const props of [{ preview: true }, { onDemoContinue() {} }]) {
    const ui = loginHarness({ search: '?error=AUTH_ACCOUNT_EXISTS', props });
    assert.equal(ui.feedback().props.children ?? '', '');
    for (const button of ui.buttons()) await button.props.onClick();
    ui.render();
    const notice = ui.feedback().props.children;
    assert.match(notice, /실제 로그인은 진행되지 않아요/);
    ui.pageshow(true); ui.render();
    assert.equal(ui.feedback().props.children, notice);
    assert.equal(ui.requests.length, 0);
    assert.equal(ui.navigations.length, 0);
    assert.deepEqual(ui.redirects, []);
    ui.unmount();
  }
});
