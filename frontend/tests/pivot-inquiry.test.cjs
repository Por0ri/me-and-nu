const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const root = path.join(__dirname, '..');
const session = (id = 7) => ({ authenticated: true, sessionState: 'active', onboardingCompleted: true, user: { id, accountType: 'user' }, csrfToken: `csrf-${id}` });
const deferred = () => { let resolve; const promise = new Promise(yes => { resolve = yes; }); return { promise, resolve }; };
const response = (body = { inquiryId: 42, status: 'received' }, status = 201) => new Response(JSON.stringify(body), { status });
function setup(respond = () => response()) {
  const modules = new Map(), calls = [], events = [];
  let current = session(), cookieSession = current;
  const browser = new EventTarget(); browser.location = { origin: 'http://localhost:3194' };
  browser.addEventListener('api:unauthorized', () => events.push('401'));
  const context = vm.createContext({ AbortController, URL, URLSearchParams, Headers, Response, Event, window: browser,
    process: { env: { NEXT_PUBLIC_API_BASE_URL: 'http://localhost:8194', NEXT_PUBLIC_PIVOT_ENABLE_LOCAL_API: 'true' } },
    fetch: async (url, init) => {
      calls.push({ url, init });
      if (new URL(url).pathname.endsWith('/auth/session')) return new Response(JSON.stringify(cookieSession));
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
  const inquiry = load('lib/pivot/inquiry.ts');
  const source = load('lib/pivot/inquiry-service.ts').createServiceInquirySource(() => current);
  const states = [];
  const control = inquiry.createInquiryController(source, false, state => states.push(state));
  return { load, calls, events, states, source, inquiry, control, change(next) { current = next; }, cookie(next) { cookieSession = next; },
    posts() { return calls.filter(call => call.init.method === 'POST'); },
    fill(detail = '  서비스 문의입니다.  ', inquiryType = 'service') { control.change({ detail, inquiryType }); },
  };
}

test('type is required; trimmed blank and 501 characters cannot submit, including emoji', async () => {
  const env = setup();
  for (const draft of [{ inquiryType: null, detail: '내용' }, { inquiryType: 'other', detail: '내용' },
    { inquiryType: 'service', detail: '' }, { inquiryType: 'bug', detail: ' \n\t　 ' },
    { inquiryType: 'bug', detail: '가'.repeat(501) }, { inquiryType: 'bug', detail: '😀'.repeat(501) }]) {
    env.control.change(draft); await env.control.submit(); assert.equal(env.states.at(-1).status, 'error');
  }
  assert.equal(env.calls.length, 0);
});
for (const [type, detail] of [['service', ' 문의 '], ['bug', '가'.repeat(500)], ['service', `  ${'😀'.repeat(500)}  `]]) {
  test(`${type} / ${[...detail.trim()].length}: cookie and current CSRF, exact payload, strict receipt`, async () => {
    const env = setup(); env.fill(detail, type); await env.control.submit();
    assert.equal(env.posts().length, 1);
    const { url, init } = env.posts()[0];
    assert.equal(new URL(url).pathname, '/api/v1/inquiries');
    assert.deepEqual(JSON.parse(init.body), { inquiryType: type, detail: detail.trim() });
    assert.equal(init.credentials, 'include'); assert.equal(init.headers.get('X-CSRF-Token'), 'csrf-7'); assert.ok(init.signal);
    assert.equal(env.states.at(-1).status, 'received'); assert.equal(env.states.at(-1).detail, detail);
    assert.equal(env.states.at(-1).inquiryType, null);
    await env.control.submit(); assert.equal(env.posts().length, 1);
  });
}
test('same-tick double submit is single flight; pending edits cannot mutate the request', async () => {
  const gate = deferred(); const env = setup(async () => { await gate.promise; return response(); }); env.fill();
  const first = env.control.submit(); const second = env.control.submit();
  env.control.change({ detail: 'different', inquiryType: 'bug' });
  gate.resolve(); await Promise.all([first, second]);
  assert.equal(env.posts().length, 1); assert.equal(JSON.parse(env.posts()[0].init.body).detail, '서비스 문의입니다.');
});
for (const failure of ['401', '403', 'csrf', '422', '503', 'network']) {
  test(`${failure}: never claims receipt; retry preserves detail and refreshes CSRF`, async () => {
    let fail = true;
    const env = setup(() => {
      if (!fail) return response();
      if (failure === 'network') throw new TypeError('Failed to fetch');
      return response({ code: failure === 'csrf' ? 'CSRF_INVALID' : 'ERROR', message: 'private server message' }, failure === 'csrf' ? 403 : Number(failure));
    });
    env.fill(); await env.control.submit(); const failed = env.states.at(-1);
    assert.notEqual(failed.status, 'received'); assert.doesNotMatch(failed.message, /private server/);
    if (failure === '401') {
      assert.equal(failed.status, 'unavailable'); assert.equal(failed.detail, ''); assert.deepEqual(env.events, ['401']);
      await env.control.submit(); assert.equal(env.posts().length, 1); return;
    }
    assert.equal(failed.detail, '  서비스 문의입니다.  '); assert.equal(failed.inquiryType, 'service');
    if (failure === '403') { assert.match(failed.message, /권한/); assert.doesNotMatch(failed.message, /중복/); }
    if (failure === 'csrf') { assert.match(failed.message, /보안 토큰/); assert.doesNotMatch(failed.message, /중복/); }
    if (failure === '422') { assert.match(failed.message, /입력 내용/); assert.doesNotMatch(failed.message, /중복/); }
    if (failure === 'network') assert.match(failed.message, /중복/);
    env.cookie({ ...session(), csrfToken: 'rotated-csrf' }); fail = false; await env.control.submit();
    assert.equal(env.states.at(-1).status, 'received'); assert.equal(env.posts()[1].init.headers.get('X-CSRF-Token'), 'rotated-csrf');
  });
}
for (const failure of [500, 502, 503, 504, 'network', 'lost-body']) {
  test(`uncertain receipt ${failure}: no automatic retry; draft and warning survive editing and manual retry`, async () => {
    const gate = deferred(); const sent = deferred(); let phase = 'uncertain', saved = 0;
    const env = setup(async () => {
      // The server may commit before its gateway/transport fails to deliver the receipt.
      if (phase === 'uncertain') {
        saved++;
        if (failure === 'network') throw new TypeError('Connection lost after commit');
        if (failure === 'lost-body') return new Response(new ReadableStream({
          start(controller) { controller.error(new TypeError('Response stream lost')); },
        }), { status: 201 });
        return response({ code: 'UNKNOWN_ERROR' }, failure);
      }
      if (phase === 'validation') return response({ code: 'VALIDATION_ERROR' }, 422);
      sent.resolve(); await gate.promise; saved++; return response();
    });
    const { InquiryScreen } = env.load('components/pivot/inquiry-screen.tsx');
    const render = state => renderToStaticMarkup(React.createElement(InquiryScreen, {
      state, demo: false, available: true, onChange() {}, onSubmit() {}, onMy() {}, onHome() {}, onSaved() {},
    }));
    function assertUncertain(state) {
      assert.notEqual(state.status, 'received');
      assert.match(state.message, /접수 결과.*확인/); assert.match(state.message, /이미 접수되었을 수/); assert.match(state.message, /중복 접수/);
      assert.doesNotMatch(state.message, /문의를 접수하지 못했어요|문의가 접수되었어요/);
      assert.match(render(state), /role="alert"[^>]*>[^<]*중복 접수/);
      assert.equal(state.inquiryType, 'bug');
    }
    env.fill('  보존할 문의 내용  ', 'bug'); await env.control.submit();
    assertUncertain(env.states.at(-1)); assert.equal(env.states.at(-1).detail, '  보존할 문의 내용  ');
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(env.posts().length, 1); assert.equal(saved, 1);

    env.control.change({ detail: '  보존할 문의 내용 수정  ' }); assertUncertain(env.states.at(-1));
    // Rejecting a later attempt cannot prove that the first attempt was not received.
    phase = 'validation'; await env.control.submit(); assertUncertain(env.states.at(-1));
    assert.match(env.states.at(-1).message, /입력 내용/); assert.equal(env.posts().length, 2);

    phase = 'success'; const retry = env.control.submit(); const doubleClick = env.control.submit();
    assert.equal(env.states.at(-1).status, 'submitting'); assertUncertain(env.states.at(-1));
    await sent.promise; assert.equal(env.posts().length, 3);
    gate.resolve(); await Promise.all([retry, doubleClick]);
    assert.equal(env.states.at(-1).status, 'received'); assert.equal(env.states.at(-1).message, '문의가 접수되었어요.');
    assert.equal(env.states.at(-1).detail, '  보존할 문의 내용 수정  '); assert.equal(saved, 2);
  });
}
test('unexpected HTTP or payload cannot become a successful receipt', async () => {
  for (const [body, status] of [[{ inquiryId: 42, status: 'received' }, 200], [null, 201], [{ inquiryId: 0, status: 'received' }, 201],
    [{ inquiryId: '42', status: 'received' }, 201], [{ inquiryId: 42, status: 'queued' }, 201], [{}, 201]]) {
    const env = setup(() => response(body, status)); env.fill(); await env.control.submit();
    assert.equal(env.states.at(-1).status, 'error'); assert.match(env.states.at(-1).message, /접수 결과/);
    assert.equal(env.states.at(-1).detail, '  서비스 문의입니다.  ');
  }
});
test('inactive/local changed/cookie changed sessions cannot POST', async () => {
  for (const update of [env => env.change(null), env => env.change(session(9)), env => env.change(session()),
    env => env.cookie(session(9)), env => env.cookie({ ...session(), onboardingCompleted: false, sessionState: 'onboarding_pending' })]) {
    const env = setup(); env.fill(); update(env); await env.control.submit();
    assert.equal(env.posts().length, 0); assert.equal(env.states.at(-1).status, 'unavailable'); assert.equal(env.states.at(-1).detail, '');
  }
});
for (const status of [201, 503]) test(`session changes during ${status} response discard the receipt and personal draft`, async () => {
  const gate = deferred(); const sent = deferred();
  const env = setup(async () => { sent.resolve(); await gate.promise; return status === 201 ? response() : response({}, status); });
  env.fill(); const task = env.control.submit(); await sent.promise; env.change(session(9)); gate.resolve(); await task;
  assert.equal(env.states.at(-1).status, 'unavailable'); assert.equal(env.states.at(-1).detail, '');
  assert.doesNotMatch(env.states.at(-1).message, /중복|접수되었어요/);
});
for (const status of [201, 401, 503]) test(`leaving aborts POST and ignores late ${status} without expiring another session`, async () => {
  const gate = deferred(); const sent = deferred();
  const env = setup(async () => { sent.resolve(); await gate.promise; return status === 201 ? response() : response({}, status); });
  env.fill(); const task = env.control.submit(); await sent.promise; const count = env.states.length;
  env.control.cancel(); assert.equal(env.posts()[0].init.signal.aborted, true); gate.resolve(); await task;
  assert.equal(env.states.length, count); assert.equal(env.events.length, 0);
});
test('abort while session read is pending prevents POST', async () => {
  const gate = deferred(); let writes = 0;
  const env = setup(); const owner = session();
  const actual = env.load('lib/pivot/inquiry-service.ts').createServiceInquirySource(() => owner,
    { getSession: () => gate.promise, submitInquiry: async () => { writes++; return { inquiryId: 42, status: 'received' }; } }, () => {});
  const control = env.inquiry.createInquiryController(actual, false, () => {}); control.change({ inquiryType: 'bug', detail: '내용' });
  const task = control.submit(); control.cancel(); gate.resolve(owner); await task; assert.equal(writes, 0);
});
test('Demo and missing service source cannot request a session or POST, even if invoked directly', async () => {
  const env = setup();
  for (const [source, demo] of [[env.source, true], [undefined, false]]) {
    const states = []; const control = env.inquiry.createInquiryController(source, demo, state => states.push(state));
    control.change({ inquiryType: 'bug', detail: '버그' }); await control.submit();
    assert.equal(states.at(-1).status, 'error'); assert.equal(env.calls.length, 0);
  }
});
test('screen exposes labeled single-select inputs, validation, errors and truthful Demo status', () => {
  const env = setup(); const { InquiryScreen } = env.load('components/pivot/inquiry-screen.tsx');
  const render = (state, demo = false) => renderToStaticMarkup(React.createElement(InquiryScreen, {
    state, demo, available: true, onChange() {}, onSubmit() {}, onMy() {}, onHome() {}, onSaved() {},
  }));
  const empty = render(env.inquiry.initialInquiryState()); assert.equal((empty.match(/type="radio"/g) || []).length, 2);
  assert.match(empty, /placeholder="문의하실 내용을 자세히 입력해주세요"/); assert.match(empty, /type="submit" disabled/);
  const filled = { inquiryType: 'bug', detail: '버그', status: 'editing', message: '' };
  assert.doesNotMatch(render(filled), /type="submit" disabled/);
  assert.match(render({ ...filled, detail: '가'.repeat(501) }), /aria-invalid="true"/);
  assert.match(render(filled, true), /Demo에서는 실제 문의를 접수하지 않아요/);
  assert.match(render(filled, true), /type="submit" disabled/);
});
