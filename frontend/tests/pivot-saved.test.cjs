const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
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
  vm.runInNewContext(output, { module: mod, exports: mod.exports, AbortController, structuredClone,
    require(name) {
      if (name === 'react' || name === 'react/jsx-runtime') return require(name);
      if (name.endsWith('.module.css')) return { default: new Proxy({}, { get: (_, key) => String(key) }), __esModule: true };
      const relative = name.startsWith('@/') ? name.slice(2) : path.join(path.dirname(file), name);
      return load(relative + (fs.existsSync(path.join(root, relative + '.tsx')) ? '.tsx' : '.ts'));
    },
  });
  return mod.exports;
}
const { createDemoSavedSource } = load('lib/pivot/saved-mock.ts');
const { pendingSavedSource } = load('lib/pivot/saved-service.ts');
const { createSavedRequestController } = load('lib/pivot/saved.ts');
const { mockHomeSource } = load('lib/pivot/home-mock.ts');
const { mockDetailSource } = load('lib/pivot/detail-mock.ts');
const { readSavedReturn } = load('lib/pivot/saved-navigation.ts');
const { readHomeReturn } = load('lib/pivot/detail-navigation.ts');
const { PivotSavedScreen, SavedContentCard } = load('components/pivot/saved-screen.tsx');
const { PivotHomeScreen } = load('components/pivot/home-screen.tsx');
const options = () => ({ signal: new AbortController().signal });
const plain = value => JSON.parse(JSON.stringify(value));
const render = (state, demo = true) => renderToStaticMarkup(React.createElement(PivotSavedScreen, { state, demo, onHome() {}, onMy() {}, onViewDetail() {}, onRetry() {} }));
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };

