# Kod Tabanı Soru-Cevap Asistanı — Devir Belgesi

Yusuf Güneş · staj projesi · 1 Eylül 2026 · *yeni bir sohbete bağlam vermek için*

Bu belge projenin şu anki durumunu ve nasıl buraya gelindiğini anlatıyor. Asıl amacı yeni bir
oturumun daha önce denenip elenmiş şeyleri tekrar denememesi ve **düşen sayıları "hata" sanıp
geri almaya çalışmaması** — düşüş kasıtlı, sebebi §3'te.

Önceki devir belgesi 27.08.2026 tarihliydi. Bu belge onun yerini alıyor; aradaki dokuz commit
`617ab51..HEAD` arasında.

---

## 1. Proje nedir

Bir kod tabanını indeksleyip doğal dildeki sorulara **dosya ve satır referanslı** cevap veren
araç. Amaç yeni bir projeye adapte olma süresini kısaltmak: her cevap kaynak gösterdiği için
doğruluğu anında kontrol edilebiliyor.

Kalıbın adı **RAG**. Modele kod tabanı ezberletilmiyor; soru sorulduğu anda ilgili parçalar
bulunup bağlam olarak veriliyor.

Sorular **Türkçe**, kod **İngilizce** — projenin birçok bulgusunun arkasındaki temel gerilim.

Kod sabit uzunlukta bloklar yerine sözdizimi ağacı üzerinden bölünüyor: her fonksiyon, metot ve
sınıf ayrı bir parça. İki sebebi var — parçalar anlamlı bir bütün kalıyor ve her parçanın gerçek
satır aralığı biliniyor, ki referans verebilmenin ön şartı bu.

**Yalnızca Python indeksliyor.** Sekiz dil desteği vardı, ölçülüp kaldırıldı (§7).

---

## 2. Şu anki durum

**Kod:** 4.534 satır `codeqa/` (15 modül), 2.758 satır test, **218 test** (ağ ve API anahtarı
gerektirmiyor), lint temiz, `pip install -e .` çalışıyor.

**İki kod tabanı ölçülüyor.** Bileşimleri bilerek eşitlendi (ikisi de %99+ üretim kodu):

| Kod tabanı | Ne | Dosya | Parça | İndeks |
|---|---|---|---|---|
| `anthropic` SDK | LLM istemci kütüphanesi, büyük kısmı üretilmiş kod | 1.099 | 4.311 | `data/anthropic.jsonl` |
| **Saleor** | Açık kaynak e-ticaret (Django), elle yazılmış iş uygulaması | 1.060 | 12.076 | `data/saleor.jsonl` |

Ayrıca aracın kendi kodu (`data/chunks.jsonl`, 571 parça) ve pydantic (`data/pydantic.jsonl`,
1.318 parça — ölçülmedi, sadece duruyor).

**Beş soru seti, 238 soru, 142'si test bölmesinde:**

| Set | Soru | Tipi |
|---|---|---|
| `questions_anthropic.json` | 80 (40 dev / 40 test) | Tek konum — "X nerede" |
| `questions_akis.json` | 40 (10 dev / 30 test) | Akış — cevap 2-4 dosyada |
| `questions_zor.json` | 38 (12 dev / 26 test) | Zor — benzer mekanizmaları ayırt ettiren |
| `questions_saleor.json` | 40 (14 dev / 26 test) | Saleor — üç tip karışık |
| `questions.json` | 40 (20 dev / 20 test) | Aracın kendi kodu |

**Getirme (varsayılan: voyage + vektör, k=8, test bölmeleri):**

| Set | n | recall@8 | kapsam | MRR |
|---|---|---|---|---|
| Kolay | 40 | 92% | 92% | 0.701 |
| Akış | 30 | 93% | 87% | 0.808 |
| **Zor** | 26 | **96%** | **90%** | **0.757** |
| **Saleor** | 26 | **100%** | **96%** | **0.573** |
| Kendi kodu | 20 | 100% | 100% | 0.794 |

**Rapor edilecek satır zor-test ve Saleor.** Kendi kodundaki %100/%100 rapor edilmemeli: 571
parçalık küçük bir indekste, aracın kendi kodu.

**Cevap tarafı ölçümü eskimiş durumda** — zor setin 14 soruluk hâlinde alınmış (%100 doğruluk,
%93 dosya kapsamı). Set 26'ya çıktı ama cevap koşusu ücretli olduğu için tekrarlanmadı. Bu açık
bir iş (§8).

