"""Reranking: aramanın ilk N sonucunu soruya göre yeniden sıralar.

Arama ile reranker'ın işi farklı. Arama tüm indekse bakmak zorunda olduğu için
hızlı ve kaba olmak zorunda: her parça için tek bir vektör var ve soru da tek
bir vektöre indirgeniyor. Reranker ise sadece elde kalan 20-30 adaya bakıyor ve
soruyla parçayı **birlikte** değerlendirebiliyor. Bu yüzden sıralamayı düzeltmekte
aramadan iyi, ama tüm indekse uygulanamayacak kadar pahalı.

Yani reranker recall'ı artırmaz — arama bir parçayı hiç getirmediyse reranker onu
kurtaramaz. Düzelttiği şey sıralama: doğru parçayı 6. sıradan 1. sıraya taşımak.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod

from .embeddings import retry_on_rate_limit


class Reranker(ABC):
    """Aday parçaları soruya göre yeniden sıralayan bileşen."""

    name: str

    @abstractmethod
    def rerank(self, query: str, documents: list[str], top_k: int) -> list[tuple[int, float]]:
        """(belge indeksi, skor) çiftlerini en alakalıdan başlayarak döner."""


class VoyageReranker(Reranker):
    """Voyage `rerank-2.5`. Embedding ile aynı anahtarı ve hız limitini kullanıyor."""

    RETRY_WAIT_SECONDS = 25

    def __init__(self, model: str = "rerank-2.5", api_key: str | None = None, max_retries: int = 8):
        try:
            import voyageai
        except ImportError as exc:  # pragma: no cover - kurulum hatası
            raise RuntimeError("voyageai kurulu değil: pip install voyageai") from exc

        key = api_key or os.environ.get("VOYAGE_API_KEY")
        if not key:
            raise RuntimeError("VOYAGE_API_KEY tanımlı değil; reranking için gerekli.")
        self._client = voyageai.Client(api_key=key)
        self._voyageai = voyageai
        self.model = model
        self.name = f"voyage:{model}"
        self.max_retries = max_retries

    def rerank(self, query: str, documents: list[str], top_k: int) -> list[tuple[int, float]]:
        if not documents:
            return []
        result = retry_on_rate_limit(
            lambda: self._client.rerank(
                query=query, documents=documents, model=self.model, top_k=top_k
            ),
            self._voyageai.error.RateLimitError,
            max_retries=self.max_retries,
            base_wait=self.RETRY_WAIT_SECONDS,
        )
        return [(item.index, float(item.relevance_score)) for item in result.results]


def get_reranker(provider: str, model: str | None = None) -> Reranker | None:
    """Sağlayıcı adından reranker üretir. `none` reranking'i kapatır."""
    if provider in (None, "", "none"):
        return None
    if provider == "voyage":
        return VoyageReranker(model or "rerank-2.5")
    raise ValueError(f"Bilinmeyen reranker: {provider} (voyage | none)")
