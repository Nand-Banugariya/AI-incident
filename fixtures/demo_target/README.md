# Demo target

A small order service with two planted faults. The investigation platform needs something real to investigate: the MCP servers read its logs, database and code (steps E3 and E5), and the golden-incident eval (F7) compares the platform's conclusions with `ground_truth/`.

## Run it

```powershell
cd fixtures/demo_target
docker compose up -d --build     # first start seeds 2M orders (~1 min); wait for both services to be healthy
docker compose ps
python load.py                   # ~45 s; triggers both faults, writes runs/<run_id>/manifest.json
```

`load.py` exits 0 only if both faults reproduced. Reset everything with `docker compose down -v` and delete `logs/`.

| Service | Host address | Notes |
|---|---|---|
| demo-api | `http://127.0.0.1:58080` | FastAPI, source in `api/demo_api/` |
| postgres | `127.0.0.1:55432`, db `target_db` | superuser `postgres` / `demo-superuser-local-only`; app role `demo_app`. Local fixture only |

Ports bind to 127.0.0.1 only. The passwords are fixture values. Never reuse them.

## The faults

| Fault | Trigger | What happens | Ground truth |
|---|---|---|---|
| 1. Missing index | `GET /orders/search?email=…` under concurrency (24 threads) | `orders.customer_email` has no index, so each search is a full scan. It takes ~100 ms alone, but under load the scans exceed `demo_app`'s 300 ms `statement_timeout`, and the API returns 503 | `ground_truth/fault1_missing_index.json` |
| 2. Missing config row | `POST /refunds/{order_id}` | `app_settings` has no `refund_policy` row, so `calculate_refund_deadline()` raises `TypeError: 'NoneType' object is not subscriptable`, and every request returns 500 | `ground_truth/fault2_missing_config.json` |

If fault 1 doesn't reproduce on fast hardware, raise `--fault1-concurrency`, or reseed with more rows: `DEMO_ORDERS_ROWS=5000000 docker compose up -d` after `down -v`.

## Log files (host side, under `logs/`)

These are what the Linux Logs and Database MCP servers mount read-only.

**`logs/app/app.log`** (application). Every entry starts with an ISO 8601 UTC timestamp. Stack-trace lines follow with no timestamp, so they belong to the entry above them:
```
2026-10-10T06:08:03.467Z ERROR demo_api.main [req=check-r1] Unhandled error on POST /refunds/123
Traceback (most recent call last):
  ...
TypeError: 'NoneType' object is not subscriptable
2026-10-10T06:08:03.471Z WARNING demo_api.main [req=check-r1] POST /refunds/123 -> 500 in 10ms
```
Timestamp regex for the Linux Logs MCP config: `^(?P<ts>\d{4}-\d{2}-\d{2}T[\d:.]+Z)`

**`logs/postgres/postgresql-YYYY-MM-DD.log`** (database). `log_line_prefix = '%m [%p] %u@%d app=%a '`. Multi-line statements continue on lines starting with a tab:
```
2026-10-10 06:06:51.687 UTC [193] demo_app@target_db app=demo-api:b6db-s3x1 ERROR:  canceling statement due to statement timeout
2026-10-10 06:06:51.687 UTC [193] demo_app@target_db app=demo-api:b6db-s3x1 STATEMENT:  SELECT id, status, total_cents, created_at FROM orders WHERE customer_email = $1 ORDER BY created_at DESC LIMIT $2
```
Other settings: `log_min_duration_statement = 250`, `log_lock_waits = on`, `log_timezone = UTC`, `pg_stat_statements` preloaded.

**Correlation.** The API sets `application_name = demo-api:<request id>` on each request. The same id appears as `[req=<id>]` in the app log and `app=demo-api:<id>` in the Postgres log. `load.py` sends ids of the form `<run tag>-s<worker>x<n>` (search), `-r<n>` (refund) and `-n<n>` (normal traffic).

## Run manifest

`runs/<run_id>/manifest.json` records each phase's UTC window, the HTTP status counts and up to 20 failed request ids. The `faults.<id>.window` values are the start and end times to open an investigation with.

## What is NOT here yet
- The read-only `incident_investigator` role is created in step E3 (`scripts/sql/create_investigator_role.sql`). Its compose init script gets added then.
