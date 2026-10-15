"""Mobile-app style pages (ADR 0052): green and white, bottom navigation, Turkish, server-rendered, no external requests.

Everything shown is derived from the published artifacts. Raw bookmaker prices and names are never shown (data terms,
ADR 0043); club and league logos only when the local-demo switch is on (see logos.py), otherwise neutral initials badges.
Unknown values are written "bilinmiyor", never 0. Every page carries the required attribution and 18+ text.
"""

from collections import defaultdict
from datetime import datetime, timedelta
from html import escape as e
from zoneinfo import ZoneInfo

from .brand import APP_NAME, CONTACT_EMAIL, COUNTRY, OWNER

LEAGUES = {
    "EPL": ("Premier Lig", "PL", "#6a1b9a"),
    "LALIGA": ("La Liga", "LL", "#e65100"),
    "BUNDESLIGA": ("Bundesliga", "BL", "#c62828"),
    "SERIEA": ("Serie A", "SA", "#1565c0"),
    "LIGUE1": ("Ligue 1", "L1", "#00695c"),
    "SUPERLIG": ("Süper Lig", "SL", "#b71c1c"),
    "UCL": ("Şampiyonlar Ligi", "ŞL", "#0d2a6b"),
    "UEL": ("Avrupa Ligi", "AL", "#e8710a"),
    "UECL": ("Konferans Ligi", "KL", "#2e7d32"),
}
MONTHS = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"]
DAYS = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
TR = ZoneInfo("Europe/Istanbul")
LEAN = {"strong lean": "Güçlü eğilim", "lean": "Eğilim", "toss-up": "Belirsiz"}

CSS = """
:root{--g:#00a651;--gd:#007a3d;--gl:#e6f6ed;--bg:#f1f4f2;--card:#fff;--fg:#0e1f15;--mut:#66756c;--line:#e1e8e3;--warn:#d29922}
@media (prefers-color-scheme:dark){:root{--bg:#0b120e;--card:#14201a;--fg:#e8f1ec;--mut:#8fa297;--line:#243229;--gl:#12301f}}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:#cfd8d2;color:var(--fg);font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
#app{max-width:480px;margin:0 auto;min-height:100vh;background:var(--bg);position:relative;padding-bottom:76px;box-shadow:0 0 30px rgba(0,0,0,.18)}
a{color:inherit;text-decoration:none}
.top{position:sticky;top:0;z-index:5;background:linear-gradient(135deg,var(--g),var(--gd));color:#fff;padding:14px 16px;display:flex;align-items:center;gap:10px}
.top h1{font-size:18px;margin:0;font-weight:700;flex:1}.top a.back{font-size:22px;line-height:1;padding:0 6px 0 0}
.hero{background:linear-gradient(135deg,var(--g),var(--gd));color:#fff;padding:18px 16px 26px;border-radius:0 0 22px 22px}
.hero b{font-size:20px}.hero p{margin:4px 0 0;opacity:.9;font-size:14px}
.sec{padding:14px 14px 0}.sec h2{font-size:16px;margin:0 0 8px;display:flex;justify-content:space-between;align-items:baseline}.sec h2 a{font-size:13px;color:var(--g);font-weight:600}
.chips{display:flex;gap:8px;overflow-x:auto;padding:2px 14px 4px;scrollbar-width:none}.chips::-webkit-scrollbar{display:none}
.chip{flex:none;display:flex;flex-direction:column;align-items:center;gap:4px;width:72px;font-size:12px;text-align:center}
.pill{flex:none;padding:6px 14px;border-radius:999px;background:var(--card);border:1px solid var(--line);font-size:14px;font-weight:600}
.pill.on{background:var(--g);border-color:var(--g);color:#fff}
.badge{position:relative;width:46px;height:46px;border-radius:50%;display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:14px;background:#888;overflow:hidden;flex:none}
.badge.s{width:28px;height:28px;font-size:10px}.badge img{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;background:#fff;padding:2px}
.card{background:var(--card);border-radius:14px;padding:12px;margin:10px 14px;box-shadow:0 1px 2px rgba(0,0,0,.06)}
.card.pad0{padding:0;overflow:hidden}
.day{padding:12px 16px 2px;font-weight:700;color:var(--gd);font-size:14px}
.m{display:flex;align-items:center;gap:10px}.m .t{width:46px;text-align:center;font-size:13px;color:var(--mut);line-height:1.2}.m .t b{display:block;color:var(--fg);font-size:15px}
.m .tm{flex:1;min-width:0}.tm div{display:flex;align-items:center;gap:8px;margin:3px 0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.px{display:flex;gap:5px}.px div{width:46px;text-align:center;border:1px solid var(--line);border-radius:8px;padding:3px 0;background:var(--card)}
.px small{display:block;font-size:10px;color:var(--mut)}.px b{font-size:14px}.px .hi{background:var(--g);border-color:var(--g);color:#fff}.px .hi small{color:#e6f6ed}
.tags{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}.tag{font-size:12px;padding:2px 8px;border-radius:999px;background:var(--gl);color:var(--gd);font-weight:600}
.tag.s{background:var(--g);color:#fff}.mut{color:var(--mut);font-size:13px}
.tile{display:flex;flex-direction:column;align-items:center;gap:6px;padding:14px 8px;text-align:center;font-weight:600}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;padding:0 14px}.grid .card{margin:0}
.row{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:11px 14px;border-bottom:1px solid var(--line)}.row:last-child{border:0}
.bar{display:flex;height:10px;border-radius:5px;overflow:hidden;margin:10px 0 6px}.bar i{display:block}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:6px 4px;border-bottom:1px solid var(--line)}
.warn{border-left:4px solid var(--warn)}.note{font-size:12.5px;color:var(--mut)}
.sc{display:flex;align-items:center;gap:8px;margin:4px 0}.sc b{min-width:40px}.sc div{height:10px;background:var(--g);border-radius:5px}
.ok{color:var(--gd);font-weight:600}
.tabs{display:flex;gap:8px;overflow-x:auto;padding:10px 14px;position:sticky;top:0;background:var(--bg);z-index:5;scrollbar-width:none}.tabs::-webkit-scrollbar{display:none}
.tabs button{flex:none;border:0;border-radius:18px;padding:8px 14px;background:var(--card);color:var(--fg);font:600 13px system-ui;cursor:pointer;box-shadow:0 1px 2px rgba(0,0,0,.08)}
.tabs button.on{background:var(--g);color:#fff}
.tab{display:none}.tab.on{display:block}
details.cat{background:var(--card);border-radius:14px;margin:10px 14px;overflow:hidden;box-shadow:0 1px 2px rgba(0,0,0,.06)}
details.cat summary{padding:13px 14px;font-weight:700;cursor:pointer;list-style:none;display:flex;justify-content:space-between;align-items:center}
details.cat summary::-webkit-details-marker{display:none}details.cat summary::after{content:'▾';color:var(--mut)}
details.cat[open] summary::after{content:'▴'}
.cells{display:grid;gap:6px;padding:0 12px 12px}.c2{grid-template-columns:repeat(2,1fr)}.c3{grid-template-columns:repeat(3,1fr)}
.cell{background:var(--gl);border-radius:9px;padding:8px 4px;text-align:center;font-size:12.5px}.cell b{display:block;font-size:17px;margin-top:2px}
.cell.sel{background:var(--g);color:#fff}
.more summary{cursor:pointer;color:var(--g);font-weight:600;font-size:13px;padding:8px 0;list-style:none}.more summary::-webkit-details-marker{display:none}
.res{display:inline-flex;width:24px;height:24px;border-radius:50%;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:12px}
.mrow{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:8px 0;border-bottom:1px solid var(--line);font-size:13.5px}.mrow:last-child{border:0}
.mrow .d{color:var(--mut);font-size:12px;min-width:52px}.mrow .sc2{font-weight:700;min-width:44px;text-align:center}
tr.me td{background:var(--gl);font-weight:700}
nav.bottom{position:fixed;bottom:0;left:50%;transform:translateX(-50%);width:100%;max-width:480px;background:var(--card);border-top:1px solid var(--line);display:flex;z-index:9}
nav.bottom a{flex:1;text-align:center;padding:8px 0 10px;font-size:11px;color:var(--mut)}nav.bottom a b{display:block;font-size:20px;line-height:1.2}
nav.bottom a.on{color:var(--g);font-weight:700}
.foot{padding:18px 16px;font-size:12px;color:var(--mut)}
.btn{display:block;width:100%;padding:14px;border-radius:12px;border:0;font:inherit;font-weight:700;text-align:center;cursor:pointer}
.login{min-height:100vh;background:linear-gradient(160deg,var(--g),var(--gd) 70%);color:#fff;padding:36px 22px 24px;display:flex;flex-direction:column}
.login h1{font-size:30px;margin:26px 0 6px}.login p{opacity:.92;margin:0 0 26px}.login .btn{margin:10px 0}
.b-w{background:#fff;color:#111}.b-k{background:#111;color:#fff}.b-o{background:transparent;color:#fff;border:2px solid #fff}
.msg{display:none;background:rgba(0,0,0,.28);padding:10px 12px;border-radius:10px;font-size:13px;margin-top:8px}
.guest{margin-top:auto;text-align:center;padding-top:20px}.guest button{background:none;border:0;color:#fff;font:inherit;text-decoration:underline;cursor:pointer}
"""

