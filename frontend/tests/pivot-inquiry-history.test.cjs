const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const root = path.join(__dirname, '..');
const at = '2026-11-23T04:00:00.123456Z';
const row = (id = '9007199254740993') => ({ inquiryId: id, inquiryType: 'bug', status: 'answered', createdAt: at,
  updatedAt: at, preview: '내 문의', answerAvailability: 'available' });
const detail = id => ({ ...row(id), detail: '<script>private question</script>', answer: '<img src=x onerror=bad()>\n운영자 답변', answeredAt: at });
const page = () => ({ items: [row()], page: 1, pageSize: 10, total: 1 });
const session = id => ({ authenticated: true, sessionState: 'active', onboardingCompleted: true, user: { id, accountType: 'user' }, csrfToken: 'csrf' });
function setup(respond = url => url.pathname.endsWith('/inquiries') ? page() : detail('9007199254740993')) {
  const modules = new Map(), calls = []; let current = session(7), cookie = current;
  const browser = new EventTarget(); browser.location = { origin: 'http://localhost:3194' };
  const context = vm.createContext({ AbortController, URL, URLSearchParams, Headers, Response, Event, setTimeout, clearTimeout,
    window: browser, process: { env: { NEXT_PUBLIC_API_BASE_URL: 'http://localhost:8194', NEXT_PUBLIC_PIVOT_ENABLE_LOCAL_API: 'true' } },
    fetch: async (url, init) => {
      calls.push({ url, init }); const parsed = new URL(url);
      if (parsed.pathname.endsWith('/auth/session')) return new Response(JSON.stringify(cookie));
      const result = await respond(parsed, init);
      return result instanceof Response ? result : new Response(JSON.stringify(result));
    },
  });
  function load(file) {
    file = file.replaceAll('\\', '/'); if (modules.has(file)) return modules.get(file).exports;
    const mod = { exports: {} }; modules.set(file, mod);
    const code = ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), { compilerOptions: {
      module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
    } }).outputText;
    const req = name => {
      if (name === 'react' || name === 'react/jsx-runtime') return require(name);
      if (name.endsWith('.module.css')) return { default: new Proxy({}, { get: (_, key) => String(key) }), __esModule: true };
      const relative = name.startsWith('@/') ? name.slice(2) : path.join(path.dirname(file), name);
      return load(relative + (fs.existsSync(path.join(root, relative + '.tsx')) ? '.tsx' : '.ts'));
    };
    vm.runInContext(`(function(module,exports,require){${code}\n})`, context)(mod, mod.exports, req);
    return mod.exports;
  }
  const history = load('lib/pivot/inquiry-history.ts');
  const source = load('lib/pivot/inquiry-service.ts').createServiceInquirySource(() => current).history;
  const states = [], control = history.createInquiryHistoryController(source, state => states.push(state));
  return { load, calls, history, source, states, control, cookie(next) { cookie = next; }, change(next) { current = next; } };
}

test('history GETs use current cookie owner, no-store and string bigint IDs without sending CSRF or content', async () => {
  const env = setup(); await env.control.load(); await env.control.select('9007199254740993');
  assert.equal(env.states.at(-1).detail.inquiryId, '9007199254740993');
  assert.equal(env.states.at(-1).detail.answer, detail().answer);
  const reads = env.calls.filter(call => !call.url.endsWith('/auth/session'));
  assert.equal(reads.length, 2);
  for (const { url, init } of reads) {
    assert.equal(init.credentials, 'include'); assert.equal(init.cache, 'no-store'); assert.equal(init.body, undefined);
    assert.equal(init.headers.has('X-CSRF-Token'), false); assert.doesNotMatch(url, /내 문의|private/);
    assert.equal(init.headers.get('X-Inquiry-Actor-User-Id'), '7');
  }
  assert.match(reads[1].url, /\/inquiries\/9007199254740993$/);
});

test('cookie account switch and local source change prevent private reads and clear all state', async () => {
  for (const mutate of [env => env.cookie(session(9)), env => env.change(session(9))]) {
    const env = setup(); await env.control.load(); mutate(env); await env.control.select('9007199254740993');
    assert.equal(env.states.at(-1).phase, 'unavailable'); assert.equal(env.states.at(-1).items.length, 0);
    assert.equal(env.states.at(-1).detail, null);
    assert.equal(env.calls.filter(call => /\/inquiries\/\d/.test(call.url)).length, 0);
  }
});

test('late detail cannot overwrite newer selection or restore data after leaving', async () => {
  const env = setup(); let resolveA, resolveB;
  const a = new Promise(resolve => resolveA = resolve), b = new Promise(resolve => resolveB = resolve);
  const states = []; const control = env.history.createInquiryHistoryController({
    list: async () => ({ items: [row('1'), row('2')], page: 1, pageSize: 10, total: 2 }),
    detail: id => id === '1' ? a : b,
  }, state => states.push(state));
  await control.load(); const first = control.select('1'), second = control.select('2');
  resolveB(detail('2')); await second; resolveA(detail('1')); await first;
  assert.equal(states.at(-1).detail.inquiryId, '2');
  const pending = control.select('1'); control.cancel(); const count = states.length; await pending;
  assert.equal(states.length, count);
});

test('401 and 403 clear prior history, while 503 displays an error instead of a fake empty state', async () => {
  for (const status of [401, 403, 503]) {
    let fail = false; const env = setup(url => fail ? new Response(JSON.stringify({ code: 'DENIED', message: 'SERVER_PRIVATE' }), { status }) : page());
    await env.control.load(); fail = true; await env.control.load();
    const last = env.states.at(-1); assert.equal(last.phase, status === 503 ? 'error' : 'unavailable');
    assert.equal(last.items.length, 0); assert.doesNotMatch(last.message, /SERVER_PRIVATE|없습니다/);
  }
});

test('invalid detail IDs, answer combinations and timestamps are rejected', async () => {
  for (const patch of [{ inquiryId: 9007199254740992 }, { inquiryId: '8' }, { answer: null }, { answeredAt: '2026-11-23' },
    { answerAvailability: 'none', answer: 'unexpected' }]) {
    const env = setup(url => url.pathname.endsWith('/inquiries') ? page() : { ...detail('9007199254740993'), ...patch });
    await env.control.load(); await env.control.select('9007199254740993');
    assert.equal(env.states.at(-1).detailPhase, 'error'); assert.equal(env.states.at(-1).detail, null);
  }
});

test('reply text is escaped and legacy missing answers stay distinguishable from waiting', () => {
  const env = setup(); const { InquiryHistoryPanel } = env.load('components/pivot/inquiry-history-panel.tsx');
  const state = { ...env.history.initialInquiryHistory(), phase: 'ready', items: [row()], selectedId: row().inquiryId, detailPhase: 'ready', detail: detail(row().inquiryId), total: 1 };
  const render = state => renderToStaticMarkup(React.createElement(InquiryHistoryPanel, { state, onLoad() {}, onSelect() {} }));
  const html = render(state); assert.match(html, /운영자 답변/); assert.match(html, /&lt;script&gt;/); assert.match(html, /&lt;img/);
  assert.doesNotMatch(html, /<script>|<img src=x/);
  assert.match(render({ ...state, detail: { ...state.detail, answerAvailability: 'unavailable', answer: null, answeredAt: null } }), /이전 답변 내용은 저장되어 있지/);
  assert.match(render({ ...state, detail: { ...state.detail, status: 'received', answerAvailability: 'none', answer: null, answeredAt: null } }), /아직 답변을 기다리고/);
});
