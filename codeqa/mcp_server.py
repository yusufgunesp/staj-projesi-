"""MCP sunucusu: aracı Claude Code'un içine taşır.

CLI çalışıyor ama geliştiriciyi terminale götürüyor. MCP sunucusu olarak
çalıştığında araç, geliştiricinin zaten kullandığı yerde — Claude Code'un
içinde — bir tool olarak görünüyor.

Üç tool sunuluyor:

- `search_code`   : indekste anlamsal + anahtar kelime araması
- `read_chunk`    : bulunan bir parçanın tam metnini getirir
- `index_status`  : indeksin hangi repoyu, kaç parçayı kapsadığını söyler

Cevaplama (`ask`) bilerek sunulmuyor: MCP istemcisi zaten bir dil modeli.
Ona ikinci bir modelin ürettiği cevabı vermek yerine ham arama sonuçlarını
vermek hem ucuz hem de daha doğru — istemci kendi bağlamıyla yorumluyor.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np

from .embeddings import CachedEmbedder, EmbeddingCache, get_embedder
from .search import HybridSearch

#: Tek bir arama sonucunda döndürülecek azami satır. İstemcinin bağlam
#: penceresini korumak için; tamamı `read_chunk` ile alınabiliyor.
MAX_SNIPPET_LINES = 40


class IndexNotReady(RuntimeError):
    """İndeks ya da vektörler eksik. Mesaj kullanıcıya ne yapacağını söyler."""


def load_searcher(
    index_path: Path,
    provider: str,
    model: str | None,
    cache_dir: Path | None = None,
) -> tuple[HybridSearch, list]:
    """İndeksi ve vektörleri yükleyip arama nesnesi kurar."""
    if not index_path.exists():
        raise IndexNotReady(
            f"İndeks bulunamadı: {index_path}\n"
            f"Önce çalıştırın: python -m codeqa index <repo> -o {index_path}"
        )
    with index_path.open(encoding="utf-8") as handle:
        records = [json.loads(line) for line in handle if line.strip()]

    embedder = get_embedder(provider, model)
    cache = (
        EmbeddingCache(embedder.name, cache_dir=cache_dir)
        if cache_dir
        else EmbeddingCache(embedder.name)
    )
    missing = [r for r in records if r["content_hash"] not in cache]
    if missing:
        raise IndexNotReady(
            f"{len(missing)} parçanın vektörü yok.\n"
            f"Önce çalıştırın: python -m codeqa embed -i {index_path} --provider {provider}"
        )

    vectors = np.stack([cache.get(r["content_hash"]) for r in records])
    return HybridSearch(records, vectors, CachedEmbedder(embedder, cache)), records


def format_hit(hit, max_lines: int = MAX_SNIPPET_LINES) -> str:
    """Arama sonucunu istemciye gösterilecek metne çevirir."""
    lines = hit.record["text"].splitlines()
    body = "\n".join(lines[:max_lines])
    if len(lines) > max_lines:
        body += f"\n... ({len(lines) - max_lines} satır daha — read_chunk ile tamamı)"
    header = f"{hit.location}  [{hit.record['kind']}] {hit.name}"
    return f"{header}\n{body}"


def build_server(
    index_path: Path,
    provider: str = "voyage",
    model: str | None = None,
    mode: str = "vector",
    cache_dir: Path | None = None,
):
    """MCP sunucusunu kurar. İndeks ilk istekte yükleniyor (tembel)."""
    # MCP SDK 2.x: sunucu sınıfı `MCPServer` (eski adı FastMCP).
    from mcp.server import MCPServer

    server = MCPServer(
        name="codeqa",
        instructions=(
            "Bu kod tabanı için indekslenmiş anlamsal arama. Bir sembolün nerede "
            "tanımlandığını ya da bir konunun kodda nerede ele alındığını bulmak için "
            "search_code kullan; sonuçtaki konumla read_chunk ile tam metni al."
        ),
    )
    state: dict = {}

    def searcher() -> HybridSearch:
        if "searcher" not in state:
            state["searcher"], state["records"] = load_searcher(
                index_path, provider, model, cache_dir
            )
        return state["searcher"]

    @server.tool()
    def search_code(query: str, limit: int = 5) -> str:
        """Kod tabanında doğal dilde arama yapar.

        Args:
            query: Aranacak konu ya da sembol adı.
            limit: Dönecek sonuç sayısı (varsayılan 5).
        """
        try:
            hits = searcher().search(query, k=limit, mode=mode)
        except IndexNotReady as exc:
            return str(exc)
        if not hits:
            return f"'{query}' için sonuç yok."
        return "\n\n".join(format_hit(hit) for hit in hits)

    @server.tool()
    def read_chunk(location: str) -> str:
        """Bir parçanın tam metnini getirir.

        Args:
            location: `search_code` sonucundaki `dosya.py:42` biçiminde konum.
        """
        try:
            searcher()
        except IndexNotReady as exc:
            return str(exc)
        for record in state["records"]:
            if record["location"] == location:
                return (
                    f"{record['location']}  [{record['kind']}] {record['name']}\n{record['text']}"
                )
        return f"{location} indekste yok. Önce search_code ile arayın."

    @server.tool()
    def index_status() -> str:
        """İndeksin hangi repoyu ve kaç parçayı kapsadığını söyler."""
        try:
            searcher()
        except IndexNotReady as exc:
            return str(exc)
        records = state["records"]
        # Dil dökümü kaldırıldı: araç yalnızca Python indeksliyor, satır her
        # zaman "python: N" olurdu. Parça türü dökümü bilgi taşıyor.
        kinds: dict[str, int] = {}
        paths = set()
        for record in records:
            kinds[record["kind"]] = kinds.get(record["kind"], 0) + 1
            paths.add(record["path"])
        breakdown = ", ".join(f"{name}: {count}" for name, count in sorted(kinds.items()))
        return (
            f"İndeks : {index_path}\n"
            f"Parça  : {len(records)} ({len(paths)} dosya)\n"
            f"Türler : {breakdown}\n"
            f"Arama  : {provider} / {mode}"
        )

    return server


def main() -> None:
    """`python -m codeqa.mcp_server` ile stdio üzerinden çalışır.

    Yapılandırma ortam değişkenlerinden okunuyor: MCP istemcileri sunucuyu
    komut satırı argümanı yerine ortamla yapılandırıyor.
    """
    from dotenv import load_dotenv

    load_dotenv()
    server = build_server(
        index_path=Path(os.environ.get("CODEQA_INDEX", "data/chunks.jsonl")),
        provider=os.environ.get("CODEQA_PROVIDER", "voyage"),
        model=os.environ.get("CODEQA_MODEL") or None,
        mode=os.environ.get("CODEQA_MODE", "vector"),
    )
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
