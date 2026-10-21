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
function load(relative) {
  if (modules.has(relative)) return modules.get(relative).exports;
  const mod = { exports: {} };
  modules.set(relative, mod);
  const output = ts.transpileModule(fs.readFileSync(path.join(root, relative), 'utf8'), { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
  } }).outputText;
  vm.runInNewContext(output, { module: mod, exports: mod.exports, AbortController, structuredClone,
    require(name) {
      if (name === 'react' || name === 'react/jsx-runtime') return require(name);
      if (name.endsWith('.module.css')) return { default: new Proxy({}, { get: (_, key) => String(key) }), __esModule: true };
      if (name.startsWith('@/')) return load(name.slice(2) + (name.includes('/components/') ? '.tsx' : '.ts'));
      if (name.startsWith('./')) {
        const filename = path.join(path.dirname(relative), name);
        return load(filename + (fs.existsSync(path.join(root, filename + '.tsx')) ? '.tsx' : '.ts'));
      }
      throw new Error('Unexpected Home dependency: ' + name);
    },
  });
  return mod.exports;
}
const { createHomeRequestController } = load('lib/pivot/home.ts');
const { mockHomeSource } = load('lib/pivot/home-mock.ts');
const { pendingHomeSource } = load('lib/pivot/home-service.ts');
const { PivotHomeScreen, HomeCard } = load('components/pivot/home-screen.tsx');
const { HomeController } = load('components/pivot/home-controller.tsx');
const plain = value => JSON.parse(JSON.stringify(value));
const result = (id = 'example', cards) => ({ status: 'ready', home: { origin: 'demo', cards: cards || [{ id, title: id, summary: 'Display example' }] } });
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function render(state, demo = true) {
  return renderToStaticMarkup(React.createElement(PivotHomeScreen, {
    state, demo, query: '', onQueryChange() {}, onRetry() {}, onRestart() {},
  }));
}

test('Service remains unconfigured and renders no Mock cards or demo controls', async () => {
  assert.equal(pendingHomeSource.kind, 'service');
  const states = [];
  await createHomeRequestController(pendingHomeSource, state => states.push(state)).load();
  assert.deepEqual(states.map(state => state.status), ['loading', 'unconfigured']);
  assert.doesNotMatch(render(states.at(-1), false), /시연용|<article|처음부터 시연/);
});

test('Mock gives independent copies and has no reaction aggregates or onboarding mapping', async () => {
  const first = await mockHomeSource.load({ signal: new AbortController().signal });
  const second = await mockHomeSource.load({ signal: new AbortController().signal });
  assert.deepEqual(plain(first), plain(second));
  first.home.cards[0].title = 'Modified in one instance';
  assert.notEqual(second.home.cards[0].title, first.home.cards[0].title);
  for (const card of second.home.cards) assert.deepEqual(Object.keys(card).sort(), ['id', 'summary', 'title']);
  assert.deepEqual(Object.keys(second.home).sort(), ['cards', 'origin']);
});

test('Error can retry into ready; each transition renders the corresponding UI', async () => {
  let calls = 0;
  const states = [], html = [];
  const controller = createHomeRequestController({ kind: 'demo', async load() {
    if (++calls === 1) throw new Error('Private transport detail');
    return result();
  } }, state => { states.push(state.status); html.push(render(state)); });
  await controller.load();
  assert.match(html.at(-1), /role="alert"/);
  assert.match(html.at(-1), /다시 시도/);
  assert.doesNotMatch(html.at(-1), /Private transport detail|<article/);
  await controller.load();
  assert.deepEqual(states, ['loading', 'error', 'loading', 'ready']);
  assert.match(html.at(-1), /<article/);
  assert.doesNotMatch(html.at(-1), /다시 시도/);
});

test('An empty feed is distinct from unconfigured and renders without cards', async () => {
  const states = [];
  await createHomeRequestController({ kind: 'demo', async load() { return result('', []); } }, state => states.push(state)).load();
  assert.deepEqual(states.map(state => state.status), ['loading', 'empty']);
  assert.match(render(states.at(-1)), /아직 표시할 콘텐츠/);
  assert.doesNotMatch(render(states.at(-1)), /<article/);
});

