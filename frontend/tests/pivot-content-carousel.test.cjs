const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const root = path.join(__dirname, '..');
const plain = value => JSON.parse(JSON.stringify(value));
const settle = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve; const promise = new Promise(yes => { resolve = yes; }); return { promise, resolve }; };

function loader(globals = {}, react = React) {
  const modules = new Map();
  function load(file) {
    if (modules.has(file)) return modules.get(file).exports;
    const mod = { exports: {} }; modules.set(file, mod);
    const output = ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), { compilerOptions: {
      module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
    } }).outputText;
    vm.runInNewContext(output, { module: mod, exports: mod.exports, AbortController, URL, Event, ...globals,
      require(name) {
        if (name === '@/lib/api') return { API_UNAUTHORIZED_EVENT: 'api:unauthorized' };
        if (name === 'react') return react;
        if (name === 'react/jsx-runtime') return require(name);
        if (name.endsWith('.module.css')) return { default: new Proxy({}, { get: (_, key) => String(key) }), __esModule: true };
        const relative = name.startsWith('@/') ? name.slice(2) : path.join(path.dirname(file), name);
        return load(relative + (fs.existsSync(path.join(root, relative + '.tsx')) ? '.tsx' : '.ts'));
      },
    });
    return mod.exports;
  }
  return load;
}

function slides(count = 3) {
  return Array.from({ length: count }, (_, index) => ({ id: `page-${index + 1}`, order: index + 1, page: index + 1,
    role: index ? 'body' : 'cover', eyebrow: '작품 이야기', titleLines: [`제목 ${index + 1}`], bodySentences: [`본문 ${index + 1}`],
    description: `본문 ${index + 1}`, image: { src: `https://api.example.test/api/v1/contents/10/slides/${index + 1}/image?topicId=2`, alt: '', protected: true },
    width: 1308, height: 1632, layout: { textOverlayBox: { x: .06, y: .58, width: .88, height: .36 } },
  }));
}

