const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm'), crypto = require('node:crypto');
const test = require('node:test'), assert = require('node:assert/strict'), ts = require('typescript'), React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const root = path.resolve(__dirname, '..'), modules = new Map();
function load(file) {
  file = file.replaceAll('\\', '/');
  if (modules.has(file)) return modules.get(file).exports;
  const mod = { exports: {} }; modules.set(file, mod);
  const code = ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  const localRequire = name => {
    if (['react', 'react/jsx-runtime'].includes(name)) return require(name);
    if (name === 'next/link') return { __esModule: true, default: props => React.createElement('a', props) };
    if (name.endsWith('.module.css')) return { __esModule: true, default: new Proxy({}, { get: (_, key) => String(key) }) };
    const next = name.startsWith('@/') ? name.slice(2) : path.join(path.dirname(file), name);
    return load(next + (fs.existsSync(path.join(root, next + '.tsx')) ? '.tsx' : '.ts'));
  };
  vm.runInNewContext(`(function(module,exports,require){${code}\n})`, { URL, URLSearchParams, Intl })(mod, mod.exports, localRequire);
  return mod.exports;
}
const model = load('lib/admin-notices.ts'), fixtures = load('tests/fixtures/admin-notices.ts');
const render = (file, name, props) => renderToStaticMarkup(React.createElement(load(file)[name], props));
const screen = props => render('components/admin/notices-screen.tsx', 'NoticesScreen', { query: model.initialNoticeQuery, navigate() {}, ...props });

