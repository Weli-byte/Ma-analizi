"""One-off script: renders the S0-S7 gap/deficiency report to PDF. Not part of the pipeline;
run manually (`python scripts/generate_s0_s7_gap_report.py`) when a fresh status report is
needed. Requires fpdf2 (not a project dependency; pip install fpdf2 to run this)."""

from datetime import date
from pathlib import Path

from fpdf import FPDF

FONT_DIR = Path(r"C:\Windows\Fonts")
OUT_PATH = Path(r"C:\Users\weli1\Downloads") / "S0-S7_Durum_ve_Eksiklik_Raporu.pdf"

TITLE = "Global Football Forecasting Platform — S0-S7 Durum ve Eksiklik Raporu"
SUBTITLE = (
    f"Hazırlanma tarihi: {date.today().isoformat()} | "
    "Repo: github.com/Weli-byte/Ma-analizi | Branch: remediation/s0-s3"
)


class Report(FPDF):
    def header(self):
        if self.page_no() == 1:
            return
        self.set_font("Arial", "I", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 8, TITLE, align="L")
        self.ln(10)

    def footer(self):
        self.set_y(-15)
        self.set_font("Arial", "I", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 10, f"Sayfa {self.page_no()}", align="C")


def h1(pdf, text):
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Arial", "B", 16)
    pdf.set_text_color(20, 20, 20)
    pdf.ln(4)
    pdf.multi_cell(0, 9, text)
    pdf.ln(1)


def h2(pdf, text):
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Arial", "B", 12.5)
    pdf.set_text_color(15, 55, 110)
    pdf.ln(3)
    pdf.multi_cell(0, 7.5, text)
    pdf.ln(0.5)


def h3(pdf, text):
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Arial", "B", 11)
    pdf.set_text_color(30, 30, 30)
    pdf.ln(1.5)
    pdf.multi_cell(0, 6.5, text)


def body(pdf, text):
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Arial", "", 10.5)
    pdf.set_text_color(35, 35, 35)
    pdf.multi_cell(0, 5.8, text)


def bullet(pdf, text, indent=6):
    pdf.set_font("Arial", "", 10.5)
    pdf.set_text_color(35, 35, 35)
    pdf.set_x(pdf.l_margin + indent)
    pdf.multi_cell(pdf.w - pdf.r_margin - pdf.l_margin - indent, 5.8, f"-  {text}")
    pdf.set_x(pdf.l_margin)


def divider(pdf):
    pdf.ln(1)
    pdf.set_draw_color(210, 210, 210)
    y = pdf.get_y()
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
    pdf.ln(3)


pdf = Report()
pdf.set_auto_page_break(auto=True, margin=18)
pdf.add_font("Arial", "", str(FONT_DIR / "arial.ttf"))
pdf.add_font("Arial", "B", str(FONT_DIR / "arialbd.ttf"))
pdf.add_font("Arial", "I", str(FONT_DIR / "ariali.ttf"))

# ------------------------------------------------------------------- cover
pdf.add_page()
pdf.ln(50)
pdf.set_font("Arial", "B", 22)
pdf.set_text_color(15, 55, 110)
pdf.multi_cell(0, 12, TITLE, align="C")
pdf.ln(6)
pdf.set_font("Arial", "", 11)
pdf.set_text_color(90, 90, 90)
pdf.multi_cell(0, 6, SUBTITLE, align="C")
pdf.ln(14)
pdf.set_font("Arial", "", 10.5)
pdf.set_text_color(35, 35, 35)
pdf.multi_cell(
    0,
    6,
    "Bu rapor, S0-S3 remediation raporundan sonra tamamlanan S4-S7 sprintlerini de kapsayacak\n"
    "şekilde genişletilmiştir. Her sprint için: ne tamamlandı, bilinen eksiklik/sınırlama/risk,\n"
    "ve önerilen aksiyon. Amaç: tek bakışta 'nerede zayıfız' sorusuna dürüst cevap vermek —\n"
    "kod çalışıyor olması, eksiksiz olduğu anlamına gelmez.",
    align="C",
)
pdf.ln(10)

