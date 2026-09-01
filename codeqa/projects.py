"""Proje kaydı: aracı tek bir repoya bağlı olmaktan çıkarır.

CLI'da proje, her çağrıda `-i` ve `--repo` bayraklarıyla veriliyor. Web arayüzü
için bu yetmiyor: kullanıcı projeyi bir kere ekleyip listeden seçebilmeli. Bu
modül o listeyi (`data/projects.json`) ve projeyi eklerken yapılan iki adımı
tutuyor.

İki adım bilerek ayrı:

- **Tara** (`scan_repo`) — indeksler, bileşimi çıkarır, maliyeti tahmin eder.
  Bedava, yerel, ağ yok.
- **Vektörleştir** (`embed_project`) — asıl para harcayan adım.

Arada onay ekranı olsun diye ayrıldılar: 10 bin dosyalık bir monorepoyu yanlışlıkla
tıklayan biri, ne kadar tutacağını görmeden harcamamalı.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .docs import index_docs
from .embeddings import EmbeddingCache, embed_records, get_embedder
from .indexer import DEFAULT_EXCLUDES, index_repo
from .models import read_jsonl, write_jsonl

#: Kaydın durduğu yer. İndekslerle aynı dizinde: ikisi de üretilen veri.
DEFAULT_REGISTRY = Path("data/projects.json")

#: `voyage-code-3` MTok başına dolar. **Tahmin amaçlı** — sağlayıcı fiyatı
#: değiştirirse burası güncellenmeli. Arayüz sayıyı "tahmin" diye gösteriyor,
#: kesin tutar değil.
EMBED_PRICE_PER_MTOK = 0.18

#: Karakterden token'a kaba çeviri. Kod için 4 makul bir yaklaşım; tahmini
#: birkaç puan şaşırtabilir, büyüklük mertebesini tutturur.
CHARS_PER_TOKEN = 4

#: Yolunda `test` geçen parçalar üretim kodu sayılmıyor. Bileşim özeti için;
#: doküman ve test oranının sonucu etkilediği ölçüldü (README, Öğrenilenler).
_TEST_PATTERN = re.compile(r"(^|/)(tests?|testing)(/|$)|(^|/)test_[^/]*$|_test\.[a-z]+$")


@dataclass
class Project:
    """Kayıtlı bir kod tabanı."""

    id: str
    name: str
    repo: str
    index: str
    provider: str = "voyage"
    model: str | None = None
    mode: str | None = None  # None: sağlayıcıdan çözülüyor
    chunks: int = 0
    files: int = 0
    added: str = ""
    #: Proje eklenirken koşan ucuz kontroller (duman testi, dil işareti).
    #: Ölçüm değil — ne olduğu `smoke_test` ve `language_signal` docstring'lerinde.
    checks: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "proje"


def load_projects(registry: Path = DEFAULT_REGISTRY) -> list[Project]:
    path = Path(registry)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Bozuk kayıt sunucuyu açılmaz hâle getirmesin; boş listeyle başlanır.
        return []
    return [Project(**entry) for entry in data.get("projects", [])]


def save_projects(projects: list[Project], registry: Path = DEFAULT_REGISTRY) -> None:
    path = Path(registry)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"projects": [p.to_dict() for p in projects]}
    # Atomik yazma: yarıda kesilen bir kayıt listeyi bozmasın.
    temporary = path.with_suffix(".tmp.json")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def upsert(projects: list[Project], project: Project) -> list[Project]:
    """Aynı id'li kayıt varsa değiştirir, yoksa ekler."""
    result = [p for p in projects if p.id != project.id]
    result.append(project)
    return result


def composition(records: list[dict]) -> dict:
    """İndeksin ne kadarının üretim kodu olduğunu söyler.

    Neden gösteriliyor: Prometheus ölçümünde indeksin %51'i üretim kodu değildi
    ve karşılaştırma bu yüzden yanıltıcı çıktı. Markdown parçalarının kod
    sonuçlarını bastırdığı da ayrıca ölçüldü (MRR 0.406 → 0.586). Kullanıcı
    projeyi eklerken bu oranı görmeli.
    """
    counts = {"kod": 0, "test": 0, "doküman": 0}
    for record in records:
        path = record.get("path", "")
        if record.get("kind") == "section" or path.endswith(".md"):
            counts["doküman"] += 1
        elif _TEST_PATTERN.search(path):
            counts["test"] += 1
        else:
            counts["kod"] += 1
    total = sum(counts.values()) or 1
    return {
        "counts": counts,
        "percent": {key: round(value * 100 / total) for key, value in counts.items()},
    }


