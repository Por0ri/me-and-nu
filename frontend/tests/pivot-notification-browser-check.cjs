const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { chromium } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const { createApiFixture } = require('./helpers/pivot-api-fixture.cjs');

// Run against Next with NEXT_PUBLIC_API_BASE_URL=http://localhost:8180 and
// NEXT_PUBLIC_PIVOT_ENABLE_LOCAL_API=true. The API fixture lives only in this process.
const base = process.argv[2] || 'http://localhost:3180';
const apiOrigin = 'http://localhost:8180';
const output = process.argv[3] || path.join(os.tmpdir(), 'pivot-notification-browser-check');
assert.equal(new URL(base).hostname, 'localhost');
const fixture = createApiFixture({ apiOrigin, frontendOrigin: base });
const inboxPath = '/api/v1/users/me/notifications';
const body = '첫 문단입니다.\n\n두 번째 문단도 화면에 표시합니다.';

function notice(notificationId, title, isRead) {
  const createdAt = new Date(Date.now() - (10 - notificationId) * 60_000).toISOString();
  return { notificationId, type: 'system_notice', title, body, isRead, createdAt, readAt: isRead ? createdAt : null };
}
const button = (page, name) => page.getByRole('button', { name, exact: true });
const heading = (page, name) => page.getByRole('heading', { name, exact: true });
const back = (page) => button(page, '뒤로가기').click();
const status = (page, message) => page.getByRole('status').filter({ hasText: message }).waitFor();
const inbox = (page) => page.getByRole('main', { name: '알림함', exact: true });
const alert = (page, message) => page.getByRole('alert').filter({ hasText: message }).waitFor();

async function capture(page, name, hasChevron) {
  await page.evaluate(() => document.fonts.ready);
  await page.locator('main:visible img').evaluateAll(images => Promise.all(images.map(img => img.decode())));
  const assets = await page.locator('main:visible img').evaluateAll((images) => images.map(img => ({
    path: new URL(img.src).pathname, loaded: img.complete && img.naturalWidth > 0, width: img.width, height: img.height,
  })));
  assert.ok(assets.some(asset => asset.path.endsWith('/logo.svg') && asset.loaded && asset.width === 103));
  assert.ok(assets.some(asset => asset.path.endsWith('/back.svg') && asset.loaded && asset.width === 24));
  if (hasChevron) assert.ok(assets.some(asset => asset.path.endsWith('/chevron.svg') && asset.loaded && asset.width === 24 && asset.height === 24));
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({ path: path.join(output, name) });
}

