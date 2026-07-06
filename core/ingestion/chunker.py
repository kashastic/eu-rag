"""Paragraph-aware chunking with a word budget and overlap — article-aware
for legal texts.

Legal answers live in articles; a chunk that straddles an article boundary
makes citations ambiguous and buries the answering passage. "Article N"
heading lines are therefore hard chunk boundaries, and every chunk from an
article carries its heading ("Article 37 — Designation of the data protection
officer"), so both lexical search ("article 37") and the reader know exactly
where a passage lives. Text without article structure (recitals, curated
summaries) falls back to plain paragraph chunking.
"""

import re
from dataclasses import dataclass

from core.ingestion.document_loader import Document


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    index: int
    text: str
    title: str
    source_url: str


_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
# a heading is "Article N" alone on its line — inline mentions
# ("pursuant to Article 6 of Regulation …") never match
_ARTICLE_HEADING_RE = re.compile(r"^article\s+\d+\s*[a-z]?$", re.IGNORECASE)


def _looks_like_article_title(line: str) -> bool:
    """The line after an article heading is usually its short title
    ("Definitions"). Prose starts ("1.   The controller shall…" /
    "Member States shall ensure that.") are not titles."""
    return (
        0 < len(line) <= 90
        and not line[0].isdigit()
        and not line[0].islower()
        and not line.startswith("(")
        and not line.endswith(".")
        and not _ARTICLE_HEADING_RE.match(line)
    )


def _split_articles(text: str) -> list[tuple[str, str]]:
    """Split at article headings. Returns (heading, body) sections in order;
    the preamble (title, recitals) is the first section with heading ""."""
    sections: list[tuple[str, str]] = []
    heading, buf = "", []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if _ARTICLE_HEADING_RE.match(stripped):
            sections.append((heading, "\n".join(buf)))
            heading, buf = stripped, []
            # absorb the article's title line into the heading
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and _looks_like_article_title(lines[j].strip()):
                heading = f"{stripped} — {lines[j].strip()}"
                i = j
        else:
            buf.append(lines[i])
        i += 1
    sections.append((heading, "\n".join(buf)))
    return [(h, b) for h, b in sections if b.strip()]


def _split_long_paragraph(paragraph: str, max_words: int) -> list[str]:
    sentences = _SENTENCE_RE.split(paragraph)
    pieces: list[str] = []
    current: list[str] = []
    count = 0
    for sentence in sentences:
        words = len(sentence.split())
        if current and count + words > max_words:
            pieces.append(" ".join(current))
            current, count = [], 0
        current.append(sentence)
        count += words
    if current:
        pieces.append(" ".join(current))
    return pieces


def _chunk_text(text: str, max_words: int, overlap_words: int) -> list[str]:
    paragraphs: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if len(para.split()) > max_words:
            paragraphs.extend(_split_long_paragraph(para, max_words))
        else:
            paragraphs.append(para)

    pieces: list[str] = []
    current: list[str] = []
    count = 0
    for para in paragraphs:
        words = len(para.split())
        if current and count + words > max_words:
            pieces.append("\n\n".join(current))
            # carry a tail of the previous chunk as overlap for continuity
            tail = " ".join("\n\n".join(current).split()[-overlap_words:])
            current, count = [tail], len(tail.split())
        current.append(para)
        count += words
    if current:
        pieces.append("\n\n".join(current))
    return pieces


def chunk_document(
    doc: Document, max_words: int = 320, overlap_words: int = 40
) -> list[Chunk]:
    # 320-word budget: 77% of the corpus's 1,715 articles fit in a single
    # chunk (median 122 words), so the passage that answers a question is
    # usually whole — while staying inside the reranker's 512-token window
    chunks: list[Chunk] = []
    for heading, body in _split_articles(doc.text):
        for piece in _chunk_text(body, max_words, overlap_words):
            text = f"{heading}\n\n{piece}" if heading else piece
            chunks.append(_make_chunk(doc, len(chunks), text))
    return chunks


def _make_chunk(doc: Document, index: int, text: str) -> Chunk:
    return Chunk(
        chunk_id=f"{doc.doc_id}:{index}",
        doc_id=doc.doc_id,
        index=index,
        text=text,
        title=doc.title,
        source_url=doc.source_url,
    )
