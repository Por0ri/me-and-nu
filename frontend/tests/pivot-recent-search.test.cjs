const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');

const root = path.join(__dirname, '..');
const plain = value => JSON.parse(JSON.stringify(value));
function loadRecentSearch() {
  const mod = { exports: {} };
  const code = ts.transpileModule(fs.readFileSync(path.join(root, 'lib/pivot/recent-search.ts'), 'utf8'), { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
  } }).outputText;
  vm.runInNewContext(code, { module: mod, exports: mod.exports });
  return mod.exports;
}
const { createRecentSearchStore, MAX_RECENT_SEARCHES } = loadRecentSearch();
function memoryStorage(initial = {}) {
  const data = new Map(Object.entries(initial));
  return { data, getItem: key => data.has(key) ? data.get(key) : null, setItem: (key, value) => { data.set(key, String(value)); } };
}

test('Recent searches keep the newest three per member, move repeats to the top and keep a picked work', () => {
  const storage = memoryStorage();
  let owner = 7;
  const store = createRecentSearchStore(() => owner, () => storage);
  for (const label of ['하이큐', '원피스', '나루토', '블리치', '주술회전']) store.add({ label });
  assert.equal(MAX_RECENT_SEARCHES, 3);
  assert.deepEqual(plain(store.list().map(entry => entry.label)), ['주술회전', '블리치', '나루토']);
  assert.deepEqual(plain(store.add({ label: ' 블리치 ' }).map(entry => entry.label)), ['블리치', '주술회전', '나루토']);
  assert.deepEqual(plain(store.add({ label: '진격의 거인', workId: 12 })), [{ label: '진격의 거인', workId: 12 }, { label: '블리치' }, { label: '주술회전' }]);
  owner = 8;   // another member on the same browser starts empty
  assert.deepEqual(plain(store.list()), []);
  store.add({ label: '프리렌' });
  owner = 7;
  assert.equal(store.list()[0].label, '진격의 거인');
  assert.deepEqual(plain([...storage.data.keys()].sort()), ['pivot.recent-searches.7', 'pivot.recent-searches.8']);
});

test('Recent searches remove one or all entries and drop empty or invalid saved entries', () => {
  const saved = [{ label: '하이큐' }, { label: '' }, { label: 3 }, { label: '나루토', workId: -1 }, { label: '원피스', workId: 5 }, { label: 'x'.repeat(51) }];
  const storage = memoryStorage({ 'pivot.recent-searches.7': JSON.stringify(saved) });
  const store = createRecentSearchStore(() => 7, () => storage);
  assert.deepEqual(plain(store.list()), [{ label: '하이큐' }, { label: '원피스', workId: 5 }]);
  assert.deepEqual(plain(store.add({ label: '   ' })), [{ label: '하이큐' }, { label: '원피스', workId: 5 }]);
  assert.deepEqual(plain(store.remove({ label: '원피스', workId: 5 })), [{ label: '하이큐' }]);
  assert.deepEqual(plain(store.clear()), []);
  assert.equal(storage.data.get('pivot.recent-searches.7'), '[]');
});

test('Without a member or with broken storage, searching still works', () => {
  const storage = memoryStorage();
  const nobody = createRecentSearchStore(() => null, () => storage);
  assert.deepEqual(plain(nobody.add({ label: '하이큐' })), []);
  assert.equal(storage.data.size, 0);
  const broken = createRecentSearchStore(() => 7, () => ({ getItem() { return '{not json'; }, setItem() { throw new Error('QuotaExceededError'); } }));
  assert.deepEqual(plain(broken.list()), []);
  assert.deepEqual(plain(broken.add({ label: '하이큐' })), [{ label: '하이큐' }]);
  const blocked = createRecentSearchStore(() => 7, () => null);
  assert.deepEqual(plain(blocked.add({ label: '하이큐' })), [{ label: '하이큐' }]);
});
