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
  assert.match(html, /상세 본문은 자세히 보기/);
  assert.doesNotMatch(html, /전체 글과 슬라이드 원문|슬라이드 추가 원문/);
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

test('Detail cover reuses ordered carousel pages with protected images and original text', () => {
  const { ContentCover } = loader()('components/pivot/content-carousel.tsx');
  const html = renderToStaticMarkup(React.createElement(ContentCover, { slides: slides(10).reverse(), title: '검수된 콘텐츠' }));
  assert.equal((html.match(/data-content-cover=/g) || []).length, 1);
  assert.equal((html.match(/data-carousel-overlay=/g) || []).length, 10);
  assert.match(html, /제목 1/); assert.match(html, /본문 1/);
  const region = html.match(/style="left:([^;]+);top:[^;]+;width:([^;]+);height:([^;]+);/);
  assert.ok(region); [6, 88, 36].forEach((value, index) => assert.ok(Math.abs(parseFloat(region[index + 1]) - value) < 0.00001));
  assert.match(html, /상세 본문과 출처는 카드뉴스 아래/);
  assert.doesNotMatch(html, /슬라이드 추가 원문/);
  assert.doesNotMatch(html, /본문 안에서 위아래로 스크롤/);
  assert.match(html, /data-content-carousel="detail"/);
  assert.equal((html.match(/data-detail-track=/g) || []).length, 1);
  assert.equal((html.match(/inert=""/g) || []).length, 9);
  assert.ok(html.indexOf('data-carousel-slide="1"') < html.indexOf('data-carousel-slide="10"'));
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
  assert.equal((html.match(/data-carousel-overlay=/g) || []).length, 3);
  assert.equal((html.match(/data-detail-track=/g) || []).length, 1);
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
  const load = loader();
  const { PivotDetailScreen } = load('components/pivot/detail-screen.tsx');
  const { InteractionProvider } = load(path.join('components', 'pivot', 'interaction-context.tsx'));
  const article = { postId: 'post-10', contentId: '10', topicId: 2, origin: 'service', title: '글 제목',
    displayMode: mode, slides: pages, sections: [{ heading: '상세 본문', paragraphs }], sourceUrl: 'https://example.test/source',
    sources: [{ url: 'https://example.test/source', title: '공식 출처', publisher: '발행 기관' }],
    publishedAt: '2026-11-02T00:00:00Z', publisher: '게시 기관',
    productionType: 'ai', notices: [{ code: 'AI_GENERATED', message: 'AI가 생성한 콘텐츠입니다.' }] };
  const before = JSON.stringify(article);
  const scope = { activity: {}, reactions: { value: () => ({ liked: false, preference: null }), isPending: () => false } };
  const html = renderToStaticMarkup(React.createElement(InteractionProvider, { scope },
    React.createElement(PivotDetailScreen, { state: { status: 'ready', article }, demo, onBack() {}, onRetry() {} })));
  assert.equal(JSON.stringify(article), before, 'rendering never mutates article paragraphs, slides or metadata');
  assert.doesNotMatch(html, /data-detail-slide-text|data-detail-slide-page|슬라이드 추가 원문|<h[23][^>]*>슬라이드 \d/);
  const body = html.slice(html.indexOf('<div class="body">'), html.indexOf('</article>'));
  return { html, body };
}
const occurrences = (text, value) => text.split(value).length - 1;

for (const mode of ['full_body', 'link_excerpt']) test(mode + ': article, publication, sources and feedback follow the carousel without a transcript', () => {
  const paragraphs = ['  첫\t문단의\n원문입니다.  ', '둘째 문단!', '둘째 문단!'];
  const pages = slides(10).reverse();
  pages[0].bodySentences = ['카드에만 있는 문장.'];
  const { html, body } = detailTextFixture(pages, paragraphs, mode);
  assert.match(body, /<h2>상세 본문<\/h2>/);
  assert.ok(body.includes(paragraphs.map(p => '<p>' + p + '</p>').join('')), 'paragraph whitespace, order and repetitions remain exact');
  assert.doesNotMatch(body, /카드에만 있는 문장|제목 10/);
  assert.equal(occurrences(html, '카드에만 있는 문장.'), 1, 'card-only text stays in its card');
  assert.equal((html.match(/data-carousel-slide=/g) || []).length, 10);
  assert.ok(html.indexOf('data-carousel-slide="1"') < html.indexOf('data-carousel-slide="10"'));
  assert.ok(html.indexOf('data-content-cover=') < html.indexOf('<div class="body">'));
  assert.ok(body.indexOf(paragraphs[2]) < body.indexOf('data-detail-publication='));
  assert.ok(body.indexOf('data-detail-publication=') < body.indexOf('aria-label="콘텐츠 출처"'));
  assert.ok(body.indexOf('aria-label="콘텐츠 출처"') < body.indexOf('detail-feedback-title'));
  assert.match(body, /href="https:\/\/example.test\/source" target="_blank" rel="noopener noreferrer"/);
  assert.match(body, />공식 출처<\/a><span>발행 기관<\/span>/);
  assert.match(body, /게시 기관/);
  assert.match(body, /<time dateTime="2026-11-02T00:00:00Z"/);
  assert.equal(occurrences(body, 'AI가 생성한 콘텐츠입니다.'), 1);
  assert.match(body, /더 볼래요/); assert.match(body, /그만 볼래요/);
  assert.equal(body.includes('원문에서 계속 읽기'), mode === 'link_excerpt');
});

test('Repeated article and slide copy stays unchanged in its original locations only', () => {
  const paragraphs = ['기존 첫 문단.', '기존 둘째 문단.', '기존 셋째 문단.', '기존 넷째 문단.'];
  const pages = slides(10).map(page => ({ ...page, titleLines: ['대표 제목'], bodySentences: [...paragraphs] }));
  const { html, body } = detailTextFixture(pages, paragraphs);
  for (const paragraph of paragraphs) {
    assert.equal(occurrences(body, paragraph), 1);
    assert.equal(occurrences(html, paragraph), pages.length + 1);
  }
  assert.equal(occurrences(html, '대표 제목'), pages.length);
});

test('Long and distinct slide originals remain escaped and unmodified in the carousel only', () => {
  const pages = slides(10).reverse();
  pages[0].titleLines = ['긴 제목 '.repeat(80), '<script>original</script>'];
  pages[0].bodySentences = ['  긴\t원문입니다.\n'.repeat(90), 'Title', 'title', '공연은 금요일입니다!', '공연은 금요일입니다.'];
  const { html, body } = detailTextFixture(pages, ['독립 상세 본문']);
  for (const page of pages) for (const sentence of page.bodySentences) {
    assert.ok(html.includes('<p>' + sentence + '</p>'));
    assert.equal(body.includes('<p>' + sentence + '</p>'), false);
  }
  assert.ok(html.includes(pages[0].titleLines[0]));
  assert.match(html, /&lt;script&gt;original&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script/);
});

test('Legacy cards keep their existing description without promoting unused structured fields into the article', () => {
  const pages = slides(); delete pages[0].layout; pages[0].description = '대표 카드 설명';
  const { html, body } = detailTextFixture(pages, ['독립 본문']);
  assert.equal(occurrences(html, '대표 카드 설명'), 1);
  assert.doesNotMatch(html, /제목 1|본문 1/);
  assert.doesNotMatch(body, /대표 카드 설명/);
});

test('Absent slides and Demo preserve article text and never add a transcript', () => {
  for (const pages of [undefined, [], slides()]) for (const demo of [false, true]) {
    const { body } = detailTextFixture(pages, ['기존 본문'], 'full_body', demo);
    assert.equal(occurrences(body, '<p>기존 본문</p>'), 1);
  }
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

test('Detail uses the actual slide count and leaves the supplied source unchanged', () => {
  const { ContentCover } = loader()('components/pivot/content-carousel.tsx');
  for (const count of [0, 1, 3, 7, 10]) {
    const pages = slides(count).reverse(), before = JSON.stringify(pages);
    const html = renderToStaticMarkup(React.createElement(ContentCover, { slides: pages, title: '가변 슬라이드' }));
    assert.equal((html.match(/data-carousel-slide=/g) || []).length, count);
    if (count) assert.ok(html.includes(`aria-label="${count} / ${count}"`));
    else assert.equal(html, '');
    assert.equal(JSON.stringify(pages), before);
  }
});

test('Home keeps supplied title line breaks and the complete three-line body', () => {
  const { ContentCarousel } = loader()('components/pivot/content-carousel.tsx');
  const pages = slides(); pages[0].titleLines = ['첫째 제목', '둘째 제목'];
  pages[0].bodySentences = ['첫 문장\n둘째 줄\n셋째 줄'];
  const html = renderToStaticMarkup(React.createElement(ContentCarousel, { slides: pages, title: '줄바꿈' }));
  assert.match(html, /첫째 제목\n둘째 제목/);
  assert.match(html, /첫 문장\n둘째 줄\n셋째 줄/);
});

function pointerFixture(scope, count = 3) {
  const all = node => !node || typeof node !== 'object' ? [] : [node, ...React.Children.toArray(node.props?.children).flatMap(all)];
  let left = 0, refs = 0;
  const writes = [], element = { clientWidth: 327, dataset: {}, captured: false,
    get scrollLeft() { return left; }, set scrollLeft(value) { left = Math.max(0, Math.min(value, (count - 1) * this.clientWidth)); },
    scrollTo(value) { writes.push(value.left); this.scrollLeft = value.left; }, setPointerCapture() { this.captured = true; },
    hasPointerCapture() { return this.captured; }, releasePointerCapture() { this.captured = false; } };
  const react = { ...React, useId: () => 'drag', useRef: initial => ({ current: refs++ === 0 ? element : initial }), useState: value => [value, () => {}] };
  const { ContentCarousel } = loader({}, react)('components/pivot/content-carousel.tsx');
  const track = all(ContentCarousel({ slides: slides(count), title: '드래그', scope })).find(node => node.props?.['data-carousel-track'] === '').props;
  const event = (x, y = 50, options = {}) => ({ currentTarget: element, target: { closest: () => null }, pointerId: 1,
    pointerType: 'mouse', button: 0, buttons: 1, clientX: x, clientY: y, prevented: false,
    preventDefault() { this.prevented = true; }, ...options });
  const click = (detail = 1) => {
    const e = { detail, prevented: false, stopped: false, preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; } };
    track.onClickCapture(e); return e;
  };
  return { track, element, writes, event, click };
}

for (const scope of ['home', 'detail']) {
  test(`${scope}: left mouse drag follows both directions, snaps and consumes only its release click`, () => {
    const { track, element, writes, event, click } = pointerFixture(scope);
    for (const [from, to, offset, snapped] of [[280, 60, 220, 327], [60, 280, 107, 0]]) {
      track.onPointerDown(event(from)); track.onPointerMove(event(to));
      assert.equal(element.scrollLeft, offset); assert.equal(element.dataset.dragging, 'true'); assert.equal(element.captured, true);
      track.onPointerUp(event(to));
      assert.equal(writes.at(-1), snapped); assert.equal(element.captured, false); assert.equal(element.dataset.dragging, undefined);
      assert.equal(click(0).prevented, false, 'keyboard activation remains available');
      const release = click(); assert.equal(release.prevented, true); assert.equal(release.stopped, true);
      assert.equal(click().prevented, false, 'only the release click is consumed');
    }
  });

  test(`${scope}: movement below 6px stays a click; exactly 6px starts a horizontal drag`, () => {
    const { track, element, writes, event, click } = pointerFixture(scope);
    track.onPointerDown(event(100)); const small = event(94.01);
    track.onPointerMove(small); track.onPointerUp(small);
    assert.equal(small.prevented, false); assert.equal(element.captured, false); assert.equal(writes.length, 0);
    assert.equal(click().prevented, false);
    track.onPointerDown(event(100)); const threshold = event(94);
    track.onPointerMove(threshold); assert.equal(threshold.prevented, true); assert.equal(element.captured, true);
    assert.equal(element.scrollLeft, 6); track.onPointerUp(threshold); assert.equal(writes.at(-1), 0);
  });

  test(`${scope}: vertical and equal-axis diagonal starts cancel permanently; horizontal diagonals work`, () => {
    const { track, element, writes, event, click } = pointerFixture(scope);
    for (const [dx, dy] of [[2, 40], [6, 6], [-8, -10], [-10, 10]]) {
      track.onPointerDown(event(100)); const move = event(100 + dx, 50 + dy);
      track.onPointerMove(move); track.onPointerMove(event(0)); track.onPointerUp(event(0));
      assert.equal(move.prevented, false); assert.equal(element.captured, false); assert.equal(writes.length, 0);
      assert.equal(click().prevented, false);
    }
    track.onPointerDown(event(280)); track.onPointerMove(event(60, 80)); track.onPointerUp(event(60, 80));
    assert.equal(writes.at(-1), 327);
  });

  test(`${scope}: touch, pen, non-left buttons and button/link descendants never start dragging`, () => {
    for (const options of [{ pointerType: 'touch' }, { pointerType: 'pen' }, { button: 1, buttons: 4 }, { button: 2, buttons: 2 },
      ...['button', 'a'].map(tag => ({ target: { closest(selector) { assert.equal(selector, 'button, a'); return { tagName: tag }; } } }))]) {
      const { track, element, writes, event, click } = pointerFixture(scope);
      track.onPointerDown(event(280, 50, options)); const move = event(60, 50, options);
      track.onPointerMove(move); track.onPointerUp(move);
      assert.equal(move.prevented, false); assert.equal(element.scrollLeft, 0); assert.equal(element.captured, false);
      assert.equal(writes.length, 0); assert.equal(click().prevented, false);
    }
  });

  test(`${scope}: cancellation, lost capture and lost buttons clean up once and allow the next input`, () => {
    for (const finish of ['onPointerCancel', 'onLostPointerCapture', 'onPointerMove']) {
      const { track, element, writes, event, click } = pointerFixture(scope);
      track.onPointerDown(event(280)); track.onPointerMove(event(60));
      if (finish === 'onLostPointerCapture') element.captured = false;
      track[finish](event(60, 50, { buttons: 0 }));
      assert.equal(element.dataset.dragging, undefined); assert.equal(element.captured, false); assert.equal(writes.at(-1), 327);
      track.onLostPointerCapture(event(60)); track.onPointerUp(event(60)); assert.equal(writes.length, 1);
      track.onPointerMove(event(0)); assert.equal(element.scrollLeft, 327);
      track.onPointerDown(event(100)); track.onPointerUp(event(100)); assert.equal(click().prevented, false);
    }
  });

  test(`${scope}: unrelated pointers and leaving before capture cannot create a stale drag`, () => {
    const { track, element, writes, event } = pointerFixture(scope);
    track.onPointerDown(event(280)); track.onPointerMove(event(60, 50, { pointerId: 2 }));
    track.onPointerCancel(event(60, 50, { pointerId: 2 })); assert.equal(element.captured, false);
    track.onPointerMove(event(60)); assert.equal(element.captured, true);
    track.onPointerLeave(); track.onPointerUp(event(60)); assert.equal(writes.at(-1), 327);
    track.onPointerDown(event(280)); track.onPointerLeave(); track.onPointerMove(event(60));
    assert.equal(element.captured, false); assert.equal(writes.length, 1);
  });

  test(`${scope}: first, last and single-slide boundaries remain aligned`, () => {
    for (const count of [1, 3]) {
      const { track, element, writes, event } = pointerFixture(scope, count);
      track.onPointerDown(event(60)); track.onPointerMove(event(280)); track.onPointerUp(event(280));
      assert.equal(writes.at(-1), 0);
      element.scrollLeft = (count - 1) * element.clientWidth;
      track.onPointerDown(event(280)); track.onPointerMove(event(60)); track.onPointerUp(event(60));
      assert.equal(writes.at(-1), (count - 1) * element.clientWidth);
    }
  });
}

test('Home carousel instances keep independent positions and click suppression', () => {
  const first = pointerFixture('home'), second = pointerFixture('home');
  first.track.onPointerDown(first.event(280)); first.track.onPointerMove(first.event(60)); first.track.onPointerUp(first.event(60));
  assert.equal(first.element.scrollLeft, 327); assert.equal(second.element.scrollLeft, 0);
  assert.equal(second.click().prevented, false); assert.equal(first.click().prevented, true);
  for (const pointerType of ['touch', 'mouse']) {
    first.track.onPointerDown(first.event(280)); first.track.onPointerMove(first.event(60)); first.track.onPointerCancel(first.event(60));
    first.track.onPointerDown(first.event(100, 50, { pointerType }));
    assert.equal(first.click().prevented, false, 'new input resets any unconsumed cancelled-gesture click');
  }
});
