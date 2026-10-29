const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const { createApiFixture } = require('./helpers/pivot-api-fixture.cjs');
const root = path.resolve(__dirname, '..');
const plain = value => JSON.parse(JSON.stringify(value));
const completePath = '/api/v1/onboarding/complete';
const selection = { interest: 'animation', topicId: 271, favoriteEntityIds: [101, 102], preferenceTagIds: [201, 301], consumptionType: 'ost' };
// Synthetic test input only; this file never contacts Labs or a real account.
const profile = { nickname: '  로컬 검증 회원  ' };

// The complete API client, consent mapper, onboarding service, and bookmark
// controls share one module graph. Only the HTTP/browser boundary is replaced.
function modulesFor(fixture, { cookie = '', beforeComplete, loseCompleteResponse = false } = {}) {
  const cache = new Map();
  let jar = cookie;
  let loseResponse = loseCompleteResponse;
  const browser = new EventTarget();
  browser.location = { origin: 'http://localhost:3180', hostname: 'localhost' };
  const context = vm.createContext({ URL, URLSearchParams, Headers, Response, AbortController, DOMException, Event, console,
    window: browser,
    process: { env: { NEXT_PUBLIC_API_BASE_URL: 'http://localhost:8180', NEXT_PUBLIC_PIVOT_ENABLE_LOCAL_API: 'true' } },
    fetch: async (url, init = {}) => {
      assert.equal(new URL(url).origin, 'http://localhost:8180', 'requests must stay inside the fixture boundary');
      assert.equal(init.credentials, 'include');
      assert.equal(init.cache, 'no-store');
      const isComplete = new URL(url).pathname === completePath && init.method === 'POST';
      if (isComplete && beforeComplete) await beforeComplete();
      const headers = new Headers(init.headers);
      if (jar) headers.set('Cookie', jar);
      const response = fixture.dispatch(url, { ...init, headers });
      if (response.headers?.['Set-Cookie']) jar = response.headers['Set-Cookie'].split(';')[0];
      if (isComplete && loseResponse) {
        loseResponse = false;
        throw new TypeError('The completion response was lost after the server committed');
      }
      return new Response(response.status === 204 ? null : JSON.stringify(response.body ?? null), {
        status: response.status, headers: response.headers,
      });
    },
  });
  function load(relative) {
    const file = path.resolve(root, relative);
    if (cache.has(file)) return cache.get(file).exports;
    const mod = { exports: {} }; cache.set(file, mod);
    const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    }).outputText;
    const run = vm.runInContext(`(function(require,module,exports){${code}\n})`, context, { filename: relative });
    run(specifier => {
      const imported = specifier.startsWith('@/') ? path.resolve(root, specifier.slice(2)) : path.resolve(path.dirname(file), specifier);
      return load(path.relative(root, fs.existsSync(imported + '.ts') ? imported + '.ts' : imported));
    }, mod, mod.exports);
    return mod.exports;
  }
  return { load, get cookie() { return jar; } };
}

async function pendingFlow(options = {}) {
  const fixture = createApiFixture({ strictOnboarding: true, oauthCallbackPhase: 'onboarding_pending', topicId: 271 });
  fixture.rotate('onboarding_pending');
  const modules = modulesFor(fixture, options);
  const api = modules.load('lib/api.ts');
  const session = await api.getSession();
  assert.equal(session.sessionState, 'onboarding_pending');
  assert.equal(session.user, null, 'pending accounts have no public user details');
  const service = modules.load('lib/pivot/onboarding-service.ts').createOnboardingService();
  async function consent(optionalAgreed = false) {
    const policies = await modules.load('lib/pivot/services.ts').loadPivotPolicies();
    const consentModule = modules.load('lib/pivot/consent.ts');
    const choices = Object.fromEntries(policies.map(policy => [consentModule.policyKey(policy), policy.required || optionalAgreed]));
    return consentModule.createConsentDraft(policies, choices);
  }
  return { fixture, modules, api, service, consent };
}
const completions = fixture => fixture.requests.filter(request => request.method === 'POST' && request.path === completePath);

