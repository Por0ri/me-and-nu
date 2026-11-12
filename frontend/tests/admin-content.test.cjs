const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const root = path.join(__dirname, '..');
const plain = value => JSON.parse(JSON.stringify(value));
const item = (id = 2) => ({ contentId: id, title: '달빛 콘텐츠', judgmentStatus: 'needs_review', status: 'active',
  createdAt: '2026-11-12T00:00:00Z', publishedAt: null });
const page = (items = [item()]) => ({ items, page: 1, pageSize: 20, total: items.length });
function runtime(respond = () => page()) {
  const modules = new Map(), calls = [];
  const browser = new EventTarget();
  const context = vm.createContext({ AbortController, URL, URLSearchParams, Headers, Response, Event, window: browser,
    Intl, process: { env: { NEXT_PUBLIC_API_BASE_URL: 'http://localhost:8196' } },
    fetch: async (url, init) => {
      calls.push({ url, init }); const value = await respond(new URL(url), init);
      return value instanceof Response ? value : new Response(JSON.stringify(value));
    },
  });
  function load(file) {
    file = file.replaceAll('\\', '/');
    if (modules.has(file)) return modules.get(file).exports;
    const mod = { exports: {} }; modules.set(file, mod);
    const js = ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), { compilerOptions: {
      module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
    } }).outputText;
    const requireModule = name => {
      if (name === 'react' || name === 'react/jsx-runtime') return require(name);
      if (name === 'next/link') return { __esModule: true, default: props => React.createElement('a', props) };
      if (name === 'next/navigation') return {};
      if (name.endsWith('.module.css')) return { __esModule: true, default: new Proxy({}, { get: (_, key) => String(key) }) };
      const relative = name.startsWith('@/') ? name.slice(2) : path.join(path.dirname(file), name);
      return load(relative + (fs.existsSync(path.join(root, relative + '.tsx')) ? '.tsx' : '.ts'));
    };
    vm.runInContext(`(function(module,exports,require){${js}\n})`, context)(mod, mod.exports, requireModule);
    return mod.exports;
  }
  return { load, calls, browser, api: load('lib/admin-content.ts') };
}

test('no implicit queue filter; URL preserves independent states and page size', () => {
  const { api } = runtime();
  assert.deepEqual(plain(api.parseAdminQuery(new URLSearchParams())), { q: '', judgmentStatus: '', status: '', page: 1, pageSize: 20 });
  const query = api.parseAdminQuery(new URLSearchParams('q=+달빛%25_+&judgmentStatus=needs_review&status=active&page=2&pageSize=50'));
  assert.equal(query.q, '달빛%_');
  assert.deepEqual(plain(api.parseAdminQuery(new URLSearchParams(api.adminQueryString(query)))), plain(query));
  for (const value of ['page=0', 'page=-1', 'page=1.2', 'page=1000001', 'pageSize=101', 'status=published', 'judgmentStatus=approved', 'q=' + 'a'.repeat(301)]) {
    assert.throws(() => api.parseAdminQuery(new URLSearchParams(value)));
  }
});

test('list uses existing credentialed no-store API client and exact DTO fields', async () => {
  const env = runtime(); const query = env.api.parseAdminQuery(new URLSearchParams('q=달빛%25_&judgmentStatus=needs_review&status=active'));
  const signal = new AbortController().signal;
  assert.deepEqual(plain(await env.api.loadAdminContents(query, signal)), page());
  const { url, init } = env.calls[0]; const parsed = new URL(url);
  assert.equal(parsed.pathname, '/api/v1/admin/contents');
  assert.equal(parsed.searchParams.get('q'), '달빛%_');
  assert.equal(parsed.searchParams.get('judgmentStatus'), 'needs_review');
  assert.equal(parsed.searchParams.get('status'), 'active');
  assert.equal(parsed.searchParams.get('pageSize'), '20');
  assert.equal(init.credentials, 'include'); assert.equal(init.cache, 'no-store'); assert.equal(init.signal, signal);
});

