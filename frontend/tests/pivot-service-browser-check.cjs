const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const { createApiFixture } = require('./helpers/pivot-api-fixture.cjs');

// Start Next separately with NEXT_PUBLIC_API_BASE_URL=http://localhost:8180 and
// NEXT_PUBLIC_PIVOT_ENABLE_LOCAL_API=true, then run this loopback-only check.
// The mock provider and session exist solely in this process, never in the app.
const base = process.argv[2] || 'http://localhost:3180';
const apiOrigin = process.env.PIVOT_FIXTURE_API_ORIGIN || 'http://localhost:8180';
const output = process.argv[3] || path.join(require('node:os').tmpdir(), 'pivot-service-browser-check');
for (const origin of [base, apiOrigin]) {
  const url = new URL(origin);
  assert.equal(url.protocol, 'http:');
  assert.equal(url.hostname, 'localhost', 'fixture only supports explicit localhost origins');
}
const fixture = createApiFixture({ apiOrigin, frontendOrigin: base });
const results = [];

async function waitUntil(predicate, message) {
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    if (await predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  throw new Error(message);
}
async function snapshot(page, filename) {
  await page.evaluate(() => document.fonts.ready);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'no horizontal overflow');
  await page.screenshot({ path: path.join(output, filename), fullPage: true });
}

