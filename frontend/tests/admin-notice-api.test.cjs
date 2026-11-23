const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const test = require('node:test'), assert = require('node:assert/strict'), ts = require('typescript');
const root = path.resolve(__dirname, '..'), copy = value => JSON.parse(JSON.stringify(value));
const requestId = 'a0be8743-44b4-418d-9a15-e3d901c4ba11', version = '2026-11-23T13:00:00.123456+09:00';
const scope = { actorUserId: '30', adminAccountId: '7' };
const capabilities = () => ({ allowedActions: ['create'], disabledReason: null, scope });
const draft = () => ({ title: ' 운영 공지 ', body: ' 본문\n다음 줄 ', scheduled: false, date: '2030-11-23', time: '15:30' });
const summary = (status = 'draft') => ({ id: '9223372036854775807', title: '공지 제목', status, scheduledAt: status === 'scheduled' ? '2030-11-23T15:30:00+09:00' : null,
  publishedAt: status === 'published' ? version : null, createdAt: version, updatedAt: version, readCount: 0 });
const detail = (status = 'draft') => ({ ...summary(status), body: '본문', actions: { scope, disabledReason: status === 'published' ? 'terminal_state' : null,
  allowedActions: status === 'draft' ? ['save_draft', 'publish', 'schedule'] : status === 'scheduled' ? ['cancel_schedule'] : [] } });
const page = () => ({ items: [summary()], total: 1, page: 1, pageSize: 7, statistics: { all: 1, draft: 1, scheduled: 0, published: 0 }, capabilities: capabilities() });
const session = () => ({ authenticated: true, sessionState: 'active', user: { id: 30, accountType: 'user' }, onboardingCompleted: true, csrfToken: 'qa-csrf' });
const result = (action = 'save_draft') => ({ noticeId: summary().id, status: action === 'publish' ? 'published' : action === 'schedule' ? 'scheduled' : 'draft',
  scheduledAt: action === 'schedule' ? '2030-11-23T15:30:00+09:00' : null, publishedAt: action === 'publish' ? version : null, updatedAt: version, auditLogId: '91', replayed: false });
