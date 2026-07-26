# TASK-01 — Test Harness, Schemas, and Provenance Foundation

**Repo:** `bowenjr/contractiq`
**Phase:** v0.1 (deterministic spine)
**Depends on:** nothing
**Branch:** `task-01-schemas-and-harness`

Read `AGENTS.md` before starting. All standing rules apply.

---

## Context

ContractIQ is an existing, working application (~7,700 LOC, FastAPI + SQLite + LM Studio). It is **not** being rewritten. This task adds the engineering foundation that the existing code lacks — types, tests, and the provenance model — **without changing any existing behaviour**.

**You must not refactor, rename, or alter the behaviour of any existing module in this task.** Existing code keeps working exactly as it does today. You are adding new files alongside it.

---

## Objective

1. Establish the test/type/lint harness (pytest, mypy, ruff, CI).
2. Introduce Pydantic v2 domain schemas for the **new** spine entities.
3. Define the `Provenance` model that will be retrofitted across the analysis tables in TASK-03.
4. Write characterisation tests for two existing modules to prove the harness works against real code.

---

## Files to create

### 1. `pyproject.toml` (new — repo currently has only `requirements.txt`)

Keep `requirements.txt` working; add `pyproject.toml` alongside it.

- `[project]` name `contractiq`, version `0.2.0`, `requires-python = ">=3.11"`.
- Dependencies: copy from `requirements.txt`, and **add** `pydantic>=2.6`.
- `[project.optional-dependencies] dev = ["pytest>=8", "pytest-cov", "ruff", "mypy"]`
- `[tool.ruff]` line-length 100. `[tool.ruff.lint]` select `["E","F","I","UP","B"]`.
- `[tool.mypy]`: `python_version = "3.11"`, `strict = true`, `files = ["core/schemas.py", "core/enums.py"]` — **strict applies only to new files for now.** Do not attempt to make the existing 7,700 LOC pass mypy strict. Add `[[tool.mypy.overrides]] module = "*" ignore_errors = true` scoped so legacy modules are excluded.
- `[tool.pytest.ini_options] testpaths = ["tests"]`.

### 2. `core/enums.py` (new)

All enums inherit `str, Enum`.

```python
class CustomerType(str, Enum):
    EPC = "epc"
    EPCM = "epcm"
    END_USER = "end_user"

class BidLevel(str, Enum):
    LEVEL_0 = "level_0"   # routine quote
    LEVEL_1 = "level_1"   # project quote
    LEVEL_2 = "level_2"   # complex bid
    LEVEL_3 = "level_3"   # strategic bid
    LEVEL_4 = "level_4"   # exceptional risk

class Gate(str, Enum):
    G0 = "g0"; G1 = "g1"; G2 = "g2"; G3 = "g3"
    G4 = "g4"; G5 = "g5"; G6 = "g6"; G7 = "g7"

class GateStatus(str, Enum):
    NOT_STARTED = "not_started"
    IN_REVIEW = "in_review"
    PASSED = "passed"
    HELD = "held"
    OVERRIDDEN = "overridden"

class BidStatus(str, Enum):
    ACTIVE = "active"
    HELD = "held"
    SUBMITTED = "submitted"
    WON = "won"
    LOST = "lost"
    NO_BID = "no_bid"

class RiskTrigger(str, Enum):
    NON_STANDARD_TERMS = "non_standard_terms"
    LIQUIDATED_DAMAGES = "liquidated_damages"
    BONDS_OR_GUARANTEES = "bonds_or_guarantees"
    EXTENDED_PAYMENT_OR_HOLDBACK = "extended_payment_or_holdback"
    NON_CANCELLABLE_PRODUCT = "non_cancellable_product"
    MULTIPLE_MANUFACTURERS = "multiple_manufacturers"
    SUBSTANTIAL_VENDOR_DATA = "substantial_vendor_data"
    INTERNATIONAL_EXPOSURE = "international_exposure"
    LONG_DURATION = "long_duration"
    FIELD_SERVICES = "field_services"
    EPC_FLOWDOWN = "epc_flowdown"
    WARRANTY_EXTENSION = "warranty_extension"
    UNCLEAR_SCOPE = "unclear_scope"

class ApprovalType(str, Enum):
    BID_NO_BID = "bid_no_bid"
    MARGIN = "margin"
    LEGAL = "legal"
    CREDIT = "credit"
    FINANCE = "finance"
    EXECUTIVE = "executive"

class ItemStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    APPROVED = "approved"
    ESCALATED = "escalated"

class Actor(str, Enum):
    HUMAN = "human"
    AI = "ai"
    SYSTEM = "system"

class InferencePolicy(str, Enum):
    LOCAL_ONLY = "local_only"   # default — confidential
    CLOUD_OK = "cloud_ok"
```