// Extend the existing authenticated fixture flow with readiness and sticky regressions.
async function checkHomeEntry(browser) {
  const png = fs.readFileSync(path.join(__dirname, '../public/pivot/home/slide-1.png'));
  const cors = { 'access-control-allow-origin': base, 'access-control-allow-credentials': 'true' };
  const cases = [
    { name: 'fast' }, { name: 'three-seconds', apiMs: 3000 }, { name: 'over-three-seconds', apiMs: 4500 },
    { name: 'slow-image', imageMs: 1400 }, { name: 'slow-decode', decodeMs: 1400 },
    { name: 'image-timeout', imageMs: 6500 }, { name: 'image-error', imageError: true },
    { name: 'api-error', apiError: true }, { name: 'api-timeout', apiMs: 16000 },
    { name: 'protected-image', protected: true, imageMs: 1000 },
    { name: 'protected-error', protected: true, imageError: true },
    { name: 'expired-image', protected: true, expired: true },
  ];
  for (const spec of cases) {
    if (process.env.PIVOT_ENTRY_CASES && !process.env.PIVOT_ENTRY_CASES.split(',').includes(spec.name)) continue;
    fixture.rotate('active');
    const context = await browser.newContext({ viewport: { width: 390, height: 844 }, reducedMotion: spec.name === 'three-seconds' ? 'no-preference' : 'reduce' });
    context.setDefaultTimeout(20000);
    await context.addCookies([{ name: 'fixture_session', value: fixture.state.cookie, url: apiOrigin }]);
    await context.addInitScript(({ decodeMs }) => {
      window.entryTransitions = [];
      let last = false;
      new MutationObserver(() => {
        const visible = !!document.querySelector('[data-home-entry-loading]');
        if (visible !== last) { window.entryTransitions.push({ visible, at: performance.now() }); last = visible; }
      }).observe(document, { childList: true, subtree: true });
      const original = HTMLImageElement.prototype.decode;
      HTMLImageElement.prototype.decode = async function () {
        await original.call(this);
        if (decodeMs && this.src.includes('entry-image.png')) await new Promise(resolve => setTimeout(resolve, decodeMs));
      };
    }, spec);
    let feeds = 0, firstStart, firstEnd, imageAttempts = 0, firstCard;
    await context.route(`${apiOrigin}/api/v1/topics/7/feed?*`, async route => {
      const attempt = ++feeds;
      if (attempt === 1) firstStart = Date.now();
      const response = await route.fetch();
      const json = await response.json();
      firstCard = json.sections[0].contents[0];
      firstCard.imageUrl = `${base}/entry-image.png`;
      if (spec.protected) firstCard.slides = [1, 2].map(page => ({
        page, role: page === 1 ? 'cover' : 'body', eyebrow: '검증', titleLines: [`표지 ${page}`], bodySentences: ['검증 본문'],
        imageUrl: `/api/v1/contents/41/slides/${page}/image?topicId=7`, width: 1308, height: 1632,
        layout: { template: 'cover', fitMode: 'contain', backgroundStyle: 'gradient', imageScale: 1, imageOffsetY: 0,
          contrastMode: 'dark', blurBackground: false, imageBox: { x: 0, y: 0, width: 1, height: 1 },
          copyBox: { x: 0, y: .6, width: 1, height: .4 }, textOverlayBox: { x: .05, y: .6, width: .9, height: .3 } },
      }));
      if (attempt === 1 && spec.apiMs) await new Promise(resolve => setTimeout(resolve, spec.apiMs));
      if (attempt === 1) firstEnd = Date.now();
      await route.fulfill(spec.apiError && attempt === 1
        ? { status: 503, headers: cors, json: { code: 'TEMPORARILY_UNAVAILABLE', message: 'fixture' } }
        : { response, json }).catch(() => {});
    });
    await context.route(spec.protected ? `${apiOrigin}/api/v1/contents/41/slides/*/image?*` : `${base}/entry-image.png`, async route => {
      const first = !spec.protected || route.request().url().includes('/slides/1/');
      const attempt = first ? ++imageAttempts : 0;
      if (attempt === 1 && spec.imageMs) await new Promise(resolve => setTimeout(resolve, spec.imageMs));
      await route.fulfill({ status: spec.expired ? 401 : spec.imageError && attempt === 1 ? 404 : 200,
        headers: cors, contentType: 'image/png', body: spec.imageError && attempt === 1 ? Buffer.from('invalid image') : png }).catch(() => {});
    });
    const page = await context.newPage();
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    try {
      await page.goto(`${base}/pivot/home`, { waitUntil: 'domcontentloaded' });
      const loader = page.locator('[data-home-entry-loading]');
      if ((spec.apiMs || spec.imageMs || spec.decodeMs) && !spec.expired) {
        await loader.waitFor();
        await page.waitForTimeout(350);
        assert.equal(await loader.isVisible(), true, 'readiness holds the loader');
        assert.equal(await page.getByRole('button', { name: '알림', exact: true }).count(), 0, 'underlying HOME is inaccessible while loading');
        if (spec.name !== 'three-seconds') assert.equal(await loader.locator('[data-demo-loading-phase]').getAttribute('data-demo-loading-phase'), '1');
        else await page.waitForFunction(() => document.querySelector('[data-home-entry-loading] [data-demo-loading-phase]')?.dataset.demoLoadingPhase === '2');
      }
      if (spec.expired) {
        await page.waitForURL('**/pivot/login');
        assert.equal(await page.locator('[data-home-post]').count(), 0);
        results.push({ name: spec.name, status: 'pass' });
        continue;
      }
      if (spec.apiError || spec.name === 'api-timeout') {
        await page.getByRole('button', { name: '다시 시도', exact: true }).click();
      }
      await page.getByRole('button', { name: '연결 검증 콘텐츠 하나 자세히 보기', exact: true }).waitFor();
      await loader.waitFor({ state: 'detached' });
      const readyAt = Date.now();
      const transitions = await page.evaluate(() => window.entryTransitions);
      if (spec.name === 'fast') assert.ok(readyAt - firstStart < 2400, 'fast API has no artificial three-second wait');
      if (spec.apiMs && spec.apiMs < 15000) assert.ok(readyAt >= firstEnd && firstEnd - firstStart >= spec.apiMs);
      if (spec.name === 'slow-decode' || spec.name === 'slow-image') assert.ok(readyAt - firstEnd >= 1300, 'waits for image load/decode');
      if (spec.imageError || spec.name === 'image-timeout') {
        const retry = page.getByRole('button', { name: '이미지 다시 시도', exact: true });
        await retry.waitFor();
        await snapshot(page, `entry-${spec.name}.png`);
        await retry.click();
        await retry.waitFor({ state: 'detached' });
        await page.waitForFunction(() => { const img = document.querySelector('[data-home-post] [data-home-slide] img'); return img?.complete && img.naturalWidth > 0; });
        assert.equal(await loader.count(), 0, 'image retry keeps HOME usable');
      }
      if (spec.name === 'fast') {
        const home = page.locator('[data-home-state]');
        await home.evaluate(e => e.scrollTo({ top: 140, behavior: 'instant' }));
        const before = await home.evaluate(e => e.scrollTop);
        for (const target of ['detail', 'SAVE', 'MY']) {
          if (target === 'detail') {
            await page.getByRole('button', { name: '연결 검증 콘텐츠 하나 자세히 보기', exact: true }).click();
            await page.locator('[data-detail-state="ready"]:visible').getByRole('button', { name: '뒤로가기', exact: true }).click();
          } else {
            await home.getByRole('button', { name: target, exact: true }).click();
            await page.getByRole('button', { name: 'HOME', exact: true }).click();
          }
          await page.waitForFunction(top => Math.abs(document.querySelector('[data-home-state]').scrollTop - top) < 1, before);
          assert.equal(await loader.count(), 0);
        }
        assert.equal(feeds, 1, 'internal navigation preserves the feed');
        assert.deepEqual(await page.evaluate(() => window.entryTransitions), transitions, 'no duplicate entry loader');
        await context.route(`${apiOrigin}/api/v1/topics/7/search/works?*`, route => route.fulfill({ headers: cors, json: { selectedTopicId: 7, works: [{ entityId: 101, name: '검증 작품' }] } }));
        await context.route(`${apiOrigin}/api/v1/topics/7/search?*`, route => route.fulfill({ headers: cors, json: { selectedTopicId: 7, works: ['검증 작품'], contents: [firstCard] } }));
        const search = page.getByRole('searchbox', { name: '작품 이름' });
        await search.fill('검증');
        await page.getByRole('button', { name: '검증 작품', exact: true }).click();
        await page.getByRole('button', { name: '검색 결과 닫기', exact: true }).click();
        await search.focus(); await page.keyboard.press('Escape');
        assert.equal(await page.getByRole('button', { name: '검색 닫기', exact: true }).count(), 0);
        await search.focus(); await page.getByRole('button', { name: '검색 닫기', exact: true }).click({ position: { x: 10, y: 300 } });
        await page.getByRole('button', { name: '알림', exact: true }).click();
        await page.getByRole('main', { name: '알림함', exact: true }).getByRole('button', { name: '뒤로가기', exact: true }).click();
        await snapshot(page, 'entry-fast-home.png');
      }
      assert.deepEqual(errors, []);
      results.push({ name: spec.name, status: 'pass', apiMs: firstEnd - firstStart, homeReadyMs: readyAt - firstStart, transitions });
      console.log(`PASS HOME entry ${spec.name}: ${readyAt - firstStart}ms`);
    } catch (error) {
      await page.screenshot({ path: path.join(output, `entry-${spec.name}-failure.png`) }).catch(() => {});
      throw error;
    } finally { await context.close(); }
  }
}

