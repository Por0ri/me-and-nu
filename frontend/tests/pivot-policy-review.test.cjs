const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createHash } = require('node:crypto');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const root = path.join(__dirname, '..');
const sourcePath = path.join(root, 'dev/policy-review/team-policy-2026-10-23.md');
// Public fixture: deliberately artificial, with no team policy clauses or people.
const source = ['이용약관', '운영정책', '개인정보처리방침'].map((title, index) => `## 0${index + 1} ${title}

버전 ${['v1.2', 'v0.6', 'v0.3'][index]} · 시행일 2026년 11월 2일

### 제1조 (TEST_FIXTURE)

TEST_FIXTURE_ONLY_${index}

1. TEST_ITEM_A
   - TEST_NESTED_ITEM
2. TEST_ITEM_B

| TEST_HEADER_A | TEST_HEADER_B |
|---|---|
| TEST_CELL_A | TEST_CELL_B |

3. TEST_ITEM_C [연락용 이메일 주소]
`).join('\n---\n\n');
const localSource = fs.existsSync(sourcePath) ? fs.readFileSync(sourcePath, 'utf8').replace(/\r\n/g, '\n') : null;
const provenance = JSON.parse(fs.readFileSync(path.join(root, 'dev/policy-review/provenance.json'), 'utf8'));
const hash = text => createHash('sha256').update(text).digest('hex');
function setup(env = {}, file = source, approveFixture = false) {
  const modules = new Map(); let reads = 0;
  const context = vm.createContext({ URL, Request, Response, Headers, TextEncoder, process: { env, cwd: () => root } });
  function load(relative) {
    if (modules.has(relative)) return modules.get(relative).exports;
    const mod = { exports: {} }; modules.set(relative, mod);
    const code = ts.transpileModule(fs.readFileSync(path.join(root, relative), 'utf8'), { compilerOptions: {
      module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
    } }).outputText;
    vm.runInContext(`(function(module,exports,require){${code}\n})`, context)(mod, mod.exports, name => {
      if (name === 'server-only') return {};
      if (name === 'node:fs/promises') return { readFile: async () => { reads++; if (file instanceof Error) throw file; return file; } };
      // Only unit success-path tests substitute the synthetic fixture fingerprint.
      // The application has no hash override; the local source test uses real crypto.
      if (name === 'node:crypto' && approveFixture) return { createHash: algorithm => {
        assert.equal(algorithm, 'sha256');
        return { update: text => ({ digest: encoding => {
          assert.equal(encoding, 'hex');
          return text === source ? provenance.sourceSha256Lf : hash(text);
        } }) };
      } };
      if (name.startsWith('node:') || name === 'react' || name === 'react/jsx-runtime') return require(name);
      if (name.endsWith('.module.css')) return { __esModule: true, default: new Proxy({}, { get: (_, key) => String(key) }) };
      const target = name.startsWith('@/') ? name.slice(2) : path.join(path.dirname(relative), name);
      return load(target + (fs.existsSync(path.join(root, target + '.tsx')) ? '.tsx' : '.ts'));
    });
    return mod.exports;
  }
  return { load, reads: () => reads };
}
const request = (url = 'http://127.0.0.1:3196/api/dev/policy-review', headers = {}) => new Request(url, { headers: { host: new URL(url).host, ...headers } });
const enabled = { NODE_ENV: 'development', PIVOT_POLICY_REVIEW_ENABLED: 'true', PIVOT_POLICY_REVIEW_SOURCE: sourcePath };
const review = setup().load('lib/pivot/policy-review.ts');
const docs = review.parseTeamPolicyReview(source);
const flatten = blocks => blocks.map(block => block.kind === 'list' ? block.items.map(item => item.text + flatten(item.children)).join('')
  : block.kind === 'table' ? block.header.join('') + block.rows.flat().join('') : block.text).join('');

