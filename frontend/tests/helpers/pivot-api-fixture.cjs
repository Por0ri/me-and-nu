const http = require('node:http');

// In-memory, loopback-only test server. Never imported by application code.
function createApiFixture({ apiOrigin = 'http://localhost:8180', frontendOrigin = 'http://localhost:3180',
  strictOnboarding = false, oauthCallbackPhase = 'active', today = '2026-10-27', topicId = 7 } = {}) {
  const requests = [];
  const state = { phase: 'anonymous', csrf: 'fixture-anonymous-csrf', cookie: 'fixture-anonymous', saved: new Set(), failNext: null, topics: true,
    onboardingCompletions: 0, onboarding: null };
  const topic = { id: topicId, code: 'anime', name: '애니메이션', subtopicIds: [19], status: 'active', deletedAt: null };
  const contents = [
    { id: 41, title: '연결 검증 콘텐츠 하나', summary: '첫 번째 콘텐츠의 공개 요약입니다.', body: '테스트 API에서 내려온 첫 번째 본문입니다.\n\n새로고침해도 저장 상태가 유지됩니다.' },
    { id: 42, title: '연결 검증 콘텐츠 둘', summary: '두 번째 콘텐츠의 공개 요약입니다.', body: '테스트 API에서 내려온 두 번째 본문입니다.' },
  ];
  const policies = [
    { type: 'terms', policyVersion: 'fixture-v1', required: true, text: '통합 테스트용 약관' },
    { type: 'privacy', policyVersion: 'fixture-v2', required: true, text: '통합 테스트용 개인정보 안내' },
    { type: 'marketing', policyVersion: 'fixture-v1', required: false, text: '통합 테스트용 선택 동의' },
  ];
  function rotate(phase) {
    state.phase = phase;
    state.cookie = `fixture-${phase}`;
    state.csrf = `fixture-${phase}-csrf`;
  }
  function session() {
    return { authenticated: state.phase !== 'anonymous', sessionState: state.phase,
      onboardingCompleted: state.phase === 'active', user: state.phase === 'active' ? { id: 12, accountType: 'user' } : null,
      csrfToken: state.csrf };
  }
  const error = (status, code, message = code) => ({ status, body: { code, message } });
  function validDate(value) {
    if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
    const date = new Date(`${value}T00:00:00Z`);
    return !Number.isNaN(date.valueOf()) && date.toISOString().slice(0, 10) === value;
  }
  function validateOnboarding(data) {
    const exactKeys = (value, keys) => value && typeof value === 'object' && !Array.isArray(value)
      && Object.keys(value).sort().join(',') === [...keys].sort().join(',');
    if (!exactKeys(data, ['nickname', 'birthDate', 'accountType', 'consents', 'topicId', 'answers'])
      || typeof data.nickname !== 'string' || !data.nickname.trim() || [...data.nickname.trim()].length > 30
      || !validDate(data.birthDate) || data.accountType !== 'user' || !Number.isSafeInteger(data.topicId) || data.topicId <= 0
      || !exactKeys(data.answers, ['q1', 'q2'])
      || !['world', 'relationships', 'ost', 'goods', 'events', 'news'].includes(data.answers.q1)
      || !['releases', 'characters', 'restock', 'events'].includes(data.answers.q2)
      || !Array.isArray(data.consents) || data.consents.some(item => !exactKeys(item, ['type', 'policyVersion', 'agreed'])
        || typeof item.type !== 'string' || typeof item.policyVersion !== 'string' || typeof item.agreed !== 'boolean')) {
      return error(422, 'VALIDATION_ERROR');
    }
    const [year, month, day] = today.split('-').map(Number);
    const [birthYear, birthMonth, birthDay] = data.birthDate.split('-').map(Number);
    const age = year - birthYear - Number(month < birthMonth || (month === birthMonth && day < birthDay));
    if (age < 14) return error(403, 'AGE_RESTRICTED');
    const types = new Set(data.consents.map(item => item.type));
    if (types.size !== data.consents.length || data.consents.some(item => !policies.some(policy => policy.type === item.type))) return error(422, 'VALIDATION_ERROR');
    if (types.size !== policies.length) return error(422, 'REQUIRED_CONSENT_MISSING');
    for (const policy of policies) {
      const consent = data.consents.find(item => item.type === policy.type);
      if (consent.policyVersion !== policy.policyVersion) return error(409, 'POLICY_VERSION_CONFLICT');
      if (policy.required && !consent.agreed) return error(422, 'REQUIRED_CONSENT_MISSING');
    }
    if (data.topicId !== topic.id || !state.topics) return error(404, 'TOPIC_NOT_FOUND');
    return null;
  }
  const cookie = () => `fixture_session=${state.cookie}; HttpOnly; SameSite=Lax; Path=/`;
  function dispatch(urlValue, { method = 'GET', headers = {}, body = null } = {}) {
    const url = new URL(urlValue, apiOrigin);
    const normalized = new Headers(headers);
    const data = typeof body === 'string' && body ? JSON.parse(body) : body;
    requests.push({ method, path: url.pathname, query: Object.fromEntries(url.searchParams), body: data,
      csrf: normalized.get('x-csrf-token'), cookie: normalized.get('cookie'), origin: normalized.get('origin') });
    if (state.failNext && (!state.failNext.method || state.failNext.method === method) && state.failNext.path === url.pathname) {
      const failure = state.failNext; state.failNext = null;
      return error(failure.status || 503, failure.code || 'TEMPORARILY_UNAVAILABLE', failure.message || '잠시 후 다시 시도해 주세요.');
    }
    if (method === 'GET' && url.pathname === '/api/v1/auth/session') return { status: 200, body: session(), headers: { 'Set-Cookie': cookie() } };
    if (method === 'GET' && url.pathname === '/api/v1/policies') return { status: 200, body: { consentItems: policies, aiNotice: '테스트', chatRetention: { days: null, status: 'undecided' } } };
    if (method === 'GET' && /^\/api\/v1\/auth\/login\/(kakao|naver)\/callback$/.test(url.pathname)) {
      rotate(oauthCallbackPhase);
      return { status: 303, headers: { 'Set-Cookie': cookie(), Location: `${frontendOrigin}/pivot/${oauthCallbackPhase === 'onboarding_pending' ? 'consent' : 'home'}` } };
    }
    if (method !== 'GET' && normalized.get('x-csrf-token') !== state.csrf) return error(403, 'CSRF_INVALID');
    if (method === 'POST' && /^\/api\/v1\/auth\/login\/(kakao|naver)$/.test(url.pathname)) {
      if (data?.purpose !== 'signin') return error(422, 'INVALID_PURPOSE');
      const provider = url.pathname.split('/').at(-1);
      const authorization = new URL(provider === 'kakao' ? 'https://kauth.kakao.com/oauth/authorize' : 'https://nid.naver.com/oauth2.0/authorize');
      authorization.search = new URLSearchParams({ response_type: 'code', client_id: 'fixture-client', state: 'fixture-state', redirect_uri: `${apiOrigin}/api/v1/auth/login/${provider}/callback` }).toString();
      return { status: 200, body: { authorizationUrl: authorization.href } };
    }
    if (method === 'POST' && url.pathname === '/api/v1/auth/logout') {
      rotate('anonymous'); return { status: 204, headers: { 'Set-Cookie': cookie() } };
    }
    if (!normalized.get('cookie')?.includes(`fixture_session=${state.cookie}`)) return error(401, 'AUTH_REQUIRED');
    if (state.phase === 'anonymous') return error(401, 'AUTH_REQUIRED');
    if (method === 'GET' && url.pathname === '/api/v1/topics') return { status: 200, body: { topics: state.topics ? [{ id: topic.id, code: topic.code, name: topic.name }] : [] } };
    if (method === 'POST' && url.pathname === '/api/v1/onboarding/complete') {
      if (strictOnboarding) {
        if (state.phase !== 'onboarding_pending') return error(409, 'ONBOARDING_ALREADY_COMPLETED');
        const invalid = validateOnboarding(data);
        if (invalid) return invalid;
      } else if (state.phase !== 'onboarding_pending' || data?.accountType !== 'user' || data?.topicId !== topic.id ||
        !data?.nickname || !data?.birthDate || !data?.answers?.q1 || !data?.answers?.q2 || 'subtopicIds' in data || 'profileImageId' in data) return error(422, 'VALIDATION_ERROR');
      state.onboardingCompletions++;
      state.onboarding = JSON.parse(JSON.stringify(data));
      rotate('active'); return { status: 201, headers: { 'Set-Cookie': cookie() }, body: { user: { id: 12, accountType: 'user' }, onboardingCompleted: true, activeTopic: topic, answers: data.answers } };
    }
    if (state.phase !== 'active') return error(403, 'ONBOARDING_REQUIRED');
    if (method === 'GET' && url.pathname === '/api/v1/me/topics') return { status: 200, body: { topics: state.topics ? [topic] : [] } };
    if (method === 'GET' && url.pathname === `/api/v1/topics/${topic.id}/feed`) return { status: 200, body: {
      selectedTopicId: topic.id, sections: [{ id: 'recommended', type: 'recommended', title: '추천 콘텐츠', subtopicId: null,
        contents: contents.map(item => ({ kind: 'content', id: item.id, topicId: topic.id, recommendationId: null, title: item.title,
          productionType: 'human', summary: item.summary, imageUrl: null, sourceName: '테스트 API', sourceUrl: 'https://example.com/fixture',
          publishedAt: null, saved: state.saved.has(item.id), savedItemId: state.saved.has(item.id) ? item.id + 100 : null,
          recommendationReason: null, seen: false, isPromotional: false, notices: [], myReaction: { liked: false, preference: null } })) }],
      nextCursor: null, emptyReason: null,
    } };
    if (method === 'GET' && url.pathname === '/api/v1/users/me/bookmarks') {
      if (url.searchParams.get('topicId') !== String(topic.id)) return error(422, 'TOPIC_REQUIRED');
      return { status: 200, body: { items: contents.filter(item => state.saved.has(item.id)).map(item => ({ savedItemId: item.id + 100,
        contentId: item.id, topicId: topic.id, title: item.title, summary: item.summary, imageUrl: null, sourceName: '테스트 API',
        tags: [], resurfaceEnabled: true })), nextCursor: null } };
    }
    const match = url.pathname.match(/^\/api\/v1\/contents\/(\d+)(\/bookmark)?$/);
    if (match) {
      const id = Number(match[1]); const content = contents.find(item => item.id === id);
      if (!content) return error(404, 'CONTENT_NOT_FOUND');
      if (method === 'GET' && !match[2]) {
        if (url.searchParams.get('topicId') !== String(topic.id)) return error(422, 'TOPIC_REQUIRED');
        return { status: 200, body: { contentId: id, topicId: topic.id, title: content.title, publisher: '테스트 API', publishedAt: null,
          sourceUrl: 'https://example.com/fixture', displayMode: 'full_body', productionType: 'human', excerpt: content.summary,
          body: content.body, contentType: 'article', imageUrl: null, subtopicIds: [19], practicalInfo: {}, notices: [], isPromotional: false,
          share: { url: 'https://example.com/fixture', text: content.title }, myReaction: { liked: false, preference: null, bookmarked: state.saved.has(id) }, channel: null } };
      }
      if (match[2] && method === 'PUT') {
        if (data?.topicId !== topic.id) return error(422, 'TOPIC_REQUIRED');
        state.saved.add(id); return { status: 200, body: { savedItemId: id + 100, contentId: id, topicId: topic.id, tags: [], resurfaceEnabled: true, savedAt: '2026-10-27T00:00:00Z' } };
      }
      if (match[2] && method === 'DELETE') {
        if (url.searchParams.get('topicId') !== String(topic.id)) return error(422, 'TOPIC_REQUIRED');
        state.saved.delete(id); return { status: 204 };
      }
    }
    return error(404, 'FIXTURE_ROUTE_NOT_FOUND');
  }
  async function listen(port = new URL(apiOrigin).port) {
    const server = http.createServer(async (req, res) => {
      res.setHeader('Access-Control-Allow-Origin', frontendOrigin);
      res.setHeader('Access-Control-Allow-Credentials', 'true');
      res.setHeader('Access-Control-Allow-Headers', 'content-type,x-csrf-token');
      res.setHeader('Access-Control-Allow-Methods', 'GET,POST,PUT,DELETE,OPTIONS');
      if (req.method === 'OPTIONS') { res.writeHead(204).end(); return; }
      let raw = ''; for await (const chunk of req) raw += chunk;
      try {
        const response = dispatch(req.url, { method: req.method, headers: req.headers, body: raw || null });
        res.writeHead(response.status, { 'Content-Type': 'application/json', ...response.headers });
        res.end(response.body === undefined ? '' : JSON.stringify(response.body));
      } catch (error) { res.writeHead(500).end(JSON.stringify({ code: 'FIXTURE_ERROR', message: error.message })); }
    });
    await new Promise((resolve, reject) => { server.once('error', reject); server.listen(Number(port), 'localhost', resolve); });
    return server;
  }
  return { state, requests, topic, contents, policies, rotate, dispatch, listen };
}
module.exports = { createApiFixture };
