const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const test = require('node:test'), assert = require('node:assert/strict'), ts = require('typescript');
const root = path.resolve(__dirname, '..');
const clone = value => JSON.parse(JSON.stringify(value));
function runtime(respond, deadline = 15000) {
  const modules = new Map(), calls = [], browser = new EventTarget();
  const context = vm.createContext({ URL, URLSearchParams, Headers, Response, AbortController, Event, window: browser,
    setTimeout: (fn, ms) => setTimeout(fn, Math.min(ms, deadline)), clearTimeout,
    process: { env: { NEXT_PUBLIC_API_BASE_URL: 'http://127.0.0.1:8199' } },
    fetch: async (url, init) => { calls.push({ url, init }); const value = await respond(new URL(url), init); return value instanceof Response ? value : new Response(JSON.stringify(value)); } });
  const load = file => {
    file = file.replaceAll('\\', '/'); if (modules.has(file)) return modules.get(file).exports;
    const mod = { exports: {} }; modules.set(file, mod);
    const code = ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
    const localRequire = name => load((name.startsWith('@/') ? name.slice(2) : path.join(path.dirname(file), name)) + '.ts');
    vm.runInContext(`(function(module,exports,require){${code}\n})`, context)(mod, mod.exports, localRequire);
    return mod.exports;
  };
  return { members: load('lib/admin-members.ts'), reports: load('lib/admin-reports.ts'), contract: load('lib/admin-read-contract.ts'), api: load('lib/api.ts'), calls, browser };
}
const now = '2026-11-23T01:00:00Z';
const member = (id = '101') => ({ id, nickname: '조회 테스트', maskedEmail: 'a***@example.test', provider: 'kakao', domain: '애니메이션', joinedAt: now, lastLoginAt: null, status: 'active' });
const memberDetail = () => ({ ...member(), history: null, notes: null, onboarding: { works: [], genres: [], themes: [], contentTypes: [] }, savedCount: 0, likedCount: null });
const members = () => ({ items: [member()], total: 1, page: 1, pageSize: 5 });
const summary = (type = 'report', id = '1') => ({ id: `${type === 'report' ? 'R' : 'I'}-${id}`, recordId: id, recordType: type, kind: type === 'report' ? 'content' : 'service',
  title: type === 'report' ? '테스트 제목' : null, reasonCode: null, reason: null, status: 'completed', rawStatus: type === 'report' ? 'rejected' : 'answered', receivedAt: now, updatedAt: now });
const reports = () => ({ items: [summary(), summary('inquiry')], page: 1, pageSize: 5, total: 2, periodStart: '2026-11-16T01:00:00Z', periodEnd: now, statistics: { all: 2, waiting: 0, reviewing: 0, completed: 2 } });
const detail = type => ({ ...summary(type), message: '<script>literal text</script>', target: type === 'report' ? '콘텐츠 제목' : null, content: null, answer: null, answerAvailability: 'unavailable' });

test('member list/detail GET uses cookies, no-store, private header and exact string IDs', async () => {
  const env = runtime(url => url.pathname.endsWith('/101') ? memberDetail() : members());
  const query = env.members.parseMemberQuery(new URLSearchParams(), '비공개+search@example.test');
  const controller = new AbortController();
  const page = await env.members.loadAdminMembers(query, controller.signal);
  assert.equal(page.items[0].lastLoginAt, null);
  const data = await env.members.loadAdminMember('101', controller.signal);
  assert.equal(data.savedCount, 0); assert.equal(data.likedCount, null); assert.equal(data.notes, null);
  assert.equal(env.calls[0].init.headers.get('X-Admin-Search'), encodeURIComponent(query.q));
  for (const call of env.calls) {
    assert.doesNotMatch(call.url, /search|example|%40|비공개/);
    assert.equal(call.init.method ?? 'GET', 'GET'); assert.equal(call.init.credentials, 'include'); assert.equal(call.init.cache, 'no-store'); assert.equal(call.init.body, undefined);
  }
  assert.equal(new URL(env.calls[0].url).searchParams.get('pageSize'), '5');
  for (const invalid of ['0', '../1', '9223372036854775808', 101]) await assert.rejects(env.members.loadAdminMember(invalid));
});

test('membership response rejects raw identity, invented states and contradictory withdrawn records', async () => {
  for (const patch of [{ maskedEmail: 'raw@example.test' }, { status: 'dormant' }, { status: 'restricted' }, { id: 101 }, { provider: 'google' }, { lastLoginAt: 'yesterday' }, { status: 'withdrawn' }]) {
    const value = members(); Object.assign(value.items[0], patch); const env = runtime(() => value);
    await assert.rejects(env.members.loadAdminMembers(env.members.parseMemberQuery(new URLSearchParams())), { code: 'INVALID_ADMIN_RESPONSE' });
  }
  const withdrawn = { ...member(), nickname: '탈퇴 회원', status: 'withdrawn', maskedEmail: null, provider: null, domain: null, joinedAt: null, lastLoginAt: null, history: null, notes: null, onboarding: null, savedCount: null, likedCount: null };
  assert.deepEqual(clone(await runtime(() => withdrawn).members.loadAdminMember('101')), withdrawn);
  await assert.rejects(runtime(() => ({ ...withdrawn, notes: [] })).members.loadAdminMember('101'), { code: 'INVALID_ADMIN_RESPONSE' });
  const big = member('9223372036854775807'), value = { ...members(), items: [big] }, env = runtime(() => value);
  assert.equal((await env.members.loadAdminMembers(env.members.parseMemberQuery(new URLSearchParams()))).items[0].id, big.id);
});

