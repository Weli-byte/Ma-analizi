"""Link-preview meta tags, robots.txt / sitemap.xml and the per-competition table on the track-record page."""

from pathlib import Path

from fastapi.testclient import TestClient

from src.api.app import create_app
from src.web import pages
from src.web.i18n import translate_html

ROOT = Path(__file__).resolve().parents[1]


def _match(league, pick=True, ou=False, btts=True):
    return {"league": league, "pick_hit": pick, "ou_hit": ou, "btts_hit": btts}


def test_league_table_counts_hits_per_competition_and_flags_small_groups():
    html = pages.league_hit_table([_match("EPL"), _match("EPL", pick=False), _match("LALIGA", ou=True)])
    assert "Premier Lig" in html and "1/2 (%50)" in html  # EPL: 1 of 2 on 1X2
    assert "1/1 (%100)" in html  # LALIGA over/under
    assert "20'den az" in html  # the noise warning is always printed
    assert pages.league_hit_table([]) == ""


def test_league_table_header_survives_english_translation():
    html = translate_html(pages.league_hit_table([_match("SUPERLIG")]))
    assert "League / cup" in html and "Super Lig" in html  # the competition name is not mangled


def test_pages_carry_description_and_link_preview_tags():
    html = pages.page("Profil", "<p>x</p>")
    assert "<meta name='description'" in html and "og:title' content='Profil - Welliddia'" in html
    assert "kazanç garantisi vermez" in html  # no guaranteed-win language in the preview text
    assert "og:image" not in html  # needs an absolute URL we do not know


def test_robots_and_sitemap_list_only_public_pages(tmp_path, monkeypatch):
    monkeypatch.setenv("FORECAST_USER_DB", str(tmp_path / "u.sqlite"))
    c = TestClient(create_app(ROOT, keys=[]), follow_redirects=False)
    robots = c.get("/robots.txt", headers={"host": "example.test"})
    assert (
        robots.status_code == 200 and "Disallow: /v1/" in robots.text and "Disallow: /profil" in robots.text
    )
    assert "Sitemap: https://example.test/sitemap.xml" in robots.text
    site = c.get("/sitemap.xml", headers={"host": "example.test"})
    assert site.headers["content-type"].startswith("application/xml")
    for path in ("/giris", "/gizlilik", "/kosullar", "/sunum"):
        assert f"<loc>https://example.test{path}</loc>" in site.text
    assert "/profil" not in site.text and "/basari-kaydi" not in site.text


def test_link_preview_image_and_url_are_absolute_and_come_from_the_validated_host(tmp_path, monkeypatch):
    monkeypatch.setenv("FORECAST_USER_DB", str(tmp_path / "u.sqlite"))
    c = TestClient(create_app(ROOT, keys=[]), follow_redirects=False)
    for path in ("/giris", "/gizlilik", "/kosullar"):
        html = c.get(path, headers={"host": "example.test"}).text
        assert "property='og:image' content='https://example.test/icon-512.png'" in html
        assert f"property='og:url' content='https://example.test{path}'" in html
        assert "name='twitter:image' content='https://example.test/icon-512.png'" in html
    image = c.get("/icon-512.png")
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    assert image.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_a_forged_host_header_adds_no_preview_tags_and_no_sitemap(tmp_path, monkeypatch):
    from src.web.routes import public_base

    for bad in (
        None,
        "",
        "ex ample.test",
        "evil.test'><script>",
        "https://x",
        "a" * 300,
        "-bad.test",
        "bad..test:99999999",
    ):
        assert public_base(bad) is None, bad
    assert public_base("localhost:8000") == "http://localhost:8000"
    assert public_base("mac-tahmin-eight.vercel.app") == "https://mac-tahmin-eight.vercel.app"
    monkeypatch.setenv("FORECAST_USER_DB", str(tmp_path / "u.sqlite"))
    c = TestClient(create_app(ROOT, keys=[]), follow_redirects=False)
    html = c.get("/giris", headers={"host": "ex ample.test"}).text
    assert "og:image" not in html and "<meta name='description'" in html
    assert c.get("/sitemap.xml", headers={"host": "ex ample.test"}).status_code == 400
    assert "Sitemap:" not in c.get("/robots.txt", headers={"host": "ex ample.test"}).text
