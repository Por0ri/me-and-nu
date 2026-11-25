const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');

// Loads a component file the way the other screen tests do: TSX compiled to CommonJS, CSS module classes kept as their names.
function tsxLoader(overrides = {}) {
  const root = path.join(__dirname, '..');
  const modules = new Map();
  function loadTsx(file) {
    if (modules.has(file)) return modules.get(file).exports;
    const mod = { exports: {} }; modules.set(file, mod);
    const output = ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
    vm.runInNewContext(output, { module: mod, exports: mod.exports, URL, process: { env: {} }, require(name) {
      if (name in overrides) return overrides[name];
      if (name === 'react' || name === 'react/jsx-runtime') return require(name);
      if (name.endsWith('.module.css')) return { default: new Proxy({}, { get: (_, key) => String(key) }), __esModule: true };
      const relative = name.startsWith('@/') ? name.slice(2) : path.join(path.dirname(file), name);
      return loadTsx(relative + (fs.existsSync(path.join(root, relative + '.tsx')) ? '.tsx' : '.ts'));
    } });
    return mod.exports;
  }
  return loadTsx;
}

const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const stubs = {
  'next/link': { __esModule: true, default: ({ children }) => children },
  'next/navigation': { useRouter: () => ({ push() {}, replace() {} }) },
  '@/lib/debate/client': {},
  './debate-chrome': { errorText: () => '' },
};

test('Cards show a nickname over 10 letters as its first 10 and …, and read the whole name aloud', () => {
  const { AuthorLine } = tsxLoader(stubs)('components/debate/debate-parts.tsx');
  const view = (authorNickname) => renderToStaticMarkup(React.createElement(AuthorLine, { debate: { authorNickname } }));
  const long = view('킹갓레전드스피노사우르스');
  // 얼굴은 프로필 사진 대신 닉네임 첫 글자 (2026-11-25, 채팅과 같은 규칙)
  assert.match(long, /<span class="authorFace" aria-hidden="true">킹<\/span>/);
  assert.doesNotMatch(long, /<img/);
  assert.match(long, /<span aria-hidden="true">킹갓레전드스피노사우…<\/span>/);
  assert.match(long, /<span class="srOnly">올린 사람 킹갓레전드스피노사우르스<\/span>/);
  assert.match(long, /title="킹갓레전드스피노사우르스"/);
  const ten = view('열글자닉네임입니다요');
  assert.match(ten, /<span aria-hidden="true">열글자닉네임입니다요<\/span>/);
  assert.doesNotMatch(ten, /…|title=/);
});

test('Card descriptions keep their line breaks, with no 더보기 or 접기', () => {
  const { TopicHead } = tsxLoader(stubs)('components/debate/debate-cards.tsx');
  const description = '두 작품의 주인공인 에렌과 렐르슈를 전략가로서 비교해요.\n누가 더 뛰어난 전략가일까요?';
  const html = renderToStaticMarkup(React.createElement(TopicHead, { debate: {
    works: [{ entityId: 1, name: '진격의 거인' }], title: '에렌 vs 렐르슈', description,
    sideA: { label: '에렌' }, sideB: { label: '렐르슈' },
  } }));
  assert.ok(html.includes(`<p class="topicDescription">${description}</p>`));
  assert.doesNotMatch(html, /더보기|접기/);
  // 설명 아래 'A vs B' 줄은 없다 (2026-11-25 사용자 결정): 카드는 설명에서 끝난다
  assert.ok(html.endsWith(`<p class="topicDescription">${description}</p>`), html);
});

