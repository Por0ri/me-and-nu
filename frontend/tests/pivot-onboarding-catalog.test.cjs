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
const work={entityId:101,name:'테스트 작품',aliases:['Fixture Anime'],imageUrl:null,releaseYear:2024,mediaFormat:'TV',selectable:true,unavailableReason:null};
const tag={tagId:201,kind:'genre',code:'fantasy',label:'판타지',sourceEntityIds:[101]};
const page={items:[work],nextCursor:null};
const options={entityIds:[101],genres:[tag],themes:[],minSelections:2,minGenreSelections:1,minThemeSelections:1,maxSelections:null,maxSelectionsPerKind:7};
function setup({request=async()=>page,topics=async()=>({topics:[{id:83,code:'anime',name:'애니메이션'}]}),session=async()=>({}),guard=()=>{}}={}) {
  return load('lib/pivot/onboarding-catalog.ts',{
    '@/lib/api':{apiRequest:request,getTopics:topics,getSession:session},
    './onboarding-service':{animeTopicId(response){assert.equal(response.topics[0].code,'anime');return response.topics[0].id;}},
    './onboarding':onboarding,'./services':{requirePivotApi:guard},
  });
}

test('Catalog resolves the actual anime Topic and searches the exact endpoint with an AbortSignal',async()=>{
  const calls=[];
  const signal=new AbortController().signal;
  const m=setup({request:async(...args)=>{calls.push(args);return page;}});
  assert.equal(await m.serviceCatalog.resolveTopic(),83);
  assert.deepEqual(plain(await m.serviceCatalog.search(83,'  테스트 %_  ','cursor-token',signal)),page);
  const [path,init]=calls[0];
  const url=new URL(path,'http://localhost');
  assert.equal(url.pathname,'/api/v1/onboarding/works');
  assert.deepEqual(Object.fromEntries(url.searchParams),{topicId:'83',q:'테스트 %_',limit:'20',cursor:'cursor-token'});
  assert.equal(init.signal,signal);
  assert.equal(calls.length,1);
});

test('Candidate request submits Topic and selected work IDs without persisting selections',async()=>{
  const calls=[];
  const order=[];
  const signal=new AbortController().signal;
  const m=setup({session:async()=>{order.push('session');},request:async(...args)=>{order.push('options');calls.push(args);return options;}});
  assert.deepEqual(plain(await m.serviceCatalog.options(83,[101],signal)),options);
  assert.equal(calls[0][0],'/api/v1/onboarding/preference-options');
  assert.equal(calls[0][1].method,'POST');
  assert.deepEqual(JSON.parse(calls[0][1].body),{topicId:83,entityIds:[101]});
  assert.equal(calls[0][1].signal,signal);
  assert.equal(calls.length,1);
  assert.deepEqual(order,['session','options'],'candidate retry refreshes CSRF before POST');
});

test('A rejected guard cannot call network and API failure never substitutes demo works',async()=>{
  let calls=0;
  const denied=setup({guard(){throw Error('not configured');},request:async()=>{calls++;return page;},topics:async()=>{calls++;}});
  for(const action of [()=>denied.serviceCatalog.resolveTopic(),()=>denied.serviceCatalog.search(83,'테스트'),()=>denied.serviceCatalog.options(83,[101])]) await assert.rejects(action,/not configured/);
  assert.equal(calls,0);
  const failure=new Error('API unavailable');
  const m=setup({request:async()=>{throw failure;}});
  await assert.rejects(m.serviceCatalog.search(83,'테스트'),error=>error===failure);
  await assert.rejects(m.serviceCatalog.options(83,[101]),error=>error===failure);
});

test('An aborted transport remains cancelled without another request or a demo fallback',async()=>{
  const abort=new AbortController();abort.abort();let calls=0;
  const m=setup({request:async(_path,init)=>{calls++;init.signal.throwIfAborted();return page;}});
  await assert.rejects(m.serviceCatalog.search(83,'테스트',undefined,abort.signal),error=>error.name==='AbortError');
  assert.equal(calls,1);
});

