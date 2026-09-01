# Yeni bir repo için soru seti yazmak

Arayüzdeki duman testi ve dil işareti **ölçüm değil**. Rapor edilebilir bir sayı
istiyorsan tek yol bu: o repo için elle soru seti yazmak. Yarım gün sürüyor, bedava
(getirme ölçümü API çağırmıyor) ve bir kere yapılıyor.

## Neden otomatik üretilemiyor

**Sembol soruları** ("`parse_config` nerede tanımlı?") indeksten bedavaya üretilebilir
ve doğru cevabı da kesindir. Ama bunlar neredeyse `grep`: aracın değeri kelimesi kodda
geçmeyen dolaylı sorularda. Dahası bu sorular anahtar kelime aramasını sistematik
olarak kayırıyor — o setle mod seçersen her repoda "hibrit daha iyi" çıkar, hibritin
çöktüğü repolarda bile.

**LLM'e soru yazdırmak** başka bir tuzak: soru, parçanın kendi kelimeleriyle üretiliyor,
arama da kendisine ekilen kelimeleri buluyor. Skor şişiyor. Taslak üretmek için
kullanılabilir — ama etiketleri insan doğrulamalı.

## Format

`eval/questions_<repo>.json`:

```json
{
  "_aciklama": "expect_files: cevabın gerçekten bulunduğu dosyalar — elle işaretlendi.",
  "repo": "/yol/repo",
  "index": "data/repo.jsonl",
  "questions": [
    {
      "id": "q01",
      "split": "dev",
      "question": "Bir istek 429 alırsa kaç kez ve ne kadar beklenerek yeniden deneniyor?",
      "expect_files": ["_base_client.py", "_constants.py"],
      "note": "_should_retry + _calculate_retry_timeout + DEFAULT_MAX_RETRIES"
    }
  ]
}
```

`note` alanı sonradan hayat kurtarıyor: altı ay sonra "bu etiketi neden böyle koydum"
sorusunun cevabı orada.

## Kurallar

**1. dev/test ayrımı zorunlu.** İlk yarıya `"split": "dev"`, ikinciye `"test"`. Ayar
denemelerini yalnızca dev'de yap, rapor edeceğin sayıyı test'ten al. Bu projede bir
günde üç iyileştirme dev'de kazanıp test'te sıfır verdi; ayrım olmasaydı üçü de
"iyileştirme" diye raporlanacaktı.

**2. Önce soruyu yaz, sonra kodda ara.** Ters sırada yaparsan aracın bulabileceği
soruları yazmış olursun. Soruyu yazarken cevabını bilmiyorsan bile sorun yok — sonra
kodda arayıp `expect_files`'ı işaretlersin.

**3. Etiket düzeltirken setin tamamını geçir.** Yalnızca kaçırılan soruların etiketini
gözden geçirmek skoru tek yönlü şişirir. Ve düzeltmeyi **ölçüm düzeltmesi** olarak
işaretle, sistem kazancı olarak değil.

**4. Üç tip soru karıştır.** Yalnızca "X nerede" sorarsan araç kolay görünür:

| Tip | Örnek | Kaç dosya |
|-----|-------|-----------|
| Tek konum | "Zaman aşımı varsayılanı nerede tanımlı?" | 1 |
| Akış | "Bir istek gönderilirken hangi adımlar işliyor?" | 2-4 |
| Zor | Birbirine çok benzeyen iki mekanizmayı ayırt ettiren sorular | 2-3 |

**5. En az 20 soru, tercihen 30.** Test bölmesi 14 soru olduğunda bir soru ~3,5 puan
ediyor ve tek soruluk oynama gürültü seviyesinde kalıyor.

## Ölçmek

```bash
codeqa eval -q eval/questions_<repo>.json -i data/<repo>.jsonl --provider voyage --split test
```

Bedava ve yarım saniye — API çağırmıyor. Modları karşılaştırmak için `--mode vector` ve
`--mode hybrid` ile iki kez koş; **hangisinin kazandığı repoya göre değişiyor.**

Cevap tarafını da ölçmek istersen `--answers` ekle; o Claude çağırıyor, ücretli.

## Ne rapor edilir

Yalnızca **test bölmesinden** çıkan sayı. Yanındaki güven aralığını da yaz — 14 soruyla
%100 demek, gerçek başarımın %78'in üstünde olduğunu söylemek demek.
