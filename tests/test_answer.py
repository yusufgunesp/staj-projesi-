"""Cevaplama katmanının testleri.

Claude çağrılmıyor. Test edilen şey tool gövdeleri, bağlam kurma ve referans
doğrulama — yani asıl mantık. Tool döngüsünü SDK yürütüyor.
"""

from __future__ import annotations

import pytest

from codeqa.answer import MAX_READ_LINES, Answer, CodebaseAnswerer, extract_citations
from codeqa.embeddings import HashEmbedder, embed_records
from codeqa.indexer import extract_from_source
from codeqa.search import HybridSearch

SOURCE = '''
"""Sipariş akışı."""


def create_order(payload: dict) -> int:
    """Yeni sipariş oluşturur."""
    return 1


def charge_payment(order_id: int) -> bool:
    """Ödeme sağlayıcısına gider."""
    return True
'''


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / "orders.py").write_text(SOURCE.strip() + "\n", encoding="utf-8")
    (tmp_path / "gizli.txt").write_text("repo içi\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def answerer(repo):
    records = [c.to_dict() for c in extract_from_source(SOURCE, "api/orders.py")]
    embedder = HashEmbedder(dimension=256)
    vectors, _ = embed_records(records, embedder, None)
    searcher = HybridSearch(records, vectors, embedder)
    return CodebaseAnswerer(searcher, repo_root=repo, client=object())


# --- read_file ---------------------------------------------------------------


def test_read_file_returns_numbered_lines(answerer):
    output = answerer.read_file("api/orders.py")
    assert "api/orders.py" in output
    assert "    1 |" in output
    assert "def create_order" in output


def test_read_file_respects_line_range(answerer):
    output = answerer.read_file("api/orders.py", start_line=4, end_line=6)
    assert "def create_order" in output
    assert "def charge_payment" not in output


def test_read_file_caps_output(answerer, repo):
    (repo / "buyuk.py").write_text("\n".join(f"satir_{i}" for i in range(1000)), encoding="utf-8")
    output = answerer.read_file("buyuk.py")
    numbered = [line for line in output.splitlines() if " | " in line]
    assert len(numbered) == MAX_READ_LINES
    assert "satır daha" in output


def test_read_file_missing_file(answerer):
    assert "bulunamadı" in answerer.read_file("yok/olan.py")


def test_read_file_start_beyond_end(answerer):
    assert "sadece" in answerer.read_file("api/orders.py", start_line=9999)


@pytest.mark.parametrize(
    "path",
    ["../gizli", "../../etc/passwd", "/etc/passwd", "api/../../..//etc/hosts"],
)
def test_read_file_rejects_paths_outside_repo(answerer, path):
    """Yol modelden geliyor — güvenilmez girdi, repo dışına çıkamamalı."""
    assert answerer.read_file(path).startswith("Hata: Repo dışına çıkılamaz")


def test_read_file_allows_paths_inside_repo(answerer):
    assert "repo içi" in answerer.read_file("gizli.txt")


# --- search_symbol -----------------------------------------------------------


def test_search_symbol_returns_locations(answerer):
    output = answerer.search_symbol("charge_payment")
    assert "api/orders.py:" in output
    assert "charge_payment" in output


def test_search_symbol_no_results(answerer):
    assert "sonuç yok" in answerer.search_symbol("zzzzz_olmayan_sembol")


# --- bağlam ------------------------------------------------------------------


def test_build_context_includes_locations(answerer):
    hits = answerer.searcher.search("sipariş oluştur", k=3)
    context = answerer.build_context(hits)
    assert "api/orders.py:" in context
    assert "--- " in context


def test_build_context_when_search_finds_nothing(answerer):
    assert "Tool'larla" in answerer.build_context([])


def test_build_messages_carries_question(answerer):
    messages = answerer.build_messages("sipariş nasıl oluşuyor", [])
    assert messages[0]["role"] == "user"
    assert "Soru: sipariş nasıl oluşuyor" in messages[0]["content"]


# --- referanslar -------------------------------------------------------------


def test_extract_citations_finds_file_and_line():
    text = "Akış `api/orders.py:88`'de başlıyor, services/payment.py:145'te devam ediyor."
    assert extract_citations(text) == ["api/orders.py:88", "services/payment.py:145"]


def test_extract_citations_deduplicates():
    assert extract_citations("api/a.py:1 ve yine api/a.py:1") == ["api/a.py:1"]


def test_extract_citations_ignores_plain_prose():
    assert extract_citations("Burada satır 88 civarında bir şey var.") == []


def test_verify_citations_accepts_real_lines(answerer):
    assert answerer.verify_citations(["api/orders.py:4"]) == []


def test_verify_citations_flags_missing_file(answerer):
    assert answerer.verify_citations(["yok/olan.py:3"]) == ["yok/olan.py:3"]


def test_verify_citations_flags_line_past_end_of_file(answerer):
    """Model var olmayan bir satır uydurabilir; yakalanması gereken durum bu."""
    assert answerer.verify_citations(["api/orders.py:9999"]) == ["api/orders.py:9999"]


def test_verify_citations_flags_paths_outside_repo(answerer):
    assert answerer.verify_citations(["/etc/passwd:1"]) == ["/etc/passwd:1"]


def test_verify_citations_accepts_shortened_path(answerer):
    """Model yolu kısaltabiliyor: `orders.py:4` = `api/orders.py:4`.

    Uydurma değil, eksik önek — indekste tek eşleşme varsa kabul edilmeli.
    """
    assert answerer.verify_citations(["orders.py:4"]) == []


def test_verify_citations_rejects_ambiguous_shortened_path(answerer):
    """İki dosya aynı adı taşıyorsa hangisi olduğu belirsiz — kabul edilmemeli."""
    answerer.searcher.records = [
        {"path": "api/orders.py"},
        {"path": "web/orders.py"},
    ]
    assert answerer.verify_citations(["orders.py:4"]) == ["orders.py:4"]


def test_ambiguous_short_path_resolved_by_full_path_in_same_answer(answerer, repo):
    """Model önce tam yolu yazıp sonra kısaltıyor; bu bağlam belirsizliği çözer.

    Gerçek koşuda görüldü: cevap `resources/beta/messages/batches.py:49` yazıp
    sonra `batches.py:97` demişti. İki farklı batches.py olduğu için reddedilmişti.
    """
    (repo / "web").mkdir()
    (repo / "web" / "orders.py").write_text("x = 1\n" * 20, encoding="utf-8")
    answerer.searcher.records = [{"path": "api/orders.py"}, {"path": "web/orders.py"}]

    # Tam yol aynı cevapta geçiyorsa kısaltma çözülebilmeli
    assert answerer.verify_citations(["api/orders.py:4", "orders.py:4"]) == []
    # Tam yol yoksa yine belirsiz
    assert answerer.verify_citations(["orders.py:4"]) == ["orders.py:4"]


def test_verify_citations_still_flags_unknown_file(answerer):
    assert answerer.verify_citations(["kesinlikle_yok.py:1"]) == ["kesinlikle_yok.py:1"]


# --- Answer ------------------------------------------------------------------


def test_answer_cited_files_are_unique_and_sorted():
    answer = Answer(
        question="s",
        text="",
        citations=["b.py:10", "a.py:3", "a.py:40"],
    )
    assert answer.cited_files == ["a.py", "b.py"]


def test_answer_defaults_are_empty():
    answer = Answer(question="s", text="cevap")
    assert answer.hits == [] and answer.tool_calls == [] and answer.usage == {}