async function checkOnboardingEntry(browser) {
  if (process.env.PIVOT_ENTRY_CASES && !process.env.PIVOT_ENTRY_CASES.split(',').includes('onboarding')) return;
  fixture.rotate('onboarding_pending');
  const context = await browser.newContext({ viewport: { width: 375, height: 812 }, reducedMotion: 'reduce' });
  await context.addCookies([{ name: 'fixture_session', value: fixture.state.cookie, url: apiOrigin }]);
  await context.route(`${apiOrigin}/api/v1/onboarding/nickname?*`, route => route.fulfill({
    headers: { 'access-control-allow-origin': base, 'access-control-allow-credentials': 'true' },
    json: { nickname: '브라우저검증회원', available: true, reason: null },
  }));
  const page = await context.newPage(); page.setDefaultTimeout(20000);
  try {
    await page.goto(`${base}/pivot/consent`);
    await page.getByRole('checkbox', { name: '모두 동의합니다', exact: true }).check();
    await page.getByRole('button', { name: '시작하기', exact: true }).click();
    await page.getByRole('textbox', { name: '닉네임', exact: true }).fill('브라우저검증회원');
    await page.getByRole('button', { name: '시작하기', exact: true }).click();
    await page.getByRole('checkbox', { name: '애니메이션', exact: true }).focus();
    await page.keyboard.press('Space');
    await page.getByRole('button', { name: '애니메이션 선택하고 시작하기', exact: true }).click();
    await page.getByRole('searchbox', { name: '작품 검색', exact: true }).fill('테스트 작품 1');
    await page.getByRole('list', { name: '작품 검색 결과', exact: true }).getByRole('button').filter({ hasText: '테스트 작품 1' }).click();
    await page.getByRole('button', { name: '선택한 작품으로 다음', exact: true }).click();
    await page.getByRole('checkbox', { name: '판타지', exact: true }).check();
    await page.getByRole('checkbox', { name: '우정', exact: true }).check();
    await page.getByRole('button', { name: '장르·소재 선택 완료', exact: true }).click();
    await page.getByRole('radio', { name: '설정과 세계관을 파고든다', exact: true }).check();
    await page.getByRole('button', { name: '선택 완료', exact: true }).click();
    await page.evaluate(() => {
      window.duplicateEntryLoading = false;
      new MutationObserver(() => { if (document.querySelector('[data-home-entry-loading]')) window.duplicateEntryLoading = true; })
        .observe(document.body, { childList: true, subtree: true });
    });
    await page.getByRole('button', { name: '콘텐츠 보러가기', exact: true }).click();
    await page.getByRole('heading', { name: '취향을 불러오는 중', exact: true }).waitFor();
    await page.waitForURL('**/pivot/home');
    await page.getByRole('button', { name: '연결 검증 콘텐츠 하나 자세히 보기', exact: true }).waitFor();
    assert.equal(await page.evaluate(() => window.duplicateEntryLoading), false);
    assert.equal(fixture.state.onboardingCompletions, 1);
    results.push('onboarding completion → original warm-up → HOME without duplicate entry loader');
  } catch (error) {
    await page.screenshot({ path: path.join(output, 'onboarding-failure.png') }).catch(() => {});
    throw error;
  } finally { await context.close(); }
}

