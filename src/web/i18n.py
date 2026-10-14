"""English UI (ADR 0054). Pages are written in Turkish; when the `lang=en` cookie is set the HTML is translated on the way
out: only TEXT between tags is touched (never attributes, URLs, scripts or styles), by replacing known Turkish phrases,
longest first. Club names, numbers and everything not in the table stay as they are, so a phrase that is missing shows up
in Turkish instead of breaking anything."""

import re
from html import escape, unescape

LANG_COOKIE = "lang"

PHRASES: dict[str, str] = {
    # navigation and titles
    "Ana sayfa": "Home",
    "Ligler": "Leagues",
    "Favoriler": "Favourites",
    "Tavsiyeler": "Tips",
    "Profil": "Profile",
    "Başarı kaydı": "Track record",
    "Yöntem ve sınırlar": "Method and limits",
    "Proje özeti": "Project summary",
    "Ara": "Search",
    "Bulunamadı": "Not found",
    "Giriş": "Sign in",
    "Hepsi": "All",
    # login
    "Gerçek verilerle hesaplanan maç sonucu, skor ve gol tahminleri. Altı lig ve Avrupa kupaları, tek uygulama.": "Match result, score and goal predictions computed from real data. Six leagues and the European cups, one app.",
    "Google ile devam et": "Continue with Google",
    "Apple ile devam et": "Continue with Apple",
    "Telefon numarası ile devam et": "Continue with phone number",
    "Misafir olarak devam et": "Continue as guest",
    "Devam ederek 18 yaşından büyük olduğunu onaylarsın. Bu uygulama bahis oynatmaz; yalnızca olasılık bilgisi verir. Kazanç garantisi yoktur.": "By continuing you confirm you are over 18. This app does not take bets; it only provides probability information. There is no guarantee of winnings.",
    # home
    "Merhaba": "Hello",
    "Bu hafta": "This week",
    "Gelecek hafta": "Next week",
    "maç için tahminimiz var.": "matches have predictions.",
    "Bugünün maçları": "Today's matches",
    "Öne çıkan tavsiyeler": "Top tips",
    "Takım ara (ör. Galatasaray)": "Search team",
    "Bugün tahmin edilen maç yok. Ligler sekmesinden diğer günleri gör.": "No predicted match today. See other days under Leagues.",
    "Şu an güçlü eğilimli tavsiye yok.": "No strong-lean tip right now.",
    "Favori takımlarının bu hafta": "Your favourite teams have",
    "maçı var.": "matches this week.",
    "Görmek için dokun.": "Tap to see.",
    "Bir maça girip takımın yanındaki yıldıza dokunarak favori takım ekleyebilirsin; maçları burada haber veririz.": "Open a match and tap the star next to a team to add a favourite; we will tell you about their matches here.",
    "Bu hafta ": "This week ",
    " maç": " matches",
    "En olası skor": "Most likely score",
    # leagues
    "Bu seçim için tahmin edilen maç yok. Başka bir hafta seçmeyi dene.": "No predicted match for this selection. Try another week.",
    "Pazartesi": "Monday",
    "Salı": "Tuesday",
    "Çarşamba": "Wednesday",
    "Perşembe": "Thursday",
    "Cuma": "Friday",
    "Cumartesi": "Saturday",
    "Pazar": "Sunday",
    "Premier Lig": "Premier League",
    "Süper Lig": "Super Lig",
    'Bu uygulama futbol maçları için istatistiksel olasılık bilgisi verir. Mümkün olduğunca az kişisel veri işler.': 'This app gives statistical probability information for football matches. It processes as little personal data as possible.',
    'Hangi verileri işliyoruz?': 'What data do we process?',
    'Misafir girişi: hiçbir kişisel veri alınmaz; yalnızca tarayıcında imzalı bir oturum çerezi tutulur.': 'Guest sign-in: no personal data is collected; only a signed session cookie is kept in your browser.',
    "Google ile giriş: Google'ın verdiği hesap kimliği, adın ve e-posta adresin. Şifren bize hiç ulaşmaz.": 'Sign-in with Google: the account id, your name and your email address given by Google. Your password never reaches us.',
    'Telefon ile giriş: numaran saklanmaz; yalnızca tek yönlü bir özet (hash) ve numaranın son 4 hanesi tutulur.': 'Sign-in with phone: your number is not stored; only a one-way hash and the last 4 digits are kept.',
    'Favori takımların ve maçların, hesabına bağlı olarak sunucudaki veritabanında saklanır.': 'Your favourite teams and matches are stored in the server database, linked to your account.',
    'Ne yapmıyoruz?': 'What we do not do',
    'Reklam göstermiyoruz, seni takip etmiyoruz, verini satmıyoruz veya üçüncü taraflarla pazarlama için paylaşmıyoruz.': 'We show no ads, do not track you, do not sell your data and do not share it with third parties for marketing.',
    'Analitik veya izleme çerezi kullanmıyoruz; tek çerez oturum (ve dil tercihi) çerezidir.': 'We use no analytics or tracking cookies; the only cookies are the session cookie and the language preference.',
    'Verilerini silme': 'Deleting your data',
    'Profil sayfasındaki "Verilerimi sil" düğmesi hesabına bağlı tüm verileri (favoriler dahil) siler.': 'The "Delete my data" button on the profile page deletes everything linked to your account (including favourites).',
    'Bu belge hakkında': 'About this document',
    'Bu metin bir taslaktır ve hukuki danışmanlık değildir; yayına almadan önce bir hukukçu tarafından gözden geçirilmelidir.': 'This text is a draft and not legal advice; a lawyer should review it before launch.',
    'Bu uygulamayı kullanarak aşağıdaki koşulları kabul etmiş olursun.': 'By using this app you accept the following terms.',
    'Bilgi amaçlıdır': 'Information only',
    'Gösterilen tahminler istatistiksel modellerin olasılık hesaplarıdır; kazanç garantisi vermez ve yatırım veya bahis tavsiyesi değildir.': 'The predictions shown are probability estimates of statistical models; they guarantee no profit and are not investment or betting advice.',
    'Uygulama bahis oynatmaz, bahis aracılığı yapmaz ve para kabul etmez.': 'The app takes no bets, brokers no bets and accepts no money.',
    'Geçmiş testlerde hiçbir model piyasa oranlarını geçemedi; "bahis önerisi" bölümü yalnızca araştırma amaçlıdır.': 'In past tests no model beat the market odds; the "betting suggestion" section is for research only.',
    'Yaş sınırı ve sorumlu oyun': 'Age limit and responsible play',
    'Uygulama 18 yaş ve üzeri içindir. Bahis oynamak bağımlılık yapabilir; kaybetmeyi göze alamayacağın parayla oynama.': 'The app is for ages 18 and over. Betting can be addictive; never bet money you cannot afford to lose.',
    'Bulunduğun ülkede bahis yasaksa veya kısıtlıysa o kurallara uymak sana aittir.': 'If betting is prohibited or restricted where you live, following those rules is up to you.',
    'Veri ve doğruluk': 'Data and accuracy',
    'Veriler üçüncü taraf kaynaklardan gelir ve eksik veya geç olabilir; kadro, sakatlık ve hava durumu tahmine girmez.': 'Data comes from third-party sources and can be incomplete or late; line-ups, injuries and weather are not part of the prediction.',
    'Hizmet olduğu gibi sunulur; kesintisiz veya hatasız çalışacağı garanti edilmez.': 'The service is provided as is; uninterrupted or error-free operation is not guaranteed.',
    'Gizlilik politikası': 'Privacy policy',
    'Kullanım koşulları': 'Terms of use',
    'Gizlilik': 'Privacy',
    'Koşullar': 'Terms',
    "Şampiyonlar Ligi": "Champions League",
    "Avrupa Ligi": "Europa League",
    "Konferans Ligi": "Conference League",
    "(TSİ)": "(Turkey time)",
    # tips and picks
    " kazanır ya da berabere": " to win or draw",
    " kazanır": " to win",
    "Beraberlik olmaz": "No draw",
    "Beraberlik": "Draw",
    "2.5 üst (3+ gol)": "Over 2.5 (3+ goals)",
    "2.5 alt (en fazla 2 gol)": "Under 2.5 (at most 2 goals)",
    "İki takım da gol atar": "Both teams score",
    "İki takım da gol atmaz": "Neither team scores both",
    "Güçlü eğilim": "Strong lean",
    "Eğilim": "Lean",
    "Belirsiz": "Toss-up",
    "Tahmin": "Pick",
    "İhtimal": "Chance",
    "İhtimale göre sıralı. Yüksek ihtimal, o seçimin değerli bir bahis olduğu anlamına gelmez: oran düşükse kazanç da küçüktür.": "Sorted by chance. A high chance does not make it a valuable bet: if the price is low the payout is small too.",
    "Eğilim = ihtimal ne kadar yüksek. %65+ güçlü eğilim, %55+ eğilim, altı belirsiz. Garanti değil.": "Lean = how high the chance is. 65%+ strong lean, 55%+ lean, below that a toss-up. No guarantee.",
    # match page
    "Sonuç olasılıkları": "Result probabilities",
    "Model ve piyasa": "Model and market",
    "Piyasa (komisyonsuz)": "Market (margin removed)",
    "Model": "Model",
    "Bu olasılıklar": "These probabilities were",
    "piyasa oranlarından": "computed from market odds",
    "istatistiksel modelden": "from the statistical model",
    "(komisyonu çıkarılmış) hesaplandı: geçmiş testlerde piyasa modelden daha iyi çıktı.": "(margin removed): in past tests the market beat the model.",
    "geliyor (bu maç için güvenilir oran verisi yok).": "(there is no reliable odds data for this match).",
    "Çifte şans": "Double chance",
    "En olası skorlar": "Most likely scores",
    "Skor tahmini zordur: en olası skorun bile ihtimali genelde %10-15 civarında.": "Score prediction is hard: even the most likely score usually has only a 10-15% chance.",
    "Goller": "Goals",
    "Beklenen gol": "Expected goals",
    "Çizgi": "Line",
    "Üst": "Over",
    "Alt": "Under",
    "İki takım da gol atar:": "Both teams score:",
    "kalesini gole kapatır:": "keeps a clean sheet:",
    "Son 5 maç (G galibiyet, B beraberlik, M mağlubiyet)": "Last 5 matches (W win, D draw, L loss)",
    "veri yok": "no data",
    "Toplam gol dağılımı": "Total goals distribution",
    "Maçta toplam kaç gol olacağının ihtimali.": "Chance of the total number of goals in the match.",
    "Bahis önerisi (araştırma amaçlı)": "Betting suggestion (research only)",
    "Bu maç için verinin desteklediği bir bahis önerisi yok. Bu, hiçbir bahsin iyi olmadığı anlamına gelmez, yalnızca verinin yeterince desteklemediği anlamına gelir.": "The data does not support a betting suggestion for this match. That does not mean no bet is good, only that the data does not support one.",
    "Seçim": "Selection",
    "Piyasa": "Market",
    "Değer": "Value",
    "Stake ipucu": "Stake hint",
    "Model piyasadan farklı düşündüğünde çıkar. Garanti değildir; geçmiş testlerde hiçbir model piyasayı geçemedi, bu yüzden küçük tut. Fiyat değişmiş olabilir, işlem yapmadan önce güncel fiyatı kontrol et.": "Appears when the model disagrees with the market. Not a guarantee; in past tests no model beat the market, so keep it small. The price may have moved: check the current price before acting.",
    "Veri kalitesi": "Data quality",
    "Belirgin bir veri sorunu yok.": "No obvious data problem.",
    "Kadro ve sakatlık bilgisi bu tahmine girmez (eksikler yukarıda yalnızca bilgi olarak gösterilir).": "Line-up and injury information are not part of this prediction (absent players above are shown for information only).",
    "Eksikler (bilgi amaçlı)": "Absent players (information only)",
    "Bu maç için eksik oyuncu bilgisi henüz alınmadı (kaynak yalnızca maça en çok bir gün kala veri veriyor).": "No absence information has been received for this match yet (the source only provides data up to one day before the match).",
    "Kaynakta kayıtlı eksik yok.": "No absence recorded in the source.",
    "Kaynak: API-Football (ücretsiz plan), alınma zamanı ": "Source: API-Football (free plan), received at ",
    " UTC. Bu liste tahmin olasılıklarına girmez; eksiksiz olmayabilir.": " UTC. This list is not part of the probabilities and may be incomplete.",
    "Şüpheli": "Doubtful",
    "Sakat": "Injured",
    "Cezalı": "Suspended",
    "Kadro dışı": "Not in the squad",
    "Model ": "Model ",
    "veri ": "data ",
    "tahmin zamanı": "prediction time",
    # favourites, search, profile
    "Favori takımlarım": "My favourite teams",
    "Yaklaşan maçları": "Upcoming matches",
    "Henüz favori takımın yok. Bir maç sayfasında takımın yanındaki ☆ işaretine dokun.": "You have no favourite teams yet. Tap the ☆ next to a team on a match page.",
    "Favori takımlarının yaklaşan maçı yok.": "Your favourite teams have no upcoming match.",
    "Bir takım adı yaz: yaklaşan maçlarını gösteririz.": "Type a team name: we will show its upcoming matches.",
    "için yaklaşan maç bulunamadı.": "no upcoming match found.",
    "Takım ara": "Search team",
    "Misafir": "Guest",
    "Google hesabı": "Google account",
    "Apple hesabı": "Apple account",
    "Telefon": "Phone",
    "Verilerimi sil": "Delete my data",
    "Çıkış yap": "Sign out",
    "Dil": "Language",
    "Telefonuna eklemek için tarayıcı menüsünden": "To add it to your phone use the browser menu option",
    "Ana ekrana ekle": "Add to Home Screen",
    "seçeneğini kullan: uygulama gibi açılır.": "so it opens like an app.",
    "Hesabına bağlı tüm veriler (favoriler) silinecek. Emin misin?": "All data tied to your account (favourites) will be deleted. Are you sure?",
    # track record
    "Yayınlanan tahminler maçtan önce kilitlenir; maç bitince gerçek sonuçla karşılaştırılır.": "Published predictions are locked before the match; when it ends they are compared with the real result.",
    "Henüz sonuçlanmış maç yok. İlk sonuçlar tur bittiğinde burada görünecek.": "No finished match yet. The first results will appear here when the round ends.",
    "maç değerlendirildi.": "matches scored.",
    "Henüz çok az maç var: sonuçlar gürültü sayılır": "Very few matches so far: results count as noise",
    "İlk okuma için yeterli, ama kayıt hâlâ kısa": "Enough for a first read, but the record is still short",
    "100 maçtan azsa bu sayılar rastlantıdan ayırt edilemez.": "With fewer than 100 matches these numbers cannot be told apart from chance.",
    "Ölçü": "Measure",
    "Log kaybı (düşük iyi)": "Log loss (lower is better)",
    "Lig taban oranı ile kıyas": "League base rate for comparison",
    "İlk seçimin tutma oranı": "Top pick hit rate",
    "Doğru skoru bilme": "Correct score hit rate",
    "Tuttu": "Hit",
    "Oran": "Rate",
    # about and summary
    "Nasıl tahmin ediyoruz?": "How do we predict?",
    "Her takımın saldırı ve savunma gücünü geçmiş maçlardan (yakın maçlar daha ağırlıklı) öğreniyoruz. Buradan skor dağılımını ve ondan sonuç, alt/üst gol, iki takım da gol atar gibi pazarları hesaplıyoruz. Güvenilir oran varsa sonuç olasılığı olarak piyasanın komisyonsuz hâlini kullanıyoruz.": "We learn each team's attack and defence strength from past matches (recent ones weigh more). From that we compute the score distribution and from it result, over/under and both-teams-to-score markets. When a reliable price exists we use the market's margin-free version as the result probability.",
    "Ne kadar iyi?": "How good is it?",
    "2022-24 sezonlarında 3578 maçta test ettik: skor, sonuç, 2.5 alt/üst ve iki takım da gol atar tahminleri, basit ölçütlerden net biçimde iyi.": "We tested on 3578 matches in the 2022-24 seasons: score, result, over/under 2.5 and both-teams-to-score predictions are clearly better than simple baselines.",
    "Ama hiçbir model piyasanın kendi olasılıklarını geçemedi.": "But no model beat the market's own probabilities.",
    "Bu yüzden bahis önerileri düşük güvenle sunulur ve garanti değildir.": "So betting suggestions are offered with low confidence and are not guarantees.",
    "Sınırlar": "Limits",
    "Kadro, sakatlık ve hava durumu tahmine girmiyor.": "Line-ups, injuries and weather are not part of the prediction.",
    "Süper Lig'in geçmiş verisi daha eksik: tahminleri daha az güvenilir.": "Super Lig history is less complete: its predictions are less reliable.",
    "Korner, kart ve şut tahminleri henüz yayında değil.": "Corner, card and shot predictions are not published yet.",
    "Başarı kaydı kısa: anlamlı olması için en az 100 maç gerekir.": "The track record is short: at least 100 matches are needed to mean anything.",
    "Sorumlu oyun": "Responsible gambling",
    "Bu uygulama bahis oynatmaz ve oynamanı önermez; yalnızca olasılık bilgisi verir. 18 yaşından küçükler için değildir. Bahis eğlence olmaktan çıkıyorsa yardım al (ör. begambleaware.org).": "This app does not take bets and does not encourage you to bet; it only gives probability information. Not for under-18s. If betting stops being fun, get help (e.g. begambleaware.org).",
    "Bu proje ne yapar?": "What does this project do?",
    "Altı ligin (Premier Lig, La Liga, Bundesliga, Serie A, Ligue 1, Süper Lig) yaklaşan maçları için sonuç, skor, gol ve karşılıklı gol tahmini üretir. Bahis oynatmaz; olasılık bilgisi verir.": "For the upcoming matches of six leagues (Premier League, La Liga, Bundesliga, Serie A, Ligue 1, Super Lig) it produces result, score, goal and both-teams-to-score predictions. It takes no bets; it gives probability information.",
    "Hangi verilerle?": "Which data?",
    "Maç sonuçları ve fikstür: openfootball (kamu malı) ve football-data.org.": "Results and fixtures: openfootball (public domain) and football-data.org.",
    "Bahis oranları (olasılığa çevrilir): The Odds API.": "Betting prices (turned into probabilities): The Odds API.",
    "Korner/kart/şut istatistikleri: Highlightly (şimdilik araştırma amaçlı).": "Corner/card/shot statistics: Highlightly (research use for now).",
    "Nasıl çalışır?": "How does it work?",
    "Her takımın saldırı ve savunma gücü geçmiş maçlardan öğrenilir; yakın maçlar daha ağırlıklıdır. Buradan skor dağılımı çıkarılır, ondan da sonuç, alt/üst gol ve karşılıklı gol ihtimalleri hesaplanır. Bu tahminler maçtan önce kilitlenir ve maç bitince gerçek sonuçla karşılaştırılır (Başarı kaydı).": "Each team's attack and defence strength is learned from past matches; recent ones weigh more. The score distribution follows, and from it result, over/under and both-teams-to-score chances. These predictions are locked before the match and compared with the real result afterwards (Track record).",
    "Ne kadar doğru?": "How accurate?",
    "Gelecekten bilgi sızmasın diye testler yalnızca zamana göre yapıldı (rastgele ayırma yok). 2022-24 sezonlarında 3578 maçta: skor, sonuç, 2.5 alt/üst ve karşılıklı gol tahminleri basit ölçütlerden net biçimde iyi. Önemli dürüst bulgu:": "To avoid leaking the future, tests were done strictly by time (no random split). On 3578 matches in 2022-24: score, result, over/under 2.5 and both-teams-to-score predictions are clearly better than simple baselines. An important honest finding:",
    "hiçbir model bahis piyasasının kendi olasılıklarını geçemedi": "no model beat the betting market's own probabilities",
    "; bu yüzden sonuç tahmininde güvenilir oran varsa piyasa kullanılır.": "; so for the result prediction the market is used when a reliable price exists.",
    "Kadro ve sakatlık bilgisi tahmine girmiyor.": "Line-up and injury information are not part of the prediction.",
    "Süper Lig verisi daha eksik.": "Super Lig data is less complete.",
    "Garanti verilmez; 18+.": "No guarantee; 18+.",
    "Mühendislik": "Engineering",
    "Her tahmin içerik özetli ve değişmez dosyadır; testler her değişiklikte otomatik koşar; gerçek veri kaynakları lisans şartlarına göre incelenmiştir.": "Every prediction is an immutable, hash-checked file; tests run automatically on every change; real data sources were reviewed against their licence terms.",
    # footer
    "Sonuç ve fikstür verisi ayrıca openfootball'dan (kamu malı), oran verisi The Odds API'den. Tahminler istatistiksel modellerin tahminidir, kazanç garantisi değildir. Bu uygulama bahis oynatmaz.": "Results and fixtures also from openfootball (public domain), prices from The Odds API. Predictions are statistical estimates, not a guarantee of winnings. This app does not take bets.",
    "Böyle bir lig yok.": "No such league.",
    "Maçlara dön": "Back to matches",
    "Ana sayfaya dön": "Back to home",
    "Bu maç için tahmin bulunamadı (bitmiş ya da henüz yayınlanmamış olabilir).": "No prediction found for this match (it may be finished or not published yet).",
    "Giriş yöntemi": "Sign-in method",
    "Maç Tahmin": "Match Tips",
}
MONTHS = {
    "Oca": "Jan",
    "Şub": "Feb",
    "Mar": "Mar",
    "Nis": "Apr",
    "May": "May",
    "Haz": "Jun",
    "Tem": "Jul",
    "Ağu": "Aug",
    "Eyl": "Sep",
    "Eki": "Oct",
    "Kas": "Nov",
    "Ara": "Dec",
}
_MONTH = re.compile(r"(\d{1,2}) (" + "|".join(MONTHS) + r")(?!\w)")


