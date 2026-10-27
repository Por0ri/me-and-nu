const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const root = path.join(__dirname, '..');
const modules = new Map();
class ApiError extends Error { constructor(message, status, code) { super(message); this.status = status; this.code = code; } }
function load(file) {
  file = file.replaceAll('\\', '/');
  if (modules.has(file)) return modules.get(file).exports;
  const mod = { exports: {} }; modules.set(file, mod);
  const output = ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  vm.runInNewContext(output, { module: mod, exports: mod.exports, URL, URLSearchParams, AbortController,
    require(name) {
      if (name === '@/lib/api') return { ApiError };
      if (name === './services') return { requirePivotApi() { throw new Error('Origin blocked'); } };
      return load(path.join(path.dirname(file), name) + '.ts');
    },
  });
  return mod.exports;
}
const { createServiceHomeSource, mapFeedCard } = load('lib/pivot/home-service.ts');
const { createServiceDetailSource, mapContentDetail } = load('lib/pivot/detail-service.ts');
const { createServiceSavedSource } = load('lib/pivot/saved-service.ts');
const { detailHref } = load('lib/pivot/detail-navigation.ts');
const { serviceContentId } = load('lib/pivot/service-data.ts');
const { createDetailRequestController } = load('lib/pivot/detail.ts');
const allow = () => {};
const options = () => ({ signal: new AbortController().signal });
const plain = value => JSON.parse(JSON.stringify(value));
const topics = { topics: [
  { id: 6, code: 'movie', status: 'active', deletedAt: null },
  { id: 42, code: 'anime', status: 'active', deletedAt: null },
  { id: 99, code: 'anime', status: 'deleted', deletedAt: '2026-01-01' },
] };
const feedCard = (id = 17) => ({ kind: 'content', id, topicId: 42, title: '테스트 기사', summary: '실제 요약', imageUrl: null, saved: false });
const target = { postId: '17', contentId: '17', topicId: 42 };
const content = overrides => ({ contentId: 17, topicId: 42, title: '테스트 기사', imageUrl: null,
  displayMode: 'full_body', body: '첫 문단\n\n다음 문단', excerpt: '발췌문',
  sourceUrl: 'https://example.com/article', myReaction: { bookmarked: true }, ...overrides });

test('Home derives an active Topic and maps numeric IDs, saved state and one real slide', async () => {
  const init = options(); const calls = [];
  const source = createServiceHomeSource({ async getMyTopics(_, request) { assert.equal(request.signal, init.signal); return topics; },
    async getFeed(id, query, request) { calls.push([id, query]); assert.equal(request.signal, init.signal);
      return { selectedTopicId: id, sections: [{ contents: [feedCard()] }], nextCursor: null }; } }, allow);
  const result = await source.load(init);
  assert.equal(calls[0][0], 42); assert.equal(result.home.origin, 'service');
  assert.deepEqual(plain(result.home.cards[0]), { id: '17', detailContentId: '17', topicId: 42, title: '테스트 기사', saved: false,
    slides: [{ id: '17', order: 0, description: '실제 요약' }] });
});

test('Home follows server cursors without losing records or hardcoding a first page', async () => {
  const queries = [];
  const source = createServiceHomeSource({ async getMyTopics() { return topics; }, async getFeed(id, query) {
    queries.push(query.cursor); return { selectedTopicId: id, sections: [{ contents: [feedCard(query.cursor ? 18 : 17)] }], nextCursor: query.cursor ? null : 'page-2' };
  } }, allow);
  assert.deepEqual(plain((await source.load(options())).home.cards.map(card => card.id)), ['17', '18']);
  assert.deepEqual(queries, [undefined, 'page-2']);
});

test('Absent active Topics yields empty content without guessing an ID or making feed calls', async () => {
  let feedCalls = 0;
  const result = await createServiceHomeSource({ async getMyTopics() { return { topics: [] }; }, async getFeed() { feedCalls++; } }, allow).load(options());
  assert.equal(result.home.cards.length, 0); assert.equal(feedCalls, 0);
});

test('Mismatched feed Topics and malformed IDs are rejected', async () => {
  for (const card of [{ ...feedCard(), topicId: 8 }, feedCard(0), feedCard(1.5), feedCard(Number.MAX_SAFE_INTEGER + 1), { ...feedCard(), title: '' }]) {
    assert.throws(() => mapFeedCard(card, 42));
  }
  const source = createServiceHomeSource({ async getMyTopics() { return topics; }, async getFeed() { return { selectedTopicId: 8, sections: [], nextCursor: null }; } }, allow);
  assert.equal((await source.load(options())).reason, 'invalid-response');
});

test('Detail preserves Topic and respects excerpt-only responses without exposing body', () => {
  const article = mapContentDetail(content({ displayMode: 'link_excerpt', body: 'DO NOT SHOW' }), target);
  assert.deepEqual(plain(article.sections), [{ paragraphs: ['발췌문'] }]);
  assert.equal(article.bookmarked, true); assert.equal(article.topicId, 42);
  assert.equal(article.work, undefined); assert.equal(article.image, undefined);
  assert.doesNotMatch(JSON.stringify(article), /DO NOT SHOW/);
  assert.deepEqual(plain(mapContentDetail(content(), target).sections), [{ paragraphs: ['첫 문단', '다음 문단'] }]);
});

