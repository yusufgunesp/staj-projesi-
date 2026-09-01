# Demo akışı

**Süre:** ~13 dakika, ikinci canlı soruyla ~15 · **Dinleyici:** yazılımcı olmayabilir ·
**Toplam maliyet:** ~5 sent (ölçüldü: canlı soru başına 0,6-2,2 sent, geri kalanı bedava)

Her adımda önce **ne soracağın**, sonra **ne söyleyeceğin** var. Komutlar birebir kopyalanabilir.

---

## Hazırlık (demodan 5 dakika önce)

```bash
cd ~/Desktop/staj && .venv/bin/python -m codeqa stats -i data/anthropic.jsonl
```

Bu komut indeksi ısıtır ve her şeyin yerinde olduğunu gösterir. Ayrıca `.env` içinde
`ANTHROPIC_API_KEY` ve `VOYAGE_API_KEY` dolu olmalı.

Fallback'in de hazır olduğundan emin ol (yerel, ücretsiz, yarım saniye):

```bash
.venv/bin/python -m codeqa embed -i data/anthropic.jsonl --provider hash
```

**Krediyi kontrol et.** Canlı soru Anthropic API'sine gidiyor; bakiye biterse 400 döner ve
demo orada durur. Tek satırlık kontrol (bir kuruşun altında):

```bash
.venv/bin/python -c "from codeqa.cli import _load_env; _load_env(); import anthropic; print('kredi OK' if anthropic.Anthropic().messages.create(model='claude-haiku-4-5-20251001', max_tokens=5, messages=[{'role':'user','content':'hi'}]) else '')"
```

**MCP'yi bir kez dene** (§5 canlı yapılacaksa): Claude Code'u bu dizinde aç, `codeqa`
sunucusunun bağlandığını gör. Bağlanmıyorsa §5'i yapılandırma göstererek geç.

Terminali büyüt, yazı tipini büyüt. Tarayıcıda `README.md` açık dursun — soru gelirse
oradan ölçüm tablolarını gösterirsin.

> **Uyarı:** `data/` ve `runs/` git'e girmiyor (üretilen veri). İndeks ve embedding'ler —
> 7 MB + 127 MB — yalnızca bu makinede. Demoyu başka makineden yapacaksan önce kopyala;
> sıfırdan üretmek indeksleme + embedding koşusu demek.

---

## 1. Problem (1 dakika, ekransız)

> "Yeni bir müşteri projesine girdiğinde ilk hafta kodu anlamakla geçiyor. 'Ödeme nerede
> tahsil ediliyor', 'bu istek kaç kez yeniden deneniyor' gibi sorular. Bunları kıdemli
> birine sormak gerekiyor, o da kendi işinden kalıyor."

> "Bu araç o soruları cevaplıyor. Farkı şu: her cevap kodun neresinden geldiğini
> söylüyor, yani doğruluğunu anında kontrol edebiliyorsun."

Burada henüz bir şey çalıştırma. Problemi kur, sonra göster.

---

## 2. Canlı soru (2 dakika, ikinci soruyla 4) — demonun kalbi

Kullanılan kod tabanı: **Anthropic'in kendi Python SDK'sı, 1097 kod dosyası.** Aracın kendi
kodu değil, üçüncü taraf bir repo. (`stats` komutu 1099 diyor — ikisi markdown dokümanı.)

```bash
.venv/bin/python -m codeqa ask "bir istek 429 alırsa kaç kez ve ne kadar beklenerek yeniden deneniyor?" -i data/anthropic.jsonl --provider voyage --repo .venv/lib/python3.14/site-packages/anthropic --model-name claude-haiku-4-5-20251001
```

**Süre: 6-13 saniye** (dört koşuda ölçüldü). Cevap `_constants.py` ve `_base_client.py`
referansları içerir. Bazı koşularda ekranda `read_file(...)` / `search_symbol(...)` satırları
görünüyor: model bağlamda eksik kalanı görüp dosyaya kendisi gidiyor. Uzun süren koşular bunlar
— sessiz kalma, göster:

> "Şu an bağlamda bulamadığı bir şey için dosyayı kendisi açtı. Getirme mükemmel olmak
> zorunda değil, eksiği kapatacak yolu var."

Sonra **referanslardan birini canlı aç.** Açacağın referans `_base_client.py:818` olsun —
dört koşunun dördünde de çıktı ve gösterdiği şey tam isabet:

```bash
sed -n '818,822p' .venv/lib/python3.14/site-packages/anthropic/_base_client.py
```

`_constants.py` referansının satır numarası koşudan koşuya değişiyor (parça başlangıcını
gösteriyor, bazen boş satıra denk geliyor). Ekranda onu açma, `_base_client.py:818`'i aç.

> "Araç 818. satırı gösterdi. Bakıyoruz: `_calculate_retry_timeout`. Yani uydurmuyor,
> gerçekten oradan okumuş."

Bu an demonun en ikna edici anı. Acele etme.

### Bu soru ölçüm setimde var — söyle ve ikinci bir soru sor

Yukarıdaki soru `questions_akis.json`'daki `a02`'nin neredeyse birebir aynısı ve `a02`
**dev** bölmesinde, yani ayar yaparken baktığım yarıda. Provada çalıştığından emin olduğun
için onu seçtin; bunda sorun yok, ama **söylemezsen sahnelenmiş görünür** — üstelik §3'te
"ayar yaptığın sette rapor verilmez" diyeceksin.

> "Şunu da söyleyeyim: bu soru benim ölçüm setimde var, hem de ayar yaparken baktığım
> yarıda. Bilerek onu seçtim, çünkü karşınızda çalışacağından emin olmak istedim. O yüzden
> hiç bakmadığım yarıdan bir tane daha soralım."

Sonra bunu çalıştır — `a17`, akış setinin **test** bölmesinden, hiçbir ayarın görmediği:

```bash
.venv/bin/python -m codeqa ask "akış sırasında bağlantı kapanırsa kaynaklar nasıl serbest bırakılıyor?" -i data/anthropic.jsonl --provider voyage --repo .venv/lib/python3.14/site-packages/anthropic --model-name claude-haiku-4-5-20251001
```

**Süre: 7-8 saniye** (üç koşuda ölçüldü). Cevap `_streaming.py`'ı gösteriyor; üç koşunun
üçünde de doğru çıktı, ama referans satırları koşudan koşuya değişiyor — canlı açacaksan
cevabın o an yazdığı satırı aç.

Vaktin dar değilse bu ikinci soruyu atlama. Demoya iki dakika ekliyor, karşılığında
"ölçtüğü soruyu gösteriyor" itirazını tamamen kapatıyor.

---

## 3. Neden güvenilir (3 dakika)

> "Bir aracın doğru cevap verdiğini nasıl bilirsin? Ben de bilmiyordum, o yüzden ölçtüm."

```bash
.venv/bin/python -m codeqa eval -q eval/questions_zor.json -i data/anthropic.jsonl --provider voyage --split test -k 8 --label demo
```

**Süre: yarım saniye, ücretsiz.** Çıktı: recall %100, kapsam %93, MRR 0.847.

Anlatılacak üç şey:

**Soru setleri elle hazırlandı.** 26 zor soru, her birinin cevabının hangi dosyalarda
olduğu kodda aranarak doğrulandı.

**dev/test ayrımı var.** Ayarlar yalnızca dev yarısında denenir, rapor edilen sayı hiç
dokunulmamış test yarısından alınır.

**Bunun neden önemli olduğunun canlı örneği var.** Geliştirme sırasında bir günde üç
iyileştirme denendi; üçü de dev'de kazandı, üçü de test'te sıfır verdi. Ayrım olmasaydı
üçü de "iyileştirme" diye raporlanacaktı.

> "Yani elimdeki sayı, kendimi kandırmadığımı kontrol ederek elde edilmiş bir sayı."

### Ekranda görünüp de anlatılmazsa aleyhine çalışacak iki şey

Bu ikisini **sen söyle**, sorulmasını bekleme. İkisi de aslında bölümün lehine.

**Güven aralığı çıktının içinde yazıyor:** `recall@k : 100% (%95 GA: 78%-100%, n=14)`.

> "Yanındaki aralığı da görüyorsunuz: 14 soruyla %100 demek, gerçek başarımın %78'in
> üstünde olduğunu söylemek demek. Sayıyı olduğundan büyük göstermemek için aralığı
> raporun içine koydum. Test bölmesini büyütmek listemde duruyor — yarım günlük iş."

**Bir satırda `✓ z16: sıra 9` yazıyor, oysa `-k 8` verdik.** Çelişki değil:

> "İlk 8 alaka sırasına göre geliyor, sonra üç eksende parça ekleniyor — liste 8'den
> ~15'e uzuyor, ama hiçbir şey elenmiyor. z16 o eklenenlerin içinde yakalandı. Yani
> 'recall@8' aslında modele giden listenin tamamı üzerinden; eleme yapan bir sürüm de
> denendi, sembol isabetini düşürdüğü için kabul edilmedi."

### Ve ölçmediğim şey

> "Bütün bu sayılar tek bir kod tabanından: Anthropic'in Python SDK'sı. Üç soru seti, 106
> soru, ama tek repo. İkinci bir Python projesinde ölçmek listemin ilk maddesi — yarım
> günlük, bedava iş. O ölçüm yapılana kadar bu sayıların ne kadar genellendiğini
> bilmiyorum."

Bunu söylemek sayıyı zayıflatmıyor, bölümün tezini tamamlıyor: rapor edilen tek şey
ölçülen şey.

---

## 4. Araç kendi hatasını buldu (2 dakika)

> "Geliştirme sırasında araca kendi kodu hakkında bir soru sordum. Cevabında gerçek bir
> hataya işaret etti: önbellek anahtarı, embedding'e giden metnin tamamından değil
> yalnızca bir bölümünden hesaplanıyordu. Sonucu şuydu — bir dosya taşındığında
> önbellekten eski bağlamla üretilmiş vektör dönüyordu."

> "Hata doğrulandı ve düzeltildi. Etkisi ölçülebilir oldu: 4310 parça daha önce 3549
> vektöre çöküyordu."

İstersen ikinci hikâyeyi de ekle (bu demoyu hazırlarken bulundu):

> "Bu demoyu hazırlarken komutun 68 saniye sürdüğünü fark ettim. Profil çıkardım: arama
> 0,5 saniye harcıyordu, geri kalan tek bir satırdaydı. Önbellek birleştirilirken 48
> MB'lık dizi, döngü içinde olduğu için 12 bin kez okunuyordu. Diziyi döngüden çıkardım,
> komut 6 saniyeye indi."

---

## 5. Claude Code entegrasyonu (2 dakika)

> "Araç terminalde ayrı bir komut olarak durmak zorunda değil. MCP sunucusu olarak
> çalışıyor, yani geliştirici zaten kullandığı yerden — Claude Code'un içinden —
> soruyor."

Sunucu proje kökündeki `.mcp.json` ile kayıtlı, yani **Claude Code'u bu dizinde açtığında
`codeqa` hazır geliyor.** Canlı sor; yapılandırmayı da göstereceksen dosya bu:

```json
{
  "mcpServers": {
    "codeqa": {
      "command": "/Users/yusufgunes/Desktop/staj/.venv/bin/python",
      "args": ["-m", "codeqa", "serve",
               "-i", "/Users/yusufgunes/Desktop/staj/data/anthropic.jsonl",
               "--provider", "voyage"]
    }
  }
}
```

Üç araç sunuluyor: `search_code`, `read_chunk`, `index_status`. Protokol düzeyinde
doğrulandı (`initialize` → `tools/list` → `tools/call`; arama 0,9 saniyede dönüyor).

**Demodan önce bir kez dene.** Claude Code'u bu dizinde aç ve `codeqa` sunucusunun
bağlandığını gör; bağlanmadıysa §5'i canlı yapma, yapılandırmayı göstermekle yetin.

### Burada sorulacak soru §2'deki soru OLMAMALI

Provada çıktı: *"bir istek 429 alırsa kaç kez yeniden deneniyor?"* diye sorulunca Claude
**aracı hiç çağırmadan** ezberden doğru cevap verdi — tek bir `dosya:satır` referansı yoktu.
Anthropic SDK'sının retry davranışı zaten bildiği bir şey, aramaya ihtiyaç duymuyor.

Demoda bu olursa §5 kendi kendini çürütür: dinleyici haklı olarak "bunun için araca ne gerek
var" diye düşünür. Çözüm, **ezberlenemeyecek bir iç detay** sormak:

> "Bir skill arşivi diskten açılırken zararlı bir dosyanın çıkarma dizininin dışına yazması
> nasıl engelleniyor?"

Bu soru zor setinin test bölmesinden (`z13`) — cevabı `lib/tools/_skills.py`'deki
`_extract_skill_archive`, `_safe_member_name` ve `_within`'de. Doğrulandı: `search_code` doğru
yeri getiriyor, üstelik ikinci korumayı da (`_beta_builtin_memory_tool.py`'deki
`_validate_no_symlink_escape`) yanına koyuyor.

