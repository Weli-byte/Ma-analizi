# Google Play hazırlık paketi (Welliddia)

Play Console'da hesap doğrulaması bitince bu dosyadaki metinler olduğu gibi yapıştırılır. ADR 0098 (TWA) ile birlikte okunur.
Hazırlanma: 2026-11-02. Canlı kontroller aynı gün yapıldı (aşağıda).

- Paket adı: `app.welliddia.tahmin`
- Geliştirici adı: Welliddia (kişisel hesap)
- Site: https://mac-tahmin-eight.vercel.app
- İletişim e-postası: veliparlak84@gmail.com

## 1. URL'ler

| Alan | Değer |
|---|---|
| Web sitesi | https://mac-tahmin-eight.vercel.app |
| Gizlilik politikası | https://mac-tahmin-eight.vercel.app/gizlilik |
| Kullanım koşulları | https://mac-tahmin-eight.vercel.app/kosullar |
| Hesap / veri silme | https://mac-tahmin-eight.vercel.app/gizlilik (e-posta ile talep + uygulamada Profil > "Verilerimi sil") |

## 2. Mağaza girişi

**Uygulama adı (30 karakter):** `Welliddia: Maç Tahminleri`

**Kısa açıklama (TR, 80 karakter):**
`Futbol maçları için istatistiksel olasılıklar, gol tahminleri ve başarı kaydı.`

**Short description (EN):**
`Statistical football match probabilities, goal forecasts and a public record.`

**Tam açıklama (TR):**

```
Welliddia, futbol maçları için istatistiksel olasılık bilgisi veren bir tahmin uygulamasıdır.

NELER VAR?
• Premier Lig, La Liga, Bundesliga, Serie A, Ligue 1 ve Süper Lig maçları
• UEFA Şampiyonlar Ligi, Avrupa Ligi ve Konferans Ligi
• Her maç için ev sahibi / beraberlik / deplasman olasılıkları
• Beklenen gol, en olası skor, 2.5 alt/üst ve karşılıklı gol olasılıkları
• Takım formu, son maçlar ve puan durumu
• Favori takımlar ve hızlı takım arama
• Model güveni: tahminin ne kadar belirsiz olduğu açıkça gösterilir

ŞEFFAF BAŞARI KAYDI
Yayınlanan her tahmin maçtan önce kilitlenir ve maç bitince gerçek sonuçla karşılaştırılır. İsabet oranları, güven aralıkları ve basit karşılaştırmalarla birlikte herkese açık gösterilir.

ÖNEMLİ
Welliddia bahis oynatmaz, bahis aracılığı yapmaz ve para kabul etmez. Gösterilen bilgiler istatistiksel modellerin olasılık hesaplarıdır; kesinlik veya kazanç garantisi değildir. Uygulama 18 yaş ve üzeri kullanıcılar içindir.

Misafir olarak hesap açmadan kullanabilirsin. Google ile giriş yaparsan favorilerin cihazlar arasında saklanır.
```

**Full description (EN):**

```
Welliddia gives statistical probabilities for football matches.

FEATURES
• Premier League, La Liga, Bundesliga, Serie A, Ligue 1 and the Turkish Süper Lig
• UEFA Champions League, Europa League and Conference League
• Home / draw / away probabilities for every match
• Expected goals, most likely score, over/under 2.5 and both-teams-to-score probabilities
• Team form, recent results and standings
• Favourite teams and quick team search
• Model confidence: uncertainty is shown openly

TRANSPARENT TRACK RECORD
Every published forecast is locked before kick-off and compared with the real result afterwards. Hit rates, confidence intervals and simple baselines are public.

IMPORTANT
Welliddia does not take bets, does not broker bets and does not accept money. The numbers are statistical probabilities, not certainties or a promise of winnings. The app is for users aged 18 and over.

You can use it as a guest without an account. Signing in with Google keeps your favourites across devices.
```

**Kategori:** Spor. **Etiketler:** Spor, Futbol.
**İletişim:** veliparlak84@gmail.com, web sitesi yukarıda.

## 3. Grafikler (`img/`)