# ------------------------------------------------------------------- executive summary
pdf.add_page()
h1(pdf, "1. Yönetici Özeti")
body(
    pdf,
    "S0-S3 daha önce ayrı bir remediation turundan geçti (bkz. "
    "reports/remediation/FINAL_S0_S3_REMEDIATION_REPORT.md); burada S0-S3'ün GÜNCEL "
    "durumundaki kalan bilinen sınırlamalar ile S4-S7'nin YENİ eklenen bilinen "
    "eksiklikleri birlikte listeleniyor. 'Eksiklik' burada iki anlamda kullanılıyor: "
    "(a) kapsam dışı bırakılmış ve dokümante edilmiş bilinçli sınırlamalar, "
    "(b) gerçek risk taşıyan, ileride giderilmesi gereken açıklar."
)
pdf.ln(2)
h2(pdf, "En kritik 5 bulgu")
bullet(pdf, "Süreç ihlali: CLAUDE.md \"Methodological changes require an ADR\" kuralına rağmen "
            "S4-S7 için hiç yeni ADR (0013+) yazılmadı — sadece model card (docs/*.md) yazıldı. "
            "Model card teknik dokümantasyon sağlıyor ama ADR'ın karar-gerekçe-alternatif kaydı "
            "eksik kalıyor.")  # fmt: skip
bullet(pdf, "CI sadece küçük altın (golden) fixture (36 maç) üzerinde çalışıyor; gerçek "
            "2019-2024 EPL+LaLiga veri setinde run_baselines/walk_forward/final hiç otomatik "
            "çalıştırılmıyor. Gerçek veride bir regresyon CI'da yakalanmaz.")  # fmt: skip
bullet(pdf, "S6 (XGBoost/LightGBM) gerçek veride henüz historical_prior'ı geçemiyor "
            "(log loss ~1.00-1.01 vs elo 0.97, market_implied 0.95) — küçük feature seti ve "
            "düşük Optuna bütçesi (varsayılan 8 trial) nedeniyle, rekabetçi değil.")  # fmt: skip
bullet(pdf, "Bağımlılık yüzeyi S6 ile ciddi büyüdü (xgboost/lightgbm/optuna/shap + numba/"
            "llvmlite/scipy/pandas/scikit-learn transitive) — CI süresi arttı, ve uv lock "
            "resolver kırılganlığı canlı olarak yaşandı (duckdb 1.5.5→1.5.6 sürüm kayması, "
            "--upgrade gerekmesi). Her yukarı akış (upstream) sürüm çıkışı bu sınıf hatayı "
            "tekrar tetikleyebilir.")  # fmt: skip
bullet(pdf, "Elo/Poisson/Dixon-Coles/GBM hiçbiri kalibre edilmiş değil (S9 henüz yapılmadı); "
            "ECE değerleri 0.02-0.06 aralığında ama düzeltilmemiş halde raporlanıyor.")  # fmt: skip
divider(pdf)

# ------------------------------------------------------------------- status table
h2(pdf, "Sprint durum özeti")
pdf.set_font("Arial", "B", 9.5)
pdf.set_fill_color(15, 55, 110)
pdf.set_text_color(255, 255, 255)
widths = [14, 30, 70, 76]
headers = ["Sprint", "Durum", "Kapsam", "Ana risk/eksiklik"]
for w, htext in zip(widths, headers, strict=True):
    pdf.cell(w, 7, htext, border=1, fill=True)
