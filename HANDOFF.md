# Handoff — TASK-18

## Status
COMPLETE

## Baseline

- Accepted base: `task-17-negotiation-concessions` at `8c08bcbaf35c3e49b3e92fd1d137803592d8436a`, migration `task_17_negotiation_concessions_v1`, parity `0/0`.
- Branch: `task-18-proposal-production`.
- Migration: `task_18_proposal_production_v1`, additive and idempotent.
- No company presentation profile, branding, standard wording, customer-offer policy, approver, or submission identity is seeded.

## Implementation

- `core/proposals.py` — applicability, effective profiles, families, immutable versions, customer-visible section firewall, canonical fingerprints, and local HTML/DOCX/PDF/JSON rendering.
- `core/proposal_repository.py` — additive SQLite schema, exact metadata/audit writes, monotonic versions, immutable version/artifact triggers, hard-delete protection, reviews, and explicit baseline prerequisites.
- `core/proposal_service.py` — profile/family/version/review/render workflow boundary.
- `app.py` — Proposals register/API, profile/family/version/review/render routes, and submission-assurance-deferred projection.
- `templates/proposals.html` — safe server-rendered proposal and offer-baseline register.
- `scripts/validate_task_18.py` — synthetic migration, rendering, artifact-hash/size, review, and direct-SQL oracle.
- `scripts/asgi_acceptance_task18.py` — dependency-free in-process ASGI acceptance.
- `tests/unit/test_proposals.py` — internal-data firewall and structured-source tests.

Rendered artifacts are local-only and metadata records store relative paths, media types, byte sizes, hashes, and verification state. Review is independent data-quality acceptance; approval and explicit baseline selection remain separate. No transmission, receipt, sender, recipient, portal, email, or submission evidence exists. Submission assurance is explicitly deferred.

## Acceptance evidence

- Focused TASK-18 tests: `2 passed`.
- Full suite: `282 passed, 26 warnings`.
- `uv run python scripts/validate_task_18.py`: `TASK-18 validation: PASS`.
- `uv run python scripts/asgi_acceptance_task18.py`: `TASK-18 ASGI acceptance: PASS`.
- HTML, DOCX, PDF, and canonical JSON artifacts generated and verified with non-zero sizes and SHA-256 metadata.
- Ruff format/check: PASS; canonical mypy: PASS.
- Isolated import, migration, idempotence, Uvicorn startup/shutdown, and diff-check: PASS.
- Self-review rejected; customer-visible internal margin/approval/mandate text rejected; immutable proposal deletion rejected.
- No TASK-06 override or TASK-07 work item is created.

## Protected files

```text
3c14cb821ed26d209a777d020fb340df87694f2e4da124719814102e27a1aaaa  docs/tasks/TASK-06-readiness-engine.md
4e683123d19bce4d85081408d5bfee5b0ebeb7d8d6c9d98ecc4dd52d1d467377  uv.lock
47362324978efd2ab0f479bd937ff70ca9a1c37a91224cd164c1b4f385d2622d  .claude/settings.local.json
```

All remain untracked/unstaged or the known local modification and were excluded from the commit.

## G4/G5/G6 and scope

Proposal-ready and submission-assurance-deferred states remain distinct. G4/G5/G6 remain fail-closed unless exact current upstream evidence and required approvals exist. No proposal was transmitted and no receipt evidence was created. No real company/customer/supplier information, branding, templates, managed documents, secrets, external services, or later-roadmap functionality was accessed.

## Conclusion

TASK-18 is fully accepted as the Phase 3 testing baseline. Development stopped after TASK-18; TASK-19 was not started.
