# OPS-03 — Role Framework Authoring and Controlled Publication

## Authorization and baseline

- Authorized on 2026-08-17 after accepted OPS-02 automated and browser acceptance.
- Baseline branch: `ops-02-operational-command-center`.
- Baseline commit: `73a1adf356e231338e02c4d0ca94e302d4d97ce1`.
- Implementation branch: `ops-03-role-framework-authoring`.
- Recovery pointer: `backup/ops-03-pre-implementation-20260817`.
- Armoury remains the active development environment.

## Problem and primary user outcome

OPS-01 created effective-dated role-profile storage and lifecycle values, but the Role
Framework browser page is read-only. Raw JSON mutations also bypass the typed service,
audit, provenance, and optimistic-concurrency patterns used by accepted operational
registers.

OPS-03 lets a user create, edit, publish, revise, supersede, retire, and inspect controlled
role-profile versions through a server-rendered browser workflow while preserving existing
profile IDs, version numbers, states, dates, and rows.

## In scope

- Typed Pydantic v2 role-profile domain and command models.
- One role-profile service using the existing `OpsFoundationRepository`.
- Browser create, stable detail, draft edit, publish, revise, controlled supersession,
  retirement, history, provenance, and audit views.
- Existing role-profile JSON routes routed through the same service.
- Existing 13-domain allowlist, with deterministic deduplication and display order.
- Explicit fixed-date effective-profile lookup.
- Global transactional version allocation.
- Expected-token enforcement and atomic profile/audit writes.
- Temporary-database unit, validation, and dependency-free ASGI acceptance.

## Out of scope

- Profile or work-item deletion.
- Changes to work items, My Work, My Day, responsibility-domain taxonomy, or TASK-06–18.
- Contribution review, achievement evidence, non-bid context management, TASK-19,
  dashboard redesign, notifications, calendar/email, external integrations, or Alice.
- Authentication redesign, custom domains, role assignments, seeded organization/role data,
  dependencies, configuration changes, or database migrations.

## Typed domain and command contract

Stored lifecycle values remain `DRAFT`, `PUBLISHED`, and `RETIRED`. `RoleProfile` hydrates
the existing row, including optional legacy provenance. Browser/API commands never control
profile IDs, global versions, actors, timestamps, generated tokens, or provenance.

- `RoleProfileCreate`: bounded non-empty title, explicit effective start, optional end,
  optional organization and narrative content, and existing responsibility domains.
- `RoleProfileDraftEdit`: the same authorable fields plus an expected token.
- `RoleProfileRevision`: a parent expected token and optional effective start for the child;
  authorable content is copied from the parent.
- `RoleProfileTransition`: expected token for publish or retire.

Whitespace is normalized. Title is limited to 300 characters, organization to 300, and each
narrative field to 10,000. An end date cannot precede its start date.

## Draft and publication validation

A DRAFT requires only a title and valid effective window. Mission, organization, boundaries,
coordination, outcomes, cadence, and domains may remain incomplete.

Publication additionally requires a non-empty mission, at least one valid responsibility
domain, valid human-authored provenance, and no prohibited published overlap. Organization
and the remaining narrative fields stay optional. Rejected forms retain submitted values and
return understandable HTTP 422 feedback.

## Lifecycle transition matrix

| From | Operation | To | Rule |
|---|---|---|---|
| none | create | DRAFT | Server identity/version/provenance |
| DRAFT | edit | DRAFT | Expected token; authored fields only |
| DRAFT | publish | PUBLISHED | Publication validation and overlap check |
| PUBLISHED | revise | child DRAFT | One active child, parent token consumed |
| RETIRED | revise | child DRAFT | One active child, parent token consumed |
| PUBLISHED | retire | RETIRED | Expected token |

Published and retired authored content is immutable. PUBLISHED cannot return to DRAFT in
place, RETIRED cannot return to PUBLISHED in place, and hard deletion is unsupported.

## Atomic revision and supersession

Revision creation checks the parent token and lifecycle, rejects an existing DRAFT child,
allocates `MAX(version_number) + 1` after `BEGIN IMMEDIATE`, creates the child DRAFT, rotates
the parent token, and appends one audit event in the same transaction.

Publishing a child of a PUBLISHED parent requires a child start later than the parent's
start. In one transaction it rechecks child and parent tokens/states, validates other
published windows, closes the parent at the day before the child start, publishes the child,
rotates both tokens, and appends publication and supersession audit events. The parent remains
PUBLISHED historical evidence with unchanged authored content. A child of a RETIRED parent
publishes without reactivating it and must not overlap another PUBLISHED profile.

