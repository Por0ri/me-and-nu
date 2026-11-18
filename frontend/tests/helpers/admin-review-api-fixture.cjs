const http = require('node:http');

// In-memory, loopback-only fake of the admin content API for FE integration tests.
// Never imported by application code. It mirrors the current BE review contract
// (backend/app/services/admin_content_review_service.py) and the 11/19 BE role table:
// viewer reads only; operator and admin read, approve and reject. Passing these
// scenarios does not prove the real BE; the BE owns its own PostgreSQL verification.
const READ_ROLES = new Set(['viewer', 'operator', 'admin']);
const WRITE_ROLES = new Set(['operator', 'admin']);
const FIELDS = ['requestId', 'action', 'rationale', 'expectedStatus', 'expectedJudgmentStatus', 'expectedUpdatedAt'];
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const VERSION = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(Z|([+-])(\d{2}):(\d{2}))$/;
const CONTROL = /[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff]/u;
const COMMITTING_FAULTS = new Set(['unavailable-after-commit', 'drop-after-commit', 'bad-audit-response']);

// Offset-aware instant in microseconds, like Python's aware datetime comparison.
function instant(text) {
  const parts = typeof text === 'string' && VERSION.exec(text);
  if (!parts) return null;
  const seconds = Date.UTC(+parts[1], +parts[2] - 1, +parts[3], +parts[4], +parts[5], +parts[6]) / 1000;
  const offset = parts[8] === 'Z' ? 0 : (parts[9] === '-' ? -1 : 1) * (+parts[10] * 3600 + +parts[11] * 60);
  return BigInt(seconds - offset) * 1_000_000n + BigInt((parts[7] ?? '').padEnd(6, '0'));
}
// The BE replay fingerprint is the re-serialized request: the offset text survives
// (UTC prints as Z), fraction digits are normalized, rationale is stripped.
function fingerprint(data) {
  const offset = VERSION.exec(data.expectedUpdatedAt)[8];
  return JSON.stringify([data.requestId.toLowerCase(), data.action, data.rationale.trim(), data.expectedStatus,
    data.expectedJudgmentStatus, offset === '+00:00' ? 'Z' : offset, String(instant(data.expectedUpdatedAt))]);
}
function validRequest(data) {
  if (!data || typeof data !== 'object' || Array.isArray(data)) return false;
  const keys = Object.keys(data);
  return keys.length === FIELDS.length && FIELDS.every(key => keys.includes(key)) &&
    typeof data.requestId === 'string' && UUID.test(data.requestId) && ['approve', 'reject'].includes(data.action) &&
    typeof data.rationale === 'string' && !CONTROL.test(data.rationale) && data.rationale.trim().length >= 1 &&
    Array.from(data.rationale.trim()).length <= 2000 && ['active', 'pending_review'].includes(data.expectedStatus) &&
    data.expectedJudgmentStatus === 'needs_review' && instant(data.expectedUpdatedAt) !== null;
}

