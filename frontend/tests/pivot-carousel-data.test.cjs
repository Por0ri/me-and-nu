const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const root = path.join(__dirname, '..');
const plain = value => JSON.parse(JSON.stringify(value));
class ApiError extends Error { constructor(message, status, code) { super(message); this.status = status; this.code = code; } }
function modules({ realApi = false, extras = {} } = {}) {
  const cache = new Map();
  function load(file) {
    file = file.replaceAll('\\', '/');
    if (cache.has(file)) return cache.get(file).exports;
    const mod = { exports: {} }; cache.set(file, mod);
    const code = ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), { compilerOptions: {
      module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
    } }).outputText;
    vm.runInNewContext(code, { module: mod, exports: mod.exports, URL, URLSearchParams, Headers, Response, Event,
      AbortController, DOMException, process: { env: { NEXT_PUBLIC_API_BASE_URL: 'http://localhost:8180' } }, ...extras,
      require(name) {
        if (name === '@/lib/api' && !realApi) return { ApiError, apiBaseUrl: 'http://localhost:8180' };
        if (name === './services') return { requirePivotApi() { throw new Error('Origin blocked'); } };
        return load((name.startsWith('@/') ? name.slice(2) : path.join(path.dirname(file), name)) + '.ts');
      },
    }, { filename: file });
    return mod.exports;
  }
  return load;
}
const load = modules();
const { mapFeedCard, createServiceHomeSource } = load('lib/pivot/home-service.ts');
const { mapContentDetail } = load('lib/pivot/detail-service.ts');
const { protectedSlideImage } = load('lib/pivot/carousel-data.ts');
const { mapSavedCard } = load('lib/pivot/saved-service.ts');
const box = { x: 0.06, y: 0.67, width: 0.88, height: 0.29 };
const layout = { template: 'split', fitMode: 'contain_blur', backgroundStyle: 'blur', imageBox: box, copyBox: box,
  textOverlayBox: box, imageScale: 1, imageOffsetY: 0, contrastMode: 'scrim_required', blurBackground: false };
const slides = () => [3, 1, 2].map(page => ({ page, role: page === 1 ? 'cover' : 'body', eyebrow: '소식',
  titleLines: [`카드 ${page}`], bodySentences: [`카드 본문 ${page}`], layout: structuredClone(layout), width: 1308, height: 1632,
  imageUrl: `/api/v1/contents/17/slides/${page}/image?topicId=42` }));
const feed = () => ({ kind: 'content', id: 17, topicId: 42, title: '카드 글', saved: true, summary: '요약', imageUrl: null,
  slides: slides(), productionType: 'ai', publishedAt: '2026-10-28T01:00:00Z', sourceName: '공식 출처',
  sourceUrl: 'https://example.com/article', notices: [{ code: 'AI_GENERATED', message: 'AI가 생성한 콘텐츠입니다.' }] });
const target = { postId: '17', contentId: '17', topicId: 42 };
const detail = () => ({ ...feed(), contentId: 17, displayMode: 'full_body', body: '독립 본문 첫 단락\n\n둘째 단락', excerpt: '발췌',
  myReaction: { bookmarked: true }, publisher: '공식 출처', sources: [
    { url: 'https://example.com/second', title: '둘째', publisher: null, role: 'citation', citationOrder: 2 },
    { url: 'https://example.com/article', title: '원문', publisher: '공식 출처', role: 'primary', citationOrder: 1 },
  ] });

test('Feed and detail map every service slide in page order and retain identity, bookmark and independent body', () => {
  const card = mapFeedCard(feed(), 42), article = mapContentDetail(detail(), target);
  assert.deepEqual(plain(card.slides.map(item => item.page)), [1, 2, 3]);
  assert.deepEqual(plain(card.slides), plain(article.slides));
  assert.deepEqual(plain(card.slides[0].image), { src: 'http://localhost:8180/api/v1/contents/17/slides/1/image?topicId=42', alt: '카드 1', protected: true });
  assert.deepEqual(plain(card.slides[0].layout.textOverlayBox), box);
  assert.equal(card.saved, true); assert.equal(card.topicId, 42); assert.equal(article.bookmarked, true);
  assert.equal(card.publishedAt, '2026-10-28T01:00:00Z'); assert.equal(article.productionType, 'ai');
  assert.deepEqual(plain(article.sections), [{ paragraphs: ['독립 본문 첫 단락', '둘째 단락'] }]);
  assert.equal(article.displayMode, 'full_body'); assert.equal(article.publisher, '공식 출처');
  assert.deepEqual(plain(article.sources.map(item => item.title)), ['원문', '둘째']);
  assert.equal(article.notices[0].code, 'AI_GENERATED');
});

test('Legacy no-slide content keeps its existing single-cover feed and excerpt-only detail', () => {
  const legacy = { ...feed(), slides: [], imageUrl: 'https://example.com/image.png' };
  assert.equal(mapFeedCard(legacy, 42).slides.length, 1);
  assert.equal(mapFeedCard(legacy, 42).slides[0].image.protected, undefined);
  const excerpt = mapContentDetail({ ...detail(), slides: undefined, sources: undefined,
    displayMode: 'link_excerpt', body: 'PRIVATE', excerpt: '공개 발췌' }, target);
  assert.deepEqual(plain(excerpt.slides), []); assert.deepEqual(plain(excerpt.sources), []);
  assert.doesNotMatch(JSON.stringify(excerpt), /PRIVATE/);
});

