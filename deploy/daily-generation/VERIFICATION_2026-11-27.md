# Daily success target transition — 2026-11-27

## Approved behavior

Remove the combined article/RAG attempt cap. Generate sequentially until 20 distinct Agent-reviewed results per Asia/Seoul calendar day; publication still requires an operator approval. Preserve the KST boundary, job timeout, duplicate prevention, and stop-on-uncertain-generation/usage guards. No additional monetary cap was requested for this count-based run.

The reviewed stopped production baseline is 32 historical records, with 9 attempts and 6 passes on November 27. The 6 passes remain counted. Failed jobs, unknown historical costs, source policies, admissions, exports and A08 recovery receipts retain their identities.

## Implementation

- New Worker daily policy v4 and host policy v3 explicitly use `max_attempts: null`; no large placeholder limit.
- A stopped v3 day can append a reviewed limit revision without replacing the sealed policy, plan or previous records. New records bind its digest.
- New RAG admission v2 supports ordinals over 40. Original admission v1 retains its old limits and remains readable.
- The importer accepts both original exports and new v2 exports. For a same-day revision it requires a separately reviewed immutable revision file and exact digest. Imported content remains pending review.
- Dashboard API/UI and the read-only observer display uncapped attempts separately from the 20-pass target.
- Deployments require the new API/importer image and canonical daily-limit validator alongside host scheduler and observer scripts.

## Offline validation

- Worker: 122 tests and 124 subtests passed without networking/provider credentials.
- PostgreSQL: 14 recovery tests (including 2 new over-40/history tests) and 43 existing regression tests passed in an isolated internal-only Docker database.
- Importer: 49 tests passed in Linux Docker with network disabled.
- Dashboard backend: 62 passed, 1 Windows symlink test skipped; frontend: 12 passed and changed-entry TypeScript checks passed.
- Observer: 24 tests passed. Scheduler success-only scenarios: 4 passed; existing scheduler tests: 44 passed, 2 Windows filesystem skips.
- State migration: reviewed-plan/CAS, immutable backup, crash replay, exact prefix/seen/cost/recovery preservation, and active-writer rejection are covered separately.
- Agent and recommendation source files are unchanged.

## Validation issues corrected

- A new test initially unpacked a two-value function return as three values; only the fixture was corrected and the full Worker group then passed.
- Windows socket blocking also blocked asyncio's local self-pipe and made the backend harness unsuitable. No application code was changed for this; importer tests passed in network-disabled Linux Docker.
- Independent review caught pretty-printed approval JSON conflicting with the importer's canonical JSON contract. The migration now writes the revision in canonical form.

This document records code/test evidence only. Operational image IDs, backups, migration receipts, restart state and observed paid-generation outcomes are recorded separately; passing these tests does not itself mean generation has resumed.
