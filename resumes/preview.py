"""Shows an imported resume exactly as the user's file looks.

- PDF: pages are rendered to images with PDFium (the engine Chrome uses), so fonts, bold and layout match.
- DOCX/DOC: converted to PDF once at import with LibreOffice if installed, otherwise Microsoft Word (Windows).
  Conversion runs in a separate process with a time limit, so a stuck converter can never hang the web server.
- If no converter exists (e.g. a Linux server without LibreOffice), DOCX falls back to HTML that keeps
  bold, italics, headings, lists and tables, though not the exact page layout.
"""

import io
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CONVERT_TIMEOUT = 90  # seconds


def _soffice() -> str | None:
    for c in ("soffice", "libreoffice", r"C:\Program Files\LibreOffice\program\soffice.exe", r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"):
        found = shutil.which(c) or (c if os.path.isfile(c) else None)
        if found:
            return found
    return None


def _word_available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg

        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "Word.Application"))
        import win32com.client  # noqa: F401  (pywin32)
        return True
    except Exception:  # noqa: BLE001
        return False


_WORD_SCRIPT = r"""
import sys, pythoncom, win32com.client
src, out = sys.argv[1], sys.argv[2]
pythoncom.CoInitialize()
word = win32com.client.DispatchEx("Word.Application")
try:
    word.Visible = False
    word.DisplayAlerts = 0
    word.AutomationSecurity = 3  # never run macros in uploaded documents
    doc = word.Documents.Open(src, ConfirmConversions=False, ReadOnly=True, AddToRecentFiles=False, Visible=False, OpenAndRepair=False, NoEncodingDialog=True)
    try:
        doc.ExportAsFixedFormat(out, 17)  # 17 = PDF
    finally:
        doc.Close(False)
finally:
    word.Quit()
"""


def convert_to_pdf(kind: str, raw: bytes) -> bytes | None:
    """Word file -> PDF bytes, or None if no converter is available or it failed."""
    if kind not in ("docx", "doc"):
        return None
    with tempfile.TemporaryDirectory(prefix="kaam-") as tmp:
        src = Path(tmp) / f"resume.{kind}"
        src.write_bytes(raw)
        out = Path(tmp) / "resume.pdf"
        soffice = _soffice()
        try:
            if soffice:
                subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", tmp, str(src)],
                               capture_output=True, timeout=CONVERT_TIMEOUT, check=False)
            elif _word_available():
                subprocess.run([sys.executable, "-c", _WORD_SCRIPT, str(src), str(out)],
                               capture_output=True, timeout=CONVERT_TIMEOUT, check=False)
            else:
                return None
        except (subprocess.TimeoutExpired, OSError):
            return None
        if out.exists() and out.read_bytes().startswith(b"%PDF"):
            return out.read_bytes()
    return None


def docx_to_html(raw: bytes) -> str:
    """Fallback view for DOCX when no converter exists: keeps bold, italics, headings, lists and tables."""
    import mammoth

    from outreach.richtext import sanitize_html

    try:
        result = mammoth.convert_to_html(io.BytesIO(raw))
    except Exception:  # noqa: BLE001
        return ""
    return sanitize_html(result.value)


def page_count(pdf: bytes) -> int:
    import pypdfium2 as pdfium

    try:
        doc = pdfium.PdfDocument(pdf)
    except Exception:  # noqa: BLE001
        return 0
    try:
        return min(len(doc), 20)
    finally:
        doc.close()


def render_page_png(pdf: bytes, index: int, scale: float = 2.0) -> bytes | None:
    """One page as a PNG. scale 2 is about 144 dpi: sharp on normal and high-density screens."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf)
    try:
        if not 0 <= index < len(doc):
            return None
        bitmap = doc[index].render(scale=scale)
        buf = io.BytesIO()
        bitmap.to_pil().save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    finally:
        doc.close()