#: Türkçeye özgü harfler. Reponun metninin hangi dilde olduğunu anlamanın en
#: ucuz yolu; tam bir dil tespiti değil, gerek de yok — ayırt etmesi gereken tek
#: şey "sorunun kelimeleri bu kodda geçer mi".
_TURKISH_LETTERS = set("ğĞşŞıİçÇöÖüÜ")

#: Bu oranın üstünde Türkçe metin taşıyan repoda anahtar kelime araması işe
#: yarayabiliyor. **Eşik doğrulanmadı**: elde iki ölçüm noktası var (kendi repo
#: %65, anthropic SDK %1) ve 20 ikisinin arasından seçildi. Öneri üretmek için
#: yeterli, rapor etmek için değil.
TURKISH_THRESHOLD = 20


def language_signal(records: list[dict]) -> dict:
    """Reponun metni Türkçe mi İngilizce mi — mod önerisinin dayanağı.

    Ölçülen mekanizma şu: BM25 ancak sorunun kelimeleri kodda geçtiğinde
    tutunuyor. Sorular Türkçe olduğuna göre soru "bu repo Türkçe mi" hâline
    geliyor. Ölçümde büyük İngilizce repoda BM25 recall'ı %95'ten %15'e
    düşmüştü; Türkçe yorumlu küçük repoda hibrit en iyisiydi.

    **Bu bir ölçüm değil, işaret.** İki kod tabanına bakılarak kuruldu; gerçek
    cevap o repoda soru setiyle ölçmekten geçiyor.
    """
    if not records:
        return {"percent": 0, "suggested_mode": "vector", "measured": False}
    turkish = sum(1 for r in records if _TURKISH_LETTERS & set(r.get("text", "")))
    percent = round(turkish * 100 / len(records))
    turkish_ish = percent >= TURKISH_THRESHOLD
    return {
        "chunks": turkish,
        "percent": percent,
        "threshold": TURKISH_THRESHOLD,
        "suggested_mode": "hybrid" if turkish_ish else "vector",
        "measured": False,
        "reason": (
            "Reponun metni ağırlıklı Türkçe: Türkçe sorunun kelimeleri kodda geçebiliyor, "
            "anahtar kelime araması katkı sağlayabilir."
            if turkish_ish
            else "Reponun metni ağırlıklı İngilizce: Türkçe sorunun kelimeleri kodda "
            "geçmiyor, anahtar kelime aramasının tutunacağı yer yok."
        ),
    }


#: Duman testinde sorulacak sembol sayısı.
#:
#: **Bedava değil:** her sorgu sağlayıcıya bir embedding çağrısı yapıyor, yani
#: 25 sorgu = 25 çağrı, ölçülen süre ~7 saniye. Sorgu vektörleri önbelleğe
#: girdiği için ikinci koşu bedava. Tutar bir sentin çok altında ama "yerel ve
#: ücretsiz" demek yanlış olur.
SMOKE_SAMPLE = 25


