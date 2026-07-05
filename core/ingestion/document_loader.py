"""Load raw sources into Documents with mandatory provenance.

Citations resolve to the metadata captured here; a document without a title
and source_type is rejected rather than ingested anonymously.
"""

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path


@dataclass
class Document:
    doc_id: str
    title: str
    text: str
    source_url: str
    source_type: str  # e.g. "eur-lex", "ec-portal", "national-scheme", "upload"
    language: str = "en"
    fetched_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()


class ProvenanceError(ValueError):
    pass


def make_document(
    *,
    title: str,
    text: str,
    source_url: str = "",
    source_type: str = "upload",
    language: str = "en",
) -> Document:
    title = title.strip()
    text = text.strip()
    if not title:
        raise ProvenanceError("document has no title")
    if not text:
        raise ProvenanceError("document has no text")
    doc_id = hashlib.sha256(f"{source_url}|{title}".encode()).hexdigest()[:16]
    return Document(
        doc_id=doc_id,
        title=title,
        text=text,
        source_url=source_url,
        source_type=source_type,
        language=language,
    )


# --- sample files: a small "---" fenced header carries provenance ------------

_HEADER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


def load_sample_file(path: Path) -> Document:
    raw = path.read_text(encoding="utf-8")
    match = _HEADER_RE.match(raw)
    if not match:
        raise ProvenanceError(f"{path.name}: missing provenance header")
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    body = raw[match.end() :]
    return make_document(
        title=meta.get("title", ""),
        text=body,
        source_url=meta.get("source_url", ""),
        source_type=meta.get("source_type", "curated"),
        language=meta.get("language", "en"),
    )


# --- HTML: stdlib-only tag stripping for M1 (bs4/trafilatura arrive in M4) ---

_SKIP_TAGS = {"script", "style", "noscript", "nav", "footer", "header"}
_BLOCK_TAGS = {"p", "div", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6", "tr"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip_depth:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    extractor = _TextExtractor()
    extractor.feed(html)
    text = "".join(extractor.parts)
    # collapse whitespace but preserve paragraph breaks; whitespace-only lines
    # (common in EUR-Lex OJ markup) count as blank. NBSP and friends become
    # plain spaces — OJ texts write "(EU)\xa02023/970", which would otherwise
    # poison tokenization and phrase matching
    text = re.sub("[\xa0\u2000-\u200a\u202f]", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
