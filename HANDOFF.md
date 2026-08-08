# Handoff — TASK-16

## Status
COMPLETE

## Baseline

- Accepted base: `task-15-approval-authority` at `d9ba64e9147bd980be69cf07c7ec2d5fc8037969`, migration `task_15_approval_authority_v1`, parity `0/0`.
- Branch: `task-16-commercial-scenarios`.
- Migration: `task_16_commercial_scenarios_v1`, additive and idempotent.
- No financial or company approval policy, threshold, FX rate, margin floor, or named approver is seeded.

## Implementation

- `core/commercial_scenarios.py` — bounded family/version/source/assumption/cash-event/review/comparison/baseline models and pure Decimal calculation.
- `core/scenario_repository.py` — additive SQLite persistence, immutable version/result triggers, monotonic versions, audit writes, reviews, and baseline lineage.
- `core/scenario_service.py` — calculation/review/baseline workflow boundary.
- `app.py` — scenario repository/service initialization and register/API/review/baseline routes.
- `templates/commercial_scenarios.html` — safe server-rendered register explicitly distinguishing review, approval, baseline, and G6.
- `scripts/validate_task_16.py` — synthetic migration, exact arithmetic, fingerprint, independent review, baseline, and direct-SQL oracle.
- `scripts/asgi_acceptance_task16.py` — dependency-free ASGI register/API acceptance.
- `tests/unit/test_commercial_scenarios.py` — exact Decimal, zero-denominator, fingerprint, and currency validation tests.
- `core/approval_authority.py` — additive TASK-15 scenario/price-margin/financial-exposure decision and scenario-version subject vocabularies.

## Deterministic controls

All authoritative amounts are `Decimal`; values are quantized with explicit scale and `ROUND_HALF_UP`. Margin/markup are integer basis points with explicit zero-denominator behavior. Cash events are date/event-ID ordered and produce cumulative balances and peak working capital. FX is an explicit immutable assumption only; there is no lookup, default rate, network, or float path. Scenario fingerprints are canonical SHA-256 values. NEGOTIATED/AWARD labels remain unsupported comparison labels and cannot prove G6, negotiation, submission, or award.

Calculation review is independent data-quality acceptance. Baseline selection is a separate append-only action and requires an accepted review; approval routes are not auto-created or auto-selected by calculation. G4/G5 remain honest and G6 remains incomplete.

## Acceptance evidence

- Focused TASK-16 tests: `3 passed`.
- Full suite: `278 passed, 26 warnings`.
- `uv run python scripts/validate_task_16.py`: `TASK-16 validation: PASS`.
- `uv run python scripts/asgi_acceptance_task16.py`: `TASK-16 ASGI acceptance: PASS`.
- Ruff format/check: PASS; canonical mypy: PASS.
- Isolated import, migration, idempotence, and Uvicorn startup/shutdown: PASS.
- Direct-SQL immutable scenario deletion: rejected; self-review: rejected; baseline without review: rejected.
- No TASK-06 override or TASK-07 work item is created by scenario reads/calculation.

## Protected files

```text
3c14cb821ed26d209a777d020fb340df87694f2e4da124719814102e27a1aaaa  docs/tasks/TASK-06-readiness-engine.md
4e683123d19bce4d85081408d5bfee5b0ebeb7d8d6c9d98ecc4dd52d1d467377  uv.lock
47362324978efd2ab0f479bd937ff70ca9a1c37a91224cd164c1b4f385d2622d  .claude/settings.local.json
```

All remain untracked/unstaged or the known local modification and were excluded from the commit.

## Scope, scans, and residual risk

No production data, managed bytes, real prices/rates/policies/approvers, secrets, external assets, telemetry, network services, dependencies, or deferred negotiation/proposal/submission/award/handover functionality was accessed. The trusted localhost deployment boundary and inherited multipart spooling risk remain unchanged.

## Conclusion

TASK-16 is fully accepted and safe as TASK-17’s base after final commit and remote parity verification.
