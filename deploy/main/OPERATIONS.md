# Main operations handover

These files prepare the existing production workload for Main. They have not
changed a running server. Follow `docs/MAIN_PROMOTION_2026-11-24.md` for the
coordinated release, schema migration, frontend switch and rollback sequence.
The Checking branch and its data infrastructure remain in place.

## Preserved data and explicit opt-ins

`compose.main.yaml` still starts only the API, reusing database/user
`me_nu_checking`, Redis database 0 and the external `me-nu-checking_data`
network. Those legacy names intentionally remain production dependencies.
Never remove their containers, networks or volumes as branch cleanup.

The Main cookie and Redis prefix remain `menu_main_session` and `me_nu_main`.
Checking preview remains disabled. Debate remains disabled with threshold 10
unless explicitly configured; older Main manifests receive those same defaults.
No generation worker or paid daily task is installed or started by these tools.

Before starting the Main API, prepare `deploy/main/admin-evaluation` with mode
0755 and preserve the existing aggregate evaluation files with mode 0644. Only
this directory is mounted into Main, read-only. Compose will not create it.
Missing evaluation files mean unavailable scores, not zero scores. Do not put
raw observations, credentials or private case data into this public mount.

## Operational entrypoints

Run from the reviewed, clean Main checkout on the server under
`/home/ubuntu/apps/<safe-folder-name>`. Runtime guards verify Main's healthy
immutable image, production environment and exact existing database/network.
They also probe Alembic revisions inside that verified API using a read-only,
time-limited database connection and the deployed `service_schema_ready` policy.
An old `b7d4e2a9c601` database blocks activation even when `/health` is healthy.
Unit templates show the previously documented folder as an example; controllers
render the actual verified checkout path before installation. `dry-run` can be
reviewed locally and does not install a unit.

| Entrypoint | Actions | Handover boundary |
|---|---|---|
| `sudo python3 deploy/main/main_recommend.py` | `status`, `build`, `preview`, `up` | `up` requires the old Checking worker stopped **and its restart policy disabled**; it refuses to replace an existing Main worker. |
| `sudo python3 deploy/main/main_notices.py` | `preview` (default), `run`, `status`, `enable`, `disable`, `dry-run` | `run` and `enable` require the old notice timer disabled and inactive, and its service inactive. |
| `sudo python3 deploy/main/main_evaluation.py` | `dry-run`, `status`, `enable`, `disable`, `run` | Enabling and every collection require the old evaluation timer disabled and inactive, and its service inactive. |
| `sudo python3 deploy/main/main_publish_evaluation.py` | `--input <absolute-file> --dataset offline\|service` | Validates with the deployed immutable image without network access, then atomically publishes an aggregate artifact. |
| `sudo python3 deploy/main/main_debates.py` | `preview` (default), `run`, `status`, `enable`, `disable`, `dry-run` | No legacy counterpart. The timer runs `app.jobs.finalize_debates` inside the verified API at minutes 5, 15, …, 55 (second 30, clear of the notice job). It only maintains stored debate rows, so it may stay enabled while debate is off; to keep chat for an investigation, `disable` the timer too. The job gives up on its own at 180 s (lock wait 5 s): an unfinished run rolls back, one cut off during COMMIT may already be saved, and rerunning is safe. The unit allows 8 min so the 240 s docker exec limit always fires first. A run that meets a held rollout lock logs `skipped` and exits 0; other failures log a fixed reason code. Run `preview` before `enable`, and `enable` again after moving to a new checkout folder. |

Recommendation image builds use the reviewed full commit and record that commit
in an image label. Start uses its immutable image ID and the existing read-only
`/home/ubuntu/me-nu-model/service_context.pkl.gz`; no new context is generated.
Preview uses a read-only database transaction. Start retains the existing daily
06:00 KST/new-user cadence and approved-content visibility; it does not rewrite
existing recommendation rounds. No action automatically stops Checking.

Main evaluation deliberately continues using the private sibling directory
`/home/ubuntu/apps/.checking-evaluation-pipeline`. Its observations, HMAC secret,
pending candidate and receipt remain intact; both legacy and Main job locks are
held during collection. The new API only receives aggregate files under its
own mount. Existing `.checking-evaluation-artifacts` backups remain untouched;
new publication backups use `.main-evaluation-artifacts`. Preserve both.

Disabling a timer leaves any active job to finish. Before a rollback, stop and
verify the Main counterpart inactive before resuming Checking, so only one
scheduler owns each workload. Do not invoke a broad Compose down or prune.

Offline verification (no Docker daemon or database needed):

```sh
python -m unittest discover -s deploy/main -p 'test_main_*.py' -v
python deploy/main/validate.py prepare
```

The tests include offline Docker Compose rendering. Runtime preflight and the
operator's reviewed deployment are separate steps; passing tests does not
claim that the production server has been updated.

## Disposable PostgreSQL rehearsal

The server only needs Python's standard library and its existing Docker daemon:

```sh
python3 deploy/main/postgres_rehearsal.py plan
sudo python3 deploy/main/postgres_rehearsal.py apply --image-tag <reviewed-full-commit>
```

`apply` requires that commit checked out cleanly and both images already present:
`me-nu-backend-main:<reviewed-full-commit>` and `pgvector/pgvector:pg17`. It never
pulls or builds. Image tags resolve to immutable IDs before creation. It starts
only two uniquely labeled temporary containers: PostgreSQL has no network or
published port and keeps its database in a bounded 512 MiB tmpfs; the test process
joins only that container's network namespace and connects over loopback. Both
containers have 512 MiB memory bounds. Only reviewed application, migration and
test sources are mounted read-only; the backend root and `.env` are not mounted.

The focused tests cover Main withdrawal/admin locking and synthetic data
preservation from `b7d4e2a9c601` to the service schema. All collected tests must
pass without skips. Output contains counts only. Cleanup verifies this run's
container IDs and labels before removal; it never removes existing images,
networks, volumes or service containers. If interrupted cleanup cannot finish,
the command reports failure and the operator must inspect only its uniquely
labeled rehearsal containers. An unexecuted rehearsal is not a passed database
validation.
