# MCP Layer + Remaining Work: Merged Plan

This merges the MCP plan for Blocks D, E and G into the code as it is now (commit `e17ffd8`). It keeps the D/E/G step IDs. Section 1 lists where the original plan had to change to fit the project. Section 7 covers the work after MCP.

---

## 0. Where the project stands

| Area | State | Where |
|---|---|---|
| Config, logging, Alembic 0001–0005 | Done | `app/core/*`, `alembic/versions/*` |
| Data sources, capabilities, health tables + CRUD API | Done | `app/models/data_source.py`, `app/services/data_source_service.py` |
| `CONNECTION_PROBES` hook (empty, so `/test` returns 501) | Hook only | `data_source_service.py:28` |
| Approvals, `require_approval()`, gated-action stubs (501) | Done | `app/api/deps.py:96`, `endpoints/gated_actions.py` |
| Evidence storage, deduplicated by `content_hash` | Done | `app/services/evidence_service.py` |
| `agent_tool_calls` audit table | Table only, no writer | `app/models/agent_run.py` |
| LangGraph graph: planning → collect → correlate → root_cause | Skeleton | `app/agents/nodes/investigation.py` |
| Celery worker (solo pool on Windows) | Done, no beat schedule | `app/worker/celery_app.py` |
| Secret resolver | **Missing** (`SECRET_BACKEND` setting exists, no code) | n/a |
| MCP servers, registry, demo target | **Missing** | n/a |

---

## 1. Changes from the original MCP plan to fit this codebase

### 1.1 The platform is synchronous
The app uses sync SQLAlchemy over psycopg2 because AppLocker blocks greenlet (`app/core/database.py`). Nodes are sync functions and the worker is sync Celery. The MCP SDK `ClientSession` is async.

**Decision:** `app/mcp_client/bridge.py` runs one asyncio event loop in a daemon thread per worker process. The registry's async code runs on that loop. Nodes call `registry.call(...)`, a sync facade that uses `asyncio.run_coroutine_threadsafe(...).result(timeout)`. Do **not** call `asyncio.run()` per tool call, because it would tear down HTTP sessions on every call.

- The MCP servers are separate processes. They can be fully async and use `asyncpg` directly, which needs no greenlet. Do not use SQLAlchemy async inside the servers.

### 1.2 Data sources belong to an org and live in the DB, so YAML can't hold `data_source_id`
The original plan put `data_source_id: "<uuid>"` in `config/mcp_servers.yaml`. In this project, `data_sources` rows are created per org through the API. A static file can't name them.

**Decision: split policy from binding.**
- `config/mcp_tool_policy.yaml` lives in git and goes through code review. It is keyed by **`provider_type`**. It is the authoritative tool allow-list, holding each tool's `access` and `evidence_type`. This is the "platform's own config" the registry trusts (original §2.3 and §4.2).
- The **binding** is per data source row, in `configuration_json`: `{"mcp_url": "http://127.0.0.1:9101/mcp", ...}`. The MCP server's bearer token is an internal platform secret. It is resolved by name (`mcp_token_ref`) and never stored inline. `_check_no_credentials` already rejects inline tokens.
- `provider_type` values become a closed set validated against the policy file: `local_code`, `postgresql`, `linux_server`, `kubernetes`, and later `git`.

```yaml
# config/mcp_tool_policy.yaml
providers:
  local_code:
    capabilities: [source_code]
    tools:
      list_files:            {access: read, evidence_type: source_code}
      read_file:             {access: read, evidence_type: source_code}
      search_code:           {access: read, evidence_type: source_code}
      get_method:            {access: read, evidence_type: source_code}
      find_references:       {access: read, evidence_type: source_code}
      describe_capabilities: {access: read}
  postgresql:
    capabilities: [database_state, database_schema, database_logs]
    tools: { ... }
```