test('Home preview renders every page and all source text with accessible controls', () => {
  const { ContentCarousel } = loader()('components/pivot/content-carousel.tsx');
  const pages = slides(10).reverse(); pages[9].titleLines = ['<script>unsafe</script>'];
  const html = renderToStaticMarkup(React.createElement(ContentCarousel, { slides: pages, title: '검수된 콘텐츠', scope: 'home' }));
  assert.equal((html.match(/data-carousel-slide=/g) || []).length, 10);
  assert.equal((html.match(/data-carousel-overlay=/g) || []).length, 10);
  assert.ok(html.indexOf('data-carousel-slide="1"') < html.indexOf('data-carousel-slide="10"'));
  assert.doesNotMatch(html, /style="left:/, 'Home preview uses its readable bottom area, not the constrained image-relative box');
  assert.equal((html.match(/aria-label="카드 미리보기"/g) || []).length, 10);
  assert.match(html, /전체 글과 슬라이드 원문은 자세히 보기/);
  for (const page of pages) for (const sentence of page.bodySentences) assert.ok(html.includes(sentence));
  assert.match(html, /&lt;script&gt;unsafe&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script|https:\/\/api\.example\.test/);
  assert.match(html, /data-home-track=""/);
  assert.match(html, /data-home-position=""/);
  assert.match(html, /aria-label="이전 슬라이드"[^>]*disabled=""/);
  assert.equal((html.match(/aria-current="true"/g) || []).length, 1);
  assert.equal((html.match(/inert=""/g) || []).length, 9);
});

test('Carousel buttons and keyboard use native horizontal scroll; observation does not correct gestures', () => {
  const hooks = [], writes = []; let cursor = 0, tree;
  const element = { clientWidth: 350, scrollTo(value) { writes.push(value); } };
  const react = { ...React, useId: () => 'carousel', useRef: () => hooks[cursor++] ??= { current: element },
    useState(initial) { const index = cursor++; hooks[index] ??= { value: initial }; return [hooks[index].value, value => { hooks[index].value = value; }]; },
  };
  const { ContentCarousel } = loader({}, react)('components/pivot/content-carousel.tsx');
  const render = () => { cursor = 0; tree = ContentCarousel({ slides: slides(), title: '본문', scope: 'home' }); };
  const all = (node = tree) => !node || typeof node !== 'object' ? [] : [node, ...React.Children.toArray(node.props?.children).flatMap(all)];
  render();
  all().find(node => node.props?.['data-carousel-track'] === '').props.onScroll({ currentTarget: { clientWidth: 350, scrollLeft: 350 } });
  render(); assert.equal(writes.length, 0);
  assert.equal(all().find(node => node.props?.['aria-current'] === 'true').props['aria-label'], '2번 슬라이드 보기');
  all().find(node => node.props?.['aria-label'] === '다음 슬라이드').props.onClick();
  assert.equal(writes.at(-1).left, 700);
  const track = all().find(node => node.props?.['data-carousel-track'] === ''), target = {}; let prevented = 0;
  const key = value => track.props.onKeyDown({ key: value, target, currentTarget: target, preventDefault() { prevented++; } });
  key('Home'); assert.equal(writes.at(-1).left, 0); key('End'); assert.equal(writes.at(-1).left, 700);
  key('ArrowDown'); assert.equal(prevented, 2);
});

function transport(response) {
  const calls = [], events = [], urls = [], revoked = [];
  const load = loader({ fetch: async (url, options) => { calls.push({ url, options }); return await response; },
    URL: { createObjectURL(blob) { urls.push(blob); return `blob:fixture-${urls.length}`; }, revokeObjectURL(url) { revoked.push(url); } },
    window: { dispatchEvent(event) { events.push(event.type); } },
  });
  return { ...load('components/pivot/content-carousel.tsx'), calls, events, urls, revoked };
}
const png = () => ({ ok: true, status: 200, headers: { get: () => 'image/png' }, blob: async () => ({ size: 12, type: 'image/png' }) });

test('Protected PNG fetch includes cookies, refuses redirect/cache, and releases its blob once on cleanup', async () => {
  const fixture = transport(png()), states = [];
  const stop = fixture.requestProtectedImage(slides()[0].image.src, state => states.push(plain(state)));
  await settle();
  assert.equal(fixture.calls[0].options.credentials, 'include');
  assert.equal(fixture.calls[0].options.cache, 'no-store'); assert.equal(fixture.calls[0].options.redirect, 'error');
  assert.deepEqual(states.at(-1), { status: 'ready', url: 'blob:fixture-1' });
  stop(); stop();
  assert.equal(fixture.calls[0].options.signal.aborted, true);
  assert.deepEqual(fixture.revoked, ['blob:fixture-1']);
});

test('Cancelled request cannot show late bytes or a late unauthorized response', async () => {
  for (const response of [png(), { status: 401, ok: false }]) {
    const pending = deferred(), fixture = transport(pending.promise), states = [];
    const stop = fixture.requestProtectedImage(slides()[0].image.src, state => states.push(plain(state)));
    stop(); pending.resolve(response); await settle();
    assert.deepEqual(states, [{ status: 'loading' }]); assert.equal(fixture.urls.length, 0); assert.equal(fixture.events.length, 0);
  }
});

test('Cancellation during blob read also prevents a stale object URL', async () => {
  const pending = deferred(), fixture = transport({ ...png(), blob: () => pending.promise }), states = [];
  const stop = fixture.requestProtectedImage(slides()[0].image.src, state => states.push(plain(state)));
  await settle(); stop(); pending.resolve({ size: 12 }); await settle();
  assert.equal(fixture.urls.length, 0); assert.deepEqual(states, [{ status: 'loading' }]);
});

test('401 clears the session through its event while 404 and invalid images remain explicit retryable failures', async () => {
  for (const response of [{ status: 401, ok: false }, { status: 404, ok: false }, { ...png(), headers: { get: () => 'text/html' } }, { ...png(), blob: async () => ({ size: 0 }) }]) {
    const fixture = transport(response), states = [];
    fixture.requestProtectedImage(slides()[0].image.src, state => states.push(plain(state))); await settle();
    assert.equal(states.at(-1).status, 'error'); assert.equal(fixture.urls.length, 0);
    assert.deepEqual(fixture.events, response.status === 401 ? ['api:unauthorized'] : []);
  }
});

test('Detail cover renders the first ordered slide with protected image and text, without carousel navigation', () => {
  const { ContentCover } = loader()('components/pivot/content-carousel.tsx');
  const html = renderToStaticMarkup(React.createElement(ContentCover, { slides: slides(10).reverse(), title: '검수된 콘텐츠' }));
  assert.equal((html.match(/data-content-cover=/g) || []).length, 1);
  assert.equal((html.match(/data-carousel-overlay=/g) || []).length, 1);
  assert.match(html, /제목 1/); assert.match(html, /본문 1/);
  const region = html.match(/style="left:([^;]+);top:([^;]+);width:([^;]+);height:([^"]+)"/);
  assert.ok(region); [6, 58, 88, 36].forEach((value, index) => assert.ok(Math.abs(parseFloat(region[index + 1]) - value) < 0.00001));
  assert.doesNotMatch(html, /제목 2|제목 10|data-detail-track|data-carousel-track|data-carousel-slide|이전 슬라이드|다음 슬라이드/);
  assert.match(html, /data-carousel-image-state="loading"/);
  assert.doesNotMatch(html, /<img|https:\/\/api\.example\.test/);
});

