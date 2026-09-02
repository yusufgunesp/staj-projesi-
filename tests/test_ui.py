"""Web arayüzünün testleri.

Ağ ve API anahtarı yok: `hash` sağlayıcısı, geçici bir repo ve gerçek bir HTTP
sunucusu (`127.0.0.1`, işletim sisteminin verdiği port). Test edilen şey uç
nokta sözleşmesi — arayüzün istediği alanlar dönüyor mu — ve referans
doğrulamanın repo dışına çıkamadığı.

Cevaplama (`/api/ask`) burada koşmuyor: Claude çağırıyor, yani ücretli ve ağa
bağımlı. Anahtar yokken doğru hatayı döndürdüğü test ediliyor.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from codeqa.embeddings import EmbeddingCache
from codeqa.projects import (
    Project,
    composition,
    embed_project,
    language_signal,
    load_projects,
    save_projects,
    scan_repo,
    slugify,
    smoke_test,
)
from codeqa.ui import UIServer, estimate_cost, page, read_source

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
    source = tmp_path / "repo"
    source.mkdir()
    (source / "orders.py").write_text(SOURCE.strip() + "\n", encoding="utf-8")
    return source


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    """Önbelleği geçici dizine yönlendirir.

    `EmbeddingCache.__init__` varsayılanı tanımlanma anında bağladığı için
    `DEFAULT_CACHE_DIR`'i yamalamak yetmiyor; sınıfı içeri alan modüllerde
    değiştirmek gerekiyor.
    """
    directory = tmp_path / "emb"

    def factory(name, cache_dir=None):
        return EmbeddingCache(name, cache_dir=cache_dir or directory)

    for module in ("codeqa.mcp_server", "codeqa.projects"):
        monkeypatch.setattr(f"{module}.EmbeddingCache", factory)
    return directory


@pytest.fixture
def server(repo, tmp_path, cache_dir):
    """Bir projesi kayıtlı, çalışan bir arayüz sunucusu."""
    index_path = tmp_path / "repo.jsonl"
    scan_repo(repo, index_path, provider="hash")
    embed_project(index_path, provider="hash")

    registry = tmp_path / "projects.json"
    save_projects(
        [
            Project(
                id="repo",
                name="repo",
                repo=str(repo),
                index=str(index_path),
                provider="hash",
                mode="bm25",
                chunks=1,
                files=1,
            )
        ],
        registry,
    )

    # Port 0: işletim sistemi boş bir port versin, testler paralel koşabilsin.
    srv = UIServer(("127.0.0.1", 0), registry=registry, model="test-model", data_dir=tmp_path)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def get(server, path):
    url = f"http://127.0.0.1:{server.server_address[1]}{path}"
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.status, json.load(response)


def get_raw(server, path):
    """Durum kodu lazım olduğunda: `get` 404'te istisna atıyor."""
    url = f"http://127.0.0.1:{server.server_address[1]}{path}"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


def post(server, path, body):
    url = f"http://127.0.0.1:{server.server_address[1]}{path}"
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


# --- sayfa ----------------------------------------------------------------


def test_sayfa_paketle_birlikte_geliyor():
    """`ui.html` veri dosyası; pyproject'te belirtilmezse kurulumda kaybolur."""
    assert "<title>codeqa</title>" in page()


def test_kok_html_donuyor(server):
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    with urllib.request.urlopen(url, timeout=10) as response:
        assert response.headers["Content-Type"].startswith("text/html")
        assert b"codeqa" in response.read()


# --- durum ve arama --------------------------------------------------------


def test_durum_projeleri_listeliyor(server):
    status, body = get(server, "/api/status")
    assert status == 200
    assert [p["id"] for p in body["projects"]] == ["repo"]
    assert body["model"] == "test-model"


