const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');

// Home feed kept across a TALK trip (2026-11-25): the feed and the place you were come back without reloading.
const root = path.join(__dirname, '..');
function load(file) {
  const mod = { exports: {} };
  const output = ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  vm.runInNewContext(output, { module: mod, exports: mod.exports, AbortController, setTimeout, clearTimeout, require() { return {}; } });
  return mod.exports;
}
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');
// Values made inside the module sandbox have other prototypes, so compare them as plain JSON.
const plain = (value) => JSON.parse(JSON.stringify(value));

const snapshot = { path: '/pivot/home', kind: 'service', scrollY: 1840, query: '', focusContentId: '', focusNavigation: 'talk', tracks: [{ postId: '7', index: 2 }] };
const card = (id) => ({ id: String(id), title: `카드 ${id}`, detailContentId: String(id), slides: [] });

test('The kept feed comes back only for the same login within an hour, once', () => {
  const memory = load('lib/pivot/home-feed-memory.ts');
  const feed = { home: { origin: 'service', cards: [card(1)] } };
  const start = 1_000_000;
  memory.keepHomeFeed('7:csrf-a', { feed, snapshot }, start);
  // Peeking does not remove it (the development screen draws twice), forgetting does.
  assert.equal(memory.peekHomeFeed('7:csrf-a', start + 1000).feed, feed);
  assert.equal(memory.peekHomeFeed('7:csrf-a', start + 2000).snapshot.scrollY, 1840);
  memory.forgetHomeFeed();
  assert.equal(memory.peekHomeFeed('7:csrf-a', start + 3000), null);
  // Another account or session never sees it, and it is dropped.
  memory.keepHomeFeed('7:csrf-a', { feed, snapshot }, start);
  assert.equal(memory.peekHomeFeed('8:csrf-b', start + 1000), null);
  assert.equal(memory.peekHomeFeed('7:csrf-a', start + 1000), null);
  // One hour is the limit (user decision); after that a new feed loads.
  memory.keepHomeFeed('7:csrf-a', { feed, snapshot }, start);
  assert.ok(memory.peekHomeFeed('7:csrf-a', start + memory.HOME_FEED_KEEP_MS));
  assert.equal(memory.peekHomeFeed('7:csrf-a', start + memory.HOME_FEED_KEEP_MS + 1), null);
});

test('Kept cards carry the saved · liked · preference shown now, and nothing is kept while a request is unfinished', () => {
  const memory = load('lib/pivot/home-feed-memory.ts');
  const cards = [
    { ...card(1), topicId: 7, saved: false, liked: false, preference: null },
    { ...card(2), topicId: 7, saved: true, liked: true, preference: 'positive' },
    card(3), // 관심 분야가 없는 카드는 그대로 둔다
  ];
  const feed = { home: { origin: 'service', cards }, nextPage: async () => null };
  // 피드를 받은 뒤 누른 것: 1번은 저장 · 좋아요, 2번은 저장 취소 · 싫어요
  const savedNow = new Map([['7:1', true], ['7:2', false]]);
  const reactionsNow = new Map([['7:1', { liked: true }], ['7:2', { preference: 'negative' }]]);
  const pending = new Set();
  const bookmarks = {
    has(target, server = false) { return savedNow.get(`${target.topicId}:${target.contentId}`) ?? server; },
    isPending(target) { return pending.has(`save:${target.contentId}`); },
  };
  const reactions = {
    value(target, initial = {}) { return { ...initial, ...reactionsNow.get(`${target.topicId}:${target.contentId}`) }; },
    isPending(target, kind) { return pending.has(`${kind}:${target.contentId}`); },
  };
  const shown = memory.withShownReactions(feed, { bookmarks, reactions });
  assert.equal(shown.nextPage, feed.nextPage, 'the next page still loads after the kept cards');
  assert.deepEqual(plain(shown.home.cards.map(({ id, saved, liked, preference }) => ({ id, saved, liked, preference }))), [
    { id: '1', saved: true, liked: true, preference: null },
    { id: '2', saved: false, liked: true, preference: 'negative' },
    { id: '3' },
  ]);
  assert.equal(cards[0].saved, false, 'the loaded cards themselves are not changed');
  // 저장 · 좋아요 · 선호 요청이 하나라도 끝나지 않았으면 기억하지 않는다 (돌아오면 새로 받는다)
  for (const lock of ['save:2', 'like:1', 'preference:2']) {
    pending.add(lock);
    assert.equal(memory.withShownReactions(feed, { bookmarks, reactions }), null, lock);
    pending.delete(lock);
  }
  // 글 번호를 알 수 없는 카드가 있으면 (저장 기억이 거절) 기억하지 않는다
  assert.equal(memory.withShownReactions(feed, { bookmarks: { has() { throw new Error('x'); }, isPending() { return false; } }, reactions }), null);
});

