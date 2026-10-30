const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const { createApiFixture } = require('./helpers/pivot-api-fixture.cjs');

// Start Next separately with NEXT_PUBLIC_API_BASE_URL=http://localhost:8180 and
// NEXT_PUBLIC_PIVOT_ENABLE_LOCAL_API=true, then run this loopback-only check.
// The mock provider and session exist solely in this process, never in the app.
const base = process.argv[2] || 'http://localhost:3180';
const apiOrigin = process.env.PIVOT_FIXTURE_API_ORIGIN || 'http://localhost:8180';
const output = process.argv[3] || path.join(require('node:os').tmpdir(), 'pivot-service-browser-check');
for (const origin of [base, apiOrigin]) {
  const url = new URL(origin);
  assert.equal(url.protocol, 'http:');
  assert.equal(url.hostname, 'localhost', 'fixture only supports explicit localhost origins');
}
const fixture = createApiFixture({ apiOrigin, frontendOrigin: base });
const results = [];

async function waitUntil(predicate, message) {
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    if (await predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  throw new Error(message);
}
async function snapshot(page, filename) {
  await page.evaluate(() => document.fonts.ready);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'no horizontal overflow');
  await page.screenshot({ path: path.join(output, filename), fullPage: true });
}

(async () => {
  fs.mkdirSync(output, { recursive: true });
  const server = await fixture.listen();
  let browser;
  let page;
  const errors = [];
  const diagnostics = [];
  try {
    browser = await chromium.launch({ headless: true, ...(process.env.PIVOT_BROWSER_EXECUTABLE
      ? { executablePath: process.env.PIVOT_BROWSER_EXECUTABLE } : { channel: 'chrome' }) });
    const context = await browser.newContext({ viewport: { width: 390, height: 844 }, reducedMotion: 'reduce' });
    context.setDefaultTimeout(15000);
    context.setDefaultNavigationTimeout(60000);
    let holdProviderPage = true;
    page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') diagnostics.push({ console: message.text() }); });
    page.on('requestfailed', request => diagnostics.push({ failedPath: new URL(request.url()).pathname, failure: request.failure()?.errorText }));
    page.on('response', response => {
      const url = new URL(response.url());
      if (url.origin === base && url.pathname.startsWith('/pivot')) diagnostics.push({ route: url.pathname, status: response.status() });
    });
    for (const provider of ['kakao', 'naver']) {
      const host = provider === 'kakao' ? 'kauth.kakao.com' : 'nid.naver.com';
      await context.route(`https://${host}/**`, async route => {
        const url = new URL(route.request().url());
        assert.equal(url.searchParams.get('client_id'), 'fixture-client');
        assert.equal(url.searchParams.get('state'), 'fixture-state');
        assert.equal(url.searchParams.get('redirect_uri'), `${apiOrigin}/api/v1/auth/login/${provider}/callback`);
        if (holdProviderPage) {
          await route.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html><title>Test provider</title><h1>Mock provider page</h1>' });
          return;
        }
        await route.fulfill({ status: 302, headers: { location: `${apiOrigin}/api/v1/auth/login/${provider}/callback?code=fixture-code&state=fixture-state` } });
      });
    }

    await page.goto(`${base}/pivot/home`);
    await page.waitForURL('**/pivot/login');
    await page.getByRole('button', { name: '카카오 로그인', exact: true }).click();
    await page.waitForURL('https://kauth.kakao.com/**');
    await page.getByRole('heading', { name: 'Mock provider page', exact: true }).waitFor();
    await page.goBack();
    await page.waitForURL('**/pivot/login');
    await waitUntil(async () => await page.getByRole('button', { name: '카카오 로그인', exact: true }).isEnabled(), 'provider Back must unlock login');
    assert.equal(await page.getByRole('button', { name: '네이버 로그인', exact: true }).isEnabled(), true);
    results.push('provider page → browser Back leaves both login buttons usable');
    holdProviderPage = false;
    await page.getByRole('button', { name: '카카오 로그인', exact: true }).click();
    await page.waitForURL('**/pivot/home');
    await page.getByRole('button', { name: '연결 검증 콘텐츠 하나 자세히 보기', exact: true }).waitFor();
    const started = fixture.requests.find(request => request.path === '/api/v1/auth/login/kakao');
    assert.equal(started.method, 'POST'); assert.deepEqual(started.body, { purpose: 'signin' });
    assert.equal(started.csrf, 'fixture-anonymous-csrf'); assert.match(started.cookie, /fixture_session=fixture-anonymous/);
    assert.ok(fixture.requests.some(request => request.path === '/api/v1/topics/7/feed' && /fixture_session=fixture-active/.test(request.cookie)));
    results.push('anonymous route → Kakao button → mocked provider callback → authenticated Home');
    await snapshot(page, '01-authenticated-home.png');

    await page.getByRole('button', { name: '연결 검증 콘텐츠 하나 자세히 보기', exact: true }).click();
    await page.getByRole('article', { name: '연결 검증 콘텐츠 하나', exact: true }).waitFor();
    const saveButton = page.getByRole('button', { name: '저장', exact: true });
    await saveButton.click();
    await waitUntil(async () => await saveButton.getAttribute('aria-pressed') === 'true', 'detail bookmark should become saved');
    assert.equal(fixture.state.saved.has(41), true);
    const save = fixture.requests.find(request => request.method === 'PUT' && request.path.endsWith('/bookmark'));
    assert.deepEqual(save.body, { topicId: 7 }); assert.equal(save.csrf, 'fixture-active-csrf');
    await page.getByRole('button', { name: 'SAVE', exact: true }).click();
    await page.getByRole('heading', { name: '저장한 콘텐츠', exact: true }).waitFor();
    await page.getByRole('button', { name: '연결 검증 콘텐츠 하나 자세히 보기', exact: true }).waitFor();
    await page.reload();
    await page.getByRole('button', { name: '연결 검증 콘텐츠 하나 자세히 보기', exact: true }).waitFor();
    results.push('Home → Detail → bookmark PUT with rotated CSRF → Saved → reload preserves server state');
    await snapshot(page, '02-saved-after-refresh.png');

    await page.getByRole('button', { name: '연결 검증 콘텐츠 하나 자세히 보기', exact: true }).click();
    await page.getByRole('article', { name: '연결 검증 콘텐츠 하나', exact: true }).waitFor();
    await waitUntil(async () => await saveButton.getAttribute('aria-pressed') === 'true', 'Saved detail should show persisted bookmark');
    await saveButton.click();
    await waitUntil(async () => await saveButton.getAttribute('aria-pressed') === 'false', 'bookmark deletion should update detail');
    await page.getByRole('button', { name: 'SAVE', exact: true }).click();
    await page.getByText('저장한 콘텐츠가 아직 없어요', { exact: true }).waitFor();
    await page.reload();
    await page.getByText('저장한 콘텐츠가 아직 없어요', { exact: true }).waitFor();
    assert.equal(fixture.state.saved.size, 0);
    assert.ok(fixture.requests.some(request => request.method === 'DELETE' && request.query.topicId === '7'));
    results.push('Saved → Detail → bookmark DELETE → empty Saved survives reload');

    await page.getByRole('button', { name: 'HOME', exact: true }).click();
    await page.getByRole('button', { name: '연결 검증 콘텐츠 하나 자세히 보기', exact: true }).click();
    await page.getByRole('article', { name: '연결 검증 콘텐츠 하나', exact: true }).waitFor();
    fixture.state.failNext = { method: 'PUT', path: '/api/v1/contents/41/bookmark', status: 503 };
    await saveButton.click();
    await page.getByText(/잠시 후 다시 시도|저장.*못했|저장.*실패/).first().waitFor();
    assert.equal(await saveButton.getAttribute('aria-pressed'), 'false');
    assert.equal(fixture.state.saved.size, 0);
    results.push('failed save reports error without showing a false saved state');
    await snapshot(page, '03-save-error.png');

    fixture.rotate('anonymous');
    await page.reload();
    await page.waitForURL('**/pivot/login');
    await page.getByRole('button', { name: '네이버 로그인', exact: true }).click();
    await page.waitForURL('**/pivot/home');
    await page.getByRole('button', { name: '연결 검증 콘텐츠 하나 자세히 보기', exact: true }).waitFor();
    results.push('expired session returns to login; Naver starts a fresh authenticated session');
    assert.deepEqual(errors, [], 'no uncaught browser errors');
    fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify({ results, requestCount: fixture.requests.length }, null, 2));
    console.log(JSON.stringify({ passed: results.length, results, output }, null, 2));
  } catch (error) {
    if (page && !page.isClosed()) {
      await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true }).catch(() => {});
      fs.writeFileSync(path.join(output, 'failure.txt'), `${error.stack}\n\nURL: ${page.url()}\n\n${await page.locator('body').innerText().catch(() => '')}\n\n${JSON.stringify({ errors, diagnostics }, null, 2)}`);
    }
    throw error;
  } finally {
    await browser?.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
