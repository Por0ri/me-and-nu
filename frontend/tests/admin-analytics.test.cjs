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
    if (name === 'next/navigation') return {};
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
const screen = query => render('analytics-screen', 'AnalyticsScreen', { query: parse(query), navigate() {} });

test('analytics query accepts only design options and normalizes malformed input', () => {
  for (const input of ['', 'view=bad&period=all&audience=admin', 'period=__proto__&audience=constructor']) {
    assert.equal(JSON.stringify(parse(input)), JSON.stringify({ view: 'overview', period: '7d', audience: 'all' }));
  }
  for (const period of ['7d', '30d', '3m', '6m']) for (const audience of ['all', 'existing', 'new']) {
    const query = parse(`view=performance&period=${period}&audience=${audience}`);
    const href = model.analyticsHref(query);
    assert.equal(new URL(href, 'http://localhost').pathname, '/admin/analytics');
    assert.equal(JSON.stringify(parse(href.split('?')[1])), JSON.stringify(query));
  }
  assert.equal(model.analyticsHref(parse('')), '/admin/analytics');
});

test('overview preserves Figma sections while missing aggregates never become sample data or zero', () => {
  const html = screen('');
  for (const label of ['온보딩 취향 유형 분포', '이용 기기 비중', '회원가입 전환 퍼널', '조회 수 TOP 5 콘텐츠', '인기 검색 키워드 TOP5', '검색 결과 없는 검색어 TOP5', '평균 세션 지속 시간', '재방문율 (리텐션)']) assert.ok(html.includes(label), label);
  assert.match(html, /집계 준비 중/); assert.match(html, /href="\/admin\/analytics\?view=performance"/);
  assert.doesNotMatch(html, /18,240|184,320|76\.9%|64\.2%|16분 24초|약사의 혼잣말|0명|0회/);
  assert.equal((html.match(/data-statistics="unavailable"/g) || []).length, 8);
});

test('performance uses the same route, retains selections, and does not assert unverified evaluation conditions', () => {
  const html = screen('view=performance&period=30d&audience=new');
  assert.match(html, /href="\/admin\/analytics\?period=30d&amp;audience=new"/);
  assert.match(html, /value="new" selected=""/);
  assert.match(html, /상세 수치 보기/); assert.match(html, /NDCG@10은 어떻게 읽나요/);
  assert.doesNotMatch(html, /0\.1667|33\.10%|16,357|EXP-32|MAL 공개|2020.01|5,000|0건/);
  assert.equal((html.match(/<dd>—<\/dd>/g) || []).length, 8);
  assert.equal((html.match(/<td>—<\/td>/g) || []).length, 12);
  assert.equal((html.match(/<details open=""/g) || []).length, 2);
});

test('dashboard counts, ranked content, attention queue and schedules stay unavailable', () => {
  const html = render('dashboard-screen', 'DashboardScreen');
  for (const label of ['이용 회원', '가입 후 피드 진입률', '콘텐츠 조회 수', '작품 추천 품질', '지금 확인이 필요한 항목', '에이전트 콘텐츠 생성', '오늘의 알림 및 공지']) assert.ok(html.includes(label), label);
  assert.match(html, /전체 항목 집계 준비 중/);
  assert.doesNotMatch(html, /50명|64%|11,578|0\.1667|8건|2건|1건|11월 12일|10:27|시스템 점검|0건/);
  assert.equal((html.match(/disabled=""/g) || []).length, 3);
});

test('shared charts carry unavailable semantics without filled proportions', () => {
  const html = render('statistics-panels', 'PreferenceDistribution');
  assert.match(html, /응답자: 집계 준비 중/);
  assert.doesNotMatch(html, /progressbar|conic-gradient|[0-9]+%/);
});

test('new route dependency graphs exclude fixtures, API calls and persistence', () => {
  const visited = new Set();
  function inspect(file) {
    file = file.replaceAll('\\', '/'); if (visited.has(file)) return; visited.add(file);
    const source = fs.readFileSync(path.join(root, file), 'utf8');
    assert.doesNotMatch(source, /tests\/fixtures|fixture=|fetch\(|localStorage|sessionStorage|consumer-api|useAdminRead|\/api\/v1/);
    const ast = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true);
    for (const statement of ast.statements) {
      if (!ts.isImportDeclaration(statement) || statement.importClause?.isTypeOnly) continue;
      const name = statement.moduleSpecifier.text;
      if (!name.startsWith('.') && !name.startsWith('@/') || name.endsWith('.css')) continue;
      const next = name.startsWith('@/') ? name.slice(2) : path.join(path.dirname(file), name);
      inspect(next + (fs.existsSync(path.join(root, next + '.tsx')) ? '.tsx' : '.ts'));
    }
  }
  inspect('app/admin/analytics/page.tsx'); inspect('app/admin/dashboard/page.tsx');
  assert.ok(visited.has('components/admin/content-statistics.tsx'));
  assert.ok(visited.has('components/admin/report-period-filter.tsx'));
  assert.equal(fs.existsSync(path.join(root, 'app/admin/analytics/performance')), false);
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
