const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const test = require('node:test');
const assert = require('node:assert/strict');
const { sourceAssetHash } = require('./helpers/source-asset-hash.cjs');
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
  vm.runInNewContext(output, { module: mod, exports: mod.exports, AbortController, URL, process: { env: {} },
    require(name) {
      if (name === 'react' || name === 'react/jsx-runtime') return require(name);
      if (name.endsWith('.module.css')) return { default: new Proxy({}, { get: (_, key) => String(key) }), __esModule: true };
      const relative = name.startsWith('@/') ? name.slice(2) : path.join(path.dirname(file), name);
      return load(relative + (fs.existsSync(path.join(root, relative + '.tsx')) ? '.tsx' : '.ts'));
    },
  });
  return mod.exports;
}
const { maskProfileEmail, createMyPageRequestController } = load('lib/pivot/my-page.ts');
const { mockMyPageSource } = load('lib/pivot/my-page-mock.ts');
const { pendingMyPageSource, createServiceMyPageSource } = load('lib/pivot/my-page-service.ts');
const { PivotMyPageScreen } = load('components/pivot/my-page-screen.tsx');
const render = (state, demo = true) => renderToStaticMarkup(React.createElement(PivotMyPageScreen, { state, demo, onHome() {}, onRetry() {} }));
const options = () => ({ signal: new AbortController().signal });
const deferred = () => { let resolve; const promise = new Promise(yes => { resolve = yes; }); return { promise, resolve }; };

test('Profile masking accepts valid addresses and never exposes a complete short local part', () => {
  for (const [local, expected] of [['demo.preview', 'demo'], ['abc', 'ab'], ['ab', 'a'], ['a', '']]) {
    assert.ok(maskProfileEmail([local, 'example.com'].join('@')) === `${expected}***@example.com`, 'masked display only');
  }
  for (const email of [null, undefined, 42, {}, '', 'a@b@c', 'a@b', ' a@example.com', 'a@bad domain.com',
    '.a@example.com', 'a..b@example.com', 'a@-bad.com', 'a@example.com\n', 'a@<private>.com', 'a'.repeat(65) + '@example.com']) {
    assert.ok(maskProfileEmail(email) === '이메일 정보를 확인할 수 없어요', 'invalid email uses safe guidance');
  }
});

