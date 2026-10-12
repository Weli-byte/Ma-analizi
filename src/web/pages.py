"""Public web pages (S19 UI, ADR 0051): server-rendered HTML in Turkish, no JavaScript framework, no external requests.

Everything shown is derived from the published artifacts: probabilities, scores, goal markets, tips, the track record
and the data-quality notes. Raw bookmaker prices and club logos are NOT shown (data terms, ADR 0043). Unknown values
are written as "bilinmiyor", never as 0. Every page carries the required attribution and the responsible-gambling text.
"""

from datetime import datetime
from html import escape as e
from zoneinfo import ZoneInfo

LEAGUES = {
    "EPL": "Premier Lig", "LALIGA": "La Liga", "BUNDESLIGA": "Bundesliga",
    "SERIEA": "Serie A", "LIGUE1": "Ligue 1", "SUPERLIG": "Süper Lig",
}  # fmt: skip
MONTHS = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"]
TR = ZoneInfo("Europe/Istanbul")
LEAN = {"strong lean": "Güçlü eğilim", "lean": "Eğilim", "toss-up": "Belirsiz"}

CSS = """
:root{--bg:#f4f6f8;--card:#fff;--fg:#17202a;--mut:#667;--line:#dde2e8;--acc:#0b6bcb;--h:#2da44e;--d:#8c959f;--a:#cf6a22}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--card:#161b22;--fg:#e6edf3;--mut:#8b949e;--line:#30363d;--acc:#58a6ff;--h:#3fb950;--d:#8b949e;--a:#e3813a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
a{color:var(--acc);text-decoration:none}a:hover{text-decoration:underline}
header{background:var(--card);border-bottom:1px solid var(--line)}
.wrap{max-width:980px;margin:0 auto;padding:0 16px}
nav{display:flex;gap:18px;align-items:center;padding:12px 0;flex-wrap:wrap}nav b{font-size:18px;margin-right:auto}
h1{font-size:24px;margin:22px 0 6px}h2{font-size:18px;margin:26px 0 8px}
.tabs{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}.tabs a{padding:5px 12px;border:1px solid var(--line);border-radius:999px;background:var(--card);font-size:14px;color:var(--fg)}
.tabs a.on{background:var(--acc);border-color:var(--acc);color:#fff}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:10px 0}
.row{display:flex;justify-content:space-between;gap:12px;align-items:baseline;flex-wrap:wrap}
.teams{font-size:18px;font-weight:600}.mut{color:var(--mut);font-size:14px}
.bar{display:flex;height:12px;border-radius:6px;overflow:hidden;margin:8px 0 4px}.bar i{display:block}
.pc{display:flex;justify-content:space-between;font-size:14px}.pc span{min-width:30%}
.tags{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}.tag{font-size:12px;padding:2px 8px;border-radius:999px;border:1px solid var(--line)}
.s{color:#1a7f37}.l{color:#9a6700}.t{color:var(--mut)}
table{border-collapse:collapse;width:100%;font-size:15px}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line)}
.warn{border-left:4px solid #d29922}.note{font-size:13px;color:var(--mut)}
footer{margin:30px 0;padding-top:12px;border-top:1px solid var(--line);font-size:13px;color:var(--mut)}
.sc{display:flex;align-items:center;gap:8px;margin:3px 0}.sc b{min-width:44px}.sc div{height:10px;background:var(--acc);border-radius:5px}
"""

NAV = (
    '<header><div class="wrap"><nav><b>⚽ Maç Tahmin</b><a href="/">Maçlar</a>'
    '<a href="/tavsiyeler">Tavsiyeler</a><a href="/basari-kaydi">Başarı kaydı</a><a href="/hakkinda">Yöntem ve sınırlar</a>'
    "</nav></div></header>"
)
FOOT = (
    "<footer>Football data provided by the Football-Data.org API. Sonuç ve fikstür verisi ayrıca openfootball'dan "
    "(kamu malı). Oran verisi The Odds API. Tahminler istatistiksel modellerin tahminidir, kazanç garantisi değildir. "
    "<b>Gamble Responsibly. 18+</b> Kaybetmeyi göze alamayacağın parayla oynama.</footer>"
)


