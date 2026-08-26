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

#: Alaka sıralamasının arkasına eklenen "henüz görülmemiş dosya" slotu sayısı.
#:
#: **Ölçüldü ve işe yaradı.** Sorun şuydu: arama doğru bölgeyi buluyor, sonra o
#: bölgeyi tekrar tekrar getiriyor. Akış setinde ilk 8 sonuçta ortalama yalnızca
#: 3,1 farklı dosya vardı; 160 slotun 98'i zaten listede olan bir dosyanın
#: tekrarıydı ve zincirin ikinci halkasına yer kalmıyordu.
#:
#: Önce sert kota denendi (dosya başına en fazla 1 parça). Kapsamı artırdı ama
#: **sembol isabetini 0,778'den 0,444'e düşürdü**: iki ilgili parça gerçekten
#: aynı dosyada olabiliyor (sync/async ikizleri, decoder + accumulator) ve kota
#: bunlardan birini kesiyordu. Kapsam metriği dosya çeşitliliğini ödüllendirdiği
#: için müdahaleyi kendi kendine haklı çıkarıyordu — sembol isabeti bu döngüyü
#: kıran ölçüt oldu. Yumuşak ceza da çare olmadı: RRF skorları 1/(60+sıra)
#: olduğu için fazla sıkışık, 0,8'in altındaki her çarpan sert kotaya dönüşüyor.
#:
#: Çalışan yaklaşım hiçbir şeyi elemiyor, yalnızca ekliyor: ilk k sonuç
#: dokunulmadan kalıyor, arkasına henüz temsil edilmemiş dosyaların en iyi
#: parçası ekleniyor. Dev bölmesinde (voyage, vector, k=8 + 4 slot = 12 parça):
#:
#: ==========================  ===========  ============  ========
#: yapılandırma                akış kapsam  kolay kapsam  sembol
#: ==========================  ===========  ============  ========
#: düz top-8 (önceki)          0,750        0,925         0,778
#: sert kota=1                 0,867        0,950         0,444
#: düz top-12 (kontrol)        0,750        0,950         0,778
#: k=8 + 4 slot (bu)           0,833        0,975         0,778
#: ==========================  ===========  ============  ========
#:
#: Kontrol satırı önemli: sadece daha çok parça vermek akış kapsamını hiç
#: kıpırdatmıyor (0,750). Kazanç parça sayısından değil çeşitlilikten geliyor.
#: Maliyeti modele giden parça sayısının 8'den 12'ye çıkması.
#:
#: Kapatmak için `diversity_slots=0`.
DIVERSITY_SLOTS = 4

#: Yeni dosya ararken aday listesinde ne kadar derine inileceği. Ölçüm bu
#: derinlikte yapıldı; daha sığ tarama zincirin uzak halkalarını kaçırıyor.
DIVERSITY_SCAN = 60

#: Sadece alan tanımından ibaret sınıflara uygulanan ağırlık. Bunlar üretilmiş
#: tip tanımları (TypedDict, model sınıfları) — sorunun kelimelerini içeriyorlar
#: ama mantık taşımıyorlar.
#:
#: **Ölçüldü ve işe yaramadı.** anthropic SDK'sında (parçaların %42'si `types/`
#: altında) 60 soruluk sette MRR 0.761 → 0.754, recall değişmedi. Kod duruyor
#: çünkü fikir mantıklı ve başka bir repoda karşılığı olabilir, ama varsayılan
#: **kapalı**: ölçülmüş faydası olmayan bir karmaşıklık açık gelmemeli.
#: Açmak için `HybridSearch(..., weigh_chunks=True)`.
TYPE_ONLY_WEIGHT = 0.4


def chunk_weight(record: dict) -> float:
    """Parçanın bilgi yoğunluğuna göre ağırlık.

    Metot içermeyen bir sınıf parçası, alan listesinden ibaret demektir.
    `def` geçip geçmediğine bakmak, üretilmiş dosya işaretine bakmaktan
    sağlam: bu SDK'da dosyaların %92'si "generated" işaretli, `_client.py`
    ve `_constants.py` dahil — yani o işaret ayırt edici değil.
    """
    if record.get("kind") == "class" and "def " not in record.get("text", ""):
        return TYPE_ONLY_WEIGHT
    return 1.0


def extend_with_unseen_files(ranked: list, path_of, k: int, slots: int, scan: int) -> list:
    """İlk k sonucun arkasına, henüz temsil edilmemiş dosyaların en iyi parçasını ekler.

    Eleme yok: ilk k olduğu gibi kalıyor, liste yalnızca uzuyor. Sert kotanın
    aksine bu yüzden sembol isabetini düşürmüyor — aynı dosyadaki ikinci ilgili
    parça yerinde duruyor, sadece arkasına başka dosyalar geliyor.
    """
    selected = list(ranked[:k])
    if slots <= 0:
        return selected
    seen = {path_of(item) for item in selected}
    for item in ranked[k:scan]:
        if len(selected) >= k + slots:
            break
        path = path_of(item)
        if path in seen:
            continue
        seen.add(path)
        selected.append(item)
    return selected

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
        # Sıfır ve altı benzerlik = hiç örtüşme yok. Bu eşik olmadan alakasız
        # bir sorgu bile k tane sonuç döndürüyor ve modele çöp parçalar
        # "bulunan sonuç" diye gidiyor. Mutlak bir kalite eşiği değil —
        # gerçek embedding modellerinde skorlar nadiren sıfırın altına iner.
        return [(int(i), float(scores[i])) for i in top if scores[i] > 0]