Alternatif, yazılımcı olmayan bir dinleyiciye daha yakın:

> "Kimlik bilgileri günlüğe yazılırken sırların açığa çıkmaması nasıl sağlanıyor?"

İki kural:

- **Araç adı yazma.** `index_status` gibi bir şey yazmak "önceden ayarlanmış" görünür. Doğal
  soru sor, Claude'un kendiliğinden aracı çağırması asıl gösteri.
- **Cevapta referans var mı diye bak.** Referans yoksa araç kullanılmamıştır; o an
  "bakın, bunu ezberden bilemezdi" diyeceğin cümle boşa düşer.

> "Cevaplama tarafını bilerek sunmuyorum. Claude Code zaten bir dil modeli; ona ikinci
> bir modelin cevabını vermek yerine ham arama sonuçlarını veriyorum. Hem ucuz hem daha
> doğru — kendi bağlamıyla yorumluyor."

---

## 6. Neyin işe yaramadığı (2 dakika) — en güçlü bölüm

> "Denediğim her şey işe yaramadı ve bunu da veriyle biliyorum."

Üç örnek, biri yeter:

**Hibrit arama.** Anahtar kelime + anlamsal aramayı birleştirmek mantıklı görünüyordu.
Küçük repoda doğrulandı, büyük İngilizce repoda anahtar kelime araması %95'ten %15'e
düştü — Türkçe soru kelimeleri İngilizce kodda hiç geçmiyor. Çıkarım: arama modu
sabitlenmemeli, her müşteri kod tabanında ölçülüp seçilmeli.

**Contextual retrieval.** Her parça için modele bağlam cümlesi yazdırma tekniği. 5-7 dolar
ve saatlerce işlem, karşılığında +2 puan. Masrafını çıkarmadı.

**Sert kota.** Kapsamı hemen yükseltti, kabul etmedim — çünkü kullandığım metrik tam da o
müdahaleyi ödüllendiriyordu. Bağımsız bir ölçüte baktığımda sonuç düşmüştü.

> "Bunlar zayıflık değil. Hangi tekniğin bu iş için para ettiğini artık tahminle değil
> ölçümle biliyorum."

---

## 7. Açık sorular (1 dakika) — müdüre soru

**Müşteri projeleri hangi dillerde yazılıyor?** Araç şu an yalnızca Python indeksliyor. Sekiz dil
desteği vardı; Go'da ölçülünce Python'un belirgin şekilde altında kaldığı için kaldırıldı.
Ağırlıklı dil Python değilse desteği geri getirmek kolay — ama **ölçmeden geri getirmek yanlış
olur**, çünkü iki dilin sonucu birbirini öngörmedi.

**Yerel model zorunluluk mu, yoksa seçenek mi?** Mimari her iki cevaba da hazır ve yerel
seçeneğin kalite maliyeti ölçüldü. Cevap, varsayılan yapılandırmayı belirler.

**Yarım gün kullanıcı denemesi için birinizin vaktini alabilir miyim?** Bu projede
ölçülmemiş tek iddia şu: "adapte olma süresini kısaltır". Getirme ve cevap doğruluğunu
ölçtüm, zaman kazancını ölçmedim — çünkü kendi kendime ölçemem. Protokol hazır
(`eval/KULLANICI_DENEYI.md`); kritik kural, sorunun önce yazılıp sonra araca sorulması.
İki kişi, yarım gün.

---

## Bir şeyler ters giderse

| Durum | Ne yap |
|-------|--------|
| `ask` yavaş ya da hata veriyor | `search` komutuna geç — bir saniye, LLM maliyeti yok, referansları yine gösterir. (Voyage anahtarını yine kullanıyor: bedava olan ve ağ istemeyen yol `--provider hash`.) |
| Anthropic anahtarı / kredisi bitti | `search --provider voyage` çalışmaya devam eder; demonun getirme tarafı ayakta kalır, yalnızca cevap üretimi düşer |
| Hiçbir anahtar çalışmıyor | `--provider hash --mode bm25` ile devam et ve **İngilizce anahtar kelime** ya da sembol adı ara (`retry timeout calculate` → `_calculate_retry_timeout` 1. sırada gelir). Anahtar kelime araması çalışır, anlamsal arama yapılmaz — Türkçe doğal dille sonuç alamazsın, sebebi README'deki dil farkı bulgusu |
| "Neden `--mode` yazmıyorsun?" | Yazmaya gerek yok: mod sağlayıcıdan çözülüyor (`voyage` → vector, `hash` → hybrid). Eskiden elle vermek gerekiyordu, düzeltildi |
| İnternet yok | `codeqa grep` ve `codeqa stats` tamamen yerel; ölçüm çıktıları `runs/` altında hazır |
| Soru gelir, cevabı bilmiyorsun | "Ölçmedim" demek bu projede geçerli bir cevap — raporun tamamı bunun üzerine kurulu |

