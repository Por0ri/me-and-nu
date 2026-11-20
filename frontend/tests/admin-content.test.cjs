const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const root = path.join(__dirname, '..');
const plain = value => JSON.parse(JSON.stringify(value));
const item = (id = 2) => ({ contentId: id, title: '달빛 콘텐츠', judgmentStatus: 'needs_review', status: 'active',
  createdAt: '2026-11-12T00:00:00Z', publishedAt: null });
const reviewScope = { actorUserId: 7, adminAccountId: 107 };
const allowedReview = () => ({ allowedActions: ['approve', 'reject'], disabledReason: null, scope: reviewScope });
const detailOf = () => ({ ...item(), updatedAt: '2026-11-13T09:12:34.123456+09:00', summary: null, body: null, sources: [], review: allowedReview() });
const page = (items = [item()]) => ({ items, page: 1, pageSize: 20, total: items.length });
function runtime(respond = () => page()) {
  const modules = new Map(), calls = [];
  const browser = new EventTarget();
  const context = vm.createContext({ AbortController, URL, URLSearchParams, Headers, Response, Event, window: browser,
    Intl, process: { env: { NEXT_PUBLIC_API_BASE_URL: 'http://localhost:8196' } },
    fetch: async (url, init) => {
      calls.push({ url, init }); const value = await respond(new URL(url), init);
      return value instanceof Response ? value : new Response(JSON.stringify(value));
    },
  });
  function load(file) {
    file = file.replaceAll('\\', '/');
    if (modules.has(file)) return modules.get(file).exports;
    const mod = { exports: {} }; modules.set(file, mod);
    const js = ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), { compilerOptions: {
      module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
    } }).outputText;
    const requireModule = name => {
      if (name === 'react' || name === 'react/jsx-runtime') return require(name);
      if (name === 'next/link') return { __esModule: true, default: props => React.createElement('a', props) };
      if (name === 'next/navigation') return {};
      if (name.endsWith('.module.css')) return { __esModule: true, default: new Proxy({}, { get: (_, key) => String(key) }) };
      const relative = name.startsWith('@/') ? name.slice(2) : path.join(path.dirname(file), name);
      return load(relative + (fs.existsSync(path.join(root, relative + '.tsx')) ? '.tsx' : '.ts'));
    };
    vm.runInContext(`(function(module,exports,require){${js}\n})`, context)(mod, mod.exports, requireModule);
    return mod.exports;
  }
  return { load, calls, browser, api: load('lib/admin-content.ts') };
}

test('review preparation eligibility stays separate from server write permission', () => {
  const { canPrepareReview } = runtime().load('lib/admin-content-review.ts');
  for (const status of ['active', 'pending_review', 'hidden', 'deleted']) {
    for (const judgmentStatus of ['needs_review', 'pending', 'confirmed', 'failed']) {
      assert.equal(canPrepareReview({ status, judgmentStatus }), judgmentStatus === 'needs_review' && ['active', 'pending_review'].includes(status));
    }
  }
});

test('review rationale accepts Unicode and rejects blank, oversized and control input', () => {
  const { reviewRationaleError } = runtime().load('lib/admin-content-review.ts');
  for (const value of ['  ', 'x'.repeat(2001), 'a\x00b', 'a\ud800b']) assert.ok(reviewRationaleError(value));
  for (const value of ['출처 확인\n완료', '😀'.repeat(2000), '  검토 완료  ']) assert.equal(reviewRationaleError(value), null);
});

test('review confirmation exposes an honest disconnected state and disabled execution', () => {
  const { ContentReviewPanel } = runtime().load('components/admin/content-review-panel.tsx');
  const html = renderToStaticMarkup(React.createElement(ContentReviewPanel, { content: detailOf() }));
  assert.match(html, /승인 내용 확인/); assert.match(html, /반려 내용 확인/);
  assert.match(html, /<dialog[^>]+aria-labelledby="review-title"/);
  assert.match(html, /disabled=""[^>]*>승인 실행 \(미연결\)/);
  assert.match(html, /확인 단계 · 미저장/); assert.doesNotMatch(html, /승인 완료|저장 성공/);
});

const reasons = ['permission_denied', 'deleted', 'content_not_reviewable', 'draft_not_reviewable', 'unavailable'];
const panelOf = (env, content) => renderToStaticMarkup(React.createElement(env.load('components/admin/content-review-panel.tsx').ContentReviewPanel, { content }));
const buttonState = (html, label) => {
  const tag = html.match(new RegExp(`<button[^>]*>${label}</button>`))[0];
  return /disabled=""/.test(tag) ? 'disabled' : 'enabled';
};

test('detail review metadata accepts only the BE-defined actions and reasons and is kept exactly', async () => {
  const valid = [allowedReview(), { allowedActions: ['reject', 'approve'], disabledReason: null }, { allowedActions: ['approve'], disabledReason: null },
    { allowedActions: ['reject'], disabledReason: null }, ...reasons.map(disabledReason => ({ allowedActions: [], disabledReason }))];
  for (const value of valid) {
    const review = { ...value, scope: value.scope ?? reviewScope };
    const detail = { ...detailOf(), review }, env = runtime(() => detail);
    const parsed = await env.api.loadAdminContent(2);
    assert.deepEqual(plain(parsed), detail); assert.deepEqual(plain(parsed.review), review);
    assert.equal(env.calls.length, 1); assert.equal(env.calls[0].init.method ?? 'GET', 'GET');
  }
});

