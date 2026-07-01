"""
Renders the compiled manifest as a formatted landscape PDF table.

Functionally unchanged from the original pdf_output.py (same layout, fonts,
CJK support via ReportLab's built-in CID font), but it now accepts
`List[ManifestRow]` + a `columns` list rather than a pre-flattened
`[[header], [row1], [row2], ...]` structure, so callers don't have to
manually recompile a DataFrame back into nested lists before exporting.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Union

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.models import ManifestRow

logger = logging.getLogger(__name__)


def register_cjk_font() -> str:
    """Uses ReportLab's built-in CID font -- no external font file needed."""
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    logger.info("[PDF] Using built-in CID font STSong-Light for CJK support.")
    return "STSong-Light"


def save_pipeline_to_pdf(
    rows: List[ManifestRow],
    columns: List[str],
    output_pdf_path: Union[str, Path, object],
    manifest_date: str,
) -> bool:
    """Renders `rows` (in `columns` order) as a landscape PDF table."""
    if not rows:
        logger.error("PDF export aborted: no rows to write.")
        return False

    cjk_font = register_cjk_font()
    logger.info(f"Starting PDF export: {len(rows)} records to {output_pdf_path}...")

    try:
        if isinstance(output_pdf_path, (str, Path)):
            pdf_file = Path(output_pdf_path)
            pdf_file.parent.mkdir(parents=True, exist_ok=True)
            target = str(pdf_file)
        else:
            target = output_pdf_path  # BytesIO buffer stream (e.g. from Streamlit)

        doc = SimpleDocTemplate(
            target,
            pagesize=landscape(A4),
            leftMargin=10 * mm,
            rightMargin=10 * mm,
            topMargin=12 * mm,
            bottomMargin=12 * mm,
        )

        styles = getSampleStyleSheet()
        cell_style = ParagraphStyle(
            "cell", parent=styles["Normal"], fontSize=6.5, leading=9,
            fontName=cjk_font, wordWrap="CJK",
        )
        header_style = ParagraphStyle(
            "header", parent=styles["Normal"], fontSize=7, leading=9,
            fontName=cjk_font, alignment=TA_CENTER,
        )

        def wrap_row(values, style):
            return [Paragraph(str(cell) if cell else "", style) for cell in values]

        table_data = [wrap_row(columns, header_style)]
        for row in rows:
            table_data.append(wrap_row(row.to_row(columns), cell_style))

        num_cols = len(columns)
        col_width = (277 * mm) / num_cols
        table = Table(table_data, colWidths=[col_width] * num_cols, repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#AED9E0")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#1A252F")),
            ("ALIGN", (0, 0), (-1, 0), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F2F2")]),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CCCCCC")),
            ("LINEBELOW", (0, 0), (-1, 0), 1.2, colors.HexColor("#1A252F")),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ]))

        title_style = ParagraphStyle(
            "title", parent=styles["Normal"], fontSize=11,
            fontName="Helvetica-Bold", spaceBefore=0, spaceAfter=6,
        )
        title = Paragraph(f"Flight Manifest — {manifest_date or 'Date TBD'}", title_style)

        doc.build([title, Spacer(1, 4 * mm), table])
        logger.info("PDF export complete.")
        return True

    except Exception as e:
        logger.error(f"PDF export failed: {e}")
        return False
