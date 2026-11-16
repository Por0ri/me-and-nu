const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const root = path.join(__dirname, '..');
function modules(fetch = () => { throw new Error('Unexpected network'); }) {
  const cache = new Map();
  function load(file) {
    file = file.replace(/\\/g, '/');
    if (cache.has(file)) return cache.get(file).exports;
    const mod = { exports: {} }; cache.set(file, mod);
    const code = ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    }).outputText;
    vm.runInNewContext(code, { module: mod, exports: mod.exports, AbortController, Headers, Event, URL, fetch, process: { env: {} },
      require(name) { return load((name.startsWith('@/') ? name.slice(2) : path.join(path.dirname(file), name)) + '.ts'); } });
    return mod.exports;
  }
  return load;
}
const active = (id = 7, csrfToken = 'fixture-owner') => ({ authenticated: true, sessionState: 'active', onboardingCompleted: true,
  user: { id, accountType: 'user' }, csrfToken });
const row = (version = 1) => ({ inAppEnabled: true, nightInAppEnabled: false, version });
const options = () => ({ signal: new AbortController().signal });
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
function fixture(overrides = {}, initial = active()) {
  const load = modules(), owner = initial, calls = []; let current = owner;
  const api = { async getSession() { return owner; },
    async getNotificationSettings(init) { calls.push(['get', init]); return row(); },
    async patchNotificationSettings(input, init) { calls.push(['patch', input, init]); return { ...row(input.version + 1), ...input, version: input.version + 1 }; }, ...overrides };
  const { createServiceNotificationSettingsSource } = load('lib/pivot/notification-settings-service.ts');
  const source = createServiceNotificationSettingsSource(() => current, api, () => {});
  return { source, api, calls, load, owner, switchSession(value) { current = value; } };
}

test('Service reads actual booleans and saves camelCase fields with pinned CSRF and the last version', async () => {
  const f = fixture(), opts = options();
  const initial = await f.source.load(opts); assert.equal(initial.allowed, true); assert.equal(initial.after21Allowed, false);
  const saved = await f.source.save({ allowed: false }, opts);
  assert.equal(saved.allowed, false);
  assert.equal(f.calls[0][1].signal, opts.signal);
  assert.equal(JSON.stringify(f.calls[1][1]), JSON.stringify({ version: 1, inAppEnabled: false }));
  assert.equal(f.calls[1][2].headers['X-CSRF-Token'], 'fixture-owner');
  await f.source.save({ after21Allowed: true }, opts);
  assert.equal(f.calls[2][1].version, 2); assert.equal(f.calls[2][1].nightInAppEnabled, true);
});

test('No read or write under inactive, replaced or foreign-cookie sessions, including same-user rotation', async () => {
  for (const next of [null, active(8), active(7)]) {
    const f = fixture(); f.switchSession(next);
    await assert.rejects(f.source.load(options())); assert.equal(f.calls.length, 0);
  }
  for (const cookie of [active(8), active(7, 'rotated'), { ...active(), authenticated: false }]) {
    const f = fixture(); await f.source.load(options()); f.api.getSession = async () => cookie;
    await assert.rejects(f.source.save({ allowed: false }, options()), error => error.kind === 'session');
    assert.equal(f.calls.filter(call => call[0] === 'patch').length, 0);
    await assert.rejects(f.source.load(options()), error => error.kind === 'session');
  }
  const f = fixture({}, null); await assert.rejects(f.source.load(options())); assert.equal(f.calls.length, 0);
});

test('Response/session mismatch after GET is not published and cannot seed a write version', async () => {
  const f = fixture(); let reads = 0;
  f.api.getSession = async () => ++reads === 1 ? f.owner : active(8);
  await assert.rejects(f.source.load(options()), error => error.kind === 'session');
  await assert.rejects(f.source.save({ allowed: false }, options()));
  assert.equal(f.calls.filter(call => call[0] === 'patch').length, 0);
});

test('Malformed server values never become assumed defaults or successful saves', async () => {
  for (const response of [null, {}, { ...row(), inAppEnabled: 1 }, { ...row(), nightInAppEnabled: null }, row(0), row(1.2), row('1')]) {
    const f = fixture({ getNotificationSettings: async () => response });
    await assert.rejects(f.source.load(options()));
    await assert.rejects(f.source.save({ allowed: false }, options()));
  }
  for (const response of [row(1), row(3), row(2), { ...row(2), inAppEnabled: false, nightInAppEnabled: 'false' }]) {
    const f = fixture({ patchNotificationSettings: async () => response }); await f.source.load(options());
    await assert.rejects(f.source.save({ allowed: false }, options()));
    await assert.rejects(f.source.save({ allowed: false }, options()));
  }
});