test('report summary does not contain bodies and overlapping numeric IDs select independent detail endpoints', async () => {
  const env = runtime(url => url.pathname === '/api/v1/admin/reports' ? reports() : detail(url.pathname.includes('/inquiries/') ? 'inquiry' : 'report'));
  const page = await env.reports.loadAdminReports({ ...env.reports.initialReportQuery, q: '신고+사유' });
  assert.deepEqual(Array.from(page.items, item => item.id), ['R-1', 'I-1']);
  assert.equal(page.items[0].number, 'R-1'); assert.equal(page.items[0].rawStatus, 'rejected');
  assert.equal(env.calls[0].init.headers.get('X-Admin-Search'), encodeURIComponent('신고+사유'));
  for (const item of page.items) assert.equal((await env.reports.loadAdminReport(item)).id, item.id);
  assert.deepEqual(env.calls.map(call => new URL(call.url).pathname), ['/api/v1/admin/reports', '/api/v1/admin/reports/1', '/api/v1/admin/inquiries/1']);
  assert.equal((await env.reports.loadAdminReport(page.items[1])).answer, null);
});

test('report validation detects status/cohort/count/identity mismatch and leaked list bodies', async () => {
  const mutate = [v => v.items[0].recordId = 1, v => v.items[0].id = 'I-1', v => v.items[0].rawStatus = 'received', v => v.items[0].message = 'private body',
    v => v.items.push(v.items[0]), v => v.statistics.completed = 0, v => v.items[1].kind = 'content', v => v.page = 2];
  for (const change of mutate) { const value = reports(); change(value); const env = runtime(() => value); await assert.rejects(env.reports.loadAdminReports(env.reports.initialReportQuery), { code: 'INVALID_ADMIN_RESPONSE' }); }
  const wrong = detail('inquiry');
  await assert.rejects(runtime(() => wrong).reports.loadAdminReport(summary()), { code: 'INVALID_ADMIN_RESPONSE' });
  for (const q of ['x'.repeat(301), 'bad\nheader']) {
    const env = runtime(() => reports()); await assert.rejects(env.reports.loadAdminReports({ ...env.reports.initialReportQuery, q })); assert.equal(env.calls.length, 0);
  }
});

test('HTTP auth, missing and temporary unavailable states remain distinct from empty success', async () => {
  for (const [status, code, expected] of [[401, 'AUTH_REQUIRED', 'login-required'], [403, 'FORBIDDEN', 'forbidden'], [404, 'MEMBER_NOT_FOUND', 'not-found'], [404, 'ADMIN_REPORT_NOT_FOUND', 'not-found'], [404, 'ADMIN_INQUIRY_NOT_FOUND', 'not-found'], [404, 'NOT_FOUND', 'unavailable'], [503, 'ADMIN_UNAVAILABLE', 'unavailable'], [500, 'ERROR', 'error']]) {
    const env = runtime(() => new Response(JSON.stringify({ code, message: 'fixture' }), { status }));
    await assert.rejects(env.members.loadAdminMember('101'), error => { assert.equal(env.contract.memberSupportFailure(error).status, expected); return true; });
  }
  const env = runtime(() => ({ ...members(), items: [], total: 0 }));
  assert.equal((await env.members.loadAdminMembers(env.members.parseMemberQuery(new URLSearchParams()))).total, 0);
});

test('cancellation prevents stale 401 side effects; deadline settles an unresponsive read', async () => {
  let release; const env = runtime(() => new Promise(resolve => { release = resolve; }));
  let unauthorized = 0; env.browser.addEventListener(env.api.API_UNAUTHORIZED_EVENT, () => unauthorized++);
  const controller = new AbortController(), pending = env.members.loadAdminMember('101', controller.signal);
  controller.abort(); await assert.rejects(pending, { name: 'AbortError' });
  release(new Response(JSON.stringify({ code: 'AUTH_REQUIRED' }), { status: 401 })); await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(unauthorized, 0); assert.equal(env.calls[0].init.signal.aborted, true);
  const hung = runtime(() => new Promise(() => {}), 15);
  await assert.rejects(hung.reports.loadAdminReport(summary()), { name: 'RequestTimeoutError' });
  assert.equal(hung.calls[0].init.signal.aborted, true);
});