def _compile(tr: str) -> re.Pattern:
    """Whole-word match for phrases that start/end with a letter (so 'Alt' never changes 'Altay')."""
    return re.compile(
        (r"(?<!\w)" if tr[0].isalnum() else "") + re.escape(tr) + (r"(?!\w)" if tr[-1].isalnum() else "")
    )


_ORDER = [(_compile(tr), PHRASES[tr]) for tr in sorted(PHRASES, key=len, reverse=True)]
_PCT = re.compile(r"%(\d+)")
_TAG = re.compile(r"(<[^>]+>)")


def _text(s: str) -> str:
    plain = unescape(s)
    for pat, en in _ORDER:
        plain = pat.sub(lambda _m, en=en: en, plain)
    plain = _MONTH.sub(lambda m: f"{m.group(1)} {MONTHS[m.group(2)]}", plain)
    return escape(_PCT.sub(r"\1%", plain), quote=False)


def translate_html(html: str) -> str:
    """Translate the text nodes of a page; tags, attributes, <style> and <script> are copied unchanged."""
    out, skip = [], False
    for part in _TAG.split(html):
        if part.startswith("<"):
            low = part.lower()
            if low.startswith(("<style", "<script")):
                skip = True
            elif low.startswith(("</style", "</script")):
                skip = False
            out.append(part.replace("lang='tr'", "lang='en'", 1) if low.startswith("<html") else part)
        else:
            out.append(part if skip or not part.strip() else _text(part))
    return "".join(out)
