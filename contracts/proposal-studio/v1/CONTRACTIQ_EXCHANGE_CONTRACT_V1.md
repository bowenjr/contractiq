# ContractIQ ↔ Proposal Studio File Exchange Contract V1

**Status:** FROZEN baseline  
**Contract version:** `1.0.0`  
**JSON Schema dialect:** Draft 2020-12  
**Transport:** local files only  
**Network API:** not defined in V1  
**Database coupling:** prohibited

## 1. Purpose and boundary

This contract defines the Version 1 file boundary between ContractIQ and Proposal Studio.

ContractIQ exports a **proposal package** containing the facts and controlled references needed to prepare a proposal. Proposal Studio imports that package without reading ContractIQ's database or importing ContractIQ internal modules.

After Proposal Studio produces a controlled generation for a proposal derived from a ContractIQ package, Proposal Studio may return a **generation manifest**. The manifest records which package, template and output artifacts were used. It does not write back into ContractIQ and does not define a network protocol.

The V1 boundary is deliberately one-way at each step:

```text
ContractIQ
   |
   | proposal-package-v1 JSON + separately managed referenced files
   v
Proposal Studio
   |
   | proposal-generation-manifest-v1 JSON + generated artifacts
   v
ContractIQ registration/import layer (future adapter)
```

Neither application may directly connect to the other's database.

## 2. Contract files

Schemas:

- `contracts/proposal-package-v1.schema.json`
- `contracts/proposal-generation-manifest-v1.schema.json`

Valid examples:

- `contracts/examples/proposal-package-v1.example.json`
- `contracts/examples/proposal-generation-manifest-v1.example.json`

Invalid fixtures:

- `contracts/examples/invalid/proposal-package-v1.invalid-absolute-path.json`
- `contracts/examples/invalid/proposal-package-v1.invalid-order.json`
- `contracts/examples/invalid/proposal-generation-manifest-v1.invalid-path.json`
- `contracts/examples/invalid/proposal-generation-manifest-v1.invalid-order.json`
- `contracts/examples/invalid/proposal-generation-manifest-v1.invalid-success-placeholder.json`

## 3. Schema identifiers

### Proposal package

```json
{
  "schema_id": "urn:proposal-studio:contract:proposal-package:v1",
  "schema_version": "1.0.0"
}
```

### Generation manifest

```json
{
  "schema_id": "urn:proposal-studio:contract:proposal-generation-manifest:v1",
  "schema_version": "1.0.0"
}
```

`schema_id` identifies the major contract family. `schema_version` identifies the exact semantic version.

A consumer MUST validate both before processing the document.

## 4. General V1 JSON rules

### 4.1 Encoding

Files MUST be UTF-8 JSON without a BOM.

### 4.2 Duplicate keys

Duplicate object member names are invalid. Parsers used for hashing or validation MUST reject them rather than silently taking the first or last value.

### 4.3 `additionalProperties`

The V1 schemas deliberately use `additionalProperties: false` for contract objects. Unknown properties therefore fail validation.

This is intentional. Silent acceptance of unknown proposal or commercial fields would make the exchange non-deterministic and could create false assumptions about what Proposal Studio actually consumed.

### 4.4 Collection limits and string limits

All strings and collections have explicit bounds in the schemas. The limits are intended to prevent malformed or unexpectedly large exchange documents while remaining well above normal proposal volumes.

### 4.5 Numbers

V1 does not permit non-integer JSON numbers in the exchange model. Quantities, integer proposal inputs and numeric version values are integers. Monetary values are not defined in V1; see **Unresolved Contract Decisions**.

This restriction avoids cross-language floating-point canonicalization ambiguity.

## 5. Null, omission and empty collections

V1 uses a strict convention.

### Required collections

All top-level collections are REQUIRED and MUST be arrays. When there are no records, use an empty array:

```json
"requirements": []
```

Do not use:

```json
"requirements": null
```

