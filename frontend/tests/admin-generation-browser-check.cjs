// Local Next.js UI with synthetic intercepted API responses; external requests
// are blocked. This never authenticates to a service or changes a queue/DB.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const { chromium } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const { generationFixture, unavailableFixture } = require('./helpers/generation-fixture.cjs');
const base = process.argv[2] || 'http://127.0.0.1:3296';
const output = process.argv[3] || path.resolve(__dirname, '../../out/admin-generation-browser');
const origin = new URL(base);
assert.ok(origin.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(origin.hostname), 'Local test UI only');
const NOW = '2026-11-27T04:00:02Z';
function dashboard(period = '7d') {
  const span = { '7d': 7, '30d': 30, '3m': 90, '6m': 180 }[period];
  const start = new Date(Date.parse(NOW) - span * 86400000).toISOString();
  const metric = value => ({ availability: value === null ? 'unavailable' : 'available', reason: value === null ? 'not_collected' : null,
    value, unit: 'count', definition: 'Synthetic browser fixture', numerator: null, denominator: null });
  return {
    meta: { generatedAt: NOW, window: { period, startAt: start, endAt: NOW, timezone: 'UTC' }, eventDataSource: 'automated_test', scope: 'connected_database', limitations: [] },
    usage: { activeUsers: metric(4), contentReadSegments: metric(21), signupFeedObservationRate: { ...metric(null), unit: 'percent' } },
    topContents: { availability: 'available', reason: null, definition: 'Synthetic ranking', items: [] },
    preferenceDistribution: { availability: 'unavailable', reason: 'not_collected', definition: 'Synthetic distribution', sampleSize: null, items: [] },
    generation: { availability: 'partial', reason: 'importer_does_not_record_all_live_generation_attempts', definition: 'Stored service DB only',
      windowStartAt: '2026-11-26T04:00:02Z', windowEndAt: NOW, succeeded: 1, inProgress: 0, failed: 0, cancelled: 0 },
    attention: { generatedAt: NOW, availability: 'partial', reason: 'notice_workflow_not_implemented', knownTotal: 0, reports: 0,
      inquiries: 0, scheduledNotices: metric(null), page: 1, pageSize: 5, items: [] },
    todayNotices: metric(null), recommendationPerformance: { availability: 'unavailable', reason: 'not_configured', audience: 'all', period,
      periodStart: start, periodEnd: NOW, timeBasis: 'evaluation_completed_at', source: 'offline_evaluation', metricUnit: 'ratio', k: 10,
      groundTruthPerCase: 1, evaluationId: null, evaluatedAt: null, baselineModelId: null, currentModelId: null, conditions: null, cohorts: [], models: [] },
  };
}
async function check(browser, width) {
  const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1117 }, serviceWorkers: 'block' });
  const page = await context.newPage(), requests = [], errors = [], outside = [];
  let mode = 'normal';
  page.on('pageerror', error => errors.push(error.message));
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.pathname.startsWith('/api/v1/')) {
      assert.ok(['api.me-nu.kr', 'localhost', '127.0.0.1'].includes(url.hostname), 'Known API fixture origin only');
      requests.push({ path: url.pathname, method: request.method() });
      assert.equal(request.method(), 'GET', 'Read-only UI issues no mutation requests');
      let status = 200, value;
      if (url.pathname === '/api/v1/auth/session') value = { authenticated: true, sessionState: 'active', user: { id: 999001, accountType: 'user' }, onboardingCompleted: true, csrfToken: 'synthetic-only' };
      else if (url.pathname === '/api/v1/admin/dashboard') value = dashboard(url.searchParams.get('period'));
      else if (url.pathname === '/api/v1/admin/generation') {
        value = generationFixture();
        if (mode === 'stale') Object.assign(value, { availability: 'stale', reason: 'snapshot_stale', ageSeconds: 300 });
        if (mode === 'mismatch') Object.assign(value, { availability: 'partial', reason: 'policy_day_mismatch', policyDate: '2026-11-26', daily: null, jobs: null });
        if (mode === 'unavailable') value = unavailableFixture();
        if (mode === 'forbidden') { status = 403; value = { code: 'ADMIN_FORBIDDEN', message: 'Synthetic revoked permission' }; }
        if (mode === 'error') { status = 503; value = { code: 'UNAVAILABLE', message: 'Synthetic read failure' }; }
      } else { outside.push(url.pathname); return route.abort(); }
      return route.fulfill({ status, contentType: 'application/json', headers: { 'Cache-Control': 'no-store', 'Access-Control-Allow-Origin': origin.origin, 'Access-Control-Allow-Credentials': 'true' }, body: JSON.stringify(value) });
    }
    if (url.origin !== origin.origin) { outside.push(url.origin); return route.abort(); }
    return route.continue();
  });
  try {
    await page.clock.install({ time: new Date(NOW) });
    await page.goto(`${base}/admin/dashboard`);
    const panel = page.getByRole('region', { name: '자동 콘텐츠 생성 현황', exact: true });
    await panel.locator('[aria-label="생성 시도 3 / 40건"]').waitFor();
    assert.match(await panel.innerText(), /AI 검수 통과는 공개 승인이 아닙니다/);
    assert.match(await panel.innerText(), /비용 미확정 1건 · 사용량 집계 대기 1건/);
    assert.equal(await page.getByRole('region', { name: '서비스 DB 적재 이력', exact: true }).count(), 1);
    await panel.getByText('기사·정보사전 작업 상태 보기', { exact: true }).click();
    await panel.getByText('비용·기록 집계 기준 보기', { exact: true }).click();
    assert.match(await panel.innerText(), /AI 검수 반려/);
    const panelBounds = await panel.evaluate(node => ({ bottom: node.getBoundingClientRect().bottom, nextTop: node.nextElementSibling.getBoundingClientRect().top }));
    assert.ok(panelBounds.bottom <= panelBounds.nextTop, 'Expanded live panel must not overlap existing dashboard cards');
    const size = await page.evaluate(() => ({ doc: document.documentElement.scrollWidth, viewport: innerWidth }));
    assert.ok(size.doc <= size.viewport, `No horizontal overflow at ${width}px: ${JSON.stringify(size)}`);
    await page.screenshot({ path: path.join(output, `generation-${width}.png`), fullPage: true });
    const counts = name => requests.filter(row => row.path === `/api/v1/admin/${name}`).length;
    const dashboardCalls = counts('dashboard'), generationCalls = counts('generation');
    const polled = page.waitForRequest(request => new URL(request.url()).pathname === '/api/v1/admin/generation');
    await page.clock.fastForward(31000);
    await polled;
    await page.waitForFunction(() => document.querySelector('[data-admin-generation]')?.dataset.generationState === 'ready');
    assert.ok(counts('generation') > generationCalls, 'Only the live endpoint polls');
    assert.equal(counts('dashboard'), dashboardCalls, 'Polling does not rerun dashboard SQL');
    const beforeHidden = counts('generation');
    await page.evaluate(() => Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' }));
    await page.clock.fastForward(31000);
    assert.equal(counts('generation'), beforeHidden, 'Hidden tabs do not poll');
    await page.evaluate(() => { delete document.visibilityState; });
    async function refresh(next) {
      mode = next;
      await panel.getByRole('button', { name: '생성 현황 새로고침', exact: true }).click();
      await panel.locator('[role="status"]').filter({ hasText: '불러오는 중' }).waitFor({ state: 'hidden' });
    }
    await refresh('stale'); await panel.getByText('갱신 지연', { exact: true }).waitFor();
    assert.match(await panel.innerText(), /0\.04703593/);
    await refresh('mismatch'); await panel.getByText(/이전 날짜의 시도·작업 수/).waitFor();
    assert.equal(await panel.getByRole('progressbar').count(), 0);
    await refresh('unavailable'); await panel.getByText('아직 수집된 현황이 없습니다.', { exact: true }).waitFor();
    assert.doesNotMatch(await panel.innerText(), /\$0\.0000/);
    await refresh('error'); await panel.getByRole('alert').waitFor();
    assert.doesNotMatch(await panel.innerText(), /0\.04703593/);
    await refresh('normal'); await panel.locator('[aria-label="생성 시도 3 / 40건"]').waitFor();
    await refresh('forbidden'); await page.locator('[data-admin-dashboard][data-admin-state="forbidden"]').waitFor();
    assert.equal(await page.getByRole('region', { name: '이용 회원', exact: true }).count(), 0, 'Revoked live permission clears protected dashboard');
    assert.deepEqual(errors, []); assert.deepEqual(outside, []);
    return { width, passed: true, apiRequests: requests.length, mutations: 0, outsideRequests: 0, pageErrors: 0,
      checks: ['40/20 distinct AI review', 'known/unknown/unsettled cost', 'poll only new endpoint; hidden tabs pause', 'stale/midnight/unavailable/error', '403 clears dashboard', 'no horizontal overflow'] };
  } finally { await context.close(); }
}
(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: process.env.PIVOT_BROWSER_CHANNEL || 'msedge' });
  const results = [];
  try { for (const width of [1728, 390]) results.push(await check(browser, width)); }
  finally { await browser.close(); fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(results, null, 2)); }
  console.log(JSON.stringify(results));
})().catch(error => { console.error(error); process.exitCode = 1; });
