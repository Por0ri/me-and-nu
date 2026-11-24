const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm'), crypto = require('node:crypto');
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
    if (name === 'next/navigation' || name === './use-admin-read') return {};
    if (name === '@/lib/api' || name === './api') return { ApiError: class ApiError extends Error {}, apiRequest() { throw new Error('Static view must not fetch'); } };
    if (name === '@/lib/admin-statistics-api') return {};
    if (name.endsWith('.module.css')) return { __esModule: true, default: new Proxy({}, { get: (_, key) => String(key) }) };
    const next = name.startsWith('@/') ? name.slice(2) : path.join(path.dirname(file), name);
    return load(next + (fs.existsSync(path.join(root, next + '.tsx')) ? '.tsx' : '.ts'));
  };
  vm.runInNewContext(`(function(module,exports,require){${code}\n})`, { URL, URLSearchParams, Intl })(mod, mod.exports, localRequire);
  return mod.exports;
}
const model = load('lib/admin-analytics.ts');
const parse = value => model.parseAnalyticsQuery(new URLSearchParams(value));
const render = (file, name, props) => renderToStaticMarkup(React.createElement(load(`components/admin/${file}.tsx`)[name], props));
const metric = (value, unit = 'count') => ({ availability: value === null ? 'unavailable' : 'available', reason: value === null ? 'not_collected' : null, value, unit, numerator: null, denominator: null, definition: 'Test observation definition' });
function fixture() {
  const distribution = { availability: 'partial', reason: 'current_answers_not_historical_snapshot', sampleSize: 4, definition: 'Current answers', items: [{ key: 'ost', label: '관련 OST', count: 3, percentage: 75 }, { key: 'other', label: '기타', count: 1, percentage: 25 }] };
  const ranking = { availability: 'available', reason: null, definition: 'Observed read segments', items: [{ rank: 1, label: 'Fixture content', count: 21, contentId: 101 }] };
  const meta = { generatedAt: '2026-11-21T00:00:00Z', window: { period: '7d', startAt: '2026-11-14T00:00:00Z', endAt: '2026-11-21T00:00:00Z', timezone: 'UTC' }, eventDataSource: 'local_demo', scope: 'connected_database', limitations: [] };
  const usage = { activeUsers: metric(4), signupFeedObservationRate: metric(100, 'percent'), contentReadSegments: metric(21) };
  const attention = { availability: 'partial', reason: 'notice_workflow_not_implemented', knownTotal: 9, reports: 7, inquiries: 2, scheduledNotices: metric(null), page: 1, pageSize: 5, items: [{ kind: 'report', recordId: 1, status: 'received', category: 'wrong_info', createdAt: '2026-11-20T00:00:00Z', contentId: 101 }] };
  return {
    overview: { meta, usage, preferenceDistribution: distribution, deviceDistribution: { availability: 'unavailable', reason: 'not_collected', sampleSize: null, items: [] }, funnel: [{ key: 'signupStarted', label: '가입 시작', metric: metric(null) }, { key: 'accountCreated', label: '계정 생성', metric: metric(4) }], topContents: ranking, popularSearches: { ...ranking, items: [] }, zeroResultSearches: { ...ranking, items: [] }, averageSessionDuration: metric(null, 'seconds'), returningUserRate: metric(0, 'percent') },
    dashboard: { meta, usage, topContents: ranking, preferenceDistribution: distribution, attention, todayNotices: metric(null), generation: { availability: 'partial', reason: 'importer_does_not_record_all_live_generation_attempts', succeeded: 1, inProgress: 0, failed: 0, cancelled: 0 }, recommendationPerformance: { availability: 'available', currentModelId: 'v05', models: [{ modelId: 'v05', scores: [{ audience: 'existing', ndcgAt10: 0.5436 }, { audience: 'new', ndcgAt10: 0 }] }] } }, attention,
  };
}
function dashboard(state, filter = 'all', page = 1) {
  return render('dashboard-screen', 'DashboardView', { period: '30d', changePeriod() {}, state, retry() {}, filter, page, changeFilter() {}, changePage() {} });
}
function overview(state) { return render('analytics-screen', 'AnalyticsOverviewView', { query: parse('period=30d&audience=new'), navigate() {}, state, retry() {} }); }