and do not omit the property.

This applies to:

- requirements;
- manufacturer packages;
- VDRL commitments;
- commercial positions;
- qualifications and deviations;
- proposal-issue risks;
- decisions and approvals;
- delivery commitments;
- proposal inputs;
- supporting documents;
- all nested scope lists;
- nested equipment and commitment lists;
- manifest section/change/finding lists.

### Optional singular values

Where absence is semantically meaningful, the property remains present and uses `null`.

Examples:

- no selected pricing scenario: `"selected_pricing_scenario": null`;
- no ContractIQ customer ID: `"customer_id": null`;
- no exported supporting-file path: `"relative_path": null`;
- no PDF generated: `"pdf": null`;
- no source value for a manual override: `"source_value_sha256": null`.

This convention ensures that **missing property**, **known absence**, and **empty collection** do not collapse into one state.

## 6. Proposal package V1

The proposal package is the ContractIQ → Proposal Studio exchange object.

### 6.1 Required package identity

Required:

- `schema_id`;
- `schema_version`;
- `package_id`;
- `source_application`;
- `generated_at`.

`generated_at` MUST be an ISO 8601 UTC timestamp ending in `Z`.

### 6.2 Bid

`bid` carries:

- bid ID;
- bid revision;
- bid name;
- source provenance.

The bid object identifies the bid state from which the package was exported. It does not create a Proposal Studio proposal ID.

### 6.3 Customer and opportunity

The package carries customer and opportunity identity separately so Proposal Studio does not have to derive customer identity from an opportunity string.

Customer name and opportunity name are required. ContractIQ-specific IDs may be `null` if no stable exchange identifier exists.

### 6.4 Requirements and proposed responses

Each requirement carries:

- requirement ID;
- requirement text;
- proposed response or `null`;
- normalized response status;
- source provenance.

V1 response statuses are:

- `COMPLIANT`;
- `COMPLIANT_WITH_QUALIFICATION`;
- `NON_COMPLIANT`;
- `NOT_APPLICABLE`;
- `UNRESOLVED`.

ContractIQ may maintain richer internal states. Its export adapter must map them to this V1 proposal-facing vocabulary without changing the underlying source record.

### 6.5 Scope

`scope` always contains:

- `inclusions`;
- `exclusions`;
- `interfaces`.

Inclusions and exclusions are controlled scope statements. Interfaces additionally identify responsibility and an optional counterparty.

### 6.6 Manufacturer and equipment packages

`manufacturer_packages` groups equipment and manufacturer commitments that belong together in the proposal context.

A package contains:

- package ID and description;
- manufacturer name or `null`;
- equipment items;
- manufacturer commitments;
- package-level source provenance.

A manufacturer commitment includes a status and zero or more supporting document IDs that provide evidence.

Evidence document IDs MUST reference IDs present in `supporting_documents`.

### 6.7 VDRL commitments

`vdrl_commitments` contains proposal-relevant vendor-document commitments only. It is not intended to replicate the entire ContractIQ vendor-data register.

V1 carries:

- VDRL ID;
- document code;
- title;
- commitment wording;
- normalized status;
- source provenance.

### 6.8 Commercial positions

Each commercial position contains:

- stable exchange ID;
- topic;
- customer position or `null`;
- proposed position;
- normalized status;
- source provenance.

V1 statuses are `APPROVED`, `CONDITIONAL`, `UNRESOLVED`, and `NOT_APPLICABLE`.

The package does not imply that unresolved commercial positions are acceptable for issue. Proposal Studio readiness rules determine whether they block controlled generation.

### 6.9 Qualifications, deviations and clarifications

`qualifications_and_deviations` carries proposal-facing exceptions and clarifications.

`kind` is one of:

- `QUALIFICATION`;
- `DEVIATION`;
- `CLARIFICATION`.

Related requirement IDs must reference requirement IDs in the same package.

### 6.10 Proposal-issue risks

