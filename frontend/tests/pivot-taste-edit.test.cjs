// My Page taste edit (temporary, 2026-11 user test): member-only API paths, saved-answer prefill and save payload.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const plain=value=>JSON.parse(JSON.stringify(value));
function load(relative,dependencies={}) {
  const mod={exports:{}};
  const source=fs.readFileSync(path.join(__dirname,'..',relative),'utf8');
  const output=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
  vm.runInNewContext(output,{module:mod,exports:mod.exports,URLSearchParams,require(name){
    if(!(name in dependencies)) throw new Error('Unexpected dependency: '+name);
    return dependencies[name];
  }});
  return mod.exports;
}
const onboarding=load('lib/pivot/onboarding.ts');
class ApiError extends Error {
  constructor(message,status,code,fields){super(message);this.status=status;this.code=code;this.fields=fields;}
}
// What a server from before the Q3 ranking (pydantic extra=forbid) answers to consumptionTypes, as lib/api.ts reads it.
const rankingRejected=()=>new ApiError('입력값을 확인해 주세요.',422,'VALIDATION_ERROR',[{field:'answers.consumptionTypes',code:'VALIDATION_ERROR',message:'Extra inputs are not permitted'}]);
const work=(entityId,selectable=true)=>({entityId,name:`작품 ${entityId}`,aliases:[],imageUrl:null,releaseYear:2024,mediaFormat:'TV',selectable,unavailableReason:null});
const tag=(tagId,kind)=>({tagId,kind,code:`c${tagId}`,label:`칩 ${tagId}`,sourceEntityIds:[]});
const taste={topicId:83,works:[work(11),work(12)],preferenceTagIds:[101,103],consumptionType:'world',consumptionTypes:['world','ost']};
function setup(request) {
  const topics=async()=>({topics:[{id:83,code:'anime',name:'애니메이션'}]});
  const api={ApiError,apiRequest:request,getTopics:topics,getSession:async()=>({})};
  const catalog=load('lib/pivot/onboarding-catalog.ts',{
    '@/lib/api':api,
    './onboarding-service':{animeTopicId:response=>response.topics[0].id},'./onboarding':onboarding,'./services':{requirePivotApi(){}},
  });
  // The real answer helpers (ranking payload, the rank 1 fallback, the response check); only the Topic lookup is simplified.
  const service={...load('lib/pivot/onboarding-service.ts',{'@/lib/api':api,'./onboarding':onboarding,'./services':{requirePivotApi(){}}}),animeTopicId:response=>response.topics[0].id};
  return load('lib/pivot/taste-edit.ts',{
    '@/lib/api':api,
    './onboarding':onboarding,'./onboarding-catalog':catalog,
    './onboarding-service':service,'./services':{requirePivotApi(){}},
  });
}

test('Taste edit reads, searches and lists chips through the member-only /me/taste endpoints',async()=>{
  const calls=[];
  const m=setup(async(url,init={})=>{
    calls.push([url,init.method??'GET']);
    if(url.startsWith('/api/v1/me/taste/works')) return {items:[work(13)],nextCursor:null};
    if(url==='/api/v1/me/taste/preference-options') return {entityIds:[13],genres:[tag(101,'genre')],themes:[tag(103,'theme')],minSelections:2,minGenreSelections:1,minThemeSelections:1,maxSelections:null,maxSelectionsPerKind:7};
    return taste;
  });
  assert.deepEqual(plain(await m.loadMyTaste()),taste);
  await m.tasteCatalog.search(83,' 하이큐 ');
  await m.tasteCatalog.options(83,[13]);
  assert.deepEqual(calls,[['/api/v1/me/taste?topicId=83','GET'],['/api/v1/me/taste/works?topicId=83&q=%ED%95%98%EC%9D%B4%ED%81%90&limit=20','GET'],['/api/v1/me/taste/preference-options','POST']]);
});