### 1.3 Reuse existing hooks instead of building parallel ones
| Original plan item | Fits into |
|---|---|
| D3 health probe | Register one `mcp_describe_probe` in `CONNECTION_PROBES` for every MCP provider type. `POST /data-sources/{id}/test` and the beat task then share one probe. |
| Capability sync | Write `DataSourceCapability` rows. `GET /data-sources/{id}/capabilities` already serves them. |
| Write-tool approval check | Move the body of `require_approval()` into `ApprovalService.latest_approved(db, incident_id, stage) -> Approval \| None`. The FastAPI dependency **and** `registry.call` both use it, so the "newest row wins" rule exists in one place. |
| Audit row | `agent_tool_calls` already fits. A refused call is `success=False, error="refused: <reason>", completed_at=started_at`. No migration needed. |
| Evidence write | `EvidenceService.record_many(...)` deduplicates by hash, so re-runs stay idempotent. |
| Gaps | `InvestigationState["gaps"]` (plain overwrite: append to a copy, as `planning_node` already does). |

### 1.4 Secrets move earlier (E6 before E3)
No `SecretResolver` exists yet. The DB and K8s servers need one, so E6 moves ahead of E3.
- Put it in `mcp_common/secrets.py`. The **platform never needs target credentials**: only the MCP servers do. Backends: `env` (dev), `vault`. Add `azure_keyvault` later.
- Remove `INVESTIGATION_DB_URL`, `KUBERNETES_TOKEN` and `KUBERNETES_API_URL` from `app/core/config.py` and from the platform `.env`. They become per-server settings (`mcp_servers/<name>/settings.py`). `ALLOWED_REPOSITORIES` moves to the Local Code server.
- CI check: `grep` fails if `app/` references `INVESTIGATION_DB_URL`, `KUBERNETES_TOKEN`, or reads a `credential_reference` value.

### 1.5 Health checks must not queue behind investigations
The worker runs `prefetch=1` with the solo pool on Windows and has a 10-minute soft limit. A beat task on the same queue could wait 10 minutes behind a run.
**Decision:** add a `maintenance` queue and a separate `celery beat` process. In dev, run a second solo worker: `-Q maintenance`.

### 1.6 Evidence hash and non-temporal evidence
`compute_content_hash` includes `timestamp`. For source code, using `meta.as_of` would create a new row on every re-run.
**Decision:**
- `source_code` evidence gets `timestamp = incident.start_time`, and the content is prefixed with `repo_head`. Same code at the same commit gives the same hash.
- `database_state` and `pod_state` snapshots use `as_of`. These really are new observations, so duplicates across runs are correct.
- Log lines use the timestamp parsed from the line.

### 1.7 Tests run on SQLite and don't need Docker
`tests/conftest.py` uses SQLite. Keep that for everything that can.
- Add a `postgres` marker, skipped unless `TEST_TARGET_PG_DSN` is set, for E3/E4 role tests against the demo target.
- Add a `k8s` marker, skipped unless `TEST_KUBECONFIG` is set, for the G2 `can-i` check.
- Register both in `pytest.ini`.

### 1.8 Kept unchanged from the original plan
These carry over as written: the response envelope and error codes, `@safe_tool`, limits, the path guard, per-server tool tables, the role SQL, the K8s RBAC manifest, default deny, scope enum, our code calling tools in the MVP (not the LLM), and `describe_capabilities` as a tool.

---

## 2. Target layout

```
mcp_common/                    # no imports from app/ (servers must not pull in the platform)
  envelope.py  limits.py  paths.py  auth.py  secrets.py  timewindow.py
mcp_servers/
  local_code/   server.py  tools.py  settings.py  parsers/{python.py, csharp.py, regex.py}
  database/     server.py  state.py  schema.py  logs.py  settings.py
  linux_logs/   server.py  reader.py  settings.py
  kubernetes/   server.py  tools.py  settings.py
app/mcp_client/
  policy.py     # load + validate config/mcp_tool_policy.yaml (pydantic)
  bridge.py     # background event loop thread
  registry.py   # connect, list_tools, default-deny, call(), capability sync
  scope.py      # ToolScope + tools_for(scope, incident_id)
  probes.py     # describe_capabilities probe -> CONNECTION_PROBES
  evidence.py   # envelope -> EvidenceService.record_many rows
app/worker/tasks/health.py      # D3 beat task
config/mcp_tool_policy.yaml
scripts/sql/create_investigator_role.sql
deploy/k8s/investigator-rbac.yaml
fixtures/demo_target/           # docker-compose, app, load script, ground_truth/*.json
requirements-mcp.txt            # kubernetes, tree-sitter(+grammars), pyyaml, hvac
tests/mcp/{unit,contract}/      # server tests; registry tests under tests/integration/
```