`proposal_issue_risks` contains only risks that matter to the decision to issue the proposal or to wording that must be carried into it.

It is not an export of the complete ContractIQ risk register.

### 6.11 Decisions and approvals

`decisions_and_approvals` communicates decisions needed to understand whether proposal-facing positions are approved, rejected, pending or not required.

`authority_reference` is an exchange reference, not an authorization credential. Proposal Studio MUST NOT treat it as permission to approve or modify ContractIQ data.

### 6.12 Selected pricing-scenario reference

`selected_pricing_scenario` is required at the property level but may be `null`.

When present, it identifies the selected ContractIQ pricing scenario and its source provenance.

V1 intentionally does **not** define customer-facing price totals, line-item pricing or a pricing breakdown. See **Unresolved Contract Decisions**.

### 6.13 Delivery commitments

Delivery commitments contain:

- stable commitment ID;
- description;
- optional exact target date;
- proposal-facing commitment wording;
- normalized status;
- source provenance.

The text remains authoritative for proposal wording when an exact date alone would lose conditions or dependencies.

### 6.14 Proposal inputs

`proposal_inputs` is a bounded list of scalar proposal inputs that do not belong to one of the explicit business collections.

It is not an arbitrary JSON extension mechanism.

Allowed types are:

- `TEXT`;
- `DATE`;
- `BOOLEAN`;
- `INTEGER`.

The schema enforces the declared value type. `null` is permitted to represent a known missing value.

### 6.15 Supporting documents

Supporting documents are references and metadata only. File contents are never embedded in the JSON.

Each reference carries:

- document ID;
- title;
- revision or `null`;
- relative path or `null`;
- safe media type;
- SHA-256 content hash;
- source provenance.

Permitted media types are intentionally restricted to non-macro, non-executable document/image formats listed in the schema.

A `relative_path`:

- MUST be relative;
- MUST NOT start with `/`;
- MUST NOT contain `..` path segments;
- MUST NOT contain Windows drive-qualified paths;
- MUST NOT contain backslash path separators;
- MUST NOT contain duplicate `/` separators.

Proposal Studio MUST treat these entries as inert references. It MUST NOT execute a referenced file, shell command, macro, script or URI.

V1 contains no action field, command field, executable field or external URL action.

## 7. Source provenance

Material exported records carry a `source` object containing:

- `source_type`;
- `source_id`;
- `source_version`;
- `source_sha256`.

These values exist so Proposal Studio can:

- show provenance;
- compare later packages;
- identify changed authoritative source records;
- preserve the basis of an issued proposal revision.

They do **not** authorize Proposal Studio to query ContractIQ.

`source_sha256` is an opaque ContractIQ-generated fingerprint from Proposal Studio's perspective. ContractIQ MUST generate it deterministically from the stable source projection used for exchange. Proposal Studio compares the value across packages but does not need ContractIQ's internal record schema to reproduce it.

## 8. Deterministic collection ordering

JSON object member order is not semantically significant. Array order is significant and V1 therefore mandates deterministic ordering.

### Proposal package order

Producers MUST sort:

| Collection | Sort key |
|---|---|
| `requirements` | `requirement_id` ascending |
| `scope.inclusions` | `scope_item_id` ascending |
| `scope.exclusions` | `scope_item_id` ascending |
| `scope.interfaces` | `interface_id` ascending |
| `manufacturer_packages` | `package_id` ascending |
| `manufacturer_packages[].equipment` | `equipment_id` ascending |
| `manufacturer_packages[].commitments` | `commitment_id` ascending |
| `evidence_document_ids` | ID ascending |
| `vdrl_commitments` | `vdrl_id` ascending |
| `commercial_positions` | `commercial_position_id` ascending |
| `qualifications_and_deviations` | `item_id` ascending |
| `related_requirement_ids` | ID ascending |
| `proposal_issue_risks` | `risk_id` ascending |
| `decisions_and_approvals` | `decision_id` ascending |
| `delivery_commitments` | `delivery_commitment_id` ascending |
| `proposal_inputs` | `input_key` ascending |
| `supporting_documents` | `document_id` ascending |