test('Version conflict clears display/version; explicit GET retry recovers, no automatic PATCH replay', async () => {
  const f = fixture(), states = [], { ApiError } = f.load('lib/api.ts'); let writes = 0;
  f.api.patchNotificationSettings = async () => { writes++; throw new ApiError('private', 409, 'NOTIFICATION_SETTINGS_CONFLICT'); };
  const { createNotificationSettingsController } = f.load('lib/pivot/notification-settings.ts');
  const controller = createNotificationSettingsController(f.source, state => states.push(state));
  await controller.load(); await controller.change('allowed', false);
  assert.equal(states.at(-1).status, 'error'); assert.equal(states.at(-1).reason, 'conflict'); assert.equal(states.at(-1).values, null);
  f.api.getNotificationSettings = async () => ({ ...row(5), inAppEnabled: false }); await controller.load();
  assert.equal(states.at(-1).values.allowed, false); assert.equal(states.at(-1).saved, false); assert.equal(writes, 1);
});

test('401 and CSRF failures clear preferences and do not replay writes', async () => {
  for (const status of [401, 403]) {
    const f = fixture(), states = [], { ApiError } = f.load('lib/api.ts');
    const { createNotificationSettingsController } = f.load('lib/pivot/notification-settings.ts');
    const controller = createNotificationSettingsController(f.source, state => states.push(state));
    await controller.load(); f.api.patchNotificationSettings = async () => { throw new ApiError('private', status, 'AUTH_REQUIRED'); };
    await controller.change('allowed', false); assert.equal(states.at(-1).status, 'expired'); assert.equal(states.at(-1).values, null);
  }
});

test('An aborted old read cannot clear the version established by a newer read', async () => {
  const late = deferred(), started = deferred(); let reads = 0;
  const f = fixture({ getNotificationSettings: () => { if (++reads === 1) { started.resolve(); return late.promise; } return Promise.resolve(row(5)); } });
  const abort = new AbortController(), old = f.source.load({ signal: abort.signal });
  const rejected = assert.rejects(old); await started.promise; abort.abort();
  await f.source.load(options()); late.resolve(row(1)); await rejected;
  await f.source.save({ allowed: false }, options());
  assert.equal(f.calls[0][1].version, 5);
});

test('Cancellation or cookie rotation after a dispatched write never announces success', async () => {
  const late = deferred(), started = deferred();
  const f = fixture({ patchNotificationSettings: () => { started.resolve(); return late.promise; } });
  await f.source.load(options()); const abort = new AbortController();
  const saving = f.source.save({ allowed: false }, { signal: abort.signal }), rejected = assert.rejects(saving);
  await started.promise; abort.abort(); late.resolve({ ...row(2), inAppEnabled: false }); await rejected;
  await assert.rejects(f.source.save({ allowed: false }, options()));
  const g = fixture(); await g.source.load(options());
  g.api.patchNotificationSettings = async () => { g.api.getSession = async () => active(8); return { ...row(2), inAppEnabled: false }; };
  await assert.rejects(g.source.save({ allowed: false }, options()), error => error.kind === 'session');
});

test('Shared HTTP helpers target settings only, require 200 and include credentials, no-store and CSRF', async () => {
  const calls = []; let status = 200;
  const load = modules(async (url, init) => { calls.push({ url, init }); return { ok: true, status, json: async () => url.endsWith('/auth/session') ? active() : row() }; });
  const api = load('lib/api.ts'), { signal } = options();
  await api.getNotificationSettings({ signal });
  await api.patchNotificationSettings({ version: 1, inAppEnabled: false }, { signal, headers: { 'X-CSRF-Token': 'fixture-owner' } });
  const settingsCalls = calls.filter(call => call.url.endsWith('/notification-settings'));
  assert.equal(settingsCalls.length, 2);
  for (const { init } of settingsCalls) { assert.equal(init.credentials, 'include'); assert.equal(init.cache, 'no-store'); assert.equal(init.signal, signal); }
  assert.equal(settingsCalls[1].init.method, 'PATCH'); assert.equal(settingsCalls[1].init.headers.get('X-CSRF-Token'), 'fixture-owner');
  assert.equal(settingsCalls[1].init.body, '{"version":1,"inAppEnabled":false}');
  status = 202; await assert.rejects(api.patchNotificationSettings({ version: 1, inAppEnabled: true }), error => error.code === 'INVALID_RESPONSE');
});
