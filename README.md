# Kod Tabanı Soru-Cevap Asistanı

Bir kod tabanını indeksleyip doğal dildeki sorulara **dosya ve satır referanslı** cevap veren araç.
Amaç, yeni bir projeye adapte olma süresini kısaltmak: her cevap kaynak gösterdiği için doğruluğu
anında kontrol edilebiliyor.

```
$ codeqa ask "istek kaç kez yeniden deneniyor?"

Varsayılan 2 (`_constants.py:12`). Karar `_base_client.py:842`'deki
_should_retry'da veriliyor: 408, 409, 429 ve 5xx yeniden deneniyor.
Sunucu retry-after başlığı gönderirse ona uyuluyor (`_base_client.py:793`).
```

**Ölçülen sonuç:** 1097 dosyalık bir üçüncü taraf repoda üç ayrı soru seti üzerinde. Tek konumlu
sorularda cevap doğruluğu %100; akış sorularında %95 doğruluk / %84 dosya kapsamı; cevabı 3-5
dosyaya yayılan zor sette %100 doğruluk / %57 dosya kapsamı. Uydurma referans hiçbir koşuda
görülmedi.

## İçindekiler

- [Kurulum](#kurulum) · [Kullanım](#kullanım)
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
| 4 | Claude Code / MCP entegrasyonu, çok dil desteği | Tamam — demo sırada |

## Kurulum

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env      # anahtarları .env dosyasına yazın, .env.example'a değil
```

| Anahtar | Ne için | Zorunlu mu |
|---------|---------|------------|
| `ANTHROPIC_API_KEY` | Cevaplama ve bağlam üretimi | Cevap almak için evet |
| `VOYAGE_API_KEY` | Embedding (`--provider voyage`) | Hayır — Ollama ya da `hash` de var |

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
| `eval` | Soru seti üzerinde doğruluk ölçer |
| `contextualize` | Parçalara LLM ile bağlam cümlesi ekler |
| `serve` | MCP sunucusu olarak çalışır (Claude Code entegrasyonu) |
| `stats` / `grep` | İndeksi inceleme araçları |

**Desteklenen diller:** Python, C, C++, Java, C#, Go, TypeScript, JavaScript. Python yerleşik
`ast` ile, diğerleri tree-sitter grameriyle ayrıştırılıyor.

> ⚠️ **Doğruluk yalnızca Python üzerinde ölçüldü.** Diğer diller için sembol çıkarma birim
> testlerle doğrulandı (her dil için sınıf, metot, fonksiyon, import ve satır aralıkları), ama
> uçtan uca arama/cevap doğruluğu ölçülmedi. "Java'da da %100 doğru" demek için o dilde bir soru
> seti hazırlanması gerekiyor.

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

Diller arasında düğüm adları farklı ama yapı aynı: fonksiyonlar, kapsayıcılar (sınıf/struct/
arayüz) ve dosya başlığı. `languages.py` bu farkları tek yerde topluyor, çıkarma mantığı ortak.
Go metotları alıcı tipine bağlanıyor (`OrderService.Create`), C'deki isimsiz struct'lar adını
saran `typedef`'ten alıyor.

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
ve satır sınırına göre kontrol ediliyor, tutmayanlar işaretleniyor. Kısaltılmış yollar
(`models.py` → `codeqa/models.py`) indekste tek eşleşme varsa kabul ediliyor; birden fazla
eşleşme varsa cevabın kendi içinde tam yazılmış yollar bağlam olarak kullanılıyor.

**Prompt caching açık.** Model her tool turunda geçmişi yeniden gönderiyor. Ölçülen fark: aynı
soruda 42.000 taze girdi token'ı yerine 8 taze + 18.500 önbellekten.

**Denenip geri alınan: prompt'a eksiksizlik kuralları.** Zor sette şu görüldü: 29 beklenen
dosyanın 12'si bağlamdayken cevapta hiç geçmiyordu. Prompt'a üç kural eklendi (mekanizma birden
çok dosyaya yayılıyorsa hepsini referansla, sync/async gibi varyantları da say, bitirmeden önce
verilen parçaları gözden geçir) ve zor sette dosya kapsamı %57 → %72 çıktı.

Kurallar o setin hatalarına bakılarak yazılmıştı — birinci kuraldaki örnek `z01`'in, ikincisi
`z10`'un hata kalıbıydı. Genellenip genellenmediği, prompt'un hiç görmediği akış setinde ölçüldü:

| Akış seti (20 soru) | doğruluk | dosya kapsamı | referans/cevap |
|---------------------|----------|---------------|----------------|
| Eski prompt | 95% | 84% | 2.8 |
| Yeni prompt | 95% | **84%** | 3.5 |

**Kapsam birebir aynı, referans sayısı %25 arttı.** Yani kazanç yok, gürültü var. "Akış seti
tavana yakın olduğu için fark gösteremedi" savunması tutmuyor: %84'ten %100'e 16 puanlık alan
vardı ve kuralların hiçbiri o alanı kullanamadı.

Değişiklik geri alındı. Zor setteki %15'in ne kadarı aşırı uyum, ne kadarı "kapsam düşükken işe
yarıyor" ayrımı bu veriyle yapılamıyor — ayırmak için prompt'un görmediği **ve kapsamı düşük**
üçüncü bir set gerekiyor.

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

Kurulum: `anthropic` Python SDK'sı — 1097 dosya, 4311 parça. Üç soru seti, hepsinde beklenen
dosyalar kod içinde aranarak doğrulandı:

| Set | Soru | Tipi |
|-----|------|------|
| `questions_anthropic.json` | 60 (40 dev / 20 test) | Tek konum — "X nerede" |
| `questions_akis.json` | 20 (10 dev / 10 test) | Akış — cevap 2-4 dosyada |
| `questions_zor.json` | 26 (12 dev / 14 test) | Zor — cevap 2-3 dosyada, aralarında birbirine çok benzeyen varyantlar |

Zor setin bölünmesi özel: **`z01`-`z12` dev, `z13`-`z26` test.** İlk 12 soru kirlenmiş sayılıyor
çünkü genişletme eksenleri onların hatalarına bakılarak tasarlandı. Son 14 soru hiçbir ayara
bakılmadan yazıldı; rapor edilecek sayı oradan alınır.

**Getirme (varsayılan yapılandırma, voyage + vektör, k=8):**

| Set | recall@8 | **kapsam** | MRR | modele giden parça |
|-----|----------|------------|-----|--------------------|
| Kolay (60) | 98% | **98%** | 0.735 | 15.5 |
| Akış (20) | 100% | **91%** | 0.847 | 15.3 |
| Zor — dev (12) | 100% | 94% | 0.694 | 15.4 |
| **Zor — test (14)** | **100%** | **93%**\* | **0.847** | 15.4 |

Çok dosyalı sorularda asıl ölçüt **kapsam**, recall değil: "en az bir beklenen dosyayı bulduysan
başarılı" saymak kolay — üç dosyadan birini bulmak yetiyor.

**Cevap (zor set, 12 soru, hepsi `claude-haiku-4-5`):**

**Zor set (12 soru, o zaman bölünmemişti):**

| Yapılandırma | parça | doğruluk | **dosya kapsamı** | uydurma | referans/cevap |
|--------------|-------|----------|-------------------|---------|----------------|
| Genişletmeler kapalı | 8 | 75% | 46% | 0 | 1.3 |
| **Genişletmeler açık** | 15 | **100%** | **57%** | 0 | 2.1 |

Bu ölçüm tek başına yeterli değildi: dizin kardeşi ekseni `z05`/`z06`'nın, import bağı ekseni
`z03`'ün hatalarına bakılarak tasarlanmıştı — yani ölçüldüğü set tasarım sırasında görülmüştü.
Doğrulama, tasarım sırasında hiç bakılmayan akış setinde yapıldı:

**Akış seti (20 soru, doğrulama):**

| Yapılandırma | getirme kapsamı | doğruluk | **dosya kapsamı** | uydurma | referans/cevap |
|--------------|-----------------|----------|-------------------|---------|----------------|
| Genişletmeler kapalı | 84% | 90% | 79% | 1 | 2.1 |
| **Genişletmeler açık** | 91% | **95%** | **84%** | **0** | 2.8 |

Görülmemiş sette de aynı yön: doğruluk +5, kapsam +5, uydurma referans 1'den 0'a. Aynı gün
denenen prompt değişikliği bu sınavı geçemedi (aşağıda); genişletmeler geçti.

**Zor set — test bölmesi (14 soru, hiçbir ayarın görmediği veri):**

| Metrik | Sonuç |
|--------|-------|
| Getirme kapsamı | 93%\* |
| Cevap doğruluğu | **100%** |
| **Cevap dosya kapsamı** | **93%**\* |
| Uydurma referans | 0 |
| Referans/cevap | 2.7 |

Cevap kapsamının getirme kapsamına eşit çıkması dikkat çekici: cevaplama katmanı getirmenin
tamamını kullanıyor. Eski zor sette bu iki sayı arasında 37 puan fark vardı (getirme %94, cevap
%57); aradaki farkı kapatan şey genişletmeler oldu.

\* **Ölçüm düzeltmesi içeriyor, sistem kazancı değil.** İlk koşuda iki sayı da %86/%89'du. Dört
kısmi hatanın ikisinde etiketin fazla cömert olduğu görüldü: `z13` arşiv çıkarma güvenliğini
soruyor ama etikette `agent_toolset.py` da vardı — o dosya `_within`'i kendi yol kısıtlaması için
kullanıyor, arşiv güvenliğinin tamamı `_skills.py`'de. `z23` çakışmanın nerede yakalandığını
soruyor ama etikette `_exceptions.py` vardı — orada yalnızca taban sınıf `AnthropicError` duruyor.

Tek yönlü düzeltme olmaması için **on dört sorunun tamamı** aynı titizlikle gözden geçirildi,
yalnızca sistemin kaçırdıkları değil. Kalan on ikisinde etiketler yerinde kaldı; `z15`'te
`PageInfo` gerçekten `_base_client.py`'de ve `z24`'teki kayıp gerçek bir getirme hatası.

Getirme metriği bu çalışmada tek başına yeterli değil: kapsam, eklenen slot sayısıyla matematiksel
olarak **düşemez**, yani her ekleme kendini haklı çıkarır. Kararı cevap ölçümü verdi. Son sütun o
ölçümün kontrolü — kapsam artarken referans sayısı patlamadıysa model her şeyi saymıyor, hedefe
daha çok isabet ediyor demektir.

Model seçimi hakkında: aynı ölçüm `claude-opus-5` ile %82 dosya kapsamı vermişti, yani Haiku'nun
%57'si mutlak olarak daha düşük. Ama **ölçüm aleti olarak Haiku daha iyi çıktı** — Opus tavana
yakın çalıştığı için kolları ayırt edemiyordu. Maliyet farkı da var: iki Haiku koşusu $0.55, tek
Opus koşusu $10.75.

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

### Getirme mükemmel olmak zorunda değil

Getirme recall'ı %88 ama cevap doğruluğu %100. Bir soruda arama doğru parçayı ilk 8'e sokamadı,
model `search_symbol` ile kendisi bulup doğru cevap verdi. **Recall'ı tek başına kalite göstergesi
saymak yanlış olurdu** — tool katmanının varlık sebebi tam olarak bu.

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

### Reranking ile ağırlık ayarı birbirinin yerine geçiyor

Reranking MRR'ı 0.702 → 0.750 çıkardı. RRF ağırlıklarını ayarlamak (bedava, API çağrısı yok)
0.738'e çıkardı. İkisi birlikte yine 0.750 — reranker sıralamayı baştan kurduğu için ağırlığın
etkisini siliyor.

### Kendi metriğini ödüllendiren bir iyileştirme, iyileştirme değildir

Akış kapsamını artırmak için önce **sert kota** denendi: ilk k içinde dosya başına en fazla 1
parça. Kapsam anında yükseldi (akış 0.750 → 0.867, kolay 0.925 → 0.950) ve karar verilebilirdi.

Verilmedi, çünkü kapsam metriği dosya çeşitliliğini ödüllendiriyor ve kota tam olarak çeşitliliği
artırıyor — müdahale kendi ölçütünü tanım gereği memnun ediyordu. Döngüyü kıracak bağımsız bir
ölçüte bakıldı: **sembol isabeti** (doğru dosyanın doğru fonksiyonu geldi mi). Orada sonuç
**0.778 → 0.444** düştü.

Sebep tek tek incelendi: iki ilgili parça gerçekten aynı dosyada olabiliyor — sync/async ikizleri,
decoder + accumulator çifti — ve sert kota bunlardan birini kesiyordu. Bir vaka ise ölçüm
artefaktıydı: beklenen `Anthropic.copy`, kota `AsyncAnthropic.copy`'yi tutmuştu; cevap aynı,
etiket ikizlerden birini yazdığı için hata sayılıyordu.

Yumuşak ceza da çare olmadı. RRF skorları `1/(60+sıra)` biçiminde olduğu için fazla sıkışık:
0.8'in altındaki her çarpan pratikte sert kotaya dönüşüyor, üstündeki hiçbir şey yapmıyor.

Çalışan yaklaşım **hiçbir şeyi elemeyen** genişletme oldu: ilk k dokunulmadan kalıyor, arkasına
yeni dosyalar ekleniyor. Kritik kontrol, kazancın "daha çok parça verdik"ten gelmediğini
göstermek oldu — eşit bütçede (12 parça) düz top-12 akış kapsamını 0.750'de bırakıyor, genişletme
0.833'e çıkarıyor.

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

### Dev'den çıkan iki mekanizma da test'te sıfır verdi

Zor set dev/test bölündükten sonra ilk deneme: dev hataları incelendi, iki genel mekanizma
tasarlandı, dev'de ölçüldü, sonra test'te ölçüldü.

| | dev kapsam | dev MRR | **test kapsam** | **test MRR** | parça |
|---|-----------|---------|-----------------|--------------|-------|
| Önceki hâl | 0.944 | 0.694 | 0.893 | 0.847 | 15.1 |
| + yeniden dışa aktarım kabuklarını geri itme | 0.944 | **0.736** | 0.893 | 0.847 | 15.0 |
| + import bağının ileri yönü | **1.000** | 0.736 | 0.893 | 0.847 | 17.0 |

Dev'de kapsam +5.6 puan ve MRR +0.042; test'te **ikisi de tam sıfır**, üstelik ileri yön soru
başına 2 parça daha götürüyor.

İkisi de makul fikirlerdi. Yeniden dışa aktarım kuralı, "metotsuz sınıf alan listesidir"
kuralının modül karşılığı: `MessageStreamEvent = RawMessageStreamEvent` hiçbir değer taşımıyor,
`DEFAULT_MAX_RETRIES = 2` taşıyor. İleri yön de gerçek bir boşluğu kapatıyordu — `_fallbacks.py`
ilk sıradayken onun import ettiği `_middleware.py` ilk 200'de bile yoktu.

Ama ikisi de dev'in hatalarına bakılarak tasarlandı ve tam olarak o hataları düzelttiler. Test
bölmesi olmasaydı ikisi de "kazanç" diye rapor edilecekti — nitekim aynı gün prompt değişikliğinde
tam olarak bu olmuştu.

Kod duruyor, varsayılan kapalı (`DEMOTE_REEXPORT_MODULES`, `REFERENCE_FORWARD_SLOTS`). Bu
projede daha önce bir kez, ölçülüp kapatılan bir ayarın koşullar değişince yeniden ölçülüp
açıldığı oldu.

**Asıl ders bölmenin kendisinde:** üç denemeden üçü dev'de kazandı, üçü de test'te kaybetti.
Bölme olmasaydı üçü de rapora "iyileştirme" diye girecekti.

### Getirmeyi düzeltmek her zaman cevabı düzeltmiyor

Zor sette getirme kapsamı %94, cevap dosya kapsamı %57'ydi. Aradaki 37 puan tamamen cevaplama
katmanında kaybediliyordu: 29 beklenen dosyanın 12'si **bağlamdayken** cevapta hiç geçmiyordu.

Bir ihtimal daha vardı — model dosyadan söz edip `dosya:satır` biçimini kaçırmış olabilirdi, yani
sorun metrikte olabilirdi. Ölçüldü: 29 dosyanın yalnızca 1'i öyleydi. Yani sorun biçim değil,
eksiklikti ve çözümü prompt'taydı (bkz. [Cevaplama](#cevaplama)).

Çıkarım: getirme ve cevap ayrı ayrı ölçülmeseydi bu 37 puanın nerede kaybolduğu görülemezdi.

## Dosya yapısı

```
codeqa/
  models.py      Chunk veri modeli
  indexer.py     Python AST sembol çıkarıcı
  languages.py   tree-sitter ile C/C++/Java/C#/Go/TS/JS sembol çıkarma
  docs.py        Markdown parçalayıcı
  embeddings.py  sağlayıcı arayüzü (Voyage / Ollama / hash) + disk önbelleği
  search.py      BM25, vektör araması, RRF birleştirme, genişletme eksenleri
  rerank.py      Voyage rerank-2.5
  contextual.py  LLM ile bağlam cümlesi üretimi (ölçüldü, varsayılan kapalı)
  answer.py      Claude + tool'lar, referans doğrulama
  evaluation.py  soru seti, getirme ve cevap metrikleri
  mcp_server.py  MCP sunucusu (search_code, read_chunk, index_status)
  cli.py         komut satırı arayüzü
eval/
  questions.json            kendi repo, 20 soru
  questions_anthropic.json  SDK, 60 soru (tek konum)
  questions_akis.json       SDK, 20 soru (akış, 2-4 dosya)
  questions_zor.json        SDK, 12 soru (zor, 3-5 dosya + benzer varyantlar)
tests/           208 birim testi — ağ ve API anahtarı gerektirmiyor
data/, runs/     üretilen çıktılar (git'e girmez)
```

## Sıradaki adımlar

1. **Prompt düzeltmesini görmediği bir sette sınamak** — eksiksizlik kuralları zor setin
   hatalarına bakılarak yazıldı, yani o sete ayar yapıldı. Akış setinde eski/yeni prompt yan yana
   koşulmalı (~$0.90). Sonuç genelleniyorsa %72 gerçek; genellemiyorsa raporda öyle yazılmalı.
2. **Çok dilli ölçüm** — sekiz dil destekleniyor, doğruluk yalnızca Python'da ölçüldü. Java ya da
   C++ bir repoda soru seti gerekiyor. Önce müşteri projelerinin ağırlıklı dili öğrenilmeli.
3. **Demo** — müdüre gösterilecek akış.
4. **`d36` etiketi gözden geçirilmeli** — "zamanlanmış çalıştırmalar hangi kaynak üzerinden
   yönetiliyor" sorusunun etiketi `resources/beta/deployments.py`, ama arama
   `resources/beta/deployment_runs.py`'yi getiriyor ve ikisi de savunulabilir. Etiket
   düzeltilirse **ölçüm düzeltmesi olarak işaretlenmeli**, sistem kazancı olarak değil.

**Kalan açık:** zor sette 8 eksik dosyanın 6'sı bağlamda olduğu hâlde referans verilmiyor, 2'sini
getirme kaçırıyor. Yani kaldıraç hâlâ cevaplama tarafında — ama prompt'u aynı sete göre bir kez
daha ayarlamak ölçümü tamamen anlamsızlaştırır. Önce 1. madde.
