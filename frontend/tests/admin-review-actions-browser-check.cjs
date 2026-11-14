// Read-only HTTP fixtures: confirmation preparation must never send a write.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const base = process.argv[2] || 'http://127.0.0.1:3197';
const output = process.argv[3] || path.join(require('node:os').tmpdir(), 'admin-review-actions');
assert.ok(['localhost', '127.0.0.1'].includes(new URL(base).hostname));
const results = [];

async function verify(browser, width) {
  const context = await browser.newContext({ viewport: { width, height: 1117 }, serviceWorkers: 'block' });
  const page = await context.newPage(); page.setDefaultTimeout(10000);
  let failure = null, status = 'active', judgmentStatus = 'needs_review';
  const writes = [], errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    assert.ok(['localhost', '127.0.0.1'].includes(url.hostname), 'no external requests');
    if (!url.pathname.startsWith('/api/')) return route.continue();
    if (request.method() !== 'GET') { writes.push(request.method() + ' ' + url.pathname); return route.abort(); }
    const reply = (body, code = 200) => route.fulfill({ status: code, contentType: 'application/json', body: JSON.stringify(body) });
    if (url.pathname === '/api/v1/auth/session') return reply({ authenticated: true, sessionState: 'active',
      user: { id: 7, accountType: 'user' }, onboardingCompleted: true, csrfToken: 'fixture' });
    if (failure) return reply({ code: failure === 401 ? 'AUTH_REQUIRED' : 'ADMIN_REQUIRED' }, failure);
    return reply({ contentId: 2, title: '검수 대상 콘텐츠', status, judgmentStatus,
      createdAt: '2026-11-13T00:00:00Z', updatedAt: '2026-11-13T00:12:34.123400-03:30', publishedAt: '2099-01-01T00:00:00Z', summary: '검수 요약',
      body: '기존 본문 유지', sources: [{ url: 'https://example.org', title: '원문', publisher: '테스트', role: 'primary', citationOrder: 1 }] });
  });
  const ready = () => page.locator('[data-admin-detail="ready"]').waitFor();
  const dialog = page.getByRole('dialog');
  try {
    await page.goto(`${base}/admin/contents/2?mode=review&q=검수&page=2&pageSize=20`); await ready();
    const back = await page.getByRole('link', { name: '← 콘텐츠 목록' }).getAttribute('href');
    assert.match(back, /mode=review/); assert.match(back, /page=2/);
    await page.getByRole('button', { name: '승인 내용 확인' }).click();
    await dialog.waitFor();
    assert.equal(await dialog.getByLabel('조치 사유 (필수)').evaluate(el => el === document.activeElement), true);
    await dialog.getByRole('button', { name: '입력 확인', exact: true }).click();
    await dialog.getByText('조치 사유를 입력해 주세요.', { exact: true }).waitFor();
    await dialog.getByLabel('조치 사유 (필수)').fill('x'.repeat(2001));
    await dialog.getByRole('button', { name: '입력 확인', exact: true }).click();
    await dialog.getByText('조치 사유는 2,000자 이내로 입력해 주세요.', { exact: true }).waitFor();
    await dialog.getByLabel('조치 사유 (필수)').fill('출처를 확인했습니다. <script>window.injected = true</script>');
    await dialog.getByRole('button', { name: '입력 확인', exact: true }).click();
    await dialog.getByText('입력 확인 완료 · 미저장. 콘텐츠 상태는 변경되지 않았습니다.', { exact: true }).waitFor();
    assert.equal(await dialog.getByRole('button', { name: '승인 실행 (미연결)' }).isDisabled(), true);
    assert.equal(await page.evaluate(() => Boolean(window.injected)), false);
    assert.equal(await dialog.evaluate(el => el.scrollWidth > el.clientWidth), false);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({ path: path.join(output, `approval-${width}.png`), fullPage: true });
    await page.keyboard.press('Escape'); await dialog.waitFor({ state: 'hidden' });
    assert.equal(await page.getByRole('button', { name: '승인 내용 확인' }).evaluate(el => el === document.activeElement), true);
    await page.getByRole('button', { name: '반려 내용 확인' }).click(); await dialog.waitFor();
    assert.equal(await dialog.getByLabel('조치 사유 (필수)').inputValue(), '');
    await dialog.getByLabel('조치 사유 (필수)').fill('출처 내용과 본문이 일치하지 않습니다.');
    await dialog.getByRole('button', { name: '입력 확인', exact: true }).click();
    assert.equal(await dialog.getByRole('button', { name: '반려 실행 (미연결)' }).isDisabled(), true);
    await page.screenshot({ path: path.join(output, `rejection-${width}.png`), fullPage: true });
    await dialog.getByRole('button', { name: '취소', exact: true }).click();
    for (const [nextStatus, nextJudgment] of [['hidden', 'needs_review'], ['deleted', 'needs_review'], ['active', 'confirmed'], ['hidden', 'failed']]) {
      status = nextStatus; judgmentStatus = nextJudgment;
      await page.reload(); await ready();
      assert.equal(await page.getByRole('button', { name: '승인 내용 확인' }).isDisabled(), true);
      assert.equal(await page.getByRole('button', { name: '반려 내용 확인' }).isDisabled(), true);
    }
    status = 'pending_review'; judgmentStatus = 'needs_review';
    await page.reload(); await ready();
    await page.getByRole('button', { name: '승인 내용 확인' }).click();
    await dialog.getByLabel('조치 사유 (필수)').fill('회수 후 제거할 사유');
    failure = 403;
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await page.locator('[data-admin-detail="forbidden"]').waitFor();
    assert.equal(await page.locator('dialog').count(), 0);
    assert.doesNotMatch(await page.locator('main').innerText(), /회수 후 제거할 사유|승인 완료|반려 완료/);
    failure = null; await page.reload(); await ready();
    await page.getByRole('button', { name: '승인 내용 확인' }).click();
    assert.equal(await dialog.getByLabel('조치 사유 (필수)').inputValue(), '');
    failure = 401; await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await page.locator('[data-admin-detail="login-required"]').waitFor();
    assert.equal(await page.locator('dialog').count(), 0);
    assert.deepEqual(writes, []); assert.deepEqual(errors, []);
    results.push({ width, passed: true, checks: ['approve/reject confirmation', 'reason validation', 'unsaved-only feedback',
      'execution disabled', 'no POST', 'Escape/focus restoration', 'cancel clears reason', 'state eligibility', '401/403 clears dialog', 'no overflow', 'list query preserved'] });
  } finally { await context.close(); }
}

(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: process.env.PIVOT_BROWSER_CHANNEL || 'msedge' });
  try { for (const width of [1728, 390]) await verify(browser, width); }
  finally { await browser.close(); fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(results, null, 2)); }
  console.log(JSON.stringify(results, null, 2));
})().catch(error => { console.error(error); process.exitCode = 1; });
