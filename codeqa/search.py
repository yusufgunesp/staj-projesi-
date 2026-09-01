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

import collections
import re
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

#: Dosya çeşitliliğinden sonra eklenen "aynı dizinden kardeş dosya" slotu sayısı.
#:
#: Gözlem şu: arama çoğu zaman doğru **dizini** buluyor ama o dizindeki doğru
#: dosyayı kaçırıyor. Zor sette cevap kaybına yol açan iki soru da tam olarak
#: buydu — `z05` beklenen `lib/credentials/_types.py` yerine aynı dizinden
#: `_cache.py`'yi, `z06` beklenen `_chain.py` yerine yine aynı dizinden
#: `_providers.py`, `_workload.py`, `_constants.py`'yi getirdi.
#:
#: Bir dizin sonuçlarda en az `DIRECTORY_TRIGGER` parçayla temsil ediliyorsa,
#: o dizinin henüz görülmemiş dosyalarından en iyi parça ekleniyor. Eşik
#: gerekli: tek bir isabetten dizin genişletmek gürültü getiriyor, iki isabet
#: "cevap bu dizinde" sinyali sayılıyor.
DIRECTORY_SLOTS = 2

#: Dizin genişletmesinin tetiklenmesi için o dizinden gelmesi gereken parça sayısı.
DIRECTORY_TRIGGER = 2

#: İmport grafiğinden eklenen "bu dosyayı kullanan dosya" slotu sayısı.
#:
#: "Kimler kullanıyor" bir benzerlik sorusu değil, çağrı grafiği sorusu. Embedding
#: araması bunu güvenilir biçimde çözemiyor: `lib/_scoped_client.py`'yi 1. sıraya
#: koyduğu bir soruda, onu kullanan `lib/environments/_worker.py` 136. sıradaydı —
#: tarama derinliğinin çok dışında. Sıralamayı derinleştirmek çözüm değil, çünkü
#: aradaki 135 sonuç gürültü. Import bağını doğrudan takip etmek gerekiyor.
REFERENCE_SLOTS = 2

#: İleri yönün ayrı bütçesi. **Ölçüldü ve genellenmedi, varsayılan kapalı.**
#:
#: Dev bölmesindeki `z08` şu yüzden kaçıyordu: `lib/middleware/_fallbacks.py`
#: 1. sıradaydı ve aranan `_middleware.py`'yi import ediyordu, ama genişletme
#: yalnızca "kimler kullanıyor" yönünde çalışıyordu. İleri yön eklendi
#: (yeniden dışa aktarım kabukları elenip, hub'lar atlanıp, kalanlar sorguya
#: yakınlığa göre sıralanarak) ve dev kapsamı 0.944 → 1.000 oldu.
#:
#: Test bölmesinde kazanç **tam olarak sıfır**: kapsam 0.893 → 0.893, MRR
#: 0.847 → 0.847, buna karşılık soru başına 2 parça daha. Dev'in hatalarına
#: bakarak tasarlanan bir mekanizmanın o hataları düzeltmesi sürpriz değil;
#: ölçüm başka bir sette yapılmasaydı kazanç sanılacaktı.
#:
#: Açmak için `HybridSearch(..., reference_forward_slots=2)`.
REFERENCE_FORWARD_SLOTS = 0

#: İleri yönde ("bu dosya neyi kullanıyor") hub eşiği. Geri yöndekinden gevşek,
#: çünkü iki yön simetrik değil: 13 dosya tarafından kullanılmak hub olmak
#: demektir, 13 dosyayı kullanmak sıradan bir modül demektir. Bu SDK'da
#: dosyaların yalnızca ~10'unun 25'ten çok kullanıcısı var.
REFERENCE_HUB_USERS = 25

#: Bir dosyanın kullanıcıları bu sayıdan fazlaysa import bağı ayırt edici değil.
#: Bu SDK'da `_models.py`'nin 513, `_types.py`'nin 100 kullanıcısı var — onları
#: genişletmek her soruya aynı dosyaları eklemek demek. Buna karşılık dosyaların
#: 862'sinin en fazla üç kullanıcısı var; asıl bilgi orada.
REFERENCE_MAX_USERS = 5