| Dosya | Play alanı | Boyut |
|---|---|---|
| `icon_512.png` | Uygulama simgesi | 512x512, 32-bit PNG |
| `feature_graphic_1024x500.png` | Öne çıkan grafik | 1024x500 |
| `01_ana_sayfa.png` | Telefon ekran görüntüsü 1 | 1080x1920 |
| `02_mac_ozet.png` | Telefon ekran görüntüsü 2 | 1080x1920 |
| `03_marketler.png` | Telefon ekran görüntüsü 3 | 1080x1920 |
| `04_basari_kaydi.png` | Telefon ekran görüntüsü 4 | 1080x1920 |
| `05_lig.png` | Telefon ekran görüntüsü 5 | 1080x1920 |

Ekran görüntüleri 2026-11-02'de canlı siteden misafir oturumuyla alındı (360x640, ölçek 3). "Tavsiyeler" sayfası
bilerek eklenmedi (bkz. 9. bölüm).

## 4. Uygulama içeriği formları

**Reklamlar:** Hayır, uygulamada reklam yok.

**Uygulama erişimi:** "Tüm işlevler özel erişim olmadan kullanılabilir." Giriş ekranında "Misafir olarak devam et" düğmesi
her işlevi açar; Google girişi yalnızca favorileri hesaba bağlar.

**Hedef kitle ve içerik:** Hedef yaş grubu yalnızca **18 ve üzeri**. Çocuklara hitap etmiyor.

**Haber uygulaması:** Hayır. **COVID-19:** Hayır. **Resmi kurum:** Hayır. **Finansal özellikler:** Hiçbiri.
**Sağlık:** Hayır.

**İçerik derecelendirme anketi (IARC):**
- Kategori: "Referans, Haber veya Eğitim" değil; **"Tüm diğer uygulama türleri"** (oyun değil).
- Şiddet, cinsellik, küfür, uyuşturucu: Hayır.
- Kullanıcılar birbirleriyle iletişim kuruyor / içerik paylaşıyor mu: Hayır.
- Konum paylaşımı: Hayır.
- Dijital ürün satın alma: Hayır.
- **Kumar:** Gerçek parayla kumar: **Hayır**. Simüle kumar: **Hayır**. Kumarla ilgili içerik / referans soruluyorsa: **Evet** cevabı dürüst olandır (maç olasılıkları, piyasa oranlarından türetilen olasılıklar, "sorumlu oyun" uyarısı). Bu cevap yaş derecelendirmesini yükseltebilir; hedef kitle zaten 18+.

**Veri güvenliği (Data safety):**

| Soru | Cevap |
|---|---|
| Veri topluyor veya paylaşıyor mu? | Evet, topluyor. Üçüncü taraflarla **paylaşmıyor** (hizmet sağlayıcılar adına işleme paylaşım sayılmaz). |
| Aktarımda şifreleme | Evet (HTTPS) |
| Kullanıcı silme isteyebilir mi? | Evet (Profil > "Verilerimi sil", ya da e-posta) |
| Kişisel bilgi: Ad | Toplanıyor, isteğe bağlı (Google girişi), amaç: Hesap yönetimi |
| Kişisel bilgi: E-posta adresi | Toplanıyor, isteğe bağlı (Google girişi), amaç: Hesap yönetimi |
| Kişisel bilgi: Kullanıcı kimlikleri | Toplanıyor (Google hesap kimliği / oturum kimliği), amaç: Hesap yönetimi, uygulama işlevselliği |
| Kişisel bilgi: Telefon numarası | **İşaretleme.** Telefon girişi canlıda kapalı (2026-11-02: "Telefonla giriş henüz etkin değil"). Açılırsa formu güncelle (yalnızca tek yönlü özet + son 4 hane, amaç: Hesap yönetimi). |
| Uygulama etkinliği: Diğer kullanıcı içeriği / işlemleri | Favori takımlar ve maçlar, amaç: Uygulama işlevselliği |
| Konum, kişiler, fotoğraf, finans, sağlık, mesajlar, cihaz kimliği | Toplanmıyor |
| Analitik / reklam | Yok |

**Hesap silme:** Uygulama içinde hesap oluşturulabildiği için Play bir silme URL'si ister:
`https://mac-tahmin-eight.vercel.app/gizlilik` (silme yöntemi ve e-posta adresi orada yazıyor).

