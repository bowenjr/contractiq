"""
ContractIQ - Personal Contract & Bid Analysis System
7-Pillar analysis engine with background processing and live progress.
"""

import base64
import binascii
import hashlib
import hmac
import json
import os
import sqlite3
import uuid
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath
from typing import Annotated
from urllib.parse import parse_qsl, quote, urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import uvicorn
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import ValidationError
from starlette.datastructures import UploadFile as StarletteUploadFile

from core.analysis_engine import AnalysisEngine
from core.approval_authority import (
    ApprovalEvent,
    AuthorityPolicy,
    DecisionCase,
    DecisionPackage,
    DecisionType,
    RouteCycle,
    SubjectLink,
)
from core.approval_repository import ApprovalRepository
from core.approval_service import ApprovalService
from core.bid_control_center import (
    GATE_LABELS,
    BidControlCenterService,
    BidDeadlineAttention,
    BidNotFoundError,
    BidPortfolioFilters,
    BidPortfolioRow,
    BidPortfolioView,
    BidReadinessFilter,
    BidWorkspaceAttention,
    BidWorkspaceSection,
    MyDayBidBucket,
    build_bid_portfolio,
    governance_level_guides,
    outstanding_items,
    workspace_path,
)
from core.bid_package_intake import (
    AcknowledgementCreate,
    AcknowledgementEventType,
    AnalysisEligibility,
    BulkFileDispositionCreate,
    BulkFileReviewItem,
    ChannelCheckCreate,
    ClassificationMethod,
    ContentForm,
    DirectiveCreate,
    DirectiveDispositionCreate,
    DirectiveDispositionStatus,
    DirectiveMateriality,
    FileDispositionCreate,
    FileDocumentLinkCreate,
    FileDocumentRelationship,
    NoticeExpectation,
    ReleaseChannel,
    ReleaseNoticeCreate,
    ReleasePreview,
    ReleaseRegistration,
    ReleaseType,
    SnapshotCreate,
)
from core.bid_package_presentation import (
    DIRECTIVE_TYPE_CHOICES,
    content_form_label,
    decorate_release,
    directive_type_label,
    eligibility_label,
    human_bytes,
)
from core.bid_package_repository import (
    BidPackageRepository,
    IntakeConflictError,
    IntakeNotFoundError,
    ReleaseNotIncorporableError,
    StaleIntakeError,
)
from core.bid_package_service import BidPackageService
from core.bid_package_storage import (
    BidPackageStorage,
    IntakeLimitError,
    IntakePublicationError,
    SourceChangedError,
    UnsafeIntakeSourceError,
    parse_intake_roots,
)
from core.bid_repository import BidRepository
from core.bid_workflow import (
    PackageIntakeFacts,
    intake_heading,
    project_bid_workflow,
    proposal_heading,
)
from core.commercial import (
    AssessmentVersion,
    BasisRole,
    CommercialCategory,
    CommercialItem,
    CommercialLink,
    CommercialReview,
)
from core.commercial_repository import CommercialRepository
from core.commercial_scenarios import (
    BaselineSelection,
    ScenarioFamily,
    ScenarioReview,
    ScenarioVersion,
)
from core.commercial_service import CommercialService
from core.contract_risk import (
    ContractIssue,
    RiskAssessment,
    RiskCategory,
    RiskLink,
    RiskReview,
    RiskSource,
)
from core.contract_risk_repository import ContractRiskRepository
from core.contract_risk_service import ContractRiskService
from core.database import Database
from core.deliverable_repository import DeliverableRepository
from core.deliverable_service import DeliverableService
from core.deliverables import (
    Deliverable,
    DeliverableCriticality,
    DeliverableDirection,
    DeliverableLink,
    LifecyclePhase,
    ReviewDecisionRecord,
    SubmissionVersion,
    SupplierCommitment,
)
from core.document_control import (
    ControlledDocumentIdentityError,
    ControlledDocumentIntegrityError,
    DocumentCategory,
    DocumentLifecycle,
    document_category_label,
)
from core.document_preprocessor import DocumentPreprocessor
from core.document_processor import DocumentProcessor
from core.document_repository import (
    ControlledDocumentNotFoundError,
    DocumentRepository,
    DocumentStoreBusyError,
    DocumentVersionNotFoundError,
    DuplicateDocumentVersionError,
    StaleDocumentError,
)
from core.document_service import DocumentService
from core.enums import (
    ApprovalType,
    BidLevel,
    BidStatus,
    CustomerType,
    Gate,
    InferencePolicy,
    RiskTrigger,
)
from core.excel_generator import ExcelGenerator
from core.handover import BidHandoverService, assess_manufacturer_handover
from core.knowledge_bootstrap import bootstrap_knowledge
from core.knowledge_engine import KnowledgeEngine
from core.knowledge_io import KnowledgeIO
from core.llm_client import LMStudioClient
from core.managed_document_storage import (
    EmptyManagedFileError,
    ManagedDocumentStorage,
    ManagedFileTooLargeError,
    ManagedStorageFailureError,
)
from core.my_day import (
    MyDayProjection,
    ProjectedWorkItem,
    WorkItemSnapshot,
    work_item_order_key,
)
from core.negotiation import (
    Concession,
    ConditionalTrade,
    Mandate,
    NegotiationMovement,
    NegotiationPlan,
    PlanVersion,
)
from core.negotiation_repository import NegotiationRepository
from core.negotiation_service import NegotiationService
from core.ops07 import (
    BulkRequirementTarget as Ops07BulkTarget,
)
from core.ops07 import (
    BulkResponsibilityAssignment,
    Ops07Repository,
)
from core.ops07w import (
    ClassificationAssessmentCommand,
    Ops07WorkflowRepository,
    build_navigator,
)
from core.ops08 import NegotiationState as CommercialNegotiationState
from core.ops08 import Ops08Repository, PositionDisposition, PositionRevision
from core.ops_foundation import (
    RESPONSIBILITY_DOMAINS,
    OpsFoundationRepository,
    RoleProfileNotFoundError,
    RoleProfileOverlapError,
    StaleRoleProfileError,
)
from core.proposal_exchange import CustomerIssueCommand
from core.proposal_exchange_service import (
    ProposalExchangeNotFoundError,
    ProposalExchangeService,
    ProposalImportError,
    StaleProposalCandidateError,
)
from core.proposal_repository import ProposalRepository
from core.proposal_service import ProposalService
from core.proposals import (
    ProposalApplicability,
    ProposalFamily,
    ProposalProfile,
    ProposalReview,
    ProposalVersion,
)
from core.readiness_service import evaluate_readiness
from core.report_generator import ReportGenerator
from core.requirement_browser import RequirementBrowserCommand
from core.requirement_repository import (
    RequirementNotFoundError,
    RequirementRepository,
    RequirementSourceError,
    StaleRequirementError,
)
from core.requirement_service import RequirementService
from core.requirements import (
    REQUIREMENT_SIGNIFICANCE_LABELS,
    RequirementCategory,
    RequirementLifecycle,
    RequirementOrigin,
    RequirementReviewState,
    RequirementSignificance,
    RequirementStage,
    RequirementWorkState,
    ResponseDisposition,
)
from core.role_profile_service import RoleProfileService
from core.role_profiles import ROLE_PROFILE_STATE_LABELS, RoleProfile
from core.scenario_repository import ScenarioRepository
from core.scenario_service import ScenarioService
from core.schemas import Approval, Bid, Provenance
from core.scope_interfaces import (
    CustomerNeed,
    DependencyState,
    InterfaceRecord,
    Materiality,
    OfferPosition,
    PricingState,
    ScopeArea,
    ScopeItem,
    ScopeOrigin,
)
from core.scope_repository import ScopeInterfaceRepository
from core.scope_service import ScopeInterfaceService
from core.supplier_assurance import (
    Coverage,
    FlowDownLink,
    RequestItem,
    ResponseVersion,
    ReviewState,
    Supplier,
    SupplierRequest,
)
from core.supplier_repository import SupplierRepository
from core.supplier_service import SupplierService
from core.vendor_document_control import (
    BidDisposition,
    BulkRequirementTarget,
    BulkVerificationUpdate,
    CommercialImpact,
    CustomerRequirementCreate,
    RequirementVerificationUpdate,
    SupplierPackageCreate,
    TimingAnchor,
    VerificationStatus,
)
from core.vendor_document_repository import (
    VendorDocumentNotFoundError,
    VendorDocumentRepository,
)
from core.vendor_document_service import VendorDocumentService
from core.work_item_repository import (
    StaleWorkItemError,
    WorkItemNotFoundError,
    WorkItemRepository,
)
from core.work_item_service import MyDayService, WorkItemService, validation_error_message
from core.work_items import (
    RESPONSIBILITY_DOMAIN_LABELS,
    WAITING_PARTY_KIND_LABELS,
    WORK_CATEGORY_LABELS,
    WORK_ITEM_STATUS_LABELS,
    ResponsibilityDomain,
    WaitingPartyKind,
    WorkAttentionFilter,
    WorkCategory,
    WorkContextFilter,
    WorkItem,
    WorkItemPriority,
    WorkItemStatus,
    WorkRegisterFilter,
    WorkRegisterView,
)

# ── App Setup ──────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
APP_CONFIG = (
    json.loads((BASE_DIR / "config.json").read_text())
    if (BASE_DIR / "config.json").exists()
    else {}
)
try:
    WORKING_TIMEZONE = ZoneInfo(APP_CONFIG.get("working_timezone", "America/Toronto"))
except ZoneInfoNotFoundError as exc:
    raise RuntimeError("config.json contains an invalid working_timezone") from exc
LOCAL_ACTOR = str(APP_CONFIG.get("local_actor", "local_user"))
_REQUIREMENT_FORM_STATE_TTL_SECONDS = 3600
_requirement_form_states: dict[str, tuple[datetime, dict[str, str]]] = {}
_commercial_form_states: dict[str, tuple[datetime, dict[str, str]]] = {}
UPLOADS_DIR = BASE_DIR / "uploads"
REPORTS_DIR = BASE_DIR / "reports"
_managed_root_setting = Path(
    os.environ.get(
        "CONTRACTIQ_DOCUMENT_ROOT",
        str(APP_CONFIG.get("managed_document_root", "managed_documents")),
    )
)
MANAGED_DOCUMENT_ROOT = (
    _managed_root_setting
    if _managed_root_setting.is_absolute()
    else BASE_DIR / _managed_root_setting
)
MAX_MANAGED_DOCUMENT_BYTES = int(
    os.environ.get(
        "CONTRACTIQ_MAX_DOCUMENT_BYTES",
        str(APP_CONFIG.get("max_managed_document_bytes", 52_428_800)),
    )
)
UPLOADS_DIR.mkdir(exist_ok=True)
REPORTS_DIR.mkdir(exist_ok=True)

app = FastAPI(title="ContractIQ", version="2.0.0")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.mount("/reports", StaticFiles(directory=str(REPORTS_DIR)), name="reports")

jinja_env = Environment(
    loader=FileSystemLoader(str(BASE_DIR / "templates")),
    autoescape=select_autoescape(["html"]),
)
# One negative-heading vocabulary for every surface that lists something missing.
jinja_env.globals["intake_heading"] = intake_heading
jinja_env.globals["proposal_heading"] = proposal_heading


def render(template_name: str, status_code: int = 200, **context) -> HTMLResponse:
    template = jinja_env.get_template(template_name)
    return HTMLResponse(template.render(**context), status_code=status_code)


# ── Core Services ───────────────────────────────────────────────────────────
db = Database(Path(os.environ.get("CONTRACTIQ_DB_PATH", BASE_DIR / "data" / "contractiq.db")))


def _source_operation_key() -> bytes:
    """Return a stable deployment-local key without persisting a new domain or schema."""
    identity = f"contractiq-ops07w-source-operation-v1\0{Path(db.db_path).resolve()}"
    return hashlib.sha256(identity.encode()).digest()


def _new_source_operation_token(bid_id: str) -> str:
    payload = json.dumps(
        {"bid_id": bid_id, "operation_id": str(uuid.uuid4())},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    signature = hmac.digest(_source_operation_key(), payload, "sha256")
    return base64.urlsafe_b64encode(payload + signature).decode().rstrip("=")


def _source_operation_id(token: str, bid_id: str) -> str:
    try:
        padded = token + "=" * (-len(token) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode())
        canonical_token = base64.urlsafe_b64encode(decoded).decode().rstrip("=")
        if not hmac.compare_digest(token, canonical_token):
            raise ValueError
        payload, signature = decoded[:-32], decoded[-32:]
        if len(signature) != 32 or not hmac.compare_digest(
            signature, hmac.digest(_source_operation_key(), payload, "sha256")
        ):
            raise ValueError
        values = json.loads(payload)
        operation_id = str(values["operation_id"])
        if values.get("bid_id") != bid_id or str(uuid.UUID(operation_id)) != operation_id:
            raise ValueError
        return operation_id
    except (ValueError, KeyError, TypeError, json.JSONDecodeError, binascii.Error) as exc:
        raise ValueError("Source registration operation is invalid for this Bid") from exc


bid_repository = BidRepository(db)
work_item_repository = WorkItemRepository(db)
work_item_service = WorkItemService(work_item_repository, bid_repository)
ops_repository = OpsFoundationRepository(db)
role_profile_service = RoleProfileService(ops_repository)
document_repository = DocumentRepository(db)
requirement_repository = RequirementRepository(db)
requirement_service = RequirementService(
    requirement_repository,
    bid_repository,
    document_repository,
)
scope_repository = ScopeInterfaceRepository(db)
scope_service = ScopeInterfaceService(scope_repository)
supplier_repository = SupplierRepository(db)
supplier_service = SupplierService(db, supplier_repository)
deliverable_repository = DeliverableRepository(db)
deliverable_service = DeliverableService(deliverable_repository)
vendor_document_repository = VendorDocumentRepository(db)
vendor_document_service = VendorDocumentService(vendor_document_repository, bid_repository)
vendor_document_service.ensure_standard_template()
ops07_repository = Ops07Repository(db)
ops07w_repository = Ops07WorkflowRepository(db)
bid_handover_service = BidHandoverService(db)
commercial_repository = CommercialRepository(db)
commercial_service = CommercialService(commercial_repository)
ops08_repository = Ops08Repository(db)
contract_risk_repository = ContractRiskRepository(db)
contract_risk_service = ContractRiskService(contract_risk_repository)
approval_repository = ApprovalRepository(db)
approval_service = ApprovalService(approval_repository)
scenario_repository = ScenarioRepository(db)
scenario_service = ScenarioService(scenario_repository)
negotiation_repository = NegotiationRepository(db)
negotiation_service = NegotiationService(negotiation_repository)
proposal_repository = ProposalRepository(db)
proposal_service = ProposalService(proposal_repository, BASE_DIR / "proposal_artifacts")
_intake_roots_setting = os.environ.get("CONTRACTIQ_INTAKE_ROOTS")
_legacy_intake_roots_setting = os.environ.get("CONTRACTIQ_INTAKE_SOURCE_ROOTS")
if _intake_roots_setting is not None and _legacy_intake_roots_setting is not None:
    raise RuntimeError(
        "Configure CONTRACTIQ_INTAKE_ROOTS only; "
        "CONTRACTIQ_INTAKE_SOURCE_ROOTS is a compatibility alias."
    )
if _intake_roots_setting is not None:
    INTAKE_SOURCE_ROOTS = parse_intake_roots(
        _intake_roots_setting,
        variable_name="CONTRACTIQ_INTAKE_ROOTS",
    )
elif _legacy_intake_roots_setting is not None:
    INTAKE_SOURCE_ROOTS = parse_intake_roots(
        _legacy_intake_roots_setting,
        variable_name="CONTRACTIQ_INTAKE_SOURCE_ROOTS",
    )
else:
    _default_intake_source = Path(db.db_path).resolve().parent / "intake_landing"
    _default_intake_source.mkdir(parents=True, exist_ok=True)
    INTAKE_SOURCE_ROOTS = {"default": _default_intake_source}
_intake_root_setting = Path(
    os.environ.get(
        "CONTRACTIQ_INTAKE_STORAGE_ROOT",
        str(Path(db.db_path).resolve().parent / "managed_intake"),
    )
)
MANAGED_INTAKE_ROOT = (
    _intake_root_setting if _intake_root_setting.is_absolute() else BASE_DIR / _intake_root_setting
)
bid_package_repository = BidPackageRepository(db)
bid_package_storage = BidPackageStorage(
    MANAGED_INTAKE_ROOT,
    INTAKE_SOURCE_ROOTS,
    max_outer_files=int(APP_CONFIG.get("max_intake_outer_files", 1_000)),
    max_total_bytes=int(APP_CONFIG.get("max_intake_total_bytes", 1_073_741_824)),
    max_file_bytes=int(APP_CONFIG.get("max_intake_file_bytes", 262_144_000)),
    max_relative_path_length=int(APP_CONFIG.get("max_intake_relative_path_length", 2_048)),
    max_path_depth=int(APP_CONFIG.get("max_intake_path_depth", 32)),
    quarantine_days=int(APP_CONFIG.get("intake_quarantine_days", 7)),
)
bid_package_service = BidPackageService(
    bid_package_repository,
    bid_repository,
    bid_package_storage,
    work_item_service,
)
_proposal_artifact_setting = Path(
    os.environ.get(
        "CONTRACTIQ_PROPOSAL_ARTIFACT_ROOT",
        str(BASE_DIR / "data" / "proposal_exchange_artifacts"),
    )
)
proposal_exchange_service = ProposalExchangeService(
    db,
    bid_repository,
    _proposal_artifact_setting,
    intake_blocker_loader=bid_package_repository.issue_blockers,
)
bid_handover_service = BidHandoverService(
    db,
    proposal_issue_loader=proposal_exchange_service.handover_rows,
)
my_day_service = MyDayService(
    work_item_repository,
    bid_repository,
    db,
    requirement_repository=requirement_repository,
)


def _load_bid_workspace_attention(bid_id: str, as_of: date) -> BidWorkspaceAttention:
    approval_attention = [
        {
            "bid_id": bid_id,
            "entity_id": str(row["case_id"]),
            "code": "APPROVAL_PENDING",
            "severity": "HIGH",
        }
        for row in approval_repository.cases(bid_id)
        if row["lifecycle_state"] in {"ACTIVE", "DRAFT"}
    ]
    if "ops08_repository" in globals():
        approval_attention.extend(
            {
                "bid_id": bid_id,
                "entity_id": str(row["position_id"]),
                "code": "COMMERCIAL_POSITION_BLOCKED",
                "severity": "HIGH",
            }
            for row in ops08_repository.workspace(bid_id)
            if row["disposition"] in {"NOT_REVIEWED", "QUALIFY", "CLARIFICATION_REQUIRED", "REJECT"}
        )
    supplier_attention = [
        {
            "bid_id": gap.bid_id,
            "entity_id": gap.entity_id,
            "code": gap.code,
            "severity": gap.severity,
        }
        for gap in supplier_service.gaps(bid_id, as_of)
    ]
    return BidWorkspaceAttention(
        approval_attention=approval_attention,
        supplier_attention=supplier_attention,
    )


bid_control_center_service = BidControlCenterService(
    bid_repository,
    work_item_repository,
    lambda as_of: my_day_service.get_my_day(as_of=as_of),
    lambda bid_id: evaluate_readiness(bid_repository, db, bid_id),
    _load_bid_workspace_attention,
)
managed_document_storage = ManagedDocumentStorage(
    MANAGED_DOCUMENT_ROOT,
    MAX_MANAGED_DOCUMENT_BYTES,
)
document_service = DocumentService(
    document_repository,
    bid_repository,
    managed_document_storage,
)
doc_processor = DocumentProcessor()
llm_client = LMStudioClient()
analysis_engine = AnalysisEngine(llm_client, db)
preprocessor = DocumentPreprocessor(llm_client)
report_generator = ReportGenerator(REPORTS_DIR)
excel_generator = ExcelGenerator(REPORTS_DIR)

# ── Startup recovery ─────────────────────────────────────────────────────────


def recover_stuck_documents() -> None:
    """Reset any documents left in 'processing' state from a previous server session."""
    stuck = db.get_documents_by_status("processing")
    for doc in stuck:
        db.update_document(
            doc["id"],
            {
                "status": "interrupted",
                "error_message": (
                    "Analysis was interrupted — server restarted or connection lost. "
                    "Click Analyse to retry."
                ),
            },
        )
        print(f"  Recovered stuck document: {doc['filename']}")


@app.on_event("startup")
async def startup_event():
    staged_count, orphan_count = bid_package_storage.recover(
        bid_package_repository.managed_release_directories()
    )
    if staged_count or orphan_count:
        print(
            "  Bid package intake recovery: "
            f"{staged_count} interrupted staging, "
            f"{orphan_count} orphaned publication(s) quarantined"
        )
    recover_stuck_documents()
    print("  Recovery check complete")

    # Bootstrap knowledge base (only seeds if tables are empty)
    inserted = bootstrap_knowledge(db)
    if any(inserted.values()):
        total = sum(inserted.values())
        print(f"  Knowledge Base bootstrapped: {total} rows seeded across {len(inserted)} tables")

    # Print knowledge base stats
    ke = KnowledgeEngine(db)
    stats = ke.get_knowledge_summary()
    print(
        f"  Knowledge Base: "
        f"{stats.get('company_positions', 0)} positions, "
        f"{stats.get('escalation_rules', 0)} escalation rules, "
        f"{stats.get('product_profiles', 0)} product profiles, "
        f"{stats.get('commercial_terms', 0)} commercial terms"
    )


# ── Progress store ────────────────────────────────────────────────────────────
# Keyed by document_id. Structure per entry:
#   { "step_num": int, "total_steps": int,
#     "step_name": str, "message": str, "percent": int,
#     "completed_steps": [{"step_num": int, "step_name": str}],
#     "error": str|null, "review_priority": str|null }
progress_store: dict = {}

# ── Cancellation store ────────────────────────────────────────────────────────
# Set of document IDs for which cancellation has been requested
cancel_requests: set = set()


def _run_analysis_background(doc_id: str) -> None:
    """Synchronous worker — FastAPI runs this in a thread-pool via BackgroundTasks."""
    document = db.get_document(doc_id)
    if not document:
        progress_store[doc_id] = {
            "step_num": 0,
            "total_steps": 6,
            "step_name": "Error",
            "message": "Document not found",
            "percent": 0,
            "completed_steps": [],
            "error": "Document not found",
            "review_priority": None,
        }
        return

    seen_steps: dict = {}  # step_num → step_name

    def _progress_cb(
        step_num: int, total_steps: int, step_name: str, message: str, percent: int
    ) -> None:
        seen_steps[step_num] = step_name
        completed = [
            {"step_num": k, "step_name": v} for k, v in sorted(seen_steps.items()) if k < step_num
        ]
        progress_store[doc_id] = {
            "step_num": step_num,
            "total_steps": total_steps,
            "step_name": step_name,
            "message": message,
            "percent": percent,
            "completed_steps": completed,
            "error": None,
            "review_priority": None,
        }

    def _check_cancel() -> bool:
        return doc_id in cancel_requests

    try:
        raw_text = document["raw_text"]
        doc_type_hint = document.get("doc_type", "General Contract")

        # ── Stage 1: Pure-Python pre-processing (no LLM, instant) ────────────
        pre = preprocessor.preprocess(
            raw_text,
            document["filename"],
            doc_type=doc_type_hint,
        )
        structured_md = pre["structured_markdown"]
        contractual_items = pre["contractual_items"]  # always []
        section_count = pre["section_count"]
        noise_pct = pre["noise_removed_pct"]
        word_count = pre["word_count"]

        # Persist markdown immediately so /api/document/{id}/markdown works
        db.update_document(
            doc_id,
            {
                "structured_markdown": structured_md,
                "contractual_items_json": json.dumps(contractual_items),
            },
        )

        print(
            f"  Pre-processing complete: {section_count} sections | "
            f"{word_count:,} words | {noise_pct}% noise removed"
        )
        _progress_cb(
            1,
            6,
            "Pre-processing",
            f"Pre-processing complete — {section_count} sections detected, "
            f"{noise_pct}% noise removed, {word_count:,} words",
            5,
        )

        # ── Stage 2: 7-Pillar LLM analysis (section-routed) ──────────────────
        results = analysis_engine.run_full_analysis(
            raw_text,
            document["filename"],
            preprocessed=pre,
            progress_callback=_progress_cb,
            cancel_check=_check_cancel,
        )

        # Generate PDF report
        pdf_filename = f"report_{doc_id}.pdf"
        pdf_path = REPORTS_DIR / pdf_filename
        report_generator.generate(document, results, pdf_path)

        # Generate Excel workbook
        xlsx_filename = f"report_{doc_id}.xlsx"
        xlsx_path = REPORTS_DIR / xlsx_filename
        excel_generator.generate(document, results, xlsx_path)

        # Generate tracker sheet (contract-item-level working document)
        print("  Pre-processing: Generating tracker sheet...")
        tracker_filename = f"tracker_{doc_id}.xlsx"
        tracker_path = REPORTS_DIR / tracker_filename
        preprocessor.generate_tracker_sheet(document, contractual_items, results, tracker_path)

        # Save structured findings to relational tables
        pillar_results = results.get("pillars", [])
        if pillar_results:
            db.save_clause_findings(doc_id, pillar_results)
            db.save_negotiation_issues(doc_id, pillar_results)

        obligations = results.get("obligations", [])
        if obligations:
            db.save_obligations(doc_id, obligations)

        # Save report package record
        db.save_report_package(doc_id, pdf_filename, xlsx_filename, datetime.now().isoformat())

        # Update document record
        rp = results.get("review_priority", {})
        db.update_document(
            doc_id,
            {
                "status": "complete",
                "analysis_date": datetime.now().isoformat(),
                "analysis_json": json.dumps(results),
                "pdf_report_path": pdf_filename,
                "excel_report_path": xlsx_filename,
                "review_priority": rp.get("review_priority", "Unknown"),
                "critical_flag_count": rp.get("critical_flag_count", 0),
                "high_flag_count": rp.get("high_flag_count", 0),
                "negotiation_points_count": rp.get("negotiation_points_count", 0),
                "doc_type": results.get("doc_type", "General Contract"),
                "doc_type_confidence": results.get("doc_type_confidence", "Low"),
                "executive_summary": results.get("executive_summary", ""),
                "key_subject": results.get("key_subject", ""),
                "contract_value": results.get("contract_value", ""),
                "contract_duration": results.get("contract_duration", ""),
                "governing_law": results.get("governing_law", ""),
                "counterparty": results.get("counterparty", ""),
                "tracker_path": tracker_filename,
            },
        )

        # Build completed_steps from all 6 steps
        all_completed = [{"step_num": k, "step_name": v} for k, v in sorted(seen_steps.items())]
        progress_store[doc_id] = {
            "step_num": 7,
            "total_steps": 6,
            "step_name": "Complete",
            "message": "Analysis complete",
            "percent": 100,
            "completed_steps": all_completed,
            "error": None,
            "review_priority": rp.get("review_priority", "Unknown"),
        }

    except InterruptedError:
        cancel_requests.discard(doc_id)
        completed = [{"step_num": k, "step_name": v} for k, v in sorted(seen_steps.items())]
        db.update_document(
            doc_id,
            {
                "status": "cancelled",
                "error_message": "Analysis cancelled by user",
            },
        )
        progress_store[doc_id] = {
            "step_num": 0,
            "total_steps": 6,
            "step_name": "Cancelled",
            "message": "Analysis cancelled by user",
            "percent": 0,
            "completed_steps": completed,
            "error": "Analysis cancelled by user",
            "review_priority": None,
        }

    except Exception as e:
        import traceback

        print(f"[ERROR] Analysis failed for {doc_id}: {e}")
        traceback.print_exc()
        db.update_document(doc_id, {"status": "error", "error_message": str(e)})
        progress_store[doc_id] = {
            "step_num": 0,
            "total_steps": 6,
            "step_name": "Error",
            "message": str(e),
            "percent": 0,
            "completed_steps": [
                {"step_num": k, "step_name": v} for k, v in sorted(seen_steps.items())
            ],
            "error": str(e),
            "review_priority": None,
        }


# ── Routes ───────────────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    """Open the role-aligned operational home without a second dashboard."""
    return await my_day(request)


def _working_date() -> date:
    """Return today's calendar date in the configured working timezone."""
    return datetime.now(WORKING_TIMEZONE).date()


def _parse_as_of(value: str | None) -> date:
    if value is None:
        return _working_date()
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="as_of must use YYYY-MM-DD") from exc


# One Bid vocabulary, used by every workspace tab, breadcrumb and return link.
BID_SECTION_LABELS: dict[BidWorkspaceSection, str] = {
    BidWorkspaceSection.OVERVIEW: "Bid setup",
    BidWorkspaceSection.PACKAGE_INTAKE: "Package intake",
    BidWorkspaceSection.REQUIREMENTS_SCOPE: "Requirements and scope",
    BidWorkspaceSection.MANUFACTURERS_COVERAGE: "Manufacturers and supplier coverage",
    BidWorkspaceSection.COMMERCIAL_CONTRACT: "Commercial, contract risk and approvals",
    BidWorkspaceSection.PROPOSAL_NEGOTIATION: "Proposal and negotiation",
    BidWorkspaceSection.AWARD_HANDOVER: "Bid Basis and handover",
}


def _bid_return_context(
    bid_id: str | None,
    section: BidWorkspaceSection,
) -> dict[str, object]:
    bid = bid_repository.get_bid(bid_id) if bid_id else None
    context: dict[str, object] = {
        "bid_context": bid,
        "bid_return_path": workspace_path(bid.bid_id, section) if bid else None,
        "bid_section_label": BID_SECTION_LABELS.get(
            section,
            section.value.replace("-", " ").capitalize(),
        ),
    }
    return context


def _position_context(position_id: str | None, bid_id: str) -> dict[str, object] | None:
    """Resolve optional commercial context and reject stale or cross-Bid links."""
    if not position_id:
        return None
    position = ops08_repository.detail(position_id)
    if str(position["bid_id"]) != bid_id:
        raise ValueError("Commercial topic context must belong to this Bid")
    return position


def _position_link_counts(bid_id: str) -> dict[str, dict[str, int]]:
    with db._conn() as conn:
        rows = conn.execute(
            """SELECT r.position_id,
            SUM(CASE WHEN r.risk_issue_id IS NOT NULL THEN 1 ELSE 0 END) risk_count,
            SUM(CASE WHEN r.decision_case_id IS NOT NULL
                OR r.approval_route_id IS NOT NULL THEN 1 ELSE 0 END) approval_count,
            SUM(CASE WHEN r.negotiation_plan_id IS NOT NULL THEN 1 ELSE 0 END) negotiation_count
            FROM commercial_position_relationships r
            WHERE r.bid_id=? GROUP BY r.position_id""",
            (bid_id,),
        ).fetchall()
    return {
        str(row["position_id"]): {
            "risk_count": int(row["risk_count"] or 0),
            "approval_count": int(row["approval_count"] or 0),
            "negotiation_count": int(row["negotiation_count"] or 0),
        }
        for row in rows
    }


def _linked_position_labels(bid_id: str, target_column: str) -> dict[str, list[str]]:
    allowed = {"risk_issue_id", "decision_case_id", "approval_route_id", "negotiation_plan_id"}
    if target_column not in allowed:
        raise ValueError("Unsupported commercial relationship target")
    with db._conn() as conn:
        rows = conn.execute(
            f"""SELECT r.{target_column} target_id,tv.label
            FROM commercial_position_relationships r
            JOIN commercial_positions p ON p.position_id=r.position_id
            JOIN commercial_position_versions pv ON pv.position_id=p.position_id
                AND pv.version_number=p.current_version
            JOIN commercial_topic_versions tv ON tv.topic_version_id=pv.topic_version_id
            WHERE r.bid_id=? AND r.{target_column} IS NOT NULL
            ORDER BY tv.label,r.link_id""",
            (bid_id,),
        ).fetchall()
    result: dict[str, list[str]] = {}
    for row in rows:
        result.setdefault(str(row["target_id"]), []).append(str(row["label"]))
    return result


