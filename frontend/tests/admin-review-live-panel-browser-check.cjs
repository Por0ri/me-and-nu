// Isolated browser rehearsal: real React panel, flow and API client against the
// loopback fake API. This never edits/activates the application write gate.
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const toolsRoot = process.env.PIVOT_BROWSER_TOOLS;
assert.ok(toolsRoot, 'Set PIVOT_BROWSER_TOOLS to isolated playwright/esbuild node_modules');
const { chromium } = require(path.join(toolsRoot, 'playwright'));
const { build } = require(path.join(toolsRoot, 'esbuild'));
const { createAdminReviewFixture } = require('./helpers/admin-review-api-fixture.cjs');
const root = path.join(__dirname, '..');
const output = path.resolve(process.argv[2] || path.join(root, '..', 'validation', 'fixed-live-panel'));
const report = { scope: 'PR92 safety fixes; loopback fake API; real Chromium React DOM; production activation=null', checks: [] };
const entry = `
import React, { useCallback, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ContentReviewPanel } from './components/admin/content-review-panel';
import { PendingReviewNotice } from './components/admin/pending-review-notice';
import { getSession } from './lib/api';
import { loadAdminContent, adminFailure } from './lib/admin-content';
import { createReviewFlow } from './lib/admin-review-flow';
import { submitAdminContentReview } from './lib/admin-content-review';
import { reviewWriteGate } from './lib/admin-review-write-gate';
const query = new URLSearchParams(location.search);
const id = Number(query.get('id') || 2);
const activation = { apiOrigin: location.origin, approvedBy: 'test harness', approvedOn: '2026-11-20', reference: 'loopback fake API only' };
const flow = createReviewFlow(id, { submit: submitAdminContentReview, newRequestId: () => crypto.randomUUID(),
  gate: () => reviewWriteGate(location.origin, query.get('gate') === 'off' ? null : activation) });
function App() {
  const [detail, setDetail] = useState(null), [failure, setFailure] = useState(null), [shown, setShown] = useState(true);
  const reload = useCallback(async () => {
    setDetail(null); setFailure(null);
    try { await getSession(); setDetail(await loadAdminContent(id)); }
    catch (error) { setFailure(adminFailure(error)); }
  }, []);
  useEffect(() => { void reload(); }, [reload]);
  window.reviewHarness = { state: flow.state, reload, hide: () => setShown(false), show: () => setShown(true) };
  return <><main><h1>격리된 검수 UI 리허설</h1><p>가상 계정과 메모리 API만 사용합니다.</p>
    {failure ? <p data-read-failure={failure.status}>{failure.message}</p> : detail
      ? <><p data-detail-state>{detail.status} / {detail.judgmentStatus}</p>{shown && <ContentReviewPanel content={detail} flow={flow} onReload={reload} />}</>
      : <p data-loading>불러오는 중</p>}
    {!shown && <p data-panel-hidden>패널을 떠난 상태</p>}
  </main><PendingReviewNotice /></>;
}
createRoot(document.getElementById('root')).render(<App />);
`;
const listen = server => new Promise(resolve => server.listen(0, '127.0.0.1', () => resolve(`http://127.0.0.1:${server.address().port}`)));
const close = server => new Promise(resolve => { server.closeAllConnections?.(); server.close(resolve); });
const posts = fixture => fixture.requests.filter(request => request.method === 'POST');
let fixture, origin, held = false, releaseHeld = null;

