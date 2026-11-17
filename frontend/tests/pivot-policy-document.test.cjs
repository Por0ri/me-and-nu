const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const { createHash, webcrypto } = require('node:crypto');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const root = path.join(__dirname, '..');
function setup(respond = async () => new Response(JSON.stringify({ consentItems: [] }))) {
  const modules = new Map(), calls = [], events = [];
  const browser = new EventTarget(); browser.location = { origin: 'http://127.0.0.1:3196' };
  browser.addEventListener('api:unauthorized', () => events.push('401'));
  const context = vm.createContext({ URL, URLSearchParams, Headers, AbortController, Event, TextEncoder, crypto: webcrypto,
    window: browser, process: { env: { NEXT_PUBLIC_API_BASE_URL: 'http://127.0.0.1:8196', NEXT_PUBLIC_PIVOT_ENABLE_LOCAL_API: 'true' } },
    fetch: async (url, init) => { calls.push({ url, init }); return respond(url, init); },
  });
  function load(file) {
    if (modules.has(file)) return modules.get(file).exports;
    const mod = { exports: {} }; modules.set(file, mod);
    const code = ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), { compilerOptions: {
      module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
    } }).outputText;
    vm.runInContext(`(function(module,exports,require){${code}\n})`, context)(mod, mod.exports, name => {
      if (name === 'react' || name === 'react/jsx-runtime') return require(name);
      if (name.endsWith('.module.css')) return { __esModule: true, default: new Proxy({}, { get: (_, key) => String(key) }) };
      const relative = name.startsWith('@/') ? name.slice(2) : path.join(path.dirname(file), name);
      return load(relative + (fs.existsSync(path.join(root, relative + '.tsx')) ? '.tsx' : '.ts'));
    });
    return mod.exports;
  }
  return { load, calls, events, document: load('lib/pivot/policy-document.ts') };
}
const policy = (type = 'terms', text = '문서 렌더링 검증 문자열\n두 번째 줄') => ({ type, policyVersion: 'release-1', required: true, text });
// Synthetic publication evidence for unit tests only; never registered by the app.
const approval = p => ({ type: p.type, policyVersion: p.policyVersion, textSha256: createHash('sha256').update(p.text).digest('hex') });
const plain = value => JSON.parse(JSON.stringify(value));