def _proposal_input_rows(bid_id: str) -> list[dict[str, str]]:
    """Project proposal inputs from existing authoritative Bid records."""
    rows: list[dict[str, str]] = []
    for item in requirement_service.list_requirements(bid_id=bid_id, as_of_date=_working_date()):
        rows.append(
            {
                "area": "Customer requirement",
                "input": f"{item.title}: {item.statement}",
                "response": item.response_text or "Proposed response not recorded",
                "origin": item.requirement_id,
                "href": f"/requirements/{quote(item.requirement_id)}",
            }
        )
    for item in scope_repository.list_scope_items(bid_id):
        rows.append(
            {
                "area": "Scope",
                "input": f"{item.title}: {item.description}",
                "response": item.offer_position.value.replace("_", " ").title(),
                "origin": item.scope_item_id,
                "href": f"/scope-items/{quote(item.scope_item_id)}",
            }
        )
    for item in scope_repository.list_interfaces(bid_id):
        rows.append(
            {
                "area": "Interface",
                "input": item.title,
                "response": (
                    f"{item.upstream_party} to {item.downstream_party}: "
                    f"{item.dependency_description}"
                ),
                "origin": item.interface_id,
                "href": f"/interfaces/{quote(item.interface_id)}",
            }
        )
    for package in vendor_document_repository.list_packages(bid_id):
        evidence = vendor_document_repository.list_requirements(package.package_id)
        confirmed = sum(
            row.verification_status is VerificationStatus.CONFIRMED_COMPLIANT for row in evidence
        )
        rows.append(
            {
                "area": "Manufacturer coverage",
                "input": f"{package.proposed_manufacturer}: {package.package_name}",
                "response": (
                    f"{confirmed} of {len(evidence)} vendor-document responsibilities confirmed"
                ),
                "origin": package.package_id,
                "href": f"/vendor-documents/packages/{quote(package.package_id)}",
            }
        )
    for item in ops08_repository.workspace(bid_id):
        if item["disposition"] == PositionDisposition.NOT_REVIEWED:
            continue
        rows.append(
            {
                "area": "Commercial position",
                "input": str(item["label"]),
                "response": str(
                    item["proposed_position"] or item["rationale"] or item["disposition"]
                ),
                "origin": f"Commercial position version {item['version_number']}",
                "href": f"/commercial/{quote(str(item['position_id']))}",
            }
        )
    for item in approval_repository.cases(bid_id):
        rows.append(
            {
                "area": "Decision or approval",
                "input": str(item["title"]),
                "response": str(item["lifecycle_state"]).replace("_", " ").title(),
                "origin": str(item["case_id"]),
                "href": f"/decisions?bid_id={quote(bid_id)}",
            }
        )
    for item in negotiation_repository.plans(bid_id):
        rows.append(
            {
                "area": "Negotiation",
                "input": str(item["title"]),
                "response": str(item["applicability"]).replace("_", " ").title(),
                "origin": str(item["plan_id"]),
                "href": f"/negotiations?bid_id={quote(bid_id)}",
            }
        )
    return rows


def _my_day_bid_rows_by_bucket(
    projection: MyDayProjection,
    bids: list[Bid],
    active_work_items: list[WorkItem],
    as_of: date,
) -> dict[MyDayBidBucket, list[BidPortfolioRow]]:
    """Group the one shared per-Bid portfolio row by My Day bucket.

    Reuses the already-loaded My Day projection instead of calling
    ``MyDayService.get_my_day()`` a second time, and reuses
    ``bid_repository.list_audit()``/``work_item_repository.list(active_only=True)``
    exactly as the Bids portfolio page does, so the two pages cannot disagree.
    """
    portfolio = build_bid_portfolio(
        projection,
        bids,
        active_work_items,
        bid_repository.list_audit(),
        BidPortfolioFilters(view=BidPortfolioView.ALL),
        as_of,
    )
    grouped: dict[MyDayBidBucket, list[BidPortfolioRow]] = {bucket: [] for bucket in MyDayBidBucket}
    for row in portfolio.rows:
        grouped[row.my_day_bucket].append(row)
    return grouped


def _json_item(
    item: WorkItem | list[WorkItem],
    *,
    status_code: int = 200,
) -> JSONResponse:
    return JSONResponse(jsonable_encoder(item), status_code=status_code)


def _mutation_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ValidationError):
        return HTTPException(status_code=422, detail=validation_error_message(exc))
    if isinstance(exc, WorkItemNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, StaleWorkItemError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, RequirementNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, StaleRequirementError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, RequirementSourceError):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, (ControlledDocumentNotFoundError, DocumentVersionNotFoundError)):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, (StaleDocumentError, DuplicateDocumentVersionError)):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ControlledDocumentIdentityError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, DocumentStoreBusyError):
        return HTTPException(
            status_code=503,
            detail="The document register is temporarily busy. Please retry.",
        )
    if isinstance(exc, ManagedStorageFailureError):
        return HTTPException(
            status_code=500,
            detail="Managed document storage could not complete the operation.",
        )
    if isinstance(exc, OSError):
        return HTTPException(
            status_code=503,
            detail="Managed document storage is temporarily unavailable.",
        )
    if isinstance(exc, ControlledDocumentIntegrityError):
        return HTTPException(
            status_code=409,
            detail="Controlled document integrity requires operator review.",
        )
    if isinstance(exc, ManagedFileTooLargeError):
        return HTTPException(status_code=413, detail=str(exc))
    if isinstance(exc, EmptyManagedFileError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@app.get("/my-day", response_class=HTMLResponse)
async def my_day(request: Request) -> HTMLResponse:
    projection_date = _working_date()
    projection = my_day_service.get_my_day(as_of=projection_date)
    bids = bid_repository.list_bids()
    bid_names = {bid.bid_id: bid.project_name for bid in bids}
    bid_owners = {bid.bid_id: bid.bc_owner for bid in bids}
    projected_items: list[ProjectedWorkItem] = []
    seen_work_items: set[str] = set()
    for bucket in (
        projection.blocked,
        projection.waiting,
        projection.overdue,
        projection.due_today,
        projection.upcoming,
        projection.later_or_unscheduled,
    ):
        for item in bucket:
            if item.item.work_item_id not in seen_work_items:
                seen_work_items.add(item.item.work_item_id)
                projected_items.append(item)
    projected_items.sort(
        key=lambda item: work_item_order_key(item.item, projection_date, projection.horizon_days)
    )
    standalone_work = [item for item in projected_items if item.item.bid_id is None]
    bid_blockers = [
        item
        for item in projected_items
        if item.item.bid_id is not None and item.item.status is WorkItemStatus.BLOCKED
    ]
    waiting_on_others = [
        item
        for item in projected_items
        if item.item.bid_id is not None and item.item.status is WorkItemStatus.WAITING
    ]
    critical_deadlines = [
        item
        for item in projected_items
        if item.item.bid_id is not None
        and item.item.status not in {WorkItemStatus.BLOCKED, WorkItemStatus.WAITING}
    ]
    waiting_supplier_attention = [
        item
        for item in projection.supplier_attention
        if "NO_RESPONSE" in item.get("code", "") or "SILENT" in item.get("code", "")
    ]
    blocking_supplier_attention = [
        item for item in projection.supplier_attention if item not in waiting_supplier_attention
    ]
    archived_items = [
        WorkItemSnapshot(
            item=item,
            bid_name=bid_names.get(item.bid_id, item.bid_id)
            if item.bid_id is not None
            else "Unassigned",
        )
        for item in work_item_repository.list()
        if item.status in {WorkItemStatus.COMPLETED, WorkItemStatus.CANCELLED}
    ]
    archived_items.sort(
        key=lambda snapshot: (snapshot.item.updated_at, snapshot.item.work_item_id),
        reverse=True,
    )
    active_work_items = work_item_repository.list(active_only=True)
    bid_rows_by_bucket = _my_day_bid_rows_by_bucket(
        projection, bids, active_work_items, projection_date
    )
    return render(
        "my_day.html",
        projection=projection,
        critical_deadlines=critical_deadlines,
        bid_blockers=bid_blockers,
        waiting_on_others=waiting_on_others,
        waiting_supplier_attention=waiting_supplier_attention,
        blocking_supplier_attention=blocking_supplier_attention,
        standalone_work=standalone_work,
        bid_owners=bid_owners,
        award_handover_bids=[bid for bid in bids if bid.status is BidStatus.WON],
        archived_items=archived_items,
        bids_requiring_action=bid_rows_by_bucket[MyDayBidBucket.REQUIRES_ACTION],
        bids_waiting=bid_rows_by_bucket[MyDayBidBucket.WAITING],
        bids_on_track_or_upcoming=bid_rows_by_bucket[MyDayBidBucket.ON_TRACK_OR_UPCOMING],
        gate_labels=GATE_LABELS,
        category_labels=WORK_CATEGORY_LABELS,
        status_labels=WORK_ITEM_STATUS_LABELS,
    )


@app.get("/api/work-items")
async def list_work_items(bid_id: str | None = None) -> JSONResponse:
    return _json_item(work_item_repository.list(bid_id=bid_id))


@app.get("/my-work", response_class=HTMLResponse)
async def my_work(
    request: Request,
    view: str = "current",
    status: str | None = None,
    category: str | None = None,
    domain: str | None = None,
    context: str = "any",
    bid_id: str | None = None,
    attention: str = "any",
) -> HTMLResponse:
    raw_filters = {
        "view": view,
        "status": status or "",
        "category": category or "",
        "domain": domain or "",
        "context": context,
        "bid_id": bid_id or "",
        "attention": attention,
    }
    try:
        filters = WorkRegisterFilter.model_validate(
            {
                "view": view,
                "status": status or None,
                "category": category or None,
                "domain": domain or None,
                "context": context,
                "bid_id": bid_id or None,
                "attention": attention,
            }
        )
        entries = work_item_service.get_work_register(filters, as_of=_working_date())
    except (ValidationError, ValueError) as exc:
        return render(
            "my_work.html",
            register_entries=[],
            metrics=ops_repository.metrics(),
            categories=list(WorkCategory),
            category_labels=WORK_CATEGORY_LABELS,
            domains=list(ResponsibilityDomain),
            domain_labels=RESPONSIBILITY_DOMAIN_LABELS,
            statuses=list(WorkItemStatus),
            status_labels=WORK_ITEM_STATUS_LABELS,
            views=list(WorkRegisterView),
            contexts=list(WorkContextFilter),
            attention_options=list(WorkAttentionFilter),
            bids=bid_repository.list_bids(),
            selected=raw_filters,
            filter_error=(
                validation_error_message(exc) if isinstance(exc, ValidationError) else str(exc)
            ),
            filter_query="",
            editor_query="",
            capture={},
            capture_error=None,
            status_code=422,
        )
    selected = filters.model_dump(mode="json")
    selected = {key: value or "" for key, value in selected.items()}
    filter_query = _work_register_query(filters)
    return render(
        "my_work.html",
        register_entries=entries,
        metrics=ops_repository.metrics(),
        categories=list(WorkCategory),
        category_labels=WORK_CATEGORY_LABELS,
        domains=list(ResponsibilityDomain),
        domain_labels=RESPONSIBILITY_DOMAIN_LABELS,
        statuses=list(WorkItemStatus),
        status_labels=WORK_ITEM_STATUS_LABELS,
        views=list(WorkRegisterView),
        contexts=list(WorkContextFilter),
        attention_options=list(WorkAttentionFilter),
        bids=bid_repository.list_bids(),
        selected=selected,
        filter_error=None,
        filter_query=filter_query,
        editor_query=_editor_register_query(filters),
        capture={},
        capture_error=None,
    )


def _work_register_query(filters: WorkRegisterFilter) -> str:
    values = filters.model_dump(mode="json")
    pairs = [
        (key, str(value))
        for key, value in values.items()
        if value is not None
        and value != ""
        and not (
            (key == "view" and value == WorkRegisterView.CURRENT.value)
            or (key == "context" and value == WorkContextFilter.ANY.value)
            or (key == "attention" and value == WorkAttentionFilter.ANY.value)
        )
    ]
    return urlencode(pairs)


def _editor_register_query(filters: WorkRegisterFilter) -> str:
    query = _work_register_query(filters)
    if not query:
        return ""
    return urlencode([(f"register_{key}", value) for key, value in parse_qsl(query)])


@app.post("/my-work", response_class=HTMLResponse)
async def quick_capture_work(
    request: Request,
    title: str = Form(...),
    category: str = Form(...),
    next_action_date: str = Form(""),
) -> HTMLResponse:
    capture = {"title": title, "category": category, "next_action_date": next_action_date}
    try:
        work_item_service.create_work_item(
            {
                "title": title,
                "category": category,
                "next_action_date": next_action_date or None,
            },
            LOCAL_ACTOR,
        )
    except (ValidationError, ValueError) as exc:
        filters = WorkRegisterFilter()
        return render(
            "my_work.html",
            register_entries=work_item_service.get_work_register(
                filters,
                as_of=_working_date(),
            ),
            metrics=ops_repository.metrics(),
            categories=list(WorkCategory),
            category_labels=WORK_CATEGORY_LABELS,
            domains=list(ResponsibilityDomain),
            domain_labels=RESPONSIBILITY_DOMAIN_LABELS,
            statuses=list(WorkItemStatus),
            status_labels=WORK_ITEM_STATUS_LABELS,
            views=list(WorkRegisterView),
            contexts=list(WorkContextFilter),
            attention_options=list(WorkAttentionFilter),
            bids=bid_repository.list_bids(),
            selected=filters.model_dump(mode="json"),
            filter_error=None,
            filter_query="",
            editor_query="",
            capture=capture,
            capture_error=validation_error_message(exc),
            status_code=422,
        )
    return RedirectResponse("/my-work", status_code=303)


def _work_item_editor_context(
    item: WorkItem,
    *,
    register_filters: WorkRegisterFilter | None = None,
    values: dict[str, object] | None = None,
    error: str | None = None,
) -> dict[str, object]:
    filters = register_filters or WorkRegisterFilter()
    register_query = _work_register_query(filters)
    back_href = "/my-work" + (f"?{register_query}" if register_query else "")
    context: dict[str, object] = {
        "item": item,
        "values": values or item.model_dump(mode="json"),
        "error": error,
        "categories": list(WorkCategory),
        "category_labels": WORK_CATEGORY_LABELS,
        "domains": list(ResponsibilityDomain),
        "domain_labels": RESPONSIBILITY_DOMAIN_LABELS,
        "statuses": list(WorkItemStatus),
        "status_labels": WORK_ITEM_STATUS_LABELS,
        "priorities": list(WorkItemPriority),
        "waiting_kinds": list(WaitingPartyKind),
        "waiting_kind_labels": WAITING_PARTY_KIND_LABELS,
        "bids": bid_repository.list_bids(),
        "back_href": back_href,
        "editor_query": _editor_register_query(filters),
    }
    return context


@app.get("/my-work/{work_item_id}", response_class=HTMLResponse)
async def work_item_detail(
    request: Request,
    work_item_id: str,
    register_view: str = "current",
    register_status: str | None = None,
    register_category: str | None = None,
    register_domain: str | None = None,
    register_context: str = "any",
    register_bid_id: str | None = None,
    register_attention: str = "any",
) -> HTMLResponse:
    try:
        item = work_item_service.get_work_item(work_item_id)
    except WorkItemNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        filters = WorkRegisterFilter.model_validate(
            {
                "view": register_view,
                "status": register_status,
                "category": register_category,
                "domain": register_domain,
                "context": register_context,
                "bid_id": register_bid_id,
                "attention": register_attention,
            }
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=validation_error_message(exc)) from exc
    return render(
        "work_item_detail.html",
        **_work_item_editor_context(item, register_filters=filters),
    )


@app.post("/my-work/{work_item_id}", response_class=HTMLResponse)
async def save_work_item_detail(
    request: Request,
    work_item_id: str,
    register_view: str = "current",
    register_status: str | None = None,
    register_category: str | None = None,
    register_domain: str | None = None,
    register_context: str = "any",
    register_bid_id: str | None = None,
    register_attention: str = "any",
) -> HTMLResponse:
    try:
        current = work_item_service.get_work_item(work_item_id)
    except WorkItemNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        filters = WorkRegisterFilter.model_validate(
            {
                "view": register_view,
                "status": register_status,
                "category": register_category,
                "domain": register_domain,
                "context": register_context,
                "bid_id": register_bid_id,
                "attention": register_attention,
            }
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=validation_error_message(exc)) from exc
    form = await request.form()
    values = {key: str(value) for key, value in form.items()}
    nullable = {
        "bid_id",
        "details",
        "due_date",
        "next_action_date",
        "responsibility_domain",
        "waiting_party_kind",
        "waiting_party_label",
        "waiting_owed",
        "requested_date",
        "chase_date",
        "blocker_description",
        "resolution_owner",
        "review_date",
        "completion_outcome",
        "completion_evidence",
        "cancellation_reason",
    }
    payload: dict[str, object] = {
        key: (None if key in nullable and value == "" else value) for key, value in values.items()
    }
    payload["waiting_on"] = payload.get("waiting_party_label")
    payload["blocker_note"] = payload.get("blocker_description")
    try:
        work_item_service.edit_work_item(work_item_id, payload, LOCAL_ACTOR)
    except (ValidationError, ValueError) as exc:
        return render(
            "work_item_detail.html",
            **_work_item_editor_context(
                current,
                register_filters=filters,
                values=values,
                error=validation_error_message(exc),
            ),
            status_code=422,
        )
    query = _editor_register_query(filters)
    location = f"/my-work/{quote(work_item_id)}" + (f"?{query}" if query else "")
    return RedirectResponse(location, status_code=303)


@app.get("/role-framework", response_class=HTMLResponse)
async def role_framework(request: Request) -> HTMLResponse:
    as_of = _working_date()
    try:
        effective = role_profile_service.effective_profile(as_of=as_of)
        effective_error = None
    except RoleProfileOverlapError as exc:
        effective = None
        effective_error = str(exc)
    profiles = role_profile_service.list_profiles()
    return render(
        "role_framework.html",
        profiles=profiles,
        has_lineage=bool(profiles),
        effective=effective,
        effective_error=effective_error,
        domains=list(ResponsibilityDomain),
        domain_labels=RESPONSIBILITY_DOMAIN_LABELS,
        state_labels=ROLE_PROFILE_STATE_LABELS,
        as_of=as_of,
    )


def _role_profile_values(profile: RoleProfile | None = None) -> dict[str, object]:
    if profile is None:
        return {
            "title": "",
            "organization": "",
            "mission": "",
            "boundaries": "",
            "coordination": "",
            "outcomes": "",
            "cadence": "",
            "domains": [],
            "effective_from": _working_date().isoformat(),
            "effective_until": "",
        }
    return {
        "title": profile.title,
        "organization": profile.organization or "",
        "mission": profile.mission,
        "boundaries": profile.boundaries,
        "coordination": profile.coordination,
        "outcomes": profile.outcomes,
        "cadence": profile.cadence,
        "domains": [domain.value for domain in profile.domains],
        "effective_from": profile.effective_from.isoformat(),
        "effective_until": (profile.effective_until.isoformat() if profile.effective_until else ""),
    }


def _role_profile_detail_context(
    profile: RoleProfile | None,
    *,
    values: dict[str, object] | None = None,
    error: str | None = None,
) -> dict[str, object]:
    parent = (
        role_profile_service.get_profile(profile.parent_profile_id)
        if profile is not None and profile.parent_profile_id is not None
        else None
    )
    return {
        "profile": profile,
        "values": values or _role_profile_values(profile),
        "error": error,
        "domains": list(ResponsibilityDomain),
        "domain_labels": RESPONSIBILITY_DOMAIN_LABELS,
        "state_labels": ROLE_PROFILE_STATE_LABELS,
        "parent": parent,
        "children": (role_profile_service.list_children(profile.profile_id) if profile else []),
        "audit_entries": role_profile_service.list_audit(profile.profile_id) if profile else [],
    }


def _role_profile_form_values(
    *,
    title: str,
    organization: str,
    mission: str,
    boundaries: str,
    coordination: str,
    outcomes: str,
    cadence: str,
    domains: list[str],
    effective_from: str,
    effective_until: str,
) -> dict[str, object]:
    return {
        "title": title,
        "organization": organization,
        "mission": mission,
        "boundaries": boundaries,
        "coordination": coordination,
        "outcomes": outcomes,
        "cadence": cadence,
        "domains": domains,
        "effective_from": effective_from,
        "effective_until": effective_until,
    }


@app.get("/role-framework/new", response_class=HTMLResponse)
async def new_role_profile(request: Request) -> HTMLResponse:
    profiles = role_profile_service.list_profiles()
    if profiles:
        preferred = next(
            (profile for profile in profiles if profile.state.value == "PUBLISHED"), profiles[0]
        )
        return RedirectResponse(f"/role-framework/{quote(preferred.profile_id)}", status_code=303)
    return render(
        "role_profile_detail.html",
        **_role_profile_detail_context(None),
    )


@app.post("/role-framework", response_class=HTMLResponse)
async def create_role_profile_html(
    request: Request,
    title: str = Form(...),
    organization: str = Form(""),
    mission: str = Form(""),
    boundaries: str = Form(""),
    coordination: str = Form(""),
    outcomes: str = Form(""),
    cadence: str = Form(""),
    domains: Annotated[list[str] | None, Form()] = None,
    effective_from: str = Form(...),
    effective_until: str = Form(""),
) -> HTMLResponse:
    selected_domains = domains or []
    values = _role_profile_form_values(
        title=title,
        organization=organization,
        mission=mission,
        boundaries=boundaries,
        coordination=coordination,
        outcomes=outcomes,
        cadence=cadence,
        domains=selected_domains,
        effective_from=effective_from,
        effective_until=effective_until,
    )
    try:
        profile = role_profile_service.create_profile(
            {
                **values,
                "organization": organization or None,
                "effective_until": effective_until or None,
            },
            LOCAL_ACTOR,
        )
    except (ValidationError, ValueError) as exc:
        return render(
            "role_profile_detail.html",
            **_role_profile_detail_context(
                None,
                values=values,
                error=validation_error_message(exc),
            ),
            status_code=422,
        )
    return RedirectResponse(f"/role-framework/{quote(profile.profile_id)}", status_code=303)


@app.get("/role-framework/{profile_id}", response_class=HTMLResponse)
async def role_profile_detail(request: Request, profile_id: str) -> HTMLResponse:
    try:
        profile = role_profile_service.get_profile(profile_id)
    except RoleProfileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return render(
        "role_profile_detail.html",
        **_role_profile_detail_context(profile),
    )


@app.post("/role-framework/{profile_id}", response_class=HTMLResponse)
async def edit_role_profile_html(
    request: Request,
    profile_id: str,
    expected_version_token: str = Form(...),
    title: str = Form(...),
    organization: str = Form(""),
    mission: str = Form(""),
    boundaries: str = Form(""),
    coordination: str = Form(""),
    outcomes: str = Form(""),
    cadence: str = Form(""),
    domains: Annotated[list[str] | None, Form()] = None,
    effective_from: str = Form(...),
    effective_until: str = Form(""),
) -> HTMLResponse:
    try:
        current = role_profile_service.get_profile(profile_id)
    except RoleProfileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    selected_domains = domains or []
    values = _role_profile_form_values(
        title=title,
        organization=organization,
        mission=mission,
        boundaries=boundaries,
        coordination=coordination,
        outcomes=outcomes,
        cadence=cadence,
        domains=selected_domains,
        effective_from=effective_from,
        effective_until=effective_until,
    )
    try:
        role_profile_service.edit_draft(
            profile_id,
            {
                **values,
                "organization": organization or None,
                "effective_until": effective_until or None,
                "expected_version_token": expected_version_token,
            },
            LOCAL_ACTOR,
        )
    except (ValidationError, ValueError) as exc:
        current = role_profile_service.get_profile(profile_id)
        return render(
            "role_profile_detail.html",
            **_role_profile_detail_context(
                current,
                values=values,
                error=validation_error_message(exc),
            ),
            status_code=422,
        )
    return RedirectResponse(f"/role-framework/{quote(profile_id)}", status_code=303)


@app.post("/role-framework/{profile_id}/revise", response_class=HTMLResponse)
async def revise_role_profile_html(
    request: Request,
    profile_id: str,
    expected_version_token: str = Form(...),
) -> HTMLResponse:
    try:
        child = role_profile_service.revise_profile(
            profile_id,
            {"expected_version_token": expected_version_token},
            LOCAL_ACTOR,
        )
    except RoleProfileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValidationError, ValueError) as exc:
        profile = role_profile_service.get_profile(profile_id)
        return render(
            "role_profile_detail.html",
            **_role_profile_detail_context(
                profile,
                error=validation_error_message(exc),
            ),
            status_code=422,
        )
    return RedirectResponse(f"/role-framework/{quote(child.profile_id)}", status_code=303)


@app.post("/role-framework/{profile_id}/publish", response_class=HTMLResponse)
async def publish_role_profile_html(
    request: Request,
    profile_id: str,
    expected_version_token: str = Form(...),
    parent_expected_version_token: str = Form(""),
) -> HTMLResponse:
    try:
        role_profile_service.publish_profile(
            profile_id,
            {
                "expected_version_token": expected_version_token,
                "parent_expected_version_token": parent_expected_version_token or None,
            },
            LOCAL_ACTOR,
        )
    except RoleProfileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValidationError, ValueError) as exc:
        profile = role_profile_service.get_profile(profile_id)
        return render(
            "role_profile_detail.html",
            **_role_profile_detail_context(
                profile,
                error=validation_error_message(exc),
            ),
            status_code=422,
        )
    return RedirectResponse(f"/role-framework/{quote(profile_id)}", status_code=303)


@app.post("/role-framework/{profile_id}/retire", response_class=HTMLResponse)
async def retire_role_profile_html(
    request: Request,
    profile_id: str,
    expected_version_token: str = Form(...),
) -> HTMLResponse:
    try:
        role_profile_service.retire_profile(
            profile_id,
            {"expected_version_token": expected_version_token},
            LOCAL_ACTOR,
        )
    except RoleProfileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValidationError, ValueError) as exc:
        profile = role_profile_service.get_profile(profile_id)
        return render(
            "role_profile_detail.html",
            **_role_profile_detail_context(
                profile,
                error=validation_error_message(exc),
            ),
            status_code=422,
        )
    return RedirectResponse(f"/role-framework/{quote(profile_id)}", status_code=303)


@app.get("/api/ops/role-profiles")
async def list_role_profiles() -> JSONResponse:
    try:
        effective = role_profile_service.effective_profile(as_of=_working_date())
    except RoleProfileOverlapError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return JSONResponse(
        {
            "profiles": [
                _legacy_role_profile_json(profile)
                for profile in role_profile_service.list_profiles()
            ],
            "effective": _legacy_role_profile_json(effective) if effective else None,
            "domains": list(RESPONSIBILITY_DOMAINS),
        }
    )


def _legacy_role_profile_json(profile: RoleProfile) -> dict[str, object]:
    """Preserve the accepted OPS-01 role-profile JSON response shape."""
    return {
        "profile_id": profile.profile_id,
        "version_number": profile.version_number,
        "title": profile.title,
        "organization": profile.organization,
        "mission": profile.mission,
        "boundaries": profile.boundaries,
        "coordination": profile.coordination,
        "outcomes": profile.outcomes,
        "cadence": profile.cadence,
        "domains_json": json.dumps([domain.value for domain in profile.domains]),
        "effective_from": profile.effective_from.isoformat(),
        "effective_until": (
            profile.effective_until.isoformat() if profile.effective_until else None
        ),
        "state": profile.state.value,
        "created_by": profile.created_by,
        "created_at": profile.created_at.isoformat(),
        "parent_profile_id": profile.parent_profile_id,
        "version_token": profile.version_token,
        "provenance_json": (profile.provenance.model_dump_json() if profile.provenance else None),
    }


async def _role_profile_json_body(request: Request) -> dict[str, object]:
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=422, detail="A JSON object is required.") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="A JSON object is required.")
    return body


@app.post("/api/ops/role-profiles")
async def create_role_profile(request: Request) -> JSONResponse:
    body = await _role_profile_json_body(request)
    try:
        profile = role_profile_service.create_profile(body, LOCAL_ACTOR)
        return JSONResponse({"profile_id": profile.profile_id}, status_code=201)
    except (ValidationError, ValueError, sqlite3.IntegrityError) as exc:
        raise HTTPException(status_code=422, detail=validation_error_message(exc)) from exc


@app.post("/api/ops/role-profiles/{profile_id}/publish")
async def publish_role_profile(profile_id: str, request: Request) -> JSONResponse:
    body = await _role_profile_json_body(request)
    try:
        role_profile_service.publish_profile(profile_id, body, LOCAL_ACTOR)
    except RoleProfileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except StaleRoleProfileError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=validation_error_message(exc)) from exc
    return JSONResponse({"profile_id": profile_id, "state": "PUBLISHED"})


@app.post("/api/ops/role-profiles/{profile_id}/retire")
async def retire_role_profile(profile_id: str, request: Request) -> JSONResponse:
    body = await _role_profile_json_body(request)
    try:
        role_profile_service.retire_profile(profile_id, body, LOCAL_ACTOR)
    except RoleProfileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except StaleRoleProfileError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=validation_error_message(exc)) from exc
    return JSONResponse({"profile_id": profile_id, "state": "RETIRED"})


@app.get("/api/ops/work")
async def list_ops_work(
    category: str | None = None,
    domain: str | None = None,
    status: str | None = None,
    unassigned: bool = False,
) -> JSONResponse:
    items = work_item_repository.list(active_only=False)
    if category:
        items = [item for item in items if item.category.value == category]
    if domain:
        items = [
            item
            for item in items
            if item.responsibility_domain and item.responsibility_domain.value == domain
        ]
    if status:
        items = [item for item in items if item.status.value == status]
    if unassigned:
        items = [item for item in items if item.bid_id is None]
    return _json_item(items)


