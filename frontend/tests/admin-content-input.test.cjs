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
function load(file) {
  const source = ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
  } }).outputText;
  const mod = { exports: {} };
  vm.runInNewContext(`(function(module,exports,require){${source}\n})`, { URL })(mod, mod.exports, name => {
    if (name === 'react' || name === 'react/jsx-runtime') return require(name);
    if (name.endsWith('.module.css')) return { __esModule: true, default: new Proxy({}, { get: (_, key) => key }) };
    const relative = name.startsWith('@/') ? name.slice(2) : path.join(path.dirname(file), name);
    return load(relative + (fs.existsSync(path.join(root, relative + '.tsx')) ? '.tsx' : '.ts'));
  });
  return mod.exports;
}
const input = load('lib/admin-content-input.ts');
const blank = () => input.contentFormValue();
const valid = () => ({ ...blank(), title: '제목' });

test('create input requires title; optional empty values normalize without storage defaults', () => {
  assert.equal(input.validateContentInput(blank()).errors.title, '필수 입력값입니다.');
  assert.deepEqual(plain(input.validateContentInput({ ...valid(), title: ' 제목 ', body: ' \n ' }).input),
    { title: '제목', summary: null, body: null, sources: [] });
});

test('stored values load exactly and only author fields enter the request', () => {
  const detail = { title: '기존 제목', summary: null, body: '<script>plain</script>\n본문', status: 'active', judgmentStatus: 'confirmed',
    sources: [{ url: 'https://example.org', title: '원문', role: 'primary', citationOrder: 7, publisher: '발행처' }] };
  const form = input.contentFormValue(detail);
  assert.deepEqual(plain(form), { title: detail.title, summary: '', body: detail.body, sources: [{ url: 'https://example.org', title: '원문' }] });
  const request = input.validateContentInput(form).input;
  assert.deepEqual(Object.keys(request), ['title', 'summary', 'body', 'sources']);
  assert.deepEqual(Object.keys(request.sources[0]), ['url', 'title']);
  assert.equal(detail.sources[0].role, 'primary');
});

test('Unicode length agrees with Pydantic; invalid controls and oversized text are rejected', () => {
  assert.ok(input.validateContentInput({ ...valid(), title: '😀'.repeat(300) }).input);
  for (const [field, limit] of [['title', 300], ['summary', 10000], ['body', 100000]]) {
    assert.ok(input.validateContentInput({ ...valid(), [field]: 'a'.repeat(limit + 1) }).errors[field]);
    assert.ok(input.validateContentInput({ ...valid(), [field]: 'x\x00y' }).errors[field]);
  }
  assert.ok(input.validateContentInput({ ...valid(), title: '\ud800' }).errors.title);
});

test('sources reject unsafe URLs, credentials, duplicates, blank rows and oversized lists', () => {
  for (const url of ['', '/relative', 'https:example.org', 'javascript:alert(1)', 'data:text/html,x', 'ftp://example.org',
    'https://user:pass@example.org', 'https://@example.org', 'https://example.org/a b', 'https://example.org/\npath',
    'https://example.org\\path', 'https://example.org:99999', 'https://example.org:0', 'https://[invalid', 'https:///missing',
    'https://example.org/' + 'a'.repeat(1000), 'https://<bad>.org', 'https://exa%mple.org', 'https://[::gg]']) {
    assert.ok(input.validateContentInput({ ...valid(), sources: [{ url, title: '' }] }).errors['sources.0.url'], url);
  }
  const sources = [{ url: 'https://example.org', title: '' }, { url: ' https://example.org ', title: '' }];
  assert.ok(input.validateContentInput({ ...valid(), sources }).errors['sources.1.url']);
  assert.ok(input.validateContentInput({ ...valid(), sources: Array.from({ length: 51 }, (_, n) => ({ url: `https://example.org/${n}`, title: '' })) }).errors.sources);
});

test('source URL query and fragment stay literal; title is optional and bounded', () => {
  const source = { url: ' https://example.org/a?utm_source=x#quote ', title: ' ' };
  assert.deepEqual(plain(input.validateContentInput({ ...valid(), sources: [source] }).input.sources),
    [{ url: source.url.trim(), title: null }]);
  assert.ok(input.validateContentInput({ ...valid(), sources: [{ ...source, title: 'a'.repeat(301) }] }).errors['sources.0.title']);
});

test('dirty detection includes source changes and clears on exact restoration', () => {
  const initial = valid();
  assert.equal(input.hasContentInputChanges(initial, valid()), false);
  for (const field of ['title', 'summary', 'body']) assert.equal(input.hasContentInputChanges(initial, { ...initial, [field]: '변경' }), true);
  assert.equal(input.hasContentInputChanges(initial, { ...initial, sources: [{ url: '', title: '' }] }), true);
  assert.equal(input.hasContentInputChanges(initial, { ...initial, title: initial.title }), false);
});

test('shared form renders existing plain text safely and never offers enabled save', () => {
  const { ContentForm } = load('components/admin/content-form.tsx');
  const detail = { title: '<script>x</script>', summary: '요약', body: '본문', sources: [{ url: 'https://example.org', title: '원문' }] };
  const html = renderToStaticMarkup(React.createElement(ContentForm, { detail, active: true }));
  assert.match(html, /data-admin-form="edit"/);
  assert.match(html, /&lt;script&gt;x&lt;\/script&gt;/);
  assert.match(html, /서버에 저장되지 않습니다/);
  assert.match(html, /disabled=""[^>]*>저장 준비 중/);
  assert.doesNotMatch(html, /<script|승인|반려/);
  const waiting = renderToStaticMarkup(React.createElement(ContentForm, { active: false }));
  assert.match(waiting, /fieldset disabled=""/);
});