test('optional ignored local source matches Figma fingerprints and the real route digest', { skip: !localSource }, async () => {
  assert.equal(hash(localSource), provenance.sourceSha256Lf);
  const localDocs = review.parseTeamPolicyReview(localSource);
  assert.deepEqual(Array.from(localDocs, d => d.version), ['v1.2', 'v0.6', 'v0.3']);
  localDocs.forEach((document, index) => {
    const text = (document.title + '버전 ' + document.version + ' · 시행일 2026년 11월 2일' + flatten(document.blocks)).replace(/\s/g, '');
    assert.equal(text.length, provenance.documents[index].normalizedCharacters);
    assert.equal(hash(text), provenance.documents[index].normalizedSha256);
    assert.equal(document.effectiveDate, '2026-11-02');
  });
  assert.deepEqual(Array.from(localDocs, d => d.blocks.filter(b => b.kind === 'heading' && /^제\d+조/.test(b.text)).length), [31, 15, 17]);
  assert.deepEqual(Array.from(localDocs, d => d.blocks.filter(b => b.kind === 'table').length), [0, 3, 6]);
  assert.match(flatten(localDocs[2].blocks), /\[연락용 이메일 주소\]/);
  const result = await setup(enabled, localSource).load('app/api/dev/policy-review/route.ts').GET(request());
  assert.equal(result.status, 200);
  assert.equal(hash((await result.json()).markdown), provenance.sourceSha256Lf);
});

test('synthetic clauses, nested lists, tables, numbering and literal placeholders survive parsing', () => {
  assert.deepEqual(Array.from(docs, d => d.blocks.filter(b => b.kind === 'heading').length), [1, 1, 1]);
  assert.deepEqual(Array.from(docs, d => d.blocks.filter(b => b.kind === 'table').length), [1, 1, 1]);
  assert.match(flatten(docs[2].blocks), /\[연락용 이메일 주소\]/);
  assert.deepEqual(Array.from(docs[1].blocks.filter(b => b.kind === 'list').flatMap(b => b.items), item => item.number), [1,2,3]);
  assert.ok(docs[0].blocks.some(b => b.kind === 'list' && b.items.some(item => item.children.length)));
});

