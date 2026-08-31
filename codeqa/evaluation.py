"""Ölçüm katmanı: doğruluk oranını veriye bağlar.

İki ayrı şey ölçülüyor, çünkü ikisi ayrı ayrı bozulabiliyor:

1. **Getirme (retrieval)** — arama doğru parçayı ilk k sonuca soktu mu?
   API çağrısı gerektirmiyor, saniyeler sürüyor. Arama tarafındaki her
   değişikliği bununla ölçmek lazım.
2. **Cevap** — model doğru dosyaya referans verdi mi? Claude çağrısı
   gerektiriyor, yani yavaş ve paralı.

Getirme bozuksa cevap da bozulur ama tersi doğru değil: arama doğru parçayı
getirip model yine de yanlış cevap verebilir. Ayrı ölçülmezse hangisini
düzelteceğin belli olmuyor.

Koşu çıktıları `runs/` altına yazılıyor; aynı soruyu tekrar sormak için yeniden
API çağrısı yapılmıyor.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .search import HybridSearch

RUNS_DIR = Path("runs")

#: %95 güven düzeyi için normal dağılım katsayısı.
Z95 = 1.96


def wilson_interval(basari: int, deneme: int, z: float = Z95) -> tuple[float, float]:
    """İkili bir oran için Wilson skor aralığı.

    Küçük örneklemde normal yaklaşımdan doğru: 14 soruda 13 başarı, basit
    yaklaşımla %93 ± %13 verip üst sınırı %106'ya taşırken Wilson aralığı
    [0,66 – 0,99] diyor. Bu setlerde soru sayısı hep küçük olduğu için önemli.
    """
    if deneme == 0:
        return (0.0, 0.0)
    oran = basari / deneme
    payda = 1 + z**2 / deneme
    merkez = (oran + z**2 / (2 * deneme)) / payda
    yari = z * ((oran * (1 - oran) / deneme + z**2 / (4 * deneme**2)) ** 0.5) / payda
    return (max(0.0, merkez - yari), min(1.0, merkez + yari))


def mean_interval(degerler: list[float], z: float = Z95) -> tuple[float, float]:
    """Ortalamanın güven aralığı — kapsam gibi 0-1 arası kesir ortalamaları için.

    Kapsam ikili değil (bir soru 0.5 kapsanmış olabilir), o yüzden Wilson
    uygun değil; ortalamanın standart hatası kullanılıyor.
    """
    n = len(degerler)
    if n < 2:
        return (0.0, 0.0)
    ortalama = sum(degerler) / n
    varyans = sum((d - ortalama) ** 2 for d in degerler) / (n - 1)
    hata = z * (varyans / n) ** 0.5
    return (max(0.0, ortalama - hata), min(1.0, ortalama + hata))


@dataclass
class Question:
    """Tek bir test sorusu ve beklenen cevabın nerede olduğu.

    `expect_files` zorunlu: cevabın hangi dosyalarda olduğu elle işaretleniyor.
    `expect_symbols` isteğe bağlı ve daha sıkı bir ölçüt — doğru dosyanın
    doğru fonksiyonu getirildi mi?
    """

    id: str
    question: str
    expect_files: list[str]
    expect_symbols: list[str] = field(default_factory=list)
    note: str = ""
    #: "dev" ayar yapmak için, "test" rapor etmek için. Aynı sorular üzerinde
    #: hem ayar yapıp hem rapor etmek, ayarın o setin gürültüsüne uydurulması
    #: demek — ölçüm iyimser çıkar ve gerçek kullanımda tutmaz.
    split: str = "dev"

    @classmethod
    def from_dict(cls, data: dict) -> Question:
        missing = {"id", "question", "expect_files"} - data.keys()
        if missing:
            raise ValueError(f"Soruda eksik alan: {', '.join(sorted(missing))}")
        return cls(
            id=data["id"],
            question=data["question"],
            expect_files=list(data["expect_files"]),
            expect_symbols=list(data.get("expect_symbols", [])),
            note=data.get("note", ""),
            split=data.get("split", "dev"),
        )


@dataclass
class RetrievalResult:
    """Bir sorunun getirme sonucu."""

    question_id: str
    found: bool
    rank: int | None  # doğru parçanın ilk göründüğü sıra (1'den başlar)
    #: Sorunun sembol beklentisi yoksa None. Bool olsaydı beklentisi olmayan
    #: sorular "bulundu" sayılıp metriği şişirirdi — 60 sorunun 50'si böyle.
    symbols_found: bool | None
    returned: list[str]  # getirilen konumlar, hata ayıklama için
    #: Beklenen dosyaların kaçı ilk k'da bulundu (0..1). Akış sorularında asıl
    #: ölçüt bu: `found` çok dosyalı soruda üçten birini bulmakla da doğru
    #: oluyor, oysa cevabın tamamı için hepsi gerekiyor.
    coverage: float = 0.0

    @property
    def reciprocal_rank(self) -> float:
        return 1.0 / self.rank if self.rank else 0.0


@dataclass
class AnswerResult:
    """Bir sorunun cevap sonucu."""

    question_id: str
    cited_expected: bool  # beklenen dosyalardan en az birine referans verdi mi
    covered: float  # beklenen dosyaların kaçına referans verdi (0..1)
    unverified: int  # doğrulanamayan referans sayısı
    #: Cevapta referans verilen farklı dosya sayısı. Kapsam metriğinin kontrolü:
    #: "hepsine referans ver" gibi bir yönerge kapsamı, modele her şeyi
    #: saydırarak da yükseltebilir. O durumda bu sayı da fırlar — kapsam artışı
    #: bunun sabit kaldığı yerde gerçek.
    cited_files: int = 0
    text: str = ""
    tool_calls: int = 0
    usage: dict[str, int] = field(default_factory=dict)


@dataclass
class Report:
    """Bir koşunun özeti."""

    label: str
    retrieval: list[RetrievalResult] = field(default_factory=list)
    answers: list[AnswerResult] = field(default_factory=list)
    settings: dict = field(default_factory=dict)

    @property
    def recall(self) -> float:
        """Doğru parçanın ilk k sonuçta bulunma oranı."""
        if not self.retrieval:
            return 0.0
        return sum(r.found for r in self.retrieval) / len(self.retrieval)

    @property
    def symbol_recall(self) -> float | None:
        """Sadece sembol beklentisi olan sorular üzerinden.

        Beklentisi olmayan sorular hesaba katılırsa metrik anlamsızlaşıyor:
        60 sorunun 50'sinde sembol beklentisi yok, hepsi bedava "bulundu"
        sayılırdı.

        Hiç beklentisi olan soru yoksa **None** dönüyor, 0.0 değil: akış
        setinde tek bir soruda bile sembol beklentisi yok ve rapor "%0" yazınca
        "hepsini kaçırdı" gibi okunuyordu. Ölçülmemiş olmakla başarısız olmak
        aynı şey değil.
        """
        scored = [r for r in self.retrieval if r.symbols_found is not None]
        if not scored:
            return None
        return sum(bool(r.symbols_found) for r in scored) / len(scored)

    @property
    def retrieval_coverage(self) -> float:
        """Beklenen dosyaların ortalama kaçı getirildi.

        Tek konumlu sorularda `recall` ile aynı şeyi söylüyor; çok dosyaya
        yayılan akış sorularında ayrışıyor ve asıl bilgiyi bu veriyor.
        """
        if not self.retrieval:
            return 0.0
        return sum(r.coverage for r in self.retrieval) / len(self.retrieval)

    @property
    def mrr(self) -> float:
        """Ortalama karşılıklı sıra: doğru sonuç ne kadar üstte çıkıyor."""
        if not self.retrieval:
            return 0.0
        return sum(r.reciprocal_rank for r in self.retrieval) / len(self.retrieval)

    @property
    def answer_accuracy(self) -> float:
        if not self.answers:
            return 0.0
        return sum(a.cited_expected for a in self.answers) / len(self.answers)

    @property
    def citations_per_answer(self) -> float:
        """Cevap başına ortalama kaç farklı dosyaya referans verildi.

        Kalite ölçütü değil, kapsam metriğinin kontrolü.
        """
        if not self.answers:
            return 0.0
        return sum(a.cited_files for a in self.answers) / len(self.answers)

    @property
    def file_coverage(self) -> float:
        """Beklenen dosyaların ortalama kaçına referans verildi."""
        if not self.answers:
            return 0.0
        return sum(a.covered for a in self.answers) / len(self.answers)

    @property
    def total_unverified(self) -> int:
        return sum(a.unverified for a in self.answers)

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "settings": self.settings,
            "metrics": {
                "recall": round(self.recall, 4),
                "retrieval_coverage": round(self.retrieval_coverage, 4),
                "symbol_recall": (
                    None if self.symbol_recall is None else round(self.symbol_recall, 4)
                ),
                "mrr": round(self.mrr, 4),
                "answer_accuracy": round(self.answer_accuracy, 4),
                "file_coverage": round(self.file_coverage, 4),
                "total_unverified": self.total_unverified,
                "citations_per_answer": round(self.citations_per_answer, 2),
            },
            "retrieval": [vars(r) for r in self.retrieval],
            "answers": [vars(a) for a in self.answers],
        }

    def save(self, directory: Path = RUNS_DIR) -> Path:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = directory / f"{stamp}-{self.label}.json"
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path


def load_questions(path: Path, split: str | None = None) -> tuple[list[Question], dict]:
    """Soru setini okur. (sorular, ayarlar) döner.

    `split` verilirse sadece o bölme döner ("dev" ya da "test").
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    questions = [Question.from_dict(item) for item in data["questions"]]

    ids = [q.id for q in questions]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise ValueError(f"Yinelenen soru id'si: {', '.join(sorted(duplicates))}")

    if split and split != "all":
        questions = [q for q in questions if q.split == split]
        if not questions:
            raise ValueError(f"'{split}' bölmesinde soru yok")

    settings = {key: value for key, value in data.items() if key != "questions"}
    return questions, settings


