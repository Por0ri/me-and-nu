const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');

function load(relative, dependencies) {
  const mod = { exports: {} };
  const code = ts.transpileModule(fs.readFileSync(path.join(__dirname, '..', relative), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  vm.runInNewContext(code, { module: mod, exports: mod.exports, setTimeout, clearTimeout, performance, require(name) {
    if (!(name in dependencies)) throw Error('Unexpected dependency: ' + name);
    return dependencies[name];
  } });
  return mod.exports;
}
const transitions = load('lib/pivot/transitions.ts', {});
const { warmUpFeed, FEED_LOADING_LIMITS } = load('lib/pivot/feed-warmup.ts', {
  '@/lib/api': { getFeed() { throw new Error('the real API is not used in tests'); } },
  './transitions': transitions,
});
const page = urls => ({ selectedTopicId: 1, nextCursor: null, emptyReason: null,
  sections: [{ id: 'a', type: 'feed', title: '', subtopicId: null, contents: urls.map((imageUrl, id) => ({ id, imageUrl })) }] });
const limits = { min: 40, max: 120 };
const deps = (load, preloaded = []) => ({ load, preload: async url => { preloaded.push(url); },
  wait: ms => new Promise(resolve => setTimeout(resolve, ms)), now: () => Date.now() });

test('The loading screen shows one full turn of the artwork and holds the feed back at most 10 s', () => {
  assert.equal(FEED_LOADING_LIMITS.min, 2400);
  assert.equal(FEED_LOADING_LIMITS.max, 10000);
});

test('Warm-up asks for the first feed page, preloads its first four pictures and lasts at least the minimum', async () => {
  const preloaded = [], asked = [];
  const started = Date.now();
  await warmUpFeed(7, started, deps(async topicId => { asked.push(topicId); return page(['a', null, 'b', 'c', 'd', 'e']); }, preloaded), limits);
  assert.deepEqual(asked, [7]);
  assert.deepEqual(preloaded, ['a', 'b', 'c', 'd']);
  assert.ok(Date.now() - started >= limits.min - 5);
});

test('A hanging feed stops at the maximum; a failing or missing one still waits the minimum', async () => {
  let started = Date.now();
  await warmUpFeed(7, started, deps(() => new Promise(() => {})), limits);
  const hung = Date.now() - started;
  assert.ok(hung >= limits.max - 5 && hung < limits.max + 500, String(hung));
  for (const [topicId, load] of [[7, async () => { throw new Error('offline'); }], [null, async () => { throw new Error('not asked'); }]]) {
    started = Date.now();
    await warmUpFeed(topicId, started, deps(load), limits);
    assert.ok(Date.now() - started >= limits.min - 5);
  }
});
