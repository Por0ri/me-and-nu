const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const test = require('node:test'), assert = require('node:assert/strict'), ts = require('typescript');
const root = path.resolve(__dirname, '..');
const clone = value => JSON.parse(JSON.stringify(value));
const id = 'a0be8743-44b4-418d-9a15-e3d901c4ba11';
const now = '2026-11-23T13:00:00.123456+09:00';
const scope = { actorUserId: '30', adminAccountId: '7' };
function record(type = 'report', rawStatus = 'received') {
  return { id: `${type === 'report' ? 'R' : 'I'}-1`, recordId: '1', recordType: type, number: `${type === 'report' ? 'R' : 'I'}-1`,
    kind: type === 'report' ? 'content' : 'service', title: null, reasonCode: null, reason: null, message: 'Original message', target: null,
    status: rawStatus === 'received' ? 'waiting' : rawStatus === 'reviewing' ? 'reviewing' : 'completed', rawStatus,
    receivedAt: now, updatedAt: now, content: null, answer: null, answeredAt: null, answerAvailability: type === 'inquiry' && rawStatus === 'received' ? 'none' : 'unavailable',
    actions: { allowedActions: type === 'report' ? (rawStatus === 'received' ? ['start_review', 'resolve', 'reject'] : rawStatus === 'reviewing' ? ['resolve', 'reject'] : []) : rawStatus === 'received' ? ['answer'] : [],
      disabledReason: ['received', 'reviewing'].includes(rawStatus) ? null : 'terminal_state', scope } };
}
const session = () => ({ authenticated: true, sessionState: 'active', user: { id: 30, accountType: 'user' }, onboardingCompleted: true, csrfToken: 'fixture-csrf' });
const result = (type = 'report', action = 'resolve', replayed = false) => ({ recordId: '1', recordType: type, action,
  rawStatus: { start_review: 'reviewing', resolve: 'resolved', reject: 'rejected', answer: 'answered' }[action], updatedAt: now,
  auditLogId: '9223372036854775807', replayed });
function runtime(respond = () => { throw new Error('Unexpected transport'); }, deadline = 15000) {
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
    vm.runInContext(`(function(module,exports,require){${code}\n})`, context)(mod, mod.exports, localRequire); return mod.exports;
  };
  return { write: load('lib/admin-support-write.ts'), flow: load('lib/admin-support-flow.ts'), reports: load('lib/admin-reports.ts'), api: load('lib/api.ts'), calls };
}

test('report writes preserve timestamp, exact body, CSRF, scope strings and committed response', async () => {
  const env = runtime((url, init) => url.pathname.endsWith('/session') ? session() : init.method === 'POST' ? result() : record());
  const request = env.write.prepareSupportRequest(record(), 'resolve', '  근거 확인 완료  ', id);
  const response = await env.write.submitSupportAction(record(), request, scope);
  assert.equal(response.auditLogId, '9223372036854775807');
  assert.equal(request.expectedUpdatedAt, now);
  assert.equal(request.rationale, '근거 확인 완료');
  const sent = env.calls.find(call => call.init.method === 'POST');
  assert.equal(new URL(sent.url).pathname, '/api/v1/admin/reports/1/actions');
  assert.deepEqual(JSON.parse(sent.init.body), clone(request));
  assert.equal(sent.init.headers.get('X-CSRF-Token'), 'fixture-csrf');
  assert.equal(sent.init.headers.get('X-Admin-Support-Actor'), '30');
  assert.equal(sent.init.headers.get('X-Admin-Support-Grant'), '7');
  assert.equal(sent.init.credentials, 'include'); assert.equal(sent.init.cache, 'no-store');
});

