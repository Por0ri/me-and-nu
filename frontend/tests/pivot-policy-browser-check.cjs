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
  // Terms/privacy now open bundled full texts; marketing still shows the server text.
  const policies = () => [
    { type: 'terms', policyVersion: 'release-1', required: true, text: '서버 약관 본문입니다.' },
    { type: 'privacy', policyVersion: 'release-2', required: true, text: 'https://example.org/privacy?v=2' },
    { type: 'marketing', policyVersion: 'release-3', required: false, text: body },
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
  const closeButton = dialog.getByRole('button', { name: '닫기', exact: true });
  const focused = async target => assert.equal(await target.evaluate(el => el === document.activeElement), true,
    await page.evaluate(() => `Focused element: ${document.activeElement?.outerHTML.slice(0,350)}`));
  const close = async button => { await closeButton.click(); await dialog.waitFor({ state: 'hidden' }); await focused(button); };
  const policyCalls = () => calls.filter(call => call.path === '/api/v1/policies').length;
  const checked = () => page.getByRole('checkbox').evaluateAll(inputs => inputs.map(input => input.checked));
  try {
    await page.goto(`${base}/pivot/home?view=my`); await terms.waitFor();
    step = 'MY terms modal: bundled texts, focus, Tab trap and inner scroll'; const beforeTerms = policyCalls();
    await terms.focus(); await page.keyboard.press('Enter'); await page.locator('dialog[data-policy-modal="terms"]').waitFor();
    await focused(closeButton);
    assert.equal(await dialog.getByRole('heading', { name: '이용약관 및 운영정책', exact: true, level: 2 }).count(), 1);
    assert.match(await dialog.innerText(), /시행일: 2026\. 11\.02[\s\S]*본 약관은 2026년 11월 2일부터 시행되며, 서비스 이용에 적용됩니다\./);
    const termsText = dialog.getByRole('region', { name: '이용약관 전문', exact: true });
    const operationsText = dialog.getByRole('region', { name: '운영정책 전문', exact: true });
    assert.match(await termsText.innerText(), /^이용약관\s+버전 v1\.2 · 시행일 2026년 11월 2일/);
    assert.match(await operationsText.innerText(), /^운영정책\s+버전 v0\.6 · 시행일 2026년 11월 2일/);
    await page.keyboard.press('Tab'); await focused(termsText);
    await page.keyboard.press('PageDown'); assert.ok(await termsText.evaluate(el => el.scrollTop) > 0, 'Terms text scrolls inside its box');
    await page.keyboard.press('Tab'); await focused(operationsText);
    await page.keyboard.press('Tab'); await focused(closeButton);
    await page.keyboard.press('Shift+Tab'); await focused(operationsText);
    await terms.evaluate(el => el.focus()); assert.equal(await dialog.evaluate(el => el.contains(document.activeElement)), true);
    assert.equal(policyCalls(), beforeTerms, 'My Page never loads server policy text');
    const box = await dialog.boundingBox(); assert.ok(box.x >= 0 && box.x + box.width <= width + 1 && box.y >= 0 && box.y + box.height <= 813);
    assert.ok(Math.abs(box.x - (width - box.width) / 2) < 1, 'Dialog is centered');
    assert.equal(await dialog.evaluate(el => [...el.querySelectorAll('section, div')].every(node => node.scrollWidth <= node.clientWidth + 1)), true, 'No horizontal overflow');
    await page.screenshot({ path: path.join(output, `terms-${width}.png`), fullPage: true });
    await close(terms); assert.equal(new URL(page.url()).searchParams.get('view'), 'my');
    step = 'privacy modal, Escape and browser Back';
    await privacy.click(); await page.locator('dialog[data-policy-modal="privacy"]').waitFor();
    assert.equal(await dialog.getByRole('heading', { name: '개인정보 처리방침', exact: true, level: 2 }).count(), 1);
    const privacyText = dialog.getByRole('region', { name: '개인정보 처리방침 전문', exact: true });
    assert.match(await privacyText.innerText(), /^개인정보처리방침\s+버전 v0\.3 · 시행일 2026년 11월 2일[\s\S]*meandnu2026@kakao\.com/);
    assert.doesNotMatch(await dialog.innerText(), /연락용 이메일 주소|운영정책 전문/);
    await page.screenshot({ path: path.join(output, `privacy-${width}.png`), fullPage: true });
    await page.keyboard.press('Escape'); await dialog.waitFor({ state: 'hidden' }); await focused(privacy);
    await privacy.click(); await page.goBack(); await dialog.waitFor({ state: 'hidden' }); await focused(privacy);
    assert.equal(new URL(page.url()).searchParams.get('view'), 'my');
    step = 'Demo preserves local state and scroll, with no API traffic';
    const beforeDemo = calls.length; await page.goto(`${base}/pivot/demo?view=my`); await terms.waitFor();
    const notification = page.getByRole('switch', { name: '알림 허용', exact: true }); await notification.click();
    await terms.scrollIntoViewIfNeeded();
    const scroll = await page.locator('main[data-my-mode]').evaluate(el => ({ top: el.scrollTop, window: window.scrollY }));
    await terms.click(); await page.locator('dialog[data-policy-modal="terms"]').waitFor(); await close(terms);
    assert.equal(await notification.getAttribute('aria-checked'), 'false');
    assert.deepEqual(await page.locator('main[data-my-mode]').evaluate(el => ({ top: el.scrollTop, window: window.scrollY })), scroll);
    await privacy.click(); await page.locator('dialog[data-policy-modal="privacy"]').waitFor();
    await page.goBack(); await dialog.waitFor({ state: 'hidden' }); await focused(privacy);
    assert.equal(await notification.getAttribute('aria-checked'), 'false'); assert.equal(calls.length, beforeDemo);
    step = 'onboarding terms/privacy open the bundled modals without changing the selection'; phase = 'onboarding_pending';
    await page.goto(`${base}/pivot/consent`);
    await page.getByRole('checkbox', { name: '모두 동의합니다', exact: true }).check();
    const selection = await checked();
    const termsDetails = page.getByRole('button', { name: '서비스 설명 및 이용약관 내용 보기', exact: true });
    await termsDetails.click(); await page.locator('dialog[data-policy-modal="terms"]').waitFor();
    assert.doesNotMatch(await dialog.innerText(), /서버 약관 본문|release-1/);
    await page.screenshot({ path: path.join(output, `onboarding-terms-${width}.png`), fullPage: true });
    await close(termsDetails); assert.deepEqual(await checked(), selection);
    const privacyDetails = page.getByRole('button', { name: '개인정보 수집 및 이용동의 내용 보기', exact: true });
    await privacyDetails.click(); await page.locator('dialog[data-policy-modal="privacy"]').waitFor();
    assert.equal(await dialog.getByRole('link').count(), 0, 'The server privacy URL is not shown');
    await page.keyboard.press('Escape'); await dialog.waitFor({ state: 'hidden' }); await focused(privacyDetails);
    step = 'other consent items keep the server text dialog';
    const marketingDetails = page.getByRole('button', { name: '마케팅 수신 동의 내용 보기', exact: true });
    await marketingDetails.click(); await dialog.getByRole('region', { name: '마케팅 수신 동의 본문' }).waitFor();
    assert.equal(await page.locator('dialog[data-policy-modal]').count(), 0);
    assert.equal(await dialog.getByRole('region').textContent(), body);
    await dialog.getByRole('region').focus(); await page.keyboard.press('PageDown');
    assert.ok(await dialog.locator('[class*="policyBody"]').evaluate(el => el.scrollTop) > 0);
    await close(marketingDetails); assert.deepEqual(await checked(), selection);
    assert.equal(await page.getByRole('button', { name: '시작하기', exact: true }).isEnabled(), true);
    assert.ok(calls.every(call => call.method === 'GET'), 'Policy reading never changes consent or account data');
    assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
    results.push({ width, passed: true, checks: ['MY terms/privacy bundled modals', 'no policy API from MY', 'focus/Tab trap/Escape/X/Back', 'inner scroll', 'Demo state/scroll/no API', 'onboarding terms/privacy modals', 'marketing server dialog', 'selection unchanged', 'no mutations'], pageErrors: errors });
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
