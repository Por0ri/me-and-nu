const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const test = require('node:test'), assert = require('node:assert/strict'), ts = require('typescript');
const root = path.resolve(__dirname, '..');
const NOW = '2026-11-21T12:00:00Z';
const days = { '7d': 7, '30d': 30, '3m': 90, '6m': 180 };
const start = period => new Date(Date.parse(NOW) - days[period] * 86400000).toISOString();
const clone = value => JSON.parse(JSON.stringify(value));
const metric = (value = 0, extra = {}) => ({ availability: 'available', reason: null, value, unit: 'count', definition: '테스트 집계', numerator: null, denominator: null, ...extra });
const unknown = (unit = 'count') => metric(null, { availability: 'unavailable', reason: 'not_collected', unit });
const ranking = () => ({ availability: 'available', reason: null, definition: '실행된 기록 TOP5', items: [] });
const distribution = () => ({ availability: 'unavailable', reason: 'not_collected', definition: '기기 정보', sampleSize: null, items: [] });
const meta = (period = '7d') => ({ generatedAt: NOW, window: { period, startAt: start(period), endAt: NOW, timezone: 'UTC' }, eventDataSource: 'local_demo', scope: 'connected_database', limitations: ['shared_database_not_environment_isolated'] });
const usage = () => ({ activeUsers: metric(0, { availability: 'partial', reason: 'observed_activity_only' }), contentReadSegments: metric(0), signupFeedObservationRate: unknown('percent') });
const attention = (kind = 'all', page = 1) => ({ generatedAt: NOW, availability: kind === 'notice' ? 'unavailable' : kind === 'all' ? 'partial' : 'available', reason: ['all', 'notice'].includes(kind) ? 'notice_workflow_not_implemented' : null,
  knownTotal: 0, reports: 0, inquiries: 0, scheduledNotices: unknown(), page, pageSize: 5, items: [] });
const conditions = () => ({ evaluationData: 'isolated paired sample', datasetSha256: 'a'.repeat(64), inputConditions: 'same inputs', aggregationUnit: 'series', commonRules: 'K10 same candidates', targetPeriod: 'held-out interval', groundTruth: 'one series', candidatePolicy: 'fixed', recordSource: 'test exporter', cohortInterpretation: 'input_scenarios' });
const performance = (period = '7d', audience = 'all', available = false) => {
  const data = { availability: available ? 'available' : 'unavailable', reason: available ? null : 'not_configured', audience, period, periodStart: start(period), periodEnd: NOW,
    timeBasis: 'evaluation_completed_at', source: 'offline_evaluation', metricUnit: 'ratio', k: 10, groundTruthPerCase: 1,
    evaluationId: null, evaluatedAt: null, baselineModelId: null, currentModelId: null, conditions: null, cohorts: [], models: [] };
  if (!available) return data;
  const selected = audience === 'all' ? ['existing', 'new'] : [audience];
  Object.assign(data, { evaluationId: 'paired-test', evaluatedAt: '2026-11-21T00:00:00+09:00', baselineModelId: 'popular', currentModelId: 'v05', conditions: conditions(),
    cohorts: selected.map(audience => ({ audience, definition: `${audience} input scenario`, caseSetSha256: 'b'.repeat(64), sampleSize: 4 })),
    models: ['v05', 'popular'].map(modelId => ({ modelId, label: modelId, version: 'v1', scores: selected.map(audience => ({ audience, sampleSize: 4,
      ndcgAt10: audience === 'new' ? 0 : modelId === 'v05' ? .5 : .25, hitRateAt10: audience === 'new' ? 0 : modelId === 'v05' ? .75 : .5,
      ndcgDeltaVsBaseline: audience === 'new' || modelId === 'popular' ? 0 : .25, hitRateDeltaVsBaseline: audience === 'new' || modelId === 'popular' ? 0 : .25 })) })) });
  return data;
};
const overview = (period = '7d') => ({ meta: meta(period), usage: usage(), funnel: [{ key: 'accountCreated', label: '계정 생성', metric: metric() }], preferenceDistribution: distribution(), deviceDistribution: distribution(),
  topContents: ranking(), popularSearches: ranking(), zeroResultSearches: ranking(), returningUserRate: unknown('percent'), averageSessionDuration: unknown('seconds') });
