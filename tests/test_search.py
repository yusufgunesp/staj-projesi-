"""Embedding, önbellek ve arama katmanının testleri.

Gerçek sağlayıcı çağrılmıyor; testler ağ erişimi ve API anahtarı olmadan çalışır.
"""

from __future__ import annotations

import numpy as np
import pytest

from codeqa.embeddings import (
    CachedEmbedder,
    EmbeddingCache,
    HashEmbedder,
    embed_records,
    get_embedder,
    tokenize,
)
from codeqa.indexer import extract_from_source
from codeqa.search import (
    BM25Search,
    HybridSearch,
    VectorSearch,
    extend_with_unseen_files,
    reciprocal_rank_fusion,
)

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


def test_tokenize_keeps_turkish_letters():
    """ASCII'ye kısıtlanırsa 'aşımı' → 'a' + 'm' oluyor ve Türkçe arama çöküyor."""
    assert tokenize("Varsayılan zaman aşımı süresi") == [
        "varsayılan",
        "zaman",
        "aşımı",
        "süresi",
    ]


def test_tokenize_keeps_turkish_in_code_comments():
    assert tokenize("# sipariş oluşturuluyor") == ["sipariş", "oluşturuluyor"]


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


def test_nomic_model_gets_task_prefixes():
    """Nomic ailesi görev öneki olmadan doğru çalışmıyor — ölçüldü."""
    from codeqa.embeddings import OllamaEmbedder

    embedder = OllamaEmbedder("nomic-embed-text")
    assert embedder.document_prefix == "search_document: "
    assert embedder.query_prefix == "search_query: "


def test_unknown_model_gets_no_prefix():
    from codeqa.embeddings import OllamaEmbedder

    embedder = OllamaEmbedder("baska-bir-model")
    assert embedder.document_prefix == "" and embedder.query_prefix == ""


def test_prefixes_are_applied_to_requests(monkeypatch):
    from codeqa.embeddings import OllamaEmbedder

    embedder = OllamaEmbedder("nomic-embed-text")
    sent: list[str] = []

    def fake_post(payload):
        sent.extend(payload["input"])
        return {"embeddings": [[1.0, 0.0] for _ in payload["input"]]}

    monkeypatch.setattr(embedder, "_post", fake_post)
    embedder.embed_documents(["def f(): pass"])
    embedder.embed_query("f nerede")

    assert sent[0].startswith("search_document: ")
    assert sent[1].startswith("search_query: ")


# --- önbellek ----------------------------------------------------------------


def test_cache_roundtrip(tmp_path, embedder):
    cache = EmbeddingCache(embedder.name, cache_dir=tmp_path)
    vector = embedder.embed_query("merhaba")
    cache.put("abc123", vector)
    cache.save()

    reloaded = EmbeddingCache(embedder.name, cache_dir=tmp_path)
    assert "abc123" in reloaded
    assert np.allclose(reloaded.get("abc123"), vector)


def test_cache_save_merges_with_disk(tmp_path, embedder):
    """İki süreç aynı önbelleği kullanıyor; kendi kopyasını yazan diğerini siler.

    Gerçekte yaşandı: arka planda koşan ölçüm, indeksleme sırasında eklenen
    180 vektörü sessizce sildi. Hata ancak arama sırasında ortaya çıktı.
    """
    first = EmbeddingCache(embedder.name, cache_dir=tmp_path)
    first.put("a", embedder.embed_query("bir"))
    first.save()

    # İkinci süreç aynı dosyayı yüklüyor
    second = EmbeddingCache(embedder.name, cache_dir=tmp_path)

    # Birinci süreç yeni bir vektör ekliyor
    first.put("b", embedder.embed_query("iki"))
    first.save()

    # İkinci süreç kendi eklemesini kaydediyor — "b"yi silmemeli
    second.put("c", embedder.embed_query("üç"))
    second.save()

    reloaded = EmbeddingCache(embedder.name, cache_dir=tmp_path)
    assert set(("a", "b", "c")) <= set(reloaded._vectors)


