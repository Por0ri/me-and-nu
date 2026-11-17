// Isolated local fixture checkout + real production build. No live member API.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const fixture = process.argv[2] || 'http://127.0.0.1:3197';
const production = process.argv[3] || 'http://127.0.0.1:3198';
const output = process.argv[4] || path.resolve(__dirname, '../../.preview-pivot/admin-members-1117/results');
const results = [];
const local = url => url.protocol === 'http:' && ['127.0.0.1', 'localhost'].includes(url.hostname);
assert.ok(local(new URL(fixture)) && local(new URL(production)));

async function verify(browser, width) {
  const context = await browser.newContext({ viewport: { width, height: 1117 }, serviceWorkers: 'block' });
  const page = await context.newPage(), errors = [], unexpected = [];
  page.setDefaultTimeout(15000);
  page.setDefaultNavigationTimeout(60000);
  page.on('pageerror', error => errors.push(error.message));
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (!local(url) || url.pathname.startsWith('/api/') || !['GET', 'HEAD'].includes(route.request().method())) { unexpected.push(`${route.request().method()} ${url.pathname}`); return route.abort(); }
    return route.continue();
  });
  const rows = page.locator('[data-member-row]');
  const listReady = () => page.locator('[data-member-list="ready"]').waitFor();
  const noOverflow = async () => assert.deepEqual(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth ? [...document.querySelectorAll('body *')].filter(node => node.getBoundingClientRect().right > innerWidth && getComputedStyle(node).position === 'absolute').map(node => ({ tag: node.tagName, className: node.className, text: node.textContent.slice(0, 50) })) : null), null, 'viewport overflow');
  const shot = async name => { await page.evaluate(() => document.fonts.ready); await noOverflow(); await page.screenshot({ path: path.join(output, `${name}-${width}.png`), fullPage: true }); };
  const clickAndWait = async (locator, key, value) => { await locator.click(); await page.waitForURL(url => url.searchParams.get(key) === value); await listReady(); };
  try {
    await page.goto(`${fixture}/admin/users?fixture=figma`); await listReady();
    assert.equal(await rows.count(), 5);
    assert.equal(await page.getByRole('link', { name: '회원 관리', exact: true }).getAttribute('aria-current'), 'page');
    assert.equal(await page.getByRole('button', { name: 'CSV 내보내기' }).isDisabled(), true);
    assert.equal(await page.getByRole('checkbox').count(), 5);
    for (const checkbox of await page.getByRole('checkbox').all()) assert.equal(await checkbox.isDisabled(), true);
    const geometry = await page.locator('aside, header, [data-member-statistics], [data-member-statistics] article, [data-member-list]').evaluateAll(nodes => nodes.map(node => {
      const rect = node.getBoundingClientRect(); return { tag: node.tagName, x: rect.x, y: rect.y, width: rect.width, height: rect.height };
    }));
    if (width === 1728) { assert.equal(geometry[0].width, 264); assert.equal(geometry[1].height, 84); assert.equal(geometry[2].x, 290); assert.equal(geometry[2].y, 217); }
    const assets = await page.locator('img[src^="/admin/"]').evaluateAll(async images => {
      await Promise.all(images.map(img => img.decode()));
      return images.map(img => ({ src: img.getAttribute('src'), width: img.getBoundingClientRect().width, height: img.getBoundingClientRect().height, naturalWidth: img.naturalWidth, naturalHeight: img.naturalHeight }));
    });
    for (const asset of assets) { assert.ok(asset.naturalWidth > 0); if (asset.width) { assert.ok(Math.abs(asset.width - asset.naturalWidth) < 1); assert.ok(Math.abs(asset.height - asset.naturalHeight) < 1); } }
    await shot('members');
    if (width === 390) {
      const table = page.getByRole('region', { name: '회원 목록 표, 가로 스크롤 가능' });
      assert.equal(await table.evaluate(node => node.scrollWidth > node.clientWidth), true);
      await table.focus(); await page.keyboard.press('ArrowRight');
      await page.waitForFunction(() => document.querySelector('[role="region"]').scrollLeft > 0);
    }
    await rows.first().getByRole('link').click(); await page.locator('[data-member-detail="ready"]').waitFor();
    assert.match(await page.locator('header').innerText(), /회원 상세/);
    for (const name of ['메모 작성', '메모 수정 준비 중', '메모 삭제 준비 중']) assert.equal(await page.getByRole('button', { name, exact: false }).isDisabled(), true);
    await shot('member-detail');
    const historyButton = page.getByRole('button', { name: '전체 보기' });
    await historyButton.click(); const dialog = page.getByRole('dialog', { name: '닉네임 변경 이력' }); await dialog.waitFor();
    assert.equal(await dialog.locator('tbody tr').count(), 4);
    assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('aria-label')), '닉네임 변경 이력 닫기');
    assert.equal(await page.evaluate(() => document.body.style.overflow), 'hidden');
    await page.keyboard.press('Tab'); assert.equal(await page.evaluate(() => document.activeElement?.closest('dialog') !== null), true);
    await shot('member-history');
    await page.keyboard.press('Escape'); await dialog.waitFor({ state: 'hidden' });
    assert.equal(await historyButton.evaluate(node => node === document.activeElement), true);
    assert.equal(await page.evaluate(() => document.body.style.overflow), '');
    await historyButton.click(); await dialog.getByRole('button', { name: '닉네임 변경 이력 닫기' }).click(); await dialog.waitFor({ state: 'hidden' });
    await page.getByRole('link', { name: '회원 목록으로' }).click(); await listReady();
    await clickAndWait(page.getByRole('button', { name: '다음 페이지' }), 'page', '2');
    const listUrl = page.url();
    await rows.first().getByRole('link').click(); await page.locator('[data-member-detail="ready"]').waitFor();
    await page.getByRole('link', { name: '회원 목록으로' }).click(); await listReady(); assert.equal(page.url(), listUrl);
    await page.getByRole('textbox', { name: '회원 검색' }).fill('김하루'); await page.getByRole('textbox', { name: '회원 검색' }).press('Enter');
    await page.waitForURL(url => url.searchParams.get('page') === '1'); await listReady(); assert.equal(await rows.count(), 5);
    assert.equal(new URL(page.url()).searchParams.has('q'), false);
    await rows.first().getByRole('link').click(); await page.locator('[data-member-detail="ready"]').waitFor();
    await page.getByRole('link', { name: '회원 목록으로' }).click(); await listReady(); assert.equal(await page.getByRole('textbox', { name: '회원 검색' }).inputValue(), '김하루');
    await clickAndWait(page.getByRole('button', { name: '이용 제한', exact: true }), 'status', 'restricted'); assert.equal(await rows.count(), 1);
    await clickAndWait(page.getByRole('button', { name: '네이버', exact: true }), 'provider', 'naver'); await page.locator('[data-member-empty]').waitFor();
    await page.getByRole('button', { name: '조건 초기화' }).click(); await page.waitForURL(url => !url.searchParams.has('status')); await listReady();
    assert.equal(await page.getByRole('textbox', { name: '회원 검색' }).inputValue(), '');
    await page.getByLabel('회원 정렬').selectOption('oldest'); await page.waitForURL(url => url.searchParams.get('sort') === 'oldest'); assert.equal(await rows.first().getAttribute('data-member-row'), 'fixture-31');
    await page.reload(); await listReady(); assert.equal(await page.getByLabel('회원 정렬').inputValue(), 'oldest');
    await page.getByRole('textbox', { name: '회원 검색' }).fill('%_'); await page.getByRole('button', { name: '검색', exact: true }).click(); await page.locator('[data-member-empty]').waitFor();
    for (const state of ['loading', 'login-required', 'forbidden', 'error', 'unavailable']) {
      await page.goto(`${fixture}/admin/users?fixture=${state}`); await page.locator(`[data-member-list="${state}"]`).waitFor(); assert.equal(await rows.count(), 0); await noOverflow();
    }
    await page.goto(`${fixture}/admin/users/fixture-1?fixture=missing`); await page.locator('[data-member-detail="ready"]').waitFor();
    assert.doesNotMatch(await page.locator('main').innerText(), /raw-private@example/);
    await page.getByRole('button', { name: '전체 보기' }).click(); await page.getByRole('dialog').getByText('닉네임 변경 이력 조회를 준비 중입니다.').waitFor(); await noOverflow();
    await page.goto(`${fixture}/admin/users/unknown`); await page.locator('[data-member-detail="not-found"]').waitFor();
    await page.goto(`${production}/admin/users?fixture=figma`); await page.locator('[data-member-list="unavailable"]').waitFor();
    assert.equal(await rows.count(), 0); assert.equal(await page.locator('[data-fixture-banner]').count(), 0);
    assert.doesNotMatch(await page.locator('main').innerText(), /김하루|example.test|50%|31명/); await shot('production-unavailable');
    await page.goto(`${production}/admin/users/fixture-1?fixture=figma`); await page.locator('[data-member-detail="unavailable"]').waitFor(); await shot('production-detail-unavailable');
    await page.getByRole('button', { name: '전체 보기' }).click(); await page.getByRole('dialog').getByText('닉네임 변경 이력 조회를 준비 중입니다.').waitFor(); await noOverflow();
    assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
    results.push({ width, passed: true, geometry, assets, checks: ['list/detail/drawer', 'Figma layout', 'search privacy and back restoration', 'combined filters and sorting', 'pagination and back', 'empty/loading/401/403/error/unavailable/not-found presentation states', 'dialog focus/Escape/close/scroll lock', 'masked emails', 'writes disabled', 'production fixture exclusion', 'no member/API/network writes', 'no viewport overflow'] });
  } catch (error) { await page.screenshot({ path: path.join(output, `failure-${width}.png`), fullPage: true }); throw new Error(`${width}px ${page.url()}: ${error.message}`, { cause: error }); }
  finally { await context.close(); }
}
(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: process.env.PIVOT_BROWSER_CHANNEL || 'msedge' });
  try { for (const width of [1728, 390]) await verify(browser, width); }
  finally { await browser.close(); fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(results, null, 2)); }
  console.log(JSON.stringify(results.map(({ width, passed, checks }) => ({ width, passed, checks })), null, 2));
})().catch(error => { console.error(error); process.exitCode = 1; });
