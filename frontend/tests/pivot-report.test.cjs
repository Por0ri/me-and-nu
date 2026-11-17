const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const root = path.join(__dirname, '..');
const session = (id = 7) => ({ authenticated: true, sessionState: 'active', onboardingCompleted: true, user: { id, accountType: 'user' }, csrfToken: `csrf-${id}` });
const deferred = () => { let resolve; const promise = new Promise(yes => { resolve = yes; }); return { promise, resolve }; };
const response = (body = { reportId: 42, status: 'received' }, status = 201) => new Response(JSON.stringify(body), { status });
function setup(respond = () => response(), readSession) {
  const modules = new Map(), calls = [], events = [];
  let current = session(), cookieSession = current;
  const browser = new EventTarget(); browser.location = { origin: 'http://localhost:3194' };
  browser.addEventListener('api:unauthorized', () => events.push('401'));
  const context = vm.createContext({ AbortController, URL, URLSearchParams, Headers, Response, Event, window: browser,
    process: { env: { NEXT_PUBLIC_API_BASE_URL: 'http://localhost:8194', NEXT_PUBLIC_PIVOT_ENABLE_LOCAL_API: 'true' } },
    fetch: async (url, init) => {
      calls.push({ url, init });
      if (new URL(url).pathname.endsWith('/auth/session')) return readSession ? readSession() : new Response(JSON.stringify(cookieSession));
      return respond(new URL(url), init);
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
      if (name.endsWith('.module.css')) return { default: new Proxy({}, { get: (_, key) => String(key) }), __esModule: true };
      const relative = name.startsWith('@/') ? name.slice(2) : path.join(path.dirname(file), name);
      return load(relative + (fs.existsSync(path.join(root, relative + '.tsx')) ? '.tsx' : '.ts'));
    };
    vm.runInContext(`(function(module,exports,require){${js}\n})`, context)(mod, mod.exports, requireModule);
    return mod.exports;
  }
  const report = load('lib/pivot/report.ts');
  const source = load('lib/pivot/report-service.ts').createServiceReportSource(() => current);
  const states = [];
  const control = report.createReportController(source, false, 17, state => states.push(state));
  return { load, calls, events, states, source, report, control, change(next) { current = next; }, cookie(next) { cookieSession = next; },
    posts() { return calls.filter(call => call.init.method === 'POST'); },
    fill(detail = '  신고 내용입니다.  ', reasonCode = 'other') { control.change({ detail, reasonCode }); },
  };
}

for (const reason of ['wrong_info', 'spoiler', 'rights', 'inappropriate', 'other']) test(`${reason}: optional detail, exact content identity, cookies/CSRF and one receipt`, async () => {
  for (const detail of ['', '  ', '내용', '😀'.repeat(500)]) {
    const env = setup(); env.fill(detail, reason); await env.control.submit();
    assert.equal(env.states.at(-1).status, 'received'); assert.equal(env.posts().length, 1);
    const { url, init } = env.posts()[0];
    assert.equal(new URL(url).pathname, '/api/v1/reports');
    assert.deepEqual(JSON.parse(init.body), { targetType: 'content', targetId: 17, reasonCode: reason, ...(detail.trim() ? { detail: detail.trim() } : {}) });
    assert.equal(init.credentials, 'include'); assert.equal(init.headers.get('X-CSRF-Token'), 'csrf-7');
    await env.control.submit(); assert.equal(env.posts().length, 1);
  }
});
test('invalid reason, target and 501 code points never send', async () => {
  const env = setup();
  for (const draft of [{ reasonCode: null, detail: '' }, { reasonCode: 'invalid', detail: '' }, { reasonCode: 'other', detail: '😀'.repeat(501) }]) {
    env.control.change(draft); await env.control.submit(); assert.equal(env.states.at(-1).status, 'error');
  }
  for (const target of [0, NaN, 1.5, -2]) {
    const c = env.report.createReportController(env.source, false, target, () => {}); c.change({ reasonCode: 'other' }); await c.submit();
  }
  assert.equal(env.calls.length, 0);
});
test('double click and a second controller cannot write twice', async () => {
  const sent = deferred(), gate = deferred();
  const env = setup(async () => { sent.resolve(); await gate.promise; return response(); });
  env.fill(); const task = env.control.submit(); void env.control.submit(); await sent.promise;
  const other = env.report.createReportController(env.source, false, 17, () => {}); other.change({ reasonCode: 'spoiler' }); await other.submit();
  gate.resolve(); await task; assert.equal(env.posts().length, 1);
});
for (const [failure, expected] of [['409', 'duplicate'], ['404', 'missing'], ['401', 'unavailable'], ['403', 'unavailable'], ['csrf', 'error'], ['422', 'error'], ['503', 'uncertain'], ['408', 'uncertain'], ['network', 'uncertain'], ['malformed', 'uncertain'], ['200', 'uncertain'], ['lost-body', 'uncertain']]) test(`${failure}: distinct state, no completion or automatic retry`, async () => {
  const env = setup(() => {
    if (failure === 'network') throw new TypeError('lost');
    if (failure === 'lost-body') return new Response('truncated', { status: 201 });
    if (failure === 'malformed') return response({ status: 'received' });
    if (failure === '200') return response({ reportId: 42, status: 'received' }, 200);
    return response({ code: failure === 'csrf' ? 'CSRF_INVALID' : 'ERROR', message: 'private' }, failure === 'csrf' ? 403 : Number(failure));
  });
  env.fill(); await env.control.submit();
  assert.equal(env.states.at(-1).status, expected); assert.doesNotMatch(env.states.at(-1).message, /private|정상적으로/);
  assert.equal(env.posts().length, 1);
  if (expected === 'uncertain' || expected === 'duplicate') {
    env.control.change({ detail: 'changed' }); await env.control.submit();
    const states = [], c = env.report.createReportController(env.source, false, 17, s => states.push(s));
    c.change({ reasonCode: 'other' }); await c.submit(); assert.equal(states.at(-1).status, expected); assert.equal(env.posts().length, 1);
  }
});
test('CSRF manual retry refreshes token and retains draft', async () => {
  let fail = true; const env = setup(() => fail ? response({ code: 'CSRF_INVALID' }, 403) : response());
  env.fill(); await env.control.submit(); assert.equal(env.states.at(-1).detail, '  신고 내용입니다.  ');
  env.cookie({ ...session(), csrfToken: 'rotated' }); fail = false; await env.control.submit();
  assert.equal(env.posts()[1].init.headers.get('X-CSRF-Token'), 'rotated'); assert.equal(env.states.at(-1).status, 'received');
});
test('inactive/local replaced/cookie replaced sessions cannot write', async () => {
  for (const change of [e => e.change(null), e => e.change(session()), e => e.cookie(session(8)), e => e.cookie({ ...session(), onboardingCompleted: false })]) {
    const env = setup(); env.fill(); change(env); await env.control.submit();
    assert.equal(env.posts().length, 0); assert.equal(env.states.at(-1).status, 'unavailable'); assert.equal(env.states.at(-1).detail, '');
  }
});
for (const status of [201, 401, 503]) test(`close during POST discards late ${status} and prevents reopening retransmission`, async () => {
  const gate = deferred(), sent = deferred();
  const env = setup(async () => { sent.resolve(); await gate.promise; return response({ reportId: 42, status: 'received' }, status); });
  env.fill(); const task = env.control.submit(); await sent.promise; const count = env.states.length;
  env.control.cancel(); gate.resolve(); await task;
  assert.equal(env.states.length, count); assert.equal(env.events.length, 0); assert.equal(env.posts()[0].init.signal.aborted, true);
  const states = [], c = env.report.createReportController(env.source, false, 17, s => states.push(s));
  c.change({ reasonCode: 'other' }); await c.submit(); assert.equal(states.at(-1).status, 'uncertain'); assert.equal(env.posts().length, 1);
});
test('session changes during response discard old draft and success', async () => {
  const gate = deferred(), sent = deferred(); const env = setup(async () => { sent.resolve(); await gate.promise; return response(); });
  env.fill(); const task = env.control.submit(); await sent.promise; env.change(session(8)); gate.resolve(); await task;
  assert.equal(env.states.at(-1).status, 'unavailable'); assert.equal(env.states.at(-1).detail, '');
});
test('Demo cannot reach even a provided service source', async () => {
  const env = setup(), states = [], c = env.report.createReportController(env.source, true, 17, s => states.push(s));
  c.change({ reasonCode: 'other' }); await c.submit(); assert.equal(env.calls.length, 0); assert.match(states.at(-1).message, /Demo/);
});
test('preflight failure/cancellation send nothing; manual retry is safe', async () => {
  const env = setup(), owner = session(), gate = deferred(); let fail = true, writes = 0;
  const make = api => env.load('lib/pivot/report-service.ts').createServiceReportSource(() => owner, api, () => {});
  const source = make({ async getSession() { if (fail) throw new Error('offline'); return owner; }, async submitReport() { writes++; return { reportId: 42, status: 'received' }; } });
  const states = [], c = env.report.createReportController(source, false, 17, s => states.push(s)); c.change({ reasonCode: 'other' }); await c.submit();
  assert.equal(writes, 0); assert.match(states.at(-1).message, /보내지 않았어요/); fail = false; await c.submit(); assert.equal(writes, 1);
  const delayed = make({ async getSession() { await gate.promise; return owner; }, async submitReport() { writes++; } });
  const abort = new AbortController(), task = delayed.submit({ targetType: 'content', targetId: 18, reasonCode: 'other' }, abort.signal);
  abort.abort(); gate.resolve(); await assert.rejects(task); assert.equal(writes, 1);
});

test('cancelled session preflight cannot emit a late 401 against a newer session', async () => {
  const gate = deferred(), sent = deferred();
  const env = setup(() => response(), async () => { sent.resolve(); await gate.promise; return response({ code: 'AUTH_REQUIRED' }, 401); });
  env.fill(); const task = env.control.submit(); await sent.promise; env.control.cancel(); env.change(session(8)); gate.resolve(); await task;
  assert.equal(env.posts().length, 0); assert.equal(env.events.length, 0); assert.equal(env.calls[0].init.signal.aborted, true);
});
