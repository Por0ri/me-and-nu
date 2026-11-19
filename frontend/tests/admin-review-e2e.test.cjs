const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const { createAdminReviewFixture } = require('./helpers/admin-review-api-fixture.cjs');
const root = path.join(__dirname, '..');

// Isolated rehearsal of the 11/19 E2E: the real FE client (lib/api, admin-content,
// admin-content-review) over real HTTP against a loopback fake API. No UI is enabled and
// no real BE, DB or account is touched.
function browser(fixture, actor) {
  const modules = new Map(), posts = [];
  const window = new EventTarget(); let unauthorized = 0;
  window.addEventListener('api:unauthorized', () => unauthorized++);
  const context = vm.createContext({ AbortController, URL, URLSearchParams, Headers, Response, Event, Intl, window,
    process: { env: { NEXT_PUBLIC_API_BASE_URL: fixture.origin } },
    fetch: async (url, init = {}) => {
      // Stand in for the browser cookie jar; credentials/cache semantics stay with the client.
      const headers = new Headers(init.headers); const cookie = fixture.cookie(actor);
      if (cookie) headers.set('cookie', cookie);
      const { credentials, cache, ...rest } = init;
      assert.equal(credentials, 'include'); assert.equal(cache, 'no-store');
      if ((init.method ?? 'GET') === 'POST') posts.push({ url, body: init.body, csrf: headers.get('x-csrf-token') });
      return fetch(url, { ...rest, headers });
    },
  });
  function load(file) {
    file = file.replaceAll('\\', '/');
    if (modules.has(file)) return modules.get(file).exports;
    const mod = { exports: {} }; modules.set(file, mod);
    const js = ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), { compilerOptions: {
      module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true } }).outputText;
    const requireModule = name => {
      const relative = name.startsWith('@/') ? name.slice(2) : path.join(path.dirname(file), name);
      return load(`${relative}.ts`);
    };
    vm.runInContext(`(function(module,exports,require){${js}\n})`, context)(mod, mod.exports, requireModule);
    return mod.exports;
  }
  const api = load('lib/api.ts'), content = load('lib/admin-content.ts'), review = load('lib/admin-content-review.ts');
  return { api, content, review, posts, load, unauthorized: () => unauthorized,
    list: (search = 'mode=review') => content.loadAdminContents(content.parseAdminQuery(new URLSearchParams(search))),
    detail: id => content.loadAdminContent(id),
    request: (detail, action, rationale = ' 출처 확인 완료 ') => review.prepareReviewRequest(detail, action, rationale, crypto.randomUUID()),
    submit: (id, request) => review.submitAdminContentReview(id, request) };
}

async function withFixture(run) {
  const fixture = createAdminReviewFixture();
  await fixture.start();
  try { await run(fixture); } finally { await fixture.stop(); }
}
async function rejection(promise, session) {
  try { await promise; } catch (error) { return { error, failure: session.review.reviewFailure(error), read: session.content.adminFailure(error) }; }
  assert.fail('expected a rejected request');
}
const unchanged = (fixture, id, before) => assert.deepEqual(fixture.contents.get(id), before);

test('viewer reads list and detail but every approve or reject is refused without a write', async () => withFixture(async fixture => {
  const viewer = browser(fixture, 'viewer');
  const list = await viewer.list();
  assert.deepEqual(list.items.map(row => row.contentId), [2, 3]);
  const detail = await viewer.detail(2);
  assert.equal(detail.updatedAt, '2026-11-19T09:12:34.123456+09:00');
  const before = structuredClone(fixture.contents.get(2));
  for (const action of ['approve', 'reject']) {
    const { error, failure } = await rejection(viewer.submit(2, viewer.request(detail, action)), viewer);
    assert.equal(error.code, 'ADMIN_WRITE_REQUIRED');
    assert.equal(failure.status, 'forbidden'); assert.equal(failure.retrySameRequest, false); assert.equal(failure.reload, true);
    assert.doesNotMatch(failure.message, /internal detail/);
  }
  unchanged(fixture, 2, before); assert.equal(fixture.audit.length, 0);
}));

