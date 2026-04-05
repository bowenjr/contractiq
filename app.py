"""
ContractIQ - Personal Contract & Bid Analysis System
Phase 1: Full analysis pipeline (upload → risk report)
"""

import uuid
import json
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
import uvicorn

from core.document_processor import DocumentProcessor
from core.llm_client import LMStudioClient
from core.analysis_engine import AnalysisEngine
from core.report_generator import ReportGenerator
from core.database import Database

# ── App Setup ──────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
UPLOADS_DIR = BASE_DIR / "uploads"
REPORTS_DIR = BASE_DIR / "reports"
UPLOADS_DIR.mkdir(exist_ok=True)
REPORTS_DIR.mkdir(exist_ok=True)

app = FastAPI(title="ContractIQ", version="1.0.0")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.mount("/reports", StaticFiles(directory=str(REPORTS_DIR)), name="reports")

# Use Jinja2 directly — avoids Starlette/Jinja2 version compatibility bug
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


# ── Routes ───────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    contracts = db.get_all_contracts()
    return render("index.html", contracts=contracts)


@app.get("/contract/{contract_id}", response_class=HTMLResponse)
async def contract_detail(request: Request, contract_id: str):
    contract = db.get_contract(contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    return render("contract.html", contract=contract)


@app.post("/api/upload")
async def upload_contract(file: UploadFile = File(...)):
    allowed = {".pdf", ".docx", ".doc", ".txt"}
    suffix = Path(file.filename).suffix.lower()
    if suffix not in allowed:
        raise HTTPException(status_code=400, detail=f"File type {suffix} not supported.")

    contract_id = str(uuid.uuid4())
    file_path = UPLOADS_DIR / f"{contract_id}{suffix}"
    content = await file.read()
    file_path.write_bytes(content)

    try:
        extracted = doc_processor.process(file_path)
    except Exception as e:
        file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=f"Could not extract text: {str(e)}")

    db.create_contract({
        "id": contract_id,
        "filename": file.filename,
        "file_path": str(file_path),
        "status": "uploaded",
        "upload_date": datetime.now().isoformat(),
        "word_count": extracted["word_count"],
        "page_count": extracted["page_count"],
        "raw_text": extracted["text"],
        "doc_type": "Contract",
    })

    return JSONResponse({
        "contract_id": contract_id,
        "filename": file.filename,
        "word_count": extracted["word_count"],
        "page_count": extracted["page_count"],
        "status": "uploaded"
    })


@app.post("/api/analyse/{contract_id}")
async def analyse_contract(contract_id: str):
    contract = db.get_contract(contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    raw_text = contract["raw_text"]
    if not raw_text:
        raise HTTPException(status_code=422, detail="No text content found")

    if not llm_client.health_check():
        raise HTTPException(
            status_code=503,
            detail="LM Studio is not reachable at localhost:1234. Please ensure LM Studio is running with a model loaded."
        )

    try:
        db.update_contract(contract_id, {"status": "processing"})
        results = analysis_engine.run_full_analysis(raw_text, contract["filename"])

        report_filename = f"report_{contract_id}.pdf"
        report_path = REPORTS_DIR / report_filename
        report_generator.generate(contract, results, report_path)

        db.update_contract(contract_id, {
            "status": "complete",
            "analysis_date": datetime.now().isoformat(),
            "analysis_json": json.dumps(results),
            "report_path": report_filename,
            "risk_score": results.get("risk_score", {}).get("overall_score", 0),
            "risk_level": results.get("risk_score", {}).get("level", "Unknown"),
            "doc_type": results.get("doc_type", "Contract"),
            "executive_summary": results.get("executive_summary", ""),
        })

        return JSONResponse({
            "status": "complete",
            "risk_score": results.get("risk_score", {}),
            "report_url": f"/reports/{report_filename}",
            "analysis": results
        })

    except Exception as e:
        db.update_contract(contract_id, {"status": "error", "error_message": str(e)})
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")


@app.get("/api/contracts")
async def list_contracts():
    return JSONResponse(db.get_all_contracts())


@app.delete("/api/contract/{contract_id}")
async def delete_contract(contract_id: str):
    contract = db.get_contract(contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="Not found")
    Path(contract["file_path"]).unlink(missing_ok=True)
    if contract.get("report_path"):
        (REPORTS_DIR / contract["report_path"]).unlink(missing_ok=True)
    db.delete_contract(contract_id)
    return JSONResponse({"deleted": True})


@app.get("/api/llm-status")
async def llm_status():
    reachable = llm_client.health_check()
    models = llm_client.list_models() if reachable else []
    return JSONResponse({"reachable": reachable, "models": models})


if __name__ == "__main__":
    print("\n🏛  ContractIQ starting on http://localhost:8000\n")
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
