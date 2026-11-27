# Generation observation snapshot

The host exporter reads the current scheduler binding, journal and checkpoint,
the dedicated jobs DB through `docker exec ... psql` in a read-only transaction,
and the generation Redis using `EVAL_RO` with only LLEN/HLEN/ZCARD. It never
starts/stops workers, publishes queue messages, calls providers, changes history,
or accesses the service/recommendation DB. Docker access stays on the host.

Run the reviewed source once with:

```sh
python3 export_snapshot.py --output-directory /home/ubuntu/me-nu-generation-observability
```

The exporter uses the Worker's standard-library-only
`worker/menu_worker/daily_limits.py` validator. In a standalone release, include
an exact copy beside `export_snapshot.py` and seal both files in the deployment
manifest. A repository checkout reads the canonical Worker file directly. No
Worker package, provider, DB client or bytecode cache is loaded or created.

Provision the output directory as root-owned mode 0755. The exporter replaces
`generation.json` atomically at mode 0644. Mount **the directory**, read-only,
into the API; mounting one file would retain the old inode after replacement.
The API must receive neither the Docker socket nor jobs DB/Redis credentials.
The included independent one-minute timer is a deployment template, not an
activation command or a modification of the generation scheduler.

The snapshot contains only aggregate numbers, fixed status names and timestamps.
It does not contain titles, article URLs, prompts, source text, account details,
keys, DSNs, raw logs, private paths, requests or recovery documents. File and
record sizes, subprocess duration and race retries are bounded. The exporter
does not acquire controller/generation locks: after collecting, it rechecks every
source file byte-for-byte and retries a concurrent checkpoint replacement at most
three times. An unreadable/invalid source produces a fully unavailable snapshot;
failure is never encoded as zero. API freshness is independently limited to 180
seconds. A current KST day mismatch keeps history but hides daily/job counters.

## Accounting definitions

- Daily attempts are `counts_slot=true` reservations after the current
  `daily_policy.start_record_index`. The reviewed policy explicitly specifies
  `max_attempts: null` in daily-policy schema 4 for no attempt cap, or `40` in
  schema 3 for the earlier shared cap. A validated append-only same-day limit
  revision also removes the cap without replacing the schema-3 policy or its
  existing records. Prefix, counters, approval and later record bindings use the
  same validator as the Worker.
  With no cap, both `max_attempts` and `remaining_attempts` are null while the
  actual attempt count continues past 40. An absent or invalid policy value is
  unavailable, never interpreted as unlimited. Safe retries, dispatch attempts
  and duplicate deliveries do not create more dashboard slots.
  `duplicate` records with `counts_slot=false` do not consume slots.
- Daily passes match the controller: completed, succeeded,
  `ready_for_staging`, counted records, deduplicated by article source+URL or RAG
  identity. Twenty is the target. This is Agent review, **not operator approval
  or public content count**.
- Daily costs sum known estimates once per record. Full recorded totals, RAG
  partial usage and verified recovery receipt partial usage are mutually
  exclusive. Cost reservations stay separate and are never called spending.
  Unknown and currently pending costs make the total null while preserving the
  known subtotal. Provider invoice/billing hard caps are not represented.
- History is the newest checkpoint's full inherited ledger plus its original
  baseline, counted once. Do not sum all daily checkpoint files: they contain
  overlapping history. Previous unknown costs remain unknown after rollover;
  they do not turn today's known amount into an unknown amount.
- Job status/outcome aggregates cover only today's owned, counted job UUIDs,
  across the two ledgers. Outcomes overlap execution states and must not be
  added together. Attempt rows and fake/probe queues are excluded. Checkpoint
  counters and DB state can briefly differ while completion is being recorded.
- Broker backlog sums priorities 0/3/6/9 separately for the article and RAG
  queue. `unacked` and `unacked_index` describe the same deliveries; neither
  should be added to the other or to DB running counts as unique jobs.
- Runtime worker state is Docker container state, not proof of provider health
  or Celery heartbeat. Existing authentication protects the API endpoint; this
  exporter does not implement or bypass authentication.

Local no-network checks:

```sh
python -B -m unittest discover -s deploy/generation-observability -p 'test_*.py' -v
```
