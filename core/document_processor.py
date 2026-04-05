"""
Document processor for ContractIQ.
Handles PDF and DOCX extraction, preserving structure.
"""

import re
from pathlib import Path
from typing import Dict, Any


class DocumentProcessor:

    def process(self, file_path: Path) -> Dict[str, Any]:
        """Extract text and metadata from a document file."""
        suffix = file_path.suffix.lower()
        if suffix == ".pdf":
            return self._process_pdf(file_path)
        elif suffix in (".docx", ".doc"):
            return self._process_docx(file_path)
        elif suffix == ".txt":
            return self._process_txt(file_path)
        else:
            raise ValueError(f"Unsupported file type: {suffix}")

    def _process_pdf(self, file_path: Path) -> Dict[str, Any]:
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(str(file_path))
            pages = []
            for page in doc:
                pages.append(page.get_text("text"))
            doc.close()
            full_text = "\n\n".join(pages)
            full_text = self._clean_text(full_text)
            return {
                "text": full_text,
                "word_count": len(full_text.split()),
                "page_count": len(pages),
                "doc_type": "PDF",
            }
        except ImportError:
            raise ImportError("PyMuPDF not installed. Run: pip install pymupdf")

    def _process_docx(self, file_path: Path) -> Dict[str, Any]:
        try:
            from docx import Document
            doc = Document(str(file_path))
            paragraphs = []
            for para in doc.paragraphs:
                text = para.text.strip()
                if text:
                    paragraphs.append(text)
            # Also extract tables
            for table in doc.tables:
                for row in table.rows:
                    row_text = " | ".join(
                        cell.text.strip() for cell in row.cells if cell.text.strip()
                    )
                    if row_text:
                        paragraphs.append(row_text)
            full_text = "\n\n".join(paragraphs)
            full_text = self._clean_text(full_text)
            # Estimate pages (roughly 500 words/page)
            word_count = len(full_text.split())
            return {
                "text": full_text,
                "word_count": word_count,
                "page_count": max(1, word_count // 500),
                "doc_type": "DOCX",
            }
        except ImportError:
            raise ImportError("python-docx not installed. Run: pip install python-docx")

    def _process_txt(self, file_path: Path) -> Dict[str, Any]:
        text = file_path.read_text(encoding="utf-8", errors="replace")
        text = self._clean_text(text)
        word_count = len(text.split())
        return {
            "text": text,
            "word_count": word_count,
            "page_count": max(1, word_count // 500),
            "doc_type": "TXT",
        }

    def _clean_text(self, text: str) -> str:
        """Clean extracted text: normalize whitespace, remove junk."""
        # Normalize line endings
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        # Remove excessive blank lines (keep max 2)
        text = re.sub(r"\n{3,}", "\n\n", text)
        # Remove non-printable characters except newlines and tabs
        text = re.sub(r"[^\x09\x0A\x20-\x7E\u00A0-\uFFFF]", "", text)
        # Collapse excessive spaces within lines
        lines = []
        for line in text.split("\n"):
            line = re.sub(r" {3,}", "  ", line)
            lines.append(line)
        return "\n".join(lines).strip()

    def chunk_text(self, text: str, max_chars: int = 6000, overlap: int = 500):
        """Split text into overlapping chunks for LLM processing."""
        if len(text) <= max_chars:
            return [text]
        
        chunks = []
        start = 0
        while start < len(text):
            end = start + max_chars
            if end < len(text):
                # Try to break at a sentence boundary
                boundary = text.rfind(". ", start, end)
                if boundary > start + max_chars // 2:
                    end = boundary + 1
            chunks.append(text[start:end])
            start = end - overlap
        return chunks
