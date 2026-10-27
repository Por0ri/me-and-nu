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
const options={entityIds:[101],genres:[tag],themes:[],minSelections:1,maxSelections:null};
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
  for(const invalid of [{...options,entityIds:[102]},{...options,genres:[tag,tag]},
    {...options,genres:[{...tag,kind:'theme'}]},{...options,genres:[{...tag,sourceEntityIds:[102]}]},
    {...options,genres:[{...tag,sourceEntityIds:[101,101]}]},{...options,genres:[]},
    {...options,minSelections:0},{...options,maxSelections:5}]) assert.throws(()=>m.validatePreferenceOptions(invalid,[101]));
});

test('Candidates accept one genre or one theme and an uncapped set with both selected works represented',()=>{
  const m=setup();
  const many=Array.from({length:40},(_,index)=>({...tag,tagId:201+index,kind:'theme',sourceEntityIds:[101,102]}));
  assert.equal(m.validatePreferenceOptions({...options,entityIds:[102,101],genres:[],themes:many},[101,102]).themes.length,40);
  const theme={...options,genres:[],themes:[{...tag,kind:'theme'}]};
  assert.equal(m.validatePreferenceOptions(theme,[101]).genres.length,0);
});
