"""
pdf_generator.py — PDF Generation Engine
-----------------------------------------
Converts a complaint text string into a NY Supreme Court-style PDF using ReportLab.

Formatting follows real NY CPLR Verified Complaint conventions:
    - Times-Roman 12pt body, double-spaced
    - Proper NY court caption with dashed-X separator lines
    - Two-column party block (plaintiff/against/defendant | VERIFIED COMPLAINT/Index No.)
    - Numbered allegations with tab-indented paragraph numbers
    - Optional Summons page (page 1)
    - Verification stub page (last page)
"""

import json
import logging
from io import BytesIO

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT, TA_JUSTIFY
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate,
    Paragraph, Spacer, KeepTogether, PageBreak,
    Table, TableStyle,
)
from reportlab.platypus.flowables import HRFlowable
from reportlab.lib import colors

from docx_generator import parse_complaint_text

logger = logging.getLogger(__name__)

PAGE_WIDTH, PAGE_HEIGHT = letter          # 612 × 792 pts
MARGIN                  = 1.0 * inch
CONTENT_WIDTH           = PAGE_WIDTH - 2 * MARGIN


# ──────────────────────────────────────────────
# Footer
# ──────────────────────────────────────────────
def _draw_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Times-Roman", 10)
    canvas.drawCentredString(PAGE_WIDTH / 2, 0.5 * inch, str(doc.page))
    canvas.restoreState()


# ──────────────────────────────────────────────
# Style definitions
# ──────────────────────────────────────────────
def _build_styles() -> dict:
    FONT_BODY   = "Times-Roman"
    FONT_BOLD   = "Times-Bold"
    SIZE        = 12
    LEADING     = SIZE * 2          # double-spaced
    LEAD_SINGLE = SIZE * 1.4

    return {
        # Court name / caption lines — Times-Roman (not Courier)
        "caption": ParagraphStyle(
            name        = "caption",
            fontName    = FONT_BODY,
            fontSize    = SIZE,
            leading     = LEAD_SINGLE,
            alignment   = TA_LEFT,
            spaceAfter  = 0,
            spaceBefore = 0,
        ),
        # Caption right column (VERIFIED COMPLAINT, Index No.)
        "caption_right": ParagraphStyle(
            name        = "caption_right",
            fontName    = FONT_BOLD,
            fontSize    = SIZE,
            leading     = LEAD_SINGLE,
            alignment   = TA_RIGHT,
        ),
        # Caption center (Plaintiff, Defendants.)
        "caption_center": ParagraphStyle(
            name        = "caption_center",
            fontName    = FONT_BODY,
            fontSize    = SIZE,
            leading     = LEAD_SINGLE,
            alignment   = TA_CENTER,
        ),
        # ALL-CAPS section header (used only for WHEREFORE)
        "header": ParagraphStyle(
            name        = "header",
            fontName    = FONT_BOLD,
            fontSize    = SIZE,
            leading     = LEAD_SINGLE,
            alignment   = TA_LEFT,
            spaceBefore = 12,
            spaceAfter  = 6,
        ),
        # Subheader (Respectfully submitted, firm name)
        "subheader": ParagraphStyle(
            name        = "subheader",
            fontName    = FONT_BOLD,
            fontSize    = SIZE,
            leading     = LEAD_SINGLE,
            alignment   = TA_LEFT,
            spaceBefore = 12,
            spaceAfter  = 6,
        ),
        # Numbered allegation — NY style: number at 0.4", text at 1.0"
        "numbered": ParagraphStyle(
            name            = "numbered",
            fontName        = FONT_BODY,
            fontSize        = SIZE,
            leading         = LEADING,
            alignment       = TA_JUSTIFY,
            leftIndent      = 0.9 * inch,
            firstLineIndent = -0.7 * inch,
            spaceBefore     = 6,
            spaceAfter      = 0,
        ),
        # Lettered prayer item
        "lettered": ParagraphStyle(
            name            = "lettered",
            fontName        = FONT_BODY,
            fontSize        = SIZE,
            leading         = LEADING,
            alignment       = TA_JUSTIFY,
            leftIndent      = 1.2 * inch,
            firstLineIndent = -0.5 * inch,
            spaceBefore     = 0,
            spaceAfter      = 0,
        ),
        # Regular body paragraph (intro sentence, wherefore body, etc.)
        "body": ParagraphStyle(
            name        = "body",
            fontName    = FONT_BODY,
            fontSize    = SIZE,
            leading     = LEADING,
            alignment   = TA_JUSTIFY,
            spaceBefore = 0,
            spaceAfter  = 0,
        ),
        # Signature block (left-aligned, single-spaced)
        "signature": ParagraphStyle(
            name        = "signature",
            fontName    = FONT_BODY,
            fontSize    = SIZE,
            leading     = LEAD_SINGLE,
            alignment   = TA_LEFT,
            spaceBefore = 0,
            spaceAfter  = 0,
        ),
        # Verification body
        "verification": ParagraphStyle(
            name        = "verification",
            fontName    = FONT_BODY,
            fontSize    = SIZE,
            leading     = LEADING,
            alignment   = TA_JUSTIFY,
            spaceBefore = 0,
            spaceAfter  = 0,
        ),
    }