test('Saved answers open Q1 prefilled and a changed work set keeps the chosen chips',()=>{
  const m=setup(async()=>taste);
  const state=m.tasteEditState({...taste,works:[work(11),work(12,false)]});
  assert.equal(state.step,'q1');
  assert.deepEqual(plain(state.favoriteWorks.map(item=>item.entityId)),[11]);          // an inactive work cannot stay selected
  assert.deepEqual(plain(state.preferenceTagIds),[101,103]);
  assert.deepEqual(plain(state.consumptionTypes),['world','ost']);
  const next=m.tasteEditReducer(state,{type:'toggle-work',work:work(13)});
  assert.deepEqual(plain(next.favoriteWorks.map(item=>item.entityId)),[11,13]);
  assert.deepEqual(plain(next.preferenceTagIds),[101,103]);
  assert.equal(next.optionsKey,null);                                           // Q2 reloads the chip list
  assert.equal(onboarding.onboardingReducer(state,{type:'toggle-work',work:work(13)}).preferenceTagIds.length,0);
});

const selection={interest:'animation',topicId:83,favoriteEntityIds:[11,12],preferenceTagIds:[101,103],consumptionTypes:['news','world']};
const answers={version:2,favoriteEntityIds:[11,12],preferenceTagIds:[101,103],consumptionTypes:['news','world'],consumptionType:'news'};

test('Saving sends the version 2 answers with the Q3 ranking and rank 1 by PUT, and rejects a response for another Topic',async()=>{
  const sent=[];
  const m=setup(async(url,init)=>{sent.push([url,init.method,JSON.parse(init.body)]);return {...taste,consumptionType:'news',consumptionTypes:['news','world']};});
  const saved=await m.saveMyTaste(selection);
  assert.equal(saved.consumptionType,'news');
  assert.deepEqual(plain(saved.consumptionTypes),['news','world']);
  assert.deepEqual(sent,[['/api/v1/me/taste','PUT',{topicId:83,answers}]]);
  const other=setup(async()=>({...taste,topicId:84}));
  await assert.rejects(other.saveMyTaste(selection),/내 취향을 확인하지 못했어요/);
});

test('A save is checked against the ranking sent; a server from before the ranking may answer with rank 1 alone',async()=>{
  const reply=body=>setup(async()=>({...taste,...body})).saveMyTaste(selection);
  assert.deepEqual(plain((await reply({consumptionType:'news',consumptionTypes:undefined})).consumptionTypes),['news'],'old response: rank 1 is what was saved');
  for(const body of [{consumptionType:'news',consumptionTypes:['world','news']},{consumptionType:'world',consumptionTypes:['world','news']},
    {consumptionType:'news',consumptionTypes:['news']},{consumptionType:'world',consumptionTypes:undefined},{consumptionType:null,consumptionTypes:[]}]) {
    await assert.rejects(reply(body),/내 취향을 확인하지 못했어요/,JSON.stringify(body));
  }
});

test('A server from before the ranking gets the taste once more with rank 1 alone, and other 422s are not resent',async()=>{
  const sent=[];
  const m=setup(async(url,init)=>{
    const body=JSON.parse(init.body);sent.push(body);
    if(Object.hasOwn(body.answers,'consumptionTypes')) throw rankingRejected();
    return {topicId:83,works:taste.works,preferenceTagIds:[101,103],consumptionType:body.answers.consumptionType};
  });
  const saved=await m.saveMyTaste(selection);
  assert.deepEqual(plain(saved.consumptionTypes),['news']);
  const {consumptionTypes:_ranking,...rankOne}=answers;
  assert.deepEqual(sent,[{topicId:83,answers},{topicId:83,answers:rankOne}]);
  let tries=0;
  await assert.rejects(setup(async()=>{tries++;throw rankingRejected();}).saveMyTaste(selection),error=>error.status===422);
  assert.equal(tries,2,'one resend, never a loop');
  for(const failure of [new ApiError('tags',422,'VALIDATION_ERROR',[{field:'answers.preferenceTagIds',code:'VALIDATION_ERROR',message:'kind'}]),new ApiError('none',422,'VALIDATION_ERROR'),new ApiError('busy',503,'UNAVAILABLE')]) {
    let puts=0;
    await assert.rejects(setup(async()=>{puts++;throw failure;}).saveMyTaste(selection),error=>error===failure);
    assert.equal(puts,1,failure.message);
  }
});