for (const optionalAgreed of [false, true]) {
  test(`pending → exact completion DTO → rotated session → bookmark uses new CSRF (optional consent ${optionalAgreed})`, async () => {
    const flow = await pendingFlow();
    const draft = await flow.consent(optionalAgreed);
    const result = await flow.service.submitSelection(selection, draft, profile);
    assert.equal(result.recovered, false);
    assert.equal(result.session.sessionState, 'active');
    assert.equal(result.session.user.accountType, 'user');
    assert.equal(flow.fixture.state.onboardingCompletions, 1);
    const post = completions(flow.fixture)[0];
    assert.deepEqual(post.body, {
      nickname: '로컬 검증 회원', accountType: 'user', topicId: 271,
      consents: [
        { type: 'terms', policyVersion: 'fixture-v1', agreed: true },
        { type: 'privacy', policyVersion: 'fixture-v2', agreed: true },
        { type: 'marketing', policyVersion: 'fixture-v1', agreed: optionalAgreed },
      ],
      answers: { version: 2, favoriteEntityIds: [101, 102], preferenceTagIds: [201, 301], consumptionType: 'ost' },
    });
    assert.equal(post.csrf, 'fixture-onboarding_pending-csrf');
    assert.equal(post.cookie, 'fixture_session=fixture-onboarding_pending');
    assert.equal(flow.modules.cookie, 'fixture_session=fixture-active');
    const controls = flow.modules.load('lib/pivot/bookmark-service.ts').createServiceBookmarkControls();
    await controls.toggle({ postId: '41', contentId: '41', topicId: 271 });
    const write = flow.fixture.requests.find(request => request.method === 'PUT');
    assert.deepEqual(write.body, { topicId: 271 });
    assert.equal(write.csrf, 'fixture-active-csrf', 'the pending CSRF cannot be reused after onboarding');
    assert.equal(write.cookie, 'fixture_session=fixture-active');
    assert.deepEqual(plain((await flow.api.getBookmarks({ topicId: 271 })).items.map(item => item.contentId)), [41]);
    const completeIndex = flow.fixture.requests.indexOf(post);
    const bookmarkIndex = flow.fixture.requests.indexOf(write);
    assert.ok(flow.fixture.requests.slice(completeIndex + 1, bookmarkIndex).some(request => request.path === '/api/v1/auth/session'),
      'the new token is read after completion and before the first content write');
  });
}

test('policy version changes reject the old consent, keep pending state, and require a newly reviewed draft', async () => {
  const flow = await pendingFlow();
  const previousDraft = await flow.consent();
  flow.fixture.policies[0].policyVersion = 'fixture-v3';
  await assert.rejects(flow.service.submitSelection(selection, previousDraft, profile),
    error => error.status === 409 && error.code === 'POLICY_VERSION_CONFLICT');
  assert.equal(flow.fixture.state.phase, 'onboarding_pending');
  assert.equal(flow.fixture.state.onboardingCompletions, 0);
  assert.equal(completions(flow.fixture).length, 1, 'a stale consent must not be silently retried or upgraded');
  assert.equal(completions(flow.fixture)[0].body.consents[0].policyVersion, 'fixture-v1');
  const reviewedDraft = await flow.consent();
  const result = await flow.service.submitSelection(selection, reviewedDraft, profile);
  assert.equal(result.session.sessionState, 'active');
  assert.equal(completions(flow.fixture)[1].body.consents[0].policyVersion, 'fixture-v3');
  assert.equal(flow.fixture.state.onboardingCompletions, 1);
});

test('failed submit checks the session without repeating the write; a later explicit retry can succeed', async () => {
  const flow = await pendingFlow();
  const draft = await flow.consent();
  flow.fixture.state.failNext = { method: 'POST', path: completePath, status: 503 };
  await assert.rejects(flow.service.submitSelection(selection, draft, profile), error => error.status === 503);
  assert.equal(flow.fixture.state.phase, 'onboarding_pending');
  assert.equal(flow.fixture.state.onboardingCompletions, 0);
  assert.equal(completions(flow.fixture).length, 1);
  assert.equal(flow.fixture.requests.at(-1).path, '/api/v1/auth/session');
  await flow.service.submitSelection(selection, draft, profile);
  assert.equal(flow.fixture.state.onboardingCompletions, 1);
  assert.equal(completions(flow.fixture).length, 2);
  assert.deepEqual(completions(flow.fixture)[0].body, completions(flow.fixture)[1].body);
});

test('overlapping submissions produce one HTTP completion and one session rotation', async () => {
  let release;
  let reached;
  const held = new Promise(resolve => { release = resolve; });
  const entered = new Promise(resolve => { reached = resolve; });
  const flow = await pendingFlow({ beforeComplete: async () => { reached(); await held; } });
  const draft = await flow.consent();
  const first = flow.service.submitSelection(selection, draft, profile);
  const second = flow.service.submitSelection(selection, draft, profile);
  await entered;
  assert.equal(flow.fixture.state.phase, 'onboarding_pending');
  release();
  const results = await Promise.all([first, second]);
  assert.ok(results.every(result => result.session.sessionState === 'active'));
  assert.equal(completions(flow.fixture).length, 1);
  assert.equal(flow.fixture.state.onboardingCompletions, 1);
});

test('a lost successful response recovers from the active session and never repeats personal-data submission', async () => {
  const flow = await pendingFlow({ loseCompleteResponse: true });
  const result = await flow.service.submitSelection(selection, await flow.consent(), profile);
  assert.equal(result.recovered, true);
  assert.equal(result.session.sessionState, 'active');
  assert.equal(completions(flow.fixture).length, 1);
  assert.equal(flow.fixture.state.onboardingCompletions, 1);
  await flow.api.bookmarkContent(42, 271);
  assert.equal(flow.fixture.requests.at(-1).csrf, 'fixture-active-csrf');
  assert.equal(flow.fixture.state.saved.has(42), true);
});

