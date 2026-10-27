const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');

const mod = { exports: {} };
const source = fs.readFileSync(path.join(__dirname, '../lib/pivot/session.ts'), 'utf8');
vm.runInNewContext(ts.transpileModule(source, { compilerOptions: {
  module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
} }).outputText, { module: mod, exports: mod.exports });
const { parsePivotSession, isPendingOnboarding } = mod.exports;
const plain = value => JSON.parse(JSON.stringify(value));
const pending = { authenticated: true, sessionState: 'onboarding_pending', onboardingCompleted: false, user: null, csrfToken: 'test-csrf' };
const active = { ...pending, sessionState: 'active', onboardingCompleted: true, user: { id: 12, accountType: 'user' } };
const anonymous = { ...pending, authenticated: false, sessionState: 'anonymous' };

test('Authenticated pending response needs no user ID or invented account type', () => {
  assert.deepEqual(plain(parsePivotSession(pending)), pending);
  assert.equal(isPendingOnboarding(pending), true);
  assert.equal(parsePivotSession(pending).user, null);
});

for (const response of [anonymous, active, { ...pending, sessionState: 'restoration_pending' }, { ...active, sessionState: 'restoration_pending' }]) {
  test(`${response.sessionState}, completed=${response.onboardingCompleted}: recognized but never enters onboarding`, () => {
    assert.deepEqual(plain(parsePivotSession(response)), response);
    assert.equal(isPendingOnboarding(response), false);
  });
}

test('Only contract-consistent authentication, completion and user combinations are accepted', () => {
  const valid = [anonymous, pending, active, { ...pending, sessionState: 'restoration_pending' }, { ...active, sessionState: 'restoration_pending' }];
  for (const sessionState of ['anonymous', 'onboarding_pending', 'active', 'restoration_pending', 'expired', 'unknown']) {
    for (const authenticated of [true, false, 'true', 1, null, undefined]) {
      for (const onboardingCompleted of [true, false, 'false', 0, null, undefined]) {
        for (const user of [null, undefined, active.user, { id: 12, accountType: 'consumer' }, {}]) {
          const response = { ...pending, sessionState, authenticated, onboardingCompleted, user };
          const expected = valid.some(item => item.sessionState === sessionState && item.authenticated === authenticated &&
            item.onboardingCompleted === onboardingCompleted && item.user === user);
          assert.equal(parsePivotSession(response) !== null, expected, JSON.stringify(response));
          assert.equal(isPendingOnboarding(response), expected && sessionState === 'onboarding_pending');
        }
      }
    }
  }
});

test('Malformed payloads, missing fields and missing CSRF fail closed', () => {
  for (const response of [null, undefined, false, 1, 'pending', [], [pending], {}]) {
    assert.equal(parsePivotSession(response), null);
    assert.equal(isPendingOnboarding(response), false);
  }
  for (const key of Object.keys(pending)) {
    const response = { ...pending };
    delete response[key];
    assert.equal(parsePivotSession(response), null, 'Missing ' + key);
  }
  for (const csrfToken of ['', '   ', '\n', null, undefined, 123, {}, []]) {
    assert.equal(parsePivotSession({ ...pending, csrfToken }), null);
  }
});

test('Completed users require a positive safe integer ID and the latest user account type', () => {
  for (const id of [0, -1, 1.5, Number.MAX_SAFE_INTEGER + 1, Infinity, NaN, '12', undefined, null]) {
    assert.equal(parsePivotSession({ ...active, user: { id, accountType: 'user' } }), null);
  }
  for (const accountType of ['consumer', 'creator', 'admin', 'business', '', undefined, null]) {
    assert.equal(parsePivotSession({ ...active, user: { id: 12, accountType } }), null);
  }
  for (const user of [[], {}, 'user', 12, null]) assert.equal(parsePivotSession({ ...active, user }), null);
});

test('Parsing leaves inputs unchanged and retains only the minimal session fields', () => {
  const user = Object.freeze({ ...active.user, nickname: 'unused' });
  const response = Object.freeze({ ...active, user, extra: 'unused' });
  const parsed = parsePivotSession(response);
  assert.deepEqual(plain(parsed), active);
  assert.notEqual(parsed.user, user);
  parsed.user.id = 99;
  assert.equal(user.id, 12);
});