#: `from ..paket.modul import ad` biçimindeki göreli import satırları.
_RELATIVE_IMPORT = re.compile(r"^from (\.+)([\w.]*) import", re.MULTILINE)

#: Sadece alan tanımından ibaret sınıflara uygulanan ağırlık. Bunlar üretilmiş
#: tip tanımları (TypedDict, model sınıfları) — sorunun kelimelerini içeriyorlar
#: ama mantık taşımıyorlar.
#:
#: **Tek başına işe yaramıyor, genişletme slotlarıyla birlikte yarıyor.**
#:
#: İlk ölçümde (60 soruluk set, genişletme yokken) MRR 0.761 → 0.754 ile geriledi
#: ve kapatıldı. Kod "başka bir repoda karşılığı olabilir" diye bırakılmıştı;
#: karşılığı başka repoda değil, başka yapılandırmada çıktı.
#:
#: Genişletme slotları eklendikten sonra 2×2 kontrol (yeni indeks, kapsam):
#:
#: =====================  =====  =====  ======
#: yapılandırma           zor    akış   kolay
#: =====================  =====  =====  ======
#: taban                  0,639  0,842  0,917
#: yalnız ağırlık         0,639  0,842  0,900
#: yalnız genişletme      0,944  0,883  0,950
#: ikisi birden           0,944  0,908  0,983
#: =====================  =====  =====  ======
#:
#: Yani ağırlık tek başına hâlâ zarar veriyor (0,917 → 0,900), birlikte +3,3 puan
#: katıyor. Mekanizma: ağırlık tip parçasını geri itiyor, boşalan slotu genişletme
#: olmadan sıradaki (çoğu zaman yine bir tip) parça dolduruyor; genişletme varken
#: o slot gerçekten farklı bir dosyaya gidiyor.
#:
#: Kapatmak için `HybridSearch(..., weigh_chunks=False)`.
TYPE_ONLY_WEIGHT = 0.4


#: Yalnızca başka bir ada takma ad veren atama: `MessageStreamEvent = RawMessageStreamEvent`.
#: Sağ taraf çıplak bir ad (ya da nitelenmiş ad) ise yeniden dışa aktarımdır;
#: `DEFAULT_MAX_RETRIES = 2` gibi gerçek bir değer değildir.
_REEXPORT_ASSIGNMENT = re.compile(r"^\w+(\s*:[^=]+)?\s*=\s*[A-Za-z_][\w.]*\s*$")
_IGNORED_LINE = re.compile(r"^\s*(import |from |__all__\s*=|#|\"\"\"|$)")


#: Yeniden dışa aktarım kabuklarının geri plana atılması. **Ölçüldü ve
#: genellenmedi, varsayılan kapalı.** Dev bölmesinde MRR 0.694 → 0.736; test
#: bölmesinde 0.847 → 0.847, yani hiç. Fikir doğru görünüyor (kural, metotsuz
#: sınıflar için zaten uygulanan kuralın modül karşılığı) ve maliyeti yok, ama
#: ölçülmüş faydası da yok.
DEMOTE_REEXPORT_MODULES = False


def is_reexport_module(text: str) -> bool:
    """Modül parçası hiçbir çalışma zamanı değeri taşımıyor mu?

    "Metotsuz sınıf, alan listesinden ibarettir" kuralının modül karşılığı.
    `types/message_stream_event.py` import + `__all__` + tek bir takma addan
    ibaret; `_constants.py` ise `DEFAULT_MAX_RETRIES = 2` gibi gerçek değerler
    taşıyor ve "varsayılan zaman aşımı kaç" sorusunun cevabı orada.

    Ayrımı dosya yoluna göre yapmak yanlış olurdu: bu SDK'da dosyaların %92'si
    "generated" işaretli, gerçek cevapları taşıyanlar dahil.
    """
    if "def " in text or "class " in text:
        return False
    for line in text.splitlines():
        if _IGNORED_LINE.match(line):
            continue
        if not _REEXPORT_ASSIGNMENT.match(line.strip()):
            return False
    return True


