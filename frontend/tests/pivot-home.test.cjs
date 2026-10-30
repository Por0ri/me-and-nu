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
function createLoader(react = React) {
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
      if (name === '@/lib/api') return {};
      if (name === './services') return { requirePivotApi() { throw new Error('Test origin not enabled'); } };
      if (name === 'react') return react;
      if (name === 'react/jsx-runtime') return require(name);
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
return load;
}
const load = createLoader();
const { createHomeRequestController, orderedHomeSlides } = load('lib/pivot/home.ts');
const { mockHomeSource } = load('lib/pivot/home-mock.ts');
const { pendingHomeSource } = load('lib/pivot/home-service.ts');
const { PivotHomeScreen, HomeCard } = load('components/pivot/home-screen.tsx');
const { HomeController } = load('components/pivot/home-controller.tsx');
const plain = value => JSON.parse(JSON.stringify(value));
const cardFixture = (id = 'example') => ({ id, title: id, detailContentId: 'content-' + id, slides: [
  { id: id + '-later', order: 2, description: 'Second slide' },
  { id: id + '-first', order: 1, description: 'First slide' },
  { id: id + '-last', order: 3, description: 'Last slide', image: { src: '/display.png', alt: 'Display example' } },
] });
const result = (id = 'example', cards) => ({ status: 'ready', home: { origin: 'demo', cards: cards || [cardFixture(id)] } });
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

test('A blocked Service origin renders an error without Mock cards or demo controls', async () => {
  assert.equal(pendingHomeSource.kind, 'service');
  const states = [];
  await createHomeRequestController(pendingHomeSource, state => states.push(state)).load();
  assert.deepEqual(states.map(state => state.status), ['loading', 'error']);
  assert.doesNotMatch(render(states.at(-1), false), /시연용|<article|처음부터 시연/);
});

test('A service transport failure stays an error without falling back to Demo content', async () => {
  const states = [];
  await createHomeRequestController({ kind: 'service', async load() { throw new Error('Service unavailable'); } }, state => states.push(state)).load();
  assert.deepEqual(states.map(state => state.status), ['loading', 'error']);
  const html = render(states.at(-1), false);
  assert.match(html, /role="alert"/); assert.match(html, /다시 시도/);
  assert.doesNotMatch(html, /시연용|demo-home|<article|data-home-track|Service unavailable/);
});

test('Mock gives independent copies and has no reaction aggregates or onboarding mapping', async () => {
  const first = await mockHomeSource.load({ signal: new AbortController().signal });
  const second = await mockHomeSource.load({ signal: new AbortController().signal });
  assert.deepEqual(plain(first), plain(second));
  first.home.cards[0].title = 'Modified in one instance';
  assert.notEqual(second.home.cards[0].title, first.home.cards[0].title);
  first.home.cards[0].slides[0].description = 'Changed nested copy';
  assert.notEqual(second.home.cards[0].slides[0].description, first.home.cards[0].slides[0].description);
  for (const card of second.home.cards) {
    assert.deepEqual(Object.keys(card).sort(), ['detailContentId', 'id', 'slides', 'title']);
    assert.ok(card.slides.length > 1);
    assert.equal(new Set(card.slides.map(slide => slide.id)).size, card.slides.length);
  }
  assert.deepEqual(second.home.cards.map(card => card.slides.length), [5, 3, 2]);
  assert.equal(new Set(second.home.cards.map(card => card.detailContentId)).size, 3);
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
    assert.doesNotMatch(html, /댓글|공유 수|role="tablist"/);
    assert.equal((html.match(/aria-label="좋아요" aria-disabled="true"/g) || []).length, state.status === 'ready' ? 3 : 0);
    assert.equal((html.match(/aria-roledescription="캐러셀"/g) || []).length, state.status === 'ready' ? 3 : 0);
  }
  const serviceHtml = render({ status: 'ready', home: { origin: 'service', cards: [cardFixture('service-story')] } }, false);
  assert.match(serviceHtml, /<article/);
  assert.doesNotMatch(serviceHtml, /시연용|처음부터 시연/);
  const initialController = renderToStaticMarkup(React.createElement(HomeController, { source: pendingHomeSource }));
  assert.match(initialController, /data-home-state="loading"/);
});