test('Folded vote panel keeps only the title and the members · time line, with 투표창 펼치기 at the end of that line (2026-11-25 mock)', () => {
  const { RoomPanel } = tsxLoader(stubs)('components/debate/debate-room.tsx');
  const now = Date.parse('2026-11-25T11:00:00Z');
  const debate = {
    works: [{ entityId: 1, name: '헌터×헌터' }], title: '히소카와 크로로가 다시 싸우면 누가 이길까?', description: '천공투기장 기준',
    authorNickname: '넨연구가', memberCount: 128, closesAt: new Date(now + 31 * 3600_000).toISOString(),
  };
  const view = (collapsed) => renderToStaticMarkup(React.createElement(RoomPanel, { debate, now, collapsed, onToggle() {} },
    React.createElement('p', null, '진영 투표')));
  const folded = view(true);
  assert.match(folded, /<section [^>]*data-collapsed="true"/);
  assert.match(folded, /히소카와 크로로가 다시 싸우면 누가 이길까\?/);
  assert.match(folded, /<div class="topicMeta">.*128명 참여 중.*남은 시간 [^<]*<\/span><button [^>]*aria-expanded="false"[^>]*>투표창 펼치기<\/button><\/div>/s);
  assert.doesNotMatch(folded, /헌터×헌터|천공투기장|넨연구가|진영 투표|roomTop/);
  const open = view(false);
  assert.doesNotMatch(open, /data-collapsed/);
  assert.match(open, /<div class="roomTop">.*헌터×헌터.*투표창 접기<\/button><\/div>/s);
  for (const shown of ['천공투기장 기준', '넨연구가', '128명 참여 중', '진영 투표']) assert.ok(open.includes(shown), shown);
});

test('Work chips sit in one sideways-scrolling row even with long names, on cards and in the topic form (2026-11-25)', () => {
  const { WorkChips } = tsxLoader(stubs)('components/debate/debate-parts.tsx');
  const long = '진정한 동료가 아니라고 용사 파티에서 쫓겨났기 때문에, 변경에서 슬로우 라이프 하기로 했습니다';
  const html = renderToStaticMarkup(React.createElement(WorkChips, { works: [{ entityId: 1, name: long }, { entityId: 2, name: '그날 본 꽃의 이름을 우리는 아직 모른다' }] }));
  assert.ok(html.includes(`<ul class="chips swipe" aria-label="관련 작품"><li class="chip">${long}</li><li class="chip">`), html);
  const css = fs.readFileSync(path.join(__dirname, '..', 'components/debate/debate.module.css'), 'utf8');
  const rule = (selector) => css.match(new RegExp(`(?:^|\\n)${selector.replace(/[.]/g, '\\.')} \\{([^}]*)\\}`))[1];
  for (const part of [/overflow-x: auto/, /scrollbar-width: none/]) assert.match(rule('.swipe'), part);
  assert.doesNotMatch(rule('.swipe'), /display/, 'the row keeps its own flex display');
  for (const selector of ['.chips', '.pickedWorks']) assert.match(rule(selector), /flex-wrap: nowrap/, selector);
  for (const selector of ['.chip', '.pickedWorks li']) {
    assert.match(rule(selector), /flex: none/, selector);
    assert.match(rule(selector), /white-space: nowrap/, selector);
  }
  const form = fs.readFileSync(path.join(__dirname, '..', 'components/debate/debate-create.tsx'), 'utf8');
  assert.match(form, /<ul className=\{`\$\{styles\.pickedWorks\} \$\{styles\.swipe\}`\} aria-label="고른 작품" \{\.\.\.pickedSwipe\}>/);
  // 고른 작품 칩은 온보딩 작품 칩과 같은 모양 (2026-11-25 시안): 칩 · X 버튼 규칙이 글자 하나까지 같고, X 그림도 같다
  const onboarding = fs.readFileSync(path.join(__dirname, '..', 'components/pivot/onboarding.module.css'), 'utf8');
  const onboardingRule = (selector) => onboarding.match(new RegExp(`(?:^|\\n)${selector.replace(/[.]/g, '\\.')} \\{([^}]*)\\}`))[1];
  assert.equal(rule('.pickedWorks li'), onboardingRule('.selectedWorks li'));
  assert.equal(rule('.screen .pickedWorks button'), onboardingRule('.selectedWorks button'));
  const cross = '<svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true"><path d="m2.5 2.5 7 7m0-7-7 7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>';
  assert.ok(form.includes(cross), 'topic form X');
  assert.ok(fs.readFileSync(path.join(__dirname, '..', 'components/pivot/onboarding-catalog-screens.tsx'), 'utf8').includes(cross), 'onboarding X');
});

