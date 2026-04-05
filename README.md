# ContractIQ
### Personal Bids & Contracts Analysis System — Phase 1

A local-first contract analysis tool powered by your LM Studio model.
Upload PDF or DOCX contracts → get full risk reports in minutes.

---

## Features (Phase 1)

- **Upload** PDF and DOCX contracts and bids
- **Automatic text extraction** preserving document structure
- **Full LLM analysis pipeline** via LM Studio:
  - Document classification & executive summary
  - Party & obligation mapping
  - Key clause extraction (payment, termination, liability, IP, etc.)
  - Risk scoring with red flags (0–100 score + Low/Medium/High/Critical)
  - Critical dates & deadline extraction
  - Bid/tender specific analysis (auto-detected)
  - Actionable recommendations with priority
- **Professional PDF report** generated for every contract
- **Web dashboard** — contract library, risk overview, status tracking
- **100% local** — no data leaves your machine

---

## Setup

### 1. Prerequisites

- Python 3.10 or higher
- LM Studio installed and running with a model loaded
  - Download from: https://lmstudio.ai
  - Recommended models: Mistral 7B, Llama 3 8B, Qwen 2.5 7B, or similar
  - Enable the local server in LM Studio (port 1234)

### 2. Install

```bash
# Clone or copy the contractiq folder, then:
cd contractiq

# Create a virtual environment (recommended)
python -m venv venv
source venv/bin/activate      # macOS/Linux
venv\Scripts\activate         # Windows

# Install dependencies
pip install -r requirements.txt
```

### 3. Run

```bash
python app.py
```

Open your browser at: **http://localhost:8000**

---

## Usage

1. **Start LM Studio** — load any instruct/chat model and start the local server
2. **Open ContractIQ** at http://localhost:8000
3. **Upload a contract** — drag & drop PDF or DOCX onto the upload zone
4. Analysis begins automatically
5. **View results** on the contract detail page
6. **Download PDF report** from the report button

---

## Tips for Best Results

- **Model choice matters**: A 7B+ instruct model (Mistral, Llama 3, Qwen 2.5) works well
- **Larger context models** handle longer contracts better
- **Documents over ~14,000 characters** are truncated for analysis — the tool uses the first portion
- **Processing time**: Typically 1–3 minutes per contract depending on your hardware

---

## Project Structure

```
contractiq/
├── app.py                    # FastAPI application & routes
├── requirements.txt          # Python dependencies
├── core/
│   ├── database.py           # SQLite storage layer
│   ├── document_processor.py # PDF/DOCX text extraction
│   ├── llm_client.py         # LM Studio API client
│   ├── analysis_engine.py    # All LLM analysis tasks
│   └── report_generator.py   # PDF report generation
├── templates/
│   ├── index.html            # Dashboard
│   └── contract.html         # Contract detail view
├── static/                   # CSS/JS assets
├── uploads/                  # Uploaded documents (auto-created)
├── reports/                  # Generated PDF reports (auto-created)
└── data/
    └── contractiq.db         # SQLite database (auto-created)
```

---

## Roadmap

| Phase | Features |
|-------|----------|
| ✅ Phase 1 | Upload → full analysis → PDF report → dashboard |
| 🔜 Phase 2 | RAG Q&A — ask questions about any contract |
| 🔜 Phase 3 | Multi-bid comparison & scoring |
| 🔜 Phase 4 | Deadline calendar & contract status workflow |
| 🔜 Phase 5 | Clause library & custom analysis templates |

---

*ContractIQ — For personal use only. Not legal advice.*
