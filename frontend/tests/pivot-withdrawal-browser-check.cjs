// Only the loopback frontend and the in-memory API fixture receive requests.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const { createApiFixture } = require('./helpers/pivot-api-fixture.cjs');

const base = new URL(process.argv[2] || 'http://localhost:3180').origin;
const apiOrigin = 'http://localhost:8180';
const out = process.argv[3];
if (out) fs.mkdirSync(out, { recursive: true });
assert.equal(new URL(base).hostname, 'localhost');
assert.equal(new URL(base).protocol, 'http:');
const profile = '/api/v1/users/me';
const restoration = '/api/v1/auth/withdrawal-restorations';
const session = '/api/v1/auth/session';
const button = (root, name) => root.getByRole('button', { name, exact: true });
const heading = (page, name) => page.getByRole('heading', { name, exact: true });
const message = (page, role, text) => page.getByRole(role).filter({ hasText: text }).waitFor();
const focused = locator => locator.evaluate(node => node === document.activeElement);
const fits = page => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth);

async function verify(browser, fixture, width) {
  const context = await browser.newContext({ viewport: { width, height: 680 }, timezoneId: 'America/Los_Angeles', isMobile: true, hasTouch: true, serviceWorkers: 'block' });
  const page = await context.newPage();
  page.setDefaultTimeout(12000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin === 'https://kauth.kakao.com' && url.pathname === '/oauth/authorize') {
      return route.fulfill({ status: 200, contentType: 'text/html', body:
        `<a href="${apiOrigin}/api/v1/auth/login/kakao/callback?code=fixture">Mock OAuth callback</a>` });
    }
    return [base, apiOrigin].includes(url.origin) ? route.continue() : route.abort();
  });
  const writes = (path) => fixture.requests.filter(row => row.path === path && row.method !== 'GET');
  const my = () => page.locator('[data-my-state="ready"]');
  const dialog = page.getByRole('dialog', { name: '회원탈퇴', exact: true });
  const openMy = async () => {
    await page.goto(base + '/pivot/home?view=my');
    await button(my(), '회원탈퇴').waitFor();
  };
  const openRestoration = async () => {
    fixture.rotate('restoration_pending');
    await page.goto(base + '/pivot/login');
    await heading(page, '계정 복구').waitFor();
  };
  try {
    fixture.requests.length = 0;
    fixture.rotate('active');
    await openMy();
    const trigger = button(my(), '회원탈퇴');
    await trigger.focus();
    await page.keyboard.press('Enter');
    await dialog.waitFor();
    assert.ok(await focused(button(dialog, '취소')));
    assert.ok(await fits(page));
    const rect = await dialog.boundingBox();
    assert.ok(rect.x >= 0 && rect.x + rect.width <= width && rect.y >= 0 && rect.y + rect.height <= 680);
    await page.keyboard.press('Tab');
    assert.ok(await focused(button(dialog, '탈퇴')));
    await page.keyboard.press('Escape');
    await dialog.waitFor({ state: 'hidden' });
    assert.ok(await focused(trigger));
    assert.equal(writes(profile).length, 0);

    const logout = '/api/v1/auth/logout';
    let releaseLogout;
    const logoutGate = new Promise(resolve => { releaseLogout = resolve; });
    await page.route(url => url.origin === apiOrigin && url.pathname === logout,
      async route => { await logoutGate; await route.continue(); }, { times: 1 });
    fixture.state.failNext = { path: logout, method: 'POST', status: 503 };
    await button(my(), '로그아웃').click();
    const logoutDialog = page.getByRole('dialog', { name: '로그아웃', exact: true });
    const logoutStarted = page.waitForRequest(url => url.url() === apiOrigin + logout);
    await button(logoutDialog, '로그아웃').click();
    await logoutStarted;
    // Exercise the provider guard even though the logout modal blocks pointer input behind it.
    await my().getByRole('button', { name: '회원탈퇴', exact: true, includeHidden: true }).evaluate(node => node.click());
    try {
      await button(dialog, '탈퇴').click();
      await message(dialog, 'alert', '다른 계정 요청을 처리하고 있어요');
      assert.equal(writes(profile).length, 0);
      await button(dialog, '취소').click();
    } finally { releaseLogout(); }
    await message(logoutDialog, 'alert', '로그아웃하지 못했어요');
    await button(logoutDialog, '취소').click();

    await trigger.click();
    fixture.state.failNext = { path: profile, method: 'DELETE', status: 409, code: 'WITHDRAWAL_BLOCKED' };
    await button(dialog, '탈퇴').click();
    await message(dialog, 'alert', '연결된 크리에이터 채널');
    assert.equal(fixture.state.phase, 'active');
    fixture.state.failNext = { path: profile, method: 'DELETE', status: 503 };
    await button(dialog, '탈퇴').click();
    await message(dialog, 'alert', '탈퇴 신청을 확인하지 못했어요');
    let releaseDelete;
    const deleteGate = new Promise(resolve => { releaseDelete = resolve; });
    await page.route(url => url.origin === apiOrigin && url.pathname === profile && url.searchParams.has('mode'),
      async route => { await deleteGate; await route.continue(); });
    await button(dialog, '탈퇴').click();
    const busyDelete = button(dialog, '탈퇴 신청 중…');
    await busyDelete.waitFor();
    await busyDelete.evaluate(node => node.click());
    await page.keyboard.press('Escape');
    assert.ok(await dialog.isVisible());
    releaseDelete();
    await page.waitForURL('**/pivot/login');
    await message(page, 'status', '탈퇴 신청이 완료됐어요');
    assert.equal(await heading(page, '당신의 취향에 딱 맞는 정보를 만나보세요').locator('br').count(), 1);
    await button(page, '네이버 로그인').waitFor();
    if (out) await page.screenshot({ path: path.join(out, `login-${width}.png`), fullPage: true });
    assert.equal(await my().count(), 0);
    assert.deepEqual(writes(profile).map(row => [row.method, row.query.mode, row.csrf]),
      Array.from({ length: 3 }, () => ['DELETE', 'grace', 'fixture-active-csrf']));

    await button(page, '카카오 로그인').click();
    await page.getByRole('link', { name: 'Mock OAuth callback' }).click();
    await page.waitForURL('**/pivot/login');
    const recoveryTitle = heading(page, '계정 복구');
    await recoveryTitle.waitFor();
    assert.ok(await focused(recoveryTitle));
    assert.ok(await fits(page));
    assert.equal(await page.locator('time').getAttribute('datetime'), '2027-01-01T00:00:00Z');
    await page.getByText('한국 시간', { exact: false }).waitFor();
    assert.equal(await page.locator('time').innerText(), '2027년 1월 1일까지');
    await page.getByText('한국 시간 09:00:00 전까지 복구할 수 있어요.', { exact: true }).waitFor();
    const guidance = page.getByText('탈퇴 유예 중인 계정이에요.', { exact: false });
    assert.equal(await guidance.innerText(), '탈퇴 유예 중인 계정이에요.\n유예 기간 안에 복구하면\n서비스를 다시 이용할 수 있어요.');
    assert.equal(await guidance.evaluate(node => getComputedStyle(node).textAlign), 'center');
    if (out) await page.screenshot({ path: path.join(out, `restoration-${width}.png`), fullPage: true });
    let deadlineOverride;
    const overrideSession = async route => {
      const response = await route.fetch(); const body = await response.json();
      body.scheduledDeleteAt = deadlineOverride;
      await route.fulfill({ response, json: body });
    };
    await page.route(apiOrigin + session, overrideSession);
    for (const [value, expected] of [
      ['2027-02-23T15:00:00Z', '2027년 2월 24일까지'],
      ['2027-12-31T20:30:00-05:00', '2028년 1월 1일까지'],
      [null, null], [undefined, null], ['not-a-date', null], ['2027-02-30T00:00:00Z', null],
    ]) {
      deadlineOverride = value; await page.reload(); await recoveryTitle.waitFor();
      if (expected) assert.equal(await page.locator('time').innerText(), expected);
      else { await page.getByText('삭제 예정일을 확인할 수 없어요.', { exact: true }).waitFor(); assert.equal(await page.locator('time').count(), 0); }
      assert.ok(await fits(page));
    }
    await page.unroute(apiOrigin + session, overrideSession);
    await page.reload(); await recoveryTitle.waitFor();
    assert.equal(await button(page, '카카오 로그인').count(), 0);
    assert.equal(writes(restoration).length, 0);

    fixture.state.failNext = { path: restoration, method: 'POST', status: 503 };
    await button(page, '계정 복구하기').click();
    await message(page, 'alert', '계정 복구를 확인하지 못했어요');
    let releaseRestore;
    const restoreGate = new Promise(resolve => { releaseRestore = resolve; });
    await page.route(url => url.origin === apiOrigin && url.pathname === restoration,
      async route => { await restoreGate; await route.continue(); });
    let failSessionRead = true;
    await page.route(url => url.origin === apiOrigin && url.pathname === session, async route => {
      if (failSessionRead && fixture.state.phase === 'active') {
        failSessionRead = false;
        fixture.state.failNext = { path: session, method: 'GET', status: 503 };
      }
      await route.continue();
    });
    await button(page, '다시 시도').click();
    const busyRestore = button(page, '복구 확인 중…');
    await busyRestore.waitFor();
    await busyRestore.evaluate(node => node.click());
    releaseRestore();
    await message(page, 'alert', '계정 복구를 확인하지 못했어요');
    await button(page, '다시 시도').click();
    await page.waitForURL('**/pivot/home');
    await button(page, 'MY').click();
    await my().getByText('검증 계정님').waitFor();
    assert.deepEqual(writes(restoration).map(row => [row.method, row.csrf, row.body]),
      Array.from({ length: 2 }, () => ['POST', 'fixture-restoration_pending-csrf', null]));
    const lastRestore = fixture.requests.findLastIndex(row => row.path === restoration);
    assert.equal(fixture.requests[lastRestore + 1].path, session);

    for (const failure of [
      { status: 409, code: 'RESTORATION_EXPIRED', text: '계정 복구 기한이 지났어요' },
      { status: 403, code: 'RESTORATION_SESSION_REQUIRED', text: '다시 로그인해 주세요' },
      { status: 401, code: 'AUTH_REQUIRED', text: '로그인이 만료되었어요' },
    ]) {
      await openRestoration();
      fixture.state.failNext = { path: restoration, method: 'POST', ...failure };
      await button(page, '계정 복구하기').click();
      await message(page, failure.status === 401 ? 'status' : 'alert', failure.text);
      if (failure.status !== 401) {
        assert.ok(await focused(button(page, '로그인 화면으로')));
        await button(page, '로그인 화면으로').click();
      }
      await button(page, '카카오 로그인').waitFor();
    }

    fixture.rotate('active');
    await openMy();
    await button(my(), '회원탈퇴').click();
    const deletesBeforeSwitch = writes(profile).length;
    fixture.state.userId = 99;
    fixture.state.csrf = 'another-account-csrf';
    await button(dialog, '탈퇴').click();
    await message(page, 'status', '로그인 상태가 바뀌었어요');
    assert.equal(writes(profile).length, deletesBeforeSwitch);
    assert.equal(await my().count(), 0);

    await openRestoration();
    const restoresBeforeSwitch = writes(restoration).length;
    fixture.state.csrf = 'another-pending-account-csrf';
    await button(page, '계정 복구하기').click();
    await message(page, 'alert', '복구할 계정의 로그인 상태를 확인하지 못했어요');
    assert.equal(writes(restoration).length, restoresBeforeSwitch);

    await openRestoration();
    await page.route(url => url.origin === apiOrigin && url.pathname === session, async route => {
      if (fixture.state.phase === 'active') fixture.state.userId = 100;
      await route.continue();
    });
    await button(page, '계정 복구하기').click();
    await message(page, 'alert', '복구할 계정의 로그인 상태를 확인하지 못했어요');
    assert.ok(page.url().endsWith('/pivot/login'));
    fixture.state.userId = 12;

    const requestsBeforeDemo = fixture.requests.length;
    await page.goto(base + '/pivot/demo');
    await heading(page, 'me;nu, 당신의 취향을 위한 시작').waitFor();
    await page.goto(base + '/pivot/demo?view=my');
    await button(page, '회원탈퇴').click();
    const demoDialog = page.getByRole('dialog', { name: 'Demo 회원탈퇴 안내' });
    await demoDialog.getByText('시연용 화면이라 실제 계정 삭제 API를 호출하거나 개인정보를 삭제하지 않아요.', { exact: true }).waitFor();
    await button(demoDialog, '확인').click();
    assert.equal(fixture.requests.length, requestsBeforeDemo);
    assert.deepEqual(errors, []);
    console.log(`PASS ${width}px: withdrawal, restoration, errors, account isolation, keyboard and zero Demo API requests`);
  } finally { await context.close(); }
}

(async () => {
  const fixture = createApiFixture({ apiOrigin, frontendOrigin: base, oauthCallbackPhase: 'restoration_pending' });
  const server = await fixture.listen();
  let browser;
  try {
    browser = await chromium.launch({ headless: true, channel: process.env.PIVOT_BROWSER_CHANNEL || 'chrome' });
    for (const width of [320, 375, 390]) await verify(browser, fixture, width);
  } finally {
    await browser?.close();
    server.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
