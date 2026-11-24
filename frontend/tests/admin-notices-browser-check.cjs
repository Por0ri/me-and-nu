// Loopback fixture copy + unmodified production entry point. Reject all API/write traffic.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const { chromium } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const fixture = process.argv[2] || 'http://127.0.0.1:3201';
const production = process.argv[3] || 'http://127.0.0.1:3202';
const output = process.argv[4] || path.resolve(__dirname, '../../.preview-pivot/admin-notices-1118/results');
const local = url => url.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(url.hostname);
assert.ok(local(new URL(fixture)) && local(new URL(production)));
const results = [], manifest = require('../public/admin/notice-sources.json');

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
  const rows = page.locator('[data-notice-row]'), compose = page.locator('[data-notice-compose]');
  const noOverflow = async () => assert.deepEqual(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth ? null : { width: document.documentElement.scrollWidth, positioned: [...document.querySelectorAll('body *')].filter(node => node.getBoundingClientRect().right > innerWidth && getComputedStyle(node).position === 'absolute').map(node => ({ tag: node.tagName, className: node.className, right: node.getBoundingClientRect().right })) }), null, 'viewport overflow');
  const shot = async name => {
    await page.evaluate(() => document.fonts.ready); await noOverflow();
    for (const asset of manifest.assets) for (const img of await page.locator(`img[src="/admin/${asset.file}"]:visible`).all()) {
      const box = await img.evaluate(async node => { await node.decode(); const rect = node.getBoundingClientRect(); return { width: rect.width, height: rect.height, naturalWidth: node.naturalWidth }; });
      assert.ok(Math.abs(box.width - asset.width) < .1, `${asset.file} width ${box.width}`);
      assert.ok(Math.abs(box.height - asset.height) < .1, `${asset.file} height ${box.height}`);
      assert.ok(box.naturalWidth > 0); assets.push({ file: asset.file, ...box }); seen.add(asset.file);
    }
    const modal = await page.locator('dialog[open]').count() > 0;
    if (!modal) await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: path.join(output, `${name}-${width}.png`), fullPage: !modal, animations: 'disabled' });
  };
  const count = () => page.locator('[data-notice-list] strong').innerText();
  const filter = name => page.getByRole('group', { name: '공지 상태 필터' }).getByRole('button', { name, exact: true });
  const disabled = async () => {
    for (const name of ['임시 저장', await page.getByRole('switch').getAttribute('aria-checked') === 'true' ? '예약 하기' : '게시하기']) assert.equal(await compose.getByRole('button', { name, exact: true }).isDisabled(), true);
  };
  try {
    await page.goto(`${fixture}/admin/notices?fixture=figma`); await page.locator('[data-notice-list="ready"]').waitFor();
    assert.equal(await rows.count(), 7); assert.equal(await count(), '32');
    assert.equal(await page.getByRole('link', { name: '알림 및 공지', exact: true }).getAttribute('aria-current'), 'page');
    assert.match(await page.locator('header').innerText(), /알림 및 공지/);
    const geometry = await page.locator('aside, header, [data-notice-statistics], [data-notice-statistics] article, [data-notice-list], [data-notice-compose]').evaluateAll(nodes => nodes.map(node => { const b = node.getBoundingClientRect(); return { tag: node.tagName, x: b.x, y: b.y, width: b.width, height: b.height }; }));
    if (width === 1728) { assert.equal(geometry[0].width, 264); assert.equal(geometry[1].height, 84); assert.equal(geometry[2].x, 300); assert.equal(geometry[2].y, 210); assert.equal(geometry[2].width, 781); assert.equal(geometry[3].height, 136); assert.equal(geometry.at(-2).y, 370); assert.equal(geometry.at(-1).x, 1121); }
    await disabled(); await shot('notices-base');
    const toggle = page.getByRole('switch', { name: '예약 발송' });
    await toggle.focus(); await page.keyboard.press('Space'); assert.equal(await toggle.getAttribute('aria-checked'), 'true');
    const date = page.getByLabel('예약 날짜 (한국 시간)'), time = page.getByLabel('예약 시간 (한국 시간)');
    assert.equal(await date.inputValue(), '2026-11-12'); assert.equal(await time.inputValue(), '10:00');
    await disabled(); await shot('notices-scheduled');
    await compose.getByRole('textbox', { name: '공지 제목', exact: true }).fill('로컬 작성 확인');
    await compose.getByRole('textbox', { name: '공지 내용', exact: true }).fill('전송되지 않는 내용');
    await date.fill('2026-12-01'); await time.fill('14:30'); await date.focus();
    assert.equal(await date.evaluate(node => node === document.activeElement), true);
    assert.match(await compose.innerText(), /2026.12.01 \(화\)/);
    await toggle.click(); assert.equal(await date.count(), 0); await toggle.click();
    assert.equal(await date.inputValue(), '2026-12-01'); assert.equal(await time.inputValue(), '14:30');
    await disabled(); await toggle.click();
    if (width === 390) {
      const table = page.getByRole('region', { name: '공지 목록 표, 가로 스크롤 가능' });
      assert.equal(await table.evaluate(node => node.scrollWidth > node.clientWidth), true);
      await table.focus(); await page.keyboard.press('ArrowRight'); await page.waitForFunction(() => document.querySelector('[aria-label="공지 목록 표, 가로 스크롤 가능"]').scrollLeft > 0);
    }
    const opener = rows.nth(1).getByRole('button').first(); await opener.focus(); await page.keyboard.press('Enter');
    const dialog = page.getByRole('dialog', { name: '공지 내용 상세' }); await dialog.waitFor();
    assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('aria-label')), '공지 내용 상세 닫기');
    assert.equal(await page.evaluate(() => document.body.style.overflow), 'hidden');
    assert.match(await dialog.innerText(), /11월 12일 \(목\) 새벽 2시부터/);
    assert.match(await dialog.innerText(), /2026.11.05 \(목\) 10:27/);
    for (const name of ['삭제하기', '수정하기']) assert.equal(await dialog.getByRole('button', { name }).isDisabled(), true);
    await shot('notices-detail');
    await page.keyboard.press('Shift+Tab'); assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('role')), 'region');
    await page.keyboard.press('Tab'); assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('aria-label')), '공지 내용 상세 닫기');
    for (let i = 0; i < 4; i++) { await page.keyboard.press('Tab'); assert.equal(await page.evaluate(() => !!document.activeElement?.closest('dialog')), true); }
    await page.keyboard.press('Escape'); await dialog.waitFor({ state: 'hidden' });
    assert.equal(await opener.evaluate(node => node === document.activeElement), true); assert.equal(await page.evaluate(() => document.body.style.overflow), '');
    await opener.click(); await dialog.getByRole('button', { name: '공지 내용 상세 닫기' }).click(); assert.equal(await opener.evaluate(node => node === document.activeElement), true);
    await rows.nth(2).getByRole('button').last().click(); assert.match(await dialog.innerText(), /공지 내용 연결 준비 중/); await page.keyboard.press('Escape');
    await page.getByRole('button', { name: '다음 페이지', exact: true }).click(); assert.equal(await rows.first().getAttribute('data-notice-row'), 'fixture-notice-8');
    const search = page.getByRole('textbox', { name: '공지 제목으로 검색' }); await search.fill('시스템'); await search.press('Enter');
    assert.equal(await count(), '1'); assert.equal(await page.getByRole('button', { name: '1페이지', exact: true }).getAttribute('aria-current'), 'page');
    await filter('예약 발송').click(); await page.locator('[data-notice-empty]').waitFor();
    await page.getByRole('button', { name: '조건 초기화' }).click(); assert.equal(await search.inputValue(), ''); assert.equal(await count(), '32');
    await filter('예약 발송').click(); assert.equal(await count(), '4'); await filter('게시 중').click(); assert.equal(await count(), '28');
    await search.fill('%_'); await page.getByRole('button', { name: '검색', exact: true }).click(); await page.locator('[data-notice-empty]').waitFor();
    assert.equal(new URL(page.url()).searchParams.has('q'), false);
    assert.equal(await compose.getByRole('textbox', { name: '공지 제목', exact: true }).inputValue(), '로컬 작성 확인');
    await page.reload(); assert.equal(await compose.getByRole('textbox', { name: '공지 제목', exact: true }).inputValue(), '');
    for (const state of ['loading', 'login-required', 'forbidden', 'error', 'unavailable', 'not-found', 'empty']) {
      await page.goto(`${fixture}/admin/notices?fixture=${state}`); await page.locator(`[data-notice-list="${state === 'empty' ? 'ready' : state}"]`).waitFor();
      assert.equal(await rows.count(), 0); assert.equal(await page.locator('dialog').count(), 0); await noOverflow();
    }
    await page.goto(`${production}/admin/notices?fixture=figma&q=시스템`); await page.locator('[data-notice-list="unavailable"]').waitFor();
    assert.equal(await rows.count(), 0); assert.equal(await page.locator('[data-fixture-banner]').count(), 0);
    assert.doesNotMatch(await page.locator('main').innerText(), /32건|업데이트 안내|시스템 점검/); await disabled(); await shot('production-unavailable');
    await toggle.click(); assert.equal(await date.inputValue(), ''); assert.equal(await time.inputValue(), ''); await disabled();
    await compose.getByRole('textbox', { name: '공지 제목', exact: true }).fill('운영 임시 입력'); await compose.getByRole('textbox', { name: '공지 제목', exact: true }).press('Enter');
    assert.equal(await rows.count(), 0); await page.reload(); assert.equal(await compose.getByRole('textbox', { name: '공지 제목', exact: true }).inputValue(), '');
    if (width === 1728) assert.equal(seen.size, 17, 'every original SVG slot checked');
    assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
    results.push({ width, passed: true, geometry, assets, checks: ['3 Figma states, shared menu and breadcrumb', 'search/status intersection, reset, 7-row pagination', 'in-memory drafting, switch keyboard, date/time, no persistence', 'read-only detail, null content, focus containment, close/Escape focus return', 'loading/401/403/404/error/empty/unavailable', 'production fixture exclusion, no API or write traffic', '17 exact SVG slots and responsive overflow'] });
  } catch (error) { await page.screenshot({ path: path.join(output, `failure-${width}.png`), fullPage: true }); throw new Error(`${width}px ${page.url()}: ${error.message}`, { cause: error }); }
  finally { await context.close(); }
}
(async () => {
  fs.mkdirSync(output, { recursive: true }); const browser = await chromium.launch({ headless: true, channel: process.env.PIVOT_BROWSER_CHANNEL || 'msedge' });
  try { for (const width of process.argv[5] ? [Number(process.argv[5])] : [1728, 390]) await verify(browser, width); }
  finally { await browser.close(); fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(results, null, 2)); }
  console.log(JSON.stringify(results.map(({ width, passed, checks }) => ({ width, passed, checks })), null, 2));
})().catch(error => { console.error(error); process.exitCode = 1; });