test('analytics query normalizes malformed input and preserves supported filters in links', () => {
  for (const input of ['', 'view=bad&period=all&audience=admin', 'period=__proto__&audience=constructor']) {
    assert.equal(JSON.stringify(parse(input)), JSON.stringify({ view: 'overview', period: '7d', audience: 'all' }));
  }
  for (const period of ['7d', '30d', '3m', '6m']) for (const audience of ['all', 'existing', 'new']) {
    const query = parse(`view=performance&period=${period}&audience=${audience}`), href = model.analyticsHref(query);
    assert.equal(new URL(href, 'http://localhost').pathname, '/admin/analytics');
    assert.equal(JSON.stringify(parse(href.split('?')[1])), JSON.stringify(query));
  }
  assert.equal(model.analyticsHref(parse('')), '/admin/analytics');
  const service = parse('view=performance&period=30d&audience=new&dataset=service');
  assert.equal(service.dataset, 'service');
  assert.equal(JSON.stringify(parse(model.analyticsHref(service).split('?')[1])), JSON.stringify(service));
  assert.equal(parse('dataset=offline').dataset, undefined);
  assert.equal(parse('dataset=../../etc/passwd').dataset, undefined);
});

test('overview presents measured data, empty results and missing metrics with distinct semantics', () => {
  const html = overview({ status: 'ready', data: fixture().overview });
  for (const label of ['온보딩 취향 유형 분포', '이용 기기 비중', '회원가입 전환 퍼널', '열람 구간 TOP 5 콘텐츠', '인기 검색 키워드 TOP5', '검색 결과 없는 검색어 TOP5', '평균 세션 지속 시간', '기간 간 재방문 관측률']) assert.ok(html.includes(label), label);
  assert.match(html, /href="\/admin\/analytics\?view=performance&amp;period=30d&amp;audience=new"/);
  assert.match(html, /현재 취향 답변 4개/); assert.match(html, /75\.0%/);
  assert.match(html, /Fixture content/); assert.match(html, /21회/);
  assert.match(html, /선택한 기간에 기록이 없습니다/);
  assert.match(html, /평균 세션 지속 시간: 집계 준비 중/);
  assert.match(html, /기간 간 재방문 관측률: 0%/);
  assert.match(html, /서비스·테스트 공유 기록/); assert.match(html, /종료 시각 제외/);
  assert.doesNotMatch(html, /18,240|184,320|76\.9%|64\.2%|16분 24초/);
});

