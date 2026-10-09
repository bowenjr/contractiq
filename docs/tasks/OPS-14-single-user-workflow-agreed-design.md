# OPS-14 — Single-user workflow: agreed future design (not implemented)

Status: **design record only.** Nothing in this document is implemented except where a section says
"Delivered". It records what was agreed after the workflow review of 2026-10-06 so later units can be
built from it. Evidence, screenshots and measurements are in the review report, kept outside Git:
`~/contractiq-review/r1006/report/ContractIQ-workflow-review-2026-10-06.md` (section 11 holds the
confirmed decisions). No customer content belongs in this file.

## Purpose

ContractIQ is one person's Bid preparation tool. The owner's process is: check the deadlines; understand
scope, equipment and quantities; identify what must be returned; line up suppliers and quotations; prepare the
technical and commercial response; obtain approval where he decides it is needed; send the offer and keep what
it was based on. A function is useful when it makes one of those steps easier. Optional information never
becomes a gate, and there is one progression model: the six guided tasks in `core/guided_workflow.py`.

## Agreed sequence (re-labels the existing six tasks; no second navigator)

| # | Task | Replaces today | Done when |
|---|---|---|---|
| 1 | Import package | A + first half of B | Files stored; readable files analysed or queued |
| 2 | Review Bid summary | second half of B + C | Deadlines confirmed; returnables reviewed |
| 3 | Pursue? | D | Owner decision recorded |
| 4 | Suppliers and scope | E (part) | The owner says coverage is sufficient |
| 5 | Response and price | E (part) | Returnables have a response; open terms have a position |
| 6 | Check and send | F | Pre-send checklist clear; offer recorded as sent |

A later task is never locked by an earlier optional item. After every save the page says what changed and
offers the next open item of the same kind.

## Confirmed decisions (owner, 2026-10-06)

### 1. Record offer sent
Offers are prepared in Word/Excel. Provide a direct **Record offer sent** path: the files sent, recipient,
date and method. The existing Proposal Studio export / import / issue route stays as an optional path.

Design notes: `ProposalExchangeService.issue` and `issued_offer_baselines` require a Proposal Studio
candidate, export, receipt and manifest, so a directly sent offer needs its own additive, append-only record
(offer-sent record and its files with name, size and SHA-256), written with its audit row in one transaction
and stored through managed storage. The record freezes the documents used for the offer, the approval that
applied, the changes since that approval, the qualifications and clarification requests included, and the
returnables omitted or qualified. "Offer sent" is true when either route has a record.

### 2. Approval is not invalidated by later changes
Changes after approval do not invalidate the approval and do not block sending. The original approval is
preserved and the changes are shown as information. The owner may choose **Request approval again** when he
judges a change substantial or risky. No acknowledgement is required to continue.

Design notes: this removes the current rule that ties an approval to the selected pricing baseline
(`core/senior_approvals.py`, the `legacy` / `stale` problems). The snapshot stored with an approval is used
for display only. A re-request appends a Pending row that references the earlier row; no approved row is edited.

### 3. Qualifications, clarification requests and returnables
Open qualifications and clarification requests may accompany an offer when explicitly included. Each required
returnable must be **ready**, **intentionally omitted**, or **covered by a stated qualification**. No
additional approval process: no reviewer or sign-off for qualifications, omissions or deviations.

Design notes: replaces "negotiation must be Resolved" (`core/gate_service.py`), the `UNRESOLVED` mapping for
Reject / Clarification positions and Clarify / Option responses, and `UNDISPOSED_QUALIFICATION` (which today
needs an accepted review). Returnable states map to existing response values; "include with this offer" needs
one small additive, append-only selection.

## Approval tracking (owner's brief, as amended by decision 2)

```
Approval required?   Yes / No      chosen by the owner; nothing pre-selected
  If Yes: Status (Pending / Approved / Changes requested) · Approver · Approval date · Notes or file (optional)
  Recorded automatically: when the record was updated
  Shown as information: what was approved, and what has changed since
  Optional action: Request approval again
```

Not required: monetary thresholds, margin/value/credit set-up, authority-policy publishing, role
administration, a second application user, a reviewed pricing scenario. Approval is never inferred from the
governance level. The owner's Pursue / Do not pursue decision (`bid_owner_decisions`) is a separate record and
is unchanged. Existing approval rows are kept and displayed as history; no Yes/No answer is invented for
existing Bids. Planned shape: keep the `approvals` table and its append-only lineage, with additive columns
for status, note and the approved snapshot, and one decision point (`assess_bid_senior_approvals`) whose rule
becomes: unanswered → open; not required → clear; required → clear at Approved.

## Pre-send checklist (the only things that stop "Record offer sent")

| Item | Clear when |
|---|---|
| Approval required? | Answered No, or answered Yes and currently Approved |
| Required returnables | Each is ready, intentionally omitted, or covered by an included qualification |
| Expected addenda | Each is received or dismissed |
| The offer | Files, recipient, date and method entered |

Information only, never a stop: changes since approval; open qualifications not included; negotiation
state; commercial topics not reviewed; optional registers.

## Ingestion and assistance (direction)

Suggestions from the local model, from a Work ChatGPT file exchange and from the owner's own PDF highlights
enter one review queue as findings (kind, value, source file and locator with quoted words, origin, state).
Accepting a finding writes into the existing record — Bid dates, requirement, scope item, controlled document,
commercial position — and keeps its source. Deadlines, revisions, quantities and document purpose are never
guessed: a value without a quoted source is *missing*; two sources that disagree are *conflicting*.
Extraction and review come before any autonomous action.

## Implementation order

| Order | Unit | State |
|---|---|---|
| 1 | Dependable discovery analysis and honest file handling | **Delivered 2026-10-06, uncommitted** — evidence and known extraction limits in `HANDOFF.md`; takes effect on the live server after its next restart |
| 2 | Check and send: simplified approval + Record offer sent + qualification / returnable states | Not started |
| 3 | Bid summary suggestions; lighter Bid creation (title and customer only) | Not started |
| 4 | Import and analyse in one step; one "use for this offer" pass; readable document index | Not started |
| 5 | Returnables become requirements in one click; missing deliverable and supplier controls | Not started |
| 6 | Commercial review starts empty | Not started — needs the owner's answer on the 33 standard topics |
| 7 | Highlights import, addendum comparison, conflicts and missing documents, Work ChatGPT document package, supplier enquiry | Later |

### Carried forward from unit 1 (extraction quality — not yet addressed)

Found with the real model on the synthetic package; none is fixed by unit 1:

- Documents due **after award** are proposed as Bid submissions (7 of 25 as *Required*). Unit 3 should classify each
  finding as *with the Bid* / *after award* / *unclear* from the quoted clause, and show after-award items separately.
- The same returnable named in two documents is listed twice when worded differently; one vague finding ("Bid").
- A citation quotes the whole page or sheet, not the sentence.
- Conflicting dates across documents are carried per finding, not reconciled (see "Ingestion and assistance").
- Superseded revisions are analysed like any other Included file. No OCR for text-less PDFs.

## Assumptions to confirm before unit 2 is built

1. *Reject* on a commercial term is treated like *Qualify*: a stated deviation that may accompany the offer.
2. After "Request approval again" the status is Pending and stays on the checklist until Approved.
3. The checklist must be clear before the offer-sent record can be saved.
4. Qualifications not included with the offer are shown, not blocked.
5. "Intentionally omitted" needs no reason; a note is optional.
6. The files sent are stored as protected copies, not only by name.

Still open: whether a bare "No" is enough for "Approval required?"; whether the 33 standard commercial topics
remain as an optional checklist.
