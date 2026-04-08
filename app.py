"""
ContractIQ - Personal Contract & Bid Analysis System
7-Pillar analysis engine with background processing and live progress.
"""

import uuid
import json
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException, Request, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
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

# ── App Setup ──────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
UPLOADS_DIR = BASE_DIR / "uploads"
REPORTS_DIR = BASE_DIR / "reports"
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
db = Database(BASE_DIR / "data" / "contractiq.db")
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
        db.update_document(doc["id"], {
            "status": "interrupted",
            "error_message": (
                "Analysis was interrupted — server restarted or connection lost. "
                "Click Analyse to retry."
            ),
        })
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
#     "error": str|null, "risk_level": str|null }
progress_store: dict = {}

# ── Cancellation store ────────────────────────────────────────────────────────
# Set of document IDs for which cancellation has been requested
cancel_requests: set = set()


def _run_analysis_background(doc_id: str) -> None:
    """Synchronous worker — FastAPI runs this in a thread-pool via BackgroundTasks."""
    document = db.get_document(doc_id)
    if not document:
        progress_store[doc_id] = {
            "step_num": 0, "total_steps": 7, "step_name": "Error",
            "message": "Document not found", "percent": 0,
            "completed_steps": [], "error": "Document not found", "risk_level": None,
        }
        return

    seen_steps: dict = {}  # step_num → step_name

    def _progress_cb(
        step_num: int, total_steps: int, step_name: str,
        message: str, percent: int
    ) -> None:
        seen_steps[step_num] = step_name
        completed = [
            {"step_num": k, "step_name": v}
            for k, v in sorted(seen_steps.items())
            if k < step_num
        ]
        progress_store[doc_id] = {
            "step_num": step_num,
            "total_steps": total_steps,
            "step_name": step_name,
            "message": message,
            "percent": percent,
            "completed_steps": completed,
            "error": None,
            "risk_level": None,
        }

    def _check_cancel() -> bool:
        return doc_id in cancel_requests

    try:
        raw_text      = document["raw_text"]
        doc_type_hint = document.get("doc_type", "General Contract")

        # ── Stage 1: Pure-Python pre-processing (no LLM, instant) ────────────
        pre = preprocessor.preprocess(
            raw_text,
            document["filename"],
            doc_type=doc_type_hint,
        )
        structured_md     = pre["structured_markdown"]
        contractual_items = pre["contractual_items"]   # always []
        section_count     = pre["section_count"]
        noise_pct         = pre["noise_removed_pct"]
        word_count        = pre["word_count"]

        # Persist markdown immediately so /api/document/{id}/markdown works
        db.update_document(doc_id, {
            "structured_markdown":   structured_md,
            "contractual_items_json": json.dumps(contractual_items),
        })

        print(
            f"  Pre-processing complete: {section_count} sections | "
            f"{word_count:,} words | {noise_pct}% noise removed"
        )
        _progress_cb(
            1, 7, "Pre-processing",
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
        preprocessor.generate_tracker_sheet(
            document, contractual_items, results, tracker_path
        )

        # Save structured findings to relational tables
        pillar_results = results.get("pillars", [])
        if pillar_results:
            db.save_clause_findings(doc_id, pillar_results)
            db.save_negotiation_issues(doc_id, pillar_results)

        obligations = results.get("obligations", [])
        if obligations:
            db.save_obligations(doc_id, obligations)

        # Save report package record
        db.save_report_package(doc_id, pdf_filename, xlsx_filename,
                               datetime.now().isoformat())

        # Update document record
        db.update_document(doc_id, {
            "status": "complete",
            "analysis_date": datetime.now().isoformat(),
            "analysis_json": json.dumps(results),
            "pdf_report_path": pdf_filename,
            "excel_report_path": xlsx_filename,
            "risk_score": results.get("risk_score", {}).get("overall_score", 0),
            "risk_level": results.get("risk_score", {}).get("level", "Unknown"),
            "doc_type": results.get("doc_type", "General Contract"),
            "doc_type_confidence": results.get("doc_type_confidence", "Low"),
            "executive_summary": results.get("executive_summary", ""),
            "key_subject": results.get("key_subject", ""),
            "contract_value": results.get("contract_value", ""),
            "contract_duration": results.get("contract_duration", ""),
            "governing_law": results.get("governing_law", ""),
            "counterparty": results.get("counterparty", ""),
            "tracker_path": tracker_filename,
        })

        # Build completed_steps from all 7 steps
        all_completed = [
            {"step_num": k, "step_name": v}
            for k, v in sorted(seen_steps.items())
        ]
        progress_store[doc_id] = {
            "step_num": 8,
            "total_steps": 7,
            "step_name": "Complete",
            "message": "Analysis complete",
            "percent": 100,
            "completed_steps": all_completed,
            "error": None,
            "risk_level": results.get("risk_score", {}).get("level", "Unknown"),
        }

    except InterruptedError:
        cancel_requests.discard(doc_id)
        completed = [{"step_num": k, "step_name": v} for k, v in sorted(seen_steps.items())]
        db.update_document(doc_id, {
            "status": "cancelled",
            "error_message": "Analysis cancelled by user",
        })
        progress_store[doc_id] = {
            "step_num": 0, "total_steps": 7, "step_name": "Cancelled",
            "message": "Analysis cancelled by user", "percent": 0,
            "completed_steps": completed,
            "error": "Analysis cancelled by user", "risk_level": None,
        }

    except Exception as e:
        import traceback
        print(f"[ERROR] Analysis failed for {doc_id}: {e}")
        traceback.print_exc()
        db.update_document(doc_id, {"status": "error", "error_message": str(e)})
        progress_store[doc_id] = {
            "step_num": 0, "total_steps": 7, "step_name": "Error",
            "message": str(e), "percent": 0,
            "completed_steps": [
                {"step_num": k, "step_name": v}
                for k, v in sorted(seen_steps.items())
            ],
            "error": str(e), "risk_level": None,
        }


# ── Routes ───────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    documents = db.get_all_documents()
    return render("index.html", contracts=documents)


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
        raise HTTPException(
            status_code=400,
            detail=f"File type {suffix} not supported."
        )

    doc_id = str(uuid.uuid4())
    file_path = UPLOADS_DIR / f"{doc_id}{suffix}"
    content = await file.read()
    file_path.write_bytes(content)

    try:
        extracted = doc_processor.process(file_path)
    except Exception as e:
        file_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=422,
            detail=f"Could not extract text: {str(e)}"
        )

    db.create_document({
        "id": doc_id,
        "filename": file.filename,
        "file_path": str(file_path),
        "status": "uploaded",
        "upload_date": datetime.now().isoformat(),
        "word_count": extracted["word_count"],
        "page_count": extracted["page_count"],
        "raw_text": extracted["text"],
        "doc_type": doc_type_hint or "General Contract",
    })

    # Run pre-processing immediately — pure Python, no LLM, completes in <1s
    try:
        pre = preprocessor.preprocess(
            extracted["text"],
            file.filename,
            doc_type_hint or "General Contract",
        )
        db.update_document(doc_id, {
            "structured_markdown":   pre["structured_markdown"],
            "contractual_items_json": json.dumps(pre.get("contractual_items", [])),
        })
        has_markdown  = True
        section_count = pre.get("section_count", 0)
        noise_pct     = pre.get("noise_removed_pct", 0)
        print(
            f"  Pre-processed: {section_count} sections, "
            f"{noise_pct:.1f}% noise removed, "
            f"{len(pre['structured_markdown']):,} chars markdown"
        )
    except Exception as e:
        print(f"  Pre-processing warning: {e}")
        has_markdown  = False
        section_count = 0
        noise_pct     = 0

    return JSONResponse({
        "doc_id": doc_id,
        "contract_id": doc_id,  # backward-compat alias
        "filename": file.filename,
        "word_count": extracted["word_count"],
        "page_count": extracted["page_count"],
        "status": "uploaded",
        "has_markdown": has_markdown,
        "section_count": section_count,
        "noise_removed_pct": noise_pct,
    })


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
        "step_num": 0, "total_steps": 7, "step_name": "Starting",
        "message": "Analysis queued...", "percent": 0,
        "completed_steps": [], "error": None, "risk_level": None,
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
        return JSONResponse({
            "step_num": 8, "total_steps": 7, "step_name": "Complete",
            "message": "Analysis complete", "percent": 100,
            "completed_steps": [], "error": None,
            "risk_level": document.get("risk_level"),
        })
    if status in ("processing", "interrupted"):
        msg = document.get("error_message") or "Server was restarted during analysis — please re-analyse"
        return JSONResponse({
            "step_num": 0, "total_steps": 7, "step_name": "Interrupted",
            "message": msg, "percent": 0, "completed_steps": [],
            "error": msg, "risk_level": None,
        })
    if status == "error":
        return JSONResponse({
            "step_num": 0, "total_steps": 7, "step_name": "Error",
            "message": document.get("error_message", "Analysis failed"),
            "percent": 0, "completed_steps": [],
            "error": document.get("error_message", "Analysis failed"),
            "risk_level": None,
        })
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
        filename=f"tracker_{document.get('filename','document')}.xlsx",
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
        pdf_filename  = f"report_{doc_id}.pdf"
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

        db.update_document(doc_id, {
            "pdf_report_path":   pdf_filename,
            "excel_report_path": xlsx_filename,
            "tracker_path":      tracker_filename,
        })
        return JSONResponse({
            "status":  "complete",
            "pdf":     pdf_filename,
            "excel":   xlsx_filename,
            "tracker": tracker_filename,
        })
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
    db.update_document(doc_id, {
        "status": "cancelled",
        "error_message": "Analysis cancelled by user",
    })
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
    allowed = {"business_role", "delivery_model", "product_families_json", "review_notes", "jurisdiction", "doc_type"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if "product_families" in body:
        updates["product_families_json"] = json.dumps(body["product_families"])
    if updates:
        db.update_document(doc_id, updates)
    return JSONResponse({"updated": True, "fields": list(updates.keys())})


# ── Knowledge Management API ──────────────────────────────────────────────────

_KNOWLEDGE_TABLES = {
    "company_positions":    ("get_all_company_positions",    "create_company_position",    "update_company_position",    "deactivate_company_position"),
    "insurance_positions":  ("get_all_insurance_positions",  "create_insurance_position",  "update_insurance_position",  "deactivate_insurance_position"),
    "escalation_rules":     ("get_all_escalation_rules",     "create_escalation_rule",     "update_escalation_rule",     "deactivate_escalation_rule"),
    "product_risk_profiles":("get_all_product_risk_profiles","create_product_risk_profile","update_product_risk_profile","deactivate_product_risk_profile"),
    "commercial_term_library": ("get_all_commercial_terms",  "create_commercial_term",     "update_commercial_term",     "deactivate_commercial_term"),
    "product_term_risk_map":("get_all_product_term_maps",    "create_product_term_map",    "update_product_term_map",    "deactivate_product_term_map"),
    "deliverable_templates":("get_all_deliverable_templates","create_deliverable_template","update_deliverable_template","deactivate_deliverable_template"),
    "clause_playbooks":     ("get_all_clause_playbooks",     "create_clause_playbook",     "update_clause_playbook",     "deactivate_clause_playbook"),
    "review_routing_rules": ("get_all_routing_rules",        "create_routing_rule",        "update_routing_rule",        "deactivate_routing_rule"),
    "negotiation_history":  ("get_all_negotiation_history",  "create_negotiation_record",  "update_negotiation_record",  None),
    "supplier_intelligence":("get_all_supplier_intelligence","create_supplier_intel",      "update_supplier_intel",      "deactivate_supplier_intel"),
    "project_type_profiles":("get_all_project_type_profiles","create_project_type_profile","update_project_type_profile","deactivate_project_type_profile"),
    "jurisdiction_rules":   ("get_all_jurisdiction_rules",   "create_jurisdiction_rule",   "update_jurisdiction_rule",   "deactivate_jurisdiction_rule"),
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
    return JSONResponse({
        "response":         response_text,
        "elapsed_seconds":  elapsed,
        "model_url":        llm_client.base_url,
        "status":           status,
    })


if __name__ == "__main__":
    _cfg = json.loads((BASE_DIR / "config.json").read_text()) if (BASE_DIR / "config.json").exists() else {}
    _timeout = _cfg.get("lm_studio_timeout", 600)
    _max_chars = _cfg.get("max_document_chars", 80000)
    _connect_timeout = _cfg.get("lm_studio_connect_timeout", 30)
    _read_timeout    = _cfg.get("lm_studio_read_timeout", 3600)
    print(
        f"\n  ContractIQ starting on http://localhost:8000\n"
        f"  LM Studio: {llm_client.base_url} | "
        f"Read timeout: {_read_timeout}s | Connect timeout: {_connect_timeout}s | "
        f"Max doc chars: {_max_chars:,}\n"
    )
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