test('missing or malformed review metadata keeps the detail readable but disables review', async () => {
  const malformed = [undefined, null, [], 'approve', {}, { disabledReason: null }, { allowedActions: [] },
    { allowedActions: null, disabledReason: null }, { allowedActions: 'approve', disabledReason: null },
    { allowedActions: ['delete'], disabledReason: null }, { allowedActions: ['APPROVE'], disabledReason: null },
    { allowedActions: ['approve', 'approve'], disabledReason: null }, { allowedActions: [null], disabledReason: null },
    { allowedActions: [], disabledReason: null }, { allowedActions: ['approve'], disabledReason: 'deleted' },
    { allowedActions: ['approve', 'reject'], disabledReason: 'permission_denied' }, { allowedActions: [], disabledReason: 'expired' },
    { allowedActions: [], disabledReason: 1 }, { allowedActions: [], disabledReason: '' }];
  for (const review of malformed) {
    const detail = { ...detailOf(), review }, env = runtime(() => detail);
    const parsed = await env.api.loadAdminContent(2);
    assert.equal(parsed.review, null, JSON.stringify(review));
    assert.equal(parsed.updatedAt, detail.updatedAt); assert.equal(parsed.contentId, 2); assert.equal(parsed.status, 'active');
    const { reviewAvailability } = env.load('lib/admin-content-review.ts');
    assert.deepEqual(plain(reviewAvailability(parsed)), { allowedActions: [],
      message: '검수 가능 여부를 확인하지 못했습니다. 콘텐츠를 다시 조회해 주세요.' });
    const html = panelOf(env, parsed);
    assert.equal(buttonState(html, '승인 내용 확인'), 'disabled'); assert.equal(buttonState(html, '반려 내용 확인'), 'disabled');
  }
  // The metadata never makes an otherwise invalid detail acceptable.
  const env = runtime(() => ({ ...detailOf(), updatedAt: '2026-11-13T00:12:34' }));
  await assert.rejects(env.api.loadAdminContent(2), error => error.code === 'INVALID_ADMIN_RESPONSE');
});

test('missing or malformed actor/grant scope fails closed without hiding readable content', async () => {
  for (const scope of [undefined, null, {}, [], '22', { actorUserId: 7 }, { actorUserId: '7', adminAccountId: 107 },
    { actorUserId: 7, adminAccountId: 0 }, { actorUserId: true, adminAccountId: 107 },
    { actorUserId: 7.5, adminAccountId: 107 }, { actorUserId: 7, adminAccountId: Number.MAX_SAFE_INTEGER + 1 }]) {
    const env = runtime(() => ({ ...detailOf(), review: { ...allowedReview(), scope } }));
    const detail = await env.api.loadAdminContent(2);
    assert.equal(detail.review, null); assert.equal(detail.contentId, 2);
    assert.deepEqual(plain(env.load('lib/admin-content-review.ts').reviewAvailability(detail).allowedActions), []);
  }
  const env = runtime(() => ({ ...detailOf(), review: { allowedActions: [], disabledReason: 'permission_denied', scope: null } }));
  assert.equal((await env.api.loadAdminContent(2)).review.scope, null);
});

test('viewer cannot open review; operator and admin open only what the BE allows; execution stays disconnected', () => {
  const env = runtime(); const { reviewAvailability } = env.load('lib/admin-content-review.ts');
  const cases = [
    ['viewer', { allowedActions: [], disabledReason: 'permission_denied' }, 'active', 'disabled', 'disabled', /조회 전용/],
    ['operator', allowedReview(), 'active', 'enabled', 'enabled', null],
    ['admin', allowedReview(), 'pending_review', 'enabled', 'enabled', null],
    ['operator, approve only', { allowedActions: ['approve'], disabledReason: null }, 'active', 'enabled', 'disabled', null],
    ['admin, reject only', { allowedActions: ['reject'], disabledReason: null }, 'active', 'disabled', 'enabled', null],
    ['deleted', { allowedActions: [], disabledReason: 'deleted' }, 'active', 'disabled', 'disabled', /삭제된 콘텐츠/],
    ['content state', { allowedActions: [], disabledReason: 'content_not_reviewable' }, 'active', 'disabled', 'disabled', /현재 콘텐츠 상태/],
    ['draft state', { allowedActions: [], disabledReason: 'draft_not_reviewable' }, 'active', 'disabled', 'disabled', /초안/],
    ['unjudged', { allowedActions: [], disabledReason: 'unavailable' }, 'active', 'disabled', 'disabled', /확인할 수 없습니다/],
    // Server allowed, but the shown stored state is not reviewable: re-read instead of trusting either side.
    ['stale state', allowedReview(), 'hidden', 'disabled', 'disabled', /다시 조회/],
  ];
  for (const [name, review, status, approve, reject, message] of cases) {
    review.scope = review.scope ?? reviewScope;
    const content = { ...detailOf(), status, review };
    const availability = reviewAvailability(content);
    assert.deepEqual(plain(availability.allowedActions), [approve === 'enabled' && 'approve', reject === 'enabled' && 'reject'].filter(Boolean), name);
    const html = panelOf(env, content);
    assert.equal(buttonState(html, '승인 내용 확인'), approve, name); assert.equal(buttonState(html, '반려 내용 확인'), reject, name);
    assert.match(html, /disabled=""[^>]*>승인 실행 \(미연결\)/, name);
    if (message) { assert.match(availability.message, message, name); assert.ok(html.includes(availability.message), name); }
    else assert.equal(availability.message, null, name);
  }
});

const stubFlow = (state, enabled = true) => ({ contentId: 2, state: () => state, subscribe: () => () => {},
  writeGate: () => ({ enabled, reason: enabled ? null : 'off' }), setReload() {}, execute: async () => {}, recheck: async () => {}, release() {} });
const flowRequest = Object.freeze({ requestId: '0b8c7d6e-5f4a-4b3c-8d2e-1f0a9b8c7d6e', action: 'approve', rationale: '출처 확인',
  expectedStatus: 'active', expectedJudgmentStatus: 'needs_review', expectedUpdatedAt: '2026-11-13T09:12:34.123456+09:00' });
const flowResult = (patch = {}) => ({ contentId: 2, draftId: 3, action: 'approve', status: 'active', judgmentStatus: 'confirmed', draftStatus: 'published',
  publishedAt: '2026-11-19T01:00:00.000001Z', updatedAt: '2026-11-19T01:00:00.000001Z', auditLogId: 41, replayed: false, ...patch });