def _escape(text: str) -> str:
    return (
        text
        .replace("&",  "&amp;")
        .replace("<",  "&lt;")
        .replace(">",  "&gt;")
        .replace('"',  "&quot;")
    )


# ──────────────────────────────────────────────
# NY CPLR caption builder
# ──────────────────────────────────────────────
def _build_ny_caption(plaintiff: str, defendant: str, county: str,
                      doc_title: str, styles: dict) -> list:
    """
    Build the proper NY Supreme Court caption as ReportLab flowables.

    Layout:
        SUPREME COURT OF THE STATE OF NEW YORK
        COUNTY OF [COUNTY]
        -------X
        PLAINTIFF NAME,
                              Plaintiff,
               -against-                         VERIFIED COMPLAINT
        DEFENDANT NAME,
                                                  Index No.:
                              Defendants.
        -------X
    """
    cap   = styles["caption"]
    capR  = styles["caption_right"]
    capC  = styles["caption_center"]

    flowables = []

    # Court name lines
    flowables.append(Paragraph("SUPREME COURT OF THE STATE OF NEW YORK", cap))
    flowables.append(Paragraph(f"COUNTY OF {county.upper()}", cap))

    # Dashed-X separator
    dash = "-" * 72 + "X"
    flowables.append(Paragraph(_escape(dash), cap))
    flowables.append(Spacer(1, 4))

    # Party block — 2-column table
    p_esc = _escape(plaintiff.upper()) + ","
    d_esc = _escape(defendant.upper()) + ","
    title_para = Paragraph(f"<b><u>{_escape(doc_title)}</u></b>", capR)
    index_para = Paragraph("Index No.:", capR)

    data = [
        [Paragraph(p_esc, cap),                 ""],
        [Paragraph("Plaintiff,",  capC),         ""],
        [Paragraph("-against-",   cap),          title_para],
        ["",                                      ""],
        [Paragraph(d_esc, cap),                  ""],
        ["",                                      index_para],
        [Paragraph("Defendants.", capC),          ""],
    ]

    col_l = CONTENT_WIDTH * 0.58
    col_r = CONTENT_WIDTH * 0.42
    t = Table(data, colWidths=[col_l, col_r])
    t.setStyle(TableStyle([
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
        ('TOPPADDING',    (0, 0), (-1, -1), 1),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
    ]))
    flowables.append(t)
    flowables.append(Spacer(1, 2))

    # Closing dashed-X separator
    flowables.append(Paragraph(_escape(dash), cap))
    flowables.append(Spacer(1, 0.1 * inch))

    return flowables