test('Demo uses a reserved virtual identity and returns independent display models', async () => {
  const first = await mockMyPageSource.load(options()), second = await mockMyPageSource.load(options());
  assert.equal(first.profile.maskedEmail, 'demo***@example.com');
  first.profile.nickname = 'changed';
  assert.equal(second.profile.nickname, '미뉴');
  assert.equal(second.profile.origin, 'demo');
});
test('Unbound fallback never reads a profile or exposes Demo controls', async () => {
  const states = [];
  await createMyPageRequestController(pendingMyPageSource, state => states.push(state)).load();
  assert.deepEqual(states.map(state => state.status), ['loading', 'unconfigured']);
  const html = render(states.at(-1), false);
  assert.match(html, /로그인 상태를 확인/);
  assert.doesNotMatch(html, /example.com|미뉴|시연용|role="switch"|회원탈퇴/);
});
test('Cross-source identities fail closed in both the loader and display boundary', async () => {
  for (const kind of ['service', 'demo']) {
    const other = kind === 'service' ? 'demo' : 'service';
    const ready = { status: 'ready', profile: { origin: other, nickname: 'must-not-display', maskedEmail: 'hidden***@example.com' } };
    const states = [];
    await createMyPageRequestController({ kind, async load() { return ready; } }, state => states.push(state)).load();
    assert.equal(states.at(-1).status, 'error');
    assert.doesNotMatch(render(ready, kind === 'demo'), /must-not-display|hidden\*|role="switch"/);
  }
});
test('Service transport errors do not fabricate a profile and can retry into an actual Service model', async () => {
  let fail = true; const states = [];
  const controller = createMyPageRequestController({ kind: 'service', async load() {
    if (fail) throw new Error('private transport detail');
    return { status: 'ready', profile: { origin: 'service', nickname: 'Adapter fixture', maskedEmail: 'test***@example.com' } };
  } }, state => states.push(state));
  await controller.load();
  assert.equal(states.at(-1).status, 'error');
  assert.doesNotMatch(render(states.at(-1), false), /private transport|미뉴|example.com/);
  fail = false; await controller.load();
  assert.equal(states.at(-1).profile.origin, 'service');
  const html = render(states.at(-1), false);
  assert.match(html, /role="switch" aria-checked="false"[^>]*aria-disabled="true"/);
  assert.doesNotMatch(html, /시연용 가상 프로필|Demo 알림/);
});
test('Superseded requests and cancellation ignore late identities', async () => {
  const pending = deferred(); const states = []; let calls = 0; let firstSignal;
  const controller = createMyPageRequestController({ kind: 'demo', async load({ signal }) {
    if (++calls === 1) { firstSignal = signal; return pending.promise; }
    return mockMyPageSource.load({ signal });
  } }, state => states.push(state));
  const first = controller.load(); await controller.load();
  const late = await mockMyPageSource.load(options()); late.profile.nickname = 'late';
  pending.resolve(late); await first;
  assert.equal(firstSignal.aborted, true);
  assert.equal(states.at(-1).profile.nickname, '미뉴');
  const cancelled = deferred(), cancelledStates = [];
  const next = createMyPageRequestController({ kind: 'demo', load: () => cancelled.promise }, state => cancelledStates.push(state));
  const request = next.load(); next.cancel(); cancelled.resolve(late); await request;
  assert.deepEqual(cancelledStates.map(state => state.status), ['loading']);
});
test('Ready Demo renders accessible switches, all menus, a single MY selection, and disclosure', async () => {
  const html = render(await mockMyPageSource.load(options()));
  assert.match(html, /미뉴님/); assert.match(html, /demo\*\*\*@example.com/);
  for (const label of ['프로필 수정', '내 활동', '관심분야 관리', '문의 및 신고', '이용약관', '개인정보처리방침', '로그아웃', '회원탈퇴']) assert.ok(html.includes(label), label);
  assert.equal((html.match(/role="switch"/g) || []).length, 2);
  assert.equal((html.match(/aria-current="page"/g) || []).length, 1);
  assert.match(html, /aria-current="page"[^>]*><span>MY/);
  assert.match(html, /시연용 가상 프로필/); assert.match(html, /초기화/);
  assert.doesNotMatch(html, /<a |9:41|imgWifi|imgBattery|role="dialog"|저장됐|삭제했|로그아웃했/);
});
test('Service uses the shared client without direct fetch, storage, OAuth or Mock fallback', () => {
  for (const file of ['lib/pivot/my-page-service.ts', 'components/pivot/my-page-screen.tsx', 'components/pivot/my-page-controller.tsx']) {
    const source = fs.readFileSync(path.join(root, file), 'utf8');
    assert.doesNotMatch(source, /fetch\(|localStorage|sessionStorage|Notification\.|document.cookie|signOut|\/api\/|from.*my-page-mock/);
  }
});
test('My Page assets retain their source hashes; shared chrome is an exact Figma match', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(root, 'public/pivot/my/sources.json'), 'utf8'));
  assert.equal(manifest.nodeId, '1299:4600');
  for (const asset of manifest.assets) {
    const bytes = fs.readFileSync(path.join(root, 'public', asset.path));
    assert.ok(bytes.length > 0);
    assert.equal(sourceAssetHash(asset.path, bytes), asset.sha256);
    const opening = bytes.toString('utf8').match(/<svg[^>]*>/)[0];
    assert.match(opening, new RegExp('width="' + asset.width + '"'));
    assert.match(opening, new RegExp('height="' + asset.height + '"'));
  }
});

