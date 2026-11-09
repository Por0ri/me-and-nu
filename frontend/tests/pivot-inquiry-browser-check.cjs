// Loopback-only intercepted API contracts. This does not submit to a real backend.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const base = process.argv[2] || 'http://127.0.0.1:3194';
const output = process.argv[3] || path.join(require('node:os').tmpdir(), 'pivot-inquiry');
const local = url => ['localhost', '127.0.0.1'].includes(url.hostname) && url.protocol === 'http:';
assert.ok(local(new URL(base)));
const results = [];
async function verify(browser, width) {
  const context = await browser.newContext({ viewport: { width, height: 812 }, isMobile: true, hasTouch: true, reducedMotion: 'reduce', serviceWorkers: 'block' });
  const page = await context.newPage(); page.setDefaultTimeout(15000);
  let user = 7, mode = 'normal', hold = false, release = [], calls = [], posts = [], errors = [], unexpected = [], step = 'MY entry';
  const session = () => ({ authenticated: user !== null, sessionState: user === null ? 'anonymous' : 'active', onboardingCompleted: user !== null,
    user: user === null ? null : { id: user, accountType: 'user' }, csrfToken: `fixture-${user}` });
  page.on('pageerror', error => errors.push(error.message));
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    if (!local(url)) { unexpected.push(url.origin); return route.abort(); }
    if (url.pathname === '/favicon.ico') return route.fulfill({ status: 204 });
    if (!url.pathname.startsWith('/api/')) return route.continue();
    calls.push(url.pathname);
    const reply = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) }).catch(() => {});
    if (url.pathname === '/api/v1/auth/session') return reply(session());
    if (url.pathname === '/api/v1/users/me') return reply({ userId: user, nickname: `회원 ${user}`, email: null, accountType: 'user' });
    if (url.pathname === '/api/v1/me/topics') return reply({ topics: [{ id: 83, code: 'anime', name: '애니메이션', status: 'active', deletedAt: null, subtopicIds: [] }] });
    if (url.pathname === '/api/v1/topics/83/feed') return reply({ selectedTopicId: 83, sections: [], nextCursor: null, emptyReason: null });
    if (url.pathname === '/api/v1/users/me/bookmarks') return reply({ items: [], nextCursor: null });
    if (url.pathname === '/api/v1/users/me/activity-events') return reply({ saved: request.postDataJSON().events.length, duplicated: 0, rejected: 0 });
    if (url.pathname === '/api/v1/inquiries') {
      assert.equal(request.method(), 'POST'); assert.equal(request.headers()['x-csrf-token'], `fixture-${user}`);
      const body = request.postDataJSON(); posts.push(body);
      assert.deepEqual(Object.keys(body).sort(), ['detail', 'inquiryType']); assert.ok(['service', 'bug'].includes(body.inquiryType));
      assert.equal(body.detail.trim(), body.detail); assert.ok([...body.detail].length >= 1 && [...body.detail].length <= 500);
      const sentMode = mode;
      if (hold) await new Promise(resolve => release.push(resolve));
      if (sentMode === 'network') return route.abort('failed').catch(() => {});
      if (sentMode === 'csrf') return reply({ code: 'CSRF_INVALID' }, 403);
      if (/^\d+$/.test(sentMode)) return reply({ code: 'ERROR', message: 'private text' }, Number(sentMode));
      if (sentMode === 'malformed') return reply({ status: 'received' }, 201);
      return reply({ inquiryId: posts.length, status: 'received' }, 201);
    }
    unexpected.push(`${request.method()} ${url.pathname}`); return route.abort();
  });
  const screen = page.locator('[data-inquiry-state]:visible');
  const input = screen.getByRole('textbox', { name: '세부사항' });
  const submit = screen.locator('button[type=submit]');
  const visit = async () => { await page.goto(`${base}/pivot/home?view=my`); await page.getByRole('button', { name: '문의 및 신고', exact: true }).click(); await input.waitFor(); };
  const fill = async (detail = '  문의합니다.  ', type = '서비스 문의') => { await screen.getByLabel(type, { exact: true }).check(); await input.fill(detail); };
  const releaseAll = () => { hold = false; release.splice(0).forEach(resolve => resolve()); };
  const untilHeld = async () => { for (let i = 0; i < 200 && !release.length; i++) await new Promise(resolve => setTimeout(resolve, 25)); assert.ok(release.length); };
  try {
    await visit(); assert.equal(new URL(page.url()).searchParams.get('view'), 'inquiry'); assert.equal(await submit.isDisabled(), true);
    await page.evaluate(() => document.fonts.ready);
    await screen.locator('img').evaluateAll(images => Promise.all(images.map(img => img.decode())));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    const geometry = await screen.evaluate(element => {
      const rect = selector => { const { width, height } = element.querySelector(selector).getBoundingClientRect(); return { width, height }; };
      return { card: rect('fieldset').width + 24, submit: rect('button[type=submit]'), logo: rect('img[alt="me;nu"]'), back: rect('button[aria-label="뒤로가기"] img'), notification: rect('button[aria-label="알림"] img') };
    });
    assert.equal(geometry.card, width - 48); assert.equal(geometry.submit.height, 52);
    assert.equal(geometry.logo.width, 103); assert.equal(geometry.back.width, 24); assert.equal(geometry.notification.width, 24);
    await page.screenshot({ path: path.join(output, `empty-${width}.png`), fullPage: true });
    step = 'type and boundaries'; await fill(' \n\t '); assert.equal(await submit.isDisabled(), true);
    await input.fill('가'.repeat(500)); assert.equal(await submit.isDisabled(), false);
    await input.fill('😀'.repeat(500)); await screen.getByText('500/500', { exact: true }).waitFor(); assert.equal(await submit.isDisabled(), false);
    await input.fill('가'.repeat(501)); assert.equal(await submit.isDisabled(), true); await screen.getByRole('alert').waitFor();
    await fill('애니 말고 다른 관심분야는 언제 열리나요?', '버그/오류 신고');
    assert.equal(await screen.getByLabel('서비스 문의', { exact: true }).isChecked(), false);
    assert.equal(await screen.locator('img[src="/pivot/inquiry/check.svg"]').evaluate(img => img.getBoundingClientRect().width), 18);
    await input.blur();
    await page.screenshot({ path: path.join(output, `filled-${width}.png`), fullPage: true });
    step = 'single flight and receipt'; hold = true; const before = posts.length;
    await screen.locator('form').evaluate(form => { form.requestSubmit(); form.requestSubmit(); }); await untilHeld();
    assert.equal(await submit.isDisabled(), true); assert.equal(await input.isDisabled(), true); assert.equal(posts.length, before + 1);
    releaseAll(); await page.locator('[data-inquiry-state="received"]').waitFor(); await screen.getByText('문의가 접수되었어요.', { exact: true }).waitFor();
    assert.equal(await submit.isDisabled(), true);
    await screen.getByRole('button', { name: '뒤로가기', exact: true }).click(); await page.getByRole('button', { name: '문의 및 신고', exact: true }).waitFor();
    assert.equal(new URL(page.url()).searchParams.get('view'), 'my');
    for (const failure of ['csrf', '403', '503', 'network', 'malformed']) {
      step = `${failure} retry`; mode = failure; await visit(); await fill('  보존할 문의 내용  ', '버그/오류 신고');
      await submit.click(); await page.locator('[data-inquiry-state="error"]').waitFor();
      assert.equal(await input.inputValue(), '  보존할 문의 내용  '); assert.equal(await screen.getByLabel('버그/오류 신고', { exact: true }).isChecked(), true);
      assert.doesNotMatch(await screen.innerText(), /접수되었어요|private text/);
      mode = 'normal'; await submit.click(); await page.locator('[data-inquiry-state="received"]').waitFor();
    }
    step = 'back cancels request'; await visit(); await fill(); hold = true; await submit.click(); await untilHeld();
    await page.goBack(); await page.getByRole('button', { name: '문의 및 신고', exact: true }).waitFor(); releaseAll();
    await page.getByRole('button', { name: '문의 및 신고', exact: true }).click(); assert.equal(await input.inputValue(), '');
    step = 'new session suppresses late 401'; await fill(); mode = '401'; hold = true; await submit.click(); await untilHeld();
    user = 8; mode = 'normal'; await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true })));
    await page.locator('[data-inquiry-state="editing"]').waitFor(); releaseAll(); assert.equal(await input.inputValue(), '');
    await fill(); await submit.click(); await page.locator('[data-inquiry-state="received"]').waitFor();
    step = 'SAVED and HOME'; await screen.getByRole('button', { name: 'SAVE', exact: true }).click(); await page.waitForURL('**view=saved');
    await page.getByRole('button', { name: 'HOME', exact: true }).last().click(); await page.waitForURL('**view=home');
    step = 'direct entry return'; await page.goto(`${base}/pivot/home?view=inquiry`); await input.waitFor();
    await screen.getByRole('button', { name: 'MY', exact: true }).click(); await page.waitForURL('**view=my');
    step = '401 redirects'; await visit(); await fill(); mode = '401'; await submit.click(); await page.waitForURL('**/pivot/login');
    step = 'Demo isolation'; const apiCount = calls.length; await page.goto(`${base}/pivot/demo?view=inquiry`);
    await screen.getByText(/Demo에서는 실제 문의를 접수하지 않아요/).waitFor(); await fill(); assert.equal(await submit.isDisabled(), true);
    await screen.locator('form').evaluate(form => form.requestSubmit()); assert.equal(calls.length, apiCount);
    assert.doesNotMatch(await screen.innerText(), /문의가 접수되었어요/);
    assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
    results.push({ width, passed: true, geometry, checks: ['MY entry/return', 'types/blank/500/501/Unicode', '201/receipt/single flight', '403/CSRF/503/network/malformed retry', 'back abort', 'session change/late401', 'SAVED/HOME', 'direct entry', '401/login', 'Demo isolation'], pageErrors: errors });
  } catch (error) { await page.screenshot({ path: path.join(output, `failure-${width}.png`), fullPage: true }); throw new Error(`${width}px ${step}: ${error.message}`, { cause: error }); }
  finally { releaseAll(); await context.close(); }
}
(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: process.env.PIVOT_BROWSER_CHANNEL || 'msedge' });
  try { for (const width of [375, 320]) await verify(browser, width); }
  finally { await browser.close(); fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(results, null, 2)); }
  console.log(JSON.stringify(results, null, 2));
})().catch(error => { console.error(error); process.exitCode = 1; });
