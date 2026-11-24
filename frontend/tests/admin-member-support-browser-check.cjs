// Real browser -> HTTP -> admin permissions -> isolated PostgreSQL fixture.
// This harness never fulfills API responses and refuses non-loopback traffic.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const { chromium, request } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const base = process.argv[2] || 'http://127.0.0.1:3221';
const api = process.env.ADMIN_READ_FIXTURE_URL || 'http://127.0.0.1:8199';
const output = process.argv[3] || path.resolve(__dirname, '../../.preview-pivot/admin-member-support/results');
const local = value => value.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(value.hostname);
assert.ok(local(new URL(base)) && local(new URL(api))); assert.equal(new URL(base).hostname, new URL(api).hostname);
const results = [];
let control;
async function fixture(extra = {}) {
  const response = await control.post(`${api}/__control`, { headers: { 'X-Fixture-Key': 'isolated-admin-read-browser' }, data: { mode: 'normal', revoked: false, delay_detail: false, ...extra } });
  assert.equal(response.status(), 200, await response.text());
}
async function session(browser, width, role = 'viewer') {
  const context = await browser.newContext({ viewport: { width, height: 1117 }, serviceWorkers: 'block' });
  await context.addCookies([{ name: 'menu_session', value: `admin-read-fixture-${role}`, domain: new URL(base).hostname, path: '/', httpOnly: true, secure: false, sameSite: 'Lax' }]);
  const page = await context.newPage(), errors = [], traffic = [], reads = [];
  page.setDefaultTimeout(20000); page.setDefaultNavigationTimeout(60000);
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', req => { const url = new URL(req.url()); if (url.pathname.startsWith('/api/')) reads.push({ path: url.pathname, url: req.url(), headers: req.headers(), method: req.method() }); });
  await context.route('**/*', route => {
    const req = route.request(), url = new URL(req.url());
    if (!local(url) || !['GET', 'HEAD', 'OPTIONS'].includes(req.method())) { traffic.push(`${req.method()} ${url.pathname}`); return route.abort(); }
    return route.continue();
  });
  const shot = async name => {
    await page.evaluate(() => document.fonts.ready);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `${name}: document overflow`);
    await page.screenshot({ path: path.join(output, `${name}-${width}.png`), fullPage: true, animations: 'disabled' });
  };
  return { page, context, reads, shot, clean() { assert.deepEqual(errors, []); assert.deepEqual(traffic, []); } };
}
const membersReady = page => page.locator('[data-member-list="ready"]').waitFor();
const reportsReady = page => page.locator('[data-report-list="ready"]').waitFor();
const memberDetailReady = page => page.locator('[data-member-detail="ready"]').waitFor();
const refresh = page => page.evaluate(() => window.dispatchEvent(new Event('focus')));
const readResponse = (page, pathname, status = 200) => page.waitForResponse(response => new URL(response.url()).pathname === pathname && response.status() === status);

