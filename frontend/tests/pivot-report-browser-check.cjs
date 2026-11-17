// Local browser fixtures only: all API calls are intercepted, never sent to a backend.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const base = process.argv[2] || 'http://127.0.0.1:3196';
const output = path.resolve(process.argv[3] || '../agent/out/detail-report-1109/report');
const local = url => url.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(url.hostname);
assert.ok(local(new URL(base)));
const results = [];
const box = { x: .06, y: .57, width: .88, height: .35 };
const slides = [1, 2].map(page => ({ page, role: page === 1 ? 'cover' : 'body', eyebrow: '리뷰 큐레이션', titleLines: ['밝은 장면 속 작은 이야기'], bodySentences: ['이미지와 함께 읽는 콘텐츠입니다.'],
  width: 1308, height: 1632, imageUrl: `/api/v1/contents/17/slides/${page}/image?topicId=83`,
  layout: { template: 'split', fitMode: 'contain_blur', backgroundStyle: 'blur', imageBox: box, copyBox: box, textOverlayBox: box, imageScale: 1, imageOffsetY: 0, contrastMode: 'scrim_required', blurBackground: false } }));
const content = { kind: 'content', id: 17, contentId: 17, topicId: 83, title: '2장 콘텐츠 검증', summary: '카드 요약', saved: false, imageUrl: null, slides,
  displayMode: 'full_body', body: '본문과 신고 기능을 검증합니다.\n\n기존 원문은 변경하지 않습니다.', myReaction: { bookmarked: false }, sources: [], notices: [] };
