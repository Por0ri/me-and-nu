# Administrator generation observation

The main dashboard now has a separate automatic-generation panel. Its
`GET /api/v1/admin/generation` endpoint uses the existing fresh administrator
session/grant checks and private, no-store responses. The existing dashboard,
content approval, public feed and recommendation calculations are unchanged.

## Data flow and scope

The host-only observer in `deploy/generation-observability/` reads the current
scheduler policy/checkpoint, the two generation job ledgers in a PostgreSQL
read-only transaction, Redis queue lengths with read-only commands, and worker
container/systemd state. It publishes only bounded aggregate JSON. It does not
call a model, publish/consume messages, acquire generation locks, resume a job,
start a worker, change limits, or access the service database.

An independent one-minute observation timer writes `generation.json` using an
atomic replace. Mount the **directory**, not a single file, read-only into the
API and set `ADMIN_GENERATION_SNAPSHOT_PATH` to the file inside that mount.
The API receives no jobs DB credentials, generation keys, scheduler files or
Docker socket. The frontend refreshes only the new inexpensive endpoint every
30 seconds while visible; it does not poll all existing dashboard SQL queries.

Missing/invalid inputs are unavailable, never zero. At 180 seconds the API marks
the observation stale. After a KST date change it hides old daily counters;
historical values are not silently presented as today's generation. The panel
shows the observation time and distinguishes errors, stopped controllers and
unavailable sources. Container-running state is not a Celery heartbeat or a
guarantee that an LLM provider is healthy.

## Counting and accounting

- Daily attempts come from counted reservations after the current day boundary
  in the checkpoint, with the same 40-attempt limit as the controller. Worker
  delivery, retry and attempt-table row counts do not create extra slots.
- AI review passes follow the controller's completed successful delivery
  criterion and 20-pass target. They are not operator approval/publication
  counts. Article and RAG work share these limits.
- Job state summaries cover the current day's owned jobs. Outcomes such as held
  and ready-for-staging overlap execution states and must not be added together.
- Costs are recorded **estimates in USD**, not provider invoices. Known totals,
  RAG partial evidence and verified recovery partial evidence are counted once.
  Unmeasured reservations are separate from spending. Unknown costs and usage
  awaiting finalization prevent a definitive total from being displayed.
- Usage awaiting finalization can belong to a failed job; it does not imply a
  model is currently running. Full historical cost includes today's records
  and the original baseline, so it must not be added to the daily cost again.
- The current checkpoint carries inherited history. Summing all dated checkpoint
  files would double-count it and is deliberately not supported.

## Deployment and rollback

Deploy only the new API image, a read-only snapshot directory mount, the setting
above, and the independent observer service/timer. The collector examples are
templates; installation must pin the reviewed source release. Keep the existing
generator systemd units, policy, job/cost ledger, worker images, provider key
mounts, service data and recommendation container/context untouched.

For the 2026-11-27 main deployment, the API overlay is recorded in the immutable
observation release alongside the deployed image ID. Future API deployments
must keep that overlay or equivalently retain the snapshot directory mount and
setting. Rollback restores the prior API image/configuration; stopping the
observation timer affects only dashboard freshness, never automatic generation.

## Verification

Tests cover strict JSON/size/path handling, freshness and KST rollover, incomplete
cost evidence, duplicate/retry reservation counting, snapshot replacement races,
actual exporter-to-API compatibility, administrator authorization/revocation,
no-store behavior, frontend validation/display/polling, and existing admin,
recommendation-feed and content-read regressions. All generation examples are
synthetic or read existing records; verification does not make provider calls.

Deployment receipts and live comparisons are kept in the private/local
`out/admin-generation-deploy-20261127/` operations directory. Repository code
does not embed production snapshots, credentials or generated content.
