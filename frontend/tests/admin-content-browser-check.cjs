// Test-only HTTP fixtures. Every API request is intercepted; no operating data is accessed.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const base = process.argv[2] || 'http://127.0.0.1:3196';
const output = process.argv[3] || path.join(require('node:os').tmpdir(), 'admin-content');
const local = url => ['localhost', '127.0.0.1'].includes(url.hostname) && url.protocol === 'http:';
assert.ok(local(new URL(base)));
const results = [];

async function verify(browser, width) {
  const context = await browser.newContext({ viewport: { width, height: 1117 }, reducedMotion: 'reduce', serviceWorkers: 'block' });
  const page = await context.newPage(); page.setDefaultTimeout(15000);
  let mode = 'normal', authenticated = true, holdNext = false, releases = [], step = 'list';
  const calls = [], errors = [], unexpected = [];
  const item = id => ({ contentId: id, title: `달빛천사 OST, 오래 사랑받는 이유 ${id}`, judgmentStatus: 'needs_review', status: 'active',
    createdAt: '2026-11-12T00:00:00Z', publishedAt: null });
  page.on('pageerror', error => errors.push(error.message));
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (!local(url)) { unexpected.push(url.origin); return route.abort(); }
    if (url.pathname === '/favicon.ico') return route.fulfill({ status: 204 });
    if (!url.pathname.startsWith('/api/')) return route.continue();
    calls.push(url.pathname + url.search);
    const reply = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) }).catch(() => {});
    if (url.pathname === '/api/v1/auth/session') return reply({ authenticated, sessionState: authenticated ? 'active' : 'anonymous',
      user: authenticated ? { id: 7, accountType: 'user' } : null, onboardingCompleted: authenticated, csrfToken: 'fixture' });
    if (url.pathname === '/api/v1/admin/contents') {
      const current = mode;
      if (holdNext) { holdNext = false; await new Promise(resolve => releases.push(resolve)); }
      if (['401', '403'].includes(current)) return reply({ code: current === '401' ? 'AUTH_REQUIRED' : 'ADMIN_REQUIRED' }, Number(current));
      if (current === 'unavailable') return reply({ code: 'NOT_FOUND' }, 404);
      if (current === 'error') return reply({ message: 'private internal failure' }, 503);
      const number = Number(url.searchParams.get('page')), pageSize = Number(url.searchParams.get('pageSize'));
      assert.ok(number > 0 && pageSize > 0 && pageSize <= 100);
      const total = current === 'empty' ? 0 : 45;
      const ids = Array.from({ length: Math.max(0, Math.min(pageSize, total - (number - 1) * pageSize)) }, (_, index) => total - (number - 1) * pageSize - index);
      return reply({ items: ids.map(item), page: number, pageSize, total });
    }
    if (/^\/api\/v1\/admin\/contents\/\d+$/.test(url.pathname)) {
      if (mode === 'detail-missing') return reply({ code: 'CONTENT_NOT_FOUND' }, 404);
      return reply({ ...item(Number(url.pathname.split('/').at(-1))), summary: '요약', body: '<script>window.adminInjected = true</script>\n본문 확인', sources: [
        { url: 'https://example.com/source', title: '참고 출처', publisher: null, role: 'primary', citationOrder: 1 },
        { url: 'javascript:alert(1)', title: '잘못된 출처', publisher: null, role: 'material', citationOrder: 2 },
      ] });
    }
    unexpected.push(url.pathname); return route.abort();
  });
  const releaseAll = () => releases.splice(0).forEach(resolve => resolve());
  const untilHeld = async () => {
    for (let attempt = 0; attempt < 750 && !releases.length; attempt++) await page.waitForTimeout(20);
    assert.ok(releases.length, 'Expected a held request within 15 seconds');
  };
  const ready = async (count = 20) => { await page.locator('[data-admin-content]').nth(count - 1).waitFor(); assert.equal(await page.locator('[data-admin-content]').count(), count); };
  const lastList = () => new URL(calls.filter(url => url.startsWith('/api/v1/admin/contents?')).at(-1), base);
  const changeList = async (action, expected, count = 20) => {
    const response = page.waitForResponse(response => {
      const url = new URL(response.url());
      return url.pathname === '/api/v1/admin/contents' && Object.entries(expected).every(([key, value]) => url.searchParams.get(key) === value);
    });
    await action(); await response; await ready(count);
  };
  try {
    holdNext = true; await page.goto(`${base}/admin`);
    await page.getByText('콘텐츠를 불러오는 중입니다', { exact: true }).waitFor();
    await page.waitForFunction(() => document.querySelector('section[data-admin-state="loading"]'));
    await untilHeld();
    releaseAll(); await ready();
    assert.equal(lastList().searchParams.has('judgmentStatus'), false); assert.equal(lastList().searchParams.has('status'), false);
    await page.evaluate(() => document.fonts.ready);
    const assets = await page.locator('img[src^="/admin/"]').evaluateAll(async images => {
      await Promise.all(images.map(img => img.decode()));
      return images.map(img => ({ src: img.getAttribute('src'), width: img.getBoundingClientRect().width, height: img.getBoundingClientRect().height, naturalWidth: img.naturalWidth }));
    });
    assert.ok(assets.every(asset => asset.naturalWidth > 0));
    assert.ok(Math.abs(assets.find(asset => asset.src === '/admin/logo.svg').width - 103) < 0.1);
    assert.equal(assets.find(asset => asset.src === '/admin/search.svg').width, 16);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({ path: path.join(output, `list-${width}.png`), fullPage: true });
    step = 'search and independent filters';
    await page.getByRole('textbox', { name: '콘텐츠명 검색' }).fill('달빛%_');
    await changeList(() => page.getByRole('button', { name: '검색', exact: true }).click(), { q: '달빛%_' });
    assert.equal(lastList().searchParams.get('q'), '달빛%_');
    await changeList(() => page.getByLabel('검수 상태', { exact: true }).selectOption('needs_review'), { judgmentStatus: 'needs_review' });
    await changeList(() => page.getByLabel('콘텐츠 상태', { exact: true }).selectOption('active'), { status: 'active' });
    assert.equal(lastList().searchParams.get('judgmentStatus'), 'needs_review'); assert.equal(lastList().searchParams.get('status'), 'active');
    step = 'pagination and detail round trip';
    await changeList(() => page.getByRole('button', { name: '다음 페이지' }).click(), { page: '2' });
    assert.equal(lastList().searchParams.get('page'), '2');
    const beforeDetail = page.url();
    await page.locator('[data-admin-content="25"] a').click();
    await page.locator('[data-admin-detail="ready"]').waitFor();
    assert.equal(await page.evaluate(() => window.adminInjected), undefined);
    assert.equal(await page.locator('a[href^="javascript:"]').count(), 0);
    await changeList(() => page.getByRole('link', { name: '← 콘텐츠 목록' }).click(), { page: '2' });
    assert.equal(page.url(), beforeDetail);
    await changeList(() => page.getByLabel('페이지당', { exact: true }).selectOption('50'), { page: '1', pageSize: '50' }, 45);
    assert.equal(lastList().searchParams.get('page'), '1'); assert.equal(lastList().searchParams.get('pageSize'), '50');
    step = 'empty and error retry';
    mode = 'empty'; await page.reload(); await page.locator('[data-admin-empty]').waitFor();
    mode = 'error'; await page.reload(); await page.getByRole('button', { name: '다시 시도', exact: true }).waitFor();
    assert.equal(await page.locator('[data-admin-empty]').count(), 0); assert.doesNotMatch(await page.locator('main').innerText(), /private internal/);
    mode = 'normal'; await page.getByRole('button', { name: '다시 시도', exact: true }).click(); await ready(45);
    step = 'access states';
    for (const [next, expected] of [['403', 'forbidden'], ['401', 'login-required'], ['unavailable', 'unavailable']]) {
      mode = next; await page.reload(); await page.locator(`section[data-admin-state="${expected}"]`).waitFor();
      assert.equal(await page.locator('[data-admin-content]').count(), 0);
    }
    await page.screenshot({ path: path.join(output, `unavailable-${width}.png`), fullPage: true });
    authenticated = false; const before = calls.filter(url => url.startsWith('/api/v1/admin/')).length;
    await page.reload(); await page.getByRole('link', { name: '로그인', exact: true }).waitFor();
    assert.equal(calls.filter(url => url.startsWith('/api/v1/admin/')).length, before);
    authenticated = true;
    step = 'late response cancellation';
    mode = 'normal'; holdNext = true; await page.goto(`${base}/admin/contents`);
    await untilHeld();
    mode = '403'; await page.getByLabel('콘텐츠 상태', { exact: true }).selectOption('hidden');
    await page.locator('section[data-admin-state="forbidden"]').waitFor(); releaseAll();
    assert.equal(await page.locator('[data-admin-content]').count(), 0);
    step = 'detail 404 and invalid URL';
    mode = 'detail-missing'; await page.goto(`${base}/admin/contents/999`); await page.locator('[data-admin-detail="not-found"]').waitFor();
    await page.goto(`${base}/admin/contents?page=0`); await page.getByRole('link', { name: '조건 초기화' }).waitFor();
    assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
    results.push({ width, passed: true, assets, checks: ['loading', 'search', 'independent filters', 'page/size', 'detail/return', 'empty', 'error/retry', '401/403', 'unregistered API', 'anonymous', 'late response', '404', 'invalid query', 'safe text/links'] });
  } catch (error) {
    await page.screenshot({ path: path.join(output, `failure-${width}.png`), fullPage: true });
    throw new Error(`${width}px ${step}: ${error.message}`, { cause: error });
  }
  finally { releaseAll(); await context.close(); }
}

(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: process.env.PIVOT_BROWSER_CHANNEL || 'msedge' });
  try { for (const width of [1728, 390]) await verify(browser, width); }
  finally { await browser.close(); fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(results, null, 2)); }
  console.log(JSON.stringify(results.map(({ width, passed, checks }) => ({ width, passed, checks })), null, 2));
})().catch(error => { console.error(error); process.exitCode = 1; });
