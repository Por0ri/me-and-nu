const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const test = require('node:test'), assert = require('node:assert/strict'), ts = require('typescript'), React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const root = path.resolve(__dirname, '..'), modules = new Map();
function load(file) {
  file = file.replaceAll('\\', '/');
  if (modules.has(file)) return modules.get(file).exports;
  const mod = { exports: {} }; modules.set(file, mod);
  const code = ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  const localRequire = name => {
    if (['react', 'react/jsx-runtime'].includes(name)) return require(name);
    if (name === 'next/link') return { __esModule: true, default: props => React.createElement('a', props) };
    if (name === '@/lib/api') return { ApiError: class ApiError extends Error {}, apiRequest() { throw new Error('Static view must not fetch'); } };
    if (name === '@/lib/admin-statistics-api') return { loadRecommendationPerformance() { throw new Error('Static view must not fetch'); } };
    if (name === './use-admin-read') return {};
    if (name.endsWith('.module.css')) return { __esModule: true, default: new Proxy({}, { get: (_, key) => String(key) }) };
    const next = name.startsWith('@/') ? name.slice(2) : path.join(path.dirname(file), name);
    return load(next + (fs.existsSync(path.join(root, next + '.tsx')) ? '.tsx' : '.ts'));
  };
  vm.runInNewContext(`(function(module,exports,require){${code}\n})`, { URL, URLSearchParams, Intl })(mod, mod.exports, localRequire);
  return mod.exports;
}
const { RecommendationPerformanceView } = load('components/admin/recommendation-performance.tsx');
function fixture() {
  const score = (audience, ndcgAt10, hitRateAt10, sampleSize) => ({ audience, ndcgAt10, hitRateAt10, sampleSize, ndcgDeltaVsBaseline: ndcgAt10, hitRateDeltaVsBaseline: hitRateAt10 });
  return {
    availability: 'available', reason: null, audience: 'all', period: '7d',
    periodStart: '2026-11-14T00:00:00Z', periodEnd: '2026-11-21T00:00:00Z',
    evaluatedAt: '2026-11-20T00:00:00Z', evaluationId: 'screen-test', currentModelId: 'v05', baselineModelId: 'popular',
    conditions: { evaluationData: 'Isolated evaluation', inputConditions: 'Same cases', commonRules: 'one per series', targetPeriod: 'Held-out period', groundTruth: 'One series', candidatePolicy: 'Same candidates', recordSource: 'Test fixture', cohortInterpretation: 'input_scenarios' },
    cohorts: [{ audience: 'existing', definition: 'Full history scenario', sampleSize: 11 }, { audience: 'new', definition: 'Shortened input scenario', sampleSize: 7 }],
    models: [
      { modelId: 'v05', label: 'v0.5', version: 'v1', scores: [score('existing', 0.5, 0.6, 11), score('new', 0, 0, 7)] },
      { modelId: 'popular', label: '인기순', version: 'v1', scores: [score('existing', 0, 0, 11), score('new', 0, 0, 7)] },
    ],
  };
}
function render(state, audience = 'all') {
  return renderToStaticMarkup(React.createElement(RecommendationPerformanceView, {
    query: { view: 'performance', period: '7d', audience }, navigate() {}, retry() {}, state,
  }));
}

test('paired chart preserves observed zero and never pools sample sizes', () => {
  const html = render({ status: 'ready', data: fixture() });
  assert.match(html, /data-performance-chart="ndcg"/);
  assert.equal((html.match(/data-model-id=/g) || []).length, 4);
  assert.match(html, /data-model-id="v05" data-audience="new" data-value="0"/);
  assert.match(html, /style="width:0%"/);
  assert.match(html, /0\.0000/); assert.match(html, /0\.0%/);
  assert.match(html, /11건/); assert.match(html, /7건/); assert.doesNotMatch(html, /18건/);
  assert.match(html, /실제 가입 집단으로 해석하거나 두 표본을 합산하지 않습니다/);
  assert.match(html, /평가 기준 모델 v0\.5/);
  assert.match(html, /현재 서비스에 배포된 모델의 성능을 보증하지 않습니다/);
  assert.match(html, /aria-label="비교할 추천 성능 지표"/);
  assert.match(html, /NDCG의 절대 점수 차이/);
  assert.match(html, /Hit Rate의 퍼센트포인트 차이/);
  assert.match(html, /\+60\.0%p/);
});

test('missing cohort renders unknown without a zero-width fabricated bar', () => {
  const data = fixture(); data.models.forEach(model => { model.scores = model.scores.slice(0, 1); });
  data.cohorts = data.cohorts.slice(0, 1);
  const html = render({ status: 'ready', data });
  assert.match(html, /aria-label="신규 평가 없음">—/);
  assert.match(html, /data-model-id="v05" data-audience="new" role="img" aria-label="v0.5 신규: 평가 없음"/);
  assert.doesNotMatch(html, /data-model-id="v05" data-audience="new" data-value=/);
});

test('audience selection applies to graph, summary and numeric rows', () => {
  const html = render({ status: 'ready', data: fixture() }, 'new');
  assert.equal((html.match(/data-model-id=/g) || []).length, 2);
  assert.doesNotMatch(html, /data-audience="existing"/);
  assert.match(html, /value="new" selected=""/);
  assert.match(html, /href="\/admin\/analytics\?audience=new"/);
});

test('unavailable result shows reason and no model chart or numerical zero', () => {
  const html = render({ status: 'ready', data: { ...fixture(), availability: 'unavailable', reason: 'outside_period', models: [], cohorts: [], conditions: null } });
  assert.match(html, /선택한 기간에 완료된 평가 결과가 없습니다/);
  assert.match(html, /—는 0점이 아닌 확인할 수 없는 값/);
  assert.doesNotMatch(html, /data-performance-chart|data-model-id|0\.0000|0\.0%/);
  assert.match(html, /다시 확인/);
});

test('authentication and request failures remove scores and preserve recovery UI', () => {
  for (const status of ['loading', 'login-required', 'forbidden', 'unavailable', 'error']) {
    const html = render(status === 'loading' ? { status } : { status, message: 'Request failed' });
    assert.match(html, new RegExp(`data-admin-state="${status}"`));
    assert.doesNotMatch(html, /data-performance-chart|data-model-id|0\.5000/);
    if (status === 'login-required') assert.match(html, /href="\/pivot\/login"/);
  }
});

test('evaluation text is escaped and long conditions stay plain text', () => {
  const data = fixture(); data.conditions.recordSource = '<script>alert(1)</script>';
  const html = render({ status: 'ready', data });
  assert.match(html, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>alert/);
});
