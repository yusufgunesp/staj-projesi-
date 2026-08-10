# Kod Tabanı Soru-Cevap Asistanı

Bir kod tabanını indeksleyip doğal dildeki sorulara **dosya ve satır referanslı** cevap veren araç.
Amaç, yeni bir projeye adapte olma süresini kısaltmak: her cevap kaynak gösterdiği için doğruluğu
anında kontrol edilebiliyor.

> Hedeflenen kullanım:
> **S:** "Bu projede sipariş akışı nasıl işliyor?"
> **C:** `api/orders.py:88`'de başlıyor, `services/payment.py:145`'te ödeme sağlayıcısına gidiyor,
> `models/order.py:67`'de kaydediliyor.

## Durum

| Hafta | Hedef | Durum |
|-------|-------|-------|
| 1 | Kod sembolü çıkarma, doküman parçalama | **Tamam** |
| 2 | Embedding + arama katmanı, çalışan temel sürüm | Sırada |
| 3 | Hibrit arama, reranking, ölçüm ve prompt iyileştirme | — |
| 4 | Claude Code / MCP entegrasyonu, dokümantasyon, demo | — |

## Kurulum

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

Anahtarlar (Hafta 2'den itibaren gerekli):

```bash
cp .env.example .env
```

## Kullanım

Bir repoyu indeksle:

```bash
.venv/bin/python -m codeqa index /yol/repo -o data/chunks.jsonl
```

İndeksin içine bak (embedding araması gelene kadar geçici anahtar kelime araması):

```bash
.venv/bin/python -m codeqa grep "sipariş" -i data/chunks.jsonl
```

İstatistikler:

```bash
.venv/bin/python -m codeqa stats -i data/chunks.jsonl
```

Testler:

```bash
.venv/bin/python -m pytest tests/ -q
```

## Nasıl çalışıyor

### Parçalama (chunking)

Kod, sabit uzunlukta metin blokları yerine **AST üzerinden** bölünüyor. Her fonksiyon, metot ve
sınıf ayrı bir parça oluyor. Bunun iki sebebi var: parçalar anlamlı bir bütün olarak kalıyor ve
her parçanın gerçek satır aralığı biliniyor — `dosya:satır` referansı verebilmenin ön şartı bu.

Çıkarılan parça türleri:

| Tür | İçerik |
|-----|--------|
| `module` | Modül docstring'i + import'lar — dosyanın ne yaptığı ve neye bağlı olduğu |
| `class` | Sınıf başlığı + docstring + metot imzaları (gövdeler tekrar edilmiyor) |
| `function` | Modül seviyesi fonksiyonun tamamı, dekoratörleriyle |
| `method` | Sınıf içi metodun tamamı, dekoratörleriyle |
| `section` | Markdown dosyalarının başlık bazlı bölümleri |

`class` ve `module` parçalarının metni özet olarak üretiliyor (kaynağın birebir kopyası değil),
diğerleri kaynaktan doğrudan alınıyor.

### Bağlam etiketi (contextual retrieval)

Her parçanın önüne kısa bir konum etiketi ekleniyor (`context` alanı) ve embedding'e parça bu
etiketle birlikte gidiyor (`Chunk.embed_text`). Kullanıcıya sadece kodun kendisi gösteriliyor.
Etiket şimdilik deterministik — dosya yolu, nitelenmiş ad, tür ve modül açıklaması. Parçanın ne
işe yaradığını LLM'e bir cümleyle yazdırmak Hafta 3'te denenecek.

### Çıktı formatı

`data/chunks.jsonl` — satır başına bir parça:

```json
{
  "id": "api/orders.py::OrderService.create#42",
  "kind": "method",
  "path": "api/orders.py",
  "name": "OrderService.create",
  "start_line": 42,
  "end_line": 67,
  "location": "api/orders.py:42",
  "signature": "async def create(self, payload: dict) -> int:",
  "docstring": "...",
  "context": "api/orders.py > OrderService.create (method)",
  "text": "...",
  "content_hash": "9f2c..."
}
```

`content_hash`, değişmemiş parçaların yeniden embed edilmemesi için — API maliyetini düşük tutmanın
ilk adımı.

## Doğrulama

Sembol çıkarıcı, `anthropic` SDK'sı üzerinde denendi: **1097 dosya, 4310 parça, 0 hata**.
Rastgele seçilen 500 parçanın satır aralıkları kaynak dosyalarla karşılaştırıldı, hepsi tuttu.
Ayrıca 21 birim testi var (`tests/`).

## Dosya yapısı

```
codeqa/
  models.py    Chunk veri modeli
  indexer.py   Python AST sembol çıkarıcı
  docs.py      Markdown parçalayıcı
  cli.py       komut satırı arayüzü
tests/         birim testleri
data/          indeks çıktıları (git'e girmez)
```

## Sıradaki adımlar

1. **Embedding katmanı** — Voyage AI ile parça vektörleri, diske cache'lenerek
2. **Arama** — kosinüs benzerliği + BM25, hibrit birleştirme
3. **Cevaplama** — Claude'a `read_file` ve `search_symbol` tool'ları verilerek indekste eksik
   kalan yerlerin canlı okunması
4. **Ölçüm** — test repo'su üzerinde 20 soruluk set, temel sürümden final sürüme doğruluk artışı

### Açık konular

- Ollama ile local model kullanımının gerekçesi: müşteri kodu gizliliği bir zorunluluk mu, yoksa
  karşılaştırma amaçlı bir opsiyon mu?
- Güncel model isimleri ve fiyatlandırma resmi dokümantasyondan doğrulanacak
- Test için kullanılacak örnek repo seçilecek
