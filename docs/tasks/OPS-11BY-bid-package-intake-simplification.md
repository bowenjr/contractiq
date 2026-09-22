# OPS-11BY — Bid Package Intake Simplification

Status: implemented; awaiting Jason's manual browser acceptance
Branch: `ops-11-bid-package-intake-addendum-control`
Predecessors: OPS-11A, OPS-11B, OPS-11BR, OPS-11BX (one uncommitted candidate)

## Why this round exists

OPS-11BX made the Bid workspace read as one sequence of work. It was verified against synthetic
folders of a few files each. This round drove the same application through a real received
customer package — 25 files across four parts — and simplified what that exposed.

The diagnostic report, with the evidence, is held outside Git at:

```text
/home/bowen/reviews/contractiq-ops11by-real-package-workflow-review-20260915.md
```

## What ContractIQ actually does with a received package

Stated here so no later round has to rediscover it, and so the UI never implies more.

ContractIQ is a **controlled physical-custody and evidence system** for received customer
packages. On ingestion it records, per file: the original relative path and filename, the
extension, a media type guessed **from the extension only**, the exact byte size, the SHA-256 of
the received bytes, a verified managed copy, and an immutable safe-default disposition of
`UNKNOWN` / `NOT_ASSESSED` / `SAFE_DEFAULT`.

It does **not** open, parse, render or interpret any document. There is no document-purpose
classification, no searchable description, no drawing recognition, no drawing title, no
requirement extraction and no addendum impact analysis. Those belong to OPS-11C and are listed in
§9 of the report.

What it does do, and does correctly: prove exactly what was received, when, unchanged, and which
customer releases and documents the offer stands on.

## Authorized simplifications implemented

1. **The server records receipt time.** `received_at` is set from the server clock when ingestion
   is confirmed; the field is gone from the form. The same applies to a missing-addendum notice's
   `observed_at`. The evidence contract is unchanged — both remain required and timezone-aware.
2. **Confidence appears only when there is an automated proposal to be confident about**
   (`LOCAL_RULE`, `LOCAL_AI_PROPOSAL`, `IMPORTED_AI_PROPOSAL`, or a figure already recorded).
3. **An exclusion reason appears only when Excluded is chosen.** It ships hidden and is revealed
   in place. With scripting off the server's existing validation still refuses an excluded file
   with no reason, and retains what was entered.
4. **The duplicate selector appears only when a duplicate really exists** in that release.
5. **Hashes and provenance sit under "Advanced: evidence and provenance"**, with a plain statement
   that ContractIQ inventoried the file and did not read it.
6. **The controlled-document relationship is derived, not asked.** Routine intake records
   `EXACT_BYTES`; the override lives under advanced evidence for a file already under control.
7. **Acknowledgement is out of the routine path.** The route, model, table and gating are intact;
   the control renders only when an acknowledgement event already exists, so an event created by
   any other means can still be resolved.
8. **Addendum directives appear only after an addendum-class release** (addendum, clarification,
   revised package, other). An initial package cannot carry them and no longer offers them.
9. **Directives speak the customer's effect**: New document · Replaces earlier document · Changes
   part of earlier document · Withdraws earlier document · No Bid impact · Needs review. Stored as
   stable codes; historical free text still renders.
10. **"Publish or update Bid Basis" is now "Confirm current Bid basis"**, with the meaning stated
    in place: the exact set of customer releases and documents the offer is being prepared against.
11. **One-step document control.** A received file goes under document control from the file
    itself, using the managed original ContractIQ already holds — no second upload, no leaving the
    Bid, title proposed from the filename and marked as proposed. This also makes the recorded
    exact-bytes relationship **provable** rather than asserted by whoever filled the form.
12. **Plain wording and readable sizes.** "Unknown · Not Assessed" is now "Not yet reviewed";
    byte counts render as sizes.
13. **Outstanding work is stated as a count, not as record identifiers.** The release page
    previously printed one raw `RF-<uuid>` per unreviewed file — 25 of them for this package. The
    raw list remains under advanced evidence.

## What was deliberately not done

No migration, dependency, AI integration, OCR engine or new authoritative business domain was
added. No immutable evidence, hash, audit record, provenance, concurrency control, addendum
history or Bid Basis control was removed — all 36 immutability triggers remain.

Four product decisions are Jason's and are recorded in §8 of the report rather than implemented:

- **P1** rename "Analysis eligibility" to "Include in the Bid basis review?" — recommended;
  the label is changed in the form, the stored enum is not.
- **P2** propose content form from file type — held for OPS-11C.
- **P3** a bulk file-review action — genuinely contested; the largest remaining cost.
- **P4** group received files by the customer's own folders — recommended as a follow-up; it is
  what would materially shorten a 25-file release page.
- **P5** business labels for the document Category vocabulary — recommended, but it belongs to the
  document register's own pass.

## Measured result on the real 25-file package

```text
                                         before        after
page loads / departures                     129           58
form submissions                             76           54
in-page disclosures (no page load)            0           25
total user actions                          129           83
field interactions                          277          115
manual re-uploads of held files              25            0
receipt date/times typed                      1            0
post-import page height @1366          15,294 px    13,157 px
horizontal overflow @1366 / @1920          none         none
```

## Files changed

- `core/bid_package_presentation.py` — new; the plain-language layer and the decisions about
  which controls are worth showing. Pure, fully typed, in the strict-mypy scope.
- `core/bid_package_repository.py` — `release_detail` also selects the controlled document's id,
  so a file under control can link to it.
- `app.py` — server-recorded receipt and notice times; derived link relationship; the
  `…/files/{file_id}/controlled-document` route; presentation helpers in the workspace context.
- `templates/_package_intake_section.html` — the whole routine intake surface.
- `pyproject.toml` — `core/bid_package_presentation.py` added to the strict-mypy scope.
- `scripts/asgi_acceptance_ops11by.py` — new executable acceptance.
- `scripts/validate_ops_11.py` — runs that acceptance and asserts its invariants.
- `tests/unit/test_bid_package_presentation.py` — new; 17 behavioural tests.
- `tests/unit/test_ops11_ui.py` — two new executable tests; the existing test strengthened to
  assert the simplifications instead of the wording they replaced.

## Acceptance

See the checklist in `HANDOFF.md`.