test('Full-body detail keeps a single cover followed by article paragraphs and source/AI/date footer, with bookmarks', () => {
  const { PivotDetailScreen } = loader()('components/pivot/detail-screen.tsx');
  const article = { postId: 'post-10', contentId: '10', topicId: 2, origin: 'service', title: '검수된 콘텐츠',
    displayMode: 'full_body', slides: slides(), productionType: 'ai', publishedAt: '2026-10-28T01:00:00Z',
    notices: [{ code: 'AI_GENERATED', message: 'AI가 생성한 콘텐츠입니다.' }], sourceUrl: 'https://example.test/one',
    sources: [{ url: 'https://example.test/one', title: '공식 발표', role: 'primary' }, { url: 'https://example.test/two', title: '두 번째 근거', role: 'citation' }],
    sections: [{ paragraphs: ['원본 상세 첫 문단', '원본 상세 둘째 문단'] }],
  };
  const html = renderToStaticMarkup(React.createElement(PivotDetailScreen, { state: { status: 'ready', article }, demo: false, onBack() {}, onRetry() {},
    bookmarks: { kind: 'service', has: () => false, isPending: () => false, toggle() {} } }));
  assert.equal((html.match(/data-content-cover=/g) || []).length, 1);
  assert.equal((html.match(/data-carousel-overlay=/g) || []).length, 1);
  assert.doesNotMatch(html, /data-detail-track|data-carousel-track|data-carousel-slide|이전 슬라이드|다음 슬라이드/);
  assert.match(html, /<h1[^>]*class="srOnly"/);
  assert.match(html, /원본 상세 첫 문단/); assert.match(html, /원본 상세 둘째 문단/);
  assert.ok(html.indexOf('data-content-cover=') < html.indexOf('원본 상세 첫 문단'));
  assert.ok(html.indexOf('원본 상세 둘째 문단') < html.indexOf('data-content-metadata='), 'metadata stays below the article, not between cover and body');
  assert.match(html, /href="https:\/\/example.test\/one"/); assert.match(html, /href="https:\/\/example.test\/two"/);
  assert.match(html, /rel="noopener noreferrer"/); assert.match(html, /<time dateTime="2026-10-28T01:00:00Z"/);
  assert.equal((html.match(/AI가 생성한 콘텐츠입니다\./g) || []).length, 1);
  assert.match(html, /data-service-bookmark="10"/); assert.doesNotMatch(html, /Demo 선택|원문에서 계속 읽기/);
});

