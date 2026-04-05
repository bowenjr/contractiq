"""
Report Generator for ContractIQ.
Produces professional PDF analysis reports using ReportLab.
"""

import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List


def _safe(val, default="Not specified"):
    if val is None or val == "" or val == "null":
        return default
    return str(val)


class ReportGenerator:
    def __init__(self, reports_dir: Path):
        self.reports_dir = reports_dir
        self.reports_dir.mkdir(exist_ok=True)

    def generate(self, contract: Dict, analysis: Dict, output_path: Path):
        """Generate a comprehensive PDF report."""
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.lib.units import cm
            from reportlab.lib import colors
            from reportlab.platypus import (
                SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
                HRFlowable, PageBreak
            )
            from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
        except ImportError:
            raise ImportError("ReportLab not installed. Run: pip install reportlab")

        # ── Colours ──────────────────────────────────────────────────────────
        DARK = colors.HexColor("#1a1f2e")
        ACCENT = colors.HexColor("#c8a96e")  # gold
        LIGHT_BG = colors.HexColor("#f4f1eb")
        MID_GREY = colors.HexColor("#6b7280")
        RED = colors.HexColor("#dc2626")
        AMBER = colors.HexColor("#d97706")
        GREEN = colors.HexColor("#16a34a")
        BLUE = colors.HexColor("#1d4ed8")
        TABLE_HEAD = colors.HexColor("#1a1f2e")
        TABLE_ALT = colors.HexColor("#f8f6f0")
        WHITE = colors.white

        risk_level = analysis.get("risk_score", {}).get("level", "Unknown")
        risk_colour = {
            "Low": GREEN, "Medium": AMBER, "High": RED, "Critical": RED
        }.get(risk_level, MID_GREY)

        # ── Styles ────────────────────────────────────────────────────────────
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle("Title", parent=styles["Normal"],
            fontName="Helvetica-Bold", fontSize=22, textColor=WHITE,
            spaceAfter=4, leading=26)
        subtitle_style = ParagraphStyle("Subtitle", parent=styles["Normal"],
            fontName="Helvetica", fontSize=11, textColor=ACCENT, spaceAfter=2)
        h1 = ParagraphStyle("H1", parent=styles["Normal"],
            fontName="Helvetica-Bold", fontSize=14, textColor=DARK,
            spaceBefore=16, spaceAfter=8, borderPad=0)
        h2 = ParagraphStyle("H2", parent=styles["Normal"],
            fontName="Helvetica-Bold", fontSize=11, textColor=DARK,
            spaceBefore=10, spaceAfter=4)
        body = ParagraphStyle("Body", parent=styles["Normal"],
            fontName="Helvetica", fontSize=9.5, textColor=DARK,
            leading=14, spaceAfter=4)
        small = ParagraphStyle("Small", parent=styles["Normal"],
            fontName="Helvetica", fontSize=8.5, textColor=MID_GREY, leading=12)
        bullet_style = ParagraphStyle("Bullet", parent=styles["Normal"],
            fontName="Helvetica", fontSize=9.5, textColor=DARK,
            leading=13, leftIndent=12, spaceAfter=3,
            bulletFontName="Helvetica", bulletIndent=0)

        doc = SimpleDocTemplate(
            str(output_path),
            pagesize=A4,
            leftMargin=2*cm, rightMargin=2*cm,
            topMargin=2*cm, bottomMargin=2*cm,
            title=f"ContractIQ Report – {contract['filename']}"
        )

        story = []
        W = A4[0] - 4*cm  # usable width

        # ── Header Banner ─────────────────────────────────────────────────────
        header_data = [[
            Paragraph("ContractIQ", title_style),
            Paragraph(f"Risk Level: {risk_level}", ParagraphStyle("RL",
                parent=styles["Normal"], fontName="Helvetica-Bold",
                fontSize=14, textColor=risk_colour, alignment=TA_RIGHT))
        ]]
        header_table = Table(header_data, colWidths=[W*0.65, W*0.35])
        header_table.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,-1), DARK),
            ("TOPPADDING", (0,0), (-1,-1), 14),
            ("BOTTOMPADDING", (0,0), (-1,-1), 14),
            ("LEFTPADDING", (0,0), (0,-1), 16),
            ("RIGHTPADDING", (-1,0), (-1,-1), 16),
            ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ]))
        story.append(header_table)
        story.append(Spacer(1, 0.3*cm))

        # ── Document Meta ─────────────────────────────────────────────────────
        meta_items = [
            ["Document", _safe(contract.get("filename"))],
            ["Type", _safe(analysis.get("doc_type"))],
            ["Generated", datetime.now().strftime("%d %B %Y, %H:%M")],
            ["Pages", _safe(contract.get("page_count"))],
            ["Words", f"{contract.get('word_count', 0):,}"],
            ["Value", _safe(analysis.get("contract_value"))],
            ["Duration", _safe(analysis.get("contract_duration"))],
            ["Governing Law", _safe(analysis.get("governing_law"))],
        ]
        meta_data = [[
            Paragraph(k, ParagraphStyle("MK", parent=styles["Normal"],
                fontName="Helvetica-Bold", fontSize=8.5, textColor=MID_GREY)),
            Paragraph(v, ParagraphStyle("MV", parent=styles["Normal"],
                fontName="Helvetica", fontSize=9, textColor=DARK))
        ] for k, v in meta_items]
        # Arrange in 2-column grid
        paired = []
        for i in range(0, len(meta_data), 2):
            row = meta_data[i]
            if i+1 < len(meta_data):
                row = row + meta_data[i+1]
            else:
                row = row + [Paragraph("", body), Paragraph("", body)]
            paired.append(row)
        meta_table = Table(paired, colWidths=[W*0.12, W*0.38, W*0.12, W*0.38])
        meta_table.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,-1), TABLE_ALT),
            ("TOPPADDING", (0,0), (-1,-1), 5),
            ("BOTTOMPADDING", (0,0), (-1,-1), 5),
            ("LEFTPADDING", (0,0), (-1,-1), 8),
            ("GRID", (0,0), (-1,-1), 0.3, colors.HexColor("#e5e0d5")),
            ("VALIGN", (0,0), (-1,-1), "TOP"),
        ]))
        story.append(meta_table)
        story.append(Spacer(1, 0.4*cm))

        # ── Executive Summary ─────────────────────────────────────────────────
        story.append(Paragraph("Executive Summary", h1))
        story.append(HRFlowable(width=W, thickness=1.5, color=ACCENT, spaceAfter=8))
        summary = _safe(analysis.get("executive_summary"), "No summary available.")
        story.append(Paragraph(summary, body))

        # Key subject
        if analysis.get("key_subject"):
            story.append(Spacer(1, 0.2*cm))
            story.append(Paragraph(
                f"<b>Subject:</b> {analysis['key_subject']}", body))

        story.append(Spacer(1, 0.4*cm))

        # ── Risk Score Dashboard ──────────────────────────────────────────────
        story.append(Paragraph("Risk Assessment", h1))
        story.append(HRFlowable(width=W, thickness=1.5, color=ACCENT, spaceAfter=8))

        rs = analysis.get("risk_score", {})
        score = rs.get("overall_score", 0)

        # Risk metrics grid
        risk_cols = [
            ("Overall Score", f"{score}/100", risk_colour),
            ("Risk Level", risk_level, risk_colour),
            ("Financial Risk", _safe(rs.get("financial_risk"), "—"), AMBER),
            ("Legal Risk", _safe(rs.get("legal_risk"), "—"), AMBER),
            ("Operational Risk", _safe(rs.get("operational_risk"), "—"), AMBER),
        ]
        risk_grid_data = [[
            Table([[
                Paragraph(label, ParagraphStyle("RL", parent=styles["Normal"],
                    fontName="Helvetica-Bold", fontSize=8, textColor=MID_GREY,
                    alignment=TA_CENTER)),
                Paragraph(value, ParagraphStyle("RV", parent=styles["Normal"],
                    fontName="Helvetica-Bold", fontSize=13, textColor=col,
                    alignment=TA_CENTER))
            ]], colWidths=[(W/5) - 6],
            style=TableStyle([
                ("BACKGROUND", (0,0), (-1,-1), TABLE_ALT),
                ("BOX", (0,0), (-1,-1), 1.5, col),
                ("TOPPADDING", (0,0), (-1,-1), 8),
                ("BOTTOMPADDING", (0,0), (-1,-1), 8),
                ("ALIGN", (0,0), (-1,-1), "CENTER"),
            ]))
            for label, value, col in risk_cols
        ]]
        risk_grid = Table(risk_grid_data, colWidths=[(W/5)] * 5,
                          hAlign="LEFT")
        story.append(risk_grid)

        if rs.get("score_rationale"):
            story.append(Spacer(1, 0.3*cm))
            story.append(Paragraph(rs["score_rationale"], body))

        # Red Flags
        red_flags = rs.get("red_flags", [])
        if red_flags:
            story.append(Spacer(1, 0.3*cm))
            story.append(Paragraph("Red Flags & Risk Factors", h2))
            flag_rows = [["Flag", "Severity", "Description"]]
            for f in red_flags[:12]:
                sev = _safe(f.get("severity"), "—")
                sev_colour = {"Critical": RED, "High": RED, "Medium": AMBER,
                              "Low": GREEN}.get(sev, MID_GREY)
                flag_rows.append([
                    Paragraph(_safe(f.get("flag")), small),
                    Paragraph(f'<font color="#{sev_colour.hexval()[1:]}"><b>{sev}</b></font>',
                              ParagraphStyle("FlagSev", parent=styles["Normal"],
                                  fontSize=8.5, alignment=TA_CENTER)),
                    Paragraph(_safe(f.get("description")), small),
                ])
            flag_table = Table(flag_rows, colWidths=[W*0.25, W*0.12, W*0.63])
            flag_table.setStyle(TableStyle([
                ("BACKGROUND", (0,0), (-1,0), TABLE_HEAD),
                ("TEXTCOLOR", (0,0), (-1,0), WHITE),
                ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
                ("FONTSIZE", (0,0), (-1,0), 8.5),
                ("ROWBACKGROUNDS", (0,1), (-1,-1), [WHITE, TABLE_ALT]),
                ("GRID", (0,0), (-1,-1), 0.3, colors.HexColor("#e0dbd0")),
                ("TOPPADDING", (0,0), (-1,-1), 5),
                ("BOTTOMPADDING", (0,0), (-1,-1), 5),
                ("LEFTPADDING", (0,0), (-1,-1), 6),
                ("VALIGN", (0,0), (-1,-1), "TOP"),
            ]))
            story.append(flag_table)

        story.append(Spacer(1, 0.4*cm))

        # ── Parties & Obligations ─────────────────────────────────────────────
        parties_data = analysis.get("parties", {})
        parties = parties_data.get("parties", []) if isinstance(parties_data, dict) else []
        if parties:
            story.append(Paragraph("Parties & Obligations", h1))
            story.append(HRFlowable(width=W, thickness=1.5, color=ACCENT, spaceAfter=8))
            if parties_data.get("relationship_summary"):
                story.append(Paragraph(parties_data["relationship_summary"], body))
                story.append(Spacer(1, 0.2*cm))
            for party in parties:
                story.append(Paragraph(
                    f"<b>{_safe(party.get('name'))}</b> — {_safe(party.get('role'))}", h2))
                if party.get("description"):
                    story.append(Paragraph(party["description"], body))
                obligations = party.get("key_obligations", [])
                for ob in obligations[:6]:
                    story.append(Paragraph(f"• {ob}", bullet_style))
            story.append(Spacer(1, 0.4*cm))

        # ── Key Clauses ───────────────────────────────────────────────────────
        clauses = analysis.get("clauses", {})
        if clauses and not clauses.get("error"):
            story.append(Paragraph("Key Clauses Analysis", h1))
            story.append(HRFlowable(width=W, thickness=1.5, color=ACCENT, spaceAfter=8))

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

            clause_rows = [["Clause", "Status", "Summary"]]
            for key, label in clause_order:
                cl = clauses.get(key, {})
                if not isinstance(cl, dict):
                    continue
                found = cl.get("found", False)
                status_txt = "✓ Present" if found else "✗ Absent"
                status_col = GREEN if found else RED
                summary_parts = [cl.get("summary", "—")]
                if cl.get("cap"):
                    summary_parts.append(f"Cap: {cl['cap']}")
                if cl.get("notice_period"):
                    summary_parts.append(f"Notice: {cl['notice_period']}")
                if cl.get("method"):
                    summary_parts.append(f"Method: {cl['method']}")
                clause_rows.append([
                    Paragraph(f"<b>{label}</b>", small),
                    Paragraph(f'<font color="#{status_col.hexval()[1:]}">{status_txt}</font>',
                              ParagraphStyle("CS", parent=styles["Normal"],
                                  fontSize=8.5, alignment=TA_CENTER)),
                    Paragraph(" | ".join(str(p) for p in summary_parts if p), small),
                ])

            clause_table = Table(clause_rows, colWidths=[W*0.22, W*0.13, W*0.65])
            clause_table.setStyle(TableStyle([
                ("BACKGROUND", (0,0), (-1,0), TABLE_HEAD),
                ("TEXTCOLOR", (0,0), (-1,0), WHITE),
                ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
                ("FONTSIZE", (0,0), (-1,0), 8.5),
                ("ROWBACKGROUNDS", (0,1), (-1,-1), [WHITE, TABLE_ALT]),
                ("GRID", (0,0), (-1,-1), 0.3, colors.HexColor("#e0dbd0")),
                ("TOPPADDING", (0,0), (-1,-1), 5),
                ("BOTTOMPADDING", (0,0), (-1,-1), 5),
                ("LEFTPADDING", (0,0), (-1,-1), 6),
                ("VALIGN", (0,0), (-1,-1), "TOP"),
            ]))
            story.append(clause_table)

            # Missing protections
            missing = clauses.get("missing_protections", [])
            if missing:
                story.append(Spacer(1, 0.2*cm))
                story.append(Paragraph("<b>Missing Protections:</b>", body))
                for m in missing[:8]:
                    story.append(Paragraph(f"⚠ {m}", bullet_style))

            story.append(Spacer(1, 0.4*cm))

        # ── Critical Dates ────────────────────────────────────────────────────
        dates = analysis.get("dates", {})
        if dates and not dates.get("error"):
            story.append(Paragraph("Critical Dates & Deadlines", h1))
            story.append(HRFlowable(width=W, thickness=1.5, color=ACCENT, spaceAfter=8))

            date_rows = [["Date / Period", "Description", "Importance"]]
            if dates.get("effective_date") and dates["effective_date"] != "null":
                date_rows.append([
                    Paragraph(f"<b>{dates['effective_date']}</b>", small),
                    Paragraph("Contract Effective Date", small),
                    Paragraph("High", ParagraphStyle("DI", parent=styles["Normal"],
                        fontSize=8.5, textColor=RED))
                ])
            if dates.get("expiry_date") and dates["expiry_date"] != "null":
                date_rows.append([
                    Paragraph(f"<b>{dates['expiry_date']}</b>", small),
                    Paragraph("Contract Expiry Date", small),
                    Paragraph("High", ParagraphStyle("DI", parent=styles["Normal"],
                        fontSize=8.5, textColor=RED))
                ])
            for d in dates.get("key_dates", [])[:10]:
                imp = _safe(d.get("importance"), "Medium")
                imp_col = {"High": RED, "Medium": AMBER, "Low": GREEN}.get(imp, MID_GREY)
                date_rows.append([
                    Paragraph(_safe(d.get("date")), small),
                    Paragraph(_safe(d.get("description")), small),
                    Paragraph(f'<font color="#{imp_col.hexval()[1:]}">{imp}</font>',
                              ParagraphStyle("DI2", parent=styles["Normal"], fontSize=8.5))
                ])

            if len(date_rows) > 1:
                date_table = Table(date_rows, colWidths=[W*0.22, W*0.60, W*0.18])
                date_table.setStyle(TableStyle([
                    ("BACKGROUND", (0,0), (-1,0), TABLE_HEAD),
                    ("TEXTCOLOR", (0,0), (-1,0), WHITE),
                    ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
                    ("FONTSIZE", (0,0), (-1,0), 8.5),
                    ("ROWBACKGROUNDS", (0,1), (-1,-1), [WHITE, TABLE_ALT]),
                    ("GRID", (0,0), (-1,-1), 0.3, colors.HexColor("#e0dbd0")),
                    ("TOPPADDING", (0,0), (-1,-1), 5),
                    ("BOTTOMPADDING", (0,0), (-1,-1), 5),
                    ("LEFTPADDING", (0,0), (-1,-1), 6),
                    ("VALIGN", (0,0), (-1,-1), "TOP"),
                ]))
                story.append(date_table)

            notice_periods = dates.get("notice_periods", [])
            if notice_periods:
                story.append(Spacer(1, 0.2*cm))
                story.append(Paragraph("<b>Notice Periods:</b>", body))
                for np in notice_periods[:6]:
                    story.append(Paragraph(
                        f"• {_safe(np.get('trigger'))}: {_safe(np.get('period'))}", bullet_style))

            story.append(Spacer(1, 0.4*cm))

        # ── Bid Analysis (if present) ─────────────────────────────────────────
        bid = analysis.get("bid_analysis")
        if bid and not bid.get("error"):
            story.append(PageBreak())
            story.append(Paragraph("Bid / Tender Analysis", h1))
            story.append(HRFlowable(width=W, thickness=1.5, color=ACCENT, spaceAfter=8))

            bid_meta = [
                ["Bid Type", _safe(bid.get("bid_type"))],
                ["Scope Clarity", _safe(bid.get("scope_clarity"))],
                ["Total Value", _safe(bid.get("pricing_analysis", {}).get("total_value"))],
                ["Pricing Model", _safe(bid.get("pricing_analysis", {}).get("pricing_model"))],
                ["Payment Schedule", _safe(bid.get("pricing_analysis", {}).get("payment_schedule"))],
            ]
            bm_data = [[
                Paragraph(k, ParagraphStyle("BK", parent=styles["Normal"],
                    fontName="Helvetica-Bold", fontSize=8.5, textColor=MID_GREY)),
                Paragraph(v, body)
            ] for k, v in bid_meta]
            bm_table = Table(bm_data, colWidths=[W*0.25, W*0.75])
            bm_table.setStyle(TableStyle([
                ("BACKGROUND", (0,0), (-1,-1), TABLE_ALT),
                ("TOPPADDING", (0,0), (-1,-1), 5),
                ("BOTTOMPADDING", (0,0), (-1,-1), 5),
                ("LEFTPADDING", (0,0), (-1,-1), 8),
                ("GRID", (0,0), (-1,-1), 0.3, colors.HexColor("#e0dbd0")),
                ("VALIGN", (0,0), (-1,-1), "TOP"),
            ]))
            story.append(bm_table)

            for section_key, section_label in [
                ("scope_gaps", "Scope Gaps"),
                ("exclusions", "Exclusions"),
                ("compliance_requirements", "Compliance Requirements"),
                ("submission_requirements", "Submission Requirements"),
            ]:
                items = bid.get(section_key, [])
                if items:
                    story.append(Spacer(1, 0.2*cm))
                    story.append(Paragraph(f"<b>{section_label}:</b>", body))
                    for item in items[:8]:
                        story.append(Paragraph(f"• {item}", bullet_style))

            bid_risks = bid.get("bid_risks", [])
            if bid_risks:
                story.append(Spacer(1, 0.3*cm))
                story.append(Paragraph("Bid-Specific Risks", h2))
                for br in bid_risks[:6]:
                    story.append(Paragraph(
                        f"<b>Risk:</b> {_safe(br.get('risk'))}", body))
                    if br.get("mitigation"):
                        story.append(Paragraph(
                            f"<b>Mitigation:</b> {br['mitigation']}", bullet_style))

            story.append(Spacer(1, 0.4*cm))

        # ── Recommendations ───────────────────────────────────────────────────
        recs = analysis.get("recommendations", {})
        if recs and not recs.get("error"):
            story.append(Paragraph("Recommendations", h1))
            story.append(HRFlowable(width=W, thickness=1.5, color=ACCENT, spaceAfter=8))

            overall_rec = _safe(recs.get("overall_recommendation"), "Review Required")
            rec_colour = {
                "Approve": GREEN, "Approve with Amendments": AMBER,
                "Negotiate": AMBER, "Reject": RED
            }.get(overall_rec, MID_GREY)

            rec_banner = Table([[
                Paragraph("Overall Recommendation", ParagraphStyle("RecLabel",
                    parent=styles["Normal"], fontName="Helvetica-Bold",
                    fontSize=9, textColor=WHITE)),
                Paragraph(overall_rec, ParagraphStyle("RecVal",
                    parent=styles["Normal"], fontName="Helvetica-Bold",
                    fontSize=14, textColor=rec_colour, alignment=TA_RIGHT))
            ]], colWidths=[W*0.55, W*0.45])
            rec_banner.setStyle(TableStyle([
                ("BACKGROUND", (0,0), (-1,-1), DARK),
                ("TOPPADDING", (0,0), (-1,-1), 10),
                ("BOTTOMPADDING", (0,0), (-1,-1), 10),
                ("LEFTPADDING", (0,0), (0,-1), 12),
                ("RIGHTPADDING", (-1,0), (-1,-1), 12),
                ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
            ]))
            story.append(rec_banner)

            if recs.get("recommendation_rationale"):
                story.append(Spacer(1, 0.2*cm))
                story.append(Paragraph(recs["recommendation_rationale"], body))

            # Immediate actions
            immediate = recs.get("immediate_actions", [])
            if immediate:
                story.append(Spacer(1, 0.3*cm))
                story.append(Paragraph("Immediate Actions Required", h2))
                sorted_actions = sorted(immediate, key=lambda x: x.get("priority", 5))
                for action in sorted_actions[:8]:
                    story.append(Paragraph(
                        f"<b>P{action.get('priority','?')}.</b> {_safe(action.get('action'))}", body))
                    if action.get("reason"):
                        story.append(Paragraph(f"   → {action['reason']}", bullet_style))

            # Negotiation points
            neg_points = recs.get("negotiation_points", [])
            if neg_points:
                story.append(Spacer(1, 0.3*cm))
                story.append(Paragraph("Negotiation Points", h2))
                for np_item in neg_points[:6]:
                    story.append(Paragraph(
                        f"<b>{_safe(np_item.get('clause'))}:</b> {_safe(np_item.get('recommended_change'))}",
                        body))

            # Before signing checklist
            before_signing = recs.get("before_signing", [])
            if before_signing:
                story.append(Spacer(1, 0.3*cm))
                story.append(Paragraph("Before Signing Checklist", h2))
                for item in before_signing[:10]:
                    story.append(Paragraph(f"☐  {item}", bullet_style))

        # ── Footer ────────────────────────────────────────────────────────────
        story.append(Spacer(1, 0.6*cm))
        story.append(HRFlowable(width=W, thickness=0.5, color=MID_GREY))
        story.append(Spacer(1, 0.1*cm))
        story.append(Paragraph(
            f"ContractIQ Analysis Report  •  Generated {datetime.now().strftime('%d %B %Y at %H:%M')}  •  For internal use only",
            ParagraphStyle("Footer", parent=styles["Normal"],
                fontName="Helvetica", fontSize=7.5, textColor=MID_GREY,
                alignment=TA_CENTER)
        ))

        doc.build(story)
        return output_path
