const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const root = path.join(__dirname, '..');

function fixture() {
  const modules = new Map(), instances = new Map(), effects = [];
  let hooks = [], cursor = 0;
  const react = { ...React,
    useState(initial) { const slot = hooks[cursor++] ??= { value: typeof initial === 'function' ? initial() : initial };
      return [slot.value, value => { slot.value = typeof value === 'function' ? value(slot.value) : value; }]; },
    useRef(initial) { return hooks[cursor++] ??= { current: initial }; },
    useEffect(setup, deps) {
      const index = cursor++, previous = hooks[index];
      if (previous && deps.every((dep, i) => Object.is(dep, previous.deps[i]))) return;
      const effect = { deps, setup, cleanup: previous?.cleanup }; hooks[index] = effect;
      effects.push(() => { effect.cleanup?.(); effect.cleanup = setup(); });
    },
  };
  function load(file) {
    if (modules.has(file)) return modules.get(file).exports;
    const mod = { exports: {} }; modules.set(file, mod);
    const output = ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), { compilerOptions: {
      module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
    } }).outputText;
    vm.runInNewContext(output, { module: mod, exports: mod.exports, AbortController,
      require(name) {
        if (name === 'react') return react;
        if (name === 'react/jsx-runtime') return require(name);
        if (name.endsWith('.css')) return { __esModule: true, default: new Proxy({}, { get: (_, key) => String(key) }) };
        return load((name.startsWith('@/') ? name.slice(2) : path.join(path.dirname(file), name)) + '.ts');
      },
    });
    return mod.exports;
  }
  return { load,
    draw(component, props) { if (!instances.has(component)) instances.set(component, []); hooks = instances.get(component); cursor = 0; return component(props); },
    flush() { for (const effect of effects.splice(0)) effect(); },
    unmount(component) { for (const hook of instances.get(component) ?? []) hook.cleanup?.(); instances.delete(component); },
  };
}
const lib = fixture().load('lib/pivot/notification-settings.ts');
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const tick = () => new Promise(resolve => setImmediate(resolve));
const initial = () => ({ allowed: true, after21Allowed: false });
function setup(overrides = {}) {
  const states = [], calls = []; let current = true;
  const source = { available: true, isCurrent: () => current,
    async load(options) { calls.push(['load', options]); return initial(); },
    async save(patch, options) { calls.push(['save', patch, options]); return { ...initial(), ...patch }; }, ...overrides };
  const controller = lib.createNotificationSettingsController(source, state => states.push(state));
  return { source, states, calls, controller, expire() { current = false; } };
}

test('Production unavailable source cannot load or write and has no assumed user values', async () => {
  const states = [];
  const controller = lib.createNotificationSettingsController({ ...lib.pendingNotificationSettingsSource,
    load() { throw new Error('must not call'); }, save() { throw new Error('must not call'); } }, state => states.push(state));
  await controller.load(); await controller.change('allowed', true);
  assert.ok(states.every(state => state.status === 'unavailable' && state.values === null));
});

test('Loading blocks writes; saving keeps confirmed values and rejects duplicate writes or reloads', async () => {
  const read = deferred(), write = deferred(); let writes = 0;
  const f = setup({ load: () => read.promise, save(patch) { writes++; assert.equal(patch.allowed, false); return write.promise; } });
  const loading = f.controller.load(); await f.controller.change('allowed', false); assert.equal(writes, 0);
  read.resolve(initial()); await loading;
  const saving = f.controller.change('allowed', false);
  assert.equal(f.states.at(-1).status, 'saving'); assert.equal(f.states.at(-1).values.allowed, true);
  await f.controller.change('after21Allowed', true); await f.controller.load(); assert.equal(writes, 1);
  write.resolve({ allowed: false, after21Allowed: true }); await saving;
  assert.equal(f.states.at(-1).status, 'ready'); assert.equal(f.states.at(-1).values.after21Allowed, true);
  assert.equal(f.states.at(-1).values.allowed, false, 'only the validated response changes the displayed setting');
});

test('Failed save is uncertain: discard values and reload instead of automatically replaying PATCH', async () => {
  let reads = 0, writes = 0;
  const f = setup({ async load() { reads++; return { allowed: reads === 1, after21Allowed: false }; },
    async save() { writes++; throw new Error('private transport payload'); } });
  await f.controller.load(); await f.controller.change('allowed', false);
  assert.equal(f.states.at(-1).status, 'error'); assert.equal(f.states.at(-1).operation, 'save'); assert.equal(f.states.at(-1).values, null);
  await f.controller.change('allowed', false); assert.equal(writes, 1);
  await f.controller.load(); assert.equal(reads, 2); assert.equal(writes, 1); assert.equal(f.states.at(-1).values.allowed, false);
  assert.ok(!JSON.stringify(f.states).includes('private transport'));
});

test('Malformed responses never become defaults or a successful save; load retry can recover', async () => {
  for (const response of [null, [], {}, { allowed: 1, after21Allowed: false }, { allowed: false, after21Allowed: null }]) {
    let valid = false;
    const f = setup({ async load() { return valid ? initial() : response; }, async save() { return response; } });
    await f.controller.load(); assert.equal(f.states.at(-1).status, 'error'); assert.equal(f.states.at(-1).values, null);
    valid = true; await f.controller.load(); assert.equal(f.states.at(-1).status, 'ready');
    await f.controller.change('allowed', false); assert.equal(f.states.at(-1).status, 'error'); assert.equal(f.states.at(-1).operation, 'save');
  }
});