test('Work pages allow an empty result or explicit unselectable work but reject malformed and duplicate identities',()=>{
  const m=setup();
  assert.deepEqual(plain(m.validateWorkPage({items:[],nextCursor:null})),{items:[],nextCursor:null});
  assert.equal(m.validateWorkPage({items:[{...work,selectable:false,unavailableReason:'PREFERENCE_TAGS_NOT_READY'}],nextCursor:null}).items[0].selectable,false);
  for(const invalid of [null,{items:[work]}, {...page,nextCursor:17},{...page,items:[work,work]},
    {...page,items:[{...work,entityId:'101'}]},{...page,items:[{...work,name:''}]},
    {...page,items:[{...work,selectable:'true'}]},{...page,items:[{...work,aliases:[42]}]}]) assert.throws(()=>m.validateWorkPage(invalid));
});

test('Candidates are tied to the requested works, strict kinds and source IDs',()=>{
  const m=setup();
  assert.deepEqual(plain(m.validatePreferenceOptions(options,[101])),options);
  for(const invalid of [null,{...options,entityIds:null},{...options,entityIds:['101']},
    {...options,entityIds:[102]},{...options,entityIds:[]},{...options,entityIds:[101,101]},
    {...options,genres:null},{...options,themes:{}},{...options,genres:[tag,tag]},
    {...options,themes:[{...tag,kind:'theme'}]},
    {...options,genres:[{...tag,tagId:0}]},{...options,genres:[{...tag,tagId:'201'}]},
    {...options,genres:[{...tag,tagId:201.5}]},{...options,genres:[{...tag,tagId:Number.MAX_SAFE_INTEGER+1}]},
    {...options,genres:[{...tag,kind:'theme'}]},{...options,themes:[{...tag,tagId:202,kind:'genre'}]},
    {...options,genres:[{...tag,code:null}]},{...options,genres:[{...tag,label:' '}]},
    {...options,genres:[{...tag,sourceEntityIds:undefined}]},{...options,genres:[{...tag,sourceEntityIds:null}]},
    {...options,genres:[{...tag,sourceEntityIds:'101'}]},{...options,genres:[{...tag,sourceEntityIds:101}]},
    {...options,genres:[{...tag,sourceEntityIds:{}}]},{...options,genres:[{...tag,sourceEntityIds:['101']}]},
    {...options,genres:[{...tag,sourceEntityIds:[102]}]},{...options,genres:[{...tag,sourceEntityIds:[101,101]}]},
    {...options,minSelections:1},{...options,minGenreSelections:undefined},{...options,minThemeSelections:undefined},
    {...options,minGenreSelections:0},{...options,minThemeSelections:0},{...options,maxSelections:5},
    {...options,maxSelectionsPerKind:undefined},{...options,maxSelectionsPerKind:0},{...options,maxSelectionsPerKind:8}]) assert.throws(()=>m.validatePreferenceOptions(invalid,[101]));
  assert.throws(()=>m.validatePreferenceOptions({...options,entityIds:[101,101]},[101,102]));
});

test('Candidates may lack one kind for the UI to explain; candidate lists remain uncapped while selections are capped per kind',()=>{
  const m=setup();
  const many=Array.from({length:40},(_,index)=>({...tag,tagId:201+index,kind:'theme',sourceEntityIds:[101,102]}));
  assert.equal(m.validatePreferenceOptions({...options,entityIds:[102,101],genres:[],themes:many},[101,102]).themes.length,40);
  const theme={...options,genres:[],themes:[{...tag,kind:'theme'}]};
  assert.equal(m.validatePreferenceOptions(theme,[101]).genres.length,0);
  assert.equal(m.validatePreferenceOptions(theme,[101]).minGenreSelections,1);
  assert.equal(m.validatePreferenceOptions(theme,[101]).minThemeSelections,1);
  assert.equal(m.validatePreferenceOptions(theme,[101]).minSelections,2);
});

