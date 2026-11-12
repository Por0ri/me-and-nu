// Loopback-only, in-memory API responses. No request reaches a service backend.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const { createApiFixture } = require('./helpers/pivot-api-fixture.cjs');
const base = process.argv[2] || 'http://127.0.0.1:3194';
const out = process.argv[3] || path.join(require('node:os').tmpdir(), 'pivot-my-save-ui');
assert.ok(['localhost', '127.0.0.1'].includes(new URL(base).hostname) && new URL(base).protocol === 'http:');
const near = (a, b, label) => assert.ok(Math.abs(a - b) < 1, `${label}: ${a} / ${b}`);
const button = (root, name) => root.getByRole('button', { name, exact: true });
const frame = page => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
const results = [];

async function verify(browser, width, height) {
  const context = await browser.newContext({ viewport: { width, height }, hasTouch: true, isMobile: width < 500, reducedMotion: 'reduce', serviceWorkers: 'block' });
  const page = await context.newPage(); page.setDefaultTimeout(15000);
  page.setDefaultNavigationTimeout(60000);
  const fixture = createApiFixture({ apiOrigin: base, frontendOrigin: base, topicId: 83 }); fixture.rotate('active');
  for (let id = 43; id < 81; id++) fixture.contents.push({ id, title: `긴 저장 목록 ${id}`, summary: '이미지 내부 요약', body: '본문' });
  fixture.contents.forEach(item => fixture.state.saved.add(item.id));
  fixture.state.notifications = [{ notificationId: 9, type: 'system_notice', title: 'Mock 새 알림', body: '알림 내용', isRead: false, createdAt: new Date().toISOString(), readAt: null }];
  let savedMode = 'ready', holdSaved, releaseSaved, tasteWrites = 0, step = 'SAVE', expiredTaste = false, expiredTasteReads = 0;
  let taste = { topicId: 83, works: [fixture.works[0]], preferenceTagIds: [201, 301], consumptionType: 'world' };
  const errors = [], unexpected = [], requests = [];
  page.on('pageerror', e => errors.push(e.message));
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    if (!['localhost', '127.0.0.1'].includes(url.hostname)) { unexpected.push(url.href); return route.abort(); }
    if (!url.pathname.startsWith('/api/')) return route.continue();
    requests.push([request.method(), url.pathname]);
    const reply = (body, status = 200, headers = {}) => route.fulfill({ status, headers: { 'access-control-allow-origin': base, 'access-control-allow-credentials': 'true', ...headers }, contentType: 'application/json', body: JSON.stringify(body) }).catch(() => {});
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers: { 'access-control-allow-origin': base, 'access-control-allow-credentials': 'true', 'access-control-allow-headers': 'content-type,x-csrf-token', 'access-control-allow-methods': 'GET,POST,PUT,PATCH,DELETE,OPTIONS' } });
    if (url.pathname === '/api/v1/users/me/activity-events') return reply({ saved: request.postDataJSON().events.length, duplicated: 0, rejected: 0 });
    if (url.pathname === '/api/v1/topics') return reply({ topics: [fixture.topic] });
    if (url.pathname === '/api/v1/me/taste') {
      if (expiredTaste) {
        // A real expiration also makes the following session refresh anonymous.
        expiredTasteReads++; fixture.rotate('anonymous');
        return reply({ code: 'AUTH_REQUIRED' }, 401);
      }
      if (request.method() === 'PUT') {
        tasteWrites++; const data = request.postDataJSON();
        assert.deepEqual(data.answers.favoriteEntityIds, [101, 102]); assert.deepEqual(data.answers.preferenceTagIds, [201, 301]);
        assert.equal(data.answers.consumptionType, 'news');
        taste = { ...taste, works: fixture.works.slice(0, 2), consumptionType: 'news' };
      }
      return reply(taste);
    }
    if (url.pathname === '/api/v1/me/taste/works') return reply({ items: fixture.works, nextCursor: null });
    if (url.pathname === '/api/v1/me/taste/preference-options') {
      const entityIds = request.postDataJSON().entityIds;
      const tags = fixture.tags.map(tag => ({ ...tag, sourceEntityIds: tag.sourceEntityIds.filter(id => entityIds.includes(id)) }));
      return reply({ entityIds, genres: tags.filter(t => t.kind === 'genre'), themes: tags.filter(t => t.kind === 'theme'),
        minSelections: 2, minGenreSelections: 1, minThemeSelections: 1, maxSelections: null, maxSelectionsPerKind: 7 });
    }
    if (url.pathname === '/api/v1/me/taste/hints') return reply({ topicId: 83, enough: true, contents: 8,
      works: [{ entityId: 101, name: '테스트 작품 1', percent: 62 }], genres: [{ tagId: 201, name: '판타지' }], themes: [{ tagId: 301, name: '우정' }],
      types: ['ost', 'news', 'goods', 'events', 'world', 'relationships'].map((answer, i) => ({ answer, percent: [42, 20, 18, 8, 7, 5][i] })) });
    if (url.pathname === '/api/v1/users/me/bookmarks') {
      if (savedMode === 'loading') await new Promise(resolve => { releaseSaved = resolve; holdSaved = true; });
      if (savedMode === 'error') return reply({ code: 'UNAVAILABLE' }, 503);
      if (savedMode === 'empty') return reply({ items: [], nextCursor: null });
    }
    const response = fixture.dispatch(url.href, { method: request.method(), headers: request.headers(), body: request.postData() });
    if (url.pathname === '/api/v1/users/me/bookmarks') response.body.items.forEach(item => { item.imageUrl = base + '/pivot/home/chrome/mascot.png'; item.eyebrow = '새로운 시선'; });
    if (url.pathname === '/api/v1/topics/83/feed') response.body.sections[0].contents.forEach(item => { item.imageUrl = base + '/pivot/home/chrome/mascot.png'; });
    if (response.body?.code === 'FIXTURE_ROUTE_NOT_FOUND') unexpected.push(request.method() + ' ' + url.pathname);
    return reply(response.body, response.status, response.headers);
  });
  const visit = view => page.goto(`${base}/pivot/home?view=${view}`);
  const saved = page.locator('[data-saved-state]:visible'), my = page.locator('[data-my-state]:visible'), home = page.locator('[data-home-state]:visible');
  const shot = async name => { await page.evaluate(() => document.fonts.ready); await page.screenshot({ path: path.join(out, `${name}-${width}.png`) }); };
  const checkHeader = async (root, dot = false) => {
    const header = root.locator('[data-home-header]');
    await header.locator('img').evaluateAll(images => Promise.all(images.map(img => img.decode())));
    const geometry = await header.evaluate(el => {
      const rect = selector => el.querySelector(selector).getBoundingClientRect().toJSON();
      const img = el.querySelector('img[src$="notification.svg"]'), canvas = document.createElement('canvas'); canvas.width = canvas.height = 24;
      const ctx = canvas.getContext('2d'); ctx.drawImage(img, 0, 0); const rgba = ctx.getImageData(0, 0, 24, 24).data;
      let grey = 0; for (let i = 0; i < rgba.length; i += 4) if (rgba[i] === 113 && rgba[i + 1] === 113 && rgba[i + 2] === 122 && rgba[i + 3] === 255) grey++;
      return { logo: rect('img[alt="me;nu"]'), bell: rect('img[src$="notification.svg"]'), grey, box: el.getBoundingClientRect().toJSON() };
    });
    near(geometry.logo.width, 103, 'Logo'); near(geometry.bell.width, 24, 'Bell'); near(geometry.bell.height, 24, 'Bell'); assert.ok(geometry.grey > 30, 'Rendered Grey/600 pixels');
    assert.match(await header.innerText(), /Little discoveries\s*for a more you/);
    if (dot) { await root.locator('[data-new-notice]').waitFor(); assert.equal(await root.locator('[data-new-notice]').evaluate(el => getComputedStyle(el).backgroundColor), 'rgb(255, 0, 0)'); }
    return geometry;
  };
  const hitBell = async root => { const b = await button(root, '알림').boundingBox(); assert.ok(await page.evaluate(b => !!document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2)?.closest('[data-notification-navigation]'), b)); };
  try {
    await visit('saved'); await page.locator('[data-saved-state="ready"]').waitFor(); await checkHeader(saved, true);
    const cards = saved.locator('[data-saved-content]'); assert.equal(await cards.count(), 40);
    const initial = await cards.first().boundingBox(); near(initial.y, 60, 'Initial SAVE card'); near(initial.width, (Math.min(width, 430) - 79) / 2, 'Two-column size');
    const nav = await saved.locator('nav').boundingBox();
    const samples = [];
    for (const top of [0, 120, 400, 1400, 10000]) {
      await page.evaluate(top => window.scrollTo({ top, behavior: 'instant' }), top); await frame(page);
      const b = await saved.locator('header').boundingBox(); near(b.y, 0, 'Sticky top'); await hitBell(saved);
      near((await saved.locator('nav').boundingBox()).y, nav.y, 'NAV top');
      assert.equal(await saved.evaluate(el => getComputedStyle(el).overflowY), 'visible');
      samples.push({ scroll: await page.evaluate(() => scrollY), top: b.y });
    }
    for (const x of [10, width - 10]) {
      await page.evaluate(() => scrollTo(0, 200)); await frame(page); await page.mouse.move(x, 200); await page.mouse.wheel(0, 120);
      await page.waitForFunction(() => Math.abs(scrollY - 320) < 2);
    }
    await page.evaluate(() => scrollTo(0, 200)); await frame(page);
    const cdp = await context.newCDPSession(page);
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: 10, y: 400, id: 1 }] });
    for (let i = 1; i <= 8; i++) await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: 10, y: 400 - i * 20, id: 1 }] });
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] }); await cdp.detach();
    await page.waitForFunction(() => scrollY > 230); await hitBell(saved); await shot('save-sticky');
    await cards.nth(8).click(); await page.locator('[data-detail-state="ready"]:visible').waitFor();
    await button(page.locator('[data-detail-state]:visible'), '뒤로가기').click(); await saved.waitFor();
    await page.waitForFunction(() => document.activeElement?.dataset.savedContent === '49');
    const savedY = await page.evaluate(() => scrollY); assert.ok(savedY > 100);
    await button(saved, '알림').click(); await page.getByRole('main', { name: '알림함', exact: true }).waitFor(); await button(page.getByRole('main', { name: '알림함', exact: true }), '뒤로가기').click(); await saved.waitFor(); await hitBell(saved);
    step = 'HOME restore'; await button(saved, 'HOME').click(); await page.locator('[data-home-state="ready"]:visible').waitFor();
    await checkHeader(home, true); await home.evaluate(el => el.scrollTo(0, 500)); await frame(page); const homeY = await home.evaluate(el => el.scrollTop);
    await button(home, 'SAVE').click(); await saved.waitFor(); await button(saved, 'HOME').click(); await home.waitFor();
    await page.waitForFunction(y => Math.abs(document.querySelector('[data-home-state]').scrollTop - y) < 1, homeY);
    step = 'SAVE states';
    for (const state of ['loading', 'error', 'empty']) {
      savedMode = state; await visit('saved'); await page.locator(`[data-saved-state="${state}"]`).waitFor(); await checkHeader(saved); await hitBell(saved);
      if (state === 'loading') { assert.ok(holdSaved); savedMode = 'ready'; releaseSaved(); await page.locator('[data-saved-state="ready"]').waitFor(); }
      if (state === 'error') { savedMode = 'ready'; await button(saved, '다시 시도').click(); await page.locator('[data-saved-state="ready"]').waitFor(); }
    }
    savedMode = 'ready'; step = 'MY dialogs'; await visit('my'); await page.locator('[data-my-state="ready"]').waitFor(); await checkHeader(my, true);
    for (const action of ['로그아웃', '회원탈퇴']) {
      const trigger = button(my, action); await trigger.click(); const dialog = page.getByRole('dialog', { name: action, exact: true }); await dialog.waitFor();
      const colors = await dialog.locator('button').evaluateAll(items => items.map(el => ({ bg: getComputedStyle(el).backgroundColor, color: getComputedStyle(el).color, border: getComputedStyle(el).borderColor })));
      assert.equal(colors.at(-1).bg, 'rgb(112, 67, 180)'); assert.equal(colors[0].bg, 'rgb(250, 250, 250)'); assert.equal(colors[0].border, 'rgb(156, 121, 230)');
      if (action === '회원탈퇴') assert.equal(await dialog.locator('strong').evaluate(el => getComputedStyle(el).color), 'rgb(180, 35, 24)');
      await shot(action === '로그아웃' ? 'logout' : 'withdrawal'); await page.keyboard.press('Escape'); await dialog.waitFor({ state: 'hidden' }); assert.equal(await trigger.evaluate(el => el === document.activeElement), true);
    }
    step = 'interest'; await button(my, '관심분야 관리').click(); const interest = page.locator('[data-my-interest-header]'); await interest.waitFor(); const interestGeo = await checkHeader(interest);
    near(interestGeo.box.height, 44, 'Management header'); near((await page.locator('#interest-heading').boundingBox()).y, height > 700 ? 134 : 133, 'Interest heading');
    near((await button(interest, '뒤로가기').locator('span').boundingBox()).width, 28, 'Back circle');
    assert.equal(await page.getByRole('checkbox').count(), 0); await page.getByText('선택중', { exact: true }).waitFor(); await shot('interest');
    await button(interest, '알림').click(); await page.getByRole('main', { name: '알림함', exact: true }).waitFor(); await button(page.getByRole('main', { name: '알림함', exact: true }), '뒤로가기').click(); await interest.waitFor(); await button(interest, '뒤로가기').click(); await my.waitFor();
    step = 'taste questions'; await button(my, '내 피드 취향 조정하기').click(); await page.locator('#question-heading').waitFor();
    const checkQuestion = async step => {
      await page.waitForFunction(step => document.querySelector('[role=progressbar]')?.getAttribute('aria-valuenow') === String(step), step); await page.evaluate(() => scrollTo(0, 0)); await frame(page);
      const header = page.locator('[data-my-question-header]'); await checkHeader(header); near((await header.boundingBox()).y, 0, 'MY header top');
      near((await page.getByRole('navigation', { name: '온보딩 화면 탐색' }).boundingBox()).y, 45, 'Progress row');
      assert.equal(await page.locator('#question-heading').evaluate(el => el === document.activeElement), true);
      await shot(`taste-q${step}`);
      await page.evaluate(() => scrollTo(0, 1000)); await frame(page); await hitBell(header); near((await header.boundingBox()).y, 0, 'MY sticky header');
    };
    await checkQuestion(1); await page.getByRole('searchbox', { name: '작품 검색' }).fill('테스트'); await button(page, '테스트 작품 2').click();
    page.once('dialog', dialog => dialog.dismiss()); await button(page.locator('[data-my-question-header]'), '알림').click();
    await button(page, '테스트 작품 2 선택 해제').waitFor(); assert.equal(new URL(page.url()).pathname, '/pivot/taste');
    await button(page, '선택한 작품으로 다음').click(); await page.getByRole('checkbox', { name: '판타지', exact: true }).waitFor(); await checkQuestion(2);
    const theme = page.getByRole('checkbox', { name: '우정', exact: true }); await theme.uncheck(); assert.equal(await button(page, '장르·소재 선택 완료').isDisabled(), true); await theme.check();
    await button(page, '장르·소재 선택 완료').click(); await checkQuestion(3); await button(page, '더보기').click(); assert.equal(await button(page, '접기').getAttribute('aria-expanded'), 'true');
    await page.getByRole('radio', { name: '새로운 소식을 빠르게 확인한다', exact: true }).check(); await button(page, '선택 완료').click();
    await button(page, '새로운 피드 보러가기').waitFor(); assert.equal(await page.locator('[data-home-header]').count(), 0); await shot('taste-result');
    await button(page, '새로운 피드 보러가기').click(); await page.waitForURL('**/pivot/home'); await home.waitFor(); assert.equal(tasteWrites, 1);
    await page.goto(base + '/pivot/taste'); await page.locator('#question-heading').waitFor(); page.once('dialog', dialog => dialog.accept()); await button(page.locator('[data-my-question-header]'), '알림').click();
    await page.getByRole('main', { name: '알림함', exact: true }).waitFor(); await button(page.getByRole('main', { name: '알림함', exact: true }), '뒤로가기').click(); await page.locator('#question-heading').waitFor();
    step = 'session boundary'; fixture.state.userId = 13; taste = { ...taste, works: [fixture.works[2]] };
    await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true })));
    await button(page, '테스트 작품 3 선택 해제').waitFor(); assert.equal(await button(page, '테스트 작품 1 선택 해제').count(), 0);
    expiredTaste = true; await page.goto(base + '/pivot/taste'); await page.waitForURL('**/pivot/login'); assert.ok(expiredTasteReads > 0);
    step = 'signup unchanged';
    for (const screen of ['interest', 'q1', 'q2', 'q3', 'result']) {
      await page.goto(`${base}/pivot/preview?screen=${screen}&selected=1`); assert.equal(await page.locator('[data-my-question-header], [data-my-interest-header]').count(), 0);
      if (screen.startsWith('q')) { await page.locator('#question-heading').waitFor(); assert.equal(await page.getByRole('progressbar').getAttribute('aria-valuenow'), screen[1]); }
    }
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    assert.equal(requests.filter(([method, url]) => (method === 'DELETE' && url === '/api/v1/users/me') || url === '/api/v1/auth/logout' || url === '/api/v1/inquiries').length, 0);
    assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
    results.push({ width, height, status: 'pass', samples, initial, savedY, homeY, tasteWrites, checks: ['SAVE long list/sticky/wheel/touch/hit testing/states/detail', 'HOME restoration', 'grey bell/red dot', 'account colors/cancel/focus', 'interest readonly/header/inbox', 'MY Q1-Q3/search/tags/hints/result/save/inbox warning', 'session switch/401', 'signup isolation'] });
    console.log(`PASS MY/SAVE ${width}x${height}`);
  } catch (error) { await shot('failure'); throw new Error(`${width} ${step}: ${error.message}`, { cause: error }); }
  finally { releaseSaved?.(); await context.close(); }
}
(async () => {
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: process.env.PIVOT_BROWSER_CHANNEL || 'chrome' });
  try { for (const [width, height] of [[320, 568], [375, 812], [390, 844], [1280, 900], [1440, 1000]]) {
    if (process.env.PIVOT_VIEWPORT_WIDTH && width !== Number(process.env.PIVOT_VIEWPORT_WIDTH)) continue;
    await verify(browser, width, height);
  } } finally { await browser.close(); fs.writeFileSync(path.join(out, 'results.json'), JSON.stringify(results, null, 2)); }
})().catch(error => { console.error(error); process.exitCode = 1; });