test('inquiry reply DTO omits synthetic action, preserves plain text and can replay after terminal state', async () => {
  const terminal = record('inquiry', 'answered');
  terminal.answer = '<b>사용자에게 보여줄 답변</b>'; terminal.answeredAt = now; terminal.answerAvailability = 'available';
  const env = runtime((url, init) => url.pathname.endsWith('/session') ? session() : init.method === 'POST' ? result('inquiry', 'answer', true) : terminal);
  const request = env.write.prepareSupportRequest(record('inquiry'), 'answer', terminal.answer, id);
  const response = await env.write.submitSupportAction(record('inquiry'), request, scope);
  const sent = env.calls.find(call => call.init.method === 'POST');
  assert.equal(new URL(sent.url).pathname, '/api/v1/admin/inquiries/1/answer');
  assert.deepEqual(Object.keys(JSON.parse(sent.init.body)).sort(), ['answer', 'expectedStatus', 'expectedUpdatedAt', 'requestId']);
  assert.equal(JSON.parse(sent.init.body).answer, terminal.answer); assert.equal(response.replayed, true);
});

test('viewer, terminal records, malformed timestamp/UUID and invalid input cannot prepare writes', () => {
  const env = runtime();
  const viewer = { ...record(), actions: { allowedActions: [], disabledReason: 'permission_denied', scope: null } };
  for (const current of [viewer, record('report', 'resolved'), { ...record(), updatedAt: '2026-11-23T13:00:00.123456' }]) {
    assert.throws(() => env.write.prepareSupportRequest(current, 'resolve', '사유', id));
  }
  for (const value of ['', ' ', 'a'.repeat(2001), 'bad\u0000text', '\ud800']) assert.throws(() => env.write.prepareSupportRequest(record(), 'resolve', value, id));
  assert.equal(env.write.supportTextError('😀'.repeat(5000), 'answer'), null);
  assert.ok(env.write.supportTextError('😀'.repeat(5001), 'answer'));
  assert.throws(() => env.write.prepareSupportRequest(record(), 'answer', '답변', id));
  assert.throws(() => env.write.prepareSupportRequest(record('inquiry'), 'answer', '답변', 'bad'));
});

test('fresh actor and revoked/replaced grant checks prevent POST including retries', async () => {
  for (const change of ['actor', 'grant', 'permission']) {
    const current = record();
    if (change === 'grant') current.actions.scope = { ...scope, adminAccountId: '8' };
    if (change === 'permission') current.actions = { allowedActions: [], disabledReason: 'permission_denied', scope: null };
    const env = runtime(url => url.pathname.endsWith('/session') ? { ...session(), user: { id: change === 'actor' ? 31 : 30, accountType: 'user' } } : current);
    await assert.rejects(env.write.submitSupportAction(record(), env.write.prepareSupportRequest(record(), 'resolve', '사유', id), scope), { code: 'SUPPORT_SCOPE_CHANGED' });
    assert.equal(env.calls.filter(call => call.init.method === 'POST').length, 0);
  }
});

test('detail rejects missing write capability, inconsistent answer persistence and wrong-type actions', async () => {
  const mutations = [value => delete value.actions, value => value.actions.allowedActions = ['answer'], value => value.answer = 'not persisted',
    value => value.answerAvailability = 'available', value => value.actions.scope.actorUserId = 30];
  for (const mutate of mutations) {
    const value = clone(record()); mutate(value);
    await assert.rejects(runtime(() => value).reports.loadAdminReport(record()), { code: 'INVALID_ADMIN_RESPONSE' });
  }
});

test('401/403/409 remain reloadable; 503/5xx, invalid success and network loss are unknown outcomes', () => {
  const env = runtime(), { ApiError } = env.api;
  for (const [status, code, expected] of [[401, 'AUTH_REQUIRED', 'login-required'], [403, 'ADMIN_WRITE_REQUIRED', 'forbidden'],
    [409, 'SUPPORT_STATE_CONFLICT', 'conflict'], [409, 'SUPPORT_SCOPE_CHANGED', 'conflict'], [409, 'SUPPORT_REQUEST_CONFLICT', 'conflict']]) {
    const failure = env.write.supportWriteFailure(new ApiError('test', status, code));
    assert.equal(failure.status, expected); assert.equal(failure.reload, true); assert.equal(failure.retrySameRequest, false);
  }
  for (const error of [new TypeError('lost'), new ApiError('test', 503, 'ADMIN_SUPPORT_WRITE_UNAVAILABLE'), new ApiError('test', 500, 'INTERNAL'),
    new ApiError('test', 200, 'INVALID_ADMIN_SUPPORT_RESPONSE'), new ApiError('test', 403, 'CSRF_INVALID')]) {
    assert.equal(env.write.supportWriteFailure(error).retrySameRequest, true);
  }
});

