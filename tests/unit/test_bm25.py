from core.retrieval.bm25 import BM25Index, tokenize


def test_tokenize_keeps_regulation_numbers():
    assert "2016/679" in tokenize("Regulation (EU) 2016/679 applies")


def test_exact_regulation_number_ranks_first():
    index = BM25Index()
    index.add("gdpr", "Regulation 2016/679 general data protection regulation erasure")
    index.add("sme", "Recommendation 2003/361 SME definition thresholds turnover")
    index.add("fund", "EIC Accelerator grant funding startups innovation")
    results = index.search("what does regulation 2016/679 say")
    assert results[0][0] == "gdpr"


def test_remove_then_search():
    index = BM25Index()
    index.add("a", "alpha beta gamma")
    index.add("b", "delta epsilon zeta")
    index.remove("a")
    assert all(cid != "a" for cid, _ in index.search("alpha"))


def test_re_adding_same_id_replaces():
    index = BM25Index()
    index.add("a", "old content about apples")
    index.add("a", "new content about bananas")
    assert index.search("bananas")[0][0] == "a"
    assert not index.search("apples")


def test_empty_index_returns_nothing():
    assert BM25Index().search("anything") == []
