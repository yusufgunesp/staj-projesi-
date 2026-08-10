"""Komut satırı arayüzü: `python -m codeqa ...`"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from .docs import index_docs
from .embeddings import EmbeddingCache, embed_records, get_embedder
from .indexer import DEFAULT_EXCLUDES, index_repo
from .models import Chunk
from .search import HybridSearch

DEFAULT_OUTPUT = Path("data/chunks.jsonl")


def _write_jsonl(chunks: list[Chunk], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for chunk in chunks:
            handle.write(json.dumps(chunk.to_dict(), ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        sys.exit(f"İndeks bulunamadı: {path} — önce `python -m codeqa index <repo>` çalıştırın.")
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def cmd_index(args: argparse.Namespace) -> int:
    excludes = DEFAULT_EXCLUDES | set(args.exclude)
    chunks, stats = index_repo(Path(args.repo), frozenset(excludes))

    if not args.no_docs:
        doc_chunks = index_docs(Path(args.repo), frozenset(excludes))
        for chunk in doc_chunks:
            stats.add(chunk)
        chunks.extend(doc_chunks)

    output = Path(args.output)
    _write_jsonl(chunks, output)

    print(f"Repo      : {Path(args.repo).resolve()}")
    print(f"Dosya     : {stats.files_scanned} tarandı, {stats.files_failed} atlandı")
    print(f"Parça     : {stats.chunks}")
    for kind, count in sorted(stats.by_kind.items(), key=lambda item: -item[1]):
        print(f"  {kind:<9}: {count}")
    print(f"Çıktı     : {output}")
    if stats.errors:
        print(f"\nAtlanan dosyalar ({len(stats.errors)}):")
        for error in stats.errors[:10]:
            print(f"  - {error}")
        if len(stats.errors) > 10:
            print(f"  ... ve {len(stats.errors) - 10} tane daha")
    return 0


def cmd_grep(args: argparse.Namespace) -> int:
    """İndeks üzerinde ham alt dize araması.

    Sıralama yok, embedding gerektirmiyor; indeksin içeriğini gözle kontrol
    etmek için. Gerçek arama için `search` komutu kullanılmalı.
    """
    records = _read_jsonl(Path(args.index))
    needle = args.query.lower()
    hits = [r for r in records if needle in r["name"].lower() or needle in r["text"].lower()]
    if not hits:
        print("Eşleşme yok.")
        return 1
    for record in hits[: args.limit]:
        print(f"\n=== {record['location']}  [{record['kind']}] {record['name']}")
        snippet = record["text"].splitlines()[: args.lines]
        print("\n".join(snippet))
    print(f"\n{len(hits)} eşleşme (ilk {min(len(hits), args.limit)} tanesi gösterildi).")
    return 0


def _load_env() -> None:
    """.env dosyasındaki anahtarları ortama alır (varsa)."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - opsiyonel bağımlılık
        return
    load_dotenv()


def cmd_embed(args: argparse.Namespace) -> int:
    _load_env()
    records = _read_jsonl(Path(args.index))
    embedder = get_embedder(args.provider, args.model)
    cache = EmbeddingCache(embedder.name)

    cached_before = len(cache)
    _, computed = embed_records(records, embedder, cache, progress=True)

    print(f"Sağlayıcı : {embedder.name}")
    print(f"Parça     : {len(records)}")
    print(f"Yeni      : {computed} embed edildi")
    print(f"Önbellek  : {cached_before} → {len(cache)} vektör ({cache.path})")
    return 0


def _build_search(args: argparse.Namespace) -> HybridSearch:
    _load_env()
    records = _read_jsonl(Path(args.index))
    embedder = get_embedder(args.provider, args.model)
    cache = EmbeddingCache(embedder.name)

    missing = [r for r in records if r["content_hash"] not in cache]
    if missing:
        sys.exit(
            f"{len(missing)} parçanın vektörü yok. Önce şunu çalıştırın:\n"
            f"  python -m codeqa embed --provider {args.provider}"
        )

    vectors = np.stack([cache.get(r["content_hash"]) for r in records])
    return HybridSearch(records, vectors, embedder)