def reciprocal_rank_fusion(
    rankings: dict[str, list[tuple[int, float]]],
    k: int,
    rrf_k: int = RRF_K,
    weights: dict[str, float] | None = None,
) -> list[tuple[int, float, tuple[str, ...]]]:
    """Birden çok sıralamayı tek listede birleştirir.

    Her yöntem bir parçaya sırasına göre 1/(rrf_k + sıra) puan veriyor. İki
    yöntemin de üst sıralarda gösterdiği parça doğal olarak öne çıkıyor.

    `weights` yöntemlere farklı ağırlık vermeyi sağlıyor. Eşit ağırlık, zayıf
    olan yöntemi güçlü olan kadar dinlemek demek — hangi ağırlığın doğru olduğu
    embedding kalitesine göre değişiyor, o yüzden ölçülerek belirleniyor.
    """
    weights = weights or {}
    scores: dict[int, float] = {}
    sources: dict[int, list[str]] = {}
    for method, results in rankings.items():
        weight = weights.get(method, 1.0)
        for rank, (index, _) in enumerate(results):
            scores[index] = scores.get(index, 0.0) + weight / (rrf_k + rank + 1)
            sources.setdefault(index, []).append(method)

    ordered = sorted(scores.items(), key=lambda item: -item[1])
    return [(index, score, tuple(sources[index])) for index, score in ordered[:k]]


class HybridSearch:
    """BM25 + vektör araması, RRF ile birleştirilmiş; isteğe bağlı reranking."""

    def __init__(
        self,
        records: list[dict],
        vectors: np.ndarray,
        embedder: Embedder,
        reranker=None,
        weights: dict[str, float] | None = None,
        pool_size: int | None = None,
        weigh_chunks: bool = False,
        diversity_slots: int = DIVERSITY_SLOTS,
    ):
        self.records = records
        self.bm25 = BM25Search(records)
        self.vector = VectorSearch(records, vectors, embedder)
        self.reranker = reranker
        self.weights = weights or {}
        self.pool_size = pool_size
        self.weigh_chunks = weigh_chunks
        self.diversity_slots = diversity_slots

    def _candidates(self, query: str, pool: int, mode: str):
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
        fused = reciprocal_rank_fusion(rankings, pool, weights=self.weights)
        if not self.weigh_chunks:
            return fused

        # Parça ağırlığı birleştirmeden sonra uygulanıyor: her iki yöntemin de
        # sıralamasına girmiş olsa bile düşük bilgi yoğunluklu parça geriye
        # düşsün. Sıra yeniden kuruluyor.
        weighted = [
            (index, score * chunk_weight(self.records[index]), sources)
            for index, score, sources in fused
        ]
        weighted.sort(key=lambda item: -item[1])
        return weighted

    def search(
        self, query: str, k: int = 5, mode: str = "hybrid", rerank: bool | None = None
    ) -> list[SearchHit]:
        # Birleştirme öncesi her yöntemden daha fazla aday alınıyor; sadece k
        # tane alınsa iki listenin kesişimi çok küçük kalıyor. Çeşitlilik
        # slotları açıkken havuz en az tarama derinliği kadar: yeni dosyalar
        # listenin alt sıralarında duruyor, havuz sığ kalırsa hiç görülmüyorlar.
        pool = self.pool_size or max(k * 4, 20)
        if self.diversity_slots > 0:
            pool = max(pool, DIVERSITY_SCAN)
        use_rerank = self.reranker is not None if rerank is None else rerank
        candidates = self._candidates(query, pool, mode)

        if not use_rerank or self.reranker is None or not candidates:
            selected = extend_with_unseen_files(
                candidates,
                lambda item: self.records[item[0]]["path"],
                k,
                self.diversity_slots,
                DIVERSITY_SCAN,
            )
            return [
                SearchHit(record=self.records[index], score=score, sources=sources)
                for index, score, sources in selected
            ]

        # Reranker'a parça metniyle birlikte konum etiketi de gidiyor: dosya yolu
        # ve nitelenmiş ad, alaka değerlendirmesinde işe yarayan bilgiler.
        documents = [
            f"{self.records[index]['context']}\n\n{self.records[index]['text']}"
            for index, _, _ in candidates
        ]
        sources_by_position = {position: item[2] for position, item in enumerate(candidates)}

        # Reranker'a havuzun tamamı gidiyor, çeşitlilik ondan sonra ekleniyor:
        # önce alaka sırası düzelsin, yeni dosyalar düzelmiş sıranın arkasına
        # eklensin. top_k=k verilseydi eklenecek aday kalmazdı.
        hits: list[SearchHit] = []
        for position, score in self.reranker.rerank(query, documents, top_k=len(documents)):
            record_index = candidates[position][0]
            hits.append(
                SearchHit(
                    record=self.records[record_index],
                    score=score,
                    sources=sources_by_position[position] + ("rerank",),
                )
            )
        return extend_with_unseen_files(
            hits, lambda hit: hit.record["path"], k, self.diversity_slots, DIVERSITY_SCAN
        )