def evaluate_retrieval(
    searcher: HybridSearch, questions: list[Question], k: int = 8, mode: str = "hybrid"
) -> list[RetrievalResult]:
    """Aramanın doğru parçayı getirip getirmediğini ölçer. API çağrısı yok."""
    results: list[RetrievalResult] = []
    for question in questions:
        hits = searcher.search(question.question, k=k, mode=mode)
        expected = set(question.expect_files)

        rank = None
        for position, hit in enumerate(hits, start=1):
            if hit.record["path"] in expected:
                rank = position
                break

        symbols_found = None
        if question.expect_symbols:
            names = {hit.name for hit in hits}
            # Nitelenmiş ad da sayılmalı: `create` beklenirken `Order.create` geldiyse bulundu.
            symbols_found = all(
                any(name == symbol or name.endswith(f".{symbol}") for name in names)
                for symbol in question.expect_symbols
            )

        returned_paths = {hit.record["path"] for hit in hits}
        results.append(
            RetrievalResult(
                question_id=question.id,
                found=rank is not None,
                rank=rank,
                symbols_found=symbols_found,
                returned=[hit.location for hit in hits],
                coverage=len(expected & returned_paths) / len(expected) if expected else 0.0,
            )
        )
    return results


def evaluate_answers(
    answerer, questions: list[Question], progress: bool = False
) -> list[AnswerResult]:
    """Modelin doğru dosyaya referans verip vermediğini ölçer. Claude çağırır."""
    results: list[AnswerResult] = []
    for index, question in enumerate(questions, start=1):
        if progress:
            print(f"[{index}/{len(questions)}] {question.id}: {question.question[:60]}...")
        answer = answerer.ask(question.question)

        cited = set(answer.cited_files)
        expected = set(question.expect_files)
        overlap = cited & expected

        results.append(
            AnswerResult(
                question_id=question.id,
                cited_expected=bool(overlap),
                covered=len(overlap) / len(expected) if expected else 0.0,
                unverified=len(answer.unverified_citations),
                cited_files=len(cited),
                text=answer.text,
                tool_calls=len(answer.tool_calls),
                usage=answer.usage,
            )
        )
    return results


