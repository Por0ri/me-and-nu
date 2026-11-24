// Bounded follow-up to the already-verified member slice: original SVGs and affected controls only.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const base = process.argv[2] || 'http://127.0.0.1:3197';
const output = process.argv[3] || path.resolve(__dirname, '../../.preview-pivot/admin-members-1117/icon-results');
const manifest = require('../public/admin/member-sources.json');
const results = [];
assert.ok(['127.0.0.1', 'localhost'].includes(new URL(base).hostname));
for (const asset of manifest.assets) {
  const bytes = fs.readFileSync(path.join(__dirname, '../public/admin', asset.file));
  assert.ok(bytes.length > 0);
  assert.equal(crypto.createHash('sha256').update(bytes).digest('hex'), asset.sha256, asset.file);
}
async function verify(browser, width) {
  const context = await browser.newContext({ viewport: { width, height: 1117 }, serviceWorkers: 'block' });
  const page = await context.newPage(), errors = [], unexpected = [], seen = new Set(), geometry = [];
  page.setDefaultTimeout(15000); page.setDefaultNavigationTimeout(60000);
  page.on('pageerror', error => errors.push(error.message));
  let contentNavigation = false;
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (!['127.0.0.1', 'localhost'].includes(url.hostname) || route.request().method() !== 'GET') { unexpected.push(url.pathname); return route.abort(); }
    if (!url.pathname.startsWith('/api/')) return route.continue();
    if (contentNavigation && url.pathname === '/api/v1/auth/session') return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ authenticated: true, sessionState: 'active', user: { id: 7, accountType: 'user' }, onboardingCompleted: true, csrfToken: 'fixture' }) });
    if (contentNavigation && url.pathname === '/api/v1/admin/contents') return route.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ code: 'ADMIN_REQUIRED' }) });
    unexpected.push(url.pathname); return route.abort();
  });
  const inspect = async () => {
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'viewport overflow');
    for (const asset of manifest.assets) {
      for (const img of await page.locator(`img[src="/admin/${asset.file}"]:visible`).all()) {
        const size = await img.evaluate(async node => { await node.decode(); const box = node.getBoundingClientRect(); return { width: box.width, height: box.height, naturalWidth: node.naturalWidth, naturalHeight: node.naturalHeight }; });
        assert.equal(size.width, asset.width, asset.file); assert.equal(size.height, asset.height, asset.file);
        assert.equal(size.naturalWidth, asset.width); assert.equal(size.naturalHeight, asset.height);
        seen.add(asset.file); geometry.push({ file: asset.file, ...size });
      }
    }
  };
  const screenshot = async name => { await page.evaluate(() => document.fonts.ready); await inspect(); await page.screenshot({ path: path.join(output, `${name}-${width}.png`), fullPage: true, animations: 'disabled', timeout: 60000 }); };
  try {
    await page.goto(`${base}/admin/users?fixture=figma`); await page.locator('[data-member-list="ready"]').waitFor();
    assert.equal(await page.getByRole('link', { name: '회원 관리', exact: true }).locator('img').getAttribute('src'), '/admin/users-active.svg');
    assert.equal(await page.getByRole('link', { name: '콘텐츠 관리', exact: true }).locator('img').getAttribute('src'), '/admin/contents-inactive.svg');
    assert.equal(await page.getByRole('button', { name: 'CSV 내보내기' }).isDisabled(), true);
    await screenshot('list');
    await page.getByLabel('회원 정렬').selectOption('oldest'); await page.waitForURL(url => url.searchParams.get('sort') === 'oldest');
    assert.equal(await page.getByLabel('회원 정렬').inputValue(), 'oldest');
    await page.locator('[data-member-row]').first().getByRole('link').click(); await page.locator('[data-member-detail="ready"]').waitFor();
    for (const name of ['메모 작성', '메모 수정 준비 중', '메모 삭제 준비 중']) assert.equal(await page.getByRole('button', { name, exact: true }).isDisabled(), true);
    await screenshot('detail');
    await page.getByRole('button', { name: '전체 보기' }).click(); const dialog = page.getByRole('dialog', { name: '닉네임 변경 이력' }); await dialog.waitFor();
    await screenshot('history');
    await dialog.getByRole('button', { name: '닉네임 변경 이력 닫기' }).click(); await dialog.waitFor({ state: 'hidden' });
    await page.getByRole('link', { name: '회원 목록으로' }).click(); await page.locator('[data-member-list="ready"]').waitFor();
    assert.equal(new URL(page.url()).searchParams.get('sort'), 'oldest');
    assert.equal(seen.size, manifest.assets.length, 'all 12 original icons rendered');
    contentNavigation = true;
    await page.getByRole('link', { name: '콘텐츠 관리', exact: true }).click(); await page.locator('section[data-admin-state="forbidden"]').waitFor();
    assert.equal(await page.getByRole('link', { name: '회원 관리', exact: true }).locator('img').getAttribute('src'), '/admin/users.svg');
    assert.equal(await page.getByRole('link', { name: '콘텐츠 관리', exact: true }).locator('img').getAttribute('src'), '/admin/contents.svg');
    await inspect(); assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
    results.push({ width, passed: true, originalAssets: [...seen], geometry, checks: ['download hashes', 'intrinsic SVG dimensions', 'active/inactive nav variants', 'sort/detail/back/drawer-close controls', 'disabled memo/export controls', 'no viewport overflow', 'no member API calls'] });
  } catch (error) { await page.screenshot({ path: path.join(output, `failure-${width}.png`), fullPage: true }); throw error; }
  finally { await context.close(); }
}
(async () => {
  fs.mkdirSync(output, { recursive: true }); const browser = await chromium.launch({ headless: true, channel: process.env.PIVOT_BROWSER_CHANNEL || 'msedge' });
  try { for (const width of [1728, 390]) await verify(browser, width); }
  finally { await browser.close(); fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(results, null, 2)); }
  console.log(JSON.stringify(results.map(({ width, passed, originalAssets, checks }) => ({ width, passed, originalAssets, checks })), null, 2));
})().catch(error => { console.error(error); process.exitCode = 1; });
