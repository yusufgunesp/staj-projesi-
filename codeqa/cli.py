"""Komut satırı arayüzü: `python -m codeqa ...`"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np

from .answer import DEFAULT_MODEL
from .docs import index_docs
from .embeddings import CachedEmbedder, EmbeddingCache, embed_records, get_embedder
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

    reranker = None
    if getattr(args, "rerank", "none") not in (None, "", "none"):
        from .rerank import get_reranker

        reranker = get_reranker(args.rerank, getattr(args, "rerank_model", None))

    weights = {
        "bm25": getattr(args, "bm25_weight", 1.0),
        "vector": getattr(args, "vector_weight", 1.0),
    }
    return HybridSearch(
        records,
        vectors,
        CachedEmbedder(embedder, cache),
        reranker=reranker,
        weights=weights,
        pool_size=getattr(args, "pool", 0) or None,
    )


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


def cmd_ask(args: argparse.Namespace) -> int:
    from .answer import CodebaseAnswerer

    searcher = _build_search(args)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit(
            "ANTHROPIC_API_KEY tanımlı değil.\n"
            "  cp .env.example .env    ve anahtarı .env dosyasına ekleyin."
        )

    answerer = CodebaseAnswerer(
        searcher, repo_root=Path(args.repo), model=args.model_name, context_size=args.context
    )
    answer = answerer.ask(args.question)

    print(answer.text)

    print(f"\n--- {len(answer.hits)} parça getirildi, {len(answer.tool_calls)} tool çağrısı")
    for name, arguments in answer.tool_calls:
        detail = arguments.get("path") or arguments.get("query")
        print(f"    {name}({detail})")
    if answer.citations:
        print(f"    referanslar: {', '.join(answer.citations)}")
    if answer.unverified_citations:
        print(f"    ⚠ doğrulanamayan referans: {', '.join(answer.unverified_citations)}")
    if answer.usage:
        cached = answer.usage.get("cache_read_input_tokens", 0)
        print(
            f"    token: girdi {answer.usage.get('input_tokens', 0)}, "
            f"çıktı {answer.usage.get('output_tokens', 0)}, önbellekten {cached}"
        )
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from .evaluation import (
        Report,
        evaluate_answers,
        evaluate_retrieval,
        format_report,
        load_questions,
    )

    questions, settings = load_questions(Path(args.questions), split=args.split)
    if args.limit:
        questions = questions[: args.limit]

    args.index = args.index or settings.get("index", str(DEFAULT_OUTPUT))
    searcher = _build_search(args)

    report = Report(
        label=args.label,
        settings={
            "provider": args.provider,
            "mode": args.mode,
            "rerank": args.rerank,
            "weights": {"bm25": args.bm25_weight, "vector": args.vector_weight},
            "pool": args.pool or None,
            "k": args.k,
            "questions": len(questions),
            "split": args.split,
            "model": args.model_name if args.answers else None,
        },
    )
    report.retrieval = evaluate_retrieval(searcher, questions, k=args.k, mode=args.mode)

    if args.answers:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            sys.exit("ANTHROPIC_API_KEY tanımlı değil; cevap ölçümü için gerekli.")
        from .answer import CodebaseAnswerer

        repo = args.repo or settings.get("repo", ".")
        answerer = CodebaseAnswerer(searcher, repo_root=Path(repo), model=args.model_name)
        report.answers = evaluate_answers(answerer, questions, progress=True)

    print(format_report(report, questions))
    print(f"\nKoşu kaydedildi: {report.save()}")
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
    search_parser.add_argument(
        "--rerank",
        default="none",
        choices=["none", "voyage"],
        help="İlk sonuçları yeniden sırala (varsayılan: kapalı)",
    )
    search_parser.add_argument("--rerank-model", default=None, dest="rerank_model", help="Reranker modeli")
    search_parser.add_argument("--bm25-weight", type=float, default=1.0, dest="bm25_weight",
        help="RRF'de BM25'in ağırlığı (varsayılan 1.0)")
    search_parser.add_argument("--vector-weight", type=float, default=1.0, dest="vector_weight",
        help="RRF'de vektör aramasının ağırlığı (varsayılan 1.0)")
    search_parser.add_argument("--pool", type=int, default=0,
        help="Birleştirme/reranking öncesi aday havuzu (0 = otomatik)")
    search_parser.set_defaults(func=cmd_search)

    ask_parser = subparsers.add_parser("ask", help="Kod tabanına doğal dilde soru sor")
    ask_parser.add_argument("question", help="Soru")
    ask_parser.add_argument("--repo", default=".", help="Soruların sorulduğu repo dizini")
    ask_parser.add_argument("-i", "--index", default=str(DEFAULT_OUTPUT), help="İndeks dosyası")
    ask_parser.add_argument(
        "--provider", default="hash", choices=["voyage", "ollama", "hash"], help="Embedding sağlayıcısı"
    )
    ask_parser.add_argument("--model", default=None, help="Embedding modeli")
    ask_parser.add_argument(
        "--model-name", default=DEFAULT_MODEL, dest="model_name", help="Cevaplayan Claude modeli"
    )
    ask_parser.add_argument("--context", type=int, default=8, help="Modele verilecek parça sayısı")
    ask_parser.add_argument("--mode", default="hybrid", help=argparse.SUPPRESS)
    ask_parser.add_argument(
        "--rerank",
        default="none",
        choices=["none", "voyage"],
        help="İlk sonuçları yeniden sırala (varsayılan: kapalı)",
    )
    ask_parser.add_argument("--rerank-model", default=None, dest="rerank_model", help="Reranker modeli")
    ask_parser.add_argument("--bm25-weight", type=float, default=1.0, dest="bm25_weight",
        help="RRF'de BM25'in ağırlığı (varsayılan 1.0)")
    ask_parser.add_argument("--vector-weight", type=float, default=1.0, dest="vector_weight",
        help="RRF'de vektör aramasının ağırlığı (varsayılan 1.0)")
    ask_parser.add_argument("--pool", type=int, default=0,
        help="Birleştirme/reranking öncesi aday havuzu (0 = otomatik)")
    ask_parser.set_defaults(func=cmd_ask)

    eval_parser = subparsers.add_parser("eval", help="Soru seti üzerinde doğruluk ölç")
    eval_parser.add_argument(
        "-q", "--questions", default="eval/questions.json", help="Soru seti dosyası"
    )
    eval_parser.add_argument("-i", "--index", default=None, help="İndeks dosyası")
    eval_parser.add_argument("--repo", default=None, help="Soruların sorulduğu repo dizini")
    eval_parser.add_argument(
        "--provider", default="hash", choices=["voyage", "ollama", "hash"], help="Embedding sağlayıcısı"
    )
    eval_parser.add_argument("--model", default=None, help="Embedding modeli")
    eval_parser.add_argument(
        "--model-name", default=DEFAULT_MODEL, dest="model_name", help="Cevaplayan Claude modeli"
    )
    eval_parser.add_argument("--mode", default="hybrid", choices=["hybrid", "vector", "bm25"])
    eval_parser.add_argument("-k", type=int, default=8, help="İlk kaç sonuca bakılsın")
    eval_parser.add_argument(
        "--answers", action="store_true", help="Cevapları da ölç (Claude çağırır, ücretli)"
    )
    eval_parser.add_argument("--limit", type=int, default=0, help="Sadece ilk N soruyu koş")
    eval_parser.add_argument(
        "--split",
        default="all",
        choices=["all", "dev", "test"],
        help="Hangi bölme koşulsun. Ayar denemeleri dev'de, rapor test'te.",
    )
    eval_parser.add_argument("--label", default="baseline", help="Koşu etiketi (dosya adına girer)")
    eval_parser.add_argument(
        "--rerank",
        default="none",
        choices=["none", "voyage"],
        help="İlk sonuçları yeniden sırala (varsayılan: kapalı)",
    )
    eval_parser.add_argument("--rerank-model", default=None, dest="rerank_model", help="Reranker modeli")
    eval_parser.add_argument("--bm25-weight", type=float, default=1.0, dest="bm25_weight",
        help="RRF'de BM25'in ağırlığı (varsayılan 1.0)")
    eval_parser.add_argument("--vector-weight", type=float, default=1.0, dest="vector_weight",
        help="RRF'de vektör aramasının ağırlığı (varsayılan 1.0)")
    eval_parser.add_argument("--pool", type=int, default=0,
        help="Birleştirme/reranking öncesi aday havuzu (0 = otomatik)")
    eval_parser.set_defaults(func=cmd_eval)

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
