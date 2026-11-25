// Pure in-memory synthetic API. There is deliberately no fetch/proxy fallback.
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { root, origin } = require('./config.cjs');
const contents = JSON.parse(fs.readFileSync(path.join(root, 'fixtures/contents.json')));
const manifest = JSON.parse(fs.readFileSync(path.join(root, 'fixtures/manifest.json')));
const byId = new Map(contents.map(card => [card.id, card]));
const imageBytes = new Map(manifest.images.map(image => [image.page, fs.readFileSync(path.join(root, 'fixtures', image.name))]));
const topic = { id: 7, code: 'anime', name: '합성 애니메이션', subtopicIds: [19], status: 'active', deletedAt: null };
function createSession(variant, slideCount = 10) {
  return { variant, slideCount, run: crypto.randomUUID(), token: crypto.randomBytes(24).toString('hex'),
    csrf: crypto.randomBytes(24).toString('hex'), saved: new Set(), liked: new Set(), seen: new Set(),
    notificationSettings: { inAppEnabled: true, nightInAppEnabled: false, version: 1 } };
}
const slides = (id, count) => Array.from({ length: count }, (_, i) => ({
  page: i + 1, role: i ? 'body' : 'cover', eyebrow: 'LOCAL SYNTHETIC',
  titleLines: [byId.get(id).title], bodySentences: ['합성 슬라이드 ' + (i + 1)],
  imageUrl: '/api/v1/contents/' + id + '/slides/' + (i + 1) + '/image?topicId=7', width: 1308, height: 1632,
  layout: { template: 'cover', fitMode: 'contain', backgroundStyle: 'gradient', imageScale: 1,
    imageOffsetY: 0, contrastMode: 'dark', blurBackground: false,
    imageBox: { x: 0, y: 0, width: 1, height: 1 }, copyBox: { x: .05, y: .65, width: .9, height: .3 },
    textOverlayBox: { x: .05, y: .65, width: .9, height: .3 } },
}));
function feedCard(item, s) {
  return { kind: 'content', id: item.id, topicId: 7, recommendationId: null, title: item.title,
    productionType: 'human', summary: item.summary, imageUrl: '/api/v1/contents/' + item.id + '/slides/1/image?topicId=7',
    sourceName: '로컬 합성 Mock', sourceUrl: origin + '/__lab/source/' + item.id, publishedAt: '2026-11-25T00:00:00Z',
    saved: s.saved.has(item.id), savedItemId: s.saved.has(item.id) ? item.id + 1000 : null,
    recommendationReason: null, seen: false, isPromotional: false, notices: [],
    myReaction: { liked: s.liked.has(item.id), preference: null }, slides: slides(item.id, s.slideCount) };
}
function dispatch(url, method, headers, body, s) {
  const ok = (body, status = 200) => ({ body, status });
  const error = (code, status) => ok({ code, message: 'LOCAL MOCK: ' + code }, status);
  const p = url.pathname;
  if (p.startsWith('/api/v1/auth/login/')) return error('EXTERNAL_LOGIN_DISABLED', 403);
  if (!s) return error('AUTH_REQUIRED', 401);
  if (method === 'GET' && p === '/api/v1/auth/session') return ok({
    authenticated: true, sessionState: 'active', onboardingCompleted: true,
    user: { id: 12, accountType: 'user' }, csrfToken: s.csrf,
  });
  if (!['GET', 'HEAD'].includes(method) && headers['x-csrf-token'] !== s.csrf) return error('CSRF_INVALID', 403);
  if (method === 'GET' && p === '/api/v1/me/topics') return ok({ topics: [topic] });
  if (method === 'GET' && p === '/api/v1/topics') return ok({ topics: [topic] });
  if (method === 'GET' && p === '/api/v1/users/me') return ok({ userId: 12, nickname: '로컬 검증 회원', accountType: 'user', email: null, birthDate: null, profileImageUrl: null, profileImageId: null });
  if (p === '/api/v1/users/me/notification-settings') {
    if (method === 'GET') return ok(s.notificationSettings);
    if (method === 'PATCH') { s.notificationSettings = { ...s.notificationSettings, ...body, version: s.notificationSettings.version + 1 }; return ok(s.notificationSettings); }
  }
  if (method === 'GET' && p === '/api/v1/users/me/notifications/unread-count') return ok({ unreadCount: 0 });
  if (method === 'GET' && p === '/api/v1/users/me/notifications') return ok({ items: [], nextCursor: null });
  if (method === 'GET' && p === '/api/v1/topics/7/feed') {
    const limit = Number(url.searchParams.get('limit') || 20), cursor = url.searchParams.get('cursor');
    if (!Number.isInteger(limit) || limit < 1 || limit > 100) return error('INVALID_LIMIT', 422);
    const match = cursor?.match(/^v1-s(2|5|10)-after-(\d+)$/);
    if (cursor && (!match || Number(match[1]) !== s.slideCount || Number(match[2]) >= contents.length)) return error('INVALID_CURSOR', 400);
    const offset = cursor ? Number(match[2]) : 0;
    const items = contents.slice(offset, offset + limit).map(item => feedCard(item, s));
    const nextCursor = offset + items.length < contents.length ? 'v1-s' + s.slideCount + '-after-' + (offset + items.length) : null;
    return { ...ok({ selectedTopicId: 7, sections: [{ id: 'recommended', type: 'recommended', title: '로컬 합성 추천', subtopicId: null, contents: items }], nextCursor, emptyReason: null }),
      audit: { limit, cursor, nextCursor, ids: items.map(i => i.id), contentSha256: manifest.contentSha256, slideCount: s.slideCount } };
  }
  if (method === 'GET' && p === '/api/v1/users/me/bookmarks') return ok({
    items: contents.filter(c => s.saved.has(c.id)).map(c => ({ savedItemId: c.id + 1000, contentId: c.id, topicId: 7,
      title: c.title, summary: c.summary, imageUrl: '/api/v1/contents/' + c.id + '/slides/1/image?topicId=7',
      sourceName: '로컬 합성 Mock', tags: [], resurfaceEnabled: true })), nextCursor: null,
  });
  const image = p.match(/^\/api\/v1\/contents\/(\d+)\/slides\/(\d+)\/image$/);
  if (image && method === 'GET') {
    const id = Number(image[1]), page = Number(image[2]);
    if (url.searchParams.get('topicId') !== '7' || !byId.has(id) || page < 1 || page > s.slideCount) return error('IMAGE_NOT_FOUND', 404);
    return { status: 200, bytes: imageBytes.get(page), contentType: 'image/png',
      audit: { contentId: id, page, imageSha256: manifest.images[page - 1].sha256 } };
  }
  const content = p.match(/^\/api\/v1\/contents\/(\d+)(\/bookmark|\/like)?$/);
  if (content) {
    const id = Number(content[1]), item = byId.get(id), action = content[2];
    if (!item) return error('CONTENT_NOT_FOUND', 404);
    if (method === 'GET' && !action) {
      if (url.searchParams.get('topicId') !== '7') return error('TOPIC_REQUIRED', 422);
      return ok({ contentId: id, topicId: 7, title: item.title, publisher: '로컬 합성 Mock', publishedAt: '2026-11-25T00:00:00Z',
        sourceUrl: origin + '/__lab/source/' + id, displayMode: 'full_body', productionType: 'human', excerpt: item.summary,
        body: item.body, contentType: 'article', imageUrl: '/api/v1/contents/' + id + '/slides/1/image?topicId=7',
        subtopicIds: [19], practicalInfo: {}, notices: [], isPromotional: false, slides: slides(id, s.slideCount),
        share: { url: origin + '/__lab/source/' + id, text: item.title },
        myReaction: { liked: s.liked.has(id), preference: null, bookmarked: s.saved.has(id) }, channel: null });
    }
    if (action && ['PUT', 'DELETE'].includes(method)) {
      if ((method === 'PUT' ? body?.topicId : Number(url.searchParams.get('topicId'))) !== 7) return error('TOPIC_REQUIRED', 422);
      const set = action === '/bookmark' ? s.saved : s.liked;
      if (method === 'PUT') set.add(id); else set.delete(id);
      return action === '/bookmark' ? method === 'DELETE' ? ok(undefined, 204)
        : ok({ savedItemId: id + 1000, contentId: id, topicId: 7, tags: [], resurfaceEnabled: true, savedAt: '2026-11-25T00:00:00Z' })
        : ok({ contentId: id, liked: s.liked.has(id) });
    }
  }
  if (method === 'POST' && p === '/api/v1/users/me/activity-events') {
    for (const event of body?.events || []) if (['open', 'card_swipe'].includes(event.eventType)) s.seen.add(event.contentId);
    return ok({ saved: body?.events?.length || 0, duplicated: 0, rejected: 0 });
  }
  if (method === 'GET' && ['/api/v1/users/me/likes', '/api/v1/users/me/recent-views'].includes(p)) {
    const liked = p.endsWith('/likes'), set = liked ? s.liked : s.seen;
    return ok({ items: contents.filter(c => set.has(c.id)).map(c => ({ contentId: c.id, topicId: 7, title: c.title,
      summary: c.summary, imageUrl: null, sourceName: '로컬 합성 Mock', productionType: 'human',
      publishedAt: '2026-11-25T00:00:00Z', subtopicNames: [], [liked ? 'likedAt' : 'viewedAt']: '2026-11-25T00:00:00Z' })), nextCursor: null });
  }
  return error('MOCK_ROUTE_NOT_DEFINED', 404);
}
module.exports = { createSession, dispatch, manifest };
