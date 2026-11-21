// Real FE -> HTTP -> auth/permissions -> SQL aggregates. Synthetic local DB only.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const { chromium, request } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const base = process.argv[2] || 'http://127.0.0.1:3220';
const output = process.argv[3] || path.resolve(__dirname, '../../.preview-pivot/admin-analytics-integration/results');
const api = process.env.ADMIN_ANALYTICS_FIXTURE_URL || 'http://127.0.0.1:8198';
const local = url => url.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(url.hostname);
assert.ok(local(new URL(base)) && local(new URL(api)), 'Only loopback fixture URLs are allowed');
assert.equal(new URL(base).hostname, new URL(api).hostname, 'Use the same host for cookie/CORS verification');
const results = [];
let control;
async function fixture(mode = 'normal', extra = {}) {
  const response = await control.post(`${api}/__test__/control`, { headers: { 'X-Fixture-Key': 'isolated-analytics-browser-tests' }, data: { mode, ...extra } });
  assert.equal(response.status(), 200); assert.equal((await response.json()).synthetic, true);
}
async function session(browser, width = 1728, role = 'viewer') {
  const context = await browser.newContext({ viewport: { width, height: 1117 }, serviceWorkers: 'block' });
  await context.addCookies([{ name: 'menu_session', value: `analytics-fixture-${role}`, domain: new URL(base).hostname, path: '/', httpOnly: true, secure: false, sameSite: 'Lax' }]);
  const page = await context.newPage(), errors = [], forbiddenTraffic = [], requests = [], responses = [];
  page.setDefaultTimeout(20000); page.setDefaultNavigationTimeout(60000);
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', async req => {
    const url = new URL(req.url());
    if (url.pathname.startsWith('/api/')) requests.push({ method: req.method(), path: url.pathname, query: url.search, cookiePresent: ((await req.allHeaders()).cookie || '').includes('menu_session=analytics-fixture-') });
  });
  page.on('response', response => {
    const url = new URL(response.url());
    if (url.pathname.startsWith('/api/')) responses.push({ path: url.pathname, status: response.status(), cacheControl: response.headers()['cache-control'], cors: response.headers()['access-control-allow-origin'] });
  });
  await context.route('**/*', route => {
    const req = route.request(), url = new URL(req.url());
    if (!local(url) || !['GET', 'HEAD', 'OPTIONS'].includes(req.method())) { forbiddenTraffic.push(`${req.method()} ${url.pathname}`); return route.abort(); }
    return route.continue(); // Never fulfill or alter an API response.
  });
  const clean = () => { assert.deepEqual(errors, []); assert.deepEqual(forbiddenTraffic, []); };
  const shot = async name => {
    await page.evaluate(() => document.fonts.ready);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `${name}: document overflow`);
    for (const img of await page.locator('img:visible').all()) assert.equal(await img.evaluate(async node => { await node.decode(); return node.naturalWidth > 0; }), true, 'broken image');
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: path.join(output, `${name}-${width}.png`), fullPage: true, animations: 'disabled' });
  };
  return { context, page, clean, shot, requests, responses };
}
const dashboardReady = page => page.locator('[data-admin-dashboard][data-admin-state="ready"]').waitFor();
const performanceReady = page => page.locator('[data-analytics-view="performance"][data-performance-state="ready"]').waitFor();
const expectMetric = async (page, name, value) => assert.equal(await page.getByRole('region', { name, exact: true }).locator('strong').first().innerText(), value, name);
async function period(page, label) { await page.getByRole('button', { name: /^조회 기간:/ }).click(); await page.getByRole('menuitemradio', { name: label, exact: true }).click(); }
const focusRefresh = page => page.evaluate(() => window.dispatchEvent(new Event('focus')));
const waitResponse = (page, pathname, query = '') => page.waitForResponse(response => { const url = new URL(response.url()); return url.pathname === pathname && (!query || url.search.includes(query)) && response.status() === 200; });
async function chartMatchesHttp(context, page, audience = 'all', selectedMetric = 'ndcg', selectedPeriod = '7d') {
  const response = await context.request.get(`${api}/api/v1/admin/analytics/recommendation-performance?period=${selectedPeriod}&audience=${audience}`);
  assert.equal(response.status(), 200); const data = await response.json(); assert.equal(data.availability, 'available');
  const chart = page.locator(`[data-performance-chart="${selectedMetric}"]`); await chart.waitFor();
  const scores = data.models.flatMap(model => model.scores.map(score => ({ modelId: model.modelId, ...score })));
  assert.equal(await chart.locator('[data-model-id][data-audience]').count(), scores.length);
  for (const score of scores) assert.equal(Number(await chart.locator(`[data-model-id="${score.modelId}"][data-audience="${score.audience}"]`).getAttribute('data-value')), score[selectedMetric === 'ndcg' ? 'ndcgAt10' : 'hitRateAt10']);
  return data;
}
async function happy(browser, width) {
  await fixture(); const s = await session(browser, width), { page, context } = s;
  try {
    console.log(`Real HTTP analytics: ${width}px`);
    await page.goto(`${base}/admin/dashboard`); await dashboardReady(page);
    await expectMetric(page, '이용 회원', '4'); await expectMetric(page, '콘텐츠 열람 구간 수', '21'); await expectMetric(page, '온보딩 완료 후 피드 노출 관측률', '100%');
    assert.match(await page.locator('main').innerText(), /통합 검증 콘텐츠 1/);
    assert.doesNotMatch(await page.locator('main').innerText(), /PRIVATE_REPORT_TEXT|PRIVATE_INQUIRY_TEXT/);
    const attention = page.getByRole('region', { name: '지금 확인이 필요한 항목', exact: true }), filters = attention.getByRole('group', { name: '확인 항목 분류' });
    for (const [label, count] of [['전체', 9], ['신고', 7], ['문의', 2]]) assert.match(await filters.getByRole('button', { name: new RegExp(`^${label}`) }).innerText(), new RegExp(String(count)));
    await s.shot('dashboard-real');
    let response = waitResponse(page, '/api/v1/admin/dashboard/attention', 'page=2');
    await attention.getByRole('button', { name: '다음 페이지', exact: true }).click();
    const second = await (await response).json(); assert.equal(second.page, 2); assert.equal(second.items.length, 4); await dashboardReady(page);
    for (const [label, kind, count] of [['신고', 'report', 7], ['문의', 'inquiry', 2], ['예약 공지', 'notice', 0], ['전체', 'all', 9]]) {
      response = waitResponse(page, kind === 'all' ? '/api/v1/admin/dashboard' : '/api/v1/admin/dashboard/attention', kind === 'all' ? '' : `kind=${kind}`);
      const button = filters.getByRole('button', { name: new RegExp(`^${label}`) }); await button.click();
      const body = await (await response).json(), data = kind === 'all' ? body.attention : body;
      await dashboardReady(page); assert.equal(data.knownTotal, count); assert.equal(data.page, 1); assert.equal(await button.getAttribute('aria-pressed'), 'true');
      if (kind === 'notice') { assert.equal(data.availability, 'unavailable'); await attention.getByText('—', { exact: true }).first().waitFor(); assert.equal(await attention.getByRole('button', { name: '다음 페이지', exact: true }).isDisabled(), true); }
    }
    await page.getByRole('link', { name: /^추천 성능 상세 보기/ }).click(); await performanceReady(page);
    assert.equal(new URL(page.url()).searchParams.get('view'), 'performance'); const evaluation = await chartMatchesHttp(context, page);
    assert.equal(await page.locator('[data-model-id="v05"][data-audience="new"]').getAttribute('data-value'), '0');
    assert.match(await page.locator('main').innerText(), /실제 가입 집단으로 해석하거나 두 표본을 합산하지 않습니다/); assert.match(await page.locator('main').innerText(), /0\.5436/);
    await s.shot('performance-ndcg-real');
    await page.getByRole('group', { name: '비교할 추천 성능 지표' }).getByRole('button', { name: 'Hit Rate@10', exact: true }).click();
    await chartMatchesHttp(context, page, 'all', 'hitRate'); assert.match(await page.locator('[data-performance-chart="hitRate"]').innerText(), /66\.7%/); await s.shot('performance-hit-rate-real');
    await page.getByLabel('추천 성능 사용자 범위').selectOption('existing'); await page.waitForURL(url => url.searchParams.get('audience') === 'existing'); await performanceReady(page); await chartMatchesHttp(context, page, 'existing', 'hitRate');
    await page.getByLabel('추천 성능 사용자 범위').selectOption('new'); await page.waitForURL(url => url.searchParams.get('audience') === 'new'); await performanceReady(page); await chartMatchesHttp(context, page, 'new', 'hitRate');
    await page.reload(); await performanceReady(page); assert.equal(await page.getByLabel('추천 성능 사용자 범위').inputValue(), 'new'); await chartMatchesHttp(context, page, 'new');
    const table = page.getByRole('region', { name: '추천 성능 상세 수치 표, 가로 스크롤 가능' });
    if (width === 390) { assert.equal(await table.evaluate(node => node.scrollWidth > node.clientWidth), true); await table.focus(); await page.keyboard.press('ArrowRight'); await page.waitForFunction(() => document.querySelector('[aria-label="추천 성능 상세 수치 표, 가로 스크롤 가능"]').scrollLeft > 0); }
    const detail = page.locator('summary', { hasText: '상세 수치 보기' }); await detail.focus(); await page.keyboard.press('Enter'); assert.equal(await detail.locator('..').getAttribute('open'), null); await detail.click(); await s.shot('performance-new-real');
    await period(page, '최근 30일'); await page.waitForURL(url => url.searchParams.get('period') === '30d'); await performanceReady(page); await chartMatchesHttp(context, page, 'new', 'ndcg', '30d');
    await page.locator('main').getByRole('link', { name: '데이터 분석', exact: true }).click();
    await page.getByRole('region', { name: '인기 검색 키워드 TOP5', exact: true }).getByText('검색 검증', { exact: true }).waitFor();
    assert.equal(new URL(page.url()).searchParams.get('period'), '30d'); assert.equal(new URL(page.url()).searchParams.get('audience'), 'new');
    assert.match(await page.getByRole('region', { name: '검색 결과 없는 검색어 TOP5', exact: true }).innerText(), /결과 없는 검색/);
    assert.equal(await page.getByRole('region', { name: '이용 기기 비중', exact: true }).getAttribute('data-statistics'), 'unavailable'); await expectMetric(page, '평균 세션 지속 시간', '—'); await s.shot('overview-real');
    await page.goBack(); await performanceReady(page); await page.goForward(); await page.getByRole('region', { name: '인기 검색 키워드 TOP5', exact: true }).getByText('검색 검증', { exact: true }).waitFor();
    s.clean();
    for (const endpoint of ['/api/v1/auth/session', '/api/v1/admin/dashboard', '/api/v1/admin/dashboard/attention', '/api/v1/admin/analytics/overview', '/api/v1/admin/analytics/recommendation-performance']) {
      assert.ok(s.requests.some(r => r.path === endpoint && r.cookiePresent), `${endpoint}: real session cookie`);
      assert.ok(s.responses.some(r => r.path === endpoint && r.status === 200 && r.cacheControl?.includes('no-store')), `${endpoint}: private no-store response`);
    }
    assert.ok(s.responses.some(r => r.path.startsWith('/api/v1/admin/') && r.cors === base), 'actual credentialed CORS');
    results.push({ width, passed: true, evaluation: evaluation.evaluationId, checks: ['real cookie/CORS/live authorization/SQL API responses', 'usage/source scope/attention pagination and filters', 'both cohort NDCG and Hit Rate graphs equal HTTP response including zero', 'query reload/back/forward, disclosures and mobile table scroll', 'unavailable metrics remain unknown; no private report/inquiry text', 'no write/external traffic/browser errors/viewport overflow'], requests: s.requests, responses: s.responses });
  } catch (error) { await page.screenshot({ path: path.join(output, `failure-${width}.png`), fullPage: true, timeout: 5000 }).catch(() => {}); throw new Error(`${width}px ${page.url()}: ${error.message}`, { cause: error }); }
  finally { await context.close(); }
}
async function boundaries(browser) {
  const checks = [];
  for (const role of ['viewer', 'operator', 'admin', 'member', 'anonymous']) {
    await fixture(); const s = await session(browser, 1728, role);
    try {
      const response = await s.context.request.get(`${api}/api/v1/admin/dashboard?period=7d`); assert.equal(response.status(), role === 'member' ? 403 : role === 'anonymous' ? 401 : 200);
      await s.page.goto(`${base}/admin/dashboard`); const state = role === 'member' ? 'forbidden' : role === 'anonymous' ? 'login-required' : 'ready';
      await s.page.locator(`[data-admin-dashboard][data-admin-state="${state}"]`).waitFor();
      if (state !== 'ready') assert.equal(await s.page.getByRole('region', { name: '이용 회원', exact: true }).count(), 0);
      s.clean(); checks.push(`${role}: ${response.status()} and ${state}`);
    } finally { await s.context.close(); }
  }
  await fixture(); const s = await session(browser), { page } = s;
  try {
    await page.goto(`${base}/admin/dashboard`); await dashboardReady(page);
    await fixture('normal', { revoked: true }); await focusRefresh(page); await page.locator('[data-admin-dashboard][data-admin-state="forbidden"]').waitFor();
    assert.equal(await page.getByRole('region', { name: '이용 회원', exact: true }).count(), 0); checks.push('grant revocation on focus removes previously displayed counts');
    await fixture(); await page.getByRole('button', { name: '다시 확인', exact: true }).click(); await dashboardReady(page);
    await fixture('db_failure'); await focusRefresh(page); await page.locator('[data-admin-dashboard][data-admin-state="error"]').waitFor();
    assert.ok(s.responses.some(r => r.path === '/api/v1/admin/dashboard' && r.status === 503)); assert.equal(await page.getByRole('region', { name: '이용 회원', exact: true }).count(), 0); await s.shot('dashboard-error');
    await fixture(); await page.getByRole('button', { name: '다시 시도', exact: true }).click(); await dashboardReady(page); await expectMetric(page, '이용 회원', '4'); checks.push('real SQL failure returns503; explicit retry recovers without false zero');
    await fixture('zero'); await focusRefresh(page); await dashboardReady(page); await expectMetric(page, '이용 회원', '0'); await expectMetric(page, '콘텐츠 열람 구간 수', '0'); await expectMetric(page, '온보딩 완료 후 피드 노출 관측률', '—'); await s.shot('dashboard-zero'); checks.push('observed zero counts differ from unavailable empty denominator');
    await fixture(); await focusRefresh(page); await dashboardReady(page);
    await page.getByRole('button', { name: '다음 페이지', exact: true }).click(); await dashboardReady(page);
    await page.waitForFunction(() => document.querySelector('[aria-label="확인 항목 페이지"]').textContent.includes('2 / 2'));
    await fixture('shrinking_queue'); await focusRefresh(page);
    await page.waitForFunction(() => document.querySelector('[aria-label="확인 항목 페이지"]')?.textContent.includes('1 / 1'));
    await dashboardReady(page); assert.equal(await page.locator('[data-attention-item]').count(), 1); checks.push('shrinking backlog clamps and refetches out-of-range page');
    await fixture('evaluation_missing'); await page.goto(`${base}/admin/analytics?view=performance`); await performanceReady(page); await page.locator('[data-performance-unavailable]').waitFor(); assert.equal(await page.locator('[data-performance-chart]').count(), 0); await s.shot('performance-unavailable');
    await fixture(); await page.getByRole('button', { name: '다시 확인', exact: true }).click(); await performanceReady(page); await chartMatchesHttp(s.context, page); checks.push('missing evaluation is unavailable; real artifact retry restores charts');
    await fixture('normal', { delay_period: '30d' });
    const delayed = page.waitForRequest(req => new URL(req.url()).pathname.endsWith('/recommendation-performance') && new URL(req.url()).searchParams.get('period') === '30d');
    await period(page, '최근 30일'); await delayed; await period(page, '최근 7일'); await performanceReady(page); await chartMatchesHttp(s.context, page);
    await page.waitForTimeout(2300); // Beyond delayed HTTP completion, to detect stale-response overwrite.
    assert.equal(new URL(page.url()).searchParams.get('period'), null); await chartMatchesHttp(s.context, page); checks.push('rapid period changes ignore stale delayed HTTP response');
    s.clean(); results.push({ suite: 'authorization-and-failures', passed: true, checks, responses: s.responses });
  } finally { await s.context.close(); await fixture(); }
}
(async () => {
  fs.mkdirSync(output, { recursive: true }); control = await request.newContext();
  const ready = await control.get(`${api}/__test__/ready`); assert.equal(ready.status(), 200); assert.deepEqual(await ready.json(), { synthetic: true, database: 'private-in-memory-sqlite', api: 'real-registered-routes' });
  const browser = await chromium.launch({ headless: true, channel: process.env.PIVOT_BROWSER_CHANNEL || 'msedge' });
  try { for (const width of process.argv[4] ? [Number(process.argv[4])] : [1728, 390]) await happy(browser, width); await boundaries(browser); }
  finally { await browser.close(); await control.dispose(); fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(results, null, 2)); }
  console.log(JSON.stringify(results.map(({ width, suite, passed, checks }) => ({ width, suite, passed, checks })), null, 2));
})().catch(error => { console.error(error); process.exitCode = 1; });
