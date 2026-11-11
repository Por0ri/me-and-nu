// Loopback-only API interception. No real account reads/writes or publication overrides.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const base = process.argv[2] || 'http://127.0.0.1:3196';
const output = process.argv[3] || path.join(require('node:os').tmpdir(), 'pivot-policy');
const local = url => ['localhost', '127.0.0.1'].includes(url.hostname) && url.protocol === 'http:';
assert.ok(local(new URL(base)));
const results = [];
async function verify(browser, width) {
  const context = await browser.newContext({ viewport: { width, height: 812 }, isMobile: true, hasTouch: true, reducedMotion: 'reduce', serviceWorkers: 'block' });
  const page = await context.newPage(); page.setDefaultTimeout(15000);
  let mode = 'seed', phase = 'active', hold = false, release = [], calls = [], errors = [], unexpected = [], step = 'MY';
  const body = '본문 렌더링 확인 문자열입니다.\n'.repeat(90);
  const policies = () => [
    { type: 'terms', policyVersion: mode === 'seed' ? 'checking-20261028-v1' : 'release-1', required: true,
      text: mode === 'seed' ? '정식 서비스 약관이 아닌 테스트 안내입니다.' : body },
    { type: 'privacy', policyVersion: 'release-2', required: true, text: 'https://example.org/privacy?v=2' },
  ];
  const releaseAll = () => { hold = false; release.splice(0).forEach(resolve => resolve()); };
  page.on('pageerror', error => errors.push(error.message));
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    if (!local(url)) { unexpected.push(url.origin); return route.abort(); }
    if (url.pathname === '/favicon.ico') return route.fulfill({ status: 204 });
    if (!url.pathname.startsWith('/api/')) return route.continue();
    calls.push({ method: request.method(), path: url.pathname });
    const reply = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) }).catch(() => {});
    if (url.pathname === '/api/v1/auth/session') return reply({ authenticated: true, sessionState: phase, onboardingCompleted: phase === 'active', user: phase === 'active' ? { id: 7, accountType: 'user' } : null, csrfToken: 'fixture-csrf' });
    if (url.pathname === '/api/v1/users/me') return reply({ userId: 7, nickname: '회원', email: null, accountType: 'user' });
    if (url.pathname === '/api/v1/me/topics') return reply({ topics: [{ id: 83, code: 'anime', name: '애니메이션', status: 'active', deletedAt: null, subtopicIds: [] }] });
    if (url.pathname === '/api/v1/topics/83/feed') return reply({ selectedTopicId: 83, sections: [], nextCursor: null, emptyReason: null });
    if (url.pathname === '/api/v1/policies') {
      assert.equal(request.method(), 'GET');
      const sentMode = mode;
      const items = policies();
      if (hold) await new Promise(resolve => release.push(resolve));
      if (sentMode === 'network') return route.abort('failed').catch(() => {});
      if (sentMode === 'error' || sentMode === 'late401') return reply({ message: 'private backend diagnostic' }, sentMode === 'late401' ? 401 : 503);
      if (sentMode === 'null') return reply(null);
      const consentItems = sentMode === 'empty' ? [] : sentMode === 'missing' ? [items[1]] : sentMode === 'duplicate' ? [items[0], items[0]]
        : sentMode === 'blank' ? [{ ...items[0], text: '' }] : items;
      return reply({ consentItems });
    }
    unexpected.push(`${request.method()} ${url.pathname}`); return route.abort();
  });
  const dialog = page.getByRole('dialog');
  const terms = page.getByRole('button', { name: '이용약관', exact: true });
  const privacy = page.getByRole('button', { name: '개인정보처리방침', exact: true });
  const focused = async button => assert.equal(await button.evaluate(el => el === document.activeElement), true,
    await page.evaluate(() => `Focused element: ${document.activeElement?.outerHTML.slice(0,350)}`));
  const close = async button => { await dialog.getByRole('button', { name: '닫기', exact: true }).click(); await dialog.waitFor({ state: 'hidden' }); await focused(button); };
  const untilHeld = async () => { for (let n = 0; n < 200 && !release.length; n++) await new Promise(resolve => setTimeout(resolve, 25)); assert.ok(release.length); };
  try {
    await page.goto(`${base}/pivot/home?view=my`); await terms.waitFor();
    step = 'loading and unpublished'; hold = true; await terms.focus(); await page.keyboard.press('Enter'); await dialog.waitFor();
    await dialog.getByText('정책 문서를 불러오고 있어요.').waitFor(); await untilHeld();
    await focused(dialog.getByRole('button', { name: '닫기', exact: true }));
    releaseAll(); await page.locator('[data-policy-state="unavailable"]').waitFor();
    assert.doesNotMatch(await dialog.innerText(), /checking-20261028-v1|정식 서비스 약관이 아닌 테스트 안내/);
    assert.equal(await dialog.getByRole('heading', { name: '이용약관', exact: true }).count(), 1);
    await terms.evaluate(el => el.focus()); assert.equal(await dialog.evaluate(el => el.contains(document.activeElement)), true);
    const box = await dialog.boundingBox(); assert.ok(box.x >= 0 && box.x + box.width <= width + 1);
    assert.ok(Math.abs(box.x - (width - box.width) / 2) < 1, 'Dialog is centered');
    await page.screenshot({ path: path.join(output, `unavailable-${width}.png`), fullPage: true });
    await close(terms); assert.equal(new URL(page.url()).searchParams.get('view'), 'my');
    step = 'privacy and browser Back'; await privacy.click(); await page.locator('[data-policy-state="unavailable"]').waitFor();
    assert.equal(await dialog.getByRole('heading', { name: '개인정보처리방침', exact: true }).count(), 1);
    await page.goBack(); await dialog.waitFor({ state: 'hidden' }); await focused(privacy);
    assert.equal(new URL(page.url()).searchParams.get('view'), 'my');
    step = 'error, missing, malformed and retry';
    for (const failure of ['error', 'network', 'null', 'empty', 'duplicate', 'blank', 'missing']) {
      mode = failure; await terms.click();
      await page.locator(`[data-policy-state="${failure === 'missing' ? 'unavailable' : 'error'}"]`).waitFor();
      assert.doesNotMatch(await dialog.innerText(), /private backend diagnostic/);
      mode = 'text'; await dialog.getByRole('button', { name: '다시 시도', exact: true }).click();
      await page.locator('[data-policy-state="unavailable"]').waitFor();
      assert.doesNotMatch(await dialog.innerText(), /본문 렌더링 확인 문자열/);
      await page.keyboard.press('Escape'); await dialog.waitFor({ state: 'hidden' }); await focused(terms);
    }
    step = 'close during request and late401'; mode = 'late401'; hold = true; await terms.click(); await untilHeld();
    await close(terms); releaseAll(); mode = 'seed'; await privacy.click(); await page.locator('[data-policy-state="unavailable"]').waitFor();
    assert.equal(new URL(page.url()).pathname, '/pivot/home'); await close(privacy);
    step = 'Demo preserves local state and scroll, with no API traffic';
    const beforeDemo = calls.length; await page.goto(`${base}/pivot/demo?view=my`); await terms.waitFor();
    const notification = page.getByRole('switch', { name: '알림 허용', exact: true }); await notification.click();
    await terms.scrollIntoViewIfNeeded();
    const scroll = await page.locator('main[data-my-mode]').evaluate(el => ({ top: el.scrollTop, window: window.scrollY }));
    await terms.click(); await dialog.getByText(/Demo에서는 정책 원문/).waitFor(); await close(terms);
    assert.equal(await notification.getAttribute('aria-checked'), 'false');
    assert.deepEqual(await page.locator('main[data-my-mode]').evaluate(el => ({ top: el.scrollTop, window: window.scrollY })), scroll);
    await privacy.click(); await page.goBack(); await dialog.waitFor({ state: 'hidden' }); await focused(privacy);
    assert.equal(await notification.getAttribute('aria-checked'), 'false'); assert.equal(calls.length, beforeDemo);
    step = 'onboarding body and unchanged consent selection'; phase = 'onboarding_pending'; mode = 'text';
    await page.goto(`${base}/pivot/consent`);
    await page.getByRole('checkbox', { name: '모두 동의합니다', exact: true }).check();
    const selection = await page.getByRole('checkbox').evaluateAll(inputs => inputs.map(input => input.checked));
    const termsDetails = page.getByRole('button', { name: '서비스 설명 및 이용약관 내용 보기', exact: true });
    await termsDetails.click(); await dialog.getByRole('region', { name: '서비스 설명 및 이용약관 본문' }).waitFor();
    assert.equal(await dialog.getByRole('region').textContent(), body);
    await dialog.getByRole('region').focus(); await page.keyboard.press('PageDown');
    assert.ok(await dialog.locator('[class*="policyBody"]').evaluate(el => el.scrollTop) > 0);
    await page.screenshot({ path: path.join(output, `onboarding-body-${width}.png`), fullPage: true });
    await close(termsDetails); assert.deepEqual(await page.getByRole('checkbox').evaluateAll(inputs => inputs.map(input => input.checked)), selection);
    step = 'onboarding original URL, Escape, and keyboard focus';
    const privacyDetails = page.getByRole('button', { name: '개인정보 수집 및 이용동의 내용 보기', exact: true });
    await privacyDetails.click();
    const link = dialog.getByRole('link', { name: '개인정보 수집 및 이용동의 원문 열기 (새 탭)', exact: true });
    assert.equal(await link.getAttribute('href'), 'https://example.org/privacy?v=2');
    assert.equal(await link.getAttribute('rel'), 'noopener noreferrer'); assert.equal(await link.getAttribute('target'), '_blank');
    await focused(dialog.getByRole('button', { name: '닫기', exact: true }));
    await page.keyboard.press('Shift+Tab'); await focused(link);
    await page.keyboard.press('Tab'); await focused(dialog.getByRole('button', { name: '닫기', exact: true }));
    await page.keyboard.press('Escape'); await dialog.waitFor({ state: 'hidden' }); await focused(privacyDetails);
    assert.deepEqual(await page.getByRole('checkbox').evaluateAll(inputs => inputs.map(input => input.checked)), selection);
    assert.equal(await page.getByRole('button', { name: '시작하기', exact: true }).isEnabled(), true);
    assert.ok(calls.every(call => call.method === 'GET'), 'Policy reading never changes consent or account data');
    assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
    results.push({ width, passed: true, checks: ['MY terms/privacy', 'loading/GET', 'seed and unapproved blocked', 'error/missing/malformed/retry', 'close/Escape/Back/focus', 'late401 abort', 'Demo state/scroll/no API', 'onboarding body/URL/selection', 'no mutations'], pageErrors: errors });
  } catch (error) {
    await page.screenshot({ path: path.join(output, `failure-${width}.png`), fullPage: true });
    throw new Error(`${width}px ${step}: ${error.message}`, { cause: error });
  } finally { releaseAll(); await context.close(); }
}
(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: process.env.PIVOT_BROWSER_CHANNEL || 'msedge' });
  try { for (const width of [375, 320]) await verify(browser, width); }
  finally { await browser.close(); fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(results, null, 2)); }
  console.log(JSON.stringify(results, null, 2));
})().catch(error => { console.error(error); process.exitCode = 1; });
