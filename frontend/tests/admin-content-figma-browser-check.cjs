// Isolated HTTP fixtures only. No live admin/consumer data or write requests.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const base = process.argv[2] || 'http://127.0.0.1:3196';
const output = process.argv[3] || path.join(require('node:os').tmpdir(), 'admin-content-figma');
const results = [];
const local = url => url.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(url.hostname);
assert.ok(local(new URL(base)));

async function verify(browser, width) {
  const context = await browser.newContext({ viewport: { width, height: 1117 }, serviceWorkers: 'block' });
  const page = await context.newPage(); page.setDefaultTimeout(15000);
  const calls = [], errors = [], unexpected = [];
  let limit = 5, failure = 0;
  const records = Array.from({ length: 45 }, (_, index) => ({
    contentId: 45 - index, title: `달빛천사 OST, 오래 사랑받는 이유${index === 9 ? ' %_' : ''}`,
    judgmentStatus: ['needs_review', 'confirmed', 'needs_review', 'needs_review', 'failed'][index % 5],
    status: ['active', 'active', 'pending_review', 'hidden', 'deleted'][index % 5],
    createdAt: '2026-11-05T00:00:00Z', publishedAt: null,
  }));
  page.on('pageerror', error => errors.push(error.message));
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (!local(url)) { unexpected.push(url.origin); return route.abort(); }
    if (!url.pathname.startsWith('/api/')) return route.continue();
    assert.equal(route.request().method(), 'GET'); calls.push(url.pathname + url.search);
    const reply = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (url.pathname === '/api/v1/auth/session') return reply({ authenticated: true, sessionState: 'active', user: { id: 7, accountType: 'user' }, onboardingCompleted: true, csrfToken: 'fixture' });
    if (url.pathname === '/api/v1/admin/contents') {
      if (failure) return reply({ code: failure === 403 ? 'ADMIN_REQUIRED' : 'NOT_FOUND' }, failure);
      const params = url.searchParams, pageNumber = Number(params.get('page')), pageSize = Number(params.get('pageSize'));
      let items = records.slice(0, limit).filter(item => (!params.get('q') || item.title.includes(params.get('q'))) &&
        (!params.get('status') || item.status === params.get('status')) && (!params.get('judgmentStatus') || item.judgmentStatus === params.get('judgmentStatus')));
      if (params.get('mode') === 'review') items = items.filter(item => item.judgmentStatus === 'needs_review')
        .sort((a, b) => ['active', 'pending_review', 'hidden', 'deleted'].indexOf(a.status) - ['active', 'pending_review', 'hidden', 'deleted'].indexOf(b.status));
      return reply({ items: items.slice((pageNumber - 1) * pageSize, pageNumber * pageSize), page: pageNumber, pageSize, total: items.length });
    }
    if (/^\/api\/v1\/admin\/contents\/\d+$/.test(url.pathname)) return reply({ ...records.find(item => item.contentId === Number(url.pathname.split('/').at(-1))),
      updatedAt: '2026-11-13T09:12:34.123456+09:00', summary: '테스트 전용 요약', body: '테스트 전용 본문', sources: [] });
    unexpected.push(url.pathname); return route.abort();
  });
  const ready = () => page.locator('section[data-admin-state="ready"]').waitFor();
  const rows = page.locator('[data-admin-content]');
  const navigate = async (action, expected = {}) => {
    const response = page.waitForResponse(res => {
      const url = new URL(res.url());
      return url.pathname === '/api/v1/admin/contents' && Object.entries(expected).every(([key, value]) => url.searchParams.get(key) === value);
    });
    await action(); await response; await ready();
  };
  const noOverflow = async () => {
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth ?
      [...document.querySelectorAll('body *')].filter(node => node.getBoundingClientRect().right > innerWidth && getComputedStyle(node).position === 'absolute')
        .map(node => ({ tag: node.tagName, className: node.className, x: node.getBoundingClientRect().x, text: node.textContent.slice(0, 60) })) : null);
    assert.equal(overflow, null, JSON.stringify(overflow));
  };
  try {
    await navigate(() => page.goto(`${base}/admin/contents`));
    await page.evaluate(() => document.fonts.ready);
    assert.equal(await rows.count(), 5);
    const stats = page.locator('[data-admin-statistics]');
    assert.equal(await stats.locator('article').count(), 3);
    assert.doesNotMatch(await stats.innerText(), /\d[,.]?\d*%|11,578|1,247/);
    for (const label of ['게시 중', '예약 발행', '신고 접수']) assert.equal(await page.getByRole('button', { name: label, exact: true }).isDisabled(), true);
    assert.equal(await rows.getByText('게시 중', { exact: true }).count(), 0);
    assert.equal(await rows.getByLabel('분류 연결 전').count(), 5);
    const assets = await page.locator('img[src^="/admin/"]').evaluateAll(async images => {
      await Promise.all(images.map(img => img.decode()));
      return images.map(img => ({ src: img.getAttribute('src'), width: img.getBoundingClientRect().width, height: img.getBoundingClientRect().height,
        naturalWidth: img.naturalWidth, naturalHeight: img.naturalHeight, visible: !!img.getClientRects().length }));
    });
    assert.equal(assets.length, 14);
    for (const asset of assets) {
      assert.ok(asset.naturalWidth > 0);
      if (asset.visible) {
        assert.ok(Math.abs(asset.width - asset.naturalWidth) < 1, `${asset.src} width`);
        assert.ok(Math.abs(asset.height - asset.naturalHeight) < 1, `${asset.src} height`);
      }
    }
    const geometry = await page.locator('aside, header, main, [data-admin-statistics], [data-admin-statistics] article, section[data-admin-state]').evaluateAll(nodes =>
      nodes.map(node => { const box = node.getBoundingClientRect(); return { tag: node.tagName, x: box.x, y: box.y, width: box.width, height: box.height }; }));
    if (width === 1728) {
      assert.equal(geometry[0].width, 264); assert.equal(geometry[1].height, 84);
      const cards = geometry.filter(node => node.tag === 'ARTICLE');
      assert.ok(cards.every(card => card.height === 252 && card.y === 206));
      assert.equal(geometry.at(-1).y, 490);
    }
    await noOverflow();
    await page.screenshot({ path: path.join(output, `contents-${width}.png`), fullPage: true });
    await page.getByText('표시 정보 안내 · 일부 항목 준비 중', { exact: true }).click();
    await page.getByText('검수 상태와 콘텐츠 상태는 별개입니다.', { exact: false }).waitFor();
    if (width === 390) {
      const table = page.getByRole('region', { name: '콘텐츠 목록 표, 가로 스크롤 가능' });
      assert.equal(await table.evaluate(node => node.scrollWidth > node.clientWidth), true);
      await table.focus(); await page.keyboard.press('ArrowRight');
      await page.waitForFunction(() => document.querySelector('[role="region"]').scrollLeft > 0);
      await noOverflow();
    }
    limit = 45;
    await navigate(() => page.reload());
    await navigate(() => page.getByRole('button', { name: '다음 페이지' }).click(), { page: '2' });
    const listUrl = page.url();
    await rows.first().getByRole('link').click(); await page.locator('[data-admin-detail="ready"]').waitFor();
    await noOverflow();
    await navigate(() => page.getByRole('link', { name: '← 콘텐츠 목록' }).click(), { page: '2' });
    assert.equal(page.url(), listUrl);
    await page.getByRole('textbox', { name: '콘텐츠명 검색' }).fill('%_');
    await navigate(() => page.getByRole('button', { name: '검색', exact: true }).click(), { q: '%_', page: '1' });
    assert.equal(await rows.count(), 1);
    await page.getByRole('textbox', { name: '콘텐츠명 검색' }).fill('');
    await navigate(() => page.getByRole('textbox', { name: '콘텐츠명 검색' }).press('Enter'), { page: '1' });
    await navigate(() => page.getByRole('button', { name: '검수 대기', exact: true }).click(), { mode: 'review' });
    assert.equal(await rows.first().getByText('우선 확인', { exact: true }).count(), 1);
    await page.getByRole('button', { name: '상세 필터', exact: true }).click();
    await navigate(() => page.getByLabel('콘텐츠 상태', { exact: true }).selectOption('pending_review'), { mode: 'review', status: 'pending_review' });
    assert.equal(await rows.count(), 9);
    await navigate(() => page.getByLabel('페이지당', { exact: true }).selectOption('50'), { pageSize: '50', page: '1' });
    await noOverflow();
    await page.screenshot({ path: path.join(output, `review-filters-${width}.png`), fullPage: true });
    const reviewUrl = page.url();
    await rows.first().getByRole('link').click(); await page.locator('[data-admin-detail="ready"]').waitFor();
    await navigate(() => page.goBack(), { mode: 'review', status: 'pending_review', pageSize: '50' });
    assert.equal(page.url(), reviewUrl);
    await navigate(() => page.reload(), { mode: 'review', status: 'pending_review', pageSize: '50' });
    assert.equal(await page.getByLabel('콘텐츠 상태', { exact: true }).inputValue(), 'pending_review');
    await navigate(() => page.getByLabel('검수 상태', { exact: true }).selectOption('confirmed'), { judgmentStatus: 'confirmed' });
    await page.locator('[data-admin-empty]').waitFor();
    await navigate(() => page.getByRole('button', { name: '전체 콘텐츠', exact: true }).click());
    assert.equal(new URL(page.url()).searchParams.has('mode'), false);
    assert.equal(await page.getByLabel('검수 상태', { exact: true }).inputValue(), 'confirmed');
    for (const [code, state] of [[403, 'forbidden'], [404, 'unavailable']]) {
      failure = code; await page.reload(); await page.locator(`section[data-admin-state="${state}"]`).waitFor();
      assert.equal(await rows.count(), 0); assert.equal(await page.locator('[data-admin-empty]').count(), 0); await noOverflow();
    }
    await page.screenshot({ path: path.join(output, `unavailable-${width}.png`), fullPage: true });
    assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
    results.push({ width, passed: true, geometry, assets, requests: calls.length, checks: ['Figma geometry', 'all static asset sizes', 'honest missing metrics/fields/tabs', 'search/Enter', 'pagination', 'detail/back/reload URL', 'review priority', 'independent filters/empty', 'page size', '403/404', 'GET only', 'no viewport overflow', 'keyboard table scroll'] });
  } catch (error) {
    await page.screenshot({ path: path.join(output, `failure-${width}.png`), fullPage: true });
    throw new Error(`${width}px ${page.url()}: ${error.message}`, { cause: error });
  } finally { await context.close(); }
}

(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: process.env.PIVOT_BROWSER_CHANNEL || 'msedge' });
  try { for (const width of process.argv[4] ? [Number(process.argv[4])] : [1728, 390]) await verify(browser, width); }
  finally { await browser.close(); fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(results, null, 2)); }
  console.log(JSON.stringify(results.map(({ width, passed, checks }) => ({ width, passed, checks })), null, 2));
})().catch(error => { console.error(error); process.exitCode = 1; });