def chunk_weight(record: dict) -> float:
    """Parçanın bilgi yoğunluğuna göre ağırlık.

    İki biçim geri plana atılıyor: metot içermeyen sınıf parçası (alan listesi)
    ve hiçbir değer taşımayan modül parçası (yeniden dışa aktarım kabuğu).
    `def` geçip geçmediğine bakmak, üretilmiş dosya işaretine bakmaktan
    sağlam: bu SDK'da dosyaların %92'si "generated" işaretli, `_client.py`
    ve `_constants.py` dahil — yani o işaret ayırt edici değil.
    """
    text = record.get("text", "")
    kind = record.get("kind")
    if kind == "class" and "def " not in text:
        return TYPE_ONLY_WEIGHT
    if kind == "module" and DEMOTE_REEXPORT_MODULES and is_reexport_module(text):
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

def extend_with_sibling_files(ranked: list, path_of, selected: list, slots: int, scan: int) -> list:
    """Sonuçlarda güçlü temsil edilen dizinlerin görülmemiş dosyalarını ekler.

    `extend_with_unseen_files` gibi hiçbir şeyi elemiyor, yalnızca ekliyor.
    Farkı hangi birime baktığı: o dosyaya bakıyor, bu dizine. Arama doğru
    dizini bulup yanlış dosyayı getirdiğinde devreye giren şey bu.
    """
    if slots <= 0:
        return selected

    def directory(path: str) -> str:
        return path.rsplit("/", 1)[0] if "/" in path else ""

    seen_paths = {path_of(item) for item in selected}
    counts: collections.Counter = collections.Counter(directory(p) for p in seen_paths)
    strong = {d for d, n in counts.items() if n >= DIRECTORY_TRIGGER}
    if not strong:
        return selected

    added = 0
    for item in ranked[:scan]:
        if added >= slots:
            break
        path = path_of(item)
        if path in seen_paths or directory(path) not in strong:
            continue
        seen_paths.add(path)
        selected.append(item)
        added += 1
    return selected


def _resolve_relative_import(importer: str, level: int, module: str | None) -> str:
    """Göreli import'u dosya yoluna çevirir.

    `lib/environments/_poller.py` içindeki `from .._retry import ...` →
    `lib/_retry.py`. Nokta sayısı kaç paket yukarı çıkılacağını söylüyor.
    """
    parts = importer.split("/")[:-1]
    if level > 1:
        parts = parts[: -(level - 1)] if level - 1 <= len(parts) else []
    if module:
        parts = parts + module.split(".")
    return "/".join(parts) + ".py"


@dataclass
class ImportGraph:
    """İki yönlü import bağı.

    Yön ayrımı önemli: `users` "bu dosyayı kimler kullanıyor", `imports` ise
    "bu dosya neyi kullanıyor". İkisi farklı soruları çözüyor — birincisi
    "şu yardımcıyı kimler çağırıyor", ikincisi "şu akışın dayandığı tanımlar
    nerede".
    """

    users: dict[str, set[str]]
    imports: dict[str, set[str]]


def build_import_graph(records: list[dict]) -> ImportGraph:
    """İmport bağını iki yönde birden çıkarır.

    Kaynak, modül parçalarının metnindeki import satırları — indeksleme sırasında
    zaten toplanıyorlar, ayrıca bir tarama gerekmiyor.
    """
    modules = {r["path"] for r in records if r["kind"] == "module"}
    users: dict[str, set[str]] = {}
    imports: dict[str, set[str]] = {}
    for record in records:
        if record["kind"] != "module":
            continue
        importer = record["path"]
        for level, module in _RELATIVE_IMPORT.findall(record["text"]):
            target = _resolve_relative_import(importer, len(level), module or None)
            if target in modules and target != importer:
                users.setdefault(target, set()).add(importer)
                imports.setdefault(importer, set()).add(target)
    return ImportGraph(users=users, imports=imports)


def _inject_module(records_by_path: dict, selected: list, seen: set, path: str) -> None:
    """Bir dosyanın modül parçasını sonuca ekler.

    Sıralamadan gelmediği için skoru yok; kaynağı `reference` olarak
    işaretleniyor ki çıktıda hangi parçanın import bağıyla geldiği görünsün.
    """
    seen.add(path)
    selected.append(SearchHit(record=records_by_path[path], score=0.0, sources=("reference",)))


