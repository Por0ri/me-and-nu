const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');

function load(relative, dependencies = {}) {
  const mod = { exports: {} };
  const code = ts.transpileModule(fs.readFileSync(path.join(__dirname, '..', relative), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  vm.runInNewContext(code, { module:mod, exports:mod.exports, URL, Date, AbortController, setTimeout, clearTimeout, process:{env:{}},
    require(name) { if (name === '../request-deadline') return load('lib/request-deadline.ts'); if (!(name in dependencies)) throw Error('Unexpected dependency: '+name); return dependencies[name]; },
  });
  return mod.exports;
}
class ApiError extends Error {
  constructor(message, status, code, fields) { super(message); this.status=status; this.code=code; this.fields=fields; }
}
// What a server from before the Q3 ranking (pydantic extra=forbid) answers to consumptionTypes, as lib/api.ts reads it.
const rankingRejected = () => new ApiError('입력값을 확인해 주세요.',422,'VALIDATION_ERROR',
  [{field:'answers.consumptionTypes',code:'VALIDATION_ERROR',message:'Extra inputs are not permitted'}]);
const apiModule = {ApiError};
const services = load('lib/pivot/services.ts', {'@/lib/api':apiModule, './consent':{}});
const service = load('lib/pivot/onboarding-service.ts', {
  '@/lib/api':apiModule,
  './onboarding':load('lib/pivot/onboarding.ts'), './services':services,
});
const plain = value => JSON.parse(JSON.stringify(value));
const profile = {nickname:'  새로운회원  '};
const selection = {interest:'animation',topicId:83,favoriteEntityIds:[101,102],preferenceTagIds:[201,301],consumptionTypes:['ost','news','world']};
const answers = {version:2,favoriteEntityIds:[101,102],preferenceTagIds:[201,301],consumptionTypes:['ost','news','world'],consumptionType:'ost'};
// A server from before the Q3 ranking takes and returns rank 1 alone.
const { consumptionTypes: _ranking, ...rankOne } = answers;
const consent = {consents:[
  {type:'terms',policyVersion:'terms-2026',agreed:true},
  {type:'privacy',policyVersion:'privacy-2026',agreed:true},
  {type:'marketing',policyVersion:'marketing-2026',agreed:false},
]};
const pending = {authenticated:true,sessionState:'onboarding_pending',onboardingCompleted:false,user:null,csrfToken:'before'};
const active = {authenticated:true,sessionState:'active',onboardingCompleted:true,user:{id:51,accountType:'user'},csrfToken:'rotated'};
const topics = {topics:[{id:12,code:'movie',name:'영화'},{id:83,code:'anime',name:'애니메이션'}]};
const response = {user:active.user,onboardingCompleted:true,activeTopic:{id:83,code:'anime',name:'애니메이션',subtopicIds:[]},answers};

function setup(overrides = {}, guard = () => {}) {
  const calls = [];
  let reads = 0;
  const api = {
    async getSession() {calls.push('session'); return reads++ ? active : pending;},
    async getTopics() {calls.push('topics'); return topics;},
    async complete(input) {calls.push({input:plain(input)}); return response;},
    ...overrides,
  };
  return {calls,service:service.createOnboardingService(api,guard)};
}

test('Profile validation trims nicknames and follows the backend rule (2~20 Korean, English or digits)', () => {
  assert.deepEqual(plain(service.validateOnboardingProfile(profile)), {valid:true,profile:{nickname:'새로운회원'}});
  for (const nickname of ['미뉴덕후','menu','Menu12','ㅋㅋ','가'.repeat(20),' 미뉴 ']) {
    assert.equal(service.validateOnboardingProfile({nickname}).valid,true,nickname);
  }
  for (const nickname of ['','  ','미','가'.repeat(21),'미뉴 덕후','미뉴!','me_nu','😀😀']) {
    assert.equal(service.validateOnboardingProfile({nickname}).valid,false,nickname);
  }
  assert.equal(service.nicknameProblem('미뉴 덕후'),'characters');
  assert.equal(service.nicknameProblem('미'),'length');
  assert.equal(service.nicknameProblem('미뉴덕후'),null);
});

test('Nickname check asks the onboarding API about the typed nickname and fails when the API is off', async () => {
  const calls = [];
  const checker = load('lib/pivot/onboarding-service.ts', {
    '@/lib/api': {...apiModule, apiRequest: async (path) => { calls.push(path); return {nickname:'공룡',available:false,reason:'taken'}; }},
    './onboarding': load('lib/pivot/onboarding.ts'), './services': services,
  });
  assert.equal((await checker.checkNickname('공룡', undefined, () => {})).reason, 'taken');
  assert.deepEqual(calls, ['/api/v1/onboarding/nickname?nickname=' + encodeURIComponent('공룡')]);
  await assert.rejects(checker.checkNickname('공룡', undefined, () => { throw new Error('API off'); }));
  assert.equal(calls.length, 1);
});

test('The screen marks each broken part of what is typed, a space at either end included', () => {
  const breaks = nickname => plain(service.nicknameRuleBreaks(nickname));
  assert.deepEqual(breaks(''), {length:false,characters:false});
  assert.deepEqual(breaks('미뉴덕후'), {length:false,characters:false});
  assert.deepEqual(breaks('미'), {length:true,characters:false});
  assert.deepEqual(breaks('가'.repeat(21)), {length:true,characters:false});
  assert.deepEqual(breaks('킹갓디노 '), {length:false,characters:true});
  assert.deepEqual(breaks('!'), {length:true,characters:true});
});

test('Deferred birth-date input is neither validated as an age policy nor carried into the profile DTO', async () => {
  for (const birthDate of [undefined, '', 'invalid', '2099-01-01']) {
    assert.deepEqual(plain(service.validateOnboardingProfile({...profile,birthDate})), {valid:true,profile:{nickname:'새로운회원'}});
    const app = setup();
    await app.service.submitSelection(selection,consent,{...profile,birthDate});
    assert.equal(Object.hasOwn(app.calls.find(call => call.input).input, 'birthDate'),false);
  }
});

test('Completion resolves the current anime ID and sends only the current backend DTO', async () => {
  const app = setup();
  const result = await app.service.submitSelection(selection,consent,profile);
  assert.deepEqual(app.calls, ['session','topics',{input:{
    nickname:'새로운회원',accountType:'user',topicId:83,
    consents:consent.consents,answers,
  }},'session']);
  assert.equal(result.session,active);
  assert.equal(result.recovered,false);
  assert.equal(result.session.csrfToken,'rotated');
});

test('Simultaneous clicks share one completion request', async () => {
  let finish;
  let posts = 0;
  const app = setup({async complete() {posts++; return new Promise(resolve => {finish=resolve;});}});
  const first = app.service.submitSelection(selection,consent,profile);
  const second = app.service.submitSelection(selection,consent,profile);
  assert.equal(first,second);
  while (!finish) await new Promise(resolve => setImmediate(resolve));
  finish(response);
  assert.equal((await first).session,active);
  await second;
  assert.equal(posts,1);
});

test('An already active account is recovered without a Topic read or another POST', async () => {
  let reads = 0;
  const app = setup({async getSession(){reads++;return active;}});
  const result = await app.service.submitSelection(selection,consent,profile);
  assert.equal(result.recovered,true);
  assert.equal(reads,1);
  assert.deepEqual(app.calls,[]);
});

for (const failure of [new TypeError('response lost'),new ApiError('already complete',409,'ONBOARDING_ALREADY_COMPLETED')]) {
  test(`A committed account recovers after ${failure.message}`, async () => {
    const app = setup({async complete(){throw failure;}});
    const result = await app.service.submitSelection(selection,consent,profile);
    assert.equal(result.recovered,true);
    assert.equal(result.session,active);
  });
}

test('A failed final session read can be retried without repeating a successful POST', async () => {
  let reads = 0;
  let posts = 0;
  const app = setup({
    async getSession(){reads++; if(reads===2) throw Error('session response lost'); return reads===1 ? pending : active;},
    async complete(){posts++;return response;},
  });
  await assert.rejects(app.service.submitSelection(selection,consent,profile), /session response lost/);
  const result = await app.service.submitSelection(selection,consent,profile);
  assert.equal(result.recovered,true);
  assert.equal(posts,1);
});

test('Typed policy-version and required-consent errors reach the UI unchanged', async () => {
  for (const failure of [new ApiError('changed policy',409,'POLICY_VERSION_CONFLICT'),new ApiError('required consent',422,'REQUIRED_CONSENT_MISSING')]) {
    let reads = 0;
    const app = setup({async getSession(){reads++;return pending;},async complete(){throw failure;}});
    await assert.rejects(app.service.submitSelection(selection,consent,profile), error => error === failure);
    assert.equal(reads,1,'No success recovery may hide a policy error');
  }
});

test('A failed write remains a failure if no active session can be established', async () => {
  const failure = new ApiError('temporary failure',503,'UNAVAILABLE');
  const app = setup({async getSession(){return pending;},async complete(){throw failure;}});
  await assert.rejects(app.service.submitSelection(selection,consent,profile), error => error === failure);
});

test('Anonymous and inconsistent sessions cannot submit onboarding', async () => {
  for (const session of [{...pending,authenticated:false,sessionState:'anonymous'}, {...pending,user:{id:51,accountType:'user'}}]) {
    const app = setup({async getSession(){return session;}});
    await assert.rejects(app.service.submitSelection(selection,consent,profile), error => error instanceof ApiError);
    assert.deepEqual(app.calls,[]);
  }
});

test('Unavailable, ambiguous or invalid anime IDs never produce a completion request', async () => {
  for (const result of [{topics:[]},{topics:[topics.topics[1],topics.topics[1]]},{topics:[{...topics.topics[1],id:0}]}]) {
    const app = setup({async getTopics(){return result;}});
    await assert.rejects(app.service.submitSelection(selection,consent,profile), /관심 분야/);
    assert.deepEqual(app.calls,['session']);
  }
});

test('Guard and malformed input failures occur before any session or API calls', async () => {
  const denied = setup({},() => {throw Error('origin denied');});
  await assert.rejects(denied.service.submitSelection(selection,consent,profile), /origin denied/);
  assert.deepEqual(denied.calls,[]);
  for (const [choice,draft,person] of [
    [selection,consent,{...profile,nickname:''}],
    [{...selection,consumptionTypes:['invalid']},consent,profile],
    [{...selection,consumptionTypes:[]},consent,profile],
    [{...selection,consumptionTypes:['ost','ost']},consent,profile],
    [{...selection,consumptionTypes:undefined,consumptionType:'ost'},consent,profile],
    [{...selection,favoriteEntityIds:[]},consent,profile],
    [{...selection,favoriteEntityIds:[1,2,3,4,5,6]},consent,profile],
    [{...selection,favoriteEntityIds:[101,101]},consent,profile],
    [{...selection,preferenceTagIds:[]},consent,profile],
    [{...selection,preferenceTagIds:[201]},consent,profile],
    [{...selection,preferenceTagIds:Array.from({length:15},(_,i)=>i+1)},consent,profile],
    [{...selection,preferenceTagIds:[201,'301']},consent,profile],
    [selection,{consents:[]},profile],
    [selection,{consents:[consent.consents[0],consent.consents[0]]},profile],
    [selection,{consents:[{...consent.consents[0],agreed:'true'}]},profile],
  ]) {
    const app = setup();
    await assert.rejects(app.service.submitSelection(choice,draft,person));
    assert.deepEqual(app.calls,[]);
  }
});

test('Invalid completion payloads and an unconfirmed active session do not fabricate success', async () => {
  for (const result of [{...response,answers:{...answers,consumptionType:'world'}},{...response,answers:{...answers,version:1}},
    {...response,answers:{...answers,consumptionTypes:['news','ost','world']}},{...response,answers:{...answers,consumptionTypes:['ost','news']}},
    {...response,answers:{...answers,consumptionTypes:'ost'}},{...response,answers:{...rankOne,consumptionType:'news'}},
    {...response,answers:{...answers,favoriteEntityIds:[101,101]}},{...response,answers:{...answers,preferenceTagIds:[201,999]}},
    {...response,activeTopic:{...response.activeTopic,id:1}},null]) {
    const app = setup({async complete(){return result;}});
    await assert.rejects(app.service.submitSelection(selection,consent,profile), /완료 응답/);
  }
  const app = setup({async getSession(){return pending;}});
  await assert.rejects(app.service.submitSelection(selection,consent,profile), /로그인 상태/);
});

test('Completion accepts set-equivalent IDs regardless of response order', async () => {
  const app = setup({async complete(){return {...response,answers:{...answers,favoriteEntityIds:[102,101],preferenceTagIds:[301,201]}};}});
  assert.equal((await app.service.submitSelection(selection,consent,profile)).recovered,false);
});

test('Q3 goes as the ranking in tap order plus rank 1 as consumptionType', async () => {
  let sent;
  const app = setup({async complete(input){sent=plain(input.answers); return {...response,answers:input.answers};}});
  await app.service.submitSelection({...selection,consumptionTypes:['world','ost']},consent,profile);
  assert.deepEqual(sent,{version:2,favoriteEntityIds:[101,102],preferenceTagIds:[201,301],consumptionTypes:['world','ost'],consumptionType:'world'});
  assert.deepEqual(plain(service.answersV2({...selection,consumptionTypes:['news']})),
    {version:2,favoriteEntityIds:[101,102],preferenceTagIds:[201,301],consumptionTypes:['news'],consumptionType:'news'});
});

test('Completion accepts a response from a server before the ranking when its one answer is rank 1', async () => {
  const app = setup({async complete(){return {...response,answers:rankOne};}});
  assert.equal((await app.service.submitSelection(selection,consent,profile)).recovered,false);
  assert.equal(service.sameConsumptionAnswer({consumptionType:'ost'},answers),true);
  assert.equal(service.sameConsumptionAnswer({consumptionType:'ost',consumptionTypes:null},answers),true);
  assert.equal(service.sameConsumptionAnswer({consumptionType:'ost',consumptionTypes:['ost','news','world']},answers),true);
  assert.equal(service.sameConsumptionAnswer({consumptionType:'news'},answers),false);
  assert.equal(service.sameConsumptionAnswer({consumptionType:'ost',consumptionTypes:['ost','world','news']},answers),false);
  assert.equal(service.sameConsumptionAnswer({consumptionType:'ost',consumptionTypes:['ost']},rankOne),true,'after the fallback only rank 1 was sent');
  assert.equal(service.sameConsumptionAnswer(null,answers),false);
});

test('A server from before the ranking gets the answers once more with rank 1 alone', async () => {
  const posts = [];
  const app = setup({async complete(input){posts.push(plain(input.answers)); if (posts.length===1) throw rankingRejected(); return {...response,answers:rankOne};}});
  const result = await app.service.submitSelection(selection,consent,profile);
  assert.equal(result.recovered,false);
  assert.equal(result.session,active);
  assert.deepEqual(posts,[answers,rankOne]);
  assert.equal(Object.hasOwn(posts[1],'consumptionTypes'),false);
});

test('The rank 1 fallback is sent once, and other 422s are never resent', async () => {
  let posts = 0;
  const twice = setup({async complete(){posts++; throw rankingRejected();}});
  await assert.rejects(twice.service.submitSelection(selection,consent,profile), error => error.code==='VALIDATION_ERROR' && error.status===422);
  assert.equal(posts,2,'one retry, no loop');
  for (const failure of [
    new ApiError('nickname',422,'VALIDATION_ERROR',[{field:'nickname',code:'VALIDATION_ERROR',message:'rule'}]),
    new ApiError('tags',422,'VALIDATION_ERROR',[{field:'answers.preferenceTagIds',code:'VALIDATION_ERROR',message:'kind'}]),
    new ApiError('no fields',422,'VALIDATION_ERROR'),
    new ApiError('odd fields',422,'VALIDATION_ERROR',{field:'answers.consumptionTypes'}),
    new ApiError('nested',422,'VALIDATION_ERROR',[{field:'answers.consumptionTypes.0',code:'VALIDATION_ERROR',message:'literal'}]),
    new ApiError('consent',422,'REQUIRED_CONSENT_MISSING',[{field:'answers.consumptionTypes',code:'REQUIRED_CONSENT_MISSING',message:'x'}]),
    new ApiError('conflict',409,'VALIDATION_ERROR',[{field:'answers.consumptionTypes',code:'VALIDATION_ERROR',message:'x'}]),
    Object.assign(new Error('not an ApiError'),{status:422,code:'VALIDATION_ERROR',fields:[{field:'answers.consumptionTypes'}]}),
  ]) {
    let sent = 0;
    const app = setup({async getSession(){return pending;},async complete(){sent++; throw failure;}});
    await assert.rejects(app.service.submitSelection(selection,consent,profile), error => error === failure);
    assert.equal(sent,1,failure.message);
  }
});

test('A changed anime Topic invalidates the earlier catalog selection before posting', async () => {
  const app = setup();
  await assert.rejects(app.service.submitSelection({...selection,topicId:999},consent,profile),/관심 분야/);
  assert.deepEqual(app.calls,['session','topics']);
});