## İsteğe bağlı: web arayüzü (akışın parçası değil)

`codeqa ui` diye bir komut var; `127.0.0.1:8765`'te tarayıcıda açılıyor. Projeler arayüzden
ekleniyor, ama demo için indeksi doğrudan vererek açmak daha hızlı:

```bash
.venv/bin/python -m codeqa ui -i data/anthropic.jsonl --provider voyage --repo .venv/lib/python3.14/site-packages/anthropic --model-name claude-haiku-4-5-20251001
```

**Yukarıdaki 15 dakikalık akışa dahil değil, bilerek.** §2'nin ikna gücü referansı terminalde
açmaktan geliyor: ortada gizlenecek yer olmadığı görünüyor. Arayüzde aynı şey tek tıkla oluyor
(referansa tıkla → dosyanın o satırı, hedef satır vurgulanmış hâlde altında açılıyor) ama
"hazırlanmış ekran" şüphesine açık.

Ne zaman kullan:
- Dinleyici terminale hiç bakmak istemiyorsa — §2'yi arayüzde yap, §3'ü yine terminalde
  (ölçüm çıktısının ham görünmesi işine yarıyor).
- Soru-cevap uzarsa: arayüzde arka arkaya soru sormak terminalden hızlı, her cevabın altında
  maliyet yazıyor.
- **"Bunu bizim kod tabanımıza da bağlayabilir miyiz?" sorusu gelirse** — "+ Proje ekle" ile
  yolu ver, tarama bileşimi ve maliyeti gösterir. Cevabı anlatmak yerine göstermiş olursun.

Demodan önce bir kez aç ve bir soru sor. Açılmıyorsa hiç bahsetme — akış onsuz tam.

## Sorulması muhtemel sorular

**"Yanlış cevap verirse ne olur?"** Her referans programla doğrulanıyor: dosya var mı,
satır dosya sınırları içinde mi. Bugüne kadarki bütün cevap koşularında **bir** uydurma
referans görüldü ve doğrulama katmanı onu yakaladı: model `türler/beta/...py:13` yazmıştı,
yani dizin adını Türkçeye çevirmişti — gerçek yol `types/beta/...`. Soruların Türkçe
olmasının yan etkisi. Katman olmasaydı cevap doğru görünecekti; varlık sebebi tam olarak bu.
Asıl güvence de şu — cevap kaynağını gösterdiği için yanlışsa saniyeler içinde anlaşılıyor.

**"Ne kadara mal oluyor?"** İndeksleme bir kerelik, 1097 dosyalık repo için birkaç sent.
Soru başına maliyet kullanılan modele göre değişiyor: Haiku ile ölçülen 0,6-2,2 sent —
model dosyaya kendisi gitmek için tool çağırdığında üst uca yaklaşıyor. `ask` çıktısının
son satırı bunu her koşuda yazıyor. Getirme tarafı — `eval`, `grep`, `search --provider
hash` — tamamen ücretsiz.

**"Kod dışarı çıkıyor mu?"** Varsayılanda evet, embedding sağlayıcısına gidiyor. Ama yerel
model seçeneği ölçüldü ve çalışıyor: `bge-m3` ile bulut modeliyle aynı bulma oranı, MRR'da
0,05 fark. Kodun dışarı çıkamadığı projelerde kullanılabilir.

**"Kaç dil destekliyor?"** Yalnızca Python. Bir dönem sekiz dil destekleniyordu; Go'da uçtan uca
ölçüm yapılınca sonuç Python'un belirgin şekilde altında çıktı ve destek kaldırıldı. Gerekçe:
ölçülmemiş bir yetenek savunulamaz. Kod git geçmişinde duruyor, ama geri getirilirse o dilde
ölçülerek getirilmeli.
