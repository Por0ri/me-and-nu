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
const go = state => reduce(state, { type: 'advance', from: state.step });
const back = state => reduce(state, { type: 'back', from: state.step });
const selectedInterest = () => reduce(m.initialOnboardingState(), { type: 'toggle-interest' });
test('Interest must be selected, and can be deselected', () => {
  const empty = m.initialOnboardingState();
  assert.equal(go(empty).step, 'interest');
  assert.equal(m.canAdvance(selectedInterest()), true);
  assert.equal(m.canAdvance(reduce(selectedInterest(), { type: 'toggle-interest' })), false);
});
test('Both unanswered questions block progression', () => {
  let state = go(selectedInterest());
  assert.equal(go(state).step, 'q1');
  state = go(reduce(state, { type: 'choose-q1', value: 'ost' }));
  assert.equal(go(state).step, 'q2');
});
test('Single-choice updates replace the previous answer', () => {
  let state = go(selectedInterest());
  state = reduce(state, { type: 'choose-q1', value: 'world' });
  state = reduce(state, { type: 'choose-q1', value: 'ost' });
  assert.equal(state.q1, 'ost');
});
test('Invalid and out-of-stage choice IDs fail closed', () => {
  let state = go(selectedInterest());
  assert.equal(reduce(state, { type: 'choose-q1', value: 'not-a-choice' }), state);
  assert.equal(reduce(state, { type: 'choose-q2', value: 'events' }), state);
});
test('Repeated stale advances and backs cannot skip steps', () => {
  const state = selectedInterest();
  const action = { type: 'advance', from: 'interest' };
  const next = reduce(state, action);
  assert.equal(reduce(next, action), next);
  const backAction = { type: 'back', from: 'q1' };
  const previous = reduce(next, backAction);
  assert.equal(reduce(previous, backAction), previous);
});
test('Incomplete draft cannot be submitted', () => assert.throws(() => m.buildSelectionDraft(m.initialOnboardingState())));
for (const q1 of m.q1Choices) for (const q2 of m.q2Choices) {
  test('Result reflects ' + q1.id + '/' + q2.id, () => {
    let state = go(selectedInterest());
    state = go(reduce(state, { type: 'choose-q1', value: q1.id }));
    state = go(reduce(state, { type: 'choose-q2', value: q2.id }));
    assert.equal(state.step, 'result');
    assert.equal(m.selectionSummary(state).q1, q1.summary);
    assert.equal(m.selectionSummary(state).q2, q2.summary);
    const draft = m.buildSelectionDraft(state);
    assert.deepEqual(Object.keys(draft).sort(), ['interest', 'q1', 'q2']);
    assert.equal(Object.hasOwn(draft, 'topicId'), false);
    assert.equal(Object.hasOwn(draft, 'subtopicIds'), false);
  });
}
test('Back preserves answers; edited answers immediately replace result summary', () => {
  let state = go(selectedInterest());
  state = go(reduce(state, { type: 'choose-q1', value: 'ost' }));
  state = go(reduce(state, { type: 'choose-q2', value: 'events' }));
  state = back(state);
  assert.equal(state.q2, 'events');
  state = back(state);
  assert.equal(state.q1, 'ost');
  state = go(reduce(state, { type: 'choose-q1', value: 'world' }));
  state = go(reduce(state, { type: 'choose-q2', value: 'releases' }));
  assert.equal(m.selectionSummary(state).q1, '설정과 세계관');
  assert.equal(m.selectionSummary(state).q2, '신작/시즌 공개 소식');
  assert.equal(reduce(state, { type: 'reset' }).interest, null);
});
test('Unconnected service rejects and never fabricates saved success', async () => {
  const { pendingOnboardingService } = load('onboarding-service.ts');
  assert.equal(pendingOnboardingService.availability, 'unconfigured');
  await assert.rejects(pendingOnboardingService.submitSelection({ interest: 'animation', q1: 'ost', q2: 'events' }, { consents: [] }), /계약이 아직 연결/);
});


test('Reselect clears both answers and summary while preserving interest, for all result combinations', () => {
  for (const q1 of m.q1Choices) for (const q2 of m.q2Choices) {
    let state = go(selectedInterest());
    state = go(reduce(state, { type: 'choose-q1', value: q1.id }));
    state = go(reduce(state, { type: 'choose-q2', value: q2.id }));
    const action = { type: 'reselect', from: 'result' };
    state = reduce(state, action);
    assert.equal(state.step, 'q1'); assert.equal(state.interest, 'animation');
    assert.equal(state.q1, null); assert.equal(state.q2, null);
    assert.equal(m.selectionSummary(state).q1, ''); assert.equal(m.selectionSummary(state).q2, '');
    assert.equal(m.canAdvance(state), false); assert.equal(m.isSelectionComplete(state), false);
    assert.throws(() => m.buildSelectionDraft(state));
    assert.equal(reduce(state, action), state);
    state = reduce(state, { type: 'choose-q1', value: 'goods' });
    assert.equal(reduce(state, action), state, 'Stale reselection cannot clear a new Q1 answer');
    state = go(state);
    assert.equal(state.q2, null); assert.equal(m.canAdvance(state), false);
    state = go(reduce(state, { type: 'choose-q2', value: 'restock' }));
    assert.equal(state.step, 'result');
    assert.equal(m.selectionSummary(state).q1, '굿즈 구매 정보');
    assert.equal(m.selectionSummary(state).q2, '굿즈 출시 및 재입고 정보');
  }
});
test('Reselect only works at result; ordinary back preserves both answers through interest', () => {
  for (const step of ['interest', 'q1', 'q2']) {
    const state = { step, interest: 'animation', q1: 'ost', q2: 'events' };
    assert.equal(reduce(state, { type: 'reselect', from: 'result' }), state);
  }
  let state = { step: 'result', interest: 'animation', q1: 'ost', q2: 'events' };
  for (const step of ['q2', 'q1', 'interest']) {
    state = back(state); assert.equal(state.step, step);
    assert.equal(state.interest, 'animation'); assert.equal(state.q1, 'ost'); assert.equal(state.q2, 'events');
  }
});
