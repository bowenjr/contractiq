# Handoff — TASK-17

## Status
COMPLETE

## Baseline and migration

- Accepted base: `task-16-commercial-scenarios` at `dd9682fd538c18c63f75b8818f838e69db94194d`, migration `task_16_commercial_scenarios_v1`, parity `0/0`.
- Branch: `task-17-negotiation-concessions`.
- Migration: `task_17_negotiation_concessions_v1`, additive and idempotent.
- No company negotiation policy, threshold, named negotiator, approver, position, minimum, or walk-away authority is seeded.

## Implementation

- `core/negotiation.py` — applicability, priorities, position ladders, immutable plan versions, mandates, conditional trades, movement events, concessions, value-received states, Decimal limits, and authority validation.
- `core/negotiation_repository.py` — additive SQLite migration, audit writes, monotonic plan versions, same-bid identities, immutable movement/concession/version triggers, hard-delete protection, and metrics.
- `core/negotiation_service.py` — explicit-authority workflow boundary; no default mandate, auto-commit, or self-approval path.
- `app.py` — Negotiations register/API and safe plan/version/mandate/trade/movement/concession routes.
- `templates/negotiations.html` — safe server-rendered register; empty state never implies no negotiation required.
- `scripts/validate_task_17.py` — synthetic deterministic applicability/plan/mandate/trade/authority-at-event/direct-SQL oracle.
- `scripts/asgi_acceptance_task17.py` — dependency-free in-process ASGI acceptance.
- `tests/unit/test_negotiation.py` — priority ladder and explicit mandate regression coverage.

Conditional GIVE/GET trades cannot commit before evidenced value. Company commitments require explicit authority-at-event evidence. Concessions require an authorized actor, issue, action, window, and currency-safe limit. Customer movements remain observations and do not create company authority. TASK-16 remains the sole arithmetic engine; no proposal/submission/award/handover behavior was added.

## Acceptance evidence

- Focused TASK-17 tests: `2 passed`.
- Full suite: `280 passed, 26 warnings`.
- `uv run python scripts/validate_task_17.py`: `TASK-17 validation: PASS`.
- `uv run python scripts/asgi_acceptance_task17.py`: `TASK-17 ASGI acceptance: PASS`.
- Ruff format/check: PASS; canonical mypy: PASS.
- Isolated import, migration, idempotence, and Uvicorn startup/shutdown: PASS.
- Direct-SQL movement/version/concession deletion is rejected; unevidenced conditional commitment is rejected; missing authority is rejected.
- No TASK-06 override or TASK-07 work item is created.

## Protected files

```text
3c14cb821ed26d209a777d020fb340df87694f2e4da124719814102e27a1aaaa  docs/tasks/TASK-06-readiness-engine.md
4e683123d19bce4d85081408d5bfee5b0ebeb7d8d6c9d98ecc4dd52d1d467377  uv.lock
47362324978efd2ab0f479bd937ff70ca9a1c37a91224cd164c1b4f385d2622d  .claude/settings.local.json
```

All remain untracked/unstaged or the known local modification and were excluded from the commit.

## G4/G5/G6 and scope

Negotiation authority and evidence remain separate from approval, commercial calculation, and baseline selection. G4/G5/G6 fail closed unless exact current upstream evidence is present; proposal/submission readiness remains deferred to TASK-18. No real company/customer/supplier information, managed correspondence, recordings, transcripts, secrets, external services, dependencies, or deferred functionality was accessed.

## Conclusion

TASK-17 is fully accepted and safe as TASK-18’s base after final commit and remote parity verification.