**Note the `PillarId` enum must mirror `core/pillars.py` exactly** — read that file and derive it. Do not invent values:
```python
class PillarId(str, Enum):
    MONEY = "money"
    TIME = "time"
    SCOPE = "scope"
    RISK_LIABILITY = "risk_liability"
    RELATIONSHIPS = "relationships"
    ADMINISTRATION = "administration"
    EXIT = "exit"
```


**Salvaged taxonomies** (from the retired `ai-legal-review` repo — see `docs/SALVAGE.md`).
These constrain columns in the existing `obligations` and `negotiation_issues` tables that are
currently unconstrained free text, which is why the LLM invents a new label on every run.
The enums are defined here in TASK-01; the columns are constrained in TASK-03.

```python
class ObligationType(str, Enum):
    PERFORMANCE  = "PERF"   # delivery of work, services, or goods
    PAYMENT      = "PAY"    # monetary transfers
    NOTICE       = "NOTC"   # required communications or notifications
    APPROVAL     = "APPR"   # required consent or sign-off actions
    REPORTING    = "RPT"    # submission of information or documentation
    INSURANCE    = "INS"    # maintenance of insurance coverage
    COMPLIANCE   = "COMP"   # adherence to laws, regulations, standards
    RESTRICTIVE  = "REST"   # abstention from specified activities
    CONDITIONAL  = "COND"   # triggered only if a specific event occurs
    SURVIVAL     = "SURV"   # continues after contract termination

class TriggerType(str, Enum):
    CALENDAR   = "calendar"    # fixed date or recurring schedule
    EVENT      = "event"       # an occurrence activates it
    CONDITION  = "condition"   # depends on a condition being met
    MILESTONE  = "milestone"   # tied to a project phase or deliverable
    ROLLING    = "rolling"     # calculated from a variable start point
    CONTINUOUS = "continuous"  # ongoing throughout the term
    NEGATIVE   = "negative"    # triggered by FAILURE to act (auto-renewal,
                               # deemed acceptance, time-barred claims)

class NegotiationPriority(str, Enum):
    MUST_CHANGE    = "must_change"     # dealbreaker — do not sign without this
    SHOULD_CHANGE  = "should_change"   # negotiate hard, but tradeable
    NICE_TO_CHANGE = "nice_to_change"  # raise only if leverage permits
```

### 3. `core/schemas.py` (new)

Pydantic v2. `model_config = ConfigDict(extra="forbid")` on every model.

**`Provenance`** — the most important model in the system.
- `created_by: Actor`
- `agent_name: str | None = None` — e.g. `"analysis_engine"` when AI, `"jason"` when HUMAN
- `model: str | None = None` — e.g. the LM Studio model id
- `source_document_id: str | None = None`
- `source_location: str | None = None` — e.g. `"Clause 14.2, p.31"`
- `created_at: datetime` — default `datetime.now(UTC)`
- `human_confirmed: bool = False`
- `confirmed_by: str | None = None`
- `confirmed_at: datetime | None = None`

Validator: if `human_confirmed is True`, then `confirmed_by` must be non-None → else `ValueError`.
Add a classmethod `Provenance.from_ai(agent_name: str, model: str, ...)` and `Provenance.from_human(who: str)` as convenience constructors.

**`Bid`**
- `bid_id: str` — pattern `^B-\d{4}-\d{4}$`
- `customer: str`
- `customer_type: CustomerType`
- `project_name: str`
- `location: str | None = None`
- `sales_owner: str`
- `bc_owner: str`
- `executive_sponsor: str | None = None`
- `release_date: date`
- `customer_due_date: date`
- `internal_due_date: date`
- `anticipated_award_date: date | None = None`
- `estimated_value: Decimal`
- `currency: str = "CAD"`
- `margin_range: str | None = None`
- `win_probability: int | None = None` (ge=0, le=100)
- `classification: BidLevel`
- `current_gate: Gate = Gate.G0`
- `status: BidStatus = BidStatus.ACTIVE`
- `risk_triggers: list[RiskTrigger] = []`
- `inference_policy: InferencePolicy = InferencePolicy.LOCAL_ONLY`
- `created_at: datetime`
- `updated_at: datetime`

Validator: `internal_due_date <= customer_due_date` → else `ValueError`.

**`Approval`**
- `approval_id: str`, `bid_id: str`, `approval_type: ApprovalType`, `required: bool = True`, `obtained: bool = False`, `authority: str | None = None`, `evidence_ref: str | None = None`, `decision: str | None = None`, `decided_at: datetime | None = None`, `provenance: Provenance`

**`GateRecord`**
- `bid_id: str`, `gate: Gate`, `status: GateStatus = GateStatus.NOT_STARTED`, `blockers: list[str] = []`, `override_by: str | None = None`, `override_risk_note: str | None = None`, `decided_at: datetime | None = None`