test('Legacy candidate metadata normalizes safely while Q2 still requires both kinds',async()=>{
  const legacy={...options,minSelections:1,minGenreSelections:undefined,minThemeSelections:undefined,
    themes:[{...tag,tagId:301,kind:'theme',sourceEntityIds:[]}]};
  for(const raw of [legacy,{...legacy,maxSelectionsPerKind:undefined}]) {
    const wire=JSON.parse(JSON.stringify(raw));
    const m=setup({request:async()=>wire});
    const result=await m.serviceCatalog.options(83,[101]);
    assert.equal(result.minSelections,2);
    assert.equal(result.minGenreSelections,1);
    assert.equal(result.minThemeSelections,1);
    assert.equal(result.maxSelectionsPerKind,7);
    assert.deepEqual(plain(result.themes[0].sourceEntityIds),[]);
    const state={...onboarding.initialOnboardingState(),step:'q2',interest:'animation',topicId:83,
      favoriteWorks:[work],preferenceOptions:[...result.genres,...result.themes],optionsKey:'101',preferenceTagIds:[201]};
    assert.equal(onboarding.canAdvance(state),false,'legacy min=1 cannot weaken the UI');
    assert.equal(onboarding.canAdvance({...state,preferenceTagIds:[201,301]}),true);
  }
});

test('Partial or contradictory rollout metadata is rejected',()=>{
  const m=setup();
  const legacy={...options,minSelections:1,minGenreSelections:undefined,minThemeSelections:undefined};
  for(const invalid of [{...legacy,minGenreSelections:1},{...legacy,minThemeSelections:1},
    {...legacy,minGenreSelections:1,minThemeSelections:1},{...legacy,maxSelectionsPerKind:0},
    {...legacy,maxSelectionsPerKind:8},{...options,minGenreSelections:undefined,minThemeSelections:undefined},
    {...options,minSelections:3},{...legacy,minGenreSelections:null}]) {
    assert.throws(()=>m.validatePreferenceOptions(invalid,[101]));
  }
});

test('Candidates accept empty sourceEntityIds alongside linked genre and theme hints',()=>{
  const m=setup();
  const response={...options,genres:[tag,{...tag,tagId:202,sourceEntityIds:[]}],
    themes:[{...tag,tagId:203,kind:'theme',sourceEntityIds:[]}]};
  assert.deepEqual(plain(m.validatePreferenceOptions(response,[101])),response);
});

test('Candidates do not require every selected work to have a linked chip',()=>{
  const m=setup();
  const response={...options,entityIds:[101,102]};
  assert.deepEqual(plain(m.validatePreferenceOptions(response,[102,101])),response);
});

test('Candidates accept a selected work with no linked chips at all',()=>{
  const m=setup();
  const response={...options,genres:[{...tag,sourceEntityIds:[]}],
    themes:[{...tag,tagId:202,kind:'theme',sourceEntityIds:[]}]};
  assert.deepEqual(plain(m.validatePreferenceOptions(response,[101])),response);
});

test('Empty candidate groups do not impose a selected-work coverage requirement',()=>{
  const m=setup();
  const response={...options,genres:[]};
  assert.deepEqual(plain(m.validatePreferenceOptions(response,[101])),response);
});

test('Candidates require the Checking maxSelectionsPerKind metadata',()=>{
  const m=setup();
  const response={...options,maxSelectionsPerKind:7};
  assert.deepEqual(plain(m.validatePreferenceOptions(response,[101])),response);
});

test('Catalog accepts the PR 14 response with all 16 genres and 19 themes plus a per-kind cap',async()=>{
  // Contract fixture: source IDs are optional hints; work 102 has no linked chips.
  const response={...options,entityIds:[101,102],maxSelectionsPerKind:7,
    genres:Array.from({length:16},(_,index)=>({...tag,tagId:201+index,code:'genre_'+index,
      sourceEntityIds:index===0?[101]:[]})),
    themes:Array.from({length:19},(_,index)=>({...tag,tagId:301+index,kind:'theme',code:'theme_'+index,
      sourceEntityIds:index===0?[101]:[]}))};
  const m=setup({request:async()=>response});
  const result=await m.serviceCatalog.options(83,[102,101]);
  assert.deepEqual(plain(result),response);
  assert.equal(result.genres.length,16);
  assert.equal(result.themes.length,19);
  assert.equal(result.maxSelectionsPerKind,7);
});