test('review renderer emits semantic headings/lists/tables, literal placeholders and escaped markup', () => {
  const { ReviewBlocks } = setup().load('components/pivot/local-policy-review.tsx');
  const render = blocks => renderToStaticMarkup(React.createElement(ReviewBlocks, { blocks, title: '검토' }));
  const html = render(docs[2].blocks);
  assert.equal((html.match(/<table>/g) || []).length, 1);
  assert.match(html, /scope="col"/); assert.match(html, /role="region".*tabindex="0"/);
  assert.match(html, /\[연락용 이메일 주소\]/); assert.match(render(docs[1].blocks), /<ol start="3"><li value="3">/);
  assert.match(render(review.parseReviewBlocks('<script>alert(1)</script>')), /&lt;script&gt;/);
});
test('missing/version-changed/date-changed source fails rather than fabricating a document', () => {
  for (const content of [null, '', source.replace('v1.2', 'v9'), source.replace('시행일 2026년 11월 2일', '시행일 2027년 1월 1일'), source.split('## 03')[0]]) {
    assert.throws(() => review.parseTeamPolicyReview(content));
  }
});
test('review entry requires loopback Demo and explicit query; service, hosted and lookalike URLs cannot opt in', () => {
  for (const url of ['http://127.0.0.1:3196/pivot/demo?view=my&policyReview=1', 'http://localhost:3196/pivot/demo?policyReview=1', 'http://[::1]:3196/pivot/demo?policyReview=1']) assert.equal(review.isLocalPolicyReviewUrl(url), true);
  for (const url of ['http://127.0.0.1:3196/pivot/demo?view=my', 'http://127.0.0.1:3196/pivot/home?policyReview=1', 'https://checking.me-nu.kr/pivot/demo?policyReview=1', 'http://localhost.evil/pivot/demo?policyReview=1', 'http://user@localhost/pivot/demo?policyReview=1', 'invalid']) assert.equal(review.isLocalPolicyReviewUrl(url), false);
});
test('explicit local development route reads verified source without cookies/cache/CORS', async () => {
  const env = setup(enabled, source, true), route = env.load('app/api/dev/policy-review/route.ts');
  const result = await route.GET(request(undefined, { 'sec-fetch-site': 'same-origin', origin: 'http://127.0.0.1:3196' }));
  assert.equal(result.status, 200); assert.equal(env.reads(), 1);
  assert.equal(result.headers.get('cache-control'), 'private, no-store'); assert.equal(result.headers.get('set-cookie'), null);
  assert.equal(result.headers.get('access-control-allow-origin'), null);
  assert.deepEqual(await result.json(), { kind: 'development-review', markdown: source });
});
test('Next-normalized localhost URL accepts the actual loopback Host on the same port', async () => {
  const env = setup(enabled, source, true), result = await env.load('app/api/dev/policy-review/route.ts').GET(request('http://localhost:3196/api/dev/policy-review', {
    host: '127.0.0.1:3196', origin: 'http://127.0.0.1:3196', referer: 'http://127.0.0.1:3196/pivot/demo?view=my&policyReview=1',
    'x-forwarded-host': '127.0.0.1:3196', 'x-forwarded-for': '127.0.0.1', 'sec-fetch-site': 'same-origin',
  }));
  assert.equal(result.status, 200); assert.equal(env.reads(), 1);
});
for (const env of [{}, { NODE_ENV: 'development' }, { ...enabled, PIVOT_POLICY_REVIEW_ENABLED: 'false' },
  { ...enabled, NODE_ENV: 'production' }, { ...enabled, NODE_ENV: 'test' }, { ...enabled, VERCEL_ENV: 'preview' },
  { ...enabled, VERCEL_ENV: 'production' }, { ...enabled, VERCEL_ENV: 'development' }, { ...enabled, VERCEL: '1' }]) {
  test(`disabled/hosted environment never reads policy bytes: ${JSON.stringify(env)}`, async () => {
    const fixture = setup(env), response = await fixture.load('app/api/dev/policy-review/route.ts').GET(request());
    assert.equal(response.status, 404); assert.equal(fixture.reads(), 0); assert.doesNotMatch(await response.text(), /이용약관|v1.2/);
  });
}
for (const [url, headers] of [
  ['https://checking.me-nu.kr/api/dev/policy-review', {}], ['http://192.168.0.1:3196/api/dev/policy-review', {}],
  [undefined, { host: 'checking.me-nu.kr' }], [undefined, { origin: 'https://checking.me-nu.kr' }],
  [undefined, { host: '127.0.0.1:9999' }], [undefined, { host: 'localhost/ignored' }],
  [undefined, { origin: 'null' }], [undefined, { 'sec-fetch-site': 'cross-site' }],
  [undefined, { referer: 'https://example.org/' }], [undefined, { 'x-forwarded-host': 'checking.me-nu.kr' }],
  [undefined, { 'x-forwarded-for': '192.168.0.2' }], [undefined, { 'x-forwarded-for': '127.0.0.1, 192.168.0.2' }],
  [undefined, { forwarded: 'for=127.0.0.1' }],
]) {
  test(`remote/cross-origin/proxied access is denied before source IO: ${JSON.stringify([url,headers])}`, async () => {
    const env = setup(enabled), response = await env.load('app/api/dev/policy-review/route.ts').GET(request(url, headers));
    assert.equal(response.status, 404); assert.equal(env.reads(), 0);
  });
}
test('missing private file disables review in a fresh checkout', async () => {
  const error = Object.assign(new Error('private filesystem info'), { code: 'ENOENT' });
  const env = setup(enabled, error), result = await env.load('app/api/dev/policy-review/route.ts').GET(request());
  assert.equal(result.status, 404); assert.equal(env.reads(), 1);
  assert.equal(await result.text(), 'Not found');
});

