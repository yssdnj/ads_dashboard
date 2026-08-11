# Market Monitor Final Remediation Report

Date: 2026-07-21  
Implementer: `/root/final_remediation`  
Scope: final-review blockers I1-I5 and M1/M3. No commit or push was performed.

## Disposition

### I1 — Dashboard anomaly and opportunity intelligence

Resolved in `api/market_monitoring/analytics.py` and `service.py`.

- Added centralized, immutable dashboard signal rules for market traffic, own
  market share, high-relevance visibility, and competitive pressure.
- A risk/opportunity requires both default baselines to agree, at least three
  baseline days, at least two persistent directional days, confidence >= 0.60,
  and traceable evidence IDs.
- DTOs now separate fact, inference, recommendation, evidence, evidence IDs,
  confidence, anomaly score, and opportunity priority score.
- Missing/weak/conflicting evidence fails closed to an `info` fact with no
  inference or recommendation.
- Production service output now populates `trend.anomaly_markers` and ranked
  `opportunities`.
- Regression coverage exercises the real FastAPI dashboard endpoint and passes
  that production payload into the chart/evidence DOM harness; no fabricated
  marker is used in that path.

### I2 — Atomic daily batch persistence

Resolved in `api/market_monitoring/collector.py`.

- The complete normalized payload, JSON inputs, report, section names, row
  shapes, required fields, relevance levels, market/date context, and write mode
  are validated before snapshot writers execute.
- Run start, raw request/response, every explicitly supplied normalized section,
  report, and terminal success state share one transaction.
- Any late writer/report/terminal failure rolls that transaction back. A separate
  transaction then records only the failed run metadata.
- `replace_day` distinguishes an omitted section (leave existing rows unchanged)
  from an explicitly empty list/null market snapshot (delete the prior day).
- Low-level writers accept an existing SQLAlchemy connection and no longer call
  database/schema bootstrap on each write.
- Regressions prove prevalidation happens before the first writer, a late writer
  cannot change previously visible snapshots, raw logs roll back, and failed-run
  logging remains.

### I3 — Ratio display units

Resolved in `frontend/static/market-monitor.js`.

- Current and baseline values still use their metric formatter.
- `change` and `deviation` now always use a dedicated ratio-to-percent formatter.
- DOM behavior covers traffic, rank, and share KPI cards plus the evidence
  drawer.

### I4 — Test database isolation

Resolved with `tests/conftest.py` and `tests/market_test_database.py`.

- Every test session creates a unique physical MySQL database named strictly
  `ads_funnel_market_test_<hex>`.
- A reusable guard rejects the operational name, empty suffixes, punctuation,
  and unrelated names before create/drop.
- The schema, repository, and compatibility facade are routed to the disposable
  engine before initialization; initialization uses `seed_defaults=False`.
- Seed behavior is explicit in seed/API fixtures and only affects the disposable
  database.
- The fixture disposes the engine and drops only the validated exact database at
  session end.

### I5 / M1 — Deliverability and ignored artifacts

Resolved in `.gitignore`, `.superpowers/sdd/.gitignore`, `AGENTS.md`, and keyword
catalog error handling.

- Exact exceptions version only Market Monitor regressions/helpers, the daily
  runbook, the three approved Market Monitor specs/plans, and the authoritative
  June 2026 workbook.
- Other tests and docs remain ignored by the existing broad rules.
- `.codex_tmp/` and `.superpowers/brainstorm/` remain local-only.
- A missing workbook raises an actionable `CatalogValidationError` naming the
  expected path and the CLI `--workbook PATH` provisioning option.
- `AGENTS.md` now accurately documents the narrow Market Monitor test exception.

M2 (`progress.md`) is intentionally left for the root orchestrator as assigned.
M3 is covered by the collector connection/transaction refactor above.

## TDD evidence

Observed RED before implementation:

- production Dashboard returned no risk/opportunity or marker for persistent,
  strongly changing complete-day data;
- traffic/rank ratios rendered as `0.2` / rounded `-0` rather than percentages;
- disposable database name guard raised `NotImplementedError`;
- late batch failure left the earlier ASIN replacement committed;
- explicit empty `replace_day` left stale rows;
- missing workbook leaked a raw `FileNotFoundError`.

Focused GREEN evidence:

- intelligence/format/guard: `9 passed`;
- atomic and explicit-empty regressions: `2 passed`, followed by collector file
  `10 passed` before the final prevalidation regression was added;
- HTTP production DTO to frontend marker/evidence path: `1 passed`;
- consolidated blocker tests: `128 passed`.

## Final verification evidence

Fresh commands run from `D:\python\ads_dashboard`:

```text
python -m pytest <12 Market Monitor regression modules> -q
178 passed, 1 Starlette/httpx deprecation warning in 3.99s

python -m pytest ads_funnel/tests -q
190 passed in 4.85s

python -m compileall -q ads_funnel/api/market_monitoring ads_funnel/scripts
exit 0

node --check ads_funnel/frontend/static/market-monitor.js
exit 0

git diff --check
exit 0 (only existing LF/CRLF conversion notices)

python ads_funnel/scripts/market_monitor_import_keywords.py --dry-run
{"active": 256, "high": 77, "mid": 40, "low": 139}

read-only operational keyword query
256 {'high': 77, 'mid': 40, 'low': 139}
```

Live isolated runtime verification used port `5019` and database
`ads_funnel_market_test_f1a1e2026072`:

```json
{"health":200,"database":"ads_funnel_market_test_f1a1e2026072","dashboard":200,"sections":"context,data_quality,kpis,trend,insights,opportunities","root":200,"strong":422}
```

The isolated runtime process was stopped and the exact guarded database was
dropped; a follow-up information-schema query returned `remaining=0`.
The user's existing `0.0.0.0:5001` listener remained running with PID `79780`.

## Residual note

The focused Market Monitor run reports the existing Starlette warning that
`fastapi.testclient` currently uses deprecated `httpx` compatibility. It is not
caused by these changes and does not affect the 178/178 pass result.