NAV_ITEMS = [
    ("/", "🏠", "Ana sayfa", "home"),
    ("/ligler", "🏆", "Ligler", "leagues"),
    ("/favoriler", "⭐", "Favoriler", "fav"),
    ("/tavsiyeler", "💡", "Tavsiyeler", "tips"),
    ("/profil", "👤", "Profil", "profile"),
]
FOOT = (
    "<div class='foot'><a href='/dil/tr'>Türkçe</a> · <a href='/dil/en'>English</a><br>Football data provided by the Football-Data.org API. Sonuç ve fikstür verisi ayrıca openfootball'dan "
    "(kamu malı), oran verisi The Odds API'den. Tahminler istatistiksel modellerin tahminidir, kazanç garantisi değildir. "
    "Bu uygulama bahis oynatmaz. <b>Gamble Responsibly. 18+</b></div>"
)
HEAD = (
    "<meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>"
    "<meta name='theme-color' content='#00a651'><link rel='manifest' href='/manifest.webmanifest'>"
    "<link rel='apple-touch-icon' href='/icon-192.png'><meta name='apple-mobile-web-app-capable' content='yes'>"
)


def page(title: str, body: str, active: str = "", back: str | None = None, hero: str = "") -> str:
    nav = "".join(
        f"<a href='{h}' class='{'on' if k == active else ''}'><b>{ic}</b>{lab}</a>"
        for h, ic, lab, k in NAV_ITEMS
    )
    bar = (
        ""
        if hero
        else f"<div class='top'>{f'<a class=back href={back!r}>‹</a>' if back else ''}<h1>{e(title)}</h1></div>"
    )
    return (
        f"<!doctype html><html lang='tr'><head>{HEAD}<title>{e(title)} - {e(APP_NAME)}</title><style>{CSS}</style></head>"
        f"<body><div id='app'>{bar}{hero}{body}{FOOT}<nav class='bottom'>{nav}</nav></div>"
        "<script>if('serviceWorker' in navigator){navigator.serviceWorker.register('/sw.js').catch(()=>{});}</script></body></html>"
    )


def pct(x) -> str:
    return "bilinmiyor" if x is None else f"%{round(x * 100)}"


def local(iso: str) -> datetime:
    return datetime.fromisoformat(iso).astimezone(TR)


def kickoff_tr(iso: str) -> str:
    t = local(iso)
    return f"{t.day} {MONTHS[t.month - 1]} {t.strftime('%H:%M')} (TSİ)"


def day_label(t: datetime) -> str:
    return f"{DAYS[t.weekday()]}, {t.day} {MONTHS[t.month - 1]}"


