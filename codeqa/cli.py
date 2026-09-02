"""Komut satırı arayüzü: `python -m codeqa ...`"""

from __future__ import annotations

import argparse
import atexit
import os
import sys
import time
from pathlib import Path

import numpy as np

from .answer import DEFAULT_MODEL
from .docs import index_docs
from .embeddings import CachedEmbedder, EmbeddingCache, embed_records, get_embedder
from .indexer import DEFAULT_EXCLUDES, index_repo
from .models import read_jsonl, write_jsonl
from .projects import DEFAULT_REGISTRY
from .search import (
    DIRECTORY_SLOTS,
    DIVERSITY_SLOTS,
    REFERENCE_SLOTS,
    HybridSearch,
    resolve_mode,
)

DEFAULT_OUTPUT = Path("data/chunks.jsonl")


def _read_jsonl(path: Path) -> list[dict]:
    """Okuma + komut satırına uygun hata mesajı.

    Ham okuma `models.read_jsonl`'de; buradaki tek fark, indeks yoksa
    kullanıcıya ne yapacağını söyleyip çıkması.
    """
    if not path.exists():
        sys.exit(f"İndeks bulunamadı: {path} — önce `python -m codeqa index <repo>` çalıştırın.")
    return read_jsonl(path)


def cmd_index(args: argparse.Namespace) -> int:
    excludes = DEFAULT_EXCLUDES | set(args.exclude)
    chunks, stats = index_repo(Path(args.repo), frozenset(excludes))

    if not args.no_docs:
        doc_chunks = index_docs(Path(args.repo), frozenset(excludes))
        for chunk in doc_chunks:
            stats.add(chunk)
        chunks.extend(doc_chunks)

    output = Path(args.output)
    write_jsonl(chunks, output)

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