Run each server with `python -m mcp_servers.<name>`. Each takes `--port` and reads `DATA_SOURCE_ID`, `CREDENTIAL_REFERENCE` and `MCP_AUTH_TOKEN` from the environment.

---

## 3. Build order (MCP blocks)

Each step should be one PR that leaves the full test suite passing.

| # | Step | Deliverable | Done when |
|---|---|---|---|
| 1 ✅ | **D0a** `mcp_common` | envelope, `@safe_tool`, limits, path guard, auth, time window | Unit tests: envelope shape, forced exception becomes `internal` with no traceback or secret, oversize result is truncated with `truncated: true`, slow function returns `timeout` + `retryable`, every path-escape case (§3.1 of the original plan) |
| 2 ✅ | **D0b** Demo target | `fixtures/demo_target/` compose: Postgres with `pg_stat_statements` and log settings, tiny API, 2 faults, load script, `ground_truth.json` | `docker compose up` followed by the load script produces both faults at recorded timestamps |
| 3 | **E1+E2** Local Code MCP | 6 tools, `meta.repo_head` and `meta.dirty`, ripgrep with Python fallback, regex `get_method` first | Contract tests over stdio. Escape tests include Windows junction, different drive and case variants |
| 4 | **D1** Policy + registry | `policy.py`, `bridge.py`, `registry.py`, `provider_type` validation in `schemas/data_source.py` | Against a stub server: an unlisted tool is ignored with a warning, a listed but missing tool marks the server `degraded`, capabilities sync to DB |
| 5 | **D2** Scope + audit | `ApprovalService.latest_approved` refactor, `scope.py`, `registry.call` writes `agent_tool_calls` | A fake `write` tool called from an investigation node is refused **before** any network call. A row is written for allowed **and** refused calls. Existing `test_require_approval.py` still passes |
| 6 | **D3** Health | `probes.py` registered in `CONNECTION_PROBES`, `tasks/health.py`, beat schedule, `maintenance` queue | `/test` returns 200 for MCP providers. Killing the stub flips health to `unreachable` within one cycle |
| 7 | **D4** Planning | Real `discover_data_sources()` (org + env + enabled + latest health ≠ unreachable). Dropped sources become `gaps`. Plan is built by **capability** | Integration test: unreachable source is excluded and its gap is recorded |
| 8 | **F1** Collect wiring | `collect_evidence_node` → `registry.call` per planned capability → `mcp_client/evidence.py` → `record_many`; failed calls become gaps | A run against Local Code stores `source_code` evidence. A re-run adds 0 rows |
| 9 | **E6** Secrets | `mcp_common/secrets.py` (env + vault), config cleanup (§1.4), CI grep | Grep check passes. Servers resolve credentials per call |
| 10 | **E5** Linux logs MCP | Named sources from config, multi-line grouping, rotation + `.gz` | Windowed read of the demo fault returns the stack trace as **one** entry |
| 11 | **E3** Database MCP | State, schema and log tools. Role SQL + compose init script | All three tool families work against the demo target. `get_top_queries` returns `extension_unavailable` when the extension is off |
| 12 | **E4** DB write tests (`postgres` marker) | Every write path (§3.2 of the original plan) raises a privilege or read-only error. Static test: no tool builds SQL with an f-string from caller input | Passes in CI with the demo target |
| 13 | **G1** K8s MCP | 5 tools on the official `kubernetes` client. `until` filtered client-side. `previous=True` supported | Contract tests with a mocked API client |
| 14 | **G2** RBAC | `deploy/k8s/investigator-rbac.yaml` (namespaced Role) | `kubectl auth can-i delete pods --as=...` returns `no` (`k8s` marker) |
| 15 | **G3** Degraded test | Stop the K8s server mid-run | Run completes, gap recorded, health row is `unreachable` |

