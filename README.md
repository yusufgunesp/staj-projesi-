# Kod Tabanı Soru-Cevap Asistanı

Bir kod tabanını indeksleyip doğal dildeki sorulara **dosya ve satır referanslı** cevap veren araç.
Amaç, yeni bir projeye adapte olma süresini kısaltmak: her cevap kaynak gösterdiği için doğruluğu
anında kontrol edilebiliyor.

```
$ codeqa ask "istek kaç kez yeniden deneniyor?"

Varsayılan 2 (`_constants.py:10`). Karar `_base_client.py:842`'deki
_should_retry'da veriliyor: 408, 409, 429 ve 5xx yeniden deneniyor.
Sunucu retry-after başlığı gönderirse ona uyuluyor (`_base_client.py:793`).
```

**Ölçülen sonuç:** iki Python kod tabanında, hiçbir ayarın görmediği test bölmelerinde —
`anthropic` SDK'sının en zor setinde **getirme %96, kapsam %90**, ikinci kod tabanı Saleor'da
**%100 / %96**. Aynı zor setin 26 soruluk test bölmesinde **cevap doğruluğu %100, cevabın
referans verdiği dosya kapsamı %91**. Bugüne kadarki bütün cevap koşularında toplam **bir**
uydurma referans görüldü ve doğrulama katmanı onu yakaladı (bkz. [Cevaplama](#cevaplama)).

Bu sayılar önceki sürümde daha yüksekti (%100 / %93). Araç değişmedi — test bölmeleri büyüdü ve
küçük örneklem iyimserliği ortadan kalktı. Ayrıntı [Ölçüm](#ölçüm) bölümünde.

**Araç yalnızca Python indeksliyor.** Bir dönem sekiz dil destekleniyordu; ikinci bir dilde
ölçüldüğünde sonuç belirgin şekilde düştüğü için kaldırıldı — ayrıntı
[Öğrenilenler](#bir-dilde-ölçülen-başarım-başka-dili-öngörmüyor) bölümünde.

## İçindekiler

- [Kurulum](#kurulum) · [Kullanım](#kullanım) · [Web arayüzü](#web-arayüzü)
- [Nasıl çalışıyor](#nasıl-çalışıyor) — [parçalama](#parçalama), [arama](#arama),
  [cevaplama](#cevaplama)
- [Ölçüm](#ölçüm) — asıl sonuçlar burada
- [Öğrenilenler](#öğrenilenler) — işe yarayanlar ve yaramayanlar
- [Sıradaki adımlar](#sıradaki-adımlar)

## Durum

| Hafta | Hedef | Durum |
|-------|-------|-------|
| 1 | Kod sembolü çıkarma, doküman parçalama | Tamam |
| 2 | Embedding + arama katmanı | Tamam |
| 3 | Cevaplama katmanı, ölçüm düzeneği, iyileştirmeler | Tamam |
| 4 | Claude Code / MCP entegrasyonu, çok dil desteği | Tamam |
| 5 | Web arayüzü, ikinci kod tabanı, büyütülmüş test bölmeleri | Tamam — demo sırada |

## Kurulum

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env      # anahtarları .env dosyasına yazın, .env.example'a değil
```

| Anahtar | Ne için | Zorunlu mu |
|---------|---------|------------|
| `ANTHROPIC_API_KEY` | Cevaplama ve bağlam üretimi | Cevap almak için evet |
| `VOYAGE_API_KEY` | Embedding (`--provider voyage`) | Hayır — Ollama ya da `hash` de var |
| `ANTHROPIC_WORKSPACE_ID` | Yalnızca **organizasyon seviyesinde** üretilmiş anahtar için (konsol: Settings → Workspaces → ID). Çalışma alanına bağlı anahtarda gerekmez | Anahtar türüne göre |

## Kullanım

```bash
codeqa index /yol/repo -o data/chunks.jsonl     # 1. indeksle
codeqa embed --provider voyage                  # 2. vektörleştir
codeqa ask "ödeme akışı nasıl işliyor"          # 3. sor
```

(`codeqa` yerine `.venv/bin/python -m codeqa`.)

Diğer komutlar:

| Komut | Ne yapar |
|-------|----------|
| `search` | Cevap üretmeden sadece arama sonuçlarını gösterir |
| `ui` | Yerel web arayüzünü açar (aşağıda) |
| `eval` | Soru seti üzerinde doğruluk ölçer |
| `contextualize` | Parçalara LLM ile bağlam cümlesi ekler |
| `serve` | MCP sunucusu olarak çalışır (Claude Code entegrasyonu) |
| `stats` / `grep` | İndeksi inceleme araçları |

### Web arayüzü

```bash
codeqa ui
```

`127.0.0.1:8765`'i açıyor. Bayrak gerekmiyor: **projeler arayüzden ekleniyor.**

**Proje ekleme iki adımlı**, çünkü ikinci adım para harcıyor:

1. **Tara** — repo yolunu ver. İndeksler, bileşimini (kod / test / doküman oranı) ve maliyet
   tahminini gösterir. Bedava, yerel, ağ yok. Doküman oranı yüksekse uyarır: markdown
   parçalarının kod sonuçlarını bastırdığı ölçüldü (MRR 0.406 → 0.586).
2. **Devam et** — vektörleştirir, ilerleme çubuğuyla. Zaten önbellekte olan parçalar için ödeme
   yok; ekranda kaçının bedava geldiği yazıyor.

Sonrasında projeler üstteki listeden seçiliyor. Aktif proje sunucuda tutulmuyor, her istek hangi
projeyi kastettiğini kendi söylüyor — iki sekme iki farklı projeye bakabilir.

**Proje eklenirken iki ucuz kontrol koşuyor. İkisi de ölçüm değil, arayüz bunu böyle
etiketliyor:**

*Dil işareti* — parçaların yüzde kaçının Türkçe metin taşıdığına bakıp mod öneriyor. Dayanağı
ölçülmüş mekanizma: BM25 ancak sorunun kelimeleri kodda geçtiğinde tutunuyor, sorular da Türkçe.
İki kod tabanında ölçülen ayrım keskin (kendi repo %66 → hibrit kazanıyor, anthropic SDK %1 →
hibrit çöküyor) ama eşik iki noktadan seçildi, doğrulanmadı.

*Duman testi* — indeksten 25 sembol seçip adıyla arıyor. Cevaplayabildiği tek soru "indeks ve
arama hiç çalışıyor mu"; **doğruluk değil**, çünkü sembol adıyla sembol bulmak neredeyse `grep` ve
bu sorular anahtar kelime aramasını sistematik olarak kayırıyor — mod seçmek için kullanılamaz.
Örneklem `chunk_weight` ile süzülüyor: metotsuz tip sınıfları arama katmanı tarafından bilerek
geri itiliyor, onları sorup "bulunamadı" saymak kasıtlı davranışı arıza gibi gösterirdi (bu
süzgeç eklenmeden önce anthropic SDK'sında sonuç 17/25 çıkıyordu, sonra 23/25). Bedava da değil:
25 sorgu = 25 embedding çağrısı, ~7 saniye.

Gerçek bir sayı için o repoya soru seti yazmak gerekiyor; arayüz bunu söylüyor ve rehberi
gösteriyor: [eval/SORU_SETI.md](eval/SORU_SETI.md).

**Referansı tek tıkla doğrulama.** Terminalde `_base_client.py:818` görünce kontrol etmek için
ayrı bir `sed` komutu gerekiyor; arayüzde referansa tıklayınca dosyanın o satırı, hedef satır
vurgulanmış hâlde altında açılıyor. Aracın bütün iddiası "kaynak gösteriyorum, kontrol et"
olduğuna göre kontrolü ucuzlatmak arayüzün asıl işi.

Eski çağrı biçimi de çalışıyor: `-i` ve `--repo` verilirse o indeks açılışta proje olarak
kaydediliyor.

Anahtar yoksa cevaplama düğmesi kapanıyor, arama çalışmaya devam ediyor. Sunucu yalnızca
`127.0.0.1`'e bağlanıyor ve dosya okuma repo köküne hapsedilmiş durumda (cevaplama katmanıyla
aynı kontrol, `resolve_in_repo`).

**Bağımlılık eklenmedi** — `http.server` yeterli. Bir dönem "exe yapalım mı" diye soruldu;
yapılmadı, çünkü API anahtarları binary'ye gömülemiyor (anahtarsız `hash`'e düşüyor, o da
[dil farkı bulgusu](#hibrit-arama-bedava-kazanç-değil) yüzünden Türkçe soruda çalışmıyor) ve
`.exe` Windows demek. Yerel sunucu ikisini de es geçiyor.

**Desteklenen dil: Python.** Yerleşik `ast` ile ayrıştırılıyor; başka uzantılar sessizce
atlanıyor.

**Embedding sağlayıcıları:** `voyage` (bulut, en iyi sonuç), `ollama` (local, kod dışarı çıkmaz),
`hash` (anahtarsız yer tutucu — anlamsal arama **yapmaz**, sadece boru hattını denemek için).

## Nasıl çalışıyor

```
repo ──index──> parçalar ──embed──> vektörler
                                       │
soru ──────────────────────────────> arama ──> parçalar ──> Claude ──> cevap
                                                              │  (read_file,
                                                              └── search_symbol)
```

### Parçalama

Kod, sabit uzunlukta metin blokları yerine **AST üzerinden** bölünüyor. Her fonksiyon, metot ve
sınıf ayrı bir parça oluyor. İki sebebi var: parçalar anlamlı bir bütün olarak kalıyor ve her
parçanın gerçek satır aralığı biliniyor — `dosya:satır` referansı verebilmenin ön şartı bu.

| Tür | İçerik |
|-----|--------|
| `module` | Docstring + import/include'lar + dosya seviyesi sabitler |
| `class` | Sınıf başlığı + docstring + metot imzaları (gövdeler tekrar edilmiyor) |
| `function` / `method` | Tam gövde, dekoratörleriyle |
| `section` | Markdown dosyalarının başlık bazlı bölümleri |

Sınıf parçaları alan listesini ve **öznitelik docstring'lerini** de taşıyor. Bunlar atamadan sonra
gelen çıplak string'ler (PEP 258); `ast` onları atamaya bağlamadığı için yalnızca `Import`/`Assign`
düğümlerini toplayan bir okuyucudan sessizce düşüyorlar. Bu SDK'da kaybın ölçüsü 1097 dosyanın
624'ünde 2025 docstring, 222.843 karakterdi — tipli bir kütüphanede en açıklayıcı metin bunlar.
İndekslenmeye başlandığında indeks metni %14 büyüdü, akış setinde MRR 0.777 → 0.810 çıktı.

Her parçanın önüne konum etiketi ekleniyor (dosya yolu, nitelenmiş ad, tür) ve embedding'e parça
bu etiketle gidiyor. Kullanıcıya sadece kodun kendisi gösteriliyor.

### Arama

İki yöntem: **BM25** tam isim eşleşmesinde, **vektör araması** dolaylı anlatımda iyi. Birleştirme
RRF ile — skorlar değil sıralamalar toplanıyor, böylece iki yöntemin farklı ölçekleri normalize
edilmek zorunda kalmıyor.

Hangisinin kazandığı repoya bağlı; [Ölçüm](#ölçüm) bölümüne bakın. `--mode` ile seçiliyor.

**Alaka sırasının arkasına üç eksende ekleme yapılıyor.** Hiçbiri eleme yapmıyor; ilk k olduğu
gibi kalıyor, liste yalnızca uzuyor (varsayılan 8 → ~15 parça):

| Eksen | Ne zaman devreye giriyor | Varsayılan |
|-------|--------------------------|------------|
| Dosya çeşitliliği | İlk k'da aynı dosya tekrar ediyorsa, görülmemiş dosyaların en iyi parçası | 4 slot |
| Dizin kardeşi | Bir dizin en az 2 parçayla temsil ediliyorsa, o dizinin görülmemiş dosyaları | 2 slot |
| İmport bağı | Seçilen bir dosyayı kullanan dosyalar — sıralamadan bağımsız, grafikten | 2 slot |

Üçüncüsünün ayrı bir gerekçesi var: *"kimler kullanıyor"* bir benzerlik sorusu değil, **çağrı
grafiği** sorusu. Bir ölçümde beklenen dosya sıralamada 136. sıradaydı; taramayı o derinliğe
açmak araya 135 gürültü almak demekti. Grafik indeksteki modül parçalarının import satırlarından
çıkarılıyor. Beşten çok kullanıcısı olan dosyalar atlanıyor — `_models.py`'nin 513 kullanıcısı
var, onu genişletmek her soruya aynı dosyaları eklemek olurdu.

Üretilmiş tip tanımları (metotsuz sınıflar) 0.4 ile ağırlıklandırılıp geri plana atılıyor. Bu tek
başına zarar veriyor, genişletmelerle birlikte kazandırıyor; ayrıntısı
[Öğrenilenler](#tek-başına-işe-yaramayan-iki-şey-birlikte-yarayabilir) bölümünde.

Vektörler içerik karmasına göre `data/embeddings/` altında önbelleğe alınıyor. Karma, bağlam
etiketi dahil edilerek hesaplanıyor — sadece kod metni üzerinden alınsaydı bir dosya taşındığında
önbellekten eski bağlamla üretilmiş vektör dönerdi.

### Netleştirme (isteğe bağlı)

`codeqa search "ödeme" --facets`, arayüzde **Netleştir** düğmesi. Sıralamaya dokunmuyor: havuzun
ilk 300 adayı dizin ağacına göre öbeklenip kullanıcıya geri gösteriliyor, bir öbek seçilirse arama
yeni sorguyla baştan koşuyor.

**Çözdüğü şey soyutlama seviyesi, dil değil.** Türkçe→İngilizce köprüsü zaten çalışıyor
(`ödeme`→`payment`, `yetki`→`permission`, `stok`→`warehouse`; üçünde de doğru alan ilk sırada).
Sorun şu: `"ödeme"` sorgusunun ilk sekizi baştan sona arayüz katmanı (`PaymentError`,
`PaymentInterface`, `PaymentBase`), tek bir somut sağlayıcı yok. `"ödeme sağlayıcısına istek nasıl
gönderiliyor"` ise tamamen `payment/gateways/*` getiriyor ve **iki listenin ilk sekizde ortak
parçası sıfır.** Saleor'un sekiz ödeme entegrasyonunun ilk temsilcisi havuzda 15., çoğu 48-142
arasında — ilk 8'i gruplamak hiçbirini göstermezdi.

**Kazanç ilan edilmiyor**, sebebi [Öğrenilenler](#netleştirme-dizin-yapısı-anlam-taşıyorsa-çalışıyor)
bölümünde.

### Claude Code entegrasyonu (MCP)

Araç MCP sunucusu olarak çalışabiliyor; böylece geliştirici terminale gitmeden Claude Code'un
içinden kullanıyor. `~/.claude/mcp.json` (ya da istemcinin yapılandırması):

```json
{
  "mcpServers": {
    "codeqa": {
      "command": "/yol/staj/.venv/bin/python",
      "args": ["-m", "codeqa", "serve", "-i", "data/chunks.jsonl", "--provider", "voyage"],
      "cwd": "/yol/staj"
    }
  }
}
```

Sunulan tool'lar:

| Tool | Ne yapar |
|------|----------|
| `search_code` | İndekste doğal dilde arama, `dosya:satır` konumlarıyla |
| `read_chunk` | Bulunan bir parçanın tam metnini getirir |
| `index_status` | İndeksin hangi repoyu, kaç parçayı, hangi dilleri kapsadığını söyler |

**Cevaplama (`ask`) bilerek sunulmuyor.** MCP istemcisi zaten bir dil modeli; ona ikinci bir
modelin ürettiği cevabı vermek yerine ham arama sonuçlarını vermek hem ucuz hem daha doğru —
istemci kendi bağlamıyla yorumluyor.

### Cevaplama

Bulunan parçalar bağlam olarak modele veriliyor; model eksik kalanı iki tool'la kendisi okuyor:
`read_file` (dosyayı satır numaralarıyla) ve `search_symbol` (ikinci tur arama).

Üç şey önemli:

**`read_file` repo köküne hapsedilmiş.** Yol modelden geliyor, yani güvenilmez girdi; `../..` ile
dışarı çıkma denemesi reddediliyor, testlerle sabit.

**Referanslar doğrulanıyor.** Model var olmayan bir `dosya:satır` uydurabilir. Her referans dosya
ve satır sınırına göre kontrol ediliyor, tutmayanlar işaretleniyor.

Bütün cevap koşularında bir kez gerçekleşti ve türü öğreticiydi: model
`türler/beta/beta_fallback_credit_not_applied.py:13` yazdı — **dizin adını Türkçeye çevirdi**,
gerçek yol `types/beta/...`. Soruların Türkçe olmasının yan etkisi; doğrulama katmanı olmasaydı
cevap doğru görünecekti. Katmanın varlık sebebi tam olarak bu. Kısaltılmış yollar
(`models.py` → `codeqa/models.py`) indekste tek eşleşme varsa kabul ediliyor; birden fazla
eşleşme varsa cevabın kendi içinde tam yazılmış yollar bağlam olarak kullanılıyor.

**Prompt caching açık.** Model her tool turunda geçmişi yeniden gönderiyor. Ölçülen fark: aynı
soruda 42.000 taze girdi token'ı yerine 8 taze + 18.500 önbellekten.

**Denenip geri alınan: prompt'a eksiksizlik kuralları.** Zor sette 29 beklenen dosyanın 12'si
bağlamdayken cevapta hiç geçmiyordu; prompt'a "mekanizma birden çok dosyaya yayılıyorsa hepsini
referansla" türü üç kural eklendi ve kapsam %57 → %72 çıktı. Kurallar o setin hatalarına bakılarak
yazıldığı için görmediği bir sette sınandı, kazanç sıfır çıktı ve değişiklik geri alındı. Ayrıntı:
[Kendini kandırmanın üç yolu](#kendini-kandırmanın-üç-yolu-ve-üçünün-de-yakalanışı).

## Ölçüm

Ölçüm düzeneği aracın bir parçası, yan ürünü değil — çünkü **doğru yapılandırma repoya göre
değişiyor** ve bunu ancak ölçerek bulabiliyorsunuz.

```bash
codeqa eval -q eval/questions_anthropic.json --split test    # getirme, bedava
codeqa eval -q eval/questions_anthropic.json --answers       # cevap, ücretli
```

İki şey ayrı ölçülüyor: **getirme** (arama doğru parçayı ilk k'ya soktu mu — API çağrısı yok) ve
**cevap** (model doğru dosyaya referans verdi mi — Claude çağırıyor). Getirme bozuksa cevap da
bozulur ama tersi doğru değil; ayrı ölçülmezse hangisinin düzeltileceği belli olmuyor.

**dev/test ayrımı var.** Ayarlar yalnızca dev'de denenir, rapor test'ten alınır. Aynı set üzerinde
hem ayar yapıp hem rapor etmek, ayarı o setin gürültüsüne uydurmak demek.

### Sonuçlar

**İki kod tabanı ölçülüyor.** Bileşimleri bilerek eşitlendi (ikisi de %99+ üretim kodu):

| Kod tabanı | Ne | Dosya | Parça |
|------------|-----|-------|-------|
| `anthropic` SDK | LLM API istemci kütüphanesi, büyük kısmı üretilmiş kod | 1099 | 4.311 |
| **Saleor** | Açık kaynak e-ticaret (Django), elle yazılmış iş uygulaması | 1.060 | 12.076 |

İkincisi, "bütün ölçümler tek repodan" sorununu kapatmak için eklendi. Seçim bilinçli: müşteri
projelerine bir kütüphaneden çok daha yakın.

**Beş soru seti, 238 soru.** Hepsinde beklenen dosyalar kod içinde aranarak işaretlendi:

| Set | Soru | Tipi |
|-----|------|------|
| `questions_anthropic.json` | 80 (40 dev / 40 test) | Tek konum — "X nerede" |
| `questions_akis.json` | 40 (10 dev / 30 test) | Akış — cevap 2-4 dosyada |
| `questions_zor.json` | 38 (12 dev / 26 test) | Zor — benzer mekanizmaları ayırt ettiren |
| `questions_saleor.json` | 40 (14 dev / 26 test) | Saleor — üç tip karışık |
| `questions.json` | 40 (20 dev / 20 test) | Aracın kendi kodu |

Zor setin bölünmesi özel: **`z01`-`z12` dev.** İlk 12 soru kirlenmiş sayılıyor çünkü genişletme
eksenleri onların hatalarına bakılarak tasarlandı; rapor edilecek sayı `z13`-`z38`'den alınır.

**Getirme (varsayılan yapılandırma, voyage + vektör, k=8, test bölmeleri):**

| Set | n | recall@8 | **kapsam** | MRR |
|-----|---|----------|------------|-----|
| Kolay | 40 | 92% | **92%** | 0.701 |
| Akış | 30 | 93% | **87%** | 0.808 |
| **Zor** | 26 | **96%** | **90%** | **0.757** |
| **Saleor** | 26 | **100%** | **96%** | **0.573** |
| Kendi kodu | 20 | 100% | 100% | 0.794 |

Çok dosyalı sorularda asıl ölçüt **kapsam**, recall değil: "en az bir beklenen dosyayı bulduysan
başarılı" saymak kolay — üç dosyadan birini bulmak yetiyor.

**Saleor satırı aracın zayıf noktasını gösteriyor:** doğru dosyayı buluyor (recall %100) ama
aşağıda sıralıyor (MRR 0.573 ≈ ortalama 2. sıra). 12 bin parçalık çok modüllü bir uygulamada
doğru cevabın yanında daha çok benzer aday var. Kendi kodundaki %100/%100 ise rapor edilecek bir
sayı değil: 571 parçalık küçük bir indekste, aracın kendi kodu.

**Cevap (hepsi `claude-haiku-4-5`, tek fark bağlam):**

| Set | Genişletmeler | parça | doğruluk | **dosya kapsamı** | uydurma | referans/cevap |
|-----|---------------|-------|----------|-------------------|---------|----------------|
| Zor (12, bölünmemiş hâli) | kapalı | 8 | 75% | 46% | 0 | 1.3 |
| Zor (12, bölünmemiş hâli) | açık | 15 | 100% | 57% | 0 | 2.1 |
| Akış (20, doğrulama) | kapalı | 8 | 90% | 79% | 1 | 2.1 |
| Akış (20, doğrulama) | açık | 15 | 95% | 84% | 0 | 2.8 |
| Zor — test (14 soruluk hâli) | açık | 15 | 100% | 93%\* | 0 | 2.7 |
| **Zor — test (26 soru, güncel)** | açık | 15 | **100%** | **91%** | **0** | 5.4 |

Getirme metrikleri tek başına yeterli değil: kapsam, eklenen slot sayısıyla matematiksel olarak
**düşemez**, yani her ekleme kendini haklı çıkarır. Kararı cevap ölçümü verdi. Son sütun o
ölçümün kontrolü — kapsam artarken referans sayısı patlamadıysa model her şeyi saymıyor, hedefe
daha çok isabet ediyor demektir.

İlk iki satır tek başına kanıt değildi: genişletme eksenleri zor setin hatalarına bakılarak
tasarlanmıştı (dizin kardeşi `z05`/`z06`'ya, import bağı `z03`'e). Doğrulama, tasarım sırasında
hiç bakılmayan akış setinde yapıldı ve aynı yönü verdi: doğruluk +5, kapsam +5, uydurma referans
1'den 0'a.

Son iki satır projenin en temiz sayıları: hiçbir ayarın görmediği test bölmesi. Alttaki satır
**26 soruya çıkarılmış güncel bölme** — doğruluk 26/26'da kaldı (%95 GA: %87-%100), dosya kapsamı
%93'ten %91'e indi. Getirme tarafındaki büyümenin aksine cevap tarafı neredeyse hiç oynamadı.

Cevap kapsamının (%91) getirme kapsamını (%90) **geçmesi** dikkat çekici ve sebebi ölçümde
görünüyor: getirmenin kaçırdığı tek soruda (`z34`, iki ayrı uçtaki jeton sayımı) model doğru
cevabı yine de verdi — iki `read_file` çağrısıyla getirmenin bulamadığı dosyaya kendi gitti.
**Tool katmanı getirme hatasını telafi edebiliyor.** Zor setin ilk hâlinde bu iki sayı arasında
37 puan fark vardı, şimdi cevap tarafı önde.

Referans/cevap 2.7'den 5.4'e çıktı. Bu kontrol metriği (§Kural 2): kapsam **düşerken** referans
sayısı iki katına çıktığına göre model daha çok dosya sayıp isabeti artırmıyor — 26 soruluk
bölmedeki yeni sorular basitçe daha çok dosyaya dokunuyor.

Model seçimi hakkında: aynı ölçüm `claude-opus-5` ile daha yüksek mutlak sonuç veriyor, ama
**ölçüm aleti olarak Haiku daha iyi çıktı** — Opus tavana yakın çalıştığı için kolları ayırt
edemiyordu. Maliyet farkı da var: iki Haiku koşusu $0.55, tek Opus koşusu $10.75.

\* **Ölçüm düzeltmesi içeriyor, sistem kazancı değil.** İlk koşuda %86/%89'du. Dört kısmi hatanın
ikisinde etiketin fazla cömert olduğu görüldü: `z13` arşiv çıkarma güvenliğini soruyor ama etikette
`agent_toolset.py` da vardı — o dosya `_within`'i kendi yol kısıtlaması için kullanıyor, arşiv
güvenliğinin tamamı `_skills.py`'de. `z23` çakışmanın nerede yakalandığını soruyor ama etikette
`_exceptions.py` vardı — orada yalnızca taban sınıf duruyor. Tek yönlü düzeltme olmaması için on
dört sorunun tamamı gözden geçirildi; kalan on ikisinde etiketler yerinde kaldı.

**Nasıl buraya gelindi (kolay set, saf vektör):**

| Aşama | recall@8 | MRR |
|-------|----------|-----|
| Başlangıç | 87% | 0.714 |
| + modül sabitleri indekslendi | 88% | 0.727 |
| + soru etiketleri düzeltildi\* | 93% | 0.761 |
| + genişletme slotları | 97% | 0.731 |
| + öznitelik docstring'leri indekslendi | 95% | 0.740 |
| + tip ağırlıklandırması | **98%** | 0.735 |

\* Bu satır sistem iyileşmesi değil, **ölçüm düzeltmesi**: cevabı birden fazla dosyada olan
sorulara tek dosya yazmışız, araç doğru cevap getirdiği hâlde "hata" sayılıyormuş.

Son üç satırda MRR'ın recall'la aynı yöne gitmediğine dikkat: docstring'ler MRR'ı yükseltiyor
(0.731 → 0.740) ama recall'ı düşürüyor (97% → 95%), tip ağırlıklandırması tersini yapıyor.
Zenginleşen tip parçaları vektör aramasında daha rekabetçi hâle geliyor; ağırlıklandırma onu
dengeliyor. İkisi birlikte her iki metrikte de başlangıcın üstünde.

**Embedding sağlayıcıları (60 soru, hibrit):**

| Sağlayıcı | Nerede | recall@8 | MRR |
|-----------|--------|----------|-----|
| `voyage-code-3` | bulut | 100% | 0.702 |
| `bge-m3` | local | 100% | 0.654 |
| `nomic-embed-text` | local | 65% | 0.302 |
| `hash` (yer tutucu) | — | 85% | 0.403 |

## Öğrenilenler

Bu bölüm sonuçların en değerli kısmı: neyin işe yaramadığı da veriyle biliniyor.

### Hibrit arama bedava kazanç değil

Küçük repoda (Türkçe yorumlu, 249 parça) hibrit en iyisiydi. Büyük repoda (İngilizce, 4311 parça)
**saf vektör hibriti geçti** — BM25 recall'ı %95'ten %15'e düştü.

Sebep dil: Türkçe sorgu kelimeleri İngilizce kodda geçmiyor, BM25'in tutunacağı yer kalmıyor.
RRF zayıf sıralayıcıyı da hesaba kattığı için hibrit, saf vektörü aşağı çekiyor. Sorguyu
İngilizceye çevirmek BM25'i kısmen kurtarıyor (%15 → %57) ama hibrit yine saf vektörü geçemiyor.

**Varsayılan mod sağlayıcıya bağlı.** `search`/`ask`/`eval` varsayılanı `hybrid`, `serve`
varsayılanı `vector` — bu kaza değil. İlk grubun varsayılan sağlayıcısı `hash`, yani anahtarsız
çalışan yer tutucu; onunla vektör araması zayıf kalıyor ve BM25 tarafı taşıyor (kendi repo,
20 soru: hash+vector recall %65 / MRR 0.230, hash+hybrid %90 / 0.416). `serve` ise her zaman
gerçek bir sağlayıcıyla kuruluyor ve orada saf vektör kazanıyor. Pratik sonuç: `--provider
voyage` verirken `--mode vector` de vermek gerekiyor.

**Çıkarım:** arama modu sabitlenmemeli, her müşteri kod tabanında ölçülüp seçilmeli.

### Local model kullanılabilir — ama modeli doğru seçmek şartıyla

`nomic-embed-text` ile recall %65, `bge-m3` ile %100. İlk ölçümde "local model kalite
kaybettiriyor" sonucuna varmak yanlış olurdu; kaybettiren local olmak değil, zayıf modeldi.
Doğru local modelle buluttan fark MRR'da 0.05.

Bu, "müşteri kodu dışarı çıkamıyor" kısıtı olan projelerde aracın kullanılabilir olduğu anlamına
geliyor. `nomic-embed-text` ayrıca görev öneki (`search_document:` / `search_query:`) istiyor;
öneksiz recall %40'a düşüyor.

### Contextual retrieval masrafını çıkarmadı

Her parça için LLM'e bağlam cümlesi yazdırmak: vektör aramasında +2 puan recall, +0.024 MRR.
Maliyet ~5-7 dolar ve saatlerce koşu. BM25'i belirgin şekilde kurtardı (%15 → %47) ama asıl
kullanılan yol vektör araması.

Muhtemel sebep: **baseline zaten zayıf değildi.** Parçalar AST ile bölünüyor ve hâlihazırda
deterministik bağlam etiketi taşıyor. Bu tekniğin asıl değeri sabit uzunlukta bölünmüş, bağlamsız
parçalarda ortaya çıkıyor.

### Reranking dev'de kazandı, test'te çöktü

Saleor'un bölmesi zorluğa göre dengelenince dev'de zor sorular da oldu ve yeni bir şey görünür
hâle geldi: reranking çok dosyalı zor sorularda kazanıyor.

| | dev | test |
|---|-----|------|
| Saleor | 0.752 → **0.857** | 0.573 → **0.480** |
| zor | 0.694 → **0.808** | — |
| akış | 0.867 → **0.753** | — |

Test bölmesinde recall %100'den %92'ye, kapsam %96'dan %87'ye düştü. Yani dev'de +0.10 kazandıran
ayar, görmediği bölmede her metriği bozuyor. Reddedildi.

Bu, **dev'de kazanıp test'te sıfır veren dördüncü fikir**. Zor setin test bölmesi bu doğrulama
için harcanmadı: iki ayrı yerde (akış dev'i ve Saleor test'i) genellenmediği zaten görülmüştü.

Yanında denenen yedi kol — çeşitlilik/dizin/import slotlarını artırmak, havuzu büyütmek, tip
ağırlığını kapatmak, hibrit mod — üç dev bölmesinde de tam olarak **düz** çıktı ya da zarar verdi.
Yani mevcut varsayılanlar ikinci kod tabanında da en iyisi: anthropic SDK'sında yapılan ayar
Saleor'a taşındı.

### Reranking ile ağırlık ayarı birbirinin yerine geçiyor

Reranking MRR'ı 0.702 → 0.750 çıkardı. RRF ağırlıklarını ayarlamak (bedava, API çağrısı yok)
0.738'e çıkardı. İkisi birlikte yine 0.750 — reranker sıralamayı baştan kurduğu için ağırlığın
etkisini siliyor.

### Kendini kandırmanın üç yolu ve üçünün de yakalanışı

Bu bölüm sonuçların en değerli kısmı: ölçüm düzeneğinin asıl işi iyi haber üretmek değil, kötü
haberi saklamamak.

**1. Metriğin müdahaleyi tanım gereği ödüllendirmesi.** Akış kapsamını artırmak için önce *sert
kota* denendi: ilk k içinde dosya başına en fazla 1 parça. Kapsam anında yükseldi (akış 0.750 →
0.867) ve karar verilebilirdi. Verilmedi, çünkü kapsam metriği dosya çeşitliliğini ödüllendiriyor
ve kota tam olarak çeşitliliği artırıyordu. Döngüyü kıracak bağımsız bir ölçüte bakıldı — **sembol
isabeti** (doğru dosyanın doğru fonksiyonu geldi mi) — ve orada sonuç **0.778 → 0.444** düştü.

Sebep tek tek incelendi: iki ilgili parça gerçekten aynı dosyada olabiliyor (sync/async ikizleri,
decoder + accumulator çifti) ve sert kota bunlardan birini kesiyordu. Bir vaka ölçüm artefaktıydı:
beklenen `Anthropic.copy`, kota `AsyncAnthropic.copy`'yi tutmuştu; cevap aynı, etiket ikizlerden
birini yazdığı için hata sayılıyordu.

Yumuşak ceza da çare olmadı: RRF skorları `1/(60+sıra)` biçiminde olduğu için fazla sıkışık,
0.8'in altındaki her çarpan pratikte sert kotaya dönüşüyor. Çalışan yaklaşım **hiçbir şeyi
elemeyen** genişletme oldu. Kritik kontrol, kazancın "daha çok parça verdik"ten gelmediğini
göstermek oldu: eşit bütçede (12 parça) düz top-12 akış kapsamını 0.750'de bırakıyor, genişletme
0.833'e çıkarıyor.

**2. Ayar yapılan sette rapor vermek.** Zor set dev/test bölündükten sonra üç iyileştirme denendi.
Üçü de dev'in hatalarına bakılarak tasarlandı, üçü de o hataları düzeltti:

| Deneme | dev | **test** |
|--------|-----|----------|
| Prompt'a eksiksizlik kuralları | +15 puan kapsam | **0** (akış setinde ölçüldü) |
| Yeniden dışa aktarım kabuklarını geri itme | +0.042 MRR | **0** |
| İmport bağının ileri yönü | +5.6 puan kapsam | **0** |

Üçü de makul fikirlerdi. Prompt kuralları gerçek bir boşluğu kapatıyordu (dosyalar bağlamdayken
referans verilmiyordu), yeniden dışa aktarım kuralı "metotsuz sınıf alan listesidir" kuralının
modül karşılığıydı, ileri yön `_fallbacks.py` ilk sıradayken onun import ettiği `_middleware.py`
ilk 200'de olmadığı için gerekliydi. Hiçbiri genellenmedi.

**Bölme olmasaydı üçü de rapora "iyileştirme" diye girecekti.** İkincisi ve üçüncüsü kod olarak
duruyor, varsayılan kapalı; birincisi geri alındı çünkü karşılığında %25 fazla referans
üretiyordu.

**3. Yalnızca başarısızlıkların etiketini sorgulamak.** Test bölmesindeki dört kısmi hatanın
ikisinde etiket fazla cömertti. Düzeltmek meşru ama tehlikeli: yalnızca sistemin kaçırdığı
etiketlere bakıp geçtiklerine bakmamak, skoru tek yönlü şişirir. On dört sorunun tamamı aynı
titizlikle gözden geçirildi; kalan on ikisinde etiketler yerinde kaldı. Sayı %86'dan %93'e çıktı
ve **ölçüm düzeltmesi** olarak işaretlendi, sistem kazancı olarak değil.

### Tek başına işe yaramayan iki şey birlikte yarayabilir

İndeksin %42'si üretilmiş tip tanımı ve bunlar bazı sorularda gerçek cevabı ilk 8'in dışına
itiyor. Metotsuz sınıfların ağırlığını düşürmek denendi ve **işe yaramadı**: MRR 0.761 → 0.754.
Kod bırakıldı ama varsayılan kapatıldı.

Genişletme slotları eklendikten sonra aynı ayar yeniden ölçüldü. 2×2 kontrol (kapsam):

| Yapılandırma | Zor | Akış | Kolay |
|--------------|-----|------|-------|
| Taban | 0.639 | 0.842 | 0.917 |
| Yalnız ağırlıklandırma | 0.639 | 0.842 | **0.900** ↓ |
| Yalnız genişletme | 0.944 | 0.883 | 0.950 |
| **İkisi birden** | 0.944 | **0.908** | **0.983** |

Ağırlıklandırma tek başına hâlâ zarar veriyor — ilk ölçüm yanlış değildi. Ama birlikte +3.3 puan
katıyor. Mekanizma: ağırlık tip parçasını geri itiyor, boşalan slotu genişletme olmadan sıradaki
(çoğu zaman yine bir tip) parça dolduruyor; genişletme varken o slot gerçekten farklı bir dosyaya
gidiyor.

Kod "başka bir repoda karşılığı olabilir" diye bırakılmıştı. Karşılığı başka repoda değil, başka
yapılandırmada çıktı. **Ölçülüp reddedilen bir fikir, koşullar değiştiğinde yeniden ölçülmeli.**

Not: "generated" dosya işareti kullanılamaz bir sinyal — bu SDK'da dosyaların %92'si öyle
işaretli, sorularımızın gerçek cevapları dahil.

### Bir dilde ölçülen başarım başka dili öngörmüyor

Araç bir dönem sekiz dil indeksliyordu (tree-sitter ile C, C++, Java, C#, Go, TypeScript,
JavaScript) ama doğruluk yalnızca Python'da ölçülmüştü. İkinci bir kod tabanında — Prometheus,
Go, 453 kod dosyası — ölçüldüğünde sonuç belirgin şekilde düştü:

| | kapsam | MRR |
|---|--------|-----|
| anthropic SDK (Python) | 93% | 0.847 |
| Prometheus (Go) | 79% | 0.674 |

> Buradaki Python satırı, zor setin **14 soruluk** hâlinde ölçüldü — o günkü sayı. Test bölmesi
> sonradan 26'ya çıkarıldığında %90 / 0.757'ye indi. Karşılaştırmanın kendisi geçerli (ikisi de
> aynı gün, aynı setle ölçüldü), ama bu satırı güncel tabloyla karıştırmayın.

Üç şey öğretti.

**İlk karşılaştırma adil değildi.** İki indeksin bileşimi çok farklıydı: anthropic SDK'sı
site-packages'tan kurulu olduğu için testler ve dokümanlar paketlenmiyor (%0); Prometheus depo
kökünden indekslendiği için indeksin **%51'i üretim kodu değildi**. Eşitlenince fark yarı yarıya
küçüldü (26 puan yerine 14) ama kaybolmadı. **Kod tabanları karşılaştırılırken indeks bileşimi
eşitlenmeli.**

**Doküman parçaları kod sonuçlarını bastırıyor.** Markdown indeksin %15'iyken sonuçların %28'ini
kaplıyordu; çıkarınca MRR 0.406 → 0.586. Test dosyaları neredeyse etkisizdi (0.406 → 0.421).
Mekanizma: sorular doğal dilde, markdown da düz metin — birbirlerine benziyorlar, ama cevaplar
kodda. Kod sorusu soruluyorsa `--no-docs` ile indekslemek gerekiyor.

**Tek kod tabanıyla görülemeyen bir hata çıktı.** Python'da dokümantasyon gövdenin *içinde*
(docstring), ama diğer yedi dilde bildirimin **üstünde** duruyor. Parça metni bildirimden
başladığı için bu yorumlar indekse hiç girmiyordu — incelenen 1500 Go fonksiyonunun 607'sinde
yorum vardı, **kayıp %100**. Düzeltildi ve getirmeyi kıpırdatmadı.

Sonuç: çok dil desteği kaldırıldı, araç yalnızca Python indeksliyor. **Ölçülmemiş bir yetenek,
savunulamayan bir iddiadır.** Kod git geçmişinde duruyor; geri getirilirse o dilde ölçülerek
getirilmeli.

Geriye kalan soru — *"aynı dilde başka bir repoda ne olur"* — Saleor ile cevaplandı: dil sabit
tutulunca sonuç taşındı (recall %100, kapsam %96), ama sıralama kalitesi düştü (MRR 0.757 → 0.573).
Yani **dil değişince başarım çöküyor, kod tabanı değişince sıralama zorlaşıyor.** İkisi ayrı
mesele.

### Aynı hata iki kez, iki farklı kılıkta

Önbellek katmanında art arda iki performans hatası çıktı ve ikisi de aynı kök sebebe dayanıyordu:
**maliyeti sabit sanılan bir işlemin döngü içinde tekrarlanması.**

Birincisi ölçüm koşusunda yakalandı: eşzamanlılık için eklenen birleştirmeli kaydetme her sorguda
48 MB'lık önbelleği baştan okuyup yazıyordu. Toplu yazmaya çevrildi (25 sorguda bir + süreç
sonunda), koşu 10 dakikadan 45 saniyeye indi.

İkincisi demo hazırlığında yakalandı ve daha sinsiydi. `codeqa ask` her çağrıda 68 saniye
sürüyordu; profil arama katmanının yalnızca 0,5 saniye harcadığını gösterdi. Geri kalanı tek bir
satırdaydı:

```python
for index, key in enumerate(data["keys"]):
    merged.setdefault(str(key), data["vectors"][index])   # ← döngü içinde
```

`np.load` bir `.npz` üzerinde tembel çalışıyor: `data["vectors"]` her erişimde 48 MB'lık diziyi
arşivden baştan açıyor. 12.205 anahtar için dizi 12.205 kez okunuyordu. Diziyi döngüden önce bir
kez okumak yetti — **tek kaydetme 60,8 saniyeden ölçülemeyecek kadar kısaya**, `ask` komutu 68
saniyeden 6,3 saniyeye indi.

İkisi de teste bağlandı. İkincisinin testi zamanlama ölçmüyor (kırılgan olurdu); dizinin arşivden
kaç kez açıldığını sayıyor.

### Netleştirme: dizin yapısı anlam taşıyorsa çalışıyor

Belirsiz sorguyu öbeklere ayırma fikri ölçüldü (`tools/netlestirme_olcumu.py`). Var olan sorular
mekanik olarak 1-2 kelimeye daraltıldı, etiketler elle konmuş `expect_files`'tan alındı. Üç sayı:
**düz** (bugünkü arama), **menü** (doğru dizin beş öbekten birinde mi), **tavan** (en iyi öbek
seçilseydi). Test bölmeleri, tek kelimelik sorgu:

| Set | n | düz | menü | tavan |
|-----|---|-----|------|-------|
| Saleor | 26 | 38% | 65% | 69% |
| Zor | 26 | 65% | 58% | 77% |
| Akış | 30 | 70% | 63% | 80% |
| Kolay | 40 | 52% | 42% | 62% |

**Tavan bir üst sınır, sistem kazancı değil** — öbeği etikete bakarak seçiyor, kullanıcı bunu
yapamaz. Tavan sekiz hücrenin sekizinde de düzü geçiyor (iki kelimelik daraltmada da), yani yön
tutarlı; §Reranking'in aksine bir sette kazanıp diğerinde kaybetmiyor. Ama güven aralıkları her
yerde örtüşüyor, o yüzden büyüklük hakkında bir şey söylenemez.

**Asıl bulgu zayıflıkta.** `menü` — arayüzün gerçekten verdiği söz — sekiz hücrenin beşinde düzün
altında, en kötüsü anthropic SDK'sında (%42 ve %45'e karşı düz %52 ve %72). Sebep görülebiliyor:
o indekste `"akış"` sorgusunun öbekleri `AsyncFilesWithStreamingResponse`,
`AsyncDreamsWithStreamingResponse` gibi **üretilmiş sarmalayıcılarla** doluyor — dizinler API
yüzeyini yansıtıyor, kavramı değil. Saleor'da ise `payment/gateways` altında
adyen/braintree/razorpay duruyor, yani gerçek alan yapısı.

Bu, [hibrit aramanın](#hibrit-arama-bedava-kazanç-değil) ve
[çok dil desteğinin](#bir-dilde-ölçülen-başarım-başka-dili-öngörmüyor) bulgusuyla aynı şekil:
**müdahale kod tabanına bağlı, ortalama almak yanıltıyor.** Özellik bu yüzden ayrı bir düğme,
varsayılan aramanın yerine geçmiyor ve arayüz öbekleri doğruluk sayısı gibi sunmuyor.

Ara adımda bir de şu ölçüldü ve reddedildi: öbeğe tıklayınca aramayı o dizine **hapsetmek**
(`search_within`). Makul duruyordu — dev'de dört soruda doğru dizin menüdeydi ama tıklayınca
dosya yine ilk 8'e girmemişti. Ölçüm tersini söyledi (dev tavan: zor %92 → %58, akış %80 → %70):
beklenen dosya çoğu zaman en iyi eşleşen dizinin *dışında* kalıyor ve süzme onu tamamen eliyor.
Sorguya sözcük eklemek elemediği için o dosyaya hâlâ ulaşabiliyor.

### Getirme ile cevap ayrı ayrı ölçülmeli, çünkü ikisi ayrı ayrı bozuluyor

İki yönde de ayrıştıkları görüldü.

**Getirme eksik, cevap doğru.** Erken ölçümlerde recall %88'ken cevap doğruluğu %100'dü. Arama
doğru parçayı ilk 8'e sokamadığı bir soruda model `search_symbol` ile kendisi bulup doğru cevap
verdi. Recall'ı tek başına kalite göstergesi saymak yanlış olurdu — tool katmanının varlık sebebi
tam olarak bu.

**Getirme iyi, cevap eksik.** Zor setin ilk hâlinde getirme kapsamı %94, cevap dosya kapsamı
%57'ydi. Aradaki 37 puan tamamen cevaplama katmanında kaybediliyordu: 29 beklenen dosyanın 12'si
**bağlamdayken** cevapta hiç geçmiyordu. Bir ihtimal daha vardı — model dosyadan söz edip
`dosya:satır` biçimini kaçırmış olabilirdi, yani sorun metrikte olabilirdi. Ölçüldü: 29 dosyanın
yalnızca 1'i öyleydi.

O 37 puanlık farkı genişletme eksenleri kapattı. Test bölmesinde iki sayı artık neredeyse eşit
(getirme %93, cevap %93). Ama iki metrik ayrı ayrı ölçülmeseydi farkın nerede olduğu hiç
görülemezdi.

## Dosya yapısı

```
codeqa/
  indexer.py     Python AST sembol çıkarıcı
  docs.py        Markdown parçalayıcı
  embeddings.py  sağlayıcı arayüzü (Voyage / Ollama / hash) + disk önbelleği
  search.py      BM25, vektör araması, RRF birleştirme, genişletme eksenleri
  facets.py      belirsiz sorguyu dizin ağacına göre öbekleme (isteğe bağlı, ölçüldü)
  rerank.py      Voyage rerank-2.5
  contextual.py  LLM ile bağlam cümlesi üretimi (ölçüldü, varsayılan kapalı)
  answer.py      Claude + tool'lar, referans doğrulama
  evaluation.py  soru seti, getirme ve cevap metrikleri
  mcp_server.py  MCP sunucusu (search_code, read_chunk, index_status)
  models.py      Chunk veri modeli + indeksin disk biçimi (read_jsonl / write_jsonl)
  projects.py    proje kaydı, tarama, maliyet tahmini, dil işareti, duman testi
  ui.py          yerel web arayüzü sunucusu (bağımlılıksız, http.server)
  ui.html        arayüz sayfası — proje ekleme ve referansa tıklayınca kaynağı açan kısım
  cli.py         komut satırı arayüzü
DEMO.md          müdüre gösterilecek akış, komutlar ve fallback yolları
eval/
  questions.json            kendi repo, 40 soru (20 dev / 20 test)
  questions_anthropic.json  SDK, 80 soru (tek konum, 40 dev / 40 test)
  questions_akis.json       SDK, 40 soru (akış, 2-4 dosya, 10 dev / 30 test)
  questions_zor.json        SDK, 38 soru (zor, 12 dev / 26 test)
  questions_saleor.json     Saleor, 40 soru (14 dev / 26 test)
  KULLANICI_DENEYI.md       değer hipotezini ölçmek için protokol
  SORU_SETI.md              yeni bir repo için soru seti yazma rehberi
tests/           239 birim testi — ağ ve API anahtarı gerektirmiyor
tools/
  komutlari_cikar.py      DEMO.md'den kopyalanabilir komut listesi üretir
  netlestirme_olcumu.py   netleştirme öbeklerini ölçer (düz / menü / tavan)
data/, runs/     indeksler, vektörler, proje kaydı, koşu kayıtları (git'e girmez)
repos/           ölçüm için klonlanan dış repolar (git'e girmez)
```

## Sıradaki adımlar

1. **Kullanıcı deneyi.** Projenin ölçülmemiş tek iddiası: *"adapte olma süresini kısaltır."*
   Getirme ve cevap doğruluğu ölçüldü, zaman kazancı ölçülmedi — çünkü tek başına ölçülemez.
   Protokol hazır: [eval/KULLANICI_DENEYI.md](eval/KULLANICI_DENEYI.md). Kritik kural: soru önce
   yazılır, sonra araca sorulur. İki kişi, yarım gün.

2. **Yeni eklenen 92 soru insan gözünden geçmeli.** Beş setin test bölmeleri LLM tarafından
   yazılmış sorularla büyütüldü. Yöntem kurallara uyduruldu (sorular kod okunmadan yazıldı,
   etiketler grep ile kondu — codeqa ile değil), ama zor sette ölçüldü: insanın yazdığı 14 soru
   %100/0.847, LLM'in yazdığı 12 soru %92/0.653 veriyor. Güven aralıkları örtüştüğü için fark
   ayırt edilemiyor, ama gerçek olabilir. Gözden geçirilirken **setin tamamı** geçirilmeli,
   sadece kaçırılanlar değil (Kural 3), ve düzeltmeler **ölçüm düzeltmesi** diye işaretlenmeli.

3. **Cevap ölçümü diğer setlere de yayılmalı.** Zor setin 26 soruluk bölmesi koşuldu
   (%100 doğruluk, %91 dosya kapsamı) ama Saleor ve akış setlerinin cevap tarafı hiç ölçülmedi.
   Saleor önemli: cevaplama katmanı ikinci kod tabanında hiç sınanmadı.

4. **Doküman ağırlıklandırmasını ölçmek.** Doküman parçalarının kod sonuçlarını bastırdığı
   görüldü (bkz. Öğrenilenler). Bu indekste doküman payı yalnızca %0,4 olduğu için etkisi
   ölçülemiyor; doküman ağırlıklı bir repoda ölçülmeli. `--no-docs` zaten var.

5. **`d36` etiketi gözden geçirilmeli.** "Zamanlanmış çalıştırmalar hangi kaynak üzerinden
   yönetiliyor" sorusunun etiketi `resources/beta/deployments.py`, ama arama
   `resources/beta/deployment_runs.py`'yi getiriyor ve ikisi de savunulabilir. Düzeltilirse
   **ölçüm düzeltmesi olarak işaretlenmeli**, sistem kazancı olarak değil.

### Denenmemiş aday: dizin genişletmesini temsil gücüne göre sıralamak

Teşhis test bölmesindeki `z24`'te yapıldı: ilk 8 sonucun tamamı tek dosyadan
(`lib/tools/mcp.py`) geliyor, aranan `lib/tools/_tool_dispatch.py` 45. sırada. `lib/tools/`
dizini ilk 14 sonucun 10'unu kaplıyor ama dizin slotları `types/beta/`'ya gitti — çünkü
genişletme sıralamada **önce rastladığı** dizini seçiyor, en güçlü temsil edileni değil.

Denenmedi, çünkü bedeli var: `z24` test bölmesinde ve ona göre bir şey ayarlanırsa soru dev'e
taşınmalı, rapor edilen sayı kalan 13'ten alınmalı. Dev'den çıkan üç mekanizmanın üçü de test'te
sıfır verdikten sonra, tek soruya bakarak tasarlanacak dördüncüsü için bir test sorusu harcamak
iyi bir alışveriş görünmedi.

### Kapanan maddeler

- **İkinci bir Python kod tabanı** — Saleor eklendi (açık kaynak e-ticaret, Django; 1.060 dosya,
  12.076 parça). İndeks bileşimi anthropic SDK'sıyla eşitlendi. Test bölmesinde %100 recall,
  %96 kapsam, MRR 0.573. "Bütün ölçümler tek repodan" sorunu kapandı.
- **Test bölmeleri büyütülmeli** — 44'ten 142 soruya çıktı. Zor sette bir soru artık %7 yerine
  %4 ediyor ve güven aralığı 22 puandan 13'e indi. Sayılar düştü: araç değişmedi, eski sayılar
  küçük örneklem iyimserliğiydi.
- **Akış kapsamını %80'in üzerine çıkarmak** — test bölmesinde %87 (30 soruluk hâlinde).
- **Cevap tarafını zor sette ölçmek** — yapıldı; 26 soruluk güncel test bölmesinde %100
  doğruluk, %91 dosya kapsamı, 0 uydurma referans.
- **`types/` gürültüsü** — 60 soruluk sette bulunamayan dört sorunun üçü çeşitlilik slotlarıyla
  çözüldü. İlginç olan, `types/` parçalarının payının **artmış** olması (%15 → %21): sorun tip
  tanımlarının varlığı değil, gerçek cevaba yer kalmamasıymış.
- **Demo** — akış hazır: [DEMO.md](DEMO.md). Komutların hepsi çalıştırılarak doğrulandı,
  MCP protokol düzeyinde (`initialize` → `tools/list` → `tools/call`) sınandı, fallback yolları
  denendi.