# ──────────────────────────────────────────────
# Summons page (page 1)
# ──────────────────────────────────────────────
def _build_summons_page(plaintiff: str, defendant: str, county: str,
                        styles: dict) -> list:
    """
    Generate a NY Supreme Court Summons page (page 1 of the filing).
    Uses real NY CPLR § 305 summons language.
    """
    cap  = styles["caption"]
    sig  = styles["signature"]

    # Summons uses single-spaced body so everything fits on one page
    summons_body_style = ParagraphStyle(
        "summons_body", fontName="Times-Roman", fontSize=12,
        leading=14.4, alignment=TA_JUSTIFY,
        spaceBefore=0, spaceAfter=0,
    )

    flowables = []

    # Top two-column block: Date of Filing / venue block
    venue_text = (
        "Index No."
        "<br/><br/>"
        "Plaintiff designates " + county.title() + " County"
        "<br/>as the place of trial."
        "<br/><br/>"
        "<b>The basis of the venue is</b>"
        "<br/>the residence of Plaintiff."
    )
    top_data = [
        [Paragraph("Date of Filing:", sig),
         Paragraph(venue_text, sig)],
    ]
    top_table = Table(top_data, colWidths=[CONTENT_WIDTH * 0.45, CONTENT_WIDTH * 0.55])
    top_table.setStyle(TableStyle([
        ('VALIGN',       (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING',  (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING',   (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 0),
    ]))
    flowables.append(top_table)
    flowables.append(Spacer(1, 0.15 * inch))

    # Court caption with SUMMONS title
    flowables += _build_ny_caption(plaintiff, defendant, county, "SUMMONS", styles)

    # "To the above-named Defendants:" intro
    flowables.append(Paragraph("To the above-named Defendants:", summons_body_style))
    flowables.append(Spacer(1, 0.12 * inch))

    # Summons body — single paragraph, single-spaced
    summons_text = (
        "<b>YOU ARE HEREBY SUMMONED</b>, to answer the complaint in this action and to serve "
        "a copy of your answer, or, if the complaint is not served with this summons, to serve "
        "a notice of appearance, on the plaintiff&#x2019;s attorneys within - 20- days after "
        "the service of this summons, exclusive of the day of service (or within 30 days after "
        "the service is complete if this summons is not personally delivered to you within the "
        "State of New York); and in case of your failure to appear or answer, judgment will be "
        "taken against you by default for the relief demanded in the complaint."
    )
    flowables.append(Paragraph(summons_text, summons_body_style))
    flowables.append(Spacer(1, 0.25 * inch))

    # Signature block — two-column
    dated_text = "Dated: [CITY], New York<br/>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;[DATE]"
    firm_text  = "[FIRM NAME]<br/><i>Attorneys for Plaintiff</i>"
    by_text    = "By: [ATTORNEY NAME], Esq.<br/>[ADDRESS]<br/>[PHONE]"
    sig_data = [
        [Paragraph(dated_text, sig), Paragraph(firm_text, sig)],
        ["",                          Paragraph("", sig)],
        ["",                          Paragraph(by_text, sig)],
    ]
    sig_table = Table(sig_data, colWidths=[CONTENT_WIDTH * 0.45, CONTENT_WIDTH * 0.55])
    sig_table.setStyle(TableStyle([
        ('VALIGN',       (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING',  (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING',   (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 3),
    ]))
    flowables.append(sig_table)
    flowables.append(Spacer(1, 0.15 * inch))

    # Defendant addresses
    flowables.append(Paragraph("<u>Defendant&#x2019;s addresses:</u>", sig))
    flowables.append(Spacer(1, 6))
    flowables.append(Paragraph("See attached Verified Complaint.", sig))

    # Page break → complaint starts on next page
    flowables.append(PageBreak())

    return flowables


# ──────────────────────────────────────────────
# Verification stub page (last page)
# ──────────────────────────────────────────────
def _build_verification_page(plaintiff: str, styles: dict) -> list:
    bold_style = ParagraphStyle(
        name="verif_bold", fontName="Times-Bold", fontSize=12,
        leading=14.4, alignment=TA_CENTER,
    )
    body = styles["verification"]
    sig  = styles["signature"]

    flowables = [PageBreak()]
    flowables.append(Spacer(1, 0.5 * inch))
    flowables.append(Paragraph("<b>VERIFICATION</b>", bold_style))
    flowables.append(Spacer(1, 0.3 * inch))

    flowables.append(Paragraph("STATE OF NEW YORK,", sig))
    flowables.append(Paragraph("COUNTY OF [COUNTY] <i>ss:</i>", sig))
    flowables.append(Spacer(1, 0.25 * inch))

    verif_text = (
        f"{_escape(plaintiff)}, being duly sworn says; I am the plaintiff in the action herein; "
        "I have read the annexed Verified Complaint, know the contents thereof and the same are "
        "true to my knowledge, except those matters therein which are stated to be alleged on "
        "information and belief, and as to those matters I believe them to be true."
    )
    flowables.append(Paragraph(verif_text, body))
    flowables.append(Spacer(1, 0.5 * inch))

    # Signature block — right column: signature line over printed name
    # Left column: notary sworn-to block
    p_esc = _escape(plaintiff)
    flowables.append(Spacer(1, 0.5 * inch))

    # Right: signature line + printed name (centered in right half)
    sig_right = ParagraphStyle(
        "sig_right", fontName="Times-Roman", fontSize=12,
        leading=14, alignment=TA_CENTER,
    )
    sig_block = [
        [Paragraph("", sig), Paragraph("_" * 35, sig_right)],
        ["",                  Paragraph(p_esc, sig_right)],
    ]
    sig_tbl = Table(sig_block, colWidths=[CONTENT_WIDTH * 0.45, CONTENT_WIDTH * 0.55])
    sig_tbl.setStyle(TableStyle([
        ('LEFTPADDING',  (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING',   (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 3),
    ]))
    flowables.append(sig_tbl)
    flowables.append(Spacer(1, 0.3 * inch))

    # Notary block — left column
    notary_block = [
        [Paragraph("Sworn to before me on", sig), ""],
        [Paragraph("[DATE]", sig),                 ""],
        [Paragraph("", sig),                       ""],
        [Paragraph("_" * 30, sig),                 ""],
        [Paragraph("NOTARY PUBLIC", sig),           ""],
    ]
    notary_tbl = Table(notary_block, colWidths=[CONTENT_WIDTH * 0.5, CONTENT_WIDTH * 0.5])
    notary_tbl.setStyle(TableStyle([
        ('LEFTPADDING',  (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING',   (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 3),
    ]))
    flowables.append(notary_tbl)

    return flowables


# ──────────────────────────────────────────────
# Body block renderer
# ──────────────────────────────────────────────
def _is_dash_line(text: str) -> bool:
    """True if line is a dashed-X separator (from Claude's caption output)."""
    stripped = text.strip()
    return len(stripped) > 10 and stripped.replace("-", "").replace("X", "") == ""


def _body_flowables(parsed: dict, styles: dict) -> list:
    """Render only the body blocks (post-caption) from parsed complaint."""
    flowables = []
    for block in parsed["blocks"]:
        btype = block["type"]

        if btype == "spacer":
            flowables.append(Spacer(1, 0.12 * inch))

        elif btype == "header":
            text = _escape(block["text"])
            if text.upper().startswith("WHEREFORE"):
                # WHEREFORE stands alone, not as section header
                flowables.append(Spacer(1, 0.15 * inch))
                flowables.append(Paragraph(f"<b>{text}</b>", styles["header"]))
            else:
                # Any remaining headers: suppress (body-only rendering)
                pass

        elif btype == "subheader":
            flowables.append(Paragraph(f"<b>{_escape(block['text'])}</b>", styles["subheader"]))

        elif btype == "numbered":
            # Tab-indented number, then double-spaced text
            text = f"{block['number']}.&nbsp;&nbsp;&nbsp;&nbsp;{_escape(block['text'])}"
            flowables.append(Paragraph(text, styles["numbered"]))

        elif btype == "lettered":
            text = f"{block['letter']}.&nbsp;&nbsp;{_escape(block['text'])}"
            flowables.append(Paragraph(text, styles["lettered"]))

        elif btype == "body":
            raw = block["text"]
            if _is_dash_line(raw):
                continue   # skip duplicate dashes from Claude caption output
            flowables.append(Paragraph(_escape(raw), styles["body"]))

    return flowables


# ══════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════

def generate_complaint_pdf(
    complaint_text: str,
    case_id:        str,
    case_type:      str,
    attorney_name:  str  = "",
    case_fields:    dict | None = None,
) -> bytes:
    """
    Convert a complaint text string into a properly formatted NY CPLR PDF.

    If case_fields is provided (plaintiff_name, defendant_name, incident_location),
    the caption is built from structured data — proper two-column NY format with
    dashed-X lines. A Summons page (page 1) and Verification stub page are added.

    Without case_fields: falls back to parsing the caption from complaint text.

    Args:
        complaint_text : Raw complaint string from complaint_drafter
        case_id        : Used for logging
        case_type      : For metadata
        attorney_name  : Injected into PDF metadata
        case_fields    : Dict of case fields for structured caption rendering

    Returns:
        bytes — raw PDF content ready to stream
    """
    parsed = parse_complaint_text(complaint_text)
    styles = _build_styles()

    flowables: list = []

    if case_fields:
        # ── Structured NY CPLR rendering ─────────────────────────────────────
        plaintiff = case_fields.get("plaintiff_name", "[PLAINTIFF]")
        defendant = case_fields.get("defendant_name", "[DEFENDANT]")
        location  = (case_fields.get("incident_location") or "").lower()

        # Derive county from location
        if any(x in location for x in ["manhattan", "new york county"]):
            county = "NEW YORK"
        elif any(x in location for x in ["brooklyn", "kings"]):
            county = "KINGS"
        elif "queens" in location:
            county = "QUEENS"
        elif "bronx" in location:
            county = "BRONX"
        elif any(x in location for x in ["staten island", "richmond"]):
            county = "RICHMOND"
        elif any(x in location for x in ["bqe", "brooklyn-queens"]):
            county = "QUEENS"
        else:
            county = "NEW YORK"

        # Page 1+: Verified Complaint caption
        flowables += _build_ny_caption(plaintiff, defendant, county,
                                       "VERIFIED COMPLAINT", styles)

        # Complaint body (no caption re-rendering)
        flowables += _body_flowables(parsed, styles)

        # Last page: Verification stub
        flowables += _build_verification_page(plaintiff, styles)

    else:
        # ── Fallback: render caption_lines from parsed text ───────────────────
        cap_style = styles["caption"]
        for line in parsed["caption_lines"]:
            display = line.rstrip() or " "
            if _is_dash_line(display):
                flowables.append(Paragraph(_escape(display.strip()), cap_style))
            else:
                flowables.append(Paragraph(_escape(display), cap_style))
        flowables.append(Spacer(1, 0.2 * inch))

        # Body blocks
        flowables += _body_flowables(parsed, styles)

    # ── Build PDF document ────────────────────────────────────────────────────
    buffer = BytesIO()
    doc = BaseDocTemplate(
        buffer,
        pagesize     = letter,
        leftMargin   = MARGIN,
        rightMargin  = MARGIN,
        topMargin    = MARGIN,
        bottomMargin = MARGIN + 0.3 * inch,
        title        = f"Verified Complaint — {case_type.replace('_', ' ').title()}",
        author       = attorney_name or "[ATTORNEY NAME]",
        subject      = f"Case ID: {case_id}",
        creator      = "Nyaay AI",
    )

    frame = Frame(
        MARGIN, MARGIN + 0.3 * inch,
        CONTENT_WIDTH, PAGE_HEIGHT - 2 * MARGIN - 0.3 * inch,
        id="main",
    )
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=_draw_footer)])

    try:
        doc.build(flowables)
    except Exception as e:
        raise RuntimeError(f"PDF generation failed for case_id={case_id}: {e}") from e

    pdf_bytes = buffer.getvalue()
    logger.info(
        f"PDF generated | case_id={case_id} | case_type={case_type} | "
        f"size={len(pdf_bytes):,} bytes | pages~{len(flowables)//10}"
    )
    return pdf_bytes