Why this order differs from the original: I put **F1 (collect wiring) right after D4**, so the first full investigation works end to end with only Local Code. Every later server then plugs into a working pipeline. E6 moves ahead of E3 (§1.4).

---

## 4. Evidence mapping (registry → `incident_evidence`)

| Capability / tool family | `EvidenceType` | `timestamp` | Notes |
|---|---|---|---|
| `application_logs` (linux, k8s pod logs) | `APPLICATION_LOG`, or `STACK_TRACE` if the grouped entry has more than 1 line and matches a traceback pattern | parsed line ts | `service` from the data source config |
| `database_logs` | `DATABASE_LOG`; slow-query entries become `QUERY` | parsed line ts | |
| `database_state`, `database_schema` | `QUERY` / `METRIC` | `meta.as_of` | snapshot |
| `pod_events` | `KUBERNETES_EVENT` | event `lastTimestamp` | capture early: K8s events expire |
| `pod_state` | `SERVER_EVENT` | `meta.as_of` | include `lastState.reason` (e.g. `OOMKilled`) in content |
| `deployment_info` | `DEPLOYMENT` | rollout time | |
| `source_code` | `SOURCE_CODE` | `incident.start_time` | content prefixed with `repo_head` (§1.6) |

`correlation_id` is pulled from log lines when the format contains one. F3 relies on it.

---

## 5. Open decisions (need an answer before the step that needs them)

1. **First language for `get_method`** (step 3): C# or Python? Start with a regex parser that reports `meta.parser = "regex"`. Add tree-sitter once the grammar install is checked on Windows.
2. **Postgres log access** (step 11): for the demo, a read-only volume mount. For real targets: a mount, a shipped copy, or a cloud log API. Decide per target.
3. ~~**Transport string**~~ **Resolved in step 1:** the installed SDK is `mcp` 2.2.0. `FastMCP` was renamed to `mcp.server.mcpserver.MCPServer`; `run()` takes `"streamable-http"`; tests connect in-process with `mcp.Client(server)`. Pinned `mcp>=2.2,<3`.
4. **K8s: build or reuse** (step 13): the plan defaults to building. Reuse `containers/kubernetes-mcp-server` only after confirming its read-only flag.

---

## 6. Cross-cutting tests (every server)
- Contract: every tool's `data` validates against its Pydantic model.
- Envelope: forced exception becomes `internal`, with no traceback and no secret.
- Limit: over `MAX_BYTES` gives `truncated: true` plus a hint on how to narrow the query.
- Timeout: a slow backend gives `timeout` with `retryable: true`.
- Default deny: the registry ignores a tool the server adds that is not in the policy file.

---

## 7. Remaining plan after MCP (merged with the phases in code comments)

The code comments name Phase 2 (discovery), Phase 3/4 (MCP collection), Phase 5 (LLM RCA) and Phase 7+ (implementation). The original plan references F7, H2, J2 and S2. The blocks below line up with both. **Renumber them if your master plan defines F/H/I/J differently.**

### Block F: Analysis (Phase 5). Goal: a real root cause for the demo faults
| Step | Deliverable | Done when |
|---|---|---|
| F1 | Collect wiring (scheduled in §3, step 8) | n/a |
| F2 | Log normalization: fill `normalized_content` (strip ids/timestamps, collapse numbers) for clustering | Same error from 50 requests collapses into 1 cluster |
| F3 | `correlate_node`: by `correlation_id`, then time window ±N s, then `service` | Demo fault 1 links the slow query to the API timeout |
| F4 | LLM client (`app/agents/llm.py`) for Azure OpenAI / OpenAI. Writes token + cost to `agent_tool_calls` and increments `Incident.total_llm_cost_usd`, so the existing `check_cost_budget` has real numbers | Budget test: a run stops at the limit |
| F5 | `root_cause_node`: structured `RootCauseAnalysis` (pydantic), validation-retry ×2. Every claim must cite `evidence_ids`. With weak evidence the outcome is `NEEDS_MORE_INVESTIGATION` | A citation to a non-existent evidence id is rejected |
| F6 (**S2**) | Prompt-injection hardening: one evidence-block helper that delimits and escapes tool output. Evidence is treated as data. Optional later: model returns a `ToolCallPlan` (enum of scoped read tools), which our code executes | A log line saying "ignore previous instructions" doesn't change the output schema or the tools called |
| F7 | Golden-incident eval: run both demo faults, compare with `ground_truth.json` (file, method, cause) | Both faults identified. Run in CI nightly |

