const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const test = require('node:test'), assert = require('node:assert/strict'), ts = require('typescript'), React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { generationFixture, unavailableFixture } = require('./helpers/generation-fixture.cjs');
const root = path.resolve(__dirname, '..');
function runtime(respond = generationFixture) {
  const modules = new Map(), calls = [], browser = new EventTarget();
  const context = vm.createContext({ URL, URLSearchParams, Headers, Response, AbortController, Event, Intl, window: browser,
    process: { env: { NEXT_PUBLIC_API_BASE_URL: 'http://localhost:8196' } },
    fetch: async (url, init) => { calls.push({ url, init }); const value = await respond(new URL(url), init); return value instanceof Response ? value : new Response(JSON.stringify(value)); } });
  function load(file) {
    file = file.replaceAll('\\', '/'); if (modules.has(file)) return modules.get(file).exports;
    const mod = { exports: {} }; modules.set(file, mod);
    const code = ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
    const localRequire = name => {
      if (['react', 'react/jsx-runtime'].includes(name)) return require(name);
      if (name === 'next/link') return { __esModule: true, default: props => React.createElement('a', props) };
      if (name === './use-admin-read') return {};
      if (name.endsWith('.module.css')) return { __esModule: true, default: new Proxy({}, { get: (_, key) => String(key) }) };
      const next = name.startsWith('@/') ? name.slice(2) : path.join(path.dirname(file), name);
      return load(next + (fs.existsSync(path.join(root, next + '.tsx')) ? '.tsx' : '.ts'));
    };
    vm.runInContext(`(function(module,exports,require){${code}\n})`, context)(mod, mod.exports, localRequire);
    return mod.exports;
  }
  return { api: load('lib/admin-generation.ts'), Panel: load('components/admin/generation-panel.tsx').GenerationPanel, calls, browser };
}
const clone = value => JSON.parse(JSON.stringify(value));
const render = state => renderToStaticMarkup(React.createElement(runtime().Panel, { state, refresh() {} }));

