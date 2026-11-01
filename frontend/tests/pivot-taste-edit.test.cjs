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

test('My Page shows 취향 수정하기 as its own box below 로그아웃, also while the service profile is not connected yet',()=>{
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
  const html=renderToStaticMarkup(React.createElement(PivotMyPageScreen,{state:{status:'unconfigured',message:'연결 준비 중'},demo:false,onHome(){},onRetry(){},onLogout:async()=>{}}));
  assert.ok(html.indexOf('로그아웃')>=0 && html.indexOf('취향 수정하기')>html.indexOf('로그아웃'));
  assert.ok(html.slice(html.indexOf('로그아웃'),html.indexOf('취향 수정하기')).includes('</section>'));   // its own box, not in the logout box
});
