// Verify the real routes. No fixture data or live API traffic is used by this slice.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const { chromium } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const base = process.argv[2] || 'http://127.0.0.1:3220';
const output = process.argv[3] || path.resolve(__dirname, '../../.preview-pivot/admin-analytics-1118/results');
const local = url => url.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(url.hostname);
assert.ok(local(new URL(base)));
const results = [], manifest = require('../public/admin/statistics-sources.json');

async function verify(browser, width) {
  const context = await browser.newContext({ viewport: { width, height: 1117 }, serviceWorkers: 'block' });
  const page = await context.newPage(), errors = [], unexpected = [], assets = [], seen = new Set();
  page.setDefaultTimeout(15000); page.setDefaultNavigationTimeout(60000);
  page.on('pageerror', error => errors.push(error.message));
  page.on('response', response => { if (response.status() >= 400) unexpected.push(`${response.status()} ${new URL(response.url()).pathname}`); });
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (!local(url) || url.pathname.startsWith('/api/') || !['GET', 'HEAD'].includes(route.request().method())) { unexpected.push(`${route.request().method()} ${url.pathname}`); return route.abort(); }
    return route.continue();
  });
  const noOverflow = async () => assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'viewport overflow');
  const shot = async name => {
    console.log(`Checking ${name} at ${width}px`);
    await page.evaluate(() => document.fonts.ready); await noOverflow();
    for (const img of await page.locator('img:visible').all()) assert.equal(await img.evaluate(async node => { await node.decode(); return node.naturalWidth > 0; }), true);
    for (const asset of manifest.assets) for (const img of await page.locator(`img[src="/admin/${asset.file}"]:visible`).all()) {
      const box = await img.boundingBox(); assert.ok(Math.abs(box.width - asset.width) < .1, `${asset.file} width ${box.width}`);
      assert.ok(Math.abs(box.height - asset.height) < .1, `${asset.file} height ${box.height}`); assets.push({ file: asset.file, ...box }); seen.add(asset.file);
    }
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: path.join(output, `${name}-${width}.png`), fullPage: true, animations: 'disabled' });
  };
  const unknownOnly = async () => {
    assert.match(await page.locator('main').innerText(), /집계 준비 중/);
    assert.doesNotMatch(await page.locator('main').innerText(), /18,240|184,320|16,357|50명|11,578|0\.1667|64\.2%|33\.10%|EXP-32|11월 12일|0건/);
  };
  try {
    await page.goto(`${base}/admin/analytics?fixture=figma`); await page.locator('[data-analytics-view="overview"]').waitFor();
    assert.equal(await page.getByRole('link', { name: '데이터 분석', exact: true }).getAttribute('aria-current'), 'page');
    assert.equal(await page.locator('[data-statistics="unavailable"]').count(), 8); await unknownOnly();
    const geometry = await page.locator('aside, header, main, [data-statistics="unavailable"]').evaluateAll(nodes => nodes.map(node => { const b = node.getBoundingClientRect(); return { label: node.getAttribute('aria-label') || node.tagName, x: b.x, y: b.y, width: b.width, height: b.height }; }));
    if (width === 1728) { assert.equal(geometry[0].width, 264); assert.equal(geometry[1].height, 84); assert.equal(geometry[3].x, 300); assert.equal(geometry[3].y, 202); assert.equal(geometry[3].height, 300); }
    await shot('analytics-overview');
    const period = page.getByRole('button', { name: '조회 기간: 최근 7일' });
    await period.focus(); await page.keyboard.press('Enter'); await page.getByRole('menu', { name: '조회 기간' }).waitFor(); await shot('period-open');
    await page.keyboard.press('ArrowDown'); await page.keyboard.press('Enter');
    await page.waitForURL(url => url.searchParams.get('period') === '30d');
    assert.equal(await page.getByRole('button', { name: '조회 기간: 최근 30일' }).count(), 1);
    await page.getByRole('button', { name: '조회 기간: 최근 30일' }).click(); await page.keyboard.press('Escape');
    assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('aria-label')), '조회 기간: 최근 30일');
    await page.getByRole('button', { name: '조회 기간: 최근 30일' }).click(); await page.getByRole('heading', { name: '데이터 분석', exact: true }).click();
    assert.equal(await page.getByRole('menu').count(), 0);
    await page.getByRole('link', { name: '추천 성능', exact: true }).click(); await page.locator('[data-analytics-view="performance"]').waitFor();
    assert.equal(new URL(page.url()).pathname, '/admin/analytics'); assert.equal(new URL(page.url()).searchParams.get('period'), '30d');
    assert.match(await page.locator('header').innerText(), /데이터 분석 \/ 추천 성능/); await unknownOnly(); await shot('analytics-performance');
    await page.getByLabel('추천 성능 사용자 범위').selectOption('new'); await page.waitForURL(url => url.searchParams.get('audience') === 'new');
    await page.reload(); assert.equal(await page.getByLabel('추천 성능 사용자 범위').inputValue(), 'new'); await unknownOnly();
    const detail = page.locator('summary', { hasText: '상세 수치 보기' }), help = page.locator('summary', { hasText: 'NDCG@10은 어떻게 읽나요?' });
    await detail.focus(); await page.keyboard.press('Enter'); assert.equal(await page.locator('details').first().getAttribute('open'), null);
    await help.focus(); await page.keyboard.press('Space'); assert.equal(await page.locator('details').last().getAttribute('open'), null); await shot('performance-collapsed');
    await detail.click(); await help.click();
    if (width === 390) {
      const table = page.getByRole('region', { name: '추천 성능 상세 수치 표, 가로 스크롤 가능' });
      assert.equal(await table.evaluate(node => node.scrollWidth > node.clientWidth), true);
      await table.focus(); await page.keyboard.press('ArrowRight'); await page.waitForFunction(() => document.querySelector('[aria-label="추천 성능 상세 수치 표, 가로 스크롤 가능"]').scrollLeft > 0);
    }
    await page.locator('main').getByRole('link', { name: '데이터 분석', exact: true }).click(); await page.locator('[data-analytics-view="overview"]').waitFor();
    assert.equal(new URL(page.url()).searchParams.get('period'), '30d'); assert.equal(new URL(page.url()).searchParams.get('audience'), 'new');
    await page.goBack(); await page.locator('[data-analytics-view="performance"]').waitFor();
    await page.goForward(); await page.locator('[data-analytics-view="overview"]').waitFor();
    await page.getByRole('link', { name: '대시보드', exact: true }).click(); await page.locator('[data-admin-dashboard]').waitFor();
    assert.equal(await page.getByRole('link', { name: '대시보드', exact: true }).getAttribute('aria-current'), 'page'); await unknownOnly(); await shot('dashboard');
    for (const name of ['신고', '문의', '예약 공지', '전체']) {
      const button = page.getByRole('group', { name: '확인 항목 분류' }).getByRole('button', { name: new RegExp(`^${name}`) });
      await button.focus(); await page.keyboard.press('Space'); assert.equal(await button.getAttribute('aria-pressed'), 'true');
      assert.match(await page.getByRole('status').innerText(), new RegExp(`${name} 항목 집계 준비 중`));
    }
    for (const button of await page.getByRole('navigation', { name: '확인 항목 페이지' }).getByRole('button').all()) assert.equal(await button.isDisabled(), true);
    await page.goto(`${base}/admin/analytics?view=unknown&period=__proto__&audience=constructor&fixture=figma`);
    await page.locator('[data-analytics-view="overview"]').waitFor(); assert.equal(await page.getByRole('button', { name: '조회 기간: 최근 7일' }).count(), 1); await noOverflow();
    assert.equal(seen.size, manifest.assets.length, 'all original SVG slots verified'); assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
    results.push({ width, passed: true, geometry, assets, checks: ['overview/performance/dashboard, no fake aggregates or fixture escape', 'period keyboard, Escape, outside close; audience reload; same-route back/forward', 'disclosures and keyboard table scrolling', 'attention filters and disabled unavailable pagination', 'all original SVG dimensions, no page overflow', 'no API/write/external traffic or browser errors'] });
  } catch (error) { await page.screenshot({ path: path.join(output, `failure-${width}.png`), fullPage: true, timeout: 5000 }).catch(() => {}); throw new Error(`${width}px ${page.url()}: ${error.message}`, { cause: error }); }
  finally { await context.close(); }
}

(async () => {
  fs.mkdirSync(output, { recursive: true }); const browser = await chromium.launch({ headless: true, channel: process.env.PIVOT_BROWSER_CHANNEL || 'msedge' });
  try { for (const width of process.argv[4] ? [Number(process.argv[4])] : [1728, 390]) await verify(browser, width); }
  finally { await browser.close(); fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(results, null, 2)); }
  console.log(JSON.stringify(results.map(({ width, passed, checks }) => ({ width, passed, checks })), null, 2));
})().catch(error => { console.error(error); process.exitCode = 1; });
