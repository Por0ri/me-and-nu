// Requires tests/serve_admin_review_fixture.py: real HTTP/SQL, disposable data.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const base = 'http://127.0.0.1:3197';
const api = 'http://127.0.0.1:8197';
const output = process.argv[2] || path.join(require('node:os').tmpdir(), 'admin-review');
const allIds = [8, 7, 6, 5, 4, 3, 11, 1, 12, 13, 10, 9, 2];
const reviewIds = [10, 9, 2, 11, 1, 12, 8, 7, 13];
const results = [];

async function verify(browser, width) {
  const context = await browser.newContext({ viewport: { width, height: 1117 }, serviceWorkers: 'block' });
  await context.addCookies([{ name: 'menu_session', value: 'isolated-admin-api-test-cookie', url: api, httpOnly: true }]);
  const page = await context.newPage(); page.setDefaultTimeout(15000);
  const errors = [], unexpected = [], calls = [];
  page.on('pageerror', error => errors.push(error.message));
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    if (![base, api].includes(url.origin)) { unexpected.push(request.url()); return route.abort(); }
    if (url.pathname.startsWith('/api/')) {
      if (request.method() !== 'GET') { unexpected.push(request.method() + ' ' + url.pathname); return route.abort(); }
      calls.push(url.pathname + url.search);
    }
    return route.continue();
  });
  const rows = page.locator('[data-admin-content]');
  const ready = () => page.locator('section[data-admin-state="ready"]').waitFor();
  const ids = () => rows.evaluateAll(nodes => nodes.map(node => Number(node.dataset.adminContent)));
  const total = () => page.locator('section p > strong').innerText();
  const navigate = async (action, expectedIds, expectedTotal) => {
    const response = page.waitForResponse(res => new URL(res.url()).pathname === '/api/v1/admin/contents');
    await action(); const res = await response;
    assert.equal(res.status(), 200);
    await ready();
    await page.waitForFunction(expected => JSON.stringify(Array.from(document.querySelectorAll('[data-admin-content]'), el => Number(el.dataset.adminContent))) === JSON.stringify(expected), expectedIds);
    assert.deepEqual(await ids(), expectedIds);
    assert.equal(await total(), String(expectedTotal));
    const body = await res.json();
    assert.deepEqual(body.items.map(item => item.contentId), expectedIds);
    assert.equal(body.total, expectedTotal);
  };
  try {
    await navigate(() => page.goto(`${base}/admin/contents?page=1&pageSize=2`), allIds.slice(0, 2), 13);
    assert.equal(await page.getByRole('button', { name: '전체 콘텐츠', exact: true }).getAttribute('aria-pressed'), 'true');
    await navigate(() => page.getByRole('button', { name: '검수 대기', exact: true }).click(), [10, 9], 9);
    assert.equal(await page.getByRole('button', { name: '검수 대기', exact: true }).getAttribute('aria-pressed'), 'true');
    assert.equal(await rows.getByText('우선 확인', { exact: true }).count(), 2);
    assert.doesNotMatch(await page.locator('main').innerText(), /공개 중|미리보기 중/);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({ path: path.join(output, `review-${width}.png`), fullPage: true });

    // Every server page, including the mixed-priority boundary, appears unchanged.
    const seen = await ids();
    for (let number = 2; number <= 5; number++) {
      await navigate(() => page.getByRole('button', { name: '다음 페이지' }).click(), reviewIds.slice((number - 1) * 2, number * 2), 9);
      seen.push(...await ids());
    }
    assert.deepEqual(seen, reviewIds);
    assert.equal(await page.getByRole('button', { name: '다음 페이지' }).isDisabled(), true);
    await navigate(() => page.getByRole('button', { name: '이전 페이지' }).click(), [8, 7], 9);
    assert.equal(await rows.getByText('상태 확인 필요', { exact: true }).count(), 2);

    await navigate(() => page.getByLabel('콘텐츠 상태', { exact: true }).selectOption('active'), [10, 9], 3);
    assert.equal(new URL(page.url()).searchParams.get('page'), '1');
    await page.getByRole('textbox', { name: '콘텐츠명 검색' }).fill(' 달빛 ');
    await navigate(() => page.getByRole('button', { name: '검색', exact: true }).click(), [10, 9], 3);
    await navigate(() => page.getByLabel('검수 상태', { exact: true }).selectOption('needs_review'), [10, 9], 3);
    await navigate(() => page.getByRole('button', { name: '다음 페이지' }).click(), [2], 3);
    const listUrl = page.url(), listQuery = new URL(listUrl).search;
    await rows.locator('a').click(); await page.locator('[data-admin-detail="ready"]').waitFor();
    assert.equal(new URL(page.url()).search, listQuery);
    await navigate(() => page.getByRole('link', { name: '← 콘텐츠 목록' }).click(), [2], 3);
    assert.equal(page.url(), listUrl);
    assert.equal(await page.getByLabel('콘텐츠 상태', { exact: true }).inputValue(), 'active');
    assert.equal(await page.getByLabel('검수 상태', { exact: true }).inputValue(), 'needs_review');
    assert.equal(await page.getByRole('textbox', { name: '콘텐츠명 검색' }).inputValue(), '달빛');
    assert.equal(await page.getByLabel('페이지당', { exact: true }).inputValue(), '2');
    await rows.locator('a').click(); await page.locator('[data-admin-detail="ready"]').waitFor();
    await navigate(() => page.goBack(), [2], 3);
    assert.equal(page.url(), listUrl);
    await navigate(() => page.reload(), [2], 3);
    assert.equal(page.url(), listUrl);

    await navigate(() => page.getByLabel('콘텐츠 상태', { exact: true }).selectOption('pending_review'), [11, 1], 2);
    assert.equal(await rows.getByText('일반 비공개 대기', { exact: true }).count(), 2);
    await navigate(() => page.getByLabel('검수 상태', { exact: true }).selectOption('confirmed'), [], 0);
    await page.locator('[data-admin-empty]').waitFor();
    await navigate(() => page.getByRole('button', { name: '전체 콘텐츠', exact: true }).click(), [5], 1);
    assert.equal(new URL(page.url()).searchParams.has('mode'), false);
    assert.equal(await page.getByLabel('검수 상태', { exact: true }).inputValue(), 'confirmed');

    await navigate(() => page.goto(`${base}/admin/contents?mode=review&page=1&pageSize=2`), [10, 9], 9);
    await page.getByRole('textbox', { name: '콘텐츠명 검색' }).fill('%_');
    await navigate(() => page.getByRole('button', { name: '검색', exact: true }).click(), [9], 1);
    await page.getByRole('textbox', { name: '콘텐츠명 검색' }).fill('달빛');
    await navigate(() => page.getByRole('button', { name: '검색', exact: true }).click(), [10, 9], 8);
    await navigate(() => page.getByLabel('페이지당', { exact: true }).selectOption('50'), [10, 9, 2, 11, 1, 8, 7, 13], 8);
    await navigate(() => page.goto(`${base}/admin/contents?mode=review&page=99&pageSize=2`), [], 9);
    await navigate(() => page.getByRole('button', { name: '첫 페이지로' }).click(), [10, 9], 9);
    await page.goto(`${base}/admin/contents?mode=invalid`);
    await page.getByRole('link', { name: '조건 초기화' }).waitFor();
    assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
    assert.ok(calls.some(url => url.includes('mode=review')));
    results.push({ width, passed: true, checks: ['real HTTP + SQLite', 'global priority + tie order', 'all-mode order', 'search/wildcards', 'independent/intersecting filters', 'pagination/total', 'detail link/back/reload restoration', 'page size', 'out-of-range page', 'GET only', 'no visibility claim', 'no horizontal overflow'] });
  } catch (error) {
    await page.screenshot({ path: path.join(output, `failure-${width}.png`), fullPage: true });
    throw new Error(`${width}px: ${error.message}`, { cause: error });
  } finally { await context.close(); }
}

(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: process.env.PIVOT_BROWSER_CHANNEL || 'msedge' });
  try { for (const width of [1728, 390]) await verify(browser, width); }
  finally { await browser.close(); fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(results, null, 2)); }
  console.log(JSON.stringify(results, null, 2));
})().catch(error => { console.error(error); process.exitCode = 1; });