test('Chat faces show the first letter of the nickname until profile photos exist', () => {
  const { nicknameInitial } = tsxLoader(stubs)('components/debate/debate-parts.tsx');
  assert.deepEqual(['넨연구가', '  Zed', '😀웃음', '알 수 없음', ''].map(nicknameInitial), ['넨', 'Z', '😀', '알', '']);
});

test('Card and room descriptions keep their line breaks but show at most 3 lines, then … (2026-11-25)', () => {
  const css = fs.readFileSync(path.join(__dirname, '..', 'components/debate/debate.module.css'), 'utf8');
  for (const name of ['topicDescription', 'roomDescription']) {
    const rule = css.match(new RegExp(`\\.${name} \\{([^}]*)\\}`))[1];
    for (const part of [/display: -webkit-box/, /-webkit-line-clamp: 3/, /overflow: hidden/, /white-space: pre-line/]) assert.match(rule, part, name);
  }
});

test('Debate home keeps a floating 내 활동 button on every tab, and the ticket box no longer holds it (2026-11-25)', () => {
  const linkStub = { __esModule: true, default: ({ href, className, children }) => React.createElement('a', { href, className }, children) };
  const { ActivityFab, VoteTicketBox } = tsxLoader({ ...stubs, 'next/link': linkStub })('components/debate/debate-parts.tsx');
  assert.equal(renderToStaticMarkup(React.createElement(ActivityFab)),
    '<div class="fabLayer"><a href="/pivot/debate/activity" class="activityFab">내 활동</a></div>');
  const box = renderToStaticMarkup(React.createElement(VoteTicketBox, { tickets: { balance: 3, max: 3, nextRefillAt: null } }));
  assert.match(box, /내 투표권/);
  assert.doesNotMatch(box, /내 활동/);
  // 세 탭이 같은 화면(DebateHome) 안이라 탭과 상관없이 늘 그린다. 검색창을 열어 뒤가 흐려진 동안만 숨긴다.
  const home = fs.readFileSync(path.join(__dirname, '..', 'components/debate/debate-home.tsx'), 'utf8');
  assert.match(home, /\{search\.open \? null : <ActivityFab \/>\}/);
  const css = fs.readFileSync(path.join(__dirname, '..', 'components/debate/debate.module.css'), 'utf8');
  assert.match(css, /\.fabLayer \{ position: fixed;[^}]*bottom: calc\(58px \+ 16px \+ env\(safe-area-inset-bottom\)\)/);
  assert.match(css, /\.screen \.activityFab \{[^}]*width: 56px; height: 56px; border-radius: 50%;/);
});

test('My activity opens on 내가 올린 주제, which lists only my voting or open topics (2026-11-25)', () => {
  const activity = fs.readFileSync(path.join(__dirname, '..', 'components/debate/debate-activity.tsx'), 'utf8');
  const tabs = [...activity.match(/const TABS = \[([\s\S]*?)\] as const;/)[1].matchAll(/id: "(\w+)"/g)].map((found) => found[1]);
  assert.deepEqual(tabs, ['mine', 'voted', 'joined']);
  const page = fs.readFileSync(path.join(__dirname, '..', 'app/pivot/debate/activity/page.tsx'), 'utf8');
  assert.match(page, /tab === "voted" \? "voted" : tab === "joined" \? "joined" : "mine"/);
  // 끝난 토론방은 지난 토론방에만: 내가 올린 주제 탭은 투표 중 · 토론 중만 남기고 지난 방 카드를 그리지 않는다
  assert.match(activity, /state\.data\.filter\(\(debate\) => debate\.status === "voting" \|\| debate\.status === "open"\)/);
  assert.doesNotMatch(activity, /PastCard/);
});

