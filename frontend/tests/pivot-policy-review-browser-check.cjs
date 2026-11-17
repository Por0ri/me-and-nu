// Runs against `npm run dev:policy-review`; only the real local review GET is allowed.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const ts = require('typescript');
const root = path.join(__dirname, '..');
// Real policy bytes stay in the ignored local file, never in this public test.
const sourcePath = path.join(root, 'dev/policy-review/team-policy-2026-10-23.md');
if (!fs.existsSync(sourcePath)) { console.log('SKIP: local policy review source is absent'); process.exit(0); }
const parser = { exports: {}, URL };
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(root, 'lib/pivot/policy-review.ts'), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, parser);
const reviewDocs = parser.exports.parseTeamPolicyReview(fs.readFileSync(sourcePath, 'utf8'));
const blockText = blocks => blocks.flatMap(block => block.kind === 'list' ? block.items.flatMap(item => [item.text, ...blockText(item.children)])
  : block.kind === 'table' ? [...block.header, ...block.rows.flat()] : [block.text]);
const privateSentences = reviewDocs.flatMap(doc => blockText(doc.blocks)).filter(text => text.length >= 30);
const assertNoSource = text => assert.equal(privateSentences.some(sentence => text.includes(sentence)), false, 'No private policy text');
const assertComplete = async (article, index) => {
  const actual = (await article.innerText()).replace(/\s/g, '');
  for (const text of blockText(reviewDocs[index].blocks)) assert.ok(actual.includes(text.replace(/\s/g, '')), 'All local source blocks are rendered');
};
const { chromium } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const base = process.argv[2] || 'http://127.0.0.1:3196';
const out = process.argv[3] || path.join(require('node:os').tmpdir(), 'pivot-policy-review');
const local = url => url.origin === new URL(base).origin;
assert.ok(['127.0.0.1', 'localhost'].includes(new URL(base).hostname));
const results = [];
async function verify(browser, width) {
  const context = await browser.newContext({ viewport: { width, height: 812 }, isMobile: true, hasTouch: true, reducedMotion: 'reduce', serviceWorkers: 'block' });
  await context.addCookies([{ name: 'review-test-cookie', value: 'must-not-be-sent', url: base }]);
  const page = await context.newPage(); page.setDefaultTimeout(15000);
  const calls = [], errors = [], unexpected = [], chunks = [];
  let step = 'MY', failReview = false;
  page.on('pageerror', error => errors.push(error.message));
  page.on('response', response => {
    if (new URL(response.url()).pathname.endsWith('.js')) chunks.push(response.text().catch(() => ''));
  });
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    if (!local(url)) { unexpected.push(url.origin); return route.abort(); }
    if (url.pathname === '/favicon.ico') return route.fulfill({ status: 204 });
    if (!url.pathname.startsWith('/api/')) return route.continue();
    calls.push({ path: url.pathname, method: request.method(), cookie: request.headers().cookie });
    if (url.pathname !== '/api/dev/policy-review' || request.method() !== 'GET') { unexpected.push(request.method() + ' ' + url.pathname); return route.abort(); }
    assert.equal(request.headers().cookie, undefined, 'Review fetch omits account cookies');
    if (failReview) return route.fulfill({ status: 503, contentType: 'application/json', body: '{}' });
    return route.continue();
  });
  const dialog = page.getByRole('dialog');
  const terms = page.getByRole('button', { name: '이용약관', exact: true });
  const privacy = page.getByRole('button', { name: '개인정보처리방침', exact: true });
  const ready = () => page.locator('[data-policy-review-state="ready"]').waitFor();
  const focus = async locator => assert.equal(await locator.evaluate(el => el === document.activeElement), true);
  const close = async trigger => { await dialog.getByRole('button', { name: '닫기', exact: true }).click(); await dialog.waitFor({ state: 'hidden' }); await focus(trigger); };
  try {
    await page.goto(base + '/pivot/demo?view=my&policyReview=1'); await terms.waitFor();
    assertNoSource(await page.content());
    const notification = page.getByRole('switch', { name: '알림 허용', exact: true }); await notification.click();
    await terms.scrollIntoViewIfNeeded();
    const scroll = await page.locator('main[data-my-mode]').evaluate(el => ({ top: el.scrollTop, window: window.scrollY }));
    step = 'terms and operations complete'; await terms.click(); await ready();
    await focus(dialog.getByRole('button', { name: '닫기', exact: true }));
    await dialog.getByText('개발용 검토 문서 · 정식 게시 미승인', { exact: true }).waitFor();
    assert.match(await dialog.innerText(), /가입 시 동의하는 정책과 별개/);
    const termsDoc = dialog.getByRole('article', { name: '이용약관 검토 원문', exact: true });
    const operationsDoc = dialog.getByRole('article', { name: '운영정책 검토 원문', exact: true });
    assert.equal(await termsDoc.getByRole('heading').filter({ hasText: /^제\d+조/ }).count(), 31);
    assert.equal(await operationsDoc.getByRole('heading').filter({ hasText: /^제\d+조/ }).count(), 15);
    assert.match(await termsDoc.innerText(), /버전 v1.2 · 시행일 2026-11-02/);
    assert.match(await operationsDoc.innerText(), /버전 v0.6 · 시행일 2026-11-02/);
    assert.equal(await operationsDoc.getByRole('table').count(), 3);
    await assertComplete(termsDoc, 0); await assertComplete(operationsDoc, 1);
    const box = await dialog.boundingBox(); assert.ok(box.x >= 0 && box.x + box.width <= width + 1);
    assert.ok(Math.abs(box.x - (width - box.width) / 2) < 1);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({ path: path.join(out, `terms-${width}.png`), fullPage: true });
    step = 'operations jump, tables and keyboard scroll';
    await dialog.getByRole('navigation', { name: '검토 문서 바로가기' }).getByRole('button', { name: '운영정책', exact: true }).click();
    await focus(operationsDoc.getByRole('heading', { name: '운영정책', exact: true }));
    const body = dialog.locator('[class*="policyBody"]'); assert.ok(await body.evaluate(el => el.scrollTop) > 0);
    const tableRegion = operationsDoc.getByRole('region').first();
    await tableRegion.focus(); await page.keyboard.press('ArrowRight');
    const noticeBox = await dialog.getByText('개발용 검토 문서 · 정식 게시 미승인', { exact: true }).boundingBox();
    assert.ok(noticeBox.y >= box.y && noticeBox.y + noticeBox.height <= box.y + box.height, 'Review notice remains in view while a tall table has keyboard focus');
    if (width === 320) assert.ok(await tableRegion.evaluate(el => el.scrollWidth > el.clientWidth));
    await page.screenshot({ path: path.join(out, `operations-${width}.png`), fullPage: true });
    await page.keyboard.press('Escape'); await dialog.waitFor({ state: 'hidden' }); await focus(terms);
    assert.equal(await notification.getAttribute('aria-checked'), 'false');
    assert.deepEqual(await page.locator('main[data-my-mode]').evaluate(el => ({ top: el.scrollTop, window: window.scrollY })), scroll);
    step = 'privacy all clauses, tables and placeholder'; await privacy.click(); await ready();
    const privacyDoc = dialog.getByRole('article', { name: '개인정보처리방침 검토 원문', exact: true });
    assert.equal(await privacyDoc.getByRole('heading').filter({ hasText: /^제\d+조/ }).count(), 17);
    assert.equal(await privacyDoc.getByRole('table').count(), 6);
    assert.match(await privacyDoc.innerText(), /버전 v0.3 · 시행일 2026-11-02/);
    await assertComplete(privacyDoc, 2);
    await privacyDoc.getByText('[연락용 이메일 주소]', { exact: true }).scrollIntoViewIfNeeded();
    assert.equal(await privacyDoc.getByText('[연락용 이메일 주소]', { exact: true }).isVisible(), true);
    assert.equal(await dialog.getByText('개발용 검토 문서 · 정식 게시 미승인', { exact: true }).isVisible(), true);
    await page.screenshot({ path: path.join(out, `privacy-${width}.png`), fullPage: true });
    await page.goBack(); await dialog.waitFor({ state: 'hidden' }); await focus(privacy);
    assert.equal(new URL(page.url()).searchParams.get('view'), 'my');
    assert.equal(await notification.getAttribute('aria-checked'), 'false');
    step = 'failed review retries without service fallback'; failReview = true; await terms.click();
    await page.locator('[data-policy-review-state="error"]').waitFor();
    assert.doesNotMatch(await dialog.innerText(), /v1.2|제1조/);
    failReview = false; await dialog.getByRole('button', { name: '다시 시도', exact: true }).click(); await ready(); await close(terms);
    step = 'default Demo remains blocked'; const beforeDefault = calls.length;
    await page.goto(base + '/pivot/demo?view=my'); await terms.click();
    await dialog.getByText(/Demo에서는 정책 원문을 제공하지 않아요/).waitFor();
    assert.doesNotMatch(await dialog.innerText(), /v1.2|제1조/); await close(terms); assert.equal(calls.length, beforeDefault);
    step = 'onboarding remains isolated and selection survives shared dialog';
    await page.goto(base + '/pivot/preview?screen=consent&policyReview=1');
    await page.getByRole('checkbox', { name: '모두 동의합니다', exact: true }).check();
    const choices = await page.getByRole('checkbox').evaluateAll(inputs => inputs.map(input => input.checked));
    const details = page.getByRole('button', { name: '개인정보 수집 및 이용동의 내용 보기', exact: true });
    await details.click(); await dialog.waitFor();
    assert.match(await dialog.innerText(), /figma-preview-only|테스트 환경 안내/);
    assert.doesNotMatch(await dialog.innerText(), /v0.3|연락용 이메일 주소|개발용 검토 문서/);
    await close(details); assert.deepEqual(await page.getByRole('checkbox').evaluateAll(inputs => inputs.map(input => input.checked)), choices);
    assert.equal(await page.getByRole('button', { name: '시작하기', exact: true }).isEnabled(), true);
    assert.equal(calls.length, beforeDefault);
    step = 'no static source or cross-origin endpoint exposure';
    for (const endpoint of ['/dev/policy-review/team-policy-2026-10-23.md', '/dev/policy-review/provenance.json']) {
      const result = await context.request.get(base + endpoint); assert.equal(result.status(), 404);
      assertNoSource(await result.text());
    }
    const cross = await context.request.get(base + '/api/dev/policy-review', { headers: { origin: 'https://checking.me-nu.kr' } });
    assert.equal(cross.status(), 404); assert.doesNotMatch(await cross.text(), /v1.2|이용약관/);
    for (const chunk of await Promise.all(chunks)) assertNoSource(chunk);
    assert.deepEqual(unexpected, []); assert.deepEqual(errors, []);
    assert.ok(calls.every(call => call.method === 'GET' && call.path === '/api/dev/policy-review' && !call.cookie));
    results.push({ width, passed: true, checks: ['3 complete policy documents', 'versions/dates', '31/15/17 clauses', '9 semantic tables and placeholder', 'scroll/keyboard/focus/Escape/Back/state', 'error/retry', 'default Demo blocked', 'onboarding selection unchanged', 'no account API/writes/cookies', 'static path/cross-origin blocked', 'source absent from JS'], pageErrors: errors });
  } catch (error) {
    await page.screenshot({ path: path.join(out, `failure-${width}.png`), fullPage: true });
    throw new Error(`${width}px ${step}: ${error.message}`, { cause: error });
  } finally { await context.close(); }
}
(async () => {
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: process.env.PIVOT_BROWSER_CHANNEL || 'msedge' });
  try { for (const width of [375,320]) await verify(browser, width); }
  finally { await browser.close(); fs.writeFileSync(path.join(out, 'browser-results.json'), JSON.stringify(results, null, 2)); }
  console.log(JSON.stringify(results, null, 2));
})().catch(error => { console.error(error); process.exitCode = 1; });
