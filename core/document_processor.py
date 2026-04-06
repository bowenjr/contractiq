"""
Document processor for ContractIQ.
Handles PDF and DOCX extraction, preserving structure.
"""

import re
from pathlib import Path
from typing import Dict, Any, List


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
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[^\x09\x0A\x20-\x7E\u00A0-\uFFFF]", "", text)
        lines = []
        for line in text.split("\n"):
            line = re.sub(r" {3,}", "  ", line)
            lines.append(line)
        return "\n".join(lines).strip()

    def split_by_sections(self, text: str) -> List[Dict[str, str]]:
        """
        Split a legal document into sections by detecting heading patterns.

        Recognises:
          Article 1 / ARTICLE 1
          Section 1 / SECTION 1
          1.  Heading  /  1.1  Heading
          Schedule A / SCHEDULE A
          Appendix A / APPENDIX A  /  Exhibit A / EXHIBIT A

        Returns [{"heading": str, "content": str}].
        Falls back to chunk_text(max_chars=12000, overlap=800) when fewer
        than 3 headings are found.
        """
        heading_re = re.compile(
            r"^(?:"
            r"(?:ARTICLE|Article)\s+\d+"
            r"|(?:SECTION|Section)\s+\d+"
            r"|\d+\.\d+\s+[A-Z][A-Za-z]"   # 1.1 Definitions
            r"|\d+\.\s+[A-Z][A-Za-z]"       # 1. Payment Terms
            r"|(?:SCHEDULE|Schedule)\s+[A-Z0-9]"
            r"|(?:APPENDIX|Appendix)\s+[A-Z0-9]"
            r"|(?:EXHIBIT|Exhibit)\s+[A-Z0-9]"
            r")",
        )

        lines = text.split("\n")
        heading_positions: List[tuple] = []
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped and len(stripped) < 120 and heading_re.match(stripped):
                heading_positions.append((i, stripped))

        if len(heading_positions) < 3:
            # Not enough structure — fall back to character chunking
            chunks = self.chunk_text(text, max_chars=12000, overlap=800)
            return [{"heading": f"Chunk {i + 1}", "content": c}
                    for i, c in enumerate(chunks)]

        sections: List[Dict[str, str]] = []

        # Preamble: everything before the first heading
        if heading_positions[0][0] > 0:
            preamble = "\n".join(lines[: heading_positions[0][0]]).strip()
            if preamble:
                sections.append({"heading": "Preamble", "content": preamble})

        for idx, (line_num, heading) in enumerate(heading_positions):
            next_line = (
                heading_positions[idx + 1][0]
                if idx + 1 < len(heading_positions)
                else len(lines)
            )
            content = "\n".join(lines[line_num:next_line]).strip()
            if content:
                sections.append({"heading": heading, "content": content})

        return sections

    def chunk_text(self, text: str, max_chars: int = 6000, overlap: int = 500):
        """Split text into overlapping chunks for LLM processing."""
        if len(text) <= max_chars:
            return [text]

        chunks = []
        start = 0
        while start < len(text):
            end = start + max_chars
            if end < len(text):
                boundary = text.rfind(". ", start, end)
                if boundary > start + max_chars // 2:
                    end = boundary + 1
            chunks.append(text[start:end])
            start = end - overlap
        return chunks