const photo = fs.readFileSync(path.join(__dirname, '../public/local-content/licorice-pizza-poster.png'));
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function verify(browser, width) {
  const context = await browser.newContext({ viewport: { width, height: 812 }, isMobile: true, hasTouch: true, reducedMotion: 'reduce', serviceWorkers: 'block' });
  const page = await context.newPage(); page.setDefaultTimeout(15000);
  let mode = 'normal', hold = false, release = [], posts = [], unexpected = [], errors = [], step = 'load', imageKind = 'photo';
  const solids = await page.evaluate(() => Object.fromEntries(['white', '#18181b'].map(color => {
    const canvas = document.createElement('canvas'); canvas.width = 1308; canvas.height = 1632;
    const ctx = canvas.getContext('2d'); ctx.fillStyle = color; ctx.fillRect(0, 0, canvas.width, canvas.height); return [color, canvas.toDataURL().split(',')[1]];
  })));
  page.on('pageerror', e => errors.push(e.message));
  await context.addInitScript(() => {
    // Desktop simulation of visualViewport resizing/panning, not a real OS keyboard.
    const viewport = window.visualViewport;
    window.__keyboardViewport = (height, offsetTop = 0) => {
      if (height === null) { delete viewport.height; delete viewport.offsetTop; }
      else {
        Object.defineProperty(viewport, 'height', { configurable: true, get: () => height });
        Object.defineProperty(viewport, 'offsetTop', { configurable: true, get: () => offsetTop });
      }
      viewport.dispatchEvent(new Event('resize')); viewport.dispatchEvent(new Event('scroll'));
    };
  });
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    if (!local(url)) { unexpected.push(url.origin); return route.abort(); }
    if (url.pathname === '/favicon.ico') return route.fulfill({ status: 204 });
    if (!url.pathname.startsWith('/api/')) return route.continue();
    const reply = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) }).catch(() => {});
    if (url.pathname === '/api/v1/auth/session') return reply({ authenticated: true, sessionState: 'active', onboardingCompleted: true, user: { id: 7, accountType: 'user' }, csrfToken: 'fixture-token' });
    if (url.pathname === '/api/v1/users/me') return reply({ userId: 7, nickname: '회원 7', accountType: 'user' });
    if (url.pathname === '/api/v1/me/topics') return reply({ topics: [{ id: 83, code: 'anime', name: '애니메이션', status: 'active', deletedAt: null, subtopicIds: [] }] });
    if (url.pathname === '/api/v1/topics/83/feed') return reply({ selectedTopicId: 83, sections: [{ contents: [content] }], nextCursor: null });
    if (url.pathname === '/api/v1/contents/17') return reply(content);
    if (/\/contents\/17\/slides\/\d\/image$/.test(url.pathname)) return route.fulfill({ status: 200, contentType: 'image/png', body: imageKind === 'photo' ? photo : Buffer.from(solids[imageKind === 'bright' ? 'white' : '#18181b'], 'base64') });
    if (url.pathname === '/api/v1/users/me/bookmarks') return reply({ items: [], nextCursor: null });
    if (url.pathname === '/api/v1/users/me/activity-events') return reply({ saved: request.postDataJSON().events.length, duplicated: 0, rejected: 0 });
    if (url.pathname === '/api/v1/reports') {
      assert.equal(request.method(), 'POST'); assert.equal(request.headers()['x-csrf-token'], 'fixture-token');
      const data = request.postDataJSON(); assert.equal(data.targetType, 'content'); assert.equal(data.targetId, 17); posts.push(data);
      const sentMode = mode; if (hold) await new Promise(resolve => release.push(resolve));
      if (sentMode === 'network') return route.abort('failed').catch(() => {});
      if (sentMode === 'csrf') return reply({ code: 'CSRF_INVALID' }, 403);
      if (sentMode === 'malformed') return reply({ status: 'received' }, 201);
      if (/^\d+$/.test(sentMode)) return reply({ code: 'ERROR', message: 'private' }, Number(sentMode));
      return reply({ reportId: posts.length, status: 'received' }, 201);
    }
    unexpected.push(`${request.method()} ${url.pathname}`); return route.abort();
  });
  const dialog = page.locator('dialog[open][data-report-state]'), input = dialog.getByRole('textbox');
  const shot = name => page.screenshot({ path: path.join(output, `${name}-${width}.png`) });
  const visit = async () => { await page.goto(`${base}/pivot/home?view=detail&postId=17&contentId=17&topicId=83`); await page.locator('[data-detail-state="ready"]:visible').waitFor(); };
  const open = async () => { await page.getByRole('button', { name: '콘텐츠 신고', exact: true }).click(); await dialog.waitFor(); };
  const detail = async () => { await dialog.getByLabel('기타', { exact: true }).check(); await dialog.getByRole('button', { name: '다음', exact: true }).click(); await input.waitFor(); };
  const releaseAll = () => { hold = false; release.splice(0).forEach(resolve => resolve()); };
  try {
    await visit(); await open(); assert.equal(await dialog.getByRole('radio').count(), 5); assert.equal(await dialog.getByRole('button', { name: '다음', exact: true }).isDisabled(), true);
    await page.evaluate(() => document.fonts.ready); await shot('reasons');
    step = 'focus trap, Escape and restoration';
    await page.keyboard.press('Shift+Tab'); assert.ok(await dialog.evaluate(el => el.contains(document.activeElement)));
    for (let n = 0; n < 12; n++) { await page.keyboard.press('Tab'); assert.ok(await dialog.evaluate(el => el.contains(document.activeElement))); }
    await page.keyboard.press('Escape'); await dialog.waitFor({ state: 'detached' });
    assert.equal(await page.getByRole('button', { name: '콘텐츠 신고', exact: true }).evaluate(el => el === document.activeElement), true);
    await open(); await page.goBack(); await dialog.waitFor({ state: 'detached' }); assert.equal(new URL(page.url()).searchParams.get('view'), 'detail');
    await open(); await detail(); assert.equal(await dialog.getByRole('button', { name: '신고하기', exact: true }).isDisabled(), false);
    await dialog.getByRole('button', { name: '신고 사유로 돌아가기' }).click(); assert.equal(await dialog.getByLabel('기타', { exact: true }).isChecked(), true); await dialog.getByRole('button', { name: '다음', exact: true }).click();
    await shot('detail-empty');
    step = 'keyboard resize, pan, caret and restoration'; await input.fill(Array.from({ length: 18 }, (_, i) => `${i + 1}번째 입력 줄`).join('\n')); await input.focus();
    const keyboard = [];
    for (const [height, offsetTop] of [[490, 48], [350, 0], [280, 36]]) {
      await page.evaluate(([h, y]) => window.__keyboardViewport(h, y), [height, offsetTop]); await pause(80);
      await input.evaluate(el => { el.setSelectionRange(el.value.length, el.value.length); el.dispatchEvent(new Event('input', { bubbles: true })); });
      await page.waitForFunction(() => { const el = document.querySelector('dialog textarea'); return el.scrollTop >= el.scrollHeight - el.clientHeight - 2; });
      const geometry = await input.evaluate(el => { const r = el.getBoundingClientRect(), d = el.closest('dialog').getBoundingClientRect(); return { top: r.top, bottom: r.bottom, dialogTop: d.top, dialogBottom: d.bottom, scrollTop: el.scrollTop, scrollHeight: el.scrollHeight, height: el.clientHeight, focused: el === document.activeElement }; });
      assert.ok(geometry.top >= offsetTop && geometry.bottom <= height + offsetTop); assert.ok(geometry.dialogTop >= offsetTop && geometry.dialogBottom <= height + offsetTop); assert.ok(geometry.scrollTop > 0); assert.ok(geometry.focused);
      await shot(`keyboard-${height}-end`);
      await input.evaluate(el => { el.setSelectionRange(0, 0); document.dispatchEvent(new Event('selectionchange')); });
      await page.waitForFunction(() => document.querySelector('dialog textarea').scrollTop <= 1);
      await input.evaluate(el => { const position = el.value.indexOf('9번째'); el.setSelectionRange(position, position); document.dispatchEvent(new Event('selectionchange')); });
      await page.waitForFunction(() => { const el = document.querySelector('dialog textarea'); return el.scrollTop > 10 && el.scrollTop < el.scrollHeight - el.clientHeight - 10; });
      keyboard.push({ viewportHeight: height, offsetTop, ...geometry });
      await shot(`keyboard-${height}`);
    }
    await page.evaluate(() => window.__keyboardViewport(null)); await pause(80);
    await input.fill('콘텐츠 내용 안에 주제와 상관없는 내용이 들어가 있어요.'); await shot('keyboard-restored');
    await page.setViewportSize({ width, height: 490 }); await pause(100);
    assert.ok((await input.boundingBox()).y + (await input.boundingBox()).height < 490);
    await page.setViewportSize({ width, height: 812 }); await pause(100);
    const restored = await dialog.boundingBox(); assert.ok(restored.y > 100); await dialog.getByRole('button', { name: '신고 닫기' }).click(); await dialog.waitFor({ state: 'detached' });
    assert.equal(await page.evaluate(() => document.body.style.overflow), '');
    step = 'boundaries and single flight'; await open(); await detail(); await input.fill('😀'.repeat(501)); assert.ok(await dialog.getByRole('button', { name: '신고하기' }).isDisabled());
    await input.fill('😀'.repeat(500)); assert.equal(await dialog.getByRole('button', { name: '신고하기' }).isDisabled(), false); await input.fill('');
    hold = true; const before = posts.length; await dialog.locator('form').evaluate(form => { form.requestSubmit(); form.requestSubmit(); });
    for (let n = 0; n < 100 && !release.length; n++) await pause(25); assert.equal(posts.length, before + 1); assert.equal(posts.at(-1).detail, undefined); assert.equal(await dialog.locator('[type=submit]').isDisabled(), true);
    releaseAll(); await page.locator('[data-report-state="received"]').waitFor(); await shot('received'); await dialog.getByRole('button', { name: '확인' }).click(); await dialog.waitFor({ state: 'detached' });
    step = 'close pending write and suppress late authentication response'; mode = '401'; hold = true; await visit(); await open(); await detail();
    const closingCount = posts.length; await dialog.getByRole('button', { name: '신고하기' }).click();
    for (let n = 0; n < 100 && !release.length; n++) await pause(25); assert.equal(posts.length, closingCount + 1);
    await dialog.getByRole('button', { name: '신고 닫기' }).click(); await dialog.waitFor({ state: 'detached' }); releaseAll(); mode = 'normal';
    await open(); await detail(); await dialog.getByRole('button', { name: '신고하기' }).click(); await page.locator('[data-report-state="uncertain"]').waitFor();
    assert.equal(posts.length, closingCount + 1); assert.equal(new URL(page.url()).pathname, '/pivot/home');
    for (const [failure, status] of [['409', 'duplicate'], ['404', 'missing'], ['403', 'unavailable'], ['csrf', 'error'], ['503', 'uncertain'], ['network', 'uncertain'], ['malformed', 'uncertain']]) {
      step = `response ${failure}`; mode = failure; await visit(); await open(); await detail(); await input.fill('보존할 내용'); const start = posts.length;
      await dialog.getByRole('button', { name: '신고하기', exact: true }).click(); await page.locator(`[data-report-state="${status}"]`).waitFor();
      assert.equal(posts.length, start + 1); assert.doesNotMatch(await dialog.innerText(), /정상적으로 접수|private/); assert.equal(await input.inputValue(), '보존할 내용');
      if (failure === '503') { await shot('uncertain'); await dialog.getByRole('button', { name: '확인' }).click(); await dialog.waitFor({ state: 'detached' }); await open(); await detail(); await dialog.getByRole('button', { name: '신고하기' }).click(); await page.locator('[data-report-state="uncertain"]').waitFor(); assert.equal(posts.length, start + 1); }
      if (failure === 'csrf') { mode = 'normal'; await dialog.getByRole('button', { name: '다시 시도' }).click(); await page.locator('[data-report-state="received"]').waitFor(); }
    }
    step = '401 authentication boundary'; mode = '401'; await visit(); await open(); await detail(); await dialog.getByRole('button', { name: '신고하기' }).click(); await page.waitForURL('**/pivot/login');
    step = 'two-slide navigation and eyebrow comparison'; mode = 'normal';
    const comparisons = [];
    for (imageKind of ['bright', 'dark', 'photo']) {
      await page.goto(`${base}/pivot/home`); await page.locator('[data-home-state="ready"]').waitFor();
      for (const scope of ['home', 'detail']) {
        if (scope === 'detail') { await page.locator('[data-home-detail="17"]').click(); await page.locator('[data-detail-state="ready"]:visible').waitFor(); }
        const carousel = page.locator(`[data-content-carousel="${scope}"]:visible`), track = carousel.locator('[data-carousel-track]');
        await carousel.locator('[data-carousel-slide="1"] img[data-carousel-image-state="ready"]').waitFor(); await page.evaluate(() => document.fonts.ready);
        assert.equal(await carousel.locator('[data-carousel-slide]').count(), 2);
        // Actual component render and original image/gradient; only comparison text styling changes.
        const eyebrow = carousel.locator('[data-carousel-slide="1"] p').first();
        const original = await eyebrow.evaluate(el => { const cs = getComputedStyle(el); return { shadow: cs.textShadow, stroke: cs.webkitTextStrokeWidth, paintOrder: cs.paintOrder, color: cs.color, size: cs.fontSize, weight: cs.fontWeight }; });
        await carousel.screenshot({ path: path.join(output, `eyebrow-${scope}-${imageKind}-after-${width}.png`) });
        await eyebrow.evaluate(el => { el.style.textShadow = 'none'; el.style.webkitTextStroke = '0px'; });
        await carousel.screenshot({ path: path.join(output, `eyebrow-${scope}-${imageKind}-before-${width}.png`) });
        for (const stroke of ['0.3px', '0.4px', '0.5px']) {
          await eyebrow.evaluate((el, stroke) => { el.style.webkitTextStroke = `${stroke} #18181b`; }, stroke);
          if (width === 375) await carousel.screenshot({ path: path.join(output, `eyebrow-${scope}-${imageKind}-stroke-${stroke}-${width}.png`) });
        }
        await eyebrow.evaluate(el => { el.style.removeProperty('text-shadow'); el.style.removeProperty('-webkit-text-stroke'); });
        assert.equal(original.color, 'rgb(199, 172, 251)'); assert.equal(original.size, '11px'); assert.equal(original.weight, '500');
        await track.focus(); await page.keyboard.press('End'); await pause(100); assert.equal(await track.evaluate(el => Math.round(el.scrollLeft / el.clientWidth)), 1);
        await page.keyboard.press('Home'); await pause(100); assert.equal(await track.evaluate(el => Math.round(el.scrollLeft / el.clientWidth)), 0);
        await carousel.getByRole('button', { name: '2번 슬라이드 보기' }).click(); await pause(100); assert.equal(await track.evaluate(el => Math.round(el.scrollLeft / el.clientWidth)), 1);
        comparisons.push({ scope, imageKind, ...original, pages: 2 });
      }
    }
    step = 'Demo isolation'; const demoStart = posts.length; await page.goto(`${base}/pivot/demo?view=home`); await page.locator('[data-home-detail]').first().click(); await open(); await detail(); assert.ok(await dialog.getByRole('button', { name: '신고하기' }).isDisabled()); assert.equal(posts.length, demoStart);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false); assert.deepEqual(unexpected, []); assert.deepEqual(errors, []);
    results.push({ width, passed: true, reports: posts.length, keyboardSimulation: keyboard, comparisons, realDeviceKeyboard: 'not tested', unexpected, errors });
    console.log(`${width}px report, keyboard simulation and two-slide carousel passed`);
  } catch (error) { await shot('failure'); throw new Error(`${width}px ${step}: ${error.message}`, { cause: error }); }
  finally { releaseAll(); await context.close(); }
}
(async () => {
  fs.mkdirSync(output, { recursive: true }); const browser = await chromium.launch({ headless: true, channel: process.env.PIVOT_BROWSER_CHANNEL || 'msedge' });
  try { for (const width of [375, 320, 390]) await verify(browser, width); }
  finally { await browser.close(); fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(results, null, 2)); }
  console.log(JSON.stringify(results, null, 2));
})().catch(e => { console.error(e); process.exitCode = 1; });