pdf.ln()
pdf.set_font("Arial", "", 8.7)
pdf.set_text_color(30, 30, 30)
rows = [
    ("S0-S3", "REMEDIATED", "Repo iskeleti, veri pipeline, feature engine, baseline'lar",
     "Lisans RESEARCH_ONLY; team resolution manuel; rest_days cup/continental maçları göremiyor"),
    ("S4", "Tamam", "Elo rating (leakage-safe, idempotent, replay)",
     "El yapımı gradient ascent kalibrasyonu; MOV özelliği hiç veri kaynağı yok, hep kapalı"),
    ("S5", "Tamam", "Poisson / Dixon-Coles gol modeli",
     "40 sabit IPF sweep, yakınsama kontrolü yok; rho kaba grid search, joint fit değil"),
    ("S6", "Tamam", "XGBoost / LightGBM + Optuna",
     "Küçük feature seti, rekabetçi değil; düşük Optuna bütçesi; CI süresi arttı"),
    ("S7", "Tamam", "Walk-forward backtest motoru",
     "Fold başına bootstrap CI yok; fold sayısı arttıkça runtime lineer büyüyor"),
    ("S8+", "Yapılmadı", "LLM benchmark, kalibrasyon, ensemble, canlı, deger, dashboard, API, MVP",
     "Plan sırasına göre henüz başlanmadı (bkz. CLAUDE.md Status)"),
]
fill = False
pdf.set_font("Arial", "", 8.7)
for r in rows:
    pdf.set_fill_color(240, 244, 250) if fill else pdf.set_fill_color(255, 255, 255)
    x0, y0 = pdf.l_margin, pdf.get_y()
    line_h = 5.2
    max_lines = 1
    for w, text in zip(widths, r, strict=True):
        lines = pdf.multi_cell(w, line_h, text, border=0, align="L", dry_run=True, output="LINES")
        max_lines = max(max_lines, len(lines))
    row_h = max_lines * line_h
    x = x0
    for w, text in zip(widths, r, strict=True):
        pdf.set_xy(x, y0)
        pdf.rect(x, y0, w, row_h, style="DF" if fill else "D")
        pdf.set_xy(x, y0)
        pdf.multi_cell(w, line_h, text, border=0, align="L")
        x += w
    pdf.set_xy(x0, y0 + row_h)
    fill = not fill
pdf.set_x(pdf.l_margin)
pdf.ln(4)

# ------------------------------------------------------------------- per-sprint sections
def sprint_section(code, title, done, gaps, actions):
    pdf.add_page()
    h1(pdf, f"{code} — {title}")
    h2(pdf, "Tamamlanan")
    for d in done:
        bullet(pdf, d)
    h2(pdf, "Bilinen eksiklik / sınırlama / risk")
    for g in gaps:
        bullet(pdf, g)
    h2(pdf, "Önerilen aksiyon")
    for a in actions:
        bullet(pdf, a)


sprint_section(
    "S0-S3",
    "Repo iskeleti, veri pipeline, feature engine, baseline'lar (REMEDIATED)",
    done=[
        "Schema/config/data/features/evaluation katmanları; provenance, content-derived data_version, "
        "atomik pipeline, final-test lock (EvaluationContext), golden testler.",
        "11 ADR (0002-0012), hash-locked bağımlılıklar, py3.12+3.14 CI matrisi.",
    ],
    gaps=[
        "Veri kaynağı RESEARCH_ONLY (docs/data_sources/licensing.md) — ticari kullanım öncesi "
        "lisans çözülmedi.",
        "team_resolution.auto_register_new_teams=False (varsayılan): yeni takım isimleri manuel "
        "review kuyruğuna düşüyor — ölçek büyüdükçe operasyonel yük.",
        "rest_days_raw/capped: kupa/kıtasal maçlar kaynakta yok, bu yüzden dinlenme günü hesabı "
        "bu maçları göremiyor (ADR 0008'de dokümante, ama hâlâ gerçek bir veri boşluğu).",
        "result_available_at_utc tarihsel veri için INFERRED (gözlemlenmiş değil) — gerçek bir "
        "besleme (feed) ile karşılaştırılıp doğrulanmadı.",
        "xG hiç üretilmiyor (bilinçli karar) — bu, S5/S6 modellerinin kalite tavanını sınırlıyor.",
        "Golden/reproducibility testleri yalnızca 36 maçlık küçük bir senteze dayanıyor; gerçek "
        "veri ölçeğinde ayrı bir reproducibility stres testi yok.",
    ],
    actions=[
        "Lisans durumunu S12'den önce netleştir (ticari veri kaynağına geçiş planlanıyorsa).",
        "team_resolution review kuyruğu için bir SLA/otomasyon eşiği tanımla.",
        "rest_days için kupa/kıtasal maç kaynağı eklenebilirse ADR ile değerlendir.",
    ],
)