IDs used as sort keys MUST be unique inside their collection.

### Generation manifest order

Producers MUST sort:

| Collection | Sort key |
|---|---|
| `included_sections` | `(order, section_key)` ascending |
| `excluded_sections` | `(order, section_key)` ascending |
| `manual_narratives` | `section_key` ascending |
| `manual_overrides` | `target_path` ascending |
| `warnings` | `(severity, code, target-or-empty, message)` ascending |
| `unresolved_placeholders` | `(placeholder, section_key-or-empty)` ascending |

A section key MUST NOT appear in both `included_sections` and `excluded_sections`.

Ordering constraints are semantic contract rules and are verified by the repository tests because JSON Schema cannot express general sorted-array constraints.

## 9. Canonical JSON hashing

### 9.1 Package hash purpose

The generation manifest records the SHA-256 of the exact logical proposal package used for generation. Formatting changes such as indentation or object-key order must not change that hash.

### 9.2 V1 canonicalization algorithm

To calculate a V1 canonical JSON SHA-256:

1. Decode the document as UTF-8 JSON.
2. Reject duplicate object member names.
3. Reject `NaN`, positive infinity and negative infinity.
4. Validate against the applicable V1 schema before hashing.
5. Verify deterministic array ordering rules before hashing.
6. Serialize recursively using:
   - object keys sorted ascending by Unicode code point;
   - arrays preserved in their contract-defined order;
   - no insignificant whitespace;
   - `,` between array/object entries;
   - `:` between object keys and values;
   - UTF-8 output;
   - JSON strings emitted without ASCII-only escaping, except characters JSON requires to be escaped;
   - booleans as `true`/`false`;
   - null as `null`;
   - integers in ordinary base-10 form with no leading `+` and no leading zeros except `0`.
7. Compute SHA-256 over the resulting UTF-8 bytes.
8. Encode the digest as 64 lowercase hexadecimal characters.

For Python, the V1 data model is compatible with:

```python
canonical = json.dumps(
    value,
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=False,
    allow_nan=False,
).encode("utf-8")
sha256 = hashlib.sha256(canonical).hexdigest()
```

provided duplicate keys were rejected during parsing and the schema/order checks have already passed.

### 9.3 File hashes

Generated DOCX/PDF hashes and supporting-document hashes are SHA-256 over the **raw file bytes**, not canonical JSON.

### 9.4 Source record hashes

`source_sha256` values are produced by ContractIQ from its stable proposal-exchange source projection. They are opaque to Proposal Studio and are used for provenance/change comparison.

## 10. Generation manifest V1

The generation manifest is the Proposal Studio → ContractIQ exchange object for a generation derived from a ContractIQ package.

It is not required for standalone proposals that have no ContractIQ proposal package.

### 10.1 Proposal identity

The manifest identifies:

- Proposal Studio proposal ID;
- proposal revision.

These are Proposal Studio identities and are not required to match ContractIQ bid IDs or revisions.

### 10.2 Source package identity

`source_proposal_package` contains:

- source package ID;
- canonical proposal-package SHA-256.

This binds the output to the exact logical package used for the generation.

### 10.3 Template identity

The manifest records:

- template ID;
- immutable template version.

### 10.4 Generation application

`generation_application` records Proposal Studio application name and version.

The manifest timestamp MUST be UTC and end in `Z`.

### 10.5 Generated files

`generated_files` is an object rather than an unordered artifact list:

```json
{
  "docx": {"filename": "...docx", "sha256": "..."},
  "pdf": {"filename": "...pdf", "sha256": "..."}
}
```

`pdf` may be `null` when no PDF was generated.

For `SUCCEEDED` or `SUCCEEDED_WITH_WARNINGS`, `docx` is required to be a valid DOCX artifact object.

