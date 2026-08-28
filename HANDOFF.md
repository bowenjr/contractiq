# Handoff — OPS-06 targeted review remediation

## Status

COMPLETE — ACCEPTED FOR PUBLICATION

OPS-06 manual browser acceptance passed with deferred UX findings. The targeted independent recheck passed and independently verified N1, N2, N3, N4 and N6 as corrected. Publication is authorized; the commit and push are recorded after they occur.

## Files created

- `core/bid_control_center.py` (546 lines) — typed Bid portfolio/workspace projections, truthful filter contract, and bid-scoped workspace service boundary.
- `docs/tasks/OPS-06-bid-control-center-foundation.md` (174 lines) — authoritative OPS-06 specification and targeted-remediation contract.
- `scripts/asgi_acceptance_ops06.py` (207 lines) — dependency-free browser acceptance, including retained HTML filter failures and register return paths.
- `scripts/validate_ops_06.py` (165 lines) — isolated deterministic service validator.
- `templates/_primary_navigation.html` (7 lines) — five-destination role-aligned primary navigation.
- `templates/administration.html` (5 lines) — configuration/reference landing page.
- `templates/reports_center.html` (6 lines) — supported-output landing page.
- `tests/unit/test_bid_control_center.py` (191 lines) — projection, classification, filtering, ordering and readiness tests.
- `tests/unit/test_ops06_ui.py` (292 lines) — browser shell, HTML filter, register-return, query-scaling, 404 and GET non-mutation tests.

## Files modified

- `HANDOFF.md` — records the frozen remediation implementation and evidence.
- `app.py` — role-aligned shell plus bid-scoped readiness/attention loading, retained HTML filter errors and register return context.
- `core/my_day.py` — includes every non-CLEAR readiness verdict in blocker attention.
- `scripts/asgi_acceptance_ops05.py` — preserves accepted OPS-05B entry through the Bid workspace.
- `templates/bid_detail.html`, `templates/bids.html`, `templates/my_day.html` — OPS-06 workspace, truthful filters and read-only attention presentation.
- `templates/commercial.html`, `templates/contract_risks.html`, `templates/decisions.html`, `templates/deliverables.html`, `templates/documents.html`, `templates/negotiations.html`, `templates/proposals.html`, `templates/requirements.html`, `templates/scope_interfaces.html`, `templates/suppliers.html` — direct bid-scoped return paths from every applicable operational register.
- `templates/document_detail.html`, `templates/knowledge.html`, `templates/my_work.html`, `templates/requirement_detail.html`, `templates/role_framework.html`, `templates/vendor_document_package.html`, `templates/vendor_document_register.html`, `templates/vendor_documents.html` — role-aligned navigation from the original OPS-06 implementation.
- `tests/unit/test_my_day.py` — future non-clear readiness regression.
- `tests/unit/test_document_ui.py`, `tests/unit/test_ops_navigation.py`, `tests/unit/test_requirement_ui.py`, `tests/unit/test_work_item_ui.py` — preserved operational/navigation compatibility assertions.

## Test results

- N1/N2/N3/N4/N6 focused regressions — **PASS**.
- Focused operational/navigation suite — **58 passed, 52 warnings**.
- Full unexcluded `pytest -q` — **349 passed, 0 failed, 64 warnings**.
- Manual browser acceptance — **PASS WITH DEFERRED UX FINDINGS**.
- Targeted independent recheck — **PASS WITH NON-BLOCKING OBSERVATIONS**; N1, N2, N3, N4 and N6 independently verified corrected.
- Published/pre-remediation suite retained — **346 existing tests plus 3 new remediation tests**.
- `ruff format --check` on all 13 changed Python files — **PASS**.
- `ruff check` on 12 changed Python files excluding inherited `app.py` findings — **PASS**.
- Direct `app.py` Ruff — **10 inherited findings only**. Git blame attributes them to pre-baseline commits `316b1c47`, `dc351e94`, `a111c06d`, `bbbd9c3d`, `9b3510ed`, `d5976348`, and `6ff10b1e`; no remediation line is flagged.
- `mypy --strict core/bid_control_center.py core/my_day.py` — **PASS: no issues in 2 source files**.
- `git diff --check` — **PASS (no output)**.
- Warnings are the existing FastAPI `on_event` deprecations.

## Validation command output

```text
OPS-06 validation: PASS
OPS-06 ASGI acceptance: PASS
OPS-05B validation: PASS
OPS-05B ASGI acceptance: PASS

Focused pytest: 58 passed, 52 warnings in 19.34s
Full pytest: 349 passed, 64 warnings in 43.49s
Strict scoped mypy: Success: no issues found in 2 source files
Changed-file Ruff: All checks passed
Changed-file format: 13 files already formatted
git diff --check: PASS
```

