"""
ContractIQ - Personal Contract & Bid Analysis System
7-Pillar analysis engine with background processing and live progress.
"""

import json
import os
import uuid
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, BackgroundTasks
from fastapi.encoders import jsonable_encoder
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import ValidationError
import uvicorn

from core.document_processor import DocumentProcessor
from core.document_preprocessor import DocumentPreprocessor
from core.llm_client import LMStudioClient
from core.analysis_engine import AnalysisEngine
from core.report_generator import ReportGenerator
from core.excel_generator import ExcelGenerator
from core.database import Database
from core.knowledge_bootstrap import bootstrap_knowledge
from core.knowledge_io import KnowledgeIO
from core.knowledge_engine import KnowledgeEngine
from core.bid_repository import BidRepository
from core.work_item_repository import (
    StaleWorkItemError,
    WorkItemNotFoundError,
    WorkItemRepository,
)
from core.work_item_service import MyDayService, WorkItemService, validation_error_message
from core.my_day import WorkItemSnapshot
from core.work_items import WorkItem, WorkItemKind, WorkItemPriority, WorkItemStatus
from core.document_control import (
    ControlledDocumentIdentityError,
    ControlledDocumentIntegrityError,
    DocumentCategory,
    DocumentLifecycle,
)
from core.document_repository import (
    ControlledDocumentNotFoundError,
    DocumentRepository,
    DocumentStoreBusyError,
    DocumentVersionNotFoundError,
    DuplicateDocumentVersionError,
    StaleDocumentError,
)
from core.document_service import DocumentService
from core.readiness_service import evaluate_readiness
from core.requirement_repository import (
    RequirementNotFoundError,
    RequirementRepository,
    RequirementSourceError,
    StaleRequirementError,
)
from core.requirement_service import RequirementService
from core.requirements import (
    RequirementCategory,
    RequirementLifecycle,
    RequirementOrigin,
    RequirementReviewState,
    RequirementSignificance,
    RequirementStage,
    RequirementWorkState,
    ResponseDisposition,
)
from core.managed_document_storage import (
    EmptyManagedFileError,
    ManagedDocumentStorage,
    ManagedFileTooLargeError,
    ManagedStorageFailureError,
)
from core.scope_repository import ScopeInterfaceRepository
from core.scope_service import ScopeInterfaceService
from core.scope_interfaces import InterfaceRecord, ScopeItem
from core.schemas import Provenance
from core.supplier_repository import SupplierRepository
from core.supplier_service import SupplierService
from core.supplier_assurance import (
    Coverage,
    FlowDownLink,
    RequestItem,
    ResponseVersion,
    ReviewState,
    Supplier,
    SupplierRequest,
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


def render(template_name: str, **context) -> HTMLResponse:
    template = jinja_env.get_template(template_name)
    return HTMLResponse(template.render(**context))


# ── Core Services ───────────────────────────────────────────────────────────
db = Database(Path(os.environ.get("CONTRACTIQ_DB_PATH", BASE_DIR / "data" / "contractiq.db")))
bid_repository = BidRepository(db)
work_item_repository = WorkItemRepository(db)
work_item_service = WorkItemService(work_item_repository, bid_repository)
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
my_day_service = MyDayService(
    work_item_repository,
    bid_repository,
    db,
    requirement_repository=requirement_repository,
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
async def index(request: Request):
    documents = db.get_all_documents()
    return render(
        "index.html",
        contracts=documents,
        requirement_coverage=requirement_service.coverage(
            bid_id=None,
            as_of_date=_working_date(),
        ),
    )


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
async def my_day(request: Request, as_of: str | None = None) -> HTMLResponse:
    projection_date = _parse_as_of(as_of)
    projection = my_day_service.get_my_day(as_of=projection_date)
    bids = bid_repository.list_bids()
    bid_names = {bid.bid_id: bid.project_name for bid in bids}
    archived_items = [
        WorkItemSnapshot(
            item=item,
            bid_name=bid_names.get(item.bid_id, item.bid_id),
        )
        for item in work_item_repository.list()
        if item.status in {WorkItemStatus.COMPLETED, WorkItemStatus.CANCELLED}
    ]
    archived_items.sort(
        key=lambda snapshot: (snapshot.item.updated_at, snapshot.item.work_item_id),
        reverse=True,
    )
    return render(
        "my_day.html",
        projection=projection,
        bids=bids,
        archived_items=archived_items,
        kinds=list(WorkItemKind),
        priorities=list(WorkItemPriority),
        statuses=list(WorkItemStatus),
        actor=LOCAL_ACTOR,
    )


@app.get("/api/work-items")
async def list_work_items(bid_id: str | None = None) -> JSONResponse:
    return _json_item(work_item_repository.list(bid_id=bid_id))


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
    due_state: str | None = None,
    attention: str | None = None,
    exception: str | None = None,
    as_of: str | None = None,
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
            "due_state": due_state or "",
            "attention": attention or "",
            "exception": exception or "",
            "as_of": projection_date.isoformat(),
        },
        as_of_date=projection_date,
        actor=LOCAL_ACTOR,
    )


