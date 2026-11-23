const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const root = path.join(__dirname, '..');
class ApiError extends Error {
  constructor(message, status, code) { super(message); this.status = status; this.code = code; }
}
function loader() {
  const modules = new Map();
  function load(file) {
    file = file.replaceAll('\\', '/');
    if (modules.has(file)) return modules.get(file).exports;
    const mod = { exports: {} }; modules.set(file, mod);
    const output = ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    }).outputText;
    vm.runInNewContext(output, { module: mod, exports: mod.exports, URL, URLSearchParams, AbortController, setTimeout, clearTimeout,
      require(name) {
        if (name === '@/lib/api') return { ApiError };
        if (name === './services') return { requirePivotApi() { throw new Error('Live API forbidden'); } };
        return load(path.join(path.dirname(file), name) + '.ts');
      },
    });
    return mod.exports;
  }
  return load;
}
const load = loader();
const { createServiceHomeSource } = load('lib/pivot/home-service.ts');
const { createHomeRequestController } = load('lib/pivot/home.ts');
const card = id => ({ kind: 'content', id, topicId: 42, title: `Synthetic ${id}`, saved: false });
const topics = id => ({ topics: [{ id, code: 'anime', status: 'active', deletedAt: null }] });
const page = (ids, nextCursor = null) => ({ selectedTopicId: 42, sections: [{ contents: ids.map(card) }], nextCursor });
const session = (id = 1, csrfToken = 'owner-1') => ({ authenticated: true, sessionState: 'active', onboardingCompleted: true, user: { id, accountType: 'user' }, csrfToken });
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
function setup(getFeed, { readSession, getMyTopics = async () => topics(42), timeout = 15000 } = {}) {
  const states = [];
  const source = createServiceHomeSource({ getMyTopics, getFeed }, () => {}, readSession);
  const controller = createHomeRequestController(source, state => states.push(state), timeout);
  return { source, controller, states, latest: () => states.at(-1) };
}

test('100 x 11 fallback pages display the first page immediately and all 1100 cards on demand', async () => {
  const cursors = [];
  const view = setup(async (_, query) => {
    cursors.push(query.cursor);
    const index = query.cursor === undefined ? 0 : Number(query.cursor);
    return page(Array.from({ length: 100 }, (_, i) => index * 100 + i + 1), index < 10 ? String(index + 1) : null);
  });
  await view.controller.load();
  assert.equal(view.latest().status, 'ready');
  assert.equal(view.latest().home.cards.length, 100);
  assert.equal(cursors.length, 1, 'initial paint must not wait for later pages');
  for (let i = 1; i <= 10; i++) {
    await view.controller.loadMore();
    assert.equal(view.latest().home.cards.length, (i + 1) * 100);
  }
  assert.equal(new Set(view.latest().home.cards.map(c => c.id)).size, 1100);
  assert.equal(view.latest().pagination, undefined);
  await view.controller.loadMore();
  assert.deepEqual(cursors, [undefined, ...Array.from({ length: 10 }, (_, i) => String(i + 1))]);
});

test('later failures retain the visible cards; retry uses the same cursor and deduplicates cards', async () => {
  const cursors = []; let fail = true;
  const view = setup(async (_, { cursor }) => {
    cursors.push(cursor);
    if (!cursor) return page([1, 1], 'second');
    if (fail) { fail = false; throw new ApiError('fixture failure', 503, 'UNAVAILABLE'); }
    return page([1, 2, 2]);
  });
  await view.controller.load(); const original = view.latest().home.cards[0];
  await view.controller.loadMore();
  assert.equal(view.latest().status, 'ready');
  assert.equal(view.latest().pagination.status, 'error');
  assert.equal(view.latest().home.cards[0], original);
  await view.controller.loadMore();
  assert.equal(view.latest().pagination, undefined);
  assert.deepEqual(Array.from(view.latest().home.cards, c => c.id), ['1', '2']);
  assert.equal(view.latest().home.cards[0], original, 'existing card identity preserves image readiness and carousel state');
  assert.deepEqual(cursors, [undefined, 'second', 'second']);
});

for (const cursors of [['a', 'a'], ['a', 'b', 'a'], ['a', ''], ['a', undefined]]) {
  test(`invalid/cyclic cursor ${JSON.stringify(cursors)} stops without dropping prior pages or auto retrying`, async () => {
    let calls = 0;
    const view = setup(async () => ({ ...page([calls + 1]), nextCursor: cursors[calls++] }));
    await view.controller.load();
    for (let i = 1; i < cursors.length; i++) await view.controller.loadMore();
    assert.equal(view.latest().status, 'ready');
    assert.equal(view.latest().pagination.status, 'error');
    assert.equal(view.latest().home.cards.length, cursors.length - 1);
    await Promise.resolve();
    assert.equal(calls, cursors.length);
  });
}

test('empty intermediate pages keep their continuation instead of truncating the feed', async () => {
  const view = setup(async (_, { cursor }) => cursor ? page([1]) : page([], 'second'));
  await view.controller.load();
  assert.equal(view.latest().status, 'ready');
  assert.equal(view.latest().pagination.status, 'idle');
  await view.controller.loadMore();
  assert.equal(view.latest().home.cards.length, 1);
});

test('repeated load-more gestures issue only one in-flight request', async () => {
  const pending = deferred(); let calls = 0;
  const view = setup(async (_, { cursor }) => { calls++; return cursor ? pending.promise : page([1], 'second'); });
  await view.controller.load();
  const first = view.controller.loadMore();
  await Promise.all([view.controller.loadMore(), view.controller.loadMore()]);
  assert.equal(calls, 2);
  assert.equal(view.latest().pagination.status, 'loading');
  pending.resolve(page([2])); await first;
  assert.equal(view.latest().home.cards.length, 2);
});

