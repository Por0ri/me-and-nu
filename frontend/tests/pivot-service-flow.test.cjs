const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const { createApiFixture } = require('./helpers/pivot-api-fixture.cjs');
const root = path.resolve(__dirname, '..');
const plain = value => JSON.parse(JSON.stringify(value));

// Load the real API client and adapters together, including CSRF module state.
// Only the HTTP boundary and browser location are controlled by this fixture.
function browserModules(fixture, initialCookie = '') {
  const cache = new Map(); const calls = []; const events = [];
  let jar = initialCookie;
  const browser = new EventTarget();
  browser.location = { origin: 'http://localhost:3180', hostname: 'localhost', assign(url) { browser.redirect = url; } };
  browser.addEventListener('api:unauthorized', event => events.push(event.type));
  const context = vm.createContext({ URL, URLSearchParams, Headers, Response, AbortController, DOMException, Event, console,
    window: browser, process: { env: { NEXT_PUBLIC_API_BASE_URL: 'http://localhost:8180', NEXT_PUBLIC_PIVOT_ENABLE_LOCAL_API: 'true' } },
    fetch: async (url, init = {}) => {
      if (init.signal?.aborted) throw new DOMException('Aborted', 'AbortError');
      assert.equal(init.credentials, 'include', 'every API request includes the browser session cookie');
      assert.equal(init.cache, 'no-store', 'authenticated responses must not be cached');
      const headers = new Headers(init.headers); if (jar) headers.set('Cookie', jar);
      calls.push({ url, method: init.method || 'GET', signal: init.signal });
      const response = fixture.dispatch(url, { ...init, headers });
      if (response.headers?.['Set-Cookie']) jar = response.headers['Set-Cookie'].split(';')[0];
      return new Response(response.status === 204 ? null : JSON.stringify(response.body ?? null), { status: response.status, headers: response.headers });
    },
  });
  function load(relative) {
    const file = path.resolve(root, relative);
    if (cache.has(file)) return cache.get(file).exports;
    const mod = { exports: {} }; cache.set(file, mod);
    const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
    const run = vm.runInContext(`(function(require,module,exports){${code}\n})`, context, { filename: relative });
    run(specifier => {
      const imported = specifier.startsWith('@/') ? path.resolve(root, specifier.slice(2)) : path.resolve(path.dirname(file), specifier);
      const resolved = fs.existsSync(imported + '.ts') ? imported + '.ts' : imported;
      return load(path.relative(root, resolved));
    }, mod, mod.exports);
    return mod.exports;
  }
  return { load, calls, events, get cookie() { return jar; }, browser };
}

test('real API client + adapters preserve topic and saved state through Home → Detail → Save → refresh → Saved → remove', async () => {
  const fixture = createApiFixture(); fixture.rotate('active');
  const modules = browserModules(fixture);
  const api = modules.load('lib/api.ts');
  await api.getSession();
  const signal = new AbortController().signal;
  const home = await modules.load('lib/pivot/home-service.ts').createServiceHomeSource().load({ signal });
  assert.equal(home.status, 'ready'); assert.equal(home.home.origin, 'service');
  assert.deepEqual(plain(home.home.cards.map(card => [card.detailContentId, card.topicId])), [['41', 7], ['42', 7]]);
  const target = { postId: '41', contentId: '41', topicId: 7 };
  const detail = await modules.load('lib/pivot/detail-service.ts').createServiceDetailSource().load(target, { signal });
  assert.equal(detail.status, 'ready'); assert.equal(detail.article.origin, 'service');
  assert.match(detail.article.sections.flatMap(section => section.paragraphs).join('\n'), /테스트 API에서 내려온/);
  await api.bookmarkContent(41, 7);
  const saved = await modules.load('lib/pivot/saved-service.ts').createServiceSavedSource().load({ signal });
  assert.equal(saved.status, 'ready'); assert.equal(saved.saved.cards[0].contentId, '41');
  assert.equal(saved.saved.cards[0].topicId, 7);
  // A new JS module graph has no previous UI memory but shares the server cookie.
  const reloaded = browserModules(fixture, modules.cookie);
  const reloadedApi = reloaded.load('lib/api.ts');
  await reloadedApi.getSession();
  const reloadedSavedSource = reloaded.load('lib/pivot/saved-service.ts').createServiceSavedSource();
  assert.equal((await reloadedSavedSource.load({ signal })).saved.cards.length, 1);
  await reloadedApi.unbookmarkContent(41, 7);
  assert.equal((await reloadedSavedSource.load({ signal })).saved.cards.length, 0);
  const put = fixture.requests.find(request => request.method === 'PUT');
  const remove = fixture.requests.find(request => request.method === 'DELETE');
  assert.deepEqual(put.body, { topicId: 7 }); assert.equal(remove.query.topicId, '7');
  assert.equal(put.csrf, 'fixture-active-csrf'); assert.match(put.cookie, /fixture_session=fixture-active/);
  assert.ok(modules.calls.filter(call => /\/feed|\/contents|\/bookmarks/.test(call.url) && call.method === 'GET').every(call => call.signal === signal));
});