For `BLOCKED`, both file entries MUST be `null`.

Filenames are basenames only. Paths, absolute paths and traversal are rejected.

### 10.6 Included and excluded sections

The manifest records both included and excluded template sections. Exclusions carry a reason so a later reviewer can distinguish deliberate omission from lost data.

### 10.7 Manual narrative declarations

Manual narrative text is **not duplicated in the manifest**.

For each manually authored narrative carried into the controlled generation, the manifest records:

- section key;
- SHA-256 of the effective narrative content.

This declares manual authorship while avoiding unnecessary duplication of potentially confidential proposal text.

### 10.8 Manual override declarations

Manual overrides record:

- target path;
- source-value hash or `null`;
- effective override-value hash;
- reason.

The manifest does not copy the actual overridden value. Proposal Studio's immutable proposal revision remains the authoritative record of the effective text/value used.

### 10.9 Warnings and unresolved placeholders

Warnings remain explicit in the manifest.

A successful generation MUST have zero unresolved placeholders. The schema enforces this for `SUCCEEDED` and `SUCCEEDED_WITH_WARNINGS`.

`SUCCEEDED` requires no warnings. `SUCCEEDED_WITH_WARNINGS` requires at least one warning.

### 10.10 Generation status

Allowed statuses:

- `SUCCEEDED`;
- `SUCCEEDED_WITH_WARNINGS`;
- `BLOCKED`;
- `FAILED`.

`BLOCKED` represents a generation that did not produce controlled output because readiness conditions were not satisfied.

`FAILED` represents an attempted generation that failed for a technical reason. Partial artifacts may exist internally, but they must not be treated as controlled issued files.

## 11. Security constraints

V1 is a data exchange contract, not an execution protocol.

The following are prohibited by design:

- absolute attachment paths;
- traversal paths;
- embedded confidential file contents;
- macro-enabled media types;
- executable media types;
- script/command fields;
- shell commands;
- executable actions;
- external URL actions;
- database connection information;
- credentials or secrets.

A consumer MUST NOT execute any value in either JSON document as code, a command, a macro or a URL action.

Supporting files, when eventually transported alongside the JSON, must be treated as untrusted input and verified by expected media type and SHA-256 before use. V1 does not define automatic file opening or execution.

## 12. Required versus optional data summary

The schemas are intentionally strict. At the top level, all named contract properties are required so that the shape is predictable.

Absence is represented through:

- `[]` for no collection records;
- `null` for explicitly absent nullable singular values.

Business-level readiness is separate from schema validity. For example:

```json
"selected_pricing_scenario": null
```

is schema-valid, but Proposal Studio may still block controlled generation if the selected template requires a pricing scenario.

Likewise, a requirement with:

```json
"proposed_response": null,
"response_status": "UNRESOLVED"
```

is valid exchange data and can be correctly represented as unresolved rather than being silently converted into compliance.

## 13. Compatibility and versioning policy

The contract uses semantic versioning.

### 13.1 Patch changes: `1.0.x`

Patch changes may include:

- documentation corrections;
- additional valid/invalid examples;
- test improvements;
- clarifications that do not alter accepted/rejected JSON instances or field semantics.

Existing schema files must not be silently changed in a way that alters validation behavior under a patch-only documentation release.

### 13.2 Backward-compatible minor changes: `1.x.0`

A minor version may add proposal-exchange capability without changing the meaning of existing V1 fields.

Examples may include:

- a new optional field in a new `1.1.0` schema;
- a new optional collection;
- a new optional output artifact type where old fields remain valid.

Because V1.0.0 deliberately has `additionalProperties: false`, a V1.0.0 validator will reject V1.1.0 documents containing new fields. Therefore minor-version support is **explicitly negotiated by schema/version support**, not silently accepted.

The old schema remains retained and valid. Producers must emit a version the receiving application declares it supports.

### 13.3 Breaking changes: `2.0.0`

