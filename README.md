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
| 2 | Embedding + arama katmanı, çalışan temel sürüm | **Tamam** (cevaplama katmanı hariç) |
| 3 | Reranking, ölçüm ve prompt iyileştirme | Sırada |
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

Parçaları vektörleştir:

```bash
.venv/bin/python -m codeqa embed --provider hash
```

Ara:

```bash
.venv/bin/python -m codeqa search "ödeme akışı nerede başlıyor"
```

Sağlayıcılar: `voyage` (bulut, `VOYAGE_API_KEY` gerekir), `ollama` (local, kod dışarı çıkmaz),
`hash` (anahtarsız — anlamsal arama **yapmaz**, sadece kelime örtüşmesine bakar; boru hattını
anahtar olmadan denemek için). Arama modları: `--mode hybrid` (varsayılan), `vector`, `bm25`.

İstatistikler ve ham metin araması:

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

### Arama

İki yöntem farklı soruları çözüyor, o yüzden ikisi birlikte kullanılıyor:

- **BM25** tam isim eşleşmesinde iyi. "`OrderService.create` nerede" sorusunda embedding araması
  sembolü kaçırabiliyor, BM25 doğrudan buluyor. Kod tokenları ayrıştırılıyor
  (`getUserOrders` → get, user, orders), yoksa "sipariş id" araması `order_id`'yi bulamıyor.
- **Vektör araması** dolaylı anlatımlarda iyi. Kodda "ödeme" kelimesi geçmese bile ilgili
  fonksiyonu getirebiliyor.

Birleştirme **RRF** (Reciprocal Rank Fusion) ile: skorlar değil sıralamalar toplanıyor, böylece
iki yöntemin farklı ölçekleri normalize edilmek zorunda kalmıyor. İki yöntemin de üst sıralarda
gösterdiği parça öne çıkıyor; sonuçlarda hangi yöntemin getirdiği `(bm25+vector)` olarak
gösteriliyor.

### Embedding sağlayıcısı takılabilir

Model iki ayrı yerde kullanılıyor ve bunların yeri değiştirilebilir olması önemli:

| Katman | Ne gönderiliyor | Sağlayıcı |
|--------|-----------------|-----------|
| Embedding (indeksleme) | Kod tabanının **tamamı** | `Embedder` arayüzü — Voyage / Ollama / hash |
| Cevaplama (LLM) | Sadece bulunan parçalar | Henüz yazılmadı (Hafta 3) |

Gizlilik açısından asıl hacim embedding tarafında: indeksleme sırasında kodun her satırı dışarı
çıkıyor, cevaplama sırasında sadece ilgili parçalar. Müşteri kodunun dışarı çıkamadığı bir projede
`--provider ollama` ile tüm indeksleme local kalıyor.

Vektörler içerik karmasına göre `data/embeddings/` altında önbelleğe alınıyor. Değişmemiş parça
yeniden embed edilmiyor; aynı içeriğe sahip parçalar (tekrar eden `__init__.py` kalıpları gibi)
tek kez hesaplanıyor.

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
Aynı repo uçtan uca indekslenip aranabiliyor. **43 birim testi** var (`tests/`); testler ağ
erişimi ve API anahtarı olmadan çalışıyor.

## Dosya yapısı

```
codeqa/
  models.py      Chunk veri modeli
  indexer.py     Python AST sembol çıkarıcı
  docs.py        Markdown parçalayıcı
  embeddings.py  sağlayıcı arayüzü (Voyage / Ollama / hash) + disk önbelleği
  search.py      BM25, vektör araması, RRF birleştirme
  cli.py         komut satırı arayüzü
tests/           birim testleri
data/            indeks ve vektör çıktıları (git'e girmez)
```

## Sıradaki adımlar

1. **Cevaplama katmanı** — Claude'a `read_file` ve `search_symbol` tool'ları verilerek indekste
   eksik kalan yerlerin canlı okunması, cevapların `dosya:satır` referanslı üretilmesi
2. **Reranking** — hibrit aramanın ilk N sonucunu yeniden sıralama
3. **Ölçüm** — test repo'su üzerinde 20 soruluk set, temel sürümden final sürüme doğruluk artışı
4. **Entegrasyon** — Claude Code / MCP

### Açık konular

- Ollama ile local model kullanımının gerekçesi: müşteri kodu gizliliği bir zorunluluk mu, yoksa
  karşılaştırma amaçlı bir opsiyon mu? (Mimari her iki cevaba da hazır, ama hangi sağlayıcının
  varsayılan olacağı ve local modelin ölçüleceği bu cevaba bağlı.)
- Test için kullanılacak örnek repo seçilecek

### Doğrulanan konular

- Embedding modeli: `voyage-code-3` — kod için önerilen model, 1024 boyut, 32k bağlam
  ([Voyage dokümantasyonu](https://docs.voyageai.com/docs/embeddings), 10.08.2026)
- Cevaplama katmanı için model isimleri: `claude-opus-5` (ana katman), `claude-haiku-4-5`
  (hacimli basit işler ve değerlendirme çağrıları)
