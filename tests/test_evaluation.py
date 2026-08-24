"""Ölçüm katmanının testleri.

Metrikler sentetik veriyle test ediliyor: yanlış hesaplanan bir metrik,
yanlış bir iyileştirme kararına yol açar.
"""

from __future__ import annotations

import json

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
        json.dumps({"repo": "/x", "questions": [{"id": "q1", "question": "a", "expect_files": ["a.py"]}]}),
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


def test_anthropic_question_set_is_valid():
    """Büyük soru seti bozulmamış ve dev/test dengeli olmalı."""
    questions, settings = load_questions("eval/questions_anthropic.json")
    dev = [q for q in questions if q.split == "dev"]
    test = [q for q in questions if q.split == "test"]

    assert len(questions) == 60
    assert len(dev) == 40 and len(test) == 20
    assert all(q.expect_files for q in questions)
    assert settings["index"] == "data/anthropic.jsonl"


def test_project_question_set_is_valid():
    """Repodaki gerçek soru seti bozulmamış olmalı."""
    questions, settings = load_questions("eval/questions.json")
    assert len(questions) == 20
    assert settings["repo"] == "."
    assert all(q.expect_files for q in questions)


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
        HybridSearch(records, vectors, embedder), [Question("q1", "create", ["m.py"], ["create"])], k=5
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