function interactiveModule(file, replacements = {}) {
  const hooks = []; let cursor = 0;
  const react = { ...React,
    useRef(initial) { const index = cursor++; return hooks[index] ??= { current: initial }; },
    useState(initial) { const index = cursor++; hooks[index] ??= { value: typeof initial === 'function' ? initial() : initial };
      return [hooks[index].value, value => { hooks[index].value = typeof value === 'function' ? value(hooks[index].value) : value; }]; },
    useReducer(reducer, argument, initialize) { const index = cursor++; hooks[index] ??= { value: initialize ? initialize(argument) : argument };
      return [hooks[index].value, action => { hooks[index].value = reducer(hooks[index].value, action); }]; },
    useCallback: callback => callback, useEffect() {},
  };
  const mod = { exports: {} };
  const output = ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
  } }).outputText;
  vm.runInNewContext(output, { module: mod, exports: mod.exports, require(name) {
    if (Object.hasOwn(replacements, name)) return replacements[name];
    if (name === 'react') return react;
    if (name === 'react/jsx-runtime') return require(name);
    if (name.endsWith('.module.css')) return { default: new Proxy({}, { get: (_, key) => String(key) }), __esModule: true };
    const relative = name.startsWith('@/') ? name.slice(2) : path.join(path.dirname(file), name);
    return load(relative + (fs.existsSync(path.join(root, relative + '.tsx')) ? '.tsx' : '.ts'));
  } });
  return { exports: mod.exports, draw(component, props) { cursor = 0; return component(props); } };
}
const descendants = node => !node || typeof node !== 'object' ? [] : [node, ...React.Children.toArray(node.props?.children).flatMap(descendants)];
const nextTurn = () => new Promise(resolve => setImmediate(resolve));

test('Service logout is available without a profile, blocks duplicate clicks and permits an explicit retry after failure', async () => {
  let resolve, reject, calls = 0;
  const pending = new Promise((yes, no) => { resolve = yes; reject = no; });
  const fixture = interactiveModule('components/pivot/my-page-screen.tsx');
  const props = { state: { status: 'error', message: '프로필을 불러오지 못했어요' }, demo: false, onHome() {}, onRetry() {},
    onLogout() { calls++; return calls === 1 ? pending : Promise.resolve(); } };
  const draw = () => fixture.draw(fixture.exports.PivotMyPageScreen, props);
  const logout = tree => descendants(tree).find(node => node.type === 'button' && React.Children.toArray(node.props.children).includes('로그아웃'));
  let tree = draw(); assert.ok(logout(tree));
  const click = logout(tree).props.onClick; click(); click();
  assert.equal(calls, 1);
  tree = draw(); assert.equal(logout(tree).props.disabled, true); assert.equal(logout(tree).props['aria-busy'], true);
  reject(new Error('private API diagnostics')); await nextTurn(); tree = draw();
  assert.equal(logout(tree).props.disabled, false);
  assert.ok(descendants(tree).some(node => node.props?.role === 'status' && node.props.children === '로그아웃하지 못했어요. 잠시 후 다시 시도해주세요.'));
  logout(tree).props.onClick(); await nextTurn(); assert.equal(calls, 2);
  resolve();
});

test('Session logout clears personal state and replaces the route only after API success; failed attempts keep the session', async () => {
  const active = { authenticated: true, sessionState: 'active', onboardingCompleted: true,
    user: { id: 7, accountType: 'user' }, csrfToken: 'fixture-active' };
  const replacements = [], requests = [];
  const router = { replace: route => replacements.push(route) };
  const fixture = interactiveModule('components/pivot/pivot-session.tsx', {
    'next/navigation': { usePathname: () => '/pivot/home', useRouter: () => router },
    '@/lib/api': { API_UNAUTHORIZED_EVENT: 'api:unauthorized', logout() {
      let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
      requests.push({ resolve, reject }); return promise;
    } },
    '@/lib/pivot/services': { canUsePivotApi: () => true, isActiveSession: session => session === active },
  });
  const Connected = fixture.exports.PivotSessionProvider({ children: null }).type;
  const draw = () => fixture.draw(Connected, { children: null }).props.value;
  let context = draw(); context.completeOnboarding(active); context.rememberDraft({ fixture: true }); context.rememberProfile({ nickname: '개인 입력' });
  context = draw(); const first = context.logout(), duplicate = context.logout();
  assert.equal(first, duplicate); assert.equal(requests.length, 1);
  assert.equal(draw().state.session, active); assert.deepEqual(replacements, []);
  requests[0].reject(new Error('503')); await assert.rejects(first); context = draw();
  assert.equal(context.state.session, active); assert.equal(context.profile.nickname, '개인 입력'); assert.ok(context.draft);
  assert.deepEqual(replacements, []);
  const second = context.logout(); requests[1].resolve(); await second; context = draw();
  assert.equal(context.state.session, null); assert.equal(context.draft, null); assert.equal(context.profile.nickname, '');
  assert.equal(context.onboarding.interest, null); assert.deepEqual(replacements, ['/pivot/login']);
});