test('operator approves with an audited result, replays the identical request and cannot reuse its ID', async () => withFixture(async fixture => {
  const operator = browser(fixture, 'operator');
  const detail = await operator.detail(2);
  const request = operator.request(detail, 'approve');
  const result = await operator.submit(2, request);
  assert.deepEqual({ ...result, publishedAt: undefined, updatedAt: undefined }, { contentId: 2, draftId: 1002, action: 'approve',
    status: 'active', judgmentStatus: 'confirmed', draftStatus: 'published', publishedAt: undefined, updatedAt: undefined, auditLogId: 1, replayed: false });
  assert.equal(result.publishedAt, result.updatedAt);
  // The single audit row carries the actor's grant, trimmed rationale and the exact request.
  assert.equal(fixture.audit.length, 1);
  assert.deepEqual({ ...fixture.audit[0], fingerprint: undefined, response: undefined }, { auditLogId: 1, adminAccountId: 122, actorUserId: 22,
    contentId: 2, actionType: 'content.approve', requestId: request.requestId, fingerprint: undefined, rationale: '출처 확인 완료', response: undefined });
  const sent = JSON.parse(operator.posts[0].body);
  assert.deepEqual(Object.keys(sent), ['requestId', 'action', 'rationale', 'expectedStatus', 'expectedJudgmentStatus', 'expectedUpdatedAt']);
  assert.equal(sent.expectedUpdatedAt, detail.updatedAt); assert.equal(operator.posts[0].csrf, 'csrf-operator-1');

  const replay = await operator.submit(2, request);
  assert.deepEqual(replay, { ...result, replayed: true });
  assert.equal(fixture.audit.length, 1); assert.equal(operator.posts[1].body, operator.posts[0].body);

  const reread = await operator.detail(2);
  assert.equal(reread.updatedAt, result.updatedAt); assert.equal(reread.status, 'active'); assert.equal(reread.judgmentStatus, 'confirmed');
  assert.throws(() => operator.request(reread, 'reject'));
  const changed = await rejection(operator.submit(2, { ...request, rationale: '다른 사유' }), operator);
  assert.equal(changed.error.code, 'REVIEW_REQUEST_CONFLICT');
  assert.equal(changed.failure.status, 'conflict'); assert.equal(changed.failure.reload, true); assert.equal(changed.failure.retrySameRequest, false);
  assert.equal(fixture.audit.length, 1);
}));

test('admin rejects, and a stale version held by another session is a state conflict', async () => withFixture(async fixture => {
  const admin = browser(fixture, 'admin'), operator = browser(fixture, 'operator');
  const stale = await operator.detail(3);
  const result = await admin.submit(3, admin.request(await admin.detail(3), 'reject'));
  assert.equal(result.status, 'hidden'); assert.equal(result.judgmentStatus, 'failed'); assert.equal(result.draftStatus, 'discarded');
  assert.equal(result.publishedAt, null); assert.equal(fixture.audit[0].adminAccountId, 123);
  const conflict = await rejection(operator.submit(3, operator.request(stale, 'approve')), operator);
  assert.equal(conflict.error.code, 'REVIEW_STATE_CONFLICT');
  assert.equal(conflict.failure.status, 'conflict'); assert.equal(conflict.failure.reload, true); assert.equal(conflict.failure.retrySameRequest, false);
  assert.equal(fixture.audit.length, 1); assert.equal(fixture.contents.get(3).status, 'hidden');
}));

test('a revoked grant refuses the same session at once, including replays; a downgrade keeps reads only', async () => withFixture(async fixture => {
  const operator = browser(fixture, 'operator');
  const request = operator.request(await operator.detail(2), 'approve');
  await operator.submit(2, request);
  const pending = await operator.detail(3);
  fixture.revoke('operator');
  // Start each read only when awaited, so no rejection is ever left unhandled meanwhile.
  for (const read of [() => operator.list(), () => operator.detail(3)]) {
    const { error, read: state } = await rejection(read(), operator);
    assert.equal(error.code, 'ADMIN_REQUIRED'); assert.equal(state.status, 'forbidden');
  }
  for (const [id, attempt] of [[2, request], [3, operator.request(pending, 'reject')]]) {
    const { error, failure } = await rejection(operator.submit(id, attempt), operator);
    assert.equal(error.code, 'ADMIN_REQUIRED'); assert.equal(failure.status, 'forbidden'); assert.equal(failure.reload, true);
  }
  assert.equal(fixture.audit.length, 1); assert.equal(fixture.contents.get(3).status, 'active');

  const admin = browser(fixture, 'admin');
  const detail = await admin.detail(3);
  fixture.setRole('admin', 'viewer');
  assert.equal((await admin.detail(3)).status, 'active');
  const { error, failure } = await rejection(admin.submit(3, admin.request(detail, 'approve')), admin);
  assert.equal(error.code, 'ADMIN_WRITE_REQUIRED'); assert.equal(failure.status, 'forbidden');
  assert.equal(fixture.audit.length, 1);
}));

