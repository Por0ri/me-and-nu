const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');

function load(file, dependencies = {}) {
  const module = { exports: {} };
  const source = fs.readFileSync(path.join(__dirname, '..', file), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  });
  vm.runInNewContext(outputText, {
    module, exports: module.exports, URL, process: { env: {} },
    require(name) {
      assert.ok(Object.hasOwn(dependencies, name), `Unexpected import: ${name}`);
      return dependencies[name];
    },
  }, { filename: file });
  return module.exports;
}

const services = load('lib/pivot/services.ts', { '@/lib/api': {}, './consent': {} });
const carouselData = {
  mapCoverImage(value, alt, contentId) {
    if (value === 'broken') throw new Error('Invalid content response');
    return value ? { src: `https://api.test${value}`, alt, protected: true, contentId } : undefined;
  },
};
const { formatRelativeDay } = load('lib/relative-time.ts');
const { createServiceNotificationInbox, createDemoNotificationInbox, findInboxNotification, newPostTarget, noticeHeadline, postNoticeImage, hasUnreadToday, sortInbox } =
  load('lib/pivot/notification-inbox.ts', { '@/lib/api': {}, './services': services, './carousel-data': carouselData });
const owner = {
  authenticated: true, sessionState: 'active', onboardingCompleted: true,
  user: { id: 12, accountType: 'user' }, csrfToken: 'owner-csrf',
};
const readAt = '2026-11-10T01:27:00Z';
const notice = { notificationId: 17, type: 'system_notice', isRead: false, body: '첫 문단\n\n다음 문단' };
const newPost = { notificationId: 18, type: 'topic_new_post', isRead: false, title: '‘체인소 맨’ 새 글', body: '레제편 굿즈', topicId: 1, contentId: 501 };
const signal = () => new AbortController().signal;

function fixture() {
  let current = owner;
  let cookie = owner;
  let refreshes = 0;
  const requests = [];
  const api = {
    async getSession() { return cookie; },
    async getNotifications(options) {
      requests.push(options);
      return {
        items: [notice, newPost, { ...newPost, notificationId: 19, contentId: null }, { type: 'follow' }],
        nextCursor: 'next',
      };
    },
    async getUnreadNotificationCount() { return { unreadCount: 2 }; },
    async markNotificationRead(notificationId, init) {
      requests.push({ notificationId, init });
      return { notificationId, isRead: true, readAt };
    },
  };
  return {
    requests, api,
    source: createServiceNotificationInbox(() => current, () => { refreshes++; }, api, () => {}),
    changeCookie(session) { cookie = session; },
    changeOwner(session) { current = session; },
    get refreshes() { return refreshes; },
  };
}

test('Service preserves pagination and full body, allows only notices and openable new posts, and pins CSRF for confirmed writes', async () => {
  const { source, requests } = fixture();
  const options = { unreadOnly: true, cursor: 'page-2', signal: signal() };
  const page = await source.list(options);
  assert.equal(requests[0], options);
  assert.equal(page.nextCursor, 'next');
  assert.deepEqual(page.items.map((item) => item.notificationId), [17, 18]);
  assert.equal(page.items[0].body, notice.body);
  assert.equal(await source.count(options.signal), 2);
  assert.equal(await source.markRead(notice.notificationId, options.signal), readAt);
  assert.equal(requests[1].init.headers['X-CSRF-Token'], owner.csrfToken);
  assert.equal(requests[1].init.signal, options.signal);
});

test('A post notice shows its own title with a kind prefix, and a notice keeps its title', () => {
  assert.equal(noticeHeadline(newPost), '[NEW] 레제편 굿즈');
  assert.equal(noticeHeadline({ ...newPost, type: 'recommended_post' }), '[추천] 레제편 굿즈');
  assert.equal(noticeHeadline({ ...notice, title: '[공지] 업데이트' }), '[공지] 업데이트');
});

test('Inbox time counts calendar days after 24 hours and says 어제 for the day before', () => {
  const now = new Date(2026, 10, 11, 23, 0).getTime();
  const at = (day, hour) => new Date(2026, 10, day, hour, 0).toISOString();
  assert.equal(formatRelativeDay(at(11, 3), now), '20시간 전');
  assert.equal(formatRelativeDay(at(10, 22), now), '어제');
  assert.equal(formatRelativeDay(at(10, 1), now), '어제');
  assert.equal(formatRelativeDay(at(9, 23), now), '2일 전');
  assert.equal(formatRelativeDay(at(1, 12), now), '10일 전');
  assert.equal(formatRelativeDay(undefined, now), null);
});

test('Unread notices stay on top and return to time order once read', () => {
  const row = (notificationId, type, isRead, createdAt) => ({ notificationId, type, isRead, createdAt });
  const items = [
    row(5, 'recommended_post', false, '2026-11-11T09:00:00Z'),
    row(4, 'topic_new_post', true, '2026-11-11T08:00:00Z'),
    row(3, 'system_notice', false, '2026-11-10T09:00:00Z'),
    row(2, 'system_notice', false, '2026-11-01T09:00:00Z'),
    row(1, 'topic_new_post', false, '2026-11-09T09:00:00Z'),
  ];
  const ids = (list) => Array.from(list, (item) => item.notificationId);
  assert.deepEqual(ids(sortInbox(items)), [3, 2, 5, 4, 1]);
  assert.deepEqual(ids(items), [5, 4, 3, 2, 1]);
  assert.deepEqual(ids(sortInbox(items.map((item) => item.notificationId === 3 ? { ...item, isRead: true } : item))), [2, 5, 4, 3, 1]);
});

