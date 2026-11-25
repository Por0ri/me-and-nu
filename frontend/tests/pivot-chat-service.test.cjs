const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

const root = path.join(__dirname, '..');
class ApiError extends Error {
  constructor(message, status, code) { super(message); this.status = status; this.code = code; }
}
const modules = new Map();
function load(relative) {
  if (modules.has(relative)) return modules.get(relative).exports;
  const mod = { exports: {} };
  modules.set(relative, mod);
  const output = ts.transpileModule(fs.readFileSync(path.join(root, relative), 'utf8'), { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
  } }).outputText;
  vm.runInNewContext(output, { module: mod, exports: mod.exports, AbortController, URL, setTimeout, clearTimeout, crypto: globalThis.crypto,
    require(name) {
      if (name === '@/lib/api') return { ApiError, apiBaseUrl: 'http://localhost:8100' };
      if (name === './services') return { requirePivotApi() {} };
      if (name === 'react' || name === 'react/jsx-runtime') return require(name);
      if (name.endsWith('.module.css')) return { default: new Proxy({}, { get: (_, key) => String(key) }), __esModule: true };
      const file = name.startsWith('@/') ? name.slice(2) : path.join(path.dirname(relative), name);
      return load(file + (fs.existsSync(path.join(root, file + '.tsx')) ? '.tsx' : '.ts'));
    },
  });
  return mod.exports;
}
const chat = load('lib/pivot/chat-service.ts');
const { ChatSheet, HOME_CHAT_COPY, DETAIL_CHAT_COPY } = load('components/pivot/chat-sheet.tsx');
const { HomeAIChat } = load('components/pivot/home-ai-chat.tsx');

// The modules run in a separate VM realm; compare their values as plain JSON.
const plain = value => JSON.parse(JSON.stringify(value));
const feedCard = (id, extra = {}) => ({
  kind: 'content', id, topicId: 2, recommendationId: null, title: `글 ${id}`, productionType: 'ai', summary: `요약 ${id}`,
  imageUrl: null, sourceName: '에이전트', sourceUrl: 'https://example.com/' + id, publishedAt: '2026-11-13T00:00:00Z',
  saved: false, savedItemId: null, recommendationReason: null, seen: false, isPromotional: false, notices: [],
  myReaction: { liked: false, preference: null }, ...extra,
});
const result = (extra = {}) => ({ type: 'answer', messageId: 9, content: '판타지도 결이 좀 달라.', sources: [], factSources: [],
  contents: [], notices: [], contextCoverage: 'full', targetTopicId: null, ...extra });

test('answer keeps picked cards in order and drops only the broken ones', () => {
  const answer = chat.mapChatAnswer(result({ contents: [
    feedCard(7, { imageUrl: '/api/v1/contents/7/slides/1/image?topicId=2' }), feedCard(8, { topicId: 3 }), feedCard(5, { title: ' ' }), feedCard(4),
  ] }), 2);
  assert.deepEqual(plain(answer.cards.map(card => card.contentId)), ['7', '4']);
  assert.deepEqual(plain(answer.cards[0].image), { src: 'http://localhost:8100/api/v1/contents/7/slides/1/image?topicId=2', alt: '글 7', protected: true });
  assert.equal(answer.cards[1].summary, '요약 4');
  assert.equal(answer.text, '판타지도 결이 좀 달라.');
});

test('references skip the open post and posts already shown as cards; unsafe fact links are dropped', () => {
  const answer = chat.mapChatAnswer(result({
    contents: [feedCard(7)],
    sources: [{ contentId: 3, title: '보고 있는 글', url: '' }, { contentId: 7, title: '카드 글', url: '' }, { contentId: 11, title: '참고 글', url: '' }],
    factSources: [{ name: 'AniList', url: 'https://anilist.co/anime/1' }, { name: '같은 주소', url: 'https://anilist.co/anime/1' },
      { name: '스크립트', url: 'javascript:alert(1)' }, { name: '위키데이터', url: 'https://www.wikidata.org/wiki/Q1' }],
    notices: [{ code: 'EVIDENCE_INSUFFICIENT', message: '확인된 자료가 부족해서 단정하진 않았어.' }],
  }), 2, 3);
  assert.deepEqual(plain(answer.references.map(card => [card.contentId, card.title])), [['11', '참고 글']]);
  assert.deepEqual(plain(answer.facts.map(fact => fact.name)), ['AniList', '위키데이터']);
  assert.deepEqual(plain(answer.notes), ['확인된 자료가 부족해서 단정하진 않았어.']);
});