def page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang='tr'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{e(title)} - Maç Tahmin</title><style>{CSS}</style></head><body>{NAV}"
        f"<main class='wrap'>{body}{FOOT}</main></body></html>"
    )


def pct(x) -> str:
    return "bilinmiyor" if x is None else f"%{round(x * 100)}"


def kickoff_tr(iso: str) -> str:
    t = datetime.fromisoformat(iso).astimezone(TR)
    return f"{t.day} {MONTHS[t.month - 1]} {t.strftime('%H:%M')} (TSİ)"


def prob_bar(h: float, d: float, a: float, home: str, away: str) -> str:
    return (
        f"<div class='bar' role='img' aria-label='{e(home)} {pct(h)}, beraberlik {pct(d)}, {e(away)} {pct(a)}'>"
        f"<i style='width:{h * 100:.1f}%;background:var(--h)'></i><i style='width:{d * 100:.1f}%;background:var(--d)'></i>"
        f"<i style='width:{a * 100:.1f}%;background:var(--a)'></i></div>"
        f"<div class='pc'><span>{e(home)} {pct(h)}</span><span style='text-align:center'>Beraberlik {pct(d)}</span>"
        f"<span style='text-align:right'>{e(away)} {pct(a)}</span></div>"
    )


def tr_pick(market: str, pick: str, home: str, away: str) -> str:
    return {
        "Home win": f"{home} kazanır", "Draw": "Beraberlik", "Away win": f"{away} kazanır",
        "1X": f"{home} kazanır ya da berabere", "X2": f"{away} kazanır ya da berabere", "12": "Beraberlik olmaz",
        "Over 2.5 goals": "2.5 üst (3+ gol)", "Under 2.5 goals": "2.5 alt (en fazla 2 gol)",
        "Both teams score": "İki takım da gol atar", "Not both teams score": "İki takım da gol atmaz",
    }.get(pick, pick)  # fmt: skip


def tip_cls(lean: str) -> str:
    return {"strong lean": "s", "lean": "l"}.get(lean, "t")


def fixture_card(i: dict) -> str:
    r = i["result"]["headline_probs"]
    home, away = i["home"], i["away"]
    tips = "".join(
        f"<span class='tag {tip_cls(t['lean'])}'>{e(tr_pick(t['market'], t['pick'], home, away))} {pct(t['probability'])}</span>"
        for t in i["tips"][:3]
    )
    flags = i["data_quality"]["flags"]
    warn = (
        "<div class='note'>⚠ Bu takımların geçmiş verisi az: tahmin daha az güvenilir.</div>" if flags else ""
    )
    return (
        f"<a href='/mac/{e(i['fixture_id'])}' style='color:inherit;text-decoration:none'><div class='card'>"
        f"<div class='row'><span class='teams'>{e(home)} - {e(away)}</span>"
        f"<span class='mut'>{e(LEAGUES.get(i['league'], i['league']))} · {e(kickoff_tr(i['kickoff_utc']))}</span></div>"
        f"{prob_bar(r['home'], r['draw'], r['away'], home, away)}"
        f"<div class='row mut'><span>En olası skor: <b>{e(i['most_likely_score'])}</b></span>"
        f"<span>2.5 üst: {pct(next(x for x in i['goals']['over_under'] if x['line'] == 2.5)['over'])} · "
        f"İki takım da gol atar: {pct(i['goals']['btts_yes'])}</span></div>"
        f"<div class='tags'>{tips}</div>{warn}</div></a>"
    )


def home_page(intel: list[dict], league: str | None) -> str:
    present = {i["league"] for i in intel}
    tabs = ["<a href='/' class='%s'>Hepsi</a>" % ("on" if not league else "")]
    for key, name in LEAGUES.items():
        if key in present:
            tabs.append(f"<a href='/?lig={key}' class='{'on' if league == key else ''}'>{e(name)}</a>")
    shown = [i for i in intel if not league or i["league"] == league]
    cards = (
        "".join(fixture_card(i) for i in shown)
        or "<p class='mut'>Bu seçim için yaklaşan maç tahmini yok.</p>"
    )
    return page(
        "Maçlar",
        "<h1>Yaklaşan maçlar</h1><p class='mut'>Altı lig için sonuç, skor ve gol tahminleri. Bir maça tıklayınca "
        "ayrıntıları görürsün.</p>" + f"<div class='tabs'>{''.join(tabs)}</div>" + cards,
    )


