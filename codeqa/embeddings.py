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


def _normalize(matrix: np.ndarray) -> np.ndarray:
    """Satırları birim uzunluğa getirir; böylece kosinüs benzerliği = iç çarpım."""
    norms = np.linalg.norm(matrix, axis=-1, keepdims=True)
    norms[norms == 0] = 1.0
    return (matrix / norms).astype(np.float32)


class VoyageEmbedder(Embedder):
    """Voyage AI. `voyage-code-3` kod için özel olarak eğitilmiş model."""

    #: Tek istekte gönderilecek parça sayısı. API sınırı 1000, ama büyük
    #: parçalarda token sınırına önce takılıyoruz; 128 güvenli bir orta yol.
    BATCH_SIZE = 128

    def __init__(self, model: str = "voyage-code-3", api_key: str | None = None):
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
        self.model = model
        self.name = f"voyage:{model}"
        self.dimension = 1024

    def _embed(self, texts: list[str], input_type: str) -> np.ndarray:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.BATCH_SIZE):
            batch = texts[start : start + self.BATCH_SIZE]
            result = self._client.embed(batch, model=self.model, input_type=input_type)
            vectors.extend(result.embeddings)
        return _normalize(np.asarray(vectors, dtype=np.float32))

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return self._embed(texts, "document")

    def embed_query(self, text: str) -> np.ndarray:
        return self._embed([text], "query")[0]


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
        return self._embed(texts)

    def embed_query(self, text: str) -> np.ndarray:
        return self._embed([text])[0]


_TOKEN_SPLIT = re.compile(r"[^0-9A-Za-z_]+")
_CAMEL_SPLIT = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def tokenize(text: str) -> list[str]:
    """Kodu arama için kelimelere ayırır.

    `getUserOrders` → get, user, orders. `order_id` → order, id. Bu ayrıştırma
    olmadan tam isim eşleşmesi çalışmıyor: kullanıcı "sipariş id" yazdığında
    `order_id` bulunamıyor.
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
    vectors = np.zeros((0, 0), dtype=np.float32)

    if missing:
        texts = [f"{records[i]['context']}\n\n{records[i]['text']}" for i in missing]
        if progress:
            print(f"{len(missing)} parça embed ediliyor ({embedder.name})...")
        vectors = embedder.embed_documents(texts)
        for position, index in enumerate(missing):
            if cache is not None:
                cache.put(keys[index], vectors[position])

    if cache is None:
        return vectors, len(missing)

    cache.save()
    return np.stack([cache.get(key) for key in keys]), len(missing)
