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
  vm.runInNewContext(output, { module: mod, exports: mod.exports, AbortController, structuredClone, URLSearchParams,
    require(name) {
      if (name === '@/lib/api') return {};
      if (name === './services') return { requirePivotApi() { throw new Error('Test origin not enabled'); } };
      if (name === 'react' || name === 'react/jsx-runtime') return require(name);
      if (name.endsWith('.module.css')) return { default: new Proxy({}, { get: (_, key) => String(key) }), __esModule: true };
      const relative = name.startsWith('@/') ? name.slice(2) : path.join(path.dirname(file), name);
      return load(relative + (fs.existsSync(path.join(root, relative + '.tsx')) ? '.tsx' : '.ts'));
    },
  });
  return mod.exports;
}
const { createDetailRequestController } = load('lib/pivot/detail.ts');
const { mockDetailSource, mockDetailSourceForPreview } = load('lib/pivot/detail-mock.ts');
const { pendingDetailSource } = load('lib/pivot/detail-service.ts');
const { mockHomeSource } = load('lib/pivot/home-mock.ts');
const { PivotDetailScreen } = load('components/pivot/detail-screen.tsx');
const { detailHref, readHomeReturn } = load('lib/pivot/detail-navigation.ts');
const target = { postId: 'demo-home-story', contentId: 'demo-content-story' };
const options = () => ({ signal: new AbortController().signal });
const plain = value => JSON.parse(JSON.stringify(value));
const render = (state, demo = true) => renderToStaticMarkup(React.createElement(PivotDetailScreen, { state, demo, onBack() {}, onRetry() {} }));
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };

test('Every Home card resolves the same post, content, title, image and opening text', async () => {
  const { home } = await mockHomeSource.load(options());
  for (const card of home.cards) {
    const result = await mockDetailSource.load({ postId: card.id, contentId: card.detailContentId }, options());
    assert.equal(result.status, 'ready');
    assert.equal(result.article.postId, card.id); assert.equal(result.article.contentId, card.detailContentId);
    assert.equal(result.article.title, card.title); assert.equal(result.article.work, '하이큐!!');
    assert.deepEqual(plain(result.article.image), plain(card.slides[0].image));
    assert.equal(result.article.sections[0].paragraphs[0], card.slides[0].description);
    assert.doesNotMatch(render(result), /달빛천사/);
    assert.ok(result.article.sections.length >= 4);
  }
});
test('Mock copies cannot alter another reading session', async () => {
  const a = await mockDetailSource.load(target, options()), b = await mockDetailSource.load(target, options());
  a.article.sections[0].paragraphs[0] = 'changed'; a.article.title = 'changed';
  assert.notEqual(a.article.title, b.article.title); assert.notEqual(a.article.sections[0].paragraphs[0], b.article.sections[0].paragraphs[0]);
});
test('Unknown IDs and mismatched post/content pairs never resolve another article', async () => {
  for (const requested of [{ ...target, contentId: 'missing' }, { ...target, postId: 'demo-home-reading' }]) {
    assert.equal((await mockDetailSource.load(requested, options())).status, 'not-found');
  }
});
test('Malformed IDs stop before the data source, including script and oversized input', async () => {
  let calls = 0;
  for (const contentId of ['', '../other', '<script>', 'a'.repeat(129)]) {
    const states = [];
    await createDetailRequestController({ kind: 'demo', async load() { calls++; } }, state => states.push(state)).load({ ...target, contentId });
    assert.equal(states.at(-1).status, 'not-found');
  }
  assert.equal(calls, 0);
});
test('A blocked Service origin never falls back to Mock articles or Demo feedback', async () => {
  const states = [];
  await createDetailRequestController(pendingDetailSource, state => states.push(state)).load(target);
  assert.deepEqual(states.map(s => s.status), ['loading', 'error']);
  assert.doesNotMatch(render(states.at(-1), false), /data-detail-content|하이큐|더 볼래요|Demo 선택/);
  assert.doesNotMatch(fs.readFileSync(path.join(root, 'lib/pivot/detail-service.ts'), 'utf8'), /from.*mock|fetch\(/);
});
test('A source cannot return Demo data for Service or another post/content identity', async () => {
  const ready = await mockDetailSource.load(target, options());
  for (const [kind, article] of [['service', ready.article], ['demo', { ...ready.article, postId: 'other' }], ['demo', { ...ready.article, contentId: 'other' }]]) {
    const states = [];
    await createDetailRequestController({ kind, async load() { return { status: 'ready', article }; } }, s => states.push(s)).load(target);
    assert.equal(states.at(-1).status, 'error'); assert.doesNotMatch(render(states.at(-1)), /data-detail-content/);
  }
});
test('Empty, missing, loading and transport error have distinct accessible states', async () => {
  for (const scenario of ['empty', 'error']) {
    const states = []; await createDetailRequestController(mockDetailSourceForPreview(scenario), s => states.push(s)).load(target);
    assert.equal(states.at(-1).status, scenario);
    const html = render(states.at(-1));
    assert.match(html, new RegExp(`data-detail-state="${scenario}"`));
    assert.match(html, /Home으로 돌아가기/);
    assert.equal(html.includes('다시 시도'), scenario === 'error'); assert.doesNotMatch(html, /Demo QA error/);
  }
  assert.match(render({ status: 'loading' }), /aria-busy="true"/);
  assert.match(render({ status: 'not-found' }), /요청한 콘텐츠를 찾을 수 없어요/);
  const ready = await mockDetailSource.load(target, options());
  ready.article.sections = [{ paragraphs: [' ', ''] }]; const states = [];
  await createDetailRequestController({ kind: 'demo', async load() { return ready; } }, s => states.push(s)).load(target);
  assert.equal(states.at(-1).status, 'empty');
});
test('Transport failure retries into a real article without exposing errors', async () => {
  let calls = 0; const states = [];
  const controller = createDetailRequestController({ kind: 'demo', async load(t, o) { if (++calls === 1) throw new Error('private'); return mockDetailSource.load(t, o); } }, s => states.push(s));
  await controller.load(target); await controller.load(target);
  assert.deepEqual(states.map(s => s.status), ['loading', 'error', 'loading', 'ready']);
});
for (const outcome of ['success', 'failure', 'unmount']) test('Late ' + outcome + ' cannot replace the current article', async () => {
  const pending = deferred(), ready = await mockDetailSource.load(target, options()), states = [], signals = [];
  const controller = createDetailRequestController({ kind: 'demo', async load(t, { signal }) { signals.push(signal); return signals.length === 1 ? pending.promise : ready; } }, s => states.push(s));
  const old = controller.load(target);
  if (outcome === 'unmount') controller.cancel(); else await controller.load(target);
  if (outcome === 'failure') pending.reject(new Error('late')); else pending.resolve(ready);
  await old; assert.equal(signals[0].aborted, true);
  assert.deepEqual(states.map(s => s.status), outcome === 'unmount' ? ['loading'] : ['loading', 'loading', 'ready']);
});
test('Reading UI escapes text, supports absent imagery and discloses local Demo choice', async () => {
  const ready = await mockDetailSource.load(target, options()); ready.article.image = undefined;
  ready.article.sections[0].paragraphs = ['<script>bad</script>'];
  const html = render(ready);
  assert.match(html, /대표 이미지 준비 중/); assert.match(html, /&lt;script&gt;/); assert.doesNotMatch(html, /<script/);
  assert.match(html, /실제 추천·저장·서버 전송 없음/);
  assert.equal((html.match(/aria-pressed="false"/g) || []).length, 2);
  assert.doesNotMatch(render(ready, false), /더 볼래요|그만 볼래요|Demo 선택/);
});
test('Detail URLs encode identifiers and Home history snapshots are scope-checked', () => {
  const url = new URL(detailHref('/pivot/demo', { postId: 'post&x=1', contentId: 'a b#c' }), 'http://localhost');
  assert.equal(url.searchParams.get('postId'), 'post&x=1'); assert.equal(url.searchParams.get('contentId'), 'a b#c');
  const snapshot = { path: '/pivot/demo', kind: 'demo', query: '하이큐', scrollY: 430, focusContentId: target.contentId, tracks: [{ postId: target.postId, index: 3 }] };
  assert.ok(readHomeReturn(snapshot, '/pivot/demo', 'demo'));
  for (const value of [null, {}, { ...snapshot, kind: 'service' }, { ...snapshot, path: '/elsewhere' }, { ...snapshot, scrollY: NaN }, { ...snapshot, tracks: [{ index: -1 }] }]) assert.equal(readHomeReturn(value, '/pivot/demo', 'demo'), null);
});
test('Every Detail static asset is local and retains the Figma source hash', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(root, 'public/pivot/detail/sources.json'), 'utf8').replace(/^\uFEFF/, ''));
  for (const asset of manifest.assets) {
    const bytes = fs.readFileSync(path.join(root, 'public', asset.path)); assert.ok(bytes.length);
    assert.equal(crypto.createHash('sha256').update(bytes).digest('hex'), asset.sha256);
  }
});
