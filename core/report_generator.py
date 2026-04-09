"""
ContractIQ — PDF Report Generator
Produces professional A4 reports using ReportLab.
Supports both the 7-pillar analysis format and legacy clause-based format.
NEVER uses .hexval() — always formats colors as #{r:02x}{g:02x}{b:02x}.
"""

import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List


def _safe(val, default="Not specified") -> str:
    if val is None or val == "" or str(val).lower() == "null":
        return default
    return str(val)


def _hex(color) -> str:
    """Convert a ReportLab color to a plain #RRGGBB string."""
    return (
        f"#{int(color.red*255):02x}"
        f"{int(color.green*255):02x}"
        f"{int(color.blue*255):02x}"
    )


PILLAR_COLOURS_HEX = {
    "money":          "#16a34a",
    "time":           "#d97706",
    "scope":          "#1d4ed8",
    "risk_liability": "#dc2626",
    "relationships":  "#7c3aed",
    "administration": "#0891b2",
    "exit":           "#6b7280",
}

STATUS_COLOURS_HEX = {
    "Good":            "#16a34a",
    "Issues Found":    "#d97706",
    "Critical Issues": "#dc2626",
    "Not Applicable":  "#6b7280",
}


class ReportGenerator:
    def __init__(self, reports_dir: Path):
        self.reports_dir = reports_dir
        self.reports_dir.mkdir(exist_ok=True)

    def generate(self, contract: Dict, analysis: Dict, output_path: Path) -> Path:
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.lib.units import cm
            from reportlab.lib import colors
            from reportlab.platypus import (
                SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
                HRFlowable, PageBreak, KeepTogether
            )
            from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
        except ImportError:
            raise ImportError("ReportLab not installed. Run: pip install reportlab")

        # ── Colours ───────────────────────────────────────────────────────────
        DARK     = colors.HexColor("#1a1f2e")
        ACCENT   = colors.HexColor("#c8a96e")
        LIGHT_BG = colors.HexColor("#f4f1eb")
        MID_GREY = colors.HexColor("#6b7280")
        RED      = colors.HexColor("#dc2626")
        AMBER    = colors.HexColor("#d97706")
        GREEN    = colors.HexColor("#16a34a")
        BLUE     = colors.HexColor("#1d4ed8")
        TBL_HEAD = colors.HexColor("#1a1f2e")
        TBL_ALT  = colors.HexColor("#f8f6f0")
        WHITE    = colors.white

        rp = analysis.get("review_priority", {})
        priority = rp.get("review_priority", "Unknown")
        risk_colour = {
            "Low": GREEN, "Medium": AMBER, "High": RED, "Critical": RED
        }.get(priority, MID_GREY)

        # ── Styles ────────────────────────────────────────────────────────────
        styles = getSampleStyleSheet()

        def ps(name, **kw):
            return ParagraphStyle(name, parent=styles["Normal"], **kw)

        title_st  = ps("T", fontName="Helvetica-Bold", fontSize=22,
                        textColor=WHITE, spaceAfter=4, leading=26)
        sub_st    = ps("S", fontName="Helvetica", fontSize=11,
                        textColor=ACCENT, spaceAfter=2)
        h1        = ps("H1", fontName="Helvetica-Bold", fontSize=14,
                        textColor=DARK, spaceBefore=16, spaceAfter=8)
        h2        = ps("H2", fontName="Helvetica-Bold", fontSize=11,
                        textColor=DARK, spaceBefore=10, spaceAfter=4)
        body      = ps("B", fontName="Helvetica", fontSize=9.5,
                        textColor=DARK, leading=14, spaceAfter=4)
        small     = ps("Sm", fontName="Helvetica", fontSize=8.5,
                        textColor=MID_GREY, leading=12)
        bullet_st = ps("Bul", fontName="Helvetica", fontSize=9.5,
                        textColor=DARK, leading=13, leftIndent=12,
                        spaceAfter=3)

        doc = SimpleDocTemplate(
            str(output_path),
            pagesize=A4,
            leftMargin=2*cm, rightMargin=2*cm,
            topMargin=2*cm, bottomMargin=2*cm,
            title=f"ContractIQ — {contract['filename']}",
        )

        story = []
        W = A4[0] - 4*cm  # usable page width

        # ── 1. Header Banner ──────────────────────────────────────────────────
        doc_type = _safe(analysis.get("doc_type"), "Contract")
        header_data = [[
            Paragraph("ContractIQ", title_st),
            Paragraph(
                f"<b>{doc_type}</b><br/>"
                f'<font color="{_hex(risk_colour)}">{priority} Priority</font>',
                ps("HDR2", fontName="Helvetica", fontSize=11,
                   textColor=WHITE, alignment=TA_RIGHT)
            ),
        ]]
        header_tbl = Table(header_data, colWidths=[W*0.65, W*0.35])
        header_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,-1), DARK),
            ("TOPPADDING",    (0,0), (-1,-1), 14),
            ("BOTTOMPADDING", (0,0), (-1,-1), 14),
            ("LEFTPADDING",   (0,0), (0,-1),  16),
            ("RIGHTPADDING",  (-1,0), (-1,-1), 16),
            ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ]))
        story.append(header_tbl)
        story.append(Spacer(1, 0.3*cm))

        # ── 2. Document Metadata ──────────────────────────────────────────────
        meta_items = [
            ("Document",      _safe(contract.get("filename"))),
            ("Type",          doc_type),
            ("Generated",     datetime.now().strftime("%d %B %Y, %H:%M")),
            ("Pages",         _safe(contract.get("page_count"))),
            ("Words",         f"{contract.get('word_count', 0):,}"),
            ("Value",         _safe(analysis.get("contract_value"))),
            ("Duration",      _safe(analysis.get("contract_duration"))),
            ("Governing Law", _safe(analysis.get("governing_law"))),
        ]
        meta_rows = [[
            Paragraph(k, ps("MK", fontName="Helvetica-Bold",
                             fontSize=8.5, textColor=MID_GREY)),
            Paragraph(v, ps("MV", fontName="Helvetica",
                             fontSize=9, textColor=DARK)),
        ] for k, v in meta_items]
        paired = []
        for i in range(0, len(meta_rows), 2):
            row = meta_rows[i]
            extra = meta_rows[i+1] if i+1 < len(meta_rows) else \
                [Paragraph("", body), Paragraph("", body)]
            paired.append(row + extra)
        meta_tbl = Table(paired,
                         colWidths=[W*0.12, W*0.38, W*0.12, W*0.38])
        meta_tbl.setStyle(TableStyle([
            ("BACKGROUND",   (0,0), (-1,-1), TBL_ALT),
            ("TOPPADDING",   (0,0), (-1,-1), 5),
            ("BOTTOMPADDING",(0,0), (-1,-1), 5),
            ("LEFTPADDING",  (0,0), (-1,-1), 8),
            ("GRID",         (0,0), (-1,-1), 0.3,
             colors.HexColor("#e5e0d5")),
            ("VALIGN",       (0,0), (-1,-1), "TOP"),
        ]))
        story.append(meta_tbl)
        story.append(Spacer(1, 0.4*cm))

        # ── 3. Executive Summary ──────────────────────────────────────────────
        story.append(Paragraph("Executive Summary", h1))
        story.append(HRFlowable(width=W, thickness=1.5,
                                color=ACCENT, spaceAfter=8))
        story.append(Paragraph(
            _safe(analysis.get("executive_summary"), "No summary available."),
            body))
        if analysis.get("key_subject"):
            story.append(Paragraph(
                f"<b>Subject:</b> {analysis['key_subject']}", body))
        story.append(Spacer(1, 0.4*cm))

        # ── 4. Review Priority Dashboard ─────────────────────────────────────
        story.append(Paragraph("Review Priority", h1))
        story.append(HRFlowable(width=W, thickness=1.5,
                                color=ACCENT, spaceAfter=8))

        # Pillar mini-scorecards (only for new format)
        pillar_results = analysis.get("pillars", [])
        if pillar_results:
            pillar_cards = []
            for pr in pillar_results:
                pid = pr.get("pillar_id", "")
                pname = pr.get("pillar_name", pid)
                pscore = pr.get("score", 50)
                pstatus = pr.get("status", "Issues Found")
                pcolour = colors.HexColor(
                    PILLAR_COLOURS_HEX.get(pid, "#6b7280"))
                scolour = colors.HexColor(
                    STATUS_COLOURS_HEX.get(pstatus, "#6b7280"))
                card = Table([[
                    Paragraph(
                        f'<font color="{_hex(pcolour)}"><b>{pname}</b></font><br/>'
                        f'<font color="{_hex(scolour)}">{pstatus}</font><br/>'
                        f"Score: {pscore}/100",
                        ps(f"PC_{pid}", fontName="Helvetica",
                           fontSize=7.5, leading=11, textColor=DARK)
                    )
                ]], colWidths=[(W/7) - 4],
                style=TableStyle([
                    ("BACKGROUND",    (0,0), (-1,-1), TBL_ALT),
                    ("LEFTBORDERCOLOR",(0,0), (0,-1),  pcolour),
                    ("BOX",           (0,0), (-1,-1), 1.0, pcolour),
                    ("TOPPADDING",    (0,0), (-1,-1), 6),
                    ("BOTTOMPADDING", (0,0), (-1,-1), 6),
                    ("LEFTPADDING",   (0,0), (-1,-1), 5),
                ]))
                pillar_cards.append(card)

            pillar_row_tbl = Table(
                [pillar_cards],
                colWidths=[(W/7)] * 7,
                hAlign="LEFT",
            )
            story.append(pillar_row_tbl)
            story.append(Spacer(1, 0.3*cm))

        # Overall priority row
        risk_cols = [
            ("Review Priority",       priority,                                   risk_colour),
            ("Critical Findings",     str(rp.get("critical_flag_count", "—")),    RED),
            ("High Findings",         str(rp.get("high_flag_count",    "—")),     AMBER),
            ("Negotiation Points",    str(rp.get("negotiation_points_count", "—")), GREEN),
        ]
        risk_grid_data = [[
            Table([[
                Paragraph(label, ps("RL", fontName="Helvetica-Bold",
                                    fontSize=8, textColor=MID_GREY,
                                    alignment=TA_CENTER)),
                Paragraph(value, ps("RV", fontName="Helvetica-Bold",
                                    fontSize=13, textColor=col,
                                    alignment=TA_CENTER)),
            ]], colWidths=[(W/4) - 6],
            style=TableStyle([
                ("BACKGROUND",    (0,0), (-1,-1), TBL_ALT),
                ("BOX",           (0,0), (-1,-1), 1.5, col),
                ("TOPPADDING",    (0,0), (-1,-1), 8),
                ("BOTTOMPADDING", (0,0), (-1,-1), 8),
                ("ALIGN",         (0,0), (-1,-1), "CENTER"),
            ]))
            for label, value, col in risk_cols
        ]]
        story.append(Table(risk_grid_data,
                           colWidths=[(W/4)] * 4,
                           hAlign="LEFT"))

        if rp.get("priority_rationale"):
            story.append(Spacer(1, 0.3*cm))
            story.append(Paragraph(rp["priority_rationale"], body))

        story.append(Spacer(1, 0.4*cm))

        # ── 5. 7-Pillar Summary Grid (new format) ─────────────────────────────
        if pillar_results:
            story.append(Paragraph("7-Pillar Analysis Summary", h1))
            story.append(HRFlowable(width=W, thickness=1.5,
                                    color=ACCENT, spaceAfter=8))

            # 2-column grid of pillar summary cells
            grid_rows = []
            for i in range(0, len(pillar_results), 2):
                row_cells = []
                for pr in pillar_results[i:i+2]:
                    pid = pr.get("pillar_id", "")
                    pname = pr.get("pillar_name", "")
                    pstatus = pr.get("status", "Issues Found")
                    pscore = pr.get("score", 50)
                    psummary = pr.get("summary", "")[:180]
                    top_finding = ""
                    if pr.get("findings"):
                        top_finding = pr["findings"][0].get("finding", "")[:100]
                    pcolour = colors.HexColor(
                        PILLAR_COLOURS_HEX.get(pid, "#6b7280"))
                    scolour = colors.HexColor(
                        STATUS_COLOURS_HEX.get(pstatus, "#6b7280"))

                    cell = Table([[
                        Paragraph(
                            f'<b>{pname}</b>  '
                            f'<font color="{_hex(scolour)}">{pstatus}</font>'
                            f'  Score: {pscore}/100<br/>'
                            f'{psummary}<br/>'
                            + (f'<i>{top_finding}</i>' if top_finding else ''),
                            ps(f"GC_{pid}", fontName="Helvetica",
                               fontSize=8.5, leading=12, textColor=DARK)
                        )
                    ]], colWidths=[(W/2) - 8],
                    style=TableStyle([
                        ("BACKGROUND",    (0,0), (-1,-1), TBL_ALT),
                        ("BOX",           (0,0), (-1,-1), 2.0, pcolour),
                        ("TOPPADDING",    (0,0), (-1,-1), 8),
                        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
                        ("LEFTPADDING",   (0,0), (-1,-1), 10),
                    ]))
                    row_cells.append(cell)

                if len(row_cells) == 1:
                    row_cells.append(Spacer(1, 1))

                grid_rows.append(row_cells)

            grid_tbl = Table(
                grid_rows,
                colWidths=[(W/2)] * 2,
                hAlign="LEFT",
                rowHeights=None,
            )
            grid_tbl.setStyle(TableStyle([
                ("TOPPADDING",    (0,0), (-1,-1), 4),
                ("BOTTOMPADDING", (0,0), (-1,-1), 4),
                ("LEFTPADDING",   (0,0), (-1,-1), 0),
                ("RIGHTPADDING",  (0,0), (-1,-1), 4),
            ]))
            story.append(grid_tbl)
            story.append(Spacer(1, 0.4*cm))

            # ── 6. Detailed Pillar Sections ───────────────────────────────────
            story.append(PageBreak())
            story.append(Paragraph("Detailed Pillar Analysis", h1))
            story.append(HRFlowable(width=W, thickness=1.5,
                                    color=ACCENT, spaceAfter=8))

            for pr in pillar_results:
                pid = pr.get("pillar_id", "")
                pname = pr.get("pillar_name", "")
                pstatus = pr.get("status", "Issues Found")
                pcolour = colors.HexColor(
                    PILLAR_COLOURS_HEX.get(pid, "#6b7280"))
                scolour = colors.HexColor(
                    STATUS_COLOURS_HEX.get(pstatus, "#6b7280"))

                # Pillar section header
                p_hdr = Table([[
                    Paragraph(
                        f'<b>{pname}</b>  '
                        f'<font color="{_hex(scolour)}">{pstatus}</font>  '
                        f'Score: {pr.get("score",50)}/100',
                        ps(f"PH_{pid}", fontName="Helvetica-Bold",
                           fontSize=11, textColor=WHITE)
                    )
                ]], colWidths=[W])
                p_hdr.setStyle(TableStyle([
                    ("BACKGROUND",    (0,0), (-1,-1), pcolour),
                    ("TOPPADDING",    (0,0), (-1,-1), 8),
                    ("BOTTOMPADDING", (0,0), (-1,-1), 8),
                    ("LEFTPADDING",   (0,0), (-1,-1), 12),
                ]))
                story.append(KeepTogether([p_hdr]))
                story.append(Spacer(1, 0.15*cm))

                if pr.get("summary"):
                    story.append(Paragraph(pr["summary"], body))

                # Findings table
                findings = pr.get("findings", [])
                if findings:
                    f_rows = [["Finding", "Severity", "Clause", "Action"]]
                    for f in findings[:20]:
                        sev = _safe(f.get("severity"), "")
                        sev_col = {
                            "Critical": RED, "High": RED,
                            "Medium": AMBER, "Low": GREEN,
                        }.get(sev, MID_GREY)
                        f_rows.append([
                            Paragraph(
                                f'<b>{_safe(f.get("finding"))}</b><br/>'
                                f'{_safe(f.get("detail"),"")[:200]}',
                                small),
                            Paragraph(
                                f'<font color="{_hex(sev_col)}"><b>{sev}</b></font>',
                                ps("FS", fontSize=8.5, alignment=TA_CENTER)),
                            Paragraph(_safe(f.get("clause_reference"), ""), small),
                            Paragraph(_safe(f.get("recommended_action"), ""), small),
                        ])
                    f_tbl = Table(f_rows,
                                  colWidths=[W*0.40, W*0.10, W*0.18, W*0.32])
                    f_tbl.setStyle(TableStyle([
                        ("BACKGROUND",    (0,0), (-1,0), TBL_HEAD),
                        ("TEXTCOLOR",     (0,0), (-1,0), WHITE),
                        ("FONTNAME",      (0,0), (-1,0), "Helvetica-Bold"),
                        ("FONTSIZE",      (0,0), (-1,0), 8),
                        ("ROWBACKGROUNDS",(0,1), (-1,-1), [WHITE, TBL_ALT]),
                        ("GRID",          (0,0), (-1,-1), 0.3,
                         colors.HexColor("#e0dbd0")),
                        ("TOPPADDING",    (0,0), (-1,-1), 4),
                        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
                        ("LEFTPADDING",   (0,0), (-1,-1), 5),
                        ("VALIGN",        (0,0), (-1,-1), "TOP"),
                    ]))
                    story.append(f_tbl)
                    story.append(Spacer(1, 0.2*cm))

                # Source excerpts (red flags)
                red_flags = pr.get("red_flags", [])
                if red_flags:
                    story.append(Paragraph("<b>Red Flags:</b>", body))
                    rf_rows = [["Flag", "Severity", "Description"]]
                    for rf in red_flags[:8]:
                        sev = _safe(rf.get("severity"), "")
                        sev_col = {
                            "Critical": RED, "High": RED,
                            "Medium": AMBER, "Low": GREEN,
                        }.get(sev, MID_GREY)
                        rf_rows.append([
                            Paragraph(_safe(rf.get("flag")), small),
                            Paragraph(
                                f'<font color="{_hex(sev_col)}"><b>{sev}</b></font>',
                                ps("RFS", fontSize=8.5, alignment=TA_CENTER)),
                            Paragraph(_safe(rf.get("description")), small),
                        ])
                    rf_tbl = Table(rf_rows,
                                   colWidths=[W*0.25, W*0.12, W*0.63])
                    rf_tbl.setStyle(TableStyle([
                        ("BACKGROUND",    (0,0), (-1,0), TBL_HEAD),
                        ("TEXTCOLOR",     (0,0), (-1,0), WHITE),
                        ("FONTNAME",      (0,0), (-1,0), "Helvetica-Bold"),
                        ("FONTSIZE",      (0,0), (-1,0), 8),
                        ("ROWBACKGROUNDS",(0,1), (-1,-1), [WHITE, TBL_ALT]),
                        ("GRID",          (0,0), (-1,-1), 0.3,
                         colors.HexColor("#e0dbd0")),
                        ("TOPPADDING",    (0,0), (-1,-1), 4),
                        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
                        ("LEFTPADDING",   (0,0), (-1,-1), 5),
                        ("VALIGN",        (0,0), (-1,-1), "TOP"),
                    ]))
                    story.append(rf_tbl)
                    story.append(Spacer(1, 0.2*cm))

                # Questions answered
                qa = pr.get("questions_answered", {})
                if qa:
                    story.append(Paragraph("<b>Key Questions:</b>", body))
                    qa_rows = [["Question", "Answer"]]
                    for q, a in list(qa.items())[:10]:
                        qa_rows.append([
                            Paragraph(q, small),
                            Paragraph(_safe(a), small),
                        ])
                    qa_tbl = Table(qa_rows, colWidths=[W*0.42, W*0.58])
                    qa_tbl.setStyle(TableStyle([
                        ("BACKGROUND",    (0,0), (-1,0), TBL_HEAD),
                        ("TEXTCOLOR",     (0,0), (-1,0), WHITE),
                        ("FONTNAME",      (0,0), (-1,0), "Helvetica-Bold"),
                        ("FONTSIZE",      (0,0), (-1,0), 8),
                        ("ROWBACKGROUNDS",(0,1), (-1,-1), [WHITE, TBL_ALT]),
                        ("GRID",          (0,0), (-1,-1), 0.3,
                         colors.HexColor("#e0dbd0")),
                        ("TOPPADDING",    (0,0), (-1,-1), 4),
                        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
                        ("LEFTPADDING",   (0,0), (-1,-1), 5),
                        ("VALIGN",        (0,0), (-1,-1), "TOP"),
                    ]))
                    story.append(qa_tbl)
                    story.append(Spacer(1, 0.2*cm))

                # Missing protections
                missing = pr.get("missing_protections", [])
                if missing:
                    story.append(Paragraph("<b>Missing Protections:</b>", body))
                    for m in missing[:6]:
                        story.append(Paragraph(f"⚠  {m}", bullet_st))

                story.append(Spacer(1, 0.4*cm))

        # ── Legacy: key clauses (old format) ──────────────────────────────────
        else:
            clauses = analysis.get("clauses", {})
            if clauses and not clauses.get("error"):
                story.append(Paragraph("Key Clauses Analysis", h1))
                story.append(HRFlowable(width=W, thickness=1.5,
                                        color=ACCENT, spaceAfter=8))
                clause_order = [
                    ("payment_terms", "Payment Terms"),
                    ("termination", "Termination"),
                    ("liability", "Liability"),
                    ("indemnity", "Indemnity"),
                    ("warranties", "Warranties"),
                    ("intellectual_property", "Intellectual Property"),
                    ("confidentiality", "Confidentiality"),
                    ("dispute_resolution", "Dispute Resolution"),
                    ("force_majeure", "Force Majeure"),
                    ("auto_renewal", "Auto-Renewal"),
                ]
                cl_rows = [["Clause", "Status", "Summary"]]
                for key, label in clause_order:
                    cl = clauses.get(key, {})
                    if not isinstance(cl, dict):
                        continue
                    found = cl.get("found", False)
                    status_txt = "Present" if found else "Absent"
                    status_col = GREEN if found else RED
                    parts = [cl.get("summary", "—")]
                    if cl.get("cap"):
                        parts.append(f"Cap: {cl['cap']}")
                    if cl.get("notice_period"):
                        parts.append(f"Notice: {cl['notice_period']}")
                    cl_rows.append([
                        Paragraph(f"<b>{label}</b>", small),
                        Paragraph(
                            f'<font color="{_hex(status_col)}">{status_txt}</font>',
                            ps("CS", fontSize=8.5, alignment=TA_CENTER)),
                        Paragraph(" | ".join(str(p) for p in parts if p), small),
                    ])
                cl_tbl = Table(cl_rows, colWidths=[W*0.22, W*0.13, W*0.65])
                cl_tbl.setStyle(TableStyle([
                    ("BACKGROUND",    (0,0), (-1,0), TBL_HEAD),
                    ("TEXTCOLOR",     (0,0), (-1,0), WHITE),
                    ("FONTNAME",      (0,0), (-1,0), "Helvetica-Bold"),
                    ("FONTSIZE",      (0,0), (-1,0), 8.5),
                    ("ROWBACKGROUNDS",(0,1), (-1,-1), [WHITE, TBL_ALT]),
                    ("GRID",          (0,0), (-1,-1), 0.3,
                     colors.HexColor("#e0dbd0")),
                    ("TOPPADDING",    (0,0), (-1,-1), 5),
                    ("BOTTOMPADDING", (0,0), (-1,-1), 5),
                    ("LEFTPADDING",   (0,0), (-1,-1), 6),
                    ("VALIGN",        (0,0), (-1,-1), "TOP"),
                ]))
                story.append(cl_tbl)
                story.append(Spacer(1, 0.4*cm))

            # Legacy red flags from review_priority
            red_flags = rp.get("red_flags", [])
            if red_flags:
                story.append(Paragraph("Red Flags & Risk Factors", h2))
                flag_rows = [["Flag", "Severity", "Description"]]
                for f in red_flags[:12]:
                    sev = _safe(f.get("severity"), "")
                    sev_col = {
                        "Critical": RED, "High": RED,
                        "Medium": AMBER, "Low": GREEN,
                    }.get(sev, MID_GREY)
                    flag_rows.append([
                        Paragraph(_safe(f.get("flag")), small),
                        Paragraph(
                            f'<font color="{_hex(sev_col)}"><b>{sev}</b></font>',
                            ps("FlagSev", fontSize=8.5, alignment=TA_CENTER)),
                        Paragraph(_safe(f.get("description")), small),
                    ])
                flag_tbl = Table(flag_rows, colWidths=[W*0.25, W*0.12, W*0.63])
                flag_tbl.setStyle(TableStyle([
                    ("BACKGROUND",    (0,0), (-1,0), TBL_HEAD),
                    ("TEXTCOLOR",     (0,0), (-1,0), WHITE),
                    ("FONTNAME",      (0,0), (-1,0), "Helvetica-Bold"),
                    ("FONTSIZE",      (0,0), (-1,0), 8.5),
                    ("ROWBACKGROUNDS",(0,1), (-1,-1), [WHITE, TBL_ALT]),
                    ("GRID",          (0,0), (-1,-1), 0.3,
                     colors.HexColor("#e0dbd0")),
                    ("TOPPADDING",    (0,0), (-1,-1), 5),
                    ("BOTTOMPADDING", (0,0), (-1,-1), 5),
                    ("LEFTPADDING",   (0,0), (-1,-1), 6),
                    ("VALIGN",        (0,0), (-1,-1), "TOP"),
                ]))
                story.append(flag_tbl)
                story.append(Spacer(1, 0.4*cm))

        # ── 7. Parties ────────────────────────────────────────────────────────
        parties_data = analysis.get("parties", {})
        parties = parties_data.get("parties", []) \
            if isinstance(parties_data, dict) else []
        if parties:
            story.append(Paragraph("Parties & Relationships", h1))
            story.append(HRFlowable(width=W, thickness=1.5,
                                    color=ACCENT, spaceAfter=8))
            if parties_data.get("relationship_summary"):
                story.append(Paragraph(
                    parties_data["relationship_summary"], body))
                story.append(Spacer(1, 0.2*cm))
            for party in parties:
                story.append(Paragraph(
                    f"<b>{_safe(party.get('name'))}</b> — "
                    f"{_safe(party.get('role'))}", h2))
                if party.get("description"):
                    story.append(Paragraph(party["description"], body))
                for ob in party.get("key_obligations", [])[:5]:
                    story.append(Paragraph(f"• {ob}", bullet_st))
            story.append(Spacer(1, 0.4*cm))

        # ── 8. Critical Dates ─────────────────────────────────────────────────
        dates = analysis.get("dates", {})
        if dates and not dates.get("error"):
            story.append(Paragraph("Critical Dates & Deadlines", h1))
            story.append(HRFlowable(width=W, thickness=1.5,
                                    color=ACCENT, spaceAfter=8))
            date_rows = [["Date / Period", "Description", "Importance"]]
            if dates.get("effective_date") and \
               str(dates["effective_date"]).lower() not in ("null", "none", ""):
                date_rows.append([
                    Paragraph(f"<b>{dates['effective_date']}</b>", small),
                    Paragraph("Contract Effective Date", small),
                    Paragraph(f'<font color="{_hex(RED)}">High</font>',
                              ps("DI", fontSize=8.5)),
                ])
            if dates.get("expiry_date") and \
               str(dates["expiry_date"]).lower() not in ("null", "none", ""):
                date_rows.append([
                    Paragraph(f"<b>{dates['expiry_date']}</b>", small),
                    Paragraph("Contract Expiry Date", small),
                    Paragraph(f'<font color="{_hex(RED)}">High</font>',
                              ps("DI2", fontSize=8.5)),
                ])
            for d in dates.get("key_dates", [])[:10]:
                imp = _safe(d.get("importance"), "Medium")
                imp_col = {"High": RED, "Medium": AMBER, "Low": GREEN}.get(
                    imp, MID_GREY)
                date_rows.append([
                    Paragraph(_safe(d.get("date")), small),
                    Paragraph(_safe(d.get("description")), small),
                    Paragraph(
                        f'<font color="{_hex(imp_col)}">{imp}</font>',
                        ps("DI3", fontSize=8.5)),
                ])
            if len(date_rows) > 1:
                date_tbl = Table(date_rows,
                                 colWidths=[W*0.22, W*0.60, W*0.18])
                date_tbl.setStyle(TableStyle([
                    ("BACKGROUND",    (0,0), (-1,0), TBL_HEAD),
                    ("TEXTCOLOR",     (0,0), (-1,0), WHITE),
                    ("FONTNAME",      (0,0), (-1,0), "Helvetica-Bold"),
                    ("FONTSIZE",      (0,0), (-1,0), 8.5),
                    ("ROWBACKGROUNDS",(0,1), (-1,-1), [WHITE, TBL_ALT]),
                    ("GRID",          (0,0), (-1,-1), 0.3,
                     colors.HexColor("#e0dbd0")),
                    ("TOPPADDING",    (0,0), (-1,-1), 5),
                    ("BOTTOMPADDING", (0,0), (-1,-1), 5),
                    ("LEFTPADDING",   (0,0), (-1,-1), 6),
                    ("VALIGN",        (0,0), (-1,-1), "TOP"),
                ]))
                story.append(date_tbl)
            notice_periods = dates.get("notice_periods", [])
            if notice_periods:
                story.append(Spacer(1, 0.2*cm))
                story.append(Paragraph("<b>Notice Periods:</b>", body))
                for np in notice_periods[:6]:
                    story.append(Paragraph(
                        f"• {_safe(np.get('trigger'))}: "
                        f"{_safe(np.get('period'))}", bullet_st))
            story.append(Spacer(1, 0.4*cm))

        # ── 9. Negotiation Issues ─────────────────────────────────────────────
        if pillar_results:
            all_neg = []
            for pr in pillar_results:
                for np in pr.get("negotiation_points", []):
                    all_neg.append((pr.get("pillar_name", ""), np))

            if all_neg:
                story.append(PageBreak())
                story.append(Paragraph("Negotiation Issue List", h1))
                story.append(HRFlowable(width=W, thickness=1.5,
                                        color=ACCENT, spaceAfter=8))
                neg_rows = [["#", "Pillar", "Issue", "Primary Ask",
                              "Fallback", "Priority"]]
                for i, (pname, np) in enumerate(all_neg[:30], 1):
                    pri = _safe(np.get("priority"), "Medium")
                    pri_col = {
                        "High": RED, "Medium": AMBER, "Low": GREEN
                    }.get(pri, MID_GREY)
                    neg_rows.append([
                        Paragraph(str(i), small),
                        Paragraph(pname, small),
                        Paragraph(_safe(np.get("issue")), small),
                        Paragraph(_safe(np.get("primary_ask")), small),
                        Paragraph(_safe(np.get("fallback")), small),
                        Paragraph(
                            f'<font color="{_hex(pri_col)}">{pri}</font>',
                            ps("NP", fontSize=8.5, alignment=TA_CENTER)),
                    ])
                neg_tbl = Table(neg_rows,
                                colWidths=[W*0.04, W*0.12, W*0.24,
                                           W*0.24, W*0.24, W*0.12])
                neg_tbl.setStyle(TableStyle([
                    ("BACKGROUND",    (0,0), (-1,0), TBL_HEAD),
                    ("TEXTCOLOR",     (0,0), (-1,0), WHITE),
                    ("FONTNAME",      (0,0), (-1,0), "Helvetica-Bold"),
                    ("FONTSIZE",      (0,0), (-1,0), 8),
                    ("ROWBACKGROUNDS",(0,1), (-1,-1), [WHITE, TBL_ALT]),
                    ("GRID",          (0,0), (-1,-1), 0.3,
                     colors.HexColor("#e0dbd0")),
                    ("TOPPADDING",    (0,0), (-1,-1), 5),
                    ("BOTTOMPADDING", (0,0), (-1,-1), 5),
                    ("LEFTPADDING",   (0,0), (-1,-1), 5),
                    ("VALIGN",        (0,0), (-1,-1), "TOP"),
                ]))
                story.append(neg_tbl)
                story.append(Spacer(1, 0.4*cm))

        # ── 10. Obligations Summary ───────────────────────────────────────────
        obligations = analysis.get("obligations", [])
        if obligations:
            story.append(Paragraph("Obligations Summary", h1))
            story.append(HRFlowable(width=W, thickness=1.5,
                                    color=ACCENT, spaceAfter=8))
            ob_rows = [["Party", "Type", "Description", "Deadline"]]
            for ob in obligations[:20]:
                ob_rows.append([
                    Paragraph(_safe(ob.get("party")), small),
                    Paragraph(_safe(ob.get("obligation_type")), small),
                    Paragraph(_safe(ob.get("description")), small),
                    Paragraph(_safe(ob.get("deadline")), small),
                ])
            ob_tbl = Table(ob_rows,
                           colWidths=[W*0.18, W*0.14, W*0.48, W*0.20])
            ob_tbl.setStyle(TableStyle([
                ("BACKGROUND",    (0,0), (-1,0), TBL_HEAD),
                ("TEXTCOLOR",     (0,0), (-1,0), WHITE),
                ("FONTNAME",      (0,0), (-1,0), "Helvetica-Bold"),
                ("FONTSIZE",      (0,0), (-1,0), 8),
                ("ROWBACKGROUNDS",(0,1), (-1,-1), [WHITE, TBL_ALT]),
                ("GRID",          (0,0), (-1,-1), 0.3,
                 colors.HexColor("#e0dbd0")),
                ("TOPPADDING",    (0,0), (-1,-1), 5),
                ("BOTTOMPADDING", (0,0), (-1,-1), 5),
                ("LEFTPADDING",   (0,0), (-1,-1), 5),
                ("VALIGN",        (0,0), (-1,-1), "TOP"),
            ]))
            story.append(ob_tbl)
            story.append(Spacer(1, 0.4*cm))

        # ── 11. Recommendations ───────────────────────────────────────────────
        recs = analysis.get("recommendations", {})
        if recs and not recs.get("error"):
            story.append(Paragraph("Recommendations", h1))
            story.append(HRFlowable(width=W, thickness=1.5,
                                    color=ACCENT, spaceAfter=8))

            overall_rec = _safe(recs.get("overall_recommendation"),
                                "Review Required")
            rec_colour = {
                "Approve":                 GREEN,
                "Approve with Amendments": AMBER,
                "Negotiate":               AMBER,
                "Reject":                  RED,
            }.get(overall_rec, MID_GREY)

            rec_banner = Table([[
                Paragraph("Overall Recommendation",
                          ps("RecLabel", fontName="Helvetica-Bold",
                             fontSize=9, textColor=WHITE)),
                Paragraph(overall_rec,
                          ps("RecVal", fontName="Helvetica-Bold",
                             fontSize=14, textColor=rec_colour,
                             alignment=TA_RIGHT)),
            ]], colWidths=[W*0.55, W*0.45])
            rec_banner.setStyle(TableStyle([
                ("BACKGROUND",    (0,0), (-1,-1), DARK),
                ("TOPPADDING",    (0,0), (-1,-1), 10),
                ("BOTTOMPADDING", (0,0), (-1,-1), 10),
                ("LEFTPADDING",   (0,0), (0,-1),  12),
                ("RIGHTPADDING",  (-1,0), (-1,-1), 12),
                ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
            ]))
            story.append(rec_banner)

            if recs.get("recommendation_rationale"):
                story.append(Spacer(1, 0.2*cm))
                story.append(Paragraph(
                    recs["recommendation_rationale"], body))

            if recs.get("key_risks_summary"):
                story.append(Spacer(1, 0.2*cm))
                story.append(Paragraph(
                    f"<b>Key Risks:</b> {recs['key_risks_summary']}", body))

            # Immediate actions
            immediate = sorted(
                recs.get("immediate_actions", []),
                key=lambda x: int(x.get("priority", 5))
            )
            if immediate:
                story.append(Spacer(1, 0.3*cm))
                story.append(Paragraph("Immediate Actions Required", h2))
                for action in immediate[:10]:
                    story.append(Paragraph(
                        f"<b>P{action.get('priority','?')}.</b> "
                        f"[{_safe(action.get('owner','?'))}] "
                        f"{_safe(action.get('action'))}", body))
                    if action.get("reason"):
                        story.append(Paragraph(
                            f"   → {action['reason']}", bullet_st))

            # Before signing
            before_signing = recs.get("before_signing", [])
            if before_signing:
                story.append(Spacer(1, 0.3*cm))
                story.append(Paragraph("Before Signing Checklist", h2))
                for item in before_signing[:10]:
                    story.append(Paragraph(f"☐  {item}", bullet_st))

            # Legal escalation
            legal_items = recs.get("legal_escalation_items", [])
            if legal_items:
                story.append(Spacer(1, 0.3*cm))
                story.append(Paragraph("Legal Escalation Items", h2))
                for item in legal_items[:8]:
                    story.append(Paragraph(f"⚖  {item}", bullet_st))

        # ── 12. Footer ────────────────────────────────────────────────────────
        story.append(Spacer(1, 0.6*cm))
        story.append(HRFlowable(width=W, thickness=0.5, color=MID_GREY))
        story.append(Spacer(1, 0.1*cm))
        story.append(Paragraph(
            f"ContractIQ Analysis Report  •  "
            f"Generated {datetime.now().strftime('%d %B %Y at %H:%M')}  •  "
            "For internal use only",
            ps("Footer", fontName="Helvetica", fontSize=7.5,
               textColor=MID_GREY, alignment=TA_CENTER)
        ))

        doc.build(story)
        return output_path