def _load_env(start: Path | None = None) -> None:
    """.env dosyasındaki anahtarları ortama alır (varsa).

    `start` verilirse `.env` o dizinden yukarı doğru aranıyor, verilmezse
    çalışma dizininden. Ayrım `serve` için var: MCP sunucusunun çalışma dizini
    kendi seçimi değil, onu başlatan istemcinin.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - opsiyonel bağımlılık
        return
    if start is not None:
        for directory in [start, *start.parents]:
            candidate = directory / ".env"
            if candidate.exists():
                load_dotenv(candidate)
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
    if hasattr(args, "mode"):
        args.mode = resolve_mode(args.mode, args.provider)
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
    cached = CachedEmbedder(embedder, cache)
    # Süreç biterken bekleyen sorgu vektörleri diske yazılsın: toplu yazma
    # performans için gerekli ama koşu sonunda kaydetmezsek boşa gidiyorlar.
    atexit.register(cached.flush)
    return HybridSearch(
        records,
        vectors,
        cached,
        reranker=reranker,
        weights=weights,
        pool_size=getattr(args, "pool", 0) or None,
        diversity_slots=getattr(args, "diversity_slots", DIVERSITY_SLOTS),
        directory_slots=getattr(args, "directory_slots", DIRECTORY_SLOTS),
        reference_slots=getattr(args, "reference_slots", REFERENCE_SLOTS),
        weigh_chunks=not getattr(args, "no_chunk_weight", False),
    )


def cmd_search(args: argparse.Namespace) -> int:
    searcher = _build_search(args)

    if getattr(args, "facets", False):
        from .facets import facets as build_facets

        found = build_facets(searcher, args.query, mode=args.mode)
        if not found:
            print("Öbek çıkmadı.")
            return 1
        print(f"{args.query!r} havuzda şu öbeklere ayrılıyor (ölçüm değil):\n")
        for facet in found:
            print(f"  {facet.label}")
            print(f"      {facet.query(args.query)!r}")
        return 0

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
        u = answer.usage
        # Önbellek açık olduğu için girdinin neredeyse tamamı
        # `cache_creation_input_tokens`'a düşüyor; yalnızca `input_tokens`
        # basılınca ekranda "girdi 3" görünüyordu ve maliyet iddiasıyla
        # çelişiyordu. Haiku 4.5: girdi $1 / çıktı $5 / yazma 1.25x / okuma 0.1x (MTok)
        written = u.get("cache_creation_input_tokens", 0)
        cached = u.get("cache_read_input_tokens", 0)
        cost = (
            u.get("input_tokens", 0) * 1e-6
            + written * 1.25e-6
            + cached * 0.1e-6
            + u.get("output_tokens", 0) * 5e-6
        )
        print(
            f"    token: girdi {u.get('input_tokens', 0):,}, "
            f"önbellek yazma {written:,}, okuma {cached:,}, "
            f"çıktı {u.get('output_tokens', 0):,}  (~${cost:.4f})"
        )
    return 0


def cmd_contextualize(args: argparse.Namespace) -> int:
    from .contextual import ContextGenerator

    _load_env()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY tanımlı değil; bağlam üretimi için gerekli.")

    records = _read_jsonl(Path(args.index))
    generator = ContextGenerator(
        repo_root=Path(args.repo), model=args.model_name, language=args.language
    )
    enriched, generated = generator.enrich(records, progress=True)

    output = Path(args.output or args.index)
    write_jsonl(enriched, output)
    print(f"Parça     : {len(records)}")
    print(f"Yeni cümle: {generated}")
    print(f"Çıktı     : {output}")
    if generator.aborted:
        print(f"\nKoşu durduruldu: {generator.aborted}")
        print("Üretilen cümleler önbellekte; sorun giderilince kaldığı yerden devam eder.\n")
    if generator.failures:
        print(f"Başarısız : {len(generator.failures)} parça (tekrar çalıştırınca denenir)")
        for line in generator.failures[:5]:
            print(f"    {line}")
    u = generator.usage
    if u:
        # Haiku 4.5: girdi $1 / çıktı $5 / önbellek yazma 1.25x / okuma 0.1x (MTok)
        cost = (u.get("input_tokens", 0) * 1e-6
                + u.get("cache_creation_input_tokens", 0) * 1.25e-6
                + u.get("cache_read_input_tokens", 0) * 0.1e-6
                + u.get("output_tokens", 0) * 5e-6)
        print(f"Token     : girdi {u.get('input_tokens',0):,}, "
              f"önbellek yazma {u.get('cache_creation_input_tokens',0):,}, "
              f"okuma {u.get('cache_read_input_tokens',0):,}, "
              f"çıktı {u.get('output_tokens',0):,}")
        print(f"Maliyet   : ~${cost:.2f}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """MCP sunucusunu stdio üzerinden çalıştırır.

    Doğrudan çağrılmıyor: MCP istemcisi (Claude Code gibi) bu komutu alt süreç
    olarak başlatıp stdin/stdout üzerinden konuşuyor.
    """
    from .mcp_server import build_server

    # Tek cwd'ye güvenemeyen komut bu: istemci sunucuyu kendi çalışma dizininde
    # başlatıyor, dolayısıyla `data/embeddings` ve `.env` gibi göreli yollar
    # tutmayabiliyor. İkisi de indeksin yanından çözülüyor — önbellek zaten
    # indekse ait, `.env` de indeksi taşıyan projeye.
    index_path = Path(args.index).resolve()
    _load_env(index_path.parent)
    server = build_server(
        index_path=index_path,
        provider=args.provider,
        model=args.model,
        mode=resolve_mode(args.mode, args.provider),
        cache_dir=index_path.parent / "embeddings",
    )
    server.run(transport="stdio")
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    """Yerel web arayüzünü açar.

    Yalnızca `127.0.0.1`'e bağlanıyor: indeksler, kaynak kodu ve API anahtarları
    bu süreçten geçiyor, sunucuyu ağa açmanın hiçbir gerekçesi yok.

    Projeler arayüzden ekleniyor; `-i` / `--repo` verilirse o indeks açılışta
    proje olarak kaydediliyor. Bu ikisi zorunlu değil — kayıt boşsa arayüz
    "proje ekle" ekranıyla açılıyor.
    """
    import webbrowser

    from .projects import Project, load_projects, save_projects, slugify, upsert
    from .ui import UIServer

    _load_env()
    registry = Path(args.registry)
    server = UIServer(
        ("127.0.0.1", args.port),
        registry=registry,
        model=args.model_name,
        context_size=args.context,
        data_dir=Path(args.index).parent if args.index else Path("data"),
    )

    if args.index:
        # Komut satırından indeks verildiyse kayda al: eski çağrı biçimi
        # (ve DEMO.md'deki komut) çalışmaya devam etsin.
        repo = Path(args.repo).expanduser().resolve()
        name = args.name or Path(args.index).stem
        existing = load_projects(registry)
        records = _read_jsonl(Path(args.index))
        previous = next((p for p in existing if p.id == slugify(name)), None)
        project = Project(
            id=slugify(name),
            name=name,
            repo=str(repo),
            index=str(Path(args.index)),
            provider=args.provider,
            model=args.model,
            mode=args.mode,
            chunks=len(records),
            files=len({r["path"] for r in records}),
            added=time.strftime("%Y-%m-%d %H:%M"),
            # Aynı proje daha önce arayüzden eklendiyse kontrol sonuçları
            # duruyor; bayrakla açmak onları silmemeli.
            checks=previous.checks if previous else {},
        )
        server.projects = upsert(existing, project)
        save_projects(server.projects, registry)

    url = f"http://127.0.0.1:{server.server_address[1]}"
    print(f"Arayüz  : {url}")
    print(f"Kayıt   : {registry} ({len(server.projects)} proje)")
    for project in server.projects:
        print(f"  · {project.name}  ({project.repo})")
    if not server.projects:
        print("  (kayıt boş — arayüzden 'Proje ekle' ile başlayın)")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Uyarı   : ANTHROPIC_API_KEY yok — cevaplama kapalı, arama çalışıyor.")
    print("Durdurmak için Ctrl+C.")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nKapatıldı.")
    finally:
        server.server_close()
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
            "diversity_slots": args.diversity_slots,
            "directory_slots": args.directory_slots,
            "reference_slots": args.reference_slots,
            "weigh_chunks": not args.no_chunk_weight,
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


def _add_index_arg(parser: argparse.ArgumentParser, default: str) -> None:
    parser.add_argument("-i", "--index", default=default, help="İndeks dosyası")


def _add_embedding_args(parser: argparse.ArgumentParser) -> None:
    """Vektör okuyan her komutta ortak olan sağlayıcı seçimi."""
    parser.add_argument(
        "--provider",
        default="hash",
        choices=["voyage", "ollama", "hash"],
        help="Embedding sağlayıcısı (varsayılan: hash — anahtarsız, anlamsal arama yapmaz)",
    )
    parser.add_argument("--model", default=None, help="Sağlayıcıya özel embedding modeli")


def _add_retrieval_args(parser: argparse.ArgumentParser, with_mode: bool = True) -> None:
    """Arama davranışını ayarlayan ortak seçenekler.

    `search`, `ask` ve `eval` aynı arama katmanını kurduğu için aynı
    seçenekleri alıyorlar; tek yerde tanımlı olmaları ikisinin ayrışmasını
    engelliyor.

    **Mod varsayılanı sağlayıcıya bağlı, kaza değil.** Buradaki varsayılan
    `hybrid`, çünkü bu komutların varsayılan sağlayıcısı `hash` — anahtarsız
    çalışan yer tutucu, anlamsal arama yapmıyor. Kendi repomuzda ölçüldü
    (20 soru): hash + vector recall 0.65 / MRR 0.230, hash + hybrid 0.90 /
    0.416. Yani zayıf embedding'de BM25 tarafı taşıyıcı.

    Gerçek bir sağlayıcı verildiğinde tablo tersine dönüyor: voyage ile büyük
    İngilizce repoda saf vektör hibriti açık ara geçiyor (akış kapsamı 0.750
    vs 0.683, kolay 0.925 vs 0.825), küçük repoda ise berabere (MRR 0.650 vs
    0.654). Bu yüzden `serve` varsayılanı `vector`: MCP her zaman gerçek bir
    sağlayıcıyla kuruluyor.

    Kısacası `--provider voyage` verirken `--mode vector` de vermek gerekiyor.
    """
    if with_mode:
        parser.add_argument(
            "--mode",
            default=None,
            choices=["hybrid", "vector", "bm25"],
            help="Arama modu (varsayılan: sağlayıcıya göre — bkz. resolve_mode)",
        )
    parser.add_argument(
        "--rerank",
        default="none",
        choices=["none", "voyage"],
        help="İlk sonuçları yeniden sırala (varsayılan: kapalı)",
    )
    parser.add_argument(
        "--rerank-model", default=None, dest="rerank_model", help="Reranker modeli"
    )
    parser.add_argument(
        "--bm25-weight",
        type=float,
        default=1.0,
        dest="bm25_weight",
        help="RRF'de BM25'in ağırlığı (varsayılan 1.0)",
    )
    parser.add_argument(
        "--vector-weight",
        type=float,
        default=1.0,
        dest="vector_weight",
        help="RRF'de vektör aramasının ağırlığı (varsayılan 1.0)",
    )
    parser.add_argument(
        "--pool",
        type=int,
        default=0,
        help="Birleştirme/reranking öncesi aday havuzu (0 = otomatik)",
    )
    parser.add_argument(
        "--no-chunk-weight",
        action="store_true",
        dest="no_chunk_weight",
        help="Üretilmiş tip tanımlarını geri plana atmayı kapat (varsayılan: açık)",
    )
    parser.add_argument(
        "--reference-slots",
        type=int,
        default=REFERENCE_SLOTS,
        dest="reference_slots",
        help=(
            "İmport bağıyla eklenecek 'bu dosyayı kullanan dosya' sayısı "
            f"(0 = kapalı, varsayılan {REFERENCE_SLOTS})"
        ),
    )
    parser.add_argument(
        "--directory-slots",
        type=int,
        default=DIRECTORY_SLOTS,
        dest="directory_slots",
        help=(
            "Güçlü temsil edilen dizinlerden eklenecek kardeş dosya sayısı "
            f"(0 = kapalı, varsayılan {DIRECTORY_SLOTS})"
        ),
    )
    parser.add_argument(
        "--diversity-slots",
        type=int,
        default=DIVERSITY_SLOTS,
        dest="diversity_slots",
        help=(
            "İlk k sonucun arkasına eklenecek yeni dosya sayısı "
            f"(0 = kapalı, varsayılan {DIVERSITY_SLOTS})"
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codeqa", description="Kod tabanı soru-cevap asistanı — indeksleme araçları"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser("index", help="Bir repoyu indeksle")
    index_parser.add_argument("repo", help="İndekslenecek repo dizini")
    index_parser.add_argument(
        "-o",
        "--output",
        default=str(DEFAULT_OUTPUT),
        help=f"Çıktı dosyası (varsayılan: {DEFAULT_OUTPUT})",
    )
    index_parser.add_argument("--no-docs", action="store_true", help="Markdown dosyalarını atla")
    index_parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="AD",
        help="Ek olarak dışlanacak dizin adı",
    )
    index_parser.set_defaults(func=cmd_index)

    embed_parser = subparsers.add_parser("embed", help="İndeksteki parçaları vektörleştir")
    _add_index_arg(embed_parser, str(DEFAULT_OUTPUT))
    _add_embedding_args(embed_parser)
    embed_parser.set_defaults(func=cmd_embed)

    search_parser = subparsers.add_parser("search", help="Doğal dilde arama")
    search_parser.add_argument("query", help="Soru ya da aranacak metin")
    _add_index_arg(search_parser, str(DEFAULT_OUTPUT))
    _add_embedding_args(search_parser)
    _add_retrieval_args(search_parser)
    search_parser.add_argument("-n", "--limit", type=int, default=5, help="Sonuç sayısı")
    search_parser.add_argument(
        "--lines", type=int, default=6, help="Parça başına gösterilecek satır"
    )
    search_parser.add_argument(
        "--facets",
        action="store_true",
        help="Sonuç yerine sorgunun havuzdaki öbeklerini göster (netleştirme)",
    )
    search_parser.set_defaults(func=cmd_search)

    ask_parser = subparsers.add_parser("ask", help="Kod tabanına doğal dilde soru sor")
    ask_parser.add_argument("question", help="Soru")
    ask_parser.add_argument("--repo", default=".", help="Soruların sorulduğu repo dizini")
    _add_index_arg(ask_parser, str(DEFAULT_OUTPUT))
    _add_embedding_args(ask_parser)
    _add_retrieval_args(ask_parser)
    ask_parser.add_argument(
        "--model-name", default=DEFAULT_MODEL, dest="model_name", help="Cevaplayan Claude modeli"
    )
    ask_parser.add_argument(
        "--context",
        type=int,
        default=8,
        help="Alaka sırasına göre verilecek parça sayısı (çeşitlilik slotları buna eklenir)",
    )
    ask_parser.set_defaults(func=cmd_ask)

    ui_parser = subparsers.add_parser("ui", help="Yerel web arayüzünü aç (tarayıcıda)")
    ui_parser.add_argument("--repo", default=".", help="Açılışta kaydedilecek projenin dizini")
    # `-i` burada zorunlu değil: projeler arayüzden ekleniyor. Verilirse o
    # indeks açılışta kayda giriyor — eski çağrı biçimi bozulmasın diye.
    ui_parser.add_argument("-i", "--index", default=None, help="Açılışta kaydedilecek indeks")
    ui_parser.add_argument("--name", default=None, help="Kaydedilecek projenin adı")
    ui_parser.add_argument(
        "--registry", default=str(DEFAULT_REGISTRY), help="Proje kaydı dosyası"
    )
    _add_embedding_args(ui_parser)
    _add_retrieval_args(ui_parser)
    ui_parser.add_argument(
        "--model-name", default=DEFAULT_MODEL, dest="model_name", help="Cevaplayan Claude modeli"
    )
    ui_parser.add_argument(
        "--context", type=int, default=8, help="Alaka sırasına göre verilecek parça sayısı"
    )
    ui_parser.add_argument("--port", type=int, default=8765, help="Dinlenecek port")
    ui_parser.add_argument(
        "--no-browser", action="store_true", help="Tarayıcıyı kendiliğinden açma"
    )
    ui_parser.set_defaults(func=cmd_ui)

    eval_parser = subparsers.add_parser("eval", help="Soru seti üzerinde doğruluk ölç")
    eval_parser.add_argument(
        "-q", "--questions", default="eval/questions.json", help="Soru seti dosyası"
    )
    _add_index_arg(eval_parser, None)
    eval_parser.add_argument("--repo", default=None, help="Soruların sorulduğu repo dizini")
    _add_embedding_args(eval_parser)
    _add_retrieval_args(eval_parser)
    eval_parser.add_argument(
        "--model-name", default=DEFAULT_MODEL, dest="model_name", help="Cevaplayan Claude modeli"
    )
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
    eval_parser.set_defaults(func=cmd_eval)

    serve_parser = subparsers.add_parser(
        "serve", help="MCP sunucusu olarak çalış (Claude Code entegrasyonu)"
    )
    _add_index_arg(serve_parser, str(DEFAULT_OUTPUT))
    _add_embedding_args(serve_parser)
    serve_parser.add_argument(
        "--mode",
        default=None,
        choices=["hybrid", "vector", "bm25"],
        help="Arama modu (varsayılan: sağlayıcıya göre — bkz. resolve_mode)",
    )
    serve_parser.set_defaults(func=cmd_serve)

    ctx_parser = subparsers.add_parser(
        "contextualize", help="Parçalara LLM ile bağlam cümlesi ekle"
    )
    ctx_parser.add_argument("-i", "--index", default=str(DEFAULT_OUTPUT), help="İndeks dosyası")
    ctx_parser.add_argument("-o", "--output", default=None, help="Çıktı (varsayılan: yerinde)")
    ctx_parser.add_argument("--repo", default=".", help="Kaynak dosyaların bulunduğu dizin")
    ctx_parser.add_argument("--language", default="tr", choices=["tr", "en"], help="Cümle dili")
    ctx_parser.add_argument(
        "--model-name", default="claude-haiku-4-5", dest="model_name", help="Kullanılacak model"
    )
    ctx_parser.set_defaults(func=cmd_contextualize)

    grep_parser = subparsers.add_parser(
        "grep", help="İndekste ham metin ara (embedding gerektirmez)"
    )
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