sprint_section(
    "S4",
    "Elo Rating Modeli",
    done=[
        "Leakage-safe sıralı Elo: initial rating, home advantage, K-factor, idempotent update, "
        "zaman damgalı rating history, deterministic replay.",
        "Rating farkı → 1X2 olasılığına 3-outcome ordinal-logit mapping (sadece train replay'inde fit).",
        "9 test (tests/test_elo.py); gerçek veride log loss 0.9718 (market_implied'a yakın).",
    ],
    gaps=[
        "Ordinal-logit kalibrasyonu hazır bir kütüphane (ör. statsmodels/scipy.optimize) yerine "
        "elle yazılmış gradient ascent ile fit ediliyor; yakınsama toleransı/durma kriteri yok, "
        "sabit 800 iterasyon — büyük/küçük veri setlerinde optimal olmayabilir.",
        "use_margin_of_victory parametresi var ama hiç veri kaynağı (goal margin feature) "
        "olmadığı için pratikte hep kapalı ve test edilmemiş bir kod yolu.",
        "Home advantage ve K-factor sabit hiperparametre; hiçbir tuning/cross-validation "
        "çerçevesi yok — muhtemelen optimal değil.",
        "Zaman içinde form değişimini yansıtan bir 'decay' (unutma faktörü) yok; eski maçlar "
        "yeni maçlarla aynı ağırlıkta.",
    ],
    actions=[
        "Elo K-factor/home_advantage için basit bir grid search + walk-forward değerlendirmesi ekle.",
        "Ordinal-logit fit'i scipy.optimize.minimize gibi test edilmiş bir optimizasyona taşımayı "
        "değerlendir (aynı ADR ile).",
    ],
)

sprint_section(
    "S5",
    "Poisson / Dixon-Coles Gol Modeli",
    done=[
        "Attack/defense/home-advantage Poisson modeli, Iterative Proportional Fitting (IPF) ile "
        "yalnız train döneminde fit; identifiability için mean-zero attack recentering.",
        "Dixon-Coles düşük-skor korelasyon düzeltmesi (rho, grid search).",
        "11 test (tests/test_poisson_dc.py); gerçek veride log loss 0.9998 / 1.0012, home_adv 0.19, "
        "rho -0.06 — literatürle tutarlı.",
    ],
    gaps=[
        "IPF sabit 40 sweep ile çalışıyor; açık bir yakınsama kontrolü (ör. parametre değişim "
        "eşiği) yok — bazı uç durumlarda eksik/gereksiz fazla iterasyon riski.",
        "Temel Poisson modeli ev/deplasman gol bağımsızlığı varsayıyor (bilinen yanlış varsayım); "
        "Dixon-Coles düzeltmesi yalnızca 4 düşük skor hücresini (0-0,1-0,0-1,1-1) kapsıyor.",
        "Zaman decay yok — Elo ile aynı sınırlama, form değişimini yakalamıyor.",
        "rho, attack/defense sabitlendikten SONRA ayrı bir adımda fit ediliyor (joint MLE değil); "
        "kaba grid (step 0.005) kullanılıyor.",
        "max_goals=10 üstü skorlar sınır satır/sütuna katlanıyor — son derece nadir ama tam "
        "modellenmiyor.",
    ],
    actions=[
        "IPF için yakınsama kriteri (ör. max |delta_param| < eps) ekle, sabit sweep sayısını "
        "üst sınır yap.",
        "rho ve attack/defense'in joint fit edilmesi (tam DC MLE) S9/S11 öncesi değerlendirilebilir.",
    ],
)