(async () => {
  fs.mkdirSync(output, { recursive: true });
  const server = await fixture.listen();
  let browser;
  try {
    browser = await chromium.launch({ headless: true, channel: process.env.PIVOT_BROWSER_CHANNEL || 'chrome' });
    const page = await browser.newPage({ viewport: { width: 375, height: 812 }, timezoneId: 'Asia/Seoul' });
    page.setDefaultTimeout(15000);
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    fixture.rotate('active');
    fixture.state.notifications = [notice(9, '서비스 운영 안내', false), notice(8, '지난 운영 공지', true)];
    fixture.state.notificationPageSize = 1;
    fixture.state.failNext = { path: inboxPath, method: 'GET' };
    let releaseList;
    const listGate = new Promise(resolve => { releaseList = resolve; });
    await page.route(url => url.origin === apiOrigin && url.pathname === inboxPath,
      async route => { await listGate; await route.continue(); });

    await page.goto(base + '/pivot/home');
    await button(page, '알림').click();
    await heading(page, '알림함').waitFor();
    await status(page, '알림을 불러오고 있어요.');
    releaseList();
    await alert(page, '알림을 불러오지 못했어요.');
    await button(page, '다시 시도').click();
    await button(page, '서비스 운영 안내, 읽지 않음').waitFor();
    assert.equal(await inbox(page).getByText('공지', { exact: true }).count(), 1);
    await button(page, '알림 더 보기').click();
    await button(page, '지난 운영 공지, 읽음').waitFor();
    assert.ok(fixture.requests.some(row => row.path === inboxPath && row.query.cursor === '9'));
    assert.ok(fixture.requests.some(row => row.path === inboxPath + '/unread-count'));
    await button(page, '읽지 않음').click();
    await button(page, '서비스 운영 안내, 읽지 않음').waitFor();
    assert.equal(await button(page, '읽지 않음').getAttribute('aria-pressed'), 'true');
    assert.equal(await button(page, '지난 운영 공지, 읽음').count(), 0);
    assert.ok(fixture.requests.some(row => row.path === inboxPath && row.query.unreadOnly === 'true'));

    fixture.state.failNext = { path: inboxPath + '/9', method: 'PATCH' };
    await button(page, '서비스 운영 안내, 읽지 않음').click();
    await heading(page, '알림 상세').waitFor();
    await alert(page, '읽음 상태를 저장하지 못했어요.');
    assert.equal(await page.locator('article:visible p').first().textContent(), body);
    assert.equal(fixture.state.notifications[0].isRead, false);
    await page.goBack();
    await button(page, '서비스 운영 안내, 읽지 않음').waitFor();
    fixture.state.failNext = { path: inboxPath + '/9', method: 'PATCH' };
    await button(page, '서비스 운영 안내, 읽지 않음').click();
    await alert(page, '읽음 상태를 저장하지 못했어요.');
    await button(page, '읽음 처리 다시 시도').click();
    await page.getByRole('article', { name: '서비스 운영 안내' }).waitFor();
    await page.getByText('읽음 상태를 저장하고 있어요.').waitFor({ state: 'hidden' });
    await back(page);
    await status(page, '읽지 않은 알림이 없어요.');
    const writes = fixture.requests.filter(row => row.method === 'PATCH' && row.path === inboxPath + '/9');
    assert.equal(writes.length, 3);
    assert.ok(writes.every(row => row.csrf === 'fixture-active-csrf' && row.body.isRead === true));
    await button(page, '전체').click();
    await button(page, '서비스 운영 안내, 읽음').waitFor();
    await back(page);
    await heading(page, '홈').waitFor();
    await page.waitForFunction(() => document.activeElement?.hasAttribute('data-notification-navigation'));

    await page.goto(base + '/pivot/home?view=notifications&unread=1&notificationId=8');
    await page.getByRole('article', { name: '지난 운영 공지' }).waitFor();
    await back(page);
    await status(page, '읽지 않은 알림이 없어요.');
    const bellChecks = () => fixture.requests.filter(row => row.path === inboxPath && row.query.limit === '5').length;
    const checksBefore = bellChecks();
    await back(page);
    await heading(page, '홈').waitFor();
    while (bellChecks() === checksBefore) await page.waitForTimeout(50);
    fixture.rotate('anonymous');
    await button(page, '알림').click();
    await page.waitForURL('**/pivot/login');
    assert.equal(await page.getByRole('main', { name: '알림함', exact: true }).count(), 0);
    console.log('Service: list retry, cursor, unread count/filter, full body, failed PATCH, retry, browser Back, HOME focus, direct detail, expired session');

    const requestCount = fixture.requests.length;
    await page.goto(base + '/pivot/demo?view=home');
    await button(page, '알림').click();
    await button(page, '[공지] me;nu 업데이트 안내, 읽지 않음').waitFor();
    assert.equal(await inbox(page).getByRole('listitem').count(), 3);
    await capture(page, 'all.png', true);
    await button(page, '읽지 않음').click();
    await button(page, '[공지] me;nu 업데이트 안내, 읽지 않음').waitFor();
    assert.equal(await inbox(page).getByRole('listitem').count(), 1);
    await capture(page, 'unread.png', true);
    await button(page, '전체').click();
    await button(page, '[시스템 점검] 11월 12일 새벽 2시~4시, 읽음').click();
    await heading(page, '알림 상세').waitFor();
    await capture(page, 'detail.png', false);
    await back(page);
    await button(page, '읽지 않음').click();
    await button(page, '[공지] me;nu 업데이트 안내, 읽지 않음').click();
    await back(page);
    await status(page, '읽지 않은 알림이 없어요.');
    await back(page);
    await heading(page, '홈').waitFor();
    assert.equal(fixture.requests.length, requestCount, 'Demo must make no Service requests');
    assert.deepEqual(pageErrors, []);
    console.log('Demo: all three screens, local read state, HOME return, zero API requests, screenshot geometry and no browser errors');
  } finally {
    await browser?.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