for (const lateFailure of [false, true]) {
  test(`filter cancellation retains the cursor and ignores late ${lateFailure ? 'failure' : 'success'}`, async () => {
    const pending = deferred(), started = deferred(); let signal, calls = 0;
    const view = setup(async (_, { cursor }, init) => {
      if (!cursor) return page([1], 'second');
      signal = init.signal; started.resolve();
      return ++calls === 1 ? pending.promise : page([2]);
    });
    await view.controller.load();
    const request = view.controller.loadMore();
    await started.promise;
    view.controller.cancelMore();
    assert.equal(signal.aborted, true);
    assert.equal(view.latest().pagination.status, 'idle');
    await view.controller.loadMore();
    if (lateFailure) pending.reject(new Error('late')); else pending.resolve(page([999]));
    await request;
    assert.deepEqual(Array.from(view.latest().home.cards, c => c.id), ['1', '2']);
  });
}

test('a refreshed feed cannot be overwritten by an old continuation', async () => {
  const pending = deferred(), started = deferred(); let firstPage = 0;
  const view = setup(async (_, { cursor }) => {
    if (cursor) { started.resolve(); return pending.promise; }
    return page([++firstPage], firstPage === 1 ? 'second' : null);
  });
  await view.controller.load();
  const old = view.controller.loadMore();
  await started.promise;
  await view.controller.load();
  pending.resolve(page([999])); await old;
  assert.deepEqual(Array.from(view.latest().home.cards, c => c.id), ['2']);
});

test('unmount aborts the real continuation signal and suppresses late publication', async () => {
  const pending = deferred(), started = deferred(); let signal;
  const view = setup(async (_, { cursor }, init) => {
    signal = init.signal;
    if (cursor) { started.resolve(); return pending.promise; }
    return page([1], 'second');
  });
  await view.controller.load(); const request = view.controller.loadMore();
  await started.promise;
  const count = view.states.length;
  view.controller.cancel(); assert.equal(signal.aborted, true);
  pending.resolve(page([999])); await request;
  assert.equal(view.states.length, count);
});

for (const changed of [session(2), session(1, 'rotated'), null]) {
  test(`account boundary ${changed?.csrfToken ?? 'logout'} invalidates old cards and continuation`, async () => {
    let owner = session(), calls = 0;
    const view = setup(async () => { calls++; return page([1], 'second'); }, { readSession: () => owner });
    await view.controller.load(); owner = changed;
    await view.controller.loadMore();
    assert.equal(view.latest().status, 'error');
    assert.equal(view.latest().reason, 'unauthorized');
    assert.equal(view.latest().home, undefined);
    await view.controller.loadMore(); assert.equal(calls, 1);
  });
}

for (const failed of [false, true]) test(`account switching during a page request rejects its late ${failed ? 'failure' : 'success'}`, async () => {
  const pending = deferred(), started = deferred(); let owner = session();
  const view = setup(async (_, { cursor }) => {
    if (cursor) { started.resolve(); return pending.promise; }
    return page([1], 'second');
  }, { readSession: () => owner });
  await view.controller.load(); const request = view.controller.loadMore();
  await started.promise;
  owner = session(2);
  if (failed) pending.reject(new Error('late network failure')); else pending.resolve(page([999]));
  await request;
  assert.equal(view.latest().status, 'error');
  assert.equal(view.latest().home, undefined);
});

test('changed interests within the same Topic invalidate its old cursor, but reordered interests do not', async () => {
  let interests = [10, 20], calls = 0;
  const view = setup(async () => { calls++; return page([calls], `page-${calls}`); }, {
    getMyTopics: async () => ({ topics: topics(42).topics.map(topic => ({ ...topic, subtopicIds: interests })) }),
  });
  await view.controller.load();
  interests = [20, 10]; await view.controller.loadMore();
  assert.equal(view.latest().home.cards.length, 2);
  interests = [30]; await view.controller.loadMore();
  assert.equal(calls, 2);
  assert.equal(view.latest().reason, 'scope-changed');
  assert.equal(view.latest().home, undefined);
});

test('an active Topic change is detected before requesting the next old-Topic page', async () => {
  let topicId = 42, calls = 0;
  const view = setup(async () => { calls++; return page([1], 'second'); }, { getMyTopics: async () => topics(topicId) });
  await view.controller.load(); topicId = 43;
  await view.controller.loadMore();
  assert.equal(calls, 1);
  assert.equal(view.latest().reason, 'scope-changed');
  assert.equal(view.latest().home, undefined);
});

test('a stalled later page aborts at the HOME deadline, retains cards, and permits retry', async () => {
  const pending = deferred(); let signal, calls = 0;
  const view = setup(async (_, { cursor }, init) => {
    if (!cursor) return page([1], 'second');
    signal = init.signal; return ++calls === 1 ? pending.promise : page([2]);
  }, { timeout: 10 });
  await view.controller.load(); const request = view.controller.loadMore();
  await new Promise(resolve => setTimeout(resolve, 35));
  assert.equal(signal.aborted, true);
  assert.equal(view.latest().home.cards.length, 1);
  assert.equal(view.latest().pagination.status, 'error');
  await view.controller.loadMore();
  pending.resolve(page([999])); await request;
  assert.deepEqual(Array.from(view.latest().home.cards, c => c.id), ['1', '2']);
});
