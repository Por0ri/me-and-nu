const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const root = path.join(__dirname, '..');
const modules = new Map();
function load(file) {
  if (modules.has(file)) return modules.get(file).exports;
  const mod = { exports: {} }; modules.set(file, mod);
  const output = ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
  } }).outputText;
  vm.runInNewContext(output, { module: mod, exports: mod.exports, URL, process: { env: {} },
    require(name) {
      if (name === 'react' || name === 'react/jsx-runtime') return require(name);
      if (name.endsWith('.module.css')) return { default: new Proxy({}, { get: (_, key) => String(key) }), __esModule: true };
      const relative = name.startsWith('@/') ? name.slice(2) : path.join(path.dirname(file), name);
      return load(relative + (fs.existsSync(path.join(root, relative + '.tsx')) ? '.tsx' : '.ts'));
    },
  });
  return mod.exports;
}
const h = React.createElement;
const html = element => renderToStaticMarkup(element);
const { policyTexts } = load('lib/pivot/policy-texts.ts');
const { PolicyTextModal, PolicyMarkdown, policyTextModalKind } = load('components/pivot/policy-text-modal.tsx');
const { ConsentPolicyDialog } = load('components/pivot/consent-policy-dialog.tsx');
const { MyPagePolicyDialog } = load('components/pivot/my-page-policy-dialog.tsx');
const count = (text, pattern) => (text.match(pattern) || []).length;
const attr = (markup, name) => [...markup.matchAll(new RegExp(`${name}="([^"]+)"`, 'g'))].map(match => match[1]);
const decode = value => value.replace(/&quot;/g, '"').replace(/&#x27;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
const visibleText = markup => decode(markup.replace(/<[^>]+>/g, '\n')).split('\n').map(line => line.trim()).filter(Boolean);

test('generated texts keep the three bundled documents and only replace the privacy contact', () => {
  const expected = { terms: ['이용약관', 'v1.2', 31, '이 약관은'], operations: ['운영정책', 'v0.6', 15, '이 운영정책은'], privacy: ['개인정보처리방침', 'v0.3', 17, '이 방침은'] };
  for (const [id, [title, version, articles, closing]] of Object.entries(expected)) {
    const text = policyTexts[id];
    assert.equal(text.title, title); assert.equal(text.version, version);
    assert.ok(text.markdown.startsWith(`버전 ${version} · 시행일 2026년 11월 2일\n\n`), id);
    assert.ok(text.markdown.endsWith(`### 부칙\n\n${closing} 2026년 11월 2일부터 시행한다.`), id);
    assert.equal(count(text.markdown, /^#{3,4} 제\d+조 \(/gm), articles, id);
    assert.doesNotMatch(text.markdown, /^---$|^## |건강하게만 자라다오 · 2026-10-23|가상 문서|통합본/m);
  }
  assert.equal(count(policyTexts.terms.markdown, /^### 제\d장 /gm), 8);
  assert.equal(count(policyTexts.privacy.markdown, /meandnu2026@kakao\.com/g), 1);
  assert.match(policyTexts.privacy.markdown, /^\| 연락처 \| meandnu2026@kakao\.com \|$/m);
  for (const text of Object.values(policyTexts)) assert.doesNotMatch(text.markdown, /연락용 이메일 주소|\[[^\]]*주소\]/);
});

test('markdown subset renders headings, hanging lists, nested bullets and tables as plain elements', () => {
  const markup = html(h(PolicyMarkdown, { title: '문서', markdown: [
    '버전 v9 · 시행일 2026년 11월 2일', '', '### 제1장 총칙', '', '#### 제1조 (목적)', '', '첫 문단이다.', '',
    '1. 첫째 항', '   - 묶음 하나', '   - 묶음 둘', '2. 둘째 항', '', '| 단계 | 조치 |', '|---|---|', '| 1단계 | 경고 |', '',
    '3. 표 뒤 셋째 항', '', '- 맨 위 묶음',
  ].join('\n') }));
  assert.match(markup, /^<h3[^>]*>문서<\/h3><p[^>]*>버전 v9 · 시행일 2026년 11월 2일<\/p><h4[^>]*>제1장 총칙<\/h4><h5[^>]*>제1조 \(목적\)<\/h5><p[^>]*>첫 문단이다.<\/p>/);
  assert.match(markup, /<ol role="list" class="list" start="1"><li class="item"><span class="marker">1\.<\/span><div>첫째 항<ul role="list" class="nestedList"><li class="item"><span class="marker" aria-hidden="true">-<\/span><div>묶음 하나<\/div><\/li>/);
  assert.match(markup, /<ol role="list" class="list" start="3"><li class="item"><span class="marker">3\.<\/span><div>표 뒤 셋째 항<\/div>/);
  assert.match(markup, /<table class="table"><caption class="srOnly">제1조 \(목적\)<\/caption><thead><tr><th scope="col">단계<\/th><th scope="col">조치<\/th><\/tr><\/thead><tbody><tr><td>1단계<\/td><td>경고<\/td><\/tr><\/tbody><\/table>/);
  assert.match(markup, /<ul role="list" class="list"><li class="item"><span class="marker" aria-hidden="true">-<\/span><div>맨 위 묶음<\/div>/);
});

test('policy markdown never becomes live markup or links', () => {
  const markup = html(h(PolicyMarkdown, { title: '<b>제목</b>', markdown: [
    '<img src=x onerror="alert(1)">', '', '1. <script>alert(1)</script>', '', '| <a href="javascript:alert(1)">셀</a> | [링크](javascript:alert(1)) |', '|---|---|', '| x | y |',
  ].join('\n') }));
  assert.doesNotMatch(markup, /<(?:img|script|a|b)[\s>]|\shref="|\sonerror="/);
  assert.match(markup, /&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;/);
  assert.match(markup, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.match(markup, /\[링크\]\(javascript:alert\(1\)\)/);
  for (const file of ['components/pivot/policy-text-modal.tsx', 'lib/pivot/policy-texts.ts']) assert.doesNotMatch(fs.readFileSync(path.join(root, file), 'utf8'), /dangerouslySetInnerHTML|innerHTML/);
});

test('terms modal shows both full texts in labelled scroll boxes with the mock copy', () => {
  const markup = html(h(PolicyTextModal, { kind: 'terms', onClose() {} }));
  const text = visibleText(markup);
  assert.match(markup, /^<dialog class="dialog" aria-modal="true" aria-labelledby="([^"]+)" data-policy-modal="terms">/);
  const [titleId] = attr(markup, 'aria-labelledby');
  assert.match(markup, new RegExp(`<h2 id="${titleId}" class="title">이용약관 및 운영정책</h2>`));
  assert.match(markup, /<button type="button" class="close" aria-label="닫기">/);
  assert.deepEqual(text.slice(0, 7), ['이용약관 및 운영정책', '시행일: 2026. 11. 02.', '본 약관은 2026년 11월 2일부터 시행되며, 서비스 이용에 적용됩니다.',
    '이용약관 전문', '이용약관', '버전 v1.2 · 시행일 2026년 11월 2일', '제1장 총칙']);
  assert.ok(text.includes('운영정책 전문'));
  assert.ok(text.indexOf('운영정책') > text.indexOf('운영정책 전문'));
  assert.ok(text.includes('버전 v0.6 · 시행일 2026년 11월 2일'));
  const sections = [...markup.matchAll(/<section class="document" tabindex="0" aria-labelledby="([^"]+)">/g)].map(match => match[1]);
  assert.equal(sections.length, 2);
  assert.deepEqual(sections.map(id => new RegExp(`<p id="${id}" class="label">([^<]+)</p>`).exec(markup)?.[1]), ['이용약관 전문', '운영정책 전문']);
  assert.doesNotMatch(markup, /개인정보 처리방침 전문|meandnu2026/);
  assert.equal(count(markup, /<table /g), 3);
});

test('privacy modal shows one labelled full text with the contact email in place of the placeholder', () => {
  const markup = html(h(PolicyTextModal, { kind: 'privacy', onClose() {} }));
  const text = visibleText(markup);
  assert.match(markup, /^<dialog class="dialog" aria-modal="true" aria-labelledby="[^"]+" data-policy-modal="privacy">/);
  assert.deepEqual(text.slice(0, 7), ['개인정보 처리방침', '시행일: 2026. 11. 02.', '본 방침은 2026년 11월 2일부터 시행되며, 서비스 이용에 적용됩니다.',
    '개인정보 처리방침 전문', '개인정보처리방침', '버전 v0.3 · 시행일 2026년 11월 2일', '제1조 (목적)']);
  const sections = [...markup.matchAll(/<section class="document" tabindex="0" aria-labelledby="([^"]+)">/g)].map(match => match[1]);
  assert.equal(sections.length, 1);
  assert.match(markup, new RegExp(`<p id="${sections[0]}" class="label">개인정보 처리방침 전문</p>`));
  assert.match(markup, /<td>연락처<\/td><td>meandnu2026@<wbr\/>kakao\.com<\/td>/);
  assert.doesNotMatch(markup, /연락용 이메일 주소|이용약관 전문|운영정책 전문/);
  assert.equal(count(markup, /<table /g), 6);
});

test('consent terms and privacy rows open the new modals; other items keep the server text dialog', () => {
  assert.equal(policyTextModalKind('terms'), 'terms'); assert.equal(policyTextModalKind('privacy'), 'privacy');
  for (const type of ['marketing', 'Terms', 'privacy ', '']) assert.equal(policyTextModalKind(type), null);
  const policy = (type, required = true) => ({ type, policyVersion: 'release-7', required, text: `서버 ${type} 원문` });
  const terms = html(h(ConsentPolicyDialog, { policy: policy('terms'), onClose() {} }));
  assert.match(terms, /data-policy-modal="terms"/); assert.match(terms, /이용약관 및 운영정책/); assert.doesNotMatch(terms, /서버 terms 원문|release-7/);
  const privacy = html(h(ConsentPolicyDialog, { policy: policy('privacy'), onClose() {} }));
  assert.match(privacy, /data-policy-modal="privacy"/); assert.match(privacy, /개인정보 처리방침 전문/); assert.doesNotMatch(privacy, /서버 privacy 원문/);
  const marketing = html(h(ConsentPolicyDialog, { policy: policy('marketing', false), onClose() {} }));
  assert.doesNotMatch(marketing, /data-policy-modal/);
  assert.match(marketing, /<h2 id="[^"]+">마케팅 수신 동의<\/h2>/); assert.match(marketing, /버전 release-7 · 선택/); assert.match(marketing, /서버 marketing 원문/);
  const consent = fs.readFileSync(path.join(root, 'components/pivot/consent-screen.tsx'), 'utf8');
  assert.match(consent, /<ConsentPolicyDialog policy=\{opened\} onClose=\{\(\) => setOpened\(null\)\} \/>/);
  assert.doesNotMatch(consent, /<PolicyDialog/);
});

test('My Page opens the same modals in Service and Demo without loading server policy text', () => {
  for (const demo of [false, true]) {
    const terms = html(h(MyPagePolicyDialog, { type: 'terms', demo, onClose() {} }));
    assert.match(terms, /data-policy-modal="terms"/); assert.match(terms, /운영정책 전문/);
    const privacy = html(h(MyPagePolicyDialog, { type: 'privacy', demo, onClose() {} }));
    assert.match(privacy, /data-policy-modal="privacy"/); assert.match(privacy, /meandnu2026@<wbr\/>kakao\.com/);
    for (const markup of [terms, privacy]) assert.doesNotMatch(markup, /Demo에서는 정책 원문|정책 문서를 불러오고|공개가 확인된/);
  }
  const source = fs.readFileSync(path.join(root, 'components/pivot/my-page-policy-dialog.tsx'), 'utf8');
  assert.doesNotMatch(source, /loadPivotPolicies|fetch\(/);
  assert.match(source, /isLocalPolicyReviewUrl\(window\.location\.href\)\) \{\n\s+return <LocalPolicyReview/);
});
