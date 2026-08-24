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
| 2 | Embedding + arama katmanı, çalışan temel sürüm | **Tamam** |
| 3 | Cevaplama katmanı + ölçüm | **Tamam** — reranking ve prompt iyileştirme sırada |
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

Soru sor (`ANTHROPIC_API_KEY` gerekir):

```bash
.venv/bin/python -m codeqa ask "sipariş akışı nasıl işliyor" --repo /yol/repo
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
işe yaradığını LLM'e bir cümleyle yazdırmak, ölçüm kurulduktan sonra denenecek iyileştirmelerden.

### Arama

İki yöntem var ve tasarım varsayımı ikisinin birbirini tamamladığıydı. **Ölçüm bunu kısmen
çürüttü** — hangisinin kazandığı repoya bağlı, aşağıdaki Ölçüm bölümüne bakın. Yöntemler:

- **BM25** tam isim eşleşmesinde iyi. "`OrderService.create` nerede" sorusunda embedding araması
  sembolü kaçırabiliyor, BM25 doğrudan buluyor. Kod tokenları ayrıştırılıyor
  (`getUserOrders` → get, user, orders), yoksa "sipariş id" araması `order_id`'yi bulamıyor.
- **Vektör araması** dolaylı anlatımlarda iyi. Kodda "ödeme" kelimesi geçmese bile ilgili
  fonksiyonu getirebiliyor.

Birleştirme **RRF** (Reciprocal Rank Fusion) ile: skorlar değil sıralamalar toplanıyor, böylece
iki yöntemin farklı ölçekleri normalize edilmek zorunda kalmıyor. İki yöntemin de üst sıralarda
gösterdiği parça öne çıkıyor; sonuçlarda hangi yöntemin getirdiği `(bm25+vector)` olarak
gösteriliyor. Ağırlıklar `--bm25-weight` / `--vector-weight` ile ayarlanabiliyor — RRF'nin
zayıf bir sıralayıcıyı da hesaba katması, hibritin bazı repolarda saf vektörün gerisinde
kalmasının sebebi.

### Embedding sağlayıcısı takılabilir

Model iki ayrı yerde kullanılıyor ve bunların yeri değiştirilebilir olması önemli:

| Katman | Ne gönderiliyor | Sağlayıcı |
|--------|-----------------|-----------|
| Embedding (indeksleme) | Kod tabanının **tamamı** | `Embedder` arayüzü — Voyage / Ollama / hash |
| Cevaplama (LLM) | Sadece bulunan parçalar | `codeqa/answer.py` — Claude |

Gizlilik açısından asıl hacim embedding tarafında: indeksleme sırasında kodun her satırı dışarı
çıkıyor, cevaplama sırasında sadece ilgili parçalar. Müşteri kodunun dışarı çıkamadığı bir projede
`--provider ollama` ile tüm indeksleme local kalıyor.

Vektörler içerik karmasına göre `data/embeddings/` altında önbelleğe alınıyor; değişmemiş parça
yeniden embed edilmiyor. Karma `embed_text` (bağlam etiketi + metin) üzerinden alınıyor — sadece
metin üzerinden alınsaydı bir dosya taşındığında önbellekten eski bağlamla üretilmiş vektör
dönerdi. Sorgu vektörleri de aynı dosyada önbelleğe alınıyor (`CachedEmbedder`).

#### Hız limiti

Voyage'da ödeme yöntemi eklenmemiş bir hesabın limiti **3 istek/dakika, 10.000 token/dakika**.
Ödeme yöntemi eklense bile büyük bir müşteri reposunda limite girilir, o yüzden bu sağlayıcının
kendi içinde çözüldü:

- Yığın boyutu 32 parça — dakikalık token limitini aşmamak için.
- Hız limiti hatasında artan sürelerle bekleyip yeniden deniyor.
- Önbellek her yığından sonra diske yazılıyor. Dakikalarca süren bir koşu ortada patlarsa o ana
  kadarki iş kaybolmuyor, tekrar çalıştırıldığında kalınan yerden devam ediyor.

### Cevaplama

Soru önce hibrit aramaya gidiyor, bulunan parçalar bağlam olarak modele veriliyor. Model eksik
kalan yerleri iki tool'la kendisi okuyor:

| Tool | Ne yapıyor |
|------|------------|
| `read_file` | Repodaki bir dosyayı satır numaralarıyla okur |
| `search_symbol` | İndekste ikinci bir tur arama yapar |

Tool'lar gerekli çünkü indeks her zaman yetmiyor: bir çağrının gittiği yer indekste ayrı bir
parça olarak duruyor ve arama onu getirmemiş olabiliyor.

**`read_file` repo köküne hapsedilmiş.** Yol modelden geliyor, yani güvenilmez girdi; `../..`
ile repo dışına çıkma denemesi reddediliyor ve testlerle sabitlenmiş durumda.

**Referanslar doğrulanıyor.** Model var olmayan bir `dosya:satır` uydurabilir. Cevaptaki her
referans dosyanın gerçekten var olduğuna ve satırın dosya sınırları içinde kaldığına göre
kontrol ediliyor; tutmayanlar `⚠ doğrulanamayan referans` olarak gösteriliyor. Kısaltılmış yollar
(`models.py` yerine `codeqa/models.py`) indekste tek eşleşme varsa kabul ediliyor — bu bir uydurma
değil, eksik önek. Doğrulama, ölçüm katmanının da temel girdisi olacak.

**Prompt caching açık.** Model her tool turunda tüm geçmişi yeniden gönderiyor; bağlam parçaları
ve sistem promptu turlar boyunca aynı kalıyor. Önbellek olmadan aynı token'lar tur sayısı kadar
tekrar faturalanıyor. Ölçülen fark: aynı tipteki bir soruda 42.000 taze girdi token'ı yerine
8 taze + 18.500 önbellekten.

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

### Ölçüm

20 soruluk set `eval/questions.json`'da; her sorunun cevabının hangi dosyada olduğu elle
işaretlenmiş. İki şey ayrı ölçülüyor, çünkü ayrı ayrı bozulabiliyorlar:

- **Getirme** — arama doğru parçayı ilk k sonuca soktu mu? API çağrısı yok, saniyeler sürüyor.
- **Cevap** — model doğru dosyaya referans verdi mi? Claude çağırıyor, yavaş ve paralı.

Getirme bozuksa cevap da bozulur, ama tersi doğru değil: arama doğru parçayı getirip model yine
de yanlış cevap verebilir. Ayrı ölçülmezse hangisini düzelteceğin belli olmuyor.

```bash
.venv/bin/python -m codeqa eval                    # sadece getirme (bedava)
.venv/bin/python -m codeqa eval --answers          # cevapları da ölç (ücretli)
```

Koşular `runs/` altına JSON olarak yazılıyor, aynı ölçüm için tekrar API çağrısı yapılmıyor.

#### Ölçüm sonuçları (12.08.2026, 20 soru, 249 parça)

| Embedder | Nerede | Mod | recall@8 | sembol recall | MRR |
|----------|--------|-----|----------|---------------|-----|
| `hash` (yer tutucu) | — | BM25 | 95% | 70% | 0.560 |
| `hash` | — | Vektör | 75% | 50% | 0.375 |
| `hash` | — | Hibrit | 85% | 60% | 0.403 |
| **`voyage-code-3`** | bulut | BM25 | 95% | 70% | 0.560 |
| **`voyage-code-3`** | bulut | Vektör | **100%** | **100%** | 0.650 |
| **`voyage-code-3`** | bulut | **Hibrit** | **100%** | 90% | **0.702** |
| **`voyage-code-3` + `rerank-2.5`** | bulut | **Hibrit** | **100%** | **100%** | **0.750** |
| `nomic-embed-text` | local | BM25 | 95% | 70% | 0.560 |
| `nomic-embed-text` | local | Vektör | 50% | 60% | 0.243 |
| `nomic-embed-text` | local | Hibrit | 65% | 60% | 0.302 |

Baseline'dan (hash + hibrit) final sürüme (voyage + hibrit + reranking):
**recall %85 → %100, sembol recall %60 → %100, MRR 0.403 → 0.750.**

Okunacak üç şey var:

1. **Hibrit aramanın değeri embedding kalitesine bağlı, bedava gelen bir kazanç değil.** Yer tutucu
   embedder ile hibrit, BM25'ten *daha kötüydü* (0.403'e karşı 0.560) — çünkü RRF sıralamaları
   harmanlıyor ve zayıf bir sıralayıcı iyisini aşağı çekiyor. Gerçek embedder ile tablo tersine
   döndü. Ölçüm olmasaydı bu görülmezdi.
2. **BM25 satırları iki embedder'da birebir aynı** (95% / 0.560). Beklenen sonuç, çünkü BM25
   embedding kullanmıyor — ölçüm düzeneğinin doğru şeyi ölçtüğünün iç tutarlılık kontrolü.
3. **Hibrit sıralamada en iyi (MRR 0.702), ama sembol recall'da vektörün gerisinde** (%90'a karşı
   %100). Yani BM25'i harmanlamak genel sıralamayı iyileştirirken bir soruda doğru sembolü
   ilk k'nın dışına itiyor. Reranking'in bakacağı yer burası.

#### Reranking

`rerank-2.5` hibrit aramanın ilk 20 adayını alıp soruya göre yeniden sıralıyor. Ölçülen etki:

| Metrik | Reranking'siz | Reranking'li |
|--------|---------------|--------------|
| recall@8 | 100% | 100% |
| sembol recall | 90% | **100%** |
| MRR | 0.702 | **0.750** |

Soru bazında: **6 soru iyileşti, 3 soru kötüleşti, 11 soru değişmedi.** En büyük kazanç q02'de
(8. sıradan 2. sıraya). Kötüleşenler 1. sıradan 2-3. sıraya düşenler — yani hâlâ ilk 3'te,
kaybedilmiş değil.

Beklendiği gibi **recall değişmedi**: reranker sadece elde kalan adaylara bakıyor, arama bir
parçayı hiç getirmediyse onu kurtaramıyor. Düzelttiği şey sıralama.

**Bedava değil:** her sorgu için ek bir API çağrısı demek. Ücretsiz kotadaki hız limitiyle
(3 istek/dakika) 20 soruluk ölçüm koşusu ~7 dakika sürdü. Etkileşimli kullanımda sorgu başına
bir tur ek gecikme ekliyor. Varsayılan olarak kapalı; `--rerank voyage` ile açılıyor.

#### Local model (Ollama) — müdürün sorusunun cevabı

`nomic-embed-text` ile local kurulum çalışıyor ama kalite belirgin şekilde düşük: hibritte
recall %100 → %65, MRR 0.702 → 0.302. İki not:

- **Görev öneki şart.** Nomic ailesi metnin başına `search_document: ` / `search_query: `
  bekliyor. Öneksiz recall %40'tı, önekle %50'ye çıktı — yani önek gerçek ama tek başına
  açığı kapatmıyor. Kod `TASK_PREFIXES` ile bunu modele göre otomatik ekliyor.
- **Fark ağırlıkla model kapasitesinde, dilde değil.** Türkçe/İngilizce aynı soruyu sorup
  karşılaştırdım: 3 denemenin 1'inde İngilizce belirgin daha iyi, 2'sinde fark yok. Asıl sebep
  `voyage-code-3`'ün kod için özel eğitilmiş olması, `nomic-embed-text`'in genel amaçlı bir metin
  modeli olması.

Hız tarafı tersine dönüyor: local'de 249 parça **7,5 saniyede** embed edildi, Voyage'da hız limiti
yüzünden dakikalar sürdü.

**Karar için:** kaynak kodun dışarı çıkamadığı bir müşteride local kurulum çalışır ama arama
kalitesi düşer — bu durumda BM25 ağırlıklı bir yapılandırma (local vektörden daha iyi: %95'e
karşı %50) daha mantıklı. Kodun dışarı çıkabildiği yerlerde `voyage-code-3` açık ara önde.
Denenmemiş orta yol: `bge-m3` gibi daha güçlü bir local model.

#### Büyük repo ölçümü — `anthropic` Python SDK

Küçük set (kendi repomuz, 249 parça, 20 soru) yanıltıcıydı. Gerçek boyutta bir üçüncü taraf
repoda ölçüm: **1097 dosya, 4310 parça, 60 soru** (40 dev / 20 test). Ayarlar yalnızca dev'de
denendi, aşağıdaki sayılar **hiç dokunulmamış test bölmesinden**.

| Mod | test recall@8 | sembol recall | test MRR |
|-----|---------------|---------------|----------|
| **Vektör** | **85%** | **100%** | **0.742** |
| Hibrit | 80% | 100% | 0.581 |
| BM25 | 15% | 95% | 0.025 |

**Hibrit arama bu repoda işe yaramıyor — saf vektör araması daha iyi.** Projenin baştaki
tasarım tercihlerinden biri, gerçek boyutta bir repoda doğrulanmadı.

Sebebi BM25'in çökmesi (recall %15). Küçük repoda BM25 %95'ti; aradaki fark **dil**: kendi
kodumuzun yorumları Türkçe, `anthropic` SDK'sı tamamen İngilizce. Türkçe sorgu kelimeleri
İngilizce kodda sözcük olarak hiç geçmiyor, dolayısıyla BM25'in tutunacağı bir şey kalmıyor.
RRF zayıf sıralayıcıyı da hesaba kattığı için hibrit, saf vektörü aşağı çekiyor.

Denenen çözüm: sorguyu Haiku ile İngilizceye çevirip BM25'e vermek. BM25'i belirgin şekilde
kurtardı (recall %15 → %57, MRR 0.078 → 0.290) ama hibrit yine saf vektörü geçemedi
(0.522'ye karşı 0.665, dev bölmesinde). Yani çeviri doğru teşhis ama yeterli tedavi değil.

**Buradan çıkan asıl sonuç:** doğru arama modu repoya göre değişiyor. Türkçe yorumlu küçük
repoda hibrit kazandı, İngilizce büyük repoda saf vektör. Araç bunu varsayım olarak sabitlemek
yerine **her müşteri kod tabanında ölçüp seçmeli** — ölçüm katmanı bu yüzden aracın kendisinin
bir parçası, yan ürünü değil.

Bir yan bulgu: tokenizer Türkçe harfleri ayraç sayıyordu (`aşımı` → `a` + `m`). Düzeltildi;
Türkçe yorumlu kod tabanlarında ve Türkçe sorgularda BM25'i doğrudan etkiliyordu.

#### Contextual retrieval — ölçüldü, beklenenden az işe yaradı

Her parça için Haiku'ya "bu parça dosyanın bütününde ne işe yarıyor" diye Türkçe tek cümle
yazdırıldı ve embedding'e katıldı (4310 parçanın %96'sı; kalanı kredi bitmesi yüzünden eksik).

60 sorunun tamamında:

| Yöntem | Bağlamsız | Bağlamlı | Fark |
|--------|-----------|----------|------|
| Vektör recall@8 | 85% | 87% | +2 puan |
| Vektör MRR | 0.690 | 0.714 | +0.024 |
| BM25 recall@8 | 15% | **47%** | **+32 puan** |
| BM25 MRR | 0.060 | **0.231** | **~4 kat** |

**BM25 için büyük kazanç, vektör için kayda değmez.** Türkçe cümleler indekse Türkçe metin
koyduğu için BM25'e tutunacak yer verdi — tahmin edilen etkiydi ve gerçekleşti. Ama asıl
kullanılan yol vektör araması ve orada fark ölçüm gürültüsü mertebesinde.

Maliyet tarafı: yaklaşık 5-7 dolar ve saatlerce koşu, +0.024 MRR için. **Bu repoda contextual
retrieval masrafını çıkarmıyor.**

Neden Anthropic'in yayınladığı büyük kazançlar burada çıkmadı? Muhtemelen **baseline zaten zayıf
olmadığı için**: parçalar AST ile bölündüğünden anlamsal bütünlüğünü koruyor ve her parça
hâlihazırda deterministik bir bağlam etiketi taşıyor (dosya yolu, nitelenmiş ad, tür, modül
açıklaması). Contextual retrieval'ın asıl değeri sabit uzunlukta bölünmüş, bağlamsız parçalarda
ortaya çıkıyor. Teknik yanlış değil — bu boru hattı için gereksiz.

Bölmeler arası salınıma dikkat: test bölmesinde vektör recall %85 → %95 görünüyor, dev bölmesinde
%85 → %82. 20 ve 40 soruluk bölmelerde bu normal; **60 sorunun tamamındaki +2 puan gerçek etkiye
daha yakın.** Tek bir bölmedeki sıçramayı sonuç diye raporlamak, tam da kaçınmaya çalıştığımız
hata olurdu.

**Küçük setin çekincesi:** 20 soruda bir soru %5 demek. Yukarıdaki büyük repo ölçümü bu
setin ne kadar iyimser olduğunu somut olarak gösterdi — %100 recall, gerçek boyutta %85'e indi.

Cevap tarafı 5 soruluk örneklemde: doğruluk %100, dosya kapsamı %100, uydurma referans 0.
Tam 20 soruluk cevap ölçümü henüz koşulmadı.

## Doğrulama

Sembol çıkarıcı, `anthropic` SDK'sı üzerinde denendi: **1097 dosya, 4310 parça, 0 hata**.
Rastgele seçilen 500 parçanın satır aralıkları kaynak dosyalarla karşılaştırıldı, hepsi tuttu.
Aynı repo uçtan uca indekslenip aranabiliyor. **128 birim testi** var (`tests/`); testler ağ
erişimi ve API anahtarı olmadan çalışıyor — cevaplama katmanında tool gövdeleri, bağlam kurma
ve referans doğrulama test ediliyor, tool döngüsünü SDK yürütüyor.

## Dosya yapısı

```
codeqa/
  models.py      Chunk veri modeli
  indexer.py     Python AST sembol çıkarıcı
  docs.py        Markdown parçalayıcı
  embeddings.py  sağlayıcı arayüzü (Voyage / Ollama / hash) + disk önbelleği
  search.py      BM25, vektör araması, RRF birleştirme
  answer.py      Claude + tool'lar (read_file, search_symbol), referans doğrulama
  rerank.py      Voyage rerank-2.5 ile yeniden sıralama
  contextual.py  LLM ile bağlam cümlesi üretimi (contextual retrieval)
  evaluation.py  soru seti, getirme ve cevap metrikleri
  cli.py         komut satırı arayüzü
eval/            soru setleri (küçük + büyük)
tests/           birim testleri
data/            indeks ve vektör çıktıları (git'e girmez)
runs/            ölçüm koşuları (git'e girmez)
```

## Sıradaki adımlar

1. **Daha güçlü bir local model dene** — `bge-m3`. `nomic-embed-text` ile açık büyük çıktı;
   local seçeneğin gerçekten kullanılabilir olup olmadığı buna bağlı.
2. **Tam 20 soruluk cevap ölçümü** — şimdilik sadece 5 soruluk örneklem koşuldu.
3. **Soru setini zorlaştır** — recall %100'e çarptı, bu set artık bulut tarafında iyileşme
   ölçemiyor. Daha büyük bir repo ya da daha zor sorular gerekiyor.
4. **Entegrasyon** — Claude Code / MCP.

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
