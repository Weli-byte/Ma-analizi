# Model karşılaştırması: 130 değerlendirilmiş maç (2026-10-09 → 2026-10-18)

Kaynak: `python -m src.markets.track` çıktısı (`artifacts/markets/track_record.json`, üretim zamanı 2026-10-18 18:46 UTC).
Her maç için tahmin, maçtan önce yayınlanan SON sürümdür; sonuçlar openfootball'dan gelir. Kayıt 10 günlük ve karışık
liglerden oluşur: bu bir **ilk okuma**, sonuç değildir (sistemin kendi notu: "ilk okuma için yeterli, kayıt hâlâ kısa").
Metrikler proje kuralına göre: Log Loss, Brier, RPS birincil; isabet oranı ikincil. Düşük değer iyidir (isabet hariç).

## 1X2 olasılıkları (Log Loss, düşük iyi)

| Kaynak | Maç | Log Loss |
|---|---|---|
| Lig taban oranı (hep aynı olasılık) | 130 | 1.026 |
| Yalnızca model (piyasa oranı olmadan) | 130 | 0.876 |
| Piyasa (kesin zaman damgalı oran) | 129 | 0.852 |
| Yayınlanan tahmin (başlık) | 130 | 0.850 |
| Yayınlanan tahmin, piyasalı 129 maçta | 129 | 0.852 |

Başlık tahmin için Brier 0.490, RPS 0.174.

**Okuma:**
- Model, lig taban oranından belirgin biçimde iyi (0.876 vs 1.026), yani maçlar hakkında gerçek bilgi taşıyor.
- Yalnızca model, piyasadan kötü (0.876 vs 0.852). Bu, projenin önceki bulgusuyla tutarlı: "hiçbir model piyasa
  oranlarını geçemedi".
- Yayınlanan tahmin 129/130 maçta piyasa verisiyle yönlendiriliyor (`market_driven`), bu yüzden piyasayla neredeyse aynı çıkması
  beklenen bir sonuç: 0.852 = 0.852 bir üstünlük kanıtı **değildir**. Piyasaya kıyasla fark yok.
- Farkların belirsizliğini (güven aralığı) bu raporda hesaplamadım; maç bazlı olasılıklar gerekir. Sonuç 130 maçta kesin değil.

## İsabet oranları (ikincil ölçü; %95 aralık)

| Tahmin | Tuttu | Oran | Aralık | Basit karşılaştırma |
|---|---|---|---|---|
| Maç sonucu (1X2) | 88/130 | %67,7 | %59,2 - %75,1 | "hep ev sahibi": %52,3 |
| 2.5 alt/üst | 79/130 | %60,8 | %52,2 - %68,7 | "hep üst": %67,7 |
| Karşılıklı gol | 72/130 | %55,4 | %46,8 - %63,7 | - |
| Doğru skor | 10/130 | %7,7 | %4,2 - %13,6 | - |

**Okuma:**
- 1X2'de aralığın alt ucu (%59,2) "hep ev sahibi" oranının (%52,3) üstünde: gösterge olarak olumlu, ama 130 maç ve 10 gün.
- **Alt/üst'te "hep üst" demek (%67,7) modelden (%60,8) daha iyi tuttu.** Bu bölümü model üstünlüğü olarak sunmak yanlış olur.
- Doğru skor tahmini %7,7: zaten düşük olasılıklı bir hedef.
- Not: 1X2 isabetinde "tuttu", en yüksek olasılıklı sonucun gerçekleşmesidir; berabere kalan maçlar "tutmadı" sayılır.

## Eğilim etiketine göre (dört tahmin türü birlikte, n = 520)

| Etiket | Tuttu | Oran |
|---|---|---|
| güçlü eğilim | 156/186 | %83,9 |
| eğilim | 102/163 | %62,6 |
| yazı-tura | 94/171 | %55,0 |

Etiketler sıralı: eğilim güçlendikçe isabet artıyor. Bu, olasılıkların işe yaradığına dair makul bir işaret, ama 520 tahmin
130 maçtan geliyor ve birbirinden bağımsız değil.

## Liglere göre (Log Loss; 20'den az maçta gürültü)

| Lig / kupa | Maç | Log Loss |
|---|---|---|
| Bundesliga | 18 | 1.037 |
| Premier Lig | 19 | 0.883 |
| La Liga | 17 | 0.865 |
| Ligue 1 | 17 | 0.850 |
| Serie A | 17 | 0.879 |
| Şampiyonlar Ligi | 15 | 0.832 |
| Avrupa Ligi | 11 | 0.750 |
| Konferans Ligi | 7 | 0.657 |
| Süper Lig | 9 | 0.629 |

Hiçbir grup 20 maça ulaşmıyor. Süper Lig'in düşük Log Loss'u ve %100 isabeti (9/9) tek başına anlam taşımaz.
Bundesliga'nın yüksek değeri (1.037, lig taban oranından bile kötü) gürültü olabilir; 100+ maça ulaşınca yeniden bakılmalı.

## Sınırlar
- 10 gün, 9 yarışma: mevsimsel veya lige özgü sonuçlar için yetersiz.
- Piyasa karşılaştırması yalnızca "kesin zaman damgalı" oranı olan maçlarda yapıldı (129/130).
- Bu rapor yöntem değişikliği önermez ve tahmin üretimini değiştirmez; kimseye bahis tavsiyesi değildir.
- Yeniden üretmek için: `python -m src.markets.track` (veri güncel olmalı), sonra bu dosyadaki sayılar aynı JSON'dan alınır.
