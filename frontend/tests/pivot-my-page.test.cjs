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

function interactiveModule(file, replacements = {}, globals = {}) {
  const instances = new Map(), effects = []; let hooks = [], cursor = 0;
  const react = { ...React,
    useRef(initial) { const index = cursor++; return hooks[index] ??= { current: initial }; },
    useState(initial) { const state = hooks[cursor++] ??= { value: typeof initial === 'function' ? initial() : initial };
      return [state.value, value => { state.value = typeof value === 'function' ? value(state.value) : value; }]; },
    useReducer(reducer, argument, initialize) { const state = hooks[cursor++] ??= { value: initialize ? initialize(argument) : argument };
      return [state.value, action => { state.value = reducer(state.value, action); }]; },
    useCallback: callback => callback,
    useEffect(setup, deps) {
      const index = cursor++, previous = hooks[index];
      if (previous && deps?.every((dep, i) => Object.is(dep, previous.deps?.[i]))) return;
      const effect = { deps, setup, cleanup: previous?.cleanup }; hooks[index] = effect;
      effects.push(() => { effect.cleanup?.(); effect.cleanup = setup(); });
    },
  };
  const mod = { exports: {} };
  const output = ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
  } }).outputText;
  vm.runInNewContext(output, { ...globals, module: mod, exports: mod.exports, require(name) {
    if (Object.hasOwn(replacements, name)) return replacements[name];
    if (name === 'react') return react;
    if (name === 'react/jsx-runtime') return require(name);
    if (name.endsWith('.module.css')) return { default: new Proxy({}, { get: (_, key) => String(key) }), __esModule: true };
    const relative = name.startsWith('@/') ? name.slice(2) : path.join(path.dirname(file), name);
    return load(relative + (fs.existsSync(path.join(root, relative + '.tsx')) ? '.tsx' : '.ts'));
  } });
  return { exports: mod.exports,
    draw(component, props) {
      if (!instances.has(component)) instances.set(component, []);
      hooks = instances.get(component); cursor = 0; return component(props);
    },
    flushEffects() { for (const setup of effects.splice(0)) setup(); },
    replayEffects(component) {
      const effects = (instances.get(component) ?? []).filter(hook => hook.setup);
      for (const effect of effects) effect.cleanup?.();
      for (const effect of effects) effect.cleanup = effect.setup();
    },
    unmount(component) { for (const hook of instances.get(component) ?? []) hook.cleanup?.(); instances.delete(component); },
  };
}
const descendants = node => !node || typeof node !== 'object' ? [] : [node, ...React.Children.toArray(node.props?.children).flatMap(descendants)];
const nextTurn = () => new Promise(resolve => setImmediate(resolve));

const logoutRow = tree => descendants(tree).find(node => node.type === 'button' && React.Children.toArray(node.props.children).includes('로그아웃'));
const serviceDialog = tree => descendants(tree).find(node => typeof node.props?.onConfirm === 'function');
const serviceState = { status: 'ready', profile: { origin: 'service', nickname: '회원', maskedEmail: 'me***@example.com' } };

// Native close() clears open synchronously, then queues a close event for a later task.
function queuedNativeDialog(tree) {
  const closeEvents = [];
  const element = {
    open: false,
    showModal() { this.open = true; },
    close() {
      if (!this.open) return;
      this.open = false;
      closeEvents.push(() => tree.props.onClose({ currentTarget: element }));
    },
  };
  tree.props.ref.current = element;
  return { element, closeEvents, flushCloseEvents() { for (const dispatch of closeEvents.splice(0)) dispatch(); } };
}

test('Strict Mode delayed close preserves the reopened service dialog until explicit confirmation', async () => {
  let calls = 0;
  const fixture = interactiveModule('components/pivot/my-page-screen.tsx', {}, { document: { activeElement: null } });
  const props = { state: serviceState, demo: false, onHome() {}, onRetry() {}, onLogout: async () => { calls++; } };
  const draw = () => fixture.draw(fixture.exports.PivotMyPageScreen, props);
  logoutRow(draw()).props.onClick();
  const component = serviceDialog(draw()), tree = fixture.draw(component.type, component.props);
  const native = queuedNativeDialog(tree);
  fixture.flushEffects();
  assert.equal(native.element.open, true);
  fixture.replayEffects(component.type);
  assert.equal(native.closeEvents.length, 1, 'cleanup queued a close event');
  assert.equal(native.element.open, true, 'the second setup reopened the same dialog');
  native.flushCloseEvents();
  assert.ok(serviceDialog(draw()), 'the old close event must not dismiss the reopened confirmation');
  assert.equal(native.element.open, true); assert.equal(calls, 0);
  descendants(tree).find(node => node.type === 'button' && node.props.children === '로그아웃').props.onClick();
  assert.equal(calls, 1);
  await nextTurn(); assert.equal(serviceDialog(draw()), undefined);
  fixture.unmount(component.type); native.flushCloseEvents();
  assert.equal(native.element.open, false); assert.equal(calls, 1);
});

