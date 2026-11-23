const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const root = path.resolve(__dirname, '..'), modules = new Map();
function load(file) {
  file = file.replaceAll('\\', '/');
  if (modules.has(file)) return modules.get(file).exports;
  const mod = { exports: {} }; modules.set(file, mod);
  const code = ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  const localRequire = name => {
    if (name === './api' || name === '@/lib/api') return { apiRequest() { throw new Error('Unexpected transport in render test'); }, ApiError: class extends Error {} };
    if (['react', 'react/jsx-runtime'].includes(name)) return require(name);
    if (name === 'next/link') return { __esModule: true, default: props => React.createElement('a', props) };
    if (name.endsWith('.module.css')) return { __esModule: true, default: new Proxy({}, { get: (_, key) => String(key) }) };
    const next = name.startsWith('@/') ? name.slice(2) : path.join(path.dirname(file), name);
    return load(next + (fs.existsSync(path.join(root, next + '.tsx')) ? '.tsx' : '.ts'));
  };
  vm.runInNewContext(`(function(module,exports,require){${code}\n})`, { URL, URLSearchParams, Intl })(mod, mod.exports, localRequire);
  return mod.exports;
}
const model = load('lib/admin-reports.ts'), fixtures = load('tests/fixtures/admin-reports.ts');
const render = (file, name, props) => renderToStaticMarkup(React.createElement(load(file)[name], props));
const screen = props => render('components/admin/reports-screen.tsx', 'ReportsScreen', { query: model.initialReportQuery, navigate() {}, ...props });
const detail = report => render('components/admin/report-detail-panel.tsx', 'ReportDetailPanel', { report });

