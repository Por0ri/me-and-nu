// Local Playwright mocks only. No API request is forwarded to any server.
// Start Next with NEXT_PUBLIC_PIVOT_ENABLE_LOCAL_API=true and an API URL using
// the same loopback hostname. PIVOT_PLAYWRIGHT_MODULE can reuse an installation.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const base = process.argv[2] || 'http://127.0.0.1:3193';
const output = process.argv[3] || path.join(require('node:os').tmpdir(), 'pivot-my-profile-api');
const loopback = url => ['localhost', '127.0.0.1'].includes(url.hostname) && url.protocol === 'http:';
assert.ok(loopback(new URL(base)), 'Only local mock checks are allowed');
const results = [];
const email = ['profile.fixture', 'example.com'].join('@');
const until = async predicate => {
  const deadline = Date.now() + 15000;
  while (!predicate()) {
    if (Date.now() > deadline) throw new Error('Mock wait timed out');
    await new Promise(resolve => setTimeout(resolve, 25));
  }
};

async function verify(browser, viewport) {
  const context = await browser.newContext({ viewport, isMobile: true, hasTouch: true, reducedMotion: 'reduce', serviceWorkers: 'block' });
  const page = await context.newPage(); page.setDefaultTimeout(15000);
  let step = 'initial loading', userId = 7, mode = 'valid', hold = true, pending = [];
  let logoutFails = false, logoutCalls = 0, tasteWrites = 0, pageErrors = 0, leakedConsole = false;
  const checks = [], blocked = [], calls = [];
  const work = { entityId: 11, name: '검증 작품', aliases: [], imageUrl: null, releaseYear: 2024, mediaFormat: 'TV', selectable: true, unavailableReason: null };
  let taste = { topicId: 83, works: [work], preferenceTagIds: [101, 103], consumptionType: 'world' };
  const session = () => ({ authenticated: userId !== null, sessionState: userId === null ? 'anonymous' : 'active',
    onboardingCompleted: userId !== null, user: userId === null ? null : { id: userId, accountType: 'user' }, csrfToken: 'mock-csrf' });
  page.on('pageerror', () => { pageErrors++; });
  page.on('console', message => { if (message.text().includes(email)) leakedConsole = true; });
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), method = request.method();
    if (!loopback(url)) { blocked.push('external'); return route.abort(); }
    if (url.pathname === '/favicon.ico') return route.fulfill({ status: 204 });
    if (url.pathname.startsWith('/api/')) {
      calls.push({ method, path: url.pathname });
      const reply = (body, status = 200) => route.fulfill({ status, contentType: 'application/json',
        body: status === 204 ? undefined : JSON.stringify(body) });
      if (method === 'GET' && url.pathname === '/api/v1/auth/session') return reply(session());
      if (method === 'GET' && url.pathname === '/api/v1/users/me') {
        let value = { userId, nickname: userId === 7 ? '연결 확인' : '새 사용자', accountType: 'user', email }, status = 200;
        if (mode === 'null-email') value.email = null;
        if (mode === 'invalid-email') value.email = 'invalid';
        if (mode === 'mismatch') value.userId = 999;
        if (mode === 'admin') value.accountType = 'admin';
        if (mode === 'malformed') value = { nickname: 'must-not-render' };
        if (mode === 'long') value.nickname = 'LongProfileNicknameForTesting';
        if (mode === 'error') { value = { message: email }; status = 503; }
        if (mode === 'unauthorized') { value = { code: 'AUTH_REQUIRED' }; status = 401; }
        if (hold) await new Promise(resolve => pending.push(resolve));
        return reply(value, status).catch(() => {}); // Aborted requests may already be gone.
      }
      if (method === 'GET' && url.pathname === '/api/v1/me/topics') return reply({ topics: [{ id: 83, code: 'anime', name: '애니메이션', subtopicIds: [], status: 'active', deletedAt: null }] });
      if (method === 'GET' && url.pathname === '/api/v1/topics/83/feed') return reply({ selectedTopicId: 83, sections: [], nextCursor: null, emptyReason: null });
      if (method === 'GET' && url.pathname === '/api/v1/users/me/bookmarks') return reply({ items: [], nextCursor: null });
      if (method === 'GET' && url.pathname === '/api/v1/topics') return reply({ topics: [{ id: 83, code: 'anime', name: '애니메이션' }] });
      if (method === 'GET' && url.pathname === '/api/v1/me/taste') return reply(taste);
      if (method === 'GET' && url.pathname === '/api/v1/me/taste/works') return reply({ items: [work], nextCursor: null });
      // These expected write requests receive in-memory mock replies only.
      // Nothing is sent to a BE or persisted; all other writes are aborted below.
      if (method === 'POST' && url.pathname === '/api/v1/me/taste/preference-options') return reply({ entityIds: [11],
        genres: [{ tagId: 101, kind: 'genre', code: 'genre', label: '장르 검증', sourceEntityIds: [11] }],
        themes: [{ tagId: 103, kind: 'theme', code: 'theme', label: '소재 검증', sourceEntityIds: [11] }],
        minSelections: 2, minGenreSelections: 1, minThemeSelections: 1, maxSelections: null, maxSelectionsPerKind: 7 });
      if (method === 'PUT' && url.pathname === '/api/v1/me/taste') {
        const input = request.postDataJSON();
        assert.equal(request.headers()['x-csrf-token'], 'mock-csrf');
        assert.deepEqual(input, { topicId: 83, answers: { version: 2, favoriteEntityIds: [11], preferenceTagIds: [101, 103], consumptionType: 'news' } });
        tasteWrites++; taste = { ...taste, consumptionType: input.answers.consumptionType }; return reply(taste);
      }
      if (method === 'POST' && url.pathname === '/api/v1/auth/logout') {
        logoutCalls++; assert.equal(request.headers()['x-csrf-token'], 'mock-csrf');
        if (logoutFails) return reply({ code: 'UNAVAILABLE' }, 503);
        userId = null; return reply(undefined, 204);
      }
      blocked.push(method + ' ' + url.pathname); return route.abort();
    }
    if (url.origin !== new URL(base).origin || !['GET', 'HEAD'].includes(method)) {
      blocked.push('non-asset or write'); return route.abort();
    }
    return route.continue(); // Only local frontend documents and static assets.
  });
  const my = page.locator('[data-my-state]');
  const ready = () => page.locator('[data-my-state="ready"]').waitFor();
  const visit = () => page.goto(base + '/pivot/home?view=my');
  const refresh = () => page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true })));
  const release = () => { hold = false; for (const resolve of pending.splice(0)) resolve(); };
  const privacy = async () => {
    assert.ok(!(await page.content()).includes(email), 'raw email absent from DOM');
    assert.equal(await page.evaluate(value => JSON.stringify({ ...localStorage, ...sessionStorage }).includes(value), email), false);
    assert.equal(leakedConsole, false, 'raw email absent from console');
  };
  const capture = async name => {
    await privacy(); await page.evaluate(() => document.fonts.ready);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'no horizontal overflow');
    await page.screenshot({ path: path.join(output, name + '-' + viewport.width + '.png'), fullPage: true });
  };
  try {
    await visit(); await until(() => pending.length > 0); await page.locator('[data-my-state="loading"]').waitFor();
    assert.equal(await my.getByRole('region', { name: '프로필', exact: true }).count(), 0);
    release(); await ready();
    assert.ok((await my.innerText()).includes('연결 확인님'));
    assert.ok((await my.innerText()).includes('prof***@example.com'));
    await capture('service-profile');
    const card = await my.getByRole('region', { name: '프로필', exact: true }).boundingBox();
    assert.equal(card.x, 24); assert.equal(card.width, viewport.width - 48); assert.equal(card.height, 68);
    for (const toggle of await my.getByRole('switch').all()) assert.equal(await toggle.getAttribute('aria-disabled'), 'true');
    await my.getByRole('button', { name: '회원탈퇴', exact: true }).click();
    await my.getByRole('status').filter({ hasText: '준비 중' }).waitFor();
    // 프로필 수정 opens the nickname page; an unchanged nickname cannot be saved.
    await my.getByRole('button', { name: '프로필 수정', exact: true }).click(); await page.waitForURL('**/pivot/profile');
    await page.getByText('프로필에 표시되는 닉네임을 변경할 수 있어요').waitFor();
    assert.equal(await page.getByRole('button', { name: '저장하기', exact: true }).isDisabled(), true);
    await page.getByRole('button', { name: '뒤로가기', exact: true }).click(); await page.waitForURL('**/pivot/home?view=my'); await ready();
    checks.push('loading, nickname, masked email, layout, pending controls, profile edit and back');
    step = 'email validation and retry';
    for (const variant of ['null-email', 'invalid-email']) {
      mode = variant; await visit(); await ready();
      assert.ok((await my.innerText()).includes('이메일 정보를 확인할 수 없어요')); await privacy();
    }
    for (const variant of ['mismatch', 'admin', 'malformed', 'error']) {
      mode = variant; await visit(); await page.locator('[data-my-state="error"]').waitFor();
      assert.equal(await my.getByRole('region', { name: '프로필', exact: true }).count(), 0);
      assert.ok(!(await my.innerText()).includes('미뉴님')); await privacy();
      mode = 'valid'; await my.getByRole('button', { name: '다시 시도', exact: true }).click(); await ready();
    }
    mode = 'long'; await visit(); await ready(); await capture('service-long-profile'); mode = 'valid';
    checks.push('nullable/invalid email, wrong identity/role/shape, API error/retry, long nickname');
    step = 'Service My to empty Home';
    await my.getByRole('button', { name: 'HOME', exact: true }).click(); await page.locator('[data-home-state="empty"]').waitFor({ state: 'visible' });
    step = 'Service Home to My';
    await page.locator('[data-home-state]').getByRole('button', { name: 'MY', exact: true }).click(); await ready();
    step = 'Service My to empty Saved';
    await my.getByRole('button', { name: 'SAVE', exact: true }).click(); await page.locator('[data-saved-state="empty"]').waitFor({ state: 'visible' });
    step = 'Service Saved to My';
    await page.locator('[data-saved-state]').getByRole('button', { name: 'MY', exact: true }).click(); await ready();
    checks.push('My → Home → My → Saved → My');
    step = 'interest view and back';
    await my.getByRole('button', { name: '관심분야 관리', exact: true }).click(); await page.waitForURL('**/pivot/interests');
    await page.getByText('이미 애니메이션을 탐색중입니다').waitFor();
    await page.getByRole('button', { name: '뒤로가기', exact: true }).click(); await page.waitForURL('**/pivot/home?view=my'); await ready();
    checks.push('My → 관심분야 관리 → back to My');
    step = 'taste edit and save';
    await my.getByRole('button', { name: '내 피드 취향 조정하기', exact: true }).click(); await page.waitForURL('**/pivot/taste');
    await page.getByRole('button', { name: '검증 작품 선택 해제', exact: true }).waitFor();
    await page.getByRole('button', { name: '선택한 작품으로 다음', exact: true }).click();
    await page.getByRole('checkbox', { name: '장르 검증', exact: true }).waitFor();
    assert.equal(await page.getByRole('checkbox', { name: '장르 검증', exact: true }).isChecked(), true);
    assert.equal(await page.getByRole('checkbox', { name: '소재 검증', exact: true }).isChecked(), true);
    await page.getByRole('button', { name: '장르·소재 선택 완료', exact: true }).click();
    assert.equal(await page.getByRole('radio', { name: '설정과 세계관을 파고든다', exact: true }).isChecked(), true);
    await page.getByRole('radio', { name: '새로운 소식을 빠르게 확인한다', exact: true }).check();
    await page.getByRole('button', { name: '선택 완료', exact: true }).click();
    await page.getByRole('button', { name: '새로운 피드 보러가기', exact: true }).click();
    await page.waitForURL('**/pivot/home'); assert.equal(tasteWrites, 1); await visit(); await ready();
    checks.push('taste route, Q1-Q3 prefill/edit and mocked save');
    step = 'user switch and cancellation';
    userId = 8; hold = true; await refresh(); await until(() => pending.length > 0);
    assert.ok(!(await page.locator('body').innerText()).includes('연결 확인님'));
    release(); await ready(); assert.ok((await my.innerText()).includes('새 사용자님'));
    hold = true; await visit(); await until(() => pending.length > 0);
    const oldReplies = pending.splice(0); hold = false; userId = 7; await refresh(); await ready();
    for (const resolve of oldReplies) resolve();
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    assert.ok((await my.innerText()).includes('연결 확인님')); assert.ok(!(await my.innerText()).includes('새 사용자님')); await privacy();
    checks.push('user switch clears prior identity, late response ignored');
    step = 'logout and protected route';
    const logoutDialog = page.getByRole('dialog', { name: '로그아웃', exact: true });
    const openLogout = async () => {
      await my.getByRole('button', { name: '로그아웃', exact: true }).click(); await logoutDialog.waitFor();
    };
    await openLogout(); assert.equal(logoutCalls, 0);
    assert.equal(await logoutDialog.getByRole('button', { name: '취소', exact: true }).evaluate(el => el === document.activeElement), true);
    await page.keyboard.press('Escape'); await logoutDialog.waitFor({ state: 'detached' });
    assert.equal(logoutCalls, 0);
    assert.equal(await my.getByRole('button', { name: '로그아웃', exact: true }).evaluate(el => el === document.activeElement), true);
    await openLogout(); await logoutDialog.getByRole('button', { name: '취소', exact: true }).click();
    await logoutDialog.waitFor({ state: 'detached' }); assert.equal(logoutCalls, 0);
    logoutFails = true; await my.getByRole('button', { name: '로그아웃', exact: true }).click();
    await logoutDialog.getByRole('button', { name: '로그아웃', exact: true }).click();
    await logoutDialog.getByRole('alert').filter({ hasText: '로그아웃하지 못했어요' }).waitFor(); assert.equal(logoutCalls, 1);
    assert.ok((await my.innerText()).includes('연결 확인님'));
    logoutFails = false; await logoutDialog.getByRole('button', { name: '다시 시도', exact: true }).click();
    await page.waitForURL('**/pivot/login'); assert.equal(logoutCalls, 2); assert.equal(await my.count(), 0);
    await visit(); await page.waitForURL('**/pivot/login'); assert.equal(await my.count(), 0);
    userId = 7; hold = true; await visit(); await until(() => pending.length > 0);
    await openLogout(); await logoutDialog.getByRole('button', { name: '로그아웃', exact: true }).click();
    await page.waitForURL('**/pivot/login'); release();
    await privacy(); assert.equal(await my.count(), 0);
    userId = 7; mode = 'unauthorized'; await visit(); await page.waitForURL('**/pivot/login'); assert.equal(await my.count(), 0);
    checks.push('logout confirmation, cancel/Escape/focus, API/CSRF, failure/retry, pending-read logout, 401, protected route');
    assert.deepEqual(blocked, []); assert.equal(pageErrors, 0);
    results.push({ width: viewport.width, status: 'pass', checks, requestCount: calls.length, card });
    console.log('PASS My Page API ' + viewport.width);
  } catch (error) {
    // Only status codes, fixed step names and the redacted first error line.
    // Never persist response bodies, console text or failure screenshots.
    const reason = String(error.message).split('\n')[0].replaceAll(email, '[redacted]');
    const states = await page.locator('[data-home-state], [data-my-state], [data-saved-state]').evaluateAll(elements =>
      elements.map(element => ({ home: element.dataset.homeState, my: element.dataset.myState, saved: element.dataset.savedState,
        visible: element.getClientRects().length > 0 }))).catch(() => []);
    results.push({ width: viewport.width, status: 'fail', step, reason, states, calls, blocked });
    throw new Error('My Page API failed at: ' + step + ' (' + reason + ')');
  } finally { release(); await context.close(); }
}
(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: process.env.PIVOT_BROWSER_CHANNEL || 'msedge' });
  try { for (const viewport of [{ width: 375, height: 812 }, { width: 390, height: 844 }]) await verify(browser, viewport); }
  finally { await browser.close(); fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(results, null, 2)); }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
