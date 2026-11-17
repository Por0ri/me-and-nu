// Local HTTP interception only; no request is forwarded to a real backend.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const base = process.argv[2] || 'http://127.0.0.1:3194';
const output = process.argv[3] || path.join(require('node:os').tmpdir(), 'pivot-my-activity');
const local = url => ['localhost', '127.0.0.1'].includes(url.hostname) && url.protocol === 'http:';
assert.ok(local(new URL(base)));
const results = [];
async function verify(browser, width) {
  const context = await browser.newContext({ viewport: { width, height: 812 }, reducedMotion: 'reduce', serviceWorkers: 'block' });
  const page = await context.newPage(); page.setDefaultTimeout(15000);
  let user = 7, mode = 'normal', hold = false, release = [], calls = [], errors = [], unexpected = [], step = 'MY entry';
  const card = id => ({ contentId: id, topicId: 83, title: `회원 ${user} 활동 ${id}`, summary: '서버 응답의 요약을 표시합니다.', sourceName: '테스트 출처',
    imageUrl: `${base}/pivot/home/slide-1.png`, productionType: 'human', publishedAt: null, subtopicNames: [], likedAt: '2026-11-09T01:00:00Z', viewedAt: '2026-11-09T02:00:00Z' });
  const session = () => ({ authenticated: user !== null, sessionState: user === null ? 'anonymous' : 'active', onboardingCompleted: user !== null,
    user: user === null ? null : { id: user, accountType: 'user' }, csrfToken: `fixture-${user}` });
  page.on('pageerror', error => errors.push(error.message));
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    if (!local(url)) { unexpected.push(url.origin); return route.abort(); }
    if (url.pathname === '/favicon.ico') return route.fulfill({ status: 204 });
    if (!url.pathname.startsWith('/api/')) return route.continue();
    calls.push(url.pathname + url.search);
    const reply = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) }).catch(() => {});
    if (url.pathname === '/api/v1/auth/session') return reply(session());
    if (url.pathname === '/api/v1/users/me') return reply({ userId: user, nickname: `회원 ${user}`, email: null, accountType: 'user' });
    if (url.pathname === '/api/v1/users/me/notification-settings') return reply({ inAppEnabled: true, nightInAppEnabled: false, version: 1 });
    if (url.pathname === '/api/v1/me/topics') return reply({ topics: [{ id: 83, code: 'anime', name: '애니메이션', status: 'active', deletedAt: null, subtopicIds: [] }] });
    if (url.pathname === '/api/v1/topics/83/feed') return reply({ selectedTopicId: 83, sections: [], nextCursor: null, emptyReason: null });
    if (url.pathname === '/api/v1/users/me/bookmarks') return reply({ items: [{ ...card(91), savedItemId: 10, tags: [], resurfaceEnabled: false }], nextCursor: null });
    if (/\/users\/me\/(likes|recent-views)$/.test(url.pathname)) {
      const second = url.searchParams.has('cursor');
      assert.equal(url.searchParams.get('limit'), '20'); assert.equal(url.searchParams.has('topicId'), false);
      if (second) assert.equal(url.searchParams.get('cursor'), 'opaque +/=');
      const status = mode === '401' ? 401 : mode === 'error' || (mode === 'more-error' && second) ? 503 : 200;
      const offset = url.pathname.endsWith('likes') ? 40 : 60;
      const body = status !== 200 ? { code: 'AUTH_REQUIRED' } : mode === 'empty' ? { items: [], nextCursor: null }
        : { items: (mode === 'long' ? Array.from({ length: 20 }, (_, i) => offset + i + 1) : second ? [offset + 1, offset + 3] : [offset + 1, offset + 2]).map(card), nextCursor: second || mode === 'long' ? null : 'opaque +/=' };
      if (hold) await new Promise(resolve => release.push(resolve));
      return reply(body, status);
    }
    if (/\/contents\/\d+$/.test(url.pathname)) {
      const id = Number(url.pathname.split('/').at(-1)); assert.equal(url.searchParams.get('topicId'), '83');
      return reply({ ...card(id), displayMode: 'full_body', body: '실제 DETAIL 컴포넌트로 전달된 본문입니다.', excerpt: null,
        sourceUrl: 'https://example.com/source', publisher: '출처', myReaction: { liked: true, bookmarked: false, preference: null }, subtopicIds: [], notices: [], practicalInfo: {}, isPromotional: false, share: { url: 'https://example.com/source', text: '활동' }, channel: null });
    }
    if (url.pathname === '/api/v1/users/me/activity-events') return reply({ saved: request.postDataJSON().events.length, duplicated: 0, rejected: 0 });
    unexpected.push(request.method() + ' ' + url.pathname); return route.abort();
  });
  const screen = page.locator('[data-activity-state]:visible');
  const ready = async count => { await screen.locator('[data-activity-content]').nth(count - 1).waitFor(); assert.equal(await screen.locator('[data-activity-content]').count(), count); };
  const visit = async (tab = 'likes') => page.goto(`${base}/pivot/home?view=activity&activityTab=${tab}`);
  const releaseAll = () => { hold = false; release.splice(0).forEach(resolve => resolve()); };
  const untilHeld = async () => { await page.waitForFunction(() => document.querySelector('[data-activity-state="loading"]')); for (let i = 0; i < 100 && !release.length; i++) await new Promise(resolve => setTimeout(resolve, 25)); assert.ok(release.length); };
  try {
    await page.goto(`${base}/pivot/home?view=my`); await page.getByRole('button', { name: '내 활동', exact: true }).click(); await ready(2);
    assert.equal(new URL(page.url()).searchParams.get('view'), 'activity');
    await page.evaluate(() => document.fonts.ready);
    await screen.locator('img').evaluateAll(images => Promise.all(images.map(img => img.decode())));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    const geometry = await screen.evaluate(element => {
      const rect = selector => { const { width, height } = element.querySelector(selector).getBoundingClientRect(); return { width, height }; };
      return { tabs: rect('[role=tablist]'), card: rect('[data-activity-content]'), logo: rect('img[alt="me;nu"]'), back: rect('button[aria-label="뒤로가기"] img'), notification: rect('button[aria-label="알림"] img') };
    });
    assert.equal(geometry.tabs.width, Math.min(width, 430) - 48); assert.equal(geometry.tabs.height, 38);
    assert.equal(geometry.logo.width, 103); assert.equal(geometry.back.width, 24); assert.equal(geometry.notification.width, 24);
    if (width === 375) assert.ok(Math.abs(geometry.card.width - 148) < 1);
    await page.screenshot({ path: path.join(output, `likes-${width}.png`), fullPage: true });
    step = 'pagination retry'; mode = 'more-error'; await screen.getByRole('button', { name: '더 보기', exact: true }).click();
    await screen.getByRole('button', { name: '추가 목록 다시 시도' }).waitFor(); await ready(2);
    mode = 'normal'; hold = true; const before = calls.filter(call => call.includes('/likes?')).length;
    await screen.getByRole('button', { name: '추가 목록 다시 시도' }).click();
    await screen.getByRole('button', { name: '불러오는 중…' }).waitFor(); assert.equal(await screen.getByRole('button', { name: '불러오는 중…' }).isDisabled(), true);
    releaseAll(); await ready(3); assert.equal(calls.filter(call => call.includes('/likes?')).length, before + 1);
    step = 'Detail return'; await screen.locator('[data-activity-content="43"]').click(); await page.locator('[data-detail-state="ready"]').waitFor();
    assert.equal(new URL(page.url()).searchParams.get('from'), 'activity');
    await page.getByRole('button', { name: '뒤로가기', exact: true }).click(); await ready(3);
    await page.waitForFunction(() => document.activeElement?.getAttribute('data-activity-content') === '43');
    step = 'recent and MY return'; await screen.getByRole('tab', { name: '최근 본 콘텐츠', exact: true }).click(); await ready(2);
    assert.ok(await screen.locator('[data-activity-content="61"]').count());
    await page.screenshot({ path: path.join(output, `recent-${width}.png`), fullPage: true });
    await screen.locator('[data-activity-content="61"]').click(); await page.locator('[data-detail-state="ready"]').waitFor(); await page.goBack(); await ready(2);
    assert.equal(await screen.getByRole('tab', { name: '최근 본 콘텐츠', exact: true }).getAttribute('aria-selected'), 'true');
    await screen.getByRole('button', { name: '뒤로가기', exact: true }).click(); await page.locator('[data-my-state="ready"]').waitFor();
    await page.getByRole('button', { name: '내 활동', exact: true }).click(); await ready(2);
    await screen.getByRole('button', { name: 'MY', exact: true }).click(); await page.locator('[data-my-state="ready"]').waitFor();
    step = 'Saved regression'; await page.locator('[data-my-state]').getByRole('button', { name: 'SAVE', exact: true }).click();
    await page.locator('[data-saved-content="91"]').click(); await page.locator('[data-detail-state="ready"]').waitFor(); await page.getByRole('button', { name: '뒤로가기', exact: true }).click();
    await page.locator('[data-saved-state="ready"]:visible').waitFor();
    step = 'mobile/desktop scrolling'; mode = 'long'; await visit(); await ready(20);
    await screen.locator('[data-activity-content="60"]').scrollIntoViewIfNeeded();
    const scrolled = await page.evaluate(() => {
      const card = document.querySelector('[data-activity-content="60"]');
      const rect = card.getBoundingClientRect();
      let offset = window.scrollY; for (let node = card.parentElement; node; node = node.parentElement) offset += node.scrollTop;
      return { offset, bottom: rect.bottom, top: rect.top, viewport: innerHeight };
    });
    assert.ok(scrolled.offset > 100, 'Long activity grid scrolls');
    assert.ok(scrolled.top >= 0 && scrolled.bottom <= scrolled.viewport, 'Last card is reachable');
    await screen.locator('[data-activity-content="60"]').click(); await page.locator('[data-detail-state="ready"]').waitFor();
    await page.getByRole('button', { name: '뒤로가기', exact: true }).click(); await ready(20);
    await page.waitForFunction(() => document.activeElement?.getAttribute('data-activity-content') === '60');
    for (const tab of ['likes', 'recent-views']) {
      step = `${tab} loading/empty/error`; mode = 'empty'; hold = true; await visit(tab); await untilHeld(); releaseAll(); await screen.getByText(tab === 'likes' ? '좋아요한 콘텐츠가 아직 없어요' : '최근 30일 동안 본 콘텐츠가 없어요', { exact: true }).waitFor();
      mode = 'error'; await visit(tab); await screen.getByRole('button', { name: '다시 시도', exact: true }).click();
      mode = 'normal'; await screen.getByRole('button', { name: '다시 시도', exact: true }).click(); await ready(2);
    }
    step = 'tab switch suppresses stale response'; hold = true; await visit(); await untilHeld();
    hold = false; await screen.getByRole('tab', { name: '최근 본 콘텐츠', exact: true }).click(); await ready(2); releaseAll();
    assert.equal(await screen.locator('[data-activity-content="41"]').count(), 0);
    step = 'account boundary and late 401'; mode = '401'; hold = true; await visit(); await untilHeld();
    user = 8; mode = 'normal'; hold = false; await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }))); await ready(2);
    releaseAll(); await screen.getByRole('button', { name: /회원 8 활동 41/ }).waitFor(); assert.doesNotMatch(await screen.innerText(), /회원 7/);
    step = 'expired session'; mode = '401'; user = null; await visit(); await page.waitForURL('**/pivot/login');
    assert.equal(await page.locator('[data-activity-content]').count(), 0);
    step = 'Demo isolation'; const apiCount = calls.length; await page.goto(`${base}/pivot/demo?view=activity`);
    await page.getByText(/Demo에서는 실제 좋아요/).waitFor(); assert.equal(calls.length, apiCount); assert.equal(await page.locator('[data-activity-content]').count(), 0);
    assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
    results.push({ width, passed: true, geometry, scrolled, checks: ['entry', 'likes', 'recent', 'pagination/retry/dedup', 'detail/back', 'MY/back', 'SAVED regression', 'mobile/desktop scroll', 'loading/empty/error both lists', 'tab race', 'account/late401', '401/login', 'Demo isolation'], pageErrors: errors });
  } catch (error) { throw new Error(`${width}px ${step}: ${error.message}`, { cause: error }); }
  finally { releaseAll(); await context.close(); }
}
(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: process.env.PIVOT_BROWSER_CHANNEL || 'msedge' });
  try { for (const width of [320, 375, 390, 1280]) await verify(browser, width); }
  finally { await browser.close(); fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(results, null, 2)); }
  console.log(JSON.stringify(results, null, 2));
})().catch(error => { console.error(error); process.exitCode = 1; });