## Decisions I made

- N1: bid-scoped registers link directly to the relevant Bid workspace section and render no return link when `bid_id` does not resolve to a Bid.
- N2: `workspace()` evaluates authoritative readiness for one requested Bid and loads only that Bid's approval, supplier and work-item attention. The portfolio continues to use the existing batch My Day projection.
- N3: invalid and contradictory filters re-render the Bid portfolio as HTML 422 while retaining raw entered values. Current/history status contradictions are explicitly rejected.
- N4: the Bid filter offers Any, Clear and Hold only. The published readiness enum remains compatible, but no ruleless ESCALATE choice is advertised.
- N6: My Day blocker selection is based on `verdict != CLEAR`, without changing present readiness rules.

## Deviations from the task spec

None. No migration, persistence redesign, mutation-route change, later milestone, navigation redesign, external call or automatic My Work creation was introduced.

## Accepted deferred observations

- Layout remains visually heavy and chunky, with inconsistent information hierarchy and screen maturity. A later visual/usability convergence milestone is required.
- N5: My Day blocker-detail presentation requires later usability refinement, including whether to show every blocker or only the aggregate/top blocker.
- N7: some readiness evidence, owner, owing-party and date labels require later refinement.
- N8: some workspace summaries expose mechanically generated metric labels.
- Consistency observation: `/requirements?bid_id=<unknown>` returns 422 while several other registers return 200 without Bid context. This is accepted as pre-existing behavior, not an OPS-06 regression.
- The legacy `app.py` remains large and retains 10 inherited Ruff findings; remediation was outside this bounded pass.

## Reporting requirements from the task

- Branch/base: `ops-06-bid-control-center-foundation` at `c35a191bce91d7b698a2bca779570f2e7d0bfa33`.
- Recovery pointer: `backup/ops-06-pre-implementation-20260825` at the same SHA.
- Single-workspace SQL statements before remediation: **29 with 1 Bid, 227 with 12 Bids, 659 with 36 Bids**.
- Single-workspace SQL statements after remediation: **18 with 1 Bid, 18 with 12 Bids, 18 with 36 Bids**; delta **0**.
- Database migration: **none**.
- Production database SHA-256 before and after verification: `65111b90ad39b7db9944027907208524350018b7df3adceeab060c5c100fe1ea` (unchanged).
- Production databases/backups: untouched and unstaged.
- Protected lockfiles remain untracked and byte-unchanged:
  - `uv.lock` — `4e683123d19bce4d85081408d5bfee5b0ebeb7d8d6c9d98ecc4dd52d1d467377`
  - `uv.lock.armoury-generated-20260811` — `cdd25389100dce966949e140515b080684e56b4b04a09c4c0be04ebbc793c92a`
- `.claude/settings.local.json` had a pre-existing local MCP-server diff at this remediation preflight. It was inspected, not modified, and remains excluded from the product change.
- Nothing is staged; no commit, push, merge, rebase or stash operation occurred.

### Isolated manual runtime

```bash
cd /home/bowen/dev/projects/contractiq
test ! -e /tmp/contractiq-ops06-remediation-manual-20260828.db
test ! -e /tmp/contractiq-ops06-remediation-documents-20260828
CONTRACTIQ_DB_PATH=/tmp/contractiq-ops06-remediation-manual-20260828.db \
CONTRACTIQ_DOCUMENT_ROOT=/tmp/contractiq-ops06-remediation-documents-20260828 \
uv run uvicorn app:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/`. Stop with `Ctrl-C` in the server terminal.

### Manual browser acceptance checklist

1. Create a Bid, open all six sections, and confirm the Bid header and readiness rail remain identical.
2. From Requirements & Scope, open Requirements, Controlled Documents and Scope & Interfaces; use each direct “Back to Bid” action.
3. Repeat the return journey from Suppliers, Commercial, Contract Risks, Decisions, Deliverables, Proposals and Negotiations.
4. Open `/bids?view=nonsense&owner=Jason&status=active`; confirm an HTML 422 page explains the error and retains all values.
5. Confirm the readiness filter offers Any, Clear and Hold only.
6. Confirm blockers remain truthful, My Day opens authoritative destinations, and no GET changes records or audit history.
7. Observe the accepted deferred UX and register-consistency findings without treating them as publication blockers.

OPS-06 ACCEPTANCE STATUS:
PASS WITH DEFERRED UX FINDINGS