def match_page(i: dict, picks: list[dict]) -> str:
    home, away = i["home"], i["away"]
    res, g = i["result"], i["goals"]
    hp = res["headline_probs"]
    basis = (
        "Bu olasılıklar <b>piyasa oranlarından</b> (komisyonu çıkarılmış) hesaplandı: geçmiş testlerde piyasa modelden "
        "daha iyi çıktı." if "market" in res["headline_basis"] else
        "Bu olasılıklar <b>istatistiksel modelden</b> geliyor (bu maç için güvenilir oran verisi yok)."
    )  # fmt: skip
    compare = ""
    if res["market_probs_devig"]:
        m, md = res["market_probs_devig"], res["model_probs"]
        compare = (
            "<h2>Model ve piyasa karşılaştırması</h2><div class='card'><table><tr><th></th><th>" + e(home) + "</th><th>Beraberlik</th><th>"
            + e(away) + "</th></tr>"
            f"<tr><td>Piyasa (komisyonsuz)</td><td>{pct(m['home'])}</td><td>{pct(m['draw'])}</td><td>{pct(m['away'])}</td></tr>"
            f"<tr><td>Model</td><td>{pct(md['home'])}</td><td>{pct(md['draw'])}</td><td>{pct(md['away'])}</td></tr></table></div>"
        )  # fmt: skip
    top = i["scorelines"][:6]
    mx = max(s["p"] for s in top)
    scores = "".join(
        f"<div class='sc'><b>{e(s['score'])}</b><div style='width:{s['p'] / mx * 60:.0f}%'></div><span>{pct(s['p'])}</span></div>"
        for s in top
    )
    ou = "".join(
        f"<tr><td>{x['line']}</td><td>{pct(x['over'])}</td><td>{pct(x['under'])}</td></tr>"
        for x in g["over_under"]
    )
    tips = "".join(
        f"<tr><td>{e(tr_pick(t['market'], t['pick'], home, away))}</td><td>{pct(t['probability'])}</td>"
        f"<td class='{tip_cls(t['lean'])}'>{e(LEAN.get(t['lean'], t['lean']))}</td></tr>"
        for t in i["tips"]
    )
    if picks:
        sel = {"H": f"{home} kazanır", "D": "Beraberlik", "A": f"{away} kazanır"}
        rows = "".join(
            f"<tr><td>{e(sel[p['selection']])}</td><td>{pct(p['mean_model_prob'])}</td><td>{pct(p['market_prob_devig'])}</td>"
            f"<td>{p['ev_per_unit']:+.2f}</td><td>%{p['stake_hint_pct_of_bankroll']:.1f}</td><td>{e(p['confidence'])}</td></tr>"
            for p in picks
        )
        value = (
            "<div class='card warn'><table><tr><th>Seçim</th><th>Model</th><th>Piyasa</th><th>Beklenen değer / birim</th>"
            "<th>Stake ipucu (kasanın)</th><th>Güven</th></tr>" + rows + "</table>"
            "<p class='note'>Model piyasadan farklı düşündüğünde çıkar. Garanti değildir; geçmiş testlerde hiçbir model piyasayı "
            "geçemedi, bu yüzden küçük tut. Fiyat değişmiş olabilir, işlem yapmadan önce güncel fiyatı kontrol et.</p></div>"
        )
    else:
        value = (
            "<div class='card'><p class='mut'>Bu maç için verinin desteklediği bir bahis önerisi yok. Bu, hiçbir bahsin iyi "
            "olmadığı anlamına gelmez, yalnızca verinin yeterince desteklemediği anlamına gelir.</p></div>"
        )
    flags = i["data_quality"]["flags"]
    q = "".join(f"<li>{e(f)}</li>" for f in flags) or "<li>Belirgin bir veri sorunu yok.</li>"
    cs = g
    return page(
        f"{home} - {away}",
        f"<p><a href='/'>← Maçlar</a></p><h1>{e(home)} - {e(away)}</h1>"
        f"<p class='mut'>{e(LEAGUES.get(i['league'], i['league']))} · {e(kickoff_tr(i['kickoff_utc']))}</p>"
        f"<h2>Sonuç olasılıkları</h2><div class='card'>{prob_bar(hp['home'], hp['draw'], hp['away'], home, away)}"
        f"<p class='note'>{basis}</p>"
        f"<p class='mut'>Çifte şans: {e(home)} ya da beraberlik {pct(res['double_chance']['1X'])} · "
        f"beraberlik olmaz {pct(res['double_chance']['12'])} · {e(away)} ya da beraberlik {pct(res['double_chance']['X2'])}</p></div>"
        + compare
        + f"<h2>En olası skorlar</h2><div class='card'>{scores}"
        f"<p class='note'>Skor tahmini zordur: en olası skorun bile ihtimali genelde %10-15 civarında.</p></div>"
        f"<h2>Goller</h2><div class='card'><p>Beklenen gol: {e(home)} {g['expected']['home']:.2f} - {e(away)} {g['expected']['away']:.2f}"
        f" (toplam {g['expected']['total']:.2f})</p><table><tr><th>Çizgi</th><th>Üst</th><th>Alt</th></tr>{ou}</table>"
        f"<p class='mut'>İki takım da gol atar: {pct(cs['btts_yes'])} · {e(home)} kalesini gole kapatır: {pct(cs['home_clean_sheet'])} · "
        f"{e(away)} kalesini gole kapatır: {pct(cs['away_clean_sheet'])}</p></div>"
        f"<h2>Tavsiyeler</h2><div class='card'><table><tr><th>Tahmin</th><th>İhtimal</th><th>Eğilim</th></tr>{tips}</table>"
        "<p class='note'>Eğilim = ihtimal ne kadar yüksek. %65+ güçlü eğilim, %55+ eğilim, altı belirsiz. Garanti değil.</p></div>"
        f"<h2>Bahis önerisi (araştırma amaçlı)</h2>{value}"
        f"<h2>Veri kalitesi</h2><div class='card'><ul>{q}</ul>"
        f"<p class='note'>Model {e(i['model_version'])} · veri {e(i['data_version'])} · tahmin zamanı {e(i['generated_at'])} · "
        f"bilgi kesim zamanı {e(i['information_cutoff'])}. Kadro ve sakatlık bilgisi bu tahmine girmez.</p></div>",
    )


