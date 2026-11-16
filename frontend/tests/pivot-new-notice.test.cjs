const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const mod = { exports: {} };
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, '../lib/pivot/new-notice.ts'), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, { module: mod, exports: mod.exports, AbortController });
const { createNewNoticeStore } = mod.exports;
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
function fixture() {
  let time = new Date(2026, 10, 12, 12).getTime(), unread = true, failRead = false;
  const requests = [];
  const inbox = { kind: 'service',
    async hasNewToday(signal) { requests.push(signal); return unread; },
    async markRead() { if (failRead) throw new Error('read failed'); return '2026-11-12T03:00:00Z'; },
  };
  return { requests, inbox, store: createNewNoticeStore(inbox, () => time),
    setTime(value) { time = value; }, advance(ms) { time += ms; }, unread(value) { unread = value; }, failRead() { failRead = true; } };
}

test('One shared read serves simultaneous headers and route entries; stale snapshots revalidate', async () => {
  const f = fixture(); f.store.start();
  await Promise.all([f.store.refresh(), f.store.refresh(), f.store.refresh()]);
  assert.equal(f.requests.length, 1); assert.equal(f.store.getSnapshot(), true);
  f.advance(59_999); await f.store.refresh(); assert.equal(f.requests.length, 1);
  f.advance(1); f.unread(false); await f.store.refresh();
  assert.equal(f.requests.length, 2); assert.equal(f.store.getSnapshot(), false);
  f.store.stop();
});

test('Confirmed reads refresh the shared dot and preserve it when another new unread notice remains', async () => {
  const f = fixture(); f.store.start(); await f.store.refresh();
  await f.store.source.markRead(1, new AbortController().signal); await f.store.refresh();
  assert.equal(f.requests.length, 2); assert.equal(f.store.getSnapshot(), true);
  f.unread(false); await f.store.source.markRead(2, new AbortController().signal); await f.store.refresh();
  assert.equal(f.requests.length, 3); assert.equal(f.store.getSnapshot(), false);
  f.store.stop();
});

test('A failed mark-read does not hide the dot or trigger a success refresh', async () => {
  const f = fixture(); f.store.start(); await f.store.refresh(); f.failRead();
  await assert.rejects(f.store.source.markRead(1, new AbortController().signal), /read failed/);
  assert.equal(f.store.getSnapshot(), true); assert.equal(f.requests.length, 1); f.store.stop();
});

test('A failed refresh after a successful read retries on entry even within the cache window', async () => {
  const f = fixture(); f.store.start(); await f.store.refresh();
  f.inbox.hasNewToday = async () => { throw new Error('offline'); };
  await f.store.source.markRead(1, new AbortController().signal); await new Promise(setImmediate);
  assert.equal(f.store.getSnapshot(), true);
  f.inbox.hasNewToday = async () => false;
  await f.store.refresh(); assert.equal(f.store.getSnapshot(), false); f.store.stop();
});

test('A late read from before a mutation cannot overwrite the refreshed dot', async () => {
  const first = deferred(), second = deferred(), signals = [];
  const store = createNewNoticeStore({ kind: 'service', hasNewToday(signal) { signals.push(signal); return signals.length === 1 ? first.promise : second.promise; }, async markRead() { return 'read'; } });
  store.start(); const old = store.refresh();
  await store.source.markRead(1, new AbortController().signal);
  assert.equal(signals[0].aborted, true); const latest = store.refresh();
  second.resolve(false); await latest; first.resolve(true); await old;
  assert.equal(store.getSnapshot(), false); store.stop();
});

test('Stopping an owner immediately clears and aborts; late responses cannot expose that owner', async () => {
  const held = deferred(); let signal;
  const store = createNewNoticeStore({ kind: 'service', hasNewToday(s) { signal = s; return held.promise; } });
  store.start(); const pending = store.refresh(); store.stop();
  assert.equal(signal.aborted, true); held.resolve(true); await pending;
  assert.equal(store.getSnapshot(), false); await store.refresh();
  const next = fixture(); next.unread(false); next.store.start(); await next.store.refresh();
  assert.equal(next.store.getSnapshot(), false); next.store.stop();
});

test('Effect restart ignores the old request and starts a fresh read', async () => {
  const held = deferred(); let calls = 0;
  const store = createNewNoticeStore({ kind: 'service', hasNewToday() { return ++calls === 1 ? held.promise : Promise.resolve(false); } });
  store.start(); const old = store.refresh(); store.stop(); store.start(); await store.refresh();
  held.resolve(true); await old; assert.equal(calls, 2); assert.equal(store.getSnapshot(), false); store.stop();
});

test('A date rollover clears yesterday’s dot even if the new read fails, then retries', async () => {
  const f = fixture(); f.store.start(); await f.store.refresh();
  f.setTime(new Date(2026, 10, 13, 0).getTime());
  f.inbox.hasNewToday = async () => { throw new Error('offline'); };
  await f.store.refresh(); assert.equal(f.store.getSnapshot(), false);
  f.inbox.hasNewToday = async () => true;
  await f.store.refresh(); assert.equal(f.store.getSnapshot(), true); f.store.stop();
});