test('Loading takes the saved ranking when it leads with consumptionType, else consumptionType alone, else nothing',async()=>{
  const load=async body=>plain((await setup(async()=>({...taste,...body})).loadMyTaste()).consumptionTypes);
  assert.deepEqual(await load({}),['world','ost']);
  assert.deepEqual(await load({consumptionTypes:['world','news','goods','ost','events','relationships']}),['world','news','goods','ost','events','relationships']);
  for(const consumptionTypes of [undefined,null,[],'world',['ost','world'],['world','world'],['world','releases'],['world','news','goods','ost','events','relationships','world']]) {
    assert.deepEqual(await load({consumptionTypes}),['world'],JSON.stringify(consumptionTypes));
  }
  assert.deepEqual(await load({consumptionType:null,consumptionTypes:[]}),[]);
  assert.deepEqual(await load({consumptionType:null,consumptionTypes:['world']}),[],'a ranking without rank 1 is not a saved answer');
  await assert.rejects(setup(async()=>({...taste,consumptionType:'releases'})).loadMyTaste(),/내 취향을 확인하지 못했어요/);
  const state=setup(async()=>taste).tasteEditState({...taste,consumptionTypes:['world','ost']});
  assert.deepEqual(plain(state.consumptionTypes),['world','ost']);
});

test('Unsaved taste compares answer sets, ignores navigation/metadata/order, and clears after reverting', () => {
  const m = setup(async () => taste), initial = m.tasteEditState(taste);
  for (const step of ['q1', 'q2', 'q3', 'result']) {
    assert.equal(m.hasTasteChanges({ ...initial, step, optionsKey: 'loaded', preferenceOptions: [tag(101, 'genre')] }, initial), false);
  }
  assert.equal(m.hasTasteChanges({ ...initial, favoriteWorks: [...initial.favoriteWorks].reverse(), preferenceTagIds: [...initial.preferenceTagIds].reverse() }, initial), false);
  for (const change of [{ favoriteWorks: [] }, { favoriteWorks: [work(13)] }, { preferenceTagIds: [] }, { consumptionTypes: ['news'] }, { consumptionTypes: [] },
    { consumptionTypes: ['world'] }, { consumptionTypes: ['world', 'ost', 'news'] }, { consumptionTypes: ['ost', 'world'] }, { topicId: 84 }]) {
    assert.equal(m.hasTasteChanges({ ...initial, ...change }, initial), true, JSON.stringify(change));
  }
  const removed = m.tasteEditReducer(initial, { type: 'toggle-work', work: initial.favoriteWorks[0] });
  assert.equal(m.hasTasteChanges(removed, initial), true);
  const restored = m.tasteEditReducer(removed, { type: 'toggle-work', work: initial.favoriteWorks[0] });
  assert.equal(m.hasTasteChanges(restored, initial), false);
});

test('Q3 in the taste edit: a new order is a change, the saved order again is not', () => {
  const m = setup(async () => taste), initial = m.tasteEditState(taste);
  const q3 = { ...initial, step: 'q3' };
  const tap = (state, value) => m.tasteEditReducer(state, { type: 'toggle-consumption', value });
  const reordered = tap(tap(q3, 'world'), 'world');
  assert.deepEqual(plain(reordered.consumptionTypes), ['ost', 'world'], 'world went out and came back last');
  assert.equal(m.hasTasteChanges(reordered, q3), true);
  const back = tap(tap(reordered, 'ost'), 'ost');
  assert.deepEqual(plain(back.consumptionTypes), ['world', 'ost']);
  assert.equal(m.hasTasteChanges(back, q3), false);
  assert.deepEqual(plain(onboarding.buildSelectionDraft({ ...reordered, preferenceOptions: [tag(101, 'genre'), tag(103, 'theme')], optionsKey: '11,12' }).consumptionTypes), ['ost', 'world']);
});

