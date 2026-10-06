"""Clause-aware chunking: keeps section numbers and pages, which citations and clause-level supersession need."""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.ingestion.parsers import TABLE_MARK, Parsed

MAX_WORDS = 220          # bge-small reads 512 tokens; leave room for the contextual header
TABLE_ROWS = 18

# "7.2 Minimum attendance", "7. ATTENDANCE", "8.4. Procedure", "Clause 3 ...", "1) ..."
HEADING = re.compile(
    r"^\s*(?:(?:section|clause|rule|regulation|article|para(?:graph)?)\s+(?P<w>\d{1,2}(?:\.\d{1,2}){0,3})[.):]?"
    r"|(?P<m>\d{1,2}(?:\.\d{1,2}){1,3})\.?"
    r"|(?P<s>\d{1,2})[.)])\s+(?P<rest>\S.{0,300})$",
    re.IGNORECASE,
)
SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\"'])")


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    section: str | None
    section_title: str
    page_start: int
    page_end: int
    text: str
    is_table: bool = False
    ocr: bool = False
    flagged: bool = False

    def embed_text(self, doc_title: str) -> str:
        where = f"§{self.section} {self.section_title}".strip() if self.section else self.section_title
        return f"{doc_title} ({self.doc_id}) {where}\n{self.text}"


def _title(rest: str) -> str:
    first = re.split(r"(?<=[.:;])\s", rest.strip(), maxsplit=1)[0].rstrip(".:;")
    words = first.split()
    return " ".join(words[:10]) + ("…" if len(words) > 10 else "")


def _split(text: str) -> list[str]:
    words = text.split()
    if len(words) <= MAX_WORDS:
        return [text.strip()] if text.strip() else []
    sents = SENT.split(" ".join(words))
    chunks, cur = [], []
    for s in sents:
        if cur and len(" ".join(cur + [s]).split()) > MAX_WORDS:
            chunks.append(" ".join(cur))
            cur = [cur[-1]] if len(cur[-1].split()) < MAX_WORDS // 3 else []   # one-sentence overlap
        cur.append(s)
    if cur:
        chunks.append(" ".join(cur))
    return chunks


def chunk_fixed(doc_id: str, parsed: Parsed, size: int = 800, overlap: int = 100) -> list[Chunk]:
    """Baseline for the evaluation comparison (config A): fixed character windows, no clause structure."""
    chunks: list[Chunk] = []
    for page in parsed.pages:
        text = " ".join(page.text.replace(TABLE_MARK, " ").split())
        for n, i in enumerate(range(0, max(1, len(text)), size - overlap)):
            piece = text[i:i + size].strip()
            if piece:
                chunks.append(Chunk(f"{doc_id}::p{page.number}::{n}", doc_id, None, "", page.number, page.number, piece, ocr=page.ocr))
    for ti, t in enumerate(parsed.tables):
        body = "\n".join("| " + " | ".join(r) + " |" for r in [t.header] + t.rows)
        chunks.append(Chunk(f"{doc_id}::t{ti}::0", doc_id, None, "table", t.page, t.page, body, is_table=True))
    return chunks


def chunk_document(doc_id: str, parsed: Parsed, mode: str = "clause-v1") -> list[Chunk]:
    if mode.startswith("fixed"):
        return chunk_fixed(doc_id, parsed)
    sections: list[dict] = []
    cur = {"section": None, "title": "", "lines": [], "p0": 1, "p1": 1, "ocr": False}
    last_section_on_page: dict[int, tuple[str | None, str]] = {}
    table_section: dict[int, tuple[str | None, str]] = {}

    def flush():
        if any(l.strip() for l in cur["lines"]):
            sections.append(dict(cur))

    for page in parsed.pages:
        for raw in page.text.splitlines():
            line = raw.strip()
            if line.startswith(TABLE_MARK):
                table_section[int(line[len(TABLE_MARK):])] = (cur["section"], cur["title"])
                continue
            if not line:
                cur["lines"].append("")
                continue
            m = HEADING.match(line)
            if m:
                flush()
                num = m.group("w") or m.group("m") or m.group("s")
                cur = {"section": num, "title": _title(m.group("rest")), "lines": [line], "p0": page.number,
                       "p1": page.number, "ocr": page.ocr}
            else:
                cur["lines"].append(line)
                cur["p1"] = page.number
                cur["ocr"] = cur["ocr"] or page.ocr
            last_section_on_page[page.number] = (cur["section"], cur["title"])
    flush()

    chunks: list[Chunk] = []
    counters: dict[str, int] = {}

    def add(section, title, p0, p1, text, is_table=False, ocr=False):
        key = section or f"p{p0}"
        n = counters.get(key, 0)
        counters[key] = n + 1
        chunks.append(Chunk(f"{doc_id}::{key}::{n}", doc_id, section, title, p0, p1, text, is_table, ocr))

    for s in sections:
        body = re.sub(r"\n{2,}", "\n", "\n".join(s["lines"])).strip()
        body = re.sub(r"(?<![.:;])\n(?=[a-z(])", " ", body)          # re-join wrapped lines
        for piece in _split(body):
            add(s["section"], s["title"], s["p0"], s["p1"], piece, ocr=s["ocr"])

    for ti, t in enumerate(parsed.tables):
        section, title = table_section.get(ti) or last_section_on_page.get(t.page, (None, ""))
        head = "| " + " | ".join(t.header) + " |"
        for i in range(0, len(t.rows), TABLE_ROWS):
            body = "\n".join("| " + " | ".join(r) + " |" for r in t.rows[i:i + TABLE_ROWS])
            add(section, title or "table", t.page, t.page, f"{head}\n{body}", is_table=True)
    return chunks