test('anonymous, expired and non-admin sessions get login-required or forbidden for reads and writes', async () => withFixture(async fixture => {
  const detail = await browser(fixture, 'viewer').detail(2);
  const anonymous = browser(fixture, null);
  assert.equal((await rejection(anonymous.list(), anonymous)).read.status, 'login-required');
  const write = await rejection(anonymous.submit(2, anonymous.request(detail, 'approve')), anonymous);
  assert.equal(write.error.code, 'AUTH_REQUIRED'); assert.equal(write.failure.status, 'login-required'); assert.equal(write.failure.reload, true);
  assert.equal(anonymous.unauthorized(), 2);

  const operator = browser(fixture, 'operator');
  await operator.detail(2); fixture.expire('operator');
  assert.equal((await rejection(operator.detail(2), operator)).read.status, 'login-required');

  const member = browser(fixture, 'member');
  const refused = await rejection(member.list(), member);
  assert.equal(refused.error.code, 'ADMIN_REQUIRED'); assert.equal(refused.read.status, 'forbidden');
  assert.equal(fixture.audit.length, 0);
}));

test('503 and lost responses keep the identical request until the server commits once or replays', async () => {
  for (const fault of ['permission-unavailable', 'unavailable-before-commit', 'unavailable-after-commit', 'drop-after-commit', 'bad-audit-response']) {
    await withFixture(async fixture => {
      const operator = browser(fixture, 'operator');
      const request = operator.request(await operator.detail(2), 'approve');
      fixture.failNext(fault);
      const first = await rejection(operator.submit(2, request), operator);
      assert.deepEqual({ ...first.failure }, { status: 'retry', retrySameRequest: true, reload: false,
        message: '처리 결과를 확인하지 못했습니다. 같은 요청으로 다시 확인해 주세요.' }, fault);
      const committed = fixture.committingFaults.has(fault);
      assert.equal(fixture.audit.length, committed ? 1 : 0, fault);
      const second = await operator.submit(2, request);
      assert.equal(second.replayed, committed, fault); assert.equal(second.auditLogId, 1, fault);
      assert.equal(fixture.audit.length, 1, fault);
      assert.equal(operator.posts.length, 2); assert.equal(operator.posts[1].body, operator.posts[0].body);
    });
  }
});

test('a rotated CSRF token is refused, refreshed on the next attempt and the identical request commits once', async () => withFixture(async fixture => {
  const operator = browser(fixture, 'operator');
  await operator.api.getSession();
  const request = operator.request(await operator.detail(2), 'approve');
  fixture.rotateCsrf('operator');
  const refused = await rejection(operator.submit(2, request), operator);
  assert.equal(refused.error.code, 'CSRF_INVALID'); assert.equal(refused.failure.retrySameRequest, true);
  assert.equal(fixture.audit.length, 0);
  const result = await operator.submit(2, request);
  assert.equal(result.replayed, false); assert.equal(fixture.audit.length, 1);
  assert.deepEqual(operator.posts.map(post => post.csrf), ['csrf-operator-1', 'csrf-operator-2']);
  assert.equal(operator.posts[1].body, operator.posts[0].body);
}));

// --- POST UI flow (lib/admin-review-flow.ts) over the same loopback fake API -------------
// The flow is the only UI path to the review POST. These tests enable it with a test-only
// activation bound to the fixture origin; production uses the code-only gate (off).
const tick = () => new Promise(resolve => setImmediate(resolve));
function reviewFlow(session, fixture, contentId, { enabled = true } = {}) {
  const { createReviewFlow } = session.load('lib/admin-review-flow.ts');
  const { reviewWriteGate } = session.load('lib/admin-review-write-gate.ts');
  const activation = { apiOrigin: fixture.origin, approvedBy: 'isolated test', approvedOn: '2026-11-19', reference: 'loopback fixture only' };
  const flow = createReviewFlow(contentId, { submit: session.review.submitAdminContentReview, newRequestId: () => crypto.randomUUID(),
    gate: () => reviewWriteGate(fixture.origin, enabled ? activation : null) });
  const phases = [], reloads = [];
  flow.subscribe(() => phases.push(flow.state().phase));
  flow.setReload(() => reloads.push(flow.state().phase));
  return { flow, phases, reloads };
}
const postsTo = fixture => fixture.requests.filter(request => request.method === 'POST');
const plain = value => JSON.parse(JSON.stringify(value)); // Compare vm-realm objects by value.

