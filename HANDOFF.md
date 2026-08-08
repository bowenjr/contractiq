# Handoff — TASK-15

## Status
COMPLETE

## Baseline and scope

- Base: `task-14-contract-risk-control` at `1b390700dae2894960d917a01bf497f2012bb814`, migration `task_14_contract_risk_control_v1`.
- Branch: `task-15-approval-authority` (created directly from the exact base).
- Migration: `task_15_approval_authority_v1`; additive and idempotent.
- No company policy is seeded or active by default. Synthetic policies are used only by tests and acceptance scripts.
- Existing requirement, scope/interface, supplier, deliverable, commercial, and contract-risk authorities remain read-only upstream evidence; no legacy analysis concepts were reinterpreted.

## Files created

- `core/approval_authority.py` — immutable Pydantic policy, assignment, case, subject, package, route, and event models with canonical package fingerprints.
- `core/approval_repository.py` — additive SQLite schema, migration marker, audit writes, monotonic package versions, effective assignments, route/event checks, immutability and hard-delete triggers.
- `core/approval_rules.py` — deterministic structured rule matching, complete approval gap vocabulary, stable ordering, and zero-safe projections.
- `core/approval_service.py` — policy/case/package/route/event workflow boundary.
- `templates/decisions.html` — server-rendered Decisions & Approvals register with explicit no-policy and G4-safe messaging.
- `scripts/validate_task_15.py` — synthetic deterministic acceptance and direct-SQL immutability oracle.
- `scripts/asgi_acceptance_task15.py` — dependency-free in-process ASGI page/API acceptance.
- `tests/unit/test_approval_authority.py` — deterministic matching and package completeness regression tests.

## Files modified

- `app.py` — approval repository/service initialization; Decisions register, JSON projection, policy publication, case/subject/package, route, and approval-event endpoints.
- `core/my_day.py` — read-only approval attention projection and count; no TASK-07 work items are created.
- `core/work_item_service.py` — reads active/draft decision cases into My Day attention only.

## Acceptance evidence

- Focused TASK-15 tests: `2 passed`.
- Unrestricted suite: `275 passed, 26 warnings`, normal completion.
- `uv run python scripts/validate_task_15.py`: `TASK-15 validation: PASS`.
- `uv run python scripts/asgi_acceptance_task15.py`: `TASK-15 ASGI acceptance: PASS`.
- `uv run ruff check .`: pass.
- `uv run mypy`: pass (`Success: no issues found in 22 source files`).
- Application import and additive migration smoke: pass; repository migration rerun is idempotent.
- Deterministic routing, effective-role assignment, separation-of-duties/self-approval rejection, package fingerprinting, and direct-SQL package deletion protection are proven by focused and validation tests.
- G4 remains explicitly not approved or complete; no price, margin, scenario, or permissive fallback approval exists.
- In-process ASGI covered dashboard, My Day, all prior registers, `/decisions`, and `/api/decisions`; no external network or socket was required.

## Protected workspace evidence

The protected files were not edited, staged, or committed:

```text
3c14cb821ed26d209a777d020fb340df87694f2e4da124719814102e27a1aaaa  docs/tasks/TASK-06-readiness-engine.md
4e683123d19bce4d85081408d5bfee5b0ebeb7d8d6c9d98ecc4dd52d1d467377  uv.lock
47362324978efd2ab0f479bd937ff70ca9a1c37a91224cd164c1b4f385d2622d  .claude/settings.local.json
```

## Decisions and deviations

- Policy rules and stages are persisted as canonical JSON; route requirements freeze matched rule IDs and role/stage evidence.
- Approval attention is intentionally read-only and does not create or mutate TASK-07 work items.
- No real company policies, thresholds, named approvers, production data, managed documents, secrets, or external systems were accessed. No dependencies were added.

## Residual risks

- The existing trusted-single-user localhost deployment boundary and inherited multipart spooling risk remain unchanged. Enterprise identity, notifications, delegation configuration, commercial/scenario approval, and later-task functionality remain deferred.

## Conclusion

TASK-15 is fully implemented and accepted on this branch and is safe as TASK-16's base after commit and remote parity verification.
