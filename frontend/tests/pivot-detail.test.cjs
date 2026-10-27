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
function createLoader(overrides = {}, globals = {}) {
  const modules = new Map();
  function load(file) {
    if (modules.has(file)) return modules.get(file).exports;
    const mod = { exports: {} }; modules.set(file, mod);
    const output = ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), { compilerOptions: {
      module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
    } }).outputText;
    vm.runInNewContext(output, { module: mod, exports: mod.exports, AbortController, structuredClone, URL, URLSearchParams,
      process: { env: {} }, fetch() { throw new Error('Unexpected network request'); }, ...globals,
      require(name) {
        if (Object.hasOwn(overrides, name)) return overrides[name];
        if (name === 'react' || name === 'react/jsx-runtime') return require(name);
        if (name.endsWith('.module.css')) return { default: new Proxy({}, { get: (_, key) => String(key) }), __esModule: true };
        const relative = name.startsWith('@/') ? name.slice(2) : path.join(path.dirname(file), name);
        return load(relative + (fs.existsSync(path.join(root, relative + '.tsx')) ? '.tsx' : '.ts'));
      },
    });
    return mod.exports;
  }
  return load;
}
const load = createLoader();
const { createDetailRequestController } = load('lib/pivot/detail.ts');
const { mockDetailSource, mockDetailSourceForPreview } = load('lib/pivot/detail-mock.ts');
const { pendingDetailSource } = load('lib/pivot/detail-service.ts');
const { mockHomeSource } = load('lib/pivot/home-mock.ts');
const { PivotDetailScreen } = load('components/pivot/detail-screen.tsx');
const { detailHref, readHomeReturn } = load('lib/pivot/detail-navigation.ts');
const target = { postId: 'demo-home-story', contentId: 'demo-content-story' };
const serviceTarget = { postId: '301', contentId: '301', topicId: '9' };
const serviceReady = (identity = serviceTarget) => ({ status: 'ready', article: {
  ...identity, origin: 'service', work: 'Fixture', title: 'Fixture article', sections: [{ paragraphs: ['Fixture text'] }],
} });
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
test('Service remains unconfigured without a Mock fallback or Demo feedback', async () => {
  const states = [];
  await createDetailRequestController(pendingDetailSource, state => states.push(state)).load(serviceTarget);
  assert.deepEqual(states.map(s => s.status), ['loading', 'unconfigured']);
  assert.doesNotMatch(render(states.at(-1), false), /data-detail-content|하이큐|더 볼래요|Demo 선택/);
  assert.doesNotMatch(fs.readFileSync(path.join(root, 'lib/pivot/detail-service.ts'), 'utf8'), /from.*mock|fetch\(/);
});
test('A source cannot return Demo data for Service or another post/content identity', async () => {
  const ready = await mockDetailSource.load(target, options());
  for (const [kind, article] of [['service', ready.article], ['demo', { ...ready.article, postId: 'other' }], ['demo', { ...ready.article, contentId: 'other' }]]) {
    const states = [];
    await createDetailRequestController({ kind, async load() { return { status: 'ready', article }; } }, s => states.push(s)).load(kind === 'service' ? serviceTarget : target);
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
  const url = new URL(detailHref('/pivot/demo', { postId: 'post&x=1', contentId: 'a b#c', topicId: '9&redirect=https://example.test/#x' }), 'http://localhost');
  assert.equal(url.searchParams.get('postId'), 'post&x=1'); assert.equal(url.searchParams.get('contentId'), 'a b#c');
  assert.equal(url.searchParams.get('topicId'), '9&redirect=https://example.test/#x');
  assert.equal(url.searchParams.has('redirect'), false); assert.equal(url.origin, 'http://localhost');
  assert.equal(detailHref('/pivot/demo', target), '/pivot/demo?view=detail&postId=demo-home-story&contentId=demo-content-story');
  assert.equal(detailHref('/pivot/home', serviceTarget), '/pivot/home?view=detail&postId=301&contentId=301&topicId=9');
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


test('Service validates both numeric IDs before loading while Demo keeps string IDs', async () => {
  let calls = 0;
  const source = { kind: 'service', async load(t) { calls++; return serviceReady(t); } };
  for (const field of ['contentId', 'topicId']) for (const bad of [undefined, null, '', 1, '0', '-1', '1.5', '01', '+1', ' 1', '1 ', '1e2', '0x10', 'NaN', 'Infinity', '9007199254740992', 'demo-content-story', '<script>', '1&topicId=9']) {
    const states = [];
    await createDetailRequestController(source, s => states.push(s)).load({ ...serviceTarget, [field]: bad });
    assert.deepEqual(states.map(s => s.status), ['loading', 'not-found'], field + ': ' + String(bad));
  }
  assert.equal(calls, 0);
  for (const id of ['1', '9', '9007199254740991']) {
    const states = [];
    await createDetailRequestController(source, s => states.push(s)).load({ ...serviceTarget, contentId: id, topicId: id });
    assert.equal(states.at(-1).status, 'ready');
  }
  assert.equal(calls, 3);
  const states = [];
  await createDetailRequestController(mockDetailSource, s => states.push(s)).load(target);
  assert.equal(states.at(-1).status, 'ready');
  assert.equal(states.at(-1).article.topicId, undefined);
});

test('Service rejects missing or mismatched Topic, content, post and origin in ready results', async () => {
  for (const change of [{ topicId: undefined }, { topicId: '1' }, { contentId: '302' }, { postId: '302' }, { origin: 'demo' }]) {
    const states = [], ready = serviceReady(); Object.assign(ready.article, change);
    await createDetailRequestController({ kind: 'service', async load() { return ready; } }, s => states.push(s)).load(serviceTarget);
    assert.deepEqual(states.map(s => s.status), ['loading', 'error']);
  }
});

for (const outcome of ['success', 'failure', 'invalid-next']) test('Topic-only change cancels stale ' + outcome, async () => {
  const old = deferred(), signals = [], states = [];
  const controller = createDetailRequestController({ kind: 'service', load(t, { signal }) {
    signals.push(signal); return signals.length === 1 ? old.promise : Promise.resolve(serviceReady(t));
  } }, s => states.push(s));
  const first = controller.load(serviceTarget);
  await controller.load({ ...serviceTarget, topicId: outcome === 'invalid-next' ? undefined : '10' });
  assert.equal(signals[0].aborted, true);
  if (outcome === 'failure') old.reject(new Error('stale')); else old.resolve(serviceReady());
  await first;
  assert.deepEqual(states.map(s => s.status), ['loading', 'loading', outcome === 'invalid-next' ? 'not-found' : 'ready']);
  if (outcome !== 'invalid-next') assert.equal(states.at(-1).article.topicId, '10');
  else assert.equal(signals.length, 1);
});

// Isolated hooks expose the pre-effect render where stale results could flash.
function hookRuntime() {
  let cursor = 0, slots = [], pending = [];
  const changed = (a, b) => !a || b.some((v, i) => !Object.is(v, a[i]));
  const react = { ...React,
    useState(initial) {
      const i = cursor++; slots[i] ??= { value: typeof initial === 'function' ? initial() : initial };
      return [slots[i].value, value => { slots[i].value = typeof value === 'function' ? value(slots[i].value) : value; }];
    },
    useRef(initial) { return slots[cursor++] ??= { current: initial }; },
    useEffect(effect, deps) {
      const i = cursor++, previous = slots[i];
      if (changed(previous?.deps, deps)) pending.push(() => { previous?.cleanup?.(); slots[i] = { deps, cleanup: effect() }; });
    },
    useCallback(fn, deps) {
      const i = cursor++; if (changed(slots[i]?.deps, deps)) slots[i] = { deps, value: fn }; return slots[i].value;
    },
    useSyncExternalStore(subscribe, snapshot) { return snapshot(); },
  };
  return { react, render: fn => { cursor = 0; return fn(); }, flush() { const jobs = pending; pending = []; jobs.forEach(fn => fn()); },
    reset() { slots.forEach(s => s?.cleanup?.()); slots = []; pending = []; } };
}

test('DetailController hides previous results before effects and reloads each identity change', async () => {
  const hooks = hookRuntime(), requests = [];
  const source = { kind: 'service', load(t, { signal }) {
    const wait = deferred(); requests.push({ target: plain(t), signal, ...wait }); return wait.promise;
  } };
  const { DetailController } = createLoader({ react: hooks.react })('components/pivot/detail-controller.tsx');
  let identity = serviceTarget;
  const draw = () => hooks.render(() => DetailController({ source, target: identity, onBack() {} }));
  const settle = async request => { request.resolve(serviceReady(request.target)); await new Promise(resolve => setImmediate(resolve)); };
  assert.equal(draw().props.state.status, 'loading'); hooks.flush(); await settle(requests[0]);
  let previous = draw(); hooks.flush(); assert.equal(previous.props.state.status, 'ready');
  for (const change of [{ topicId: '10' }, { contentId: '302' }, { postId: '302' }]) {
    identity = { ...identity, ...change };
    const next = draw(); assert.equal(next.props.state.status, 'loading'); assert.notEqual(next.key, previous.key);
    hooks.flush(); assert.deepEqual(requests.at(-1).target, identity);
    await settle(requests.at(-1)); previous = draw(); hooks.flush();
    assert.equal(previous.props.state.status, 'ready'); assert.deepEqual(plain(previous.props.state.article), plain(serviceReady(identity).article));
  }
  identity = { ...identity, topicId: '11' }; draw(); hooks.flush(); const late = requests.at(-1);
  identity = { ...identity, topicId: '12' }; draw(); hooks.flush();
  assert.equal(late.signal.aborted, true);
  await settle(requests.at(-1)); await settle(late);
  assert.equal(draw().props.state.article.topicId, '12'); hooks.flush(); hooks.reset();
});

// Real navigation callbacks with same-origin History/DOM fixtures, without HTTP or OAuth.
function navigationHarness(kind = 'service', initial = kind === 'demo' ? '/pivot/demo?view=home' : '/pivot/home?view=home') {
  const hooks = hookRuntime(), frames = new Map(), focus = [], scroll = [], trackWrites = [];
  let frame = 0, index = 0, tree;
  const entries = [{ href: initial, state: {} }];
  const window = { location: new URL(initial, 'http://localhost'), scrollY: 0,
    scrollTo(value) { scroll.push(value.top); this.scrollY = value.top; } };
  const document = { activeElement: null };
  const setLocation = () => { window.location = new URL(entries[index].href, 'http://localhost'); };
  window.history = {
    get state() { return entries[index].state; },
    replaceState(state, unused, href) { assert.equal(new URL(href, window.location).origin, 'http://localhost'); entries[index] = { href, state: structuredClone(state) }; state.__NA = true; setLocation(); },
    pushState(state, unused, href) { assert.equal(state.__NA, undefined, 'Do not copy Next router fields'); assert.equal(new URL(href, window.location).origin, 'http://localhost'); entries.splice(++index); entries.push({ href, state: structuredClone(state) }); state.__NA = true; setLocation(); },
    back() { if (index) { index--; setLocation(); } }, forward() { if (index < entries.length - 1) { index++; setLocation(); } },
  };
  const ids = kind === 'demo' ? target : serviceTarget;
  const focusable = label => ({ focus(options) { focus.push(label); assert.equal(options.preventScroll, true); } });
  const button = { dataset: { homeDetail: ids.contentId, savedContent: ids.contentId }, ...focusable('card') };
  const input = { value: '' };
  const track = { clientWidth: 327, scrollLeft: 0, scrollTo(value) { this.scrollLeft = value.left; trackWrites.push(value.left); } };
  const post = { dataset: { homePost: ids.postId }, querySelector: () => track };
  const dom = {
    querySelector(selector) { return selector === 'input[type=search]' ? input : focusable(selector); },
    querySelectorAll(selector) { return selector === '[data-home-post]' ? [post] : [button]; },
  };
  const HomeController = () => null, DetailController = () => null, MyPageController = () => null, SavedController = () => null;
  const { HomeDetailExperience } = createLoader({ react: hooks.react,
    'next/navigation': { useSearchParams: () => window.location.searchParams },
    './home-controller': { HomeController }, './detail-controller': { DetailController },
    './my-page-controller': { MyPageController }, './saved-controller': { SavedController },
  }, { window, document, requestAnimationFrame(fn) { frames.set(++frame, fn); return frame; }, cancelAnimationFrame(id) { frames.delete(id); } })('components/pivot/home-detail-experience.tsx');
  const props = { homeSource: { kind }, detailSource: { kind }, mySource: { kind }, savedSource: { kind } };
  const nodes = node => !node || typeof node !== 'object' ? [] : [node, ...React.Children.toArray(node.props?.children).flatMap(nodes)];
  function render() {
    tree = hooks.render(() => HomeDetailExperience(props));
    nodes(tree).filter(n => n.type === 'div' && n.props.ref).forEach(n => { n.props.ref.current = dom; });
    hooks.flush(); const jobs = [...frames.values()]; frames.clear(); jobs.forEach(fn => fn());
    tree = hooks.render(() => HomeDetailExperience(props)); hooks.flush();
  }
  render();
  return { window, input, track, focus, scroll, trackWrites, render,
    view: () => window.location.searchParams.get('view'), target: () => plain(nodes(tree).find(n => n.type === DetailController)?.props.target),
    home: () => nodes(tree).find(n => n.type === HomeController).props,
    detail: () => nodes(tree).find(n => n.type === DetailController).props,
    saved: () => nodes(tree).find(n => n.type === SavedController).props,
    my: () => nodes(tree).find(n => n.type === MyPageController).props,
    reload() { hooks.reset(); render(); }, back() { window.history.back(); render(); }, forward() { window.history.forward(); render(); },
  };
}

for (const kind of ['demo', 'service']) test(kind + ': Home, reload, history and My/Saved preserve identity and Home restoration', () => {
  const e = navigationHarness(kind), identity = kind === 'demo' ? target : serviceTarget;
  e.window.scrollY = 630; e.input.value = 'fixture search'; e.track.scrollLeft = 654;
  e.home().onViewDetail(identity); e.render();
  assert.equal(e.view(), 'detail'); assert.deepEqual(e.target(), identity);
  assert.equal(e.window.location.searchParams.has('topicId'), kind === 'service');
  e.reload(); assert.deepEqual(e.target(), identity);
  e.detail().onMy(); e.render(); assert.equal(e.view(), 'my'); assert.deepEqual(e.target(), identity);
  e.my().onSaved(); e.render(); assert.equal(e.view(), 'saved'); assert.deepEqual(e.target(), identity);
  e.reload(); assert.deepEqual(e.target(), identity);
  e.back(); assert.equal(e.view(), 'my'); e.back(); assert.equal(e.view(), 'detail'); assert.deepEqual(e.target(), identity);
  e.forward(); e.forward(); assert.deepEqual(e.target(), identity); e.back(); e.back();
  e.input.value = ''; e.track.scrollLeft = 0;
  e.detail().onBack(); e.render(); assert.equal(e.view(), 'home');
  assert.equal(e.window.scrollY, 630); assert.equal(e.home().restoreQuery, 'fixture search'); assert.equal(e.track.scrollLeft, 654); assert.equal(e.focus.at(-1), 'card');
  e.forward(); assert.deepEqual(e.target(), identity);
});

test('Direct URL parses Topic; Saved detail round trips retain old and new identities', () => {
  const e = navigationHarness('service', detailHref('/pivot/home', serviceTarget));
  assert.deepEqual(e.target(), serviceTarget);
  e.detail().onSaved(); e.render();
  const next = { postId: '401', contentId: '401', topicId: '20' };
  e.saved().onViewDetail(next); e.render(); assert.deepEqual(e.target(), next);
  assert.equal(e.window.location.searchParams.get('from'), 'saved');
  e.detail().onMy(); e.render(); e.reload(); assert.deepEqual(e.target(), next);
  e.back(); assert.deepEqual(e.target(), next);
  e.detail().onBack(); e.render(); assert.equal(e.view(), 'saved'); assert.deepEqual(e.target(), serviceTarget);
  e.back(); assert.equal(e.view(), 'detail'); assert.deepEqual(e.target(), serviceTarget);
  e.forward(); e.forward(); assert.deepEqual(e.target(), next);
});

test('Missing Topic stays missing and return parameters cannot redirect outside the local route', () => {
  const e = navigationHarness('service', '/pivot/home?view=detail&postId=301&contentId=301&returnTo=https://example.test/');
  assert.deepEqual(e.target(), { postId: '301', contentId: '301' });
  e.detail().onMy(); e.render(); assert.equal(e.window.location.searchParams.has('topicId'), false);
  e.my().onHome(); e.render();
  assert.equal(e.window.location.pathname, '/pivot/home'); assert.equal(e.view(), 'home'); assert.equal(e.window.location.origin, 'http://localhost');
});


test('Home and Saved card clicks forward the same optional Topic without substituting other IDs', () => {
  const hooks = hookRuntime(), loader = createLoader({ react: { ...hooks.react, useId: () => 'fixture' } });
  const { HomeCard } = loader('components/pivot/home-screen.tsx');
  const { SavedContentCard } = loader('components/pivot/saved-screen.tsx');
  const nodes = node => !node || typeof node !== 'object' ? [] : [node, ...React.Children.toArray(node.props?.children).flatMap(nodes)];
  for (const identity of [target, serviceTarget, { postId: '401', contentId: '401', topicId: '20' }]) {
    const calls = [], onViewDetail = t => calls.push(plain(t));
    const extra = { savedItemId: '777', recommendationId: '888', title: 'Fixture', slides: [] };
    const home = hooks.render(() => HomeCard({ card: { ...extra, id: identity.postId, detailContentId: identity.contentId, topicId: identity.topicId }, demo: !identity.topicId, onViewDetail }));
    nodes(home).find(n => n.props?.['data-home-detail']).props.onClick(); hooks.reset();
    const saved = hooks.render(() => SavedContentCard({ card: { ...extra, ...identity }, onViewDetail }));
    saved.props.onClick(); hooks.reset();
    assert.deepEqual(calls, [identity, identity]);
  }
});