### Block H: Implementation plan + approval gate (Phase 7)
| Step | Deliverable |
|---|---|
| H1 | `implementation_planner` node: proposed change, affected files, tests, **`risk_level`** (sets `Incident.risk_level`, which `requires_independent_approver` already uses). State moves to `IMPLEMENTATION_PLAN_READY` |
| H2 | `plan_approval_gate` via LangGraph `interrupt()` → `WAITING_PLAN_APPROVAL`. `ApprovalService.decide(approve=True)` enqueues a resume task on the same `thread_id`. The checkpointer already persists state |
| H3 | Plan Validator: uses `find_references` (treated as "possible callers", `match: textual`) and Git blame to flag missed call sites |

### Block I: Controlled implementation (`IMPLEMENTATION` scope)
| Step | Deliverable |
|---|---|
| I1 | The first `write` tools: a separate **Workspace MCP** that writes only into a throwaway git worktree, never the original repo. Policy entries `access: write` |
| I2 | `ToolScope.IMPLEMENTATION` enabled only while the newest PLAN approval is APPROVED, re-derived on every call (built in D2) |
| I3 | Test runner in the worktree → `TESTING` → `IMPLEMENTATION_READY` → `WAITING_CODE_APPROVAL` |
| I4 | Replace the `implement` gated-action 501 stub with the real handler |

### Block J: Git, PR, deploy (`DEPLOYMENT` scope)
| Step | Deliverable |
|---|---|
| J1 | Local Git MCP, read-only: `log`, `diff`, `blame`, `show_file_at(commit)`. Fixes the deployed-version mismatch from E1. Feeds regression analysis back into F5 (can come earlier if useful) |
| J2 | Reuse the GitHub MCP (create branch/PR) and Jenkins MCP (trigger QA job). `create-pr` and `deploy-qa` stubs become real behind a CODE approval |
| J3 | `WAITING_PRODUCTION_APPROVAL` → `deploy-production` (PRODUCTION approval, independent approver for HIGH risk) → `VERIFYING`: re-run the relevant read tools to confirm the error stopped → `RESOLVED` |
| J4 | Scheduled integrity check: every `agent_tool_calls` row with `is_write_operation = true` has an APPROVED row for its stage at or before `started_at`. The partial index `ix_agent_tool_calls_write_ops` exists for this |

### Block K: Production readiness (in parallel with H–J)
- OpenTelemetry traces across API → Celery → node → MCP call (`OTEL_EXPORTER_ENDPOINT` exists, nothing emits yet).
- Full docker-compose for the platform: API, worker, beat, maintenance worker, Redis, Postgres, the 4 MCP servers.
- TLS between platform and MCP servers once they run on separate hosts.
- Frontend (CORS already allows `localhost:5173`): incident timeline, evidence view, approval buttons.

---

## 8. Milestones
| Milestone | Steps | What you can demo |
|---|---|---|
| **M1: first real run** | D0a, D0b, E1/E2, D1, D2, D3, D4, F1 | Trigger an investigation, then see source-code evidence, audit rows and health status |
| **M2: all read sources** | E6, E5, E3, E4, G1–G3 | Demo faults leave logs, DB and K8s evidence. A degraded source becomes a recorded gap |
| **M3: root cause** | F2–F7 | Both demo faults correctly explained, with citations. Golden eval green |
| **M4: human-gated fix** | H, I | Approved plan leads to a code change in a sandbox, then tests, then code approval |
| **M5: closed loop** | J, K | PR, QA, production approval, deploy, verify, resolved |
