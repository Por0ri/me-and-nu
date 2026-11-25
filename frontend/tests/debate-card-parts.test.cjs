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

test('Card descriptions show in full with their line breaks, with no 더보기 or 접기', () => {
  const { TopicHead } = tsxLoader(stubs)('components/debate/debate-cards.tsx');
  const description = '두 작품의 주인공인 에렌과 렐르슈를 전략가로서 비교해요.\n누가 더 뛰어난 전략가일까요?';
  const html = renderToStaticMarkup(React.createElement(TopicHead, { debate: {
    works: [{ entityId: 1, name: '진격의 거인' }], title: '에렌 vs 렐르슈', description,
    sideA: { label: '에렌' }, sideB: { label: '렐르슈' },
  } }));
  assert.ok(html.includes(`<p class="topicDescription">${description}</p>`));
  assert.doesNotMatch(html, /더보기|접기/);
});

test('Topic form limits follow the 2026-11-25 design: 2 works, a 100-letter description and 8-letter choices', () => {
  const { DEBATE_RULES } = tsxLoader(stubs)('lib/debate/rules.ts');
  assert.deepEqual([DEBATE_RULES.worksMax, DEBATE_RULES.descriptionMax, DEBATE_RULES.sideLabelMax], [2, 100, 8]);
});
