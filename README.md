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

**Ölçülen sonuç:** 1097 dosyalık bir üçüncü taraf repoda, 20 soruluk ayrı bir test setinde
**cevap doğruluğu %100**, uydurma referans yok.

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

Her parçanın önüne konum etiketi ekleniyor (dosya yolu, nitelenmiş ad, tür) ve embedding'e parça
bu etiketle gidiyor. Kullanıcıya sadece kodun kendisi gösteriliyor.

### Arama

İki yöntem: **BM25** tam isim eşleşmesinde, **vektör araması** dolaylı anlatımda iyi. Birleştirme
RRF ile — skorlar değil sıralamalar toplanıyor, böylece iki yöntemin farklı ölçekleri normalize
edilmek zorunda kalmıyor.

Hangisinin kazandığı repoya bağlı; [Ölçüm](#ölçüm) bölümüne bakın. `--mode` ile seçiliyor.

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

Kurulum: `anthropic` Python SDK'sı — 1097 dosya, 4311 parça. 60 soru (40 dev / 20 test), her
sorunun cevabının hangi dosyada olduğu elle işaretlenmiş.

**Cevap doğruluğu (test bölmesi, 20 soru):**

| Metrik | Sonuç |
|--------|-------|
| Doğru cevap oranı | **100%** |
| Beklenen dosya kapsamı | 100% |
| Uydurma referans | 0 |

**Akış soruları (20 soru, çok dosyalı):**

Kolay setin tamamı "X nerede" tipindeydi ve cevap doğruluğu %100'e vurdu — yani iyileştirme
ölçülemez hâle geldi. Projenin asıl vaadi ise akış takibi ("nerede başlıyor, nereye gidiyor"),
ki o hiç ölçülmemişti. `eval/questions_akis.json` bunu ölçüyor: her sorunun cevabı 2-4 dosyaya
yayılıyor.

| Set | recall@8 | MRR | **kapsam** |
|-----|----------|-----|------------|
| Kolay (tek konum, 60 soru) | 93% | 0.761 | 92% |
| **Zor (akış, 20 soru)** | **100%** | **0.846** | **80%** |

Zor sette recall'ın daha yüksek çıkması iyi haber değil — **metriğin yanlış olduğunu gösteriyor.**
Çok dosyalı soruda "en az bir beklenen dosyayı bulduysan başarılı" saymak kolay: üç dosyadan
birini bulmak yetiyor. Asıl ölçüt **kapsam** ve orada %92'den %80'e düşüyor.

Yani araç akış sorularında da doğru yere gidiyor ama zincirin tamamını getiremiyor: ortalama her
beş dosyadan biri ilk 8'in dışında kalıyor. İyileştirme çalışmasının ölçüleceği yer burası.

**Çeşitlilik slotları (test bölmesi, hiç ayar yapılmamış veri):**

Teşhis: ilk 8 sonuçta ortalama yalnızca **3,1 farklı dosya** vardı. 160 slotun 98'i zaten listede
olan bir dosyanın tekrarıydı — arama doğru bölgeyi buluyor, sonra o bölgeyi tekrar tekrar
getiriyor ve zincirin ikinci halkasına yer kalmıyordu. Çözüm ilk k sonucun arkasına henüz temsil
edilmemiş dosyaların en iyi parçasını eklemek (`DIVERSITY_SLOTS`, varsayılan 4).

| Set (test bölmesi) | | recall@8 | MRR | **kapsam** |
|--------------------|---|----------|-----|------------|
| Akış (10 soru) | önce | 100% | 0.914 | 85% |
| Akış (10 soru) | **sonra** | 100% | 0.914 | **90%** |
| Kolay (20 soru) | önce | 90% | 0.850 | 90% |
| Kolay (20 soru) | **sonra** | **100%** | **0.860** | **100%** |

Ayar yalnızca dev bölmesinde seçildi, tablodaki sayılar hiç dokunulmamış test bölmesinden.
Hiçbir metrik gerilemedi. Koşular: `runs/*-test-div{0,4}.json`.

**Getirme (60 soru, saf vektör):**

| Aşama | recall@8 | MRR |
|-------|----------|-----|
| Başlangıç | 87% | 0.714 |
| + modül sabitleri indekslendi | 88% | 0.727 |
| + soru etiketleri düzeltildi\* | 93% | 0.761 |

\* Bu satır sistem iyileşmesi değil, ölçüm düzeltmesi: cevabı birden fazla dosyada olan sorulara
tek dosya yazmışız, araç doğru cevap getirdiği hâlde "hata" sayılıyormuş.

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

### Denenip kapatılan: tip-only parçaları geri plana atmak

İndeksin %42'si üretilmiş tip tanımı ve bunlar bazı sorularda gerçek cevabı ilk 8'in dışına
itiyor. Metotsuz sınıfların ağırlığını düşürmek mantıklı görünüyordu ama **MRR'ı 0.761'den
0.754'e düşürdü.** Kod duruyor, varsayılan kapalı (`weigh_chunks=True` ile açılıyor).

Not: "generated" dosya işareti kullanılamaz bir sinyal — bu SDK'da dosyaların %92'si öyle
işaretli, sorularımızın gerçek cevapları dahil.

## Dosya yapısı

```
codeqa/
  models.py      Chunk veri modeli
  indexer.py     Python AST sembol çıkarıcı
  docs.py        Markdown parçalayıcı
  embeddings.py  sağlayıcı arayüzü (Voyage / Ollama / hash) + disk önbelleği
  search.py      BM25, vektör araması, RRF birleştirme
  rerank.py      Voyage rerank-2.5
  contextual.py  LLM ile bağlam cümlesi üretimi
  answer.py      Claude + tool'lar, referans doğrulama
  evaluation.py  soru seti, getirme ve cevap metrikleri
  languages.py   tree-sitter ile C/C++/Java/C#/Go/TS/JS sembol çıkarma
  mcp_server.py  MCP sunucusu (search_code, read_chunk, index_status)
  cli.py         komut satırı arayüzü
eval/            soru setleri (kendi repo 20, anthropic SDK 60 kolay + 20 akış)
tests/           182 birim testi — ağ ve API anahtarı gerektirmiyor
data/, runs/     üretilen çıktılar (git'e girmez)
```

## Sıradaki adımlar

1. **Akış sorularında kapsamı yükseltmek** — %80'de; zincirin tamamını getirmek için
   HyDE (soruya cevap olabilecek sahte kod üretip onu embed etmek) denenmeli.
2. **Çok dilli ölçüm** — diller kod tarafında destekleniyor ama doğruluk yalnızca Python
   üzerinde ölçüldü. Java ya da C++ bir repoda soru seti hazırlanıp tekrarlanmalı.
3. **Demo** — müdüre gösterilecek akış.
4. **Cevap tarafını zor sette ölçmek** — getirme ölçüldü, cevap ölçümü (ücretli) bekliyor.
5. **`types/` gürültüsü** — 4 soruda gerçek cevap hâlâ üretilmiş tip tanımlarının altında
   kalıyor; ağırlık düşürmek yetmedi, başka bir yaklaşım gerekiyor.
