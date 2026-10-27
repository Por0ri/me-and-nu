const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const ts = require('../node_modules/typescript');

function factory() {
  const file = path.join(__dirname, '../lib/pivot/bookmark-service.ts');
  const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const module = { exports: {} };
  vm.runInNewContext(`(function(require,module,exports){${code}\n})`, {})(
    (name) => name === './services' ? { requirePivotApi() { throw new Error('unconfigured origin'); } } : {},
    module, module.exports,
  );
  return module.exports.createServiceBookmarkControls;
}
const target = { postId: '41', contentId: '41', topicId: 7 };

test('bookmark waits for server success, locks duplicate writes, and notifies Saved once', async () => {
  let release; let calls = 0; let changes = 0;
  const store = factory()({
    bookmarkContent(contentId, topicId) { calls++; return new Promise(resolve => { release = () => resolve({ contentId, topicId }); }); },
    unbookmarkContent() { throw new Error('unexpected DELETE'); },
  }, () => {});
  store.subscribeChanges(() => { changes++; });
  const first = store.toggle(target, false);
  assert.equal(store.isPending(target), true);
  assert.equal(store.has(target, false), false);
  assert.equal(store.toggle(target, false), first);
  await Promise.resolve();
  assert.equal(calls, 1); assert.equal(changes, 0);
  release();
  assert.equal(await first, true);
  assert.equal(store.isPending(target), false);
  assert.equal(store.has(target, false), true);
  assert.equal(changes, 1);
});

test('failed saves preserve server state and can be explicitly retried', async () => {
  let fail = true; let changes = 0;
  const store = factory()({
    async bookmarkContent(contentId, topicId) { if (fail) throw new Error('503 unavailable'); return { contentId, topicId }; },
    async unbookmarkContent() {},
  }, () => {});
  store.subscribeChanges(() => { changes++; });
  await assert.rejects(store.toggle(target, false), /503/);
  assert.equal(store.has(target, false), false);
  assert.equal(store.isPending(target), false); assert.equal(changes, 0);
  fail = false; await store.toggle(target, false);
  assert.equal(store.has(target, false), true); assert.equal(changes, 1);
});

test('server-saved initial state removes by content and topic, including 204 without a body', async () => {
  let removed;
  const store = factory()({
    async bookmarkContent() { throw new Error('unexpected PUT'); },
    async unbookmarkContent(contentId, topicId) { removed = [contentId, topicId]; },
  }, () => {});
  assert.equal(store.has(target, true), true);
  assert.equal(await store.toggle(target, true), false);
  assert.deepEqual(removed, [41, 7]);
  assert.equal(store.has(target, true), false);
});

test('bookmark identity is topic-scoped and a mismatched response is not accepted as success', async () => {
  const store = factory()({
    async bookmarkContent(contentId, topicId) { return { contentId, topicId }; },
    async unbookmarkContent() {},
  }, () => {});
  await store.toggle(target);
  assert.equal(store.has({ ...target, topicId: 8 }), false);
  const mismatch = factory()({
    async bookmarkContent() { return { contentId: 42, topicId: 7 }; },
    async unbookmarkContent() {},
  }, () => {});
  await assert.rejects(mismatch.toggle(target));
  assert.equal(mismatch.has(target), false);
});

test('missing/unsafe identities and unapproved origins never call the mutation API', () => {
  let calls = 0;
  const api = { bookmarkContent() { calls++; }, unbookmarkContent() { calls++; } };
  const store = factory()(api, () => {});
  for (const bad of [{ ...target, topicId: undefined }, { ...target, topicId: -1 },
    { ...target, contentId: '1e2' }, { ...target, contentId: '9007199254740992' }]) {
    assert.throws(() => store.toggle(bad));
  }
  assert.throws(() => factory()(api).toggle(target), /unconfigured/);
  assert.equal(calls, 0);
});