def test_arama_arayuzun_bekledigi_alanlari_donuyor(server):
    status, body = post(server, "/api/search", {"query": "charge payment", "k": 5})
    assert status == 200
    assert body["hits"]
    hit = body["hits"][0]
    # Arayüz bu üçü olmadan referansı açamıyor.
    for field in ("location", "path", "line", "kind", "name", "score", "sources"):
        assert field in hit


def test_netlestirme_arayuzun_bekledigi_alanlari_donuyor(server):
    status, body = post(server, "/api/facets", {"query": "payment"})
    assert status == 200
    for facet in body["facets"]:
        # Öneri metni yanlış olabilir; dizin ve parça sayısı kullanıcının
        # kontrol yüzeyi, o yüzden her zaman gitmeli.
        for field in ("directory", "chunks", "label", "query"):
            assert field in facet
        # Özgün sorgu korunuyor: öbek onu değiştirmiyor, üstüne ekliyor.
        assert facet["query"].startswith("payment")


def test_netlestirmede_bos_sorgu_reddediliyor(server):
    status, body = post(server, "/api/facets", {"query": "   "})
    assert status == 400
    assert "boş" in body["error"].lower()


def test_bos_sorgu_reddediliyor(server):
    status, body = post(server, "/api/search", {"query": "   "})
    assert status == 400
    assert "boş" in body["error"].lower()


# --- referans doğrulama: arayüzün asıl işi ---------------------------------


def test_kaynak_hedef_satiri_isaretliyor(server, repo):
    lines = (repo / "orders.py").read_text(encoding="utf-8").splitlines()
    target = next(i for i, line in enumerate(lines, 1) if line.startswith("def create_order"))

    status, body = post(server, "/api/source", {"path": "orders.py", "line": target})
    assert status == 200
    assert body["verified"] is True
    assert body["start"] <= target
    # Hedef satır dönen dilimin içinde olmalı, yoksa arayüz vurgulayamaz.
    assert body["lines"][target - body["start"]].startswith("def create_order")


def test_dosya_disi_satir_dogrulanmiyor(server):
    status, body = post(server, "/api/source", {"path": "orders.py", "line": 9999})
    assert status == 200
    assert body["verified"] is False
    assert "satır yok" in body["error"]


def test_olmayan_dosya_hata_donuyor(server):
    status, body = post(server, "/api/source", {"path": "yok.py", "line": 1})
    assert status == 200
    assert "bulunamadı" in body["error"]


@pytest.mark.parametrize(
    "path",
    ["../../../../etc/passwd", "/etc/passwd", "sub/../../../../etc/passwd"],
)
def test_repo_disina_cikilamiyor(server, path):
    """İstek tarayıcıdan geliyor; yol hapsi cevaplama katmanıyla ortak."""
    status, body = post(server, "/api/source", {"path": path, "line": 1})
    assert status == 400
    assert "Repo dışına çıkılamaz" in body["error"]


def test_read_source_dogrudan_da_hapsediyor(repo):
    with pytest.raises(ValueError, match="Repo dışına çıkılamaz"):
        read_source(repo, "../../etc/passwd", 1)


# --- cevaplama -------------------------------------------------------------


