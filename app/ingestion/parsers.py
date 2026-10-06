"""Turn an uploaded file into page texts and tables. OCR is used only when a page has no text layer."""
from __future__ import annotations

import io
import shutil
import unicodedata
from dataclasses import dataclass, field


@dataclass
class Page:
    number: int
    text: str
    ocr: bool = False


@dataclass
class Table:
    page: int
    header: list[str]
    rows: list[list[str]]


@dataclass
class Parsed:
    pages: list[Page] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


TABLE_MARK = "@@TABLE:"
SUPPORTED = {".pdf", ".docx", ".txt", ".md", ".html", ".htm", ".png", ".jpg", ".jpeg"}


def ocr_available() -> bool:
    if shutil.which("tesseract") is None:
        return False
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return False
    return True


def _ocr_image(img) -> str:
    import pytesseract
    return pytesseract.image_to_string(img, lang="eng")


def _clean(cell) -> str:
    return " ".join(str(cell or "").split())


def parse_pdf(data: bytes) -> Parsed:
    import pymupdf as fitz

    out = Parsed()
    doc = fitz.open(stream=data, filetype="pdf")
    can_ocr = ocr_available()
    for i, page in enumerate(doc, start=1):
        boxes = []
        markers: list[tuple[float, str]] = []
        try:
            for t in page.find_tables().tables:
                grid = [[_clean(c) for c in row] for row in t.extract()]
                grid = [r for r in grid if any(r)]
                if _is_register_table(grid):            # the document's own metadata header, not policy content
                    boxes.append(fitz.Rect(t.bbox))
                    continue
                if len(grid) >= 2:
                    markers.append((t.bbox[1], f"{TABLE_MARK}{len(out.tables)}"))
                    out.tables.append(Table(i, grid[0], grid[1:]))
                    boxes.append(fitz.Rect(t.bbox))
        except Exception as e:  # table detection is best-effort
            out.warnings.append(f"page {i}: table detection failed ({e.__class__.__name__})")
        blocks = [b for b in page.get_text("blocks") if b[6] == 0 and not any(fitz.Rect(b[:4]).intersects(bx) for bx in boxes)]
        items = [(b[1], b[4].strip()) for b in blocks] + markers
        text = "\n".join(t for _, t in sorted(items, key=lambda x: x[0]))
        if len(text.strip()) < 40 and not boxes:
            if can_ocr:
                from PIL import Image
                pix = page.get_pixmap(dpi=300)
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                out.pages.append(Page(i, _ocr_image(img), ocr=True))
                continue
            out.warnings.append(f"page {i} has no text layer and OCR is unavailable (install tesseract)")
        # a page that mixes printed text with a scanned image (a stamped notice, a pasted scan): read the image too
        area = abs(page.rect)
        scans = [fitz.Rect(b["bbox"]) for b in page.get_text("dict")["blocks"]
                 if b.get("type") == 1 and abs(fitz.Rect(b["bbox"])) >= 0.12 * area]
        ocr_used = False
        for bbox in scans:
            if not can_ocr:
                out.warnings.append(f"page {i} contains a scanned image whose text was not read: OCR is unavailable "
                                    f"(install tesseract)")
                break
            from PIL import Image
            img = Image.open(io.BytesIO(page.get_pixmap(dpi=300, clip=bbox).tobytes("png")))
            scanned = _ocr_image(img).strip()
            if scanned:
                items.append((bbox.y0, scanned))
                ocr_used = True
        if ocr_used:
            text = "\n".join(t for _, t in sorted(items, key=lambda x: x[0]))
        out.pages.append(Page(i, text, ocr=ocr_used))
    return out


REGISTER_LABELS = {"document id", "authority level", "document type", "effective from", "effective to", "scope",
                   "supersedes", "issuer", "doc id", "version"}


def _is_register_table(grid: list[list[str]]) -> bool:
    """A two-column 'Document ID | ACAD-2026-08' header table: Annex B metadata, which the register already holds."""
    labels = {row[0].strip().lower() for row in grid if row and row[0]}
    return len(labels & REGISTER_LABELS) >= 4


def parse_docx(data: bytes) -> Parsed:
    import docx

    d = docx.Document(io.BytesIO(data))
    out = Parsed()
    out.pages.append(Page(1, "\n".join(p.text for p in d.paragraphs if p.text.strip())))
    for t in d.tables:
        grid = [[_clean(c.text) for c in row.cells] for row in t.rows]
        if len(grid) >= 2:
            out.tables.append(Table(1, grid[0], grid[1:]))
    return out


def parse_html(data: bytes) -> Parsed:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(data, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    out = Parsed()
    for t in soup.find_all("table"):
        grid = [[_clean(c.get_text(" ")) for c in tr.find_all(["th", "td"])] for tr in t.find_all("tr")]
        grid = [r for r in grid if any(r)]
        if len(grid) >= 2:
            out.tables.append(Table(1, grid[0], grid[1:]))
        t.decompose()
    out.pages.append(Page(1, soup.get_text("\n")))
    return out


def parse_image(data: bytes) -> Parsed:
    out = Parsed()
    if not ocr_available():
        out.warnings.append("image upload needs OCR, which is unavailable (install tesseract)")
        out.pages.append(Page(1, ""))
        return out
    from PIL import Image
    out.pages.append(Page(1, _ocr_image(Image.open(io.BytesIO(data))), ocr=True))
    return out


def _normalise(text: str) -> str:
    """NFKC folds typographic ligatures (ﬁ ﬂ ﬀ ﬃ), full-width characters and non-breaking spaces that PDF exports
    contain, so 'eﬀect' matches 'effect' in BM25, claim extraction and the prompt. Soft hyphens are dropped."""
    return unicodedata.normalize("NFKC", text).replace("\u00ad", "")


def parse(data: bytes, ext: str) -> Parsed:
    ext = ext.lower()
    if ext == ".pdf":
        out = parse_pdf(data)
    elif ext == ".docx":
        out = parse_docx(data)
    elif ext in (".html", ".htm"):
        out = parse_html(data)
    elif ext in (".png", ".jpg", ".jpeg"):
        out = parse_image(data)
    else:
        out = Parsed(pages=[Page(1, data.decode("utf-8", errors="replace"))])
    for pg in out.pages:
        pg.text = _normalise(pg.text)
    for t in out.tables:
        t.header = [_normalise(h) for h in t.header]
        t.rows = [[_normalise(c) for c in row] for row in t.rows]
    return out
