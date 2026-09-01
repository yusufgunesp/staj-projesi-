"""Ölçüm katmanının testleri.

Metrikler sentetik veriyle test ediliyor: yanlış hesaplanan bir metrik,
yanlış bir iyileştirme kararına yol açar.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codeqa.embeddings import HashEmbedder, embed_records
from codeqa.evaluation import (
    AnswerResult,
    Question,
    Report,
    RetrievalResult,
    evaluate_answers,
    evaluate_retrieval,
    format_report,
    load_questions,
)
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
def searcher():
    records = [c.to_dict() for c in extract_from_source(SOURCE, "api/orders.py")]
    embedder = HashEmbedder(dimension=512)
    vectors, _ = embed_records(records, embedder, None)
    return HybridSearch(records, vectors, embedder)


# --- soru seti ---------------------------------------------------------------


def test_question_requires_expected_files():
    with pytest.raises(ValueError, match="expect_files"):
        Question.from_dict({"id": "q1", "question": "nedir"})


def test_question_from_dict():
    question = Question.from_dict(
        {"id": "q1", "question": "nedir", "expect_files": ["a.py"], "expect_symbols": ["f"]}
    )
    assert question.id == "q1" and question.expect_symbols == ["f"]


def test_load_questions_rejects_duplicate_ids(tmp_path):
    path = tmp_path / "q.json"
    path.write_text(
        json.dumps(
            {
                "questions": [
                    {"id": "q1", "question": "a", "expect_files": ["a.py"]},
                    {"id": "q1", "question": "b", "expect_files": ["b.py"]},
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Yinelenen soru id"):
        load_questions(path)


def test_load_questions_returns_settings(tmp_path):
    path = tmp_path / "q.json"
    path.write_text(
        json.dumps(
            {"repo": "/x", "questions": [{"id": "q1", "question": "a", "expect_files": ["a.py"]}]}
        ),
        encoding="utf-8",
    )
    questions, settings = load_questions(path)
    assert len(questions) == 1 and settings["repo"] == "/x"


def test_load_questions_filters_by_split(tmp_path):
    path = tmp_path / "q.json"
    path.write_text(
        json.dumps(
            {
                "questions": [
                    {"id": "d1", "question": "a", "expect_files": ["a.py"], "split": "dev"},
                    {"id": "t1", "question": "b", "expect_files": ["b.py"], "split": "test"},
                ]
            }
        ),
        encoding="utf-8",
    )
    dev, _ = load_questions(path, split="dev")
    test, _ = load_questions(path, split="test")
    every, _ = load_questions(path, split="all")

    assert [q.id for q in dev] == ["d1"]
    assert [q.id for q in test] == ["t1"]
    assert len(every) == 2


def test_load_questions_rejects_empty_split(tmp_path):
    path = tmp_path / "q.json"
    path.write_text(
        json.dumps({"questions": [{"id": "d1", "question": "a", "expect_files": ["a.py"]}]}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="bölmesinde soru yok"):
        load_questions(path, split="test")


def test_question_defaults_to_dev_split():
    question = Question.from_dict({"id": "q1", "question": "a", "expect_files": ["a.py"]})
    assert question.split == "dev"


#: Setler zaman içinde büyüyor; test onları dondurmamalı. Sayıyı eşitlik yerine
#: alt sınırla kontrol etmek bozulmayı (silinmiş soru, boş etiket, kaybolan bölme)
#: yine yakalıyor ama soru eklemeyi engellemiyor. Eşitlik yazıldığında her büyütme
#: testi kırıyordu ve testi güncellemek işin bir parçası hâline geliyordu.
def assert_question_set_sound(questions, *, en_az: int) -> None:
    """Bir soru setinin yapısal olarak sağlam olduğunu doğrular."""
    assert len(questions) >= en_az
    assert all(q.expect_files for q in questions), "etiketsiz soru var"
    assert all(q.split in ("dev", "test") for q in questions), "geçersiz bölme"
    ids = [q.id for q in questions]
    assert len(ids) == len(set(ids)), "yinelenen soru id'si"
    # Kural 1: ayar yapılan sette rapor verilmez — iki bölme de dolu olmalı.
    assert any(q.split == "dev" for q in questions)
    assert any(q.split == "test" for q in questions)


def test_anthropic_question_set_is_valid():
    """Büyük soru seti bozulmamış ve dev/test dengeli olmalı."""
    questions, settings = load_questions("eval/questions_anthropic.json")
    assert_question_set_sound(questions, en_az=60)
    assert settings["index"] == "data/anthropic.jsonl"


def test_project_question_set_is_valid():
    """Repodaki gerçek soru seti bozulmamış olmalı."""
    questions, settings = load_questions("eval/questions.json")
    assert_question_set_sound(questions, en_az=20)
    assert settings["repo"] == "."


def test_saleor_question_set_is_valid():
    """İkinci kod tabanının seti: 'bütün ölçümler tek repodan' sorununu kapatan set."""
    questions, settings = load_questions("eval/questions_saleor.json")
    assert_question_set_sound(questions, en_az=40)
    assert settings["index"] == "data/saleor.jsonl"
    # Yollar depo kökünden alınmış indekse göre önekli; önek düşerse hepsi kaçar.
    assert all(f.startswith("saleor/") for q in questions for f in q.expect_files)


# --- getirme ölçümü ----------------------------------------------------------


def test_retrieval_finds_expected_file(searcher):
    questions = [Question("q1", "charge_payment", ["api/orders.py"])]
    result = evaluate_retrieval(searcher, questions, k=5)[0]
    assert result.found and result.rank == 1


def test_retrieval_misses_wrong_file(searcher):
    questions = [Question("q1", "charge_payment", ["baska/dosya.py"])]
    result = evaluate_retrieval(searcher, questions, k=5)[0]
    assert not result.found and result.rank is None


def test_retrieval_checks_symbols(searcher):
    hit = evaluate_retrieval(
        searcher, [Question("q1", "charge_payment", ["api/orders.py"], ["charge_payment"])], k=5
    )[0]
    miss = evaluate_retrieval(
        searcher, [Question("q2", "charge_payment", ["api/orders.py"], ["olmayan_sembol"])], k=5
    )[0]
    assert hit.symbols_found and not miss.symbols_found


def test_retrieval_accepts_qualified_symbol_name():
    """`create` beklenirken `Order.create` geldiyse bulunmuş sayılmalı."""
    source = "class Order:\n    def create(self):\n        return 1\n"
    records = [c.to_dict() for c in extract_from_source(source, "m.py")]
    embedder = HashEmbedder(dimension=512)
    vectors, _ = embed_records(records, embedder, None)

    result = evaluate_retrieval(
        HybridSearch(records, vectors, embedder),
        [Question("q1", "create", ["m.py"], ["create"])],
        k=5,
    )[0]

    assert result.symbols_found


# --- metrikler ---------------------------------------------------------------


def test_recall_and_mrr():
    report = Report(
        label="t",
        retrieval=[
            RetrievalResult("q1", True, 1, True, []),
            RetrievalResult("q2", True, 4, True, []),
            RetrievalResult("q3", False, None, True, []),
        ],
    )
    assert report.recall == pytest.approx(2 / 3)
    assert report.mrr == pytest.approx((1.0 + 0.25 + 0.0) / 3)


def test_answer_metrics():
    report = Report(
        label="t",
        answers=[
            AnswerResult("q1", True, 1.0, 0),
            AnswerResult("q2", True, 0.5, 2),
            AnswerResult("q3", False, 0.0, 0),
        ],
    )
    assert report.answer_accuracy == pytest.approx(2 / 3)
    assert report.file_coverage == pytest.approx(0.5)
    assert report.total_unverified == 2


def test_metrics_on_empty_report():
    report = Report(label="bos")
    assert report.recall == 0.0 and report.mrr == 0.0 and report.answer_accuracy == 0.0


def test_report_saves_to_disk(tmp_path):
    report = Report(label="deneme", retrieval=[RetrievalResult("q1", True, 1, True, [])])
    path = report.save(tmp_path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["label"] == "deneme"
    assert saved["metrics"]["recall"] == 1.0
    assert "created_at" in saved


# --- cevap ölçümü ------------------------------------------------------------


class _StubAnswerer:
    """Claude çağırmadan cevap ölçümünü test etmek için."""

    def __init__(self, cited_files, unverified=()):
        self._cited = cited_files
        self._unverified = list(unverified)

    def ask(self, question):
        from codeqa.answer import Answer

        return Answer(
            question=question,
            text="cevap",
            citations=[f"{path}:1" for path in self._cited],
            unverified_citations=self._unverified,
        )


def test_answer_evaluation_counts_partial_coverage():
    questions = [Question("q1", "soru", ["a.py", "b.py"])]
    result = evaluate_answers(_StubAnswerer(["a.py"]), questions)[0]
    assert result.cited_expected and result.covered == pytest.approx(0.5)


def test_answer_evaluation_marks_miss():
    questions = [Question("q1", "soru", ["a.py"])]
    result = evaluate_answers(_StubAnswerer(["baska.py"]), questions)[0]
    assert not result.cited_expected and result.covered == 0.0


def test_answer_evaluation_counts_unverified():
    questions = [Question("q1", "soru", ["a.py"])]
    result = evaluate_answers(_StubAnswerer(["a.py"], ["yok.py:9"]), questions)[0]
    assert result.unverified == 1


# --- rapor ------------------------------------------------------------------


def test_format_report_lists_failures():
    questions = [Question("q1", "sipariş nasıl oluşuyor", ["api/orders.py"])]
    report = Report(label="t", retrieval=[RetrievalResult("q1", False, None, True, [])])
    output = format_report(report, questions)
    assert "Getirmede başarısız sorular" in output
    assert "sipariş nasıl oluşuyor" in output
    assert "api/orders.py" in output


def test_format_report_without_failures():
    questions = [Question("q1", "soru", ["a.py"])]
    report = Report(label="t", retrieval=[RetrievalResult("q1", True, 1, True, [])])
    assert "Getirmede başarısız" not in format_report(report, questions)


def test_retrieval_coverage_differs_from_recall():
    """Çok dosyalı soruda `found` yeterli değil: üçten birini bulmak da doğru sayılıyor.

    Akış sorularında asıl ölçüt kapsam.
    """
    report = Report(
        label="t",
        retrieval=[
            RetrievalResult("q1", True, 1, None, [], coverage=1 / 3),
            RetrievalResult("q2", True, 1, None, [], coverage=1.0),
        ],
    )
    assert report.recall == 1.0  # ikisi de "bulundu"
    assert report.retrieval_coverage == pytest.approx((1 / 3 + 1.0) / 2)


def test_coverage_is_computed_from_expected_files(searcher):
    question = Question("q1", "charge_payment", ["api/orders.py", "yok/olan.py"])
    result = evaluate_retrieval(searcher, [question], k=5)[0]
    assert result.found  # bir dosya bulundu
    assert result.coverage == pytest.approx(0.5)  # ikiden biri


def test_akis_question_set_is_valid():
    """Akış seti bozulmamış ve gerçekten çok dosyalı olmalı."""
    questions, _ = load_questions("eval/questions_akis.json")
    multi = [q for q in questions if len(q.expect_files) > 1]

    assert_question_set_sound(questions, en_az=20)
    assert len(multi) >= 8, "akış seti çok dosyalı sorular içermeli"


def test_zor_question_set_is_valid():
    """Zor set: raporlanan sayı buradan geliyor, bölme kaybolmamalı."""
    questions, settings = load_questions("eval/questions_zor.json")
    assert_question_set_sound(questions, en_az=26)
    assert settings["index"] == "data/anthropic.jsonl"


def test_symbol_recall_is_none_when_no_question_expects_symbols():
    """Ölçülmemiş olmak başarısız olmak değil: %0 yerine None dönmeli."""
    report = Report(label="t")
    report.retrieval = [
        RetrievalResult(question_id="a", found=True, rank=1, symbols_found=None, returned=[]),
        RetrievalResult(question_id="b", found=True, rank=2, symbols_found=None, returned=[]),
    ]
    assert report.symbol_recall is None
    assert report.to_dict()["metrics"]["symbol_recall"] is None


def test_symbol_recall_counts_only_questions_with_expectations():
    report = Report(label="t")
    report.retrieval = [
        RetrievalResult(question_id="a", found=True, rank=1, symbols_found=True, returned=[]),
        RetrievalResult(question_id="b", found=True, rank=1, symbols_found=False, returned=[]),
        RetrievalResult(question_id="c", found=True, rank=1, symbols_found=None, returned=[]),
    ]
    assert report.symbol_recall == 0.5  # beklentisiz soru paydaya girmiyor


def test_pyproject_dependencies_match_requirements():
    """İki kurulum yolu ayrışırsa `pip install -e .` farklı bir ortam üretir."""
    import re
    import tomllib

    kok = Path(__file__).resolve().parent.parent
    with open(kok / "pyproject.toml", "rb") as handle:
        bagimliliklar = tomllib.load(handle)["project"]["dependencies"]
    kod = {re.split(r"[><=]", d)[0].strip() for d in bagimliliklar}

    istenen = set()
    for satir in (kok / "requirements.txt").read_text().splitlines():
        satir = satir.split("#")[0].strip()
        if satir:
            istenen.add(re.split(r"[><=]", satir)[0].strip())
    # pytest ve ruff pyproject'te ayrı grupta (optional-dependencies.dev)
    istenen -= {"pytest", "ruff"}
    assert kod == istenen, f"eksik: {istenen - kod}, fazla: {kod - istenen}"


def test_mode_defaults_follow_the_provider():
    """Yanlış eşleşme sessizce kalite kaybettiriyordu; kullanıcıya bırakılmıyor."""
    from codeqa.cli import resolve_mode

    assert resolve_mode(None, "hash") == "hybrid"  # yer tutucu: BM25 taşıyor
    assert resolve_mode(None, "voyage") == "vector"  # gerçek sağlayıcı: saf vektör
    assert resolve_mode(None, "ollama") == "vector"
    assert resolve_mode("bm25", "voyage") == "bm25"  # açıkça verilen kazanır


def test_wilson_interval_stays_inside_zero_one():
    """Basit normal yaklaşım 14/14'te üst sınırı %100'ün üstüne taşıyor."""
    from codeqa.evaluation import wilson_interval

    alt, ust = wilson_interval(14, 14)
    assert ust == 1.0
    assert 0.7 < alt < 0.8  # tavana vursa bile belirsizlik görünür kalıyor
    assert wilson_interval(0, 0) == (0.0, 0.0)


def test_small_sample_interval_is_wider_than_large_sample():
    """Aynı oran, farklı örneklem: 14 soruda aralık 60 soruya göre geniş olmalı."""
    from codeqa.evaluation import wilson_interval

    dar = wilson_interval(56, 60)
    genis = wilson_interval(13, 14)
    assert (genis[1] - genis[0]) > (dar[1] - dar[0])


def test_mean_interval_handles_fractional_coverage():
    """Kapsam ikili değil (bir soru 0.5 kapsanmış olabilir); Wilson uygun değil."""
    from codeqa.evaluation import mean_interval

    alt, ust = mean_interval([1.0, 0.5, 1.0, 0.5, 1.0])
    assert alt < 0.8 < ust
    assert mean_interval([1.0]) == (0.0, 0.0)  # tek gözlemde aralık tanımsız


def test_demo_komutlari_guncel():
    """DEMO_KOMUTLAR.txt, DEMO.md ile eşit olmalı.

    Provada çıktı: senaryo ham markdown olarak açılınca kod bloğunun ```
    tırnakları da kopyalanıyor ve zsh onları komut ikamesi sayıp iç içe bir
    kabuk açıyor — sonraki komutlar sessizce koşmuyor. Düz metin kopya bunun
    için var; DEMO.md değişip bu dosya kalırsa demoda yanlış komut kopyalanır.
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path("tools").resolve()))
    from komutlari_cikar import uret

    beklenen = uret(Path("DEMO.md").read_text(encoding="utf-8"))
    assert Path("DEMO_KOMUTLAR.txt").read_text(encoding="utf-8") == beklenen, (
        "DEMO.md değişmiş — `python3 tools/komutlari_cikar.py` çalıştırın."
    )