test('successful empty result is distinct from API failure, login, permission and disconnected routes', async () => {
  const empty = runtime(() => page([]));
  assert.deepEqual(plain(await empty.api.loadAdminContents(empty.api.parseAdminQuery(new URLSearchParams()))), page([]));
  for (const [status, code, expected] of [[401, 'AUTH_REQUIRED', 'login-required'], [403, 'ADMIN_REQUIRED', 'forbidden'],
    [404, 'NOT_FOUND', 'unavailable'], [404, 'CONTENT_NOT_FOUND', 'not-found'], [500, 'PRIVATE_DETAIL', 'error']]) {
    const env = runtime(() => new Response(JSON.stringify({ code, message: 'internal secret' }), { status }));
    await assert.rejects(env.api.loadAdminContents(env.api.parseAdminQuery(new URLSearchParams())), error => {
      const failure = env.api.adminFailure(error); assert.equal(failure.status, expected); assert.doesNotMatch(failure.message, /secret/); return true;
    });
  }
});

test('invalid wire data cannot become a successful list', async () => {
  for (const response of [null, {}, { ...page(), total: -1 }, { ...page(), page: 2 }, { ...page(), pageSize: 50 },
    page([item(), item()]), page([{ ...item(), status: 'published' }]), page([{ ...item(), judgmentStatus: 'approved' }]),
    page([{ ...item(), createdAt: 'bad date' }]), page([{ ...item(), contentId: Number.MAX_SAFE_INTEGER + 1 }])]) {
    const env = runtime(() => response);
    await assert.rejects(env.api.loadAdminContents(env.api.parseAdminQuery(new URLSearchParams())), error => error.code === 'INVALID_ADMIN_RESPONSE');
  }
});

test('detail ID and source fields are validated, unsafe source URLs cannot be links', async () => {
  const detail = { ...item(), summary: null, body: '<script>not executable</script>', sources: [
    { url: 'https://example.com/source', title: null, publisher: null, role: 'primary', citationOrder: 1 },
  ] };
  const env = runtime(() => detail);
  assert.deepEqual(plain(await env.api.loadAdminContent(2)), detail);
  await assert.rejects(env.api.loadAdminContent(3), error => error.code === 'INVALID_ADMIN_RESPONSE');
  assert.equal(env.api.safeSourceUrl('https://example.com/source'), 'https://example.com/source');
  for (const url of ['javascript:alert(1)', 'data:text/html,x', 'https://name:password@example.com', '/relative']) assert.equal(env.api.safeSourceUrl(url), undefined);
});

test('an aborted read suppresses a late 401 event', async () => {
  let finish; const waiting = new Promise(resolve => finish = resolve);
  const env = runtime(() => waiting), controller = new AbortController(); let unauthorized = 0;
  env.browser.addEventListener('api:unauthorized', () => unauthorized++);
  const result = env.api.loadAdminContents(env.api.parseAdminQuery(new URLSearchParams()), controller.signal);
  controller.abort(); finish(new Response('{}', { status: 401 }));
  await assert.rejects(result); assert.equal(unauthorized, 0);
});

test('screen separates loading/error/empty and preserves both status values and detail return query', () => {
  const env = runtime(); const { ContentsScreen } = env.load('components/admin/contents-screen.tsx');
  const query = env.api.parseAdminQuery(new URLSearchParams('q=달빛&status=active'));
  const render = state => renderToStaticMarkup(React.createElement(ContentsScreen, { query, state, retry() {}, navigate() {} }));
  assert.match(render({ status: 'loading' }), /불러오는 중/);
  const error = render({ status: 'error', message: '네트워크 오류' });
  assert.match(error, /다시 시도/); assert.doesNotMatch(error, /조회된 콘텐츠가 없습니다|data-admin-empty/);
  assert.match(render({ status: 'ready', data: page([]) }), /조회된 콘텐츠가 없습니다/);
  assert.match(render({ status: 'login-required', message: '로그인 필요' }), /href="\/pivot\/login"/);
  assert.match(render({ status: 'forbidden', message: '관리자 권한 없음' }), /접근 권한이 없습니다/);
  assert.match(render({ status: 'unavailable', message: '연결 준비' }), /조회 연결 준비 중/);
  const html = render({ status: 'ready', data: page([{ ...item(), title: '<script>x</script>' }]) });
  assert.match(html, /needs_review<\/span>/); assert.match(html, /active<\/span>/);
  assert.match(html, /href="\/admin\/contents\/2\?q=/); assert.match(html, /&amp;status=active/);
  assert.match(html, /&lt;script&gt;x&lt;\/script&gt;/); assert.doesNotMatch(html, /<script>x/);
  assert.match(html, /09:00/);
});