@app.get("/requirements/{requirement_id}", response_class=HTMLResponse)
async def requirement_detail(request: Request, requirement_id: str) -> HTMLResponse:
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
    return render(
        "requirement_detail.html",
        detail=detail,
        requirement=detail.requirement,
        bid=bid,
        coverage=coverage,
        readiness=readiness,
        history=history,
        categories=list(RequirementCategory),
        significances=list(RequirementSignificance),
        stages=list(RequirementStage),
        dispositions=list(ResponseDisposition),
        work_states=list(RequirementWorkState),
        review_states=list(RequirementReviewState),
        actor=LOCAL_ACTOR,
    )


@app.get("/bids/{bid_id}", response_class=HTMLResponse)
async def bid_detail(request: Request, bid_id: str) -> HTMLResponse:
    bid = bid_repository.get_bid(bid_id)
    if bid is None:
        raise HTTPException(status_code=404, detail=f"Bid not found: {bid_id}")
    as_of_date = _working_date()
    requirements = requirement_service.list_requirements(
        bid_id=bid_id,
        as_of_date=as_of_date,
    )
    return render(
        "bid_detail.html",
        bid=bid,
        requirements=requirements,
        coverage=requirement_service.coverage(
            bid_id=bid_id,
            as_of_date=as_of_date,
        ),
        readiness=evaluate_readiness(bid_repository, db, bid_id),
        documents=document_service.list_register_entries(bid_id=bid_id),
    )


@app.get("/scope-interfaces", response_class=HTMLResponse)
async def scope_interfaces_register(bid_id: str | None = None) -> HTMLResponse:
    scopes = scope_repository.list_scope_items(bid_id)
    interfaces = scope_repository.list_interfaces(bid_id)
    coverage = scope_service.projection(bid_id, _working_date()) if bid_id else None
    return render(
        "scope_interfaces.html",
        scopes=scopes,
        interfaces=interfaces,
        coverage=coverage,
        bid_id=bid_id,
    )


@app.get("/suppliers", response_class=HTMLResponse)
async def suppliers_register(bid_id: str | None = None) -> HTMLResponse:
    """Render the authoritative, bid-scoped supplier assurance register."""
    return render(
        "suppliers.html",
        suppliers=supplier_service.suppliers(bid_id),
        requests=supplier_service.requests(bid_id),
        bid_id=bid_id,
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
                "SELECT * FROM supplier_item_flow_down WHERE request_item_id IN (SELECT request_item_id FROM supplier_request_items WHERE request_id=?) ORDER BY request_item_id",
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
                "SELECT * FROM supplier_response_coverage WHERE response_version_id=? ORDER BY request_item_id",
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
    )


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
        lifecycles=list(DocumentLifecycle),
        selected_bid=bid_id or "",
        selected_category=category or "",
        selected_lifecycle=lifecycle or "",
        actor=LOCAL_ACTOR,
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
        logical_issues=[
            issue
            for issue in document_repository.diagnose_logical_integrity()
            if issue.document_id == document_id
        ],
        actor=LOCAL_ACTOR,
    )


@app.post("/api/controlled-documents")
async def register_controlled_document(
    file: UploadFile = File(...),
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
    file: UploadFile = File(...),
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

    def chunks():
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
    file: UploadFile = File(...),
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
        raise HTTPException(status_code=422, detail=f"Could not extract text: {str(e)}")

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
        f"  ⚠ Analysis running — prevent computer sleep to avoid interruption. "
        f"Windows: Settings → Power & sleep → Sleep → Never"
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
        raise HTTPException(status_code=500, detail=f"Delete failed: {str(e)}")


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
    except (json.JSONDecodeError, TypeError):
        raise HTTPException(status_code=422, detail="Analysis data is corrupted")

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
        raise HTTPException(status_code=500, detail=f"Report generation failed: {str(e)}")


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
async def import_knowledge_table(table_name: str, file: UploadFile = File(...)):
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