def _module_records(records: list[dict]) -> dict[str, dict]:
    return {r["path"]: r for r in records if r["kind"] == "module"}


def extend_with_referencing_files(
    records: list[dict],
    graph: ImportGraph,
    selected: list,
    path_of,
    slots: int,
    max_users: int = REFERENCE_MAX_USERS,
) -> list:
    """Geri yön: seçimdeki dosyaları **kullanan** dosyaların modül parçasını ekler.

    Diğer genişletmelerden farkı, sıralamaya hiç bakmaması: eklenen dosya aday
    havuzunda olmayabilir, çoğu zaman değil de. Bir ölçümde beklenen dosya
    sıralamada 136. sıradaydı; taramayı o derinliğe açmak araya 135 gürültü
    almak demekti.

    Bu yön kendi başına seçici: az kullanıcılı bir dosyayı çağıran yerler o
    dosyanın varlık sebebini anlatıyor. Çok kullanıcılı dosyalar (hub) atlanıyor.
    """
    if slots <= 0:
        return selected

    modules = _module_records(records)
    seen = {path_of(item) for item in selected}
    kalan = slots
    for item in list(selected):
        if kalan <= 0:
            break
        users = graph.users.get(path_of(item), set())
        if not users or len(users) > max_users:
            continue
        for path in sorted(users):
            if kalan <= 0:
                break
            if path not in seen and path in modules:
                _inject_module(modules, selected, seen, path)
                kalan -= 1
    return selected


def extend_with_imported_files(
    records: list[dict],
    graph: ImportGraph,
    selected: list,
    path_of,
    slots: int,
    rank_key,
    hub_users: int = REFERENCE_HUB_USERS,
) -> list:
    """İleri yön: seçimdeki dosyaların **kullandığı** dosyaların modül parçasını ekler.

    Geri yönden daha gürültülü, çünkü sıradan bir modül on küsur şey import
    ediyor ve çoğu (`_models.py`, `_base_client.py`) her dosyada geçiyor. Üç
    eleme var: değer taşımayan yeniden dışa aktarım kabukları atlanıyor, çok
    kullanıcılı hub'lar atlanıyor, kalanlar sorguya yakınlığa göre sıralanıyor.
    İmport bağı adayları binlerden bir avuca indiriyor, benzerlik o avucun
    içinde seçiyor.

    **Ölçüldü ve genellenmedi**, gerekçesi `REFERENCE_FORWARD_SLOTS` üzerinde.
    """
    if slots <= 0 or rank_key is None:
        return selected

    modules = _module_records(records)
    seen = {path_of(item) for item in selected}
    candidates = {
        path
        for item in list(selected)
        for path in graph.imports.get(path_of(item), set())
        if path in modules
        and path not in seen
        and len(graph.users.get(path, ())) <= hub_users
        and not is_reexport_module(modules[path]["text"])
    }
    for path in sorted(candidates, key=lambda p: -rank_key(modules[p]))[:slots]:
        _inject_module(modules, selected, seen, path)
    return selected


