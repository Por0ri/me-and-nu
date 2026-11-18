// Run against the isolated fixture checkout and the unmodified production entry point.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const fixture = process.argv[2] || 'http://127.0.0.1:3197';
const production = process.argv[3] || 'http://127.0.0.1:3198';
const output = process.argv[4] || path.resolve(__dirname, '../../.preview-pivot/admin-reports-1118/results');
const local = url => url.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(url.hostname);
assert.ok(local(new URL(fixture)) && local(new URL(production)));
const results = [], manifest = require('../public/admin/report-sources.json');

async function verify(browser, width) {
  const context = await browser.newContext({ viewport: { width, height: 1117 }, serviceWorkers: 'block' });
  const page = await context.newPage(), errors = [], unexpected = [], assets = [], seen = new Set();
  page.setDefaultTimeout(15000); page.setDefaultNavigationTimeout(60000);
  page.on('pageerror', error => errors.push(error.message));
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (!local(url) || url.pathname.startsWith('/api/') || !['GET', 'HEAD'].includes(route.request().method())) { unexpected.push(`${route.request().method()} ${url.pathname}`); return route.abort(); }
    return route.continue();
  });
  const rows = page.locator('[data-report-row]'), detail = page.locator('[data-report-detail]');
  const ready = () => page.locator('[data-report-list="ready"]').waitFor();
  const noOverflow = async () => assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'viewport overflow');
  const checkAssets = async () => {
    for (const asset of manifest.assets.filter(a => a.file.endsWith('.svg'))) {
      for (const img of await page.locator(`img[src="/admin/${asset.file}"]:visible`).all()) {
        const geometry = await img.evaluate(async node => { await node.decode(); const box = node.getBoundingClientRect(); return { width: box.width, height: box.height, naturalWidth: node.naturalWidth, naturalHeight: node.naturalHeight }; });
        assert.ok(Math.abs(geometry.width - asset.width) < .1, asset.file); assert.ok(Math.abs(geometry.height - asset.height) < .1, asset.file);
        assert.ok(geometry.naturalWidth > 0); assets.push({ file: asset.file, ...geometry }); seen.add(asset.file);
      }
    }
  };
  const shot = async name => {
    await page.evaluate(() => document.fonts.ready); await noOverflow(); await checkAssets();
    const modal = await page.locator('dialog[open]').count() > 0;
    if (!modal) await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: path.join(output, `${name}-${width}.png`), fullPage: !modal, animations: 'disabled' });
  };
  const period = page.getByRole('button', { name: /^조회 기간:/ });
  const setPeriod = async name => { await period.click(); await page.getByRole('menuitemradio', { name, exact: true }).click(); };
  const count = () => page.locator('[data-report-list] strong').innerText();
  try {
    await page.goto(`${fixture}/admin/reports?fixture=figma`); await ready();
    assert.equal(await rows.count(), 5); assert.equal(await count(), '145');
    assert.equal(await page.getByRole('link', { name: '신고 및 문의 관리', exact: true }).getAttribute('aria-current'), 'page');
    assert.match(await page.locator('header').innerText(), /신고 및 문의 관리/);
    const geometry = await page.locator('aside, header, [data-report-statistics], [data-report-statistics] article, [data-report-list], [data-report-detail]').evaluateAll(nodes => nodes.map(node => {
      const box = node.getBoundingClientRect(); return { tag: node.tagName, x: box.x, y: box.y, width: box.width, height: box.height };
    }));
    if (width === 1728) { assert.equal(geometry[0].width, 264); assert.equal(geometry[1].height, 84); assert.equal(geometry[2].x, 300); assert.equal(geometry[2].y, 210); assert.equal(geometry[3].height, 136); assert.equal(geometry.at(-2).y, 370); }
    assert.equal(await detail.getByRole('button', { name: '보류', exact: true }).isDisabled(), true);
    await shot('reports-waiting');
    if (width === 390) {
      const table = page.getByRole('region', { name: '신고 및 문의 목록 표, 가로 스크롤 가능' });
      assert.equal(await table.evaluate(node => node.scrollWidth > node.clientWidth), true);
      await table.focus(); await page.keyboard.press('ArrowRight'); await page.waitForFunction(() => document.querySelector('[aria-label="신고 및 문의 목록 표, 가로 스크롤 가능"]').scrollLeft > 0);
    }
    const before = await detail.innerText(), opener = detail.getByRole('button', { name: '검토 시작 (콘텐츠 읽기 전용)' });
    await opener.focus(); await page.keyboard.press('Enter');
    const dialog = page.getByRole('dialog', { name: '신고 상세 검토' }); await dialog.waitFor();
    assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('aria-label')), '신고 상세 검토 닫기');
    assert.equal(await page.evaluate(() => document.body.style.overflow), 'hidden');
    assert.equal(await dialog.getByRole('button', { name: '콘텐츠 삭제' }).isDisabled(), true);
    assert.equal(await dialog.getByRole('button', { name: '콘텐츠 수정' }).isDisabled(), true);
    const cover = await dialog.locator('article img').evaluate(async img => { await img.decode(); const rect = img.getBoundingClientRect(); return { width: rect.width, height: rect.height, naturalWidth: img.naturalWidth, naturalHeight: img.naturalHeight }; });
    assert.deepEqual(cover, { width: 327, height: 408, naturalWidth: 1308, naturalHeight: 1632 });
    await shot('reports-content-drawer');
    await page.keyboard.press('Shift+Tab'); assert.equal(await page.evaluate(() => document.activeElement?.tagName), 'A');
    await page.keyboard.press('Tab'); assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('aria-label')), '신고 상세 검토 닫기');
    for (let i = 0; i < 7; i++) { await page.keyboard.press('Tab'); assert.equal(await page.evaluate(() => !!document.activeElement?.closest('dialog')), true, 'modal focus containment'); }
    const scroll = dialog.getByRole('region'); await scroll.focus(); await page.keyboard.press('End');
    await page.waitForFunction(() => document.querySelector('dialog [role="region"]').scrollTop > 0);
    await shot('reports-content-actions');
    await page.keyboard.press('Escape'); await dialog.waitFor({ state: 'hidden' });
    assert.equal(await opener.evaluate(node => node === document.activeElement), true);
    assert.equal(await page.evaluate(() => document.body.style.overflow), ''); assert.equal(await detail.innerText(), before);
    await opener.click(); await dialog.waitFor(); await dialog.getByRole('button', { name: '신고 상세 검토 닫기' }).click(); await dialog.waitFor({ state: 'hidden' });
    assert.equal(await opener.evaluate(node => node === document.activeElement), true);
    await rows.nth(1).getByRole('button').focus(); await page.keyboard.press('Space');
    await page.locator('[data-report-detail="fixture-report-2"]').waitFor(); assert.equal(await detail.locator('[data-report-status]').getAttribute('data-report-status'), 'reviewing');
    await rows.nth(3).getByRole('button').click(); await page.locator('[data-report-detail="fixture-report-4"]').waitFor();
    assert.match(await detail.innerText(), /서비스 문의/); assert.equal(await detail.getByRole('button').count(), 0); await shot('reports-completed-inquiry');
    await rows.nth(4).getByRole('button').click(); assert.match(await detail.innerText(), /버그\/오류 신고/);
    await rows.nth(2).getByRole('button').click(); assert.equal(await detail.getByRole('button', { name: '검토 시작 (콘텐츠 읽기 전용)' }).isDisabled(), true);
    await period.focus(); await page.keyboard.press('ArrowDown'); assert.equal(await page.getByRole('menu').isVisible(), true);
    assert.equal(await page.getByRole('menuitemradio', { name: '최근 7일', exact: true }).evaluate(node => node === document.activeElement), true);
    await page.keyboard.press('ArrowDown'); await page.keyboard.press('Enter'); assert.equal(await count(), '146');
    assert.equal(await period.evaluate(node => node === document.activeElement), true);
    await period.click(); await shot('reports-period-menu'); await page.keyboard.press('Escape'); assert.equal(await period.evaluate(node => node === document.activeElement), true);
    await setPeriod('최근 3개월'); assert.equal(await count(), '147'); await setPeriod('최근 6개월'); assert.equal(await count(), '148'); await setPeriod('최근 7일');
    await page.getByRole('button', { name: '다음 페이지', exact: true }).click(); assert.equal(await rows.first().getAttribute('data-report-row'), 'fixture-report-6');
    const search = page.getByRole('textbox', { name: '신고 및 문의 검색' }); await search.fill('R-004'); await search.press('Enter');
    assert.equal(await rows.count(), 1); assert.equal(await page.getByRole('button', { name: '1페이지', exact: true }).getAttribute('aria-current'), 'page');
    await page.getByRole('group', { name: '신고 처리 상태 필터' }).getByRole('button', { name: '검토 대기', exact: true }).click(); await page.locator('[data-report-empty]').waitFor();
    await page.getByRole('button', { name: '조건 초기화' }).click(); assert.equal(await search.inputValue(), ''); assert.equal(await rows.count(), 5);
    await search.fill('사이버펑크'); await page.getByRole('button', { name: '검색', exact: true }).click(); assert.equal(await count(), '2');
    await page.getByRole('group', { name: '신고 처리 상태 필터' }).getByRole('button', { name: '처리 중', exact: true }).click(); assert.equal(await count(), '1');
    await search.fill('%_'); await search.press('Enter'); await page.locator('[data-report-empty]').waitFor();
    assert.equal(new URL(page.url()).searchParams.has('q'), false);
    for (const state of ['loading', 'login-required', 'forbidden', 'error', 'unavailable', 'not-found', 'empty']) {
      await page.goto(`${fixture}/admin/reports?fixture=${state}`); await page.locator(`[data-report-list="${state === 'empty' ? 'ready' : state}"]`).waitFor(); assert.equal(await rows.count(), 0); await noOverflow();
      assert.doesNotMatch(await detail.innerText(), /R-001|김하루/);
    }
    await page.goto(`${production}/admin/reports?fixture=figma&q=R-001`); await page.locator('[data-report-list="unavailable"]').waitFor();
    assert.equal(await rows.count(), 0); assert.equal(await page.locator('[data-fixture-banner]').count(), 0);
    assert.doesNotMatch(await page.locator('main').innerText(), /R-001|김하루|145건|사이버펑크/); await shot('production-unavailable');
    assert.equal(await page.locator('input').inputValue(), '');
    if (width === 1728) assert.equal(seen.size, 12, 'every visible SVG slot verified');
    assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
    results.push({ width, passed: true, geometry, assets, cover, checks: ['3 Figma frames and state badges', '4 period variants, keyboard, Escape and focus return', 'search + status + period intersection, reset and pagination', 'read-only drawer, native focus containment, scroll lock, close/Escape and opener focus', 'service/bug completion and missing content', 'loading/401/403/404/error/empty/unavailable', 'production fixture exclusion, no API calls or writes', 'exact original asset dimensions and no viewport overflow'] });
  } catch (error) { await page.screenshot({ path: path.join(output, `failure-${width}.png`), fullPage: true }); throw new Error(`${width}px ${page.url()}: ${error.message}`, { cause: error }); }
  finally { await context.close(); }
}
(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: process.env.PIVOT_BROWSER_CHANNEL || 'msedge' });
  try { for (const width of process.argv[5] ? [Number(process.argv[5])] : [1728, 390]) await verify(browser, width); }
  finally { await browser.close(); fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(results, null, 2)); }
  console.log(JSON.stringify(results.map(({ width, passed, checks }) => ({ width, passed, checks })), null, 2));
})().catch(error => { console.error(error); process.exitCode = 1; });