def cmd_search(args: argparse.Namespace) -> int:
    searcher = _build_search(args)
    hits = searcher.search(args.query, k=args.limit, mode=args.mode)

    if not hits:
        print("Sonuç bulunamadı.")
        return 1

    for rank, hit in enumerate(hits, start=1):
        methods = "+".join(hit.sources)
        print(f"\n{rank}. {hit.location}  [{hit.record['kind']}] {hit.name}")
        print(f"   skor {hit.score:.4f} ({methods})")
        if hit.record.get("signature"):
            print(f"   {hit.record['signature']}")
        snippet = hit.record["text"].splitlines()[: args.lines]
        for line in snippet:
            print(f"   | {line}")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    records = _read_jsonl(Path(args.index))
    by_kind: dict[str, int] = {}
    by_file: dict[str, int] = {}
    total_chars = 0
    for record in records:
        by_kind[record["kind"]] = by_kind.get(record["kind"], 0) + 1
        by_file[record["path"]] = by_file.get(record["path"], 0) + 1
        total_chars += len(record["text"])

    print(f"Parça sayısı : {len(records)}")
    print(f"Dosya sayısı : {len(by_file)}")
    print(f"Ortalama boy : {total_chars // max(len(records), 1)} karakter")
    print("\nTürlere göre:")
    for kind, count in sorted(by_kind.items(), key=lambda item: -item[1]):
        print(f"  {kind:<9}: {count}")
    print("\nEn çok parça çıkan dosyalar:")
    for path, count in sorted(by_file.items(), key=lambda item: -item[1])[:10]:
        print(f"  {count:>4}  {path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codeqa", description="Kod tabanı soru-cevap asistanı — indeksleme araçları"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser("index", help="Bir repoyu indeksle")
    index_parser.add_argument("repo", help="İndekslenecek repo dizini")
    index_parser.add_argument(
        "-o", "--output", default=str(DEFAULT_OUTPUT), help=f"Çıktı dosyası (varsayılan: {DEFAULT_OUTPUT})"
    )
    index_parser.add_argument("--no-docs", action="store_true", help="Markdown dosyalarını atla")
    index_parser.add_argument(
        "--exclude", action="append", default=[], metavar="AD", help="Ek olarak dışlanacak dizin adı"
    )
    index_parser.set_defaults(func=cmd_index)

    embed_parser = subparsers.add_parser("embed", help="İndeksteki parçaları vektörleştir")
    embed_parser.add_argument("-i", "--index", default=str(DEFAULT_OUTPUT), help="İndeks dosyası")
    embed_parser.add_argument(
        "--provider",
        default="hash",
        choices=["voyage", "ollama", "hash"],
        help="Embedding sağlayıcısı (varsayılan: hash — anahtar gerektirmez, anlamsal arama yapmaz)",
    )
    embed_parser.add_argument("--model", default=None, help="Sağlayıcıya özel model adı")
    embed_parser.set_defaults(func=cmd_embed)

    search_parser = subparsers.add_parser("search", help="Doğal dilde arama")
    search_parser.add_argument("query", help="Soru ya da aranacak metin")
    search_parser.add_argument("-i", "--index", default=str(DEFAULT_OUTPUT), help="İndeks dosyası")
    search_parser.add_argument(
        "--mode",
        default="hybrid",
        choices=["hybrid", "vector", "bm25"],
        help="Arama modu (varsayılan: hybrid)",
    )
    search_parser.add_argument(
        "--provider", default="hash", choices=["voyage", "ollama", "hash"], help="Embedding sağlayıcısı"
    )
    search_parser.add_argument("--model", default=None, help="Sağlayıcıya özel model adı")
    search_parser.add_argument("-n", "--limit", type=int, default=5, help="Sonuç sayısı")
    search_parser.add_argument("--lines", type=int, default=6, help="Parça başına gösterilecek satır")
    search_parser.set_defaults(func=cmd_search)

    grep_parser = subparsers.add_parser("grep", help="İndekste ham metin ara (embedding gerektirmez)")
    grep_parser.add_argument("query", help="Aranacak metin")
    grep_parser.add_argument("-i", "--index", default=str(DEFAULT_OUTPUT), help="İndeks dosyası")
    grep_parser.add_argument("-n", "--limit", type=int, default=5, help="Gösterilecek sonuç sayısı")
    grep_parser.add_argument("--lines", type=int, default=8, help="Parça başına gösterilecek satır")
    grep_parser.set_defaults(func=cmd_grep)

    stats_parser = subparsers.add_parser("stats", help="İndeks istatistikleri")
    stats_parser.add_argument("-i", "--index", default=str(DEFAULT_OUTPUT), help="İndeks dosyası")
    stats_parser.set_defaults(func=cmd_stats)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