test('snapshot uses one read-only authenticated no-cache cancellable endpoint', async () => {
  const env = runtime(), signal = new AbortController().signal;
  assert.deepEqual(clone(await env.api.loadAdminGeneration(signal)), generationFixture());
  assert.equal(env.calls.length, 1);
  const { url, init } = env.calls[0];
  assert.equal(new URL(url).pathname, '/api/v1/admin/generation');
  assert.equal(init.method ?? 'GET', 'GET'); assert.equal(init.credentials, 'include'); assert.equal(init.cache, 'no-store');
  assert.equal(init.signal, signal); assert.equal(init.body, undefined);
});
test('unknowns are retained and stale values remain explicitly historical', () => {
  const env = runtime(), value = generationFixture(); value.availability = 'stale'; value.reason = 'snapshot_stale'; value.ageSeconds = 300;
  value.broker.ragQueued = null;
  assert.equal(env.api.parseGenerationSnapshot(value).broker.ragQueued, null);
  const html = render({ status: 'ready', data: value });
  assert.match(html, /갱신 지연/); assert.match(html, /마지막 수집 당시/); assert.match(html, /5분 전 수집/);
  assert.match(html, /0\.04703593/); assert.match(html, /비용 미확정 1건/); assert.match(html, /사용량 집계 대기 1건/);
  assert.match(html, /AI 검수 통과는 공개 승인이 아닙니다/); assert.match(html, /확인 불가/);
});
test('unavailable and failed reads never invent zero costs or retain protected values', () => {
  const env = runtime(), value = unavailableFixture(); env.api.parseGenerationSnapshot(value);
  const html = render({ status: 'ready', data: value }); assert.match(html, /아직 수집된 현황이 없습니다/);
  assert.doesNotMatch(html, /\$0[.,]0|<progress/);
  for (const status of ['loading', 'error', 'forbidden', 'login-required']) {
    const output = render({ status, message: 'hidden' });
    assert.doesNotMatch(output, /0\.04703593|오늘 기록된 추정 비용|40건|20건/);
  }
});
test('today data is not reused across KST policy-day mismatch', () => {
  const env = runtime(), value = generationFixture(); value.reason = 'policy_day_mismatch'; value.daily = value.jobs = null;
  env.api.parseGenerationSnapshot(value);
  assert.match(render({ status: 'ready', data: value }), /이전 날짜의 시도·작업 수를 오늘 수치로 표시하지 않습니다/);
  value.daily = generationFixture().daily; assert.throws(() => env.api.parseGenerationSnapshot(value), { code: 'INVALID_ADMIN_RESPONSE' });
});
test('limits, costs, nullable fields and date/time metadata reject contradictions', () => {
  const env = runtime();
  for (const mutate of [
    x => x.daily.attempts = 41, x => x.daily.maxAttempts = 50, x => x.daily.passes = 21,
    x => x.daily.remainingAttempts = 38, x => x.daily.passes = 4, x => x.daily.articleAttempts = 2,
    x => x.daily.totalEstimatedCostUsd = x.daily.knownEstimatedCostUsd,
    x => x.daily.knownEstimatedCostUsd = 0.047, x => x.daily.knownEstimatedCostUsd = '-1',
    x => x.daily.knownEstimatedCostUsd = '1e2', x => x.daily.knownEstimatedCostUsd = '00.1',
    x => delete x.jobs.rag.rejected, x => x.jobs.rag.queued = -1, x => x.ageSeconds = 0.5,
    x => x.timezone = 'UTC', x => x.policyDate = '2026-13-01', x => x.policyDate = '2026-02-30',
    x => x.generatedAt = '2026-11-27 12:00', x => x.staleAfterSeconds = 600,
    x => x.runtime.ragWorker = 'healthy', x => x.availability = 'unavailable',
  ]) { const value = generationFixture(); mutate(value); assert.throws(() => env.api.parseGenerationSnapshot(value), { code: 'INVALID_ADMIN_RESPONSE' }); }
});
test('real zero costs are distinct from unknown and no history is added to daily', () => {
  const env = runtime(); assert.equal(env.api.generationUsd('0'), '$0.0000'); assert.equal(env.api.generationUsd(null), '확인 불가');
  assert.equal(env.api.generationUsd('0.000000001'), '$0.000000001');
  assert.equal(env.api.generationUsd('999999999999.000000000001'), '$999,999,999,999.000000000001');
  const html = render({ status: 'ready', data: generationFixture() });
  assert.match(html, /전체 기록된 추정 비용 \(오늘 포함\)/); assert.match(html, /전체 누적에는 오늘과 이월 비용이 포함/);
  assert.match(html, /AI 검수 반려/); assert.match(html, /실행 실패/); assert.match(html, /검수 대기 목록/);
});
test('cost totals preserve all twelve decimal places rather than binary floating point equality', () => {
  const env = runtime(), value = generationFixture();
  value.daily.unknownCostJobs = value.daily.inflightCostJobs = 0;
  value.daily.knownEstimatedCostUsd = '999999999999.000000000001';
  value.daily.totalEstimatedCostUsd = '999999999999.000000000002';
  assert.throws(() => env.api.parseGenerationSnapshot(value), { code: 'INVALID_ADMIN_RESPONSE' });
  value.daily.knownEstimatedCostUsd = '0.1000'; value.daily.totalEstimatedCostUsd = '0.1';
  env.api.parseGenerationSnapshot(value);
});
test('an unfinished failed controller is never labelled as active paid generation', () => {
  const value = generationFixture(); value.runtime.controller = 'failed'; value.jobs.rag.failed = 1;
  const html = render({ status: 'ready', data: value });
  assert.match(html, /실행 상태 확인 필요/); assert.match(html, /현재 생성 중이라는 뜻은 아닙니다/);
  assert.match(html, /사용량 집계 대기 1건/); assert.doesNotMatch(html, /· 진행 중 1건/);
});
test('authorization failures remain failures and emit existing unauthorized invalidation', async () => {
  const env = runtime(() => new Response(JSON.stringify({ code: 'AUTH_REQUIRED', message: 'denied' }), { status: 401 }));
  let invalidations = 0; env.browser.addEventListener('api:unauthorized', () => invalidations++);
  await assert.rejects(env.api.loadAdminGeneration(), { status: 401 }); assert.equal(invalidations, 1);
  await assert.rejects(runtime(() => new Response('{}', { status: 403 })).api.loadAdminGeneration(), { status: 403 });
});