def test_anahtar_yokken_cevaplama_kapali_ama_arama_calisiyor(server, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    status, body = post(server, "/api/ask", {"question": "ödeme nerede"})
    assert status == 400
    assert "ANTHROPIC_API_KEY" in body["error"]

    status, _ = post(server, "/api/search", {"query": "ödeme"})
    assert status == 200


def test_bilinmeyen_uc_nokta_404(server):
    status, _ = post(server, "/api/yok", {})
    assert status == 404


# --- maliyet ---------------------------------------------------------------


def test_maliyet_onbellek_yazmayi_sayiyor():
    """`girdi 3` görünüp maliyetin sıfır sanılması bu alanın atlanmasındandı."""
    assert estimate_cost({"cache_creation_input_tokens": 1_000_000}) == pytest.approx(1.25)
    assert estimate_cost({"output_tokens": 1_000_000}) == pytest.approx(5.0)
    assert estimate_cost({}) == 0


# --- proje kaydı ------------------------------------------------------------


def test_slugify_dosya_adina_uygun_ad_uretiyor():
    assert slugify("My Repo!") == "my-repo"
    assert slugify("...") == "proje"


def test_bilesim_test_ve_dokumani_ayiriyor():
    """Test/doküman oranı ölçümde sonucu değiştirdiği için ayrı sayılıyor."""
    records = [
        {"path": "codeqa/search.py", "kind": "function"},
        {"path": "tests/test_search.py", "kind": "function"},
        {"path": "README.md", "kind": "section"},
        {"path": "src/test_helpers.py", "kind": "function"},
    ]
    counts = composition(records)["counts"]
    assert counts == {"kod": 1, "test": 2, "doküman": 1}


def test_tarama_para_harcamadan_onizleme_veriyor(server, repo, tmp_path):
    """Tara adımı indeksliyor ama vektörleştirmiyor; onay ekranının verisi bu."""
    status, body = post(server, "/api/projects/scan", {"repo": str(repo), "provider": "hash"})
    assert status == 200
    assert body["chunks"] > 0
    assert body["files"] == 1
    assert "estimate" in body and "composition" in body
    # Bu repo zaten embed edilmişti: aynı içerik, aynı karma, ödeme yok.
    assert body["estimate"]["missing"] == 0
    assert Path(body["index"]).exists()


def test_olmayan_dizin_taranamiyor(server):
    status, body = post(server, "/api/projects/scan", {"repo": "/yok/boyle/bir/yer"})
    assert status == 400
    assert "bulunamadı" in body["error"]


def test_bos_yol_reddediliyor(server):
    status, _ = post(server, "/api/projects/scan", {"repo": "  "})
    assert status == 400


def test_proje_eklenip_listeye_giriyor(server, repo, tmp_path):
    scan_status, scan = post(server, "/api/projects/scan", {"repo": str(repo), "provider": "hash"})
    assert scan_status == 200
    scan["name"] = "ikinci"
    scan["id"] = "ikinci"

    status, body = post(server, "/api/projects/add", scan)
    assert status == 200
    job_id = body["job"]

    for _ in range(60):
        _, job = get(server, f"/api/jobs/{job_id}")
        if job["state"] != "çalışıyor":
            break
        time.sleep(0.2)
    assert job["state"] == "bitti", job.get("error")

    _, status_body = get(server, "/api/status")
    assert "ikinci" in [p["id"] for p in status_body["projects"]]
    # Kayıt diske de yazılmış olmalı: sunucu yeniden açılınca kaybolmasın.
    assert "ikinci" in [p.id for p in load_projects(server.registry)]


def test_bilinmeyen_is_404(server):
    status, _ = get_raw(server, "/api/jobs/yokboyle")
    assert status == 404


def test_bilinmeyen_proje_reddediliyor(server):
    status, body = post(server, "/api/search", {"project": "yok", "query": "ödeme"})
    assert status == 400
    assert "bulunamadı" in body["error"]


def test_proje_kaldirilinca_indeks_diskte_kaliyor(server):
    index_path = Path(server.projects[0].index)
    status, body = post(server, "/api/projects/remove", {"project": "repo"})
    assert status == 200
    assert body["projects"] == []
    # Silmek geri alınamaz; kayıttan çıkarmak indeksi silmemeli.
    assert index_path.exists()


# --- ucuz kontroller: ölçüm değil, işaret -----------------------------------


def test_dil_isareti_turkce_ve_ingilizce_repoyu_ayiriyor():
    """Ölçülen mekanizma: BM25 ancak sorunun kelimeleri kodda geçince tutunuyor."""
    turkish = [{"text": "# ödeme akışı burada başlıyor"} for _ in range(8)]
    english = [{"text": "# payment flow starts here"} for _ in range(8)]

    assert language_signal(turkish)["suggested_mode"] == "hybrid"
    assert language_signal(english)["suggested_mode"] == "vector"
    assert language_signal(english)["percent"] == 0
    # Hiçbiri ölçüm değil; arayüz bunu böyle etiketliyor.
    assert language_signal(turkish)["measured"] is False


def test_dil_isareti_bos_indekste_patlamiyor():
    assert language_signal([])["percent"] == 0


def test_duman_testi_kendi_sembollerini_buluyor(server):
    status, body = post(server, "/api/projects/smoke", {"project": "repo"})
    assert status == 200
    smoke = body["smoke"]
    assert smoke["total"] > 0
    # Sembolün adıyla sorulunca bulunmalı — bulunmuyorsa indeks bozuktur.
    assert smoke["rate"] == 1.0
    assert smoke["measured"] is False


def test_duman_testi_sonucu_kayda_yaziliyor(server):
    post(server, "/api/projects/smoke", {"project": "repo"})
    saved = load_projects(server.registry)[0]
    assert saved.checks["smoke"]["total"] > 0


def test_rehber_sunuluyor(server):
    status, body = get(server, "/api/guide")
    assert status == 200
    assert "dev/test ayrımı zorunlu" in body["markdown"]


def test_duman_testi_tip_siniflarini_ornekleme_almiyor():
    """Metotsuz tip sınıfları arama katmanı tarafından bilerek geri itiliyor.

    Onları sorup "bulunamadı" saymak, ölçülmüş bir tasarım kararını arıza gibi
    gösterirdi — ilk koşuda anthropic SDK'sında sonuç bu yüzden 17/25 çıkmıştı.
    """

    class FakeSearcher:
        def __init__(self):
            self.asked = []
            self.records = [
                {
                    "kind": "class",
                    "name": "TipStub",
                    "location": "t.py:1",
                    "text": "class TipStub:\n    a: int",
                },
                {
                    "kind": "function",
                    "name": "gercek_is",
                    "location": "g.py:1",
                    "text": "def gercek_is():\n    pass",
                },
            ]

        def search(self, query, k=8):
            self.asked.append(query)
            return []

    searcher = FakeSearcher()
    result = smoke_test(searcher)
    assert searcher.asked == ["gercek_is"]
    assert result["total"] == 1


def test_cli_yolu_mevcut_kaydin_kontrollerini_silmiyor(tmp_path, repo, cache_dir, monkeypatch):
    """`codeqa ui -i ...` daha önce arayüzden eklenmiş projeyi ezmemeli.

    İlk hâlinde bayrakla açmak kaydı `chunks=0, files=0, checks={}` ile
    üzerine yazıyordu; arayüzde proje "0 parça" görünüyordu.
    """
    import argparse

    from codeqa import cli

    index_path = tmp_path / "repo.jsonl"
    scan_repo(repo, index_path, provider="hash")
    registry = tmp_path / "projects.json"
    save_projects(
        [
            Project(
                id="repo",
                name="repo",
                repo=str(repo),
                index=str(index_path),
                provider="hash",
                chunks=99,
                files=9,
                checks={"smoke": {"total": 5, "found": 5}},
            )
        ],
        registry,
    )

    started = {}

    class FakeServer:
        server_address = ("127.0.0.1", 0)

        def __init__(self, *a, **kw):
            started["kw"] = kw
            self.projects: list = []

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            pass

    monkeypatch.setattr("codeqa.ui.UIServer", FakeServer)
    args = argparse.Namespace(
        registry=str(registry), port=0, model_name="m", context=8, no_browser=True,
        index=str(index_path), repo=str(repo), name="repo", provider="hash", model=None, mode=None,
    )
    cli.cmd_ui(args)

    saved = {p.id: p for p in load_projects(registry)}["repo"]
    assert saved.chunks > 0 and saved.files > 0  # indeksten sayıldı
    assert saved.checks["smoke"]["found"] == 5  # önceki kontroller korundu
