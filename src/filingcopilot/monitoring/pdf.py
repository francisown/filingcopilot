"""Minimal PDF rendering for a monitoring digest.

Reportlab is a declared core dependency. The CJK CID font keeps Chinese briefs
readable without shipping a font file.
"""

from __future__ import annotations

from html import escape
from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer


def _font() -> str:
    try:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        return "STSong-Light"
    except Exception:
        return "Helvetica"


def render_markdown_pdf(markdown: str) -> bytes:
    font = _font()
    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "MonitorBody",
        parent=styles["BodyText"],
        fontName=font,
        fontSize=9.5,
        leading=14,
    )
    title = ParagraphStyle(
        "MonitorTitle",
        parent=styles["Heading1"],
        fontName=font,
        fontSize=16,
        leading=22,
    )
    heading = ParagraphStyle(
        "MonitorHeading",
        parent=styles["Heading2"],
        fontName=font,
        fontSize=12,
        leading=17,
    )
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=40,
        rightMargin=40,
        topMargin=36,
        bottomMargin=36,
    )
    story = []
    for raw in markdown.splitlines():
        line = raw.rstrip()
        if not line:
            story.append(Spacer(1, 5))
            continue
        if line.startswith("# "):
            story.append(Paragraph(escape(line[2:]), title))
        elif line.startswith("## "):
            story.append(Paragraph(escape(line[3:]), heading))
        elif line.startswith("- "):
            story.append(Paragraph("• " + escape(line[2:]), body))
        else:
            text = line.replace("**", "")
            story.append(Paragraph(escape(text), body))
    document.build(story)
    return buffer.getvalue()