const hints={topicId:83,enough:true,contents:6,works:[{entityId:11,name:'진격의 거인',percent:62},{entityId:12,name:'나루토',percent:20}],
  genres:[{tagId:101,name:'코미디'},{tagId:102,name:'로맨스'}],themes:[{tagId:201,name:'성장'}],
  types:[{answer:'ost',percent:42},{answer:'news',percent:20},{answer:'goods',percent:18},{answer:'events',percent:8},{answer:'world',percent:7},{answer:'relationships',percent:5}]};
const {topicId:_topic,...shownHints}=hints;

test('Hints come from /me/taste/hints, and an unreadable or failed answer leaves the edit without them',async()=>{
  const calls=[];
  assert.deepEqual(plain(await setup(async url=>{calls.push(url);return hints;}).loadTasteHints(83)),shownHints);
  assert.deepEqual(calls,['/api/v1/me/taste/hints?topicId=83']);
  for(const bad of [{...hints,topicId:84},{...hints,works:[{entityId:11,name:'진격의 거인',percent:101}]},{...hints,types:[{answer:'anime',percent:3}]},{...hints,genres:[{tagId:0,name:'코미디'}]}]) {
    assert.equal(await setup(async()=>bad).loadTasteHints(83),null);
  }
  assert.equal(await setup(async()=>{throw new Error('offline');}).loadTasteHints(83),null);
});

