"""Contextual retrieval testleri. LLM çağrılmıyor; sahte istemciyle mantık test ediliyor."""

from __future__ import annotations

import json

import pytest

from codeqa.contextual import ContextGenerator, _hash_embed_text
from codeqa.indexer import extract_from_source

SOURCE = '''
"""HTTP istemcisi."""


def should_retry(status: int) -> bool:
    """Yeniden deneme kararı."""
    return status >= 500


def build_headers(token: str) -> dict:
    """Başlıkları hazırlar."""
    return {"authorization": token}
'''


class _FakeClient:
    """messages.create çağrılarını kaydeden sahte istemci."""

    def __init__(self, reply: str = "Bu parça yeniden deneme kararını veriyor."):
        self.reply = reply
        self.calls: list[dict] = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)

        class _Block:
            type = "text"
            text = self.reply

        class _Usage:
            input_tokens = 10
            output_tokens = 5
            cache_read_input_tokens = 0
            cache_creation_input_tokens = 0

        class _Response:
            content: list = [_Block()]  # noqa: RUF012 - testte sabit sahte yanıt
            usage = _Usage()

        return _Response()


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "client.py").write_text(SOURCE.strip() + "\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def records():
    return [c.to_dict() for c in extract_from_source(SOURCE, "client.py")]


def make(repo, tmp_path, client, **kwargs):
    return ContextGenerator(
        repo_root=repo, client=client, cache_path=tmp_path / "ctx.json", **kwargs
    )


def test_context_sentence_is_appended(repo, tmp_path, records):
    generator = make(repo, tmp_path, _FakeClient())
    enriched, generated = generator.enrich(records)

    assert generated == len(records)
    assert all("yeniden deneme kararını" in r["context"] for r in enriched)
    # Deterministik etiket kaybolmamalı
    assert all("client.py" in r["context"] for r in enriched)


def test_llm_context_stored_separately(repo, tmp_path, records):
    enriched, _ = make(repo, tmp_path, _FakeClient()).enrich(records)
    assert all(r["llm_context"] for r in enriched)


def test_content_hash_is_recomputed(repo, tmp_path, records):
    """Bağlam değişti ama karma aynı kalırsa embedding önbelleği eski vektörü döndürür."""
    before = {r["id"]: r["content_hash"] for r in records}
    enriched, _ = make(repo, tmp_path, _FakeClient()).enrich(records)

    for record in enriched:
        assert record["content_hash"] != before[record["id"]]
        assert record["content_hash"] == _hash_embed_text(record)


def test_cache_prevents_second_call(repo, tmp_path, records):
    client = _FakeClient()
    generator = make(repo, tmp_path, client)
    generator.enrich(records)
    first_calls = len(client.calls)

    again = make(repo, tmp_path, client)
    _, generated = again.enrich(records)

    assert generated == 0
    assert len(client.calls) == first_calls  # yeni çağrı yok


def test_file_content_is_cached_in_prompt(repo, tmp_path, records):
    """Dosya içeriği her parça için tekrar gönderiliyor; önbelleklenmezse pahalı."""
    client = _FakeClient()
    make(repo, tmp_path, client).enrich(records)

    first_block = client.calls[0]["messages"][0]["content"][0]
    assert first_block["cache_control"] == {"type": "ephemeral"}
    assert "HTTP istemcisi" in first_block["text"]


def test_chunks_of_same_file_are_processed_together(repo, tmp_path, records):
    """Araya başka dosya girerse prompt önbelleği boşa gidiyor."""
    other = [dict(r, path="other.py", id=r["id"] + "-o") for r in records]
    (repo / "other.py").write_text(SOURCE.strip(), encoding="utf-8")
    client = _FakeClient()

    # Kayıtlar bilerek karıştırılmış hâlde veriliyor
    mixed = [records[0], other[0], records[1], other[1]]
    make(repo, tmp_path, client).enrich(mixed)

    paths = [c["messages"][0]["content"][0]["text"].split('"')[1] for c in client.calls]
    assert paths == sorted(paths), "aynı dosyanın parçaları ardışık olmalı"


def test_missing_file_leaves_record_untouched(tmp_path, records):
    generator = ContextGenerator(
        repo_root=tmp_path / "yok", client=_FakeClient(), cache_path=tmp_path / "c.json"
    )
    enriched, generated = generator.enrich(records)

    assert generated == 0
    assert enriched[0]["content_hash"] == records[0]["content_hash"]


def test_language_is_part_of_cache_key(repo, tmp_path, records):
    """Farklı dilde üretilmiş cümleler birbirinin yerine kullanılamaz."""
    client = _FakeClient()
    make(repo, tmp_path, client, language="tr").enrich(records)
    calls_after_tr = len(client.calls)

    make(repo, tmp_path, client, language="en").enrich(records)

    assert len(client.calls) > calls_after_tr


def test_unknown_language_rejected(repo, tmp_path):
    with pytest.raises(ValueError, match="Desteklenmeyen dil"):
        make(repo, tmp_path, _FakeClient(), language="de")


def test_cache_survives_restart(repo, tmp_path, records):
    make(repo, tmp_path, _FakeClient()).enrich(records)
    saved = json.loads((tmp_path / "ctx.json").read_text(encoding="utf-8"))
    assert len(saved) == len(records)
    assert all("::" in key for key in saved)


class _FlakyClient(_FakeClient):
    """İlk N çağrıda zaman aşımı atan istemci."""

    def __init__(self, fail_times: int):
        super().__init__()
        self.fail_times = fail_times
        self.attempts = 0

    def create(self, **kwargs):
        import anthropic

        self.attempts += 1
        if self.attempts <= self.fail_times:
            raise anthropic.APITimeoutError(request=None)
        return super().create(**kwargs)


def test_transient_error_is_retried(repo, tmp_path, records):
    client = _FlakyClient(fail_times=2)
    generator = ContextGenerator(
        repo_root=repo, client=client, cache_path=tmp_path / "c.json", max_retries=4
    )
    enriched, generated = generator.enrich(records[:1])

    assert generated == 1
    assert generator.failures == []
    assert enriched[0]["llm_context"]


def test_permanent_failure_does_not_stop_the_run(repo, tmp_path, records):
    """Binlerce çağrılık işte tek bir hata tüm koşuyu düşürmemeli."""
    client = _FlakyClient(fail_times=99)
    generator = ContextGenerator(
        repo_root=repo, client=client, cache_path=tmp_path / "c.json", max_retries=2
    )
    enriched, generated = generator.enrich(records)

    assert generated == 0
    assert len(generator.failures) == len(records)
    # Kayıtlar bozulmadan geri dönmeli
    assert enriched[0]["content_hash"] == records[0]["content_hash"]
    assert "llm_context" not in enriched[0]


class _BrokeClient(_FakeClient):
    """Kredi bitmiş gibi davranan istemci."""

    def create(self, **kwargs):
        import anthropic
        import httpx

        raise anthropic.BadRequestError(
            "Error code: 400 - Your credit balance is too low",
            response=httpx.Response(400, request=httpx.Request("POST", "http://x")),
            body=None,
        )


def test_fatal_error_stops_run_without_crashing(repo, tmp_path, records):
    """Kredi bitmesi 1000 dosyalık ilerlemeyi traceback'le düşürmemeli."""
    client = _BrokeClient()
    generator = ContextGenerator(
        repo_root=repo, client=client, cache_path=tmp_path / "c.json", max_retries=3
    )

    enriched, generated = generator.enrich(records)

    assert generated == 0
    assert generator.aborted and "credit balance" in generator.aborted
    assert enriched[0]["content_hash"] == records[0]["content_hash"]


def test_fatal_error_stops_further_calls(repo, tmp_path, records):
    """Durduktan sonra kalan parçalar için boşuna çağrı yapılmamalı."""
    client = _BrokeClient()
    generator = ContextGenerator(
        repo_root=repo, client=client, cache_path=tmp_path / "c.json", max_retries=3
    )
    generator.enrich(records)
    assert len(client.calls) == 0  # BrokeClient hiç kaydetmiyor, hata fırlatıyor
    assert generator.aborted