function fakeApi(jobs, { topics = [{ id: 2, code: 'anime', status: 'active', deletedAt: null }], sendErrors = [] } = {}) {
  const calls = [];
  return { calls, api: {
    async getMyTopics() { calls.push(['topics']); return { topics }; },
    async createChatSession(topicId, anchor) { calls.push(['session', topicId, anchor]); return { sessionId: 40 + calls.length, topicId, anchorContentId: anchor ?? null, aiNotice: 'AI 안내' }; },
    async sendChatMessage(sessionId, content) {
      calls.push(['send', sessionId, content]);
      const error = sendErrors.shift(); if (error) throw error;
      return { userMessageId: 1, jobId: 77, status: 'processing' };
    },
    async getChatJob(jobId) { calls.push(['job', jobId]); return jobs.shift(); },
  } };
}
const deps = (api, clock = { now: 0 }) => ({ api, guard() {}, sleep: async ms => { clock.now += ms; }, now: () => clock.now, newId: () => 'id' });

test('home question finds the active Topic, waits for the job and keeps the conversation', async () => {
  const { api, calls } = fakeApi([{ status: 'processing' }, { status: 'completed', result: result({ contents: [feedCard(7)] }) }]);
  const conversation = chat.createChatConversation({ kind: 'home' }, deps(api));
  const seen = [];
  conversation.subscribe(() => seen.push(conversation.snapshot().busy));
  await conversation.ask('  판타지 애니 찾아줘 ');
  const { turns, busy, aiNotice } = conversation.snapshot();
  assert.deepEqual(calls.map(call => call[0]), ['topics', 'session', 'send', 'job', 'job']);
  assert.deepEqual(calls[1], ['session', 2, undefined]);
  assert.equal(calls[2][2], '판타지 애니 찾아줘');
  assert.equal(busy, false); assert.equal(aiNotice, 'AI 안내'); assert.ok(seen.includes(true));
  assert.deepEqual(plain(turns.map(turn => turn.role === 'user' ? turn.text : turn.status)), ['판타지 애니 찾아줘', 'answered']);
  assert.deepEqual(plain(turns[1].answer.cards.map(card => card.contentId)), ['7']);
  await conversation.ask('더 보여줘');
  assert.equal(calls.filter(call => call[0] === 'session').length, 1, 'the same session continues');
});

test('detail question uses the open post as the anchor and never asks for Topics', async () => {
  const { api, calls } = fakeApi([{ status: 'completed', result: result() }]);
  await chat.createChatConversation({ kind: 'detail', topicId: 2, contentId: 3 }, deps(api)).ask('이 글 핵심만 알려줘');
  assert.deepEqual(calls.slice(0, 2), [['session', 2, 3], ['send', 41, '이 글 핵심만 알려줘']]);
});

test('an expired session is replaced once', async () => {
  const { api, calls } = fakeApi([{ status: 'completed', result: result() }], { sendErrors: [new ApiError('gone', 404, 'CHAT_SESSION_NOT_FOUND')] });
  const conversation = chat.createChatConversation({ kind: 'detail', topicId: 2, contentId: 3 }, deps(api));
  await conversation.ask('질문');
  assert.deepEqual(calls.map(call => call[0]), ['session', 'send', 'session', 'send', 'job']);
  assert.equal(conversation.snapshot().turns[1].status, 'answered');
});

test('limits, failures and slow answers become retryable messages', async () => {
  const cases = [
    [fakeApi([], { sendErrors: [new ApiError('limit', 429, 'CHAT_DAILY_LIMIT')] }).api, '오늘 물어볼 수 있는 질문을 다 썼어. 내일 다시 물어봐.'],
    [fakeApi([], { sendErrors: [new ApiError('limit', 429, 'CHAT_SERVICE_DAILY_LIMIT')] }).api, '오늘은 챗봇이 쉬는 중이야. 내일 다시 물어봐 줘.'],
    [fakeApi([{ status: 'failed', error: { code: 'GENERATION_FAILED', message: '실패' } }]).api, '답을 만들지 못했어. 다시 물어봐 줘.'],
    [fakeApi(Array.from({ length: 60 }, () => ({ status: 'processing' }))).api, '답이 너무 늦어. 다시 물어봐 줘.'],
    [fakeApi([], { sendErrors: [new TypeError('Failed to fetch')] }).api, '인터넷 연결을 확인하고 다시 물어봐 줘.'],
  ];
  for (const [api, message] of cases) {
    const conversation = chat.createChatConversation({ kind: 'detail', topicId: 2, contentId: 3 }, deps(api));
    await conversation.ask('질문');
    const turn = conversation.snapshot().turns[1];
    assert.equal(turn.status, 'error'); assert.equal(turn.message, message); assert.equal(turn.question, '질문');
    assert.equal(conversation.snapshot().busy, false);
  }
});