async function flows(browser, width) {
  await fixture();
  const s = await session(browser, width), { page } = s;
  try {
    await page.goto(`${base}/admin/users`); await membersReady(page);
    assert.equal(await page.getByRole('button', { name: 'CSV 내보내기' }).isDisabled(), true);
    assert.equal(await page.getByRole('columnheader', { name: '최근 로그인', exact: true }).count(), 1);
    assert.equal(await page.getByRole('button', { name: '휴면 회원', exact: true }).count(), 0);
    await shotAndRecord('members');

    await page.getByRole('button', { name: '카카오', exact: true }).click(); await membersReady(page);
    assert.ok(await page.locator('[data-member-row]').count() > 0);
    await page.getByRole('button', { name: '전체', exact: true }).click(); await membersReady(page);
    const search = page.getByRole('textbox', { name: '회원 검색', exact: true });
    const searchValue = 'no-such-member+비공개@example.test';
    const searched = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/admin/members' && response.request().headers()['x-admin-search'] === encodeURIComponent(searchValue));
    await search.fill(searchValue); await search.press('Enter'); await searched; await membersReady(page);
    await page.locator('[data-member-empty]').waitFor();
    assert.doesNotMatch(page.url(), /no-such|example|%40|%EB/);
    assert.equal(await page.evaluate(() => [...Object.values(localStorage), ...Object.values(sessionStorage)].some(value => value.includes('no-such-member'))), false);
    await page.getByRole('button', { name: '조건 초기화', exact: true }).click(); await membersReady(page);

    await page.goto(`${base}/admin/users/101?page=2`); await memberDetailReady(page);
    assert.match(await page.locator('[data-member-detail]').innerText(), /변경 이력 미수집|관리 메모 미수집/);
    assert.equal(await page.getByRole('button', { name: '메모 작성', exact: true }).isDisabled(), true);
    assert.match(await page.getByRole('link', { name: '회원 목록으로' }).getAttribute('href'), /page=2/);
    await shotAndRecord('member-detail');
    await page.goto(`${base}/admin/users/105`); await memberDetailReady(page);
    assert.equal(await page.getByRole('heading', { name: '탈퇴 회원', exact: true }).count(), 1);
    assert.doesNotMatch(await page.locator('[data-member-detail]').innerText(), /@/);
    await page.goto(`${base}/admin/users/9223372036854775807`); await page.locator('[data-admin-state="not-found"]').waitFor();

    await page.goto(`${base}/admin/reports`); await reportsReady(page);
    await page.locator('[data-report-detail]:not([data-report-detail="unavailable"])').waitFor();
    await shotAndRecord('reports');
    await page.getByRole('combobox', { name: '신고 및 문의 유형', exact: true }).selectOption('service'); await reportsReady(page);
    await page.locator('[data-report-detail^="I-"]').waitFor();
    assert.match(await page.locator('[data-report-detail]').innerText(), /문의 내용|답변 내용은 수집되지 않았습니다/);
    assert.ok(s.reads.some(item => item.path.startsWith('/api/v1/admin/inquiries/')));
    await page.getByRole('combobox', { name: '신고 및 문의 유형', exact: true }).selectOption('content'); await reportsReady(page);
    await page.locator('[data-report-detail^="R-"]').waitFor();
    const body = await page.locator('[data-report-detail]').innerText(); assert.match(body, /처리 상태/);
    await page.getByRole('button', { name: '처리 완료', exact: true }).click(); await reportsReady(page);
    await page.locator('[data-report-detail^="R-"]').waitFor();
    assert.match(await page.locator('[data-report-detail]').innerText(), /반려|해결/);
    await page.getByRole('button', { name: '전체 신고', exact: true }).click(); await reportsReady(page);

    // An older selected request must not overwrite the second selection.
    await fixture({ delay_detail: true });
    const buttons = page.locator('[data-report-row] td:first-child button');
    assert.ok(await buttons.count() >= 2);
    const firstId = await page.locator('[data-report-row]').nth(0).getAttribute('data-report-row');
    const secondId = await page.locator('[data-report-row]').nth(1).getAttribute('data-report-row');
    await buttons.nth(1).click();
    await page.locator('[data-report-detail] [data-admin-state="loading"]').waitFor();
    await buttons.nth(0).click();
    await page.locator(`[data-report-detail="${firstId}"]`).waitFor();
    assert.equal(await page.locator(`[data-report-detail="${secondId}"]`).count(), 0);
    await fixture();

    // Losing the grant during a detail request clears list, selected body and counts.
    await fixture({ revoked: true });
    await page.locator('[data-report-row] td:first-child button').nth(1).click();
    await page.locator('[data-report-list="forbidden"]').waitFor();
    assert.equal(await page.locator('[data-report-row]').count(), 0);
    assert.equal(await page.locator('[data-report-detail^="R-"]').count(), 0);
    await fixture(); await page.getByRole('button', { name: '다시 확인', exact: true }).click(); await reportsReady(page);

    await fixture({ mode: 'failure' }); await refresh(page); await page.locator('[data-report-list="unavailable"]').waitFor();
    assert.equal(await page.locator('[data-report-empty]').count(), 0);
    await shotAndRecord('support-unavailable');
    await fixture(); await page.getByRole('button', { name: '다시 확인', exact: true }).click(); await reportsReady(page);
    await fixture({ mode: 'empty' }); await refresh(page); await page.locator('[data-report-empty]').waitFor();
    assert.equal(await page.locator('[data-report-detail^="R-"]').count(), 0);
    await shotAndRecord('support-empty');
    s.clean(); results.push({ width, checks: 'members/detail/search/filter/redaction/not-found/support/detail/raw-status/selection-race/revocation/503/empty', requestCount: s.reads.length });
  } finally { await s.context.close(); }
  async function shotAndRecord(name) { await s.shot(name); }
}

async function permissions(browser) {
  await fixture();
  for (const [role, expected] of [['viewer', 'ready'], ['operator', 'ready'], ['admin', 'ready'], ['member', 'forbidden'], ['anonymous', 'login-required']]) {
    const s = await session(browser, 1728, role);
    try {
      for (const [route, selector] of [['users', 'member'], ['reports', 'report']]) {
        await s.page.goto(`${base}/admin/${route}`); await s.page.locator(`[data-${selector}-list="${expected}"]`).waitFor();
        if (expected !== 'ready') { assert.equal(await s.page.locator(`[data-${selector}-row]`).count(), 0); assert.equal(await s.page.locator(`[data-${selector}-empty]`).count(), 0); }
      }
      s.clean(); results.push({ role, state: expected });
    } finally { await s.context.close(); }
  }
}

(async () => {
  fs.mkdirSync(output, { recursive: true }); control = await request.newContext();
  const browser = await chromium.launch({ headless: true, ...(process.env.PIVOT_BROWSER_EXECUTABLE ? { executablePath: process.env.PIVOT_BROWSER_EXECUTABLE } : { channel: 'chrome' }) });
  try { await flows(browser, 1728); await flows(browser, 390); await permissions(browser); fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify({ success: true, results }, null, 2)); console.log(JSON.stringify({ success: true, results }, null, 2)); }
  finally { await fixture().catch(() => {}); await browser.close(); await control.dispose(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