@app.get("/api/work-items/{work_item_id}")
async def get_work_item(work_item_id: str) -> JSONResponse:
    try:
        return _json_item(work_item_service.get_work_item(work_item_id))
    except (WorkItemNotFoundError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.post("/api/work-items")
async def create_work_item(request: Request) -> JSONResponse:
    body = await request.json()
    actor = str(body.pop("actor", LOCAL_ACTOR)) if isinstance(body, dict) else LOCAL_ACTOR
    try:
        return _json_item(
            work_item_service.create_work_item(body, actor),
            status_code=201,
        )
    except (ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.patch("/api/work-items/{work_item_id}")
async def edit_work_item(work_item_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    actor = str(body.pop("actor", LOCAL_ACTOR)) if isinstance(body, dict) else LOCAL_ACTOR
    try:
        return _json_item(work_item_service.edit_work_item(work_item_id, body, actor))
    except (ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.post("/api/work-items/{work_item_id}/transition")
async def transition_work_item(work_item_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    actor = str(body.pop("actor", LOCAL_ACTOR)) if isinstance(body, dict) else LOCAL_ACTOR
    try:
        return _json_item(work_item_service.transition_work_item(work_item_id, body, actor))
    except (ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.get("/requirements", response_class=HTMLResponse)
async def requirements_register(
    request: Request,
    bid_id: str | None = None,
    origin: str | None = None,
    category: str | None = None,
    significance: str | None = None,
    lifecycle: str | None = None,
    disposition: str | None = None,
    work_state: str | None = None,
    review_state: str | None = None,
    owner: str | None = None,
    contributor: str | None = None,
    reviewer: str | None = None,
    due_state: str | None = None,
    attention: str | None = None,
    exception: str | None = None,
    as_of: str | None = None,
    form_state: str | None = None,
) -> HTMLResponse:
    projection_date = _parse_as_of(as_of)
    try:
        records = requirement_service.list_requirements(
            bid_id=bid_id or None,
            origin=RequirementOrigin(origin) if origin else None,
            category=RequirementCategory(category) if category else None,
            significance=RequirementSignificance(significance) if significance else None,
            lifecycle=RequirementLifecycle(lifecycle) if lifecycle else None,
            disposition=ResponseDisposition(disposition) if disposition else None,
            work_state=RequirementWorkState(work_state) if work_state else None,
            review_state=RequirementReviewState(review_state) if review_state else None,
            owner=owner or None,
            contributor=contributor or None,
            reviewer=reviewer or None,
            due_state=due_state or None,
            attention_only=attention == "1",
            exception_only=exception == "1",
            as_of_date=projection_date,
        )
        coverage = requirement_service.coverage(
            bid_id=bid_id or None,
            as_of_date=projection_date,
        )
        sources = requirement_service.source_choices(bid_id) if bid_id else None
        readiness = evaluate_readiness(bid_repository, db, bid_id) if bid_id else None
    except ValueError as exc:
        raise _mutation_error(exc) from exc
    entered: dict[str, str] = {}
    if form_state:
        saved = _requirement_form_states.get(form_state)
        if (
            saved is None
            or (datetime.now(UTC) - saved[0]).total_seconds() > _REQUIREMENT_FORM_STATE_TTL_SECONDS
        ):
            _requirement_form_states.pop(form_state, None)
            raise HTTPException(
                status_code=422, detail="Requirement form state expired; register the source again"
            )
        entered = dict(saved[1])
        if entered.get("bid_id") != (bid_id or ""):
            raise HTTPException(
                status_code=422, detail="Requirement form state does not belong to this Bid"
            )
    source_operation_token = entered.get("source_operation_token", "")
    if bid_id and not source_operation_token:
        source_operation_token = _new_source_operation_token(bid_id)
    bids = bid_repository.list_bids()
    bid_names = {bid.bid_id: bid.project_name for bid in bids}
    return render(
        "requirements.html",
        requirements=records,
        coverage=coverage,
        readiness=readiness,
        bids=bids,
        bid_names=bid_names,
        sources=sources,
        origins=list(RequirementOrigin),
        categories=list(RequirementCategory),
        significances=list(RequirementSignificance),
        significance_labels=REQUIREMENT_SIGNIFICANCE_LABELS,
        stages=list(RequirementStage),
        lifecycles=list(RequirementLifecycle),
        dispositions=list(ResponseDisposition),
        work_states=list(RequirementWorkState),
        review_states=list(RequirementReviewState),
        selected={
            "bid_id": bid_id or "",
            "origin": origin or "",
            "category": category or "",
            "significance": significance or "",
            "lifecycle": lifecycle or "",
            "disposition": disposition or "",
            "work_state": work_state or "",
            "review_state": review_state or "",
            "owner": owner or "",
            "contributor": contributor or "",
            "reviewer": reviewer or "",
            "due_state": due_state or "",
            "attention": attention or "",
            "exception": exception or "",
            "as_of": projection_date.isoformat(),
        },
        as_of_date=projection_date,
        actor=LOCAL_ACTOR,
        entered=entered,
        form_state=form_state or "",
        source_operation_token=source_operation_token,
        source_registered=bool(form_state and entered.get("source_document_version_id")),
        create_error=None,
        **_bid_return_context(bid_id, BidWorkspaceSection.REQUIREMENTS_SCOPE),
    )


@app.post("/requirements", response_class=HTMLResponse)
async def create_requirement_browser(request: Request) -> Response:
    form = dict(await request.form())
    bid_id = str(form.get("bid_id") or "")
    try:
        command = RequirementBrowserCommand.model_validate(form)
        created = requirement_service.create_requirement(command.requirement_payload(), LOCAL_ACTOR)
    except (ValidationError, ValueError, sqlite3.Error) as exc:
        return render(
            "requirement_create_error.html",
            status_code=422,
            error=validation_error_message(exc) if isinstance(exc, ValidationError) else str(exc),
            entered=form,
            bid=bid_repository.get_bid(bid_id),
            sources=requirement_service.source_choices(bid_id)
            if bid_repository.get_bid(bid_id)
            else None,
            origins=list(RequirementOrigin),
            categories=list(RequirementCategory),
            significances=list(RequirementSignificance),
            significance_labels=REQUIREMENT_SIGNIFICANCE_LABELS,
            stages=list(RequirementStage),
            dispositions=list(ResponseDisposition),
        )
    state_token = str(form.get("form_state") or "")
    if state_token:
        _requirement_form_states.pop(state_token, None)
    return RedirectResponse(f"/requirements/{quote(created.requirement_id)}", status_code=303)


@app.post("/requirements/with-source", response_class=HTMLResponse)
async def create_requirement_with_source(request: Request) -> Response:
    """Register source evidence, then PRG back to the retained requirement form."""
    raw = await request.form()
    submitted = dict(raw)
    form = {
        key: value for key, value in submitted.items() if not isinstance(value, StarletteUploadFile)
    }
    bid_id = str(form.get("bid_id") or "")
    upload = submitted.get("new_source_file")
    try:
        command = RequirementBrowserCommand.model_validate(submitted)
        if not isinstance(upload, StarletteUploadFile) or not upload.filename:
            raise ValueError("Choose the customer source file before registering Revision 0")
        if bid_repository.get_bid(bid_id) is None:
            raise ValueError("Bid not found")
        operation_id = _source_operation_id(command.source_operation_token or "", bid_id)
        _document, version = document_service.register_document_idempotent(
            operation_id,
            {
                "bid_id": bid_id,
                "title": command.new_source_title or "",
                "category": DocumentCategory.SPECIFICATION,
                "version_label": command.new_source_version_label or "Revision 0",
            },
            upload.file,
            upload.filename,
            upload.content_type,
            LOCAL_ACTOR,
        )
        form = command.retained_values()
        form["source_document_version_id"] = version.document_version_id
        token = uuid.uuid4().hex
        if len(_requirement_form_states) >= 100:
            oldest = min(_requirement_form_states, key=lambda key: _requirement_form_states[key][0])
            _requirement_form_states.pop(oldest, None)
        _requirement_form_states[token] = (
            datetime.now(UTC),
            {key: str(value) for key, value in form.items()},
        )
    except (ValidationError, ValueError, sqlite3.Error, OSError) as exc:
        return render(
            "requirement_create_error.html",
            status_code=422,
            error=validation_error_message(exc) if isinstance(exc, ValidationError) else str(exc),
            entered=form,
            bid=bid_repository.get_bid(bid_id),
            sources=requirement_service.source_choices(bid_id)
            if bid_repository.get_bid(bid_id)
            else None,
            origins=list(RequirementOrigin),
            categories=list(RequirementCategory),
            significances=list(RequirementSignificance),
            significance_labels=REQUIREMENT_SIGNIFICANCE_LABELS,
            stages=list(RequirementStage),
            dispositions=list(ResponseDisposition),
        )
    return RedirectResponse(
        f"/requirements?bid_id={quote(bid_id)}&form_state={quote(token)}#create",
        status_code=303,
    )


@app.get("/requirements/{requirement_id}", response_class=HTMLResponse)
async def requirement_detail(request: Request, requirement_id: str) -> HTMLResponse:
    return render("requirement_detail.html", **_requirement_detail_context(requirement_id))


def _requirement_detail_context(
    requirement_id: str,
    *,
    error: str | None = None,
    entered: dict[str, str] | None = None,
) -> dict[str, object]:
    """Build the complete retained browser context for requirement detail."""
    try:
        detail = requirement_service.detail(requirement_id)
        history = requirement_service.audit_history(requirement_id)
    except ValueError as exc:
        raise _mutation_error(exc) from exc
    bid = bid_repository.get_bid(detail.requirement.bid_id)
    coverage = requirement_service.coverage(
        bid_id=detail.requirement.bid_id,
        as_of_date=_working_date(),
    )
    readiness = evaluate_readiness(bid_repository, db, detail.requirement.bid_id)
    relationships = ops07_repository.coverage(requirement_id)
    return {
        "detail": detail,
        "requirement": detail.requirement,
        "bid": bid,
        "coverage": coverage,
        "readiness": readiness,
        "history": history,
        "relationships": relationships,
        "available_scopes": scope_repository.list_scope_items(detail.requirement.bid_id),
        "available_packages": vendor_document_repository.list_packages(detail.requirement.bid_id),
        "exact_evidence": ops07w_repository.exact_evidence(requirement_id),
        "available_exact_evidence": ops07w_repository.available_exact_evidence(requirement_id),
        "available_work": work_item_repository.list(bid_id=detail.requirement.bid_id),
        "categories": list(RequirementCategory),
        "significances": list(RequirementSignificance),
        "significance_labels": REQUIREMENT_SIGNIFICANCE_LABELS,
        "stages": list(RequirementStage),
        "dispositions": list(ResponseDisposition),
        "work_states": list(RequirementWorkState),
        "review_states": list(RequirementReviewState),
        "actor": LOCAL_ACTOR,
        "error": error,
        "entered": entered or {},
    }


@app.post("/bids/{bid_id}/requirements/bulk", response_class=HTMLResponse)
async def bulk_requirement_responsibility(bid_id: str, request: Request) -> HTMLResponse:
    form = await request.form()
    selected = [str(value) for value in form.getlist("requirement_target")]
    try:
        targets = []
        for encoded in selected:
            requirement_id, separator, version = encoded.partition("|")
            if not separator:
                raise ValueError("Select valid requirement rows")
            targets.append(
                Ops07BulkTarget(requirement_id=requirement_id, expected_version=int(version))
            )
        payload: dict[str, object] = {"targets": targets}
        for field in ("owner", "contributor", "reviewer"):
            value = str(form.get(field, "")).strip()
            if value:
                payload[field] = value
            if form.get(f"clear_{field}") == "1":
                payload[f"clear_{field}"] = True
        ops07_repository.bulk_assign(
            bid_id, BulkResponsibilityAssignment.model_validate(payload), LOCAL_ACTOR
        )
    except (ValidationError, ValueError, sqlite3.Error) as exc:
        context = _bid_workspace_context(bid_id, BidWorkspaceSection.REQUIREMENTS_SCOPE)
        context.update(
            ops07_error=validation_error_message(exc)
            if isinstance(exc, ValidationError)
            else str(exc),
            ops07_entered=form,
            selected_requirement_targets=selected,
        )
        return render("bid_detail.html", status_code=422, **context)
    return RedirectResponse(
        f"/bids/{quote(bid_id)}/requirements-scope#requirements", status_code=303
    )


@app.post("/requirements/{requirement_id}/scope-links", response_class=HTMLResponse)
async def link_requirement_scope(requirement_id: str, request: Request) -> HTMLResponse:
    form = await request.form()
    try:
        requirement = requirement_service.get_requirement(requirement_id)
        ops07_repository.link_scope(
            requirement.bid_id, requirement_id, str(form.get("scope_item_id", "")), LOCAL_ACTOR
        )
    except RequirementNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, sqlite3.Error) as exc:
        if "not found" in str(exc).casefold():
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return render(
            "requirement_detail.html",
            status_code=422,
            **_requirement_detail_context(
                requirement_id,
                error=str(exc),
                entered={"scope_item_id": str(form.get("scope_item_id", ""))},
            ),
        )
    return RedirectResponse(f"/requirements/{quote(requirement_id)}#coverage", status_code=303)


@app.post("/requirements/{requirement_id}/scope-links/{scope_item_id}/remove")
async def unlink_requirement_scope(requirement_id: str, scope_item_id: str) -> RedirectResponse:
    try:
        requirement = requirement_service.get_requirement(requirement_id)
        ops07_repository.unlink_scope(
            requirement.bid_id, requirement_id, scope_item_id, LOCAL_ACTOR
        )
    except RequirementNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RedirectResponse(f"/requirements/{quote(requirement_id)}#coverage", status_code=303)


@app.post("/requirements/{requirement_id}/manufacturer-links", response_class=HTMLResponse)
async def link_requirement_manufacturer(requirement_id: str, request: Request) -> HTMLResponse:
    form = await request.form()
    try:
        requirement = requirement_service.get_requirement(requirement_id)
        ops07_repository.link_manufacturer(
            requirement.bid_id, requirement_id, str(form.get("package_id", "")), LOCAL_ACTOR
        )
    except RequirementNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, sqlite3.Error) as exc:
        return render(
            "requirement_detail.html",
            status_code=422,
            **_requirement_detail_context(
                requirement_id,
                error=str(exc),
                entered={"package_id": str(form.get("package_id", ""))},
            ),
        )
    return RedirectResponse(f"/requirements/{quote(requirement_id)}#coverage", status_code=303)


@app.post("/requirements/{requirement_id}/manufacturer-packages", response_class=HTMLResponse)
async def create_and_link_requirement_manufacturer(
    requirement_id: str, request: Request
) -> Response:
    requirement = requirement_service.get_requirement(requirement_id)
    form = dict(await request.form())
    form["bid_id"] = requirement.bid_id
    package = None
    try:
        # Validate relationship prerequisites before package creation. The only remaining
        # association failure is compensated below so no unintended package survives.
        if any(
            item.package_code == str(form.get("package_code") or "")
            for item in vendor_document_repository.list_packages(requirement.bid_id)
        ):
            raise ValueError("A package with this code already exists for the Bid")
        package = vendor_document_service.create_package(
            SupplierPackageCreate.model_validate(
                {key: value for key, value in form.items() if value not in ("", None)}
            ),
            LOCAL_ACTOR,
        )
        ops07_repository.link_manufacturer(
            requirement.bid_id, requirement_id, package.package_id, LOCAL_ACTOR
        )
    except (ValidationError, ValueError, sqlite3.Error) as exc:
        if package is not None:
            with db._conn() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "DELETE FROM audit_log WHERE bid_id=? "
                    "AND action='vendor_vdrl_package_created' AND detail LIKE ?",
                    (requirement.bid_id, f'%"package_id": "{package.package_id}"%'),
                )
                conn.execute(
                    "DELETE FROM vendor_bid_packages WHERE package_id=?", (package.package_id,)
                )
                conn.commit()
        return render(
            "requirement_detail.html",
            status_code=422,
            **_requirement_detail_context(
                requirement_id,
                error=validation_error_message(exc)
                if isinstance(exc, ValidationError)
                else str(exc),
                entered={key: str(value) for key, value in form.items()},
            ),
        )
    return RedirectResponse(f"/requirements/{quote(requirement_id)}#coverage", status_code=303)


@app.post("/requirements/{requirement_id}/manufacturer-links/{package_id}/remove")
async def unlink_requirement_manufacturer(requirement_id: str, package_id: str) -> RedirectResponse:
    try:
        requirement = requirement_service.get_requirement(requirement_id)
        ops07_repository.unlink_manufacturer(
            requirement.bid_id, requirement_id, package_id, LOCAL_ACTOR
        )
    except RequirementNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RedirectResponse(f"/requirements/{quote(requirement_id)}#coverage", status_code=303)


@app.post("/requirements/{requirement_id}/work-links")
async def link_requirement_work(requirement_id: str, request: Request) -> RedirectResponse:
    form = await request.form()
    requirement = requirement_service.get_requirement(requirement_id)
    try:
        ops07_repository.link_work_item(
            bid_id=requirement.bid_id,
            work_item_id=str(form.get("work_item_id", "")),
            source_kind="requirement",
            source_id=requirement_id,
            purpose=str(form.get("purpose", "requirement closure")),
            actor=LOCAL_ACTOR,
        )
    except (ValueError, sqlite3.Error) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RedirectResponse(f"/requirements/{quote(requirement_id)}#coverage", status_code=303)


def _work_source_context(source_kind: str, source_id: str) -> dict[str, object]:
    if source_kind == "commercial":
        try:
            position = ops08_repository.detail(source_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Work source not found") from exc
        bid_id = str(position["bid_id"])
        return {
            "source_kind": source_kind,
            "source_id": source_id,
            "source_label": str(position["label"]),
            "bid_id": bid_id,
            "bid": bid_repository.get_bid(bid_id),
            "categories": list(WorkCategory),
        }
    sources = {
        "requirement": (requirement_repository.get, "title"),
        "scope": (scope_repository.get_scope_item, "title"),
        "interface": (scope_repository.get_interface, "title"),
        "manufacturer": (vendor_document_repository.get_package, "package_name"),
    }
    if source_kind not in sources:
        raise HTTPException(status_code=404, detail="Work source not found")
    getter, label = sources[source_kind]
    record = getter(source_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Work source not found")
    bid_id = str(record.bid_id)
    return {
        "source_kind": source_kind,
        "source_id": source_id,
        "source_label": str(getattr(record, label)),
        "bid_id": bid_id,
        "bid": bid_repository.get_bid(bid_id),
        "categories": list(WorkCategory),
    }


@app.get("/work/from/{source_kind}/{source_id}", response_class=HTMLResponse)
async def contextual_work_form(
    source_kind: str, source_id: str, return_to_bid: bool = False
) -> HTMLResponse:
    context = _work_source_context(source_kind, source_id)
    section = (
        BidWorkspaceSection.COMMERCIAL_CONTRACT
        if source_kind == "commercial"
        else BidWorkspaceSection.MANUFACTURERS_COVERAGE
        if source_kind == "manufacturer"
        else BidWorkspaceSection.REQUIREMENTS_SCOPE
    )
    return render(
        "contextual_work.html",
        **context,
        bid_return_path=workspace_path(str(context["bid_id"]), section),
        return_to_bid=return_to_bid,
        error=None,
        entered={},
    )


@app.post("/work/from/{source_kind}/{source_id}", response_class=HTMLResponse)
async def create_contextual_work(source_kind: str, source_id: str, request: Request) -> Response:
    form = dict(await request.form())
    context = _work_source_context(source_kind, source_id)
    try:
        create_work = (
            ops08_repository.create_linked_work
            if source_kind == "commercial"
            else ops07w_repository.create_linked_work
        )
        create_work(
            source_kind=source_kind,
            source_id=source_id,
            title=str(form.get("title") or ""),
            purpose=str(form.get("purpose") or ""),
            category=WorkCategory(str(form.get("category") or WorkCategory.OTHER.value)),
            actor=LOCAL_ACTOR,
            next_action_date=(
                date.fromisoformat(str(form["next_action_date"]))
                if form.get("next_action_date")
                else None
            ),
        )
    except (ValidationError, ValueError, sqlite3.Error) as exc:
        return render(
            "contextual_work.html",
            status_code=422,
            **context,
            error=validation_error_message(exc) if isinstance(exc, ValidationError) else str(exc),
            entered=form,
        )
    return_paths = {
        "requirement": f"/requirements/{quote(source_id)}#coverage",
        "scope": f"/scope-items/{quote(source_id)}?bid_id={quote(str(context['bid_id']))}",
        "interface": f"/interfaces/{quote(source_id)}?bid_id={quote(str(context['bid_id']))}",
        "manufacturer": f"/vendor-documents/packages/{quote(source_id)}",
        "commercial": f"/commercial/{quote(source_id)}",
    }
    if form.get("return_to_bid") == "1":
        section = (
            BidWorkspaceSection.COMMERCIAL_CONTRACT
            if source_kind == "commercial"
            else BidWorkspaceSection.MANUFACTURERS_COVERAGE
            if source_kind == "manufacturer"
            else BidWorkspaceSection.REQUIREMENTS_SCOPE
        )
        return_paths[source_kind] = workspace_path(str(context["bid_id"]), section)
    return RedirectResponse(return_paths[source_kind], status_code=303)


@app.post("/requirements/{requirement_id}/verification-links", response_class=HTMLResponse)
async def link_requirement_verification(requirement_id: str, request: Request) -> Response:
    form = await request.form()
    try:
        requirement = requirement_service.get_requirement(requirement_id)
        ops07w_repository.link_exact_evidence(
            requirement.bid_id,
            requirement_id,
            str(form.get("verification_row_id") or ""),
            LOCAL_ACTOR,
        )
    except RequirementNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, sqlite3.Error) as exc:
        if "not found" in str(exc).casefold():
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return render(
            "requirement_detail.html",
            status_code=422,
            **_requirement_detail_context(
                requirement_id,
                error=str(exc),
                entered={"verification_row_id": str(form.get("verification_row_id") or "")},
            ),
        )
    return RedirectResponse(f"/requirements/{quote(requirement_id)}#coverage", status_code=303)


@app.post("/requirements/{requirement_id}/verification-links/{verification_row_id}/remove")
async def unlink_requirement_verification(
    requirement_id: str, verification_row_id: str
) -> RedirectResponse:
    try:
        requirement = requirement_service.get_requirement(requirement_id)
        ops07w_repository.unlink_exact_evidence(
            requirement.bid_id, requirement_id, verification_row_id, LOCAL_ACTOR
        )
    except RequirementNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RedirectResponse(f"/requirements/{quote(requirement_id)}#coverage", status_code=303)


@app.get("/bids/{bid_id}/requirements-handover.csv")
async def requirements_handover(bid_id: str) -> Response:
    try:
        content = ops07_repository.handover_csv(bid_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        content,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="requirements-handover.csv"'},
    )


def _bids_browser_context(
    *,
    filters: BidPortfolioFilters | None = None,
    filter_values: dict[str, str] | None = None,
    error: str | None = None,
    filter_error: bool = False,
    entered: dict[str, object] | None = None,
    assessment_preview: object | None = None,
) -> dict[str, object]:
    selected = filters or BidPortfolioFilters()
    selected_values = filter_values or {
        "view": selected.view.value,
        "status": selected.status.value if selected.status else "",
        "classification": selected.classification.value if selected.classification else "",
        "readiness": selected.readiness.value,
        "owner": selected.owner or "",
        "deadline": selected.deadline.value,
    }
    portfolio = bid_control_center_service.portfolio(selected, as_of=_working_date())
    portfolio_views = list(BidPortfolioView)
    bid_statuses = list(BidStatus)
    readiness_filters = list(BidReadinessFilter)
    deadline_filters = list(BidDeadlineAttention)
    levels = list(BidLevel)
    minimum_level = getattr(assessment_preview, "level", None)
    higher_classifications = (
        levels[levels.index(minimum_level) + 1 :] if minimum_level in levels else []
    )
    return {
        "portfolio": portfolio,
        "bids": [row.bid for row in portfolio.rows],
        "filters": selected,
        "filter_values": selected_values,
        "gate_labels": GATE_LABELS,
        "portfolio_views": portfolio_views,
        "portfolio_view_values": [item.value for item in portfolio_views],
        "bid_statuses": bid_statuses,
        "bid_status_values": [item.value for item in bid_statuses],
        "readiness_filters": readiness_filters,
        "readiness_filter_values": [item.value for item in readiness_filters],
        "deadline_filters": deadline_filters,
        "deadline_filter_values": [item.value for item in deadline_filters],
        "classification_values": [item.value for item in levels],
        "customer_types": list(CustomerType),
        "classifications": levels,
        "higher_classifications": higher_classifications,
        "governance_guides": governance_level_guides(),
        "risk_triggers": list(RiskTrigger),
        "assessment_preview": assessment_preview,
        "error": error,
        "filter_error": filter_error,
        "entered": entered or {},
    }


def _next_browser_bid_id() -> str:
    year = _working_date().year
    prefix = f"B-{year}-"
    used = {
        int(bid.bid_id.removeprefix(prefix))
        for bid in bid_repository.list_bids()
        if bid.bid_id.startswith(prefix) and bid.bid_id.removeprefix(prefix).isdigit()
    }
    for number in range(1, 10_000):
        if number not in used:
            return f"{prefix}{number:04d}"
    raise ValueError(f"No bid identifiers remain available for {year}")


@app.get("/bids", response_class=HTMLResponse)
async def bids_projects(
    view: str = "current",
    status: str | None = None,
    classification: str | None = None,
    readiness: str = "any",
    owner: str | None = None,
    deadline: str = "any",
) -> HTMLResponse:
    raw_filters = {
        "view": view,
        "status": status or "",
        "classification": classification or "",
        "readiness": readiness,
        "owner": owner or "",
        "deadline": deadline,
    }
    try:
        filters = BidPortfolioFilters.model_validate(
            {
                "view": view,
                "status": status or None,
                "classification": classification or None,
                "readiness": readiness,
                "owner": owner or None,
                "deadline": deadline,
            }
        )
    except ValidationError as exc:
        return render(
            "bids.html",
            status_code=422,
            **_bids_browser_context(
                filter_values=raw_filters,
                error=validation_error_message(exc),
                filter_error=True,
            ),
        )
    return render(
        "bids.html",
        **_bids_browser_context(filters=filters, filter_values=raw_filters),
    )


@app.post("/bids", response_class=HTMLResponse)
async def create_bid_project(request: Request) -> Response:
    submitted = await request.form()
    form = dict(submitted)
    form["risk_triggers"] = submitted.getlist("risk_triggers")
    now = datetime.now(WORKING_TIMEZONE)
    try:
        required = {
            "project_name": "Bid title",
            "customer": "Customer",
            "customer_type": "Customer type",
            "sales_owner": "Sales owner",
            "bc_owner": "Bids & Contracts owner",
            "release_date": "Release date",
            "customer_due_date": "Customer due date",
            "internal_due_date": "Internal due date",
            "estimated_value": "Estimated value",
        }
        missing = [label for key, label in required.items() if not str(form.get(key) or "").strip()]
        if missing:
            raise ValueError(f"Required field(s): {', '.join(missing)}")
        assessment_input = ClassificationAssessmentCommand.model_validate(
            {
                "estimated_value": form["estimated_value"],
                "customer_type": form["customer_type"],
                "is_epc_epcm": str(form.get("is_epc_epcm") or "") == "1",
                "strategic_customer": str(form.get("strategic_customer") or "") == "1",
                "triggers": form["risk_triggers"],
                "selected_level": BidLevel.LEVEL_0,
                "override_rationale": None,
            }
        )
        classification_result = ops07w_repository.assess(assessment_input)
        selected_level = form.get("classification") or classification_result.level.value
        classification_command = ClassificationAssessmentCommand.model_validate(
            {
                "estimated_value": form["estimated_value"],
                "customer_type": form["customer_type"],
                "is_epc_epcm": str(form.get("is_epc_epcm") or "") == "1",
                "strategic_customer": str(form.get("strategic_customer") or "") == "1",
                "triggers": form["risk_triggers"],
                "selected_level": selected_level,
                "override_rationale": form.get("classification_override_rationale") or None,
            }
        )
        ops07w_repository.validate_level(classification_command, classification_result)
        payload = {
            key: value
            for key, value in form.items()
            if value not in ("", None)
            and key
            not in {
                "strategic_customer",
                "is_epc_epcm",
                "classification_override_rationale",
            }
        }
        payload.update(
            {
                "bid_id": _next_browser_bid_id(),
                "currency": str(form.get("currency") or "CAD").strip().upper(),
                "current_gate": Gate.G0,
                "status": BidStatus.ACTIVE,
                "risk_triggers": classification_command.triggers,
                "classification": classification_command.selected_level,
                "inference_policy": InferencePolicy.LOCAL_ONLY,
                "created_at": now,
                "updated_at": now,
            }
        )
        bid = Bid.model_validate(payload)
        ops07w_repository.create_bid_with_assessment(bid, classification_command, LOCAL_ACTOR)
    except (ValidationError, ValueError, sqlite3.Error) as exc:
        return render(
            "bids.html",
            status_code=422,
            **_bids_browser_context(
                error=validation_error_message(exc)
                if isinstance(exc, ValidationError)
                else str(exc),
                entered=form,
            ),
        )
    return RedirectResponse(f"/bids/{bid.bid_id}", status_code=303)


@app.post("/bids/classification-assessment", response_class=HTMLResponse)
async def preview_bid_classification(request: Request) -> HTMLResponse:
    submitted = await request.form()
    form = dict(submitted)
    form["risk_triggers"] = submitted.getlist("risk_triggers")
    try:
        allowed_fields = {
            "anticipated_award_date",
            "bc_owner",
            "classification",
            "classification_override_rationale",
            "currency",
            "customer",
            "customer_due_date",
            "customer_type",
            "estimated_value",
            "executive_sponsor",
            "internal_due_date",
            "is_epc_epcm",
            "location",
            "margin_range",
            "project_name",
            "release_date",
            "risk_triggers",
            "sales_owner",
            "strategic_customer",
            "win_probability",
        }
        unknown_fields = sorted(set(form) - allowed_fields)
        if unknown_fields:
            raise ValueError("Unrecognized classification field(s): " + ", ".join(unknown_fields))
        assessment_input = ClassificationAssessmentCommand.model_validate(
            {
                "estimated_value": form.get("estimated_value"),
                "customer_type": form.get("customer_type"),
                "is_epc_epcm": str(form.get("is_epc_epcm") or "") == "1",
                "strategic_customer": str(form.get("strategic_customer") or "") == "1",
                "triggers": form["risk_triggers"],
                "selected_level": BidLevel.LEVEL_0,
                "override_rationale": None,
            }
        )
        result = ops07w_repository.assess(assessment_input)
        selected = form.get("classification") or result.level.value
        command = ClassificationAssessmentCommand.model_validate(
            {
                "estimated_value": form.get("estimated_value"),
                "customer_type": form.get("customer_type"),
                "is_epc_epcm": str(form.get("is_epc_epcm") or "") == "1",
                "strategic_customer": str(form.get("strategic_customer") or "") == "1",
                "triggers": form["risk_triggers"],
                "selected_level": selected,
                "override_rationale": form.get("classification_override_rationale") or None,
            }
        )
        ops07w_repository.validate_level(command, result)
    except (ValidationError, ValueError) as exc:
        return render(
            "bids.html",
            status_code=422,
            **_bids_browser_context(
                error=validation_error_message(exc)
                if isinstance(exc, ValidationError)
                else str(exc),
                entered=form,
            ),
        )
    return render(
        "bids.html",
        **_bids_browser_context(entered=form, assessment_preview=result),
    )


@app.get("/bids/{bid_id}/classification", response_class=HTMLResponse)
async def bid_classification_review(bid_id: str) -> HTMLResponse:
    bid = bid_repository.get_bid(bid_id)
    if bid is None:
        raise HTTPException(status_code=404, detail="Bid not found")
    return render(
        "classification_review.html",
        bid=bid,
        assessment=ops07w_repository.latest_assessment(bid_id),
        risk_triggers=list(RiskTrigger),
        classifications=list(BidLevel),
        governance_guides=governance_level_guides(),
        error=None,
        entered={},
    )


@app.post("/bids/{bid_id}/classification", response_class=HTMLResponse)
async def reassess_bid_classification(bid_id: str, request: Request) -> Response:
    bid = bid_repository.get_bid(bid_id)
    if bid is None:
        raise HTTPException(status_code=404, detail="Bid not found")
    submitted = await request.form()
    form = dict(submitted)
    form["risk_triggers"] = submitted.getlist("risk_triggers")
    try:
        command = ClassificationAssessmentCommand.model_validate(
            {
                "estimated_value": bid.estimated_value,
                "customer_type": bid.customer_type.value,
                "is_epc_epcm": bid.customer_type in {CustomerType.EPC, CustomerType.EPCM},
                "strategic_customer": str(form.get("strategic_customer") or "") == "1",
                "triggers": form["risk_triggers"],
                "selected_level": form.get("classification"),
                "override_rationale": form.get("classification_override_rationale") or None,
            }
        )
        ops07w_repository.record_assessment(
            bid_id,
            command,
            LOCAL_ACTOR,
            expected_bid_updated_at=str(form.get("expected_bid_updated_at") or ""),
        )
    except (ValidationError, ValueError, sqlite3.Error) as exc:
        return render(
            "classification_review.html",
            status_code=422,
            bid=bid,
            assessment=ops07w_repository.latest_assessment(bid_id),
            risk_triggers=list(RiskTrigger),
            classifications=list(BidLevel),
            governance_guides=governance_level_guides(),
            error=validation_error_message(exc) if isinstance(exc, ValidationError) else str(exc),
            entered=form,
        )
    return RedirectResponse(f"/bids/{quote(bid_id)}#navigator", status_code=303)


@app.get("/bids/{bid_id}", response_class=HTMLResponse)
async def bid_detail(request: Request, bid_id: str) -> HTMLResponse:
    return _render_bid_workspace(bid_id, BidWorkspaceSection.OVERVIEW)


def _bid_workspace_context(
    bid_id: str,
    section: BidWorkspaceSection,
) -> dict[str, object]:
    as_of_date = _working_date()
    workspace = bid_control_center_service.workspace(bid_id, as_of=as_of_date)
    requirements = requirement_service.list_requirements(bid_id=bid_id, as_of_date=as_of_date)
    scope_items = scope_repository.list_scope_items(bid_id)
    navigator_counts = ops07w_repository.navigator_counts(bid_id)
    classification_assessment = ops07w_repository.latest_assessment(bid_id)
    governance_guides = governance_level_guides()
    navigator = build_navigator(
        bid_id,
        navigator_counts,
        workspace.readiness.verdict.value,
        workspace.bid.bc_owner,
    )
    intake_summary = bid_package_service.summary(bid_id)
    intake_release_rows = list(intake_summary.releases)
    intake_release_details = [
        bid_package_service.release_detail(bid_id, str(row["release_id"]))
        for row in intake_release_rows
    ]
    files_requiring_review = sum(
        1
        for detail in intake_release_details
        for file_row in detail["files"]
        if file_row.get("analysis_eligibility") == AnalysisEligibility.NOT_ASSESSED.value
    )
    intake_facts = PackageIntakeFacts(
        releases_received=len(intake_release_rows),
        releases_incorporated=sum(
            1 for row in intake_release_rows if bool(row.get("incorporated"))
        ),
        files_requiring_review=files_requiring_review,
        expected_missing=sum(
            1 for item in intake_summary.attention if item.code == "EXPECTED_RELEASE_MISSING"
        ),
        basis_published=intake_summary.current_snapshot is not None,
        latest_release_id=(
            str(intake_release_rows[0]["release_id"]) if intake_release_rows else None
        ),
    )
    outstanding = outstanding_items(
        workspace.blockers,
        intake_attention=intake_summary.attention,
        package_imported=bool(intake_release_rows),
        bid_id=bid_id,
        owner=workspace.bid.bc_owner,
        due_date=workspace.bid.internal_due_date,
    )
    workflow = project_bid_workflow(
        bid_id,
        bid_status=workspace.bid.status.value,
        navigator=navigator,
        intake=intake_facts,
        readiness_verdict=workspace.readiness.verdict.value,
        outstanding=outstanding,
    )
    commercial_positions = ops08_repository.workspace(bid_id)
    link_counts = _position_link_counts(bid_id)
    for position in commercial_positions:
        position.update(
            link_counts.get(
                str(position["position_id"]),
                {"risk_count": 0, "approval_count": 0, "negotiation_count": 0},
            )
        )
    package_rows = []
    for package in vendor_document_repository.list_packages(bid_id):
        package_requirements = vendor_document_repository.list_requirements(package.package_id)
        package_rows.append(
            {
                "package": package,
                "requirements": package_requirements,
                "readiness": vendor_document_service.readiness(package.package_id),
                "linked_customer_requirements": sum(
                    len(
                        ops07w_repository.package_requirement_evidence(package.package_id).get(
                            row.requirement_id, []
                        )
                    )
                    for row in package_requirements
                ),
            }
        )
    context: dict[str, object] = {
        "workspace": workspace,
        "bid": workspace.bid,
        "readiness": workspace.readiness,
        "section": section,
        "bid_section_label": BID_SECTION_LABELS[section],
        "workspace_sections": list(BidWorkspaceSection),
        "requirements": requirements,
        "coverage": requirement_service.coverage(bid_id=bid_id, as_of_date=as_of_date),
        "documents": document_service.list_register_entries(bid_id=bid_id),
        "scope_items": scope_items,
        "interfaces": scope_repository.list_interfaces(bid_id),
        "scope_projection": scope_service.projection(bid_id, as_of_date),
        "scope_areas": list(ScopeArea),
        "offer_positions": list(OfferPosition),
        "pricing_states": list(PricingState),
        "materialities": list(Materiality),
        "vendor_packages": vendor_document_repository.list_packages(bid_id),
        "manufacturer_packages": package_rows,
        "supplier_metrics": supplier_service.metrics(bid_id),
        "commercial_metrics": commercial_service.metrics(
            bid_id,
            as_of_date,
            [item.model_dump(mode="json") for item in scope_items],
        ),
        "contract_risk_metrics": contract_risk_service.metrics(bid_id, as_of_date),
        "approval_metrics": approval_service.metrics(
            bid_id,
            datetime.combine(as_of_date, datetime.min.time(), tzinfo=WORKING_TIMEZONE),
        ),
        "deliverable_metrics": deliverable_service.metrics(bid_id, as_of_date),
        "proposal_metrics": proposal_service.metrics(bid_id),
        "negotiation_metrics": negotiation_service.metrics(bid_id),
        "commercial_positions": commercial_positions,
        "commercial_position_readiness": ops08_repository.readiness(bid_id),
        "commercial_dispositions": list(PositionDisposition),
        "proposal_inputs": _proposal_input_rows(bid_id),
        "proposal_control": proposal_exchange_service.assess(bid_id),
        "proposal_control_history": proposal_exchange_service.history(bid_id),
        "ops07_error": None,
        "ops07_entered": {},
        "selected_requirement_targets": [],
        "classification_assessment": classification_assessment,
        "governance_guide": next(
            guide for guide in governance_guides if guide.level is workspace.bid.classification
        ),
        "workflow": workflow,
        "outstanding": outstanding,
        "intake_error": None,
        "intake_entered": {},
        "intake_preview": None,
        "intake_retained_values": [],
        "intake_import": None,
    }
    if section is BidWorkspaceSection.PACKAGE_INTAKE:
        release_rows = intake_release_rows
        latest_partial = next((row for row in release_rows if row.get("partial")), None)
        latest_full = next((row for row in release_rows if row.get("incorporated")), None)
        latest_notice = intake_summary.notices[0] if intake_summary.notices else None
        latest_received = release_rows[0] if release_rows else None
        latest_known_addendum = next(
            (
                row
                for row in [*intake_summary.notices, *release_rows]
                if row.get("release_type") == ReleaseType.ADDENDUM.value
            ),
            None,
        )
        release_details = intake_release_details
        unresolved_directives = sum(
            1
            for detail in release_details
            for directive in detail["directives"]
            if directive.get("disposition_status") in {None, "UNRESOLVED"}
        )
        context.update(
            {
                "intake": intake_summary,
                "intake_status": {
                    "latest_notice": latest_notice,
                    "latest_known_addendum": latest_known_addendum,
                    "latest_received": latest_received,
                    "latest_partial": latest_partial,
                    "latest_full": latest_full,
                    "portal_email_discrepancy": any(
                        item.code == "PORTAL_EMAIL_DISCREPANCY" for item in intake_summary.attention
                    ),
                    "proposal_basis_stale": (
                        "supporting_documents"
                        in proposal_exchange_service.assess(bid_id).changed_areas
                    ),
                },
                "intake_operational": {
                    "received_not_incorporated": sum(
                        1 for row in release_rows if not bool(row["incorporated"])
                    ),
                    "expected_missing": sum(
                        1
                        for item in intake_summary.attention
                        if item.code == "EXPECTED_RELEASE_MISSING"
                    ),
                    "files_requiring_review": files_requiring_review,
                    "unresolved_directives": unresolved_directives,
                    "outstanding_acknowledgements": sum(
                        1
                        for item in intake_summary.attention
                        if item.code == "ACKNOWLEDGEMENT_OUTSTANDING"
                    ),
                },
                "intake_sources": bid_package_storage.source_choices(),
                "release_types": list(ReleaseType),
                "release_channels": list(ReleaseChannel),
                "notice_expectations": list(NoticeExpectation),
                "content_forms": list(ContentForm),
                "analysis_eligibilities": list(AnalysisEligibility),
                "directive_materialities": list(DirectiveMateriality),
                "directive_dispositions": list(DirectiveDispositionStatus),
                "acknowledgement_types": list(AcknowledgementEventType),
                "directive_type_choices": DIRECTIVE_TYPE_CHOICES,
                "document_categories": list(DocumentCategory),
                "document_category_label": document_category_label,
                "directive_type_label": directive_type_label,
                "content_form_label": content_form_label,
                "eligibility_label": eligibility_label,
                "human_bytes": human_bytes,
                "controlled_versions": [
                    {"document": entry.document, "version": entry.current_version}
                    for entry in document_service.list_register_entries(bid_id=bid_id)
                    if entry.document is not None and entry.current_version is not None
                ],
                "intake_approvals": [
                    approval
                    for approval in bid_repository.list_approvals(bid_id)
                    if approval.obtained
                ],
                "intake_approval_routes": [
                    route
                    for route in approval_repository.routes(bid_id)
                    if route["state"] == "APPROVED"
                ],
                "intake_work_items": bid_package_repository.linked_work_items(bid_id),
                "new_operation_id": lambda: str(uuid.uuid4()),
            }
        )
    if section is BidWorkspaceSection.AWARD_HANDOVER:
        context["handover_report"] = bid_handover_service.report(
            bid_id,
            gate_verdict=workspace.readiness.verdict.value,
            gate_blockers=[
                f"{blocker.heading} — {blocker.detail}" for blocker in workspace.blockers
            ],
            generated_by=f"ContractIQ server · {LOCAL_ACTOR}",
        )
    return context


def _render_bid_workspace(
    bid_id: str,
    section: BidWorkspaceSection,
) -> HTMLResponse:
    try:
        context = _bid_workspace_context(bid_id, section)
    except BidNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return render("bid_detail.html", **context)


@app.get("/bids/{bid_id}/requirements-scope", response_class=HTMLResponse)
async def bid_requirements_scope(bid_id: str) -> HTMLResponse:
    return _render_bid_workspace(bid_id, BidWorkspaceSection.REQUIREMENTS_SCOPE)


@app.get("/bids/{bid_id}/package-intake-addenda", response_class=HTMLResponse)
async def bid_package_intake_addenda(bid_id: str) -> HTMLResponse:
    return _render_bid_workspace(bid_id, BidWorkspaceSection.PACKAGE_INTAKE)


def _intake_import_context(
    bid_id: str,
    *,
    source_root_alias: str | None = None,
    source_folder: str | None = None,
    mode: str = "initial",
) -> dict[str, object]:
    """Build the safe folder-selection view without mutating intake evidence."""
    context = _bid_workspace_context(bid_id, BidWorkspaceSection.PACKAGE_INTAKE)
    normalized_mode = mode if mode in {"initial", "addendum"} else "initial"
    inboxes = bid_package_service.intake_inboxes(bid_id)
    selected_folder = None
    if source_root_alias and source_folder:
        for inbox in inboxes:
            if inbox.source_root_alias != source_root_alias:
                continue
            selected_folder = next(
                (item for item in inbox.folders if item.folder_name == source_folder), None
            )
            break
        if selected_folder is None or selected_folder.warning is not None:
            raise ValueError("Select an available intake folder from the inbox list")
    context["intake_import"] = {
        "mode": normalized_mode,
        "inboxes": inboxes,
        "selected_folder": selected_folder,
    }
    return context


@app.get("/bids/{bid_id}/package-intake-addenda/import", response_class=HTMLResponse)
async def select_bid_package_intake_folder(
    bid_id: str,
    source_root_alias: str | None = None,
    source_folder: str | None = None,
    mode: str = "initial",
) -> HTMLResponse:
    try:
        return render(
            "bid_detail.html",
            **_intake_import_context(
                bid_id,
                source_root_alias=source_root_alias,
                source_folder=source_folder,
                mode=mode,
            ),
        )
    except (BidNotFoundError, ValueError, IntakeLimitError, UnsafeIntakeSourceError) as exc:
        if isinstance(exc, BidNotFoundError):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _intake_error_response(
            bid_id,
            exc,
            entered={
                "source_root_alias": source_root_alias or "",
                "source_folder": source_folder or "",
                "intake_mode": mode,
            },
        )


def _intake_datetime(value: str, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid date and time") from exc
    return (
        parsed.replace(tzinfo=WORKING_TIMEZONE) if parsed.tzinfo is None else parsed
    ).astimezone(UTC)


def _intake_confirmation_token(
    bid_id: str,
    values: dict[str, str],
    preview: ReleasePreview,
) -> str:
    payload = json.dumps(
        {
            "context": "contractiq-ops11-confirmation-v1",
            "bid_id": bid_id,
            "values": values,
            "fingerprint": preview.source_fingerprint,
            "operation_id": str(uuid.uuid4()),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    signature = hmac.digest(_source_operation_key(), payload, "sha256")
    return base64.urlsafe_b64encode(payload + signature).decode().rstrip("=")


def _decode_intake_confirmation(token: str, bid_id: str) -> dict[str, object]:
    try:
        padded = token + "=" * (-len(token) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode())
        canonical = base64.urlsafe_b64encode(decoded).decode().rstrip("=")
        if not hmac.compare_digest(token, canonical):
            raise ValueError
        payload, signature = decoded[:-32], decoded[-32:]
        if len(signature) != 32 or not hmac.compare_digest(
            signature, hmac.digest(_source_operation_key(), payload, "sha256")
        ):
            raise ValueError
        values = json.loads(payload)
        if values.get("context") != "contractiq-ops11-confirmation-v1":
            raise ValueError
        if values.get("bid_id") != bid_id or not isinstance(values.get("values"), dict):
            raise ValueError
        return dict(values)
    except (ValueError, TypeError, json.JSONDecodeError, binascii.Error) as exc:
        raise ValueError("Release confirmation is invalid for this Bid; preview again") from exc


def _intake_preview_display(preview: ReleasePreview, token: str) -> dict[str, object]:
    """Return business-facing inventory facts without exposing managed storage details."""
    duplicate_counts: dict[tuple[int, str], int] = {}
    for item in preview.files:
        key = (item.byte_size, item.sha256)
        duplicate_counts[key] = duplicate_counts.get(key, 0) + 1
    archives = {".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz"}
    files = []
    for item in preview.files:
        row = item.model_dump(mode="json")
        row["is_archive"] = item.extension.casefold() in archives
        row["is_duplicate"] = duplicate_counts[(item.byte_size, item.sha256)] > 1
        row["needs_identification"] = item.detected_media_type is None
        files.append(row)
    directories = sorted(
        {
            str(PurePosixPath(item.original_relative_path).parent)
            for item in preview.files
            if str(PurePosixPath(item.original_relative_path).parent) != "."
        }
    )
    warnings: list[str] = []
    if any(bool(item["is_archive"]) for item in files):
        warnings.append("Archives will be retained as received but will not be expanded.")
    if any(bool(item["needs_identification"]) for item in files):
        warnings.append(
            "Some file types need human identification before analysis eligibility is set."
        )
    if any(bool(item["is_duplicate"]) for item in files):
        warnings.append(
            "Potential exact-byte duplicates are marked for review; "
            "each occurrence remains evidence."
        )
    return {
        "token": token,
        "folder_name": preview.source_folder or "",
        "file_count": preview.file_count,
        "total_bytes": preview.total_bytes,
        "directories": directories,
        "files": files,
        "warnings": warnings,
    }


def _intake_error_response(
    bid_id: str,
    error: Exception | str,
    *,
    status_code: int = 422,
    entered: dict[str, str] | None = None,
    preview: dict[str, object] | None = None,
    release_id: str | None = None,
) -> HTMLResponse:
    try:
        context = _intake_import_context(
            bid_id,
            source_root_alias=(entered or {}).get("source_root_alias") or None,
            source_folder=(entered or {}).get("source_folder") or None,
            mode=(entered or {}).get("intake_mode", "initial"),
        )
    except (ValueError, IntakeLimitError, UnsafeIntakeSourceError):
        context = _bid_workspace_context(bid_id, BidWorkspaceSection.PACKAGE_INTAKE)
    context["intake_error"] = (
        validation_error_message(error) if isinstance(error, ValidationError) else str(error)
    )
    context["intake_entered"] = entered or {}
    retained_labels = {
        "release_type": "Release kind",
        "exact_customer_reference": "Customer reference",
        "customer_issue_date": "Customer issue date",
        "received_at": "Received at",
        "received_channel": "Received channel",
        "source_folder": "Selected intake folder",
        "note": "Note",
        "channel": "Channel",
        "observed_at": "Observed at",
        "checked_at": "Checked at",
        "expectation": "Expectation",
        "summary": "Summary",
        "evidence_reference": "Evidence reference",
        "result": "Result",
        "content_form": "Content form",
        "analysis_eligibility": "Analysis eligibility",
        "confidence": "Confidence",
        "exclusion_reason": "Exclusion reason",
        "directive_type": "Directive type",
        "description": "Description",
        "materiality": "Materiality",
        "status": "Disposition",
        "rationale": "Rationale",
        "event_type": "Acknowledgement event",
        "due_at": "Due at",
        "acknowledgement_reference": "Acknowledgement reference",
        "label": "Snapshot label",
        "title": "Work-item title",
    }
    context["intake_retained_values"] = [
        (retained_labels[key], value)
        for key, value in (entered or {}).items()
        if key in retained_labels and value
    ]
    context["intake_preview"] = preview
    if release_id is not None:
        try:
            context["selected_release"] = decorate_release(
                bid_package_service.release_detail(bid_id, release_id)
            )
        except IntakeNotFoundError:
            pass
    return render("bid_detail.html", status_code=status_code, **context)


@app.post("/bids/{bid_id}/package-intake-addenda/preview", response_class=HTMLResponse)
async def preview_bid_release(bid_id: str, request: Request) -> HTMLResponse:
    raw = await request.form()
    entered = {key: str(value) for key, value in raw.items() if isinstance(value, str)}
    try:
        if not entered.get("source_folder"):
            raise ValueError("Select an available intake folder before previewing")
        preview = bid_package_service.preview(
            bid_id,
            entered.get("source_root_alias", ""),
            entered.get("source_folder"),
        )
        if not preview.files:
            raise ValueError("The selected intake location contains no files")
        token = _intake_confirmation_token(bid_id, entered, preview)
        context = _intake_import_context(
            bid_id,
            source_root_alias=entered.get("source_root_alias"),
            source_folder=entered.get("source_folder"),
            mode=entered.get("intake_mode", "initial"),
        )
        context["intake_entered"] = entered
        context["intake_preview"] = _intake_preview_display(preview, token)
        return render("bid_detail.html", **context)
    except (ValueError, ValidationError, IntakeLimitError, UnsafeIntakeSourceError) as exc:
        return _intake_error_response(bid_id, exc, entered=entered)


@app.post("/bids/{bid_id}/package-intake-addenda/register")
async def register_bid_release(bid_id: str, request: Request) -> Response:
    raw = await request.form()
    entered = {key: str(value) for key, value in raw.items() if isinstance(value, str)}
    try:
        token = _decode_intake_confirmation(entered.get("confirmation_token", ""), bid_id)
        values = {str(key): str(value) for key, value in dict(token["values"]).items()}
        entered = values
        release = bid_package_service.register_release(
            ReleaseRegistration(
                bid_id=bid_id,
                release_type=ReleaseType(values.get("release_type", "")),
                exact_customer_reference=values.get("exact_customer_reference") or None,
                customer_issue_date=(
                    date.fromisoformat(values["customer_issue_date"])
                    if values.get("customer_issue_date")
                    else None
                ),
                received_at=datetime.now(UTC),
                received_channel=ReleaseChannel(values.get("received_channel", "")),
                source_root_alias=values.get("source_root_alias", ""),
                source_folder=values.get("source_folder") or None,
                expected_source_fingerprint=str(token["fingerprint"]),
                operation_id=str(token["operation_id"]),
                note=values.get("note") or None,
            ),
            LOCAL_ACTOR,
        )
    except (
        ValueError,
        ValidationError,
        IntakeLimitError,
        UnsafeIntakeSourceError,
        SourceChangedError,
        IntakePublicationError,
        sqlite3.Error,
    ) as exc:
        return _intake_error_response(bid_id, exc, entered=entered)
    return RedirectResponse(
        f"/bids/{quote(bid_id)}/package-intake-addenda/releases/{quote(str(release['release_id']))}",
        status_code=303,
    )


@app.post("/bids/{bid_id}/package-intake-addenda/notices")
async def record_bid_release_notice(bid_id: str, request: Request) -> Response:
    raw = await request.form()
    entered = {key: str(value) for key, value in raw.items() if isinstance(value, str)}
    try:
        bid_package_service.record_notice(
            ReleaseNoticeCreate(
                bid_id=bid_id,
                release_type=ReleaseType(entered.get("release_type", "")),
                exact_customer_reference=entered.get("exact_customer_reference") or None,
                customer_issue_date=(
                    date.fromisoformat(entered["customer_issue_date"])
                    if entered.get("customer_issue_date")
                    else None
                ),
                expected_receipt_date=(
                    date.fromisoformat(entered["expected_receipt_date"])
                    if entered.get("expected_receipt_date")
                    else None
                ),
                channel=ReleaseChannel(entered.get("channel", "")),
                observed_at=datetime.now(UTC),
                expectation=NoticeExpectation(entered.get("expectation", "")),
                summary=entered.get("summary", ""),
                evidence_reference=entered.get("evidence_reference") or None,
                supersedes_notice_id=entered.get("supersedes_notice_id") or None,
                operation_id=entered.get("operation_id", ""),
            ),
            LOCAL_ACTOR,
        )
    except (ValueError, ValidationError, StaleIntakeError, sqlite3.Error) as exc:
        return _intake_error_response(bid_id, exc, entered=entered)
    return RedirectResponse(f"/bids/{quote(bid_id)}/package-intake-addenda", status_code=303)


@app.post("/bids/{bid_id}/package-intake-addenda/channel-checks")
async def record_bid_release_channel_check(bid_id: str, request: Request) -> Response:
    raw = await request.form()
    entered = {key: str(value) for key, value in raw.items() if isinstance(value, str)}
    try:
        bid_package_service.record_channel_check(
            ChannelCheckCreate(
                bid_id=bid_id,
                channel=ReleaseChannel(entered.get("channel", "")),
                checked_at=_intake_datetime(entered.get("checked_at", ""), "Checked at"),
                observed_customer_reference=entered.get("observed_customer_reference") or None,
                customer_issue_date=(
                    date.fromisoformat(entered["customer_issue_date"])
                    if entered.get("customer_issue_date")
                    else None
                ),
                result=entered.get("result", ""),
                evidence_reference=entered.get("evidence_reference") or None,
                operation_id=entered.get("operation_id", ""),
            ),
            LOCAL_ACTOR,
        )
    except (ValueError, ValidationError, sqlite3.Error) as exc:
        return _intake_error_response(bid_id, exc, entered=entered)
    return RedirectResponse(f"/bids/{quote(bid_id)}/package-intake-addenda", status_code=303)


@app.post("/bids/{bid_id}/package-intake-addenda/notice-links")
async def link_bid_release_notice(bid_id: str, request: Request) -> Response:
    raw = await request.form()
    entered = {key: str(value) for key, value in raw.items() if isinstance(value, str)}
    try:
        bid_package_service.link_notice(
            bid_id,
            entered.get("notice_id", ""),
            entered.get("release_id", ""),
            entered.get("relationship", ""),
            entered.get("operation_id", ""),
            LOCAL_ACTOR,
        )
    except (ValueError, IntakeConflictError, IntakeNotFoundError, sqlite3.Error) as exc:
        return _intake_error_response(bid_id, exc, entered=entered)
    return RedirectResponse(f"/bids/{quote(bid_id)}/package-intake-addenda", status_code=303)


@app.get(
    "/bids/{bid_id}/package-intake-addenda/releases/{release_id}",
    response_class=HTMLResponse,
)
async def review_bid_release(bid_id: str, release_id: str) -> HTMLResponse:
    try:
        context = _bid_workspace_context(bid_id, BidWorkspaceSection.PACKAGE_INTAKE)
        context["selected_release"] = decorate_release(
            bid_package_service.release_detail(bid_id, release_id)
        )
        return render("bid_detail.html", **context)
    except IntakeNotFoundError as exc:
        return _intake_error_response(bid_id, exc, status_code=404)


@app.post("/bids/{bid_id}/package-intake-addenda/files/{file_id}/dispositions")
async def review_bid_received_file(bid_id: str, file_id: str, request: Request) -> Response:
    raw = await request.form()
    entered = {key: str(value) for key, value in raw.items() if isinstance(value, str)}
    release_id = entered.get("release_id")
    try:
        bid_package_service.classify_file(
            bid_id,
            file_id,
            FileDispositionCreate(
                content_form=ContentForm(entered.get("content_form", "")),
                classification_method=ClassificationMethod.HUMAN_REVIEW,
                confidence=(float(entered["confidence"]) if entered.get("confidence") else None),
                analysis_eligibility=AnalysisEligibility(entered.get("analysis_eligibility", "")),
                exclusion_reason=entered.get("exclusion_reason") or None,
                duplicate_of_file_id=entered.get("duplicate_of_file_id") or None,
                supersedes_event_id=entered.get("supersedes_event_id") or None,
                operation_id=entered.get("operation_id", ""),
            ),
            LOCAL_ACTOR,
        )
    except (
        ValueError,
        ValidationError,
        IntakeConflictError,
        StaleIntakeError,
        sqlite3.Error,
    ) as exc:
        return _intake_error_response(bid_id, exc, entered=entered, release_id=release_id)
    return RedirectResponse(
        f"/bids/{quote(bid_id)}/package-intake-addenda/releases/{quote(str(release_id))}#files",
        status_code=303,
    )


@app.post("/bids/{bid_id}/package-intake-addenda/releases/{release_id}/bulk-dispositions")
async def review_bid_received_files_bulk(
    bid_id: str, release_id: str, request: Request
) -> Response:
    """Apply one selected review decision without allowing a partial package update."""
    raw = await request.form()
    entered = {
        key: str(value)
        for key, value in raw.items()
        if isinstance(value, str) and key not in {"file_ids", "review_tokens"}
    }
    try:
        tokens: dict[str, str] = {}
        for value in raw.getlist("review_tokens"):
            file_id, separator, event_id = str(value).partition("|")
            if not separator or not file_id or not event_id or file_id in tokens:
                raise ValueError("the selected file review tokens are invalid; refresh the package")
            tokens[file_id] = event_id
        file_ids = [str(value) for value in raw.getlist("file_ids")]
        items = tuple(
            BulkFileReviewItem(
                file_id=file_id,
                supersedes_event_id=tokens.get(file_id, ""),
            )
            for file_id in file_ids
        )
        bid_package_service.bulk_classify_files(
            bid_id,
            BulkFileDispositionCreate(
                release_id=release_id,
                items=items,
                analysis_eligibility=AnalysisEligibility(
                    str(raw.get("analysis_eligibility") or "")
                ),
                content_form=(
                    ContentForm(str(raw.get("content_form"))) if raw.get("content_form") else None
                ),
                exclusion_reason=str(raw.get("exclusion_reason") or "") or None,
                operation_id=str(raw.get("operation_id") or ""),
            ),
            LOCAL_ACTOR,
        )
    except (
        ValueError,
        ValidationError,
        IntakeConflictError,
        IntakeNotFoundError,
        StaleIntakeError,
        sqlite3.Error,
    ) as exc:
        return _intake_error_response(bid_id, exc, entered=entered, release_id=release_id)
    return RedirectResponse(
        f"/bids/{quote(bid_id)}/package-intake-addenda/releases/{quote(release_id)}#files",
        status_code=303,
    )


@app.post("/bids/{bid_id}/package-intake-addenda/files/{file_id}/document-links")
async def link_bid_received_file(bid_id: str, file_id: str, request: Request) -> Response:
    raw = await request.form()
    entered = {key: str(value) for key, value in raw.items() if isinstance(value, str)}
    release_id = entered.get("release_id")
    try:
        bid_package_service.link_file_document(
            bid_id,
            file_id,
            FileDocumentLinkCreate(
                document_version_id=entered.get("document_version_id", ""),
                relationship=FileDocumentRelationship(
                    entered.get("relationship") or FileDocumentRelationship.EXACT_BYTES.value
                ),
                supersedes_link_id=entered.get("supersedes_link_id") or None,
                operation_id=entered.get("operation_id", ""),
            ),
            LOCAL_ACTOR,
        )
    except (
        ValueError,
        ValidationError,
        IntakeConflictError,
        StaleIntakeError,
        sqlite3.Error,
    ) as exc:
        return _intake_error_response(bid_id, exc, entered=entered, release_id=release_id)
    return RedirectResponse(
        f"/bids/{quote(bid_id)}/package-intake-addenda/releases/{quote(str(release_id))}#files",
        status_code=303,
    )


@app.post("/bids/{bid_id}/package-intake-addenda/files/{file_id}/controlled-document")
async def control_bid_received_file(bid_id: str, file_id: str, request: Request) -> Response:
    """Put a received file under document control using the managed original ContractIQ holds.

    The bytes come from verified managed storage, never from a second upload, so the recorded
    exact-bytes relationship is proven rather than asserted by the person filling the form.
    """
    raw = await request.form()
    entered = {key: str(value) for key, value in raw.items() if isinstance(value, str)}
    release_id = entered.get("release_id", "")
    operation_id = entered.get("operation_id", "")
    try:
        source, evidence = bid_package_service.open_received_file(bid_id, release_id, file_id)
        try:
            _document, version = document_service.register_document_idempotent(
                operation_id,
                {
                    "bid_id": bid_id,
                    "title": entered.get("title", ""),
                    "document_number": entered.get("document_number") or None,
                    "category": entered.get("category", ""),
                    "issuer": entered.get("issuer") or None,
                    "notes": entered.get("notes") or None,
                    "version_label": entered.get("version_label") or "Original",
                    "issued_date": entered.get("issued_date") or None,
                },
                source,
                str(evidence["original_filename"]),
                str(evidence["detected_media_type"] or "") or None,
                LOCAL_ACTOR,
            )
        finally:
            source.close()
        bid_package_service.link_file_document(
            bid_id,
            file_id,
            FileDocumentLinkCreate(
                document_version_id=version.document_version_id,
                relationship=FileDocumentRelationship.EXACT_BYTES,
                supersedes_link_id=entered.get("supersedes_link_id") or None,
                operation_id=operation_id,
            ),
            LOCAL_ACTOR,
        )
    except (
        ValueError,
        ValidationError,
        OSError,
        IntakeConflictError,
        IntakeNotFoundError,
        IntakePublicationError,
        StaleIntakeError,
        ManagedStorageFailureError,
        DocumentStoreBusyError,
        sqlite3.Error,
    ) as exc:
        return _intake_error_response(bid_id, exc, entered=entered, release_id=release_id)
    return RedirectResponse(
        f"/bids/{quote(bid_id)}/package-intake-addenda/releases/{quote(release_id)}#files",
        status_code=303,
    )


@app.post("/bids/{bid_id}/package-intake-addenda/releases/{release_id}/directives")
async def record_bid_addendum_directive(bid_id: str, release_id: str, request: Request) -> Response:
    raw = await request.form()
    entered = {key: str(value) for key, value in raw.items() if isinstance(value, str)}
    try:
        bid_package_service.record_directive(
            bid_id,
            release_id,
            DirectiveCreate(
                directive_type=entered.get("directive_type", ""),
                description=entered.get("description", ""),
                materiality=DirectiveMateriality(entered.get("materiality", "")),
                source_file_id=entered.get("source_file_id") or None,
                source_locator=entered.get("source_locator") or None,
                target_document_version_id=entered.get("target_document_version_id") or None,
                supersedes_directive_id=entered.get("supersedes_directive_id") or None,
                operation_id=entered.get("operation_id", ""),
            ),
            LOCAL_ACTOR,
        )
    except (
        ValueError,
        ValidationError,
        IntakeConflictError,
        StaleIntakeError,
        sqlite3.Error,
    ) as exc:
        return _intake_error_response(bid_id, exc, entered=entered, release_id=release_id)
    return RedirectResponse(
        f"/bids/{quote(bid_id)}/package-intake-addenda/releases/{quote(release_id)}#directives",
        status_code=303,
    )


@app.post("/bids/{bid_id}/package-intake-addenda/directives/{directive_id}/dispositions")
async def dispose_bid_addendum_directive(
    bid_id: str, directive_id: str, request: Request
) -> Response:
    raw = await request.form()
    entered = {key: str(value) for key, value in raw.items() if isinstance(value, str)}
    release_id = entered.get("release_id")
    try:
        bid_package_service.dispose_directive(
            bid_id,
            directive_id,
            DirectiveDispositionCreate(
                status=DirectiveDispositionStatus(entered.get("status", "")),
                rationale=entered.get("rationale", ""),
                resulting_document_version_id=(
                    entered.get("resulting_document_version_id") or None
                ),
                approval_id=entered.get("approval_id") or None,
                route_id=entered.get("route_id") or None,
                supersedes_disposition_id=entered.get("supersedes_disposition_id") or None,
                operation_id=entered.get("operation_id", ""),
            ),
            LOCAL_ACTOR,
        )
    except (
        ValueError,
        ValidationError,
        IntakeConflictError,
        StaleIntakeError,
        sqlite3.Error,
    ) as exc:
        return _intake_error_response(bid_id, exc, entered=entered, release_id=release_id)
    return RedirectResponse(
        f"/bids/{quote(bid_id)}/package-intake-addenda/releases/{quote(str(release_id))}#directives",
        status_code=303,
    )


@app.post("/bids/{bid_id}/package-intake-addenda/releases/{release_id}/acknowledgements")
async def record_bid_release_acknowledgement(
    bid_id: str, release_id: str, request: Request
) -> Response:
    raw = await request.form()
    entered = {key: str(value) for key, value in raw.items() if isinstance(value, str)}
    try:
        bid_package_service.record_acknowledgement(
            bid_id,
            release_id,
            AcknowledgementCreate(
                event_type=AcknowledgementEventType(entered.get("event_type", "")),
                due_at=(
                    _intake_datetime(entered["due_at"], "Acknowledgement due")
                    if entered.get("due_at")
                    else None
                ),
                acknowledgement_reference=entered.get("acknowledgement_reference") or None,
                approval_id=entered.get("approval_id") or None,
                route_id=entered.get("route_id") or None,
                note=entered.get("note") or None,
                supersedes_event_id=entered.get("supersedes_event_id") or None,
                operation_id=entered.get("operation_id", ""),
            ),
            LOCAL_ACTOR,
        )
    except (
        ValueError,
        ValidationError,
        IntakeConflictError,
        StaleIntakeError,
        sqlite3.Error,
    ) as exc:
        return _intake_error_response(bid_id, exc, entered=entered, release_id=release_id)
    return RedirectResponse(
        f"/bids/{quote(bid_id)}/package-intake-addenda/releases/{quote(release_id)}#acknowledgement",
        status_code=303,
    )


@app.post("/bids/{bid_id}/package-intake-addenda/releases/{release_id}/reverify")
async def reverify_bid_received_release(bid_id: str, release_id: str, request: Request) -> Response:
    raw = await request.form()
    entered = {key: str(value) for key, value in raw.items() if isinstance(value, str)}
    try:
        bid_package_service.reverify_release(
            bid_id,
            release_id,
            entered.get("operation_id", ""),
            LOCAL_ACTOR,
        )
    except (ValueError, IntakeNotFoundError, sqlite3.Error) as exc:
        return _intake_error_response(bid_id, exc, entered=entered, release_id=release_id)
    return RedirectResponse(
        f"/bids/{quote(bid_id)}/package-intake-addenda/releases/{quote(release_id)}",
        status_code=303,
    )


@app.post("/bids/{bid_id}/package-intake-addenda/basis-snapshots")
async def publish_bid_basis_snapshot(bid_id: str, request: Request) -> Response:
    raw = await request.form()
    entered = {key: str(value) for key, value in raw.items() if isinstance(value, str)}
    release_ids = tuple(str(value) for value in raw.getlist("release_ids"))
    try:
        bid_package_service.publish_basis(
            bid_id,
            SnapshotCreate(
                expected_current_snapshot_id=entered.get("expected_current_snapshot_id") or None,
                release_ids=release_ids,
                label=entered.get("label", ""),
                note=entered.get("note") or None,
                operation_id=entered.get("operation_id", ""),
            ),
            LOCAL_ACTOR,
        )
    except (
        ValueError,
        ValidationError,
        IntakeConflictError,
        StaleIntakeError,
        ReleaseNotIncorporableError,
        sqlite3.Error,
    ) as exc:
        return _intake_error_response(bid_id, exc, entered=entered)
    return RedirectResponse(
        f"/bids/{quote(bid_id)}/package-intake-addenda/bid-basis-register",
        status_code=303,
    )


@app.get("/bids/{bid_id}/package-intake-addenda/bid-basis-register")
async def bid_basis_register(bid_id: str) -> HTMLResponse:
    try:
        return render(
            "bid_basis_register.html",
            register=bid_package_service.basis_register(bid_id),
        )
    except IntakeNotFoundError as exc:
        return _intake_error_response(bid_id, exc, status_code=404)


@app.get("/bids/{bid_id}/package-intake-addenda/bid-basis-register.csv")
async def bid_basis_register_csv(bid_id: str) -> Response:
    try:
        content = bid_package_service.basis_register_csv(bid_id)
    except IntakeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{bid_id}-bid-basis.csv"'},
    )


@app.get("/bids/{bid_id}/package-intake-addenda/releases/{release_id}/files/{file_id}/download")
async def download_bid_received_file(bid_id: str, release_id: str, file_id: str) -> Response:
    try:
        source, evidence = bid_package_service.open_received_file(bid_id, release_id, file_id)
    except (IntakeNotFoundError, IntakeConflictError, IntakePublicationError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    filename = quote(str(evidence["original_filename"]))
    return StreamingResponse(
        source,
        media_type=str(evidence["detected_media_type"] or "application/octet-stream"),
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@app.post("/bids/{bid_id}/package-intake-addenda/work-items")
async def create_bid_intake_work_item(bid_id: str, request: Request) -> Response:
    raw = await request.form()
    entered = {key: str(value) for key, value in raw.items() if isinstance(value, str)}
    target_kind = entered.get("target_kind", "")
    target_id = entered.get("target_id", "")
    target_arguments = {
        "notice": {"notice_id": target_id},
        "release": {"release_id": target_id},
        "file": {"file_id": target_id},
        "directive": {"directive_id": target_id},
        "acknowledgement": {"acknowledgement_event_id": target_id},
    }
    try:
        if target_kind not in target_arguments:
            raise ValueError("Select a supported intake follow-up target")
        item = bid_package_service.create_linked_work_item(
            bid_id=bid_id,
            title=entered.get("title", ""),
            operation_id=entered.get("operation_id", ""),
            actor=LOCAL_ACTOR,
            **target_arguments[target_kind],
        )
    except (ValueError, ValidationError, IntakeConflictError, sqlite3.Error) as exc:
        return _intake_error_response(
            bid_id,
            exc,
            entered=entered,
            release_id=entered.get("release_id") or None,
        )
    return RedirectResponse(f"/my-work/{quote(item.work_item_id)}", status_code=303)


@app.get("/bids/{bid_id}/manufacturers-coverage", response_class=HTMLResponse)
async def bid_manufacturers_coverage(bid_id: str) -> HTMLResponse:
    return _render_bid_workspace(bid_id, BidWorkspaceSection.MANUFACTURERS_COVERAGE)


@app.get("/bids/{bid_id}/commercial-contract", response_class=HTMLResponse)
async def bid_commercial_contract(bid_id: str) -> HTMLResponse:
    return _render_bid_workspace(bid_id, BidWorkspaceSection.COMMERCIAL_CONTRACT)


@app.get("/bids/{bid_id}/proposal-negotiation", response_class=HTMLResponse)
async def bid_proposal_negotiation(bid_id: str) -> HTMLResponse:
    return _render_bid_workspace(bid_id, BidWorkspaceSection.PROPOSAL_NEGOTIATION)


def _proposal_issue_control_context(
    bid_id: str,
    *,
    form_error: str | None = None,
    entered: dict[str, str] | None = None,
) -> dict[str, object]:
    workspace = bid_control_center_service.workspace(bid_id, as_of=_working_date())
    return {
        "workspace": workspace,
        "bid": workspace.bid,
        "control": proposal_exchange_service.assess(bid_id),
        "history": proposal_exchange_service.history(bid_id),
        "form_error": form_error,
        "entered": entered or {},
        "now_local": datetime.now(WORKING_TIMEZONE).strftime("%Y-%m-%dT%H:%M"),
    }


def _proposal_issue_control_error(
    bid_id: str,
    message: str,
    status_code: int,
    entered: dict[str, str] | None = None,
) -> HTMLResponse:
    try:
        context = _proposal_issue_control_context(
            bid_id,
            form_error=message,
            entered=entered,
        )
    except BidNotFoundError:
        return HTMLResponse(
            "<!doctype html><html><body><h1>Bid not found</h1>"
            '<p><a href="/bids">Return to Bids</a></p></body></html>',
            status_code=404,
        )
    return render("proposal_issue_control.html", status_code=status_code, **context)


@app.get("/bids/{bid_id}/proposal-issue-control", response_class=HTMLResponse)
async def proposal_issue_control(bid_id: str) -> HTMLResponse:
    try:
        return render(
            "proposal_issue_control.html",
            **_proposal_issue_control_context(bid_id),
        )
    except BidNotFoundError:
        return _proposal_issue_control_error(bid_id, "Bid not found", 404)


@app.post("/bids/{bid_id}/proposal-exports", response_class=HTMLResponse)
async def prepare_proposal_export(bid_id: str) -> Response:
    try:
        export, _replayed = proposal_exchange_service.export_package(bid_id, LOCAL_ACTOR)
    except ProposalExchangeNotFoundError as exc:
        return _proposal_issue_control_error(bid_id, str(exc), 404)
    except (ValueError, sqlite3.Error) as exc:
        return _proposal_issue_control_error(bid_id, str(exc), 422)
    return RedirectResponse(
        f"/bids/{quote(bid_id)}/proposal-exports/{quote(export.export_id)}/download",
        status_code=303,
    )


@app.get("/bids/{bid_id}/proposal-exports/{export_id}/download")
async def download_proposal_export(bid_id: str, export_id: str) -> Response:
    try:
        export = proposal_exchange_service.export_by_id(bid_id, export_id)
    except ProposalExchangeNotFoundError as exc:
        return _proposal_issue_control_error(bid_id, str(exc), 404)
    return Response(
        export.canonical_package_json.encode("utf-8"),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{export.package_id}.json"',
            "X-ContractIQ-Canonical-SHA256": export.canonical_sha256,
        },
    )


@app.post("/bids/{bid_id}/proposal-manifests", response_class=HTMLResponse)
async def import_proposal_manifest(bid_id: str, request: Request) -> Response:
    form = await request.form()
    export_id = str(form.get("export_id", ""))
    entered = {"export_id": export_id}
    manifest_upload = form.get("manifest")
    if not isinstance(manifest_upload, StarletteUploadFile) or not manifest_upload.filename:
        return _proposal_issue_control_error(
            bid_id,
            "Choose a Proposal Studio generation manifest.",
            422,
            entered,
        )
    manifest_bytes = await manifest_upload.read()
    if not manifest_bytes or len(manifest_bytes) > 5_000_000:
        return _proposal_issue_control_error(
            bid_id,
            "Generation manifest must be a non-empty JSON file no larger than 5 MB.",
            422,
            entered,
        )
    artifacts: dict[str, tuple[str, bytes]] = {}
    for role in ("docx", "pdf"):
        upload = form.get(role)
        if isinstance(upload, StarletteUploadFile) and upload.filename:
            content = await upload.read()
            if len(content) > 100_000_000:
                return _proposal_issue_control_error(
                    bid_id,
                    f"{role.upper()} artifact exceeds the 100 MB local import limit.",
                    422,
                    entered,
                )
            artifacts[role] = (upload.filename, content)
    try:
        proposal_exchange_service.import_manifest(
            bid_id,
            export_id,
            manifest_bytes,
            artifacts,
            LOCAL_ACTOR,
        )
    except ProposalExchangeNotFoundError as exc:
        return _proposal_issue_control_error(bid_id, str(exc), 404, entered)
    except StaleProposalCandidateError as exc:
        return _proposal_issue_control_error(bid_id, str(exc), 409, entered)
    except (ProposalImportError, sqlite3.Error, OSError) as exc:
        return _proposal_issue_control_error(bid_id, str(exc), 422, entered)
    return RedirectResponse(
        f"/bids/{quote(bid_id)}/proposal-issue-control#approve",
        status_code=303,
    )


@app.post("/bids/{bid_id}/proposal-candidates/{candidate_id}/approve")
async def approve_proposal_candidate(
    bid_id: str,
    candidate_id: str,
    expected_version: Annotated[int, Form()],
) -> Response:
    try:
        proposal_exchange_service.approve_candidate(
            bid_id,
            candidate_id,
            expected_version,
            LOCAL_ACTOR,
        )
    except ProposalExchangeNotFoundError as exc:
        return _proposal_issue_control_error(bid_id, str(exc), 404)
    except StaleProposalCandidateError as exc:
        return _proposal_issue_control_error(bid_id, str(exc), 409)
    except (ValueError, sqlite3.Error) as exc:
        return _proposal_issue_control_error(bid_id, str(exc), 422)
    return RedirectResponse(
        f"/bids/{quote(bid_id)}/proposal-issue-control#record-issue",
        status_code=303,
    )


@app.post("/bids/{bid_id}/proposal-candidates/{candidate_id}/issue")
async def record_proposal_customer_issue(
    bid_id: str,
    candidate_id: str,
    request: Request,
) -> Response:
    form = dict(await request.form())
    entered = {key: str(value) for key, value in form.items() if isinstance(value, str)}
    try:
        raw_issued_at = datetime.fromisoformat(str(form.get("issued_at", "")))
        issued_at = (
            raw_issued_at.replace(tzinfo=WORKING_TIMEZONE)
            if raw_issued_at.tzinfo is None
            else raw_issued_at
        ).astimezone(UTC)
        offer_valid_until = (
            date.fromisoformat(str(form["offer_valid_until"]))
            if form.get("offer_valid_until")
            else None
        )
        command = CustomerIssueCommand(
            candidate_id=candidate_id,
            expected_version=int(str(form.get("expected_version", "0"))),
            issue_revision=str(form.get("issue_revision", "")),
            issued_at=issued_at,
            issue_method=str(form.get("issue_method", "")),
            destination_reference=str(form.get("destination_reference", "")),
            offer_valid_until=offer_valid_until,
            note=str(form.get("note", "")),
        )
        proposal_exchange_service.issue(bid_id, command, LOCAL_ACTOR)
    except StaleProposalCandidateError as exc:
        return _proposal_issue_control_error(bid_id, str(exc), 409, entered)
    except ProposalExchangeNotFoundError as exc:
        return _proposal_issue_control_error(bid_id, str(exc), 404, entered)
    except (ValidationError, ValueError, sqlite3.Error) as exc:
        message = validation_error_message(exc) if isinstance(exc, ValidationError) else str(exc)
        return _proposal_issue_control_error(bid_id, message, 422, entered)
    return RedirectResponse(
        f"/bids/{quote(bid_id)}/proposal-issue-control#history",
        status_code=303,
    )


@app.get("/bids/{bid_id}/award-handover", response_class=HTMLResponse)
async def bid_award_handover(bid_id: str) -> HTMLResponse:
    return _render_bid_workspace(bid_id, BidWorkspaceSection.AWARD_HANDOVER)


@app.get("/bids/{bid_id}/handover", response_class=HTMLResponse)
async def bid_handover_report(bid_id: str) -> HTMLResponse:
    """Render the authoritative printable whole-Bid pre-award handover."""
    try:
        workspace = bid_control_center_service.workspace(bid_id, as_of=_working_date())
        report = bid_handover_service.report(
            bid_id,
            gate_verdict=workspace.readiness.verdict.value,
            gate_blockers=[
                f"{blocker.heading} — {blocker.detail}" for blocker in workspace.blockers
            ],
            generated_by=f"ContractIQ server · {LOCAL_ACTOR}",
        )
    except (BidNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return render("bid_handover.html", report=report, workspace=workspace)


@app.get("/bids/{bid_id}/handover.csv")
async def bid_handover_csv(bid_id: str) -> Response:
    """Export the same whole-Bid projection as deterministic structured transfer."""
    try:
        workspace = bid_control_center_service.workspace(bid_id, as_of=_working_date())
        report = bid_handover_service.report(
            bid_id,
            gate_verdict=workspace.readiness.verdict.value,
            gate_blockers=[
                f"{blocker.heading} — {blocker.detail}" for blocker in workspace.blockers
            ],
            generated_by=f"ContractIQ server · {LOCAL_ACTOR}",
        )
    except (BidNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        BidHandoverService.csv(report),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="bid-handover-{bid_id}.csv"'},
    )


@app.get("/administration", response_class=HTMLResponse)
async def administration() -> HTMLResponse:
    """Expose configuration and controlled reference functions away from Bid work."""
    return render("administration.html")


@app.get("/reports-center", response_class=HTMLResponse)
async def reports_center() -> HTMLResponse:
    """List only exports that existing authoritative services can produce."""
    bids = {bid.bid_id: bid for bid in bid_repository.list_bids()}
    packages = vendor_document_repository.list_packages()
    return render(
        "reports_center.html",
        report_packages=[
            {"package": package, "bid": bids.get(package.bid_id)} for package in packages
        ],
    )


@app.get("/scope-interfaces", response_class=HTMLResponse)
async def scope_interfaces_register(
    bid_id: str | None = None,
    requirement_id: str | None = None,
    scope_item_id: str | None = None,
) -> HTMLResponse:
    if bid_id is not None and bid_repository.get_bid(bid_id) is None:
        raise HTTPException(status_code=404, detail="Bid not found")
    if requirement_id is not None:
        try:
            contextual_requirement = requirement_service.get_requirement(requirement_id)
        except RequirementNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if contextual_requirement.bid_id != bid_id:
            return render(
                "scope_interfaces.html",
                status_code=422,
                **_scope_register_context(
                    bid_id or "",
                    error="Requirement context must belong to this Bid",
                    entered={"requirement_id": requirement_id},
                ),
            )
    if scope_item_id is not None:
        contextual_scope = scope_repository.get_scope_item(scope_item_id)
        if contextual_scope is None:
            raise HTTPException(status_code=404, detail="Scope item not found")
        if contextual_scope.bid_id != bid_id:
            return render(
                "scope_interfaces.html",
                status_code=422,
                **_scope_register_context(
                    bid_id or "",
                    error="Scope context must belong to this Bid",
                    entered={"scope_item_id": scope_item_id},
                ),
            )
    scopes = scope_repository.list_scope_items(bid_id)
    interfaces = scope_repository.list_interfaces(bid_id)
    coverage = scope_service.projection(bid_id, _working_date()) if bid_id else None
    return render(
        "scope_interfaces.html",
        scopes=scopes,
        interfaces=interfaces,
        coverage=coverage,
        bid_id=bid_id,
        bids=bid_repository.list_bids(),
        scope_areas=list(ScopeArea),
        scope_origins=list(ScopeOrigin),
        customer_needs=list(CustomerNeed),
        offer_positions=list(OfferPosition),
        pricing_states=list(PricingState),
        materialities=list(Materiality),
        dependency_states=list(DependencyState),
        requirements=requirement_service.list_requirements(
            bid_id=bid_id, as_of_date=_working_date()
        )
        if bid_id
        else [],
        error=None,
        entered={"requirement_id": requirement_id or "", "scope_item_id": scope_item_id or ""},
        **_bid_return_context(bid_id, BidWorkspaceSection.REQUIREMENTS_SCOPE),
    )


def _scope_register_context(
    bid_id: str, *, error: str | None = None, entered: dict[str, object] | None = None
) -> dict[str, object]:
    if bid_repository.get_bid(bid_id) is None:
        raise HTTPException(status_code=404, detail="Bid not found")
    return {
        "scopes": scope_repository.list_scope_items(bid_id),
        "interfaces": scope_repository.list_interfaces(bid_id),
        "coverage": scope_service.projection(bid_id, _working_date()),
        "bid_id": bid_id,
        "bids": bid_repository.list_bids(),
        "scope_areas": list(ScopeArea),
        "scope_origins": list(ScopeOrigin),
        "customer_needs": list(CustomerNeed),
        "offer_positions": list(OfferPosition),
        "pricing_states": list(PricingState),
        "materialities": list(Materiality),
        "dependency_states": list(DependencyState),
        "requirements": requirement_service.list_requirements(
            bid_id=bid_id, as_of_date=_working_date()
        ),
        "error": error,
        "entered": entered or {},
        **_bid_return_context(bid_id, BidWorkspaceSection.REQUIREMENTS_SCOPE),
    }


@app.post("/scope-items", response_class=HTMLResponse)
async def create_scope_item_browser(request: Request) -> Response:
    form = dict(await request.form())
    bid_id = str(form.get("bid_id") or "")
    actor = LOCAL_ACTOR
    now = datetime.now(ZoneInfo("UTC"))
    try:
        item = ScopeItem.model_validate(
            {
                **{
                    key: value
                    for key, value in form.items()
                    if value not in ("", None) and key not in {"requirement_id", "origin_section"}
                },
                "created_at": now,
                "updated_at": now,
                "created_by": actor,
                "provenance": Provenance.from_human(actor),
            }
        )
        requirement_id = str(form.get("requirement_id") or "")
        scope_service.create_scope_item(item, actor, [requirement_id] if requirement_id else None)
    except (ValidationError, ValueError, sqlite3.Error) as exc:
        return render(
            "scope_interfaces.html",
            status_code=422,
            **_scope_register_context(
                bid_id,
                error=validation_error_message(exc)
                if isinstance(exc, ValidationError)
                else str(exc),
                entered=form,
            ),
        )
    origin_requirement = str(form.get("requirement_id") or "")
    location = (
        f"/bids/{quote(bid_id)}/requirements-scope#scope-and-interfaces"
        if form.get("origin_section") == "bid"
        else f"/requirements/{quote(origin_requirement)}#coverage"
        if origin_requirement
        else f"/scope-items/{quote(item.scope_item_id)}?bid_id={quote(bid_id)}"
    )
    return RedirectResponse(location, status_code=303)


@app.post("/interfaces", response_class=HTMLResponse)
async def create_interface_browser(request: Request) -> Response:
    submitted = await request.form()
    form = dict(submitted)
    bid_id = str(form.get("bid_id") or "")
    now = datetime.now(ZoneInfo("UTC"))
    try:
        record = InterfaceRecord.model_validate(
            {
                **{
                    key: value
                    for key, value in form.items()
                    if value not in ("", None) and key not in {"scope_item_id", "origin_section"}
                },
                "created_at": now,
                "updated_at": now,
                "created_by": LOCAL_ACTOR,
                "provenance": Provenance.from_human(LOCAL_ACTOR),
            }
        )
        scope_ids = [str(value) for value in submitted.getlist("scope_item_id")]
        scope_service.create_interface(record, LOCAL_ACTOR, scope_ids)
    except (ValidationError, ValueError, sqlite3.Error) as exc:
        return render(
            "scope_interfaces.html",
            status_code=422,
            **_scope_register_context(
                bid_id,
                error=validation_error_message(exc)
                if isinstance(exc, ValidationError)
                else str(exc),
                entered=form,
            ),
        )
    location = (
        f"/bids/{quote(bid_id)}/requirements-scope#scope-and-interfaces"
        if form.get("origin_section") == "bid"
        else f"/interfaces/{quote(record.interface_id)}?bid_id={quote(bid_id)}"
    )
    return RedirectResponse(location, status_code=303)


@app.get("/deliverables", response_class=HTMLResponse)
async def deliverables_register(
    bid_id: str | None = None, as_of: str | None = None
) -> HTMLResponse:
    projection_date = _parse_as_of(as_of)
    rows = deliverable_service.list(bid_id)
    return render(
        "deliverables.html",
        deliverables=rows,
        bids=bid_repository.list_bids(),
        bid_id=bid_id or "",
        metrics=deliverable_service.metrics(bid_id, projection_date),
        gaps=deliverable_service.gaps(bid_id, projection_date),
        deliverable_criticalities=list(DeliverableCriticality),
        deliverable_directions=list(DeliverableDirection),
        **_bid_return_context(bid_id, BidWorkspaceSection.PROPOSAL_NEGOTIATION),
    )


@app.post("/deliverables", response_class=HTMLResponse)
async def create_deliverable_html(request: Request) -> Response:
    form = dict(await request.form())
    bid_id = str(form.get("bid_id", ""))
    try:
        bid_repository.get_bid(bid_id)
        now = datetime.now(UTC)
        deliverable_service.create(
            Deliverable(
                bid_id=bid_id,
                title=str(form.get("title", "")),
                description=str(form.get("description", "")),
                category=str(form.get("category", "")),
                criticality=DeliverableCriticality(str(form.get("criticality", "MANDATORY"))),
                lifecycle_phase=LifecyclePhase.PRE_AWARD,
                direction=DeliverableDirection(str(form.get("direction", "COMPANY_TO_CUSTOMER"))),
                owner=str(form.get("owner") or "") or None,
                provenance=Provenance.from_human(LOCAL_ACTOR),
                created_at=now,
                updated_at=now,
                created_by=LOCAL_ACTOR,
            ),
            LOCAL_ACTOR,
        )
    except (ValidationError, ValueError, sqlite3.Error) as exc:
        projection_date = date.today()
        return render(
            "deliverables.html",
            status_code=422,
            deliverables=deliverable_service.list(bid_id or None),
            bids=bid_repository.list_bids(),
            bid_id=bid_id,
            metrics=deliverable_service.metrics(bid_id or None, projection_date),
            gaps=deliverable_service.gaps(bid_id or None, projection_date),
            deliverable_criticalities=list(DeliverableCriticality),
            deliverable_directions=list(DeliverableDirection),
            form_error=validation_error_message(exc)
            if isinstance(exc, ValidationError)
            else str(exc),
            entered=form,
            **_bid_return_context(bid_id or None, BidWorkspaceSection.PROPOSAL_NEGOTIATION),
        )
    return RedirectResponse(f"/deliverables?bid_id={quote(bid_id)}#author", status_code=303)


@app.get("/deliverables/{deliverable_id}", response_class=HTMLResponse)
async def deliverable_detail(deliverable_id: str) -> HTMLResponse:
    try:
        detail = deliverable_service.detail(deliverable_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return render("deliverable_detail.html", **detail)


@app.get("/deliverables/{deliverable_id}/history", response_class=HTMLResponse)
async def deliverable_history(deliverable_id: str) -> HTMLResponse:
    try:
        history = deliverable_service.history(deliverable_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return render("deliverable_history.html", history=history, deliverable_id=deliverable_id)


@app.get("/api/deliverables")
async def deliverables_api(bid_id: str | None = None, as_of: str | None = None) -> JSONResponse:
    projection_date = _parse_as_of(as_of)
    return JSONResponse(
        {
            "deliverables": deliverable_service.list(bid_id),
            "gaps": [
                gap.model_dump(mode="json")
                for gap in deliverable_service.gaps(bid_id, projection_date)
            ],
            "metrics": deliverable_service.metrics(bid_id, projection_date),
        }
    )


@app.get("/api/deliverables/{deliverable_id}")
async def deliverable_api(deliverable_id: str) -> JSONResponse:
    try:
        return JSONResponse(deliverable_service.detail(deliverable_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/deliverables")
async def create_deliverable_api(request: Request) -> JSONResponse:
    body = await request.json()
    actor = str(body.pop("actor", LOCAL_ACTOR))
    try:
        item = deliverable_service.create(Deliverable.model_validate(body), actor)
        return JSONResponse(item.model_dump(mode="json"), status_code=201)
    except (ValidationError, ValueError, sqlite3.Error) as exc:
        raise _mutation_error(exc) from exc


@app.post("/api/deliverables/{deliverable_id}/links")
async def add_deliverable_link(deliverable_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    body["deliverable_id"] = deliverable_id
    actor = str(body.pop("actor", LOCAL_ACTOR))
    try:
        link = deliverable_service.add_link(DeliverableLink.model_validate(body), actor)
        return JSONResponse(link.model_dump(mode="json"), status_code=201)
    except (ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.post("/api/deliverables/{deliverable_id}/activate")
async def activate_deliverable(deliverable_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    actor = str(body.get("actor", LOCAL_ACTOR))
    try:
        deliverable_service.activate(deliverable_id, int(body.get("expected_version", 1)), actor)
        return JSONResponse({"deliverable_id": deliverable_id, "activated": True})
    except ValueError as exc:
        raise _mutation_error(exc) from exc


@app.post("/api/deliverables/{deliverable_id}/commitments")
async def add_deliverable_commitment(deliverable_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    body["deliverable_id"] = deliverable_id
    actor = str(body.pop("actor", LOCAL_ACTOR))
    try:
        value = deliverable_service.add_commitment(SupplierCommitment.model_validate(body), actor)
        return JSONResponse(value.model_dump(mode="json"), status_code=201)
    except (ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.post("/api/deliverables/{deliverable_id}/submissions")
async def add_deliverable_submission(deliverable_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    body["deliverable_id"] = deliverable_id
    actor = str(body.pop("actor", LOCAL_ACTOR))
    try:
        value = deliverable_service.submit(SubmissionVersion.model_validate(body), actor)
        return JSONResponse(value.model_dump(mode="json"), status_code=201)
    except (ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.post("/api/deliverable-submissions/{submission_id}/review")
async def review_deliverable_submission(submission_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    body["submission_id"] = submission_id
    actor = str(body.pop("actor", LOCAL_ACTOR))
    try:
        value = deliverable_service.review(ReviewDecisionRecord.model_validate(body), actor)
        return JSONResponse(value.model_dump(mode="json"), status_code=201)
    except (ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


def _vdrl_dashboard_context(
    *,
    bid_id: str | None = None,
    error: str | None = None,
    entered: dict[str, object] | None = None,
) -> dict[str, object]:
    bids = bid_repository.list_bids()
    if bid_id is not None and bid_repository.get_bid(bid_id) is None:
        raise HTTPException(status_code=422, detail="Selected Bid does not exist")
    packages = vendor_document_repository.list_packages(bid_id)
    package_rows = [
        {
            "package": package,
            "bid": bid_repository.get_bid(package.bid_id),
            "readiness": vendor_document_service.readiness(package.package_id),
        }
        for package in packages
    ]
    return {
        "bids": bids,
        "packages": package_rows,
        "rows": vendor_document_service.register_rows(bid_id=bid_id),
        "selected_bid_id": bid_id or "",
        "error": error,
        "entered": entered or {},
        "template": vendor_document_service.ensure_standard_template(),
        "admin": False,
        # Manufacturer and supplier coverage is stage 6, so a Bid-scoped visit to
        # this whole-portfolio register always keeps its way back to that Bid.
        **_bid_return_context(bid_id, BidWorkspaceSection.MANUFACTURERS_COVERAGE),
    }


@app.get("/vendor-documents", response_class=HTMLResponse)
async def vendor_documents_dashboard(bid_id: str | None = None) -> HTMLResponse:
    return render("vendor_documents.html", **_vdrl_dashboard_context(bid_id=bid_id))


@app.get("/vendor-documents/admin/templates", response_class=HTMLResponse)
async def vendor_document_template_admin() -> HTMLResponse:
    return render(
        "vendor_documents.html",
        **(_vdrl_dashboard_context() | {"admin": True}),
    )


@app.post("/vendor-documents/packages", response_class=HTMLResponse)
async def create_vendor_package(request: Request) -> HTMLResponse:
    form = dict(await request.form())
    try:
        package = vendor_document_service.create_package(
            SupplierPackageCreate.model_validate(
                {
                    key: value
                    for key, value in form.items()
                    if value not in ("", None) and key != "origin_section"
                }
            ),
            LOCAL_ACTOR,
        )
    except (ValidationError, ValueError, sqlite3.Error) as exc:
        return render(
            "vendor_documents.html",
            status_code=422,
            **_vdrl_dashboard_context(
                bid_id=str(form.get("bid_id") or "") or None,
                error=validation_error_message(exc)
                if isinstance(exc, ValidationError)
                else str(exc),
                entered=form,
            ),
        )
    location = (
        f"/bids/{quote(package.bid_id)}/manufacturers-coverage#manufacturer-packages"
        if form.get("origin_section") == "bid"
        else f"/vendor-documents/packages/{package.package_id}"
    )
    return RedirectResponse(location, status_code=303)


def _vdrl_package_context(
    package_id: str,
    *,
    error: str | None = None,
    entered: dict[str, object] | None = None,
    import_preview: object | None = None,
    import_csv: str = "",
    import_digest: str = "",
) -> dict[str, object]:
    try:
        package = vendor_document_service.package(package_id)
    except VendorDocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    bid = bid_repository.get_bid(package.bid_id)
    if bid is None:
        raise HTTPException(status_code=500, detail="Package Bid context is missing")
    requirements = vendor_document_repository.list_requirements(package_id)
    handover_assessments = {
        item.requirement_id: assess_manufacturer_handover(
            required=item.required,
            applicable=item.applicable,
            verification_status=item.verification_status,
            response_source=item.response_source,
            response_received_date=item.response_received_date,
            internal_owner=item.internal_owner,
            commercial_impact=item.commercial_impact,
            bid_disposition=item.bid_disposition,
            disposition_approved=item.disposition_approved,
            unresolved_action=item.unresolved_action,
        )
        for item in requirements
    }
    return {
        "package": package,
        "bid": bid,
        "requirements": requirements,
        "rows": vendor_document_service.register_rows(package_id=package_id),
        "readiness": vendor_document_service.readiness(package_id),
        "handover_assessments": handover_assessments,
        "verification_statuses": list(VerificationStatus),
        "commercial_impacts": list(CommercialImpact),
        "bid_dispositions": list(BidDisposition),
        "timing_anchors": list(TimingAnchor),
        "template": vendor_document_service.ensure_standard_template(),
        "audit": vendor_document_repository.audit(package.bid_id),
        "error": error,
        "entered": entered or {},
        "import_preview": import_preview,
        "import_csv": import_csv,
        "import_digest": import_digest,
        "linked_work": ops07w_repository.linked_work("manufacturer", package_id),
        "requirement_evidence": ops07w_repository.package_requirement_evidence(package_id),
    }


@app.get("/vendor-documents/packages/{package_id}", response_class=HTMLResponse)
async def vendor_document_package_workspace(package_id: str) -> HTMLResponse:
    return render("vendor_document_package.html", **_vdrl_package_context(package_id))


@app.post("/vendor-documents/packages/{package_id}/requirements", response_class=HTMLResponse)
async def create_vendor_requirement(package_id: str, request: Request) -> HTMLResponse:
    submitted = await request.form()
    form = dict(submitted)
    form["package_id"] = package_id
    form["requested_stages"] = submitted.getlist("requested_stages")
    try:
        vendor_document_service.create_requirement(
            CustomerRequirementCreate.model_validate(
                {key: value for key, value in form.items() if value not in ("", None)}
            ),
            LOCAL_ACTOR,
        )
    except (ValidationError, ValueError, sqlite3.Error) as exc:
        return render(
            "vendor_document_package.html",
            status_code=422,
            **_vdrl_package_context(
                package_id,
                error=validation_error_message(exc)
                if isinstance(exc, ValidationError)
                else str(exc),
                entered=form,
            ),
        )
    return RedirectResponse(
        f"/vendor-documents/packages/{package_id}#compliance-register", status_code=303
    )


@app.post("/vendor-documents/requirements/{requirement_id}", response_class=HTMLResponse)
async def update_vendor_requirement(requirement_id: str, request: Request) -> HTMLResponse:
    current = vendor_document_repository.get_requirement(requirement_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Customer requirement not found")
    submitted = await request.form()
    form = dict(submitted)
    form["committed_stages"] = submitted.getlist("committed_stages")
    form["disposition_approved"] = "disposition_approved" in submitted
    try:
        vendor_document_service.update_verification(
            requirement_id,
            RequirementVerificationUpdate.model_validate(
                {key: value for key, value in form.items() if value not in ("", None)}
            ),
            LOCAL_ACTOR,
        )
    except (ValidationError, ValueError, sqlite3.Error) as exc:
        return render(
            "vendor_document_package.html",
            status_code=422,
            **_vdrl_package_context(
                current.package_id,
                error=validation_error_message(exc)
                if isinstance(exc, ValidationError)
                else str(exc),
                entered={"verification_requirement_id": requirement_id, **form},
            ),
        )
    return RedirectResponse(
        f"/vendor-documents/packages/{current.package_id}#requirement-{requirement_id}",
        status_code=303,
    )


@app.post("/vendor-documents/packages/{package_id}/bulk", response_class=HTMLResponse)
async def bulk_update_vendor_requirements(package_id: str, request: Request) -> HTMLResponse:
    submitted = await request.form()
    form = dict(submitted)
    try:
        targets = []
        for encoded in submitted.getlist("requirement_target"):
            requirement_id, separator, version = str(encoded).partition("|")
            if not separator:
                raise ValueError("Invalid requirement selection")
            targets.append(
                BulkRequirementTarget(requirement_id=requirement_id, expected_version=int(version))
            )
        command = BulkVerificationUpdate.model_validate(
            {
                "targets": targets,
                "proposed_manufacturer": form.get("proposed_manufacturer") or None,
                "internal_owner": form.get("internal_owner") or None,
                "verification_status": form.get("verification_status") or None,
            }
        )
        vendor_document_service.bulk_update(package_id, command, LOCAL_ACTOR)
    except (ValidationError, ValueError, sqlite3.Error) as exc:
        return render(
            "vendor_document_package.html",
            status_code=422,
            **_vdrl_package_context(
                package_id,
                error=validation_error_message(exc)
                if isinstance(exc, ValidationError)
                else str(exc),
                entered=form,
            ),
        )
    return RedirectResponse(
        f"/vendor-documents/packages/{package_id}#compliance-register", status_code=303
    )


@app.post("/vendor-documents/packages/{package_id}/import/preview", response_class=HTMLResponse)
async def preview_vendor_requirement_import(package_id: str, request: Request) -> HTMLResponse:
    form = await request.form()
    uploaded = form.get("csv_file")
    try:
        if not isinstance(uploaded, StarletteUploadFile):
            raise ValueError("Choose a CSV file")
        csv_text = (await uploaded.read()).decode("utf-8-sig")
        preview = vendor_document_service.preview_import(package_id, csv_text)
        digest = vendor_document_service.import_digest(csv_text)
    except (UnicodeDecodeError, ValueError) as exc:
        return render(
            "vendor_document_package.html",
            status_code=422,
            **_vdrl_package_context(package_id, error=str(exc)),
        )
    return render(
        "vendor_document_package.html",
        status_code=200 if preview.valid else 422,
        **_vdrl_package_context(
            package_id,
            error="CSV preview contains errors" if not preview.valid else None,
            import_preview=preview,
            import_csv=csv_text,
            import_digest=digest,
        ),
    )


@app.post("/vendor-documents/packages/{package_id}/import/confirm", response_class=HTMLResponse)
async def confirm_vendor_requirement_import(package_id: str, request: Request) -> HTMLResponse:
    form = dict(await request.form())
    csv_text = str(form.get("csv_text") or "")
    digest = str(form.get("csv_digest") or "")
    try:
        vendor_document_service.confirm_import(package_id, csv_text, digest, LOCAL_ACTOR)
    except (ValidationError, ValueError, sqlite3.Error) as exc:
        preview = vendor_document_service.preview_import(package_id, csv_text)
        return render(
            "vendor_document_package.html",
            status_code=422,
            **_vdrl_package_context(
                package_id,
                error=validation_error_message(exc)
                if isinstance(exc, ValidationError)
                else str(exc),
                import_preview=preview,
                import_csv=csv_text,
                import_digest=digest,
            ),
        )
    return RedirectResponse(
        f"/vendor-documents/packages/{package_id}#compliance-register", status_code=303
    )


@app.get("/vendor-documents/register", response_class=HTMLResponse)
async def vendor_document_register(
    bid_id: str | None = None,
    package_id: str | None = None,
    requirement_code: str | None = None,
    stage: str | None = None,
    verification_status: str | None = None,
    manufacturer: str | None = None,
    owner: str | None = None,
    commercial_impact: str | None = None,
    attention: str | None = None,
) -> HTMLResponse:
    try:
        status_value = VerificationStatus(verification_status) if verification_status else None
        impact_value = CommercialImpact(commercial_impact) if commercial_impact else None
        attention_value = None if not attention else attention == "required"
        if attention not in {None, "", "required", "none"}:
            raise ValueError("attention must be required or none")
        rows = vendor_document_service.register_rows(
            bid_id=bid_id,
            package_id=package_id,
            requirement_code=requirement_code,
            stage=stage,
            verification_status=status_value,
            manufacturer=manufacturer,
            owner=owner,
            commercial_impact=impact_value,
            attention=attention_value,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return render(
        "vendor_document_register.html",
        rows=rows,
        bids=bid_repository.list_bids(),
        packages=vendor_document_repository.list_packages(bid_id),
        verification_statuses=list(VerificationStatus),
        commercial_impacts=list(CommercialImpact),
        selected={
            "bid_id": bid_id or "",
            "package_id": package_id or "",
            "requirement_code": requirement_code or "",
            "stage": stage or "",
            "verification_status": verification_status or "",
            "manufacturer": manufacturer or "",
            "owner": owner or "",
            "commercial_impact": commercial_impact or "",
            "attention": attention or "",
        },
    )


@app.get("/vendor-documents/packages/{package_id}/handover.csv")
async def vendor_document_handover(package_id: str) -> Response:
    try:
        content = vendor_document_service.handover_csv(package_id)
    except VendorDocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        content,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="vendor-vdrl-handover-{package_id}.csv"'
        },
    )


def _commercial_browser_context(
    bid_id: str | None,
    as_of: str | None,
    *,
    form_error: str | None = None,
    entered: dict[str, object] | None = None,
    selected_position_targets: list[str] | None = None,
) -> dict[str, object]:
    projection_date = _parse_as_of(as_of)
    scope_rows = [
        dict(row)
        for row in scope_repository._conn()
        .execute(
            "SELECT * FROM scope_interface_items WHERE (? IS NULL OR bid_id=?)", (bid_id, bid_id)
        )
        .fetchall()
    ]
    return {
        "positions": ops08_repository.workspace(bid_id) if bid_id else [],
        "commercial_readiness": ops08_repository.readiness(bid_id)
        if bid_id
        else {"state": "NOT_STARTED", "blockers": [], "total": 0, "reviewed": 0},
        "commercial_items": commercial_service.list(bid_id),
        "bids": bid_repository.list_bids(),
        "bid_id": bid_id or "",
        "gaps": commercial_service.gaps(bid_id, projection_date, scope_rows),
        "metrics": commercial_service.metrics(bid_id, projection_date, scope_rows),
        "commercial_categories": list(CommercialCategory),
        "basis_roles": list(BasisRole),
        "form_error": form_error,
        "entered": entered or {},
        "selected_position_targets": selected_position_targets or [],
        **_bid_return_context(bid_id, BidWorkspaceSection.COMMERCIAL_CONTRACT),
    }


@app.get("/commercial", response_class=HTMLResponse)
async def commercial_register(bid_id: str | None = None, as_of: str | None = None) -> HTMLResponse:
    return render("commercial.html", **_commercial_browser_context(bid_id, as_of))


@app.get("/commercial/{commercial_item_id}", response_class=HTMLResponse)
async def commercial_detail(
    commercial_item_id: str,
    form_state: str | None = None,
    source_error: str | None = None,
) -> HTMLResponse:
    if commercial_item_id.startswith("CP-"):
        try:
            detail = ops08_repository.detail(commercial_item_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        with db._conn() as conn:
            sources = [
                dict(row)
                for row in conn.execute(
                    """SELECT v.document_version_id,
                    COALESCE(d.control_title,d.filename) AS title,v.version_label
                    FROM document_versions v
                    JOIN documents d ON d.id=v.document_id
                    WHERE d.bid_id=? ORDER BY title,v.version_label""",
                    (detail["bid_id"],),
                ).fetchall()
            ]
        entered: dict[str, str] = {}
        if form_state:
            saved = _commercial_form_states.get(form_state)
            if saved and (datetime.now(UTC) - saved[0]).total_seconds() <= 3600:
                entered = saved[1]
        return render(
            "commercial_position_detail.html",
            position=detail,
            sources=sources,
            dispositions=list(PositionDisposition),
            negotiation_states=list(CommercialNegotiationState),
            risks=contract_risk_service.list(str(detail["bid_id"])),
            bid=bid_repository.get_bid(str(detail["bid_id"])),
            error=source_error,
            entered=entered,
            source_operation_token=(
                entered.get("source_operation_token")
                or _new_source_operation_token(str(detail["bid_id"]))
            ),
            source_registered=bool(entered.get("source_document_version_id")),
        )
    try:
        detail = commercial_service.detail(commercial_item_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return render("commercial_detail.html", **detail)


@app.post("/commercial/positions/{position_id}/with-source", response_class=HTMLResponse)
async def register_commercial_source(position_id: str, request: Request) -> Response:
    raw = await request.form()
    submitted = dict(raw)
    form = {
        key: str(value)
        for key, value in submitted.items()
        if not isinstance(value, StarletteUploadFile)
    }
    upload = submitted.get("new_source_file")
    try:
        detail = ops08_repository.detail(position_id)
        bid_id = str(detail["bid_id"])
        if not isinstance(upload, StarletteUploadFile) or not upload.filename:
            raise ValueError("Choose the customer source file before registering it")
        operation_id = _source_operation_id(str(form.get("source_operation_token") or ""), bid_id)
        _document, version = document_service.register_document_idempotent(
            operation_id,
            {
                "bid_id": bid_id,
                "title": str(form.get("new_source_title") or ""),
                "category": DocumentCategory.SPECIFICATION,
                "version_label": str(form.get("new_source_version_label") or "Revision 0"),
            },
            upload.file,
            upload.filename,
            upload.content_type,
            LOCAL_ACTOR,
        )
        form["source_document_version_id"] = version.document_version_id
        token = uuid.uuid4().hex
        if len(_commercial_form_states) >= 100:
            oldest = min(_commercial_form_states, key=lambda key: _commercial_form_states[key][0])
            _commercial_form_states.pop(oldest, None)
        _commercial_form_states[token] = (datetime.now(UTC), form)
    except (ValueError, sqlite3.Error, OSError) as exc:
        token = uuid.uuid4().hex
        _commercial_form_states[token] = (datetime.now(UTC), form)
        return RedirectResponse(
            f"/commercial/{quote(position_id)}?form_state={quote(token)}"
            f"&source_error={quote(str(exc))}",
            status_code=303,
        )
    return RedirectResponse(
        f"/commercial/{quote(position_id)}?form_state={quote(token)}", status_code=303
    )


@app.post("/commercial/positions/{position_id}/risk-links", response_class=HTMLResponse)
async def link_commercial_risk(position_id: str, request: Request) -> Response:
    form = dict(await request.form())
    try:
        ops08_repository.link(
            position_id,
            "risk",
            str(form.get("risk_id") or ""),
            str(form.get("relation") or "EXPOSURE"),
            LOCAL_ACTOR,
        )
    except (ValueError, sqlite3.Error) as exc:
        try:
            detail = ops08_repository.detail(position_id)
        except ValueError as missing:
            raise HTTPException(status_code=404, detail=str(missing)) from missing
        with db._conn() as conn:
            sources = [
                dict(row)
                for row in conn.execute(
                    """SELECT v.document_version_id,
                    COALESCE(d.control_title,d.filename) AS title,v.version_label
                    FROM document_versions v JOIN documents d ON d.id=v.document_id
                    WHERE d.bid_id=? ORDER BY title,v.version_label""",
                    (detail["bid_id"],),
                ).fetchall()
            ]
        return render(
            "commercial_position_detail.html",
            status_code=422,
            position=detail,
            sources=sources,
            dispositions=list(PositionDisposition),
            negotiation_states=list(CommercialNegotiationState),
            risks=contract_risk_service.list(str(detail["bid_id"])),
            bid=bid_repository.get_bid(str(detail["bid_id"])),
            error=str(exc),
            entered={},
        )
    return RedirectResponse(f"/commercial/{quote(position_id)}", status_code=303)


@app.post("/commercial/positions/{position_id}", response_class=HTMLResponse)
async def revise_commercial_position(position_id: str, request: Request) -> Response:
    form = dict(await request.form())
    try:
        current = ops08_repository.detail(position_id)
        source_fields = {
            "expected_version",
            "source_operation_token",
            "new_source_title",
            "new_source_version_label",
            "new_source_file",
        }
        revision = PositionRevision.model_validate(
            {key: value or None for key, value in form.items() if key not in source_fields}
        )
        ops08_repository.revise(
            position_id,
            int(form.get("expected_version", 0)),
            revision,
            LOCAL_ACTOR,
        )
    except (ValidationError, ValueError, sqlite3.Error) as exc:
        try:
            current = ops08_repository.detail(position_id)
        except ValueError as missing:
            raise HTTPException(status_code=404, detail=str(missing)) from missing
        with db._conn() as conn:
            sources = [
                dict(row)
                for row in conn.execute(
                    """SELECT v.document_version_id,
                    COALESCE(d.control_title,d.filename) AS title,v.version_label
                    FROM document_versions v JOIN documents d ON d.id=v.document_id
                    WHERE d.bid_id=? ORDER BY title,v.version_label""",
                    (current["bid_id"],),
                ).fetchall()
            ]
        return render(
            "commercial_position_detail.html",
            status_code=422,
            position=current,
            sources=sources,
            dispositions=list(PositionDisposition),
            negotiation_states=list(CommercialNegotiationState),
            risks=contract_risk_service.list(str(current["bid_id"])),
            bid=bid_repository.get_bid(str(current["bid_id"])),
            error=validation_error_message(exc) if isinstance(exc, ValidationError) else str(exc),
            entered=form,
        )
    return RedirectResponse(f"/commercial/{quote(position_id)}", status_code=303)


@app.get("/commercial-qualifications.csv")
async def commercial_qualifications_csv(bid_id: str) -> Response:
    if bid_repository.get_bid(bid_id) is None:
        raise HTTPException(status_code=404, detail="Bid not found")
    return Response(
        ops08_repository.qualifications_csv(bid_id),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="qualifications-{bid_id}.csv"'},
    )


@app.get("/commercial-qualifications", response_class=HTMLResponse)
async def commercial_qualifications(bid_id: str) -> HTMLResponse:
    bid = bid_repository.get_bid(bid_id)
    if bid is None:
        raise HTTPException(status_code=404, detail="Bid not found")
    rows = [
        row
        for row in ops08_repository.workspace(bid_id)
        if row["disposition"] in {"QUALIFY", "REJECT"}
    ]
    return render("commercial_qualifications.html", bid=bid, rows=rows)


@app.get("/commercial-proposal-input.csv")
async def commercial_proposal_input_csv(bid_id: str) -> Response:
    if bid_repository.get_bid(bid_id) is None:
        raise HTTPException(status_code=404, detail="Bid not found")
    return Response(
        ops08_repository.proposal_input_csv(bid_id),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="proposal-input-{bid_id}.csv"'},
    )


@app.get("/commercial-unresolved-actions.csv")
async def commercial_unresolved_actions_csv(bid_id: str) -> Response:
    if bid_repository.get_bid(bid_id) is None:
        raise HTTPException(status_code=404, detail="Bid not found")
    return Response(
        ops08_repository.unresolved_actions_csv(bid_id),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="commercial-actions-{bid_id}.csv"'},
    )


@app.post("/commercial/bulk-owner", response_class=HTMLResponse)
async def commercial_bulk_owner(request: Request) -> Response:
    form = await request.form()
    bid_id = str(form.get("bid_id") or "")
    try:
        targets: list[tuple[str, int]] = []
        for encoded in form.getlist("position_target"):
            position_id, separator, version = str(encoded).partition("|")
            if not separator:
                raise ValueError("invalid commercial position selection")
            targets.append((position_id, int(version)))
        ops08_repository.bulk_assign_owner(
            bid_id, targets, str(form.get("owner") or ""), LOCAL_ACTOR
        )
    except (ValueError, ValidationError, sqlite3.Error) as exc:
        return render(
            "commercial.html",
            status_code=422,
            **_commercial_browser_context(
                bid_id,
                None,
                form_error=(
                    validation_error_message(exc) if isinstance(exc, ValidationError) else str(exc)
                ),
                entered={"owner": str(form.get("owner") or "")},
                selected_position_targets=[str(value) for value in form.getlist("position_target")],
            ),
        )
    return RedirectResponse(f"/commercial?bid_id={quote(bid_id)}", status_code=303)


@app.post("/commercial", response_class=HTMLResponse)
async def commercial_author(request: Request) -> Response:
    form = dict(await request.form())
    bid_id = str(form.get("bid_id", ""))
    try:
        bid_repository.get_bid(bid_id)
        if form.get("operation") == "initialize":
            default_owner = str(form.get("default_owner") or "").strip()
            if not default_owner:
                raise ValueError("Assign a default owner before initializing commercial factors")
            commercial_service.initialize_standard(bid_id, LOCAL_ACTOR, default_owner)
            ops08_repository.initialize(bid_id, default_owner, LOCAL_ACTOR)
        else:
            now = datetime.now(UTC)
            commercial_service.create(
                CommercialItem(
                    bid_id=bid_id,
                    title=str(form.get("title", "")),
                    description=str(form.get("description", "")),
                    category=CommercialCategory(str(form.get("category", "OTHER"))),
                    basis_role=BasisRole(str(form.get("basis_role", "COMMERCIAL_FACTOR"))),
                    owner=str(form.get("owner") or "") or None,
                    provenance=Provenance.from_human(LOCAL_ACTOR),
                    created_at=now,
                    updated_at=now,
                    created_by=LOCAL_ACTOR,
                ),
                LOCAL_ACTOR,
            )
    except (ValidationError, ValueError, sqlite3.Error) as exc:
        projection_date = date.today()
        return render(
            "commercial.html",
            status_code=422,
            commercial_items=commercial_service.list(bid_id or None),
            bids=bid_repository.list_bids(),
            bid_id=bid_id,
            gaps=commercial_service.gaps(bid_id or None, projection_date),
            metrics=commercial_service.metrics(bid_id or None, projection_date),
            form_error=validation_error_message(exc)
            if isinstance(exc, ValidationError)
            else str(exc),
            entered=form,
            commercial_categories=list(CommercialCategory),
            basis_roles=list(BasisRole),
            **_bid_return_context(bid_id or None, BidWorkspaceSection.COMMERCIAL_CONTRACT),
        )
    location = (
        f"/bids/{quote(bid_id)}/commercial-contract#commercial"
        if form.get("origin_section") == "bid"
        else f"/commercial?bid_id={quote(bid_id)}#author"
    )
    return RedirectResponse(location, status_code=303)


@app.get("/api/commercial")
async def commercial_api(bid_id: str | None = None, as_of: str | None = None) -> JSONResponse:
    projection_date = _parse_as_of(as_of)
    return JSONResponse(
        {
            "items": commercial_service.list(bid_id),
            "gaps": [
                gap.model_dump(mode="json")
                for gap in commercial_service.gaps(bid_id, projection_date)
            ],
            "metrics": commercial_service.metrics(bid_id, projection_date),
        }
    )


@app.post("/api/commercial/initialize")
async def initialize_commercial(request: Request) -> JSONResponse:
    body = await request.json()
    try:
        return JSONResponse(
            {
                "created": commercial_service.initialize_standard(
                    str(body["bid_id"]),
                    str(body.get("actor", LOCAL_ACTOR)),
                    str(body.get("default_owner") or "").strip() or None,
                )
            }
        )
    except (KeyError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.post("/api/commercial")
async def create_commercial(request: Request) -> JSONResponse:
    body = await request.json()
    actor = str(body.pop("actor", LOCAL_ACTOR))
    try:
        value = commercial_service.create(CommercialItem.model_validate(body), actor)
        return JSONResponse(value.model_dump(mode="json"), status_code=201)
    except (ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.post("/api/commercial/{commercial_item_id}/links")
async def link_commercial(commercial_item_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    body["commercial_item_id"] = commercial_item_id
    actor = str(body.pop("actor", LOCAL_ACTOR))
    try:
        value = commercial_service.add_link(CommercialLink.model_validate(body), actor)
        return JSONResponse(value.model_dump(mode="json"), status_code=201)
    except (ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.post("/api/commercial/{commercial_item_id}/activate")
async def activate_commercial(commercial_item_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    try:
        commercial_service.activate(
            commercial_item_id,
            int(body.get("expected_version", 1)),
            str(body.get("actor", LOCAL_ACTOR)),
        )
        return JSONResponse({"commercial_item_id": commercial_item_id, "activated": True})
    except ValueError as exc:
        raise _mutation_error(exc) from exc


@app.post("/api/commercial/{commercial_item_id}/assessments")
async def assess_commercial(commercial_item_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    body["commercial_item_id"] = commercial_item_id
    actor = str(body.pop("actor", LOCAL_ACTOR))
    try:
        value = commercial_service.add_assessment(AssessmentVersion.model_validate(body), actor)
        return JSONResponse(value.model_dump(mode="json"), status_code=201)
    except (ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.post("/api/commercial/{commercial_item_id}/reviews")
async def review_commercial(commercial_item_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    body["commercial_item_id"] = commercial_item_id
    actor = str(body.pop("actor", LOCAL_ACTOR))
    try:
        value = commercial_service.review(CommercialReview.model_validate(body), actor)
        return JSONResponse(value.model_dump(mode="json"), status_code=201)
    except (ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.get("/contract-risks", response_class=HTMLResponse)
async def contract_risks_register(
    bid_id: str | None = None,
    as_of: str | None = None,
    position_id: str | None = None,
    topic: str | None = None,
) -> HTMLResponse:
    projection_date = _parse_as_of(as_of)
    contextual_position = None
    context_error = None
    if position_id:
        try:
            contextual_position = _position_context(position_id, bid_id or "")
        except ValueError as exc:
            context_error = str(exc)
    return render(
        "contract_risks.html",
        issues=contract_risk_service.list(bid_id),
        bids=bid_repository.list_bids(),
        bid_id=bid_id or "",
        gaps=contract_risk_service.gaps(bid_id, projection_date),
        metrics=contract_risk_service.metrics(bid_id, projection_date),
        risk_categories=list(RiskCategory),
        contextual_position=contextual_position,
        form_error=context_error,
        entered={"title": topic or "", "position_id": position_id or ""},
        **_bid_return_context(bid_id, BidWorkspaceSection.COMMERCIAL_CONTRACT),
    )


@app.post("/contract-risks", response_class=HTMLResponse)
async def contract_risk_author(request: Request) -> Response:
    form = dict(await request.form())
    bid_id = str(form.get("bid_id", ""))
    try:
        if bid_repository.get_bid(bid_id) is None:
            raise ValueError("Bid not found")
        position_id = str(form.get("position_id") or "")
        if position_id:
            _position_context(position_id, bid_id)
            if form.get("confirm_authoritative") != "on":
                raise ValueError("Confirm creation of the authoritative contract risk")
        now = datetime.now(UTC)
        issue = ContractIssue(
            bid_id=bid_id,
            issue_code=str(form.get("issue_code", "")),
            title=str(form.get("title", "")),
            summary=str(form.get("summary", "")),
            owner=str(form.get("owner") or "") or None,
            provenance=Provenance.from_human(LOCAL_ACTOR),
            created_at=now,
            updated_at=now,
            created_by=LOCAL_ACTOR,
        )
        contract_risk_service.create(issue, LOCAL_ACTOR)
        if position_id:
            ops08_repository.link(position_id, "risk", issue.issue_id, "EXPOSURE", LOCAL_ACTOR)
    except (ValidationError, ValueError, sqlite3.Error) as exc:
        projection_date = date.today()
        error_position = None
        error_position_id = str(form.get("position_id") or "")
        if error_position_id:
            try:
                error_position = _position_context(error_position_id, bid_id)
            except ValueError:
                pass
        return render(
            "contract_risks.html",
            status_code=422,
            issues=contract_risk_service.list(bid_id or None),
            bids=bid_repository.list_bids(),
            bid_id=bid_id,
            gaps=contract_risk_service.gaps(bid_id or None, projection_date),
            metrics=contract_risk_service.metrics(bid_id or None, projection_date),
            risk_categories=list(RiskCategory),
            contextual_position=error_position,
            form_error=validation_error_message(exc)
            if isinstance(exc, ValidationError)
            else str(exc),
            entered=form,
            **_bid_return_context(bid_id or None, BidWorkspaceSection.COMMERCIAL_CONTRACT),
        )
    if form.get("position_id"):
        return RedirectResponse(
            f"/bids/{quote(bid_id)}/commercial-contract#commercial", status_code=303
        )
    return RedirectResponse(f"/contract-risks?bid_id={quote(bid_id)}#author", status_code=303)


@app.get("/contract-risks/{issue_id}", response_class=HTMLResponse)
async def contract_risk_detail(issue_id: str) -> HTMLResponse:
    try:
        detail = contract_risk_service.detail(issue_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return render(
        "contract_risk_detail.html",
        **detail,
        bid_return_path=workspace_path(
            str(detail["issue"]["bid_id"]), BidWorkspaceSection.COMMERCIAL_CONTRACT
        ),
        linked_commercial_positions=_linked_position_labels(
            str(detail["issue"]["bid_id"]), "risk_issue_id"
        ).get(issue_id, []),
    )


@app.get("/decisions", response_class=HTMLResponse)
async def decisions_register(
    bid_id: str | None = None, position_id: str | None = None
) -> HTMLResponse:
    contextual_position = None
    context_error = None
    if position_id:
        try:
            contextual_position = _position_context(position_id, bid_id or "")
        except ValueError as exc:
            context_error = str(exc)
    return render(
        "decisions.html",
        bids=bid_repository.list_bids(),
        bid_id=bid_id or "",
        policies=approval_repository.policies(),
        cases=approval_repository.cases(bid_id),
        gaps=approval_service.gaps(bid_id),
        gate_approvals=bid_repository.list_approvals(bid_id) if bid_id else [],
        approval_types=list(ApprovalType),
        decision_types=list(DecisionType),
        linked_position_labels=(
            _linked_position_labels(bid_id, "decision_case_id") if bid_id else {}
        ),
        contextual_position=contextual_position,
        form_error=context_error,
        entered={"position_id": position_id or ""},
        **_bid_return_context(bid_id, BidWorkspaceSection.COMMERCIAL_CONTRACT),
    )


def _decisions_error(bid_id: str, entered: dict[str, object], exc: Exception) -> HTMLResponse:
    position_id = str(entered.get("position_id") or "")
    contextual_position = None
    if position_id:
        try:
            contextual_position = _position_context(position_id, bid_id)
        except ValueError:
            pass
    return render(
        "decisions.html",
        status_code=422,
        bids=bid_repository.list_bids(),
        bid_id=bid_id,
        policies=approval_repository.policies(),
        cases=approval_repository.cases(bid_id or None),
        gaps=approval_service.gaps(bid_id or None),
        gate_approvals=bid_repository.list_approvals(bid_id) if bid_id else [],
        approval_types=list(ApprovalType),
        decision_types=list(DecisionType),
        contextual_position=contextual_position,
        linked_position_labels=(
            _linked_position_labels(bid_id, "decision_case_id") if bid_id else {}
        ),
        form_error=validation_error_message(exc) if isinstance(exc, ValidationError) else str(exc),
        entered=entered,
        **_bid_return_context(bid_id or None, BidWorkspaceSection.COMMERCIAL_CONTRACT),
    )


@app.post("/decisions/gate-approvals", response_class=HTMLResponse)
async def record_gate_approval(request: Request) -> Response:
    form = dict(await request.form())
    bid_id = str(form.get("bid_id", ""))
    try:
        bid_repository.get_bid(bid_id)
        obtained = form.get("obtained") == "on"
        authority = str(form.get("authority") or "") or None
        evidence = str(form.get("evidence_ref") or "") or None
        decision = str(form.get("decision") or "") or None
        if obtained and (not authority or not evidence or not decision):
            raise ValueError("Obtained approval requires authority, evidence and decision")
        bid_repository.create_approval(
            Approval(
                approval_id=f"APR-{uuid.uuid4().hex}",
                bid_id=bid_id,
                approval_type=ApprovalType(str(form.get("approval_type", ""))),
                obtained=obtained,
                authority=authority,
                evidence_ref=evidence,
                decision=decision,
                decided_at=datetime.now(UTC) if obtained else None,
                provenance=Provenance.from_human(LOCAL_ACTOR),
            ),
            LOCAL_ACTOR,
        )
    except (ValidationError, ValueError, sqlite3.Error) as exc:
        return _decisions_error(bid_id, form, exc)
    return RedirectResponse(f"/decisions?bid_id={quote(bid_id)}#gate-approval", status_code=303)


@app.post("/decisions/cases", response_class=HTMLResponse)
async def create_decision_case_html(request: Request) -> Response:
    form = dict(await request.form())
    bid_id = str(form.get("bid_id", ""))
    try:
        if bid_repository.get_bid(bid_id) is None:
            raise ValueError("Bid not found")
        position_id = str(form.get("position_id") or "")
        if position_id:
            _position_context(position_id, bid_id)
            if form.get("confirm_authoritative") != "on":
                raise ValueError("Confirm creation of the authoritative approval request")
        now = datetime.now(UTC)
        case = DecisionCase(
            bid_id=bid_id,
            case_code=str(form.get("case_code", "")),
            decision_type=DecisionType(str(form.get("decision_type", ""))),
            title=str(form.get("title", "")),
            owner=str(form.get("owner", "")),
            created_by=LOCAL_ACTOR,
            created_at=now,
            provenance=Provenance.from_human(LOCAL_ACTOR),
        )
        approval_service.create_case(case, LOCAL_ACTOR)
        if position_id:
            ops08_repository.link(
                position_id, "decision", case.case_id, "APPROVAL_FOR", LOCAL_ACTOR
            )
    except (ValidationError, ValueError, sqlite3.Error) as exc:
        return _decisions_error(bid_id, form, exc)
    if form.get("position_id"):
        return RedirectResponse(
            f"/bids/{quote(bid_id)}/commercial-contract#commercial", status_code=303
        )
    return RedirectResponse(f"/decisions?bid_id={quote(bid_id)}#decision-case", status_code=303)


@app.get("/api/decisions")
async def decisions_api(bid_id: str | None = None) -> JSONResponse:
    return JSONResponse(
        {
            "policies": approval_repository.policies(),
            "cases": approval_repository.cases(bid_id),
            "routes": approval_repository.routes(bid_id),
            "gaps": approval_service.gaps(bid_id),
            "metrics": approval_service.metrics(bid_id),
        }
    )


@app.post("/api/authority-policies")
async def create_authority_policy(request: Request) -> JSONResponse:
    body = await request.json()
    actor = str(body.pop("actor", LOCAL_ACTOR))
    try:
        value = approval_service.create_policy(AuthorityPolicy.model_validate(body), actor)
        return JSONResponse(value.model_dump(mode="json"), status_code=201)
    except (ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.post("/api/authority-policies/{policy_id}/publish")
async def publish_authority_policy(policy_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    try:
        approval_service.publish_policy(policy_id, str(body.get("actor", LOCAL_ACTOR)))
        return JSONResponse({"policy_id": policy_id, "published": True})
    except ValueError as exc:
        raise _mutation_error(exc) from exc


@app.post("/api/decision-cases")
async def create_decision_case(request: Request) -> JSONResponse:
    body = await request.json()
    actor = str(body.pop("actor", LOCAL_ACTOR))
    try:
        value = approval_service.create_case(DecisionCase.model_validate(body), actor)
        return JSONResponse(value.model_dump(mode="json"), status_code=201)
    except (ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.post("/api/decision-cases/{case_id}/subjects")
async def add_decision_subject(case_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    body["case_id"] = case_id
    actor = str(body.pop("actor", LOCAL_ACTOR))
    try:
        value = approval_service.add_subject(SubjectLink.model_validate(body), actor)
        return JSONResponse(value.model_dump(mode="json"), status_code=201)
    except (ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.post("/api/decision-cases/{case_id}/packages")
async def add_decision_package(case_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    body["case_id"] = case_id
    actor = str(body.pop("actor", LOCAL_ACTOR))
    try:
        value = approval_service.add_package(DecisionPackage.model_validate(body), actor)
        return JSONResponse(value.model_dump(mode="json"), status_code=201)
    except (ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.post("/api/decision-cases/{case_id}/route")
async def route_decision_case(case_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    body["case_id"] = case_id
    facts = body.pop("facts", {})
    actor = str(body.pop("actor", LOCAL_ACTOR))
    try:
        value = approval_service.route(RouteCycle.model_validate(body), facts, actor=actor)
        return JSONResponse(value.model_dump(mode="json"), status_code=201)
    except (ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.post("/api/approval-routes/{route_id}/events")
async def record_approval_event(route_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    body["route_id"] = route_id
    actor = str(body.pop("actor", LOCAL_ACTOR))
    try:
        value = approval_service.event(ApprovalEvent.model_validate(body), actor)
        return JSONResponse(value.model_dump(mode="json"), status_code=201)
    except (ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.get("/commercial-scenarios", response_class=HTMLResponse)
async def commercial_scenarios_register(bid_id: str | None = None) -> HTMLResponse:
    return render(
        "commercial_scenarios.html",
        families=scenario_repository.families(bid_id),
        bid_id=bid_id or "",
        **_bid_return_context(bid_id, BidWorkspaceSection.PROPOSAL_NEGOTIATION),
        metrics=scenario_service.metrics(bid_id),
    )


@app.get("/api/commercial-scenarios")
async def commercial_scenarios_api(bid_id: str | None = None) -> JSONResponse:
    return JSONResponse(
        {
            "families": scenario_repository.families(bid_id),
            "baselines": scenario_repository.baselines(bid_id),
            "metrics": scenario_service.metrics(bid_id),
        }
    )


@app.post("/api/commercial-scenarios/families")
async def create_scenario_family(request: Request) -> JSONResponse:
    body = await request.json()
    actor = str(body.pop("actor", LOCAL_ACTOR))
    try:
        value = scenario_service.create_family(ScenarioFamily.model_validate(body), actor)
        return JSONResponse(value.model_dump(mode="json"), status_code=201)
    except (ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.post("/api/commercial-scenarios/versions")
async def calculate_scenario_version(request: Request) -> JSONResponse:
    body = await request.json()
    actor = str(body.pop("actor", LOCAL_ACTOR))
    try:
        value = scenario_service.calculate(ScenarioVersion.model_validate(body), actor)
        return JSONResponse(value.model_dump(mode="json"), status_code=201)
    except (ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.post("/api/commercial-scenarios/versions/{version_id}/reviews")
async def review_scenario_version(version_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    body["scenario_version_id"] = version_id
    actor = str(body.pop("actor", LOCAL_ACTOR))
    try:
        value = scenario_service.review(
            ScenarioReview.model_validate(body), str(body.get("bid_id", "synthetic")), actor
        )
        return JSONResponse(value.model_dump(mode="json"), status_code=201)
    except (ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.post("/api/commercial-scenarios/baselines")
async def select_scenario_baseline(request: Request) -> JSONResponse:
    body = await request.json()
    actor = str(body.pop("actor", LOCAL_ACTOR))
    body["selected_by"] = actor
    try:
        value = scenario_service.select_baseline(BaselineSelection.model_validate(body))
        return JSONResponse(value.model_dump(mode="json"), status_code=201)
    except (ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.get("/negotiations", response_class=HTMLResponse)
async def negotiations_register(
    bid_id: str | None = None, position_id: str | None = None
) -> HTMLResponse:
    contextual_position = None
    context_error = None
    if position_id:
        try:
            contextual_position = _position_context(position_id, bid_id or "")
        except ValueError as exc:
            context_error = str(exc)
    return render(
        "negotiations.html",
        plans=negotiation_repository.plans(bid_id),
        metrics=negotiation_service.metrics(bid_id),
        bid_id=bid_id or "",
        contextual_position=contextual_position,
        linked_position_labels=(
            _linked_position_labels(bid_id, "negotiation_plan_id") if bid_id else {}
        ),
        form_error=context_error,
        entered={"position_id": position_id or ""},
        **_bid_return_context(
            bid_id,
            BidWorkspaceSection.COMMERCIAL_CONTRACT
            if position_id
            else BidWorkspaceSection.PROPOSAL_NEGOTIATION,
        ),
    )


@app.post("/negotiations/plans", response_class=HTMLResponse)
async def create_negotiation_plan_html(request: Request) -> Response:
    form = dict(await request.form())
    bid_id = str(form.get("bid_id") or "")
    position_id = str(form.get("position_id") or "")
    try:
        if bid_repository.get_bid(bid_id) is None:
            raise ValueError("Bid not found")
        position = _position_context(position_id, bid_id) if position_id else None
        if position_id and form.get("confirm_authoritative") != "on":
            raise ValueError("Confirm creation of the authoritative negotiation plan")
        plan = NegotiationPlan(
            bid_id=bid_id,
            code=str(form.get("code") or ""),
            applicability="NEGOTIATION_REQUIRED",
            title=str(form.get("title") or (position or {}).get("label") or ""),
            owner=str(form.get("owner") or ""),
            created_by=LOCAL_ACTOR,
            created_at=datetime.now(UTC),
        )
        negotiation_service.create_plan(plan, LOCAL_ACTOR)
        if position_id:
            ops08_repository.link(
                position_id, "negotiation", plan.plan_id, "NEGOTIATES", LOCAL_ACTOR
            )
    except (ValidationError, ValueError, sqlite3.Error) as exc:
        error_position = None
        if position_id:
            try:
                error_position = _position_context(position_id, bid_id)
            except ValueError:
                pass
        return render(
            "negotiations.html",
            status_code=422,
            plans=negotiation_repository.plans(bid_id or None),
            metrics=negotiation_service.metrics(bid_id or None),
            bid_id=bid_id,
            contextual_position=error_position,
            linked_position_labels=(
                _linked_position_labels(bid_id, "negotiation_plan_id") if bid_id else {}
            ),
            form_error=validation_error_message(exc)
            if isinstance(exc, ValidationError)
            else str(exc),
            entered=form,
            **_bid_return_context(bid_id or None, BidWorkspaceSection.COMMERCIAL_CONTRACT),
        )
    return RedirectResponse(
        f"/bids/{quote(bid_id)}/commercial-contract#commercial"
        if position_id
        else f"/negotiations?bid_id={quote(bid_id)}",
        status_code=303,
    )


@app.get("/api/negotiations")
async def negotiations_api(bid_id: str | None = None) -> JSONResponse:
    return JSONResponse(
        {
            "plans": negotiation_repository.plans(bid_id),
            "metrics": negotiation_service.metrics(bid_id),
        }
    )


@app.post("/api/negotiations/plans")
async def create_negotiation_plan(request: Request) -> JSONResponse:
    body = await request.json()
    actor = str(body.pop("actor", LOCAL_ACTOR))
    try:
        value = negotiation_service.create_plan(NegotiationPlan.model_validate(body), actor)
        return JSONResponse(value.model_dump(mode="json"), status_code=201)
    except (ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.post("/api/negotiations/plan-versions")
async def create_negotiation_plan_version(request: Request) -> JSONResponse:
    body = await request.json()
    actor = str(body.pop("actor", LOCAL_ACTOR))
    try:
        value = negotiation_service.add_version(PlanVersion.model_validate(body), actor)
        return JSONResponse(value.model_dump(mode="json"), status_code=201)
    except (ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.post("/api/negotiations/mandates")
async def create_negotiation_mandate(request: Request) -> JSONResponse:
    body = await request.json()
    actor = str(body.pop("actor", LOCAL_ACTOR))
    try:
        value = negotiation_service.add_mandate(Mandate.model_validate(body), actor)
        return JSONResponse(value.model_dump(mode="json"), status_code=201)
    except (ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.post("/api/negotiations/trades")
async def create_negotiation_trade(request: Request) -> JSONResponse:
    body = await request.json()
    actor = str(body.pop("actor", LOCAL_ACTOR))
    try:
        value = negotiation_service.add_trade(ConditionalTrade.model_validate(body), actor)
        return JSONResponse(value.model_dump(mode="json"), status_code=201)
    except (ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.post("/api/negotiations/movements")
async def record_negotiation_movement(request: Request) -> JSONResponse:
    body = await request.json()
    try:
        value = negotiation_service.add_movement(NegotiationMovement.model_validate(body))
        return JSONResponse(value.model_dump(mode="json"), status_code=201)
    except (ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.post("/api/negotiations/concessions")
async def record_negotiation_concession(request: Request) -> JSONResponse:
    body = await request.json()
    actor = str(body.pop("actor", LOCAL_ACTOR))
    mandate = Mandate.model_validate(body.pop("mandate")) if body.get("mandate") else None
    try:
        value = negotiation_service.add_concession(
            Concession.model_validate(body), mandate, actor, datetime.now()
        )
        return JSONResponse(value.model_dump(mode="json"), status_code=201)
    except (ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.get("/proposals", response_class=HTMLResponse)
async def proposals_register(bid_id: str | None = None) -> HTMLResponse:
    return render(
        "proposals.html",
        families=proposal_repository.families(bid_id),
        metrics=proposal_service.metrics(bid_id),
        bid_id=bid_id or "",
        proposal_applicabilities=list(ProposalApplicability),
        **_bid_return_context(bid_id, BidWorkspaceSection.PROPOSAL_NEGOTIATION),
    )


@app.post("/proposals/families", response_class=HTMLResponse)
async def create_proposal_family_html(request: Request) -> Response:
    form = dict(await request.form())
    bid_id = str(form.get("bid_id", ""))
    try:
        bid_repository.get_bid(bid_id)
        proposal_service.create_family(
            ProposalFamily(
                bid_id=bid_id,
                code=str(form.get("code", "")),
                applicability=ProposalApplicability(str(form.get("applicability", ""))),
                title=str(form.get("title", "")),
                owner=str(form.get("owner", "")),
                created_by=LOCAL_ACTOR,
                created_at=datetime.now(UTC),
            ),
            LOCAL_ACTOR,
        )
    except (ValidationError, ValueError, sqlite3.Error) as exc:
        return render(
            "proposals.html",
            status_code=422,
            families=proposal_repository.families(bid_id or None),
            metrics=proposal_service.metrics(bid_id or None),
            bid_id=bid_id,
            proposal_applicabilities=list(ProposalApplicability),
            form_error=validation_error_message(exc)
            if isinstance(exc, ValidationError)
            else str(exc),
            entered=form,
            **_bid_return_context(bid_id or None, BidWorkspaceSection.PROPOSAL_NEGOTIATION),
        )
    return RedirectResponse(f"/proposals?bid_id={quote(bid_id)}#author", status_code=303)


@app.get("/api/proposals")
async def proposals_api(bid_id: str | None = None) -> JSONResponse:
    return JSONResponse(
        {
            "families": proposal_repository.families(bid_id),
            "metrics": proposal_service.metrics(bid_id),
            "submission_assurance": "DEFERRED",
        }
    )


@app.post("/api/proposals/profiles")
async def create_proposal_profile(request: Request) -> JSONResponse:
    body = await request.json()
    actor = str(body.pop("actor", LOCAL_ACTOR))
    try:
        value = proposal_service.create_profile(ProposalProfile.model_validate(body), actor)
        return JSONResponse(value.model_dump(mode="json"), status_code=201)
    except (ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.post("/api/proposals/families")
async def create_proposal_family(request: Request) -> JSONResponse:
    body = await request.json()
    actor = str(body.pop("actor", LOCAL_ACTOR))
    try:
        value = proposal_service.create_family(ProposalFamily.model_validate(body), actor)
        return JSONResponse(value.model_dump(mode="json"), status_code=201)
    except (ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.post("/api/proposals/versions")
async def create_proposal_version(request: Request) -> JSONResponse:
    body = await request.json()
    actor = str(body.pop("actor", LOCAL_ACTOR))
    try:
        value = proposal_service.add_version(ProposalVersion.model_validate(body), actor)
        return JSONResponse(value.model_dump(mode="json"), status_code=201)
    except (ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.post("/api/proposals/versions/{version_id}/review")
async def review_proposal_version(version_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    body["proposal_version_id"] = version_id
    actor = str(body.pop("actor", LOCAL_ACTOR))
    bid_id = str(body.pop("bid_id", "synthetic"))
    try:
        value = proposal_service.review(ProposalReview.model_validate(body), bid_id, actor)
        return JSONResponse(value.model_dump(mode="json"), status_code=201)
    except (ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.post("/api/proposals/versions/{version_id}/render")
async def render_proposal_version(version_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    actor = str(body.pop("actor", LOCAL_ACTOR))
    try:
        value = proposal_service.render(ProposalVersion.model_validate(body), actor)
        return JSONResponse(
            {"artifacts": [item.model_dump(mode="json") for item in value]}, status_code=201
        )
    except (ValidationError, ValueError, OSError) as exc:
        raise _mutation_error(exc) from exc


@app.get("/api/contract-risks")
async def contract_risks_api(bid_id: str | None = None, as_of: str | None = None) -> JSONResponse:
    projection_date = _parse_as_of(as_of)
    return JSONResponse(
        {
            "issues": contract_risk_service.list(bid_id),
            "gaps": [
                gap.model_dump(mode="json")
                for gap in contract_risk_service.gaps(bid_id, projection_date)
            ],
            "metrics": contract_risk_service.metrics(bid_id, projection_date),
        }
    )


@app.post("/api/contract-risks")
async def create_contract_risk(request: Request) -> JSONResponse:
    body = await request.json()
    actor = str(body.pop("actor", LOCAL_ACTOR))
    try:
        value = contract_risk_service.create(ContractIssue.model_validate(body), actor)
        return JSONResponse(value.model_dump(mode="json"), status_code=201)
    except (ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.post("/api/contract-risks/{issue_id}/sources")
async def add_contract_source(issue_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    body["issue_id"] = issue_id
    actor = str(body.pop("actor", LOCAL_ACTOR))
    try:
        value = contract_risk_service.add_source(RiskSource.model_validate(body), actor)
        return JSONResponse(value.model_dump(mode="json"), status_code=201)
    except (ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.post("/api/contract-risks/{issue_id}/links")
async def add_contract_link(issue_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    body["issue_id"] = issue_id
    actor = str(body.pop("actor", LOCAL_ACTOR))
    try:
        value = contract_risk_service.add_link(RiskLink.model_validate(body), actor)
        return JSONResponse(value.model_dump(mode="json"), status_code=201)
    except (ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.post("/api/contract-risks/{issue_id}/activate")
async def activate_contract_risk(issue_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    try:
        contract_risk_service.activate(
            issue_id, int(body.get("expected_version", 1)), str(body.get("actor", LOCAL_ACTOR))
        )
        return JSONResponse({"issue_id": issue_id, "activated": True})
    except ValueError as exc:
        raise _mutation_error(exc) from exc


@app.post("/api/contract-risks/{issue_id}/assessments")
async def add_contract_assessment(issue_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    body["issue_id"] = issue_id
    actor = str(body.pop("actor", LOCAL_ACTOR))
    try:
        value = contract_risk_service.assessment(RiskAssessment.model_validate(body), actor)
        return JSONResponse(value.model_dump(mode="json"), status_code=201)
    except (ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.post("/api/contract-risks/{issue_id}/reviews")
async def add_contract_review(issue_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    body["issue_id"] = issue_id
    actor = str(body.pop("actor", LOCAL_ACTOR))
    try:
        value = contract_risk_service.review(RiskReview.model_validate(body), actor)
        return JSONResponse(value.model_dump(mode="json"), status_code=201)
    except (ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.get("/suppliers", response_class=HTMLResponse)
async def suppliers_register(bid_id: str | None = None) -> HTMLResponse:
    """Render the authoritative, bid-scoped supplier assurance register."""
    return render(
        "suppliers.html",
        suppliers=supplier_service.suppliers(bid_id),
        requests=supplier_service.requests(bid_id),
        bid_id=bid_id,
        **_bid_return_context(bid_id, BidWorkspaceSection.MANUFACTURERS_COVERAGE),
    )


@app.get("/supplier-detail/{supplier_id}", response_class=HTMLResponse)
async def supplier_detail(supplier_id: str) -> HTMLResponse:
    suppliers = [row for row in supplier_service.suppliers() if row["supplier_id"] == supplier_id]
    if not suppliers:
        raise HTTPException(status_code=404, detail="Supplier not found")
    supplier = suppliers[0]
    return render(
        "supplier_detail.html",
        supplier=supplier,
        requests=[row for row in supplier_service.requests() if row["supplier_id"] == supplier_id],
        bid_return_path=workspace_path(
            str(supplier["bid_id"]), BidWorkspaceSection.MANUFACTURERS_COVERAGE
        ),
    )


@app.get("/api/suppliers")
async def suppliers_api(bid_id: str | None = None) -> JSONResponse:
    return JSONResponse(
        {
            "suppliers": supplier_service.suppliers(bid_id),
            "requests": supplier_service.requests(bid_id),
        }
    )


@app.get("/supplier-requests/{request_id}", response_class=HTMLResponse)
async def supplier_request_detail(request_id: str) -> HTMLResponse:
    rows = [row for row in supplier_service.requests() if row["request_id"] == request_id]
    if not rows:
        raise HTTPException(status_code=404, detail="Supplier request not found")
    with supplier_service.db._conn() as conn:
        items = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM supplier_request_items WHERE request_id=? ORDER BY sequence",
                (request_id,),
            ).fetchall()
        ]
        links = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM supplier_item_flow_down WHERE request_item_id IN "
                "(SELECT request_item_id FROM supplier_request_items WHERE request_id=?) "
                "ORDER BY request_item_id",
                (request_id,),
            ).fetchall()
        ]
    return render("supplier_request_detail.html", request=rows[0], items=items, links=links)


@app.get("/supplier-responses/{response_version_id}", response_class=HTMLResponse)
async def supplier_response_detail(response_version_id: str) -> HTMLResponse:
    with supplier_service.db._conn() as conn:
        row = conn.execute(
            "SELECT * FROM supplier_response_versions WHERE response_version_id=?",
            (response_version_id,),
        ).fetchone()
        coverage = [
            dict(item)
            for item in conn.execute(
                "SELECT * FROM supplier_response_coverage WHERE response_version_id=? "
                "ORDER BY request_item_id",
                (response_version_id,),
            ).fetchall()
        ]
    if row is None:
        raise HTTPException(status_code=404, detail="Supplier response version not found")
    return render("supplier_response_detail.html", response=dict(row), coverage=coverage)


@app.post("/api/suppliers")
async def create_supplier_api(request: Request) -> JSONResponse:
    try:
        supplier = Supplier.model_validate(await request.json())
        supplier_service.create_supplier(supplier, LOCAL_ACTOR)
        return JSONResponse(jsonable_encoder(supplier.model_dump()), status_code=201)
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/supplier-requests")
async def create_supplier_request_api(request: Request) -> JSONResponse:
    try:
        body = await request.json()
        items = [RequestItem.model_validate(item) for item in body.pop("items", [])]
        supplier_request = SupplierRequest.model_validate(body)
        supplier_service.create_request(supplier_request, items, LOCAL_ACTOR)
        return JSONResponse(jsonable_encoder(supplier_request.model_dump()), status_code=201)
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/supplier-flow-down")
async def create_supplier_flow_down_api(request: Request) -> JSONResponse:
    try:
        link = FlowDownLink.model_validate(await request.json())
        supplier_service.add_flow_down(link, LOCAL_ACTOR)
        return JSONResponse(jsonable_encoder(link.model_dump()), status_code=201)
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/supplier-requests/{request_id}/issue")
async def issue_supplier_request_api(request_id: str, request: Request) -> JSONResponse:
    try:
        body = await request.json()
        supplier_service.issue_request(
            request_id, int(body.get("expected_version", 1)), LOCAL_ACTOR
        )
        return JSONResponse({"request_id": request_id, "state": "ISSUED"})
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=409 if "stale" in str(exc) else 422, detail=str(exc)
        ) from exc


@app.post("/api/supplier-requests/{request_id}/close")
async def close_supplier_request_api(request_id: str, request: Request) -> JSONResponse:
    try:
        body = await request.json()
        supplier_service.close_request(
            request_id,
            int(body.get("expected_version", 1)),
            str(body.get("rationale", "")),
            LOCAL_ACTOR,
        )
        return JSONResponse({"request_id": request_id, "state": "CLOSED"})
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=409 if "stale" in str(exc) else 422, detail=str(exc)
        ) from exc


@app.post("/api/supplier-requests/{request_id}/withdraw")
async def withdraw_supplier_request_api(request_id: str, request: Request) -> JSONResponse:
    try:
        body = await request.json()
        supplier_service.withdraw_request(
            request_id, int(body.get("expected_version", 1)), LOCAL_ACTOR
        )
        return JSONResponse({"request_id": request_id, "state": "WITHDRAWN"})
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=409 if "stale" in str(exc) else 422, detail=str(exc)
        ) from exc


@app.post("/api/supplier-responses")
async def create_supplier_response_api(request: Request) -> JSONResponse:
    try:
        body = await request.json()
        coverage = [Coverage.model_validate(item) for item in body.pop("coverage", [])]
        response = ResponseVersion.model_validate(body)
        supplier_service.create_response(response, coverage, LOCAL_ACTOR)
        return JSONResponse(jsonable_encoder(response.model_dump()), status_code=201)
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/supplier-responses/{response_version_id}/review")
async def review_supplier_response_api(response_version_id: str, request: Request) -> JSONResponse:
    try:
        body = await request.json()
        supplier_service.review_response(
            response_version_id,
            str(body.get("reviewer", LOCAL_ACTOR)),
            ReviewState(str(body.get("state"))),
            body.get("note"),
            body.get("expected_version"),
        )
        return JSONResponse(
            {"response_version_id": response_version_id, "state": body.get("state")}
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=409 if "stale" in str(exc) else 422, detail=str(exc)
        ) from exc


@app.get("/scope-items/{scope_item_id}", response_class=HTMLResponse)
async def scope_item_detail(scope_item_id: str) -> HTMLResponse:
    item = scope_repository.get_scope_item(scope_item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Scope item not found")
    return render(
        "scope_item_detail.html",
        item=item,
        links=scope_repository.requirement_links(scope_item_id=scope_item_id),
        requirements=requirement_service.list_requirements(
            bid_id=item.bid_id, as_of_date=_working_date()
        ),
        scopes=scope_repository.list_scope_items(item.bid_id),
        scope_areas=list(ScopeArea),
        customer_needs=list(CustomerNeed),
        offer_positions=list(OfferPosition),
        pricing_states=list(PricingState),
        materialities=list(Materiality),
        error=None,
        entered={},
        linked_work=ops07w_repository.linked_work("scope", scope_item_id),
    )


@app.get("/interfaces/{interface_id}", response_class=HTMLResponse)
async def interface_detail(interface_id: str) -> HTMLResponse:
    record = scope_repository.get_interface(interface_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Interface not found")
    return render(
        "interface_detail.html",
        interface=record,
        links=scope_repository.interface_scope_links(interface_id),
        scopes=scope_repository.list_scope_items(record.bid_id),
        materialities=list(Materiality),
        dependency_states=list(DependencyState),
        error=None,
        entered={},
        linked_work=ops07w_repository.linked_work("interface", interface_id),
    )


@app.post("/scope-items/{scope_item_id}", response_class=HTMLResponse)
async def edit_scope_item_browser(scope_item_id: str, request: Request) -> Response:
    current = scope_repository.get_scope_item(scope_item_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Scope item not found")
    form = dict(await request.form())
    try:
        expected = int(str(form.pop("expected_version")))
        scope_service.update_scope_item(
            scope_item_id, expected, {k: v for k, v in form.items() if v != ""}, LOCAL_ACTOR
        )
    except (ValidationError, ValueError, sqlite3.Error, TypeError) as exc:
        return render(
            "scope_item_detail.html",
            status_code=422,
            item=current,
            links=scope_repository.requirement_links(scope_item_id=scope_item_id),
            requirements=requirement_service.list_requirements(
                bid_id=current.bid_id, as_of_date=_working_date()
            ),
            scopes=scope_repository.list_scope_items(current.bid_id),
            scope_areas=list(ScopeArea),
            customer_needs=list(CustomerNeed),
            offer_positions=list(OfferPosition),
            pricing_states=list(PricingState),
            materialities=list(Materiality),
            error=validation_error_message(exc) if isinstance(exc, ValidationError) else str(exc),
            entered=form,
        )
    return RedirectResponse(
        f"/scope-items/{quote(scope_item_id)}?bid_id={quote(current.bid_id)}", status_code=303
    )


@app.post("/scope-items/{scope_item_id}/withdraw")
async def withdraw_scope_item_browser(scope_item_id: str, request: Request) -> Response:
    current = scope_repository.get_scope_item(scope_item_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Scope item not found")
    form = await request.form()
    try:
        scope_service.withdraw_scope_item(
            scope_item_id, int(str(form.get("expected_version"))), LOCAL_ACTOR
        )
    except (ValueError, TypeError) as exc:
        return render(
            "scope_item_detail.html",
            status_code=422,
            item=current,
            links=scope_repository.requirement_links(scope_item_id=scope_item_id),
            requirements=requirement_service.list_requirements(
                bid_id=current.bid_id, as_of_date=_working_date()
            ),
            scopes=scope_repository.list_scope_items(current.bid_id),
            scope_areas=list(ScopeArea),
            customer_needs=list(CustomerNeed),
            offer_positions=list(OfferPosition),
            pricing_states=list(PricingState),
            materialities=list(Materiality),
            error=str(exc),
            entered={"expected_version": str(form.get("expected_version") or "")},
            linked_work=ops07w_repository.linked_work("scope", scope_item_id),
        )
    return RedirectResponse(f"/scope-interfaces?bid_id={quote(current.bid_id)}", status_code=303)


@app.post("/interfaces/{interface_id}", response_class=HTMLResponse)
async def edit_interface_browser(interface_id: str, request: Request) -> Response:
    current = scope_repository.get_interface(interface_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Interface not found")
    form = dict(await request.form())
    try:
        expected = int(str(form.pop("expected_version")))
        scope_service.update_interface(
            interface_id, expected, {k: v for k, v in form.items() if v != ""}, LOCAL_ACTOR
        )
    except (ValidationError, ValueError, sqlite3.Error, TypeError) as exc:
        return render(
            "interface_detail.html",
            status_code=422,
            interface=current,
            links=scope_repository.interface_scope_links(interface_id),
            scopes=scope_repository.list_scope_items(current.bid_id),
            materialities=list(Materiality),
            dependency_states=list(DependencyState),
            error=validation_error_message(exc) if isinstance(exc, ValidationError) else str(exc),
            entered=form,
        )
    return RedirectResponse(
        f"/interfaces/{quote(interface_id)}?bid_id={quote(current.bid_id)}", status_code=303
    )


@app.post("/interfaces/{interface_id}/withdraw")
async def withdraw_interface_browser(interface_id: str, request: Request) -> Response:
    current = scope_repository.get_interface(interface_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Interface not found")
    form = await request.form()
    try:
        scope_service.withdraw_interface(
            interface_id, int(str(form.get("expected_version"))), LOCAL_ACTOR
        )
    except (ValueError, TypeError) as exc:
        return render(
            "interface_detail.html",
            status_code=422,
            interface=current,
            links=scope_repository.interface_scope_links(interface_id),
            scopes=scope_repository.list_scope_items(current.bid_id),
            materialities=list(Materiality),
            dependency_states=list(DependencyState),
            error=str(exc),
            entered={"expected_version": str(form.get("expected_version") or "")},
            linked_work=ops07w_repository.linked_work("interface", interface_id),
        )
    return RedirectResponse(f"/scope-interfaces?bid_id={quote(current.bid_id)}", status_code=303)


@app.post("/interfaces/{interface_id}/scope-links", response_class=HTMLResponse)
async def link_interface_scope_browser(interface_id: str, request: Request) -> Response:
    current = scope_repository.get_interface(interface_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Interface not found")
    form = dict(await request.form())
    try:
        scope_service.link_interface_scope(
            interface_id, str(form.get("scope_item_id", "")), current.bid_id, LOCAL_ACTOR
        )
    except (ValueError, sqlite3.Error) as exc:
        return render(
            "interface_detail.html",
            status_code=422,
            interface=current,
            links=scope_repository.interface_scope_links(interface_id),
            scopes=scope_repository.list_scope_items(current.bid_id),
            materialities=list(Materiality),
            dependency_states=list(DependencyState),
            error=str(exc),
            entered=form,
        )
    return RedirectResponse(f"/interfaces/{quote(interface_id)}#relationships", status_code=303)


@app.post("/interfaces/{interface_id}/scope-links/{scope_item_id}/remove")
async def unlink_interface_scope_browser(interface_id: str, scope_item_id: str) -> Response:
    current = scope_repository.get_interface(interface_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Interface not found")
    try:
        scope_service.unlink_interface_scope(
            interface_id, scope_item_id, current.bid_id, LOCAL_ACTOR
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RedirectResponse(f"/interfaces/{quote(interface_id)}#relationships", status_code=303)


@app.post("/scope-items/{scope_item_id}/requirement-links", response_class=HTMLResponse)
async def link_scope_requirement_browser(scope_item_id: str, request: Request) -> Response:
    scope = scope_repository.get_scope_item(scope_item_id)
    if scope is None:
        raise HTTPException(status_code=404, detail="Scope item not found")
    form = dict(await request.form())
    requirement_id = str(form.get("requirement_id", ""))
    try:
        ops07_repository.link_scope(scope.bid_id, requirement_id, scope_item_id, LOCAL_ACTOR)
    except (ValueError, sqlite3.Error) as exc:
        return render(
            "scope_item_detail.html",
            status_code=422,
            item=scope,
            links=scope_repository.requirement_links(scope_item_id=scope_item_id),
            requirements=requirement_service.list_requirements(
                bid_id=scope.bid_id, as_of_date=_working_date()
            ),
            scopes=scope_repository.list_scope_items(scope.bid_id),
            scope_areas=list(ScopeArea),
            customer_needs=list(CustomerNeed),
            offer_positions=list(OfferPosition),
            pricing_states=list(PricingState),
            materialities=list(Materiality),
            error=str(exc),
            entered=form,
        )
    return RedirectResponse(f"/scope-items/{quote(scope_item_id)}#relationships", status_code=303)


@app.get("/api/scope-interfaces")
async def scope_interfaces_api(bid_id: str | None = None) -> JSONResponse:
    return JSONResponse(
        jsonable_encoder(
            {
                "scope_items": scope_repository.list_scope_items(bid_id),
                "interfaces": scope_repository.list_interfaces(bid_id),
            }
        )
    )


@app.post("/api/scope-items")
async def create_scope_item_api(request: Request) -> JSONResponse:
    body = await request.json()
    actor = str(body.pop("actor", LOCAL_ACTOR))
    body["created_at"] = datetime.now(ZoneInfo("UTC"))
    body["updated_at"] = body["created_at"]
    body["created_by"] = actor
    body["provenance"] = Provenance.from_human(actor)
    requirement_ids = body.pop("requirement_ids", None)
    try:
        item = ScopeItem(**body)
        scope_service.create_scope_item(item, actor, requirement_ids)
        return JSONResponse(jsonable_encoder(item), status_code=201)
    except (ValueError, ValidationError) as exc:
        raise _mutation_error(exc) from exc


@app.post("/api/interfaces")
async def create_interface_api(request: Request) -> JSONResponse:
    body = await request.json()
    actor = str(body.pop("actor", LOCAL_ACTOR))
    body["created_at"] = datetime.now(ZoneInfo("UTC"))
    body["updated_at"] = body["created_at"]
    body["created_by"] = actor
    body["provenance"] = Provenance.from_human(actor)
    scope_item_ids = body.pop("scope_item_ids", None)
    try:
        record = InterfaceRecord(**body)
        scope_service.create_interface(record, actor, scope_item_ids)
        return JSONResponse(jsonable_encoder(record), status_code=201)
    except (ValueError, ValidationError) as exc:
        raise _mutation_error(exc) from exc


@app.get("/api/requirement-source-choices")
async def requirement_source_choices(bid_id: str) -> JSONResponse:
    try:
        return JSONResponse(jsonable_encoder(requirement_service.source_choices(bid_id)))
    except ValueError as exc:
        raise _mutation_error(exc) from exc


@app.get("/api/requirements")
async def list_requirements_api(bid_id: str | None = None) -> JSONResponse:
    records = requirement_service.list_requirements(
        bid_id=bid_id,
        as_of_date=_working_date(),
    )
    return JSONResponse(jsonable_encoder(records))


@app.get("/api/requirements/{requirement_id}")
async def get_requirement_api(requirement_id: str) -> JSONResponse:
    try:
        return JSONResponse(jsonable_encoder(requirement_service.detail(requirement_id)))
    except ValueError as exc:
        raise _mutation_error(exc) from exc


@app.post("/api/requirements")
async def create_requirement(request: Request) -> JSONResponse:
    body = await request.json()
    actor = str(body.pop("actor", LOCAL_ACTOR)) if isinstance(body, dict) else LOCAL_ACTOR
    try:
        created = requirement_service.create_requirement(body, actor)
        return JSONResponse(jsonable_encoder(created), status_code=201)
    except (ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.patch("/api/requirements/{requirement_id}/metadata")
async def update_requirement_metadata(
    requirement_id: str,
    request: Request,
) -> JSONResponse:
    body = await request.json()
    actor = str(body.pop("actor", LOCAL_ACTOR)) if isinstance(body, dict) else LOCAL_ACTOR
    try:
        updated = requirement_service.update_metadata(requirement_id, body, actor)
        return JSONResponse(jsonable_encoder(updated))
    except (ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.post("/api/requirements/{requirement_id}/workflow")
async def update_requirement_workflow(
    requirement_id: str,
    request: Request,
) -> JSONResponse:
    body = await request.json()
    actor = str(body.pop("actor", LOCAL_ACTOR)) if isinstance(body, dict) else LOCAL_ACTOR
    try:
        updated = requirement_service.update_workflow(requirement_id, body, actor)
        return JSONResponse(jsonable_encoder(updated))
    except (ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.post("/api/requirements/{requirement_id}/review")
async def review_requirement(requirement_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    actor = str(body.pop("actor", LOCAL_ACTOR)) if isinstance(body, dict) else LOCAL_ACTOR
    try:
        updated = requirement_service.record_review(requirement_id, body, actor)
        return JSONResponse(jsonable_encoder(updated))
    except (ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.post("/api/requirements/{requirement_id}/withdraw")
async def withdraw_requirement(requirement_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    actor = str(body.pop("actor", LOCAL_ACTOR)) if isinstance(body, dict) else LOCAL_ACTOR
    try:
        updated = requirement_service.withdraw(requirement_id, body, actor)
        return JSONResponse(jsonable_encoder(updated))
    except (ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.get("/documents", response_class=HTMLResponse)
async def controlled_documents(
    request: Request,
    bid_id: str | None = None,
    category: str | None = None,
    lifecycle: str | None = None,
) -> HTMLResponse:
    try:
        typed_category = DocumentCategory(category) if category else None
        typed_lifecycle = DocumentLifecycle(lifecycle) if lifecycle else None
        entries = document_service.list_register_entries(
            bid_id=bid_id or None,
            category=typed_category,
            lifecycle=typed_lifecycle,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    bids = bid_repository.list_bids()
    bid_names = {bid.bid_id: bid.project_name for bid in bids}
    return render(
        "documents.html",
        entries=entries,
        bids=bids,
        bid_names=bid_names,
        categories=list(DocumentCategory),
        document_category_label=document_category_label,
        lifecycles=list(DocumentLifecycle),
        selected_bid=bid_id or "",
        selected_category=category or "",
        selected_lifecycle=lifecycle or "",
        actor=LOCAL_ACTOR,
        # Controlled customer documents are stage 3, which lives in the Bid's
        # Package intake section; returning to Requirements would lose the stage.
        **_bid_return_context(bid_id, BidWorkspaceSection.PACKAGE_INTAKE),
    )


@app.get("/documents/{document_id}", response_class=HTMLResponse)
async def controlled_document_detail(
    request: Request,
    document_id: str,
    verify_version_id: str | None = None,
) -> HTMLResponse:
    try:
        document = document_service.get_document(document_id)
        versions = document_service.list_versions(document_id)
        integrity_result = None
        if verify_version_id is not None:
            if verify_version_id not in {version.document_version_id for version in versions}:
                raise DocumentVersionNotFoundError(
                    f"Document version not found in {document_id}: {verify_version_id}"
                )
            integrity_result = document_service.verify_integrity(verify_version_id)
    except ControlledDocumentIntegrityError:
        issues = [
            issue
            for issue in document_repository.diagnose_logical_integrity()
            if issue.document_id == document_id
        ]
        return render(
            "document_integrity_error.html",
            document_id=document_id,
            issues=issues,
        )
    except ValueError as exc:
        raise _mutation_error(exc) from exc
    bid = bid_repository.get_bid(document.bid_id)
    return render(
        "document_detail.html",
        document=document,
        versions=versions,
        bid=bid,
        integrity_result=integrity_result,
        categories=list(DocumentCategory),
        document_category_label=document_category_label,
        logical_issues=[
            issue
            for issue in document_repository.diagnose_logical_integrity()
            if issue.document_id == document_id
        ],
        actor=LOCAL_ACTOR,
    )


@app.post("/api/controlled-documents")
async def register_controlled_document(
    file: UploadFile = File(...),  # noqa: B008
    bid_id: str = Form(...),
    title: str = Form(...),
    category: str = Form(...),
    version_label: str = Form(...),
    document_number: str | None = Form(None),
    issuer: str | None = Form(None),
    notes: str | None = Form(None),
    issued_date: str | None = Form(None),
    received_at: str | None = Form(None),
    actor: str = Form(LOCAL_ACTOR),
) -> JSONResponse:
    try:
        document, version = document_service.register_document(
            {
                "bid_id": bid_id,
                "title": title,
                "document_number": document_number,
                "category": category,
                "issuer": issuer,
                "notes": notes,
                "version_label": version_label,
                "issued_date": issued_date or None,
                "received_at": received_at or None,
            },
            file.file,
            file.filename or "document.bin",
            file.content_type,
            actor,
        )
        return JSONResponse(
            jsonable_encoder({"document": document, "version": version}),
            status_code=201,
        )
    except (
        ValidationError,
        ValueError,
        OSError,
        ManagedStorageFailureError,
        DocumentStoreBusyError,
    ) as exc:
        raise _mutation_error(exc) from exc


@app.post("/api/controlled-documents/{document_id}/versions")
async def add_controlled_document_version(
    document_id: str,
    file: UploadFile = File(...),  # noqa: B008
    version_label: str = Form(...),
    expected_document_version: int = Form(...),
    expected_current_version_id: str = Form(...),
    issued_date: str | None = Form(None),
    received_at: str | None = Form(None),
    actor: str = Form(LOCAL_ACTOR),
) -> JSONResponse:
    try:
        document, version = document_service.add_version(
            document_id,
            {
                "version_label": version_label,
                "expected_document_version": expected_document_version,
                "expected_current_version_id": expected_current_version_id,
                "issued_date": issued_date or None,
                "received_at": received_at or None,
            },
            file.file,
            file.filename or "document.bin",
            file.content_type,
            actor,
        )
        return JSONResponse(jsonable_encoder({"document": document, "version": version}))
    except (
        ValidationError,
        ValueError,
        OSError,
        ManagedStorageFailureError,
        DocumentStoreBusyError,
    ) as exc:
        raise _mutation_error(exc) from exc


@app.patch("/api/controlled-documents/{document_id}")
async def edit_controlled_document(document_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    actor = str(body.pop("actor", LOCAL_ACTOR)) if isinstance(body, dict) else LOCAL_ACTOR
    try:
        updated = document_service.update_metadata(document_id, body, actor)
        return JSONResponse(jsonable_encoder(updated))
    except (ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.post("/api/controlled-documents/{document_id}/withdraw")
async def withdraw_controlled_document(document_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    actor = str(body.pop("actor", LOCAL_ACTOR)) if isinstance(body, dict) else LOCAL_ACTOR
    try:
        expected_version = int(body["expected_version"])
        updated = document_service.withdraw(document_id, expected_version, actor)
        return JSONResponse(jsonable_encoder(updated))
    except (KeyError, TypeError, ValidationError, ValueError) as exc:
        raise _mutation_error(exc) from exc


@app.get("/api/controlled-document-versions/{document_version_id}/integrity")
async def verify_controlled_document_version(document_version_id: str) -> JSONResponse:
    try:
        return JSONResponse(
            jsonable_encoder(document_service.verify_integrity(document_version_id))
        )
    except ValueError as exc:
        raise _mutation_error(exc) from exc


@app.get("/api/controlled-document-versions/{document_version_id}/download")
async def download_controlled_document_version(
    document_version_id: str,
) -> StreamingResponse:
    try:
        version, stream = document_service.open_download(document_version_id)
    except (ValueError, OSError, ManagedStorageFailureError) as exc:
        raise _mutation_error(exc) from exc

    async def chunks():
        try:
            while chunk := stream.read(1024 * 1024):
                yield chunk
        finally:
            stream.close()

    encoded_name = quote(version.original_filename, safe="")
    return StreamingResponse(
        chunks(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}"},
    )


@app.get("/contract/{doc_id}", response_class=HTMLResponse)
async def contract_detail(request: Request, doc_id: str):
    document = db.get_document(doc_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    return render("contract.html", contract=document)


@app.post("/api/upload")
async def upload_document(
    file: UploadFile = File(...),  # noqa: B008
    doc_type_hint: str = None,
):
    allowed = {".pdf", ".docx", ".doc", ".txt"}
    suffix = Path(file.filename).suffix.lower()
    if suffix not in allowed:
        raise HTTPException(status_code=400, detail=f"File type {suffix} not supported.")

    doc_id = str(uuid.uuid4())
    file_path = UPLOADS_DIR / f"{doc_id}{suffix}"
    content = await file.read()
    file_path.write_bytes(content)

    try:
        extracted = doc_processor.process(file_path)
    except Exception as e:
        file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=f"Could not extract text: {str(e)}") from e

    # Check for scanned PDF pages
    scan_warning = None
    scanned = extracted.get("scanned_pages", 0)
    total_pages = extracted.get("page_count", 1) or 1
    if scanned > 0:
        pct_scanned = round(scanned / total_pages * 100)
        if pct_scanned > 80:
            scan_warning = (
                f"WARNING: This PDF appears to be scanned ({pct_scanned}% image pages). "
                f"Text extraction will be very limited. For best results use the original "
                f"Word document or a text-based PDF."
            )
        elif pct_scanned > 30:
            scan_warning = (
                f"Note: {scanned} of {total_pages} pages appear to be scanned images. "
                f"Some content may not be extracted."
            )

    db.create_document(
        {
            "id": doc_id,
            "filename": file.filename,
            "file_path": str(file_path),
            "status": "uploaded",
            "upload_date": datetime.now().isoformat(),
            "word_count": extracted["word_count"],
            "page_count": extracted["page_count"],
            "raw_text": extracted["text"],
            "doc_type": doc_type_hint or "General Contract",
            **({"error_message": scan_warning} if scan_warning else {}),
        }
    )

    # Run pre-processing immediately — pure Python, no LLM, completes in <1s
    try:
        pre = preprocessor.preprocess(
            extracted["text"],
            file.filename,
            doc_type_hint or "General Contract",
        )
        db.update_document(
            doc_id,
            {
                "structured_markdown": pre["structured_markdown"],
                "contractual_items_json": json.dumps(pre.get("contractual_items", [])),
            },
        )
        has_markdown = True
        section_count = pre.get("section_count", 0)
        noise_pct = pre.get("noise_removed_pct", 0)
        print(
            f"  Pre-processed: {section_count} sections, "
            f"{noise_pct:.1f}% noise removed, "
            f"{len(pre['structured_markdown']):,} chars markdown"
        )
    except Exception as e:
        print(f"  Pre-processing warning: {e}")
        has_markdown = False
        section_count = 0
        noise_pct = 0

    return JSONResponse(
        {
            "doc_id": doc_id,
            "contract_id": doc_id,  # backward-compat alias
            "filename": file.filename,
            "word_count": extracted["word_count"],
            "page_count": extracted["page_count"],
            "status": "uploaded",
            "has_markdown": has_markdown,
            "section_count": section_count,
            "noise_removed_pct": noise_pct,
            "scan_warning": scan_warning,
        }
    )


@app.post("/api/analyse/{doc_id}")
async def analyse_document(doc_id: str, background_tasks: BackgroundTasks):
    document = db.get_document(doc_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    if not document.get("raw_text"):
        raise HTTPException(status_code=422, detail="No text content found")

    if not llm_client.health_check():
        raise HTTPException(
            status_code=503,
            detail=(
                f"LM Studio is not reachable at {llm_client.base_url}. "
                "Please ensure LM Studio is running with a model loaded."
            ),
        )

    print(
        "  ⚠ Analysis running — prevent computer sleep to avoid interruption. "
        "Windows: Settings → Power & sleep → Sleep → Never"
    )
    db.update_document(doc_id, {"status": "processing"})
    progress_store[doc_id] = {
        "step_num": 0,
        "total_steps": 6,
        "step_name": "Starting",
        "message": "Analysis queued...",
        "percent": 0,
        "completed_steps": [],
        "error": None,
        "review_priority": None,
    }
    background_tasks.add_task(_run_analysis_background, doc_id)
    return JSONResponse({"status": "processing", "doc_id": doc_id})


@app.get("/api/progress/{doc_id}")
async def get_progress(doc_id: str):
    if doc_id in progress_store:
        return JSONResponse(progress_store[doc_id])

    # progress_store was lost (server restart) — fall back to DB
    document = db.get_document(doc_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    status = document.get("status", "")
    if status == "complete":
        return JSONResponse(
            {
                "step_num": 7,
                "total_steps": 6,
                "step_name": "Complete",
                "message": "Analysis complete",
                "percent": 100,
                "completed_steps": [],
                "error": None,
                "review_priority": document.get("review_priority"),
            }
        )
    if status in ("processing", "interrupted"):
        msg = (
            document.get("error_message")
            or "Server was restarted during analysis — please re-analyse"
        )
        return JSONResponse(
            {
                "step_num": 0,
                "total_steps": 6,
                "step_name": "Interrupted",
                "message": msg,
                "percent": 0,
                "completed_steps": [],
                "error": msg,
                "review_priority": None,
            }
        )
    if status == "error":
        return JSONResponse(
            {
                "step_num": 0,
                "total_steps": 6,
                "step_name": "Error",
                "message": document.get("error_message", "Analysis failed"),
                "percent": 0,
                "completed_steps": [],
                "error": document.get("error_message", "Analysis failed"),
                "review_priority": None,
            }
        )
    # uploaded / unknown — no progress to report yet
    raise HTTPException(status_code=404, detail="No progress data for this document")


@app.get("/api/contracts")
async def list_contracts():
    return JSONResponse(db.get_all_documents())


@app.get("/api/documents")
async def list_documents():
    return JSONResponse(db.get_all_documents())


@app.delete("/api/contract/{doc_id}")
async def delete_document(doc_id: str):
    try:
        document = db.get_document(doc_id)
        if not document:
            raise HTTPException(
                status_code=404,
                detail=f"Document {doc_id} not found",
            )
        if document.get("control_managed") == 1:
            raise HTTPException(
                status_code=405,
                detail="Controlled documents cannot be hard deleted; withdraw them instead",
            )
        # Delete associated files — each is optional so guard against None
        if document.get("file_path"):
            Path(document["file_path"]).unlink(missing_ok=True)
        if document.get("pdf_report_path"):
            (REPORTS_DIR / document["pdf_report_path"]).unlink(missing_ok=True)
        if document.get("excel_report_path"):
            (REPORTS_DIR / document["excel_report_path"]).unlink(missing_ok=True)
        if document.get("tracker_path"):
            (REPORTS_DIR / document["tracker_path"]).unlink(missing_ok=True)
        db.delete_document(doc_id)
        # Clean up any in-memory state
        progress_store.pop(doc_id, None)
        cancel_requests.discard(doc_id)
        return JSONResponse({"deleted": True, "id": doc_id})
    except HTTPException:
        raise
    except Exception as e:
        import traceback

        print(f"[ERROR] Delete failed for {doc_id}: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Delete failed: {str(e)}") from e


@app.patch("/api/document/{doc_id}/issue/{issue_id}")
async def patch_issue(doc_id: str, issue_id: int, request: Request):
    body = await request.json()
    field, value = body.get("field"), body.get("value")
    if not field:
        raise HTTPException(status_code=422, detail="field required")
    ok = db.update_issue(issue_id, field, value)
    if not ok:
        raise HTTPException(status_code=422, detail=f"Field '{field}' is not editable")
    return JSONResponse({"updated": True, "issue_id": issue_id})


@app.patch("/api/document/{doc_id}/obligation/{ob_id}")
async def patch_obligation(doc_id: str, ob_id: int, request: Request):
    body = await request.json()
    field, value = body.get("field"), body.get("value")
    if not field:
        raise HTTPException(status_code=422, detail="field required")
    ok = db.update_obligation_field(ob_id, field, value)
    if not ok:
        raise HTTPException(status_code=422, detail=f"Field '{field}' is not editable")
    return JSONResponse({"updated": True, "ob_id": ob_id})


@app.get("/api/document/{doc_id}/findings")
async def get_findings(doc_id: str):
    document = db.get_document(doc_id)
    if not document:
        raise HTTPException(status_code=404, detail="Not found")
    findings = db.get_clause_findings(doc_id)
    return JSONResponse(findings)


@app.get("/api/document/{doc_id}/obligations")
async def get_obligations(doc_id: str):
    document = db.get_document(doc_id)
    if not document:
        raise HTTPException(status_code=404, detail="Not found")
    obligations = db.get_obligations_for_document(doc_id)
    return JSONResponse(obligations)


@app.get("/api/document/{doc_id}/issues")
async def get_negotiation_issues(doc_id: str):
    document = db.get_document(doc_id)
    if not document:
        raise HTTPException(status_code=404, detail="Not found")
    issues = db.get_issues_for_document(doc_id)
    return JSONResponse(issues)


@app.get("/api/document/{doc_id}/markdown/download")
async def download_markdown(doc_id: str):
    """Download structured markdown as a .md file attachment."""
    import re

    from fastapi.responses import Response as FastAPIResponse

    document = db.get_document(doc_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    md = document.get("structured_markdown")
    if not md:
        raise HTTPException(
            status_code=404,
            detail="Markdown not available for this document. Re-analyse to generate it.",
        )
    original_name = Path(document["filename"]).stem
    safe_name = re.sub(r"[^\w\-_]", "_", original_name)
    filename = f"{safe_name}_processed.md"
    return FastAPIResponse(
        content=md,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/document/{doc_id}/markdown")
async def view_markdown(doc_id: str):
    """View structured markdown as plain text (no download prompt)."""
    from fastapi.responses import Response as FastAPIResponse

    document = db.get_document(doc_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    md = document.get("structured_markdown")
    if not md:
        raise HTTPException(
            status_code=404,
            detail="Markdown not available for this document. Re-analyse to generate it.",
        )
    return FastAPIResponse(content=md, media_type="text/plain; charset=utf-8")


@app.get("/api/document/{doc_id}/tracker")
async def get_tracker(doc_id: str):
    document = db.get_document(doc_id)
    if not document:
        raise HTTPException(status_code=404, detail="Not found")
    tracker = document.get("tracker_path")
    if not tracker:
        raise HTTPException(status_code=404, detail="Tracker sheet not yet generated")
    from fastapi.responses import FileResponse

    path = REPORTS_DIR / tracker
    if not path.exists():
        raise HTTPException(status_code=404, detail="Tracker file not found on disk")
    return FileResponse(
        str(path),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"tracker_{document.get('filename', 'document')}.xlsx",
    )


@app.post("/api/document/{doc_id}/regenerate-reports")
async def regenerate_reports(doc_id: str):
    """Re-generate PDF, Excel, and tracker reports from existing analysis_json.
    Does NOT re-run LLM analysis — useful after fixing report generation bugs."""
    document = db.get_document(doc_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    if document.get("status") not in ("complete", "error"):
        raise HTTPException(
            status_code=422,
            detail="Reports can only be regenerated for completed documents",
        )
    analysis_json_str = document.get("analysis_json")
    if not analysis_json_str:
        raise HTTPException(
            status_code=422,
            detail="No analysis data found — please run analysis first",
        )
    try:
        results = json.loads(analysis_json_str)
    except (json.JSONDecodeError, TypeError) as exc:
        raise HTTPException(status_code=422, detail="Analysis data is corrupted") from exc

    try:
        pdf_filename = f"report_{doc_id}.pdf"
        xlsx_filename = f"report_{doc_id}.xlsx"
        tracker_filename = f"tracker_{doc_id}.xlsx"

        report_generator.generate(document, results, REPORTS_DIR / pdf_filename)
        excel_generator.generate(document, results, REPORTS_DIR / xlsx_filename)

        contractual_items = []
        if document.get("contractual_items_json"):
            try:
                contractual_items = json.loads(document["contractual_items_json"])
            except Exception:
                pass
        preprocessor.generate_tracker_sheet(
            document, contractual_items, results, REPORTS_DIR / tracker_filename
        )

        db.update_document(
            doc_id,
            {
                "pdf_report_path": pdf_filename,
                "excel_report_path": xlsx_filename,
                "tracker_path": tracker_filename,
            },
        )
        return JSONResponse(
            {
                "status": "complete",
                "pdf": pdf_filename,
                "excel": xlsx_filename,
                "tracker": tracker_filename,
            }
        )
    except Exception as e:
        import traceback

        print(f"[ERROR] Report regeneration failed for {doc_id}: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Report generation failed: {str(e)}") from e


@app.post("/api/cancel/{doc_id}")
async def cancel_analysis(doc_id: str):
    document = db.get_document(doc_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    cancel_requests.add(doc_id)
    db.update_document(
        doc_id,
        {
            "status": "cancelled",
            "error_message": "Analysis cancelled by user",
        },
    )
    if doc_id in progress_store:
        progress_store[doc_id] = {
            **progress_store[doc_id],
            "step_name": "Cancelling",
            "message": "Cancellation requested — stopping after current step...",
            "error": None,
        }
    return JSONResponse({"cancelled": True, "id": doc_id})


@app.patch("/api/document/{doc_id}/context")
async def update_document_context(doc_id: str, request: Request):
    """Update review context fields (business_role, jurisdiction, etc.) for a document."""
    document = db.get_document(doc_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    body = await request.json()
    allowed = {
        "business_role",
        "delivery_model",
        "product_families_json",
        "review_notes",
        "jurisdiction",
        "doc_type",
    }
    updates = {k: v for k, v in body.items() if k in allowed}
    if "product_families" in body:
        updates["product_families_json"] = json.dumps(body["product_families"])
    if updates:
        db.update_document(doc_id, updates)
    return JSONResponse({"updated": True, "fields": list(updates.keys())})


# ── Knowledge Management API ──────────────────────────────────────────────────

_KNOWLEDGE_TABLES = {
    "company_positions": (
        "get_all_company_positions",
        "create_company_position",
        "update_company_position",
        "deactivate_company_position",
    ),
    "insurance_positions": (
        "get_all_insurance_positions",
        "create_insurance_position",
        "update_insurance_position",
        "deactivate_insurance_position",
    ),
    "escalation_rules": (
        "get_all_escalation_rules",
        "create_escalation_rule",
        "update_escalation_rule",
        "deactivate_escalation_rule",
    ),
    "product_risk_profiles": (
        "get_all_product_risk_profiles",
        "create_product_risk_profile",
        "update_product_risk_profile",
        "deactivate_product_risk_profile",
    ),
    "commercial_term_library": (
        "get_all_commercial_terms",
        "create_commercial_term",
        "update_commercial_term",
        "deactivate_commercial_term",
    ),
    "product_term_risk_map": (
        "get_all_product_term_maps",
        "create_product_term_map",
        "update_product_term_map",
        "deactivate_product_term_map",
    ),
    "deliverable_templates": (
        "get_all_deliverable_templates",
        "create_deliverable_template",
        "update_deliverable_template",
        "deactivate_deliverable_template",
    ),
    "clause_playbooks": (
        "get_all_clause_playbooks",
        "create_clause_playbook",
        "update_clause_playbook",
        "deactivate_clause_playbook",
    ),
    "review_routing_rules": (
        "get_all_routing_rules",
        "create_routing_rule",
        "update_routing_rule",
        "deactivate_routing_rule",
    ),
    "negotiation_history": (
        "get_all_negotiation_history",
        "create_negotiation_record",
        "update_negotiation_record",
        None,
    ),
    "supplier_intelligence": (
        "get_all_supplier_intelligence",
        "create_supplier_intel",
        "update_supplier_intel",
        "deactivate_supplier_intel",
    ),
    "project_type_profiles": (
        "get_all_project_type_profiles",
        "create_project_type_profile",
        "update_project_type_profile",
        "deactivate_project_type_profile",
    ),
    "jurisdiction_rules": (
        "get_all_jurisdiction_rules",
        "create_jurisdiction_rule",
        "update_jurisdiction_rule",
        "deactivate_jurisdiction_rule",
    ),
}


def _resolve_table(table_name: str):
    if table_name not in _KNOWLEDGE_TABLES:
        raise HTTPException(status_code=404, detail=f"Knowledge table '{table_name}' not found")
    return _KNOWLEDGE_TABLES[table_name]


@app.get("/api/knowledge/{table_name}")
async def get_knowledge_table(table_name: str):
    get_name, _, _, _ = _resolve_table(table_name)
    method = getattr(db, get_name, None)
    if not method:
        raise HTTPException(status_code=404, detail=f"No getter for {table_name}")
    try:
        records = method(active_only=False)
    except TypeError:
        records = method()
    return JSONResponse(records)


@app.post("/api/knowledge/{table_name}")
async def create_knowledge_record(table_name: str, request: Request):
    _, create_name, _, _ = _resolve_table(table_name)
    method = getattr(db, create_name, None)
    if not method:
        raise HTTPException(status_code=404, detail=f"No creator for {table_name}")
    body = await request.json()
    new_id = method(body)
    return JSONResponse({"created": True, "id": new_id})


@app.patch("/api/knowledge/{table_name}/{record_id}")
async def update_knowledge_record(table_name: str, record_id: int, request: Request):
    _, _, update_name, _ = _resolve_table(table_name)
    method = getattr(db, update_name, None)
    if not method:
        raise HTTPException(status_code=404, detail=f"No updater for {table_name}")
    body = await request.json()
    method(record_id, body)
    return JSONResponse({"updated": True})


@app.delete("/api/knowledge/{table_name}/{record_id}")
async def deactivate_knowledge_record(table_name: str, record_id: int):
    _, _, _, deactivate_name = _resolve_table(table_name)
    if not deactivate_name:
        # negotiation_history uses delete instead
        _, _, _, _ = _resolve_table(table_name)
        db.delete_negotiation_record(record_id)
        return JSONResponse({"deleted": True})
    method = getattr(db, deactivate_name, None)
    if not method:
        raise HTTPException(status_code=404, detail=f"No deactivator for {table_name}")
    method(record_id)
    return JSONResponse({"deactivated": True})


@app.get("/api/knowledge/{table_name}/export")
async def export_knowledge_table(table_name: str):
    _resolve_table(table_name)  # validate
    import tempfile

    from fastapi.responses import FileResponse

    kio = KnowledgeIO(db)
    tmp = Path(tempfile.mktemp(suffix=".xlsx"))
    success = kio.export_table_to_excel(table_name, tmp)
    if not success:
        raise HTTPException(status_code=404, detail=f"No data to export for {table_name}")
    return FileResponse(
        str(tmp),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"{table_name}_export.xlsx",
    )


@app.post("/api/knowledge/{table_name}/import")
async def import_knowledge_table(
    table_name: str,
    file: UploadFile = File(...),  # noqa: B008
):
    _resolve_table(table_name)  # validate
    import tempfile

    kio = KnowledgeIO(db)
    tmp = Path(tempfile.mktemp(suffix=".xlsx"))
    tmp.write_bytes(await file.read())
    try:
        results = kio.import_table_from_excel(table_name, tmp)
    finally:
        tmp.unlink(missing_ok=True)
    return JSONResponse(results)


@app.get("/api/knowledge-export-all")
async def export_all_knowledge():
    import tempfile

    from fastapi.responses import FileResponse

    kio = KnowledgeIO(db)
    tmp = Path(tempfile.mktemp(suffix=".xlsx"))
    kio.export_all_knowledge(tmp)
    return FileResponse(
        str(tmp),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="contractiq_knowledge_base.xlsx",
    )


@app.get("/knowledge", response_class=HTMLResponse)
async def knowledge_base_page(request: Request):
    ke = KnowledgeEngine(db)
    stats = ke.get_knowledge_summary()
    return render("knowledge.html", stats=stats)


@app.get("/api/llm-status")
async def llm_status():
    reachable = llm_client.health_check()
    models = llm_client.list_models() if reachable else []
    return JSONResponse({"reachable": reachable, "models": models})


@app.get("/api/llm-test")
async def llm_test():
    import time

    if not llm_client.health_check():
        raise HTTPException(
            status_code=503,
            detail=f"LM Studio is not reachable at {llm_client.base_url}.",
        )
    start = time.time()
    status = "ok"
    response_text = ""
    try:
        response_text = llm_client.chat(
            messages=[{"role": "user", "content": "Reply with the single word: OK"}],
            max_tokens=10,
            temperature=0,
            context_label="llm-test",
        ).strip()
    except TimeoutError as e:
        status = "timeout"
        response_text = str(e)
    except Exception as e:
        status = "error"
        response_text = str(e)
    elapsed = round(time.time() - start, 2)
    return JSONResponse(
        {
            "response": response_text,
            "elapsed_seconds": elapsed,
            "model_url": llm_client.base_url,
            "status": status,
        }
    )


if __name__ == "__main__":
    _timeout = APP_CONFIG.get("lm_studio_timeout", 600)
    _max_chars = APP_CONFIG.get("max_document_chars", 80000)
    _connect_timeout = APP_CONFIG.get("lm_studio_connect_timeout", 30)
    _read_timeout = APP_CONFIG.get("lm_studio_read_timeout", 3600)
    print(
        f"\n  ContractIQ starting on http://localhost:8000\n"
        f"  LM Studio: {llm_client.base_url} | "
        f"Read timeout: {_read_timeout}s | Connect timeout: {_connect_timeout}s | "
        f"Max doc chars: {_max_chars:,}\n"
    )
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