test('Viewport gating and rapid disable/return never render a revoked image URL', async () => {
  const hooks = [], effects = [], revoked = [], requests = []; let cursor = 0, tree, observe;
  const react = { ...React,
    useRef(initial) { const index = cursor++; return hooks[index] ??= { current: initial }; },
    useState(initial) { const index = cursor++; hooks[index] ??= { value: initial }; return [hooks[index].value, value => { hooks[index].value = typeof value === 'function' ? value(hooks[index].value) : value; }]; },
    useEffect(effect, deps) {
      const index = cursor++, previous = hooks[index];
      if (previous?.deps && deps.every((value, i) => Object.is(value, previous.deps[i]))) return;
      hooks[index] = { deps, cleanup: previous?.cleanup };
      effects.push(() => { hooks[index].cleanup?.(); hooks[index].cleanup = effect(); });
    },
  };
  let created = 0;
  const { ProtectedSlideImage } = loader({
    IntersectionObserver: class { constructor(callback) { observe = value => callback([{ isIntersecting: value }]); } observe() {} disconnect() {} },
    fetch: async (url, options) => { requests.push(options); return png(); },
    URL: { createObjectURL: () => `blob:visited-${++created}`, revokeObjectURL: url => revoked.push(url) },
  }, react)('components/pivot/content-carousel.tsx');
  const all = (node = tree) => !node || typeof node !== 'object' ? [] : [node, ...React.Children.toArray(node.props?.children).flatMap(all)];
  const flush = () => { while (effects.length) effects.shift()(); };
  const draw = (enabled = true, commit = true) => {
    cursor = 0; tree = ProtectedSlideImage({ src: slides()[0].image.src, alt: '', enabled });
    tree.props.ref.current ??= {}; if (commit) flush();
  };
  draw(); await settle(); assert.equal(requests.length, 0, 'Far-away cards do not fetch');
  observe(true); draw(); await settle(); draw();
  assert.equal(all().find(node => node.type === 'img').props.src, 'blob:visited-1');
  draw(false); assert.deepEqual(revoked, ['blob:visited-1']);
  draw(true, false);
  assert.equal(all().some(node => node.type === 'img'), false, 'Return render must not use the revoked URL before effects');
  flush(); await settle(); draw();
  assert.equal(all().find(node => node.type === 'img').props.src, 'blob:visited-2');
  observe(false); draw(); assert.deepEqual(revoked, ['blob:visited-1', 'blob:visited-2']);
  assert.equal(requests.every(request => request.signal.aborted), true);
});

test('Protected Saved thumbnails keep one detail button and do not leak the authenticated URL into an img', () => {
  const { SavedContentCard } = loader()('components/pivot/saved-screen.tsx');
  const html = renderToStaticMarkup(React.createElement(SavedContentCard, { card: {
    postId: 'post-10', contentId: '10', topicId: 2, title: '저장한 캐러셀', image: slides()[0].image,
  }, onViewDetail() {} }));
  assert.equal((html.match(/<button/g) || []).length, 1);
  assert.match(html, /data-saved-content="10"/); assert.match(html, /data-carousel-image-state="loading"/);
  assert.doesNotMatch(html, /<img|https:\/\/api\.example\.test/);
});

function detailTextFixture(pages, paragraphs, mode = 'full_body', demo = false) {
  const { PivotDetailScreen } = loader()('components/pivot/detail-screen.tsx');
  const article = { postId: 'post-10', contentId: '10', topicId: 2, origin: 'service', title: '글 제목',
    displayMode: mode, slides: pages, sections: [{ paragraphs }], sourceUrl: 'https://example.test/source',
    productionType: 'ai', notices: [{ code: 'AI_GENERATED', message: 'AI가 생성한 콘텐츠입니다.' }] };
  const before = JSON.stringify(article);
  const html = renderToStaticMarkup(React.createElement(PivotDetailScreen,
    { state: { status: 'ready', article }, demo, onBack() {}, onRetry() {} }));
  assert.equal(JSON.stringify(article), before, 'display filtering never mutates the source');
  const start = html.indexOf('data-detail-slide-text=');
  return { html, extra: start < 0 ? '' : html.slice(start, html.indexOf('<footer data-detail-publication=', start)) };
}
const occurrences = (text, value) => text.split(value).length - 1;

