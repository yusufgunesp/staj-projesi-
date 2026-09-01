"""Cevaplama katmanı: aramayı Claude'a bağlar.

Akış şöyle: soru hibrit aramaya gidiyor, bulunan parçalar bağlam olarak
modele veriliyor, model eksik kalan yerleri `read_file` ve `search_symbol`
tool'larıyla canlı okuyor. İndeks her zaman yetmiyor — parça sınırında kesilen
bir fonksiyonun devamı ya da bir çağrının gittiği yer indekste ayrı bir parça
olarak duruyor; tool'lar bu boşluğu kapatıyor.

Cevapların `dosya:satır` referansı taşıması şart: doğruluğu anında kontrol
edilebilmesinin tek yolu bu. Üretilen referanslar `Answer.unverified_citations`
ile ayrıca doğrulanıyor, çünkü model var olmayan bir satır uydurabilir.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .search import HybridSearch, SearchHit

#: Ana katman. Hacimli ve basit işler (ör. değerlendirme çağrıları) için
#: `claude-haiku-4-5` kullanılabilir — `model` parametresiyle değiştirilebilir.
DEFAULT_MODEL = "claude-opus-5"

#: Adaptive thinking'i destekleyen model aileleri. Haiku desteklemiyor ve
#: istek `adaptive thinking is not supported on this model` ile 400 dönüyor —
#: yani ucuz modelle ölçüm yapmak isteyen herkes bu duvara çarpıyor.
THINKING_MODELS = ("claude-opus", "claude-sonnet")


def supports_adaptive_thinking(model: str) -> bool:
    return model.startswith(THINKING_MODELS)


#: Modelin tool çağrısı yapabileceği tur sayısı. Sonsuz döngüye karşı sınır.
MAX_ITERATIONS = 12

#: Bir tool çağrısında okunacak azami satır. Bağlam penceresini korumak için.
MAX_READ_LINES = 400

SYSTEM_PROMPT = """Bir kod tabanı hakkındaki soruları cevaplıyorsun.

Sana aramayla bulunmuş kod parçaları veriliyor. Yetmezse `read_file` ve
`search_symbol` tool'larıyla kodu doğrudan okuyabilirsin.

Kurallar:
- Her iddianı `dosya:satır` biçiminde referansla, ör. `api/orders.py:88`.
  Yolu repo köküne göre tam yaz (`codeqa/models.py:36`, `models.py:36` değil).
  Referansı gerçekten okuduğun koddan al; satır numarası uydurma.
- Akış sorularında adımları sırayla anlat: nerede başlıyor, nereye gidiyor,
  nerede bitiyor.
- Kodda göremediğin bir şeyi tahmin etme. Bilgi indekste yoksa tool'larla ara;
  yine bulamazsan bulamadığını söyle.