(async () => {
  fs.mkdirSync(output, { recursive: true });
  const server = await fixture.listen();
  let browser;
  let page;
  const errors = [];
  const diagnostics = [];
  try {
    browser = await chromium.launch({ headless: true, ...(process.env.PIVOT_BROWSER_EXECUTABLE
      ? { executablePath: process.env.PIVOT_BROWSER_EXECUTABLE } : { channel: 'chrome' }) });
    const context = await browser.newContext({ viewport: { width: 390, height: 844 }, reducedMotion: 'reduce' });
    context.setDefaultTimeout(15000);
    context.setDefaultNavigationTimeout(60000);
    let holdProviderPage = true;
    page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') diagnostics.push({ console: message.text() }); });
    page.on('requestfailed', request => diagnostics.push({ failedPath: new URL(request.url()).pathname, failure: request.failure()?.errorText }));
    page.on('response', response => {
      const url = new URL(response.url());
      if (url.origin === base && url.pathname.startsWith('/pivot')) diagnostics.push({ route: url.pathname, status: response.status() });
    });
    for (const provider of ['kakao', 'naver']) {
      const host = provider === 'kakao' ? 'kauth.kakao.com' : 'nid.naver.com';
      await context.route(`https://${host}/**`, async route => {
        const url = new URL(route.request().url());
        assert.equal(url.searchParams.get('client_id'), 'fixture-client');
        assert.equal(url.searchParams.get('state'), 'fixture-state');
        assert.equal(url.searchParams.get('redirect_uri'), `${apiOrigin}/api/v1/auth/login/${provider}/callback`);
        if (holdProviderPage) {
          await route.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html><title>Test provider</title><h1>Mock provider page</h1>' });
          return;
        }
        await route.fulfill({ status: 302, headers: { location: `${apiOrigin}/api/v1/auth/login/${provider}/callback?code=fixture-code&state=fixture-state` } });
      });
    }

    await page.goto(`${base}/pivot/home`);
    await page.waitForURL('**/pivot/login');
    await page.getByRole('button', { name: '카카오 로그인', exact: true }).click();
    await page.waitForURL('https://kauth.kakao.com/**');
    await page.getByRole('heading', { name: 'Mock provider page', exact: true }).waitFor();
    await page.goBack();
    await page.waitForURL('**/pivot/login');
    await waitUntil(async () => await page.getByRole('button', { name: '카카오 로그인', exact: true }).isEnabled(), 'provider Back must unlock login');
    assert.equal(await page.getByRole('button', { name: '네이버 로그인', exact: true }).isEnabled(), true);
    results.push('provider page → browser Back leaves both login buttons usable');
    holdProviderPage = false;
    await page.getByRole('button', { name: '카카오 로그인', exact: true }).click();
    await page.waitForURL('**/pivot/home');
    await page.getByRole('button', { name: '연결 검증 콘텐츠 하나 자세히 보기', exact: true }).waitFor();
    const started = fixture.requests.find(request => request.path === '/api/v1/auth/login/kakao');
    assert.equal(started.method, 'POST'); assert.deepEqual(started.body, { purpose: 'signin' });
    assert.equal(started.csrf, 'fixture-anonymous-csrf'); assert.match(started.cookie, /fixture_session=fixture-anonymous/);
    assert.ok(fixture.requests.some(request => request.path === '/api/v1/topics/7/feed' && /fixture_session=fixture-active/.test(request.cookie)));
    results.push('anonymous route → Kakao button → mocked provider callback → authenticated Home');
    await snapshot(page, '01-authenticated-home.png');

    await page.getByRole('button', { name: '연결 검증 콘텐츠 하나 자세히 보기', exact: true }).click();
    await page.getByRole('article', { name: '연결 검증 콘텐츠 하나', exact: true }).waitFor();
    const saveButton = page.getByRole('button', { name: '저장', exact: true });
    await saveButton.click();
    await waitUntil(async () => await saveButton.getAttribute('aria-pressed') === 'true', 'detail bookmark should become saved');
    assert.equal(fixture.state.saved.has(41), true);
    const save = fixture.requests.find(request => request.method === 'PUT' && request.path.endsWith('/bookmark'));
    assert.deepEqual(save.body, { topicId: 7 }); assert.equal(save.csrf, 'fixture-active-csrf');
    await page.getByRole('button', { name: 'SAVE', exact: true }).click();
    await page.getByRole('heading', { name: '저장한 콘텐츠', exact: true }).waitFor();
    await page.getByRole('button', { name: '연결 검증 콘텐츠 하나 자세히 보기', exact: true }).waitFor();
    await page.reload();
    await page.getByRole('button', { name: '연결 검증 콘텐츠 하나 자세히 보기', exact: true }).waitFor();
    results.push('Home → Detail → bookmark PUT with rotated CSRF → Saved → reload preserves server state');
    await snapshot(page, '02-saved-after-refresh.png');

    await page.getByRole('button', { name: '연결 검증 콘텐츠 하나 자세히 보기', exact: true }).click();
    await page.getByRole('article', { name: '연결 검증 콘텐츠 하나', exact: true }).waitFor();
    await waitUntil(async () => await saveButton.getAttribute('aria-pressed') === 'true', 'Saved detail should show persisted bookmark');
    await saveButton.click();
    await waitUntil(async () => await saveButton.getAttribute('aria-pressed') === 'false', 'bookmark deletion should update detail');
    await page.getByRole('button', { name: 'SAVE', exact: true }).click();
    await page.getByText('저장한 콘텐츠가 아직 없어요', { exact: true }).waitFor();
    await page.reload();
    await page.getByText('저장한 콘텐츠가 아직 없어요', { exact: true }).waitFor();
    assert.equal(fixture.state.saved.size, 0);
    assert.ok(fixture.requests.some(request => request.method === 'DELETE' && request.query.topicId === '7'));
    results.push('Saved → Detail → bookmark DELETE → empty Saved survives reload');

    await page.getByRole('button', { name: 'HOME', exact: true }).click();
    await page.getByRole('button', { name: '연결 검증 콘텐츠 하나 자세히 보기', exact: true }).click();
    await page.getByRole('article', { name: '연결 검증 콘텐츠 하나', exact: true }).waitFor();
    fixture.state.failNext = { method: 'PUT', path: '/api/v1/contents/41/bookmark', status: 503 };
    await saveButton.click();
    await page.getByText(/잠시 후 다시 시도|저장.*못했|저장.*실패/).first().waitFor();
    assert.equal(await saveButton.getAttribute('aria-pressed'), 'false');
    assert.equal(fixture.state.saved.size, 0);
    results.push('failed save reports error without showing a false saved state');
    await snapshot(page, '03-save-error.png');

    fixture.rotate('anonymous');
    await page.reload();
    await page.waitForURL('**/pivot/login');
    await page.getByRole('button', { name: '네이버 로그인', exact: true }).click();
    await page.waitForURL('**/pivot/home');
    await page.getByRole('button', { name: '연결 검증 콘텐츠 하나 자세히 보기', exact: true }).waitFor();
    results.push('expired session returns to login; Naver starts a fresh authenticated session');
    await checkHomeEntry(browser);
    await checkOnboardingEntry(browser);
    assert.deepEqual(errors, [], 'no uncaught browser errors');
    fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify({ results, requestCount: fixture.requests.length }, null, 2));
    console.log(JSON.stringify({ passed: results.length, results, output }, null, 2));
  } catch (error) {
    if (page && !page.isClosed()) {
      await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true }).catch(() => {});
      fs.writeFileSync(path.join(output, 'failure.txt'), `${error.stack}\n\nURL: ${page.url()}\n\n${await page.locator('body').innerText().catch(() => '')}\n\n${JSON.stringify({ errors, diagnostics }, null, 2)}`);
    }
    throw error;
  } finally {
    await browser?.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