test('production panel stays disconnected: the registry flow uses the code-only gate and no execute is possible', () => {
  const env = runtime();
  const html = renderToStaticMarkup(React.createElement(env.load('components/admin/content-review-panel.tsx').ContentReviewPanel, { content: detailOf() }));
  assert.match(html, /<button[^>]*disabled=""[^>]*>승인 실행 \(미연결\)<\/button>/);
  assert.match(html, /승인·반려 실행은 아직 연결되지 않았습니다/);
  assert.equal(env.load('lib/admin-review-flow.ts').reviewFlowFor(2).writeGate().enabled, false);
  const screen = fs.readFileSync(path.join(root, 'components/admin/content-detail-screen.tsx'), 'utf8');
  assert.doesNotMatch(screen, /flow=|reviewWriteGate|ACTIVATION/, 'the detail screen never injects a flow or gate');
});

test('POST UI states: success only after 200, unknown outcomes get a limited same-request recheck, conflicts never re-run', () => {
  const env = runtime(); const { ContentReviewPanel } = env.load('components/admin/content-review-panel.tsx');
  const render = (state, enabled = true) => renderToStaticMarkup(React.createElement(ContentReviewPanel, { content: detailOf(), flow: stubFlow(state, enabled) }));
  const idle = render({ phase: 'idle' });
  assert.match(idle, /<button[^>]*disabled=""[^>]*>승인 실행<\/button>/, 'enabled gate still needs the confirmed input');
  assert.match(idle, /HTTP 200으로 확인한 경우에만 완료로 표시/); assert.equal(buttonState(idle, '승인 내용 확인'), 'enabled');

  const submitting = render({ phase: 'submitting', request: flowRequest, recheck: false });
  assert.equal(buttonState(submitting, '승인 내용 확인'), 'disabled'); assert.equal(buttonState(submitting, '반려 내용 확인'), 'disabled');
  assert.match(submitting, /data-review-phase="submitting"/); assert.match(submitting, /aria-busy="true"/);
  assert.match(submitting, /승인 요청을 처리하는 중입니다. 서버 응답을 받기 전에는 완료로 표시하지 않습니다/);
  assert.ok(submitting.includes(flowRequest.requestId)); assert.doesNotMatch(submitting, /승인 완료|감사 기록 #/);

  const done = render({ phase: 'succeeded', request: flowRequest, result: flowResult() });
  assert.match(done, /<strong>승인 완료<\/strong> · 감사 기록 #41/);
  assert.match(done, /처리 결과: 콘텐츠 active · 검수 confirmed · 초안 published/);
  assert.match(done, /현재 상태는 상세를 다시 조회해 표시합니다/); assert.doesNotMatch(done, /이미 처리된/);
  assert.match(render({ phase: 'succeeded', request: flowRequest, result: flowResult({ replayed: true }) }), /이미 처리된 같은 요청의 결과를 다시 받았습니다/);

  const uncertain = render({ phase: 'uncertain', request: flowRequest, rechecks: 1, message: '처리 결과를 확인하지 못했습니다. 같은 요청으로 다시 확인해 주세요.' });
  assert.match(uncertain, /data-review-phase="uncertain"/); assert.ok(uncertain.includes(flowRequest.requestId));
  assert.equal(buttonState(uncertain, '같은 요청으로 다시 확인 \\(1/3\\)'), 'enabled');
  assert.equal(buttonState(uncertain, '승인 내용 확인'), 'disabled', 'no new action while unresolved');
  assert.doesNotMatch(uncertain, /실패|저장되지|저장하지 못|미저장|취소되었|승인 완료/);
  const exhausted = render({ phase: 'uncertain', request: flowRequest, rechecks: 3, message: '처리 결과를 확인하지 못했습니다. 같은 요청으로 다시 확인해 주세요.' });
  assert.equal(buttonState(exhausted, '같은 요청으로 다시 확인 \\(3/3\\)'), 'disabled'); assert.match(exhausted, /BE 감사 기록/);
  const gated = render({ phase: 'uncertain', request: flowRequest, rechecks: 0, message: 'x' }, false);
  assert.equal(buttonState(gated, '같은 요청으로 다시 확인 \\(0/3\\)'), 'disabled', 'no recheck without an enabled gate');

  const conflict = render({ phase: 'stopped', request: flowRequest, failure: { status: 'conflict', message: '콘텐츠 상태가 변경되었습니다. 다시 조회한 뒤 확인해 주세요.', retrySameRequest: false, reload: true } });
  assert.match(conflict, /자동으로 다시 실행하지 않습니다/); assert.doesNotMatch(conflict, /같은 요청으로 다시 확인 \(/);
  const forbidden = render({ phase: 'stopped', request: flowRequest, failure: { status: 'forbidden', message: '콘텐츠 검수 권한이 없습니다.', retrySameRequest: false, reload: true } });
  assert.match(forbidden, /콘텐츠 검수 권한이 없습니다/); assert.doesNotMatch(forbidden, /자동으로 다시 실행/);
  assert.match(render({ phase: 'blocked', message: '현재 계정은 조회 전용입니다. 승인·반려 권한이 없습니다.' }), /data-review-phase="blocked"[\s\S]*조회 전용/);
});

const reviewId = '6f1c2b8e-3d4a-4f5b-9c6d-7e8f9a0b1c2d';
const reviewFields = ['requestId', 'action', 'rationale', 'expectedStatus', 'expectedJudgmentStatus', 'expectedUpdatedAt'];
const approved = (patch = {}) => ({ contentId: 2, draftId: 3, action: 'approve', status: 'active', judgmentStatus: 'confirmed',
  draftStatus: 'published', publishedAt: '2026-11-18T01:02:03.123456Z', updatedAt: '2026-11-18T01:02:03.123456Z', auditLogId: 41, replayed: false, ...patch });
const rejected = (patch = {}) => ({ ...approved(), action: 'reject', status: 'hidden', judgmentStatus: 'failed', draftStatus: 'discarded', publishedAt: null, ...patch });
function reviewRuntime(reply = () => approved()) {
  let issued = 0;
  const env = runtime((url, init) => url.pathname === '/api/v1/auth/session'
    ? { authenticated: true, sessionState: 'active', user: { id: 7, accountType: 'user' }, onboardingCompleted: true, csrfToken: `csrf-${++issued}` }
    : url.pathname === '/api/v1/admin/contents/2' && (init.method ?? 'GET') === 'GET' ? detailOf() : reply(url, init));
  const review = env.load('lib/admin-content-review.ts');
  return { ...env, review: { ...review, submitAdminContentReview: (id, request, signal) => review.submitAdminContentReview(id, request, reviewScope, signal) }, posts: () => env.calls.filter(call => call.init.method === 'POST') };
}

test('review request forwards the detail version verbatim with only the server DTO fields', () => {
  const { review } = reviewRuntime();
  for (const updatedAt of ['2026-11-13T09:12:34.123456+09:00', '2026-11-13T00:12:34Z', '2026-11-13T00:12:34.1Z', '2026-11-12T20:42:34.123400-03:30']) {
    for (const status of ['active', 'pending_review']) {
      const request = review.prepareReviewRequest({ ...detailOf(), status, updatedAt }, 'reject', '  출처 불일치\n확인 😀  ', reviewId);
      assert.deepEqual(plain(request), { requestId: reviewId, action: 'reject', rationale: '출처 불일치\n확인 😀',
        expectedStatus: status, expectedJudgmentStatus: 'needs_review', expectedUpdatedAt: updatedAt });
      assert.deepEqual(Object.keys(request), reviewFields);
    }
  }
});

test('review request refuses ineligible state, unknown version, bad rationale, action or requestId', () => {
  const { review } = reviewRuntime();
  const cases = [
    [{ ...detailOf(), status: 'hidden' }, 'approve', '사유', reviewId], [{ ...detailOf(), judgmentStatus: 'confirmed' }, 'approve', '사유', reviewId],
    [{ ...detailOf(), updatedAt: '2026-11-13T00:12:34' }, 'approve', '사유', reviewId], [{ ...detailOf(), updatedAt: undefined }, 'approve', '사유', reviewId],
    [detailOf(), 'approve', '  ', reviewId], [detailOf(), 'approve', 'a\x00b', reviewId], [detailOf(), 'hide', '사유', reviewId],
    [detailOf(), 'approve', '사유', 'not-a-uuid'], [detailOf(), 'approve', '사유', ''],
  ];
  for (const args of cases) assert.throws(() => review.prepareReviewRequest(...args), String(args[1]));
});

test('review submit sends nothing for a request that bypassed preparation', async () => {
  const env = reviewRuntime();
  const request = env.review.prepareReviewRequest(detailOf(), 'approve', '사유', reviewId);
  for (const [contentId, patch] of [[0, {}], [2, { action: 'hide' }], [2, { requestId: 'x' }], [2, { expectedUpdatedAt: '2026-11-13' }]]) {
    await assert.rejects(env.review.submitAdminContentReview(contentId, { ...request, ...patch }));
  }
  assert.equal(env.calls.length, 0);
});

test('review submit sends one credentialed POST with session CSRF and the exact request body', async () => {
  for (const [action, result] of [['approve', approved()], ['approve', approved({ replayed: true })],
    ['reject', rejected()], ['reject', rejected({ publishedAt: '2026-11-01T00:00:00Z' })]]) {
    const env = reviewRuntime(() => result), signal = new AbortController().signal;
    const request = env.review.prepareReviewRequest(detailOf(), action, '출처 확인', reviewId);
    assert.deepEqual(plain(await env.review.submitAdminContentReview(2, request, signal)), result);
    const [post] = env.posts();
    assert.equal(env.calls.length, 3); assert.equal(env.posts().length, 1);
    assert.equal(new URL(post.url).pathname, '/api/v1/admin/contents/2/review');
    assert.equal(post.init.credentials, 'include'); assert.equal(post.init.cache, 'no-store'); assert.equal(post.init.signal, signal);
    assert.equal(post.init.headers.get('X-CSRF-Token'), 'csrf-1');
    assert.equal(post.init.headers.get('X-Admin-Review-Actor'), '7');
    assert.equal(post.init.headers.get('X-Admin-Review-Grant'), '107');
    assert.equal(post.init.headers.get('Content-Type'), 'application/json');
    assert.equal(post.init.body, JSON.stringify(plain(request)));
    assert.deepEqual(Object.keys(JSON.parse(post.init.body)), reviewFields);
  }
});

test('a 200 that does not prove the audited transition is never a success and is retried as the same request', async () => {
  for (const response of [null, [], approved({ contentId: 3 }), approved({ action: 'reject' }), approved({ status: 'hidden' }),
    approved({ judgmentStatus: 'failed' }), approved({ draftStatus: 'discarded' }), approved({ publishedAt: null }),
    approved({ updatedAt: '2026-11-18T01:02:03' }), approved({ updatedAt: undefined }), approved({ auditLogId: 0 }),
    approved({ auditLogId: undefined }), approved({ draftId: 0 }), approved({ replayed: 'false' }),
    new Response(JSON.stringify(approved()), { status: 201 })]) {
    const env = reviewRuntime(() => response);
    const request = env.review.prepareReviewRequest(detailOf(), 'approve', '출처 확인', reviewId);
    await assert.rejects(env.review.submitAdminContentReview(2, request), error => {
      const failure = env.review.reviewFailure(error);
      assert.equal(failure.status, 'retry'); assert.equal(failure.retrySameRequest, true); return true;
    });
  }
  const env = reviewRuntime(() => rejected({ publishedAt: 'bad' }));
  await assert.rejects(env.review.submitAdminContentReview(2, env.review.prepareReviewRequest(detailOf(), 'reject', '사유', reviewId)),
    error => error.code === 'INVALID_ADMIN_REVIEW_RESPONSE');
});

test('review failures map every BE code without exposing server messages', async () => {
  for (const [status, code, expected, retrySameRequest, reload] of [
    [401, 'AUTH_REQUIRED', 'login-required', false, true], [403, 'CSRF_INVALID', 'retry', true, false],
    [403, 'ADMIN_REQUIRED', 'forbidden', false, true], [403, 'ADMIN_WRITE_REQUIRED', 'forbidden', false, true],
    [404, 'CONTENT_NOT_FOUND', 'not-found', false, true], [404, 'NOT_FOUND', 'unavailable', false, false],
    [409, 'REVIEW_STATE_CONFLICT', 'conflict', false, true], [409, 'REVIEW_REQUEST_CONFLICT', 'conflict', false, true],
    [422, 'VALIDATION_ERROR', 'invalid', false, false], [503, 'ADMIN_REVIEW_UNAVAILABLE', 'retry', true, false],
    [503, 'ADMIN_PERMISSION_UNAVAILABLE', 'retry', true, false], [503, 'ADMIN_AUTH_UNAVAILABLE', 'retry', true, false],
    [500, 'INTERNAL_ERROR', 'retry', true, false]]) {
    const env = reviewRuntime(() => new Response(JSON.stringify({ code, message: 'internal secret' }), { status }));
    await assert.rejects(env.review.submitAdminContentReview(2, env.review.prepareReviewRequest(detailOf(), 'approve', '사유', reviewId)), error => {
      assert.deepEqual({ ...plain(env.review.reviewFailure(error)), message: undefined },
        { status: expected, retrySameRequest, reload, message: undefined }, code);
      assert.doesNotMatch(env.review.reviewFailure(error).message, /secret/); return true;
    });
  }
  const offline = reviewRuntime(() => { throw new TypeError('Failed to fetch'); });
  await assert.rejects(offline.review.submitAdminContentReview(2, offline.review.prepareReviewRequest(detailOf(), 'approve', '사유', reviewId)),
    error => offline.review.reviewFailure(error).retrySameRequest === true);
});

test('retrying keeps the same requestId and body and refreshes a rejected CSRF token', async () => {
  const replies = [new Response(JSON.stringify({ code: 'CSRF_INVALID' }), { status: 403 }),
    new Response(JSON.stringify({ code: 'ADMIN_REVIEW_UNAVAILABLE' }), { status: 503 }), approved({ replayed: true })];
  const env = reviewRuntime(() => replies.shift());
  const request = env.review.prepareReviewRequest(detailOf(), 'approve', '출처 확인', reviewId);
  for (let attempt = 0; attempt < 2; attempt++) {
    await assert.rejects(env.review.submitAdminContentReview(2, request), error => env.review.reviewFailure(error).retrySameRequest);
  }
  assert.equal((await env.review.submitAdminContentReview(2, request)).replayed, true);
  const posts = env.posts();
  assert.equal(posts.length, 3);
  assert.ok(posts.every(call => call.init.body === posts[0].init.body));
  assert.equal(JSON.parse(posts[0].init.body).requestId, reviewId);
  assert.deepEqual(posts.map(call => call.init.headers.get('X-CSRF-Token')), ['csrf-1', 'csrf-2', 'csrf-3']);
});

test('unknown outcomes never claim a failed or unsaved review and resend the identical request', async () => {
  const error = (status, code) => () => new Response(JSON.stringify({ code, message: '검수 저장에 실패했습니다.' }), { status });
  const unknown = [error(503, 'ADMIN_REVIEW_UNAVAILABLE'), error(503, 'ADMIN_PERMISSION_UNAVAILABLE'), error(503, 'ADMIN_AUTH_UNAVAILABLE'),
    error(500, 'INTERNAL_ERROR'), error(502, 'BAD_GATEWAY'), error(504, 'GATEWAY_TIMEOUT'), error(408, 'TIMEOUT'), error(429, 'RATE_LIMITED'),
    () => { throw new TypeError('Failed to fetch'); }, () => approved({ auditLogId: 0 }), () => new Response(JSON.stringify(approved()), { status: 201 })];
  for (const [index, first] of unknown.entries()) {
    const replies = [first, () => approved({ replayed: true })];
    const env = reviewRuntime(() => replies.shift()());
    const request = env.review.prepareReviewRequest(detailOf(), 'approve', '출처 확인', reviewId);
    await assert.rejects(env.review.submitAdminContentReview(2, request), failure => {
      const result = env.review.reviewFailure(failure);
      assert.deepEqual(plain(result), { status: 'retry', retrySameRequest: true, reload: false,
        message: '처리 결과를 확인하지 못했습니다. 같은 요청으로 다시 확인해 주세요.' }, String(index));
      assert.doesNotMatch(result.message, /실패|완료하지 못|저장되지|저장하지 못|미저장|반영되지|취소/);
      return true;
    });
    assert.equal((await env.review.submitAdminContentReview(2, request)).replayed, true);
    const posts = env.posts();
    assert.equal(posts.length, 2); assert.equal(posts[1].init.body, posts[0].init.body);
    assert.equal(JSON.parse(posts[1].init.body).requestId, reviewId);
  }
  // A response lost to an abort after the request left is also an unknown outcome.
  let release; const pending = new Promise(resolve => release = resolve);
  const env = reviewRuntime(() => pending), controller = new AbortController();
  const sent = env.review.submitAdminContentReview(2, env.review.prepareReviewRequest(detailOf(), 'approve', '사유', reviewId), controller.signal);
  await new Promise(resolve => setImmediate(resolve)); assert.equal(env.posts().length, 1);
  controller.abort(); release(approved());
  await assert.rejects(sent, failure => {
    const result = env.review.reviewFailure(failure);
    assert.equal(result.status, 'retry'); assert.equal(result.retrySameRequest, true);
    assert.doesNotMatch(result.message, /실패|저장되지|미저장/); return true;
  });
});

test('no admin screen sends a review write before BE registers the route', () => {
  const files = ['components/admin', 'app/admin'].flatMap(dir => fs.readdirSync(path.join(root, dir), { recursive: true })
    .filter(file => /\.tsx?$/.test(file)).map(file => path.join(root, dir, file)));
  assert.ok(files.some(file => file.endsWith('content-review-panel.tsx')));
  for (const file of files) assert.doesNotMatch(fs.readFileSync(file, 'utf8'), /submitAdminContentReview/, file);
});

test('no implicit queue filter; URL preserves independent states and page size', () => {
  const { api } = runtime();
  assert.deepEqual(plain(api.parseAdminQuery(new URLSearchParams())), { mode: 'all', q: '', judgmentStatus: '', status: '', page: 1, pageSize: 20 });
  const query = api.parseAdminQuery(new URLSearchParams('q=+달빛%25_+&judgmentStatus=needs_review&status=active&page=2&pageSize=50'));
  assert.equal(query.q, '달빛%_');
  assert.deepEqual(plain(api.parseAdminQuery(new URLSearchParams(api.adminQueryString(query)))), plain(query));
  for (const value of ['mode=invalid', 'mode=', 'page=0', 'page=-1', 'page=1.2', 'page=1000001', 'pageSize=101', 'status=published', 'judgmentStatus=approved', 'q=' + 'a'.repeat(301)]) {
    assert.throws(() => api.parseAdminQuery(new URLSearchParams(value)));
  }
});

test('list uses existing credentialed no-store API client and exact DTO fields', async () => {
  const env = runtime(); const query = env.api.parseAdminQuery(new URLSearchParams('q=달빛%25_&judgmentStatus=needs_review&status=active'));
  const signal = new AbortController().signal;
  assert.deepEqual(plain(await env.api.loadAdminContents(query, signal)), page());
  const { url, init } = env.calls[0]; const parsed = new URL(url);
  assert.equal(parsed.pathname, '/api/v1/admin/contents');
  assert.equal(parsed.searchParams.get('q'), '달빛%_');
  assert.equal(parsed.searchParams.get('judgmentStatus'), 'needs_review');
  assert.equal(parsed.searchParams.get('status'), 'active');
  assert.equal(parsed.searchParams.get('pageSize'), '20');
  assert.equal(init.credentials, 'include'); assert.equal(init.cache, 'no-store'); assert.equal(init.signal, signal);
});

test('review mode preserves URL filters and requests the server queue without client sorting', async () => {
  const rows = [item(4), { ...item(99), status: 'pending_review', createdAt: '2026-11-13T00:00:00Z' }];
  const data = { items: rows, page: 2, pageSize: 2, total: 7 };
  const env = runtime(() => data);
  const query = env.api.parseAdminQuery(new URLSearchParams('mode=review&q=+달빛%25_+&judgmentStatus=needs_review&page=2&pageSize=2'));
  const roundTrip = env.api.parseAdminQuery(new URLSearchParams(env.api.adminQueryString(query)));
  assert.deepEqual(plain(roundTrip), plain(query));
  assert.equal(roundTrip.mode, 'review');
  assert.deepEqual(plain(await env.api.loadAdminContents(query)), data);
  assert.equal(new URL(env.calls[0].url).search, '?' + env.api.adminQueryString(query));
  for (const mode of [undefined, 'all']) {
    assert.equal(new URLSearchParams(env.api.adminQueryString({ ...query, mode })).has('mode'), false);
  }
});

test('review mode rejects a successful response containing completed judgments', async () => {
  for (const judgmentStatus of ['pending', 'confirmed', 'failed']) {
    const env = runtime(() => page([{ ...item(), judgmentStatus }]));
    await assert.rejects(env.api.loadAdminContents(env.api.parseAdminQuery(new URLSearchParams('mode=review'))),
      error => error.code === 'INVALID_ADMIN_RESPONSE');
    // All mode continues to accept the same stored states.
    assert.equal((await env.api.loadAdminContents(env.api.parseAdminQuery(new URLSearchParams()))).items[0].judgmentStatus, judgmentStatus);
  }
});

test('review screen marks priority without claiming public visibility and carries the full detail query', () => {
  const env = runtime(); const { ContentsScreen } = env.load('components/admin/contents-screen.tsx');
  const query = env.api.parseAdminQuery(new URLSearchParams('mode=review&q=달빛&judgmentStatus=needs_review&page=2&pageSize=3'));
  const rows = [item(2), { ...item(1), status: 'pending_review' }, { ...item(8), status: 'hidden' }];
  const html = renderToStaticMarkup(React.createElement(ContentsScreen, {
    query, state: { status: 'ready', data: { items: rows, page: 2, pageSize: 3, total: 9 } }, retry() {}, navigate() {},
  }));
  assert.match(html, /aria-pressed="true"[^>]*>검수 대기/);
  assert.match(html, /우선 확인/); assert.match(html, /일반 비공개 대기/); assert.match(html, /상태 확인 필요/);
  assert.match(html, /needs_review<\/span>/); assert.match(html, /active<\/span>/);
  assert.doesNotMatch(html, /공개 중|미리보기 중/);
  assert.ok(html.includes(`/admin/contents/2?${env.api.adminQueryString(query).replaceAll('&', '&amp;')}`));
  assert.match(html, /<strong>9<\/strong>/);
});

test('successful empty result is distinct from API failure, login, permission and disconnected routes', async () => {
  const empty = runtime(() => page([]));
  assert.deepEqual(plain(await empty.api.loadAdminContents(empty.api.parseAdminQuery(new URLSearchParams()))), page([]));
  for (const [status, code, expected] of [[401, 'AUTH_REQUIRED', 'login-required'], [403, 'ADMIN_REQUIRED', 'forbidden'],
    [404, 'NOT_FOUND', 'unavailable'], [404, 'CONTENT_NOT_FOUND', 'not-found'], [500, 'PRIVATE_DETAIL', 'error'],
    [503, 'ADMIN_AUTH_UNAVAILABLE', 'error'], [503, 'ADMIN_PERMISSION_UNAVAILABLE', 'error']]) {
    const env = runtime(() => new Response(JSON.stringify({ code, message: 'internal secret' }), { status }));
    await assert.rejects(env.api.loadAdminContents(env.api.parseAdminQuery(new URLSearchParams())), error => {
      const failure = env.api.adminFailure(error); assert.equal(failure.status, expected); assert.doesNotMatch(failure.message, /secret/); return true;
    });
    await assert.rejects(env.api.loadAdminContent(2), error => {
      const failure = env.api.adminFailure(error); assert.equal(failure.status, expected); assert.doesNotMatch(failure.message, /secret/); return true;
    });
  }
});

test('invalid wire data cannot become a successful list', async () => {
  for (const response of [null, {}, { ...page(), total: -1 }, { ...page(), page: 2 }, { ...page(), pageSize: 50 },
    page([item(), item()]), page([{ ...item(), status: 'published' }]), page([{ ...item(), judgmentStatus: 'approved' }]),
    page([{ ...item(), createdAt: 'bad date' }]), page([{ ...item(), contentId: Number.MAX_SAFE_INTEGER + 1 }])]) {
    const env = runtime(() => response);
    await assert.rejects(env.api.loadAdminContents(env.api.parseAdminQuery(new URLSearchParams())), error => error.code === 'INVALID_ADMIN_RESPONSE');
  }
});

test('detail ID and source fields are validated, unsafe source URLs cannot be links', async () => {
  const detail = { ...detailOf(), body: '<script>not executable</script>', sources: [
    { url: 'https://example.com/source', title: null, publisher: null, role: 'primary', citationOrder: 1 },
  ] };
  const env = runtime(() => detail);
  assert.deepEqual(plain(await env.api.loadAdminContent(2)), detail);
  await assert.rejects(env.api.loadAdminContent(3), error => error.code === 'INVALID_ADMIN_RESPONSE');
  assert.equal(env.api.safeSourceUrl('https://example.com/source'), 'https://example.com/source');
  for (const url of ['javascript:alert(1)', 'data:text/html,x', 'https://name:password@example.com', '/relative']) assert.equal(env.api.safeSourceUrl(url), undefined);
});

test('detail preserves the exact version string for a future expectedUpdatedAt payload', async () => {
  for (const updatedAt of ['2026-11-13T00:12:34Z', '2026-11-13T00:12:34+00:00',
    '2026-11-13T00:12:34.1Z', '2026-11-13T00:12:34.12Z', '2026-11-13T00:12:34.123Z',
    '2026-11-13T00:12:34.1234Z', '2026-11-13T00:12:34.12345Z', '2026-11-13T00:12:34.123456Z',
    '2026-11-13T09:12:34.000001+09:00', '2026-11-12T20:42:34.123400-03:30',
    '2000-02-29T23:59:59.000000Z', '2024-02-29T00:00:00Z', '0001-01-01T00:00:00Z', '9999-12-31T23:59:59Z']) {
    const detail = { ...detailOf(), updatedAt }, env = runtime(() => detail);
    const parsed = await env.api.loadAdminContent(2);
    assert.deepEqual(plain(parsed), detail);
    const futurePayload = JSON.stringify({ expectedUpdatedAt: parsed.updatedAt });
    assert.equal(JSON.parse(futurePayload).expectedUpdatedAt, updatedAt);
    assert.equal(env.calls.length, 1);
    assert.equal(env.calls[0].init.method ?? 'GET', 'GET');
  }
  // Adjacent DB versions inside the same JS millisecond remain distinguishable.
  const versions = [];
  for (const updatedAt of ['2026-11-13T00:12:34.123456Z', '2026-11-13T00:12:34.123457Z']) {
    const env = runtime(() => ({ ...detailOf(), updatedAt }));
    versions.push((await env.api.loadAdminContent(2)).updatedAt);
  }
  assert.notEqual(...versions);
});

test('detail rejects missing, invalid, ambiguous or unsupported precision versions', async () => {
  const invalid = [undefined, null, '', 1794528000, {}, 'not a date',
    '2026-11-13', '2026-11-13T00:12:34', '2026-11-13T00:12:34.123456',
    '2026-11-13T00:12:34-00:00', '2026-11-13T00:12:34+24:00', '2026-11-13T00:12:34+09:60',
    '2026-11-13T00:12:34+0900', '2026-11-13T00:12:34+09:00:30', '2026-11-13T00:12:34Zjunk',
    '2026-11-13T00:12:34.1234567Z', '2026-11-13T00:12:34.1234560Z', '2026-11-13T00:12:34.Z',
    '2026-11-13T00:12:34,123456Z', '2026-11-13 00:12:34Z', ' 2026-11-13T00:12:34Z',
    '2026-11-13T00:12:34Z\n', '2026-11-13T00:12:34Z ', '0000-01-01T00:00:00Z',
    '2026-00-13T00:12:34Z', '2026-13-13T00:12:34Z', '2026-11-00T00:12:34Z', '2026-11-31T00:12:34Z',
    '2026-02-29T00:12:34Z', '1900-02-29T00:12:34Z', '2024-02-30T00:12:34Z',
    '2026-11-13T24:00:00Z', '2026-11-13T00:60:00Z', '2026-11-13T00:12:60Z'];
  for (const updatedAt of invalid) {
    const env = runtime(() => ({ ...detailOf(), updatedAt }));
    await assert.rejects(env.api.loadAdminContent(2), error => error.code === 'INVALID_ADMIN_RESPONSE', String(updatedAt));
  }
});

test('an aborted read suppresses a late 401 event', async () => {
  let finish; const waiting = new Promise(resolve => finish = resolve);
  const env = runtime(() => waiting), controller = new AbortController(); let unauthorized = 0;
  env.browser.addEventListener('api:unauthorized', () => unauthorized++);
  const result = env.api.loadAdminContents(env.api.parseAdminQuery(new URLSearchParams()), controller.signal);
  controller.abort(); finish(new Response('{}', { status: 401 }));
  await assert.rejects(result); assert.equal(unauthorized, 0);
});

test('source display preserves stored metadata and response order without inventing review values', async () => {
  const detail = { ...detailOf(), sources: [
    { url: 'https://example.com/source', title: '원문 제목', publisher: '발행처 이름', role: 'primary', citationOrder: 7 },
    { url: 'http://example.org/reference', title: '참고 제목', publisher: null, role: 'unrecognized-role', citationOrder: 2 },
  ] };
  const env = runtime(() => detail);
  const { ContentSources } = env.load('components/admin/content-detail-screen.tsx');
  const data = await env.api.loadAdminContent(2);
  const html = renderToStaticMarkup(React.createElement(ContentSources, { sources: data.sources }));
  assert.match(html, /href="https:\/\/example.com\/source" target="_blank" rel="noopener noreferrer">원문 제목<\/a>/);
  assert.match(html, /URL: https:\/\/example.com\/source/);
  assert.match(html, /발행처: 발행처 이름/);
  assert.match(html, /역할: primary · 인용 순서: 7/);
  assert.match(html, /역할: unrecognized-role · 인용 순서: 2/);
  assert.ok(html.indexOf('원문 제목') < html.indexOf('참고 제목'));
  assert.doesNotMatch(html, /<button|<input|<select|검증 완료|승인/);
  assert.deepEqual(plain(data), detail);
  assert.equal(env.calls.length, 1);
});

test('source display handles empty sources, nullable metadata and blank text', () => {
  const env = runtime(); const { ContentSources } = env.load('components/admin/content-detail-screen.tsx');
  const render = sources => renderToStaticMarkup(React.createElement(ContentSources, { sources }));
  assert.equal(render([]), '<p>등록된 출처가 없습니다.</p>');
  const nullable = render([{ url: 'https://example.com/', title: null, publisher: null, role: 'material', citationOrder: null }]);
  assert.match(nullable, />https:\/\/example.com\/<\/a>/);
  assert.match(nullable, /발행처: —/);
  assert.match(nullable, /역할: material · 인용 순서: —/);
  assert.doesNotMatch(nullable, /null|undefined/);
  const blank = render([{ url: ' ', title: ' ', publisher: ' ', role: ' ', citationOrder: null }]);
  assert.match(blank, /출처 링크를 표시할 수 없습니다/);
  assert.match(blank, /URL: — \(유효하지 않은 URL\)/);
  assert.match(blank, /발행처: —/);
  assert.match(blank, /역할: — · 인용 순서: —/);
  assert.doesNotMatch(blank, /<a\b/);
});

test('invalid source URLs stay plain text and every source field is escaped', () => {
  const env = runtime(); const { ContentSources } = env.load('components/admin/content-detail-screen.tsx');
  for (const url of ['', 'not a URL', '/relative', 'javascript:alert(1)', 'data:text/html,<script>x</script>',
    'https://user:password@example.com', 'ftp://example.com/source']) {
    const html = renderToStaticMarkup(React.createElement(ContentSources, { sources: [
      { url, title: '<script>title</script>', publisher: '<img src=x onerror=alert(1)>', role: '<b>primary</b>', citationOrder: null },
    ] }));
    assert.doesNotMatch(html, /<a\b|<script\b|<img\b|<b>/);
    assert.match(html, /유효하지 않은 URL/);
    assert.match(html, /&lt;script&gt;title&lt;\/script&gt;/);
    assert.match(html, /발행처: &lt;img/);
    assert.match(html, /역할: &lt;b&gt;primary&lt;\/b&gt;/);
  }
});

test('screen separates loading/error/empty and preserves both status values and detail return query', () => {
  const env = runtime(); const { ContentsScreen } = env.load('components/admin/contents-screen.tsx');
  const query = env.api.parseAdminQuery(new URLSearchParams('q=달빛&status=active'));
  const render = state => renderToStaticMarkup(React.createElement(ContentsScreen, { query, state, retry() {}, navigate() {} }));
  assert.match(render({ status: 'loading' }), /불러오는 중/);
  const error = render({ status: 'error', message: '네트워크 오류' });
  assert.match(error, /다시 시도/); assert.doesNotMatch(error, /조회된 콘텐츠가 없습니다|data-admin-empty/);
  assert.match(render({ status: 'ready', data: page([]) }), /조회된 콘텐츠가 없습니다/);
  assert.match(render({ status: 'login-required', message: '로그인 필요' }), /href="\/pivot\/login"/);
  assert.match(render({ status: 'forbidden', message: '관리자 권한 없음' }), /접근 권한이 없습니다/);
  assert.match(render({ status: 'unavailable', message: '연결 준비' }), /조회 연결 준비 중/);
  const html = render({ status: 'ready', data: page([{ ...item(), title: '<script>x</script>' }]) });
  assert.match(html, /needs_review<\/span>/); assert.match(html, /active<\/span>/);
  assert.match(html, /href="\/admin\/contents\/2\?q=/); assert.match(html, /&amp;status=active/);
  assert.match(html, /&lt;script&gt;x&lt;\/script&gt;/); assert.doesNotMatch(html, /<script>x/);
  assert.match(html, /09:00/);
});

test('Figma statistics and unavailable tabs never invent operating metrics or published status', () => {
  const env = runtime(); const { ContentsScreen } = env.load('components/admin/contents-screen.tsx');
  const query = env.api.parseAdminQuery(new URLSearchParams());
  const html = renderToStaticMarkup(React.createElement(ContentsScreen, {
    query, state: { status: 'ready', data: page([{ ...item(), judgmentStatus: 'confirmed', publishedAt: '2026-11-01T00:00:00Z' }]) }, retry() {}, navigate() {},
  }));
  const stats = html.match(/<section[^>]*data-admin-statistics="unavailable"[\s\S]*?<\/section>/)[0];
  assert.equal((stats.match(/<article/g) || []).length, 3);
  assert.doesNotMatch(stats, /\d[,.]?\d*%|11,578|1,247|<strong>\d/);
  for (const label of ['게시 중', '예약 발행', '신고 접수']) assert.match(html, new RegExp(`<button[^>]*disabled=""[^>]*>${label}</button>`));
  const row = html.match(/<tr data-admin-content=[\s\S]*?<\/tr>/)[0];
  assert.match(row, /confirmed<\/span>/); assert.match(row, /active<\/span>/);
  assert.doesNotMatch(row, /게시 중|예약 발행|신고 접수|애니메이션/);
  for (const label of ['분류', '분야', '조회수']) assert.ok(row.includes(`aria-label="${label} 연결 전">—`));
});

test('Figma pagination follows server total without treating unavailable metrics as counts', () => {
  const env = runtime(); const { ContentsScreen } = env.load('components/admin/contents-screen.tsx');
  const query = env.api.parseAdminQuery(new URLSearchParams('page=7&pageSize=20'));
  const html = renderToStaticMarkup(React.createElement(ContentsScreen, {
    query, state: { status: 'ready', data: { items: [item()], page: 7, pageSize: 20, total: 200 } }, retry() {}, navigate() {},
  }));
  assert.match(html, /<strong>200<\/strong>/);
  assert.match(html, /aria-label="7페이지" aria-current="page"/);
  assert.match(html, /aria-label="1페이지"/); assert.match(html, /aria-label="10페이지"/);
});
