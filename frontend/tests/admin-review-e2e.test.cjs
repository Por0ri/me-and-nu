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
  return { api, content, review, posts, unauthorized: () => unauthorized,
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
  for (const read of [operator.list(), operator.detail(3)]) {
    const { error, read: state } = await rejection(read, operator);
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
