// Test-only HTTP interception. Every API is local, read-only, and isolated.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const base = process.argv[2] || 'http://127.0.0.1:3197';
const output = process.argv[3] || path.join(require('node:os').tmpdir(), 'admin-content-input');
const local = url => url.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(url.hostname);
assert.ok(local(new URL(base)));
const results = [];

async function verify(browser, width) {
  const context = await browser.newContext({ viewport: { width, height: 1117 }, serviceWorkers: 'block' });
  const page = await context.newPage(); page.setDefaultTimeout(15000); page.setDefaultNavigationTimeout(10000);
  let mode = 'normal', authenticated = true, hold = false, release;
  const unexpected = [], errors = [], calls = [];
  const item = { contentId: 7, title: '원래 제목', status: 'active', judgmentStatus: 'confirmed', createdAt: '2026-11-13T00:00:00Z', publishedAt: null };
  const detail = { ...item, summary: '기존 요약', body: '<script>window.injected = true</script>\n기존 본문',
    sources: [{ url: 'https://example.org/original', title: '기존 출처', publisher: '기존 발행처', role: 'primary', citationOrder: 7 }] };
  page.on('pageerror', error => errors.push(error.message));
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (!local(url)) { unexpected.push(url.origin); return route.abort(); }
    if (url.pathname === '/favicon.ico') return route.fulfill({ status: 204 });
    if (!url.pathname.startsWith('/api/')) return route.continue();
    calls.push({ path: url.pathname, method: route.request().method() });
    assert.equal(route.request().method(), 'GET', 'The input slice must never send writes');
    const reply = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) }).catch(() => {});
    if (url.pathname === '/api/v1/auth/session') return reply({ authenticated, sessionState: authenticated ? 'active' : 'anonymous',
      user: authenticated ? { id: 7, accountType: 'user' } : null, onboardingCompleted: authenticated, csrfToken: 'fixture' });
    if (url.pathname.startsWith('/api/v1/admin/contents')) {
      if (hold) { hold = false; await new Promise(resolve => release = resolve); }
      if (mode === '403') return reply({ code: 'ADMIN_REQUIRED' }, 403);
      if (mode === '401') return reply({ code: 'AUTH_REQUIRED' }, 401);
      if (mode === 'missing') return reply({ code: 'CONTENT_NOT_FOUND' }, 404);
      if (mode === 'unavailable') return reply({ code: 'NOT_FOUND' }, 404);
      if (mode === 'error') return reply({ message: 'private error' }, 503);
      if (url.pathname.endsWith('/7')) return reply(detail);
      if (url.pathname === '/api/v1/admin/contents') return reply({ items: [item], page: Number(url.searchParams.get('page')),
        pageSize: Number(url.searchParams.get('pageSize')), total: 1 });
    }
    unexpected.push(url.pathname); return route.abort();
  });
  const ready = async () => { await page.locator('[data-admin-editor="ready"] [data-admin-form]').waitFor(); };
  const dialogAction = async (action, accept, type) => {
    let seen = false;
    page.once('dialog', async dialog => { seen = true; if (type) assert.equal(dialog.type(), type); await (accept ? dialog.accept() : dialog.dismiss()); });
    await action();
    assert.ok(seen, 'Expected unsaved-change confirmation');
  };
  let step = 'create entry';
  try {
    await page.goto(`${base}/admin/contents?q=원래&page=1&pageSize=20`);
    await page.getByRole('link', { name: '콘텐츠 등록', exact: true }).click(); await ready();
    assert.match(page.url(), /\/new\?q=/);
    assert.equal(await page.getByLabel('제목 (필수)', { exact: true }).inputValue(), '');
    assert.equal(await page.getByRole('button', { name: '저장 준비 중' }).isDisabled(), true);
    await page.getByRole('button', { name: '입력 확인' }).click();
    await page.getByText('필수 입력값입니다.', { exact: true }).waitFor();
    assert.equal(await page.getByLabel('제목 (필수)', { exact: true }).evaluate(element => document.activeElement === element), true);
    await page.getByLabel('제목 (필수)', { exact: true }).fill('직접 등록 제목');
    await page.getByLabel('요약 (선택)', { exact: true }).fill('요약 입력');
    await page.getByLabel('본문 (선택)', { exact: true }).fill('본문 입력\n두 번째 줄');
    await page.getByRole('button', { name: '출처 추가', exact: true }).click();
    await page.getByLabel('출처 1 URL (필수)', { exact: true }).fill('javascript:alert(1)');
    await page.getByRole('button', { name: '입력 확인' }).click();
    await page.getByText('계정 정보가 없는 올바른 http 또는 https URL을 입력해 주세요.').waitFor();
    await page.getByLabel('출처 1 URL (필수)', { exact: true }).fill('https://example.org/article');
    await page.getByLabel('출처 1 제목 (선택)', { exact: true }).fill('출처 제목');
    await page.getByRole('button', { name: '출처 추가', exact: true }).click();
    await page.getByLabel('출처 2 URL (필수)', { exact: true }).fill('https://example.org/article');
    await page.getByRole('button', { name: '입력 확인' }).click();
    await page.getByText('같은 출처 URL이 이미 있습니다.').waitFor();
    await page.getByRole('button', { name: '출처 2 삭제', exact: true }).click();
    await page.getByRole('button', { name: '입력 확인' }).click();
    await page.getByText('입력 확인을 마쳤습니다. 아직 저장되지 않았습니다.').waitFor();
    assert.equal(await page.getByRole('button', { name: '저장 준비 중' }).isDisabled(), true);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({ path: path.join(output, `create-${width}.png`), fullPage: true });

    step = 'recheck preserves dirty values'; console.log(width, step); hold = true;
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await page.locator('[data-admin-editor="loading"]').waitFor();
    assert.equal(await page.locator('[data-admin-form]').isVisible(), false);
    await page.waitForFunction(() => document.querySelector('[data-admin-editor="loading"]'));
    for (let attempt = 0; attempt < 200 && !release; attempt++) await page.waitForTimeout(10);
    assert.ok(release); release(); release = null; await ready();
    assert.equal(await page.getByLabel('제목 (필수)', { exact: true }).inputValue(), '직접 등록 제목');

    step = 'links, reload and browser back guard'; console.log(width, step);
    await dialogAction(() => page.getByRole('link', { name: '← 콘텐츠 목록', exact: true }).click(), false, 'confirm');
    await dialogAction(() => page.getByRole('link', { name: '콘텐츠 관리', exact: true }).click(), false, 'confirm');
    await dialogAction(() => page.reload().catch(() => {}), false, 'beforeunload');
    assert.equal(await page.getByLabel('제목 (필수)', { exact: true }).inputValue(), '직접 등록 제목');
    await dialogAction(() => page.goBack().catch(() => {}), false, 'beforeunload');
    assert.match(page.url(), /\/new\?/);
    await dialogAction(() => page.getByRole('link', { name: '← 콘텐츠 목록', exact: true }).click(), true, 'confirm');
    await page.locator('[data-admin-content="7"]').waitFor();
    assert.equal(new URL(page.url()).searchParams.get('q'), '원래');

    step = 'edit prefill and clean navigation'; console.log(width, step);
    await page.locator('[data-admin-content="7"] a').click();
    await page.getByRole('link', { name: '콘텐츠 수정', exact: true }).click(); await ready();
    assert.equal(await page.getByLabel('제목 (필수)', { exact: true }).inputValue(), detail.title);
    assert.equal(await page.getByLabel('요약 (선택)', { exact: true }).inputValue(), detail.summary);
    assert.equal(await page.getByLabel('본문 (선택)', { exact: true }).inputValue(), detail.body);
    assert.equal(await page.getByLabel('출처 1 URL (필수)', { exact: true }).inputValue(), detail.sources[0].url);
    assert.equal(await page.getByLabel('출처 1 제목 (선택)', { exact: true }).inputValue(), detail.sources[0].title);
    assert.equal(await page.evaluate(() => window.injected), undefined);
    await page.getByLabel('제목 (필수)', { exact: true }).fill('수정 제목');
    await page.getByLabel('제목 (필수)', { exact: true }).fill(detail.title);
    await page.getByRole('link', { name: '← 콘텐츠 상세', exact: true }).click();
    await page.getByRole('link', { name: '콘텐츠 수정', exact: true }).click(); await ready();
    await page.getByLabel('본문 (선택)', { exact: true }).fill('수정 본문');
    await dialogAction(() => page.goBack().catch(() => {}), true, 'beforeunload');
    await page.getByRole('link', { name: '콘텐츠 수정', exact: true }).click(); await ready();
    assert.equal(await page.getByLabel('본문 (선택)', { exact: true }).inputValue(), detail.body);
    await page.screenshot({ path: path.join(output, `edit-${width}.png`), fullPage: true });

    step = 'retry retains input, permission revocation clears input'; console.log(width, step);
    await page.getByLabel('제목 (필수)', { exact: true }).fill('보존할 수정 제목');
    mode = 'error'; await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await page.locator('[data-admin-editor="error"]').waitFor();
    assert.equal(await page.locator('[data-admin-form]').isVisible(), false);
    mode = 'normal'; await page.getByRole('button', { name: '다시 시도', exact: true }).click(); await ready();
    assert.equal(await page.getByLabel('제목 (필수)', { exact: true }).inputValue(), '보존할 수정 제목');
    mode = '403'; await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await page.locator('[data-admin-editor="forbidden"]').waitFor();
    assert.equal(await page.locator('[data-admin-form]').count(), 0);
    mode = 'normal'; await page.reload(); await ready();
    assert.equal(await page.getByLabel('제목 (필수)', { exact: true }).inputValue(), detail.title);
    for (const [next, status] of [['401', 'login-required'], ['unavailable', 'unavailable'], ['missing', 'not-found']]) {
      mode = next; await page.reload(); await page.locator(`[data-admin-editor="${status}"]`).waitFor();
      assert.equal(await page.locator('[data-admin-form]').count(), 0);
    }
    mode = 'normal'; authenticated = false;
    const before = calls.filter(call => call.path.startsWith('/api/v1/admin/')).length;
    await page.goto(`${base}/admin/contents/new`); await page.locator('[data-admin-editor="login-required"]').waitFor();
    assert.equal(calls.filter(call => call.path.startsWith('/api/v1/admin/')).length, before);
    assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
    results.push({ width, passed: true, apiMethods: [...new Set(calls.map(call => call.method))],
      checks: ['create/edit', 'required/URL/duplicate errors', 'prefill', 'disabled save', 'no fake success', 'read refresh retains input',
        'link/sidebar/reload/back guard', 'discard/restore', 'query return', 'XSS text', 'retry', '401/403/404', 'revocation clears input', 'anonymous', 'responsive'] });
  } catch (error) {
    await page.screenshot({ path: path.join(output, `failure-${width}.png`), fullPage: true });
    throw new Error(`${width}px ${step}: ${error.message}`, { cause: error });
  } finally { release?.(); await context.close(); }
}

(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: process.env.PIVOT_BROWSER_CHANNEL || 'msedge' });
  try { for (const width of [1728, 390]) await verify(browser, width); }
  finally { await browser.close(); fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(results, null, 2)); }
  console.log(JSON.stringify(results, null, 2));
})().catch(error => { console.error(error); process.exitCode = 1; });