test('blank, too long and overlapping questions are not sent', async () => {
  let release;
  const { api, calls } = fakeApi([]);
  api.getChatJob = () => new Promise(resolve => { release = resolve; });
  const conversation = chat.createChatConversation({ kind: 'detail', topicId: 2, contentId: 3 }, deps(api));
  await conversation.ask('   ');
  await conversation.ask('가'.repeat(501));
  const first = conversation.ask('첫 질문');
  await new Promise(resolve => setTimeout(resolve, 0));
  await conversation.ask('겹친 질문');
  release({ status: 'completed', result: result() });
  await first;
  assert.deepEqual(calls.filter(call => call[0] === 'send').map(call => call[2]), ['첫 질문']);
});

test('the sheet opens like the design: handle, centred title, examples and input, no notice before a question', () => {
  const home = renderToStaticMarkup(React.createElement(ChatSheet, { place: { kind: 'detail', topicId: 2, contentId: 99 }, copy: HOME_CHAT_COPY, onClose() {} }));
  assert.match(home, /<dialog/);
  assert.match(home, /aria-label="AI 대화 닫기"/);
  assert.ok(home.includes('어떤 콘텐츠를 찾고 계시나요?'));
  assert.ok(home.includes('placeholder="보고싶은 콘텐츠가 무엇인가요?"'));
  for (const example of HOME_CHAT_COPY.examples) assert.ok(home.includes(example), example);
  assert.ok(HOME_CHAT_COPY.examples.includes('‘나루토’ 관련 카드뉴스 보여줘'));
  assert.ok(!home.includes('AI가 생성한 답변'), 'the AI notice appears only after the first question');
  assert.equal(DETAIL_CHAT_COPY.placeholder, '콘텐츠 내용 중 궁금한 점이 있나요?');
  // 보내기 비행기는 토론방 채팅창의 보내기 아이콘과 같은 선 그림, 같은 크기 (2026-11-25 사용자 요청)
  const plane = 'M20.5 3.5 10 14M20.5 3.5 14 20.5l-4-6.5-6.5-4z';
  assert.match(home, new RegExp(`aria-label="보내기"[^>]*><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"[^>]*><path d="${plane}"></path></svg></button>`));
  assert.ok(fs.readFileSync(path.join(root, 'components/debate/debate-icons.tsx'), 'utf8').includes(`SendIcon = (props: IconProps) => <Icon {...props}><path d="${plane}" /></Icon>`));
  assert.match(fs.readFileSync(path.join(root, 'components/pivot/chat.module.css'), 'utf8'), /\.send:disabled \{ color: #a1a1aa; \}/);
});

test('the home dock keeps its AI label; the face and the bar share one 48px circle and appear only when expanded', () => {
  const open = renderToStaticMarkup(React.createElement(HomeAIChat, { expanded: true, onExpandedChange() {} }));
  assert.match(open, /aria-label="AI 콘텐츠 탐색" aria-expanded="true"/);
  assert.ok(open.includes('/pivot/detail/ai-expanded.svg'));
  assert.ok(open.includes('어떤 콘텐츠를 찾고 계시나요? 무엇이든 물어보세요!'));
  assert.doesNotMatch(open, /<dialog|inert=""/);
  const closed = renderToStaticMarkup(React.createElement(HomeAIChat, { expanded: false, onExpandedChange() {} }));
  assert.match(closed, /aria-expanded="false"/);
  assert.ok(closed.includes('/pivot/home/chrome/ai.svg'));
  assert.match(closed, /inert=""/, 'the folded bar cannot be focused or clicked');
  const css = fs.readFileSync(path.join(root, 'components/pivot/chat.module.css'), 'utf8');
  assert.match(css, /\.dockFace \{[^}]*width: 48px; height: 48px;/);
  assert.match(css, /\.dockLayer \{[^}]*height: 48px;/);
});