sprint_section(
    "S6",
    "XGBoost / LightGBM + Optuna",
    done=[
        "S2 leakage-safe feature seti + her feature'ın _available flag'i; kronolojik internal "
        "split (Optuna + early stopping için); SHAP top-feature diagnostics (bilgilendirme amaçlı).",
        "Determinism + artifact reload (dump/load byte-identical) testleri; 24 test "
        "(tests/test_gbm.py).",
        "Gerçek veride log loss: xgboost 1.0050, lightgbm 1.0112.",
    ],
    gaps=[
        "Her iki model de gerçek veride historical_prior'ı (1.0615) hafif geçiyor ama elo (0.9718) "
        "ve market_implied'ı (0.9476) GEÇEMİYOR — S2 feature seti küçük (sadece form/gol/dinlenme "
        "istatistikleri, oran/xG yok) ve varsayılan Optuna bütçesi (8 trial) gerçek bir tuning "
        "için yetersiz; bu bir 'zemin', nihai sonuç değil.",
        "Hiper-parametre seçimi tek bir kronolojik iç holdout'a dayanıyor (k-fold cross-validation "
        "yok) — seçim varyansı yüksek olabilir.",
        "SHAP yalnızca 300 satırlık örneklemde hesaplanıyor; sadece bilgi amaçlı olsa da sayılar "
        "yaklaşık.",
        "form_points_3/5/10 gibi yüksek korelasyonlu feature'lar arasında bir çoklu-doğrusallık "
        "(multicollinearity) kontrolü/azaltımı yok.",
        "Yeni bağımlılıklar (xgboost/lightgbm/optuna/shap + numba/llvmlite/scipy/pandas/"
        "scikit-learn) CI süresini belirgin artırdı (~45sn → ~1.5dk/job) ve lock dosyası "
        "kırılganlığını gösterdi (bkz. kesişen bulgular).",
    ],
    actions=[
        "S9 (kalibrasyon) ve S11 (ensemble) öncesi Optuna bütçesini research/final modda artırmayı "
        "değerlendir (config zaten var: n_optuna_trials).",
        "Feature seti S12'de gerçek/ticari veri kaynağıyla genişleyince GBM'leri yeniden "
        "değerlendir — küçük feature setiyle erken yargıya varma.",
    ],
)

sprint_section(
    "S7",
    "Walk-Forward Backtest Motoru",
    done=[
        "Expanding/rolling, season-by-season fold'lar (mevcut split.py altyapısı ilk kez "
        "gerçekten çalıştırıldı); her fold için taze model instance'ları, tek immutable/"
        "content-hashed prediction ledger, fold+model başına ExperimentRecord + config_hash.",
        "Final-test izolasyonu yapısal (VALIDATION-mode EvaluationContext); 11 test "
        "(tests/test_walk_forward.py).",
    ],
    gaps=[
        "bootstrap_samples=0 (bilinçli tasarım): fold başına güven aralığı YOK — fold'lar "
        "arası metrik farkının istatistiksel olarak anlamlı olup olmadığı söylenemiyor.",
        "Otomatik fold-arası agregasyon/özet yok; her fold ayrı raporlanıyor — fold sayısı "
        "arttıkça (daha uzun veri geçmişiyle) rapor okunması zorlaşacak.",
        "Runtime, fold sayısı × model sayısı ile lineer büyüyor; GBM'lerin her fold'da yeniden "
        "Optuna'dan geçmesi bunu ağırlaştırıyor (paralel fold çalıştırma yok).",
        "Model listesi run_baselines ile aynı configs/model.yaml'dan geliyor — walk-forward'a "
        "özel/azaltılmış bir model alt kümesi çalıştırmak için ayrı bir mekanizma yok.",
    ],
    actions=[
        "Veri geçmişi büyüdükçe (S12) fold sayısı artacağı için fold-arası özet tablo/rapor "
        "eklemeyi değerlendir.",
        "GBM'lerin walk-forward'daki toplam maliyetini izlemek için bir runtime bütçesi/uyarısı "
        "düşünülebilir.",
    ],
)

