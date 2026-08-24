"""Contextual retrieval: her parçaya LLM ile üretilmiş bağlam cümlesi ekler.

Sorun şu: bir parça tek başına arandığında, dosyanın bütününde ne işe yaradığı
bilgisini taşımıyor. `def _should_retry(self, response)` fonksiyonu kendi başına
"hangi durum kodlarında yeniden deneme yapılıyor" sorusuna cevap gibi durmuyor;
bunu anlamak için dosyanın HTTP istemcisi olduğunu bilmek gerekiyor.

Çözüm: parçayı dosyanın tamamıyla birlikte modele verip "bu parça burada ne
yapıyor" diye bir cümle yazdırmak ve o cümleyi embedding'e katmak.

Maliyet kontrolü iki yerden geliyor:
- **Prompt caching**: aynı dosyanın içeriği o dosyadaki her parça için tekrar
  gönderiliyor. Önbellek olmadan bu, dosya boyutu × parça sayısı kadar token
  demek. Dosya bir kez cache'e yazılıp sonraki parçalarda okunuyor.
- **Disk önbelleği**: üretilen cümleler içerik karmasına göre saklanıyor,
  değişmemiş parça için yeniden LLM çağrılmıyor.

Bağlam cümlesinin dili ayarlanabilir. Sorular Türkçe ama kod İngilizce olduğunda
arama sözcük düzeyinde tutunacak yer bulamıyor; bağlamı soru diliyle üretmek bu
açığı doğrudan hedefliyor.
"""

from __future__ import annotations

import json
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .models import _hash


def _hash_embed_text(record: dict) -> str:
    """Kaydın embedding'e giden metninin karması. `Chunk.content_hash` ile aynı kural."""
    return _hash(f"{record['context']}\n\n{record['text']}")

#: Bağlam üretimi hacimli ve basit bir iş — ucuz model doğru tercih.
DEFAULT_MODEL = "claude-haiku-4-5"

#: Dosyanın modele gönderilecek azami uzunluğu. Çok büyük dosyalarda bağlamın
#: tamamını göndermek hem pahalı hem gereksiz; parçanın çevresi yeterli.
MAX_FILE_CHARS = 60_000

PROMPTS = {
    "tr": (
        "Sana bir kaynak dosya ve o dosyadan bir parça veriliyor.\n"
        "Bu parçanın dosyanın bütününde ne işe yaradığını **Türkçe**, tek cümlede yaz.\n"
        "Arama motorunda bulunabilirliği artırmak için yazıyorsun: parçanın hangi işi\n"
        "yaptığını ve hangi kavramla ilgili olduğunu belirt.\n"
        "Sadece o cümleyi yaz, başka hiçbir şey yazma."
    ),
    "en": (
        "You are given a source file and one chunk from it.\n"
        "Write one sentence in **English** situating this chunk within the file.\n"
        "You are writing to improve search retrieval: state what the chunk does and\n"
        "which concept it relates to.\n"
        "Answer with that sentence only, nothing else."
    ),
}