test('Excerpt-only content with no text keeps its real source link and bookmark actions available', async () => {
  const states = [];
  const source = createServiceDetailSource({ async getContent() {
    return content({ displayMode: 'link_excerpt', body: null, excerpt: null });
  } }, allow);
  await createDetailRequestController(source, state => states.push(state)).load(target);
  assert.equal(states.at(-1).status, 'ready');
  assert.equal(states.at(-1).article.sourceUrl, 'https://example.com/article');
  assert.equal(states.at(-1).article.bookmarked, true);
  assert.equal(states.at(-1).article.sections.length, 0);
  const invalidStates = [];
  const invalidSource = createServiceDetailSource({ async getContent() {
    return content({ displayMode: 'link_excerpt', body: null, excerpt: null, sourceUrl: 'javascript:bad' });
  } }, allow);
  await createDetailRequestController(invalidSource, state => invalidStates.push(state)).load(target);
  assert.equal(invalidStates.at(-1).status, 'empty');
});

test('Service detail validates canonical numeric IDs and Topic before the API call', async () => {
  let calls = 0;
  const source = createServiceDetailSource({ async getContent() { calls++; return content(); } }, allow);
  for (const invalid of [{ ...target, topicId: undefined }, { ...target, topicId: 0 }, { ...target, contentId: 'demo-id' },
    { ...target, postId: '18' }, { ...target, contentId: '01', postId: '01' }, { ...target, contentId: '9007199254740992', postId: '9007199254740992' }]) {
    assert.equal(serviceContentId(invalid), null);
    assert.equal((await source.load(invalid, options())).status, 'not-found');
  }
  assert.equal(calls, 0);
  const init = options();
  const valid = createServiceDetailSource({ async getContent(id, topicId, request) {
    assert.equal(id, 17); assert.equal(topicId, 42); assert.equal(request.signal, init.signal); return content(); } }, allow);
  assert.equal((await valid.load(target, init)).status, 'ready');
});

test('Detail distinguishes missing content from authentication and transport errors', async () => {
  for (const code of ['CONTENT_NOT_FOUND', 'TOPIC_NOT_FOUND']) {
    const source = createServiceDetailSource({ async getContent() { throw new ApiError('missing', 404, code); } }, allow);
    assert.equal((await source.load(target, options())).status, 'not-found');
  }
  const auth = new ApiError('expired', 401, 'AUTH_REQUIRED');
  await assert.rejects(createServiceDetailSource({ async getContent() { throw auth; } }, allow).load(target, options()), error => error === auth);
  assert.throws(() => mapContentDetail(content({ topicId: 8 }), target), /Invalid detail identity/);
});

test('Image and source URLs allow only HTTP(S) without embedded credentials', () => {
  for (const url of ['javascript:alert(1)', 'data:image/svg+xml,bad', 'https://user:secret@example.com/image', 'broken']) {
    assert.equal(mapFeedCard({ ...feedCard(), imageUrl: url }, 42).slides[0].image, undefined);
    assert.equal(mapContentDetail(content({ sourceUrl: url }), target).sourceUrl, undefined);
  }
  assert.equal(mapFeedCard({ ...feedCard(), imageUrl: 'https://example.com/image.png' }, 42).slides[0].image.src, 'https://example.com/image.png');
});

test('Saved loads scoped server pages and preserves each detail target', async () => {
  const queries = []; const init = options();
  const source = createServiceSavedSource({ async getMyTopics() { return topics; }, async getBookmarks(query, request) {
    queries.push(query); assert.equal(request.signal, init.signal);
    return { items: [{ contentId: query.cursor ? 18 : 17, topicId: 42, title: '저장한 글', imageUrl: null }], nextCursor: query.cursor ? null : 'next' };
  } }, allow);
  const result = await source.load(init);
  assert.deepEqual(queries.map(query => query.topicId), [42, 42]);
  assert.deepEqual(plain(result.saved.cards.map(({ postId, contentId, topicId }) => ({ postId, contentId, topicId }))),
    [{ postId: '17', contentId: '17', topicId: 42 }, { postId: '18', contentId: '18', topicId: 42 }]);
});

test('Repeated cursors fail instead of looping or presenting a silently partial Saved list', async () => {
  let calls = 0;
  const source = createServiceSavedSource({ async getMyTopics() { return topics; }, async getBookmarks() { calls++; return { items: [], nextCursor: 'same' }; } }, allow);
  await assert.rejects(source.load(options()), /pagination/); assert.equal(calls, 2);
});

test('Cancellation stops between Topic lookup and content request even when a transport ignores abort', async () => {
  const controller = new AbortController(); let calls = 0;
  const source = createServiceHomeSource({ async getMyTopics() { controller.abort(); return topics; }, async getFeed() { calls++; } }, allow);
  await assert.rejects(source.load({ signal: controller.signal }), error => error.name === 'AbortError');
  assert.equal(calls, 0);
});

test('Origin checks run before requests and service URLs retain validated Topic IDs', async () => {
  let calls = 0;
  const source = createServiceHomeSource({ async getMyTopics() { calls++; return topics; }, async getFeed() {} });
  await assert.rejects(source.load(options()), /Origin blocked/); assert.equal(calls, 0);
  const url = new URL(detailHref('/pivot/home', target), 'https://test.me-nu.kr');
  assert.equal(url.searchParams.get('topicId'), '42'); assert.equal(url.searchParams.get('contentId'), '17');
  assert.throws(() => detailHref('/pivot/home', { ...target, topicId: -1 }), /Invalid Topic/);
});
