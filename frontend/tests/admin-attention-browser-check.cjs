// Production FE + intercepted synthetic GETs. No backend, DB, real login or external traffic.
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const { chromium } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const fixture = require('./helpers/admin-attention-fixture.cjs');
const base = process.argv[2] || 'http://127.0.0.1:3236';
const apiOrigin = process.env.ADMIN_ATTENTION_MOCK_API || 'http://127.0.0.1:8197';
const output = process.argv[3] || path.resolve(__dirname, '../../.preview-pivot/admin-attention/results');
const loopback = value => value.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(value.hostname);
assert.ok(loopback(new URL(base)) && loopback(new URL(apiOrigin)));
const results = [];
const ready = page => page.locator('[data-admin-dashboard][data-admin-state="ready"]').waitFor();
const detailReady = (page, type = 'report', id = '41') => page.locator('[data-report-detail="' + (type === 'report' ? 'R-' : 'I-') + id + '"]').waitFor();
const expectDashboard = async (page, attention, pageNumber) => {
  await page.waitForURL(url => url.pathname === '/admin/dashboard' && url.searchParams.get('period') === '30d' &&
    url.searchParams.get('attention') === attention && url.searchParams.get('page') === String(pageNumber));
  await ready(page);
  const url = new URL(page.url());
  assert.equal(url.pathname, '/admin/dashboard');
  assert.equal(url.searchParams.get('period'), '30d');
  assert.equal(url.searchParams.get('attention'), attention);
  assert.equal(url.searchParams.get('page'), String(pageNumber));
  assert.equal((await page.getByRole('group', { name: '확인 항목 분류' }).locator('[aria-pressed="true"]').innerText()).replace(/\s/g, ''), (attention === 'report' ? '신고' : '문의') + '6');
};
async function run(browser, width) {
  const context = await browser.newContext({ viewport: { width, height: width < 500 ? 844 : 1117 }, serviceWorkers: 'block' });
  const control = { mode: 'normal', session: true, delay: false, count: 6 };
  const requests = [], blocked = [], pageErrors = [];
  let delayed = null;
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), method = request.method();
    if (![new URL(base).origin, new URL(apiOrigin).origin].includes(url.origin) || !['GET', 'HEAD', 'OPTIONS'].includes(method)) {
      blocked.push(method + ' ' + url.origin + url.pathname); return route.abort();
    }
    if (!url.pathname.startsWith('/api/')) return route.continue();
    requests.push({ path: url.pathname, search: url.search, method });
    if (url.origin !== new URL(apiOrigin).origin) { blocked.push('wrong API origin: ' + url.origin); return route.abort(); }
    const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', headers: { 'Access-Control-Allow-Origin': new URL(base).origin, 'Access-Control-Allow-Credentials': 'true', 'Cache-Control': 'private, no-store' }, body: JSON.stringify(body) });
    if (url.pathname === '/api/v1/auth/session') return json(control.session ? fixture.session() : { ...fixture.session(), authenticated: false, sessionState: 'anonymous', user: null });
    if (url.pathname === '/api/v1/admin/dashboard') return json({ ...fixture.dashboard(url.searchParams.get('period')), attention: fixture.attention('all', 1, control.count) });
    if (url.pathname === '/api/v1/admin/dashboard/attention') return json(fixture.attention(url.searchParams.get('kind'), Number(url.searchParams.get('page')), control.count));
    if (url.pathname === '/api/v1/admin/reports') return json(fixture.recentReports());
    const match = url.pathname.match(/^\/api\/v1\/admin\/(reports|inquiries)\/([1-9]\d*)$/);
    if (match) {
      const type = match[1] === 'reports' ? 'report' : 'inquiry';
      if (control.delay && type === 'report') await new Promise(resolve => { delayed = resolve; });
      if (control.mode === 'network') return route.abort('failed');
      const code = control.mode === '401' ? 'AUTH_REQUIRED' : control.mode === '403' ? 'FORBIDDEN' : control.mode === '404' ? (type === 'report' ? 'ADMIN_REPORT_NOT_FOUND' : 'ADMIN_INQUIRY_NOT_FOUND') : 'ADMIN_UNAVAILABLE';
      if (['401', '403', '404', '503'].includes(control.mode)) return json({ code, message: '격리된 오류 응답' }, Number(control.mode));
      if (control.mode === 'mismatch') return json(fixture.detail(type === 'report' ? 'inquiry' : 'report', match[2]));
      return json(fixture.detail(type, match[2]));
    }
    blocked.push('unexpected API: ' + method + ' ' + url.pathname); return route.abort();
  });
  const page = await context.newPage();
  page.setDefaultTimeout(15000); page.setDefaultNavigationTimeout(60000);
  page.on('pageerror', error => pageErrors.push(error.message));
  const screenshot = async name => {
    await page.evaluate(() => document.fonts.ready);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), name + ': document overflow');
    await page.screenshot({ path: path.join(output, name + '-' + width + '.png'), fullPage: true, animations: 'disabled' });
  };
  try {
    await page.goto(base + '/admin/dashboard'); await ready(page);
    await page.getByRole('button', { name: /^조회 기간:/ }).click();
    await page.getByRole('menuitemradio', { name: '최근 30일', exact: true }).click();
    await page.waitForURL(url => url.searchParams.get('period') === '30d'); await ready(page);
    const filters = page.getByRole('group', { name: '확인 항목 분류' });
    await filters.getByRole('button', { name: /^신고/ }).click();
    await page.waitForURL(url => url.searchParams.get('attention') === 'report'); await ready(page);
    await page.getByRole('navigation', { name: '확인 항목 페이지' }).getByRole('button', { name: '다음 페이지' }).click();
    await expectDashboard(page, 'report', 2);
    await page.reload(); await expectDashboard(page, 'report', 2);
    const reportLink = page.getByRole('link', { name: '신고 #41 상세 보기', exact: true });
    const reportUrl = await reportLink.getAttribute('href');
    assert.equal(reportUrl, '/admin/reports/report/41?period=30d&attention=report&page=2');
    await screenshot('dashboard');
    await reportLink.focus(); await reportLink.press('Enter'); await detailReady(page);
    assert.match(await page.locator('[data-report-detail]').innerText(), /오래된 신고의 고유 내용|2026.10.01/);
    assert.equal(await page.getByRole('button', { name: '검토 시작', exact: true }).isEnabled(), true);
    assert.equal(await page.getByRole('link', { name: '← 대시보드로', exact: true }).getAttribute('href'), '/admin/dashboard?period=30d&attention=report&page=2');
    await screenshot('report-detail');
    await page.reload(); await detailReady(page);
    await page.goBack(); await expectDashboard(page, 'report', 2);
    await page.goForward(); await detailReady(page);
    await page.getByRole('link', { name: '← 대시보드로', exact: true }).click(); await expectDashboard(page, 'report', 2);

    await filters.getByRole('button', { name: /^문의/ }).click();
    await page.waitForURL(url => url.searchParams.get('attention') === 'inquiry' && !url.searchParams.has('page')); await ready(page);
    assert.equal(new URL(page.url()).searchParams.get('page'), null, 'changing kind resets page');
    await page.getByRole('navigation', { name: '확인 항목 페이지' }).getByRole('button', { name: '다음 페이지' }).click();
    await expectDashboard(page, 'inquiry', 2);
    await page.getByRole('link', { name: '문의 #41 상세 보기', exact: true }).click(); await detailReady(page, 'inquiry');
    assert.equal(await page.getByRole('heading', { name: '문의 상세 검토', exact: true }).count(), 1);
    assert.match(await page.locator('[data-report-detail]').innerText(), /오래된 문의의 고유 내용/);
    assert.doesNotMatch(await page.locator('[data-report-detail]').innerText(), /오래된 신고의 고유 내용/);
    // Even opening/cancelling the existing confirmation UI must not post.
    await page.getByRole('button', { name: '답변 작성', exact: true }).click();
    await page.getByRole('dialog').getByRole('button', { name: '취소', exact: true }).click();
    await screenshot('inquiry-detail');
    await page.reload(); await detailReady(page, 'inquiry');
    await page.goBack(); await expectDashboard(page, 'inquiry', 2);
    await page.goForward(); await detailReady(page, 'inquiry');
    await page.getByRole('link', { name: '← 대시보드로', exact: true }).click(); await expectDashboard(page, 'inquiry', 2);
    assert.equal(requests.filter(r => r.path === '/api/v1/admin/reports').length, 0, 'deep links must not depend on list');
    assert.ok(requests.some(r => r.path === '/api/v1/admin/reports/41'));
    assert.ok(requests.some(r => r.path === '/api/v1/admin/inquiries/41'));

    // Direct URL, no dashboard visit/selection required; bigint ID stays exact.
    await page.goto(base + '/admin/reports/inquiry/9223372036854775807');
    await detailReady(page, 'inquiry', '9223372036854775807');
    assert.equal(await page.getByRole('link', { name: '← 대시보드로', exact: true }).getAttribute('href'), '/admin/dashboard');
    await screenshot('direct-bigint');
    // Existing report list remains independent, with no fabricated selection.
    await page.goto(base + '/admin/reports'); await page.locator('[data-report-empty]').waitFor();
    assert.equal(await page.locator('[data-report-detail="I-41"]').count(), 0);

    for (const type of ['report', 'inquiry']) for (const [mode, state] of [['401', 'login-required'], ['403', 'forbidden'], ['404', 'not-found'], ['503', 'unavailable'], ['network', 'error'], ['mismatch', 'error']]) {
      control.mode = mode;
      await page.goto(base + '/admin/reports/' + type + '/41?period=30d&attention=inquiry&page=2');
      await page.locator('[data-report-detail="unavailable"] [data-admin-state="' + state + '"]').waitFor();
      assert.equal(await page.locator('[data-report-detail="R-41"], [data-report-detail="I-41"]').count(), 0);
      assert.equal(await page.getByRole('link', { name: '← 대시보드로', exact: true }).getAttribute('href'), '/admin/dashboard?period=30d&attention=inquiry&page=2');
      if (mode === '503') {
        control.mode = 'normal';
        await page.getByRole('button', { name: '다시 확인', exact: true }).click(); await detailReady(page, type);
      }
    }
    control.mode = 'normal';
    await page.goto(base + '/admin/reports/inquiry/41'); await detailReady(page, 'inquiry');
    control.mode = '403'; await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await page.locator('[data-admin-state="forbidden"]').waitFor();
    assert.equal(await page.locator('[data-report-detail="I-41"]').count(), 0, 'revoked access clears body');
    await screenshot('forbidden');
    control.mode = 'normal'; control.session = false;
    const beforeAnonymous = requests.filter(r => /\/(reports|inquiries)\/\d+$/.test(r.path)).length;
    await page.goto(base + '/admin/reports/report/41'); await page.locator('[data-admin-state="login-required"]').waitFor();
    assert.equal(requests.filter(r => /\/(reports|inquiries)\/\d+$/.test(r.path)).length, beforeAnonymous, 'anonymous never reads detail');
    control.session = true;
    for (const bad of ['/admin/reports/report/0', '/admin/reports/invalid/41', '/admin/reports/inquiry/9223372036854775808']) {
      const before = requests.length;
      await page.goto(base + bad); await page.locator('[data-admin-state="not-found"]').waitFor();
      assert.equal(requests.length, before, 'invalid target issues no API request');
    }

    // Navigate away while report GET is in flight; it cannot replace the inquiry.
    control.delay = true;
    await page.goto(base + reportUrl);
    await page.waitForFunction(() => !!document.querySelector('[data-admin-state="loading"]'));
    await assertEventually(() => delayed !== null);
    await page.getByRole('link', { name: '← 대시보드로', exact: true }).click(); await ready(page);
    await filters.getByRole('button', { name: /^문의/ }).click();
    await page.waitForURL(url => url.searchParams.get('attention') === 'inquiry' && !url.searchParams.has('page')); await ready(page);
    await page.getByRole('link', { name: '문의 #1 상세 보기', exact: true }).click(); await detailReady(page, 'inquiry', '1');
    control.delay = false; delayed(); delayed = null;
    await page.waitForLoadState('networkidle');
    assert.equal(await page.locator('[data-report-detail="I-1"]').count(), 1);
    assert.equal(await page.locator('[data-report-detail="R-41"]').count(), 0);

    // Queue shrink canonicalizes the URL with replace, without an invalid empty page.
    control.count = 1;
    await page.goto(base + '/admin/dashboard?period=30d&attention=report&page=2');
    await page.waitForURL(url => url.pathname === '/admin/dashboard' && !url.searchParams.has('page')); await ready(page);
    assert.equal(new URL(page.url()).searchParams.get('attention'), 'report');

    assert.deepEqual(pageErrors, []); assert.deepEqual(blocked, []);
    assert.equal(requests.filter(r => r.method !== 'GET').length, 0);
    results.push({ width, checks: 'period/filter/page URL; same ID report/inquiry; old detail; reload; back/forward; explicit return; direct bigint; list regression; 401/403/404/503/network/mismatch; retry; revoke; anonymous; invalid URLs; stale request; queue shrink', apiRequests: requests.length, writes: 0, externalRequests: 0, pageErrors: 0 });
  } catch (error) {
    await page.screenshot({ path: path.join(output, 'failure-' + width + '.png'), fullPage: true }).catch(() => {});
    fs.writeFileSync(path.join(output, 'failure-' + width + '.json'), JSON.stringify({ url: page.url(), requests, blocked, pageErrors }, null, 2));
    throw error;
  } finally {
    if (delayed) delayed();
    await context.close();
  }
}
async function consumerSmoke(browser, width) {
  const context = await browser.newContext({ viewport: { width, height: width < 500 ? 844 : 1117 }, serviceWorkers: 'block' });
  const page = await context.newPage(), blocked = [], pageErrors = [];
  page.setDefaultTimeout(15000);
  await context.route('**/*', route => {
    const request = route.request(), url = new URL(request.url());
    if (url.origin !== new URL(base).origin || url.pathname.startsWith('/api/') || !['GET', 'HEAD'].includes(request.method())) {
      blocked.push(request.method() + ' ' + url.pathname); return route.abort();
    }
    return route.continue();
  });
  page.on('pageerror', error => pageErrors.push(error.message));
  const nav = () => page.locator('main:visible nav[aria-label="주요 메뉴"]');
  const screen = name => page.locator('[data-' + name + '-state="ready"]').waitFor();
  try {
    // Existing demo data only; local preview process must explicitly enable it.
    await page.goto(base + '/pivot/demo?view=home'); await screen('home');
    await nav().getByRole('button', { name: 'SAVE', exact: true }).click(); await screen('saved');
    await page.locator('[data-saved-content]').first().click(); await screen('detail');
    await nav().getByRole('button', { name: 'MY', exact: true }).click(); await screen('my');
    await nav().getByRole('button', { name: 'HOME', exact: true }).click(); await screen('home');
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({ path: path.join(output, 'consumer-home-' + width + '.png'), fullPage: true });
    assert.deepEqual(pageErrors, []); assert.deepEqual(blocked, []);
    results.push({ area: 'consumer-demo', width, checks: 'HOME → SAVE → detail → MY → HOME', apiRequests: 0, writes: 0, externalRequests: 0, pageErrors: 0 });
  } finally { await context.close(); }
}
async function assertEventually(check) {
  for (let i = 0; i < 100; i++) { if (check()) return; await new Promise(resolve => setTimeout(resolve, 50)); }
  assert.ok(check(), 'expected in-flight request');
}
(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, ...(process.env.PIVOT_BROWSER_EXECUTABLE ? { executablePath: process.env.PIVOT_BROWSER_EXECUTABLE } : { channel: 'msedge' }) });
  try {
    for (const width of [375, 390, 1728]) await run(browser, width);
    if (process.env.ADMIN_ATTENTION_CONSUMER_SMOKE === 'true') {
      for (const width of [375, 390, 1728]) await consumerSmoke(browser, width);
    }
    fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify({ success: true, results }, null, 2));
    console.log(JSON.stringify({ success: true, results }, null, 2));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