test('Four article paragraphs repeated across ten slides add no forty duplicate paragraphs or empty section', () => {
  const paragraphs = ['기존 첫 문단.', '기존 둘째 문단.', '기존 셋째 문단.', '기존 넷째 문단.'];
  const pages = slides(10).map(page => ({ ...page, titleLines: ['대표 제목'], bodySentences: [...paragraphs] }));
  const { html, extra } = detailTextFixture(pages, paragraphs);
  assert.equal(extra, '');
  for (const paragraph of paragraphs) assert.equal(occurrences(html, paragraph), 2, 'one existing article paragraph and one unchanged cover paragraph');
  const differentTitles = pages.map((page, i) => ({ ...page, titleLines: ['고유 제목 ' + (i + 1)] }));
  const unique = detailTextFixture(differentTitles, paragraphs).extra;
  assert.equal((unique.match(/data-detail-slide-page=/g) || []).length, 9);
  for (const paragraph of paragraphs) assert.equal(occurrences(unique, paragraph), 0);
});

test('Every missing title and sentence from pages two through ten remains readable in page order', () => {
  const pages = slides(10).reverse();
  for (const page of pages) page.bodySentences = Array.from({ length: 4 }, (_, i) => page.page + '번의 새로운 원문 ' + i + '.');
  pages[0].titleLines = ['긴 제목 '.repeat(80), '<script>original</script>'];
  pages[0].bodySentences[3] = '마지막 원문 '.repeat(90);
  const { html, extra } = detailTextFixture(pages, ['독립 상세 본문']);
  assert.equal((extra.match(/data-detail-slide-page=/g) || []).length, 9);
  assert.ok(extra.indexOf('data-detail-slide-page="2"') < extra.indexOf('data-detail-slide-page="10"'));
  assert.ok(html.indexOf('독립 상세 본문') < html.indexOf('data-detail-slide-text='));
  for (const page of pages.filter(page => page.page !== 1)) {
    for (const sentence of page.bodySentences) assert.equal(occurrences(extra, sentence), 1);
  }
  assert.ok(extra.includes(pages[0].titleLines[0]));
  assert.match(extra, /&lt;script&gt;original&lt;\/script&gt;/);
  assert.doesNotMatch(extra, /<script|data-carousel-track|data-carousel-overlay/);
});

test('Repeated titles and sentences across later slides are displayed once', () => {
  const pages = slides();
  pages[1].titleLines = ['같은 새 제목']; pages[2].titleLines = ['같은 새 제목'];
  pages[1].bodySentences = ['공통 추가 문장.', '두 번째만의 원문.'];
  pages[2].bodySentences = ['공통 추가 문장.', '세 번째만의 원문.'];
  const { extra } = detailTextFixture(pages, ['독립 본문']);
  for (const text of ['같은 새 제목', '공통 추가 문장.', '두 번째만의 원문.', '세 번째만의 원문.']) assert.equal(occurrences(extra, text), 1);
});

test('Only whitespace and linebreak differences are normalized; displayed new text is unchanged', () => {
  const pages = slides();
  pages[1].titleLines = ['  기존\n제목  ']; pages[2].titleLines = ['새로운', '제목'];
  pages[1].bodySentences = ['\n기존\t문장입니다.  ', '  새\t원문은\n그대로입니다.  '];
  pages[2].bodySentences = ['새 원문은 그대로입니다.'];
  const { extra } = detailTextFixture(pages, ['기존 제목', '기존 문장입니다.', '새로운 제목']);
  assert.doesNotMatch(extra, /기존|새로운|data-detail-slide-page="3"/);
  assert.equal(occurrences(extra, '  새\t원문은\n그대로입니다.  '), 1);
});

test('Partial matches, different facts, punctuation and case are preserved without fuzzy deduplication', () => {
  const pages = slides(), distinct = ['공연은 토요일입니다.', '공연은 금요일입니다. 입장은 무료입니다.', '금요일입니다.', '공연은 금요일입니다!', 'title'];
  pages[1].bodySentences = distinct.slice(0, 4); pages[2].bodySentences = distinct.slice(4);
  const { extra } = detailTextFixture(pages, ['공연은 금요일입니다.', 'Title']);
  for (const sentence of distinct) assert.equal(occurrences(extra, '<p>' + sentence + '</p>'), 1);
});

