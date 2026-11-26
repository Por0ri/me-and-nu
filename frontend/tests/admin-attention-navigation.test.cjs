const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const test = require('node:test'), assert = require('node:assert/strict'), ts = require('typescript');
const fixture = require('./helpers/admin-attention-fixture.cjs');
const root = path.resolve(__dirname, '..'), clone = value => JSON.parse(JSON.stringify(value));
function runtime(respond = () => { throw new Error('Unexpected request'); }) {
  const modules = new Map(), calls = [], browser = new EventTarget();
  const context = vm.createContext({ URL, URLSearchParams, Headers, Response, AbortController, Event, window: browser, setTimeout, clearTimeout,
    process: { env: { NEXT_PUBLIC_API_BASE_URL: 'http://127.0.0.1:8197' } },
    fetch: async (url, init) => { calls.push({ url, init }); const body = await respond(new URL(url), init); return body instanceof Response ? body : new Response(JSON.stringify(body)); } });
  function load(file) {
    file = file.replaceAll('\\', '/'); if (modules.has(file)) return modules.get(file).exports;
    const mod = { exports: {} }; modules.set(file, mod);
    const code = ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
    const localRequire = name => load((name.startsWith('@/') ? name.slice(2) : path.join(path.dirname(file), name)) + '.ts');
    vm.runInContext('(function(module,exports,require){' + code + '\n})', context)(mod, mod.exports, localRequire);
    return mod.exports;
  }
  return { navigation: load('lib/admin-attention-navigation.ts'), reports: load('lib/admin-reports.ts'),
    statistics: load('lib/admin-statistics-api.ts'), contract: load('lib/admin-read-contract.ts'), calls };
}

test('dashboard URL round-trips every supported period/filter and bounded page', () => {
  const { navigation: n, calls } = runtime();
  for (const period of ['7d', '30d', '3m', '6m']) for (const attention of ['all', 'report', 'inquiry', 'notice']) for (const page of [1, 2, 100000]) {
    const expected = { period, attention, page }, url = new URL(n.dashboardHref(expected), 'http://localhost');
    assert.equal(url.pathname, '/admin/dashboard');
    assert.deepEqual(clone(n.parseDashboardQuery(url.searchParams)), expected);
  }
  assert.equal(n.dashboardHref({ period: '7d', attention: 'all', page: 1 }), '/admin/dashboard');
  assert.equal(calls.length, 0);
});
test('invalid navigation values normalize locally and arbitrary return URLs are ignored', () => {
  const { navigation: n } = runtime();
  for (const page of ['0', '-1', '1.5', '1e2', '01', '100001', 'Infinity', '9007199254740993', '1/path']) {
    const parsed = n.parseDashboardQuery(new URLSearchParams({ period: '__proto__', attention: 'constructor', page, returnTo: 'https://example.test' }));
    assert.deepEqual(clone(parsed), { period: '7d', attention: 'all', page: 1 });
    assert.equal(n.dashboardHref(parsed), '/admin/dashboard');
  }
});
test('same numeric IDs produce distinct routes and preserve only dashboard selections', () => {
  const { navigation: n } = runtime(), query = { period: '30d', attention: 'all', page: 3 };
  for (const kind of ['report', 'inquiry']) {
    const url = new URL(n.attentionDetailHref({ kind, recordId: 41 }, query), 'http://localhost');
    assert.equal(url.pathname, '/admin/reports/' + kind + '/41');
    assert.deepEqual(clone(n.parseDashboardQuery(url.searchParams)), query);
    assert.equal(n.parseSupportTarget(kind, '41').id, kind === 'report' ? 'R-41' : 'I-41');
  }
});
test('direct detail IDs remain lossless bigint strings and malformed targets cannot fetch', () => {
  const { navigation: n, calls } = runtime();
  for (const type of ['report', 'inquiry']) {
    assert.equal(n.parseSupportTarget(type, '9223372036854775807').recordId, '9223372036854775807');
    for (const id of ['0', '-1', '01', '1.5', '1e2', '../41', '41/actions', '9223372036854775808', '']) assert.equal(n.parseSupportTarget(type, id), null);
  }
  for (const type of ['reports', 'inquiries', '__proto__', 'constructor', '../report', '']) assert.equal(n.parseSupportTarget(type, '41'), null);
  assert.equal(calls.length, 0);
});
test('old report and inquiry bypass the seven-day list and issue only matching detail GETs', async () => {
  const env = runtime(url => fixture.detail(url.pathname.includes('/inquiries/') ? 'inquiry' : 'report', url.pathname.split('/').pop()));
  assert.equal(fixture.recentReports().items.length, 0);
  for (const type of ['report', 'inquiry']) {
    const target = env.navigation.parseSupportTarget(type, '41');
    const data = await env.reports.loadAdminReport(target);
    assert.equal(data.id, target.id); assert.equal(data.receivedAt, fixture.old);
  }
  assert.deepEqual(env.calls.map(c => new URL(c.url).pathname), ['/api/v1/admin/reports/41', '/api/v1/admin/inquiries/41']);
  for (const { url, init } of env.calls) {
    assert.equal(new URL(url).search, ''); assert.equal(init.method ?? 'GET', 'GET');
    assert.equal(init.body, undefined); assert.equal(init.credentials, 'include'); assert.equal(init.cache, 'no-store');
  }
});
test('wrong record type in a detail response never displays as the requested item', async () => {
  const env = runtime(() => fixture.detail('inquiry'));
  await assert.rejects(env.reports.loadAdminReport(env.navigation.parseSupportTarget('report', '41')), { code: 'INVALID_ADMIN_RESPONSE' });
});
test('direct detail preserves distinct 401/403/404/503/network failure states', async () => {
  for (const [status, code, expected] of [[401, 'AUTH_REQUIRED', 'login-required'], [403, 'FORBIDDEN', 'forbidden'],
    [404, 'ADMIN_REPORT_NOT_FOUND', 'not-found'], [404, 'ADMIN_INQUIRY_NOT_FOUND', 'not-found'], [503, 'ADMIN_UNAVAILABLE', 'unavailable'], [500, 'ERROR', 'error']]) {
    const env = runtime(() => new Response(JSON.stringify({ code }), { status }));
    await assert.rejects(env.reports.loadAdminReport(env.navigation.parseSupportTarget('report', '41')), error => {
      assert.equal(env.contract.memberSupportFailure(error).status, expected); return true;
    });
  }
  const env = runtime(() => { throw new TypeError('Network failed'); });
  await assert.rejects(env.reports.loadAdminReport(env.navigation.parseSupportTarget('inquiry', '41')), error => {
    assert.equal(env.contract.memberSupportFailure(error).status, 'error'); return true;
  });
});
test('browser fixtures satisfy existing dashboard/attention GET contracts, including an empty recent list', async () => {
  const env = runtime(url => url.pathname.endsWith('/attention') ? fixture.attention(url.searchParams.get('kind'), Number(url.searchParams.get('page'))) :
    url.pathname.endsWith('/reports') ? fixture.recentReports() : fixture.dashboard(url.searchParams.get('period')));
  for (const period of ['7d', '30d', '3m', '6m']) await env.statistics.loadAdminDashboard(period);
  for (const kind of ['all', 'report', 'inquiry', 'notice']) await env.statistics.loadAdminAttention({ kind, page: 2, pageSize: 5 });
  assert.equal((await env.reports.loadAdminReports(env.reports.initialReportQuery)).total, 0);
});