test('double submit, remount and uncertainty retain one frozen request and scope until replay succeeds', async () => {
  const env = runtime(); let release, uuidCalls = 0; const calls = [];
  const flow = env.flow.createSupportFlow(record(), { newRequestId: () => { uuidCalls++; return id; },
    submit: async (item, request, capturedScope) => { calls.push({ request, capturedScope }); if (calls.length === 1) return new Promise((_, reject) => { release = reject; }); return result('report', 'resolve', true); } });
  const first = flow.execute(record(), 'resolve', '사유');
  await flow.execute(record(), 'reject', '중복'); assert.equal(calls.length, 1); assert.equal(flow.state().phase, 'submitting');
  let notices = 0; const unsubscribe = flow.subscribe(() => notices++); unsubscribe();
  release(new TypeError('response lost')); await first;
  assert.equal(flow.state().phase, 'uncertain'); assert.equal(env.flow.pendingSupportFlows()[0], flow);
  flow.release(); await flow.execute(record(), 'reject', '새 내용');
  assert.equal(flow.state().phase, 'uncertain'); assert.equal(calls.length, 1);
  await flow.recheck();
  assert.equal(calls[0].request, calls[1].request); assert.equal(calls[0].capturedScope, calls[1].capturedScope);
  assert.equal(Object.isFrozen(calls[0].request), true); assert.equal(uuidCalls, 1); assert.equal(flow.state().phase, 'succeeded');
  assert.equal(env.flow.pendingSupportFlows().length, 0); assert.equal(notices, 0);
  // Successful state intentionally drops the answer/request body from its public snapshot.
  assert.equal('request' in flow.state(), false);
});

test('409 refreshes reads without resubmitting, and denial after an unknown result stays unknown', async () => {
  const env = runtime(); let calls = 0;
  const conflict = env.flow.createSupportFlow(record(), { newRequestId: () => id, submit: async () => { calls++; throw new env.api.ApiError('changed', 409, 'SUPPORT_STATE_CONFLICT'); } });
  const version = env.flow.supportCompletedVersion(); await conflict.execute(record(), 'resolve', '사유');
  assert.equal(conflict.state().phase, 'stopped'); assert.equal(env.flow.supportCompletedVersion(), version + 1); assert.equal(calls, 1);
  await conflict.recheck(); assert.equal(calls, 1);
  calls = 0;
  const unknown = env.flow.createSupportFlow(record('inquiry'), { newRequestId: () => id, submit: async () => {
    calls++; if (calls === 1) throw new TypeError('lost'); throw new env.api.ApiError('revoked', 403, 'ADMIN_WRITE_REQUIRED');
  } });
  await unknown.execute(record('inquiry'), 'answer', '답변'); await unknown.recheck();
  assert.equal(unknown.state().phase, 'uncertain'); assert.match(unknown.state().message, /처리 여부는 아직 확인되지 않았습니다/);
  unknown.release(); assert.equal(unknown.state().phase, 'uncertain');
  await unknown.recheck(); await unknown.recheck(); await unknown.recheck(); assert.equal(calls, 4);
});

test('malformed committed response is never displayed as success', async () => {
  for (const change of [{ recordId: '2' }, { auditLogId: 1 }, { rawStatus: 'rejected' }, { updatedAt: '2026-11-23T13:00:00' }]) {
    const env = runtime((url, init) => url.pathname.endsWith('/session') ? session() : init.method === 'POST' ? { ...result(), ...change } : record());
    await assert.rejects(env.write.submitSupportAction(record(), env.write.prepareSupportRequest(record(), 'resolve', '사유', id), scope), { code: 'INVALID_ADMIN_SUPPORT_RESPONSE' });
  }
});
