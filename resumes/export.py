"""DOCX and PDF export of a structured resume (single column, ATS-friendly)."""

import io
import os
import re

from django.conf import settings

from .schema import SECTION_LABELS, ResumeData


def _dates(start: str, end: str, current: bool = False) -> str:
    end_text = "Present" if current else end
    return " – ".join(x for x in (start, end_text) if x)


def _contact_line(r: ResumeData) -> str:
    b = r.basics
    return "  |  ".join(x for x in [b.email, b.phone, b.location, *[ln.url for ln in b.links]] if x)


def _sections(r: ResumeData):
    """Yields (label, rows) in the user's order. Each row: (title, right, sub, bullets, paragraph)."""
    for key in r.section_order:
        rows = []
        if key == "summary" and r.summary.strip():
            rows.append(("", "", "", [], r.summary.strip()))
        elif key == "skills":
            for g in r.skills:
                if g.items:
                    rows.append(("", "", "", [], (f"{g.category}: " if g.category else "") + ", ".join(g.items)))
        elif key == "experience":
            for e in r.experience:
                title = " — ".join(x for x in (e.title, e.company) if x)
                rows.append((title, _dates(e.start, e.end, e.current), e.location, [b for b in e.bullets if b.strip()], ""))
        elif key == "projects":
            for p in r.projects:
                rows.append((p.name, p.url, ", ".join(p.tech), [b for b in p.bullets if b.strip()], ""))
        elif key == "education":
            for e in r.education:
                rows.append((e.school, _dates(e.start, e.end), e.degree, [], e.details))
        elif key == "certifications":
            for c in r.certifications:
                rows.append((" — ".join(x for x in (c.name, c.issuer) if x), c.year, "", [], ""))
        if rows:
            yield SECTION_LABELS[key], rows
    if r.additional.strip():
        yield "Additional", [("", "", "", [], r.additional.strip())]


def to_docx(r: ResumeData) -> bytes:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    d = fit_one_page(r)[0]  # same spacing level the PDF needed to stay on one page
    k = d["scale"]
    style.font.size = Pt(10.5 * k)
    style.paragraph_format.space_after = Pt(2 * d["gap"])
    for s in doc.sections:
        s.left_margin = s.right_margin = Pt(d["side"])
        s.top_margin = s.bottom_margin = Pt(d["top"])

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(r.basics.name or "Your Name")
    run.bold = True
    run.font.size = Pt(18 * k)
    if r.basics.headline:
        h = doc.add_paragraph(r.basics.headline)
        h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if line := _contact_line(r):
        c = doc.add_paragraph(line)
        c.alignment = WD_ALIGN_PARAGRAPH.CENTER

    for label, rows in _sections(r):
        hp = doc.add_paragraph()
        hr = hp.add_run(label.upper())
        hr.bold = True
        hr.font.size = Pt(11 * k)
        hp.paragraph_format.space_before = Pt(10 * d["gap"])
        for title, right, sub, bullets, para in rows:
            if title or right:
                tp = doc.add_paragraph()
                tr = tp.add_run(title)
                tr.bold = True
                if right:
                    tp.add_run(f"    {right}")
                tp.paragraph_format.space_after = Pt(0)
            if sub:
                sp = doc.add_paragraph()
                sp.add_run(sub).italic = True
                sp.paragraph_format.space_after = Pt(0)
            for b in bullets:
                doc.add_paragraph(b, style="List Bullet")
            if para:
                doc.add_paragraph(para)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


_FONT_CANDIDATES = [
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]


def _unicode_font() -> tuple[str, str] | None:
    """Returns (regular, bold) TTF paths for full Unicode output, or None to fall back to Helvetica."""
    for path in [settings.PDF_FONT_PATH, *_FONT_CANDIDATES]:
        if path and os.path.exists(path):
            bold = re.sub(r"(?i)arial\.ttf$", "arialbd.ttf", path).replace("DejaVuSans.ttf", "DejaVuSans-Bold.ttf")
            return path, bold if os.path.exists(bold) and bold != path else path
    return None