function createAdminReviewFixture({ actors = {
  viewer: { userId: 21, role: 'viewer' }, operator: { userId: 22, role: 'operator' },
  admin: { userId: 23, role: 'admin' }, member: { userId: 24, role: null },
}, allowedOrigins = [] } = {}) {
  const sessions = new Map(), grants = new Map(), requests = [], audit = [], faults = [];
  let clock = 0, origin = null;
  for (const [name, { userId, role }] of Object.entries(actors)) {
    sessions.set(`session-${name}`, { name, userId, csrf: `csrf-${name}-1`, rotation: 1 });
    if (role) grants.set(userId, { adminAccountId: 100 + userId, role, revoked: false });
  }
  const item = (contentId, status, judgmentStatus, updatedAt) => ({ contentId, title: `검수 대상 ${contentId}`, judgmentStatus, status,
    createdAt: '2026-11-18T00:00:00Z', publishedAt: null, updatedAt, summary: '요약', body: '본문', sources: [],
    draftId: contentId + 1000, draftStatus: 'checking' });
  const contents = new Map([
    [2, item(2, 'pending_review', 'needs_review', '2026-11-19T09:12:34.123456+09:00')],
    [3, item(3, 'active', 'needs_review', '2026-11-19T00:00:00.000001Z')],
    [4, item(4, 'active', 'confirmed', '2026-11-18T12:00:00Z')],
  ]);
  const stamp = () => `2026-11-19T01:00:00.${String(++clock).padStart(6, '0')}Z`;
  const view = (content, detail) => {
    const { contentId, title, judgmentStatus, status, createdAt, publishedAt, updatedAt, summary, body, sources } = content;
    return detail ? { contentId, title, judgmentStatus, status, createdAt, publishedAt, updatedAt, summary, body, sources }
      : { contentId, title, judgmentStatus, status, createdAt, publishedAt };
  };

  async function handle(req, res) {
    const url = new URL(req.url, origin);
    const chunks = []; for await (const chunk of req) chunks.push(chunk);
    const body = Buffer.concat(chunks).toString('utf8');
    requests.push({ method: req.method, path: url.pathname, headers: req.headers, body });
    const send = (status, payload) => {
      res.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'private, no-store' });
      res.end(JSON.stringify(payload));
    };
    const fail = (status, code) => send(status, { code, message: `fixture ${code} internal detail` });
    const token = /(?:^|;\s*)menu_session=([^;]+)/.exec(req.headers.cookie ?? '')?.[1];
    const session = token ? sessions.get(token) : undefined;
    if (req.method === 'GET' && url.pathname === '/api/v1/auth/session') {
      return send(200, session
        ? { authenticated: true, sessionState: 'active', user: { id: session.userId, accountType: 'user' }, onboardingCompleted: true, csrfToken: session.csrf }
        : { authenticated: false, sessionState: 'anonymous', user: null, onboardingCompleted: false, csrfToken: 'csrf-anonymous' });
    }
    const detail = /^\/api\/v1\/admin\/contents\/(\d+)$/.exec(url.pathname);
    const review = /^\/api\/v1\/admin\/contents\/(\d+)\/review$/.exec(url.pathname);
    const listing = url.pathname === '/api/v1/admin/contents';
    if (!(listing || detail || review) || (review ? req.method !== 'POST' : req.method !== 'GET')) return fail(404, 'NOT_FOUND');
    // require_admin: real session, then a fresh read grant on every request (revocation applies at once).
    if (!session) return fail(401, 'AUTH_REQUIRED');
    const grant = grants.get(session.userId);
    if (!grant || grant.revoked || !READ_ROLES.has(grant.role)) return fail(403, 'ADMIN_REQUIRED');
    if (listing) {
      const page = Number(url.searchParams.get('page') ?? 1), pageSize = Number(url.searchParams.get('pageSize') ?? 20);
      const rows = [...contents.values()].filter(content => url.searchParams.get('mode') !== 'review' || content.judgmentStatus === 'needs_review');
      return send(200, { items: rows.slice((page - 1) * pageSize, page * pageSize).map(row => view(row, false)), page, pageSize, total: rows.length });
    }
    const contentId = Number((detail ?? review)[1]);
    const content = contents.get(contentId);
    if (detail) return content ? send(200, view(content, true)) : fail(404, 'CONTENT_NOT_FOUND');

    // POST review: Origin/CSRF, strict DTO, fresh write grant, content, replay, version/state, commit.
    if (req.headers.origin && !allowedOrigins.includes(req.headers.origin)) return fail(403, 'CSRF_INVALID');
    if (req.headers['x-csrf-token'] !== session.csrf) return fail(403, 'CSRF_INVALID');
    let data; try { data = JSON.parse(body); } catch { return fail(422, 'VALIDATION_ERROR'); }
    if (!validRequest(data)) return fail(422, 'VALIDATION_ERROR');
    const fault = faults.shift();
    if (fault === 'permission-unavailable') return fail(503, 'ADMIN_PERMISSION_UNAVAILABLE');
    if (!WRITE_ROLES.has(grant.role)) return fail(403, 'ADMIN_WRITE_REQUIRED');
    if (!content) return fail(404, 'CONTENT_NOT_FOUND');
    const print = fingerprint(data);
    const previous = audit.find(row => row.contentId === contentId && row.adminAccountId === grant.adminAccountId &&
      row.requestId === data.requestId.toLowerCase());
    if (previous) {
      return previous.fingerprint === print ? send(200, { ...previous.response, replayed: true }) : fail(409, 'REVIEW_REQUEST_CONFLICT');
    }
    if (fault === 'unavailable-before-commit') return fail(503, 'ADMIN_REVIEW_UNAVAILABLE');
    if (content.judgmentStatus !== 'needs_review' || content.status !== data.expectedStatus ||
      instant(content.updatedAt) !== instant(data.expectedUpdatedAt) || !['checking', 'approved'].includes(content.draftStatus)) {
      return fail(409, 'REVIEW_STATE_CONFLICT');
    }
    const now = stamp(), approve = data.action === 'approve';
    Object.assign(content, approve
      ? { status: 'active', judgmentStatus: 'confirmed', draftStatus: 'published', publishedAt: content.publishedAt ?? now }
      : { status: 'hidden', judgmentStatus: 'failed', draftStatus: 'discarded' }, { updatedAt: now });
    const response = { contentId, draftId: content.draftId, action: data.action, status: content.status,
      judgmentStatus: content.judgmentStatus, draftStatus: content.draftStatus, publishedAt: content.publishedAt,
      updatedAt: now, auditLogId: audit.length + 1, replayed: false };
    audit.push({ auditLogId: response.auditLogId, adminAccountId: grant.adminAccountId, actorUserId: session.userId,
      contentId, actionType: `content.${data.action}`, requestId: data.requestId.toLowerCase(), fingerprint: print,
      rationale: data.rationale.trim(), response });
    // Commit has happened; the client may still not learn it.
    if (fault === 'unavailable-after-commit') return fail(503, 'ADMIN_REVIEW_UNAVAILABLE');
    if (fault === 'drop-after-commit') return res.socket.destroy();
    if (fault === 'bad-audit-response') return send(200, { ...response, auditLogId: 0 });
    return send(200, response);
  }

  const server = http.createServer((req, res) => handle(req, res).catch(() => { res.statusCode = 500; res.end('{}'); }));
  return {
    contents, audit, requests, committingFaults: COMMITTING_FAULTS,
    get origin() { return origin; },
    start: () => new Promise(resolve => server.listen(0, '127.0.0.1', () => {
      origin = `http://127.0.0.1:${server.address().port}`; resolve(origin);
    })),
    stop: () => new Promise(resolve => { server.closeAllConnections?.(); server.close(() => resolve()); }),
    cookie: name => (name && sessions.has(`session-${name}`) ? `menu_session=session-${name}` : null),
    failNext: kind => faults.push(kind),
    revoke: name => { grants.get(actors[name].userId).revoked = true; },
    setRole: (name, role) => { grants.get(actors[name].userId).role = role; },
    expire: name => sessions.delete(`session-${name}`),
    rotateCsrf: name => { const session = sessions.get(`session-${name}`); session.csrf = `csrf-${name}-${++session.rotation}`; },
  };
}

module.exports = { createAdminReviewFixture, instant };