test('text stays literal, preserves line breaks and never executes markup', () => {
  const env = setup(), text = '<script>alert(1)</script>\nhttps://example.org/reference';
  const content = env.document.policyContent(text);
  assert.deepEqual(plain(content), { kind: 'text', text });
  const { PolicyDocumentContent } = env.load('components/pivot/policy-dialog.tsx');
  const html = renderToStaticMarkup(React.createElement(PolicyDocumentContent, { content, title: '이용약관' }));
  assert.match(html, /&lt;script&gt;/); assert.doesNotMatch(html, /<script/);
  assert.match(html, /tabindex="0".*role="region".*aria-label="이용약관 본문"/);
});
test('complete HTTP(S) URL uses an explicit original-document link in a new isolated tab', () => {
  const env = setup(), { PolicyDocumentContent } = env.load('components/pivot/policy-dialog.tsx');
  for (const url of ['https://example.org/policy?v=1#terms', 'http://example.org/privacy']) {
    const content = env.document.policyContent(`  ${url}  `);
    assert.equal(content.kind, 'url'); assert.equal(content.url, url);
    const html = renderToStaticMarkup(React.createElement(PolicyDocumentContent, { content, title: '개인정보처리방침' }));
    assert.match(html, /target="_blank" rel="noopener noreferrer"/); assert.match(html, /원문 열기 \(새 탭\)/);
  }
});
for (const value of ['', ' ', 'javascript:alert(1)', 'data:text/html,hi', 'file:///tmp/policy', '//example.org', '/terms',
  'www.example.org', 'https://', 'https://name:secret@example.org', 'https://example.org/ bad', 'https://example.org/\npath', 'https://example.org\\path']) {
  test(`unusable policy location is not rendered: ${JSON.stringify(value)}`, () => assert.equal(setup().document.policyContent(value).kind, 'unavailable'));
}
for (const type of ['terms', 'privacy']) {
  test(`${type}: exact publication evidence allows body or URL and retains API version`, async () => {
    const env = setup();
    for (const text of ['문서 렌더링 검증 문자열\n두 번째 줄', 'https://example.org/approved-document']) {
      const item = policy(type, text), state = await env.document.resolvePolicyDocument([item], type, [approval(item)]);
      assert.equal(state.status, 'ready'); assert.deepEqual(plain(state.policy), item);
      assert.equal(state.content.kind, text.startsWith('https:') ? 'url' : 'text');
    }
  });
}
test('default release blocks even plausible/Figma-matching versions without publication evidence', async () => {
  const env = setup();
  for (const policyVersion of ['release-1', 'v1.2', 'v0.3']) {
    const state = await env.document.resolvePolicyDocument([{ ...policy(), policyVersion }], 'terms');
    assert.equal(state.status, 'unavailable'); assert.equal(state.policy, undefined); assert.equal(state.content, undefined);
  }
});
test('wrong type/version, changed body, changed URL, missing and duplicated documents fail closed', async () => {
  const env = setup(), item = policy(), approved = [approval(item)];
  for (const items of [[], [policy('privacy')], [item, item], [{ ...item, policyVersion: 'release-2' }],
    [{ ...item, text: item.text + ' changed' }], [{ ...item, text: 'https://example.org/different' }]]) {
    assert.equal((await env.document.resolvePolicyDocument(items, 'terms', approved)).status, 'unavailable');
  }
});
test('Checking/local/preview fixtures cannot be published even with matching evidence', async () => {
  const env = setup();
  for (const item of [{ ...policy(), policyVersion: 'checking-20261028-v1' }, { ...policy(), policyVersion: 'test-v1' },
    { ...policy(), policyVersion: 'figma-preview-only' }, policy('terms', '로컬 개발 환경에서 API 흐름을 검증하기 위한 임시 이용약관입니다. 실제 서비스 약관이 아닙니다.'),
    policy('privacy', '정식 서비스용 개인정보 안내가 아닙니다.')]) {
    assert.equal(env.document.isSamplePolicy(item), true);
    assert.equal((await env.document.resolvePolicyDocument([item], item.type, [approval(item)])).status, 'unavailable');
  }
});
test('read adapter reuses only credentialed no-store GET, including explicit retry', async () => {
  let attempt = 0;
  const env = setup(async () => ++attempt === 1 ? new Response('{}', { status: 503 }) : new Response(JSON.stringify({ consentItems: [policy()] })));
  const { loadPivotPolicies } = env.load('lib/pivot/services.ts');
  await assert.rejects(loadPivotPolicies());
  assert.equal((await loadPivotPolicies())[0].policyVersion, 'release-1');
  assert.equal(env.calls.length, 2);
  for (const call of env.calls) {
    assert.equal(new URL(call.url).pathname, '/api/v1/policies'); assert.equal(call.init.method ?? 'GET', 'GET');
    assert.equal(call.init.credentials, 'include'); assert.equal(call.init.cache, 'no-store'); assert.equal(call.init.body, undefined);
  }
});
test('missing, empty, blank, duplicated and malformed API data rejects without invented content', async () => {
  for (const payload of [null, {}, { consentItems: [] }, { consentItems: [null] }, { consentItems: [policy(), policy()] },
    { consentItems: [{ ...policy(), text: '' }] }, { consentItems: [{ ...policy(), policyVersion: '' }] }]) {
    const env = setup(async () => new Response(JSON.stringify(payload)));
    await assert.rejects(env.load('lib/pivot/services.ts').loadPivotPolicies());
  }
});
test('cancellation prevents requests and suppresses a late 401 from a dismissed dialog', async () => {
  let release;
  const env = setup(() => new Promise(resolve => { release = resolve; }));
  const { loadPivotPolicies } = env.load('lib/pivot/services.ts');
  const cancelled = new AbortController(); cancelled.abort(); await assert.rejects(loadPivotPolicies(cancelled.signal));
  assert.equal(env.calls.length, 0);
  const control = new AbortController(), pending = loadPivotPolicies(control.signal); control.abort();
  release(new Response('{}', { status: 401 })); await assert.rejects(pending);
  assert.deepEqual(env.events, []);
});