Validator: if `status == GateStatus.OVERRIDDEN`, both `override_by` and `override_risk_note` must be non-None → else `ValueError`. This enforces the rule that an override must always record the residual risk.

**`AuditEntry`**
- `entry_id: str`, `bid_id: str | None`, `actor: str`, `action: str`, `detail: str`, `timestamp: datetime`

### 4. `tests/conftest.py` (new)

Fixtures: `valid_provenance`, `valid_bid`, and `tmp_db` (an in-memory or tmp-file SQLite initialised via the existing `core/database.py` init function — read that file to find the right entry point).

### 5. `tests/unit/test_schemas.py` (new)

At minimum:
1. Every model instantiates from valid minimal data.
2. `Bid` with `internal_due_date > customer_due_date` → `ValidationError`.
3. `Bid` with malformed `bid_id` (e.g. `"B-26-42"`) → `ValidationError`.
4. `Bid` with `win_probability=101` → `ValidationError`.
5. `Bid` defaults to `InferencePolicy.LOCAL_ONLY`.
6. `Provenance(human_confirmed=True, confirmed_by=None)` → `ValidationError`.
7. `Provenance.from_ai(...)` yields `created_by=AI` and `human_confirmed=False`.
8. `GateRecord(status=OVERRIDDEN)` without `override_risk_note` → `ValidationError`.
9. Unknown field on any model → `ValidationError` (proves `extra="forbid"`).
10. `PillarId` members exactly match the `pillar_id` values in `core/pillars.py` — import `ALL_PILLARS` and assert set equality. **This test is a guard against the enum drifting from the domain model.**
11. `ObligationType` has exactly 10 members; `TriggerType` has exactly 7; `NegotiationPriority` has exactly 3. (Guards against accidental deletion of salvaged taxonomies.)
12. `ObligationType.PERFORMANCE.value == "PERF"` — the short codes are the stored values, not the member names.

### 6. `tests/unit/test_pillars.py` (new — characterisation test on existing code)

Prove the harness works against real existing code. Import from `core/pillars.py`:
- `ALL_PILLARS` has exactly 7 members.
- Every pillar has a non-empty `key_questions`, `red_flag_patterns`, `missing_protection_patterns`.
- Every pillar's `weight_by_doc_type` values are floats in `(0, 1]`.
- For each document type present in the weightings, assert the weights across all 7 pillars sum to approximately 1.0 (tolerance 0.05). **If this fails, do not "fix" pillars.py — report the actual sums in `HANDOFF.md`.** This is a finding, not a bug to silently patch.

### 7. `tests/unit/test_llm_client.py` (new — mocked, no network)

Test `core/llm_client.py` `_parse_json_response` in isolation:
- Plain JSON parses.
- JSON wrapped in ```` ```json ```` fences parses.
- JSON with leading prose (`"Here is the result: {...}"`) parses.
- Malformed JSON returns the `{"error": ..., "raw_response": ...}` shape rather than raising.

**Use `unittest.mock` to patch `requests`. No test may hit the network — including `10.0.0.10`.**

### 8. `.github/workflows/ci.yml` (new)

On push and PR: Python 3.11, `pip install -e ".[dev]"`, then run `ruff check .`, `mypy`, `pytest -v`. CI must pass.

### 9. `.gitignore` — verify and extend

Ensure it contains at minimum: `.env`, `data/`, `*.db`, `*.sqlite3`, `uploads/`, `outputs/`, `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `venv/`, `.venv/`, `*.egg-info/`.

**Critical:** confirm no `.db` file, uploaded document, or generated report is currently tracked in git. If any is, report it in `HANDOFF.md` — **do not delete it yourself**, flag it for Jason.

---

## Expected outcome

- `pip install -e ".[dev]"` succeeds.
- `pytest -v` passes.
- `ruff check .` passes.
- `mypy` passes on `core/schemas.py` and `core/enums.py`.
- CI green.
- **`python app.py` still starts and behaves exactly as before.** No existing behaviour changed.

## Validation command

```bash
pip install -e ".[dev]" && \
ruff check . && \
mypy && \
pytest -v && \
python -c "import app; print('app imports OK')"
```

Paste the full output into `HANDOFF.md`.

## Out of scope — do NOT implement

- The `bids` table or any migration (TASK-02)
- Provenance columns on existing tables (TASK-03)
- Classifier, gates, readiness engine (TASK-04/05/06)
- Any change to `analysis_engine.py`, `database.py`, `app.py`, `pillars.py`, or any other existing module
- Any refactor of existing code, however tempting

## Reporting requirements

In `HANDOFF.md`, additionally report:
- The actual doc-type weight sums from `test_pillars.py` (whether or not they sum to 1.0).
- Whether any database, upload, or report artifact is currently tracked in git.
- Any existing module that would fail `mypy --strict` badly enough to matter later.