const activeSession = (id = 7) => ({ authenticated: true, sessionState: 'active', onboardingCompleted: true,
  user: { id, accountType: 'user' }, csrfToken: 'test-csrf' });
const profileFixture = (overrides = {}) => ({ userId: 7, nickname: '연결 확인', accountType: 'user',
  email: ['profile.fixture', 'example.com'].join('@'), ...overrides });
function serviceFixture(response = profileFixture(), session = activeSession()) {
  let current = session;
  const calls = [];
  const source = createServiceMyPageSource(() => current, { async getProfile(options) { calls.push(options); return response; } }, () => {});
  return { source, calls, switchSession(value) { current = value; } };
}

test('Active Consumer loads its own profile, forwards cancellation, and only returns a display model', async () => {
  const fixture = serviceFixture(), request = options();
  const result = await fixture.source.load(request);
  assert.equal(fixture.calls[0].signal, request.signal);
  assert.equal(result.status, 'ready');
  assert.equal(result.profile.nickname, '연결 확인');
  assert.equal(result.profile.origin, 'service');
  assert.equal(result.profile.maskedEmail, 'prof***@example.com');
  assert.ok(!Object.hasOwn(result.profile, 'email'), 'raw email must not enter the display model');
  assert.ok(!JSON.stringify(result).includes(profileFixture().email), 'no raw email in the published model');
  const html = render(result, false);
  assert.ok(html.includes('취향 수정하기'));
  assert.ok(!html.includes(profileFixture().email), 'no raw email in markup');
});

test('Missing, null and malformed email retain the actual nickname with safe guidance', async () => {
  for (const email of [null, undefined, 42, {}, 'invalid', 'person@private domain.com']) {
    const result = await serviceFixture(profileFixture({ email })).source.load(options());
    assert.equal(result.profile.nickname, '연결 확인');
    assert.equal(result.profile.maskedEmail, '이메일 정보를 확인할 수 없어요');
  }
});

test('Invalid identities and profile payloads fail closed without leaking response data', async () => {
  for (const response of [null, [], 'payload', {}, profileFixture({ userId: 8 }), profileFixture({ userId: '7' }),
    profileFixture({ userId: 0 }), profileFixture({ accountType: 'admin' }), profileFixture({ accountType: null }),
    profileFixture({ nickname: null }), profileFixture({ nickname: '  ' }), profileFixture({ nickname: 3 }),
    profileFixture({ nickname: 'x'.repeat(31) }), profileFixture({ nickname: 'bad\nname' })]) {
    const states = [];
    await createMyPageRequestController(serviceFixture(response).source, state => states.push(state)).load();
    assert.deepEqual(states.map(state => state.status), ['loading', 'error']);
    assert.ok(!JSON.stringify(states).includes(profileFixture().email), 'invalid responses must remain private');
    assert.doesNotMatch(render(states.at(-1), false), /연결 확인|demo\*|미뉴님/);
  }
});

test('Inactive, non-Consumer and invalid sessions cannot initiate a profile read', async () => {
  for (const session of [null, activeSession(0), activeSession(-1), activeSession('7'),
    { ...activeSession(), authenticated: false }, { ...activeSession(), sessionState: 'onboarding_pending' },
    { ...activeSession(), onboardingCompleted: false }, { ...activeSession(), user: { id: 7, accountType: 'admin' } }]) {
    const fixture = serviceFixture(profileFixture(), session);
    await assert.rejects(fixture.source.load(options()), /Profile session changed/);
    assert.equal(fixture.calls.length, 0);
  }
  let calls = 0;
  const source = createServiceMyPageSource(() => activeSession(), { getProfile: async () => { calls++; } }, () => { throw new Error('API blocked'); });
  await assert.rejects(source.load(options()), /API blocked/);
  assert.equal(calls, 0);
});