test('Retry supersedes stale loads and unmount cancels writes even when transport ignores abort', async () => {
  const late = deferred(); let reads = 0, signal;
  const f = setup({ load(options) { if (++reads === 1) { signal = options.signal; return late.promise; } return Promise.resolve(initial()); } });
  const old = f.controller.load(); await f.controller.load(); assert.equal(signal.aborted, true);
  late.resolve({ allowed: false, after21Allowed: true }); await old; assert.equal(f.states.at(-1).values.allowed, true);
  const write = deferred(); let writeSignal;
  const g = setup({ save(_, options) { writeSignal = options.signal; return write.promise; } });
  await g.controller.load(); const pending = g.controller.change('allowed', false); g.controller.cancel();
  const count = g.states.length; write.resolve({ allowed: false, after21Allowed: false }); await pending;
  assert.equal(writeSignal.aborted, true); assert.equal(g.states.length, count);
  await g.controller.load(); await g.controller.change('allowed', true); assert.equal(g.states.length, count);
});

test('Session replacement before/after load or save clears values and rejects old-session writes', async () => {
  const before = setup(); before.expire(); await before.controller.load(); assert.equal(before.calls.length, 0); assert.equal(before.states.at(-1).status, 'expired');
  for (const operation of ['load', 'save']) {
    const pending = deferred();
    const f = setup({ [operation]: () => pending.promise });
    if (operation === 'save') await f.controller.load();
    const request = operation === 'load' ? f.controller.load() : f.controller.change('allowed', false);
    f.expire(); pending.resolve(initial()); await request;
    assert.equal(f.states.at(-1).status, 'expired'); assert.equal(f.states.at(-1).values, null);
    await f.controller.change('allowed', true); assert.equal(f.states.at(-1).status, 'expired');
  }
});

test('Service lifecycle hides prior scope synchronously, cancels late work and recovers after effect replay', async () => {
  const ui = fixture(), { MyNotificationSettings } = ui.load('components/pivot/my-notification-settings.tsx');
  const f = setup(); let scope = {};
  const child = () => React.Children.toArray(MyNotificationSettings({ demo: false, source: f.source, scope }).props.children).at(-1);
  const draw = () => { const element = child(); return ui.draw(element.type, element.props); };
  assert.equal(draw().props.state.status, 'loading'); ui.flush(); await tick(); assert.equal(draw().props.state.status, 'ready');
  scope = {}; const changed = draw(); assert.equal(changed.props.state.status, 'loading'); assert.equal(changed.props.state.values, null);
  const writes = f.calls.length; changed.props.onChange('allowed', false); assert.equal(f.calls.length, writes, 'old controller is inaccessible before cleanup');
  ui.flush(); await tick(); assert.equal(draw().props.state.status, 'ready');
  ui.unmount(child().type); assert.equal(draw().props.state.status, 'loading'); ui.flush(); await tick(); assert.equal(draw().props.state.status, 'ready');
});

test('Demo remains independent local toggles, never uses a supplied Service source, and resets on remount', () => {
  const ui = fixture(), { MyNotificationSettings } = ui.load('components/pivot/my-notification-settings.tsx');
  const section = MyNotificationSettings({ demo: true, source: { available: true, load() { throw new Error('network'); }, save() { throw new Error('network'); } } });
  const demo = React.Children.toArray(section.props.children).at(-1);
  const draw = () => ui.draw(demo.type, demo.props);
  assert.equal(draw().props.values.allowed, true); draw().props.onChange('allowed', false); draw().props.onChange('after21Allowed', true);
  assert.equal(draw().props.values.allowed, false); assert.equal(draw().props.values.after21Allowed, true);
  ui.unmount(demo.type); assert.equal(draw().props.values.allowed, true); assert.equal(draw().props.values.after21Allowed, false);
});

test('Service UI exposes loading/save/error/retry and unavailable explanation with labelled disabled switches', () => {
  const { NotificationSettingsView } = fixture().load('components/pivot/my-notification-settings.tsx');
  for (const status of ['unavailable', 'loading', 'saving', 'expired', 'error', 'ready']) {
    const state = { status, values: ['ready', 'saving'].includes(status) ? initial() : null, operation: 'save' };
    const html = renderToStaticMarkup(React.createElement(NotificationSettingsView, { state, onChange() {}, onRetry() {} }));
    assert.equal((html.match(/role="switch"/g) ?? []).length, 2);
    assert.equal((html.match(/aria-disabled="true"/g) ?? []).length, status === 'ready' ? 0 : 2);
    assert.equal((html.match(/aria-describedby="my-notification-status"/g) ?? []).length, 2);
    assert.ok(html.includes('id="my-notification-status"'));
    if (['loading', 'saving'].includes(status)) assert.match(html, /aria-busy="true"/);
    if (status === 'unavailable') assert.match(html, /현재 설정값을 확인하거나 변경할 수 없어요/);
    if (status === 'error') { assert.match(html, /role="alert"/); assert.match(html, /설정 다시 불러오기/); }
    assert.doesNotMatch(html, /저장했|저장 완료|저장 성공/);
  }
});
