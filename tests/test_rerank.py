"""Reranking testleri. Voyage çağrılmıyor; sahte reranker'la mantık test ediliyor."""

from __future__ import annotations

import numpy as np
import pytest

from codeqa.embeddings import HashEmbedder, embed_records
from codeqa.indexer import extract_from_source
from codeqa.rerank import Reranker, get_reranker
from codeqa.search import HybridSearch

SOURCE = '''
"""Sipariş akışı."""


def create_order(payload: dict) -> int:
    """Yeni sipariş oluşturur."""
    return 1


def charge_payment(order_id: int) -> bool:
    """Ödeme sağlayıcısına gider."""
    return True


def send_invoice(order_id: int) -> None:
    """Fatura gönderir."""
    return None
'''


class _ReverseReranker(Reranker):
    """Aday sırasını tersine çeviren sahte reranker.

    Gerçek bir modelin ne yapacağı belirlenimci değil; test edilmesi gereken şey
    reranker'ın sıralamayı gerçekten değiştirebildiği ve sonuçların doğru
    parçalara bağlandığı.
    """

    name = "ters"

    def __init__(self):
        self.calls: list[tuple[str, int]] = []

    def rerank(self, query, documents, top_k):
        self.calls.append((query, len(documents)))
        order = list(range(len(documents)))[::-1]
        return [(index, 1.0 - position * 0.01) for position, index in enumerate(order[:top_k])]


@pytest.fixture
def parts():
    records = [c.to_dict() for c in extract_from_source(SOURCE, "api/orders.py")]
    embedder = HashEmbedder(dimension=512)
    vectors, _ = embed_records(records, embedder, None)
    return records, vectors, embedder


def test_reranker_changes_order(parts):
    records, vectors, embedder = parts
    plain = HybridSearch(records, vectors, embedder)
    reranked = HybridSearch(records, vectors, embedder, reranker=_ReverseReranker())

    before = [hit.name for hit in plain.search("sipariş", k=3)]
    after = [hit.name for hit in reranked.search("sipariş", k=3)]

    assert before != after


def test_reranker_marks_source(parts):
    records, vectors, embedder = parts
    searcher = HybridSearch(records, vectors, embedder, reranker=_ReverseReranker())
    hits = searcher.search("sipariş", k=3)
    assert all("rerank" in hit.sources for hit in hits)


def test_reranker_sees_more_candidates_than_k(parts):
    """Reranker'ın işi ilk k'yı düzeltmek; ona k'dan fazla aday verilmeli."""
    records, vectors, embedder = parts
    reranker = _ReverseReranker()
    HybridSearch(records, vectors, embedder, reranker=reranker).search("sipariş", k=2)
    _, document_count = reranker.calls[0]
    assert document_count > 2


def test_rerank_can_be_disabled_per_call(parts):
    records, vectors, embedder = parts
    reranker = _ReverseReranker()
    searcher = HybridSearch(records, vectors, embedder, reranker=reranker)

    searcher.search("sipariş", k=3, rerank=False)

    assert reranker.calls == []


def test_hits_point_at_correct_records(parts):
    """Reranker indeksleri kaydırırsa sonuçlar yanlış parçaya bağlanır."""
    records, vectors, embedder = parts
    searcher = HybridSearch(records, vectors, embedder, reranker=_ReverseReranker())
    for hit in searcher.search("sipariş", k=3):
        assert hit.record["name"] == hit.name
        assert hit.record["location"] == hit.location


def test_search_without_reranker_is_unchanged(parts):
    records, vectors, embedder = parts
    searcher = HybridSearch(records, vectors, embedder)
    hits = searcher.search("sipariş", k=3)
    assert hits and all("rerank" not in hit.sources for hit in hits)


def test_get_reranker_none():
    assert get_reranker("none") is None
    assert get_reranker(None) is None


def test_get_reranker_unknown():
    with pytest.raises(ValueError, match="Bilinmeyen reranker"):
        get_reranker("cohere")


def test_empty_candidate_list_is_safe(parts):
    _, _, embedder = parts
    searcher = HybridSearch([], np.zeros((0, 512), dtype=np.float32), embedder, _ReverseReranker())
    assert searcher.search("herhangi") == []