test('Once my topic room has ended, 내가 올린 주제 shows the same empty screen as before posting, with no 새 주제 banner (2026-11-25)', () => {
  const linkStub = { __esModule: true, default: ({ href, className, children }) => React.createElement('a', { href, className }, children) };
  const ended = {
    debateId: 9, status: 'closed', title: '누가 더 예쁠까', description: '마키마와 마린땅 중 누가 더 예쁜가요?',
    works: [{ entityId: 1, name: '체인소 맨' }], sideA: { label: '마키마', count: 1, percent: 100 }, sideB: { label: '마린땅', count: 0, percent: 0 },
    authorNickname: '킹갓레전드스피노사우르스', participantCount: 1, memberCount: 0, result: 'a', isMine: true,
  };
  const chrome = {
    errorText: () => '',
    DebateScreen: ({ children }) => React.createElement('main', null, children),
    DebateHomeHeader: () => null,
    DebateTabs: () => null,
    DebateState: () => React.createElement('p', null, '불러오는 중'),
    // 서버를 배포하기 전처럼 끝난 방도 목록에 온 경우
    useLoad: () => [{ loading: false, error: null, data: [ended] }, () => {}, () => {}],
    useToast: () => [null, () => {}],
  };
  const { DebateActivity } = tsxLoader({ ...stubs, './debate-chrome': chrome, 'next/link': linkStub })('components/debate/debate-activity.tsx');
  const html = renderToStaticMarkup(React.createElement(DebateActivity, { initialTab: 'mine', created: false }));
  assert.match(html, /아직 올린 주제가 없어요!/);
  assert.match(html, /<a href="\/pivot\/debate\/new" class="bigButton">주제 올리기<\/a>/);
  assert.doesNotMatch(html, /새 주제를 올릴 수 있어요|누가 더 예쁠까|투표 결과 확인하기/);
});

test('Topic form limits follow the 2026-11-25 design: 2 works, a 100-letter description and 8-letter choices', () => {
  const { DEBATE_RULES } = tsxLoader(stubs)('lib/debate/rules.ts');
  assert.deepEqual([DEBATE_RULES.worksMax, DEBATE_RULES.descriptionMax, DEBATE_RULES.sideLabelMax], [2, 100, 8]);
});

test('The vote toast follows the 2026-11-25 design and floats above the 내 활동 button', () => {
  const css = fs.readFileSync(path.join(__dirname, '..', 'components/debate/debate.module.css'), 'utf8');
  const rule = (selector) => css.match(new RegExp(`(?:^|\\n)${selector.replace(/[.()]/g, '\\$&')} \\{([^}]*)\\}`))[1];
  // 카드와 같은 폭, 높이 38px, 연보라 바탕 · 보라 테두리 · 모서리 8px, 그림자 없음, 검정 12px 글씨, 체크와 글씨 사이 10px
  const toast = rule('.toast');
  for (const part of [/width: min\(calc\(100% - 48px\), 382px\)/, /min-height: 38px/, /border: 1px solid #9c79e6/, /border-radius: 8px/,
    /background: #efe7ff/, /color: #000;/, /font-size: 12px/, /font-weight: 500/, /gap: 10px/]) assert.match(toast, part);
  assert.doesNotMatch(toast, /box-shadow/);
  assert.match(rule('.toast svg'), /margin: -1px/, 'the 22px icon box keeps only its 20px circle in the layout');
  assert.match(rule('.screen:has(.fabLayer)'), /--toast-bottom: calc\(58px \+ 16px \+ 56px \+ 12px\)/);
  // 방 나가기만 회색 글씨 (2026-11-25 시안), 주제 투표하기 같은 다른 연보라 버튼은 보라 그대로
  assert.match(rule('.screen .cardButtons > .softFillButton'), /color: var\(--debate-muted\)/);
  assert.match(rule('.screen .softFillButton'), /color: var\(--debate-primary\)/);
  const cards = fs.readFileSync(path.join(__dirname, '..', 'components/debate/debate-cards.tsx'), 'utf8');
  assert.ok(cards.includes('"투표를 취소했어요.  투표권 1개가 반환되었어요."'), 'the refund sentence ends with a period, as in the design');
});
