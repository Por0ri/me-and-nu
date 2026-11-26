// Synthetic responses for the existing GET contracts. No server, DB or account.
const now = '2026-11-26T03:00:00Z', old = '2026-10-01T03:00:00Z';
const days = { '7d': 7, '30d': 30, '3m': 90, '6m': 180 };
const start = period => new Date(Date.parse(now) - days[period] * 86400000).toISOString();
const metric = (value = 0, unit = 'count') => ({ availability: 'available', reason: null, value, unit, definition: '격리 테스트 집계', numerator: null, denominator: null });
const unknown = () => ({ ...metric(null), availability: 'unavailable', reason: 'not_collected' });
const session = () => ({ authenticated: true, sessionState: 'active', user: { id: 30, accountType: 'user' }, onboardingCompleted: true, csrfToken: 'synthetic-csrf' });
function detail(type = 'report', id = '41') {
  const report = type === 'report';
  return { id: (report ? 'R-' : 'I-') + id, recordType: type, recordId: id, kind: report ? 'content' : 'service',
    title: report ? '합성 콘텐츠' : null, reasonCode: report ? 'wrong_info' : null, reason: report ? '잘못된 정보' : null,
    rawStatus: 'received', status: 'waiting', receivedAt: old, updatedAt: old,
    message: report ? '오래된 신고의 고유 내용' : '오래된 문의의 고유 내용', target: report ? '합성 콘텐츠' : null, content: null,
    answer: null, answeredAt: null, answerAvailability: 'none',
    actions: { allowedActions: report ? ['start_review', 'resolve', 'reject'] : ['answer'], disabledReason: null, scope: { actorUserId: '30', adminAccountId: '7' } } };
}
function attention(kind = 'all', page = 1, count = 6) {
  const records = ['report', 'inquiry'].flatMap(type => Array.from({ length: count }, (_, i) => ({
    kind: type, recordId: i === count - 1 ? 41 : i + 1, status: 'received', category: type === 'report' ? 'wrong_info' : 'service', createdAt: old, contentId: type === 'report' ? 101 : null,
  }))).sort((a, b) => a.recordId - b.recordId || a.kind.localeCompare(b.kind));
  const selected = records.filter(item => kind === 'all' || item.kind === kind);
  return { generatedAt: now, availability: kind === 'notice' ? 'unavailable' : kind === 'all' ? 'partial' : 'available',
    reason: ['all', 'notice'].includes(kind) ? 'notice_workflow_not_implemented' : null,
    knownTotal: selected.length, reports: count, inquiries: count, scheduledNotices: unknown(), page, pageSize: 5,
    items: selected.slice((page - 1) * 5, page * 5) };
}
function dashboard(period = '7d') {
  return { meta: { generatedAt: now, window: { period, startAt: start(period), endAt: now, timezone: 'UTC' }, eventDataSource: 'automated_test', scope: 'connected_database', limitations: [] },
    usage: { activeUsers: metric(3), contentReadSegments: metric(4), signupFeedObservationRate: metric(50, 'percent') },
    topContents: { availability: 'available', reason: null, definition: '합성 열람 기록', items: [] },
    preferenceDistribution: { availability: 'unavailable', reason: 'no_samples', definition: '현재 답변', sampleSize: null, items: [] },
    attention: attention(), todayNotices: unknown(),
    generation: { availability: 'partial', reason: 'recorded_runs_only', definition: '기록된 작업', windowStartAt: new Date(Date.parse(now) - 86400000).toISOString(), windowEndAt: now, succeeded: 0, inProgress: 0, failed: 0, cancelled: 0 },
    recommendationPerformance: { availability: 'unavailable', reason: 'not_configured', audience: 'all', period, periodStart: start(period), periodEnd: now,
      timeBasis: 'evaluation_completed_at', source: 'offline_evaluation', metricUnit: 'ratio', k: 10, groundTruthPerCase: 1,
      evaluationId: null, evaluatedAt: null, baselineModelId: null, currentModelId: null, conditions: null, cohorts: [], models: [] } };
}
function recentReports() {
  // The default seven-day list deliberately excludes the old attention records.
  return { items: [], total: 0, page: 1, pageSize: 5, periodStart: start('7d'), periodEnd: now, statistics: { all: 0, waiting: 0, reviewing: 0, completed: 0 } };
}
module.exports = { now, old, session, detail, attention, dashboard, recentReports };
