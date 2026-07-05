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


def test_chunks_carry_provenance():
    doc = make_document(
        title="GDPR", text="Some text.", source_url="https://example.eu", source_type="eur-lex"
    )
    chunk = chunk_document(doc)[0]
    assert chunk.title == "GDPR"
    assert chunk.source_url == "https://example.eu"
    assert chunk.doc_id == doc.doc_id
