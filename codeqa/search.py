"""Arama katmanı: BM25, vektör araması ve hibrit birleştirme.

İki arama biçimi farklı soruları çözüyor:

- **BM25** tam isim eşleşmesinde iyi. "OrderService.create nerede" gibi sorularda
  embedding araması sembolü kaçırabiliyor, BM25 doğrudan buluyor.
- **Vektör araması** dolaylı anlatımlarda iyi. "ödeme nasıl doğrulanıyor"
  sorusunda kodda "ödeme" kelimesi geçmese bile ilgili fonksiyonu getiriyor.

Hibrit arama ikisini RRF (Reciprocal Rank Fusion) ile birleştiriyor: skorlar
değil sıralamalar toplanıyor, böylece iki yöntemin ölçekleri normalize edilmek
zorunda kalmıyor.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .embeddings import Embedder, tokenize

#: RRF sabiti. Literatürdeki standart değer; ilk sıraların baskınlığını
#: yumuşatıp alt sıralardaki uzlaşmayı da hesaba katıyor.
RRF_K = 60


@dataclass
class SearchHit:
    """Tek bir arama sonucu."""

    record: dict
    score: float
    sources: tuple[str, ...]  # hangi yöntemler bu sonucu getirdi

    @property
    def location(self) -> str:
        return self.record["location"]

    @property
    def name(self) -> str:
        return self.record["name"]


class BM25Search:
    """Anahtar kelime araması. Kod tokenlarına göre ayrıştırılmış metin üzerinde."""

    def __init__(self, records: list[dict]):
        from rank_bm25 import BM25Okapi

        self.records = records
        corpus = [tokenize(f"{r['name']} {r['context']} {r['text']}") for r in records]
        # rank_bm25 boş korpusta patlıyor; tek elemanlı sahte doküman koruma sağlıyor.
        self._index = BM25Okapi(corpus or [[""]])

    def search(self, query: str, k: int) -> list[tuple[int, float]]:
        if not self.records:
            return []
        scores = self._index.get_scores(tokenize(query))
        top = np.argsort(scores)[::-1][:k]
        return [(int(i), float(scores[i])) for i in top if scores[i] > 0]


class VectorSearch:
    """Kosinüs benzerliğine göre arama. Vektörler birim uzunlukta olduğu için iç çarpım."""

    def __init__(self, records: list[dict], vectors: np.ndarray, embedder: Embedder):
        if len(records) != len(vectors):
            raise ValueError(
                f"Parça sayısı ({len(records)}) ile vektör sayısı ({len(vectors)}) uyuşmuyor"
            )
        self.records = records
        self.vectors = vectors
        self.embedder = embedder

    def search(self, query: str, k: int) -> list[tuple[int, float]]:
        if not self.records:
            return []
        query_vector = self.embedder.embed_query(query)
        scores = self.vectors @ query_vector
        top = np.argsort(scores)[::-1][:k]
        return [(int(i), float(scores[i])) for i in top]


def reciprocal_rank_fusion(
    rankings: dict[str, list[tuple[int, float]]], k: int, rrf_k: int = RRF_K
) -> list[tuple[int, float, tuple[str, ...]]]:
    """Birden çok sıralamayı tek listede birleştirir.

    Her yöntem bir parçaya sırasına göre 1/(rrf_k + sıra) puan veriyor. İki
    yöntemin de üst sıralarda gösterdiği parça doğal olarak öne çıkıyor.
    """
    scores: dict[int, float] = {}
    sources: dict[int, list[str]] = {}
    for method, results in rankings.items():
        for rank, (index, _) in enumerate(results):
            scores[index] = scores.get(index, 0.0) + 1.0 / (rrf_k + rank + 1)
            sources.setdefault(index, []).append(method)

    ordered = sorted(scores.items(), key=lambda item: -item[1])
    return [(index, score, tuple(sources[index])) for index, score in ordered[:k]]


class HybridSearch:
    """BM25 + vektör araması, RRF ile birleştirilmiş."""

    def __init__(self, records: list[dict], vectors: np.ndarray, embedder: Embedder):
        self.records = records
        self.bm25 = BM25Search(records)
        self.vector = VectorSearch(records, vectors, embedder)

    def search(self, query: str, k: int = 5, mode: str = "hybrid") -> list[SearchHit]:
        # Birleştirme öncesi her yöntemden daha fazla aday alınıyor; sadece k
        # tane alınsa iki listenin kesişimi çok küçük kalıyor.
        pool = max(k * 4, 20)

        if mode == "bm25":
            rankings = {"bm25": self.bm25.search(query, pool)}
        elif mode == "vector":
            rankings = {"vector": self.vector.search(query, pool)}
        elif mode == "hybrid":
            rankings = {
                "bm25": self.bm25.search(query, pool),
                "vector": self.vector.search(query, pool),
            }
        else:
            raise ValueError(f"Bilinmeyen arama modu: {mode} (hybrid | vector | bm25)")

        return [
            SearchHit(record=self.records[index], score=score, sources=sources)
            for index, score, sources in reciprocal_rank_fusion(rankings, k)
        ]
