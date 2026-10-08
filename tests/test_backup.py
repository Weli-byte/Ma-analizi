"""Backup script: round-trips real files and detects corruption."""

import zipfile
from datetime import UTC, datetime

from scripts.backup_artifacts import backup, verify


def test_backup_contains_state_excludes_env_and_detects_corruption(tmp_path):
    (tmp_path / "artifacts" / "odds").mkdir(parents=True)
    (tmp_path / "artifacts" / "odds" / "quotes.jsonl").write_text('{"a": 1}\n', encoding="utf-8")
    (tmp_path / "artifacts" / ".env").write_text("SECRET=1", encoding="utf-8")
    z = backup(tmp_path, tmp_path / "out", datetime(2026, 10, 8, tzinfo=UTC))
    names = zipfile.ZipFile(z).namelist()
    assert "artifacts/odds/quotes.jsonl" in names and "MANIFEST.json" in names
    assert not any(n.endswith(".env") for n in names) and verify(z) == []
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(z) as src, zipfile.ZipFile(bad, "w") as dst:
        for n in src.namelist():
            dst.writestr(n, b"tampered" if n.endswith("quotes.jsonl") else src.read(n))
    assert verify(bad) == ["artifacts/odds/quotes.jsonl"]
