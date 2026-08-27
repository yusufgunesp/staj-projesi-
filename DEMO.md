# Demo akışı

**Süre:** 10-12 dakika · **Dinleyici:** yazılımcı olmayabilir · **Toplam maliyet:** ~2 sent

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

Terminali büyüt, yazı tipini büyüt. Tarayıcıda `README.md` açık dursun — soru gelirse
oradan ölçüm tablolarını gösterirsin.

---

## 1. Problem (1 dakika, ekransız)

> "Yeni bir müşteri projesine girdiğinde ilk hafta kodu anlamakla geçiyor. 'Ödeme nerede
> tahsil ediliyor', 'bu istek kaç kez yeniden deneniyor' gibi sorular. Bunları kıdemli
> birine sormak gerekiyor, o da kendi işinden kalıyor."

> "Bu araç o soruları cevaplıyor. Farkı şu: her cevap kodun neresinden geldiğini
> söylüyor, yani doğruluğunu anında kontrol edebiliyorsun."

Burada henüz bir şey çalıştırma. Problemi kur, sonra göster.

---

## 2. Canlı soru (2 dakika) — demonun kalbi

Kullanılan kod tabanı: **Anthropic'in kendi Python SDK'sı, 1097 kod dosyası.** Aracın hiç
görmediği, üçüncü taraf bir repo. (`stats` komutu 1099 diyor — ikisi markdown dokümanı.)

```bash
.venv/bin/python -m codeqa ask "bir istek 429 alırsa kaç kez ve ne kadar beklenerek yeniden deneniyor?" -i data/anthropic.jsonl --provider voyage --mode vector --repo .venv/lib/python3.14/site-packages/anthropic --model-name claude-haiku-4-5-20251001
```

**Süre: ~6 saniye.** Cevap `_constants.py:3`, `_base_client.py:818` gibi referanslar içerir.

Sonra **referanslardan birini canlı aç**:

```bash
sed -n '818,822p' .venv/lib/python3.14/site-packages/anthropic/_base_client.py
```

> "Araç 818. satırı gösterdi. Bakıyoruz: `_calculate_retry_timeout`. Yani uydurmuyor,
> gerçekten oradan okumuş."

Bu an demonun en ikna edici anı. Acele etme.

---

## 3. Neden güvenilir (3 dakika)

> "Bir aracın doğru cevap verdiğini nasıl bilirsin? Ben de bilmiyordum, o yüzden ölçtüm."

```bash
.venv/bin/python -m codeqa eval -q eval/questions_zor.json -i data/anthropic.jsonl --provider voyage --mode vector --split test -k 8 --label demo
```

**Süre: yarım saniye, ücretsiz.** Çıktı: recall %100, kapsam %93, MRR 0.847.

Anlatılacak üç şey:

**Soru setleri elle hazırlandı.** 26 zor soru, her birinin cevabının hangi dosyalarda
olduğu kodda aranarak doğrulandı.

**dev/test ayrımı var.** Ayarlar yalnızca dev yarısında denenir, rapor edilen sayı hiç
dokunulmamış test yarısından alınır.

**Bunun neden önemli olduğunun canlı örneği var.** Bugün üç iyileştirme denendi; üçü de
dev'de kazandı, üçü de test'te sıfır verdi. Ayrım olmasaydı üçü de "iyileştirme" diye
raporlanacaktı.

> "Yani elimdeki sayı, kendimi kandırmadığımı kontrol ederek elde edilmiş bir sayı."

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

Claude Code açıksa canlı sor. Değilse yapılandırmayı göster — sunucunun konuştuğu
protokol düzeyinde doğrulandı (`initialize` → `tools/list` → `tools/call`, üç araç da
yanıt veriyor):

```json
{
  "mcpServers": {
    "codeqa": {
      "command": "/Users/yusufgunes/Desktop/staj/.venv/bin/python",
      "args": ["-m", "codeqa", "serve", "-i", "data/anthropic.jsonl", "--provider", "voyage"],
      "cwd": "/Users/yusufgunes/Desktop/staj"
    }
  }
}
```

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

**Müşteri projeleri hangi dillerde yazılıyor?** Araç sekiz dili okuyabiliyor ama doğruluk
yalnızca Python'da ölçüldü. Ağırlıklı dil bilinirse ölçüm oradan tekrarlanır.

**Yerel model zorunluluk mu, yoksa seçenek mi?** Mimari her iki cevaba da hazır ve yerel
seçeneğin kalite maliyeti ölçüldü. Cevap, varsayılan yapılandırmayı belirler.

---

## Bir şeyler ters giderse

| Durum | Ne yap |
|-------|--------|
| `ask` yavaş ya da hata veriyor | `search` komutuna geç — ücretsiz, yarım saniye, referansları yine gösterir |
| API anahtarı çalışmıyor | `--provider hash --mode bm25` ile devam et ve **İngilizce anahtar kelime** ya da sembol adı ara (`retry timeout calculate`). Anahtar kelime araması çalışır, anlamsal arama yapılmaz — Türkçe doğal dille sonuç alamazsın, sebebi README'deki dil farkı bulgusu |
| İnternet yok | `codeqa grep` ve `codeqa stats` tamamen yerel; ölçüm çıktıları `runs/` altında hazır |
| Soru gelir, cevabı bilmiyorsun | "Ölçmedim" demek bu projede geçerli bir cevap — raporun tamamı bunun üzerine kurulu |

## Sorulması muhtemel sorular

**"Yanlış cevap verirse ne olur?"** Her referans programla doğrulanıyor: dosya var mı,
satır dosya sınırları içinde mi. Uydurma referans sayısı ölçümlerde sıfır. Ama asıl
güvence şu — cevap kaynağını gösterdiği için yanlışsa saniyeler içinde anlaşılıyor.

**"Ne kadara mal oluyor?"** İndeksleme bir kerelik, 1097 dosyalık repo için birkaç sent.
Soru başına maliyet kullanılan modele göre değişiyor: Haiku ile soru başına yaklaşık yarım
sent. Arama tarafı tamamen ücretsiz.

**"Kod dışarı çıkıyor mu?"** Varsayılanda evet, embedding sağlayıcısına gidiyor. Ama yerel
model seçeneği ölçüldü ve çalışıyor: `bge-m3` ile bulut modeliyle aynı bulma oranı, MRR'da
0,05 fark. Kodun dışarı çıkamadığı projelerde kullanılabilir.

**"Kaç dil destekliyor?"** Sekiz: Python, C, C++, Java, C#, Go, TypeScript, JavaScript.
Ama doğruluk yalnızca Python'da ölçüldü — diğerleri için sembol çıkarma birim testlerle
doğrulandı, uçtan uca ölçüm yapılmadı.