The following require a new major contract and new schema identifier, for example `...:v2`:

- removing a field;
- renaming a field;
- changing a field's meaning;
- changing required/null behavior;
- changing an identifier's semantic meaning;
- changing canonical hashing rules;
- changing deterministic ordering rules;
- converting a scalar to a collection or vice versa;
- making previously valid V1 business values invalid in a way that requires producer remapping;
- adding a transport/action behavior that changes the trust boundary.

## 14. Validation sequence

A V1 importer should process a proposal package in this order:

```text
1. Read local JSON file
2. Reject invalid UTF-8 / duplicate JSON keys
3. Validate schema_id and schema_version
4. Validate against Draft 2020-12 schema
5. Validate semantic ordering / uniqueness / cross-references
6. Calculate canonical package SHA-256
7. Create immutable Proposal Studio import snapshot
8. Continue into Proposal Studio readiness/mapping logic
```

A V1 ContractIQ manifest importer should similarly:

```text
1. Validate manifest schema
2. Validate semantic ordering / section disjointness
3. Locate the registered source package ID
4. Recalculate/compare canonical package SHA-256
5. Verify generated artifact hashes when files are present
6. Register results without modifying the historical source package
```

No database-to-database access is involved.

## 15. Unresolved contract decisions

The following decisions are intentionally **not invented** in V1 and must be resolved before the relevant functionality is implemented.

### UCD-01 — Customer-facing pricing data

V1 carries only the **selected pricing-scenario reference**.

It does not yet define:

- total proposal price;
- currency presentation;
- alternate prices;
- line-item price tables;
- taxes;
- escalation;
- freight breakdown;
- pricing schedule formatting.

This is unresolved because Proposal Studio clearly needs customer-facing pricing in some proposals, but the correct authoritative model must be decided rather than hidden inside generic `proposal_inputs`.

**Recommendation:** define a dedicated pricing exchange structure only when the Proposal Studio pricing-section requirements are designed.

### UCD-02 — Supporting-file transport/package layout

V1 defines secure relative document references and hashes but does not define whether ContractIQ will deliver referenced files as:

- siblings of the JSON file;
- a controlled directory tree;
- a ZIP package;
- an explicit user-selected export bundle.

No assumption is made yet.

### UCD-03 — VDRL schedule granularity

V1 carries proposal-facing VDRL commitment wording but does not model every possible planned/required submission date, cycle or customer review duration.

If Proposal Studio needs a detailed VDRL schedule table, a later compatible/new contract field should be designed from that real rendering requirement.

### UCD-04 — Approval authority identity vocabulary

`authority_reference` is intentionally a bounded opaque reference. V1 does not dictate whether ContractIQ exposes a role ID, authority-matrix ID or display-safe person reference.

The ContractIQ adapter must choose a stable non-secret reference before production integration.

### UCD-05 — Source-hash projection definitions

Proposal Studio treats each `source_sha256` as an opaque deterministic ContractIQ fingerprint. ContractIQ still needs to freeze the stable source projection for each exported `source_type` so harmless database-only metadata changes do not create false proposal staleness.

This is a ContractIQ-side producer implementation decision, not a Proposal Studio database dependency.

## 16. Frozen V1 decisions

The following are frozen for `1.0.0`:

- file exchange only;
- no network API;
- no direct database access;
- JSON Schema Draft 2020-12;
- strict object properties;
- required top-level collections;
- `[]` for no records;
- `null` for explicitly absent nullable singular values;
- UTC timestamps ending in `Z`;
- lowercase SHA-256 hex;
- deterministic array ordering;
- canonical JSON package hashing;
- source provenance on material exported records;
- supporting-document references rather than embedded file contents;
- no executable/macro/action semantics;
- Proposal Studio proposal/revision identity remains separate from ContractIQ bid identity;
- generation manifest is required only for ContractIQ-derived round-trip registration, not for standalone manual proposals.