test('The first ordered cover seeds only text it actually displays', () => {
  const pages = slides();
  pages[1].titleLines = [...pages[0].titleLines]; pages[1].bodySentences = [...pages[0].bodySentences];
  let result = detailTextFixture([...pages].reverse(), ['독립 본문']);
  assert.doesNotMatch(result.extra, /data-detail-slide-page="1"|data-detail-slide-page="2"/);
  assert.match(result.html, /data-carousel-image-state="loading"/);
  // A legacy cover renders its description, not otherwise unused structured text.
  delete pages[0].layout; pages[0].description = '대표 카드 설명';
  result = detailTextFixture(pages, ['독립 본문']);
  assert.match(result.extra, /data-detail-slide-page="1"/);
  assert.equal(occurrences(result.extra, '제목 1'), 1);
  assert.equal(occurrences(result.extra, '본문 1'), 1);
  assert.doesNotMatch(result.extra, /대표 카드 설명/);
});

test('Absent slides and Demo never produce a new original-text section', () => {
  assert.equal(detailTextFixture([], ['기존 본문']).extra, '');
  assert.equal(detailTextFixture(slides(), ['기존 본문'], 'full_body', true).extra, '');
});

for (const mode of ['full_body', 'link_excerpt']) test(mode + ': visible sections take precedence and existing source/AI/cover remain', () => {
  const pages = slides(), visible = mode === 'full_body' ? '전체 본문에 있는 내용.' : '발췌에 있는 내용.';
  pages[1].bodySentences = [visible, '두 번째 슬라이드에만 있는 내용.'];
  pages[2].bodySentences = ['두 번째 슬라이드에만 있는 내용.', '세 번째 슬라이드에만 있는 내용.'];
  const { html, extra } = detailTextFixture(pages, [visible], mode);
  assert.equal(occurrences(html, '<p>' + visible + '</p>'), 1);
  assert.equal(occurrences(extra, '두 번째 슬라이드에만 있는 내용.'), 1);
  assert.equal(occurrences(extra, '세 번째 슬라이드에만 있는 내용.'), 1);
  assert.equal((html.match(/data-content-cover=/g) || []).length, 1);
  assert.doesNotMatch(html, /data-carousel-track=/);
  assert.equal(occurrences(html, 'AI가 생성한 콘텐츠입니다.'), 1);
  assert.equal(html.includes('원문에서 계속 읽기'), mode === 'link_excerpt');
  assert.match(html, /aria-label="뒤로가기"/);
});

test('Consecutive keyboard moves use settled DOM position even before onScroll state rerenders', () => {
  const writes = [], element = { clientWidth: 350, scrollLeft: 0, scrollTo(value) { writes.push(value); } };
  const react = { ...React, useId: () => 'keys', useRef: () => ({ current: element }), useState: value => [value, () => {}] };
  const { ContentCarousel } = loader({}, react)('components/pivot/content-carousel.tsx');
  const tree = ContentCarousel({ slides: slides(), title: '키보드', scope: 'home' });
  const all = node => !node || typeof node !== 'object' ? [] : [node, ...React.Children.toArray(node.props?.children).flatMap(all)];
  const track = all(tree).find(node => node.props?.['data-carousel-track'] === '');
  const key = value => track.props.onKeyDown({ key: value, target: element, currentTarget: element, preventDefault() {} });
  key('ArrowRight'); assert.equal(writes.at(-1).left, 350);
  element.scrollLeft = 350; key('ArrowRight'); assert.equal(writes.at(-1).left, 700);
  element.scrollLeft = 700; key('ArrowLeft'); assert.equal(writes.at(-1).left, 350);
  key('Home'); element.scrollLeft = 0; key('ArrowRight'); assert.equal(writes.at(-1).left, 350);
});
