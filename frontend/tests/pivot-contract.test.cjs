const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
// Promoted from the original 23 temporary Slice A contract checks.
const root = require('node:path').resolve(__dirname, '..');
const ts = require(root + '/node_modules/typescript');
function load(relative, dependencies = {}, extras = {}) {
  const mod = { exports: {} };
  const code = ts.transpileModule(fs.readFileSync(root + relative, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  vm.runInNewContext(code, {
    module: mod, exports: mod.exports, URL, process: { env: {} },
    require(name) {
      if (!(name in dependencies)) throw new Error('Unexpected import: ' + name);
      return dependencies[name];
    }, ...extras,
  }, { filename: relative });
  return mod.exports;
}
const plain = value => JSON.parse(JSON.stringify(value));
const c = load('/lib/pivot/consent.ts');
const pivotSession = load('/lib/pivot/session.ts');
const required = { type: 'new_required_policy', policyVersion: '2026-10-v3', required: true, text: 'Dynamic required policy' };
const optional = { type: 'marketing', policyVersion: 'v2', required: false, text: 'Optional policy' };
const policies = [required, optional];
test('Unknown dynamic policy keeps server ID, requirement and version', () => {
  assert.deepEqual(plain(c.validatePolicies(policies)), policies);
  assert.equal(c.policyLabel(required), required.type);
});
test('Required consent blocks draft until selected', () => {
  assert.equal(c.consentSummary(policies, {}).canContinue, false);
  assert.throws(() => c.createConsentDraft(policies, {}), /필수/);
});
test('Optional refusal is valid and is included in draft', () => {
  const selection = { [c.policyKey(required)]: true };
  assert.deepEqual(plain(c.createConsentDraft(policies, selection)), { consents: [
    { type: required.type, policyVersion: required.policyVersion, agreed: true },
    { type: optional.type, policyVersion: optional.policyVersion, agreed: false },
  ] });
  assert.equal(c.consentSummary(policies, selection).all, false);
  assert.equal(c.consentSummary(policies, selection).partial, true);
});
test('Version changes revoke old consent', () => {
  const old = c.selectAll(policies, true);
  const revised = [{ ...required, policyVersion: '2026-10-v4' }, optional];
  const next = c.reconcileSelection(revised, old);
  assert.equal(c.consentSummary(revised, next).canContinue, false);
  assert.equal(next[c.policyKey(optional)], true);
  assert.equal(Object.hasOwn(next, c.policyKey(required)), false);
});
test('New required policy blocks and removed policies disappear', () => {
  const added = { ...required, type: 'additional_required' };
  const next = c.reconcileSelection([optional, added], c.selectAll(policies, true));
  assert.equal(c.consentSummary([optional, added], next).canContinue, false);
  assert.equal(Object.hasOwn(next, c.policyKey(required)), false);
});
test('Select all and clear all produce correct CTA state', () => {
  assert.equal(c.consentSummary(policies, c.selectAll(policies, true)).all, true);
  assert.equal(c.consentSummary(policies, c.selectAll(policies, false)).canContinue, false);
});
for (const [name, input] of [
  ['empty response', []], ['missing response', null],
  ['duplicate policy type', [required, { ...required, policyVersion: 'other' }]],
  ['missing policy version', [{ ...required, policyVersion: '' }]],
  ['non-boolean required flag', [{ ...required, required: 'true' }]],
  ['missing policy text', [{ ...required, text: '' }]],
]) test('Fail closed: ' + name, () => assert.throws(() => c.validatePolicies(input)));

let reads = 0;
const s = load('/lib/pivot/services.ts', {
  '@/lib/api': { apiBaseUrl: 'http://localhost:8000', getSession() { reads++; }, getPolicies() { reads++; } },
  './consent': c, './session': pivotSession,
}, { window: { location: { hostname: 'localhost' } } });
test('Explicit local opt-in accepts matching loopback hosts across ports', () => {
  for (const host of ['localhost', '127.0.0.1', '[::1]']) {
    const browserUrl = new URL('http://' + host + ':3000');
    assert.equal(s.isLocalApiAllowed(true, browserUrl.hostname, 'http://' + host + ':8000'), true);
  }
});
for (const [name, enabled, browserHost, baseUrl] of [
  ['disabled flag', false, 'localhost', 'http://localhost:8000'],
  ['remote browser', true, 'menu.example', 'http://localhost:8000'],
  ['remote backend', true, 'localhost', 'https://api.example.test'],
  ['lookalike host', true, 'localhost', 'http://localhost.example.test'],
  ['embedded credentials', true, 'localhost', 'http://user:secret@localhost:8000'],
  ['file scheme', true, 'localhost', 'file:///localhost'],
  ['invalid URL', true, 'localhost', 'invalid'],
]) test('API guard rejects ' + name, () => assert.equal(s.isLocalApiAllowed(enabled, browserHost, baseUrl), false));
const session = { authenticated: true, sessionState: 'onboarding_pending', onboardingCompleted: false, user: null, csrfToken: 'test-csrf' };
test('Disabled configuration never calls session/policy API', async () => {
  await assert.rejects(s.restorePivotSession(), /아직 연결/);
  await assert.rejects(s.loadPivotPolicies(), /아직 연결/);
  assert.equal(reads, 0);
});
test('Both SNS providers reject without API opt-in', async () => {
  await assert.rejects(s.startSnsLogin('kakao'), /아직 연결/);
  await assert.rejects(s.startSnsLogin('naver'), /아직 연결/);
  assert.equal(reads, 0);
});

for (const [name, input] of [
  ['non-array object', {}], ['null item', [null]], ['missing fields', [{}]],
  ['blank policy type', [{ ...required, type: '   ' }]],
  ['blank version', [{ ...required, policyVersion: '   ' }]],
  ['blank text', [{ ...required, text: '   ' }]],
  ['oversized policy type', [{ ...required, type: 'x'.repeat(51) }]],
  ['oversized version', [{ ...required, policyVersion: 'x'.repeat(51) }]],
  ['non-string text', [{ ...required, text: 1 }]],
]) test('Additional malformed policy: ' + name, () => assert.throws(() => c.validatePolicies(input)));

test('Policy validation returns independent item copies', () => {
  const validated = c.validatePolicies(policies);
  assert.notEqual(validated[0], required);
  validated[0].text = 'Changed copy';
  assert.equal(required.text, 'Dynamic required policy');
});
test('Policy keys distinguish delimiter characters', () => {
  assert.notEqual(c.policyKey({type:'a:b',policyVersion:'c'}), c.policyKey({type:'a',policyVersion:'b:c'}));
});
test('Truthy non-boolean consent is rejected and unknown choices are omitted', () => {
  assert.equal(c.consentSummary(policies, { [c.policyKey(required)]: 'true' }).canContinue, false);
  const selected = { ...c.selectAll(policies, true), unknown: true };
  assert.equal(c.createConsentDraft(policies, selected).consents.length, 2);
});
test('An empty policy list never enables progression', () => {
  assert.deepEqual(plain(c.consentSummary([], {})), { all:false, partial:false, canContinue:false });
});
for (const [name, browserHost, baseUrl] of [
  ['loopback HTTPS', 'localhost', 'https://localhost:8000'],
  ['IPv4 browser', '127.0.0.1', 'http://127.0.0.1:8000'],
  ['IPv6 browser and backend', '[::1]', 'http://[::1]:8000'],
  ['bare IPv6 browser hostname', '::1', 'http://[::1]:8000'],
  ['URL-normalized IPv6 loopback', '[::1]', 'http://[0:0:0:0:0:0:0:1]:8000'],
]) test('API guard allows ' + name + ' with opt-in', () => assert.equal(s.isLocalApiAllowed(true, browserHost, baseUrl), true));
for (const [name, browserHost, baseUrl] of [
  ['localhost browser with IPv4 API', 'localhost', 'http://127.0.0.1:8000'],
  ['IPv4 browser with localhost API', '127.0.0.1', 'http://localhost:8000'],
  ['localhost browser with IPv6 API', 'localhost', 'http://[::1]:8000'],
  ['IPv6 browser with localhost API', '[::1]', 'http://localhost:8000'],
  ['IPv4 browser with IPv6 API', '127.0.0.1', 'http://[::1]:8000'],
  ['bare IPv6 browser with IPv4 API', '::1', 'http://127.0.0.1:8000'],
  ['matching remote hosts', 'api.example.test', 'https://api.example.test'],
  ['matching non-loopback IPv6', '[::2]', 'http://[::2]:8000'],
  ['IPv4-mapped IPv6', '[::ffff:7f00:1]', 'http://[::ffff:127.0.0.1]:8000'],
  ['username-only credentials', 'localhost', 'http://user@localhost:8000'],
  ['password-only credentials', 'localhost', 'http://:secret@localhost:8000'],
  ['unbracketed IPv6 URL', '::1', 'http://::1:8000'],
  ['browser host suffix', 'localhost.example.test', 'http://localhost:8000'],
  ['FTP loopback', 'localhost', 'ftp://localhost:8000'],
  ['relative URL', 'localhost', '/api'],
]) test('API guard rejects ' + name, () => assert.equal(s.isLocalApiAllowed(true, browserHost, baseUrl), false));

test('Mixed loopback hosts never call session/policy API even with explicit opt-in', async () => {
  for (const [browserHost, apiBaseUrl] of [
    ['localhost', 'http://127.0.0.1:8000'],
    ['127.0.0.1', 'http://localhost:8000'],
    ['::1', 'http://localhost:8000'],
  ]) {
    let calls = 0;
    const guarded = load('/lib/pivot/services.ts', {
      '@/lib/api': { apiBaseUrl, getSession() { calls++; }, getPolicies() { calls++; } },
      './consent': c, './session': pivotSession,
    }, { window: { location: { hostname: browserHost } }, process: { env: { NEXT_PUBLIC_PIVOT_ENABLE_LOCAL_API: 'true' } } });
    assert.equal(guarded.canUsePivotApi(), false);
    await assert.rejects(guarded.restorePivotSession(), /아직 연결/);
    await assert.rejects(guarded.loadPivotPolicies(), /아직 연결/);
    assert.equal(calls, 0);
  }
});

test('Only an exact public true flag enables API reads', async () => {
  for (const flag of [undefined, 'false', 'TRUE', '1', ' true ']) {
    let calls = 0;
    const guarded = load('/lib/pivot/services.ts', {
      '@/lib/api': { apiBaseUrl:'http://localhost:8000', getSession(){ calls++; }, getPolicies(){ calls++; } }, './consent': c, './session': pivotSession,
    }, { window:{location:{hostname:'localhost'}}, process:{env:{NEXT_PUBLIC_PIVOT_ENABLE_LOCAL_API:flag}} });
    await assert.rejects(guarded.restorePivotSession());
    await assert.rejects(guarded.loadPivotPolicies());
    assert.equal(calls, 0);
  }
});
test('A missing browser window fails closed without API reads', async () => {
  let calls = 0;
  const server = load('/lib/pivot/services.ts', {
    '@/lib/api': {apiBaseUrl:'http://localhost:8000',getSession(){calls++;},getPolicies(){calls++;}}, './consent': c, './session': pivotSession,
  }, {process:{env:{NEXT_PUBLIC_PIVOT_ENABLE_LOCAL_API:'true'}}});
  assert.equal(server.canUsePivotApi(), false);
  await assert.rejects(server.restorePivotSession());
  assert.equal(calls, 0);
});
test('Allowed service reads return the session and validate dynamic policy data', async () => {
  const calls = [];
  let response = {consentItems:policies};
  const connected = load('/lib/pivot/services.ts', {
    '@/lib/api': {apiBaseUrl:'http://localhost:8000',async getSession(){calls.push('session');return session;},async getPolicies(){calls.push('policies');return response;}}, './consent': c, './session': pivotSession,
  }, {window:{location:{hostname:'localhost'}},process:{env:{NEXT_PUBLIC_PIVOT_ENABLE_LOCAL_API:'true'}}});
  assert.deepEqual(plain(await connected.restorePivotSession()), session);
  assert.deepEqual(plain(await connected.loadPivotPolicies()), policies);
  response = {consentItems:[]};
  await assert.rejects(connected.loadPivotPolicies(), /약관 목록/);
  assert.deepEqual(calls, ['session','policies','policies']);
});
test('Session restore rejects malformed responses and propagates transport failures without a fallback', async () => {
  let response;
  let failure;
  const connected = load('/lib/pivot/services.ts', {
    '@/lib/api': { apiBaseUrl: 'http://localhost:8000', async getSession() {
      if (failure) throw failure;
      return response;
    } }, './consent': c, './session': pivotSession,
  }, { window: { location: { hostname: 'localhost' } }, process: { env: { NEXT_PUBLIC_PIVOT_ENABLE_LOCAL_API: 'true' } } });
  for (response of [null, {}, { ...session, csrfToken: '' }, { ...session, user: { id: 12, accountType: 'consumer' } }]) {
    await assert.rejects(connected.restorePivotSession(), /로그인 상태 응답/);
  }
  response = { ...session, authenticated: false, sessionState: 'anonymous' };
  assert.equal(pivotSession.isPendingOnboarding(await connected.restorePivotSession()), false, 'Expired cookie restored as anonymous stays outside onboarding');
  for (failure of [Object.assign(new Error('expired'), { status: 401 }), new Error('offline')]) {
    await assert.rejects(connected.restorePivotSession(), error => error === failure);
  }
});
