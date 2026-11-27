// Synthetic metadata only. No job IDs, secrets, source text or service DB access.
function generationFixture() {
  const jobs = () => ({ queued: 0, running: 0, succeeded: 0, failed: 0, needsAttention: 0, cancelled: 0, held: 0, rejected: 0, skipped: 0, readyForStaging: 0 });
  return {
    availability: 'partial', reason: 'source_partial', observedAt: '2026-11-27T04:00:02Z', generatedAt: '2026-11-27T04:00:00Z',
    ageSeconds: 2, staleAfterSeconds: 180, timezone: 'Asia/Seoul', policyDate: '2026-11-27',
    daily: { attempts: 3, maxAttempts: 40, passes: 1, successTarget: 20, remainingAttempts: 37, remainingPasses: 19,
      pendingRecords: 1, duplicateRecords: 0, knownEstimatedCostUsd: '0.04703593', totalEstimatedCostUsd: null,
      reservedCostUsd: '0', unknownCostJobs: 1, inflightCostJobs: 1, articleAttempts: 1, ragAttempts: 2 },
    history: { knownEstimatedCostUsd: '1.04703593', baselineEstimatedCostUsd: '1', totalEstimatedCostUsd: null, unknownCostJobs: 3 },
    jobs: { article: { ...jobs(), failed: 1 }, rag: { ...jobs(), running: 1, succeeded: 1, readyForStaging: 1 } },
    broker: { articleQueued: 0, ragQueued: 0, unacked: 1, unackedIndex: 1 },
    runtime: { controller: 'active', timer: 'active', articleWorker: 'running', ragWorker: 'running' },
  };
}
function unavailableFixture(reason = 'snapshot_missing') {
  return { ...generationFixture(), availability: 'unavailable', reason, generatedAt: null, ageSeconds: null,
    policyDate: null, daily: null, history: null, jobs: null, broker: null, runtime: null };
}
module.exports = { generationFixture, unavailableFixture };