const dashboard = (period = '7d') => ({ meta: meta(period), usage: usage(), topContents: ranking(), preferenceDistribution: distribution(),
  generation: { windowStartAt: '2026-11-20T12:00:00Z', windowEndAt: NOW, availability: 'partial', reason: 'importer_does_not_record_all_live_generation_attempts', definition: '기록된 작업', succeeded: 0, inProgress: 0, failed: 0, cancelled: 0 },
  attention: attention(), todayNotices: unknown(), recommendationPerformance: performance(period) });

function runtime(respond) {
  const modules = new Map(), calls = [], browser = new EventTarget();
  const context = vm.createContext({ URL, URLSearchParams, Headers, Response, AbortController, Event, window: browser,
    process: { env: { NEXT_PUBLIC_API_BASE_URL: 'http://localhost:8196' } },
    fetch: async (url, init) => { calls.push({ url, init }); const value = await respond(new URL(url), init); return value instanceof Response ? value : new Response(JSON.stringify(value)); } });
  const load = file => {
    file = file.replaceAll('\\', '/'); if (modules.has(file)) return modules.get(file).exports;
    const mod = { exports: {} }; modules.set(file, mod);
    const code = ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
    const localRequire = name => load((name.startsWith('@/') ? name.slice(2) : path.join(path.dirname(file), name)) + '.ts');
    vm.runInContext(`(function(module,exports,require){${code}\n})`, context)(mod, mod.exports, localRequire);
    return mod.exports;
  };
  return { api: load('lib/admin-statistics-api.ts'), calls, browser };
}

test('four GET transports use server cookies, no cache, cancellation and exact query contracts', async () => {
  const env = runtime(url => url.pathname.endsWith('/overview') ? overview(url.searchParams.get('period')) : url.pathname.endsWith('/attention') ? attention(url.searchParams.get('kind'), Number(url.searchParams.get('page'))) :
    url.pathname.endsWith('/recommendation-performance') ? performance(url.searchParams.get('period'), url.searchParams.get('audience')) : dashboard(url.searchParams.get('period')));
  const signal = new AbortController().signal;
  assert.equal((await env.api.loadAdminDashboard('30d', signal)).usage.activeUsers.value, 0);
  assert.equal((await env.api.loadAdminOverview('3m', signal)).averageSessionDuration.value, null);
  assert.equal((await env.api.loadAdminAttention({ kind: 'report', page: 2, pageSize: 5 }, signal)).page, 2);
  assert.equal((await env.api.loadRecommendationPerformance({ period: '6m', audience: 'new' }, signal)).reason, 'not_configured');
  assert.deepEqual(env.calls.map(c => new URL(c.url).pathname), ['/api/v1/admin/dashboard', '/api/v1/admin/analytics/overview', '/api/v1/admin/dashboard/attention', '/api/v1/admin/analytics/recommendation-performance']);
  for (const { init } of env.calls) { assert.equal(init.method ?? 'GET', 'GET'); assert.equal(init.credentials, 'include'); assert.equal(init.cache, 'no-store'); assert.equal(init.signal, signal); assert.equal(init.body, undefined); }
  assert.equal(new URL(env.calls[2].url).search, '?kind=report&page=2&pageSize=5');
});

test('available paired model scores preserve real zero and never pool the scenarios', async () => {
  for (const audience of ['all', 'existing', 'new']) {
    const expected = performance('7d', audience, true), env = runtime(() => expected);
    const actual = await env.api.loadRecommendationPerformance({ period: '7d', audience });
    assert.deepEqual(clone(actual), expected);
    if (audience !== 'existing') assert.equal(actual.models[0].scores.find(s => s.audience === 'new').ndcgAt10, 0);
  }
});

