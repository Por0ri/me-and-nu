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
async function chartMatchesHttp(context, page, audience = 'all', selectedMetric = 'ndcg', selectedPeriod = '7d', dataset = 'offline') {
  const response = await context.request.get(`${api}/api/v1/admin/analytics/recommendation-performance?period=${selectedPeriod}&audience=${audience}&dataset=${dataset}`);
  assert.equal(response.status(), 200); const data = await response.json(); assert.equal(data.availability, 'available');
  const chart = page.locator(`[data-performance-chart="${selectedMetric}"]`); await chart.waitFor();
  const cohorts = audience === 'all' ? ['existing', 'new'] : [audience], labels = { existing: '기존', new: '신규' };
  const ndcg = value => value.toFixed(4), hitRate = value => `${(value * 100).toFixed(1)}%`;
  const delta = value => `${value > 0 ? '+' : ''}${value.toFixed(4)}`;
  const pp = value => `${value > 0 ? '+' : ''}${(value * 100).toFixed(1)}%p`;
  const format = selectedMetric === 'ndcg' ? ndcg : hitRate;
  assert.equal(await chart.locator('[data-model-id][data-audience]').count(), data.models.length * cohorts.length);
  assert.deepEqual(await chart.locator('[data-model]').evaluateAll(nodes => nodes.map(node => node.dataset.model)), data.models.map(model => model.modelId), 'model order follows the API');
  for (const model of data.models) {
    const group = chart.locator(`[data-model="${model.modelId}"]`);
    assert.equal(await group.locator('strong').first().innerText(), model.label, 'model label follows the API');
    for (const cohort of cohorts) {
      const score = model.scores.find(item => item.audience === cohort);
      const track = group.locator(`[data-model-id="${model.modelId}"][data-audience="${cohort}"]`), bar = track.locator(':scope > span');
      const value = score?.[selectedMetric === 'ndcg' ? 'ndcgAt10' : 'hitRateAt10'];
      assert.equal(await track.getAttribute('data-value'), score ? String(value) : null);
      assert.equal(await track.locator('..').locator('strong').innerText(), score ? format(value) : '—', 'visible graph value');
      assert.equal(await track.getAttribute('aria-label'), `${model.label} ${labels[cohort]}: ${score ? format(value) : '평가 없음'}`);
      assert.equal(await bar.count(), score ? 1 : 0, 'missing cohort must not become a fabricated zero bar');
      if (score) {
        const geometry = await bar.evaluate(node => ({ percentage: Number.parseFloat(node.style.width), width: node.getBoundingClientRect().width, trackWidth: node.parentElement.getBoundingClientRect().width }));
        assert.ok(geometry.trackWidth > 0, 'graph track has visible width');
        assert.ok(Math.abs(geometry.percentage - value * 100) < .0001, `${model.modelId}/${cohort}: CSS percentage follows HTTP score`);
        assert.ok(Math.abs(geometry.width - geometry.trackWidth * value) <= 1, `${model.modelId}/${cohort}: rendered bar width follows HTTP score`);
      }
    }
  }
  const reference = data.models.find(model => model.modelId === data.currentModelId);
  const unit = dataset === 'service' ? '콘텐츠' : '시리즈';
  for (const [title, getValue] of [[`${unit} NDCG@10`, score => ndcg(score.ndcgAt10)], [`${unit} Hit Rate@10`, score => hitRate(score.hitRateAt10)], ['평가 표본', score => `${score.sampleSize.toLocaleString('ko-KR')}건`]]) {
    const rows = page.getByRole('region', { name: title, exact: true }).locator('dl > div');
    assert.equal(await rows.count(), cohorts.length);
    for (const [index, cohort] of cohorts.entries()) {
      const score = reference.scores.find(item => item.audience === cohort);
      assert.equal(await rows.nth(index).locator('dt').innerText(), labels[cohort]);
      assert.equal(await rows.nth(index).locator('dd').innerText(), score ? getValue(score) : '—', `${title}/${cohort}: summary follows reference model`);
    }
  }
  const table = page.getByRole('region', { name: '추천 성능 상세 수치 표, 가로 스크롤 가능' }), rows = table.locator('tbody tr');
  assert.equal(await rows.count(), data.models.length * cohorts.length);
  let rowIndex = 0;
  for (const model of data.models) for (const cohort of cohorts) {
    const score = model.scores.find(item => item.audience === cohort);
    const values = score ? [ndcg(score.ndcgAt10), hitRate(score.hitRateAt10), score.sampleSize.toLocaleString('ko-KR'), delta(score.ndcgDeltaVsBaseline), pp(score.hitRateDeltaVsBaseline)] : Array(5).fill('—');
    assert.deepEqual((await rows.nth(rowIndex++).locator('th, td').allTextContents()).map(text => text.trim()), [model.label, labels[cohort], ...values], 'detail table follows HTTP values and model order');
  }
  const toggle = page.getByRole('group', { name: '비교할 추천 성능 지표' });
  assert.equal(await toggle.getByRole('button', { name: 'NDCG@10', exact: true }).getAttribute('aria-pressed'), String(selectedMetric === 'ndcg'));
  assert.equal(await toggle.getByRole('button', { name: 'Hit Rate@10', exact: true }).getAttribute('aria-pressed'), String(selectedMetric === 'hitRate'));
  assert.equal(await page.getByRole('region', { name: '평가 조건', exact: true }).getByText(data.evaluationId, { exact: true }).count(), 1, 'displayed evaluation ID matches this artifact');
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
    await page.getByRole('region', { name: '작품 추천 품질', exact: true }).getByRole('link', { name: /^추천 성능 상세 보기/ }).click(); await performanceReady(page);
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
async function evaluationVariants(browser, width) {
  await fixture(); const s = await session(browser, width), { page, context } = s, checks = [];
  const refresh = async () => {
    const pending = waitResponse(page, '/api/v1/admin/analytics/recommendation-performance');
    await focusRefresh(page); await pending; await performanceReady(page);
  };
  try {
    console.log(`Real HTTP evaluation variants: ${width}px`);
    await page.goto(`${base}/admin/analytics?view=performance`); await performanceReady(page);
    const original = await chartMatchesHttp(context, page);
    await fixture('normal', { evaluation_variant: 'existing_only' }); await refresh();
    const existing = await chartMatchesHttp(context, page);
    assert.deepEqual(existing.cohorts.map(cohort => cohort.audience), ['existing']);
    await page.getByRole('group', { name: '비교할 추천 성능 지표' }).getByRole('button', { name: 'Hit Rate@10', exact: true }).click();
    await chartMatchesHttp(context, page, 'all', 'hitRate'); await s.shot('performance-existing-only');
    await page.getByLabel('추천 성능 사용자 범위').selectOption('new'); await page.waitForURL(url => url.searchParams.get('audience') === 'new'); await performanceReady(page);
    await page.locator('[data-performance-unavailable]').getByText('선택한 사용자 범위의 평가 결과가 없습니다.', { exact: true }).waitFor();
    assert.equal(await page.locator('[data-performance-chart]').count(), 0, 'unevaluated audience has no fabricated scores');
    await page.getByLabel('추천 성능 사용자 범위').selectOption('all'); await page.waitForURL(url => !url.searchParams.has('audience')); await performanceReady(page);
    await chartMatchesHttp(context, page, 'all', 'hitRate');
    checks.push('one-cohort API displays missing companion values as unknown; unevaluated filter shows no scores');

    await fixture('evaluation_missing'); await refresh(); await page.locator('[data-performance-unavailable]').waitFor();
    await fixture('normal', { evaluation_variant: 'refreshed' });
    const pending = waitResponse(page, '/api/v1/admin/analytics/recommendation-performance');
    await page.getByRole('button', { name: '다시 확인', exact: true }).click(); await pending; await performanceReady(page);
    const replaced = await chartMatchesHttp(context, page, 'all', 'hitRate');
    assert.notEqual(replaced.evaluationId, original.evaluationId);
    assert.notEqual(replaced.currentModelId, original.currentModelId);
    assert.notDeepEqual(replaced.models.map(model => model.modelId), original.models.map(model => model.modelId));
    assert.equal(await page.getByRole('region', { name: '평가 조건', exact: true }).getByText(original.evaluationId, { exact: true }).count(), 0, 'previous artifact ID is removed');
    await page.getByRole('group', { name: '비교할 추천 성능 지표' }).getByRole('button', { name: 'NDCG@10', exact: true }).click();
    await chartMatchesHttp(context, page); await s.shot('performance-refreshed-artifact');
    checks.push('artifact replacement and explicit retry update model order, reference model, scores, bars, summaries and table');

    await fixture('normal', { evaluation_variant: 'disjoint_users' }); await refresh();
    const disjoint = await chartMatchesHttp(context, page);
    assert.equal(disjoint.conditions.cohortInterpretation, 'disjoint_users');
    assert.match(await page.locator('main').innerText(), /기존·신규의 집단 기준은 아래 평가 조건을 확인하세요/);
    assert.doesNotMatch(await page.locator('main').innerText(), /같은 표본에 전체 시청 기록 또는 온보딩 입력/);
    for (const cohort of disjoint.cohorts) assert.ok((await page.getByRole('region', { name: '평가 조건', exact: true }).innerText()).includes(cohort.definition));
    checks.push('disjoint actual-user cohort metadata is shown independently of simulated input scenarios');

    await fixture('normal', { evaluation_variant: 'long_labels' }); await refresh();
    const longLabels = await chartMatchesHttp(context, page);
    assert.ok(longLabels.models.some(model => model.label.length === 100 && !/\s/.test(model.label)), 'fixture exercises a maximum-length unbroken model label');
    await s.shot('performance-long-model-labels');
    checks.push('maximum supported model labels preserve viewport layout and remain readable');
    s.clean(); results.push({ suite: 'evaluation-variants', width, passed: true, checks, requests: s.requests, responses: s.responses });
  } catch (error) {
    await page.screenshot({ path: path.join(output, `failure-evaluation-variants-${width}.png`), fullPage: true, timeout: 5000 }).catch(() => {});
    throw new Error(`Evaluation variants ${width}px ${page.url()}: ${error.message}`, { cause: error });
  } finally { await context.close(); await fixture(); }
}
async function serviceEvaluation(browser, width) {
  await fixture(); const s = await session(browser, width), { page, context } = s;
  const checks = [];
  const refresh = async () => {
    const pending = waitResponse(page, '/api/v1/admin/analytics/recommendation-performance', 'dataset=service');
    await focusRefresh(page); await pending; await performanceReady(page);
  };
  try {
    console.log(`Real HTTP service evaluation: ${width}px`);
    await page.goto(`${base}/admin/dashboard`); await dashboardReady(page);
    const card = page.getByRole('region', { name: '서비스 추천 품질', exact: true });
    await card.waitFor(); assert.match(await card.innerText(), /평가 결과 준비 중/);
    const dashboard = await (await context.request.get(`${api}/api/v1/admin/dashboard?period=7d`)).json();
    assert.equal(dashboard.serviceRecommendationPerformance.source, 'service_feedback');
    assert.equal(dashboard.recommendationPerformance.source, 'offline_evaluation');
    await card.getByRole('link', { name: /^추천 성능 상세 보기/ }).click(); await performanceReady(page);
    assert.equal(new URL(page.url()).searchParams.get('dataset'), 'service');
    assert.equal(await page.getByLabel('추천 성능 평가 데이터').inputValue(), 'service');
    await page.locator('[data-pipeline-state="waiting"]').waitFor();
    assert.equal(await page.locator('[data-performance-chart]').count(), 0);
    assert.match(await page.locator('main').innerText(), /Checking 시험 데이터/);
    await s.shot('service-waiting');
    checks.push('service summary links to its own dataset; waiting status has no fabricated score');

    await fixture('normal', { service_variant: 'published' }); await refresh();
    const published = await chartMatchesHttp(context, page, 'all', 'ndcg', '7d', 'service');
    assert.equal(published.dataSource, 'local_demo');
    assert.equal(published.currentModelId, 'stored');
    assert.match(await page.locator('main').innerText(), /Checking 시험 데이터/);
    assert.equal(await page.locator('[data-model="v05"]').count(), 0);
    await page.getByRole('group', { name: '비교할 추천 성능 지표' }).getByRole('button', { name: 'Hit Rate@10', exact: true }).click();
    await chartMatchesHttp(context, page, 'all', 'hitRate', '7d', 'service');
    await s.shot('service-published-hit-rate');
    checks.push('content NDCG/Hit Rate bars, summaries and table match real service HTTP scores');

    await fixture('normal', { service_variant: 'failed' }); await refresh();
    await page.locator('[data-pipeline-state="failed"]').waitFor();
    const retained = await chartMatchesHttp(context, page, 'all', 'hitRate', '7d', 'service');
    assert.equal(retained.evaluationId, published.evaluationId);
    assert.match(await page.locator('main').innerText(), /이전에 게시된 유효한 결과는 유지됩니다/);
    await fixture('normal', { service_variant: 'manifest_missing' }); await refresh();
    const missingManifest = await chartMatchesHttp(context, page, 'all', 'hitRate', '7d', 'service');
    assert.equal(missingManifest.pipeline, null);
    assert.match(await page.locator('main').innerText(), /Checking 시험 데이터/);
    await page.goto(`${base}/admin/dashboard`); await dashboardReady(page);
    assert.match(await page.getByRole('region', { name: '서비스 추천 품질', exact: true }).innerText(), /Checking 시험 데이터/);
    await s.shot('service-dashboard-retained');
    checks.push('failed/missing manifest preserves valid results and independent trial-data labeling');

    await page.getByRole('region', { name: '서비스 추천 품질', exact: true }).getByRole('link', { name: /^추천 성능 상세 보기/ }).click(); await performanceReady(page);
    await page.getByLabel('추천 성능 사용자 범위').selectOption('new'); await performanceReady(page);
    await chartMatchesHttp(context, page, 'new', 'ndcg', '7d', 'service');
    await page.reload(); await performanceReady(page);
    assert.equal(await page.getByLabel('추천 성능 평가 데이터').inputValue(), 'service');
    assert.equal(await page.getByLabel('추천 성능 사용자 범위').inputValue(), 'new');
    await page.getByLabel('추천 성능 평가 데이터').selectOption('offline'); await performanceReady(page);
    await chartMatchesHttp(context, page, 'new');
    assert.equal(new URL(page.url()).searchParams.get('dataset'), null);
    assert.equal(await page.locator('[data-pipeline-state]').count(), 0);
    await fixture('normal', { service_variant: 'published', delay_dataset: 'service' });
    const delayed = page.waitForRequest(req => new URL(req.url()).pathname.endsWith('/recommendation-performance') && new URL(req.url()).searchParams.get('dataset') === 'service');
    await page.getByLabel('추천 성능 평가 데이터').selectOption('service'); await delayed;
    await page.getByLabel('추천 성능 평가 데이터').selectOption('offline'); await performanceReady(page);
    await chartMatchesHttp(context, page, 'new');
    await page.waitForTimeout(2300); // Ensure the delayed service response cannot replace the offline view.
    await chartMatchesHttp(context, page, 'new');
    assert.equal(await page.locator('[data-model="stored"]').count(), 0);
    checks.push('dataset/filter reload and rapid dataset switches retain their own response and units');
    s.clean(); results.push({ suite: 'service', width, passed: true, checks, requests: s.requests, responses: s.responses });
  } catch (error) {
    await page.screenshot({ path: path.join(output, `failure-service-${width}.png`), fullPage: true, timeout: 5000 }).catch(() => {});
    throw new Error(`Service ${width}px ${page.url()}: ${error.message}`, { cause: error });
  } finally { await context.close(); await fixture(); }
  if (width === 1728) for (const role of ['viewer', 'operator', 'admin', 'member', 'anonymous']) {
    const roleSession = await session(browser, width, role);
    try {
      const response = await roleSession.context.request.get(`${api}/api/v1/admin/analytics/recommendation-performance?dataset=service`);
      assert.equal(response.status(), role === 'member' ? 403 : role === 'anonymous' ? 401 : 200);
    } finally { await roleSession.context.close(); }
  }
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
  const suite = process.env.ADMIN_ANALYTICS_SUITE || 'all';
  assert.ok(['all', 'variants', 'service'].includes(suite), 'ADMIN_ANALYTICS_SUITE must be all, variants or service');
  try {
    for (const width of process.argv[4] ? [Number(process.argv[4])] : [1728, 390]) {
      if (suite === 'all') await happy(browser, width);
      if (suite !== 'service') await evaluationVariants(browser, width);
      if (suite !== 'variants') await serviceEvaluation(browser, width);
    }
    if (suite === 'all') await boundaries(browser);
  }
  finally { await browser.close(); await control.dispose(); fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(results, null, 2)); }
  console.log(JSON.stringify(results.map(({ width, suite, passed, checks }) => ({ width, suite, passed, checks })), null, 2));
})().catch(error => { console.error(error); process.exitCode = 1; });