def tips_page(rows: list[dict], names: dict[str, tuple[str, str]]) -> str:
    body = "".join(
        f"<tr><td><a href='/mac/{e(r['fixture_id'])}'>{e(names[r['fixture_id']][0])} - {e(names[r['fixture_id']][1])}</a></td>"
        f"<td>{e(kickoff_tr(r['kickoff_utc']))}</td><td>{e(tr_pick(r['market'], r['pick'], *names[r['fixture_id']]))}</td>"
        f"<td>{pct(r['probability'])}</td><td class='{tip_cls(r['lean'])}'>{e(LEAN.get(r['lean'], r['lean']))}</td></tr>"
        for r in rows
        if r["fixture_id"] in names
    )
    return page(
        "Tavsiyeler",
        "<h1>En yüksek ihtimalli tavsiyeler</h1><p class='mut'>İhtimale göre sıralı. Yüksek ihtimal, o seçimin değerli bir "
        "bahis olduğu anlamına gelmez: oran düşükse kazanç da küçüktür.</p><div class='card'><table><tr><th>Maç</th><th>Zaman</th>"
        f"<th>Tahmin</th><th>İhtimal</th><th>Eğilim</th></tr>{body}</table></div>",
    )


def track_page(t: dict) -> str:
    if not t["available"]:
        return page("Başarı kaydı", "<h1>Başarı kaydı</h1><div class='card'><p>Henüz kayıt yok.</p></div>")
    h = t["headline_1x2"]
    tips = "".join(
        f"<tr><td>{e(LEAN.get(k, k))}</td><td>{v['hit']}/{v['n']}</td><td>{pct(v['rate'])}</td></tr>"
        for k, v in t["tips_by_lean"].items()
    )
    return page(
        "Başarı kaydı",
        f"<h1>Başarı kaydı</h1><p class='mut'>Yayınlanan tahminler maçtan önce kilitlenir; maç bitince gerçek sonuçla karşılaştırılır.</p>"
        f"<div class='card warn'><b>{t['matches_scored']} maç değerlendirildi.</b> {e(t['reliability_note'])}. "
        "100 maçtan azsa bu sayılar rastlantıdan ayırt edilemez.</div>"
        f"<h2>Sonuç tahmini (1X2)</h2><div class='card'><table><tr><th>Ölçü</th><th>Değer</th></tr>"
        f"<tr><td>Log kaybı (düşük iyi)</td><td>{h['log_loss']:.3f}</td></tr>"
        f"<tr><td>Lig taban oranı ile kıyas</td><td>{h['log_loss_league_base_rate']:.3f}</td></tr>"
        f"<tr><td>İlk seçimin tutma oranı</td><td>{pct(h['top_pick_accuracy'])}</td></tr>"
        f"<tr><td>Doğru skoru bilme</td><td>{pct(t['correct_score_hit_rate'])}</td></tr></table></div>"
        f"<h2>Tavsiyelerin tutma oranı</h2><div class='card'><table><tr><th>Eğilim</th><th>Tuttu</th><th>Oran</th></tr>{tips}</table></div>"
        if t["matches_scored"]
        else "<h1>Başarı kaydı</h1><div class='card warn'>Henüz sonuçlanmış maç yok. İlk sonuçlar tur bittiğinde burada görünecek.</div>",
    )