class ContextGenerator:
    """Parçalar için bağlam cümlesi üretir ve diskte önbelleğe alır."""

    def __init__(
        self,
        repo_root: Path,
        client=None,
        model: str = DEFAULT_MODEL,
        language: str = "tr",
        cache_path: Path = Path("data/contexts.json"),
        workers: int = 6,
        max_retries: int = 4,
    ):
        if language not in PROMPTS:
            raise ValueError(f"Desteklenmeyen dil: {language} ({' | '.join(PROMPTS)})")
        self.repo_root = Path(repo_root)
        self.model = model
        self.language = language
        self.cache_path = Path(cache_path)
        self._client = client
        self.workers = workers
        self.max_retries = max_retries
        self.usage: dict[str, int] = {}
        #: Kalıcı olarak başarısız olan parçalar; koşu sonunda raporlanıyor.
        self.failures: list[str] = []
        #: Koşuyu durduran kalıcı hata (kredi bitti, anahtar geçersiz gibi).
        self.aborted: str | None = None
        # Dosyalar paralel işleniyor, önbellek sözlüğü paylaşımlı.
        self._lock = threading.Lock()
        self._cache: dict[str, str] = {}
        if self.cache_path.exists():
            self._cache = json.loads(self.cache_path.read_text(encoding="utf-8"))

    def _get_client(self):
        if self._client is None:
            import anthropic

            # Varsayılan 10 dk değil ama büyük dosyalarda 1 dk da kısa kalıyor;
            # 8 eşzamanlı istekte kuyruk beklemesi de buna ekleniyor.
            self._client = anthropic.Anthropic(timeout=180.0, max_retries=3)
        return self._client

    def _cache_key(self, record: dict) -> str:
        # Dil ve model anahtara giriyor: farklı dilde üretilmiş cümleler
        # birbirinin yerine kullanılamaz.
        return f"{self.language}::{self.model}::{record['content_hash']}"

    def _read_file(self, path: str) -> str:
        target = self.repo_root / path
        if not target.is_file():
            return ""
        return target.read_bytes().decode("utf-8", errors="replace")[:MAX_FILE_CHARS]

    def describe(self, record: dict, file_source: str) -> str:
        """Tek bir parça için bağlam cümlesi üretir.

        Geçici hatalarda yeniden deniyor; kalıcı hatada boş dönüp koşuyu
        sürdürüyor. Binlerce çağrılık bir işte tek bir zaman aşımının tüm
        koşuyu düşürmesi kabul edilemez — o parça bağlamsız kalsın, iş devam
        etsin. Eksik kalanlar bir sonraki koşuda tamamlanıyor.
        """
        key = self._cache_key(record)
        with self._lock:
            if key in self._cache:
                return self._cache[key]
            if self.aborted:
                return ""

        for attempt in range(self.max_retries):
            try:
                return self._request(record, file_source, key)
            except self._fatal_errors() as exc:
                # Kredi bitmesi gibi durumlarda denemeye devam etmek anlamsız.
                # Koşuyu durduruyoruz ama o ana kadarki iş önbellekte kalıyor,
                # sorun giderilince kalınan yerden devam ediliyor.
                with self._lock:
                    self.aborted = str(exc).split(" - ")[-1][:200]
                return ""
            except self._transient_errors() as exc:
                if attempt == self.max_retries - 1:
                    with self._lock:
                        self.failures.append(f"{record['id']}: {type(exc).__name__}")
                    return ""
                time.sleep(2 ** attempt)
        return ""  # pragma: no cover

    @staticmethod
    def _fatal_errors() -> tuple[type[BaseException], ...]:
        """Tekrar denemenin anlamsız olduğu hatalar: kredi bitti, anahtar geçersiz."""
        import anthropic

        return (
            anthropic.BadRequestError,
            anthropic.AuthenticationError,
            anthropic.PermissionDeniedError,
        )

    @staticmethod
    def _transient_errors() -> tuple[type[BaseException], ...]:
        import anthropic

        return (
            anthropic.APITimeoutError,
            anthropic.APIConnectionError,
            anthropic.RateLimitError,
            anthropic.InternalServerError,
        )

    def _request(self, record: dict, file_source: str, key: str) -> str:
        client = self._get_client()
        response = client.messages.create(
            model=self.model,
            max_tokens=200,
            system=PROMPTS[self.language],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"<dosya path=\"{record['path']}\">\n{file_source}\n</dosya>",
                            # Dosya içeriği o dosyadaki her parça için tekrar
                            # gönderiliyor; önbelleklenmezse maliyet dosya
                            # boyutu × parça sayısı kadar oluyor.
                            "cache_control": {"type": "ephemeral"},
                        },
                        {
                            "type": "text",
                            "text": (
                                f"<parça name=\"{record['name']}\" "
                                f"lines=\"{record['start_line']}-{record['end_line']}\">\n"
                                f"{record['text']}\n</parça>"
                            ),
                        },
                    ],
                }
            ],
        )
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        with self._lock:
            self._cache[key] = text
            for name in (
                "input_tokens",
                "output_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
            ):
                self.usage[name] = self.usage.get(name, 0) + (
                    getattr(response.usage, name, 0) or 0
                )
        return text

    def save(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps(self._cache, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    def enrich(self, records: list[dict], progress: bool = False) -> tuple[list[dict], int]:
        """Kayıtların `context` alanını LLM cümlesiyle zenginleştirir.

        Aynı dosyanın parçaları arka arkaya işleniyor: prompt önbelleği ancak
        böyle tutuyor, araya başka dosya girerse önek değişiyor ve önbellek
        boşa gidiyor.
        """
        by_file: dict[str, list[dict]] = defaultdict(list)
        for record in records:
            by_file[record["path"]].append(record)

        enriched: dict[str, dict] = {}
        counter = {"files": 0, "generated": 0}

        def process(item: tuple[str, list[dict]]) -> list[dict]:
            path, group = item
            source = self._read_file(path)
            output = []
            for record in group:
                with self._lock:
                    was_cached = self._cache_key(record) in self._cache
                sentence = self.describe(record, source) if source else ""
                copy = dict(record)
                if sentence:
                    copy["context"] = f"{record['context']}\n{sentence}"
                    copy["llm_context"] = sentence
                    # Bağlam değişti, yani embedding'e giden metin de değişti.
                    # Karma güncellenmezse embedding önbelleği eski (bağlamsız)
                    # vektörü döndürür ve tüm iş boşa gider.
                    copy["content_hash"] = _hash_embed_text(copy)
                    if not was_cached:
                        with self._lock:
                            counter["generated"] += 1
                output.append(copy)
            with self._lock:
                counter["files"] += 1
                if progress and counter["files"] % 50 == 0:
                    print(f"  {counter['files']}/{len(by_file)} dosya "
                          f"({counter['generated']} yeni cümle)")
                # Sık kaydetmek önemli: ilk koşu 50. dosyada çöktüğünde
                # 100 dosyada bir kaydettiği için o ana kadar üretilen 723
                # cümle kayboldu. Diske yazmak birkaç bin kayıt için ucuz,
                # yeniden üretmek pahalı.
                if counter["files"] % 20 == 0:
                    self.save()
            return output

        # Aynı dosyanın parçaları tek bir görevde ve sırayla işleniyor: prompt
        # önbelleği ancak böyle tutuyor. Paralellik dosyalar arasında.
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            for group_output in pool.map(process, sorted(by_file.items())):
                for record in group_output:
                    enriched[record["id"]] = record
        generated = counter["generated"]

        self.save()
        return [enriched.get(r["id"], r) for r in records], generated
