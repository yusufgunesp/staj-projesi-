"""Yerel web arayüzü: aracı terminal bilmeyen birinin önüne koyar.

Neden exe değil de bu: exe'nin iki çözülmemiş sorunu vardı — API anahtarları
binary'ye gömülemez (anahtarsız `hash`'e düşüyor, o da Türkçe soruda çalışmıyor)
ve `.exe` Windows demek, geliştirme macOS'ta. Yerel sunucu ikisini de es geçiyor:
anahtarlar `.env`'de kalıyor, tarayıcı her platformda var.

Arayüz iki şey yapıyor:

1. **Proje yönetimi.** Kullanıcı bir repo yolu veriyor, arayüz tarıyor (bedava),
   bileşimini ve maliyet tahminini gösteriyor, onay alınca vektörleştiriyor.
   Sonrasında projeler listeden seçiliyor. CLI'daki `-i` / `--repo` bayrakları
   bu iş için yetmiyordu: proje her çağrıda elle veriliyordu.
2. **Referansı tek tıkla doğrulama.** Terminalde `_base_client.py:818` görünce
   doğrulamak için ayrı bir `sed` komutu gerekiyor; burada referansa tıklayınca
   kaynağın o satırı altında açılıyor. Aracın bütün iddiası "kaynak gösteriyorum,
   kontrol et" olduğuna göre kontrolü ucuzlatmak arayüzün asıl işi.

Bağımlılık eklenmedi: `http.server` yeterli. Paket bağımlılık listesi büyürse
`pip install -e .` iddiası da büyür, demo yolu ise şu an doğrulanmış durumda.
"""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .answer import resolve_in_repo
from .indexer import DEFAULT_EXCLUDES
from .mcp_server import IndexNotReady, load_searcher
from .projects import (
    DEFAULT_REGISTRY,
    JobRunner,
    Project,
    embed_project,
    load_projects,
    save_projects,
    scan_repo,
    slugify,
    smoke_test,
    upsert,
)
from .search import HybridSearch, resolve_mode

#: Referansa tıklanınca gösterilecek satır sayısı (hedefin öncesi ve sonrası).
SOURCE_CONTEXT_LINES = 12

#: İstek gövdesi üst sınırı. Yerel sunucu ama sınırsız okumak yine de yanlış.
MAX_BODY_BYTES = 64 * 1024

#: Haiku 4.5 birim fiyatları (MTok): girdi $1 / çıktı $5 / yazma 1.25x / okuma 0.1x.
#: `cmd_ask` ile aynı hesap; arayüz de her cevabın altında maliyeti yazıyor.
_PRICES = {
    "input_tokens": 1e-6,
    "cache_creation_input_tokens": 1.25e-6,
    "cache_read_input_tokens": 0.1e-6,
    "output_tokens": 5e-6,
}


def estimate_cost(usage: dict[str, int]) -> float:
    return sum(usage.get(field, 0) * price for field, price in _PRICES.items())


def hit_payload(hit) -> dict:
    """Bir arama sonucunu arayüzün anlayacağı sözlüğe çevirir."""
    record = hit.record
    return {
        "location": record["location"],
        "path": record["path"],
        "line": record["start_line"],
        "kind": record["kind"],
        "name": record["name"],
        "signature": record.get("signature") or "",
        "score": round(hit.score, 4),
        "sources": list(hit.sources),
        "text": record["text"],
    }