def test_cache_save_is_atomic(tmp_path, embedder):
    """Yarıda kesilen yazma önbelleği bozuk bırakmamalı."""
    cache = EmbeddingCache(embedder.name, cache_dir=tmp_path)
    cache.put("a", embedder.embed_query("bir"))
    cache.save()
    assert not list(tmp_path.glob("*.tmp")), "geçici dosya bırakılmamalı"
    assert EmbeddingCache(embedder.name, cache_dir=tmp_path).get("a") is not None


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


class _CountingEmbedder(HashEmbedder):
    """Kaç kez sorgu embed edildiğini sayar."""

    def __init__(self, dimension=256):
        super().__init__(dimension)
        self.query_calls = 0

    def embed_query(self, text: str) -> np.ndarray:
        self.query_calls += 1
        return super().embed_query(text)


def test_cached_embedder_reuses_query_vectors(tmp_path):
    """Aynı sorgu ikinci kez embed edilmemeli — hız limitli sağlayıcıda kritik."""
    inner = _CountingEmbedder()
    cached = CachedEmbedder(inner, EmbeddingCache(inner.name, cache_dir=tmp_path))

    first = cached.embed_query("sipariş akışı")
    second = cached.embed_query("sipariş akışı")

    assert inner.query_calls == 1
    assert np.array_equal(first, second)


def test_cached_embedder_separates_different_queries(tmp_path):
    inner = _CountingEmbedder()
    cached = CachedEmbedder(inner, EmbeddingCache(inner.name, cache_dir=tmp_path))

    cached.embed_query("sipariş")
    cached.embed_query("ödeme")

    assert inner.query_calls == 2


def test_cached_embedder_survives_restart(tmp_path):
    """flush() sonrası sorgu vektörü diskte kalmalı."""
    inner = _CountingEmbedder()
    cache_name = inner.name
    first = CachedEmbedder(inner, EmbeddingCache(cache_name, cache_dir=tmp_path))
    first.embed_query("soru")
    first.flush()

    fresh = _CountingEmbedder()
    CachedEmbedder(fresh, EmbeddingCache(cache_name, cache_dir=tmp_path)).embed_query("soru")

    assert fresh.query_calls == 0  # diskteki önbellekten geldi


def test_cached_embedder_batches_disk_writes(tmp_path):
    """Her sorguda diske yazmak, birleştirmeli kaydetmeyle birlikte çok pahalı.

    Ölçüldü: 40 soruluk bir koşu, 40 MB'lık önbelleği her sorguda baştan
    okuyup yazdığı için 10 dakikayı aştı.
    """
    inner = _CountingEmbedder()
    embedder = CachedEmbedder(inner, EmbeddingCache(inner.name, cache_dir=tmp_path))

    embedder.embed_query("bir")

    # Henüz eşik dolmadı: dosya yazılmamış olmalı
    assert not list(tmp_path.glob("*.npz"))

    embedder.flush()
    assert list(tmp_path.glob("*.npz"))


def test_cached_embedder_query_keys_do_not_collide_with_chunks(tmp_path):
    """Sorgu ve parça anahtarları aynı dosyada, çakışmamalı."""
    inner = _CountingEmbedder()
    cache = EmbeddingCache(inner.name, cache_dir=tmp_path)
    cached = CachedEmbedder(inner, cache)

    cache.put("abc123", np.zeros(inner.dimension, dtype=np.float32))
    cached.embed_query("abc123")

    assert len(cache) == 2


# --- arama -------------------------------------------------------------------


def test_bm25_finds_exact_symbol(records):
    hits = BM25Search(records).search("charge_payment", k=3)
    assert records[hits[0][0]]["name"] == "charge_payment"


def test_vector_search_returns_ranked_results(records, embedder):
    vectors, _ = embed_records(records, embedder, None)
    hits = VectorSearch(records, vectors, embedder).search("invoice email", k=3)
    assert records[hits[0][0]]["name"] == "send_invoice_email"


class _FixedEmbedder(HashEmbedder):
    """Sorguyu sabit bir vektöre çeviren embedder; eşik testini belirlenimci kılar."""

    def __init__(self, query_vector: np.ndarray):
        super().__init__(dimension=len(query_vector))
        self._query_vector = query_vector

    def embed_query(self, text: str) -> np.ndarray:
        return self._query_vector


