const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const root = path.resolve(__dirname, '..');
const modules = new Map();
function load(file) {
  file = file.replaceAll('\\', '/');
  if (modules.has(file)) return modules.get(file).exports;
  const mod = { exports: {} }; modules.set(file, mod);
  const code = ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  const localRequire = name => {
    if (['react', 'react/jsx-runtime'].includes(name)) return require(name);
    if (name === 'next/link') return { __esModule: true, default: props => React.createElement('a', props) };
    if (name === 'next/navigation') return {};
    if (name.endsWith('.module.css')) return { __esModule: true, default: new Proxy({}, { get: (_, key) => String(key) }) };
    const next = name.startsWith('@/') ? name.slice(2) : path.join(path.dirname(file), name);
    return load(next + (fs.existsSync(path.join(root, next + '.tsx')) ? '.tsx' : '.ts'));
  };
  vm.runInNewContext(`(function(module,exports,require){${code}\n})`, { URLSearchParams, Intl })(mod, mod.exports, localRequire);
  return mod.exports;
}
const model = load('lib/admin-members.ts');
const fixtures = load('tests/fixtures/admin-members.ts');
const plain = value => JSON.parse(JSON.stringify(value));
const render = (file, name, props) => renderToStaticMarkup(React.createElement(load(file)[name], props));

test('member query validates filters, bounded pages and preserves list context without private search', () => {
  const query = model.parseMemberQuery(new URLSearchParams('status=active&provider=kakao&sort=oldest&page=2'), '  private@example.test  ');
  assert.equal(query.q, 'private@example.test');
  const serialized = model.memberQueryString(query);
  assert.doesNotMatch(serialized, /private|example|q=/);
  assert.deepEqual(plain(model.parseMemberQuery(new URLSearchParams(serialized), query.q)), plain(query));
  for (const invalid of ['page=0', 'page=-1', 'page=1e2', 'page=1000001', 'status=admin', 'provider=google', 'sort=bad', 'status=__proto__']) assert.throws(() => model.parseMemberQuery(new URLSearchParams(invalid)));
  assert.equal(model.parseMemberQuery(new URLSearchParams('q=raw@example.test')).q, '');
});
test('fixture-only search, combined filters, sorting and pagination compose without mutating records', () => {
  const query = model.parseMemberQuery(new URLSearchParams());
  const first = fixtures.selectMemberFixtures(query), second = fixtures.selectMemberFixtures({ ...query, page: 2 });
  assert.equal(first.items.length, 5); assert.equal(second.items.length, 5); assert.notEqual(first.items[0].id, second.items[0].id);
  assert.equal(fixtures.selectMemberFixtures({ ...query, q: '김하루' }).total, 5);
  assert.equal(fixtures.selectMemberFixtures({ ...query, q: '%_' }).total, 0);
  const filtered = fixtures.selectMemberFixtures({ ...query, status: 'active', provider: 'naver', sort: 'oldest' });
  assert.ok(filtered.items.every(item => item.status === 'active' && item.provider === 'naver'));
  assert.equal(fixtures.selectMemberFixtures({ ...query, sort: 'oldest' }).items[0].id, 'fixture-31');
  assert.equal(fixtures.memberFixtures[0].id, 'fixture-1');
});
test('raw emails never render and dates use Korea time', () => {
  assert.equal(model.privateEmail('raw-private@example.test'), '—');
  assert.equal(model.privateEmail('haru***@example.test'), 'haru***@example.test');
  assert.equal(model.privateEmail(null), '—');
  assert.equal(model.memberDate('2026-11-05T00:42:00Z'), '2026.11.05 09:42');
});
test('unavailable list is distinct from successful empty, with no fake totals or rows', () => {
  const props = { query: model.parseMemberQuery(new URLSearchParams()), navigate() {} };
  const unavailable = render('components/admin/members-screen.tsx', 'MembersScreen', { ...props, state: model.memberUnavailable });
  assert.match(unavailable, /조회 연결 준비 중/); assert.doesNotMatch(unavailable, /data-member-row|조회된 회원이 없습니다|24,318|50%/);
  const empty = render('components/admin/members-screen.tsx', 'MembersScreen', { ...props, state: { status: 'ready', data: { total: 0, items: [] } } });
  assert.match(empty, /조회된 회원이 없습니다/);
});
test('detail keeps unavailable blocks, masks raw identity and disables every write control', () => {
  const data = { ...fixtures.memberFixtures[0], maskedEmail: 'private@example.test', savedCount: null, likedCount: 0, onboarding: null, history: null };
  const html = render('components/admin/member-detail-screen.tsx', 'MemberDetailScreen', { state: { status: 'ready', data }, back: '/admin/users?page=2' });
  assert.doesNotMatch(html, /private@example|진격의 거인/); assert.match(html, /답변 연결 준비 중/); assert.match(html, /변경 이력 연결 준비 중/);
  assert.match(html, /disabled=""[^>]*><img[^>]*src="\/admin\/memo-add.svg"[^>]*\/>메모 작성/);
  assert.match(html, /disabled="" aria-label="메모 수정 준비 중"/); assert.match(html, /disabled="" aria-label="메모 삭제 준비 중"/);
  assert.match(html, /<dialog[^>]+aria-labelledby="nickname-history-title"/);
});
test('production member entry points are disconnected and have no fixture/transport/storage escape hatch', () => {
  for (const file of ['app/admin/users/page.tsx', 'app/admin/users/[memberId]/page.tsx', 'components/admin/members-screen.tsx', 'components/admin/member-detail-screen.tsx', 'lib/admin-members.ts']) {
    const source = fs.readFileSync(path.join(root, file), 'utf8');
    assert.doesNotMatch(source, /from ["'][^"']*tests\/|fetch\(|apiRequest\(|localStorage|sessionStorage|NEXT_PUBLIC_.*FIXTURE/);
  }
  assert.equal(model.memberUnavailable.status, 'unavailable');
});