test('Slides preserve explicit order without mutating source; counts and indicators match', () => {
  const card = cardFixture();
  const before = plain(card.slides);
  assert.deepEqual(plain(orderedHomeSlides(card.slides)).map(slide => slide.order), [1, 2, 3]);
  assert.deepEqual(plain(card.slides), before);
  const html = render(result('example', [card]));
  assert.ok(html.indexOf('First slide') < html.indexOf('Second slide'));
  assert.ok(html.indexOf('Second slide') < html.indexOf('Last slide'));
  assert.equal((html.match(/aria-roledescription="슬라이드"/g) || []).length, 3);
  for (let i = 1; i <= 3; i++) assert.match(html, new RegExp('aria-label="' + i + ' / 3"'));
  assert.equal((html.match(/aria-current="true"/g) || []).length, 1);
  assert.match(html, /aria-label="1번 슬라이드 보기" aria-current="true"/);
  assert.match(html, /현재 슬라이드 <\/span>1 \/ 3/);
  assert.match(html, /aria-label="이전 슬라이드"[^>]*disabled=""/);
});

test('Cards escape content and show safe missing-image and no-slide states without fake URLs', () => {
  const card = { ...cardFixture('safe'), title: '<script>unsafe</script>' };
  const html = renderToStaticMarkup(React.createElement(HomeCard, { demo: true, card }));
  assert.match(html, /&lt;script&gt;/);
  assert.equal((html.match(/aria-label="이미지 준비 중"/g) || []).length, 2);
  assert.match(html, /<figcaption[^>]*>First slide/);
  assert.match(html, /자세히 보기/);
  assert.match(html, /상세 화면은 준비 중/);
  assert.doesNotMatch(html, /<script|<a |src=""/);
  const empty = renderToStaticMarkup(React.createElement(HomeCard, { demo: true, card: { ...card, slides: [] } }));
  assert.match(empty, /아직 표시할 슬라이드/);
  assert.doesNotMatch(empty, /현재 슬라이드|data-home-track/);
});

// Invoke the actual component event handlers with isolated hook stores. Browser
// tests additionally exercise hydration, native scrolling and failed image loads.
function cardHarness(card, onViewDetail) {
  const hooks = [], writes = [];
  const element = { clientWidth: 327, scrollTo(options) { writes.push(options); } };
  let cursor = 0, tree;
  const react = { ...React, useId: () => card.id, useContext: () => null, useSyncExternalStore: () => 0, useEffect: () => {},
    useRef(initial) { return hooks[cursor++] ??= { current: initial ?? element }; },
    useState(initial) {
      const index = cursor++;
      hooks[index] ??= { value: initial };
      return [hooks[index].value, value => { hooks[index].value = value; }];
    },
  };
  const { HomeCard: Component } = createLoader(react)('components/pivot/home-screen.tsx');
  function render() { cursor = 0; tree = Component({ card, demo: true, onViewDetail }); }
  function all(node = tree) {
    if (!node || typeof node !== 'object') return [];
    return [node, ...React.Children.toArray(node.props?.children).flatMap(child => all(child))];
  }
  render();
  return { writes, render, find: predicate => all().find(predicate),
    current: () => all().filter(node => node.props?.['aria-current'] === 'true').map(node => node.props['aria-label']),
    observe(index) {
      const track = all().find(node => node.props?.['data-home-track'] === '');
      track.props.onScroll({ currentTarget: { clientWidth: 327, scrollLeft: index * 327 } }); render();
    },
  };
}

test('Each post observes its own scroll without JS snap correction; buttons and keys navigate explicitly', () => {
  const first = cardHarness(cardFixture('first')), second = cardHarness(cardFixture('second'));
  first.observe(2);
  assert.deepEqual(first.current(), ['3번 슬라이드 보기']);
  assert.deepEqual(second.current(), ['1번 슬라이드 보기']);
  assert.deepEqual(first.writes, [], 'Observation must never correct native scroll');
  second.observe(1);
  assert.deepEqual(first.current(), ['3번 슬라이드 보기']);
  assert.deepEqual(second.current(), ['2번 슬라이드 보기']);
  const button = second.find(node => node.props?.['aria-label'] === '다음 슬라이드');
  button.props.onClick(); assert.equal(second.writes.at(-1).left, 654);
  const track = first.find(node => node.props?.['data-home-track'] === '');
  const target = {};
  let prevented = 0;
  const key = value => track.props.onKeyDown({ key: value, target, currentTarget: target, preventDefault() { prevented++; } });
  key('ArrowDown'); assert.equal(prevented, 0, 'Vertical keys remain native');
  key('Home'); assert.equal(first.writes.at(-1).left, 0);
  key('ArrowLeft'); assert.equal(first.writes.at(-1).left, 327);
  key('End'); assert.equal(first.writes.at(-1).left, 654);
  assert.equal(prevented, 3);
});