def test_vector_search_drops_zero_similarity_results():
    """Örtüşmeyen parça sonuç olarak dönmemeli — yoksa modele çöp parça gidiyor."""
    documents = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)  # 2. parça dik
    fake_records = [{"name": "eslesen"}, {"name": "alakasiz"}]
    searcher = VectorSearch(
        fake_records, documents, _FixedEmbedder(np.array([1.0, 0.0], np.float32))
    )

    results = searcher.search("herhangi", k=5)

    assert [fake_records[i]["name"] for i, _ in results] == ["eslesen"]


def test_vector_search_returns_nothing_when_all_orthogonal():
    documents = np.array([[0.0, 1.0], [0.0, 1.0]], dtype=np.float32)
    searcher = VectorSearch(
        [{"name": "a"}, {"name": "b"}], documents, _FixedEmbedder(np.array([1.0, 0.0], np.float32))
    )
    assert searcher.search("herhangi", k=5) == []


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


# --- çeşitlilik slotları --------------------------------------------------

MULTI_FILE_SOURCES = {
    "api/orders.py": '''
"""Sipariş uçları."""


def create_order(payload: dict) -> int:
    """Sipariş kaydı açar ve ödeme akışını başlatır."""
    return 1


def cancel_order(order_id: int) -> None:
    """Siparişi iptal eder, ödeme iadesini tetikler."""
    return None
''',
    "services/payment.py": '''
"""Ödeme sağlayıcısı."""


def charge(order_id: int) -> bool:
    """Sipariş için tahsilat yapar."""
    return True
''',
    "models/order.py": '''
"""Sipariş modeli."""


def save_order(order_id: int) -> None:
    """Siparişi veritabanına yazar."""
    return None
''',
}


@pytest.fixture
def multi_file_records():
    records = []
    for path, source in MULTI_FILE_SOURCES.items():
        records.extend(c.to_dict() for c in extract_from_source(source, path))
    return records


def test_extend_with_unseen_files_keeps_head_intact():
    ranked = [("a.py", 1), ("a.py", 2), ("b.py", 3)]
    result = extend_with_unseen_files(ranked, lambda item: item[0], k=2, slots=1, scan=10)
    assert result[:2] == ranked[:2]  # ilk k dokunulmadı
    assert result[2] == ("b.py", 3)  # arkasına yeni dosya eklendi


def test_extend_with_unseen_files_skips_already_seen():
    ranked = [("a.py", 1), ("a.py", 2), ("a.py", 3), ("b.py", 4)]
    result = extend_with_unseen_files(ranked, lambda item: item[0], k=1, slots=2, scan=10)
    # a.py zaten temsil edildiği için 2 ve 3 atlanıyor, yalnızca b.py ekleniyor
    assert result == [("a.py", 1), ("b.py", 4)]


def test_extend_with_unseen_files_disabled_returns_head():
    ranked = [("a.py", 1), ("b.py", 2)]
    assert extend_with_unseen_files(ranked, lambda i: i[0], k=1, slots=0, scan=10) == [("a.py", 1)]


def test_diversity_slots_add_new_files(multi_file_records, embedder):
    """Kota değil ekleme: ilk k korunuyor, sonuç listesi yeni dosyalarla uzuyor."""
    vectors, _ = embed_records(multi_file_records, embedder, None)
    plain = HybridSearch(multi_file_records, vectors, embedder, diversity_slots=0)
    diverse = HybridSearch(multi_file_records, vectors, embedder, diversity_slots=2)

    base = plain.search("sipariş", k=2, mode="bm25")
    extended = diverse.search("sipariş", k=2, mode="bm25")

    assert [h.location for h in extended[:2]] == [h.location for h in base]
    assert len(extended) > len(base)
    assert len({h.record["path"] for h in extended}) > len({h.record["path"] for h in base})


def test_diversity_slots_never_shrink_result(multi_file_records, embedder):
    """Eleme yapmadığı için sonuç sayısı hiçbir koşulda azalmıyor."""
    vectors, _ = embed_records(multi_file_records, embedder, None)
    plain = HybridSearch(multi_file_records, vectors, embedder, diversity_slots=0)
    diverse = HybridSearch(multi_file_records, vectors, embedder, diversity_slots=4)
    for mode in ("hybrid", "vector", "bm25"):
        assert len(diverse.search("ödeme", k=3, mode=mode)) >= len(
            plain.search("ödeme", k=3, mode=mode)
        )
