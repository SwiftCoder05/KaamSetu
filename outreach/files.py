"""Reading contacts out of uploaded files: CSV, XLSX, XLS, DOCX, DOC, PDF, TXT and images (OCR)."""

import csv
import io
import re
from dataclasses import dataclass, field

from django.conf import settings

from .extract import ContactDraft, extract_emails_from_text, finalize_drafts, is_valid_email

MAX_ROWS = 5000
MAX_PDF_PAGES = 50

EXT_TO_KIND = {
    "csv": "csv", "txt": "txt", "xlsx": "xlsx", "xls": "xls", "docx": "docx", "doc": "doc",
    "pdf": "pdf", "png": "image", "jpg": "image", "jpeg": "image",
}


class FileExtractError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ExtractedFile:
    kind: str
    drafts: list[ContactDraft]
    warnings: list[str] = field(default_factory=list)


def _starts(b: bytes, sig: bytes) -> bool:
    return b[: len(sig)] == sig


def detect_file_kind(filename: str, data: bytes) -> str:
    """Picks the type from the extension and verifies the file's magic bytes agree."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    kind = EXT_TO_KIND.get(ext)
    if not kind:
        raise FileExtractError("unsupported_type", f'".{ext}" files are not supported. Use CSV, TXT, XLSX, XLS, DOCX, DOC, PDF, PNG or JPG.')
    ok = (
        (kind == "pdf" and _starts(data, b"%PDF"))
        or (kind in ("xlsx", "docx") and _starts(data, b"PK\x03\x04"))
        or (kind in ("xls", "doc") and _starts(data, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"))
        or (kind == "image" and (_starts(data, b"\x89PNG") or _starts(data, b"\xff\xd8\xff")))
        or (kind in ("csv", "txt") and b"\x00" not in data[:4096])
    )
    if not ok:
        raise FileExtractError("type_mismatch", f"This file's contents do not look like a real {kind.upper()} file.")
    return kind


# ── tabular ────────────────────────────────────────────────────────────────


def _header_index(header: list[str], patterns: list[str]) -> int:
    for i, h in enumerate(header):
        if any(re.search(p, h) for p in patterns):
            return i
    return -1


def drafts_from_rows(rows: list[list]) -> tuple[list[ContactDraft], list[str]]:
    warnings: list[str] = []
    table = [[("" if c is None else str(c)).strip() for c in r] for r in rows]
    table = [r for r in table if any(r)]
    if not table:
        return [], warnings
    if len(table) > MAX_ROWS:
        warnings.append(f"Only the first {MAX_ROWS} rows were read.")
        table = table[:MAX_ROWS]
    first = [h.lower() for h in table[0]]
    email_col = _header_index(first, [r"e-?mail", r"^mail$"])
    has_header = email_col >= 0
    name_col = _header_index(first, [r"^(full[\s_-]*)?name$", r"contact|recruiter|person"]) if has_header else -1
    company_col = _header_index(first, [r"company|organi[sz]ation|employer|firm"]) if has_header else -1
    role_col = _header_index(first, [r"^(job[\s_-]*)?(title|role|position)$", r"designation"]) if has_header else -1
    body = table[1:] if has_header else table

    def cell(r, i):
        return r[i] if 0 <= i < len(r) else ""

    drafts: list[ContactDraft] = []
    for r in body:
        cells = [cell(r, email_col)] if has_header else r
        found = [d.email for c in cells for d in extract_emails_from_text(c)]
        if has_header and cells[0] and not found:
            drafts.append(ContactDraft(cells[0], status="invalid", reason="Not a valid email address"))
            continue
        for email in found:
            drafts.append(
                ContactDraft(
                    email,
                    name=cell(r, name_col)[:120],
                    company=cell(r, company_col)[:120],
                    role_title=cell(r, role_col)[:120],
                    status="valid" if is_valid_email(email) else "invalid",
                )
            )
    if not has_header:
        warnings.append("No header row with an 'email' column was found, so every cell was scanned for addresses.")
    return drafts, warnings


def _decode_text(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _parse_csv(data: bytes):
    text = _decode_text(data)
    if not text.strip():
        raise FileExtractError("empty", "The CSV file is empty.")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = []
    for i, row in enumerate(csv.reader(io.StringIO(text), dialect)):
        if i > MAX_ROWS:
            break
        rows.append(row)
    return drafts_from_rows(rows)


def _parse_xlsx(data: bytes):
    import openpyxl

    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:  # noqa: BLE001
        raise FileExtractError("corrupt", "The spreadsheet could not be read. It may be corrupted or password protected.") from e
    drafts, warnings = [], []
    try:
        for ws in wb.worksheets[:10]:
            rows = []
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i > MAX_ROWS:
                    break
                rows.append(list(row))
            d, w = drafts_from_rows(rows)
            drafts += d
            warnings += [f"{ws.title}: {x}" for x in w]
    finally:
        wb.close()
    return drafts, warnings


def _parse_xls(data: bytes):
    import xlrd

    try:
        book = xlrd.open_workbook(file_contents=data)
    except Exception as e:  # noqa: BLE001
        raise FileExtractError("corrupt", "The XLS file could not be read. It may be corrupted or password protected.") from e
    drafts, warnings = [], []
    for sh in book.sheets()[:10]:
        rows = [sh.row_values(i) for i in range(min(sh.nrows, MAX_ROWS + 1))]
        d, w = drafts_from_rows(rows)
        drafts += d
        warnings += [f"{sh.name}: {x}" for x in w]
    return drafts, warnings


# ── documents ──────────────────────────────────────────────────────────────


def text_from_docx(data: bytes) -> str:
    import docx

    try:
        d = docx.Document(io.BytesIO(data))
    except Exception as e:  # noqa: BLE001
        raise FileExtractError("corrupt", "The DOCX file could not be read. It may be corrupted, password protected, or not a Word document.") from e
    parts = [p.text for p in d.paragraphs]
    for t in d.tables:
        for row in t.rows:
            parts.append(" | ".join(c.text for c in row.cells))
    for sec in d.sections:
        parts += [p.text for p in sec.header.paragraphs] + [p.text for p in sec.footer.paragraphs]
    # mailto: hyperlinks whose display text is not the address
    for rel in d.part.rels.values():
        target = getattr(rel, "_target", "")
        if isinstance(target, str) and target.lower().startswith("mailto:"):
            parts.append(target[7:].split("?")[0])
    return "\n".join(parts)


def text_from_doc(data: bytes) -> str:
    """Word 97-2003. Full layout parsing is out of scope; the text stream is decoded well enough to find
    addresses and plain wording. For resume import, saving as DOCX gives better results."""
    import olefile

    try:
        ole = olefile.OleFileIO(io.BytesIO(data))
        raw = ole.openstream("WordDocument").read()
        ole.close()
    except Exception as e:  # noqa: BLE001
        raise FileExtractError("corrupt", "The DOC file could not be read. It may be corrupted, password protected, or not a Word 97-2003 document.") from e
    utf16 = raw.decode("utf-16-le", errors="ignore")
    latin = raw.decode("latin-1", errors="ignore")
    keep = lambda s: re.sub(r"[^\x09\x0a\x0d\x20-\x7e -ɏ‐-‧]+", "\n", s)  # noqa: E731
    return keep(latin) + "\n" + keep(utf16)


def text_from_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise FileExtractError("corrupt", "This PDF is password protected.")
        texts, links = [], []
        for page in reader.pages[:MAX_PDF_PAGES]:
            texts.append(page.extract_text() or "")
            for annot in page.get("/Annots") or []:
                try:
                    uri = annot.get_object().get("/A", {}).get("/URI", "")
                except Exception:  # noqa: BLE001
                    uri = ""
                if isinstance(uri, str) and uri.lower().startswith("mailto:"):
                    links.append(uri[7:].split("?")[0])
    except FileExtractError:
        raise
    except Exception as e:  # noqa: BLE001
        raise FileExtractError("corrupt", "The PDF could not be read. It may be corrupted or password protected.") from e
    text = "\n".join(texts)
    if not text.strip() and not links:
        raise FileExtractError(
            "no_text_layer",
            "This PDF has no selectable text (it looks scanned). Export the pages as PNG or JPG and upload those instead.",
        )
    return text + "\n" + "\n".join(links)


def text_from_image(data: bytes) -> str:
    """Local OCR with Tesseract. Needs the tesseract program installed (see README)."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError as e:
        raise FileExtractError("ocr_failed", "OCR libraries are not installed on the server.") from e
    if settings.TESSERACT_CMD:
        pytesseract.pytesseract.tesseract_cmd = settings.TESSERACT_CMD
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:  # noqa: BLE001
        raise FileExtractError("corrupt", "The image could not be opened.") from e
    try:
        return pytesseract.image_to_string(img.convert("L"), timeout=60)
    except pytesseract.TesseractNotFoundError as e:
        raise FileExtractError(
            "ocr_failed",
            "Reading emails from images needs Tesseract OCR, which is not installed on this server. "
            "Install it (see README) or paste the addresses instead.",
        ) from e
    except Exception as e:  # noqa: BLE001
        raise FileExtractError("ocr_failed", "Text could not be read from this image. Make sure it is a clear PNG or JPG.") from e


def extract_document_text(kind: str, data: bytes) -> str:
    return {"docx": text_from_docx, "doc": text_from_doc, "pdf": text_from_pdf, "txt": _decode_text}[kind](data)


# ── entry point ────────────────────────────────────────────────────────────


def extract_contacts_from_file(filename: str, data: bytes, existing=()) -> ExtractedFile:
    if not data:
        raise FileExtractError("empty", "The file is empty.")
    if len(data) > settings.MAX_UPLOAD_BYTES:
        raise FileExtractError("too_large", f"The file is larger than {settings.MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    kind = detect_file_kind(filename, data)
    warnings: list[str] = []
    if kind in ("csv", "xlsx", "xls"):
        drafts, warnings = {"csv": _parse_csv, "xlsx": _parse_xlsx, "xls": _parse_xls}[kind](data)
    else:
        text = text_from_image(data) if kind == "image" else extract_document_text(kind, data)
        drafts = extract_emails_from_text(text)
        if kind == "image" and drafts:
            warnings.append("OCR can misread characters. Check every address before importing.")
    if not drafts:
        raise FileExtractError("no_emails", "No email addresses were found in this file.")
    return ExtractedFile(kind, finalize_drafts(drafts, existing), warnings)