---

## 3. Bu oturumda ne oldu — ÖNCE BUNU OKU

**Sayılar düştü ve bu kasıtlı.** Test bölmeleri 44 sorudan 142'ye çıkarıldı; araç değişmedi,
indeks aynı, ayarlar aynı, kodun tek satırı bile bu yüzden oynamadı. Değişen tek şey kaç soruyla
ölçüldüğü.

| Set | test | recall | kapsam | MRR |
|---|---|---|---|---|
| Kolay | 20 → 40 | %98 → %92 | %98 → %92 | 0.735 → 0.701 |
| Akış | 10 → 30 | %100 → %93 | %91 → %87 | 0.847 → 0.808 |
| Zor | 14 → 26 | %100 → %96 | %93 → %90 | 0.847 → 0.757 |

Güven aralıkları daraldı: zor sette 22 puandan 13 puana, akışta 28'den 11'e. **Eski sayılar küçük
örneklem iyimserliğiydi.** Yeni bir oturum bu düşüşü gerileme sanıp "düzeltmeye" çalışmamalı.

Yapılan diğer işler:

- **İkinci kod tabanı (Saleor)** eklendi — "bütün ölçümler tek repodan" sorunu kapandı.
- **Web arayüzü** yazıldı (`codeqa ui`): proje kaydı, tarama + maliyet önizlemesi, referansa
  tıklayınca kaynağı açma. Ek bağımlılık yok, `http.server` yeterli.
- **Tanıtım sayfası** yayınlandı (Claude artifact, aşağıda link). Aracın kendisi değil, ölçümleri
  ve yöntemi anlatan statik sayfa.
- **Referans satır numarası hatası** bulundu ve düzeltildi (§6).
- Demo provası yapıldı, altı uyumsuzluk düzeltildi.
- JSONL okuma/yazma altı yerden bire indirildi (`models.read_jsonl` / `write_jsonl`).

---

## 4. Yöntem kuralları — bunlara uyulmalı

Projenin ayırt edici özelliği araç değil ölçüm disiplini. Kurallar acı deneyimle kondu.

**Kural 1 — Ayar yapılan sette rapor verilmez.** Her sette dev/test ayrımı var. Dev'e istediğin
kadar bak, sayıyı test'ten al. Bu proje bir günde üç kez dev'de kazanıp test'te sıfır aldı; bu
oturumda dördüncüsü oldu (reranking, §5).

**Kural 2 — Metriğin müdahaleyi ödüllendirip ödüllendirmediğine bak.** Kapsam metriği eklenen
parça sayısıyla matematiksel olarak düşemez. Her kapsam kazancının yanında bağımsız bir kontrol
gerekiyor (MRR, sembol isabeti, cevap başına referans sayısı).

**Kural 3 — Yalnızca başarısızlıkların etiketi sorgulanmaz.** Etiket düzeltmek meşru ama tek yönlü
yapılırsa skoru şişirir. Bir seti gözden geçireceksen tamamını geçir. Düzeltmeyi **ölçüm
düzeltmesi** olarak işaretle, sistem kazancı olarak değil.

**Kural 4 — Kod tabanları karşılaştırılırken indeks bileşimi eşitlenmeli.** Bir repoyu depo
kökünden, diğerini kurulu paketten indekslemek adil değil. Bu hata bir kez yapıldı ve 26 puanlık
sahte bir fark üretti.

---

## 5. Denenip reddedilenler — TEKRAR DENENMEMELİ

Her biri makul göründü, uygulandı, ölçüldü ve veriyle elendi. Kod çoğunda duruyor, varsayılan
kapalı — silmek de ölçülmemiş bir iddia olurdu.

| Fikir | Ne oldu |
|---|---|
| Hibrit arama | Küçük repoda kazandı, büyük İngilizce repoda BM25 %95'ten %15'e düştü. Sebep dil. |
| Contextual retrieval | +2 puan, 5-7 dolar, saatlerce koşu. Masrafını çıkarmadı. |
| Sert kota (dosya başına 1 parça) | Kapsamı yükseltti ama sembol isabetini 0.778 → 0.444 düşürdü. |
| Prompt'a eksiksizlik kuralları | Zor sette +15 puan, görmediği akış setinde 0, üstelik %25 fazla referans. |
| Yeniden dışa aktarım kabuklarını geri itmek | Dev MRR 0.694 → 0.736, test'te 0. |
| İmport bağının ileri yönü | Dev kapsamı 0.944 → 1.000, test'te 0. |
| **Reranking** (bu oturumda) | Saleor dev 0.752 → 0.857, zor dev 0.694 → 0.808 — ama Saleor **test**'inde 0.573 → 0.480, recall %100 → %92. Akış dev'inde de zarar verdi. Tutarsız, reddedildi. |