test('dashboard shows supplied counts and separate recommendation scenarios with a filtered detail link', () => {
  const data = fixture(), html = dashboard({ status: 'ready', data });
  for (const label of ['이용 회원: 4', '온보딩 완료 후 피드 노출 관측률: 100%', '콘텐츠 열람 구간 수: 21', '작품 추천 품질', '에이전트 콘텐츠 생성', '오늘의 알림 및 공지']) assert.ok(html.includes(label), label);
  assert.match(html, /오프라인 평가/);
  assert.match(html, /0\.5436/); assert.match(html, /0\.0000/);
  assert.match(html, /href="\/admin\/analytics\?view=performance&amp;period=30d"/);
  assert.match(html, /신고 #1/); assert.match(html, /콘텐츠 확인/);
  assert.match(html, /1 \/ 2/); assert.match(html, /disabled="" aria-label="이전 페이지"/);
  assert.doesNotMatch(html, /disabled="" aria-label="다음 페이지"/);
  assert.match(html, /전체 건수는 확인 가능한 신고·문의 합계/);
});

test('unsupported notice queue is unavailable rather than an empty successful queue', () => {
  const data = fixture(); data.attention = { ...data.attention, availability: 'unavailable', knownTotal: 0, items: [] };
  const html = dashboard({ status: 'ready', data }, 'notice');
  assert.match(html, /예약 공지 연결을 준비/); assert.doesNotMatch(html, /미처리 예약 공지 항목이 없습니다/);
  assert.match(html, /disabled="" aria-label="다음 페이지"/);
});

test('service dashboard summary stays separate and labels retained trial scores without a pipeline manifest', () => {
  const data = fixture();
  data.dashboard.serviceRecommendationPerformance = {
    ...data.dashboard.recommendationPerformance, source: 'service_feedback', dataSource: 'local_demo', pipeline: null,
  };
  const html = dashboard({ status: 'ready', data });
  assert.ok(html.indexOf('서비스 추천 품질') < html.indexOf('지금 확인이 필요한 항목'));
  assert.ok(html.includes('작품 추천 품질'));
  assert.match(html, /Checking 시험 데이터/);
  assert.match(html, /콘텐츠 NDCG@10/);
  assert.match(html, /href="\/admin\/analytics\?view=performance&amp;period=30d&amp;dataset=service"/);
  assert.match(html, /href="\/admin\/analytics\?view=performance&amp;period=30d"/);
});

test('a shrinking queue does not present an invalid page or a false empty backlog', () => {
  const data = fixture(); data.attention = { ...data.attention, knownTotal: 2, items: [], page: 2 };
  const html = dashboard({ status: 'ready', data }, 'all', 2);
  assert.doesNotMatch(html, /2 \/ 1|미처리 전체 항목이 없습니다/);
  assert.match(html, /불러오는 중/);
});

test('pending and failed reads render no previous protected counts or chart values', () => {
  for (const status of ['loading', 'login-required', 'forbidden', 'unavailable', 'error']) for (const view of [dashboard, overview]) {
    const html = view(status === 'loading' ? { status } : { status, message: 'Request failed' });
    assert.match(html, new RegExp(`data-admin-state="${status}"`));
    assert.doesNotMatch(html, /Fixture content|0\.5436|data-analytics-scope|conic-gradient/);
    if (status === 'login-required') assert.match(html, /href="\/pivot\/login"/);
  }
});

test('shared metric cards preserve observed zero and distinguish a zero denominator', () => {
  const zero = render('statistics-panels', 'MetricCard', { title: '관측 수', metric: metric(0) });
  assert.match(zero, /aria-label="관측 수: 0">0/);
  const absent = render('statistics-panels', 'MetricCard', { title: '비율', metric: { ...metric(null, 'percent'), reason: 'empty_denominator', numerator: 0, denominator: 0 } });
  assert.match(absent, /비율: 집계 준비 중">—/); assert.match(absent, /비율을 계산할 대상이 없어요/); assert.doesNotMatch(absent, /0%/);
  const noAnswers = render('statistics-panels', 'PreferenceDistribution', { data: { availability: 'unavailable', reason: 'no_samples', sampleSize: 0, items: [] } });
  assert.doesNotMatch(noAnswers, /conic-gradient|[0-9]+%/); assert.match(noAnswers, /응답 표본이 없어요/);
});

test('Figma SVG sources remain intact with their natural root dimensions', () => {
  const manifest = require('../public/admin/statistics-sources.json'); assert.equal(manifest.assets.length, 14);
  for (const asset of manifest.assets) {
    const bytes = fs.readFileSync(path.join(root, 'public/admin', asset.file)); assert.ok(bytes.length);
    assert.equal(crypto.createHash('sha256').update(bytes).digest('hex'), asset.sha256, asset.file);
    const tag = bytes.toString().match(/<svg[^>]+>/)[0];
    assert.equal(Number(tag.match(/width="([^"]+)"/)[1]), asset.width);
    assert.equal(Number(tag.match(/height="([^"]+)"/)[1]), asset.height);
  }
});