def resolve_mode(mode: str | None, provider: str) -> str:
    """Mod verilmediyse sağlayıcıya göre seçer.

    İkisi bağımsız değil ve yanlış eşleşme sessizce kalite kaybettiriyor:

    - `hash` anahtarsız yer tutucu, anlamsal arama yapmıyor. Onunla vektör
      araması zayıf kalıyor ve BM25 tarafı taşıyor (kendi repo, 20 soru:
      hash+vector recall %65 / MRR 0.230, hash+hybrid %90 / 0.416).
    - Gerçek bir sağlayıcıda tablo tersine dönüyor: voyage ile büyük İngilizce
      repoda saf vektör hibriti açık ara geçiyor (akış kapsamı 0.750 vs 0.683).

    Eskiden varsayılan sabit `hybrid`'di ve kullanıcının `--provider voyage`
    verirken `--mode vector` de vermesi gerekiyordu; vermezse aracın kötü
    çalıştığını sanıyordu. README bunu uyarı olarak belgeliyordu — uyarmak
    yerine düzeltmek daha iyi.
    """
    if mode:
        return mode
    return "hybrid" if provider == "hash" else "vector"


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
        weigh_chunks: bool = True,
        diversity_slots: int = DIVERSITY_SLOTS,
        directory_slots: int = DIRECTORY_SLOTS,
        reference_slots: int = REFERENCE_SLOTS,
        reference_forward_slots: int = REFERENCE_FORWARD_SLOTS,
    ):
        self.records = records
        self.bm25 = BM25Search(records)
        self.vector = VectorSearch(records, vectors, embedder)
        self.reranker = reranker
        self.weights = weights or {}
        self.pool_size = pool_size
        self.weigh_chunks = weigh_chunks
        self.diversity_slots = diversity_slots
        self.directory_slots = directory_slots
        self.reference_slots = reference_slots
        self.reference_forward_slots = reference_forward_slots
        # İmport grafiği ilk ihtiyaçta kuruluyor: kurulumu ucuz ama referans
        # genişletmesi kapalıysa hiç gerekmiyor.
        self._import_graph: ImportGraph | None = None

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
        if not candidates:
            return []

        if use_rerank and self.reranker is not None:
            # Reranker'a havuzun tamamı gidiyor, genişletmeler ondan sonra
            # uygulanıyor: önce alaka sırası düzelsin, eklemeler düzelmiş sıranın
            # arkasına gelsin. top_k=k verilseydi eklenecek aday kalmazdı.
            documents = [
                f"{self.records[index]['context']}\n\n{self.records[index]['text']}"
                for index, _, _ in candidates
            ]
            sources_by_position = {position: item[2] for position, item in enumerate(candidates)}
            ranked = [
                SearchHit(
                    record=self.records[candidates[position][0]],
                    score=score,
                    sources=sources_by_position[position] + ("rerank",),
                )
                for position, score in self.reranker.rerank(query, documents, top_k=len(documents))
            ]
        else:
            ranked = [
                SearchHit(record=self.records[index], score=score, sources=sources)
                for index, score, sources in candidates
            ]

        return self._expand(ranked, k, query)

    def _rank_key(self, query: str):
        """İmport komşuluğundaki adayları sorguya yakınlığa göre sıralar.

        Adaylar aday havuzunda olmayabildiği için sıralama bilgileri yok; tek
        elde kalan sinyal vektör benzerliği. Sorgu vektörü zaten önbellekte.
        """
        try:
            query_vector = self.vector.embedder.embed_query(query)
        except Exception:
            return None
        by_hash = {r["content_hash"]: i for i, r in enumerate(self.records)}

        def score(record: dict) -> float:
            index = by_hash.get(record["content_hash"])
            return float(self.vector.vectors[index] @ query_vector) if index is not None else 0.0

        return score

    def _expand(self, ranked: list[SearchHit], k: int, query: str) -> list[SearchHit]:
        """Alaka sırasının arkasına üç ayrı eksende ekleme yapar.

        Üçü de eleme yapmıyor, yalnızca ekliyor — sert kota denendiğinde ilk k
        korunmadığı için sembol isabeti düşmüştü. Sıra da rastgele değil: önce
        dosya (en genel), sonra dizin (daha dar), en sonra import bağı (en
        seçici ve sıralamadan bağımsız).
        """

        def path_of(hit: SearchHit) -> str:
            return hit.record["path"]

        selected = extend_with_unseen_files(
            ranked, path_of, k, self.diversity_slots, DIVERSITY_SCAN
        )
        selected = extend_with_sibling_files(
            ranked, path_of, selected, self.directory_slots, DIVERSITY_SCAN
        )
        if self.reference_slots > 0 or self.reference_forward_slots > 0:
            if self._import_graph is None:
                self._import_graph = build_import_graph(self.records)
            selected = extend_with_referencing_files(
                self.records, self._import_graph, selected, path_of, self.reference_slots
            )
            selected = extend_with_imported_files(
                self.records,
                self._import_graph,
                selected,
                path_of,
                self.reference_forward_slots,
                self._rank_key(query) if self.reference_forward_slots > 0 else None,
            )
        return selected
