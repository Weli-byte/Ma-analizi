"""Android app link (ADR 0098): the site serves Digital Asset Links for the Welliddia app and a maskable icon."""

import json
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from src.api.app import create_app

ROOT = Path(__file__).resolve().parents[1]


def test_assetlinks_names_the_package_and_only_set_fingerprints(tmp_path):
    c = TestClient(create_app(tmp_path, keys=["k"]))
    r = c.get("/.well-known/assetlinks.json")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    (link,) = r.json()
    cfg = yaml.safe_load((ROOT / "configs" / "android" / "assetlinks.yaml").read_text(encoding="utf-8"))
    assert link["target"]["package_name"] == cfg["package_name"] == "app.welliddia.tahmin"
    assert link["target"]["sha256_cert_fingerprints"] == [f for f in cfg["fingerprints"].values() if f]
    assert all(len(f.split(":")) == 32 for f in link["target"]["sha256_cert_fingerprints"])


def test_manifest_has_a_maskable_icon_and_the_twa_points_at_it(tmp_path):
    c = TestClient(create_app(tmp_path, keys=["k"]))
    m = c.get("/manifest.webmanifest").json()
    assert any(i["purpose"] == "maskable" for i in m["icons"]) and c.get("/icon-maskable-512.png").content[:8] == b"\x89PNG\r\n\x1a\n"
    twa = json.loads((ROOT / "android" / "twa-manifest.json").read_text(encoding="utf-8"))
    assert twa["packageId"] == "app.welliddia.tahmin" and twa["name"] == "Welliddia" and twa["maskableIconUrl"].endswith("/icon-maskable-512.png")
    assert twa["signingKey"]["alias"] == "upload"