function tsxLoader(overrides={}) {
  const root=path.join(__dirname,'..');
  const modules=new Map();
  function loadTsx(file){
    if(modules.has(file)) return modules.get(file).exports;
    const mod={exports:{}};modules.set(file,mod);
    const output=ts.transpileModule(fs.readFileSync(path.join(root,file),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText;
    vm.runInNewContext(output,{module:mod,exports:mod.exports,AbortController,URL,process:{env:{}},require(name){
      if(name in overrides) return overrides[name];
      if(name==='react'||name==='react/jsx-runtime') return require(name);
      if(name.endsWith('.module.css')) return {default:new Proxy({},{get:(_,key)=>String(key)}),__esModule:true};
      if(name.endsWith('.json')) return {default:JSON.parse(fs.readFileSync(path.join(root,name.slice(2)),'utf8')),__esModule:true};
      const relative=name.startsWith('@/')?name.slice(2):path.join(path.dirname(file),name);
      return loadTsx(relative+(fs.existsSync(path.join(root,relative+'.tsx'))?'.tsx':'.ts'));
    }});
    return mod.exports;
  }
  return loadTsx;
}

test('Hint boxes start folded under the question and hold the top works, the genre and theme rows and every seen Q3 type',()=>{
  const React=require('react');
  const {renderToStaticMarkup}=require('react-dom/server');
  const {TasteHintBox}=tsxLoader()('components/pivot/taste-hints.tsx');
  const view=props=>renderToStaticMarkup(React.createElement(TasteHintBox,props));
  // 2026-11 feedback: one folded line with 펼쳐 보기; the box is in the page but hidden until opened.
  const q1=view({hints:shownHints,step:'q1'});
  assert.match(q1,/최근 본 콘텐츠에서 선호 작품 알아보기!/);
  assert.match(q1,/aria-expanded="false"[^>]*>펼쳐 보기</);
  assert.match(q1,/<div id="[^"]+" hidden="">/);
  assert.deepEqual(q1.match(/[가-힣]+ \d+%/g),['거인 62%','나루토 20%']);
  const q2=view({hints:shownHints,step:'q2'});
  assert.match(q2,/최근 본 콘텐츠에서 발견한 취향 알아보기!/);
  assert.ok(q2.indexOf('<dt>장르</dt>')<q2.indexOf('코미디') && q2.indexOf('<dt>소재</dt>')<q2.indexOf('성장'));
  const q3=view({hints:shownHints,step:'q3'});
  assert.match(q3,/최근 내가 많이 본 콘텐츠 유형 알아보기/);
  assert.match(q3,/aria-expanded="false"[^>]*>펼쳐 보기</);
  assert.deepEqual(q3.match(/(OST|새 소식|굿즈|팝업|설정|캐릭터) \d+%/g),['OST 42%','새 소식 20%','굿즈 18%','팝업 8%','설정 7%','캐릭터 5%']);
  // A type the member did not see (0%) says nothing, so it is left out; with none seen the hint is not shown.
  const zero=view({hints:{...shownHints,types:[...shownHints.types.slice(0,5),{answer:'relationships',percent:0}]},step:'q3'});
  assert.doesNotMatch(zero,/캐릭터 0%/);
  assert.equal(view({hints:{...shownHints,types:[{answer:'ost',percent:0}]},step:'q3'}),'');
  for(const step of ['q1','q2','q3']) {
    assert.equal(view({hints:{...shownHints,enough:false},step}),'');
    assert.equal(view({hints:null,step}),'');
  }
});

test('Taste edit questions use the signup words with the folded hint under them; onboarding keeps its own spacing',()=>{
  const React=require('react');
  const {renderToStaticMarkup}=require('react-dom/server');
  const loadTsx=tsxLoader({'@/lib/api':{ApiError:class ApiError extends Error {}},'@/lib/pivot/onboarding-catalog':{serviceCatalog:{},demoCatalog:{}},
    './use-onboarding-navigation':{useOnboardingNavigation(){}}});
  const {tasteQuestionCopy}=loadTsx('components/pivot/taste-hints.tsx');
  const {QuestionScreen}=loadTsx('components/pivot/onboarding-screens.tsx');
  const copy=tasteQuestionCopy(shownHints);
  assert.deepEqual([copy.q1.heading,copy.q2.heading,copy.q3.heading],['요즘 가장 관심 있는 작품은 무엇인가요?','어떤 장르와 소재를 좋아하나요?',undefined]);
  const base={values:[],onToggle(){},onBack(){},onNext(){},busy:false};
  const edit=renderToStaticMarkup(React.createElement(QuestionScreen,{...base,copy:copy.q3}));
  assert.match(edit,/<span>Q3\.<\/span>주로 어떤 정보를 찾나요\?/);
  assert.match(edit,/가장 많이 찾는 것 순서대로 선택해 주세요<br\/>1개만 선택해도 되고 6개 다 선택해도 됩니다/);
  // The hint sits under the question (after its description), and the edit's question sits close to the progress row.
  const hint=edit.indexOf('최근 내가 많이 본 콘텐츠 유형 알아보기');
  assert.ok(hint>edit.indexOf('1개만 선택해도 되고'));
  assert.match(edit,/class="questionBody compactTop"/);
  // The taste edit mock's sizes (search box, chips, hint box, button) apply to the edit only, not to signup.
  assert.match(edit,/class="[^"]*frame tasteEdit"/);
  assert.doesNotMatch(edit,/이전 열람 성향|더 보고싶나요|하나만/);
  // Without enough hints there is no hint line.
  const bare=renderToStaticMarkup(React.createElement(QuestionScreen,{...base,copy:tasteQuestionCopy({...shownHints,enough:false}).q3}));
  assert.doesNotMatch(bare,/많이 본 콘텐츠 유형/);
  // Q2 now points at the analysis below the question.
  assert.match(renderToStaticMarkup(copy.q2.description),/아래 분석결과를 참고하여/);
  const onboardingHtml=renderToStaticMarkup(React.createElement(QuestionScreen,base));
  assert.match(onboardingHtml,/<span>Q3\.<\/span>주로 어떤 정보를 찾나요\?/);
  assert.match(onboardingHtml,/가장 많이 찾는 것 순서대로 선택해 주세요<br\/>1개만 선택해도 되고 6개 다 선택해도 됩니다/);
  assert.match(onboardingHtml,/class="questionBody"/);
  assert.doesNotMatch(onboardingHtml,/많이 본 콘텐츠 유형|compactTop|tasteEdit|하나만/);
});

test('My Page opens the taste edit from 내 피드 취향 조정하기 inside 내 정보, in the design order',()=>{
  const React=require('react');
  const {renderToStaticMarkup}=require('react-dom/server');
  const root=path.join(__dirname,'..');
  const modules=new Map();
  function loadTsx(file){
    if(modules.has(file)) return modules.get(file).exports;
    const mod={exports:{}};modules.set(file,mod);
    const output=ts.transpileModule(fs.readFileSync(path.join(root,file),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText;
    vm.runInNewContext(output,{module:mod,exports:mod.exports,AbortController,URL,process:{env:{}},require(name){
      if(name==='react'||name==='react/jsx-runtime') return require(name);
      if(name.endsWith('.module.css')) return {default:new Proxy({},{get:(_,key)=>String(key)}),__esModule:true};
      const relative=name.startsWith('@/')?name.slice(2):path.join(path.dirname(file),name);
      return loadTsx(relative+(fs.existsSync(path.join(root,relative+'.tsx'))?'.tsx':'.ts'));
    }});
    return mod.exports;
  }
  const {PivotMyPageScreen}=loadTsx('components/pivot/my-page-screen.tsx');
  const html=renderToStaticMarkup(React.createElement(PivotMyPageScreen,{state:{status:'ready',profile:{origin:'service',nickname:'검증',maskedEmail:'te***@example.com'}},demo:false,onHome(){},onRetry(){},onLogout:async()=>{}}));
  const start=html.indexOf('>내 정보<'), box=html.slice(start,html.indexOf('</section>',start));
  const at=['내 활동','내 피드 취향 조정하기','관심분야 관리'].map(label=>box.indexOf(label));
  assert.ok(at[0]>=0 && at[0]<at[1] && at[1]<at[2], 'the three 내 정보 rows, in the design order');
  assert.ok(!html.includes('취향 수정하기') && !html.includes('연결 준비 중이에요'), 'no separate box and no pending notice');
});

test('Only MY Q1-Q3 opt into the shared logo and live bell; signup and result stay unchanged', () => {
  const React = require('react'), { renderToStaticMarkup } = require('react-dom/server');
  const { OnboardingFlow } = tsxLoader()('components/pivot/onboarding-screens.tsx');
  for (const step of ['q1', 'q2', 'q3', 'result']) {
    const props = { state: { ...onboarding.initialOnboardingState(), step }, dispatch() {}, onExit() {}, mode: 'service' };
    const render = extra => renderToStaticMarkup(React.createElement(OnboardingFlow, { ...props, ...extra }));
    assert.doesNotMatch(render({}), /data-my-question-header|aria-label="알림"/);
    const my = render({ myHeader: { onNotifications() {} } });
    if (step === 'result') assert.doesNotMatch(my, /data-my-question-header|aria-label="알림"/);
    else {
      assert.match(my, /data-my-question-header/); assert.match(my, /alt="me;nu" width="103"/);
      assert.match(my, /aria-label="알림" data-notification-navigation/);
      assert.ok(my.indexOf('data-my-question-header') < my.indexOf('aria-label="온보딩 화면 탐색"'));
      assert.match(my, new RegExp(`aria-valuenow="${step[1]}"`));
    }
  }
});

test('Question screens start 12px under the top and the question sits 4px under the progress row, in signup and the taste edit alike (2026-11-25)', () => {
  // 시안의 45px 는 폰 상태 표시줄 자리라 진짜 폰에서 비어 보였다 → 막대 줄 위 12px, 질문은 막대 줄 아래 4px (온보딩도 취향 수정과 같게)
  const css = fs.readFileSync(path.join(__dirname, '..', 'components/pivot/onboarding.module.css'), 'utf8');
  assert.match(css, /\n\.navigation \{[^}]*margin: 12px 24px 0;/);
  assert.match(css, /\n\.questionBody \{ margin: 4px 24px 0; \}/);
  assert.doesNotMatch(css, /\.tasteEdit \.navigation|\.navigation \{ margin-top|\.questionBody \{ margin-top/);
});
