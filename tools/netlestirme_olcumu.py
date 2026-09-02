"""Netleştirme öbekleri belirsiz sorguyu kurtarıyor mu?

**Ölçtüğü şey.** Var olan soru daraltılıp 1-2 kelimeye indiriliyor (`"Bir ürünün
fiyatı nerede tanımlanıyor?"` → `"ürün fiyat"`), sonra üç sayı alınıyor:

- **düz** — daraltılmış sorguyla normal arama. Kullanıcının bugün aldığı sonuç.
- **menü** — beklenen dosyanın dizini beş öbekten birinde görünüyor mu. Arayüzün
  verdiği söz tam olarak bu: "doğru kapı listede var mı".
- **tavan** — kullanıcı *en iyi* öbeği seçseydi recall ne olurdu.

**Tavan bir sistem kazancı değil, üst sınır.** Öbeği etikete bakarak seçiyor,
kullanıcı bunu yapamaz. İşe yarayan tarafı tek yönlü: tavan düzü geçmiyorsa
özellik ölü demektir, geçiyorsa "olabilir" demektir.

Etiketler elle konmuş `expect_files`'tan geliyor, codeqa'dan değil.

    .venv/bin/python tools/netlestirme_olcumu.py eval/questions_saleor.json --split dev
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from codeqa.cli import _load_env
from codeqa.embeddings import CachedEmbedder, EmbeddingCache, get_embedder
from codeqa.evaluation import wilson_interval
from codeqa.facets import facets
from codeqa.models import read_jsonl
from codeqa.search import HybridSearch, resolve_mode

#: Daraltmada atılan kelimeler: soru kalıbı ve bağlaçlar.
#:
#: Liste elle yazıldı ve **sorulara bakılmadan** kondu — soru başına ayarlansaydı
#: daraltma kolaylaştırılmış olurdu. Ekleme yapılacaksa hepsi için yapılmalı.
STOPWORDS = {
    "bir",
    "bu",
    "şu",
    "ne",
    "neden",
    "nasıl",
    "nerede",
    "nereden",
    "hangi",
    "hangisi",
    "kaç",
    "kim",
    "kimler",
    "var",
    "yok",
    "mı",
    "mi",
    "mu",
    "mü",
    "ve",
    "ile",
    "için",
    "olan",
    "olarak",
    "gibi",
    "daha",
    "sonra",
    "önce",
    "en",
    "de",
    "da",
    "ki",
}

#: Daraltılmış sorguda kalacak kelime sayısı.
KEEP = 1


def narrow(question: str) -> str:
    """Soruyu mekanik olarak 1-2 kelimeye indirir.

    Mekanik olması şart: elle seçilseydi lehte örnek seçme riski doğardı.
    Türkçe ekler kırpılmıyor — kırpmak morfolojik bir karar olurdu ve bu
    ölçümün konusu değil.
    """
    # Türkçe küçültme önce: `"İ".lower()` Python'da `i` + birleşik nokta (U+0307)
    # veriyor, birleşik nokta `\w`'ye girmediği için regex kelimeyi oradan
    # bölüyordu — "İndirim" → "i" + "ndirim". Ölçümün ilk koşusunda üç soru bu
    # yüzden bozuk daraltılmıştı.
    folded = question.replace("İ", "i").replace("I", "ı").lower()
    words = re.findall(r"[\wçğıöşü]+", folded)
    kept = [word for word in words if word not in STOPWORDS and len(word) > 2]
    return " ".join(kept[:KEEP])


def covers(directory: str, expected: list[str]) -> bool:
    """Öbek dizini beklenen dosyalardan birini kapsıyor mu."""
    return any(path == directory or path.startswith(directory + "/") for path in expected)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("questions")
    parser.add_argument("--split", default="dev", choices=["dev", "test", "all"])
    parser.add_argument("-k", type=int, default=8)
    parser.add_argument("--provider", default="voyage")
    parser.add_argument("--show", action="store_true", help="Soru soru dök")
    args = parser.parse_args()

    _load_env()
    data = json.loads(Path(args.questions).read_text(encoding="utf-8"))
    questions = [
        q for q in data["questions"] if args.split == "all" or q.get("split") == args.split
    ]
    if not questions:
        print("Bölmede soru yok.")
        return 1

    records = read_jsonl(Path(data["index"]))
    embedder = get_embedder(args.provider, None)
    cache = EmbeddingCache(embedder.name)
    vectors = np.stack([cache.get(r["content_hash"]) for r in records])
    searcher = HybridSearch(records, vectors, CachedEmbedder(embedder, cache))
    mode = resolve_mode(None, args.provider)

    plain = menu = ceiling = 0
    rows = []
    for question in questions:
        expected = question["expect_files"]
        vague = narrow(question["question"])

        hits = searcher.search(vague, k=args.k, mode=mode)
        got = {hit.record["path"] for hit in hits}
        plain_hit = bool(got & set(expected))

        found = facets(searcher, vague, mode=mode)
        menu_hit = any(covers(facet.directory, expected) for facet in found)

        ceiling_hit = plain_hit
        for facet in found:
            refined = searcher.search(facet.query(vague), k=args.k, mode=mode)
            if {hit.record["path"] for hit in refined} & set(expected):
                ceiling_hit = True
                break

        plain += plain_hit
        menu += menu_hit
        ceiling += ceiling_hit
        rows.append((question["id"], vague, plain_hit, menu_hit, ceiling_hit))

    n = len(questions)
    if args.show:
        for qid, vague, p, m, c in rows:
            flags = f"{'✓' if p else '✗'} düz  {'✓' if m else '✗'} menü  {'✓' if c else '✗'} tavan"
            print(f"  {qid:<5} {flags}   {vague}")
        print()

    print(f"{Path(args.questions).name} · {args.split} · n={n} · daraltılmış sorgu, k={args.k}")
    for name, hits, note in (
        ("düz", plain, "bugünkü davranış"),
        ("menü", menu, "doğru dizin beş öbekten birinde"),
        ("tavan", ceiling, "en iyi öbek seçilseydi — ÜST SINIR"),
    ):
        alt, ust = wilson_interval(hits, n)
        print(f"  {name:<6} : {hits / n:.0%}  ({hits}/{n})  (%95 GA: {alt:.0%}-{ust:.0%})   {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