## 5. Closed test

- Track: Kapalı test, sürüm `1.0.0` (versionCode 1), `.aab` = `android-build` iş akışının son başarılı çıktısı.
- Play App Signing: kabul et. İlk yüklemeden sonra **Uygulama bütünlüğü > Uygulama imzalama anahtarı sertifikası > SHA-256**
  değerini `configs/android/assetlinks.yaml` içindeki `play_app_signing` alanına yaz ve yeniden dağıt.
- Test kullanıcıları: en az 12 (öneri 15-20) Gmail adresi, 14 gün kesintisiz.

**Davet mesajı (WhatsApp / e-posta):**

```
Merhaba! Futbol maç tahmini uygulamam Welliddia'yı Google Play'de yayınlamadan önce test ediyorum.
Google, yayın için 12 kişinin uygulamayı 14 gün test etmesini istiyor. Bana yardım eder misin?

1) Gmail adresini bana yaz (seni test listesine ekleyeceğim).
2) Sana göndereceğim bağlantıyı Android telefonunda aç ve "Test kullanıcısı ol" de.
3) Uygulamayı Play Store'dan kur.
4) 14 gün boyunca uygulamayı silme; ara sıra açman yeterli.

Para, ödeme veya bahis yok; uygulama yalnızca istatistik gösteriyor. Teşekkürler!
```

## 6. Canlı kontroller (2026-11-02)

| URL | Sonuç |
|---|---|
| `/.well-known/assetlinks.json` | 200, `application/json`, yönlendirme yok; paket `app.welliddia.tahmin`, yalnızca upload key parmak izi |
| `/icon-512.png`, `/icon-maskable-512.png` | 200, `image/png` |
| `/manifest.webmanifest` | 200, `application/manifest+json` |
| `/gizlilik`, `/kosullar`, `/durum`, `/v1/health` | 200 |

Eksik: `play_app_signing` parmak izi (ilk yüklemeden sonra eklenecek).

## 7. Sahibin yapacakları (sırayla)

1. Kimlik ve adres doğrulamasının e-postasını bekle.
2. Telefon numarasını doğrula (SMS / arama kodu).
3. Uygulama oluştur (ad, Türkçe, Uygulama, Ücretsiz).
4. 2-4. bölümdeki metin ve görselleri yapıştır.
5. Kapalı test, `.aab` yükle, test kullanıcılarını ekle, bağlantıyı gönder.
6. SHA-256'yı ilet; `assetlinks.yaml` güncellenir.
7. 14 gün sonra üretime başvur.

## 8. Bilinen açıklar

- Gizlilik ve koşullar sayfaları kendilerini "taslak, hukuki danışmanlık değildir" diye tanımlıyor. Play bunu reddetmez,
  ama yayından önce bir hukukçuya göstermek önerilir.
- Giriş ekranında "Apple ile devam et" ve "Telefon numarası ile devam et" düğmeleri görünüyor. Canlıda basınca
  "henüz etkin değil, misafir olarak devam edebilirsin" yazıyor (2026-11-02). Çökme değil, ama inceleyen "bozuk işlev"
  sayabilir; yapılandırılmamış düğmeleri gizlemek daha güvenli.
- Giriş ekranı "Altı lig" diyor; ligler: 5 büyük lig + Süper Lig = 6. Tutarlı.

## 9. Politika riski: bahis içeriği

Uygulama bahis oynatmaz (ADR 0039), ama "Tavsiyeler" sekmesi ve maç sayfasındaki "Tavsiye" sekmesi seçim önerisi,
piyasa oranları ve "bahis" kelimesini içeriyor. Google Play'in gerçek parayla kumar politikası bahis ipuçları ve oran
içeriğine dikkatle bakar; bu durumda incelemenin reddetme ihtimali vardır. Bu Google'ın şu anki metniyle teyit edilmedi.
Seçenekler (sahip kararı):

1. Olduğu gibi gönder, ret gelirse gerekçeye göre değiştir.
2. TWA içinde (Android uygulamasından açıldığında) "Tavsiyeler" ve "Tavsiye" sekmesini gizle; web aynı kalır.