test('report dates use Korea time, unknown values stay unknown, source links reject active URLs', () => {
  assert.equal(model.reportDate('2026-11-04T16:42:00Z'), '2026.11.05');
  assert.equal(model.reportDate('2026-11-05T00:42:00Z', true), '2026.11.05 09:42');
  for (const invalid of [null, 'bad']) assert.equal(model.reportDate(invalid), '—');
  for (const invalid of ['javascript:alert(1)', 'data:text/html,x', '/relative']) assert.equal(model.reportSourceUrl(invalid), null);
  assert.equal(model.reportSourceUrl('https://example.test/a'), 'https://example.test/a');
});
test('isolated fixture composes literal search, status, periods and pagination without mutations', () => {
  const query = model.initialReportQuery, before = JSON.stringify(fixtures.reportFixtures);
  const first = fixtures.selectReportFixtures(query), second = fixtures.selectReportFixtures({ ...query, page: 2 });
  assert.equal(first.total, 145); assert.equal(first.items.length, 5); assert.equal(second.items[0].number, 'R-006');
  assert.equal(fixtures.selectReportFixtures({ ...query, q: 'R-004', status: 'completed' }).total, 1);
  assert.equal(fixtures.selectReportFixtures({ ...query, q: 'R-004', status: 'waiting' }).total, 0);
  assert.equal(fixtures.selectReportFixtures({ ...query, q: '사이버펑크' }).total, 2);
  assert.equal(fixtures.selectReportFixtures({ ...query, q: '%_' }).total, 0);
  assert.equal(fixtures.selectReportFixtures({ ...query, period: '30d' }).total, 146);
  assert.equal(fixtures.selectReportFixtures({ ...query, period: '3m' }).total, 147);
  assert.equal(fixtures.selectReportFixtures({ ...query, period: '6m' }).total, 148);
  assert.equal(JSON.stringify(fixtures.reportFixtures), before);
});
test('unavailable and denied states never imply empty results, sample details or aggregate counts', () => {
  for (const status of ['unavailable', 'forbidden', 'login-required', 'error', 'loading', 'not-found']) {
    const html = screen({ state: { status, message: '조회 미연결' }, statistics: fixtures.reportFixtureStatistics('7d') });
    assert.doesNotMatch(html, /data-report-row|R-001|김하루|145건|조회된 신고·문의가 없습니다/);
    assert.match(html, /집계 준비 중/);
  }
  assert.match(screen({ state: { status: 'ready', data: { total: 0, items: [] } } }), /조회된 신고·문의가 없습니다/);
});
test('waiting/reviewing/completed badges preserve raw status and viewer cannot write', () => {
  for (const [index, status] of [[0, 'waiting'], [1, 'reviewing'], [3, 'completed']]) {
    const html = detail(fixtures.reportFixtures[index]);
    assert.match(html, new RegExp(`data-report-status="${status}"`));
    if (status !== 'completed') { assert.match(html, /신고 콘텐츠 보기/); assert.match(html, /disabled="">검토 시작/); }
    else { assert.match(html, /서비스 문의/); assert.match(html, /disabled="">답변 작성/); }
  }
  assert.match(detail(fixtures.reportFixtures[2]), /disabled="" aria-haspopup="dialog"/);
  assert.match(detail({ ...fixtures.reportFixtures[0], status: null }), /상태 미연결/);
});
test('untrusted report content renders as text without HTML execution', () => {
  const html = detail({ ...fixtures.reportFixtures[0], message: '<img src=x onerror=alert(1)>', target: '<script>alert(1)</script>' });
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/); assert.doesNotMatch(html, /<script>|<img src="x"/);
});
test('persisted inquiry answers render as plain text with a real timestamp; no email delivery is implied', () => {
  const html = detail({ ...fixtures.reportFixtures[3], recordType: 'inquiry', rawStatus: 'answered',
    answer: '<script>unsafe()</script>\n문의 답변', answeredAt: '2026-11-23T01:30:00.123456Z', answerAvailability: 'available',
    actions: { allowedActions: [], disabledReason: 'terminal_state', scope: { actorUserId: '30', adminAccountId: '7' } } });
  assert.match(html, /data-inquiry-answer/); assert.match(html, /&lt;script&gt;unsafe\(\)&lt;\/script&gt;/);
  assert.match(html, /2026.11.23 10:30/); assert.match(html, /disabled="">답변 작성/);
  assert.doesNotMatch(html, /<script>|이메일로 발송|답변 내용은 수집되지 않았습니다/);
});
test('production report components exclude fixture data, unsafe rendering and browser persistence', () => {
  const seen = new Set();
  function inspect(file) {
    file = file.replaceAll('\\', '/');
    if (seen.has(file)) return; seen.add(file);
    const source = fs.readFileSync(path.join(root, file), 'utf8');
    assert.doesNotMatch(source, /tests\/fixtures|localStorage|sessionStorage|NEXT_PUBLIC_.*FIXTURE|dangerouslySetInnerHTML/);
    for (const match of source.matchAll(/import (?!type\b)[^;]*?from ["']([^"']+)["']/g)) {
      const name = match[1]; if (!name.startsWith('.') && !name.startsWith('@/')) continue;
      if (name === './api' || name === '@/lib/api') continue;
      if (name.endsWith('.css')) continue;
      const next = name.startsWith('@/') ? name.slice(2) : path.join(path.dirname(file), name);
      inspect(next + (fs.existsSync(path.join(root, next + '.tsx')) ? '.tsx' : '.ts'));
    }
  }
  inspect('app/admin/reports/page.tsx');
  assert.ok(seen.has('components/admin/report-detail-panel.tsx'));
  assert.equal(fs.existsSync(path.join(root, 'public/admin/report-fixture-cover.png')), false);
  assert.equal(model.reportUnavailable.status, 'unavailable');
});
test('every Figma asset is non-empty and preserves its LF source hash and root dimensions', () => {
  const manifest = require('../public/admin/report-sources.json');
  assert.equal(manifest.assets.length, 13);
  for (const asset of manifest.assets) {
    const bytes = fs.readFileSync(path.join(root, 'public/admin', asset.file));
    // Canonicalize only SVG text for Git's Windows CRLF checkout; keep binary assets byte-exact.
    const source = asset.file.endsWith('.svg') ? bytes.toString('utf8').replace(/\r\n/g, '\n') : bytes;
    assert.ok(bytes.length); assert.equal(crypto.createHash('sha256').update(source).digest('hex'), asset.sha256, asset.file);
    if (asset.file.endsWith('.svg')) {
      const tag = bytes.toString().match(/<svg[^>]+>/)[0];
      assert.equal(Number(tag.match(/width="([^"]+)"/)[1]), asset.width);
      assert.equal(Number(tag.match(/height="([^"]+)"/)[1]), asset.height);
    }
  }
});
test('shared AdminStatus keeps existing member/content messages and adds report context', () => {
  for (const [subject, word] of [['회원', '회원을'], ['콘텐츠', '콘텐츠를'], ['신고·문의', '신고·문의를']]) {
    const html = render('components/admin/admin-status.tsx', 'AdminStatus', { subject, state: { status: 'not-found', message: '조회 실패' } });
    assert.match(html, new RegExp(`${word} 찾을 수 없습니다`));
  }
});