def format_report(report: Report, questions: list[Question]) -> str:
    """Raporu okunabilir metne çevirir."""
    by_id = {q.id: q for q in questions}
    lines = [f"Koşu: {report.label}", ""]

    if report.retrieval:
        lines.append("Getirme (arama doğru parçayı buldu mu):")
        for result in report.retrieval:
            mark = "✓" if result.found else "✗"
            rank = f"sıra {result.rank}" if result.rank else "bulunamadı"
            symbol = "  (sembol eksik)" if result.symbols_found is False else ""
            lines.append(f"  {mark} {result.question_id}: {rank}{symbol}")
        lines.append("")
        n = len(report.retrieval)
        alt, ust = wilson_interval(sum(r.found for r in report.retrieval), n)
        lines.append(
            f"  recall@k     : {report.recall:.0%}  (%95 GA: {alt:.0%}-{ust:.0%}, n={n})"
        )
        kalt, kust = mean_interval([r.coverage for r in report.retrieval])
        lines.append(
            f"  kapsam       : {report.retrieval_coverage:.0%}  (%95 GA: {kalt:.0%}-{kust:.0%})"
        )
        symbol_line = (
            "ölçülmedi (sette sembol beklentisi yok)"
            if report.symbol_recall is None
            else f"{report.symbol_recall:.0%}"
        )
        lines.append(f"  sembol recall: {symbol_line}")
        lines.append(f"  MRR          : {report.mrr:.3f}")
        lines.append("")

    if report.answers:
        lines.append("Cevap (doğru dosyaya referans verdi mi):")
        for result in report.answers:
            mark = "✓" if result.cited_expected else "✗"
            warn = f"  ⚠ {result.unverified} doğrulanamayan" if result.unverified else ""
            lines.append(
                f"  {mark} {result.question_id}: kapsam {result.covered:.0%}, "
                f"{result.tool_calls} tool{warn}"
            )
        lines.append("")
        na = len(report.answers)
        aalt, aust = wilson_interval(sum(a.cited_expected for a in report.answers), na)
        lines.append(
            f"  doğruluk       : {report.answer_accuracy:.0%}"
            f"  (%95 GA: {aalt:.0%}-{aust:.0%}, n={na})"
        )
        lines.append(f"  dosya kapsamı  : {report.file_coverage:.0%}")
        lines.append(f"  uydurma referans: {report.total_unverified}")
        lines.append(f"  cevap başına referans: {report.citations_per_answer:.1f} dosya")

    failures = [r.question_id for r in report.retrieval if not r.found]
    if failures:
        lines.append("")
        lines.append("Getirmede başarısız sorular:")
        for question_id in failures:
            question = by_id.get(question_id)
            if question:
                lines.append(f"  {question_id}: {question.question}")
                lines.append(f"    beklenen: {', '.join(question.expect_files)}")

    return "\n".join(lines)