// Exercise the real route gates with lightweight UI/service substitutes; no server or browser is contacted.
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
let view;
let effects = [];
let redirects = [];
const router = { replace(url) { redirects.push(url); }, push() {} };
const frame = ({ children }) => React.createElement('main', null, children);
const dependencies = {
  react: { ...React, useEffect(effect) { effects.push(effect); } },
  'react/jsx-runtime': require('react/jsx-runtime'),
  'next/link': { __esModule: true, default: ({ href, children }) => React.createElement('a', { href }, children) },
  'next/navigation': { useRouter: () => router },
  '@/lib/pivot/session': mod.exports,
  '@/lib/pivot/services': { startSnsLogin() { throw new Error('Unexpected OAuth call'); } },
  './pivot-session': { usePivotSession: () => view },
  './pivot-ui': { PivotFrame: frame, PivotBrand: () => null, PivotAsset: () => null, PrimaryButton: ({ children }) => React.createElement('button', null, children) },
  './consent-screen': { ConsentScreen: () => React.createElement('p', null, 'consent-flow') },
  './onboarding-screens': { OnboardingFlow: () => React.createElement('p', null, 'onboarding-flow') },
  '@/lib/pivot/home-service': {
    pendingHomeSource: { kind: 'service', configured: false },
    createServiceHomeSource: readSession => ({ kind: 'service', configured: true, readSession }),
  },
  '@/lib/pivot/detail-service': {
    pendingDetailSource: { kind: 'service', configured: false },
    createServiceDetailSource: readSession => ({ kind: 'service', configured: true, readSession }),
  },
  './home-detail-experience': { HomeDetailExperience: ({ homeSource, detailSource }) => {
    assert.equal(homeSource.kind, 'service');
    assert.equal(detailSource.kind, 'service');
    assert.equal(detailSource.configured, homeSource.configured);
    if (detailSource.configured) assert.equal(detailSource.readSession().sessionState, 'active');
    if (homeSource.configured) assert.equal(homeSource.readSession().sessionState, 'active');
    return React.createElement('p', null, homeSource.configured ? 'service-content-connected' : 'service-content-unconfigured');
  } },
  './pivot.module.css': { __esModule: true, default: {} },
};
function loadComponent(file) {
  const component = { exports: {} };
  const code = ts.transpileModule(fs.readFileSync(path.join(__dirname, '../components/pivot/', file), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  vm.runInNewContext(code, { module: component, exports: component.exports, require(name) {
    if (!(name in dependencies)) throw new Error('Unexpected component dependency: ' + name);
    return dependencies[name];
  } });
  return component.exports;
}
const { ConsentRoute } = loadComponent('consent-route.tsx');
const { OnboardingRoute } = loadComponent('onboarding-route.tsx');
const { LoginScreen } = loadComponent('login-screen.tsx');
const { HomeRoute } = loadComponent('home-route.tsx');
function render(component, session, { status = 'ready', draft = { consents: [] }, props = {} } = {}) {
  view = { state: { status, session }, draft, onboarding: {}, refresh() {}, rememberDraft() {}, dispatchOnboarding() {} };
  effects = [];
  redirects = [];
  const html = renderToStaticMarkup(React.createElement(component, props));
  effects.forEach(effect => effect());
  return html;
}

test('Pending null-user session reaches consent and onboarding only after session restore', () => {
  assert.match(render(ConsentRoute, pending), /consent-flow/);
  assert.match(render(OnboardingRoute, pending), /onboarding-flow/);
  assert.doesNotMatch(render(OnboardingRoute, pending, { draft: null }), /onboarding-flow/);
  for (const status of ['loading', 'error', 'disconnected']) {
    assert.doesNotMatch(render(ConsentRoute, pending, { status }), /consent-flow/);
    assert.doesNotMatch(render(OnboardingRoute, pending, { status }), /onboarding-flow/);
  }
});

test('Anonymous, expired, completed, restoration and malformed sessions cannot use either route', () => {
  for (const session of [null, anonymous, active, { ...pending, sessionState: 'restoration_pending' },
    { ...pending, authenticated: false }, { ...pending, user: active.user }, { ...pending, csrfToken: '' }]) {
    assert.doesNotMatch(render(ConsentRoute, session), /consent-flow/);
    assert.doesNotMatch(render(OnboardingRoute, session), /onboarding-flow/);
  }
});

test('Service login redirects a restored pending session; demo and preview keep their own flow', () => {
  render(LoginScreen, pending);
  assert.deepEqual(redirects, ['/pivot/consent']);
  render(LoginScreen, active);
  assert.deepEqual(redirects, ['/pivot/home']);
  for (const session of [null, anonymous, { ...pending, user: active.user }]) {
    render(LoginScreen, session);
    assert.deepEqual(redirects, []);
  }
  render(LoginScreen, pending, { status: 'loading' });
  assert.deepEqual(redirects, []);
  render(LoginScreen, pending, { props: { preview: true } });
  assert.deepEqual(redirects, []);
  assert.match(render(LoginScreen, pending, { props: { onDemoContinue() {} } }), /로그인 없이 시연 계속하기/);
  assert.deepEqual(redirects, []);
});

test('Home connects the service only for a validated active user', () => {
  const html = render(HomeRoute, active);
  assert.doesNotMatch(html, /콘텐츠는 아직 연결 준비/);
  assert.match(html, /service-content-connected/);
  assert.deepEqual(redirects, []);
  for (const session of [anonymous, pending, { ...active, sessionState: 'restoration_pending' },
    { ...active, authenticated: false }, { ...active, onboardingCompleted: false },
    { ...active, user: null }, { ...active, user: { id: 12, accountType: 'consumer' } }]) {
    assert.doesNotMatch(render(HomeRoute, session), /service-content-(?:connected|unconfigured)/);
  }
});

test('Home routes anonymous/expired to login, pending null-user to consent, and restoration to a notice', () => {
  for (const session of [anonymous, null]) {
    render(HomeRoute, session);
    assert.deepEqual(redirects, ['/pivot/login']);
  }
  render(HomeRoute, pending);
  assert.deepEqual(redirects, ['/pivot/consent']);
  for (const session of [{ ...pending, sessionState: 'restoration_pending' }, { ...active, sessionState: 'restoration_pending' }]) {
    assert.match(render(HomeRoute, session), /계정 복원/);
    assert.deepEqual(redirects, []);
  }
});

test('Home loading/error never show stale authenticated content; errors offer retry, offline mode stays unconfigured', () => {
  assert.match(render(HomeRoute, active, { status: 'loading' }), /로그인 상태를 확인하고/);
  assert.deepEqual(redirects, []);
  const error = render(HomeRoute, active, { status: 'error' });
  assert.match(error, /로그인 상태 다시 확인/);
  assert.doesNotMatch(error, /service-content-(?:connected|unconfigured)/);
  assert.deepEqual(redirects, []);
  const disconnected = render(HomeRoute, null, { status: 'disconnected' });
  assert.match(disconnected, /service-content-unconfigured/);
  assert.doesNotMatch(disconnected, /로그인이 확인/);
  assert.deepEqual(redirects, []);
});
