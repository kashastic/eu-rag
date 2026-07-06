from core.ingestion.chunker import chunk_document
from core.ingestion.document_loader import make_document


def make_doc(text: str):
    return make_document(title="Test doc", text=text, source_type="upload")


def test_short_document_is_one_chunk():
    doc = make_doc("One short paragraph.\n\nAnother short paragraph.")
    chunks = chunk_document(doc)
    assert len(chunks) == 1
    assert "One short paragraph." in chunks[0].text
    assert chunks[0].chunk_id == f"{doc.doc_id}:0"


def test_word_budget_respected():
    para = " ".join(f"word{i}" for i in range(100))
    doc = make_doc("\n\n".join([para] * 6))
    chunks = chunk_document(doc, max_words=220, overlap_words=40)
    assert len(chunks) > 1
    for chunk in chunks:
        # budget + one paragraph of slack + overlap tail
        assert len(chunk.text.split()) <= 220 + 100 + 40


def test_overlap_carries_context_between_chunks():
    para = " ".join(f"word{i}" for i in range(100))
    doc = make_doc("\n\n".join([para] * 6))
    chunks = chunk_document(doc, max_words=220, overlap_words=40)
    first_tail = " ".join(chunks[0].text.split()[-5:])
    assert first_tail in chunks[1].text


def test_very_long_paragraph_is_split_on_sentences():
    sentences = " ".join(
        f"This is sentence number {i} with several extra words in it." for i in range(80)
    )
    doc = make_doc(sentences)
    chunks = chunk_document(doc, max_words=220)
    assert len(chunks) > 1


def test_article_headings_are_hard_boundaries():
    doc = make_doc(
        "Recital text before any article.\n\n"
        "Article 1\n\nScope\n\nThis law applies to widgets.\n\n"
        "Article 2\n\nDefinitions\n\n'Widget' means a thing.\n\n"
        "pursuant to Article 3 of Regulation X, inline mentions do not split."
    )
    chunks = chunk_document(doc)
    art1 = [c for c in chunks if c.text.startswith("Article 1 — Scope")]
    art2 = [c for c in chunks if c.text.startswith("Article 2 — Definitions")]
    assert art1 and art2
    assert "'Widget' means" not in art1[0].text  # no straddling
    assert "inline mentions do not split" in art2[0].text


def test_every_article_chunk_carries_its_heading():
    long_body = "\n\n".join(" ".join(f"w{i}{j}" for j in range(80)) for i in range(8))
    doc = make_doc(f"Article 7\n\nConditions for consent\n\n{long_body}")
    chunks = chunk_document(doc)
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.text.startswith("Article 7 — Conditions for consent")


def test_prose_after_heading_is_not_mistaken_for_title():
    doc = make_doc("Article 5\n\nMember States shall ensure compliance.\n\nMore text.")
    chunks = chunk_document(doc)
    assert chunks[0].text.startswith("Article 5\n")
    assert "Member States shall ensure compliance." in chunks[0].text


def test_chunks_carry_provenance():
    doc = make_document(
        title="GDPR", text="Some text.", source_url="https://example.eu", source_type="eur-lex"
    )
    chunk = chunk_document(doc)[0]
    assert chunk.title == "GDPR"
    assert chunk.source_url == "https://example.eu"
    assert chunk.doc_id == doc.doc_id