function runtime(respond = () => { throw new Error('Unexpected transport'); }) {
  const modules = new Map(), calls = [], browser = new EventTarget();
  const context = vm.createContext({ URL, URLSearchParams, Headers, Response, AbortController, Event, window: browser, setTimeout, clearTimeout,
    process: { env: { NEXT_PUBLIC_API_BASE_URL: 'http://127.0.0.1:8199' } },
    fetch: async (url, init) => { calls.push({ url, init }); const value = await respond(new URL(url), init); return value instanceof Response ? value : new Response(JSON.stringify(value)); } });
  const load = file => {
    file = file.replaceAll('\\', '/'); if (modules.has(file)) return modules.get(file).exports;
    const mod = { exports: {} }; modules.set(file, mod);
    const code = ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
    vm.runInContext(`(function(module,exports,require){${code}\n})`, context)(mod, mod.exports,
      name => load((name.startsWith('@/') ? name.slice(2) : path.join(path.dirname(file), name)) + '.ts'));
    return mod.exports;
  };
  return { model: load('lib/admin-notices.ts'), write: load('lib/admin-notice-write.ts'), flow: load('lib/admin-notice-flow.ts'), api: load('lib/api.ts'), calls };
}
test('list reads private search header, paginates seven rows, preserves bigint IDs and real counts', async () => {
  const env = runtime(() => ({ ...page(), page: 2 }));
  const data = await env.model.loadAdminNotices({ q: ' 운영 공지 ', status: 'draft', page: 2 });
  const call = env.calls[0], url = new URL(call.url);
  assert.equal(url.pathname, '/api/v1/admin/notices'); assert.equal(url.searchParams.get('status'), 'draft'); assert.equal(url.searchParams.get('page'), '2');
  assert.equal(url.searchParams.get('pageSize'), '7'); assert.equal(url.searchParams.has('q'), false);
  assert.equal(call.init.headers.get('X-Admin-Search'), encodeURIComponent('운영 공지')); assert.equal(call.init.cache, 'no-store');
  assert.equal(data.items[0].id, '9223372036854775807'); assert.equal(data.items[0].updatedAt, version); assert.equal(data.statistics.draft, 1);
});
test('malformed statuses, missing scope and counts are rejected rather than enabling writes', async () => {
  for (const mutate of [x => x.items[0].id = 1, x => x.items[0].status = 'queued', x => x.items[0].updatedAt = '2026-11-23T13:00:00',
    x => x.items[0].readCount = -1, x => delete x.capabilities, x => x.capabilities.scope.actorUserId = 30, x => x.capabilities.allowedActions = ['publish'], x => x.statistics.draft = null, x => x.page = 2, x => x.pageSize = 20]) {
    const data = copy(page()); mutate(data);
    await assert.rejects(runtime(() => data).model.loadAdminNotices({ q: '', status: '', page: 1 }), { code: 'INVALID_ADMIN_RESPONSE' });
  }
});
test('detail fetch verifies target and state-specific actions including terminal read-only status', async () => {
  for (const status of ['draft', 'scheduled', 'published']) assert.equal((await runtime(() => detail(status)).model.loadAdminNotice(summary().id)).status, status);
  for (const mutate of [x => x.id = '42', x => x.actions.allowedActions = ['create'], x => x.actions.allowedActions = ['cancel_schedule'], x => x.body = null]) {
    const data = detail(); mutate(data); await assert.rejects(runtime(() => data).model.loadAdminNotice(summary().id), { code: 'INVALID_ADMIN_RESPONSE' });
  }
});
test('create carries exact immutable request, current CSRF and pinned actor/grant headers', async () => {
  const env = runtime((url, init) => url.pathname.endsWith('/session') ? session() : init.method === 'POST' ? result() : page());
  const request = env.write.prepareNoticeRequest(null, 'save_draft', draft(), capabilities(), requestId);
  const response = await env.write.submitNoticeRequest(null, request, scope);
  const sent = env.calls.find(call => call.init.method === 'POST');
  assert.equal(new URL(sent.url).pathname, '/api/v1/admin/notices'); assert.deepEqual(JSON.parse(sent.init.body), { requestId, action: 'save_draft', title: '운영 공지', body: '본문\n다음 줄', scheduledAt: null });
  assert.equal(sent.init.headers.get('X-Admin-Notice-Actor'), '30'); assert.equal(sent.init.headers.get('X-Admin-Notice-Grant'), '7');
  assert.equal(sent.init.headers.get('X-CSRF-Token'), 'qa-csrf'); assert.equal(sent.init.credentials, 'include'); assert.equal(response.noticeId, summary().id);
});
test('draft update preserves microseconds and schedule serializes a validated Korea time', async () => {
  const env = runtime((url, init) => url.pathname.endsWith('/session') ? session() : init.method === 'POST' ? result('schedule') : page());
  const request = env.write.prepareNoticeRequest(detail(), 'schedule', draft(), capabilities(), requestId, Date.parse(version));
  assert.equal(request.expectedUpdatedAt, version); assert.equal(request.expectedStatus, 'draft'); assert.equal(request.scheduledAt, '2030-11-23T15:30:00+09:00');
  await env.write.submitNoticeRequest(summary().id, request, scope);
  assert.equal(new URL(env.calls.find(call => call.init.method === 'POST').url).pathname, `/api/v1/admin/notices/${summary().id}/actions`);
  assert.throws(() => env.write.prepareNoticeRequest(detail(), 'schedule', { ...draft(), date: '2026-02-30' }, capabilities(), requestId));
  assert.throws(() => env.write.prepareNoticeRequest(detail(), 'schedule', { ...draft(), date: '2020-01-01' }, capabilities(), requestId));
  assert.throws(() => env.write.scheduleInstant('2030-01-01', '24:00'));
});
test('scheduled cancellation is explicit and cannot edit or publish the scheduled item', () => {
  const env = runtime();
  assert.deepEqual(copy(env.write.prepareNoticeRequest(detail('scheduled'), 'cancel_schedule', draft(), capabilities(), requestId)), { requestId, action: 'cancel_schedule', expectedStatus: 'scheduled', expectedUpdatedAt: version });
  for (const action of ['save_draft', 'publish', 'schedule']) assert.throws(() => env.write.prepareNoticeRequest(detail('scheduled'), action, draft(), capabilities(), requestId));
  assert.throws(() => env.write.prepareNoticeRequest(detail('published'), 'save_draft', draft(), capabilities(), requestId));
});
test('input validation counts Unicode code points and fails closed without capability', () => {
  const env = runtime();
  assert.equal(env.write.noticeTextError('😀'.repeat(100), '😀'.repeat(5000)), null);
  for (const [title, body] of [['a'.repeat(101), 'x'], ['x', 'a'.repeat(5001)], [' ', 'x'], ['x', '\u0000'], ['x', '\ud800']]) assert.ok(env.write.noticeTextError(title, body));
  assert.throws(() => env.write.prepareNoticeRequest(null, 'publish', draft(), undefined, requestId));
  assert.throws(() => env.write.prepareNoticeRequest(null, 'publish', draft(), { allowedActions: [], disabledReason: 'permission_denied', scope: null }, requestId));
});
test('changed actor, grant or revoked permission prevents every POST', async () => {
  for (const changed of ['actor', 'grant', 'permission']) {
    const value = copy(page()); if (changed === 'grant') value.capabilities.scope.adminAccountId = '8';
    if (changed === 'permission') value.capabilities = { allowedActions: [], disabledReason: 'permission_denied', scope: null };
    const env = runtime(url => url.pathname.endsWith('/session') ? { ...session(), user: { id: changed === 'actor' ? 31 : 30, accountType: 'user' } } : value);
    const request = env.write.prepareNoticeRequest(null, 'publish', draft(), capabilities(), requestId);
    await assert.rejects(env.write.submitNoticeRequest(null, request, scope), { code: 'NOTICE_SCOPE_CHANGED' });
    assert.equal(env.calls.filter(call => call.init.method === 'POST').length, 0);
  }
});
test('unknown outcomes reuse exact request, scope and UUID, block duplicates and survive release', async () => {
  const env = runtime(); let submissions = [], attempts = 0;
  const flow = env.flow.createNoticeFlow({ newRequestId: () => requestId, submit: async (id, request, captured) => {
    submissions.push({ id, request, captured }); if (++attempts === 1) throw new Error('connection lost'); return { ...result(), replayed: true };
  } });
  await flow.execute(null, 'save_draft', draft(), capabilities()); assert.equal(flow.state().phase, 'uncertain');
  flow.release(); await flow.execute(null, 'publish', { ...draft(), body: 'changed' }, capabilities()); assert.equal(submissions.length, 1);
  await flow.recheck(); assert.equal(flow.state().phase, 'succeeded'); assert.equal(flow.state().result.replayed, true);
  assert.equal(submissions[0].request, submissions[1].request); assert.equal(submissions[0].captured, submissions[1].captured);
  assert.ok(Object.isFrozen(submissions[0].request));
});
test('409 after an uncertain attempt remains uncertain and retries have a bounded count', async () => {
  const env = runtime(); let count = 0;
  const flow = env.flow.createNoticeFlow({ newRequestId: () => requestId, submit: async () => { if (++count === 1) throw new Error('lost'); throw new env.api.ApiError('', 409, 'NOTICE_SCOPE_CHANGED'); } });
  await flow.execute(null, 'publish', draft(), capabilities());
  for (let i = 0; i < 5; i++) await flow.recheck();
  assert.equal(count, 4); assert.equal(flow.state().phase, 'uncertain'); assert.equal(flow.state().rechecks, 3);
});
test('permission/conflict failures requery while malformed success and server failures never claim failure', () => {
  const env = runtime(), { ApiError } = env.api;
  for (const [status, code] of [[401, 'AUTH_REQUIRED'], [403, 'ADMIN_WRITE_REQUIRED'], [409, 'NOTICE_STATE_CONFLICT']]) {
    assert.equal(env.write.noticeWriteFailure(new ApiError('', status, code)).reload, true);
    assert.equal(env.write.noticeWriteFailure(new ApiError('', status, code)).uncertain, false);
  }
  for (const [status, code] of [[200, 'INVALID_NOTICE_RESPONSE'], [403, 'CSRF_INVALID'], [503, 'UNAVAILABLE'], [500, 'INTERNAL']]) assert.equal(env.write.noticeWriteFailure(new ApiError('', status, code)).uncertain, true);
});
test('a malformed success cannot be shown as confirmed publication', async () => {
  for (const mutate of [x => x.noticeId = 4, x => x.status = 'scheduled', x => x.auditLogId = null, x => x.updatedAt = '2026-11-23T12:00:00', x => x.publishedAt = null]) {
    const response = result('publish'); mutate(response);
    const env = runtime((url, init) => url.pathname.endsWith('/session') ? session() : init.method === 'POST' ? response : page());
    const request = env.write.prepareNoticeRequest(null, 'publish', draft(), capabilities(), requestId);
    await assert.rejects(env.write.submitNoticeRequest(null, request, scope), { code: 'INVALID_NOTICE_RESPONSE' });
  }
});
test('cancel sends only its immutable version fields and scheduled retries remain possible after due time', async () => {
  const env = runtime((url, init) => url.pathname.endsWith('/session') ? session() : init.method === 'POST' ? result('cancel_schedule') : page());
  const request = env.write.prepareNoticeRequest(detail('scheduled'), 'cancel_schedule', draft(), capabilities(), requestId);
  await env.write.submitNoticeRequest(summary().id, request, scope);
  const sent = env.calls.find(call => call.init.method === 'POST');
  assert.deepEqual(Object.keys(JSON.parse(sent.init.body)).sort(), ['action', 'expectedStatus', 'expectedUpdatedAt', 'requestId']);
  const past = '2020-01-01T00:00:00+09:00';
  const retry = runtime((url, init) => url.pathname.endsWith('/session') ? session() : init.method === 'POST' ? { ...result('schedule'), scheduledAt: past, replayed: true } : page());
  const response = await retry.write.submitNoticeRequest(null, { requestId, action: 'schedule', title: '공지', body: '본문', scheduledAt: past }, scope);
  assert.equal(response.replayed, true);
});
