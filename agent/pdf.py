"""PDF-версия отчёта (reportlab). Шрифт DejaVu Sans лежит в папке fonts — с кириллицей всё в порядке."""
from __future__ import annotations

import re
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle)

FONTS = Path(__file__).resolve().parent.parent / "fonts"
_registered = False

INK = colors.HexColor("#1F2328")
MUTED = colors.HexColor("#5F6368")
LINE = colors.HexColor("#D0D7DE")
HEAD_BG = colors.HexColor("#F2F4F7")
ACCENT = colors.HexColor("#0F4C81")
SEV_BG = {"критично": colors.HexColor("#FDECEC"), "существенно": colors.HexColor("#FFF4E0"),
          "незначительно": colors.HexColor("#EEF2F6")}
SEV_INK = {"критично": colors.HexColor("#A1261C"), "существенно": colors.HexColor("#8A5300"),
           "незначительно": colors.HexColor("#44505C")}
VERDICT = {"Нет": ("#FDECEC", "#A1261C"), "С оговорками": ("#FFF4E0", "#8A5300"), "Да": ("#E8F5EC", "#1E6B37")}


def _fonts() -> None:
    global _registered
    if _registered:
        return
    pdfmetrics.registerFont(TTFont("DejaVu", str(FONTS / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("DejaVu-Bold", str(FONTS / "DejaVuSans-Bold.ttf")))
    pdfmetrics.registerFontFamily("DejaVu", normal="DejaVu", bold="DejaVu-Bold", italic="DejaVu", boldItalic="DejaVu-Bold")
    _registered = True


def _styles() -> dict:
    base = dict(fontName="DejaVu", textColor=INK)
    return {
        "title": ParagraphStyle("title", fontSize=18, leading=23, spaceAfter=4, textColor=ACCENT, fontName="DejaVu-Bold"),
        "h1": ParagraphStyle("h1", fontSize=13, leading=17, spaceBefore=12, spaceAfter=6, textColor=ACCENT,
                             fontName="DejaVu-Bold", keepWithNext=1),
        "h3": ParagraphStyle("h3", fontSize=9.5, leading=12.5, spaceBefore=8, spaceAfter=2, fontName="DejaVu-Bold",
                             keepWithNext=1, textColor=INK),
        "p": ParagraphStyle("p", fontSize=9, leading=12.5, spaceAfter=4, **base),
        "note": ParagraphStyle("note", fontSize=8.5, leading=11.5, spaceAfter=3, fontName="DejaVu", textColor=SEV_INK["существенно"]),
        "ok": ParagraphStyle("ok", fontSize=8.5, leading=11.5, spaceAfter=3, fontName="DejaVu", textColor=colors.HexColor("#1E6B37")),
        "quote": ParagraphStyle("quote", fontSize=8.5, leading=11.5, leftIndent=8, spaceAfter=4, fontName="DejaVu",
                                textColor=MUTED, borderPadding=(3, 4, 3, 6), backColor=colors.HexColor("#F6F8FA")),
        "bullet": ParagraphStyle("bullet", fontSize=8.8, leading=12, leftIndent=12, bulletIndent=2, spaceAfter=2, **base),
        "cell": ParagraphStyle("cell", fontSize=7.4, leading=9.4, **base),
        "cellb": ParagraphStyle("cellb", fontSize=7.4, leading=9.4, fontName="DejaVu-Bold", textColor=INK),
        "mcell": ParagraphStyle("mcell", fontSize=7.4, leading=9, alignment=TA_CENTER, **base),
        "mhead": ParagraphStyle("mhead", fontSize=6.6, leading=8, alignment=TA_CENTER, fontName="DejaVu-Bold", textColor=INK),
    }


def _inline(text: str) -> str:
    """Экранирует текст и превращает **жирный** и *курсив* в разметку reportlab."""
    t = escape(str(text))
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", t)
    return t


def _grid(t: Table, header: bool = True) -> None:
    style = [("GRID", (0, 0), (-1, -1), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("LEFTPADDING", (0, 0), (-1, -1), 3.5), ("RIGHTPADDING", (0, 0), (-1, -1), 3.5),
             ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5)]
    if header:
        style.append(("BACKGROUND", (0, 0), (-1, 0), HEAD_BG))
    t.setStyle(TableStyle(style))


def render_pdf(blocks: list[tuple], path: Path, footer: str = "") -> None:
    _fonts()
    S = _styles()
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=14 * mm, bottomMargin=15 * mm, title="Отчёт о проверке требований",
                            author="Агент проверки требований")
    W = doc.width
    story = []

    for b in blocks:
        kind = b[0]
        if kind in ("title", "h1", "h3", "p", "note", "ok"):
            story.append(Paragraph(_inline(b[1]), S[kind]))
        elif kind == "quote":
            story.append(Paragraph("«" + _inline(b[1]) + "»", S["quote"]))
        elif kind in ("bullets", "numbers"):
            for n, item in enumerate(b[1], 1):
                story.append(Paragraph(_inline(item), S["bullet"], bulletText="•" if kind == "bullets" else f"{n}."))
            story.append(Spacer(1, 3))
        elif kind == "kv":
            rows = [[Paragraph(_inline(k), S["cellb"]), Paragraph(_inline(v), S["cell"])] for k, v in b[1]]
            t = Table(rows, colWidths=[W * 0.28, W * 0.72])
            _grid(t, header=False)
            t.setStyle(TableStyle([("BACKGROUND", (0, 0), (0, -1), HEAD_BG)]))
            story += [t, Spacer(1, 6)]
        elif kind == "table":
            head, rows, ratios = b[1], b[2], b[3]
            data = [[Paragraph(_inline(h), S["cellb"]) for h in head]]
            data += [[Paragraph(_inline(c), S["cell"]) for c in r] for r in rows]
            t = Table(data, colWidths=[W * r for r in ratios], repeatRows=1)
            _grid(t)
            story += [t, Spacer(1, 6)]
        elif kind == "matrix":
            head, rows = b[1], b[2]
            first = W * 0.1
            other = (W - first) / (len(head) - 1)
            data = [[Paragraph(_inline(h), S["mhead"]) for h in head]]
            data += [[Paragraph(_inline(c), S["mcell"]) for c in r] for r in rows]
            t = Table(data, colWidths=[first] + [other] * (len(head) - 1), repeatRows=1)
            _grid(t)
            extra = []
            for ri, r in enumerate(rows, 1):
                for ci, c in enumerate(r[1:], 1):
                    if c == "✗":
                        extra.append(("BACKGROUND", (ci, ri), (ci, ri), SEV_BG["критично"]))
                    elif c == "?":
                        extra.append(("BACKGROUND", (ci, ri), (ci, ri), SEV_BG["существенно"]))
            t.setStyle(TableStyle(extra + [("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
            story += [t, Spacer(1, 8)]
        elif kind == "defect":
            title, fields = b[1], b[2]
            sev = next((k for k in SEV_BG if k in title), "незначительно")
            ink = "#" + SEV_INK[sev].hexval()[2:]
            data = [[Paragraph(f"<font color='{ink}'>" + _inline(title) + "</font>", S["cellb"]), ""]]
            data += [[Paragraph(_inline(k), S["cellb"]), Paragraph(_inline(v), S["cell"])] for k, v in fields]
            t = Table(data, colWidths=[W * 0.14, W * 0.86])
            t.setStyle(TableStyle([
                ("SPAN", (0, 0), (-1, 0)), ("BACKGROUND", (0, 0), (-1, 0), SEV_BG[sev]),
                ("BOX", (0, 0), (-1, -1), 0.5, LINE), ("LINEBELOW", (0, 0), (-1, -2), 0.25, LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5)]))
            story += [KeepTogether([t]), Spacer(1, 4)]
        elif kind == "verdict":
            bg, ink = VERDICT.get(b[1].split(": ")[-1], ("#EEF2F6", "#1F2328"))
            st = ParagraphStyle("v", parent=S["p"], fontName="DejaVu-Bold", fontSize=11, leading=15, textColor=colors.HexColor(ink))
            t = Table([[Paragraph(_inline(b[1]), st)], [Paragraph("Правило: " + _inline(b[2]) + ".", S["p"])]], colWidths=[W])
            t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(bg)),
                                   ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor(ink)),
                                   ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 5),
                                   ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
            story += [t, Spacer(1, 6)]

    def on_page(canvas, d):
        canvas.saveState()
        canvas.setFont("DejaVu", 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(15 * mm, 8 * mm, footer[:120])
        canvas.drawRightString(A4[0] - 15 * mm, 8 * mm, f"стр. {d.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
