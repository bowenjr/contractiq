# VDDR "147 rendered rows vs 146 evaluator rows" — resolved (Stage 2.1)

**Verdict: a counting difference plus a merge defect, not an extraction defect.**

| Count | Value | What it really is |
|---|---|---|
| Rows rendered by the reader (`ROW <item> | …` lines) | **149** distinct item numbers | Every row of the Schedule of Vendor Drawing and Data Requirements, pp.2–4 |
| Item numbers in the PDF layout (independent word-position extraction, `vddr-layout.txt`) | **149** | Identical set: no row missing, none invented |
| "146 template rows" (CIQ-EVAL-001 narrative) | 146 | A manual count that is 3 short of the 149 rows actually in the template; no row of the evaluator's 43 *Required* / 12 *tender* truth is affected |
| "147" in the Stage 2 acceptance report | 147 | Distinct item codes among the stored rule findings, **not** rendered rows: 149 − 2 |

The two rows missing from Stage 2's 147 were lost by the title + lifecycle merge key (Unit A):

* **1.09 Monthly Progress Report** — merged into the model's same-titled POST_AWARD clause finding;
* **9.16 Statutory Authority Approval Certificates** — merged into row 8.16, which has the same description.

After Stage 2.1 the full re-run (`~/contractiq-review/stage2.1/results/vddr-row-count.txt`) gives 149 rendered rows =
149 layout item numbers, every one with a rule finding (160 findings: 11 rows yield both a tender-stage and a
post-award finding). Rows with equal descriptions but different item codes are different obligations and are kept;
a rule row and a model finding with the same title are kept apart and flagged as a possible collision unless they cite
the same quotation.