test('measured preference percentages are checked against their common denominator', async () => {
  const value = overview(); value.preferenceDistribution = { availability: 'partial', reason: 'current_answers_not_historical_snapshot', definition: '현재 답변', sampleSize: 3,
    items: [{ key: 'ost', label: 'OST', count: 1, percentage: 33.3333 }, { key: 'other', label: '기타', count: 2, percentage: 66.6667 }] };
  assert.equal((await runtime(() => value).api.loadAdminOverview('7d')).preferenceDistribution.sampleSize, 3);
  value.preferenceDistribution.items[0].percentage = 70;
  await assert.rejects(runtime(() => value).api.loadAdminOverview('7d'), { code: 'INVALID_ADMIN_RESPONSE' });
});

test('query values are validated before any request, including prototype names and bounds', async () => {
  const env = runtime(() => { throw Error('must not fetch'); });
  for (const period of ['today', '__proto__', 'constructor']) await assert.rejects(env.api.loadAdminDashboard(period), { status: 422 });
  await assert.rejects(env.api.loadRecommendationPerformance({ period: '7d', audience: 'admin' }), { status: 422 });
  for (const query of [{ kind: 'all', page: 0, pageSize: 5 }, { kind: 'all', page: 100001, pageSize: 5 }, { kind: 'all', page: 1, pageSize: 101 }, { kind: 'delete', page: 1, pageSize: 5 }]) await assert.rejects(env.api.loadAdminAttention(query), { status: 422 });
  assert.equal(env.calls.length, 0);
});

test('a stale response for a different period, audience or page is rejected', async () => {
  await assert.rejects(runtime(() => dashboard('7d')).api.loadAdminDashboard('30d'), { code: 'INVALID_ADMIN_RESPONSE' });
  await assert.rejects(runtime(() => overview('7d')).api.loadAdminOverview('6m'), { code: 'INVALID_ADMIN_RESPONSE' });
  await assert.rejects(runtime(() => performance('7d', 'all')).api.loadRecommendationPerformance({ period: '7d', audience: 'new' }), { code: 'INVALID_ADMIN_RESPONSE' });
  await assert.rejects(runtime(() => attention('report', 1)).api.loadAdminAttention({ kind: 'report', page: 2, pageSize: 5 }), { code: 'INVALID_ADMIN_RESPONSE' });
});

test('malformed metric and aggregate responses fail closed rather than becoming zero', async () => {
  const mutations = [v => v.usage.activeUsers.value = null, v => v.usage.activeUsers.value = -1, v => v.usage.activeUsers.value = .5,
    v => v.usage.signupFeedObservationRate.value = 0, v => v.averageSessionDuration = null, v => v.averageSessionDuration.unit = 'percent',
    v => v.returningUserRate.unit = 'count', v => v.funnel.push(v.funnel[0]),
    v => v.meta.window.startAt = '2026-11-14T12:00:00', v => v.meta.window.startAt = '2026-11-13T12:00:00Z', v => v.meta.scope = 'Checking',
    v => v.topContents.items = [{ rank: 2, label: 'wrong rank', count: 0, contentId: null }],
    v => v.topContents.items = [{ rank: 1, label: 'negative count', count: -1, contentId: null }],
    v => v.preferenceDistribution = { ...distribution(), availability: 'available', reason: null }];
  for (const mutate of mutations) { const value = overview(); mutate(value); await assert.rejects(runtime(() => value).api.loadAdminOverview('7d'), { code: 'INVALID_ADMIN_RESPONSE' }); }
  const badGeneration = dashboard(); badGeneration.generation.failed = null;
  await assert.rejects(runtime(() => badGeneration).api.loadAdminDashboard('7d'), { code: 'INVALID_ADMIN_RESPONSE' });
});