test('Saved thumbnail accepts only its own content and Topic first-page protected URL', () => {
  const saved = { contentId: 17, topicId: 42, title: '저장 카드', imageUrl: '/api/v1/contents/17/slides/1/image?topicId=42' };
  const card = mapSavedCard(saved, 42);
  assert.equal(card.image.protected, true);
  assert.equal(card.image.src, 'http://localhost:8180/api/v1/contents/17/slides/1/image?topicId=42');
  for (const imageUrl of ['/api/v1/contents/18/slides/1/image?topicId=42', '/api/v1/contents/17/slides/2/image?topicId=42',
    '/api/v1/contents/17/slides/1/image?topicId=43']) assert.throws(() => mapSavedCard({ ...saved, imageUrl }, 42));
  assert.equal(mapSavedCard({ ...saved, imageUrl: 'https://example.com/image.png' }, 42).image.protected, undefined);
  assert.equal(mapSavedCard({ ...saved, imageUrl: 'http://localhost:8180/api/v1/contents/18/slides/1/image?topicId=42' }, 42).image, undefined);
});

for (const [name, value] of Object.entries({
  absolute: 'http://localhost:8180/api/v1/contents/17/slides/1/image?topicId=42',
  external: '//evil.invalid/api/v1/contents/17/slides/1/image?topicId=42',
  content: '/api/v1/contents/18/slides/1/image?topicId=42',
  topic: '/api/v1/contents/17/slides/1/image?topicId=43',
  page: '/api/v1/contents/17/slides/2/image?topicId=42',
  query: '/api/v1/contents/17/slides/1/image?topicId=42&redirect=evil',
  fragment: '/api/v1/contents/17/slides/1/image?topicId=42#x',
  encoded: '/api/v1/contents/%31%37/slides/1/image?topicId=42',
  script: 'javascript:alert(1)', data: 'data:image/png;base64,xxx',
})) test(`Protected image rejects ${name} instead of sending credentials`, () => {
  assert.throws(() => protectedSlideImage(value, 17, 42, 1, '카드'), error => error.code === 'INVALID_CONTENT_RESPONSE');
});

for (const name of ['two', 'eleven', 'gap', 'duplicate', 'wrongDimensions', 'badBox', 'badText', 'null']) test(`Invalid ${name} carousel fails closed`, () => {
  const data = feed();
  if (name === 'two') data.slides.pop();
  if (name === 'eleven') data.slides = Array(11).fill(data.slides[0]);
  if (name === 'gap') data.slides[0].page = 4;
  if (name === 'duplicate') data.slides[0].page = 1;
  if (name === 'wrongDimensions') data.slides[0].width = 1;
  if (name === 'badBox') data.slides[0].layout.copyBox.x = 2;
  if (name === 'badText') data.slides[0].titleLines = [''];
  if (name === 'null') data.slides = null;
  assert.throws(() => mapFeedCard(data, 42));
});

test('Response projection drops private keys and rejects unsafe source links', () => {
  const data = detail(); data.slides[0].layout.internalReport = 'secret'; data.slides[0].layout.imageBox.path = 'secret';
  data.slides[0].imageSha256 = 'secret'; data.sources.push({ url: 'javascript:evil', role: 'citation', title: 'unsafe' });
  const article = mapContentDetail(data, target);
  assert.doesNotMatch(JSON.stringify(article), /secret|javascript:|unsafe/);
});

const active = id => ({ authenticated: true, sessionState: 'active', onboardingCompleted: true, user: { id, accountType: 'user' }, csrfToken: 'token' });
const topics = { topics: [{ id: 42, code: 'anime', status: 'active', deletedAt: null }] };
test('Session changes after Topic or feed await never publish the old member data', async () => {
  for (const stage of ['topics', 'feed']) {
    let session = active(1), calls = 0;
    const source = createServiceHomeSource({ async getMyTopics() { if (stage === 'topics') session = active(2); return topics; },
      async getFeed() { calls++; session = null; return { selectedTopicId: 42, sections: [{ contents: [feed()] }], nextCursor: null }; } }, () => {}, () => session);
    const result = await source.load({ signal: new AbortController().signal });
    assert.equal(result.reason, 'unauthorized'); assert.equal(result.home, undefined);
    assert.equal(calls, stage === 'topics' ? 0 : 1);
  }
});

test('Home preserves typed HTTP and transport error reasons', async () => {
  for (const [error, reason] of [[new ApiError('private', 401, 'AUTH_REQUIRED'), 'unauthorized'],
    [new ApiError('private', 403, 'FORBIDDEN'), 'forbidden'], [new ApiError('private', 503, 'SERVER'), 'server'], [new Error('private'), 'network']]) {
    const source = createServiceHomeSource({ async getMyTopics() { throw error; }, async getFeed() {} }, () => {});
    const result = await source.load({ signal: new AbortController().signal });
    assert.equal(result.reason, reason); assert.doesNotMatch(result.message, /private/);
  }
});

test('Aborted API reads never dispatch a stale unauthorized event before or after JSON parsing', async () => {
  for (const phase of ['already', 'headers', 'json']) {
    const controller = new AbortController(), browser = new EventTarget(); let events = 0, calls = 0;
    browser.addEventListener('api:unauthorized', () => events++);
    const api = modules({ realApi: true, extras: { window: browser, fetch: async () => {
      calls++; if (phase === 'headers') controller.abort();
      return { status: 401, ok: false, async json() { controller.abort(); return { code: 'AUTH_REQUIRED' }; } };
    } } })('lib/api.ts');
    if (phase === 'already') controller.abort();
    await assert.rejects(api.getFeed(42, {}, { signal: controller.signal }), error => error.name === 'AbortError');
    assert.equal(events, 0); assert.equal(calls, phase === 'already' ? 0 : 1);
  }
});