test('failed bookmark writes remain failed and cannot appear in the server-backed Saved list', async () => {
  const fixture = createApiFixture(); fixture.rotate('active');
  const modules = browserModules(fixture); const api = modules.load('lib/api.ts');
  await api.getSession();
  fixture.state.failNext = { method: 'PUT', path: '/api/v1/contents/41/bookmark', status: 503 };
  await assert.rejects(api.bookmarkContent(41, 7), error => error.status === 503);
  assert.equal(fixture.state.saved.size, 0);
  assert.equal(fixture.requests.filter(request => request.method === 'PUT').length, 1, 'failed writes are not silently repeated');
  const result = await modules.load('lib/pivot/saved-service.ts').createServiceSavedSource().load({ signal: new AbortController().signal });
  assert.equal(result.saved.cards.length, 0);
});

test('stale CSRF is rejected; only a subsequent explicit write reloads the current token', async () => {
  const fixture = createApiFixture(); fixture.rotate('active');
  const modules = browserModules(fixture); const api = modules.load('lib/api.ts');
  await api.getSession(); fixture.state.csrf = 'fixture-rotated-csrf';
  await assert.rejects(api.bookmarkContent(41, 7), error => error.status === 403 && error.code === 'CSRF_INVALID');
  assert.equal(fixture.state.saved.size, 0);
  await api.bookmarkContent(41, 7);
  assert.equal(fixture.requests.at(-1).csrf, 'fixture-rotated-csrf');
  assert.equal(fixture.requests.filter(request => request.path === '/api/v1/auth/session').length, 2);
});

test('expired session emits the authentication event and does not fall back to demo content', async () => {
  const fixture = createApiFixture(); fixture.rotate('active');
  const modules = browserModules(fixture); const api = modules.load('lib/api.ts');
  await api.getSession(); fixture.rotate('anonymous');
  const result = await modules.load('lib/pivot/home-service.ts').createServiceHomeSource().load({ signal: new AbortController().signal });
  assert.equal(result.status, 'error'); assert.equal(result.reason, 'unauthorized');
  assert.deepEqual(modules.events, ['api:unauthorized']);
  assert.equal((await api.getSession()).user, null);
});

test('missing active topic produces an empty service feed instead of requesting a guessed topic', async () => {
  const fixture = createApiFixture(); fixture.rotate('active'); fixture.state.topics = false;
  const modules = browserModules(fixture); await modules.load('lib/api.ts').getSession();
  const result = await modules.load('lib/pivot/home-service.ts').createServiceHomeSource().load({ signal: new AbortController().signal });
  assert.equal(result.status, 'ready'); assert.equal(result.home.cards.length, 0);
  assert.equal(fixture.requests.some(request => request.path.endsWith('/feed')), false);
});

test('concurrent initial session reads share one request and one CSRF for the first authenticated write', async () => {
  const fixture = createApiFixture();
  const modules = browserModules(fixture); const api = modules.load('lib/api.ts');
  const [first, second, third] = await Promise.all([api.getSession(), api.getSession(), api.getSession()]);
  assert.equal(fixture.requests.filter(request => request.path === '/api/v1/auth/session').length, 1);
  assert.equal(first.csrfToken, second.csrfToken); assert.equal(second.csrfToken, third.csrfToken);
  await modules.load('lib/pivot/services.ts').startSnsLogin('kakao');
  const start = fixture.requests.find(request => request.path === '/api/v1/auth/login/kakao');
  assert.equal(start.csrf, 'fixture-anonymous-csrf');
  assert.match(start.cookie, /fixture_session=fixture-anonymous/);
  assert.equal(new URL(modules.browser.redirect).hostname, 'kauth.kakao.com');
});

test('logout uses the active session CSRF, preserves failures, and refreshes anonymous CSRF after success', async () => {
  const fixture = createApiFixture(); fixture.rotate('active');
  const modules = browserModules(fixture); const api = modules.load('lib/api.ts');
  await api.getSession();
  fixture.state.failNext = { method: 'POST', path: '/api/v1/auth/logout', status: 503 };
  await assert.rejects(api.logout(), error => error.status === 503);
  assert.equal((await api.getSession()).sessionState, 'active');
  await api.logout();
  const request = fixture.requests.filter(item => item.path === '/api/v1/auth/logout').at(-1);
  assert.equal(request.method, 'POST'); assert.equal(request.csrf, 'fixture-active-csrf');
  assert.match(request.cookie, /fixture_session=fixture-active/);
  assert.equal((await api.getSession()).sessionState, 'anonymous');
  await modules.load('lib/pivot/services.ts').startSnsLogin('kakao');
  const nextLogin = fixture.requests.find(item => item.path === '/api/v1/auth/login/kakao');
  assert.equal(nextLogin.csrf, 'fixture-anonymous-csrf');
});
