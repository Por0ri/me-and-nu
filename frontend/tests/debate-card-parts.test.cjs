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

test('Chat faces show the first letter of the nickname until profile photos exist', () => {
  const { nicknameInitial } = tsxLoader(stubs)('components/debate/debate-room.tsx');
  assert.deepEqual(['넨연구가', '  Zed', '😀웃음', '알 수 없음', ''].map(nicknameInitial), ['넨', 'Z', '😀', '알', '']);
});

test('Card and room descriptions keep their line breaks but show at most 3 lines, then … (2026-11-25)', () => {
  const css = fs.readFileSync(path.join(__dirname, '..', 'components/debate/debate.module.css'), 'utf8');
  for (const name of ['topicDescription', 'roomDescription']) {
    const rule = css.match(new RegExp(`\\.${name} \\{([^}]*)\\}`))[1];
    for (const part of [/display: -webkit-box/, /-webkit-line-clamp: 3/, /overflow: hidden/, /white-space: pre-line/]) assert.match(rule, part, name);
  }
});

test('Topic form limits follow the 2026-11-25 design: 2 works, a 100-letter description and 8-letter choices', () => {
  const { DEBATE_RULES } = tsxLoader(stubs)('lib/debate/rules.ts');
  assert.deepEqual([DEBATE_RULES.worksMax, DEBATE_RULES.descriptionMax, DEBATE_RULES.sideLabelMax], [2, 100, 8]);
});
