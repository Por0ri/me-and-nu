// Loopback-only API interception; no request is sent to a real service.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const { createApiFixture } = require('./helpers/pivot-api-fixture.cjs');
const base = process.argv[2] || 'http://127.0.0.1:3194';
const out = process.argv[3] || path.join(require('node:os').tmpdir(), 'pivot-my-notice');
assert.ok(['localhost', '127.0.0.1'].includes(new URL(base).hostname));
assert.equal(new URL(base).protocol, 'http:');
const button = (root, name) => root.getByRole('button', { name, exact: true });
const results = [];
const until = async condition => { for (let i = 0; i < 300; i++) { if (condition()) return; await new Promise(r => setTimeout(r, 20)); } assert.ok(condition(), 'Mock response completed'); };

async function verify(browser, width, height) {
  const context = await browser.newContext({ viewport: { width, height }, isMobile: width < 500, hasTouch: width < 500, timezoneId: 'Asia/Seoul', reducedMotion: 'reduce', serviceWorkers: 'block' });
  const page = await context.newPage(); page.setDefaultTimeout(15000); page.setDefaultNavigationTimeout(60000);
  const fixture = createApiFixture({ apiOrigin: base, frontendOrigin: base, topicId: 83 }); fixture.rotate('active');
  const today = new Date().toISOString(), yesterday = new Date(Date.now() - 86_400_000).toISOString();
  const notice = (notificationId, createdAt, isRead = false) => ({ notificationId, type: 'system_notice', title: `알림 ${notificationId}`, body: 'Mock 알림 내용', createdAt, isRead, readAt: isRead ? today : null });
  fixture.state.notifications = [notice(4, today), notice(3, today), notice(2, yesterday), notice(1, today, true)];
  let taste = { topicId: 83, works: [fixture.works[0]], preferenceTagIds: [201, 301], consumptionType: 'world', consumptionTypes: ['world'] };
  let badgeCalls = 0, tasteWrites = 0, logoutCalls = 0, holdBadge = false, held = [], pendingBadge = 0, dialogCount = 0, accept = false, step = 'shared headers';
  const errors = [], unexpected = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('dialog', async dialog => { dialogCount++; assert.equal(dialog.type(), 'confirm'); assert.match(dialog.message(), /저장하지 않고 알림함/); await (accept ? dialog.accept() : dialog.dismiss()); });
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    if (!['localhost', '127.0.0.1'].includes(url.hostname)) { unexpected.push(url.href); return route.abort(); }
    if (!url.pathname.startsWith('/api/')) return route.continue();
    const reply = (body, status = 200, headers = {}) => route.fulfill({ status, headers, contentType: 'application/json', body: status === 204 ? undefined : JSON.stringify(body) }).catch(() => {});
    if (url.pathname === '/api/v1/topics') return reply({ topics: [fixture.topic] });
    if (url.pathname === '/api/v1/users/me/activity-events') return reply({ saved: request.postDataJSON().events.length, duplicated: 0, rejected: 0 });
    if (url.pathname === '/api/v1/users/me/nickname') return reply({ available: true, reason: null });
    if (url.pathname === '/api/v1/me/taste') { if (request.method() === 'PUT') tasteWrites++; return reply(taste); }
    if (url.pathname === '/api/v1/me/taste/works') return reply({ items: fixture.works, nextCursor: null });
    if (url.pathname === '/api/v1/me/taste/hints') return reply({ topicId: 83, enough: false, contents: 0, works: [], genres: [], themes: [], types: [] });
    if (url.pathname === '/api/v1/me/taste/preference-options') {
      const entityIds = request.postDataJSON().entityIds;
      const tags = fixture.tags.map(tag => ({ ...tag, sourceEntityIds: tag.sourceEntityIds.filter(id => entityIds.includes(id)) }));
      return reply({ entityIds, genres: tags.filter(t => t.kind === 'genre'), themes: tags.filter(t => t.kind === 'theme'), minSelections: 2, minGenreSelections: 1, minThemeSelections: 1, maxSelections: null, maxSelectionsPerKind: 7 });
    }
    if (url.pathname === '/api/v1/auth/logout') logoutCalls++;
    const response = fixture.dispatch(url.href, { method: request.method(), headers: request.headers(), body: request.postData() });
    if (response.body?.code === 'FIXTURE_ROUTE_NOT_FOUND') unexpected.push(request.method() + ' ' + url.pathname);
    if (url.pathname === '/api/v1/users/me/notifications' && url.searchParams.get('limit') === '5') {
      badgeCalls++;
      if (holdBadge) { pendingBadge++; await new Promise(resolve => held.push(resolve)); await reply(response.body, response.status, response.headers); pendingBadge--; return; }
    }
    return reply(response.body, response.status, response.headers);
  });
  const my = page.locator('[data-my-state="ready"]:visible');
  const home = page.locator('[data-home-state]:visible');
  const inbox = page.getByRole('main', { name: '알림함', exact: true });
  const bell = () => page.locator('[data-home-header]:visible').getByRole('button', { name: '알림', exact: true });
  const dot = () => page.locator('[data-home-header]:visible [data-new-notice]');
  const expectDot = async visible => {
    await bell().waitFor();
    if (visible) await dot().waitFor(); else await dot().waitFor({ state: 'hidden' });
    assert.equal(await dot().count(), visible ? 1 : 0);
    if (visible) assert.equal(await dot().evaluate(el => getComputedStyle(el).backgroundColor), 'rgb(255, 0, 0)');
    const icon = bell().locator('img'); assert.equal((await icon.boundingBox()).width, 24);
    assert.equal(await icon.getAttribute('src'), '/pivot/home/chrome/notification.svg');
  };
  const q = async number => page.waitForFunction(number => document.querySelector('[role=progressbar]')?.getAttribute('aria-valuenow') === String(number), number);
  const openTaste = async () => { await button(my, '내 피드 취향 조정하기').click(); await q(1); await button(page, '테스트 작품 1 선택 해제').waitFor(); };
  const cleanInbox = async () => { const count = dialogCount; await bell().click(); await inbox.waitFor(); assert.equal(dialogCount, count, 'Unchanged answers navigate without confirmation'); };
  const refreshSession = () => page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true })));
  const shot = name => page.screenshot({ path: path.join(out, `${name}-${width}.png`), animations: 'disabled', timeout: 30000 });
  try {
    await page.goto(base + '/pivot/home'); await home.waitFor(); await expectDot(true); assert.equal(badgeCalls, 1);
    await button(home, 'MY').click(); await my.waitFor(); await expectDot(true);
    for (const [label, pathname] of [['관심분야 관리', '/pivot/interests'], ['프로필 수정', '/pivot/profile']]) {
      await button(my, label).click(); await page.waitForURL('**' + pathname); await expectDot(true);
      if (pathname.endsWith('interests')) { assert.equal(await page.getByRole('checkbox').count(), 0); await page.getByText('선택중', { exact: true }).waitFor(); }
      await cleanInbox(); await button(inbox, '뒤로가기').click(); await page.waitForURL('**' + pathname); await expectDot(true);
      await button(page.locator('[data-home-header]:visible'), '뒤로가기').click(); await my.waitFor();
    }
    // 2026-11 design feedback: taste questions start with the progress row (no me;nu header or bell), so the
    // unread/read checks run from MY and the old "leave unsaved answers through the bell" steps are gone.
    await openTaste(); assert.equal(await page.locator('[data-my-question-header], [data-home-header]:visible').count(), 0, 'No bell on taste questions');
    await button(page, '이전 단계').click(); await my.waitFor(); await expectDot(true); assert.equal(badgeCalls, 1, 'MY routes share the cached badge read'); await shot('my-unread');
    await cleanInbox();
    step = 'read updates';
    for (const id of [4, 3]) {
      await button(inbox, `알림 ${id}, 읽지 않음`).click(); await page.getByRole('main', { name: '알림 상세', exact: true }).waitFor();
      await until(() => fixture.state.notifications.find(n => n.notificationId === id).isRead);
      await button(page.getByRole('main', { name: '알림 상세', exact: true }), '뒤로가기').click(); await inbox.waitFor();
      await button(inbox, '뒤로가기').click(); await my.waitFor();
      await expectDot(id === 4); if (id === 4) await cleanInbox();
    }
    assert.equal(badgeCalls, 3, 'Only confirmed reads invalidate the shared snapshot'); await shot('my-read');
    assert.equal(fixture.state.notifications.find(n => n.notificationId === 2).isRead, false, 'Yesterday unread does not light the dot');
    await expectDot(false); await button(my, 'HOME').click(); await home.waitFor(); await expectDot(false);
    await button(home, 'MY').click(); await my.waitFor();
    step = 'session isolation';
    // Hold one badge read of member 7 on MY, switch to member 13 on the taste questions, then release the stale read.
    fixture.state.notifications = [notice(5, today)]; holdBadge = true; await refreshSession(); await until(() => held.length === 1);
    await openTaste();
    fixture.state.userId = 13; fixture.state.notifications = []; taste = { ...taste, works: [fixture.works[2]] }; holdBadge = false; await refreshSession();
    await button(page, '테스트 작품 3 선택 해제').waitFor(); assert.equal(await button(page, '테스트 작품 1 선택 해제').count(), 0);
    held.splice(0).forEach(resolve => resolve()); await until(() => pendingBadge === 0);
    await button(page, '이전 단계').click(); await my.waitFor();
    await page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))); await expectDot(false);
    step = 'logout'; fixture.state.notifications = [notice(6, today)]; await refreshSession(); await expectDot(true);
    await button(my, '로그아웃').click(); await button(page.getByRole('dialog', { name: '로그아웃', exact: true }), '로그아웃').click();
    await page.waitForURL('**/pivot/login'); assert.equal(logoutCalls, 1); assert.equal(await dot().count(), 0);
    await page.goto(base + '/pivot/taste'); await page.getByText('로그인한 뒤 취향을 수정할 수 있어요.', { exact: true }).waitFor(); assert.equal(await dot().count(), 0);
    step = 'Demo isolation'; const beforeDemo = badgeCalls;
    await page.goto(base + '/pivot/demo?view=home'); await home.waitFor(); await expectDot(true); await cleanInbox();
    await button(inbox, '[공지] me;nu 업데이트 안내, 읽지 않음').click(); await page.getByRole('main', { name: '알림 상세', exact: true }).waitFor();
    await button(page.getByRole('main', { name: '알림 상세', exact: true }), '뒤로가기').click(); await button(inbox, '뒤로가기').click(); await home.waitFor(); await expectDot(false);
    assert.equal(badgeCalls, beforeDemo); assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
    results.push({ width, height, status: 'pass', badgeCalls, dialogCount, tasteWrites, logoutCalls, checks: ['shared HOME/MY/taste/interests/profile dot', 'today/read/yesterday/empty policy', 'read invalidation', 'deduplicated route reads', 'Q1/Q2/Q3 clean/dirty/revert/cancel/discard', 'late old-session response', 'logout', 'Demo isolation'] });
    console.log(`PASS MY notice/dirty ${width}x${height}`);
  } catch (error) { await shot('failure').catch(() => {}); throw new Error(`${width} ${step}: ${error.message}`, { cause: error }); }
  finally { held.splice(0).forEach(resolve => resolve()); await context.close(); }
}
(async () => {
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: process.env.PIVOT_BROWSER_CHANNEL || 'chrome' });
  try { for (const [width, height] of [[320, 568], [375, 812], [1440, 1000]]) {
    if (process.env.PIVOT_VIEWPORT_WIDTH && width !== Number(process.env.PIVOT_VIEWPORT_WIDTH)) continue;
    await verify(browser, width, height);
  } } finally { await browser.close(); fs.writeFileSync(path.join(out, 'results.json'), JSON.stringify(results, null, 2)); }
})().catch(error => { console.error(error); process.exitCode = 1; });