- Dikkat çeken bir sorun görürsen (eksik hata yönetimi, ölü kod) kısaca not düş.
- Cevabı sorunun gerektirdiği uzunlukta tut."""

_CITATION = re.compile(r"`?([\w./\\-]+\.[A-Za-z]{1,6}):(\d+)`?")


@dataclass
class Answer:
    """Bir sorunun cevabı ve nasıl üretildiğine dair kayıt.

    Tool çağrıları ve getirilen parçalar da tutuluyor; ölçüm katmanı bunlara
    bakarak "doğru parça getirildi mi" sorusunu cevaplayacak.
    """

    question: str
    text: str
    hits: list[SearchHit] = field(default_factory=list)
    tool_calls: list[tuple[str, dict]] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)
    unverified_citations: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    stop_reason: str | None = None

    @property
    def cited_files(self) -> list[str]:
        seen = {citation.rsplit(":", 1)[0] for citation in self.citations}
        return sorted(seen)


def resolve_in_repo(repo_root: Path, path: str) -> Path:
    """Yolu repo köküne hapseder.

    `path` güvenilmez girdi: cevaplama katmanında modelden, web arayüzünde
    tarayıcıdan geliyor. `../../etc/passwd` ya da mutlak bir yol repo dışına
    çıkabilir; buna izin verilmiyor. İki çağıranın da aynı kontrolü kullanması
    için modül düzeyinde duruyor.
    """
    root = Path(repo_root).resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root):
        raise ValueError(f"Repo dışına çıkılamaz: {path}")
    return target


def extract_citations(text: str) -> list[str]:
    """Cevap metnindeki `dosya:satır` referanslarını sırayla çıkarır."""
    seen: list[str] = []
    for path, line in _CITATION.findall(text):
        citation = f"{path.replace(chr(92), '/')}:{line}"
        if citation not in seen:
            seen.append(citation)
    return seen


class CodebaseAnswerer:
    """Arama + Claude. Tek bir repo için kurulur, çok kez sorgulanır."""

    def __init__(
        self,
        searcher: HybridSearch,
        repo_root: Path,
        client=None,
        model: str = DEFAULT_MODEL,
        context_size: int = 8,
        max_iterations: int = MAX_ITERATIONS,
        mode: str = "hybrid",
    ):
        self.searcher = searcher
        self.repo_root = Path(repo_root).resolve()
        self.model = model
        self.context_size = context_size
        self.max_iterations = max_iterations
        self.mode = mode
        self._client = client
        self._tool_calls: list[tuple[str, dict]] = []

    # --- tool gövdeleri -------------------------------------------------
    # Ayrı metot olarak duruyorlar ki tool sarmalayıcısından bağımsız test
    # edilebilsinler.

    def _resolve(self, path: str) -> Path:
        return resolve_in_repo(self.repo_root, path)

    def read_file(self, path: str, start_line: int = 1, end_line: int = 0) -> str:
        """Dosyayı satır numaralarıyla döndürür."""
        try:
            target = self._resolve(path)
        except ValueError as exc:
            return f"Hata: {exc}"
        if not target.is_file():
            return f"Hata: dosya bulunamadı: {path}"

        lines = target.read_bytes().decode("utf-8", errors="replace").splitlines()
        start = max(1, start_line)
        stop = len(lines) if end_line <= 0 else min(end_line, len(lines))
        stop = min(stop, start + MAX_READ_LINES - 1)
        if start > len(lines):
            return f"Hata: {path} sadece {len(lines)} satır."

        body = "\n".join(f"{i:>5} | {lines[i - 1]}" for i in range(start, stop + 1))
        suffix = ""
        if stop < len(lines):
            suffix = f"\n... ({len(lines) - stop} satır daha, {path}:{stop + 1} ile devam)"
        return f"{path} ({start}-{stop} / {len(lines)} satır)\n{body}{suffix}"

    def search_symbol(self, query: str, limit: int = 5) -> str:
        """İndekste arama yapar; modelin ikinci bir tur arama yapmasını sağlar."""
        hits = self.searcher.search(query, k=limit, mode=self.mode)
        if not hits:
            return f"'{query}' için sonuç yok."
        return "\n\n".join(self._format_hit(hit) for hit in hits)

    # --- bağlam kurma ---------------------------------------------------

    @staticmethod
    def _format_hit(hit: SearchHit) -> str:
        """Parçayı modele satır numaralarıyla verir.

        Numarasız verildiğinde model yalnızca parçanın **başlangıç** satırını
        referans gösterebiliyordu: `_constants.py:3` deyip aslında 10. satırdaki
        `DEFAULT_MAX_RETRIES`'i kastediyordu. Referansı açan kişi ilgisiz bir
        satır görüyordu — oysa aracın bütün iddiası referansın kontrol
        edilebilmesi. `read_file` zaten numaralı veriyor; bağlam da vermeli.
        """
        header = f"--- {hit.location}  [{hit.record['kind']}] {hit.name}"
        start = int(hit.record["start_line"])
        body = "\n".join(
            f"{start + offset:>5} | {line}"
            for offset, line in enumerate(hit.record["text"].splitlines())
        )
        return f"{header}\n{body}"

    def build_context(self, hits: list[SearchHit]) -> str:
        """Bulunan parçaları modele verilecek metne çevirir."""
        if not hits:
            return "(Arama sonuç vermedi. Tool'larla kodu doğrudan aramayı dene.)"
        blocks = "\n\n".join(self._format_hit(hit) for hit in hits)
        return f"Aramayla bulunan parçalar:\n\n{blocks}"

    def build_messages(self, question: str, hits: list[SearchHit]) -> list[dict]:
        content = f"{self.build_context(hits)}\n\nSoru: {question}"
        return [{"role": "user", "content": content}]

    def _resolve_citation_path(self, path: str, preferred: set[str] | None = None) -> Path | None:
        """Referanstaki yolu gerçek bir dosyaya bağlar.

        Model bazen yolu kısaltıyor: `codeqa/models.py` yerine `models.py`.
        Bu bir uydurma değil, eksik önek — indekste tek eşleşme varsa kabul
        ediliyor.

        Birden fazla eşleşme varsa (`resources/messages/batches.py` ve
        `resources/beta/messages/batches.py` gibi) hangisi olduğu belirsiz.
        Ama model genelde ilk geçtiği yerde tam yolu yazıp sonra kısaltıyor;
        `preferred` o cevapta tam yazılmış yolları taşıyor ve belirsizliği
        çözüyor.
        """
        try:
            target = self._resolve(path)
        except ValueError:
            return None
        if target.is_file():
            return target

        suffix = f"/{path.lstrip('/')}"
        matches = {
            record["path"]
            for record in self.searcher.records
            if record["path"] == path or record["path"].endswith(suffix)
        }
        if len(matches) > 1 and preferred:
            narrowed = matches & preferred
            if len(narrowed) == 1:
                matches = narrowed
        if len(matches) != 1:
            return None
        candidate = self.repo_root / matches.pop()
        return candidate if candidate.is_file() else None

    def verify_citations(self, citations: list[str]) -> list[str]:
        """Var olmayan dosya/satıra işaret eden referansları döner."""
        # Aynı cevapta tam yazılmış yollar, sonraki kısaltmaların bağlamı.
        preferred = {
            citation.rpartition(":")[0]
            for citation in citations
            if "/" in citation.rpartition(":")[0]
        }
        unverified: list[str] = []
        for citation in citations:
            path, _, line = citation.rpartition(":")
            target = self._resolve_citation_path(path, preferred)
            if target is None:
                unverified.append(citation)
                continue
            total = len(target.read_bytes().decode("utf-8", errors="replace").splitlines())
            if not 1 <= int(line) <= total:
                unverified.append(citation)
        return unverified

    # --- ana akış -------------------------------------------------------

    def _get_client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    def _build_tools(self):
        from anthropic import beta_tool

        record = self._tool_calls

        @beta_tool
        def read_file(path: str, start_line: int = 1, end_line: int = 0) -> str:
            """Repodaki bir dosyayı satır numaralarıyla okur.

            Args:
                path: Repo köküne göre dosya yolu, ör. api/orders.py
                start_line: Başlangıç satırı (1'den başlar). Varsayılan 1.
                end_line: Bitiş satırı. 0 ise dosyanın sonuna kadar okur.
            """
            record.append(("read_file", {"path": path, "start_line": start_line}))
            return self.read_file(path, start_line, end_line)

        @beta_tool
        def search_symbol(query: str, limit: int = 5) -> str:
            """Kod tabanında sembol ya da konu araması yapar.

            Args:
                query: Aranacak fonksiyon/sınıf adı ya da doğal dilde ifade.
                limit: Dönecek sonuç sayısı. Varsayılan 5.
            """
            record.append(("search_symbol", {"query": query, "limit": limit}))
            return self.search_symbol(query, limit)

        return [read_file, search_symbol]

    def ask(self, question: str) -> Answer:
        """Soruyu cevaplar. Model, gerekirse tool'larla kodu kendisi okur."""
        hits = self.searcher.search(question, k=self.context_size, mode=self.mode)
        self._tool_calls = []

        client = self._get_client()
        # `thinking` yalnızca destekleyen modellere gönderiliyor; Haiku'ya
        # gidince istek 400 ile reddediliyor.
        extra = {"thinking": {"type": "adaptive"}} if supports_adaptive_thinking(self.model) else {}
        runner = client.beta.messages.tool_runner(
            model=self.model,
            max_tokens=16000,
            **extra,
            system=SYSTEM_PROMPT,
            tools=self._build_tools(),
            messages=self.build_messages(question, hits),
            max_iterations=self.max_iterations,
            # Model her tool turunda tüm geçmişi yeniden gönderiyor; bağlam
            # parçaları ve sistem promptu turlar boyunca aynı kalıyor. Önbellek
            # olmadan aynı token'lar tur sayısı kadar tekrar faturalanıyor.
            cache_control={"type": "ephemeral"},
        )

        usage: dict[str, int] = {}
        last = None
        for message in runner:
            last = message
            for field_name in (
                "input_tokens",
                "output_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
            ):
                value = getattr(message.usage, field_name, 0) or 0
                usage[field_name] = usage.get(field_name, 0) + value

        text = ""
        stop_reason = None
        if last is not None:
            stop_reason = last.stop_reason
            text = "\n".join(b.text for b in last.content if b.type == "text").strip()

        citations = extract_citations(text)
        return Answer(
            question=question,
            text=text,
            hits=hits,
            tool_calls=list(self._tool_calls),
            citations=citations,
            unverified_citations=self.verify_citations(citations),
            usage=usage,
            stop_reason=stop_reason,
        )