async function run() {
  fs.mkdirSync(output, { recursive: true });
  const built = await build({ stdin: { contents: entry, loader: 'tsx', resolveDir: root },
    bundle: true, write: false, outfile: 'bundle.js', platform: 'browser', jsx: 'automatic',
    define: { 'process.env.NODE_ENV': '"production"', 'process.env.NEXT_PUBLIC_API_BASE_URL': 'location.origin' },
    alias: { '@': root }, external: ['/ui-onboarding/*'], logLevel: 'silent' });
  const assets = new Map(built.outputFiles.map(file => [path.basename(file.path), file.contents]));
  const server = http.createServer(async (req, res) => {
    try {
      const url = new URL(req.url, origin);
      if (url.pathname.startsWith('/api/')) {
        const chunks = []; for await (const chunk of req) chunks.push(chunk);
        const headers = { ...req.headers }; delete headers.host; delete headers.connection;
        const response = await fetch(`${fixture.origin}${url.pathname}${url.search}`, { method: req.method, headers,
          body: ['GET', 'HEAD'].includes(req.method) ? undefined : Buffer.concat(chunks) });
        const body = Buffer.from(await response.arrayBuffer());
        if (held && req.method === 'POST') await new Promise(resolve => { releaseHeld = resolve; });
        res.writeHead(response.status, { 'content-type': 'application/json', 'cache-control': 'no-store' });
        res.end(body); return;
      }
      if (url.pathname === '/bundle.js' || url.pathname === '/bundle.css') {
        res.writeHead(200, { 'content-type': url.pathname.endsWith('.css') ? 'text/css' : 'text/javascript' });
        res.end(assets.get(url.pathname.slice(1)) || ''); return;
      }
      if (url.pathname === '/ui-onboarding/PretendardVariable.woff2') {
        res.writeHead(200, { 'content-type': 'font/woff2' });
        res.end(fs.readFileSync(path.join(root, 'public', 'ui-onboarding', 'PretendardVariable.woff2'))); return;
      }
      res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' });
      res.end('<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/bundle.css"><style>body{font-family:Arial,sans-serif;margin:0;background:#f5f6f7}main{max-width:1000px;padding:24px;margin:auto}button,textarea{font:inherit}</style><div id="root"></div><script src="/bundle.js"></script></html>');
    } catch { res.writeHead(503, { 'content-type': 'application/json' }); res.end('{"code":"PROXY_RESPONSE_LOST"}'); }
  });
  origin = await listen(server);
  const browser = await chromium.launch({ headless: true, channel: process.env.PIVOT_BROWSER_CHANNEL || 'msedge' });
  async function scenario(name, actor, runCase, { width = 1100, query = '', prepare } = {}) {
    if (process.env.PIVOT_REVIEW_CASE && process.env.PIVOT_REVIEW_CASE !== name) return;
    fixture = createAdminReviewFixture({ allowedOrigins: [origin] }); await fixture.start();
    prepare?.(fixture);
    const context = await browser.newContext({ viewport: { width, height: 1000 }, serviceWorkers: 'block' });
    const errors = [], external = [];
    const page = await context.newPage(); page.setDefaultTimeout(10000);
    page.on('pageerror', error => errors.push(error.message));
    await context.route('**/*', route => {
      if (new URL(route.request().url()).origin !== origin) { external.push(route.request().url()); return route.abort(); }
      return route.continue();
    });
    await context.addCookies([{ name: 'menu_session', value: `session-${actor}`, url: origin }]);
    try {
      await page.goto(`${origin}/?${query}`); await page.locator('[data-detail-state]').waitFor();
      await runCase({ page, context, fixture });
      assert.deepEqual(errors, []); assert.deepEqual(external, []);
      report.checks.push({ name, width, posts: posts(fixture).length, auditRows: fixture.audit.length, passed: true });
    } catch (error) {
      report.checks.push({ name, width, passed: false, error: error.message, errors, external });
      await page.screenshot({ path: path.join(output, `${name}-failed.png`), fullPage: true }).catch(() => {});
      throw error;
    } finally {
      held = false; releaseHeld?.(); releaseHeld = null;
      await context.close(); await fixture.stop();
      fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(report, null, 2));
    }
  }
  async function execute(page, action = '승인') {
    await page.getByRole('button', { name: `${action} 내용 확인`, exact: true }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel('조치 사유 (필수)').fill('  출처와 본문 확인  ');
    await dialog.getByRole('button', { name: '입력 확인', exact: true }).click();
    await dialog.getByRole('button', { name: `${action} 실행`, exact: true }).evaluate(button => { button.click(); button.click(); });
  }
  const phase = (page, value) => page.locator(`[data-review-phase="${value}"]`).waitFor();
  const unloadGuard = page => page.evaluate(() => { const event = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(event); return event.defaultPrevented; });
  try {
    await scenario('production-gate-off', 'operator', async ({ page, fixture }) => {
      await page.getByRole('button', { name: '승인 내용 확인', exact: true }).click();
      const dialog = page.getByRole('dialog'); await dialog.getByLabel('조치 사유 (필수)').fill('출처 확인');
      await dialog.getByRole('button', { name: '입력 확인', exact: true }).click();
      assert.equal(await dialog.getByRole('button', { name: '승인 실행 (미연결)' }).isDisabled(), true);
      assert.equal(posts(fixture).length, 0);
    }, { query: 'gate=off' });
    await scenario('viewer-read-only', 'viewer', async ({ page, fixture }) => {
      assert.equal(await page.getByRole('button', { name: '승인 내용 확인', exact: true }).isDisabled(), true);
      assert.equal(await page.getByRole('button', { name: '반려 내용 확인', exact: true }).isDisabled(), true);
      assert.equal(posts(fixture).length, 0);
    });
    for (const width of [1100, 390]) await scenario(`approve-${width}`, 'operator', async ({ page, fixture }) => {
      await execute(page); await phase(page, 'succeeded');
      assert.equal(posts(fixture).length, 1); assert.equal(fixture.audit.length, 1);
      const body = JSON.parse(posts(fixture)[0].body);
      assert.equal(body.expectedUpdatedAt, '2026-11-19T09:12:34.123456+09:00');
      assert.equal(body.rationale, '출처와 본문 확인');
      await page.getByText('active / confirmed', { exact: true }).waitFor();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await page.screenshot({ path: path.join(output, `approve-${width}.png`), fullPage: true });
    }, { width });
    await scenario('reject-admin', 'admin', async ({ page, fixture }) => {
      await execute(page, '반려'); await phase(page, 'succeeded');
      assert.equal(fixture.contents.get(2).status, 'hidden'); assert.equal(fixture.audit[0].actorUserId, 23);
    });
    await scenario('uncertain-replay', 'operator', async ({ page, fixture }) => {
      fixture.failNext('unavailable-after-commit'); await execute(page); await phase(page, 'uncertain');
      await page.waitForTimeout(250); assert.equal(posts(fixture).length, 1); assert.equal(fixture.audit.length, 1);
      await page.screenshot({ path: path.join(output, 'uncertain-before-recheck.png'), fullPage: true });
      await page.getByRole('button', { name: '같은 요청으로 다시 확인 (0/3)', exact: true }).click();
      await phase(page, 'succeeded'); assert.equal(fixture.audit.length, 1);
      assert.equal(posts(fixture)[0].body, posts(fixture)[1].body);
      await page.getByText('이미 처리된 같은 요청의 결과를 다시 받았습니다. 새로 변경하지 않았습니다.', { exact: true }).waitFor();
    });
    await scenario('stale-state-409', 'operator', async ({ page, fixture }) => {
      fixture.contents.get(2).updatedAt = '2026-11-19T10:00:00.000001+09:00';
      await execute(page); await phase(page, 'stopped');
      assert.equal(posts(fixture).length, 1); assert.equal(fixture.audit.length, 0);
      await page.waitForTimeout(200); assert.equal(posts(fixture).length, 1);
    });
    await scenario('grant-revoked', 'operator', async ({ page, fixture }) => {
      fixture.revoke('operator'); await execute(page);
      await page.locator('[data-read-failure="forbidden"]').waitFor();
      assert.equal(fixture.audit.length, 0); assert.equal(await page.locator('[data-review-phase="succeeded"]').count(), 0);
    });
    await scenario('csrf-rotation', 'operator', async ({ page, fixture }) => {
      fixture.rotateCsrf('operator'); await execute(page);
      await phase(page, 'succeeded'); assert.equal(fixture.audit.length, 1);
      assert.deepEqual(posts(fixture).map(request => request.headers['x-csrf-token']), ['csrf-operator-2']);
    });
    await scenario('fixed-account-scope', 'operator', async ({ page, context, fixture }) => {
      fixture.failNext('unavailable-before-commit'); await execute(page); await phase(page, 'uncertain');
      const requestId = JSON.parse(posts(fixture)[0].body).requestId;
      await context.addCookies([{ name: 'menu_session', value: 'session-admin', url: origin }]);
      // Simulate the same app reading the new account session after another-tab login.
      await page.evaluate(() => reviewHarness.reload()); await page.locator('[data-detail-state]').waitFor();
      await page.getByRole('button', { name: '같은 요청으로 다시 확인 (0/3)', exact: true }).click();
      await phase(page, 'uncertain');
      assert.equal(posts(fixture).length, 1); assert.equal(fixture.audit.length, 0);
      assert.equal(await page.evaluate(() => reviewHarness.state().scope.actorUserId), 22);
      assert.ok((await page.locator('[data-pending-reviews]').innerText()).includes(requestId));
      assert.equal(await unloadGuard(page), true);
      await page.screenshot({ path: path.join(output, 'fixed-account-scope.png'), fullPage: true });
      await page.setViewportSize({ width: 390, height: 1000 });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await page.screenshot({ path: path.join(output, 'fixed-account-scope-390.png'), fullPage: true });
      await context.addCookies([{ name: 'menu_session', value: 'session-operator', url: origin }]);
      await page.getByRole('button', { name: '원래 요청 다시 확인 (1/3)', exact: true }).click();
      await phase(page, 'succeeded');
      assert.equal(fixture.audit[0].actorUserId, 22); assert.equal(fixture.audit[0].requestId, requestId);
      assert.equal(posts(fixture)[0].body, posts(fixture)[1].body);
    });
    await scenario('fixed-uncertain-navigation', 'operator', async ({ page, fixture }) => {
      fixture.failNext('unavailable-after-commit'); await execute(page); await phase(page, 'uncertain');
      const requestId = JSON.parse(posts(fixture)[0].body).requestId;
      assert.equal(await unloadGuard(page), true);
      const prompt = page.waitForEvent('dialog');
      await page.evaluate(() => { setTimeout(() => location.reload(), 0); });
      const navigationDialog = await prompt;
      assert.equal(navigationDialog.type(), 'beforeunload'); await navigationDialog.dismiss();
      await phase(page, 'uncertain');
      assert.equal(await page.evaluate(() => reviewHarness.state().request.requestId), requestId);
      await page.screenshot({ path: path.join(output, 'fixed-uncertain-navigation.png'), fullPage: true });
    });
    await scenario('fixed-submitting-unmount', 'operator', async ({ page, fixture }) => {
      held = true; await execute(page); await phase(page, 'submitting');
      assert.equal(await unloadGuard(page), true);
      await page.evaluate(() => reviewHarness.hide()); await page.locator('[data-panel-hidden]').waitFor();
      assert.equal(await unloadGuard(page), true);
      const requestId = await page.evaluate(() => reviewHarness.state().request.requestId);
      assert.ok((await page.locator('[data-pending-reviews]').innerText()).includes(requestId));
      await page.screenshot({ path: path.join(output, 'fixed-submitting-unmount.png'), fullPage: true });
      while (!releaseHeld) await new Promise(resolve => setTimeout(resolve, 10));
      held = false; releaseHeld(); releaseHeld = null;
      await page.waitForFunction(() => reviewHarness.state().phase === 'succeeded');
      await page.evaluate(() => reviewHarness.show()); await phase(page, 'succeeded');
      assert.equal(fixture.audit.length, 1);
      assert.equal(await unloadGuard(page), false); assert.equal(await page.locator('[data-pending-reviews]').count(), 0);
    });
    await scenario('same-user-regrant', 'operator', async ({ page, fixture }) => {
      fixture.failNext('unavailable-before-commit'); await execute(page); await phase(page, 'uncertain');
      const requestId = await page.evaluate(() => reviewHarness.state().request.requestId);
      fixture.regrant('operator');
      await page.getByRole('button', { name: '원래 요청 다시 확인 (0/3)', exact: true }).click();
      await phase(page, 'uncertain');
      assert.equal(posts(fixture).length, 1); assert.equal(fixture.audit.length, 0);
      assert.equal(await page.evaluate(() => reviewHarness.state().scope.adminAccountId), 122);
      assert.ok((await page.locator('[data-pending-reviews]').innerText()).includes(requestId));
    });
    await scenario('permission-read-failure-retains-request', 'operator', async ({ page, fixture }) => {
      fixture.failNext('unavailable-after-commit'); await execute(page); await phase(page, 'uncertain');
      const requestId = await page.evaluate(() => reviewHarness.state().request.requestId);
      fixture.revoke('operator'); await page.evaluate(() => reviewHarness.reload());
      await page.locator('[data-read-failure="forbidden"]').waitFor();
      const notice = page.locator('[data-pending-reviews]'); assert.ok((await notice.innerText()).includes(requestId));
      await notice.getByRole('button', { name: '원래 요청 다시 확인 (0/3)', exact: true }).click();
      await notice.getByRole('button', { name: '원래 요청 다시 확인 (1/3)', exact: true }).waitFor();
      assert.equal(posts(fixture).length, 1); assert.equal(fixture.audit.length, 1); assert.equal(await unloadGuard(page), true);
      await page.screenshot({ path: path.join(output, 'permission-read-failure.png'), fullPage: true });
      page.once('dialog', dialog => dialog.accept());
      await notice.getByRole('button', { name: '요청 번호를 기록하고 확인 종료', exact: true }).click();
      await notice.waitFor({ state: 'hidden' }); assert.equal(await unloadGuard(page), false);
    });
    for (const [name, scope] of [['missing', undefined], ['malformed', { actorUserId: 22, adminAccountId: '122' }]]) {
      await scenario(`scope-${name}`, 'operator', async ({ page, fixture }) => {
        assert.equal(await page.getByRole('button', { name: '승인 내용 확인', exact: true }).isDisabled(), true);
        assert.equal(posts(fixture).length, 0);
      }, { prepare: fixture => { fixture.contents.get(2).reviewOverride = { allowedActions: ['approve', 'reject'], disabledReason: null, scope }; } });
    }
  } finally { await browser.close(); await close(server); }
  console.log(JSON.stringify(report, null, 2));
}
run().catch(error => { console.error(error); process.exitCode = 1; });
