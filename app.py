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
report_generator = ReportGenerator(REPORTS_DIR)
excel_generator = ExcelGenerator(REPORTS_DIR)

# ── Progress store ────────────────────────────────────────────────────────────
# Keyed by document_id. Structure per entry:
#   { "step_num": int, "total_steps": int,
#     "step_name": str, "message": str, "percent": int,
#     "completed_steps": [{"step_num": int, "step_name": str}],
#     "error": str|null, "risk_level": str|null }
progress_store: dict = {}


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

    try:
        results = analysis_engine.run_full_analysis(
            document["raw_text"], document["filename"],
            progress_callback=_progress_cb,
        )

        # Generate PDF report
        pdf_filename = f"report_{doc_id}.pdf"
        pdf_path = REPORTS_DIR / pdf_filename
        report_generator.generate(document, results, pdf_path)

        # Generate Excel workbook
        xlsx_filename = f"report_{doc_id}.xlsx"
        xlsx_path = REPORTS_DIR / xlsx_filename
        excel_generator.generate(document, results, xlsx_path)

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

    except Exception as e:
        db.update_document(doc_id, {"status": "error", "error_message": str(e)})
        progress_store[doc_id] = {
            "step_num": 0, "total_steps": 7, "step_name": "Error",
            "message": str(e), "percent": 0,
            "completed_steps": list(
                {"step_num": k, "step_name": v}
                for k, v in sorted(seen_steps.items())
            ),
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
    if doc_id not in progress_store:
        raise HTTPException(
            status_code=404,
            detail="No progress data for this document"
        )
    return JSONResponse(progress_store[doc_id])


@app.get("/api/contracts")
async def list_contracts():
    return JSONResponse(db.get_all_documents())


@app.get("/api/documents")
async def list_documents():
    return JSONResponse(db.get_all_documents())


@app.delete("/api/contract/{doc_id}")
async def delete_document(doc_id: str):
    document = db.get_document(doc_id)
    if not document:
        raise HTTPException(status_code=404, detail="Not found")
    Path(document["file_path"]).unlink(missing_ok=True)
    if document.get("pdf_report_path"):
        (REPORTS_DIR / document["pdf_report_path"]).unlink(missing_ok=True)
    if document.get("excel_report_path"):
        (REPORTS_DIR / document["excel_report_path"]).unlink(missing_ok=True)
    db.delete_document(doc_id)
    return JSONResponse({"deleted": True})


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


@app.get("/api/llm-status")
async def llm_status():
    reachable = llm_client.health_check()
    models = llm_client.list_models() if reachable else []
    return JSONResponse({"reachable": reachable, "models": models})


if __name__ == "__main__":
    print("\n  ContractIQ starting on http://localhost:8000\n")
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