test('Session changes before or during a read, including same-user replacement, discard old identities', async () => {
  for (const next of [null, activeSession(8), activeSession(7)]) {
    const fixture = serviceFixture(); fixture.switchSession(next);
    await assert.rejects(fixture.source.load(options()), /Profile session changed/);
    assert.equal(fixture.calls.length, 0);
    let current = activeSession(); const pending = deferred(); const states = [];
    const source = createServiceMyPageSource(() => current, { getProfile: () => pending.promise }, () => {});
    const request = createMyPageRequestController(source, state => states.push(state)).load();
    current = next; pending.resolve(profileFixture()); await request;
    assert.deepEqual(states.map(state => state.status), ['loading', 'error']);
    if (next) {
      const nextSource = createServiceMyPageSource(() => current, { getProfile: async () => profileFixture({ userId: next.user.id, nickname: '새 사용자' }) }, () => {});
      assert.equal((await nextSource.load(options())).profile.nickname, '새 사용자');
    }
  }
});

test('Service requests abort before fetch and discard late responses even if the transport ignores abort', async () => {
  const fixture = serviceFixture(), abort = new AbortController(); abort.abort();
  await assert.rejects(fixture.source.load({ signal: abort.signal }));
  assert.equal(fixture.calls.length, 0);
  const session = activeSession(), pending = deferred(), states = []; let signal;
  const source = createServiceMyPageSource(() => session, { getProfile(options) { signal = options.signal; return pending.promise; } }, () => {});
  const controller = createMyPageRequestController(source, state => states.push(state));
  const request = controller.load(); controller.cancel();
  pending.resolve(profileFixture()); await request;
  assert.equal(signal.aborted, true);
  assert.deepEqual(states.map(state => state.status), ['loading']);
});

test('Actual Service adapter errors stay generic and retry reloads a validated profile', async () => {
  const session = activeSession(), states = []; let fail = true, calls = 0;
  const source = createServiceMyPageSource(() => session, { async getProfile() {
    calls++; if (fail) throw new Error(profileFixture().email); return profileFixture();
  } }, () => {});
  const controller = createMyPageRequestController(source, state => states.push(state));
  await controller.load();
  assert.equal(states.at(-1).status, 'error');
  assert.ok(!JSON.stringify(states).includes(profileFixture().email), 'error messages are sanitized');
  fail = false; await controller.load();
  assert.deepEqual(states.map(state => state.status), ['loading', 'error', 'loading', 'ready']);
  assert.equal(calls, 2);
});

function profileApi(fetch, dispatchEvent = () => {}) {
  const mod = { exports: {} };
  const output = ts.transpileModule(fs.readFileSync(path.join(root, 'lib/api.ts'), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  vm.runInNewContext(output, { module: mod, exports: mod.exports, Headers, Event, fetch,
    window: { dispatchEvent }, process: { env: {} }, require: () => ({}) });
  return mod.exports;
}

test('getProfile reuses the credentialed no-store GET client and supports existing no-argument callers', async () => {
  const calls = [];
  const api = profileApi(async (url, init) => { calls.push({ url, init }); return { ok: true, status: 200, json: async () => profileFixture() }; });
  const { signal } = options();
  await api.getProfile({ signal }); await api.getProfile();
  assert.equal(calls[0].url.endsWith('/api/v1/users/me'), true);
  assert.equal(calls[0].init.signal, signal);
  assert.equal(calls[0].init.credentials, 'include'); assert.equal(calls[0].init.cache, 'no-store');
  assert.equal(calls[0].init.method ?? 'GET', 'GET');
  assert.equal(calls[1].init.signal, undefined);
});

test('A cancelled profile response cannot expire a newer session through a late 401', async () => {
  const body = deferred(), events = [], abort = new AbortController(); let reading;
  const started = new Promise(resolve => { reading = resolve; });
  const api = profileApi(async () => ({ ok: false, status: 401, json() { reading(); return body.promise; } }), event => events.push(event.type));
  const request = api.getProfile({ signal: abort.signal });
  await started; abort.abort(); body.resolve({ code: 'AUTH_REQUIRED' });
  await assert.rejects(request); assert.deepEqual(events, []);
});
