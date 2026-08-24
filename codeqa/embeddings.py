"""Embedding sağlayıcıları ve disk önbelleği.

Sağlayıcı seçimi tek bir arayüzün arkasında: `Embedder`. Voyage AI bulutta,
Ollama local'de çalışır; ikisi de aynı metotları sunar. Bu ayrım baştan
kuruldu çünkü müşteri kodunun dışarı çıkamadığı projelerde local model bir
zorunluluk hâline gelebiliyor — sonradan sökülüp takılması günler alırdı.

İndeksleme sırasında kod tabanının **tamamı** embedding sağlayıcısına gider;
cevaplama sırasında ise sadece bulunan parçalar. Gizlilik açısından asıl
hacim burada, o yüzden bu katmanın takılabilir olması önemli.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np

DEFAULT_CACHE_DIR = Path("data/embeddings")


class Embedder(ABC):
    """Metinleri vektöre çeviren sağlayıcı arayüzü.

    `name` önbellek anahtarı olarak kullanılıyor: farklı modellerin vektörleri
    karıştırılamaz, model değişince yeniden hesaplanır.
    """

    name: str
    dimension: int

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> np.ndarray:
        """İndekslenecek parçaları vektörleştirir. (n, dimension) float32 döner."""

    @abstractmethod
    def embed_query(self, text: str) -> np.ndarray:
        """Kullanıcı sorusunu vektörleştirir. (dimension,) float32 döner.

        Ayrı metot çünkü bazı modeller soru ve doküman için farklı kodlama
        kullanıyor (Voyage'ın `input_type` parametresi).
        """


def retry_on_rate_limit(call, error_type, max_retries: int = 8, base_wait: int = 25):
    """Hız limitine takılan bir Voyage çağrısını artan sürelerle yeniden dener.

    Hem embedding hem reranker aynı limite tabi olduğu için ortak.
    Limitler dakikalık pencerelerde sıfırlandığından bekleme süresi 25 saniyeden
    başlıyor — daha kısası boşuna deneme oluyor.
    """
    for attempt in range(max_retries):
        try:
            return call()
        except error_type:
            if attempt == max_retries - 1:
                raise
            wait = base_wait * (attempt + 1)
            print(f"  hız limiti — {wait} sn bekleniyor ({attempt + 1}/{max_retries})")
            time.sleep(wait)
    raise RuntimeError("ulaşılamaz")  # pragma: no cover


def _normalize(matrix: np.ndarray) -> np.ndarray:
    """Satırları birim uzunluğa getirir; böylece kosinüs benzerliği = iç çarpım."""
    norms = np.linalg.norm(matrix, axis=-1, keepdims=True)
    norms[norms == 0] = 1.0
    return (matrix / norms).astype(np.float32)


class VoyageEmbedder(Embedder):
    """Voyage AI. `voyage-code-3` kod için özel olarak eğitilmiş model.

    Hız limitine takılmak istisna değil kural: ödeme yöntemi eklenmemiş bir
    hesapta limit 3 istek/dakika ve 10.000 token/dakika. Ödeme yöntemi eklense
    bile büyük bir müşteri reposunu indekslerken limite girilir. Bu yüzden
    yeniden deneme ve istekler arası bekleme sağlayıcının kendi içinde.
    """

    #: Tek istekte gönderilecek parça sayısı. API 1000'e izin veriyor ama
    #: dakikalık token limitine önce takılıyoruz; küçük yığın daha güvenli.
    BATCH_SIZE = 32

    #: Hız limiti hatasında beklenecek süre. Limitler dakikalık pencerelerde
    #: sıfırlandığı için bir dakikaya yakın beklemek en garantili yol.
    RETRY_WAIT_SECONDS = 25

    def __init__(
        self,
        model: str = "voyage-code-3",
        api_key: str | None = None,
        batch_size: int | None = None,
        max_retries: int = 8,
    ):
        try:
            import voyageai
        except ImportError as exc:  # pragma: no cover - kurulum hatası
            raise RuntimeError("voyageai kurulu değil: pip install voyageai") from exc

        key = api_key or os.environ.get("VOYAGE_API_KEY")
        if not key:
            raise RuntimeError(
                "VOYAGE_API_KEY tanımlı değil. .env dosyasına ekleyin ya da "
                "--provider ollama / --provider hash kullanın."
            )
        self._client = voyageai.Client(api_key=key)
        self._voyageai = voyageai
        self.model = model
        self.name = f"voyage:{model}"
        self.dimension = 1024
        self.batch_size = batch_size or self.BATCH_SIZE
        self.max_retries = max_retries

    def _request(self, batch: list[str], input_type: str):
        """Tek bir isteği, hız limitinde bekleyerek dener."""
        return retry_on_rate_limit(
            lambda: self._client.embed(batch, model=self.model, input_type=input_type),
            self._voyageai.error.RateLimitError,
            max_retries=self.max_retries,
            base_wait=self.RETRY_WAIT_SECONDS,
        )

    def _embed(self, texts: list[str], input_type: str) -> np.ndarray:
        vectors: list[list[float]] = []
        batches = range(0, len(texts), self.batch_size)
        total = len(range(0, len(texts), self.batch_size))
        for index, start in enumerate(batches, start=1):
            batch = texts[start : start + self.batch_size]
            if total > 1:
                print(f"  yığın {index}/{total} ({len(vectors)}/{len(texts)} parça)")
            result = self._request(batch, input_type)
            vectors.extend(result.embeddings)
        return _normalize(np.asarray(vectors, dtype=np.float32))

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return self._embed(texts, "document")

    def embed_query(self, text: str) -> np.ndarray:
        return self._embed([text], "query")[0]


#: Bazı modeller metnin başına görev öneki bekliyor. Nomic ailesi bunu zorunlu
#: kılıyor: önek olmadan model neyin doküman neyin sorgu olduğunu bilmiyor ve
#: arama kalitesi çöküyor (ölçüldü: recall %40 → %95).
#: Anahtar model adının önekiyle eşleşiyor, değer (doküman öneki, sorgu öneki).
TASK_PREFIXES: dict[str, tuple[str, str]] = {
    "nomic-embed-text": ("search_document: ", "search_query: "),
}


def _task_prefixes(model: str) -> tuple[str, str]:
    for prefix, pair in TASK_PREFIXES.items():
        if model.startswith(prefix):
            return pair
    return ("", "")


class OllamaEmbedder(Embedder):
    """Local Ollama sunucusu (http://localhost:11434).

    HTTP çağrısı standart kütüphaneyle yapılıyor; ek bağımlılık gerekmiyor.
    """

    BATCH_SIZE = 32

    def __init__(self, model: str = "nomic-embed-text", host: str | None = None):
        self.model = model
        self.host = (host or os.environ.get("OLLAMA_HOST") or "http://localhost:11434").rstrip("/")
        self.name = f"ollama:{model}"
        self.dimension = 0  # ilk cevaptan öğreniliyor
        self.document_prefix, self.query_prefix = _task_prefixes(model)

    def _post(self, payload: dict) -> dict:
        request = urllib.request.Request(
            f"{self.host}/api/embed",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.loads(response.read())
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Ollama'ya bağlanılamadı ({self.host}). Sunucu çalışıyor mu? "
                f"Model için: ollama pull {self.model}"
            ) from exc

    def _embed(self, texts: list[str]) -> np.ndarray:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.BATCH_SIZE):
            batch = texts[start : start + self.BATCH_SIZE]
            data = self._post({"model": self.model, "input": batch})
            vectors.extend(data["embeddings"])
        matrix = _normalize(np.asarray(vectors, dtype=np.float32))
        self.dimension = matrix.shape[1]
        return matrix

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return self._embed([f"{self.document_prefix}{text}" for text in texts])

    def embed_query(self, text: str) -> np.ndarray:
        return self._embed([f"{self.query_prefix}{text}"])[0]


#: Sözcük olmayan her şey ayraç. `\w` Unicode farkında olduğu için Türkçe
#: harfler (ı, ş, ğ, ü, ö, ç) korunuyor. ASCII'ye kısıtlanırsa "aşımı" kelimesi
#: "a" + "m" diye parçalanıyor ve Türkçe sorgu/yorumlar aramada kayboluyor.
_TOKEN_SPLIT = re.compile(r"[^\w]+", re.UNICODE)
_CAMEL_SPLIT = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def tokenize(text: str) -> list[str]:
    """Kodu ve soruyu arama için kelimelere ayırır.

    `getUserOrders` → get, user, orders. `order_id` → order, id. Bu ayrıştırma
    olmadan tam isim eşleşmesi çalışmıyor: kullanıcı "sipariş id" yazdığında
    `order_id` bulunamıyor.

    İngilizce olmayan metin de bozulmadan geçmeli — hem soru Türkçe olabiliyor
    hem de kod tabanında Türkçe yorum/docstring bulunabiliyor.
    """
    tokens: list[str] = []
    for piece in _TOKEN_SPLIT.split(text):
        if not piece:
            continue
        for part in piece.split("_"):
            if not part:
                continue
            for sub in _CAMEL_SPLIT.split(part):
                if sub:
                    tokens.append(sub.lower())
    return tokens


class HashEmbedder(Embedder):
    """API anahtarı gerektirmeyen yerel embedder (hashing trick).

    Anlamsal arama yapmaz — sadece kelime örtüşmesine bakar. Amacı, anahtar
    olmadan da boru hattının uçtan uca çalıştırılıp test edilebilmesi.
    Gerçek kullanımda Voyage ya da Ollama seçilmeli.
    """

    def __init__(self, dimension: int = 512):
        self.dimension = dimension
        self.name = f"hash:{dimension}"

    def _vector(self, text: str) -> np.ndarray:
        vector = np.zeros(self.dimension, dtype=np.float32)
        for token in tokenize(text):
            # Python'un yerleşik hash() fonksiyonu süreçler arası değişiyor
            # (PYTHONHASHSEED); önbelleğin tutarlı kalması için sabit bir karma
            # gerekiyor. blake2b hem sabit hem de eşit dağılıyor.
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            vector[int.from_bytes(digest, "big") % self.dimension] += 1.0
        return vector

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return _normalize(np.stack([self._vector(t) for t in texts]))

    def embed_query(self, text: str) -> np.ndarray:
        return _normalize(self._vector(text)[None, :])[0]


class CachedEmbedder(Embedder):
    """Sorgu vektörlerini de önbelleğe alan sarmalayıcı.

    Parça vektörleri zaten `embed_records` içinde önbelleğe alınıyor, ama her
    arama sorgusu ayrı bir API isteği demek. Ölçüm koşusunda 20 soru üç ayrı
    modda çalışınca aynı sorgu defalarca embed ediliyor ve hız limitli bir
    hesapta bu tek başına dakikalar sürüyor.
    """

    def __init__(self, embedder: Embedder, cache: EmbeddingCache):
        self._embedder = embedder
        self._cache = cache
        self.name = embedder.name
        self.dimension = embedder.dimension

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return self._embedder.embed_documents(texts)

    def embed_query(self, text: str) -> np.ndarray:
        # Parça anahtarlarıyla çakışmasın diye ayrı önek.
        digest = hashlib.blake2b(text.encode("utf-8"), digest_size=8).hexdigest()
        key = f"query::{digest}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        vector = self._embedder.embed_query(text)
        self._cache.put(key, vector)
        self._cache.save()
        return vector


def get_embedder(provider: str, model: str | None = None) -> Embedder:
    """Sağlayıcı adından embedder üretir."""
    if provider == "voyage":
        return VoyageEmbedder(model or "voyage-code-3")
    if provider == "ollama":
        return OllamaEmbedder(model or "nomic-embed-text")
    if provider == "hash":
        return HashEmbedder()
    raise ValueError(f"Bilinmeyen sağlayıcı: {provider} (voyage | ollama | hash)")


class EmbeddingCache:
    """Vektörleri içerik karmasına göre diskte tutar.

    Aynı parça iki kez embed edilmiyor: bir dosya değişmediyse yeniden
    indekslemede o parçanın vektörü önbellekten geliyor. API maliyetini düşük
    tutmanın ana yolu bu.
    """

    def __init__(self, embedder_name: str, cache_dir: Path = DEFAULT_CACHE_DIR):
        slug = re.sub(r"[^0-9A-Za-z._-]+", "_", embedder_name)
        self.path = Path(cache_dir) / f"{slug}.npz"
        self._vectors: dict[str, np.ndarray] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        with np.load(self.path, allow_pickle=False) as data:
            keys = data["keys"]
            vectors = data["vectors"]
        self._vectors = {str(key): vectors[i] for i, key in enumerate(keys)}

    def save(self) -> None:
        if not self._vectors:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        keys = list(self._vectors)
        vectors = np.stack([self._vectors[k] for k in keys])
        np.savez(self.path, keys=np.array(keys), vectors=vectors)

    def __contains__(self, key: str) -> bool:
        return key in self._vectors

    def __len__(self) -> int:
        return len(self._vectors)

    def get(self, key: str) -> np.ndarray | None:
        return self._vectors.get(key)

    def put(self, key: str, vector: np.ndarray) -> None:
        self._vectors[key] = vector.astype(np.float32)


def embed_records(
    records: list[dict],
    embedder: Embedder,
    cache: EmbeddingCache | None = None,
    progress: bool = False,
) -> tuple[np.ndarray, int]:
    """Parça kayıtlarını vektörleştirir; önbellekte olanları atlar.

    Kayıt sırasıyla hizalı (n, dim) matris ve kaç parçanın yeniden hesaplandığı
    döner.
    """
    if not records:
        return np.zeros((0, embedder.dimension), dtype=np.float32), 0

    keys = [record["content_hash"] for record in records]
    missing = [i for i, key in enumerate(keys) if cache is None or key not in cache]

    if cache is None:
        texts = [f"{records[i]['context']}\n\n{records[i]['text']}" for i in missing]
        return embedder.embed_documents(texts), len(missing)

    if missing and progress:
        print(f"{len(missing)} parça embed ediliyor ({embedder.name})...")

    # Önbellek grup grup diske yazılıyor. Hız limitli bir sağlayıcıda koşu
    # dakikalar sürebiliyor; ortada bir yerde patlarsa o ana kadarki iş
    # kaybolmasın diye. Tekrar çalıştırıldığında kalınan yerden devam ediyor.
    group_size = max(getattr(embedder, "batch_size", 64), 1) * 2
    for start in range(0, len(missing), group_size):
        group = missing[start : start + group_size]
        texts = [f"{records[i]['context']}\n\n{records[i]['text']}" for i in group]
        vectors = embedder.embed_documents(texts)
        for position, index in enumerate(group):
            cache.put(keys[index], vectors[position])
        cache.save()

    return np.stack([cache.get(key) for key in keys]), len(missing)