test('Detail callbacks preserve post/content IDs and missing callback gives inline feedback', () => {
  const targets = [], card = cardFixture('identity');
  const connected = cardHarness(card, target => targets.push(plain(target)));
  connected.find(node => node.props?.['data-home-detail']).props.onClick();
  assert.deepEqual(targets, [{ postId: 'identity', contentId: 'content-identity' }]);
  const pending = cardHarness(card);
  pending.find(node => node.props?.['data-home-detail']).props.onClick(); pending.render();
  assert.equal(pending.find(node => node.props?.className === 'detailHelp').props.children, '자세한 콘텐츠 화면을 준비하고 있어요.');
});

test('Native CSS keeps horizontal snap and vertical document chaining with reduced motion support', () => {
  const css = fs.readFileSync(path.join(root, 'components/pivot/home.module.css'), 'utf8');
  assert.match(css, /scroll-snap-type:\s*x mandatory/);
  assert.match(css, /grid-auto-columns:\s*100%/);
  assert.match(css, /touch-action:\s*auto/);
  assert.match(css, /overscroll-behavior-y:\s*auto/);
  assert.match(css, /prefers-reduced-motion: reduce/);
  const source = fs.readFileSync(path.join(root, 'components/pivot/home-screen.tsx'), 'utf8');
  assert.doesNotMatch(source, /gesture-spike|onTouch|onPointer|scrollend|requestAnimationFrame|setTimeout/);
});

test('All five Figma slide assets are local, match provenance hashes, and are used by the fixed Mock', async () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(root, 'public/pivot/home/sources.json'), 'utf8'));
  const { home } = await mockHomeSource.load({ signal: new AbortController().signal });
  const used = new Set(home.cards.flatMap(card => card.slides.flatMap(slide => slide.image ? [slide.image.src] : [])));
  assert.equal(manifest.nodeId, '1011:3544'); assert.equal(manifest.assets.length, 5);
  for (const asset of manifest.assets) {
    assert.ok(used.has(asset.path));
    const bytes = fs.readFileSync(path.join(root, 'public', asset.path));
    assert.equal(crypto.createHash('sha256').update(bytes).digest('hex'), asset.sha256);
    assert.equal(bytes.readUInt32BE(16), 297); assert.equal(bytes.readUInt32BE(20), 371);
  }
  assert.equal(used.size, 5);
});

test('H2-B chrome uses local Figma assets and a single HOME/SAVE/MY default state', async () => {
  const { home } = await mockHomeSource.load({ signal: new AbortController().signal });
  const html = render({ status: 'ready', home });
  for (const filename of ['logo.svg', 'mascot.png', 'notification.svg', 'search.svg', 'share.svg', 'like.svg', 'save.svg', 'detail-arrow.svg', 'ai.svg']) {
    assert.ok(html.includes('/pivot/home/chrome/' + filename), filename);
  }
  assert.match(html, /aria-label="주요 메뉴"/);
  for (const label of ['HOME', 'SAVE', 'MY']) assert.ok(html.includes('>' + label + '</span>'));
  assert.doesNotMatch(html, />TALK<|role="dialog"|9:41|imgWifi|imgBatteryEnd|aria-pressed=/);
  assert.equal((html.match(/aria-label="저장" aria-disabled="true"/g) || []).length, 3);
  assert.match(html, /aria-label="AI 콘텐츠 탐색" aria-disabled="true"/);
});

test('H2-B reaction placeholders announce pending work without claiming a saved result', () => {
  const card = cardHarness(cardFixture('pending-action'));
  card.find(node => node.props?.['aria-label'] === '저장').props.onClick(); card.render();
  assert.equal(card.find(node => node.props?.className === 'actionNotice').props.children, '저장 기능은 준비 중이에요. 아직 반영되지 않아요.');
  assert.equal(card.find(node => node.props?.['aria-label'] === '저장').props['aria-pressed'], undefined);
});

test('H2-B chrome and authored Splash pose assets retain their recorded hashes', () => {
  for (const file of ['public/pivot/home/chrome/sources.json', 'public/pivot/splash/sources.json']) {
    const manifest = JSON.parse(fs.readFileSync(path.join(root, file), 'utf8'));
    assert.equal(manifest.page, '범수님');
    for (const asset of manifest.assets) {
      const bytes = fs.readFileSync(path.join(root, 'public', asset.path));
      assert.equal(sourceAssetHash(asset.path, bytes), asset.sha256);
    }
  }
});