test('a refreshed completed session does not submit the old onboarding form again', async () => {
  const flow = await pendingFlow();
  const draft = await flow.consent();
  await flow.service.submitSelection(selection, draft, profile);
  const reloaded = modulesFor(flow.fixture, { cookie: flow.modules.cookie });
  const service = reloaded.load('lib/pivot/onboarding-service.ts').createOnboardingService();
  const result = await service.submitSelection(selection, draft, profile);
  assert.equal(result.recovered, true);
  assert.equal(result.session.sessionState, 'active');
  assert.equal(completions(flow.fixture).length, 1);
  assert.equal(flow.fixture.state.onboardingCompletions, 1);
});

test('search → full Topic candidates → capped selections → completion uses real catalog IDs and the current CSRF',async()=>{
  const flow=await pendingFlow();
  const catalog=flow.modules.load('lib/pivot/onboarding-catalog.ts').serviceCatalog;
  const topicId=await catalog.resolveTopic();
  assert.equal(topicId,271);
  assert.equal((await catalog.search(topicId,'')).items.length,0);
  const works=await catalog.search(topicId,'Fixture Anime');
  assert.equal(works.items.length,6);
  assert.equal(works.items.at(-1).selectable,false);
  flow.fixture.state.csrf='fixture-new-pending-csrf';
  const candidates=await catalog.options(topicId,[102,101]);
  assert.deepEqual(plain(candidates.entityIds),[101,102]);
  const tags=[...candidates.genres,...candidates.themes];
  assert.equal(tags.length,12);
  assert.equal(candidates.maxSelections,null);
  assert.equal(candidates.maxSelectionsPerKind,7);
  assert.equal(candidates.minSelections,2);
  assert.equal(candidates.minGenreSelections,1);
  assert.equal(candidates.minThemeSelections,1);
  assert.equal(flow.fixture.state.onboardingCompletions,0);
  assert.equal(flow.fixture.state.onboarding,null,'candidate lookup must never save a partial signup');
  assert.equal(flow.fixture.requests.at(-1).csrf,'fixture-new-pending-csrf');
  const chosen=[...candidates.genres,...candidates.themes.slice(0,7)];
  const result=await flow.service.submitSelection({...selection,favoriteEntityIds:[102,101],preferenceTagIds:chosen.map(tag=>tag.tagId)},await flow.consent(),profile);
  assert.equal(result.session.sessionState,'active');
  assert.equal(flow.fixture.state.onboarding.answers.preferenceTagIds.length,8);
  assert.equal(Object.hasOwn(flow.fixture.state.onboarding,'birthDate'),false);
});

test('more than seven selections of either kind are rejected without partial signup',async()=>{
  for(const kind of ['genre','theme']) {
    const flow=await pendingFlow();
    const original=flow.fixture.tags.find(tag=>tag.kind===kind);
    const extra=Array.from({length:7},(_,i)=>({...original,tagId:801+i,code:'overflow_'+i,sourceEntityIds:[]}));
    flow.fixture.tags.push(...extra);
    const opposite=flow.fixture.tags.find(tag=>tag.kind!==kind);
    await assert.rejects(flow.service.submitSelection({...selection,preferenceTagIds:[original.tagId,...extra.map(tag=>tag.tagId),opposite.tagId]},await flow.consent(),profile),error=>error.code==='VALIDATION_ERROR');
    assert.equal(flow.fixture.state.phase,'onboarding_pending');
    assert.equal(flow.fixture.state.onboardingCompletions,0);
    assert.equal(flow.fixture.state.onboarding,null);
  }
});

test('same-kind-only selections are rejected at completion without partial signup',async()=>{
  for(const kind of ['genre','theme']) {
    const flow=await pendingFlow();
    const original=flow.fixture.tags.find(tag=>tag.kind===kind);
    flow.fixture.tags.push({...original,tagId:999,code:'additional_'+kind});
    await assert.rejects(flow.service.submitSelection({...selection,preferenceTagIds:[original.tagId,999]},await flow.consent(),profile),error=>error.code==='VALIDATION_ERROR');
    assert.equal(flow.fixture.state.phase,'onboarding_pending');
    assert.equal(flow.fixture.state.onboardingCompletions,0);
    assert.equal(flow.fixture.state.onboarding,null);
  }
});

test('a candidate removed after selection is rejected at completion and cannot fabricate signup success',async()=>{
  const flow=await pendingFlow();
  const catalog=flow.modules.load('lib/pivot/onboarding-catalog.ts').serviceCatalog;
  await catalog.options(271,[101,102]);
  flow.fixture.tags.splice(flow.fixture.tags.findIndex(tag=>tag.tagId===301),1);
  await assert.rejects(flow.service.submitSelection(selection,await flow.consent(),profile),error=>error.code==='VALIDATION_ERROR');
  assert.equal(flow.fixture.state.phase,'onboarding_pending');
  assert.equal(flow.fixture.state.onboardingCompletions,0);
  assert.equal(flow.fixture.state.onboarding,null);
});
