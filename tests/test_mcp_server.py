"""MCP sunucusunun testleri.

Ağ yok: `hash` sağlayıcısı ve geçici bir indeks kullanılıyor. Test edilen şey
tool sözleşmesi — istemciye ne döndüğü ve indeks hazır değilken ne söylendiği.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from codeqa.embeddings import EmbeddingCache, embed_records, get_embedder
from codeqa.indexer import index_repo
from codeqa.mcp_server import IndexNotReady, build_server, format_hit, load_searcher
from codeqa.search import SearchHit

SOURCE = '''
"""Sipariş akışı."""


def create_order(payload: dict) -> int:
    """Yeni sipariş oluşturur."""
    return 1


def charge_payment(order_id: int) -> bool:
    """Ödeme sağlayıcısına gider."""
    return True
'''

GO_SOURCE = "package orders\nfunc Charge(amount int) bool { return true }\n"


@pytest.fixture
def indexed(tmp_path):
    """Küçük, iki dilli bir repo indeksleyip vektörlerini üretir."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "orders.py").write_text(SOURCE.strip() + "\n", encoding="utf-8")
    (repo / "billing.go").write_text(GO_SOURCE, encoding="utf-8")

    chunks, _ = index_repo(repo)
    records = [c.to_dict() for c in chunks]
    index_path = tmp_path / "chunks.jsonl"
    index_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8"
    )

    cache_dir = tmp_path / "emb"
    embedder = get_embedder("hash")
    embed_records(records, embedder, EmbeddingCache(embedder.name, cache_dir=cache_dir))
    return index_path, cache_dir


def server_for(indexed):
    index_path, cache_dir = indexed
    return build_server(index_path, provider="hash", mode="hybrid", cache_dir=cache_dir)


def call(server, name: str, arguments: dict) -> str:
    """Tool'u çağırıp metin içeriğini döndürür."""
    result = asyncio.run(server.call_tool(name, arguments))
    return "\n".join(getattr(block, "text", "") for block in result.content)


# --- indeks hazır değilken -----------------------------------------------


def test_missing_index_explains_what_to_run(tmp_path):
    with pytest.raises(IndexNotReady, match="codeqa index"):
        load_searcher(tmp_path / "yok.jsonl", "hash", None)


def test_missing_vectors_explain_what_to_run(tmp_path, indexed):
    """Vektörler üretilmemişse kullanıcı ne yapacağını bilmeli."""
    index_path, _ = indexed
    with pytest.raises(IndexNotReady, match="codeqa embed"):
        load_searcher(index_path, "hash", None, cache_dir=tmp_path / "bos")


def test_tool_returns_message_instead_of_crashing(tmp_path):
    """MCP istemcisine traceback değil, anlaşılır bir mesaj gitmeli."""
    server = build_server(tmp_path / "yok.jsonl", provider="hash")
    assert "codeqa index" in call(server, "search_code", {"query": "herhangi"})


# --- tool sözleşmesi ------------------------------------------------------


def test_three_tools_are_registered(indexed):
    names = {t.name for t in asyncio.run(server_for(indexed).list_tools())}
    assert names == {"search_code", "read_chunk", "index_status"}


def test_search_returns_locations(indexed):
    output = call(server_for(indexed), "search_code", {"query": "ödeme tahsilatı", "limit": 3})
    assert "orders.py:" in output
    assert "charge_payment" in output


def test_search_respects_limit(indexed):
    output = call(server_for(indexed), "search_code", {"query": "sipariş", "limit": 1})
    assert output.count("[") == 1  # tek sonuç başlığı


def test_search_with_no_match(indexed):
    """Eşleşme yoksa istemciye bunu açıkça söylemeli.

    BM25 modunda ölçülüyor: `hash` embedder'ın vektör tarafı çakışma yüzünden
    alakasız sorguya da sıfırdan büyük benzerlik üretebiliyor, o yüzden bu dalı
    vektör moduyla belirlenimci şekilde test etmek mümkün değil.
    """
    index_path, cache_dir = indexed
    server = build_server(index_path, provider="hash", mode="bm25", cache_dir=cache_dir)
    output = call(server, "search_code", {"query": "zzzz_hicbir_yerde_yok"})
    assert "sonuç yok" in output


def test_read_chunk_returns_full_text(indexed):
    server = server_for(indexed)
    location = call(server, "search_code", {"query": "ödeme", "limit": 1}).split()[0]
    output = call(server, "read_chunk", {"location": location})
    assert location in output
    assert "return True" in output


def test_read_chunk_unknown_location(indexed):
    output = call(server_for(indexed), "read_chunk", {"location": "yok.py:1"})
    assert "indekste yok" in output


def test_index_status_reports_chunk_kinds(indexed):
    """Dil dökümü kaldırıldı: tek dil kaldığı için satır her zaman aynıydı."""
    output = call(server_for(indexed), "index_status", {})
    assert "Türler" in output
    assert "function" in output or "method" in output
    assert "Parça" in output


# --- biçimlendirme --------------------------------------------------------


def test_format_hit_truncates_long_chunks():
    """Uzun parça istemcinin bağlam penceresini doldurmamalı."""
    record = {
        "text": "\n".join(f"satir {i}" for i in range(200)),
        "kind": "function",
        "location": "a.py:1",
        "name": "f",
    }
    output = format_hit(SearchHit(record=record, score=1.0, sources=("vector",)), max_lines=10)
    assert "read_chunk" in output
    assert output.count("satir") == 10


def test_format_hit_keeps_short_chunks_whole():
    record = {
        "text": "def f():\n    return 1",
        "kind": "function",
        "location": "a.py:1",
        "name": "f",
    }
    output = format_hit(SearchHit(record=record, score=1.0, sources=("vector",)))
    assert "return 1" in output
    assert "read_chunk" not in output