test('Repeated service dialog opens still cancel, Escape and accept a genuinely closed native dialog after effect replay', () => {
  let calls = 0, focus = 0;
  const trigger = { isConnected: true, focus() { focus++; } };
  const fixture = interactiveModule('components/pivot/my-page-screen.tsx', {}, { document: { activeElement: trigger } });
  const props = { state: serviceState, demo: false, onHome() {}, onRetry() {}, onLogout: async () => { calls++; } };
  const draw = () => fixture.draw(fixture.exports.PivotMyPageScreen, props);
  for (const dismiss of ['cancel', 'escape', 'close', 'cancel']) {
    logoutRow(draw()).props.onClick();
    const component = serviceDialog(draw()), tree = fixture.draw(component.type, component.props);
    const native = queuedNativeDialog(tree);
    fixture.flushEffects(); fixture.replayEffects(component.type); native.flushCloseEvents();
    assert.ok(serviceDialog(draw())); assert.equal(native.element.open, true); assert.equal(calls, 0);
    if (dismiss === 'cancel') descendants(tree).find(node => node.type === 'button' && node.props.children === '취소').props.onClick();
    if (dismiss === 'escape') {
      let prevented = false;
      tree.props.onCancel({ preventDefault() { prevented = true; } });
      assert.equal(prevented, true);
    }
    if (dismiss === 'close') {
      native.element.close(); assert.equal(native.element.open, false);
      native.flushCloseEvents();
    }
    assert.equal(serviceDialog(draw()), undefined); assert.equal(calls, 0);
    assert.equal(props.state, serviceState);
    const previousFocus = focus;
    fixture.unmount(component.type); native.flushCloseEvents();
    assert.equal(native.element.open, false); assert.equal(focus, previousFocus + 1);
  }
});

test('Service logout opens confirmation without a request, including when the profile is loading or unavailable', () => {
  for (const state of [serviceState, { status: 'loading' }, { status: 'error', message: '프로필 조회 실패' }]) {
    let calls = 0;
    const fixture = interactiveModule('components/pivot/my-page-screen.tsx');
    const props = { state, demo: false, onHome() {}, onRetry() {}, onLogout: async () => { calls++; } };
    const draw = () => fixture.draw(fixture.exports.PivotMyPageScreen, props);
    assert.equal(serviceDialog(draw()), undefined);
    logoutRow(draw()).props.onClick();
    const opened = draw(), dialog = serviceDialog(opened);
    assert.ok(dialog); assert.equal(calls, 0);
    assert.equal(opened.props['aria-busy'], false, 'profile loading must not suppress modal announcements');
    const html = renderToStaticMarkup(fixture.draw(dialog.type, dialog.props));
    assert.match(html, /정말 로그아웃하시겠어요/);
    assert.match(html, /다시 로그인하면 저장한 내용은 그대로 볼 수 있어요/);
    assert.doesNotMatch(html, /Demo|로그아웃했/);
  }
});

test('Service cancellation, Escape and native close dismiss confirmation without logging out', () => {
  for (const dismiss of ['cancel', 'escape', 'close']) {
    let calls = 0, prevented = false;
    const fixture = interactiveModule('components/pivot/my-page-screen.tsx');
    const props = { state: serviceState, demo: false, onHome() {}, onRetry() {}, onLogout: async () => { calls++; } };
    const draw = () => fixture.draw(fixture.exports.PivotMyPageScreen, props);
    logoutRow(draw()).props.onClick();
    const dialog = serviceDialog(draw()), tree = fixture.draw(dialog.type, dialog.props);
    if (dismiss === 'cancel') descendants(tree).find(node => node.type === 'button' && node.props.children === '취소').props.onClick();
    if (dismiss === 'escape') tree.props.onCancel({ preventDefault() { prevented = true; } });
    if (dismiss === 'close') tree.props.onClose({ currentTarget: { open: false } });
    assert.equal(calls, 0); assert.equal(serviceDialog(draw()), undefined);
    assert.equal(props.state, serviceState);
    if (dismiss === 'escape') assert.equal(prevented, true);
  }
});

