"""Paragraph-aware chunking with a word budget and overlap.

Legal texts have strong structure; splitting mid-article makes citations
useless, so paragraphs are kept intact whenever they fit the budget.
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


def chunk_document(
    doc: Document, max_words: int = 220, overlap_words: int = 40
) -> list[Chunk]:
    paragraphs: list[str] = []
    for para in re.split(r"\n\s*\n", doc.text):
        para = para.strip()
        if not para:
            continue
        if len(para.split()) > max_words:
            paragraphs.extend(_split_long_paragraph(para, max_words))
        else:
            paragraphs.append(para)

    chunks: list[Chunk] = []
    current: list[str] = []
    count = 0
    for para in paragraphs:
        words = len(para.split())
        if current and count + words > max_words:
            chunks.append(_make_chunk(doc, len(chunks), "\n\n".join(current)))
            # carry a tail of the previous chunk as overlap for continuity
            tail = " ".join("\n\n".join(current).split()[-overlap_words:])
            current, count = [tail], len(tail.split())
        current.append(para)
        count += words
    if current:
        chunks.append(_make_chunk(doc, len(chunks), "\n\n".join(current)))
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