def week_range(now: datetime, which: str) -> tuple[datetime, datetime] | None:
    mon = (now.astimezone(TR) - timedelta(days=now.astimezone(TR).weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    if which == "gelecek":
        return mon + timedelta(days=7), mon + timedelta(days=14)
    if which == "hepsi":
        return None
    return mon, mon + timedelta(days=7)


def badge(name: str, color: str, logo: str | None = None, small: bool = False) -> str:
    letters = "".join(w[0] for w in name.replace("-", " ").split()[:2]).upper()[:2] or "?"
    inner = e(letters) + (
        f"<img src='{e(logo)}' alt='' loading='lazy' onerror=\"this.remove()\">" if logo else ""
    )
    return f"<span class='badge{' s' if small else ''}' style='background:{color}'>{inner}</span>"


def hue(name: str) -> str:
    return f"hsl({sum(map(ord, name)) * 37 % 360},45%,40%)"


def team_badge(i: dict, side: str, logos, small=True) -> str:
    tid, name = i[f"{side}_team_id"], i[side]
    return badge(name, hue(name), logos.team_url(tid), small)


def league_badge(key: str, logos, small=False) -> str:
    _, short, color = LEAGUES[key]
    return badge(short, color, logos.league_url(key), small)


def boxes(h: float, d: float, a: float) -> str:
    vals = [("1", h), ("X", d), ("2", a)]
    top = max(vals, key=lambda v: v[1])[0]
    return (
        "<div class='px'>"
        + "".join(
            f"<div class='{'hi' if k == top else ''}'><small>{k}</small><b>{round(v * 100)}</b></div>"
            for k, v in vals
        )
        + "</div>"
    )


def tr_pick(market: str, pick: str, home: str, away: str) -> str:
    return {
        "Home win": f"{home} kazanır", "Draw": "Beraberlik", "Away win": f"{away} kazanır",
        "1X": f"{home} kazanır ya da berabere", "X2": f"{away} kazanır ya da berabere", "12": "Beraberlik olmaz",
        "Over 2.5 goals": "2.5 üst (3+ gol)", "Under 2.5 goals": "2.5 alt (en fazla 2 gol)",
        "Both teams score": "İki takım da gol atar", "Not both teams score": "İki takım da gol atmaz",
    }.get(pick, pick)  # fmt: skip


def match_row(i: dict, logos, tags: bool = True) -> str:
    r = i["result"]["headline_probs"]
    t = local(i["kickoff_utc"])
    tag_html = ""
    if tags:
        tag_html = (
            "<div class='tags'>"
            + "".join(
                f"<span class='tag {'s' if x['lean'] == 'strong lean' else ''}'>{e(tr_pick(x['market'], x['pick'], i['home'], i['away']))} {pct(x['probability'])}</span>"
                for x in i["tips"][:2]
            )
            + "</div>"
        )
    warn = " ⚠" if i["data_quality"]["flags"] else ""
    return (
        f"<a href='/mac/{e(i['fixture_id'])}'><div class='card'><div class='m'>"
        f"<div class='t'><b>{t.strftime('%H:%M')}</b>{t.day} {MONTHS[t.month - 1]}</div>"
        f"<div class='tm'><div>{team_badge(i, 'home', logos)}{e(i['home'])}</div><div>{team_badge(i, 'away', logos)}{e(i['away'])}</div></div>"
        f"{boxes(r['home'], r['draw'], r['away'])}</div>{tag_html}"
        f"<div class='mut' style='margin-top:6px'>En olası skor <b>{e(i['most_likely_score'])}</b>{warn}</div></div></a>"
    )


def google_button(client_id: str | None) -> str:
    """Google's own button when a client id is configured (a popup that always opens); otherwise our button, which says why it is off."""
    if client_id:
        return (
            "<div class='g_id_signin' data-type='standard' data-shape='pill' data-theme='outline' data-text='continue_with' "
            "data-size='large' data-width='340' data-locale='tr' style='margin:10px 0'></div>"
        )
    return "<button class='btn b-w' onclick=\"pick('google')\">Google ile devam et</button>"


def login_page(status: dict[str, bool], client_id: str | None, apple_id: str | None = None) -> str:
    gsi = (
        "<script src='https://accounts.google.com/gsi/client' async></script>"
        f"<div id='g_id_onload' data-client_id='{e(client_id)}' data-callback='onGoogle' data-auto_prompt='false' data-ux_mode='popup'></div>"
        if client_id
        else ""
    )
    if apple_id:
        gsi += (
            "<script src='https://appleid.cdn-apple.com/appleauth/static/jsapi/appleid/1/en_US/appleid.auth.js'></script>"
            f"<meta name='apple-client' content='{e(apple_id)}'>"
        )
    return (
        f"<!doctype html><html lang='tr'><head>{HEAD}<title>Giriş - {e(APP_NAME)}</title><style>{CSS}</style></head><body><div id='app' style='padding:0'>"
        f"<div class='login'><div style='font-size:46px'>⚽</div><h1>{e(APP_NAME)}</h1>"
        "<p>Gerçek verilerle hesaplanan maç sonucu, skor ve gol tahminleri. Altı lig ve Avrupa kupaları, tek uygulama.</p>"
        f"{gsi}{google_button(client_id)}"
        "<button class='btn b-k' onclick=\"pick('apple')\"> Apple ile devam et</button>"
        "<button class='btn b-o' onclick=\"pick('phone')\">Telefon numarası ile devam et</button>"
        "<div id='msg' class='msg'></div>"
        "<form class='guest' method='post' action='/giris/misafir'><button type='submit'>Misafir olarak devam et</button>"
        "<p style='font-size:12px;opacity:.85;margin-top:14px'>Devam ederek 18 yaşından büyük olduğunu onaylarsın. "
        "Bu uygulama bahis oynatmaz; yalnızca olasılık bilgisi verir. Kazanç garantisi yoktur.</p>"
        "<p style='font-size:12px;margin-top:6px'><a href='/gizlilik'>Gizlilik</a> · <a href='/kosullar'>Koşullar</a></p></form></div></div>"
        "<script>"
        f"const ST={str(status).lower()};"
        "const WHY={google:'Google ile giriş henüz etkin değil (Google istemci kimliği ayarlanmadı).',"
        "apple:'Apple ile giriş henüz etkin değil (Apple Developer hesabı gerekir).',"
        "phone:'Telefonla giriş henüz etkin değil (SMS sağlayıcısı gerekir).'};"
        "function pick(p){const m=document.getElementById('msg');"
        "if(p==='google'&&ST.google&&window.google){google.accounts.id.prompt();return;}"
        "if(p==='phone'&&ST.phone){location.href='/giris/telefon';return;}"
        "if(p==='apple'&&ST.apple&&window.AppleID){AppleID.auth.init({clientId:document.querySelector('meta[name=apple-client]').content,"
        "scope:'name email',redirectURI:location.origin+'/giris',usePopup:true});"
        "AppleID.auth.signIn().then(r=>fetch('/giris/apple',{method:'POST',headers:{'Content-Type':'application/json'},"
        "body:JSON.stringify({id_token:r.authorization.id_token})}).then(x=>{if(x.ok){location.href='/';}else{m.style.display='block';"
        "m.textContent='Apple girişi doğrulanamadı.';}})).catch(()=>{});return;}"
        "m.style.display='block';m.textContent=WHY[p]+' Şimdilik misafir olarak devam edebilirsin.';}"
        "function onGoogle(r){fetch('/giris/google',{method:'POST',headers:{'Content-Type':'application/json'},"
        "body:JSON.stringify({credential:r.credential})}).then(x=>{if(x.ok){location.href='/';}else{const m=document.getElementById('msg');m.style.display='block';m.textContent='Google girişi doğrulanamadı (sunucu '+x.status+'). Hesabın test kullanıcıları listesinde mi, istemci kimliği doğru mu kontrol et.';}});}"
        "</script>"
        "</body></html>"
    )


def home_page(
    user_name: str, upcoming: list[dict], now: datetime, logos, fav_ids: list[str] | None = None
) -> str:
    rng = week_range(now, "bu")
    week = [i for i in upcoming if rng[0] <= local(i["kickoff_utc"]) < rng[1]]
    chips = "".join(
        f"<a class='chip' href='/lig/{k}'>{league_badge(k, logos)}{e(LEAGUES[k][0])}</a>" for k in LEAGUES
    )  # every league is listed, also one with no predicted match yet (its page says so)
    today = [i for i in upcoming if local(i["kickoff_utc"]).date() == now.astimezone(TR).date()][:6]
    best = sorted(
        (t for i in upcoming for t in [{**x, "i": i} for x in i["tips"] if x["lean"] == "strong lean"]),
        key=lambda x: -x["probability"],
    )[:5]
    best_html = (
        "".join(
            f"<a href='/mac/{e(t['i']['fixture_id'])}'><div class='row'><div><b>{e(tr_pick(t['market'], t['pick'], t['i']['home'], t['i']['away']))}</b>"
            f"<div class='mut'>{e(t['i']['home'])} - {e(t['i']['away'])} · {e(kickoff_tr(t['i']['kickoff_utc']))}</div></div><span class='ok'>{pct(t['probability'])}</span></div></a>"
            for t in best
        )
        or "<div class='row mut'>Şu an güçlü eğilimli tavsiye yok.</div>"
    )
    fav_ids = fav_ids or []
    mine = [i for i in week if i["home_team_id"] in fav_ids or i["away_team_id"] in fav_ids]
    bell = (
        f"<a href='/favoriler'><div class='card' style='background:var(--gl)'>🔔 <b>Favori takımlarının bu hafta {len(mine)} maçı var.</b> "
        "<span class='mut'>Görmek için dokun.</span></div></a>"
        if mine
        else "<div class='card mut'>⭐ Bir maça girip takımın yanındaki yıldıza dokunarak favori takım ekleyebilirsin; maçları burada haber veririz.</div>"
    )
    hero = (
        f"<div class='hero'><b>Merhaba {e(user_name)} 👋</b><p>Bu hafta {len(week)} maç için tahminimiz var.</p>"
        "<form method='get' action='/ara' style='margin-top:12px'><input name='q' placeholder='Takım ara (ör. Galatasaray)' "
        "style='width:100%;padding:11px 14px;border-radius:12px;border:0;font:inherit'></form></div>" + bell
    )
    body = (
        f"<div class='sec'><h2>Ligler <a href='/ligler'>Hepsi</a></h2></div><div class='chips'>{chips}</div>"
        f"<div class='sec'><h2>Bugünün maçları</h2></div>"
        + (
            "".join(match_row(i, logos) for i in today)
            or "<div class='card mut'>Bugün tahmin edilen maç yok. Ligler sekmesinden diğer günleri gör.</div>"
        )
        + f"<div class='sec'><h2>Öne çıkan tavsiyeler <a href='/tavsiyeler'>Hepsi</a></h2></div><div class='card pad0'>{best_html}</div>"
    )
    return page("Ana sayfa", body, "home", hero=hero)


def leagues_page(upcoming: list[dict], now: datetime, logos) -> str:
    rng = week_range(now, "bu")
    tiles = ""
    for k, (name, _, _) in LEAGUES.items():
        n = sum(1 for i in upcoming if i["league"] == k and rng[0] <= local(i["kickoff_utc"]) < rng[1])
        tiles += f"<a href='/lig/{k}'><div class='card tile'>{league_badge(k, logos)}{e(name)}<span class='mut'>Bu hafta {n} maç</span></div></a>"
    return page("Ligler", f"<div style='height:6px'></div><div class='grid'>{tiles}</div>", "leagues")


def league_page(key: str, matches: list[dict], which: str, now: datetime, logos) -> str:
    name = LEAGUES[key][0]
    rng = week_range(now, which)
    sel = [
        i
        for i in matches
        if i["league"] == key and (rng is None or rng[0] <= local(i["kickoff_utc"]) < rng[1])
    ]
    pills = "".join(
        f"<a class='pill {'on' if which == k else ''}' href='/lig/{key}?hafta={k}'>{lab}</a>"
        for k, lab in (("bu", "Bu hafta"), ("gelecek", "Gelecek hafta"), ("hepsi", "Hepsi"))
    )
    by_day = defaultdict(list)
    for i in sel:
        by_day[local(i["kickoff_utc"]).date()].append(i)
    out = ""
    for d in sorted(by_day):
        out += f"<div class='day'>{e(day_label(local(by_day[d][0]['kickoff_utc'])))}</div>" + "".join(
            match_row(i, logos) for i in by_day[d]
        )
    if not out:
        out = "<div class='card mut'>Bu seçim için tahmin edilen maç yok. Başka bir hafta seçmeyi dene.</div>"
    top = f"<div class='top'><a class='back' href='/ligler'>‹</a>{league_badge(key, logos, True)}<h1>{e(name)}</h1></div>"
    return page(name, f"<div class='chips' style='padding-top:12px'>{pills}</div>{out}", "leagues").replace(
        f"<div class='top'><h1>{e(name)}</h1></div>", top, 1
    )


def injuries_card(i: dict, injuries: dict | None) -> str:
    """Absent players as INFORMATION (never in the probabilities). No observation is said plainly, not as 'nobody'."""
    from src.markets.injuries import WEB_LABEL

    head = "<div class='sec'><h2>Eksikler (bilgi amaçlı)</h2></div>"
    if injuries is None:
        return (
            head
            + "<div class='card mut'>Bu maç için eksik oyuncu bilgisi henüz alınmadı (kaynak yalnızca maça en çok bir gün kala veri veriyor).</div>"
        )

    def side(name: str, rows: list[dict]) -> str:
        if not rows:
            return f"<p><b>{e(name)}</b><br><span class='mut'>Kaynakta kayıtlı eksik yok.</span></p>"
        items = "".join(
            f"<li>{e(r['player'])} · {e(WEB_LABEL.get(r['status'], r['status']))}"
            f"{' · ' + e(r['reason']) if r.get('reason') else ''}</li>"
            for r in rows
        )
        return f"<p><b>{e(name)}</b></p><ul style='margin:0 0 8px;padding-left:18px'>{items}</ul>"

    return (
        head
        + "<div class='card'>"
        + side(i["home"], injuries["home"])
        + side(i["away"], injuries["away"])
        + "<p class='note'>Kaynak: API-Football (ücretsiz plan), alınma zamanı "
        + f"<b>{e(injuries['observed_at'][:16].replace('T', ' '))}</b>"
        + " UTC. Bu liste tahmin olasılıklarına girmez; eksiksiz olmayabilir.</p></div>"
    )


COMP_EXTRA = {
    "AUT1": "Avusturya Bundesliga",
    "BEL1": "Belçika Pro Lig",
    "GRE1": "Yunanistan Süper Ligi",
    "NED1": "Eredivisie",
    "POR1": "Portekiz Primeira Liga",
    "SCO1": "İskoçya Premiership",
}
TAB_SCRIPT = (
    "<script>document.querySelectorAll('.tabs button').forEach(function(b){b.addEventListener('click',function(){"
    "document.querySelectorAll('.tabs button').forEach(function(x){x.classList.remove('on')});"
    "document.querySelectorAll('.tab').forEach(function(x){x.classList.remove('on')});"
    "b.classList.add('on');document.getElementById('t-'+b.dataset.t).classList.add('on');"
    "window.scrollTo({top:0,behavior:'smooth'});});});</script>"
)


def short_date(iso: str) -> str:
    y, m, d = iso.split("-")
    return f"{d}.{m}.{y[2:]}"


def stale_note(rows: list[dict], generated_at: str) -> str:
    """A team whose newest match is old has no current data in our sources: say so instead of hiding it."""
    if not rows:
        return ""
    newest = datetime.fromisoformat(rows[0]["date"]).date()
    now = datetime.fromisoformat(generated_at).date()
    if (now - newest).days <= 21:
        return ""
    return f"<p class='note'>Bu takımın en son maçı {e(short_date(rows[0]['date']))}: güncel ligi için kaynak verimizde sonuç yok, bu yüzden gücü eski verilerden tahmin edildi.</p>"


def comp_name(key: str) -> str:
    return LEAGUES[key][0] if key in LEAGUES else COMP_EXTRA.get(key, key)


def cell(label: str, p: float | None, top: bool = False) -> str:
    return f"<div class='cell{' sel' if top else ''}'>{e(label)}<b>{pct(p)}</b></div>"


def category(title: str, inner: str, open_: bool = False) -> str:
    return f"<details class='cat'{' open' if open_ else ''}><summary>{e(title)}</summary>{inner}</details>"


def markets_tab(i: dict) -> str:
    home, away = i["home"], i["away"]
    res, g = i["result"], i["goals"]
    hp, dc = res["headline_probs"], res["double_chance"]
    topk = max(hp, key=hp.get)
    out = [
        category(
            "Maç sonucu",
            "<div class='cells c3'>"
            + cell(f"{home} (1)", hp["home"], topk == "home")
            + cell("Beraberlik (X)", hp["draw"], topk == "draw")
            + cell(f"{away} (2)", hp["away"], topk == "away")
            + "</div>",
            True,
        ),
        category(
            "Çifte şans",
            "<div class='cells c3'>"
            + cell("1X", dc["1X"])
            + cell("12", dc["12"])
            + cell("X2", dc["X2"])
            + "</div>",
        ),
        category(
            "Beraberlikte iade",
            "<div class='cells c2'>"
            + cell(home, res["draw_no_bet"]["home"])
            + cell(away, res["draw_no_bet"]["away"])
            + "</div><p class='note' style='padding:0 14px 12px;margin:0'>Beraberlik olursa bahis iade sayılır: yalnızca iki sonuç hesaplanır.</p>",
        ),
        category(
            "Handikap (-1)",
            "<div class='cells c2'>"
            + cell(f"{home} (-1)", res["handicap_home_minus_1"])
            + cell(f"{away} (-1)", res["handicap_away_minus_1"])
            + "</div><p class='note' style='padding:0 14px 12px;margin:0'>Takımın en az 2 farkla kazanma ihtimali.</p>",
        ),
    ]
    ou_rows = "".join(
        f"<div class='cells c2' style='padding-bottom:0'>{cell(f'{x["line"]} Üst', x['over'])}{cell(f'{x["line"]} Alt', x['under'])}</div>"
        for x in g["over_under"]
    )
    out.append(category("Toplam gol alt/üst", ou_rows + "<div style='height:12px'></div>", True))
    out.append(
        category(
            "Karşılıklı gol",
            "<div class='cells c2'>" + cell("Var", g["btts_yes"]) + cell("Yok", g["btts_no"]) + "</div>",
        )
    )
    out.append(
        category(
            "Takım golleri",
            "<div class='cells c2'>"
            + cell(f"{home} 1.5 üst", g["home_over_1_5"])
            + cell(f"{away} 1.5 üst", g["away_over_1_5"])
            + cell(f"{home} gol yemez", g["home_clean_sheet"])
            + cell(f"{away} gol yemez", g["away_clean_sheet"])
            + "</div>",
        )
    )
    top = i["scorelines"][:8]
    mx = max(x["p"] for x in top)
    scores = "".join(
        f"<div class='sc'><b>{e(x['score'])}</b><div style='width:{x['p'] / mx * 60:.0f}%'></div><span>{pct(x['p'])}</span></div>"
        for x in top
    )
    out.append(
        category(
            "Skor",
            f"<div style='padding:0 14px 12px'>{scores}<p class='note'>Skor tahmini zordur: en olası skorun bile ihtimali genelde %10-15 civarında.</p></div>",
        )
    )
    dist = g.get("total_goals_distribution")
    if dist:
        mxd = max(dist)
        rows = "".join(
            f"<div class='sc'><b>{'7+' if k == 7 else k}</b><div style='width:{v / mxd * 60:.0f}%'></div><span>{pct(v)}</span></div>"
            for k, v in enumerate(dist)
        )
        out.append(category("Toplam gol sayısı", f"<div style='padding:0 14px 12px'>{rows}</div>"))
    names = {"corners": "Korner", "yellow_cards": "Sarı kart", "shots_on_target": "İsabetli şut"}
    for stat, c in (i.get("counts") or {}).items():
        lines = "".join(
            f"<div class='cells c2' style='padding-bottom:0'>{cell(f'{x["line"]} Üst', x['over'])}{cell(f'{x["line"]} Alt', x['under'])}</div>"
            for x in c["over_under"]
        )
        out.append(
            category(
                names.get(stat, stat),
                f"<p class='note' style='padding:0 14px'>Beklenen toplam: <b>{c['expected']['total']:.1f}</b></p>{lines}<div style='height:12px'></div>",
            )
        )
    return "".join(out)


RES_COLOR = {"W": "var(--g)", "D": "#9aa7a0", "L": "#c62828"}
RES_LABEL = {"W": "G", "D": "B", "L": "M"}


def match_rows(rows: list[dict], team_first: bool = True) -> str:
    return "".join(
        f"<div class='mrow'><span class='d'>{e(short_date(r['date']))}<br><small>{e(comp_name(r['competition']))}</small></span>"
        f"<span style='flex:1'>{'(E)' if r['home'] else '(D)'} {e(r['opponent'])}</span>"
        f"<span class='sc2'>{e(r['score'])}</span><span class='res' style='background:{RES_COLOR[r['result']]}'>{RES_LABEL[r['result']]}</span></div>"
        for r in rows
    )


def form_tab(i: dict) -> str:
    d = i.get("details")
    if not d:
        return "<div class='card mut'>Bu tahmin ayrıntı istatistikleri eklenmeden önce üretildi; bir sonraki yenilemede görünür.</div>"
    home, away = i["home"], i["away"]
    out = []
    for name, rows in ((home, d["recent"]["home"]), (away, d["recent"]["away"])):
        chips = (
            "".join(
                f"<span class='res' style='background:{RES_COLOR[r['result']]};margin-right:4px'>{RES_LABEL[r['result']]}</span>"
                for r in reversed(rows[:5])
            )
            or "<span class='mut'>veri yok</span>"
        )
        first, rest = match_rows(rows[:3]), match_rows(rows[3:])
        more = f"<details class='more'><summary>Tümünü gör</summary>{rest}</details>" if rest else ""
        out.append(
            f"<div class='sec'><h2>{e(name)} · son maçlar</h2></div><div class='card'><p style='margin:0 0 8px'>{chips}</p>"
            f"{first or '<span class=mut>Maç verisi yok.</span>'}{more}{stale_note(rows, i['generated_at'])}</div>"
        )
    h2h = d["h2h"]
    if h2h:
        rows = "".join(
            f"<div class='mrow'><span class='d'>{e(short_date(r['date']))}<br><small>{e(comp_name(r['competition']))}</small></span>"
            f"<span style='flex:1;text-align:right'>{e(r['home'])}</span><span class='sc2'>{e(r['score'])}</span>"
            f"<span style='flex:1'>{e(r['away'])}</span></div>"
            for r in h2h
        )
        out.append(f"<div class='sec'><h2>Aralarındaki maçlar</h2></div><div class='card'>{rows}</div>")
    else:
        out.append(
            "<div class='sec'><h2>Aralarındaki maçlar</h2></div><div class='card mut'>Bu iki takımın veri setimizde ortak maçı yok (2019'dan beri).</div>"
        )
    return "".join(out)


def table_tab(i: dict) -> str:
    d = i.get("details") or {}
    t = d.get("table")
    if not t:
        why = (
            "Bu maç bir Avrupa kupası maçı: güncel sezon puan tablosu için kaynak veride kupa sonuçları henüz yok."
            if i["league"] in ("UCL", "UEL", "UECL")
            else "Bu lig için puan tablosu verisi yok."
        )
        return f"<div class='card mut'>{e(why)}</div>"
    ids = {i["home_team_id"], i["away_team_id"]}
    rows = "".join(
        f"<tr class='{'me' if r['team_id'] in ids else ''}'><td>{r['pos']}</td><td>{e(r['team'])}</td><td>{r['p']}</td>"
        f"<td>{r['w']}-{r['d']}-{r['l']}</td><td>{r['gd']:+d}</td><td><b>{r['pts']}</b></td></tr>"
        for r in t["rows"]
    )
    return (
        f"<div class='sec'><h2>{e(comp_name(i['league']))} {e(t['season'])}</h2></div><div class='card'><table>"
        "<tr><th>#</th><th>Takım</th><th>O</th><th>G-B-M</th><th>Av</th><th>P</th></tr>"
        f"{rows}</table><p class='note'>Tablo, elimizdeki sonuçlardan hesaplandı; resmi tablodan farklı olabilir (eksik maç, puan silme).</p></div>"
    )


def referee_tab(people: dict | None, names: dict[str, str] | None = None) -> str:
    names = names or {}
    ref = (people or {}).get("referee")
    if not ref:
        return (
            "<div class='card mut'>Hakem henüz açıklanmadı ya da bu lig için kaynağımızda hakem bilgisi yok "
            "(hakem bilgisi Premier Lig, La Liga, Bundesliga, Serie A, Ligue 1, Eredivisie, Portekiz ligi ve Şampiyonlar Ligi için var).</div>"
        )
    st = people.get("referee_stats") or {"matches": 0}
    head = f"<div class='card'><b>{e(ref['name'])}</b><div class='mut'>{e(ref.get('nationality') or '')}</div></div>"
    if not st["matches"]:
        return head + "<div class='card mut'>Bu sezon elimizdeki maçlarda bu hakemin yönettiği maç yok.</div>"
    recent = "".join(
        f"<div class='mrow'><span class='d'>{e(short_date(r['date']))}</span><span style='flex:1'>{e(names.get(r['home_id'], r['home_id']))} - {e(names.get(r['away_id'], r['away_id']))}</span><span class='sc2'>{e(r['score'])}</span></div>"
        for r in st["recent"]
    )
    return (
        head + "<div class='sec'><h2>Bu sezon yönettiği maçlar</h2></div><div class='card'><table>"
        f"<tr><td>Maç</td><td><b>{st['matches']}</b></td></tr>"
        f"<tr><td>Maç başı gol</td><td><b>{st['avg_goals']:.2f}</b></td></tr>"
        f"<tr><td>Ev sahibi / beraberlik / deplasman</td><td>{pct(st['home_win'])} / {pct(st['draw'])} / {pct(st['away_win'])}</td></tr>"
        f"<tr><td>2.5 üst</td><td>{pct(st['over_2_5'])}</td></tr></table>"
        f"<p class='note'>Az maçta bu oranlar tesadüf olabilir; tahmine girmez. Kart verisi kaynağımızda yok.</p></div>"
        + (
            f"<div class='sec'><h2>Son yönettikleri</h2></div><div class='card'>{recent}</div>"
            if recent
            else ""
        )
    )


def players_tab(i: dict, people: dict | None) -> str:
    sc = (people or {}).get("scorers") or {}
    out = []
    for side in ("home", "away"):
        rows = sc.get(side) or []
        if not rows:
            body = "<span class='mut'>Bu takım için gol krallığı verisi yok (yalnızca Premier Lig, La Liga, Bundesliga, Serie A, Ligue 1, Eredivisie, Portekiz ligi ve Şampiyonlar Ligi; listede yalnızca ilk 60 oyuncu var).</span>"
        else:
            body = (
                "<table><tr><th>Oyuncu</th><th>Gol</th><th>Asist</th><th>Pen.</th><th>Maç</th></tr>"
                + "".join(
                    f"<tr><td>{e(r['player'])}<div class='mut' style='font-size:11px'>{e(comp_name(r['competition']))}</div></td>"
                    f"<td><b>{r['goals']}</b></td><td>{'-' if r['assists'] is None else r['assists']}</td>"
                    f"<td>{'-' if r['penalties'] is None else r['penalties']}</td><td>{'-' if r['played'] is None else r['played']}</td></tr>"
                    for r in rows
                )
                + "</table>"
            )
        out.append(f"<div class='sec'><h2>{e(i[side])} · golcüler</h2></div><div class='card'>{body}</div>")
    return (
        "".join(out)
        + "<div class='card mut'>Kaynak: football-data.org (ücretsiz). Bilgi amaçlıdır, tahmine girmez.</div>"
    )


def match_page(
    i: dict,
    picks: list[dict],
    logos,
    fav_ids: list[str] | None = None,
    injuries: dict | None = None,
    people: dict | None = None,
    names: dict[str, str] | None = None,
) -> str:
    home, away = i["home"], i["away"]
    res, g = i["result"], i["goals"]
    hp = res["headline_probs"]
    basis = (
        "Bu olasılıklar <b>piyasa oranlarından</b> (komisyonu çıkarılmış) hesaplandı: geçmiş testlerde piyasa modelden daha iyi çıktı."
        if "market" in res["headline_basis"]
        else "Bu olasılıklar <b>istatistiksel modelden</b> geliyor (bu maç için güvenilir oran verisi yok)."
    )
    hero = (
        f"<div class='hero'><div class='mut' style='color:#e6f6ed'>{e(comp_name(i['league']))} · {e(kickoff_tr(i['kickoff_utc']))}</div>"
        f"<div style='display:flex;justify-content:space-around;align-items:center;margin-top:10px;text-align:center'>"
        f"<div>{team_badge(i, 'home', logos, False)}<div><b>{e(home)}</b> {star(i['home_team_id'], fav_ids, i['fixture_id'])}</div></div><b style='font-size:20px'>VS</b>"
        f"<div>{team_badge(i, 'away', logos, False)}<div><b>{e(away)}</b> {star(i['away_team_id'], fav_ids, i['fixture_id'])}</div></div></div></div>"
    )
    hero = hero.replace("<div class='hero'>", "<div class='hero'><a href='/' style='font-size:22px'>‹</a>", 1)
    neutral = (
        "<div class='card mut'>Final tek bir tarafsız sahada oynanır: ev sahibi avantajı hesaba katılmadı.</div>"
        if i.get("neutral_venue")
        else ""
    )
    compare = ""
    if res["market_probs_devig"]:
        m, md = res["market_probs_devig"], res["model_probs"]
        compare = (
            "<div class='sec'><h2>Model ve piyasa</h2></div><div class='card'><table><tr><th></th><th>1</th><th>X</th><th>2</th></tr>"
            f"<tr><td>Piyasa (komisyonsuz)</td><td>{pct(m['home'])}</td><td>{pct(m['draw'])}</td><td>{pct(m['away'])}</td></tr>"
            f"<tr><td>Model</td><td>{pct(md['home'])}</td><td>{pct(md['draw'])}</td><td>{pct(md['away'])}</td></tr></table></div>"
        )
    top = i["scorelines"][:6]
    mx = max(x["p"] for x in top)
    scores = "".join(
        f"<div class='sc'><b>{e(x['score'])}</b><div style='width:{x['p'] / mx * 60:.0f}%'></div><span>{pct(x['p'])}</span></div>"
        for x in top
    )
    tips = "".join(
        f"<tr><td>{e(tr_pick(t['market'], t['pick'], home, away))}</td><td>{pct(t['probability'])}</td><td class='{'ok' if t['lean'] != 'toss-up' else ''}'>{e(LEAN.get(t['lean'], t['lean']))}</td></tr>"
        for t in i["tips"]
    )
    if picks:
        sel = {"H": f"{home} kazanır", "D": "Beraberlik", "A": f"{away} kazanır"}
        rows = "".join(
            f"<tr><td>{e(sel[x['selection']])}</td><td>{pct(x['mean_model_prob'])}</td><td>{pct(x['market_prob_devig'])}</td><td>{x['ev_per_unit']:+.2f}</td><td>%{x['stake_hint_pct_of_bankroll']:.1f}</td></tr>"
            for x in picks
        )
        value = (
            "<div class='card warn'><table><tr><th>Seçim</th><th>Model</th><th>Piyasa</th><th>Değer</th><th>Stake ipucu</th></tr>"
            + rows
            + "</table><p class='note'>Model piyasadan farklı düşündüğünde çıkar. Garanti değildir; geçmiş testlerde hiçbir model piyasayı geçemedi, bu yüzden küçük tut. "
            "Fiyat değişmiş olabilir, işlem yapmadan önce güncel fiyatı kontrol et.</p></div>"
        )
    else:
        value = "<div class='card mut'>Bu maç için verinin desteklediği bir bahis önerisi yok. Bu, hiçbir bahsin iyi olmadığı anlamına gelmez, yalnızca verinin yeterince desteklemediği anlamına gelir.</div>"
    flags = (
        "".join(f"<li>{e(f)}</li>" for f in i["data_quality"]["flags"])
        or "<li>Belirgin bir veri sorunu yok.</li>"
    )
    summary = (
        "<div class='sec'><h2>Sonuç olasılıkları</h2></div><div class='card'>"
        f"<div class='bar'><i style='width:{hp['home'] * 100:.1f}%;background:var(--g)'></i><i style='width:{hp['draw'] * 100:.1f}%;background:#9aa7a0'></i><i style='width:{hp['away'] * 100:.1f}%;background:#e3813a'></i></div>"
        f"<div style='display:flex;justify-content:space-between'><span>{e(home)} <b>{pct(hp['home'])}</b></span><span>Beraberlik <b>{pct(hp['draw'])}</b></span><span>{e(away)} <b>{pct(hp['away'])}</b></span></div>"
        f"<p class='note'>{basis}</p><p class='mut'>Çifte şans: 1X {pct(res['double_chance']['1X'])} · 12 {pct(res['double_chance']['12'])} · X2 {pct(res['double_chance']['X2'])}</p></div>"
        + neutral
        + compare
        + f"<div class='sec'><h2>En olası skorlar</h2></div><div class='card'>{scores}<p class='note'>Skor tahmini zordur: en olası skorun bile ihtimali genelde %10-15 civarında.</p></div>"
        f"<div class='sec'><h2>Goller</h2></div><div class='card'><p>Beklenen gol: {e(home)} <b>{g['expected']['home']:.2f}</b> - {e(away)} <b>{g['expected']['away']:.2f}</b></p>"
        f"<p class='mut'>İki takım da gol atar: {pct(g['btts_yes'])} · 2.5 üst: {pct(next(x['over'] for x in g['over_under'] if x['line'] == 2.5))}</p></div>"
    )
    advice = (
        f"<div class='sec'><h2>Tavsiyeler</h2></div><div class='card'><table><tr><th>Tahmin</th><th>İhtimal</th><th>Eğilim</th></tr>{tips}</table>"
        "<p class='note'>Eğilim = ihtimal ne kadar yüksek. %65+ güçlü eğilim, %55+ eğilim, altı belirsiz. Garanti değil.</p></div>"
        f"<div class='sec'><h2>Bahis önerisi (araştırma amaçlı)</h2></div>{value}"
    )
    quality = (
        f"<div class='sec'><h2>Veri kalitesi</h2></div><div class='card'><ul style='margin:0;padding-left:18px'>{flags}</ul>"
        f"<p class='note'>Model {e(i['model_version'])} · veri {e(i['data_version'])} · tahmin zamanı {e(i['generated_at'])}. Kadro ve sakatlık bilgisi bu tahmine girmez (eksikler yalnızca bilgi olarak gösterilir).</p></div>"
    )
    tabs = (
        ("ozet", "Özet"),
        ("market", "Marketler"),
        ("form", "Form ve maçlar"),
        ("puan", "Puan"),
        ("hakem", "Hakem"),
        ("oyuncu", "Oyuncular"),
        ("eksik", "Eksikler"),
        ("oneri", "Tavsiye"),
        ("veri", "Veri"),
    )
    bar = (
        "<div class='tabs'>"
        + "".join(
            f"<button data-t='{k}' class='{'on' if k == 'ozet' else ''}'>{lab}</button>" for k, lab in tabs
        )
        + "</div>"
    )
    contents = {
        "ozet": summary,
        "market": markets_tab(i),
        "form": form_tab(i),
        "puan": table_tab(i),
        "hakem": referee_tab(people, names),
        "oyuncu": players_tab(i, people),
        "eksik": injuries_card(i, injuries),
        "oneri": advice,
        "veri": quality,
    }
    body = (
        bar
        + "".join(
            f"<section id='t-{k}' class='tab{' on' if k == 'ozet' else ''}'>{contents[k]}</section>"
            for k, _ in tabs
        )
        + TAB_SCRIPT
    )
    return page(f"{home} - {away}", body, "leagues", hero=hero)


def tips_page(rows: list[dict], names: dict[str, tuple[str, str]]) -> str:
    items = "".join(
        f"<a href='/mac/{e(r['fixture_id'])}'><div class='row'><div><b>{e(tr_pick(r['market'], r['pick'], *names[r['fixture_id']]))}</b>"
        f"<div class='mut'>{e(names[r['fixture_id']][0])} - {e(names[r['fixture_id']][1])} · {e(kickoff_tr(r['kickoff_utc']))}</div></div>"
        f"<div style='text-align:right'><span class='ok'>{pct(r['probability'])}</span><div class='mut'>{e(LEAN.get(r['lean'], r['lean']))}</div></div></div></a>"
        for r in rows
        if r["fixture_id"] in names
    )
    return page(
        "Tavsiyeler",
        "<div class='card mut'>İhtimale göre sıralı. Yüksek ihtimal, o seçimin değerli bir bahis olduğu anlamına gelmez: oran düşükse kazanç da küçüktür.</div>"
        f"<div class='card pad0'>{items}</div>",
        "tips",
    )


def _mark(ok: bool) -> str:
    return (
        "<span style='color:var(--g);font-weight:700'>✓</span>"
        if ok
        else "<span style='color:#c62828;font-weight:700'>✗</span>"
    )


def track_match(m: dict) -> str:
    side = {"H": "1", "D": "X", "A": "2"}
    return (
        "<div class='card' style='margin:8px 14px'>"
        f"<div class='mut' style='font-size:12px'>{e(m['date'])} · {e(comp_name(m['league']))}</div>"
        f"<div style='display:flex;justify-content:space-between;gap:8px;align-items:center'><b>{e(m['home'])} - {e(m['away'])}</b>"
        f"<b style='font-size:18px'>{e(m['score'])}</b></div>"
        "<div style='display:flex;flex-wrap:wrap;gap:10px;margin-top:6px;font-size:13px'>"
        f"<span>Sonuç {side[m['pick']]} ({pct(m['pick_prob'])}) {_mark(m['pick_hit'])}</span>"
        f"<span>Skor {e(m['score_pick'])} {_mark(m['score_hit'])}</span>"
        f"<span>2.5 {e(m['ou_pick'])} {_mark(m['ou_hit'])}</span>"
        f"<span>KG {e(m['btts_pick'])} {_mark(m['btts_hit'])}</span></div></div>"
    )


def track_page(t: dict) -> str:
    if not t["available"] or not t["matches_scored"]:
        body = "<div class='card warn'>Henüz sonuçlanmış maç yok. İlk sonuçlar tur bittiğinde burada görünecek.</div>"
    else:
        h = t["headline_1x2"]
        hr = t.get("hit_rates") or {}
        naive = t.get("naive_hit_rates") or {}

        def hit_row(label: str, key: str, note: str = "") -> str:
            v = hr.get(key)
            if not v or not v["n"]:
                return ""
            lo, hi = v["ci95"]
            return (
                f"<tr><td>{e(label)}</td><td>{v['hits']}/{v['n']}</td><td><b>{pct(v['rate'])}</b></td>"
                f"<td class='mut'>{pct(lo)} - {pct(hi)}</td></tr>"
            )

        rows = (
            hit_row("Maç sonucu (1X2)", "1x2")
            + hit_row("2.5 alt/üst", "over_under_2_5")
            + hit_row("Karşılıklı gol", "btts")
            + hit_row("Doğru skor", "correct_score")
        )
        base = ""
        if naive.get("1x2_always_home") is not None:
            base = (
                f"<p class='note'>Karşılaştırma: her maça \"ev sahibi kazanır\" deseydik {pct(naive['1x2_always_home'])}, "
                f'her maça "2.5 üst" deseydik {pct(naive["over_2_5_always_over"])} tutardı. '
                "Aralık, %95 güven aralığıdır: az maçta çok geniş çıkar.</p>"
            )
        tips = "".join(
            f"<tr><td>{e(LEAN.get(k, k))}</td><td>{v['hit']}/{v['n']}</td><td>{pct(v['rate'])}</td></tr>"
            for k, v in t["tips_by_lean"].items()
        )
        few = (
            "Henüz çok az maç var: sonuçlar gürültü sayılır."
            if t["reliability_note"].startswith("noise")
            else "İlk okuma için yeterli maç var; kayıt hâlâ kısa."
        )
        listing = t.get("matches") or []
        shown, rest = listing[:30], listing[30:]
        lst = "".join(track_match(m) for m in shown)
        if rest:
            lst += f"<details class='more' style='margin:0 14px'><summary>Kalan {len(rest)} maçı gör</summary>{''.join(track_match(m) for m in rest)}</details>"
        body = (
            f"<div class='card warn'><b>{t['matches_scored']} maç değerlendirildi.</b> {few}</div>"
            "<div class='sec'><h2>İsabet oranı</h2></div>"
            f"<div class='card'><table><tr><th>Tahmin</th><th>Tuttu</th><th>Oran</th><th>Aralık</th></tr>{rows}</table>{base}</div>"
            f"<div class='card'><table><tr><th>Ölçü</th><th>Değer</th></tr><tr><td>Log kaybı (düşük iyi)</td><td>{h['log_loss']:.3f}</td></tr>"
            f"<tr><td>Lig taban oranı ile kıyas</td><td>{h['log_loss_league_base_rate']:.3f}</td></tr></table></div>"
            f"<div class='sec'><h2>Eğilime göre</h2></div><div class='card'><table><tr><th>Eğilim</th><th>Tuttu</th><th>Oran</th></tr>{tips}</table></div>"
            f"<div class='sec'><h2>Tüm değerlendirilen maçlar</h2></div>{lst}"
        )
    return page(
        "Başarı kaydı",
        "<div class='card mut'>Yayınlanan tahminler maçtan önce kilitlenir; maç bitince gerçek sonuçla karşılaştırılır. Listede yalnızca tahminin maçtan önce üretildiği maçlar vardır.</div>"
        + body,
        "profile",
        back="/profil",
    )


def about_page() -> str:
    return page(
        "Yöntem ve sınırlar",
        "<div class='card'><b>Nasıl tahmin ediyoruz?</b><p>Her takımın saldırı ve savunma gücünü geçmiş maçlardan (yakın maçlar daha ağırlıklı) öğreniyoruz. Buradan skor dağılımını ve ondan sonuç, alt/üst gol, iki takım da gol atar gibi pazarları hesaplıyoruz. Güvenilir oran varsa sonuç olasılığı olarak piyasanın komisyonsuz hâlini kullanıyoruz.</p></div>"
        "<div class='card'><b>Ne kadar iyi?</b><p>2022-24 sezonlarında 3578 maçta test ettik: skor, sonuç, 2.5 alt/üst ve iki takım da gol atar tahminleri, basit ölçütlerden net biçimde iyi. <b>Ama hiçbir model piyasanın kendi olasılıklarını geçemedi.</b> Bu yüzden bahis önerileri düşük güvenle sunulur ve garanti değildir.</p></div>"
        "<div class='card warn'><b>Sınırlar</b><ul><li>Kadro, sakatlık ve hava durumu tahmine girmiyor.</li><li>Süper Lig'in geçmiş verisi daha eksik: tahminleri daha az güvenilir.</li><li>Korner, kart ve şut tahminleri henüz yayında değil.</li><li>Başarı kaydı kısa: anlamlı olması için en az 100 maç gerekir.</li></ul></div>"
        "<div class='card'><b>Sorumlu oyun</b><p>Bu uygulama bahis oynatmaz ve oynamanı önermez; yalnızca olasılık bilgisi verir. 18 yaşından küçükler için değildir. Bahis eğlence olmaktan çıkıyorsa yardım al (ör. begambleaware.org).</p></div>",
        "profile", back="/profil",
    )  # fmt: skip


LEGAL = {
    "privacy": (
        "Gizlilik politikası",
        [
            (
                "p",
                "Bu uygulama futbol maçları için istatistiksel olasılık bilgisi verir. Mümkün olduğunca az kişisel veri işler.",
            ),
            ("h", "Hangi verileri işliyoruz?"),
            (
                "li",
                "Misafir girişi: hiçbir kişisel veri alınmaz; yalnızca tarayıcında imzalı bir oturum çerezi tutulur.",
            ),
            (
                "li",
                "Google ile giriş: Google'ın verdiği hesap kimliği, adın ve e-posta adresin. Şifren bize hiç ulaşmaz.",
            ),
            (
                "li",
                "Telefon ile giriş: numaran saklanmaz; yalnızca tek yönlü bir özet (hash) ve numaranın son 4 hanesi tutulur.",
            ),
            ("li", "Favori takımların ve maçların, hesabına bağlı olarak sunucudaki veritabanında saklanır."),
            ("h", "Ne yapmıyoruz?"),
            (
                "li",
                "Reklam göstermiyoruz, seni takip etmiyoruz, verini satmıyoruz veya üçüncü taraflarla pazarlama için paylaşmıyoruz.",
            ),
            ("li", "Analitik veya izleme çerezi kullanmıyoruz; tek çerez oturum (ve dil tercihi) çerezidir."),
            ("h", "Verilerini silme"),
            (
                "p",
                'Profil sayfasındaki "Verilerimi sil" düğmesi hesabına bağlı tüm verileri (favoriler dahil) siler.',
            ),
            ("h", "Bu belge hakkında"),
            (
                "p",
                "Bu metin bir taslaktır ve hukuki danışmanlık değildir; yayına almadan önce bir hukukçu tarafından gözden geçirilmelidir.",
            ),
        ],
    ),
    "terms": (
        "Kullanım koşulları",
        [
            ("p", "Bu uygulamayı kullanarak aşağıdaki koşulları kabul etmiş olursun."),
            ("h", "Bilgi amaçlıdır"),
            (
                "li",
                "Gösterilen tahminler istatistiksel modellerin olasılık hesaplarıdır; kazanç garantisi vermez ve yatırım veya bahis tavsiyesi değildir.",
            ),
            ("li", "Uygulama bahis oynatmaz, bahis aracılığı yapmaz ve para kabul etmez."),
            (
                "li",
                'Geçmiş testlerde hiçbir model piyasa oranlarını geçemedi; "bahis önerisi" bölümü yalnızca araştırma amaçlıdır.',
            ),
            ("h", "Yaş sınırı ve sorumlu oyun"),
            (
                "li",
                "Uygulama 18 yaş ve üzeri içindir. Bahis oynamak bağımlılık yapabilir; kaybetmeyi göze alamayacağın parayla oynama.",
            ),
            ("li", "Bulunduğun ülkede bahis yasaksa veya kısıtlıysa o kurallara uymak sana aittir."),
            ("h", "Veri ve doğruluk"),
            (
                "li",
                "Veriler üçüncü taraf kaynaklardan gelir ve eksik veya geç olabilir; kadro, sakatlık ve hava durumu tahmine girmez.",
            ),
            ("li", "Hizmet olduğu gibi sunulur; kesintisiz veya hatasız çalışacağı garanti edilmez."),
            ("h", "Bu belge hakkında"),
            (
                "p",
                "Bu metin bir taslaktır ve hukuki danışmanlık değildir; yayına almadan önce bir hukukçu tarafından gözden geçirilmelidir.",
            ),
        ],
    ),
}


def legal_blocks(kind: str) -> tuple[str, list[tuple[str, str]]]:
    """The draft texts plus the sections that carry the owner's identity, contact and the KVKK rights (Türkiye)."""
    title, base = LEGAL[kind]
    base = list(base)
    tail = base[-2:]  # "About this document" heading + the draft notice stay last
    base = base[:-2]
    who = f"Veri sorumlusu: {OWNER} ({APP_NAME}). İletişim ve veri silme talepleri: {CONTACT_EMAIL}"
    if kind == "privacy":
        extra = [
            ("h", "Veri sorumlusu"),
            ("p", who),
            ("h", "Verilerin nerede işleniyor?"),
            (
                "li",
                "Site Vercel (ABD) üzerinde çalışır ve Google ile giriş Google (ABD) üzerinden yapılır; kişisel verilerin yurt dışındaki bu hizmet sağlayıcılarda işlenebilir.",
            ),
            ("li", "Telefonla giriş etkinleştirilirse doğrulama kodu Twilio (ABD) üzerinden gönderilir."),
            (
                "li",
                "Hesap kaydın ve favorilerin, bulut üzerinde barındırılan Turso (libSQL) veritabanında saklanır; bu hizmet sağlayıcı yurt dışındadır.",
            ),
            ("h", "Saklama süresi"),
            (
                "p",
                'Hesap verilerin, sen silene kadar saklanır. Profil sayfasındaki "Verilerimi sil" düğmesiyle ya da yukarıdaki e-posta adresine yazarak silebilirsin.',
            ),
            ("h", "KVKK kapsamındaki haklarınız"),
            (
                "p",
                "6698 sayılı Kişisel Verilerin Korunması Kanunu'nun 11. maddesi uyarınca; verilerinin işlenip işlenmediğini öğrenme, işlenmişse bilgi isteme, düzeltilmesini veya silinmesini isteme ve verilerinin aktarıldığı üçüncü kişileri bilme haklarına sahipsin. Başvurunu yukarıdaki e-posta adresine yapabilirsin.",
            ),
            ("h", "Çocuklar"),
            ("p", "Uygulama 18 yaşından küçükler için değildir."),
        ]
    else:
        extra = [
            ("h", "Hizmet sağlayıcı"),
            ("p", who),
            ("h", "Uygulanacak hukuk"),
            ("p", "Bu koşullar Türkiye Cumhuriyeti hukukuna tabidir."),
        ]
    return title, base + extra + tail


def legal_page(kind: str) -> str:
    """Public privacy / terms pages (a Google consent screen needs reachable URLs). DRAFTS, not legal advice."""
    title, blocks = legal_blocks(kind)
    out, items = [], []

    def flush():
        if items:
            out.append("<ul>" + "".join(items) + "</ul>")
            items.clear()

    for tag, text in blocks:
        if tag == "li":
            items.append(f"<li>{e(text)}</li>")
            continue
        flush()
        out.append(f"<b>{e(text)}</b>" if tag == "h" else f"<p>{e(text)}</p>")
    flush()
    out.append(
        f"<p class='note'>{e(OWNER)} · <a href='mailto:{e(CONTACT_EMAIL)}'>{e(CONTACT_EMAIL)}</a> · {e(COUNTRY)}</p>"
    )
    return page(title, "<div class='card'>" + "".join(out) + "</div>", "profile", back="/giris")


def upgrade_page() -> str:
    return page(
        "Pro özellik",
        "<div class='card'><b>Bu bölüm Pro planına ait.</b>"
        "<p class='mut'>Pro planı henüz satışa açılmadı. Açıldığında buradan duyurulacak.</p>"
        "<p><a href='/'><b>‹ Ana sayfaya dön</b></a></p></div>",
    )


def profile_page(user, plan: str = "free") -> str:
    kind = {"guest": "Misafir", "google": "Google hesabı", "apple": "Apple hesabı", "phone": "Telefon"}.get(
        user.provider, user.provider
    )
    body = (
        f"<div class='card'><div style='display:flex;gap:12px;align-items:center'>{badge(user.name, 'var(--g)')}<div><b>{e(user.name)}</b>"
        f"<div class='mut'>{e(kind)}{(' · ' + e(user.email)) if user.email else ''}</div>"
        f"<div class='mut'>Plan: <b>{'Pro' if plan == 'pro' else 'Ücretsiz'}</b></div></div></div></div>"
        "<div class='card pad0'><a href='/basari-kaydi'><div class='row'>Başarı kaydı <span>›</span></div></a>"
        "<a href='/hakkinda'><div class='row'>Yöntem ve sınırlar <span>›</span></div></a>"
        "<a href='/sunum'><div class='row'>Proje özeti <span>›</span></div></a>"
        "<a href='/gizlilik'><div class='row'>Gizlilik politikası <span>›</span></div></a>"
        "<a href='/kosullar'><div class='row'>Kullanım koşulları <span>›</span></div></a>"
        "<form method='post' action='/hesabi-sil' onsubmit=\"return confirm('Hesabına bağlı tüm veriler (favoriler) silinecek. Emin misin?')\">"
        "<button class='row' style='width:100%;background:none;border:0;font:inherit;color:#c62828;text-align:left;cursor:pointer'>Verilerimi sil</button></form>"
        "<form method='post' action='/cikis'><button class='row' style='width:100%;background:none;border:0;font:inherit;color:#c62828;text-align:left;cursor:pointer'>Çıkış yap</button></form></div>"
        "<div class='card mut'>Telefonuna eklemek için tarayıcı menüsünden <b>Ana ekrana ekle</b> seçeneğini kullan: uygulama gibi açılır.</div>"
    )
    return page("Profil", body, "profile")


def not_found(msg: str) -> str:
    return page(
        "Bulunamadı",
        f"<div class='card'><p>{e(msg)}</p><p><a href='/'><b>‹ Ana sayfaya dön</b></a></p></div>",
    )


def star(team_id: str, fav_ids: list[str] | None, fixture_id: str) -> str:
    on = team_id in (fav_ids or [])
    return (
        f"<form method='post' action='/favori/{e(team_id)}?next=/mac/{e(fixture_id)}' style='display:inline'>"
        f"<button style='background:none;border:0;font-size:20px;cursor:pointer;color:#ffd54f' title='Favori'>{'★' if on else '☆'}</button></form>"
    )


def form_section(i: dict) -> str:
    form = i.get("form")
    if not form or not (form.get("home") or form.get("away")):
        return ""
    col = {"W": "var(--g)", "D": "#9aa7a0", "L": "#c62828"}
    lab = {"W": "G", "D": "B", "L": "M"}

    def chips(rows: list[dict]) -> str:
        return (
            "".join(
                f"<span title='{e(r['date'])} {e(r['opponent'])} {e(r['score'])}' style='display:inline-flex;width:30px;height:30px;border-radius:50%;"
                f"align-items:center;justify-content:center;color:#fff;font-weight:700;background:{col[r['result']]};margin-right:5px'>{lab[r['result']]}</span>"
                for r in reversed(rows)  # oldest to newest, left to right
            )
            or "<span class='mut'>veri yok</span>"
        )

    def last(rows: list[dict]) -> str:
        return "".join(
            f"<div class='mut'>{'(E)' if r['home'] else '(D)'} {e(r['opponent'])} {e(r['score'])}</div>"
            for r in rows[:3]
        )

    return (
        "<div class='sec'><h2>Son 5 maç (G galibiyet, B beraberlik, M mağlubiyet)</h2></div><div class='card'>"
        f"<p><b>{e(i['home'])}</b><br>{chips(form['home'])}</p>{last(form['home'])}"
        f"<p style='margin-top:12px'><b>{e(i['away'])}</b><br>{chips(form['away'])}</p>{last(form['away'])}</div>"
    )


def goals_chart(g: dict) -> str:
    dist = g.get("total_goals_distribution")
    if not dist:
        return ""
    mx = max(dist)
    rows = "".join(
        f"<div class='sc'><b>{'7+' if k == 7 else k}</b><div style='width:{v / mx * 60:.0f}%'></div><span>{pct(v)}</span></div>"
        for k, v in enumerate(dist)
    )
    return f"<div class='sec'><h2>Toplam gol dağılımı</h2></div><div class='card'>{rows}<p class='note'>Maçta toplam kaç gol olacağının ihtimali.</p></div>"


def favorites_page(matches: list[dict], fav_names: list[tuple[str, str]], logos) -> str:
    chips = (
        "".join(f"<span class='pill'>⭐ {e(n)}</span>" for _, n in fav_names)
        or "<span class='mut'>Henüz favori takımın yok. Bir maç sayfasında takımın yanındaki ☆ işaretine dokun.</span>"
    )
    body = f"<div class='card'><b>Favori takımlarım</b><div class='chips' style='padding:8px 0 0;flex-wrap:wrap'>{chips}</div></div>"
    if fav_names:
        body += "<div class='sec'><h2>Yaklaşan maçları</h2></div>" + (
            "".join(match_row(i, logos) for i in matches)
            or "<div class='card mut'>Favori takımlarının yaklaşan maçı yok.</div>"
        )
    return page("Favoriler", body, "fav")


def search_page(q: str, results: list[dict], logos) -> str:
    form = (
        f"<form method='get' action='/ara' style='padding:12px 14px'><input name='q' value='{e(q)}' placeholder='Takım ara' autofocus "
        "style='width:100%;padding:12px 14px;border-radius:12px;border:1px solid var(--line);font:inherit'></form>"
    )
    if not q.strip():
        return page(
            "Ara",
            form + "<div class='card mut'>Bir takım adı yaz: yaklaşan maçlarını gösteririz.</div>",
            "home",
            back="/",
        )
    body = (
        "".join(match_row(i, logos) for i in results)
        or f"<div class='card mut'>“{e(q)}” için yaklaşan maç bulunamadı.</div>"
    )
    return page("Ara", form + body, "home", back="/")


def sunum_page() -> str:
    return page(
        "Proje özeti",
        "<div class='card'><b>Bu proje ne yapar?</b><p>Altı ligin (Premier Lig, La Liga, Bundesliga, Serie A, Ligue 1, Süper Lig) yaklaşan maçları için "
        "sonuç, skor, gol ve karşılıklı gol tahmini üretir. Bahis oynatmaz; olasılık bilgisi verir.</p></div>"
        "<div class='card'><b>Hangi verilerle?</b><ul><li>Maç sonuçları ve fikstür: openfootball (kamu malı) ve football-data.org.</li>"
        "<li>Bahis oranları (olasılığa çevrilir): The Odds API.</li><li>Korner/kart/şut istatistikleri: Highlightly (şimdilik araştırma amaçlı).</li></ul></div>"
        "<div class='card'><b>Nasıl çalışır?</b><p>Her takımın saldırı ve savunma gücü geçmiş maçlardan öğrenilir; yakın maçlar daha ağırlıklıdır. "
        "Buradan skor dağılımı çıkarılır, ondan da sonuç, alt/üst gol ve karşılıklı gol ihtimalleri hesaplanır. Bu tahminler maçtan önce kilitlenir ve "
        "maç bitince gerçek sonuçla karşılaştırılır (Başarı kaydı).</p></div>"
        "<div class='card'><b>Ne kadar doğru?</b><p>Gelecekten bilgi sızmasın diye testler yalnızca zamana göre yapıldı (rastgele ayırma yok). 2022-24 sezonlarında "
        "3578 maçta: skor, sonuç, 2.5 alt/üst ve karşılıklı gol tahminleri basit ölçütlerden net biçimde iyi. Önemli dürüst bulgu: "
        "<b>hiçbir model bahis piyasasının kendi olasılıklarını geçemedi</b>; bu yüzden sonuç tahmininde güvenilir oran varsa piyasa kullanılır.</p></div>"
        "<div class='card warn'><b>Sınırlar</b><ul><li>Kadro ve sakatlık bilgisi tahmine girmiyor.</li><li>Süper Lig verisi daha eksik.</li>"
        "<li>Başarı kaydı kısa: anlamlı olması için en az 100 maç gerekir.</li><li>Garanti verilmez; 18+.</li></ul></div>"
        "<div class='card'><b>Mühendislik</b><p>Her tahmin içerik özetli ve değişmez dosyadır; testler her değişiklikte otomatik koşar; "
        "gerçek veri kaynakları lisans şartlarına göre incelenmiştir.</p></div>",
        "profile", back="/profil",
    )  # fmt: skip


def phone_page(step: str, masked: str = "", error: str = "") -> str:
    err = f"<div class='card warn'>{e(error)}</div>" if error else ""
    box = "width:100%;padding:12px;border-radius:10px;border:1px solid var(--line);font:inherit"
    if step == "code":
        form = (
            f"<div class='card'><p>{e(masked)} numarasına bir SMS kodu gönderdik.</p><form method='post' action='/giris/telefon/dogrula'>"
            f"<input name='code' inputmode='numeric' autocomplete='one-time-code' placeholder='Kod' style='{box}'>"
            "<button class='btn' style='background:var(--g);color:#fff;margin-top:10px'>Doğrula</button></form></div>"
        )
    else:
        form = (
            "<div class='card'><p>Telefon numaranı yaz: sana tek kullanımlık bir SMS kodu göndereceğiz. Numaran saklanmaz.</p>"
            f"<form method='post' action='/giris/telefon/kod'><input name='phone' inputmode='tel' placeholder='+90 5xx xxx xx xx' style='{box}'>"
            "<button class='btn' style='background:var(--g);color:#fff;margin-top:10px'>Kod gönder</button></form></div>"
        )
    return page("Telefonla giriş", err + form, "profile", back="/giris")