test('Service confirmation blocks duplicate requests and dismissal while pending, then permits retry after failure', async () => {
  let reject, calls = 0;
  const pending = new Promise((_, no) => { reject = no; });
  const fixture = interactiveModule('components/pivot/my-page-screen.tsx');
  const props = { state: { status: 'error', message: '프로필을 불러오지 못했어요' }, demo: false, onHome() {}, onRetry() {},
    onLogout() { calls++; return calls === 1 ? pending : Promise.resolve(); } };
  const draw = () => fixture.draw(fixture.exports.PivotMyPageScreen, props);
  logoutRow(draw()).props.onClick(); assert.equal(calls, 0);
  const initial = serviceDialog(draw());
  initial.props.onConfirm(); initial.props.onConfirm(); initial.props.onClose();
  assert.equal(calls, 1);
  let tree = draw(), dialog = serviceDialog(tree);
  assert.ok(dialog); assert.equal(dialog.props.busy, true);
  assert.equal(logoutRow(tree).props.disabled, true);
  const busyDialog = fixture.draw(dialog.type, dialog.props);
  assert.equal(busyDialog.props['aria-busy'], true);
  const buttons = descendants(busyDialog).filter(node => node.type === 'button');
  assert.equal(buttons[0].props.disabled, true);
  assert.equal(buttons[1].props['aria-disabled'], true);
  assert.equal(buttons[1].props.disabled, undefined, 'confirmation retains keyboard focus');
  busyDialog.props.onCancel({ preventDefault() {} }); assert.ok(serviceDialog(draw()));
  reject(new Error('private API diagnostics')); await nextTurn(); tree = draw(); dialog = serviceDialog(tree);
  assert.equal(dialog.props.busy, false); assert.equal(logoutRow(tree).props.disabled, false);
  const failed = renderToStaticMarkup(fixture.draw(dialog.type, dialog.props));
  assert.match(failed, /role="alert"[^>]*>로그아웃하지 못했어요/);
  assert.match(failed, />다시 시도<\/button>/); assert.doesNotMatch(failed, /private API|로그아웃했/);
  dialog.props.onConfirm(); await nextTurn(); assert.equal(calls, 2);
  assert.equal(serviceDialog(draw()), undefined, 'only success dismisses the submitted confirmation');
});

test('Service dialog uses a labelled native modal, safe initial focus and restores only a connected trigger', () => {
  for (const connected of [true, false]) {
    let opens = 0, closes = 0, focus = 0;
    const trigger = { isConnected: connected, focus(options) { assert.equal(options.preventScroll, true); focus++; } };
    const fixture = interactiveModule('components/pivot/my-page-screen.tsx', {}, { document: { activeElement: trigger } });
    const props = { state: serviceState, demo: false, onHome() {}, onRetry() {}, onLogout: async () => {} };
    const draw = () => fixture.draw(fixture.exports.PivotMyPageScreen, props);
    logoutRow(draw()).props.onClick(); const component = serviceDialog(draw());
    const tree = fixture.draw(component.type, component.props);
    tree.props.ref.current = { showModal() { opens++; }, close() { closes++; } };
    fixture.flushEffects();
    assert.equal(opens, 1); assert.equal(tree.type, 'dialog'); assert.equal(tree.props['aria-modal'], 'true');
    const ids = new Set(descendants(tree).map(node => node.props?.id));
    for (const id of (tree.props['aria-labelledby'] + ' ' + tree.props['aria-describedby']).split(' ')) assert.ok(ids.has(id));
    const cancel = descendants(tree).find(node => node.type === 'button' && node.props.children === '취소');
    assert.equal(cancel.props.autoFocus, true);
    fixture.unmount(component.type);
    assert.equal(closes, 1); assert.equal(focus, connected ? 1 : 0);
  }
});

test('Demo account dialogs retain their disclosure and never call a supplied service logout', () => {
  let calls = 0;
  const fixture = interactiveModule('components/pivot/my-page-screen.tsx');
  const props = { state: { ...serviceState, profile: { ...serviceState.profile, origin: 'demo' } }, demo: true,
    onHome() {}, onRetry() {}, onLogout: async () => { calls++; } };
  const draw = () => fixture.draw(fixture.exports.PivotMyPageScreen, props);
  for (const action of ['로그아웃', '회원탈퇴']) {
    descendants(draw()).find(node => node.type === 'button' && React.Children.toArray(node.props.children).includes(action)).props.onClick();
    const tree = draw(), dialog = descendants(tree).find(node => node.props?.action === action);
    assert.ok(dialog); assert.equal(serviceDialog(tree), undefined);
    const html = renderToStaticMarkup(fixture.draw(dialog.type, dialog.props));
    assert.ok(html.includes('Demo ' + action + ' 안내'));
    dialog.props.onClose(); assert.equal(calls, 0);
  }
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
  assert.ok(html.includes('내 피드 취향 조정하기'));
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