def read_source(repo_root: Path, path: str, line: int) -> dict:
    """Bir referansın etrafındaki kaynağı okur.

    Arayüzün en önemli çağrısı: referans doğrulaması buradan geçiyor. Yol
    `resolve_in_repo` ile repo köküne hapsediliyor — istek tarayıcıdan geliyor,
    yani güvenilmez.
    """
    target = resolve_in_repo(repo_root, path)
    if not target.is_file():
        return {"error": f"Dosya bulunamadı: {path}"}
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    if not 1 <= line <= len(lines):
        return {
            "error": f"{path} dosyasında {line}. satır yok (dosya {len(lines)} satır).",
            "verified": False,
        }
    start = max(1, line - SOURCE_CONTEXT_LINES // 2)
    end = min(len(lines), line + SOURCE_CONTEXT_LINES)
    return {
        "path": path,
        "line": line,
        "start": start,
        "verified": True,
        "lines": lines[start - 1 : end],
        "total": len(lines),
    }


class _Handler(BaseHTTPRequestHandler):
    """İstekleri karşılar. Durum `server` üzerinde taşınıyor."""

    server_version = "codeqa"

    def log_message(self, fmt, *args) -> None:
        # Varsayılan günlük her isteği stderr'e basıyor; sunum sırasında
        # terminalde akan satırlar dikkat dağıtıyor.
        pass

    # --- yardımcılar ----------------------------------------------------

    def _send(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            raise ValueError("İstek gövdesi çok büyük.")
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    # --- yönlendirme ----------------------------------------------------

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            body = page().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/api/status":
            self._send(self.server.status())
            return
        if self.path == "/api/projects":
            self._send({"projects": self.server.list_projects()})
            return
        if self.path == "/api/guide":
            self._send({"markdown": guide(), "path": str(GUIDE_PATH)})
            return
        if self.path.startswith("/api/jobs/"):
            job = self.server.jobs.get(self.path.rsplit("/", 1)[-1])
            if job is None:
                self._send({"error": "İş bulunamadı."}, status=404)
                return
            self._send(job.to_dict())
            return
        self._send({"error": "yok"}, status=404)

    def do_POST(self) -> None:
        routes = {
            "/api/search": self.server.do_search,
            "/api/ask": self.server.do_ask,
            "/api/source": self.server.do_source,
            "/api/projects/scan": self.server.do_scan,
            "/api/projects/add": self.server.do_add,
            "/api/projects/remove": self.server.do_remove,
            "/api/projects/smoke": self.server.do_smoke,
        }
        handler = routes.get(self.path)
        if handler is None:
            self._send({"error": "yok"}, status=404)
            return
        try:
            self._send(handler(self._body()))
        except ValueError as exc:
            self._send({"error": str(exc)}, status=400)
        except Exception as exc:  # sunum sırasında çökmek yerine mesaj göster
            self._send({"error": f"{type(exc).__name__}: {exc}"}, status=500)


class UIServer(ThreadingHTTPServer):
    """Proje kaydını, aramayı ve cevaplamayı HTTP'ye bağlar.

    `ThreadingHTTPServer` bilinçli: bir cevap 6-13 saniye, bir embedding koşusu
    dakikalar sürüyor ve o sırada arayüzün geri kalanı çalışmaya devam etmeli.

    Aktif proje sunucuda tutulmuyor: her istek hangi projeyi kastettiğini
    kendisi söylüyor. Böylece iki sekme iki farklı projeye bakabiliyor ve
    sunucunun hatırlaması gereken bir durum kalmıyor.
    """

    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        registry: Path = DEFAULT_REGISTRY,
        model: str = "claude-opus-5",
        context_size: int = 8,
        data_dir: Path = Path("data"),
    ):
        super().__init__(address, _Handler)
        self.registry = Path(registry)
        self.model = model
        self.context_size = context_size
        self.data_dir = Path(data_dir)
        self.projects = load_projects(self.registry)
        self.jobs = JobRunner()
        self._searchers: dict[str, tuple[HybridSearch, Path, str]] = {}
        self._lock = threading.Lock()
        self._answer_lock = threading.Lock()

    # --- proje kaydı ------------------------------------------------------

    def list_projects(self) -> list[dict]:
        return [p.to_dict() for p in self.projects]

    def find(self, project_id: str | None) -> Project:
        if not self.projects:
            raise ValueError("Kayıtlı proje yok. Önce bir proje ekleyin.")
        if not project_id:
            return self.projects[0]
        for project in self.projects:
            if project.id == project_id:
                return project
        raise ValueError(f"Proje bulunamadı: {project_id}")

    def register(self, project: Project) -> Project:
        with self._lock:
            self.projects = upsert(self.projects, project)
            save_projects(self.projects, self.registry)
            self._searchers.pop(project.id, None)
        return project

    def searcher_for(self, project: Project) -> tuple[HybridSearch, Path, str]:
        """Projenin arama nesnesini kurar; bir kere kurulup saklanıyor.

        Tembel: sunucu açılışta bütün projelerin vektörlerini belleğe almıyor,
        yalnızca sorulan projeninkini alıyor.
        """
        with self._lock:
            cached = self._searchers.get(project.id)
        if cached:
            return cached

        mode = resolve_mode(project.mode, project.provider)
        try:
            searcher, _ = load_searcher(Path(project.index), project.provider, project.model)
        except IndexNotReady as exc:
            # Kullanıcı hatası, sunucu hatası değil: indeks silinmiş ya da
            # vektörler eksik. Arayüzde 500 yerine okunur mesaj görünsün.
            raise ValueError(str(exc)) from exc
        entry = (searcher, Path(project.repo), mode)
        with self._lock:
            self._searchers[project.id] = entry
        return entry

    # --- uç noktalar ------------------------------------------------------

    def status(self) -> dict:
        return {
            "projects": self.list_projects(),
            "model": self.model,
            "can_answer": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "registry": str(self.registry),
        }

    def do_scan(self, payload: dict) -> dict:
        """Repoyu indeksler ve maliyet önizlemesi döner. Para harcamaz."""
        repo = (payload.get("repo") or "").strip()
        if not repo:
            raise ValueError("Proje yolu boş.")
        provider = payload.get("provider") or "voyage"
        name = (payload.get("name") or "").strip() or Path(repo).expanduser().name
        index_path = self.data_dir / f"{slugify(name)}.jsonl"
        # Dışlama olmadan, içinde klonlanmış repo taşıyan bir proje taranınca
        # sayı ve maliyet on katına çıkıyor. CLI'da `--exclude` var; arayüzde de
        # olmalı, yoksa kullanıcı sebebini anlamadan büyük bir tutar görüyor.
        extra = {x.strip() for x in (payload.get("exclude") or "").split(",") if x.strip()}
        result = scan_repo(
            repo,
            index_path,
            provider=provider,
            model=payload.get("model") or None,
            no_docs=bool(payload.get("no_docs")),
            excludes=frozenset(DEFAULT_EXCLUDES | extra),
        )
        result["exclude"] = ", ".join(sorted(extra))
        result["id"] = slugify(name)
        result["name"] = name
        return result

    def do_add(self, payload: dict) -> dict:
        """Taranmış bir projeyi vektörleştirip kaydeder. Arka planda koşuyor."""
        project = Project(
            id=slugify(payload.get("id") or payload.get("name") or ""),
            name=(payload.get("name") or "").strip() or "proje",
            repo=str(Path(payload["repo"]).expanduser().resolve()),
            index=str(payload["index"]),
            provider=payload.get("provider") or "voyage",
            model=payload.get("model") or None,
            mode=payload.get("mode") or None,
            chunks=int(payload.get("chunks") or 0),
            files=int(payload.get("files") or 0),
            added=time.strftime("%Y-%m-%d %H:%M"),
        )

        language = payload.get("language") or {}

        def work(job) -> dict:
            job.message = "vektörleştiriliyor"

            def progress(done: int, total: int) -> None:
                job.done, job.total = done, total

            computed = embed_project(
                Path(project.index), project.provider, project.model, on_progress=progress
            )

            # Duman testi vektörler yazıldıktan sonra koşuyor: aramanın hiç
            # çalışıp çalışmadığını söylüyor, doğruluğu değil.
            job.message = "duman testi"
            project.checks = {"language": language}
            self.register(project)
            try:
                searcher, _, _ = self.searcher_for(project)
                project.checks["smoke"] = smoke_test(searcher)
            except ValueError as exc:
                project.checks["smoke"] = {"error": str(exc)}
            self.register(project)
            return {"project": project.to_dict(), "computed": computed}

        job = self.jobs.start("embed", work)
        return {"job": job.id, "project": project.to_dict()}

    def do_remove(self, payload: dict) -> dict:
        """Projeyi listeden çıkarır. İndeks ve vektörler diskte kalıyor —
        silmek geri alınamaz ve kullanıcı bunu istemiş olmayabilir."""
        project = self.find(payload.get("project"))
        with self._lock:
            self.projects = [p for p in self.projects if p.id != project.id]
            save_projects(self.projects, self.registry)
            self._searchers.pop(project.id, None)
        return {"removed": project.id, "projects": self.list_projects()}

    def do_smoke(self, payload: dict) -> dict:
        """Duman testini kayıtlı bir proje üzerinde yeniden koşar."""
        project = self.find(payload.get("project"))
        searcher, _, _ = self.searcher_for(project)
        project.checks = {**project.checks, "smoke": smoke_test(searcher)}
        self.register(project)
        return {"project": project.id, "smoke": project.checks["smoke"]}

    def do_search(self, payload: dict) -> dict:
        query = (payload.get("query") or "").strip()
        if not query:
            raise ValueError("Soru boş.")
        project = self.find(payload.get("project"))
        searcher, _, mode = self.searcher_for(project)
        hits = searcher.search(query, k=int(payload.get("k") or 8), mode=mode)
        return {"project": project.id, "hits": [hit_payload(hit) for hit in hits]}

    def do_ask(self, payload: dict) -> dict:
        question = (payload.get("question") or "").strip()
        if not question:
            raise ValueError("Soru boş.")
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise ValueError(
                "ANTHROPIC_API_KEY tanımlı değil; cevaplama kapalı. "
                "Arama tarafı çalışmaya devam ediyor."
            )
        project = self.find(payload.get("project"))
        answer = self._answer(project, question)
        return {
            "project": project.id,
            "text": answer.text,
            "citations": answer.citations,
            "unverified": answer.unverified_citations,
            "tool_calls": [
                {"name": name, "detail": args.get("path") or args.get("query") or ""}
                for name, args in answer.tool_calls
            ],
            "hits": [hit_payload(hit) for hit in answer.hits],
            "usage": answer.usage,
            "cost": round(estimate_cost(answer.usage), 4),
        }

    def do_source(self, payload: dict) -> dict:
        path = (payload.get("path") or "").strip()
        if not path:
            raise ValueError("Yol boş.")
        project = self.find(payload.get("project"))
        return read_source(Path(project.repo), path, int(payload.get("line") or 1))

    # --- iç ---------------------------------------------------------------

    def _answer(self, project: Project, question: str):
        from .answer import CodebaseAnswerer

        searcher, repo_root, mode = self.searcher_for(project)
        # Aynı anda tek soru koşuyor: iki paralel cevap hem bütçeyi hem
        # sunumu karıştırır.
        with self._answer_lock:
            answerer = CodebaseAnswerer(
                searcher,
                repo_root=repo_root,
                model=self.model,
                context_size=self.context_size,
                mode=mode,
            )
            return answerer.ask(question)


#: Soru seti rehberi. Arayüz duman testinin ölçüm olmadığını söylerken
#: "peki gerçek ölçüm nasıl yapılır" sorusunu havada bırakmasın diye sunuluyor.
GUIDE_PATH = Path(__file__).resolve().parent.parent / "eval" / "SORU_SETI.md"


def guide() -> str:
    try:
        return GUIDE_PATH.read_text(encoding="utf-8")
    except OSError:
        # Kurulu pakette `eval/` yok; rehber depoda duruyor.
        return (
            "# Soru seti rehberi\n\n"
            "Rehber depo içinde: `eval/SORU_SETI.md`. Kurulu pakette bulunmuyor.\n"
        )


#: Sayfa ayrı dosyada duruyor: 300 satırlık HTML/CSS/JS bir Python string'inin
#: içinde ne düzenlenebiliyor ne de linter tarafından okunabiliyor.
PAGE_PATH = Path(__file__).with_name("ui.html")


def page() -> str:
    return PAGE_PATH.read_text(encoding="utf-8")

