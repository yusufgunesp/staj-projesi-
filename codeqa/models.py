"""Indeks parçalarının (chunk) veri modeli."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass
class Chunk:
    """İndekslenen tek bir parça: bir fonksiyon, sınıf, modül başlığı ya da doküman bölümü.

    `text` aranan/gösterilen içerik, `context` ise contextual retrieval için
    parçanın önüne eklenen kısa konum bilgisi. Embedding'e ikisi birlikte gider
    (bkz. `embed_text`), ama kullanıcıya sadece `text` gösterilir.
    """

    source: str  # "code" | "doc"
    kind: str  # module | class | function | method | section
    path: str  # repo köküne göre yol
    name: str  # nitelenmiş ad, ör. "OrderService.create"
    start_line: int
    end_line: int
    text: str
    context: str
    signature: str | None = None
    docstring: str | None = None
    parent: str | None = None
    language: str = "python"

    @property
    def id(self) -> str:
        return f"{self.path}::{self.name}#{self.start_line}"

    @property
    def content_hash(self) -> str:
        """Değişiklik tespiti için; aynı içerik yeniden embed edilmesin diye."""
        return _hash(self.text)

    @property
    def embed_text(self) -> str:
        return f"{self.context}\n\n{self.text}"

    @property
    def location(self) -> str:
        """Cevaplarda kaynak göstermek için: `api/orders.py:88`."""
        return f"{self.path}:{self.start_line}"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "kind": self.kind,
            "path": self.path,
            "name": self.name,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "location": self.location,
            "signature": self.signature,
            "docstring": self.docstring,
            "parent": self.parent,
            "language": self.language,
            "context": self.context,
            "text": self.text,
            "content_hash": self.content_hash,
        }


@dataclass
class IndexStats:
    """Bir indeksleme koşusunun özeti."""

    files_scanned: int = 0
    files_failed: int = 0
    chunks: int = 0
    by_kind: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def add(self, chunk: Chunk) -> None:
        self.chunks += 1
        self.by_kind[chunk.kind] = self.by_kind.get(chunk.kind, 0) + 1