_LATIN1_MAP = {"\u2013": "-", "\u2014": "-", "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2022": "-", "\u2026": "...", "\u00a0": " "}


def _render_pdf(r: ResumeData, d: dict) -> tuple[bytes, int]:
    from fpdf import FPDF

    pdf = FPDF(format="A4", unit="pt")
    k = d["scale"]
    pdf.set_margins(d["side"], d["top"], d["side"])
    pdf.set_auto_page_break(True, d["top"])
    pdf.add_page()

    font = _unicode_font()
    if font:
        pdf.add_font("Body", "", font[0])
        pdf.add_font("Body", "B", font[1])
        family = "Body"
        clean = lambda s: s  # noqa: E731
    else:
        family = "Helvetica"

        def clean(s: str) -> str:
            s = "".join(_LATIN1_MAP.get(ch, ch) for ch in s)
            return s.encode("latin-1", "replace").decode("latin-1")

    width = pdf.w - pdf.l_margin - pdf.r_margin

    def text(s: str, size: float = 10.5, style: str = "", align: str = "L", gap: float = 2):
        size *= k
        pdf.set_font(family, style, size)
        pdf.multi_cell(width, size * d["leading"], clean(s), align=align, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(gap * d["gap"])

    text(r.basics.name or "Your Name", 18, "B", "C", 2)
    if r.basics.headline:
        text(r.basics.headline, 11, "", "C", 0)
    if line := _contact_line(r):
        text(line, 9, "", "C", 4)

    for label, rows in _sections(r):
        pdf.ln(6 * d["gap"])
        text(label.upper(), 10.5, "B", gap=1)
        y = pdf.get_y()
        pdf.set_draw_color(140, 140, 140)
        pdf.line(pdf.l_margin, y, pdf.l_margin + width, y)
        pdf.ln(4 * d["gap"])
        for title, right, sub, bullets, para in rows:
            if title or right:
                pdf.set_font(family, "B", 10.5 * k)
                right_w = pdf.get_string_width(clean(right)) + 4 if right else 0
                pdf.multi_cell(width - right_w, 14 * k, clean(title), new_x="RIGHT", new_y="TOP")
                if right:
                    pdf.set_font(family, "", 9.5 * k)
                    pdf.set_x(pdf.l_margin + width - right_w)
                    pdf.cell(right_w, 14 * k, clean(right), align="R")
                pdf.ln(14 * k)
            if sub:
                text(sub, 9.5, "", gap=1)
            for b in bullets:
                pdf.set_font(family, "", 10 * k)
                pdf.set_x(pdf.l_margin + 6)
                pdf.cell(10, 13.5 * k, "-" if not font else "•")
                pdf.multi_cell(width - 16, 13.5 * k, clean(b), new_x="LMARGIN", new_y="NEXT")
            if para:
                text(para, 10, gap=2)
            pdf.ln(3 * d["gap"])

    return bytes(pdf.output()), pdf.page_no()


# Spacing levels tried in order until the resume fits on one page (a recruiter-friendly resume is one page).
DENSITIES = [
    {"name": "normal", "scale": 1.0, "leading": 1.35, "gap": 1.0, "side": 48, "top": 44},
    {"name": "compact", "scale": 0.94, "leading": 1.28, "gap": 0.75, "side": 40, "top": 34},
    {"name": "tight", "scale": 0.88, "leading": 1.22, "gap": 0.55, "side": 34, "top": 28},
    {"name": "tightest", "scale": 0.82, "leading": 1.18, "gap": 0.4, "side": 30, "top": 24},
]


def fit_one_page(r: ResumeData) -> tuple[dict, bytes, int]:
    """Returns (density, pdf, pages) for the roomiest layout that fits on one page (or the tightest if none does)."""
    for d in DENSITIES:
        data, pages = _render_pdf(r, d)
        if pages <= 1:
            return d, data, pages
    return d, data, pages


def page_count(r: ResumeData) -> int:
    return fit_one_page(r)[2]


def to_pdf(r: ResumeData) -> bytes:
    return fit_one_page(r)[1]


def safe_filename(name: str, ext: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "resume"
    return f"{base[:80]}.{ext}"