Ordinary DRAFT publication never silently modifies another profile.

## Effective-window and version rules

- Published windows are inclusive.
- Ordinary publication rejects overlap with every other PUBLISHED profile.
- Supersession excludes the PUBLISHED parent only after calculating its closed window.
- Effective lookup accepts an explicit `as_of`, returns zero or one PUBLISHED profile, and
  raises a visible error for overlapping legacy data.
- The HTML route supplies the established application working date; tests inject fixed dates.
- GET operations never mutate profiles, tokens, or audit history.
- Global versions are allocated only inside the profile insert transaction.

## Audit, provenance, and concurrency

Global role events use the existing `audit_log` with `bid_id=NULL`. Accepted logical actions
write `role_profile_created`, `role_profile_draft_updated`, `role_profile_revised`,
`role_profile_published`, `role_profile_superseded`, or `role_profile_retired`. Audit detail
contains identity, global version, state/window changes, parent/child identity, actor, and
provenance source. Audit failure rolls back all associated profile changes.

New profiles and revisions receive service-generated `Provenance.from_human(actor)`. Missing,
empty, or malformed legacy provenance remains explicitly uncertain and is never treated as
human-confirmed or backfilled.

Draft edit, publish, revise, and retire require the existing expected `version_token`.
Accepted actions rotate or consume applicable tokens. Stale and invalid actions change
neither profile nor audit. HTML stale responses retain input with HTTP 422; JSON stale
responses use HTTP 409.

## Browser and JSON routes

Browser routes:

- `GET /role-framework`
- `GET /role-framework/new`
- `POST /role-framework`
- `GET /role-framework/{profile_id}`
- `POST /role-framework/{profile_id}`
- `POST /role-framework/{profile_id}/revise`
- `POST /role-framework/{profile_id}/publish`
- `POST /role-framework/{profile_id}/retire`

Successful HTML writes use POST/redirect/GET. The detail page exposes state-appropriate
controls, a hidden expected token, effective windows, parent/child links, provenance, and
audit history. Missing profiles return 404. No arbitrary return URL is accepted.

Existing `/api/ops/role-profiles` route paths and successful response shapes remain. JSON
mutations use supported authorable fields only; server-owned fields are rejected with 422.

## Data and migration decision

No database migration or persisted-domain redesign is authorized or required. Existing
`parent_profile_id`, `version_token`, `provenance_json`, effective-window, lifecycle, and
narrative fields are sufficient. Existing rows are preserved without backfill.

## Automated acceptance

- Domain normalization, draft/publication validation, allowlist ordering, and legacy
  provenance uncertainty.
- HTML setup state, create/redirect/reload, full draft persistence, draft editing, 404, and
  read-only GET behavior.
- Fixed-date effective lookup, overlap rejection, immutable authored content, global version
  allocation, one-child rule, revision atomicity, supersession before/at/after lookup, and
  retirement history.
- Expected-token checks for every mutation, two-tab stale behavior, audit rollback, and no
  false audit on rejected writes.
- Existing JSON paths with request hardening, no delete route, and no network call.
- OPS-01/OPS-02 and TASK-06–18 validators, ASGI scripts, focused tests, full pytest, scoped
  Ruff/format, optional configured mypy, and `git diff --check`.

## Manual acceptance

1. Open the empty Role Framework and create a DRAFT using every field.
2. Reload and edit the draft; verify values and domain labels persist.
3. Use two tabs to verify a stale draft save is rejected without data loss.
4. Publish a complete draft and verify it is effective on the application working date.
5. Verify published authored content is read-only and audit/provenance are visible.
6. Create a child revision and confirm parent/child links and duplicate-child protection.
7. Publish the revision and verify the parent closes the day before the child starts while
   the correct profile is effective before and after changeover.
8. Retire a PUBLISHED profile and confirm it remains in history but is no longer effective.
9. Confirm missing IDs return 404 and My Day/My Work/TASK-06–18 routes remain unchanged.

## Deferred candidates

Contribution-candidate review, non-bid context management, executive operational dashboard,
TASK-19, integrations, Alice prioritization, and broad UI redesign remain deferred.

## Definition of done

All lifecycle, persistence, provenance, audit, concurrency, browser, and JSON acceptance is
passing against temporary databases; all 300 published tests plus OPS-03 tests pass; no
migration, dependency, configuration, production-data, work-item, or My Day/My Work change
occurs; changed Python passes scoped Ruff/format; and `HANDOFF.md` reports
`AWAITING MANUAL BROWSER ACCEPTANCE`. Nothing is staged, committed, pushed, merged, rebased,
or published before Jason's browser acceptance.