**Bir istisna:** tip ağırlıklandırması ilk ölçümde reddedilmişti (MRR 0.761 → 0.754), genişletme
slotları eklendikten sonra yeniden ölçüldü ve kazandırdı (0.950 → 0.983). **Ders: ölçülüp
reddedilen bir fikir, koşullar değişince yeniden ölçülmeli.**

**Kıpırdamayan kollar** (bu oturumda üç dev bölmesinde denendi): çeşitlilik slotları (2/4/8),
dizin slotları (2/4), import slotları (2/4), havuz (200/300). Hepsi tam olarak düz. Hibrit mod ve
tip ağırlığını kapatmak zarar veriyor. **Mevcut varsayılanlar iki kod tabanında da en iyisi.**

---

## 6. Çalışan mekanizmalar

**Parçalama.** `module` / `class` / `function` / `method` / `section`. Sınıf parçaları alan
listesini ve öznitelik docstring'lerini taşıyor (PEP 258 — `ast` bunları atamaya bağlamadığı için
sessizce düşüyorlardı; 1.097 dosyanın 624'ünde 2.025 docstring, 222.843 karakter. İndekslenince
indeks metni %14 büyüdü, akış MRR 0.777 → 0.810).

**Embedding.** Voyage `voyage-code-3` (bulut, varsayılan), Ollama `bge-m3` (yerel), `hash`
(anahtarsız yer tutucu). Ölçüm: voyage %100/0.702, bge-m3 %100/0.654, nomic %65/0.302, hash
%85/0.403. Vektörler içerik karmasına göre `data/embeddings/` altında önbellekleniyor.

**Arama.** BM25 + vektör, RRF ile birleştirme. Mod sağlayıcıdan çözülüyor (`hash` → hybrid,
gerçek sağlayıcı → vector).

**Üç genişletme ekseni.** Hiçbiri eleme yapmıyor; ilk k dokunulmadan kalıyor, liste uzuyor
(8 → ~15 parça):

| Eksen | Ne zaman | Slot |
|---|---|---|
| Dosya çeşitliliği | İlk k'da aynı dosya tekrar ediyorsa | 4 |
| Dizin kardeşi | Bir dizin en az 2 parçayla temsil ediliyorsa | 2 |
| İmport bağı (geri yön) | Seçilen dosyayı kullanan dosyalar | 2 |

Üçüncüsünün ayrı gerekçesi var: *"kimler kullanıyor"* bir benzerlik sorusu değil, çağrı grafiği
sorusu. Bir ölçümde beklenen dosya 136. sıradaydı.

Üretilmiş tip tanımları (metotsuz sınıflar) 0.4 ile ağırlıklandırılıp geri plana atılıyor.

**Cevaplama.** Bağlam + iki tool (`read_file`, `search_symbol`). Her referans dosya varlığına ve
satır sınırına göre doğrulanıyor. Yol repo köküne hapsedilmiş (`resolve_in_repo`, cevaplama ve web
arayüzü aynı fonksiyonu kullanıyor).

---

## 7. Bulunan gerçek hatalar

**Önbellek anahtarı eksik hesaplanıyordu.** Anahtar, embedding'e giden metnin tamamından değil bir
bölümünden hesaplanıyordu; dosya taşındığında eski bağlamla üretilmiş vektör dönüyordu — 4.310
parça 3.549 vektöre çöküyordu.

**Önbellek kaydetmede O(n) arşiv okuması.** `codeqa ask` 68 saniye sürüyordu. `np.load` bir `.npz`
üzerinde tembel çalışıyor ve `data["vectors"]` her erişimde 48 MB'lık diziyi baştan açıyordu.
Döngü içinde olduğu için 12.205 kez okunuyordu. 68 sn → 6,3 sn.

**Öznitelik docstring'leri indekslenmiyordu** (§6).