test('Every Saved card resolves the exact Home and Detail identity, title and cover', async () => {
  const { saved } = await createDemoSavedSource().load(options());
  const { home } = await mockHomeSource.load(options());
  assert.equal(saved.cards.length, 3); assert.equal(new Set(saved.cards.map(c => c.contentId)).size, 3);
  for (const card of saved.cards) {
    const post = home.cards.find(p => p.id === card.postId && p.detailContentId === card.contentId);
    const detail = await mockDetailSource.load(card, options());
    assert.ok(post); assert.equal(detail.status, 'ready');
    assert.equal(card.title, post.title); assert.equal(card.title, detail.article.title);
    assert.deepEqual(plain(card.image), plain(post.slides[0].image));
    assert.deepEqual(plain(card.image), plain(detail.article.image));
  }
});
test('Demo bookmarks toggle one matching identity, reject mismatched pairs and notify only subscribers', async () => {
  const source = createDemoSavedSource(), { saved } = await source.load(options());
  const [first, second] = saved.cards; let notifications = 0;
  const off = source.subscribe(() => notifications++);
  source.toggle({ ...first, postId: second.postId }); source.toggle({ postId: 'missing', contentId: 'missing' });
  assert.equal(notifications, 0); assert.equal(source.getSnapshot(), 0);
  source.toggle(first); assert.equal(source.has(first), false); assert.equal(source.has(second), true);
  assert.equal((await source.load(options())).saved.cards.length, 2);
  source.toggle(first); assert.equal(source.has(first), true);
  assert.equal((await source.load(options())).saved.cards.length, 3);
  assert.equal(notifications, 2); off(); source.toggle(second); assert.equal(notifications, 2);
});
test('Demo instances, returned models and reset never leak another session state', async () => {
  const a = createDemoSavedSource(), b = createDemoSavedSource();
  const first = await a.load(options()); const target = first.saved.cards[0];
  a.toggle(target); assert.equal(b.has(target), true);
  first.saved.cards[1].title = 'mutated';
  assert.notEqual((await a.load(options())).saved.cards[0].title, 'mutated');
  a.reset(); assert.equal(a.has(target), true); assert.equal((await a.load(options())).saved.cards.length, 3);
});
test('Removing all Demo bookmarks produces a real empty state without reseeding', async () => {
  const source = createDemoSavedSource(); const { saved } = await source.load(options());
  saved.cards.forEach(card => source.toggle(card));
  const states = []; await createSavedRequestController(source, state => states.push(state)).load();
  assert.deepEqual(states.map(s => s.status), ['loading', 'empty']);
  const html = render(states.at(-1)); assert.match(html, /저장한 콘텐츠가 아직 없어요/);
  assert.match(html, /콘텐츠 보러가기/); assert.doesNotMatch(html, /data-saved-content=/);
  assert.equal((await source.load(options())).saved.cards.length, 0);
});
test('Service stays unconfigured and shows no Demo or empty-list success', async () => {
  const states = []; await createSavedRequestController(pendingSavedSource, state => states.push(state)).load();
  assert.deepEqual(states.map(s => s.status), ['loading', 'unconfigured']);
  const html = render(states.at(-1), false);
  assert.match(html, /저장한 콘텐츠 화면을 준비/); assert.doesNotMatch(html, /Demo|하이큐|저장한 콘텐츠가 아직 없어요|콘텐츠 보러가기|data-saved-content/);
});
test('Cross-source data, duplicate content IDs and malformed identities fail closed', async () => {
  const ready = await createDemoSavedSource().load(options());
  const cases = [
    { kind: 'service', result: ready },
    { kind: 'demo', result: { ...ready, saved: { ...ready.saved, origin: 'service' } } },
    ...[{ ...ready.saved.cards[0], postId: undefined }, { ...ready.saved.cards[0], contentId: '<script>' }, { ...ready.saved.cards[0], title: '' }, ready.saved.cards[0]].map(card => ({ kind: 'demo', result: { status: 'ready', saved: { origin: 'demo', cards: [ready.saved.cards[0], card] } } })),
  ];
  for (const { kind, result } of cases) {
    const states = []; await createSavedRequestController({ kind, async load() { return result; } }, state => states.push(state)).load();
    assert.equal(states.at(-1).status, 'error'); assert.doesNotMatch(render(states.at(-1), kind === 'demo'), /data-saved-content|하이큐/);
  }
  assert.doesNotMatch(render(ready, false), /data-saved-content|하이큐/);
});
test('Transport failure remains an error without a Mock fallback and can retry into a Service model', async () => {
  let fail = true; const states = [];
  const controller = createSavedRequestController({ kind: 'service', async load() {
    if (fail) throw new Error('private transport detail');
    return { status: 'ready', saved: { origin: 'service', cards: [{ postId: 'actual-post', contentId: 'actual-content', title: 'Adapter fixture' }] } };
  } }, state => states.push(state));
  await controller.load(); assert.equal(states.at(-1).status, 'error');
  assert.doesNotMatch(render(states.at(-1), false), /private transport|하이큐/);
  fail = false; await controller.load(); assert.equal(states.at(-1).saved.origin, 'service');
});
test('Superseded success, late failure and cancellation cannot publish stale Saved lists', async () => {
  for (const outcome of ['success', 'failure', 'cancel']) {
    const pending = deferred(), source = createDemoSavedSource(), ready = await source.load(options()), states = []; let calls = 0;
    const controller = createSavedRequestController({ kind: 'demo', load: () => ++calls === 1 ? pending.promise : Promise.resolve(ready) }, state => states.push(state));
    const first = controller.load();
    if (outcome === 'cancel') controller.cancel(); else await controller.load();
    const before = states.length;
    if (outcome === 'failure') pending.reject(new Error('late')); else pending.resolve(ready);
    await first; assert.equal(states.length, before);
    assert.equal(states.at(-1).status, outcome === 'cancel' ? 'loading' : 'ready');
  }
});
test('Saved UI distinguishes states, exposes one SAVE selection and escapes missing-image titles', async () => {
  const ready = await createDemoSavedSource().load(options()); const html = render(ready);
  assert.equal((html.match(/data-saved-content=/g) || []).length, 3);
  assert.equal((html.match(/aria-current="page"/g) || []).length, 1); assert.match(html, /aria-current="page"[^>]*><span>SAVE/);
  assert.match(html, /서버에는 저장하지 않아요/); assert.doesNotMatch(html, /9:41|imgWifi|imgBattery/);
  for (const state of [{ status: 'loading' }, { status: 'empty' }, { status: 'error', message: '실패 안내' }, { status: 'unconfigured', message: '연결 안내' }]) {
    const markup = render(state); assert.match(markup, new RegExp('data-saved-state="' + state.status + '"'));
    assert.doesNotMatch(markup, /data-saved-content=/);
    if (state.status === 'empty') {
      assert.match(markup, /저장한 콘텐츠가 아직 없어요/); assert.match(markup, /콘텐츠 보러가기/);
      assert.match(markup, /src="\/pivot\/home\/chrome\/mascot.png"/);
    } else assert.doesNotMatch(markup, /콘텐츠 보러가기|mascot.png/);
  }
  const card = { postId: 'post', contentId: 'content', title: '<script>unsafe</script>' };
  const fallback = renderToStaticMarkup(React.createElement(SavedContentCard, { card, onViewDetail() {} }));
  assert.match(fallback, /이미지 준비 중/); assert.match(fallback, /&lt;script&gt;/); assert.doesNotMatch(fallback, /<img|<script|src=""/);
});
test('Bookmark affordances are enabled only by a Demo source and explicit Demo controls', async () => {
  const state = await mockHomeSource.load(options()), bookmarks = createDemoSavedSource();
  function home(demo, controls) { return renderToStaticMarkup(React.createElement(PivotHomeScreen, { state, demo, bookmarks: controls, query: '', onQueryChange() {}, onRetry() {} })); }
  assert.equal((home(true, bookmarks).match(/data-demo-bookmark=/g) || []).length, 3);
  assert.equal((home(true, bookmarks).match(/aria-pressed="true"/g) || []).length, 3);
  assert.doesNotMatch(home(false, bookmarks), /data-demo-bookmark=|aria-pressed=/);
  assert.doesNotMatch(home(true), /data-demo-bookmark=|aria-pressed=/);
});
test('Saved return snapshots are scoped to pathname and source and reject unsafe scroll values', () => {
  const snapshot = { path: '/pivot/demo', kind: 'demo', scrollY: 120, focusContentId: 'demo-content-reading' };
  assert.deepEqual(plain(readSavedReturn(snapshot, snapshot.path, 'demo')), snapshot);
  for (const bad of [null, { ...snapshot, path: '/pivot/home' }, { ...snapshot, kind: 'service' }, { ...snapshot, scrollY: -1 }, { ...snapshot, scrollY: Infinity }, { ...snapshot, scrollY: '120' }, { ...snapshot, focusContentId: 12 }]) assert.equal(readSavedReturn(bad, snapshot.path, 'demo'), null);
  assert.equal(readHomeReturn({ path: snapshot.path, kind: 'demo', scrollY: 0, query: '', focusContentId: '', tracks: [], focusNavigation: 'external' }, snapshot.path, 'demo'), null);
});
test('Saved records the latest list and empty frames and reuses exact Figma assets', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(root, 'public/pivot/saved/sources.json'), 'utf8'));
  assert.equal(manifest.nodeId, '1569:8811'); assert.equal(manifest.emptyNodeId, '1363:9389');
  assert.equal(manifest.layout.columns, 2); assert.equal(manifest.layout.panelWidth, 327);
  assert.equal(manifest.layout.cardWidth, 148); assert.equal(manifest.layout.cardHeight, 184.661);
  for (const asset of manifest.assets) {
    const bytes = fs.readFileSync(path.join(root, 'public', asset.path));
    assert.equal(crypto.createHash('sha256').update(bytes).digest('hex'), asset.sha256);
    if (asset.path.endsWith('.png')) { assert.equal(bytes.readUInt32BE(16), asset.width); assert.equal(bytes.readUInt32BE(20), asset.height); }
  }
});
test('Saved Service and Demo changes have no API, persistent storage or Consumer side effects', () => {
  for (const file of ['lib/pivot/saved-service.ts', 'lib/pivot/saved-mock.ts', 'components/pivot/saved-screen.tsx']) {
    const source = fs.readFileSync(path.join(root, file), 'utf8');
    assert.doesNotMatch(source, /fetch\(|localStorage|sessionStorage|document.cookie|\/api\/|consumer/);
  }
  assert.doesNotMatch(fs.readFileSync(path.join(root, 'lib/pivot/saved-service.ts'), 'utf8'), /from.*mock/);
});