test('fake API detail carries review metadata by role and state, like the BE eligibility service', async () => withFixture(async fixture => {
  assert.deepEqual(plain((await browser(fixture, 'viewer').detail(2)).review), { allowedActions: [], disabledReason: 'permission_denied' });
  const operator = browser(fixture, 'operator');
  assert.deepEqual(plain((await operator.detail(2)).review), { allowedActions: ['approve', 'reject'], disabledReason: null });
  assert.deepEqual(plain((await operator.detail(4)).review), { allowedActions: [], disabledReason: 'content_not_reviewable' });
  fixture.contents.get(3).draftStatus = 'writing';
  assert.deepEqual(plain((await browser(fixture, 'admin').detail(3)).review), { allowedActions: [], disabledReason: 'draft_not_reviewable' });
}));

test('flow: operator approval shows success only after HTTP 200, with audit and result, then re-reads the detail', async () => withFixture(async fixture => {
  const operator = browser(fixture, 'operator');
  const { flow, phases, reloads } = reviewFlow(operator, fixture, 2);
  const detail = await operator.detail(2);
  const running = flow.execute(detail, 'approve', '  출처와 본문 확인  ');
  const submitting = flow.state();
  assert.equal(submitting.phase, 'submitting'); assert.equal(reloads.length, 0);
  assert.ok(Object.isFrozen(submitting.request)); assert.equal(submitting.request.expectedUpdatedAt, detail.updatedAt);
  await running;
  const done = flow.state();
  assert.deepEqual(phases, ['submitting', 'succeeded']);
  assert.equal(done.request, submitting.request);
  assert.deepEqual({ auditLogId: done.result.auditLogId, replayed: done.result.replayed, status: done.result.status,
    judgmentStatus: done.result.judgmentStatus, draftStatus: done.result.draftStatus }, { auditLogId: 1, replayed: false,
    status: 'active', judgmentStatus: 'confirmed', draftStatus: 'published' });
  assert.deepEqual(reloads, ['succeeded']);
  const sent = JSON.parse(postsTo(fixture)[0].body);
  assert.deepEqual(sent, { ...submitting.request }); assert.equal(sent.rationale, '출처와 본문 확인');
  const reread = await operator.detail(2);
  assert.equal(reread.updatedAt, done.result.updatedAt);
  assert.deepEqual(plain(reread.review), { allowedActions: [], disabledReason: 'content_not_reviewable' });
  await flow.execute(reread, 'reject', '다시 반려');
  assert.equal(flow.state().phase, 'blocked'); assert.equal(postsTo(fixture).length, 1);
}));

test('flow: a double click sends one request, and no new action starts while one is unresolved', async () => withFixture(async fixture => {
  const operator = browser(fixture, 'operator');
  const { flow } = reviewFlow(operator, fixture, 2);
  const detail = await operator.detail(2);
  await Promise.all([flow.execute(detail, 'approve', '사유'), flow.execute(detail, 'approve', '사유'), flow.execute(detail, 'reject', '사유')]);
  assert.equal(postsTo(fixture).length, 1); assert.equal(fixture.audit.length, 1); assert.equal(flow.state().phase, 'succeeded');

  const second = reviewFlow(operator, fixture, 3);
  fixture.failNext('unavailable-before-commit');
  await second.flow.execute(await operator.detail(3), 'reject', '사유');
  assert.equal(second.flow.state().phase, 'uncertain');
  await second.flow.execute(await operator.detail(3), 'approve', '다른 조치');
  assert.equal(second.flow.state().phase, 'uncertain'); assert.equal(postsTo(fixture).length, 2);
}));

