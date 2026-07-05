"""In-memory Okapi BM25 over chunk tokens.

Lexical matching is load-bearing in this domain: regulation numbers
("2016/679"), article references, and scheme names are exact strings that
semantic search reliably fumbles.
"""

import math
import re
from collections import Counter

_TOKEN_RE = re.compile(r"[a-zà-öø-ÿ0-9/§.-]+", re.IGNORECASE)


def tokenize(text: str) -> list[str]:
    return [t.strip(".-") for t in _TOKEN_RE.findall(text.lower()) if t.strip(".-")]


class BM25Index:
    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self._doc_tokens: dict[str, Counter] = {}
        self._doc_len: dict[str, int] = {}
        self._df: Counter = Counter()
        self._avg_len = 0.0

    def add(self, chunk_id: str, text: str) -> None:
        tokens = Counter(tokenize(text))
        if chunk_id in self._doc_tokens:
            self.remove(chunk_id)
        self._doc_tokens[chunk_id] = tokens
        self._doc_len[chunk_id] = sum(tokens.values())
        for term in tokens:
            self._df[term] += 1
        self._avg_len = sum(self._doc_len.values()) / len(self._doc_len)

    def remove(self, chunk_id: str) -> None:
        tokens = self._doc_tokens.pop(chunk_id, None)
        if tokens is None:
            return
        self._doc_len.pop(chunk_id)
        for term in tokens:
            self._df[term] -= 1
            if self._df[term] <= 0:
                del self._df[term]
        if self._doc_len:
            self._avg_len = sum(self._doc_len.values()) / len(self._doc_len)

    def search(self, query: str, k: int = 10) -> list[tuple[str, float]]:
        if not self._doc_tokens:
            return []
        n = len(self._doc_tokens)
        scores: dict[str, float] = {}
        for term in tokenize(query):
            df = self._df.get(term)
            if not df:
                continue
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            for chunk_id, tokens in self._doc_tokens.items():
                tf = tokens.get(term)
                if not tf:
                    continue
                denom = tf + self.k1 * (
                    1 - self.b + self.b * self._doc_len[chunk_id] / self._avg_len
                )
                scores[chunk_id] = scores.get(chunk_id, 0.0) + idf * tf * (self.k1 + 1) / denom
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        return ranked[:k]
