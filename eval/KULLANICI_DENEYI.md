# Kullanıcı deneyi protokolü

Projenin varlık sebebi "yeni bir kod tabanına adapte olma süresini kısaltmak". Bu cümle şu ana
kadar **test edilmemiş bir varsayım**. Bütün ölçümler aracın soruları doğru cevaplayıp
cevaplamadığına bakıyor; hiçbiri birinin işini kolaylaştırıp kolaylaştırmadığına bakmıyor.

Bu protokol o boşluğu tek kişiyle, yarım günde kapatmak için.

## Neden bunu ben (araç) yapamam

Ölçülmek istenen şey **senin** bir kod tabanını anlarken gerçekten sorduğun sorular. Ben aynı
deneyi koşarsam ürettiğim sorular aracın cevaplayabileceği sorulara benzer çıkar — çünkü aracı
ben kurdum ve nasıl çalıştığını biliyorum. Bu, ölçmek istediğimiz şeyi tam olarak yok eder.

## Kurulum (15 dakika)

Hiç bilmediğin, orta boy bir depo seç. Kendi projelerinden biri olmasın. Öneri: Go ya da
TypeScript, 300-1000 dosya.

```bash
git clone --depth 1 <repo> /tmp/deney
.venv/bin/python -m codeqa index /tmp/deney -o data/deney.jsonl
.venv/bin/python -m codeqa embed -i data/deney.jsonl --provider voyage
```

Kendine **somut bir görev** ver. "Kodu anla" görev değildir. Örnekler:

- "Bu projede yapılandırma dosyası okunduktan sonra hangi doğrulamalardan geçiyor, bul ve bir
  doğrulama daha eklemenin nereye dokunacağını yaz."
- "Şu özelliğin uçtan uca akışını çıkar ve üç cümleyle anlat."

## Koşu (2-3 saat)

Görevi yaparken **iki şeyi kaydet**:

**1. Aklına gelen her soru.** Araca sorup sormadığın fark etmez; aklına geldiği anda bir dosyaya
yaz. Bunlar gerçek sorular — uydurulmuş değil, aracı memnun etmek için seçilmemiş.

**2. Süre.** Görevi bitirme süren. Ayrıca her sorunun cevabını bulma süren: araçla mı buldun,
grep/okuyarak mı, hangisi daha hızlıydı.

Kural: soruyu **önce yaz, sonra sor**. Tersini yaparsan araca uyacak soru seçme eğilimine
düşersin.

## Değerlendirme (1 saat)

Kaydettiğin soruları soru setine çevir: her birinin cevabının hangi dosyalarda olduğunu — artık
kodu bildiğin için — işaretle. Sonra ölç:

```bash
.venv/bin/python -m codeqa eval -q eval/questions_deney.json -i data/deney.jsonl --provider voyage
```

## Raporda nasıl yazılmalı

Bu deney tek kişilik ve kendi üzerinde yapılmış; dış kullanıcı ölçümü **değil**. Raporda böyle
yazılmalı. Yine de şu an olandan iyi: "değer hipotezi hiç test edilmedi" cümlesi yerine "tek
kişilik, gerçek bir görev üzerinde ölçülmüş bir denemesi var" cümlesi kurulabilir.

Şu üç sayı raporlanabilir:

| Ne | Nasıl |
|----|-------|
| Gerçek soruların kaçına doğru cevap verdi | Kaydedilen soru seti üzerinde `eval` |
| Soru başına ortalama süre kazancı | Araçla bulma süresi vs. elle bulma süresi |
| Hangi soru tipinde işe yaradı, hangisinde yaramadı | Soruları elle sınıflandır |

Üçüncüsü en değerlisi: aracın nerede işe yaramadığını bilmek, ortalama bir yüzdeden daha çok şey
söylüyor.