test('The bell dot needs an unread notice that arrived today', async () => {
  const now = new Date(2026, 10, 11, 9, 0).getTime();
  const today = new Date(2026, 10, 11, 0, 30).toISOString();
  const yesterday = new Date(2026, 10, 10, 23, 30).toISOString();
  assert.equal(hasUnreadToday([{ isRead: false, createdAt: today }], now), true);
  assert.equal(hasUnreadToday([{ isRead: true, createdAt: today }], now), false);
  assert.equal(hasUnreadToday([{ isRead: false, createdAt: yesterday }], now), false);
  assert.equal(hasUnreadToday([], now), false);

  const run = fixture();
  const seen = [];
  run.api.getNotifications = async (options) => {
    seen.push(options);
    return { items: [{ ...notice, createdAt: new Date().toISOString() }], nextCursor: null };
  };
  assert.equal(await run.source.hasNewToday(signal()), true);
  assert.equal(seen[0].unreadOnly, true);
  assert.equal(seen[0].limit, 5);
});

test('A post notice uses its cover, and a missing or unusable one leaves no image', () => {
  const imageUrl = '/api/v1/contents/501/slides/1/image?topicId=1';
  assert.equal(postNoticeImage({ ...newPost, imageUrl }).src, `https://api.test${imageUrl}`);
  assert.equal(postNoticeImage({ ...newPost, imageUrl }).alt, '');
  assert.equal(postNoticeImage({ ...newPost, imageUrl: null }), undefined);
  assert.equal(postNoticeImage({ ...newPost, imageUrl: 'broken' }), undefined);
  assert.equal(postNoticeImage({ ...notice, imageUrl }), undefined);
  assert.equal(postNoticeImage({ ...newPost, contentId: null, imageUrl }), undefined);
});

test('A new-post notice opens its post, and other notices open nothing', () => {
  assert.deepEqual({ ...newPostTarget(newPost) }, { postId: '501', contentId: '501', topicId: 1 });
  assert.deepEqual({ ...newPostTarget({ ...newPost, type: 'recommended_post' }) }, { postId: '501', contentId: '501', topicId: 1 });
  assert.equal(newPostTarget(notice), null);
  assert.equal(newPostTarget({ ...newPost, topicId: 0 }), null);
  assert.equal(newPostTarget({ ...newPost, contentId: 1.5 }), null);
});

test('Service rejects an unconfirmed write and prevents writes after same-user cookie rotation', async () => {
  const f = fixture();
  f.api.markNotificationRead = async () => ({ notificationId: notice.notificationId, isRead: false, readAt: null });
  await assert.rejects(f.source.markRead(notice.notificationId, signal()), /Unconfirmed/);
  f.api.markNotificationRead = async () => assert.fail('must not write after cookie rotation');
  f.changeCookie({ ...owner, csrfToken: 'rotated' });
  await assert.rejects(f.source.markRead(notice.notificationId, signal()), /session changed/);
  assert.equal(f.refreshes, 1);
});

test('Service discards a response if its cookie or mounted owner changed in flight', async () => {
  const f = fixture();
  f.api.getNotifications = async () => {
    f.changeCookie({ ...owner, user: { ...owner.user, id: 13 } });
    return { items: [notice], nextCursor: null };
  };
  await assert.rejects(f.source.list({ signal: signal() }), /session changed/);
  assert.equal(f.refreshes, 1);
  f.changeCookie(owner);
  f.api.markNotificationRead = async () => {
    f.changeOwner(null);
    return { notificationId: notice.notificationId, isRead: true, readAt };
  };
  await assert.rejects(f.source.markRead(notice.notificationId, signal()), /session changed/);
});

test('Detail fallback follows all-list cursors and stops on repeated cursors or cancellation', async () => {
  const cursors = [];
  const source = { async list({ cursor, unreadOnly }) {
    assert.equal(unreadOnly, undefined);
    cursors.push(cursor);
    return cursor ? { items: [notice], nextCursor: null } : { items: [], nextCursor: 'next' };
  } };
  assert.equal(await findInboxNotification(source, 17, signal()), notice);
  assert.deepEqual(cursors, [undefined, 'next']);
  assert.equal(await findInboxNotification(source, 20, signal()), null);
  const loop = { async list() { return { items: [], nextCursor: 'next' }; } };
  await assert.rejects(findInboxNotification(loop, 17, signal()), /Repeated/);
  const cancelled = new AbortController();
  cancelled.abort();
  await assert.rejects(findInboxNotification(source, 17, cancelled.signal), { name: 'AbortError' });
});

test('Demo starts with three notices and keeps read state local to its instance', async () => {
  const demo = createDemoNotificationInbox(Date.parse(readAt));
  const requestSignal = signal();
  const unread = await demo.list({ unreadOnly: true, signal: requestSignal });
  assert.equal(unread.items.length, 1);
  await demo.markRead(unread.items[0].notificationId, requestSignal);
  assert.equal(await demo.count(requestSignal), 0);
  assert.equal((await demo.list({ signal: requestSignal })).items.length, 3);
  assert.equal(await createDemoNotificationInbox().count(requestSignal), 1);
});
