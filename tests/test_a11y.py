"""Findings of the 2026-10-20 Lighthouse audit of the live site: a main landmark on every page and enough text contrast."""

import re

from src.web import pages


def _lum(hex_color: str) -> float:
    rgb = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a: str, b: str) -> float:
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _css_var(name: str, block: str = "light") -> str:
    """A colour of the light `:root{...}` block or of the dark `@media (prefers-color-scheme:dark){:root{...}}` block."""
    css = pages.CSS
    scope = (
        css.split("@media (prefers-color-scheme:dark)")[1].split("}}")[0]
        if block == "dark"
        else css.split("@media")[0]
    )
    value = re.search(rf"--{name}:(#[0-9a-fA-F]{{3,6}})", scope).group(1)
    return "#" + "".join(c * 2 for c in value[1:]) if len(value) == 4 else value


def test_white_text_on_the_brand_green_and_muted_text_meet_wcag_aa():
    assert _contrast("#ffffff", _css_var("g")) >= 4.5  # tabs, tags, hero: white on --g
    assert _contrast("#ffffff", _css_var("gd")) >= 4.5
    assert _contrast(_css_var("mut"), _css_var("bg")) >= 4.5  # secondary text on the page background
    assert _contrast(_css_var("mut"), _css_var("card")) >= 4.5
    for surface in ("bg", "card"):  # dark theme: muted and normal text on its surfaces
        assert _contrast(_css_var("mut", "dark"), _css_var(surface, "dark")) >= 4.5
        assert _contrast(_css_var("fg", "dark"), _css_var(surface, "dark")) >= 4.5


def test_green_text_is_readable_on_every_surface_in_both_themes():
    # --gt is the green used for TEXT (links, tags, "ok" figures, active nav); --g/--gd stay backgrounds under white text
    for block in ("light", "dark"):
        for surface in ("bg", "card", "gl"):
            assert _contrast(_css_var("gt", block), _css_var(surface, block)) >= 4.5, (block, surface)


def test_initials_badges_have_enough_contrast_for_every_hue():
    import colorsys

    assert pages.hue("Arsenal").endswith(",45%,30%)")
    for hue in range(0, 360, 5):  # white initials on hsl(hue,45%,30%), the yellow-green hues are the lightest
        r, g, b = colorsys.hls_to_rgb(hue / 360, 0.30, 0.45)
        colour = f"#{round(r * 255):02x}{round(g * 255):02x}{round(b * 255):02x}"
        assert _contrast("#ffffff", colour) >= 4.5, hue


def test_every_page_has_one_main_landmark():
    assert pages.page("Profil", "<p>x</p>").count("<main>") == 1
    login = pages.login_page({"google": False, "apple": False, "phone": False}, None)
    assert login.count("<main") == 1 and login.count("</main>") == 1


def test_privacy_page_discloses_googles_own_cookies():
    html = pages.legal_page("privacy")
    assert "NID" in html and "Google kendi çerezlerini" in html
