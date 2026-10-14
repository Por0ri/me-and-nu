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