test('unreadable/altered/synthetic source returns generic 503 with real digest validation', async () => {
  for (const text of [new Error('private filesystem info'), source, source.replace('TEST_ITEM_A', 'changed'), source.replace('v0.6', 'v0.7')]) {
    const env = setup(enabled, text), result = await env.load('app/api/dev/policy-review/route.ts').GET(request());
    assert.equal(result.status, 503); assert.doesNotMatch(await result.text(), /private|changed|v0.7|이용약관/);
  }
});
test('publication gate remains empty, including exact review versions, independent of local review', async () => {
  const publication = setup().load('lib/pivot/policy-document.ts');
  for (const document of docs) {
    const result = await publication.resolvePolicyDocument([{ type: document.id, policyVersion: document.version, required: true, text: flatten(document.blocks) }], document.id);
    assert.equal(result.status, 'unavailable');
    assert.equal(result.policy, undefined); assert.equal(result.content, undefined);
  }
});

test('deployment trace exclusions match local sources on the deployment platform', () => {
  const config = setup().load('next.config.ts').default;
  const picomatch = require('next/dist/compiled/picomatch');
  const deployRoot = '/app/frontend';
  for (const route of ['/api/dev/policy-review', '/pivot/demo', 'next-server']) {
    const excludes = Object.entries(config.outputFileTracingExcludes)
      .filter(([glob]) => picomatch(glob, { dot: true, contains: true })(route))
      .flatMap(([, patterns]) => patterns.map(pattern => path.posix.join(deployRoot, pattern)));
    const matches = picomatch(excludes, { dot: true, contains: true });
    assert.equal(matches(path.posix.join(deployRoot, 'dev/policy-review/team-policy-2026-10-23.md')), true);
    assert.equal(matches(path.posix.join(deployRoot, 'dev/policy-review/backup/copy.pdf')), true);
    assert.equal(matches(path.posix.join(deployRoot, 'app/api/dev/policy-review/route.ts')), false);
  }
});

test('local launcher binds only IPv4 loopback, disables account API and rejects deployment environments', () => {
  const script = fs.readFileSync(path.join(root, 'scripts/dev-policy-review.cjs'), 'utf8');
  function launch(env) {
    const calls = [];
    const stubRequire = name => name === 'node:child_process' ? { spawn: (...args) => {
      calls.push(args); return { on() {}, kill() {} };
    } } : require(name);
    stubRequire.resolve = () => 'next-cli';
    vm.runInNewContext(script, { require: stubRequire, __dirname: path.join(root, 'scripts'), process: { env, execPath: 'node', on() {} } });
    return calls;
  }
  for (const env of [{ VERCEL: '1' }, { VERCEL_ENV: 'preview' }, { VERCEL_ENV: 'production' }]) assert.throws(() => launch(env));
  const [[, args, options]] = launch({});
  assert.deepEqual(Array.from(args), ['next-cli', 'dev', '--webpack', '--hostname', '127.0.0.1', '--port', '3196']);
  assert.equal(options.env.NODE_ENV, 'development');
  assert.equal(options.env.PIVOT_POLICY_REVIEW_ENABLED, 'true');
  assert.equal(options.env.NEXT_PUBLIC_PIVOT_ENABLE_LOCAL_API, 'false');
  assert.equal(options.env.PIVOT_POLICY_REVIEW_SOURCE, sourcePath);
});

test('missing runtime source path stays disabled without any filesystem read', async () => {
  const env = setup({ ...enabled, PIVOT_POLICY_REVIEW_SOURCE: undefined });
  const result = await env.load('app/api/dev/policy-review/route.ts').GET(request());
  assert.equal(result.status, 404); assert.equal(env.reads(), 0);
});

test('actual Next file tracer cannot collect the private runtime source, even if it exists locally', async () => {
  const { nodeFileTrace } = require('next/dist/compiled/@vercel/nft');
  const entry = path.join(root, 'policy-review-trace-test.cjs');
  const code = ts.transpileModule(fs.readFileSync(path.join(root, 'app/api/dev/policy-review/route.ts'), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const result = await nodeFileTrace([entry], {
    base: root, processCwd: root,
    readFile: async file => path.resolve(file) === entry ? code : fs.promises.readFile(file),
  });
  assert.ok(result.fileList.has(path.basename(entry)), 'Tracer analyzed the real route entry');
  assert.equal([...result.fileList].some(file => /dev[/\\]policy-review/.test(file)), false);
});