test('A resumed feed shows the kept cards without asking the server again, and the next page still loads', async () => {
  const { createHomeRequestController } = load('lib/pivot/home.ts');
  let loads = 0, pages = 0;
  const source = {
    kind: 'service',
    async load() {
      loads++;
      return { status: 'ready', home: { origin: 'service', cards: [card(1), card(2)] },
        nextPage: async () => { pages++; return { status: 'ready', home: { origin: 'service', cards: [card(3)] } }; } };
    },
  };
  const before = createHomeRequestController(source, () => {});
  assert.equal(before.resumable(), null, 'nothing to keep before the feed is ready');
  await before.load();
  const kept = before.resumable();
  assert.deepEqual(plain(kept.home.cards).map((item) => item.id), ['1', '2']);
  assert.equal(typeof kept.nextPage, 'function');
  before.cancel();

  // Coming back from TALK: a new controller (the home screen was rebuilt) takes the kept feed.
  const states = [];
  const after = createHomeRequestController(source, (state) => states.push(state));
  after.resume(kept);
  assert.equal(loads, 1, 'page 1 is not loaded again');
  assert.deepEqual(plain(states.at(-1)), plain({ status: 'ready', home: kept.home, pagination: { status: 'idle' } }));
  await after.loadMore();
  assert.equal(pages, 1);
  assert.deepEqual(plain(states.at(-1).home.cards).map((item) => item.id), ['1', '2', '3']);
});

test('TALK tells the home to keep its feed before moving, and the home uses it only for the same login', () => {
  const screen = read('components/pivot/home-screen.tsx');
  const talk = screen.slice(screen.indexOf('function openTalk()'), screen.indexOf('return <>', screen.indexOf('function openTalk()')));
  assert.ok(talk.indexOf('window.dispatchEvent(new Event(LEAVE_HOME_FOR_TALK))') < talk.indexOf('navigate("/pivot/debate")'), talk);
  const route = read('components/pivot/home-route.tsx');
  assert.match(route, /const feedOwner = userId === undefined \? undefined : `\$\{userId\}:\$\{state\.session\?\.csrfToken \?\? ""\}`;/);
  assert.match(route, /<HomeDetailExperience key=\{state\.session\?\.csrfToken\} feedOwner=\{feedOwner\}/);
  const experience = read('components/pivot/home-detail-experience.tsx');
  assert.match(experience, /useState\(\(\) => feedOwner \? peekHomeFeed\(feedOwner\) : null\)/);
  assert.match(experience, /skipEntryLoading=\{skipEntryLoading \|\| !!talkReturn\} resume=\{talkReturn\?\.feed\} keeper=\{feedKeeper\}/);
  assert.match(experience, /window\.addEventListener\(LEAVE_HOME_FOR_TALK, leave\)/);
  assert.match(experience, /withShownReactions\(kept, \{ bookmarks: serviceBookmarks, reactions: interactions\?\.reactions \}\)/);
  // 다른 화면에서 TALK 으로 가면 아직 되돌리지 않은 TALK 자리부터 쓰고, 검색 결과를 보던 중이면 피드는 맨 위부터
  assert.match(experience, /const place = away \? talkSnapshot\.current \?\? readHomeReturn\(/);
  assert.match(experience, /const searched = !!home\.current\?\.querySelector\("#home-search-results"\);/);
  assert.match(experience, /\.\.\.\(searched \? \{ scrollY: 0, tracks: \[\] \} : \{\}\)/);
  // 상세 · MY 가 열린 채로 돌아왔고 그 화면을 열 때의 피드 자리가 있으면 그 자리(그때 누른 카드 포커스)를 쓴다
  assert.match(experience, /if \(awayAtStart\.current && readHomeReturn\([^)]*\)\) talkSnapshot\.current = null;/);
  const controller = read('components/pivot/home-controller.tsx');
  assert.match(controller, /if \(attempt === 0 && resumeFirst\.current\) controller\.resume\(resumeFirst\.current\);\s*else void controller\.load\(\);/);
});