# ------------------------------------------------------------------- cross-cutting
pdf.add_page()
h1(pdf, "2. Kesişen (Cross-Cutting) Bulgular")

h2(pdf, "2.1 Süreç: ADR eksikliği")
body(
    pdf,
    "CLAUDE.md açıkça der: \"Methodological changes require an ADR.\" S4-S7'de dört yeni model "
    "sınıfı (Elo, Poisson/Dixon-Coles, XGBoost/LightGBM, walk-forward motoru) eklendi ama "
    "hiçbiri için 0013+ numaralı bir ADR yazılmadı — yalnızca docs/*.md model card'ları. Model "
    "card 'nasıl çalışır'ı anlatıyor, ADR ise 'neden bu yaklaşım, hangi alternatifler "
    "değerlendirildi, ne zaman gözden geçirilir'i kayıt altına alır. Bu bir süreç borcu."
)
h3(pdf, "Öneri")
bullet(pdf, "S8'e geçmeden önce (ya da paralel) S4-S7 için özet bir ADR (0013) yazılabilir; "
            "sonraki her yeni model sınıfı için ADR kuralı S8'den itibaren gerçek zamanlı "
            "uygulanmalı.")  # fmt: skip

h2(pdf, "2.2 CI kapsamı: yalnızca sentetik altın veri")
body(
    pdf,
    "run_baselines / walk_forward / final CI'da yalnızca 36 maçlık golden fixture üzerinde "
    "çalışıyor. Gerçek 2019-2024 EPL+LaLiga veri setinde (2280+ satır) bu komutları ben bu "
    "oturumda manuel çalıştırdım (sonuçlar raporun ilgili sprint bölümlerinde) ama bu "
    "otomatik/tekrarlanabilir değil — CI'da gerçek veri regresyon testi yok."
)
h3(pdf, "Öneri")
bullet(pdf, "Gerçek veri setinde haftalık/manuel bir 'sanity run' script'i (CI dışı, "
            "zamanlanmış) eklenebilir; sonuçlar bir dashboard'a (S17) beslenene kadar en "
            "azından bir artifact olarak saklanabilir.")  # fmt: skip

h2(pdf, "2.3 Bağımlılık yüzeyi ve lock kırılganlığı")
body(
    pdf,
    "S6 ile xgboost, lightgbm, optuna, shap ve transitive olarak numba, llvmlite, scipy, "
    "pandas, scikit-learn eklendi. Bu oturumda canlı olarak yaşanan bir kırılganlık: "
    "'uv pip compile', var olan requirements.lock dosyasını bir 'preference' kaynağı olarak "
    "okuyup eski sürümlerde (duckdb 1.5.5) takılı kalıyordu; CI'nın taze dosyaya compile etmesi "
    "farklı (güncel, 1.5.6) sonuç verdi ve lock-up-to-date job'ı kırdı. --upgrade bayrağıyla "
    "çözüldü ama bu SINIF hata (herhangi bir upstream sürüm çıkışında) tekrar edebilir."
)
h3(pdf, "Öneri")
bullet(pdf, "Lock dosyası her regenerasyonunda `--upgrade` kullanımını CLAUDE.md'ye komut "
            "olarak ekle (zaten bu oturumda öğrenildi, kalıcı hale getirilmeli).")  # fmt: skip
