const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
function load(relative, dependencies = {}) {
  const mod = { exports: {} };
  const code = ts.transpileModule(fs.readFileSync(path.join(__dirname, '../lib/pivot/', relative), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  vm.runInNewContext(code, { module: mod, exports: mod.exports, require(name) {
    if (!(name in dependencies)) throw new Error('Unexpected dependency: ' + name);
    return dependencies[name];
  } });
  return mod.exports;
}
const m = load('onboarding.ts');
const reduce = m.onboardingReducer;
const go = state => reduce(state,{type:'advance',from:state.step});
const back = state => reduce(state,{type:'back',from:state.step});
const plain = value => JSON.parse(JSON.stringify(value));
const work = (entityId=101) => ({entityId,name:`작품 ${entityId}`,aliases:[],imageUrl:null,releaseYear:2024,mediaFormat:'TV',selectable:true,unavailableReason:null});
const tag = (tagId=201,kind='genre') => ({tagId,kind,code:`tag_${tagId}`,label:`태그 ${tagId}`,sourceEntityIds:[101]});
function q1() {
  let state=reduce(m.initialOnboardingState(),{type:'toggle-interest'});
  state=reduce(state,{type:'set-topic',topicId:83});
  return go(state);
}
function q2(ids=[101],options=[tag()]) {
  let state=q1();
  for(const id of ids) state=reduce(state,{type:'toggle-work',work:work(id)});
  state=go(state);
  return reduce(state,{type:'set-options',topicId:83,entityIds:ids,options});
}
function result(consumptionType='ost') {
  let state=q2();
  state=reduce(state,{type:'toggle-tag',tagId:201});
  state=go(state);
  return go(reduce(state,{type:'choose-consumption',value:consumptionType}));
}

test('Interest selection and all three unanswered questions block progression',()=>{
  const empty=m.initialOnboardingState();
  assert.equal(go(empty).step,'interest');
  let state=q1();
  assert.equal(go(state).step,'q1');
  state=go(reduce(state,{type:'toggle-work',work:work()}));
  assert.equal(go(state).step,'q2');
  state=reduce(state,{type:'set-options',topicId:83,entityIds:[101],options:[tag()]});
  assert.equal(go(state).step,'q2');
  state=go(reduce(state,{type:'toggle-tag',tagId:201}));
  assert.equal(state.step,'q3');
  assert.equal(go(state).step,'q3');
});

test('Q1 accepts one to five works, prevents a sixth and allows removing a selected work',()=>{
  let state=q1();
  for(let id=101;id<=105;id++) state=reduce(state,{type:'toggle-work',work:work(id)});
  assert.equal(m.canAdvance(state),true);
  assert.equal(reduce(state,{type:'toggle-work',work:work(106)}),state);
  state=reduce(state,{type:'toggle-work',work:work(103)});
  assert.equal(state.favoriteWorks.length,4);
  state=reduce(state,{type:'toggle-work',work:work(106)});
  assert.equal(state.favoriteWorks.length,5);
  for(const selected of state.favoriteWorks) state=reduce(state,{type:'toggle-work',work:selected});
  assert.equal(m.canAdvance(state),false);
});

test('Invalid IDs, unavailable works and out-of-stage answers fail closed',()=>{
  const state=q1();
  for(const invalid of [work(0),work('101'),work(1.5),{...work(),selectable:false}]) assert.equal(reduce(state,{type:'toggle-work',work:invalid}),state);
  assert.equal(reduce(state,{type:'choose-consumption',value:'ost'}),state);
  assert.equal(reduce(state,{type:'toggle-tag',tagId:201}),state);
  const tags=q2();
  assert.equal(reduce(tags,{type:'toggle-work',work:work(102)}),tags);
  assert.equal(reduce(tags,{type:'toggle-tag',tagId:999}),tags);
});

test('Q2 needs one tag total, accepts genre-only or theme-only and has no fixed cap',()=>{
  for(const kind of ['genre','theme']) {
    let state=q2([101],[tag(201,kind)]);
    state=reduce(state,{type:'toggle-tag',tagId:201});
    assert.equal(m.canAdvance(state),true,kind);
  }
  const options=Array.from({length:40},(_,i)=>tag(201+i,'theme'));
  let state=q2([101],options);
  for(const option of options) state=reduce(state,{type:'toggle-tag',tagId:option.tagId});
  assert.equal(state.preferenceTagIds.length,40);
  assert.equal(m.canAdvance(state),true);
  state=reduce(state,{type:'toggle-tag',tagId:201});
  assert.equal(state.preferenceTagIds.length,39);
});

test('Stale candidate responses cannot attach another work set or Topic to Q2',()=>{
  const state=q2();
  for(const payload of [{topicId:83,entityIds:[102]},{topicId:99,entityIds:[101]}]) assert.equal(reduce(state,{type:'set-options',...payload,options:[tag(999)]}),state);
  const previous=back(state);
  assert.equal(reduce(previous,{type:'set-options',topicId:83,entityIds:[101],options:[tag(999)]}),previous);
});

test('Changing Q1 clears Q2 candidates and selected tags before recalculating',()=>{
  let state=back(back(back(result())));
  assert.equal(state.step,'q1');
  assert.equal(state.preferenceTagIds.length,1);
  state=reduce(state,{type:'toggle-work',work:work(102)});
  assert.equal(state.optionsKey,null);
  assert.equal(state.preferenceTagIds.length,0);
  assert.equal(state.preferenceOptions.length,0);
  state=go(state);
  assert.equal(m.canAdvance(state),false);
});

test('Refreshing Q2 blocks advancement and prunes tags no longer in the current candidates',()=>{
  let state=q2([101],[tag(201),tag(202)]);
  state=reduce(state,{type:'toggle-tag',tagId:201});
  state=reduce(state,{type:'toggle-tag',tagId:202});
  state=reduce(state,{type:'invalidate-options'});
  assert.equal(m.canAdvance(state),false);
  state=reduce(state,{type:'set-options',topicId:83,entityIds:[101],options:[tag(202)]});
  assert.deepEqual(plain(state.preferenceTagIds),[202]);
  assert.equal(m.canAdvance(state),true);
});

for(const choice of m.q1Choices) test(`Q3 keeps one ${choice.id} choice and produces a V2 draft`,()=>{
  let state=back(result('world'));
  state=reduce(state,{type:'choose-consumption',value:choice.id});
  state=go(state);
  assert.equal(state.step,'result');
  assert.equal(m.selectionSummary(state).consumption,choice.summary);
  assert.deepEqual(plain(m.buildSelectionDraft(state)),{interest:'animation',topicId:83,favoriteEntityIds:[101],preferenceTagIds:[201],consumptionType:choice.id});
});

test('Repeated stale navigation cannot skip a question',()=>{
  const state=q1();
  const selected=reduce(state,{type:'toggle-work',work:work()});
  const action={type:'advance',from:'q1'};
  const next=reduce(selected,action);
  assert.equal(reduce(next,action),next);
  const previous=back(next);
  assert.equal(reduce(previous,{type:'back',from:'q2'}),previous);
});

test('Back retains selections, explicit result reselection clears them, stale reselection cannot clear a new answer',()=>{
  let state=result();
  for(const step of ['q3','q2','q1','interest']) {
    state=back(state); assert.equal(state.step,step);
    assert.equal(state.favoriteWorks.length,1); assert.equal(state.preferenceTagIds.length,1);
    assert.equal(state.consumptionType,'ost');
  }
  state=reduce(result(),{type:'reselect',from:'result'});
  assert.equal(state.step,'q1'); assert.equal(state.interest,'animation'); assert.equal(state.topicId,83);
  assert.equal(state.favoriteWorks.length,0); assert.equal(state.preferenceTagIds.length,0); assert.equal(state.consumptionType,null);
  assert.throws(()=>m.buildSelectionDraft(state));
  state=reduce(state,{type:'toggle-work',work:work()});
  assert.equal(reduce(state,{type:'reselect',from:'result'}),state);
});

test('A changed Topic resets old catalog selections and session reset clears personal choices',()=>{
  const state=reduce(result(),{type:'set-topic',topicId:99});
  assert.equal(state.step,'q1'); assert.equal(state.topicId,99); assert.equal(state.favoriteWorks.length,0);
  assert.deepEqual(plain(reduce(state,{type:'reset'})),plain(m.initialOnboardingState()));
});

test('Incomplete or malformed drafts cannot be submitted',()=>{
  assert.throws(()=>m.buildSelectionDraft(m.initialOnboardingState()));
  const draft=m.buildSelectionDraft(result());
  for(const invalid of [{...draft,favoriteEntityIds:[101,101]},{...draft,preferenceTagIds:[]},{...draft,preferenceTagIds:[201,'202']},{...draft,consumptionType:'releases'},{...draft,topicId:0}]) assert.equal(m.isSelectionComplete(invalid),false);
});