def smoke_test(searcher, sample: int = SMOKE_SAMPLE, k: int = 8) -> dict:
    """İndeksten sembol soruları üretip aramanın hiç çalıştığını kontrol eder.

    **Doğruluk ölçümü değil.** Sembolün adını sorup onu bulmak neredeyse
    `grep`; aracın değeri kelimesi kodda geçmeyen dolaylı sorularda. Dahası bu
    sorular anahtar kelime aramasını sistematik olarak kayırıyor, o yüzden mod
    seçmek için **kullanılamaz** — kullanılırsa her repoda "hibrit daha iyi"
    çıkar, hibritin çöktüğü repolarda bile.

    Yakaladığı şey gerçek ve dar: indeks bozuk mu, vektörler eksik mi, arama
    hiç dönüyor mu. Yeni bir repo eklendikten sonra "çalışıyor mu" sorusunun
    cevabı.

    Örneklem iki kez süzülüyor:

    - İndekste **tek kez** geçen semboller; iki dosyada aynı adı taşıyan bir
      fonksiyonun doğru cevabı belirsiz olurdu.
    - `chunk_weight` ile geri plana atılmayan parçalar. Metotsuz tip sınıfları
      arama katmanı tarafından **bilerek** geri itiliyor (ölçülmüş bir karar);
      onları sorup "bulunamadı" saymak, aracın kasıtlı davranışını arıza gibi
      göstermek olurdu. İlk koşuda anthropic SDK'sında sonuç bu yüzden 17/25
      çıkmıştı ve kaçırılanların hepsi üretilmiş tip sınıfıydı.
    """
    from .search import chunk_weight

    records = searcher.records
    seen: dict[str, list[dict]] = {}
    for record in records:
        if record.get("kind") in ("function", "method", "class") and chunk_weight(record) == 1.0:
            seen.setdefault(record["name"], []).append(record)

    unique = [group[0] for group in seen.values() if len(group) == 1]
    # Sabit sıralama: aynı indekste aynı sonuç çıksın.
    unique.sort(key=lambda r: r["location"])
    if not unique:
        return {"total": 0, "found": 0, "rate": None, "misses": []}
    step = max(1, len(unique) // sample)
    chosen = unique[::step][:sample]

    found, misses = 0, []
    for record in chosen:
        hits = searcher.search(record["name"], k=k)
        if any(hit.record["location"] == record["location"] for hit in hits):
            found += 1
        elif len(misses) < 5:
            misses.append(f"{record['name']} ({record['location']})")
    return {
        "total": len(chosen),
        "found": found,
        "rate": round(found / len(chosen), 3),
        "misses": misses,
        "measured": False,
    }


def estimate_embedding(records: list[dict], cache: EmbeddingCache) -> dict:
    """Kaç parçanın embed edileceğini ve kabaca ne tutacağını söyler."""
    missing = [r for r in records if r["content_hash"] not in cache]
    characters = sum(len(r["text"]) + len(r.get("context") or "") for r in missing)
    tokens = characters / CHARS_PER_TOKEN
    return {
        "total": len(records),
        "cached": len(records) - len(missing),
        "missing": len(missing),
        "characters": characters,
        "tokens": round(tokens),
        "cost": round(tokens / 1e6 * EMBED_PRICE_PER_MTOK, 4),
        "price_per_mtok": EMBED_PRICE_PER_MTOK,
    }


def scan_repo(
    repo: Path,
    index_path: Path,
    provider: str = "voyage",
    model: str | None = None,
    no_docs: bool = False,
    excludes: frozenset[str] = DEFAULT_EXCLUDES,
) -> dict:
    """Repoyu indeksler ve maliyet önizlemesi döner. Para harcamaz.

    `get_embedder` burada da çağrılıyor çünkü önbellek adı sağlayıcıya bağlı ve
    "kaç parça zaten var" sorusu ancak doğru önbellekte cevaplanabilir.
    """
    root = Path(repo).expanduser()
    if not root.is_dir():
        raise ValueError(f"Dizin bulunamadı: {root}")
    root = root.resolve()

    chunks, stats = index_repo(root, excludes)
    records = [chunk.to_dict() for chunk in chunks]
    if not no_docs:
        records += [chunk.to_dict() for chunk in index_docs(root, excludes)]
    if not records:
        raise ValueError(f"{root} içinde indekslenecek Python dosyası bulunamadı.")

    index_path = Path(index_path)
    write_jsonl(records, index_path)

    embedder = get_embedder(provider, model)
    return {
        "repo": str(root),
        "index": str(index_path),
        "name": root.name,
        "files": stats.files_scanned,
        "failed": stats.files_failed,
        "errors": stats.errors[:5],
        "chunks": len(records),
        "composition": composition(records),
        "language": language_signal(records),
        "estimate": estimate_embedding(records, EmbeddingCache(embedder.name)),
        "provider": provider,
    }


def embed_project(
    index_path: Path,
    provider: str = "voyage",
    model: str | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> int:
    """İndeksteki parçaları vektörleştirir; kaç yenisinin hesaplandığını döner."""
    records = read_jsonl(index_path)
    embedder = get_embedder(provider, model)
    _, computed = embed_records(
        records, embedder, EmbeddingCache(embedder.name), on_progress=on_progress
    )
    return computed


# --- arka plan işleri ------------------------------------------------------


@dataclass
class Job:
    """Uzun süren bir işin durumu.

    Web arayüzü bunu yokluyor. SSE yerine yoklama: `http.server` üzerinde açık
    tutulan bağlantıyı yönetmek gereksiz karmaşık, iş zaten dakikalar sürüyor.
    """

    id: str
    kind: str
    state: str = "çalışıyor"  # çalışıyor | bitti | hata
    done: int = 0
    total: int = 0
    message: str = ""
    error: str = ""
    result: dict = field(default_factory=dict)
    started: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["elapsed"] = round(time.time() - self.started, 1)
        return data


class JobRunner:
    """İşleri arka planda koşturur, durumlarını bellekte tutar."""

    def __init__(self, keep: int = 20):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._keep = keep

    def start(self, kind: str, work: Callable[[Job], dict]) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind)
        with self._lock:
            self._jobs[job.id] = job
            # Eski işleri bellekte biriktirme.
            if len(self._jobs) > self._keep:
                for old in sorted(self._jobs.values(), key=lambda j: j.started)[: -self._keep]:
                    self._jobs.pop(old.id, None)

        def run() -> None:
            try:
                job.result = work(job)
                job.state = "bitti"
            except Exception as exc:
                job.state = "hata"
                job.error = f"{type(exc).__name__}: {exc}"

        threading.Thread(target=run, daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)