def about_page() -> str:
    return page(
        "Yöntem ve sınırlar",
        "<h1>Yöntem ve sınırlar</h1>"
        "<div class='card'><h2>Nasıl tahmin ediyoruz?</h2><p>Her takımın saldırı ve savunma gücünü geçmiş maçlardan (yakın maçlar daha "
        "ağırlıklı) öğreniyoruz. Buradan skor dağılımını ve ondan sonuç, alt/üst gol, iki takım da gol atar gibi pazarları "
        "hesaplıyoruz. Güvenilir oran varsa sonuç olasılığı olarak piyasanın komisyonsuz hâlini kullanıyoruz.</p></div>"
        "<div class='card'><h2>Ne kadar iyi?</h2><p>2022-24 sezonlarında 3578 maçta test ettik: skor, sonuç, 2.5 alt/üst ve iki takım da "
        "gol atar tahminleri, basit ölçütlerden net biçimde iyi. <b>Ama hiçbir model piyasanın kendi olasılıklarını geçemedi.</b> "
        "Bu yüzden bahis önerileri düşük güvenle sunulur ve garanti değildir.</p></div>"
        "<div class='card warn'><h2>Sınırlar</h2><ul><li>Kadro, sakatlık ve hava durumu tahmine girmiyor.</li>"
        "<li>Süper Lig'in geçmiş verisi daha eksik: tahminleri daha az güvenilir.</li>"
        "<li>Korner, kart ve şut tahminleri henüz yayında değil.</li>"
        "<li>Başarı kaydı kısa: anlamlı olması için en az 100 maç gerekir.</li></ul></div>"
        "<div class='card'><h2>Sorumlu oyun</h2><p>Bu site bahis oynatmaz ve oynamanı önermez; yalnızca olasılık bilgisi verir. "
        "18 yaşından küçükler için değildir. Bahis eğlence olmaktan çıkıyorsa yardım al (ör. begambleaware.org).</p></div>",
    )


def not_found(msg: str) -> str:
    return page(
        "Bulunamadı",
        f"<h1>Bulunamadı</h1><div class='card'><p>{e(msg)}</p><p><a href='/'>← Maçlara dön</a></p></div>",
    )