test('notice timestamps and schedule labels use Korea time; unknown data stays unknown', () => {
  assert.equal(model.noticeDate('2026-11-04T16:27:00Z'), '2026.11.05');
  assert.equal(model.noticeDate('2026-11-05T01:27:00Z', true), '2026.11.05 (목) 10:27');
  for (const invalid of [null, 'bad']) assert.equal(model.noticeDate(invalid), '—');
  assert.equal(model.noticeScheduleDate('2026-11-12'), '2026.11.12 (목)');
  for (const invalid of ['', 'bad']) assert.equal(model.noticeScheduleDate(invalid), '날짜 선택');
});
test('fixture search/status intersection and seven-row pagination do not mutate records', () => {
  const q = model.initialNoticeQuery, before = JSON.stringify(fixtures.noticeFixtures);
  assert.equal(fixtures.selectNoticeFixtures(q).total, 32); assert.equal(fixtures.selectNoticeFixtures(q).items.length, 7);
  assert.equal(fixtures.selectNoticeFixtures({ ...q, page: 2 }).items[0].id, 'fixture-notice-8');
  assert.equal(fixtures.selectNoticeFixtures({ ...q, status: 'scheduled' }).total, 4);
  assert.equal(fixtures.selectNoticeFixtures({ ...q, q: '시스템', status: 'published' }).total, 1);
  assert.equal(fixtures.selectNoticeFixtures({ ...q, q: '시스템', status: 'scheduled' }).total, 0);
  assert.equal(fixtures.selectNoticeFixtures({ ...q, q: '%_' }).total, 0);
  assert.equal(JSON.stringify(fixtures.noticeFixtures), before);
});
test('unavailable/denied/loading states hide records and statistics instead of implying empty success', () => {
  for (const status of ['unavailable', 'forbidden', 'login-required', 'error', 'loading', 'not-found']) {
    const html = screen({ state: { status, message: '조회 미연결' }, statistics: fixtures.noticeFixtureStatistics });
    assert.doesNotMatch(html, /data-notice-row|32건|업데이트 안내|조회된 공지가 없습니다/);
    assert.match(html, /집계 준비 중/);
  }
  assert.match(screen({ state: { status: 'ready', data: { total: 0, items: [] } } }), /조회된 공지가 없습니다/);
});
test('published and scheduled presentation statuses, missing view count and detail data remain distinct', () => {
  const html = screen({ state: { status: 'ready', data: fixtures.selectNoticeFixtures(model.initialNoticeQuery) }, statistics: fixtures.noticeFixtureStatistics });
  assert.match(html, /data-notice-status="published"/); assert.match(html, /data-notice-status="scheduled"/); assert.match(html, /<td>—<\/td>/);
  const detail = render('components/admin/notice-detail-panel.tsx', 'NoticeDetailPanel', { notice: fixtures.noticeFixtures[2], onClose() {} });
  assert.match(detail, /공지 내용 연결 준비 중/);
  assert.match(detail, /disabled="">삭제하기/); assert.match(detail, /disabled="">수정하기/);
});
test('compose switches between immediate and scheduled display while every write action remains disabled', () => {
  for (const scheduled of [false, true]) {
    const html = render('components/admin/notice-compose-panel.tsx', 'NoticeComposePanel', { initialDraft: { ...model.emptyNoticeDraft, scheduled, date: '2026-11-12', time: '10:00' } });
    assert.match(html, new RegExp(`aria-checked="${scheduled}"`));
    assert.match(html, /disabled="">임시 저장/); assert.match(html, new RegExp(`disabled="">${scheduled ? '예약 하기' : '게시하기'}`));
    assert.equal(html.includes('type="date"'), scheduled); assert.equal(html.includes('type="time"'), scheduled);
    assert.match(html, /작성 내용은 저장되지 않으며/);
  }
});
test('untrusted notice title and body are escaped text, never executable markup', () => {
  const html = render('components/admin/notice-detail-panel.tsx', 'NoticeDetailPanel', { notice: { ...fixtures.noticeFixtures[0], title: '<script>alert(1)</script>', body: '<img src=x onerror=alert(1)>' }, onClose() {} });
  assert.match(html, /&lt;script&gt;/); assert.match(html, /&lt;img/); assert.doesNotMatch(html, /<script>|<img src="x"/);
});
test('production route has no fixture switch, transport, permission bypass or persistent draft storage', () => {
  const seen = new Set();
  function inspect(file) {
    file = file.replaceAll('\\', '/'); if (seen.has(file)) return; seen.add(file);
    const source = fs.readFileSync(path.join(root, file), 'utf8');
    assert.doesNotMatch(source, /tests\/fixtures|fetch\(|apiRequest\(|localStorage|sessionStorage|useSearchParams|NEXT_PUBLIC_.*FIXTURE|dangerouslySetInnerHTML/);
    for (const match of source.matchAll(/import (?!type\b)[^;]*?from ["']([^"']+)["']/g)) {
      const name = match[1]; if ((!name.startsWith('.') && !name.startsWith('@/')) || name.endsWith('.css')) continue;
      const next = name.startsWith('@/') ? name.slice(2) : path.join(path.dirname(file), name);
      inspect(next + (fs.existsSync(path.join(root, next + '.tsx')) ? '.tsx' : '.ts'));
    }
  }
  inspect('app/admin/notices/page.tsx'); assert.ok(seen.has('components/admin/notice-detail-panel.tsx')); assert.equal(model.noticeUnavailable.status, 'unavailable');
});
test('all seventeen original Figma SVG assets preserve their LF source hashes and root dimensions', () => {
  const manifest = require('../public/admin/notice-sources.json'); assert.equal(manifest.assets.length, 17);
  for (const asset of manifest.assets) {
    const bytes = fs.readFileSync(path.join(root, 'public/admin', asset.file)); assert.ok(bytes.length);
    // Git may check text out with CRLF on Windows; manifests hash the committed LF SVG source.
    const source = bytes.toString('utf8').replace(/\r\n/g, '\n');
    assert.equal(crypto.createHash('sha256').update(source).digest('hex'), asset.sha256, asset.file);
    const tag = bytes.toString().match(/<svg[^>]+>/)[0];
    assert.equal(Number(tag.match(/width="([^"]+)"/)[1]), asset.width); assert.equal(Number(tag.match(/height="([^"]+)"/)[1]), asset.height);
  }
});
