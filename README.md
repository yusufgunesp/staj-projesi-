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

**Ölçülen sonuç:** `anthropic` Python SDK'sında (1097 dosya, üç soru seti) en zor setin
dokunulmamış test bölmesinde **cevap doğruluğu %100, dosya kapsamı %93**; uydurma referans hiçbir
koşuda görülmedi.

**Ama ikinci bir kod tabanında sayılar taşınmadı.** Prometheus'ta (Go, 453 kod dosyası) kapsam
%79, MRR 0.674 — Python'daki %93 ve 0.847'nin belirgin şekilde altında. Bir repoda ölçülen
başarım başka bir repoyu öngörmüyor; bu araç bir müşteri projesinde kullanılacaksa **o projede
ayrıca ölçülmeli.** Ayrıntı [Sonuçlar](#sonuçlar) bölümünde.

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

> ⚠️ **Go ölçüldü ve Python'dan kötü çıktı: kapsam %93 yerine %79.** Farkın bir kısmı indeks
> bileşiminden geliyordu (doküman parçaları kod sonuçlarını bastırıyor — `--no-docs` ile
> ölçülmeli), kalanının sebebi bilinmiyor.
> Kalan altı dil (C, C++, Java, C#, TypeScript, JavaScript) için yalnızca sembol çıkarma birim
> testlerle doğrulandı; uçtan uca doğruluk ölçülmedi. Bu araç bir müşteri projesinde
> kullanılacaksa, **o projenin dilinde ve kod tabanında ayrıca ölçülmesi gerekiyor** — iki
> kod tabanının sonucu birbirini öngörmedi.

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

Kurulum: `anthropic` Python SDK'sı — 1097 dosya, 4311 parça. Üç soru seti, hepsinde beklenen
dosyalar kod içinde aranarak doğrulandı:

| Set | Soru | Tipi |
|-----|------|------|
| `questions_anthropic.json` | 60 (40 dev / 20 test) | Tek konum — "X nerede" |
| `questions_akis.json` | 20 (10 dev / 10 test) | Akış — cevap 2-4 dosyada |
| `questions_zor.json` | 26 (12 dev / 14 test) | Zor — cevap 2-3 dosyada, aralarında birbirine çok benzeyen varyantlar |
| `questions_prometheus.json` | 24 (12 dev / 12 test) | **İkinci kod tabanı** — Go, farklı alan |
| `questions_prometheus_changelog.json` | 6 (test) | Konuları bakım ekibinin CHANGELOG'undan alındı |

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

**Cevap (hepsi `claude-haiku-4-5`, tek fark bağlam):**

| Set | Genişletmeler | parça | doğruluk | **dosya kapsamı** | uydurma | referans/cevap |
|-----|---------------|-------|----------|-------------------|---------|----------------|
| Zor (12, bölünmemiş hâli) | kapalı | 8 | 75% | 46% | 0 | 1.3 |
| Zor (12, bölünmemiş hâli) | açık | 15 | 100% | 57% | 0 | 2.1 |
| Akış (20, doğrulama) | kapalı | 8 | 90% | 79% | 1 | 2.1 |
| Akış (20, doğrulama) | açık | 15 | 95% | 84% | 0 | 2.8 |
| **Zor — test (14)** | açık | 15 | **100%** | **93%**\* | **0** | 2.7 |

Getirme metrikleri tek başına yeterli değil: kapsam, eklenen slot sayısıyla matematiksel olarak
**düşemez**, yani her ekleme kendini haklı çıkarır. Kararı cevap ölçümü verdi. Son sütun o
ölçümün kontrolü — kapsam artarken referans sayısı patlamadıysa model her şeyi saymıyor, hedefe
daha çok isabet ediyor demektir.

İlk iki satır tek başına kanıt değildi: genişletme eksenleri zor setin hatalarına bakılarak
tasarlanmıştı (dizin kardeşi `z05`/`z06`'ya, import bağı `z03`'e). Doğrulama, tasarım sırasında
hiç bakılmayan akış setinde yapıldı ve aynı yönü verdi: doğruluk +5, kapsam +5, uydurma referans
1'den 0'a.

Son satır projenin en temiz sayısı: hiçbir ayarın görmediği 14 soru. Cevap kapsamının getirme
kapsamına eşit çıkması dikkat çekici — cevaplama katmanı getirmenin tamamını kullanıyor. Zor setin
ilk hâlinde bu iki sayı arasında 37 puan fark vardı.

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

**İkinci kod tabanı: Prometheus (Go, 453 kod dosyası)**

Bütün ölçümler tek repodan geliyordu. İkinci bir kod tabanı eklendi — farklı dil, farklı alan
(zaman serisi veritabanı), daha büyük indeks. 24 soru, dev/test ayrımı baştan kurulu.

İlk karşılaştırma **adil değildi** ve bunu ancak sebebi ararken fark ettim. İki indeksin bileşimi
çok farklı:

| İndeks | Toplam parça | Doküman | Test dosyası |
|--------|--------------|---------|--------------|
| anthropic (Python) | 4.311 | %0 | %0 |
| Prometheus (Go) | 15.197 | %15 | %36 |

anthropic SDK'sı site-packages'tan kurulu olduğu için testler ve dokümanlar paketlenmiyor;
Prometheus depo kökünden indekslendiği için **indeksin %51'i üretim kodu değil.** İkisini
eşdeğermiş gibi karşılaştırmak, aracı Go'da olduğundan kötü göstermek demekti.

| Set (test bölmesi) | recall@8 | kapsam | MRR |
|--------------------|----------|--------|-----|
| anthropic SDK (Python), zor | 100% | **93%** | **0.847** |
| Prometheus (Go), ham indeks | 92% | 67% | 0.406 |
| **Prometheus (Go), yalnızca kod** | **100%** | **79%** | **0.674** |

Adil karşılaştırmada fark yarı yarıya küçülüyor: kapsamda 26 puan yerine 14, MRR'da 0.44 yerine
0.17. **Ama fark hâlâ duruyor** ve sebebi bilinmiyor — kalan adaylar indeks boyutu (8.776 vs
4.311 parça), alanın yapısı ve soru zorluğu.

**Baskın gürültü kaynağı markdown, test dosyaları değil**

| İndeks bileşimi | test kapsam | test MRR |
|-----------------|-------------|----------|
| Tam | 67% | 0.406 |
| Markdown çıkarılmış | 71% | 0.586 |
| Test dosyaları çıkarılmış | 67% | 0.421 |
| İkisi de çıkarılmış | **79%** | **0.674** |

Markdown tek başına MRR'ı 0.18 yükseltiyor, test dosyaları neredeyse hiçbir şey yapmıyor.
Mekanizma anlaşılır: sorular Türkçe doğal dilde, markdown da düz metin — birbirlerine benziyorlar.
Ama cevaplar kodda. Doküman parçaları indeksin %15'iyken sonuçların %28'ini kaplıyor, iki kat
fazla temsil ediliyor.

Pratik sonuç: **kod sorusu soruluyorsa doküman indekslenmemeli** (`codeqa index --no-docs`).
Varsayılan değiştirilmedi, çünkü bu bulguyu ararken test bölmesinin sayılarına bakıldı; ayarı
şimdi ona göre değiştirmek, ölçülen sette ayar yapmak olur. Üçüncü bir kod tabanında
doğrulanmalı.

**Yol boyunca bulunan hata — ve düzeltilmesinin işe yaramaması**

Farkın sebebini ararken ayrı bir kusur da çıktı: Python'da dokümantasyon gövdenin *içinde*
(docstring), ama Go, C, C++, Java, C#, TypeScript ve JavaScript'te bildirimin **üstünde** duruyor.
Parça metni bildirimden başladığı için bu yorumlar indekse hiç girmiyordu.

Ölçülen kayıp: Prometheus'ta incelenen 1500 Go fonksiyonunun 607'sinin üstünde yorum var ve
**hiçbiri parçaya girmiyordu — kayıp %100.** Yani araç, desteklediğini söylediği sekiz dilin
yedisinde dokümantasyonu görmüyordu. Tek kod tabanıyla ölçüldüğü sürece bu görülemezdi, çünkü
Python o yolu kullanmıyor.

Düzeltildi (Go'da docstring taşıyan parça oranı %0 → %36, satır aralıkları hâlâ doğru) ve
**getirme hiç kıpırdamadı.** Düzeltme yine de tutuldu, ama gerekçesi ölçüm değil doğruluk: var
olan dokümantasyonu indekslememek zaten yanlıştı.

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
DEMO.md          müdüre gösterilecek akış, komutlar ve fallback yolları
eval/
  questions.json            kendi repo, 20 soru
  questions_anthropic.json  SDK, 60 soru (tek konum)
  questions_akis.json       SDK, 20 soru (akış, 2-4 dosya)
  questions_zor.json        SDK, 26 soru (12 dev / 14 test)
tests/           211 birim testi — ağ ve API anahtarı gerektirmiyor
data/, runs/     üretilen çıktılar (git'e girmez)
```

## Sıradaki adımlar

1. **Doküman ağırlıklandırmasını üçüncü bir kod tabanında doğrulamak.** Doküman parçalarının kod
   sonuçlarını bastırdığı ölçüldü (markdown indeksin %15'i ama sonuçların %28'i; çıkarılınca MRR
   0.406 → 0.586). Ama bu bulgu test bölmesinin sayılarına bakılarak elde edildi, dolayısıyla
   varsayılanı ona göre değiştirmek ölçülen sette ayar yapmak olur. Temiz bir sette
   doğrulanmalı — ya doküman parçalarına ağırlık verilerek ya da `--no-docs` varsayılan yapılarak.

2. **Kalan Go açığının sebebini bulmak.** Bileşim düzeltildikten sonra bile kapsam %93'e karşı
   %79. Kalan adaylar: indeks boyutu (8.776 vs 4.311 parça), alanın yapısı, soru zorluğu.

3. **Zor setin test bölmesi büyütülmeli.** Şu an 14 soru; bir soru ~3.5 puan ediyor, yani tek bir
   soruluk oynama gürültü seviyesinde. Ayrıca her yeni deneme test'ten soru harcıyor (aşağıya
   bakın) — bölme bir bütçe ve şu an dar.

4. **Üçüncü bir kod tabanı.** İki nokta bir eğri çizmiyor: Python iyi, Go zayıf çıktı ama
   aradaki farkın dilden mi boyuttan mı alandan mı geldiği belli değil. Üçüncü bir repo (küçük bir
   Go projesi ya da büyük bir Python projesi) değişkenleri ayırmaya yarar.

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

- **Akış kapsamını %80'in üzerine çıkarmak** — test bölmesinde %91.
- **Cevap tarafını zor sette ölçmek** — yapıldı, test bölmesinde %93 dosya kapsamı.
- **`types/` gürültüsü** — 60 soruluk sette bulunamayan dört sorunun üçü çeşitlilik slotlarıyla
  çözüldü. İlginç olan, `types/` parçalarının payının **artmış** olması (%15 → %21): sorun tip
  tanımlarının varlığı değil, gerçek cevaba yer kalmamasıymış.
- **Demo** — akış hazır: [DEMO.md](DEMO.md). Komutların hepsi çalıştırılarak doğrulandı,
  MCP protokol düzeyinde (`initialize` → `tools/list` → `tools/call`) sınandı, fallback yolları
  denendi.