for (const lateOutcome of ['success', 'failure']) test('Superseded ' + lateOutcome + ' cannot replace the latest feed', async () => {
  const first = deferred(), second = deferred(), states = [], signals = [];
  const controller = createHomeRequestController({ kind: 'demo', load({ signal }) {
    signals.push(signal); return signals.length === 1 ? first.promise : second.promise;
  } }, state => states.push(state));
  const oldRequest = controller.load();
  const newRequest = controller.load();
  assert.equal(signals[0].aborted, true);
  second.resolve(result('latest')); await newRequest;
  if (lateOutcome === 'success') first.resolve(result('stale')); else first.reject(new Error('late failure'));
  await oldRequest;
  assert.deepEqual(states.map(state => state.status), ['loading', 'loading', 'ready']);
  assert.equal(states.at(-1).home.cards[0].id, 'latest');
});

test('Unmount cancellation ignores even a source that does not honor abort', async () => {
  const pending = deferred(), states = [];
  let signal;
  const controller = createHomeRequestController({ kind: 'demo', load(options) { signal = options.signal; return pending.promise; } }, state => states.push(state.status));
  const loading = controller.load();
  controller.cancel(); pending.resolve(result()); await loading;
  assert.equal(signal.aborted, true);
  assert.deepEqual(states, ['loading']);
});

test('Independent controllers do not cancel or mutate one another', async () => {
  const pending = deferred(), firstStates = [], secondStates = [];
  const first = createHomeRequestController({ kind: 'demo', load() { return pending.promise; } }, state => firstStates.push(state.status));
  const firstRequest = first.load();
  await createHomeRequestController(mockHomeSource, state => secondStates.push(state.status)).load();
  first.cancel(); pending.resolve(result()); await firstRequest;
  assert.deepEqual(firstStates, ['loading']);
  assert.deepEqual(secondStates, ['loading', 'ready']);
});

test('A service source returning a demo model fails closed', async () => {
  const states = [];
  await createHomeRequestController({ kind: 'service', async load() { return result('must-not-display'); } }, state => states.push(state)).load();
  assert.equal(states.at(-1).status, 'error');
  assert.doesNotMatch(render(states.at(-1), false), /must-not-display|<article/);
});

test('Shared UI renders all five states and only input UI for search', async () => {
  const loaded = await mockHomeSource.load({ signal: new AbortController().signal });
  for (const state of [{ status: 'loading' }, loaded, { status: 'empty' }, { status: 'error', message: '오류 안내' }, { status: 'unconfigured', message: '연결 안내' }]) {
    const html = render(state);
    assert.match(html, new RegExp('data-home-state="' + state.status + '"'));
    assert.match(html, /<h1[^>]*>홈<\/h1>/);
    assert.match(html, /type="search"/);
    assert.match(html, /입력만 가능/);
    assert.doesNotMatch(html, /<p\b[^>]*aria-label=/);
    assert.doesNotMatch(html, /댓글|좋아요|저장하기|공유 수|role="tablist"|캐러셀/);
  }
  const serviceHtml = render(loaded, false);
  assert.match(serviceHtml, /<article/);
  assert.doesNotMatch(serviceHtml, /시연용|처음부터 시연/);
  const initialController = renderToStaticMarkup(React.createElement(HomeController, { source: pendingHomeSource }));
  assert.match(initialController, /data-home-state="loading"/);
});

test('Cards escape content, support missing media, and expose no interaction controls', () => {
  const html = renderToStaticMarkup(React.createElement(HomeCard, { demo: true, card: { id: 'safe', title: '<script>unsafe</script>', summary: 'x'.repeat(500) } }));
  assert.match(html, /&lt;script&gt;/);
  assert.doesNotMatch(html, /<img|<button|<a /);
});
