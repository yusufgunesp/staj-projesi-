"""Embedding, önbellek ve arama katmanının testleri.

Gerçek sağlayıcı çağrılmıyor; testler ağ erişimi ve API anahtarı olmadan çalışır.
"""

from __future__ import annotations

import numpy as np
import pytest

from codeqa.embeddings import (
    EmbeddingCache,
    HashEmbedder,
    embed_records,
    get_embedder,
    tokenize,
)
from codeqa.indexer import extract_from_source
from codeqa.search import BM25Search, HybridSearch, VectorSearch, reciprocal_rank_fusion

SOURCE = '''
"""Sipariş akışı."""

import json


def create_order(payload: dict) -> int:
    """Yeni sipariş oluşturur."""
    return 1


def charge_payment(order_id: int) -> bool:
    """Ödeme sağlayıcısına gider ve tahsilat yapar."""
    return True


def send_invoice_email(order_id: int) -> None:
    """Fatura e-postasını gönderir."""
    return None
'''


@pytest.fixture
def records():
    return [c.to_dict() for c in extract_from_source(SOURCE, "api/orders.py")]


@pytest.fixture
def embedder():
    return HashEmbedder(dimension=256)


# --- tokenizer ---------------------------------------------------------------


def test_tokenize_splits_snake_case():
    assert tokenize("order_id") == ["order", "id"]


def test_tokenize_splits_camel_case():
    assert tokenize("getUserOrders") == ["get", "user", "orders"]


def test_tokenize_splits_dotted_paths():
    assert tokenize("services.payment.charge") == ["services", "payment", "charge"]


def test_tokenize_lowercases_and_drops_punctuation():
    assert tokenize("def charge_payment(order_id: int) -> bool:") == [
        "def",
        "charge",
        "payment",
        "order",
        "id",
        "int",
        "bool",
    ]


# --- embedder ----------------------------------------------------------------


def test_hash_embedder_is_deterministic(embedder):
    first = embedder.embed_query("sipariş oluştur")
    second = embedder.embed_query("sipariş oluştur")
    assert np.array_equal(first, second)


def test_vectors_are_unit_length(embedder):
    vectors = embedder.embed_documents(["def create_order(): pass", "class Foo: pass"])
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0)


def test_shared_tokens_score_higher_than_unrelated(embedder):
    query = embedder.embed_query("charge payment")
    related = embedder.embed_documents(["def charge_payment(order_id): ..."])[0]
    unrelated = embedder.embed_documents(["def render_template(name): ..."])[0]
    assert query @ related > query @ unrelated


def test_get_embedder_unknown_provider():
    with pytest.raises(ValueError, match="Bilinmeyen sağlayıcı"):
        get_embedder("openai")


def test_get_embedder_returns_hash():
    assert isinstance(get_embedder("hash"), HashEmbedder)


# --- önbellek ----------------------------------------------------------------


def test_cache_roundtrip(tmp_path, embedder):
    cache = EmbeddingCache(embedder.name, cache_dir=tmp_path)
    vector = embedder.embed_query("merhaba")
    cache.put("abc123", vector)
    cache.save()

    reloaded = EmbeddingCache(embedder.name, cache_dir=tmp_path)
    assert "abc123" in reloaded
    assert np.allclose(reloaded.get("abc123"), vector)


def test_cache_prevents_recomputation(tmp_path, records, embedder):
    cache = EmbeddingCache(embedder.name, cache_dir=tmp_path)

    _, computed_first = embed_records(records, embedder, cache)
    _, computed_second = embed_records(records, embedder, cache)

    assert computed_first == len(records)
    assert computed_second == 0  # hepsi önbellekten geldi


def test_embed_records_aligns_with_record_order(tmp_path, records, embedder):
    cache = EmbeddingCache(embedder.name, cache_dir=tmp_path)
    vectors, _ = embed_records(records, embedder, cache)
    assert vectors.shape == (len(records), embedder.dimension)


def test_embed_records_handles_empty_input(embedder):
    vectors, computed = embed_records([], embedder, None)
    assert vectors.shape == (0, embedder.dimension)
    assert computed == 0


# --- arama -------------------------------------------------------------------


def test_bm25_finds_exact_symbol(records):
    hits = BM25Search(records).search("charge_payment", k=3)
    assert records[hits[0][0]]["name"] == "charge_payment"


def test_vector_search_returns_ranked_results(records, embedder):
    vectors, _ = embed_records(records, embedder, None)
    hits = VectorSearch(records, vectors, embedder).search("invoice email", k=3)
    assert records[hits[0][0]]["name"] == "send_invoice_email"


def test_vector_search_rejects_mismatched_vectors(records, embedder):
    with pytest.raises(ValueError, match="uyuşmuyor"):
        VectorSearch(records, np.zeros((2, 8), dtype=np.float32), embedder)


def test_rrf_prefers_results_both_methods_agree_on():
    rankings = {
        "bm25": [(1, 9.0), (2, 8.0), (3, 7.0)],
        "vector": [(3, 0.9), (1, 0.8), (4, 0.7)],
    }
    fused = reciprocal_rank_fusion(rankings, k=4)
    assert fused[0][0] == 1  # iki listede de üstlerde
    assert set(fused[0][2]) == {"bm25", "vector"}


def test_rrf_keeps_single_method_results():
    fused = reciprocal_rank_fusion({"bm25": [(5, 1.0)]}, k=3)
    assert fused[0][0] == 5
    assert fused[0][2] == ("bm25",)


def test_hybrid_search_reports_sources(records, embedder):
    vectors, _ = embed_records(records, embedder, None)
    hits = HybridSearch(records, vectors, embedder).search("charge_payment", k=3)
    assert hits[0].name == "charge_payment"
    assert hits[0].location.startswith("api/orders.py:")
    assert set(hits[0].sources) <= {"bm25", "vector"}


def test_hybrid_search_modes(records, embedder):
    vectors, _ = embed_records(records, embedder, None)
    searcher = HybridSearch(records, vectors, embedder)
    for mode in ("hybrid", "vector", "bm25"):
        assert searcher.search("payment", k=2, mode=mode)


def test_hybrid_search_rejects_unknown_mode(records, embedder):
    vectors, _ = embed_records(records, embedder, None)
    searcher = HybridSearch(records, vectors, embedder)
    with pytest.raises(ValueError, match="Bilinmeyen arama modu"):
        searcher.search("payment", mode="magic")


def test_search_on_empty_index(embedder):
    searcher = HybridSearch([], np.zeros((0, 256), dtype=np.float32), embedder)
    assert searcher.search("herhangi bir şey") == []