test('inconsistent evaluation comparisons never reach the chart', async () => {
  const mutations = [v => v.models[0].scores[0].ndcgAt10 = 1.1, v => v.models[0].scores[0].hitRateAt10 = null,
    v => v.models[0].scores[0].sampleSize = 10, v => v.models[0].scores[0].ndcgDeltaVsBaseline = 0,
    v => v.models[0].scores[0].ndcgAt10 = .8, v => v.models[0].scores[1].audience = 'existing',
    v => v.models.push(v.models[0]), v => v.cohorts.push(v.cohorts[0]), v => v.models[1].modelId = 'missing-base',
    v => v.evaluatedAt = NOW, v => v.conditions = null, v => v.source = 'online', v => v.k = 20,
    v => v.models[0].scores[0].hitRateDeltaVsBaseline = .3, v => v.cohorts[0].caseSetSha256 = 'not-a-digest'];
  for (const mutate of mutations) { const value = performance('7d', 'all', true); mutate(value); await assert.rejects(runtime(() => value).api.loadRecommendationPerformance({ period: '7d', audience: 'all' }), { code: 'INVALID_ADMIN_RESPONSE' }); }
  const stale = performance(); stale.models = performance('7d', 'all', true).models;
  await assert.rejects(runtime(() => stale).api.loadRecommendationPerformance({ period: '7d', audience: 'all' }), { code: 'INVALID_ADMIN_RESPONSE' });
});

test('attention counts are partial-known totals and filtered items must match the query', async () => {
  const value = attention('report'); value.reports = value.knownTotal = 1;
  value.items = [{ kind: 'report', recordId: 1, status: 'received', category: 'wrong_info', createdAt: NOW, contentId: 2 }];
  assert.equal((await runtime(() => value).api.loadAdminAttention({ kind: 'report', page: 1, pageSize: 5 })).items.length, 1);
  value.items[0].kind = 'inquiry';
  await assert.rejects(runtime(() => value).api.loadAdminAttention({ kind: 'report', page: 1, pageSize: 5 }), { code: 'INVALID_ADMIN_RESPONSE' });
  const notices = await runtime(() => attention('notice')).api.loadAdminAttention({ kind: 'notice', page: 1, pageSize: 5 });
  assert.equal(notices.availability, 'unavailable'); assert.equal(notices.scheduledNotices.value, null);
});

test('HTTP failures retain 401/403/503 semantics; wrong success status is not accepted', async () => {
  for (const [status, code] of [[401, 'AUTH_REQUIRED'], [403, 'ADMIN_REQUIRED'], [503, 'ADMIN_ANALYTICS_UNAVAILABLE']]) {
    const env = runtime(() => new Response(JSON.stringify({ code, message: 'test error' }), { status }));
    let unauthorized = 0; env.browser.addEventListener('api:unauthorized', () => unauthorized++);
    await assert.rejects(env.api.loadAdminDashboard('7d'), { status, code });
    assert.equal(unauthorized, status === 401 ? 1 : 0);
  }
  await assert.rejects(runtime(() => new Response(null, { status: 204 })).api.loadAdminDashboard('7d'), { code: 'INVALID_RESPONSE' });
});

test('cancelled reads cannot publish data or expire a newer session through late 401', async () => {
  const first = new AbortController(); first.abort();
  const idle = runtime(() => dashboard());
  await assert.rejects(idle.api.loadAdminDashboard('7d', first.signal), { name: 'AbortError' }); assert.equal(idle.calls.length, 0);
  const late = new AbortController();
  const env = runtime(() => { late.abort(); return new Response(JSON.stringify({ code: 'AUTH_REQUIRED' }), { status: 401 }); });
  let unauthorized = 0; env.browser.addEventListener('api:unauthorized', () => unauthorized++);
  await assert.rejects(env.api.loadAdminOverview('7d', late.signal), { name: 'AbortError' }); assert.equal(unauthorized, 0);
});
