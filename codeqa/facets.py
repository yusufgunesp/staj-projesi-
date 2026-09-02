"""Belirsiz sorguyu netleştirme: havuzu dizin ağacına göre öbeklere ayırır.

**Ne sorunu çözüyor.** Ölçüldü (Saleor, `voyage`, k=8): tek kelimelik bir sorgu
kaybolmuyor, aksine **fazla iyi** çalışıyor — ama yalnızca en soyut okumayı
getiriyor. `"ödeme"` sorgusunun ilk sekizi baştan sona arayüz/tip katmanı
(`PaymentError`, `PaymentInterface`, `PaymentsByOrderIdLoader`); tek bir somut
ödeme sağlayıcısı yok. `"ödeme sağlayıcısına istek nasıl gönderiliyor"` ise
tamamen `payment/gateways/*` getiriyor ve **iki listenin ilk sekizde ortak
parçası sıfır.** Yani netleştirme kozmetik değil: cevabın tamamını değiştiriyor.

Türkçe→İngilizce köprüsü bu işin sorunu *değil*, ölçüldü ve çalışıyor
(`ödeme`→`payment`, `yetki`→`permission`, `stok`→`warehouse`, `indirim`→
`discount`; dördünde de doğru alan ilk sırada). Sorun kelime değil, soyutlama
seviyesi.

**Neden derin havuz.** Öbekler sonuç listesinde yok, havuzun dibinde. Aynı
ölçümde `payment/gateways` altındaki sekiz sağlayıcının ilk temsilcisi 15.
sıradaydı, çoğu 48-142 arasında. İlk 8'i ya da ilk 120'yi gruplamak bunları
kaçırırdı; bu yüzden öbekleme `POOL` derinliğindeki ham havuzdan yapılıyor.

**Sıralamaya dokunmuyor.** Bu modül `HybridSearch.search`'ü çağırmıyor, ham
adayları okuyup gruplayıp bırakıyor. Ne ilk k değişiyor ne genişletme slotları;
mevcut soru setlerinin sayıları bu modül yüzünden oynayamaz. Kullanıcı bir öbeği
seçerse arama **yeni bir sorguyla baştan** koşuyor.

**Ölçüldü, ama kazanç ilan edilmedi.** `tools/netlestirme_olcumu.py` var olan
soruları mekanik olarak 1-2 kelimeye daraltıp üç sayı alıyor: *düz* (bugünkü
arama), *menü* (doğru dizin beş öbekten birinde mi), *tavan* (en iyi öbek
seçilseydi). Test bölmeleri, tek kelimelik sorgu:

===========  ==  =====  =====  ======
set           n   düz    menü   tavan
===========  ==  =====  =====  ======
Saleor       26   38%    65%     69%
Zor          26   65%    58%     77%
Akış         30   70%    63%     80%
Kolay        40   52%    42%     62%
===========  ==  =====  =====  ======

**Tavan bir üst sınır, sistem kazancı değil:** öbeği etikete bakarak seçiyor,
kullanıcı bunu yapamaz. Tavan sekiz hücrenin sekizinde de düzü geçiyor (iki
kelimelik daraltmada da), yani yön tutarlı — ama güven aralıkları her yerde
örtüşüyor, o yüzden büyüklük hakkında bir şey söylenemez.

**Bilinen zayıflık: üretilmiş kod.** `menü` sekiz hücrenin beşinde düzün altında
ve en kötüsü anthropic SDK'sında (%42 / %45, düz %52 / %72). Sebebi görülebilir:
o indekste `"akış"` sorgusunun öbekleri `AsyncFilesWithStreamingResponse`,
`AsyncDreamsWithStreamingResponse` gibi üretilmiş sarmalayıcılarla doluyor —
dizinler API yüzeyini yansıtıyor, kavramı değil. Saleor'da ise
`payment/gateways` altında adyen/braintree/razorpay duruyor. **Öbekleme dizin
yapısının anlam taşıdığı kod tabanlarında çalışıyor, üretilmiş kodda bozuluyor.**

Bu yüzden özellik açıkça isteğe bağlı (arayüzde ayrı düğme), varsayılan aramanın
yerine geçmiyor ve arayüz öbekleri doğruluk sayısı gibi sunmuyor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath

#: Öbeklemenin okuduğu ham aday sayısı.
#:
#: 300 keyfi değil: ölçümde `payment/gateways/authorize_net` 111., `stripe` 92.,
#: `dummy` 142. sıradaydı. 150'lik bir havuz sağlayıcıların yarısını kaçırıyor.
#: Havuz derinleştikçe maliyet artmıyor — sorgu vektörü zaten hesaplanmış, geri
#: kalanı bellekteki skorların sıralaması.
POOL = 300

#: Bir dizinin ayrı öbek sayılması için havuzda tutması gereken en az parça.
#:
#: Altındaki dizinler kullanıcıya "burada bir şey var" demiyor, gürültü diyor.
MIN_CHUNKS = 3

#: Ağaç kesme eşiği: havuzun bu kadarından fazlasını kaplayan dizin öbek
#: sayılmıyor, altına iniliyor.
#:
#: Sebebi şu: `saleor/payment` havuzun %37'sini tutuyor ve *içinde* aradığımız
#: ayrım duruyor (`gateways` vs. arayüz katmanı). Öbek olarak sunulsaydı
#: "ödeme, ödeme demektir" denmiş olurdu. Eşik %25'te `saleor/payment` bölünüp
#: `payment/gateways` (57 parça) ayrı bir öbek olarak yüzeye çıkıyor — aranan
#: davranış tam olarak bu.
COVERAGE_CAP = 0.25

#: Kullanıcıya gösterilecek en fazla öbek. Beşten sonrası netleştirme olmaktan
#: çıkıp ikinci bir sonuç listesine dönüyor.
DEFAULT_LIMIT = 5

#: Öbek etiketinde anılacak en fazla sembol / alt dizin.
SAMPLE = 3


@dataclass
class Facet:
    """Sorgunun bir okuması: havuzun bir dizin altında toplanan kısmı."""

    directory: str
    chunks: int
    best_rank: int  # öbeğin havuzdaki en iyi sırası
    symbols: list[str] = field(default_factory=list)
    children: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        """Ekranda görünen satır. Dizin ve parça sayısı **her zaman** burada:
        öneri metni yanlışsa kullanıcı neye baktığını görebilmeli."""
        parts = [f"{self.directory} · {self.chunks} parça"]
        if self.children:
            extra = len(self.children) - SAMPLE
            names = ", ".join(self.children[:SAMPLE])
            parts.append(f"{names}{f' +{extra}' if extra > 0 else ''}")
        elif self.symbols:
            parts.append(", ".join(self.symbols[:SAMPLE]))
        return " — ".join(parts)

    def query(self, base: str) -> str:
        """Öbek seçilince koşulacak sorgu.

        Özgün sorgu **korunuyor**, üstüne öbeğin kendi sözcükleri ekleniyor.
        Değiştirilseydi kullanıcının sorduğu şey kaybolurdu; eklenen sözcükler
        de uydurma değil, indeksten geliyor.
        """
        terms = [base, PurePosixPath(self.directory).name]
        terms += self.children[:SAMPLE] or self.symbols[:SAMPLE]
        seen, out = set(), []
        for term in terms:
            key = term.lower()
            if key not in seen:
                seen.add(key)
                out.append(term)
        return " ".join(out)

    def to_dict(self) -> dict:
        return {
            "directory": self.directory,
            "chunks": self.chunks,
            "best_rank": self.best_rank,
            "symbols": self.symbols[:SAMPLE],
            "children": self.children[:SAMPLE],
            "children_total": len(self.children),
            "label": self.label,
        }


def _directory(location: str) -> str:
    """`saleor/payment/models.py:441` → `saleor/payment`."""
    return str(PurePosixPath(location.split(":")[0]).parent)


def _cut(tree: dict[str, int], root: str, total: int, cap: int) -> list[str]:
    """Ağacı, her dalı `cap`'in altına düşene kadar aşağı doğru keser.

    Tek kural: bir dizin havuzun `cap`'inden fazlasını kaplıyorsa öbek değil,
    içine inilecek bir dal. `saleor` → `saleor/payment` → (`payment` doğrudan,
    `payment/gateways`) bu şekilde ayrışıyor.
    """
    if tree.get(root, 0) <= cap:
        return [root]
    children = sorted(
        {path for path in tree if path.startswith(root + "/") and "/" not in path[len(root) + 1 :]}
    )
    if not children:
        return [root]
    cuts = [root]  # dizinin kendi dosyaları ayrı bir öbek olarak kalıyor
    for child in children:
        cuts += _cut(tree, child, total, cap)
    return cuts


def facets(
    searcher,
    query: str,
    mode: str = "vector",
    pool: int = POOL,
    limit: int = DEFAULT_LIMIT,
) -> list[Facet]:
    """Sorgunun havuzunu okunabilir öbeklere ayırır.

    `HybridSearch.search` yerine `_candidates` okunuyor: aranan şey ham
    sıralama. `search` genişletme slotlarını uygular ve listeyi k'ya kırpar —
    ikisi de burada istenmeyen şeyler, çünkü öbekler tam olarak kırpılan
    kısımda duruyor.
    """
    candidates = searcher._candidates(query, pool, mode)
    if not candidates:
        return []

    records = [searcher.records[index] for index, _, _ in candidates]
    total = len(records)

    # Her dizin ve her üst dizini için altındaki parça sayısı.
    under: dict[str, int] = {}
    best: dict[str, int] = {}
    for rank, record in enumerate(records, start=1):
        directory = _directory(record["location"])
        node = PurePosixPath(directory)
        for ancestor in [node, *node.parents]:
            key = str(ancestor)
            if key == ".":
                continue
            under[key] = under.get(key, 0) + 1
            best.setdefault(key, rank)

    if not under:
        return []

    roots = sorted({path.split("/")[0] for path in under})
    cap = int(total * COVERAGE_CAP)
    selected = [node for root in roots for node in _cut(under, root, total, cap)]

    # Seçilen düğümün *kendi* öbeği: altındaki toplam değil, o düğümün altında
    # kalıp başka bir öbeğe gitmemiş parçalar.
    chosen = set(selected)
    buckets: dict[str, list[dict]] = {node: [] for node in selected}
    for record in records:
        directory = _directory(record["location"])
        node = PurePosixPath(directory)
        for ancestor in [node, *node.parents]:
            key = str(ancestor)
            if key in chosen:
                buckets[key].append(record)
                break

    out = []
    for node, group in buckets.items():
        if len(group) < MIN_CHUNKS:
            continue
        children = sorted(
            {
                _directory(record["location"])[len(node) + 1 :].split("/")[0]
                for record in group
                if _directory(record["location"]) != node
            }
        )
        symbols = []
        for record in group:
            if record["name"] not in symbols:
                symbols.append(record["name"])
        out.append(
            Facet(
                directory=node,
                chunks=len(group),
                best_rank=best[node],
                symbols=symbols,
                children=children,
            )
        )

    # Büyük öbek önce; eşitlikte havuzda daha yukarıda çıkan önce.
    out.sort(key=lambda facet: (-facet.chunks, facet.best_rank))
    return out[:limit]


def search_within(searcher, query: str, directory: str, k: int, mode: str = "vector") -> list:
    """Öbek seçilince aramayı o dizinin altına hapseder.

    **Ölçüldü ve reddedildi. Kullanılmıyor.** Gerekçesi makuldü: ilk ölçümde
    (Saleor dev, tek kelimelik sorgu) dört soruda doğru dizin öbek listesinde
    çıktı ama tıklandığında beklenen dosya yine ilk 8'e giremedi — eklenen
    sözcükler sıralamayı o dizine *yaklaştırıyor*, oraya *hapsetmiyor*. Süzmek
    çözer gibi duruyordu.

    Ölçüm tersini söyledi (dev, tavan): zor sette %92 → %58, akışta %80 → %70,
    iki kelimelik sorguda Saleor'da %100 → %93. Sebep: beklenen dosya çoğu zaman
    en iyi eşleşen dizinin *dışında* kalıyor ve süzme onu tamamen eliyor.
    Sorguya sözcük eklemek elemediği için o dosyaya hâlâ ulaşabiliyor.

    Kod §5 geleneğine uyarak duruyor: silmek de ölçülmemiş bir iddia olurdu.
    """
    hits = searcher.search(query, k=POOL, mode=mode)
    inside = [
        hit
        for hit in hits
        if _directory(hit.location) == directory
        or _directory(hit.location).startswith(directory + "/")
    ]
    return inside[:k]
