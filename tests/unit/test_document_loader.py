import pytest

from core.ingestion.document_loader import (
    ProvenanceError,
    html_to_text,
    load_sample_file,
    make_document,
)


def test_untitled_document_rejected():
    with pytest.raises(ProvenanceError):
        make_document(title="  ", text="body", source_type="upload")


def test_empty_document_rejected():
    with pytest.raises(ProvenanceError):
        make_document(title="T", text="", source_type="upload")


def test_doc_id_is_stable_for_same_source():
    a = make_document(title="T", text="v1", source_url="https://x.eu")
    b = make_document(title="T", text="v2 changed", source_url="https://x.eu")
    assert a.doc_id == b.doc_id
    assert a.content_hash != b.content_hash


def test_sample_file_header_parsed(tmp_path):
    f = tmp_path / "sample.txt"
    f.write_text(
        "---\ntitle: My Doc\nsource_url: https://eur-lex.europa.eu/x\n"
        "source_type: eur-lex\nlanguage: en\n---\nBody paragraph here.\n"
    )
    doc = load_sample_file(f)
    assert doc.title == "My Doc"
    assert doc.source_type == "eur-lex"
    assert doc.text == "Body paragraph here."


def test_sample_file_without_header_rejected(tmp_path):
    f = tmp_path / "bad.txt"
    f.write_text("no header at all")
    with pytest.raises(ProvenanceError):
        load_sample_file(f)


def test_html_to_text_normalizes_nbsp():
    # OJ markup writes "(EU)&nbsp;2023/970" — must match plain-space phrases
    text = html_to_text("<p>DIRECTIVE (EU)\xa02023/970 OF THE COUNCIL</p>")
    assert "(EU) 2023/970" in text


def test_html_to_text_strips_chrome_keeps_content():
    html = (
        "<html><head><style>x{}</style><script>evil()</script></head>"
        "<body><nav>menu</nav><p>Real   content.</p><p>Second para.</p>"
        "<footer>foot</footer></body></html>"
    )
    text = html_to_text(html)
    assert "Real content." in text
    assert "Second para." in text
    for junk in ("evil", "menu", "foot", "x{}"):
        assert junk not in text