bullet(pdf, "Yeni ML bağımlılıkları için lisans/CVE taraması henüz yapılmadı; S16 (MLOps) "
            "öncesi bir bağımlılık güvenlik taraması eklenebilir.")  # fmt: skip

h2(pdf, "2.4 Kalibrasyon eksikliği (S9 bekleniyor)")
body(
    pdf,
    "Şu ana kadarki hiçbir model (Elo/Poisson/Dixon-Coles/XGBoost/LightGBM) kalibre edilmedi. "
    "ECE değerleri gerçek veride 0.02-0.06 aralığında — kötü değil ama düzeltilmemiş. S9'a "
    "kadar bu olasılıkların 'ham' (raw) olduğu her raporlamada hatırlanmalı; GBM'lerin "
    "raw_probs_ alanı zaten bu adım için hazırlanmış durumda."
)

h2(pdf, "2.5 Golden/regresyon testi kapsamı")
body(
    pdf,
    "tests/test_golden_and_reproducibility.py yalnızca S3 baseline modellerini (always_home, "
    "historical_prior, recent_form_naive, market_implied) kapsıyor — golden fixture'ın "
    "configs/model.yaml'ı elo/poisson/dixon_coles/xgboost/lightgbm içermiyor. Bu modellerin "
    "kendi birim testleri (test_elo.py, test_poisson_dc.py, test_gbm.py) determinism'i ayrı "
    "ayrı doğruluyor ama TEK bir golden-hash zincirinden geçmiyorlar; bir regresyon golden "
    "testinde değil, ancak ilgili modelin kendi testinde yakalanır."
)

# ------------------------------------------------------------------- closing
pdf.add_page()
h1(pdf, "3. Öncelik Sıralı Aksiyon Listesi")
body(pdf, "Yüksekten düşüğe önerilen aksiyon sırası (S8'e geçmeden veya paralel yürütülebilir):")
pdf.ln(2)
priorities = [
    ("Yüksek", "S4-S7 için özet bir ADR (0013) yaz — süreç kuralına uyum."),
    ("Yüksek", "Lock dosyası regenerasyon komutuna --upgrade'i CLAUDE.md'ye kalıcı olarak ekle."),
    ("Orta", "Gerçek veri setinde run_baselines/walk_forward için CI-dışı zamanlanmış bir "
             "sanity-run script'i ekle."),  # fmt: skip
    ("Orta", "Elo/Poisson hiperparametreleri için basit bir grid search + walk-forward "
             "değerlendirmesi ekle."),  # fmt: skip
    ("Orta", "Yeni ML bağımlılıkları için lisans/CVE taraması (S16 öncesi)."),
    ("Düşük", "GBM Optuna bütçesini research/final modda artırmayı değerlendir (S9/S11 "
              "öncesi, feature seti S12'de genişleyince yeniden değerlendir)."),  # fmt: skip
    ("Düşük", "Walk-forward fold-arası özet raporu (veri geçmişi büyüdükçe önem kazanacak)."),
]
for prio, text in priorities:
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Arial", "B", 10.5)
    pdf.set_text_color(15, 55, 110)
    pdf.cell(22, 6, prio)
    pdf.set_font("Arial", "", 10.5)
    pdf.set_text_color(35, 35, 35)
    pdf.set_x(pdf.l_margin + 22)
    pdf.multi_cell(pdf.w - pdf.r_margin - pdf.l_margin - 22, 6, text)
    pdf.set_x(pdf.l_margin)
    pdf.ln(1)

pdf.ln(6)
divider(pdf)
body(
    pdf,
    "Not: Bu rapor kod tabanının şu anki (2026-09-28, commit dec5c04 sonrası) durumunu "
    "yansıtır. Her yeni sprint sonunda güncellenmesi önerilir."
)

OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
pdf.output(str(OUT_PATH))
print(f"written: {OUT_PATH}")