test('flow: unknown outcomes never auto-resend; manual rechecks reuse the identical request and are limited', async () => {
  for (const fault of ['permission-unavailable', 'unavailable-before-commit', 'unavailable-after-commit', 'drop-after-commit', 'bad-audit-response']) {
    await withFixture(async fixture => {
      const operator = browser(fixture, 'operator');
      const { flow, reloads } = reviewFlow(operator, fixture, 2);
      fixture.failNext(fault);
      await flow.execute(await operator.detail(2), 'approve', '출처 확인');
      const uncertain = flow.state();
      assert.equal(uncertain.phase, 'uncertain', fault); assert.equal(uncertain.rechecks, 0);
      assert.equal(uncertain.message, '처리 결과를 확인하지 못했습니다. 같은 요청으로 다시 확인해 주세요.');
      await tick(); await tick();
      assert.equal(postsTo(fixture).length, 1, `${fault}: no automatic resend`); assert.equal(reloads.length, 0);
      await flow.recheck();
      const done = flow.state();
      assert.equal(done.phase, 'succeeded', fault); assert.equal(done.request, uncertain.request);
      assert.equal(done.result.replayed, fixture.committingFaults.has(fault), fault); assert.equal(fixture.audit.length, 1);
      const [first, again] = postsTo(fixture);
      assert.equal(again.body, first.body); assert.equal(JSON.parse(again.body).requestId, uncertain.request.requestId);
    });
  }
  await withFixture(async fixture => {
    const operator = browser(fixture, 'operator');
    const { flow, reloads } = reviewFlow(operator, fixture, 2);
    for (let index = 0; index < 5; index++) fixture.failNext('unavailable-before-commit');
    await flow.execute(await operator.detail(2), 'approve', '출처 확인');
    for (let attempt = 0; attempt < 5; attempt++) await flow.recheck();
    assert.equal(flow.state().phase, 'uncertain'); assert.equal(flow.state().rechecks, 3);
    assert.equal(postsTo(fixture).length, 4, 'one request plus three manual rechecks');
    assert.ok(postsTo(fixture).every(post => post.body === postsTo(fixture)[0].body));
    flow.release();
    assert.equal(flow.state().phase, 'idle'); assert.deepEqual(reloads, ['idle']); assert.equal(fixture.audit.length, 0);
  });
});

test('flow: 409 stops the request, re-reads state and permission, and never re-runs it', async () => withFixture(async fixture => {
  const admin = browser(fixture, 'admin'), operator = browser(fixture, 'operator');
  const stale = await operator.detail(3);
  await admin.submit(3, admin.request(await admin.detail(3), 'reject'));
  const { flow, reloads } = reviewFlow(operator, fixture, 3);
  await flow.execute(stale, 'approve', '출처 확인');
  const stopped = flow.state();
  assert.equal(stopped.phase, 'stopped'); assert.equal(stopped.failure.status, 'conflict');
  assert.deepEqual(reloads, ['stopped']);
  await tick(); await flow.recheck();
  assert.equal(postsTo(fixture).length, 2, 'admin reject + one operator attempt, no re-run');
  const fresh = await operator.detail(3);
  assert.deepEqual(plain(fresh.review), { allowedActions: [], disabledReason: 'content_not_reviewable' });
  await flow.execute(fresh, 'approve', '출처 확인');
  assert.equal(flow.state().phase, 'blocked'); assert.equal(postsTo(fixture).length, 2);
}));

test('flow: viewer sends nothing; revoked or downgraded grants end in forbidden with a re-read, never success', async () => withFixture(async fixture => {
  const viewer = browser(fixture, 'viewer');
  const viewerFlow = reviewFlow(viewer, fixture, 2);
  await viewerFlow.flow.execute(await viewer.detail(2), 'approve', '사유');
  assert.equal(viewerFlow.flow.state().phase, 'blocked'); assert.match(viewerFlow.flow.state().message, /조회 전용/);
  assert.equal(postsTo(fixture).length, 0);

  const operator = browser(fixture, 'operator');
  const revoked = reviewFlow(operator, fixture, 2);
  const detail = await operator.detail(2);
  fixture.revoke('operator');
  await revoked.flow.execute(detail, 'approve', '사유');
  assert.equal(revoked.flow.state().phase, 'stopped'); assert.equal(revoked.flow.state().failure.status, 'forbidden');
  assert.deepEqual(revoked.reloads, ['stopped']);

  const admin = browser(fixture, 'admin');
  const pending = reviewFlow(admin, fixture, 3);
  fixture.failNext('unavailable-after-commit');
  await pending.flow.execute(await admin.detail(3), 'reject', '사유');
  assert.equal(pending.flow.state().phase, 'uncertain');
  fixture.setRole('admin', 'viewer');
  await pending.flow.recheck();
  assert.equal(pending.flow.state().phase, 'stopped'); assert.equal(pending.flow.state().failure.status, 'forbidden');
  assert.equal(fixture.audit.length, 1, 'the earlier commit stays; the UI just cannot confirm it');
}));

