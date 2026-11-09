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
const work=(entityId,selectable=true)=>({entityId,name:`작품 ${entityId}`,aliases:[],imageUrl:null,releaseYear:2024,mediaFormat:'TV',selectable,unavailableReason:null});
const tag=(tagId,kind)=>({tagId,kind,code:`c${tagId}`,label:`칩 ${tagId}`,sourceEntityIds:[]});
const taste={topicId:83,works:[work(11),work(12)],preferenceTagIds:[101,103],consumptionType:'world'};
function setup(request) {
  const topics=async()=>({topics:[{id:83,code:'anime',name:'애니메이션'}]});
  const catalog=load('lib/pivot/onboarding-catalog.ts',{
    '@/lib/api':{apiRequest:request,getTopics:topics,getSession:async()=>({})},
    './onboarding-service':{animeTopicId:response=>response.topics[0].id},'./onboarding':onboarding,'./services':{requirePivotApi(){}},
  });
  return load('lib/pivot/taste-edit.ts',{
    '@/lib/api':{apiRequest:request,getTopics:topics,getSession:async()=>({})},
    './onboarding':onboarding,'./onboarding-catalog':catalog,
    './onboarding-service':{animeTopicId:response=>response.topics[0].id},'./services':{requirePivotApi(){}},
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
  assert.equal(state.consumptionType,'world');
  const next=m.tasteEditReducer(state,{type:'toggle-work',work:work(13)});
  assert.deepEqual(plain(next.favoriteWorks.map(item=>item.entityId)),[11,13]);
  assert.deepEqual(plain(next.preferenceTagIds),[101,103]);
  assert.equal(next.optionsKey,null);                                           // Q2 reloads the chip list
  assert.equal(onboarding.onboardingReducer(state,{type:'toggle-work',work:work(13)}).preferenceTagIds.length,0);
});

test('Saving sends the version 2 answers with PUT and rejects a response for another Topic',async()=>{
  const sent=[];
  const m=setup(async(url,init)=>{sent.push([url,init.method,JSON.parse(init.body)]);return {...taste,consumptionType:'news'};});
  const selection={interest:'animation',topicId:83,favoriteEntityIds:[11,12],preferenceTagIds:[101,103],consumptionType:'news'};
  assert.equal((await m.saveMyTaste(selection)).consumptionType,'news');
  assert.deepEqual(sent,[['/api/v1/me/taste','PUT',{topicId:83,answers:{version:2,favoriteEntityIds:[11,12],preferenceTagIds:[101,103],consumptionType:'news'}}]]);
  const other=setup(async()=>({...taste,topicId:84}));
  await assert.rejects(other.saveMyTaste(selection),/내 취향을 확인하지 못했어요/);
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

test('Hint boxes show the top works, the genre and theme rows, and three of the six Q3 types until 더보기',()=>{
  const React=require('react');
  const {renderToStaticMarkup}=require('react-dom/server');
  const {TasteHintBox}=tsxLoader()('components/pivot/taste-hints.tsx');
  const view=props=>renderToStaticMarkup(React.createElement(TasteHintBox,props));
  const q1=view({hints:shownHints,step:'q1'});
  assert.match(q1,/최근 본 콘텐츠에서 이런 취향을 발견했어요!/);
  assert.deepEqual(q1.match(/[가-힣]+ \d+%/g),['거인 62%','나루토 20%']);
  const q2=view({hints:shownHints,step:'q2'});
  assert.ok(q2.indexOf('<dt>장르</dt>')<q2.indexOf('코미디') && q2.indexOf('<dt>소재</dt>')<q2.indexOf('성장'));
  const q3=view({hints:shownHints,step:'q3'});
  assert.match(q3,/최근 내가 많이 본 콘텐츠 유형이에요!/);
  assert.match(q3,/aria-expanded="false"[^>]*>더보기</);
  assert.deepEqual(q3.match(/(OST|새 소식|굿즈|팝업|설정|캐릭터) \d+%/g),['OST 42%','새 소식 20%','굿즈 18%']);
  for(const step of ['q1','q2','q3']) {
    assert.equal(view({hints:{...shownHints,enough:false},step}),'');
    assert.equal(view({hints:null,step}),'');
  }
});

test('Taste edit questions use their own words under the hints; onboarding keeps its own',()=>{
  const React=require('react');
  const {renderToStaticMarkup}=require('react-dom/server');
  const loadTsx=tsxLoader({'@/lib/api':{ApiError:class ApiError extends Error {}},'@/lib/pivot/onboarding-catalog':{serviceCatalog:{},demoCatalog:{}},
    './use-onboarding-navigation':{useOnboardingNavigation(){}}});
  const {tasteQuestionCopy}=loadTsx('components/pivot/taste-hints.tsx');
  const {QuestionScreen}=loadTsx('components/pivot/onboarding-screens.tsx');
  const copy=tasteQuestionCopy(shownHints);
  assert.deepEqual([copy.q1.heading,copy.q2.heading,copy.q3.heading],['요즘 가장 관심 있는 작품은 무엇인가요?','어떤 장르와 소재를 좋아하나요?','어떤 콘텐츠를 더 보고싶나요?']);
  const base={value:null,onChoose(){},onBack(){},onNext(){},busy:false};
  const edit=renderToStaticMarkup(React.createElement(QuestionScreen,{...base,copy:copy.q3}));
  const title=edit.indexOf('최근 내가 많이 본 콘텐츠 유형이에요!');
  assert.ok(title>=0&&title<edit.indexOf('어떤 콘텐츠를 더 보고싶나요?'));
  assert.match(edit,/이전 열람 성향과 다른 답변을 선택해도 괜찮아요/);
  // Without enough hints the edit does not point at them.
  const bare=renderToStaticMarkup(React.createElement(QuestionScreen,{...base,copy:tasteQuestionCopy({...shownHints,enough:false}).q3}));
  assert.doesNotMatch(bare,/많이 본 콘텐츠 유형|이전 열람 성향/);
  const onboardingHtml=renderToStaticMarkup(React.createElement(QuestionScreen,base));
  assert.match(onboardingHtml,/덕질할 때 주로 무엇을 하나요\?/);
  assert.doesNotMatch(onboardingHtml,/많이 본 콘텐츠 유형|더 보고싶나요/);
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