**Referanslar parçanın başlangıç satırını gösteriyordu** *(bu oturumda bulundu)*. `read_file` modele
satır numaralı metin veriyordu ama bağlam olarak verilen parçalarda numara yoktu. Model o yüzden
`_constants.py:3` deyip aslında 10. satırdaki `DEFAULT_MAX_RETRIES`'i kastediyordu — referansı açan
kişi `import httpx` görüyordu. Doğrulama katmanı yakalamıyordu çünkü satır dosya sınırları içinde.
`_format_hit` artık satır numaralı veriyor. İki kod tabanında iki soru koşuldu, on referansın onu da
iddia edilen satıra oturdu. **Ama bu n=2, ölçüm değil.**

**Bir uydurma referans** — model `türler/beta/...py:13` yazdı, dizin adını Türkçeye çevirmişti
(gerçek yol `types/beta/...`). Soruların Türkçe olmasının yan etkisi. Doğrulama katmanı yakaladı.

**Çok dil desteği neden kaldırıldı.** İkinci bir kod tabanında (Prometheus, Go, 453 dosya)
ölçüldü: kapsam %79 / MRR 0.674 (Python'da %93 / 0.847 — o günkü 14 soruluk bölmeyle). Üç şey
öğretti: (a) indeks bileşimi eşitlenmeliydi, fark 26 puandan 14'e indi ama kaybolmadı;
(b) doküman parçaları kod sonuçlarını bastırıyor (MRR 0.406 → 0.586); (c) Python'da dokümantasyon
gövdenin içinde, diğer yedi dilde bildirimin üstünde — 1500 Go fonksiyonunun 607'sindeki yorum
indekse hiç girmiyordu. Kod git geçmişinde (`916f046~1`); geri getirilirse o dilde ölçülerek.

---

## 8. Sıradaki adımlar

1. **Kullanıcı deneyi.** Projenin ölçülmemiş tek iddiası: *"adapte olma süresini kısaltır."*
   Getirme ve cevap doğruluğu ölçüldü, zaman kazancı ölçülmedi — tek başına ölçülemez. Protokol
   hazır: `eval/KULLANICI_DENEYI.md`. Kritik kural: soru önce yazılır, sonra araca sorulur.
   İki kişi, yarım gün.

2. **Yeni eklenen 92 soru insan gözünden geçmeli.** Beş setin test bölmeleri bu oturumda LLM
   tarafından yazılmış sorularla büyütüldü. Yöntem kurallara uyduruldu (sorular kod okunmadan
   yazıldı, etiketler **grep ile** kondu — codeqa ile değil, o %100'ü garantilerdi; her
   `expect_files` yolunun diskte var olduğu programla doğrulandı). Ama zor sette ölçüldü:
   insanın yazdığı 14 soru %100/0.847, LLM'in yazdığı 12 soru %92/0.653 veriyor. Güven aralıkları
   örtüştüğü için fark ayırt edilemiyor ama gerçek olabilir. Gözden geçirirken **setin tamamı**
   geçirilmeli (Kural 3) ve düzeltmeler ölçüm düzeltmesi diye işaretlenmeli.

3. **Genişletilmiş sette cevap ölçümü.** Getirme 238 soruya çıktı; cevap tarafı hâlâ zor setin 14
   soruluk hâlinde. Ücretli olduğu için koşulmadı. Satır numarası düzeltmesi (§7) de burada
   ölçülmeli.

4. **Doküman ağırlıklandırmasını ölçmek.** Doküman parçalarının kod sonuçlarını bastırdığı
   görüldü ama bu indekslerde doküman payı düşük. Doküman ağırlıklı bir repoda ölçülmeli.
   `--no-docs` zaten var.

5. **`d36` etiketi gözden geçirilmeli.** Etiketi `resources/beta/deployments.py`, arama
   `deployment_runs.py`'yi getiriyor, ikisi de savunulabilir. Düzeltilirse ölçüm düzeltmesi
   olarak işaretlenmeli.

**Kaçırılan sorular** (hepsi gerçek getirme hatası; hedef dosyalar indekste, etiket hatası değil):
`z34`, `a27`, `a28`, `t27`, `t33`, `t40`. Hiçbiri düzeltilmedi — yalnızca kaçıranı düzeltmek
Kural 3'ün yasakladığı şey.

**Denenmemiş aday:** dizin genişletmesi sıralamada önce rastladığı dizini seçiyor, en güçlü temsil
edileni değil. Denenirse ilgili soru test'ten dev'e taşınmalı.

---

## 9. Demo

Akış `DEMO.md`'de (389 satır), komutlar `DEMO_KOMUTLAR.txt`'de. **Komutları DEMO.md'den
kopyalama** — ham markdown olarak açılırsa kod bloğunun ``` tırnakları da kopyalanıyor ve zsh
iç içe bir kabuk açıyor; sonraki komutlar sessizce koşmuyor. Provada tam olarak bu oldu.
`DEMO_KOMUTLAR.txt`, `tools/komutlari_cikar.py` ile DEMO.md'den üretiliyor ve senkron kalması
teste bağlı.

Süre ~14 dakika. On komutun onu da prova edildi. Demoda dikkat edilecek üç şey:

- §2'deki canlı soru `questions_akis.json`'daki `a02`'nin neredeyse aynısı ve **dev** bölmesinde.
  Senaryo bunu söylemeyi ve arkasından test bölmesinden ikinci bir soru sormayı içeriyor.
- §3'te ekranda kırmızı bir `✗` görünüyor (`z34: bulunamadı`). Senaryoda hazır cümlesi var:
  gösterilecek, saklanmayacak.
- §5'te (MCP) sorulacak soru §2'dekinden **farklı olmalı**. Provada retry sorusu sorulunca Claude
  aracı hiç çağırmadan ezberden cevapladı. Senaryoda ezberlenemeyecek bir soru var (skill arşivi
  güvenliği).

MCP sunucusu `.mcp.json` ile kayıtlı ve Claude Code'da çalıştığı doğrulandı.

---

## 10. Pratik bilgiler

```
codeqa index <repo> -o data/x.jsonl --exclude repos
codeqa embed -i data/x.jsonl --provider voyage
codeqa ask "soru" -i data/x.jsonl --provider voyage --repo <repo>
codeqa ui                       # yerel web arayüzü, 127.0.0.1:8765
codeqa eval -q eval/... --split test    # bedava, yarım saniye
```

**`--exclude repos` önemli:** `repos/` altında ölçüm için klonlanmış Saleor ve InvenTree duruyor.
Dışlanmazsa proje kendi indeksine 42 bin parça olarak giriyor ve tarama ~$1,60 gösteriyor.

**Maliyet:** canlı soru Haiku ile 0,6-2,2 sent, 6-13 saniye. Getirme ölçümü tamamen ücretsiz ve
yarım saniye. Saleor'un 12.076 parçasını embed etmek 36 sent tuttu. Ölçüm aleti olarak Haiku
Opus'tan iyi çıktı — Opus tavana yakın çalıştığı için kolları ayırt edemiyor.

**Dosya yapısı:**

```
codeqa/          15 modül, 4.534 satır
  indexer.py · docs.py · models.py · embeddings.py · search.py · rerank.py
  contextual.py · answer.py · evaluation.py · projects.py · mcp_server.py
  ui.py + ui.html · cli.py
tests/           218 test, 2.758 satır
eval/            5 soru seti + KULLANICI_DENEYI.md + SORU_SETI.md
tools/           komutlari_cikar.py
DEMO.md          sunum akışı · DEMO_KOMUTLAR.txt kopyalanabilir komutlar
README.md        703 satır — bütün ölçümler ve gerekçeler
.mcp.json        Claude Code entegrasyonu (mutlak yol, bu makineye özel)
data/ runs/ repos/   git'e girmiyor
```

**Tanıtım sayfası:** https://claude.ai/code/artifact/5420df44-0466-4585-8223-3f1f39e4278e
(özel; paylaşmak için sayfanın paylaş menüsünden açmak gerekiyor). Aracın kendisi değil, ölçümleri
ve yöntemi anlatan statik sayfa. Tepesinde `127.0.0.1:8765`'e giden bir düğme var — araç yerel
kalıyor, sayfa sadece kapısını açıyor.

**Dal:** `embedding-arama`. `main`'e birleştirilmedi.

---

## 11. Bu belgeyi okuyan yeni oturuma

Üç şeyi yapma:

1. **Düşen sayıları geri almaya çalışma.** §3'ü oku. Araç kötüleşmedi, ölçüm hassaslaştı.
2. **Kaçırılan soruları silme ya da yumuşatma.** Sayı yükselir, anlamı kaybolur (Kural 3).
3. **§5'teki fikirleri yeniden deneme** — koşullar değiştiyse ayrı, ama o zaman *neden* değiştiğini
   yazarak dene.

Bir şey ölçeceksen: dev'de dene, test'te doğrula, sonucu olduğu gibi yaz.