test('flow: 401 asks for login with a re-read; an expired CSRF token is rechecked with the identical request', async () => withFixture(async fixture => {
  const operator = browser(fixture, 'operator');
  const expired = reviewFlow(operator, fixture, 2);
  const detail = await operator.detail(2);
  fixture.expire('operator');
  await expired.flow.execute(detail, 'approve', '사유');
  assert.equal(expired.flow.state().failure.status, 'login-required'); assert.deepEqual(expired.reloads, ['stopped']);

  const admin = browser(fixture, 'admin');
  await admin.api.getSession();
  const { flow } = reviewFlow(admin, fixture, 3);
  fixture.rotateCsrf('admin');
  await flow.execute(await admin.detail(3), 'reject', '사유');
  assert.equal(flow.state().phase, 'uncertain'); assert.match(flow.state().message, /보안 확인이 만료/);
  await flow.recheck();
  assert.equal(flow.state().phase, 'succeeded'); assert.equal(flow.state().result.replayed, false);
  assert.deepEqual(admin.posts.map(post => post.csrf), ['csrf-admin-1', 'csrf-admin-2']);
  assert.equal(admin.posts[1].body, admin.posts[0].body);
}));

test('flow: a request outlives an unmounted panel or closed modal; the result and pending re-read land on reattach', async () => withFixture(async fixture => {
  const operator = browser(fixture, 'operator');
  const { flow, reloads } = reviewFlow(operator, fixture, 2);
  const detail = await operator.detail(2);
  const running = flow.execute(detail, 'approve', '사유');
  flow.setReload(null); // The panel unmounted (detail re-read, navigation inside the app).
  await running;
  assert.equal(flow.state().phase, 'succeeded'); assert.equal(reloads.length, 0);
  const later = [];
  flow.setReload(() => later.push('reload'));
  assert.deepEqual(later, ['reload']);
  flow.setReload(() => later.push('again'));
  assert.deepEqual(later, ['reload']);
  const { reviewFlowFor } = operator.load('lib/admin-review-flow.ts');
  assert.equal(reviewFlowFor(2), reviewFlowFor(2), 'remounts reattach to the same registry flow');
}));

test('production write gate is off in code: no env, query or storage switch, and the registry flow sends nothing', async () => withFixture(async fixture => {
  const operator = browser(fixture, 'operator');
  const { reviewWriteGate } = operator.load('lib/admin-review-write-gate.ts');
  for (const base of [fixture.origin, 'http://localhost:8000', 'https://api.checking.me-nu.kr', 'https://api.me-nu.kr']) {
    assert.equal(reviewWriteGate(base).enabled, false, base);
  }
  const activation = { apiOrigin: 'https://api.checking.me-nu.kr', approvedBy: 'x', approvedOn: '2026-11-19', reference: 'x' };
  assert.equal(reviewWriteGate('https://api.checking.me-nu.kr/', activation).enabled, true);
  assert.equal(reviewWriteGate('https://api.me-nu.kr', activation).enabled, false, 'an activation is bound to one API origin');
  assert.equal(reviewWriteGate('not a url', activation).enabled, false);
  const source = fs.readFileSync(path.join(root, 'lib/admin-review-write-gate.ts'), 'utf8');
  assert.match(source, /const ACTIVATION: ReviewWriteActivation \| null = null;/);
  assert.doesNotMatch(source.replace(/^\s*\/\/.*$/gm, ''), /process\.env|NEXT_PUBLIC|localStorage|sessionStorage|location|searchParams|cookie/);

  const { reviewFlowFor } = operator.load('lib/admin-review-flow.ts');
  const flow = reviewFlowFor(2);
  assert.equal(flow.writeGate().enabled, false);
  await flow.execute(await operator.detail(2), 'approve', '출처 확인');
  assert.equal(flow.state().phase, 'blocked'); assert.match(flow.state().message, /활성화되지 않았습니다/);
  await flow.recheck();
  assert.equal(postsTo(fixture).length, 0); assert.equal(fixture.audit.length, 0);
}));
