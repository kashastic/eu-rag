# EURAG Wiki

The single entry point for project documentation. Everything here is version-
controlled with the code it describes.

| Page | Contents |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | System design: pipeline stages, module boundaries, key decisions and why |
| [PROJECT_PLAN.md](PROJECT_PLAN.md) | Milestones 1–6, what "done" means for each, current status |
| [DATA_SOURCES.md](DATA_SOURCES.md) | Registry of corpus sources, licensing notes, scraper status |
| [SECURITY.md](SECURITY.md) | Security & GDPR model: what is enforced today vs. designed-for |
| [DEVLOG.md](DEVLOG.md) | Running log of build sessions: what changed, what's next |

## Orientation in 60 seconds

EURAG answers EU compliance/funding questions for SMEs. A question flows:

```
question → hybrid retrieval (BM25 + vectors, RRF fusion)
         → top-k chunks
         → generator (Claude, or extractive fallback)
         → answer with [N] citations → each [N] resolves to a source chunk
```

Documents flow in the other direction:

```
source (EUR-Lex, EC portal, national schemes, upload)
  → loader (HTML/PDF/text) → chunker → embedder → vector store
                                     → BM25 index
```

The load-bearing product rule: **no uncited claims**. The citation schema is
defined in `core/generation/citations.py` and enforced end-to-end.
