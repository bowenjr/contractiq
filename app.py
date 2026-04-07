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
analysis_engine = AnalysisEngine(llm_client)
preprocessor = DocumentPreprocessor(llm_client)
report_generator = ReportGenerator(REPORTS_DIR)
excel_generator = ExcelGenerator(REPORTS_DIR)

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

    return JSONResponse({
        "doc_id": doc_id,
        "contract_id": doc_id,  # backward-compat alias
        "filename": file.filename,
        "word_count": extracted["word_count"],
        "page_count": extracted["page_count"],
        "status": "uploaded",
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
    if status == "processing":
        return JSONResponse({
            "step_num": 0, "total_steps": 7, "step_name": "Interrupted",
            "message": "Server was restarted during analysis — please re-analyse",
            "percent": 0, "completed_steps": [],
            "error": "Server was restarted during analysis — please re-analyse",
            "risk_level": None,
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
    obligations = db.get_obligations(doc_id)
    return JSONResponse(obligations)


@app.get("/api/document/{doc_id}/issues")
async def get_negotiation_issues(doc_id: str):
    document = db.get_document(doc_id)
    if not document:
        raise HTTPException(status_code=404, detail="Not found")
    issues = db.get_negotiation_issues(doc_id)
    return JSONResponse(issues)


@app.get("/api/document/{doc_id}/markdown")
async def get_markdown(doc_id: str):
    document = db.get_document(doc_id)
    if not document:
        raise HTTPException(status_code=404, detail="Not found")
    md = document.get("structured_markdown")
    if not md:
        raise HTTPException(status_code=404, detail="Structured markdown not yet generated")
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(md, media_type="text/markdown; charset=utf-8")


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
    print(
        f"\n  ContractIQ starting on http://localhost:8000\n"
        f"  LM Studio: {llm_client.base_url} | "
        f"Timeout: {_timeout}s ({_timeout // 60} minutes) per LLM call | "
        f"Max doc chars: {_max_chars:,}\n"
    )
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
