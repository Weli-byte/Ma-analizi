import json
import ssl
from datetime import UTC, datetime

import pytest

from src.config import SourceEntry, SourcesConfig, load_config
from src.data import download as dl
from src.data.download import DownloadError, FetchError, download_all, download_file, register_manual

GOOD = b"Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR\nTST,13/08/2021,15:00,Alpha FC,Beta Utd,2,0,H\n"
NOW = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)


def sources(**over):
    base = dict(
        primary=SourceEntry(
            id="primary", provider="p", origin="official", url_template="https://p/{season_code}/{div}.csv"
        ),
        fallbacks=[
            SourceEntry(
                id="mirror",
                provider="m",
                origin="archive",
                url_template="https://m/{snapshot}/{season_code}/{div}.csv",
                snapshot="2026",
            )
        ],
        allow_fallback=True,
        retries=3,
        backoff_seconds=1.0,
        timeout_seconds=5.0,
    )
    base.update(over)
    return SourcesConfig(**base)


def run(project, fetch, cfg=None, sleeps=None):
    league = load_config("leagues", project / "configs").leagues["DEMO"]
    out = project / "data/raw/football_data"
    return download_file(
        "DEMO", league, "2024-25", cfg or sources(), out, project / "data/provenance/football_data",
        fetch=fetch, sleep=(sleeps.append if sleeps is not None else lambda s: None), now=lambda: NOW,
    )  # fmt: skip


def test_official_download_records_official_provenance(project):
    seen = []
    rec = run(project, lambda url, t: seen.append(url) or GOOD)
    assert seen == ["https://p/2425/TST.csv"] and rec["origin"] == "official"
    side = json.loads((project / "data/provenance/football_data/TST_2425.csv.json").read_text())
    assert side["origin"] == "official" and side["retrieved_at_utc"] == "2026-09-26T10:00:00+00:00"
    assert (project / "data/raw/football_data/TST_2425.csv").read_bytes() == GOOD


def test_retries_with_exponential_backoff_then_succeeds(project):
    calls, sleeps = [], []

    def flaky(url, timeout):
        calls.append(url)
        if len(calls) < 3:
            raise FetchError("timeout")
        return GOOD

    rec = run(project, flaky, sleeps=sleeps)
    assert len(calls) == 3 and sleeps == [1.0, 2.0] and rec["origin"] == "official"


def test_tls_failure_is_not_retried_and_falls_back_with_archive_provenance(project):
    calls = []

    def fetch(url, timeout):
        calls.append(url)
        if url.startswith("https://p/"):
            raise FetchError("TLS verification failed: self-signed certificate", retryable=False)
        return GOOD

    rec = run(project, fetch)
    assert calls == ["https://p/2425/TST.csv", "https://m/2026/2425/TST.csv"]  # one TLS attempt, no retry
    assert rec["origin"] == "archive" and "mirror" in rec["note"]  # never labelled official


def test_no_fallback_unless_explicitly_enabled_and_error_is_actionable(project):
    def always_tls(url, timeout):
        raise FetchError("TLS verification failed", retryable=False)

    with pytest.raises(DownloadError) as e:
        run(project, always_tls, cfg=sources(allow_fallback=False))
    msg = str(e.value)
    assert "TLS verification failed" in msg and "download" in msg and "--register-manual" in msg
    assert not (project / "data/raw/football_data/TST_2425.csv").exists()


@pytest.mark.parametrize("bad", [b"", b"<html>blocked</html>", b"Foo\n1\n"])
def test_invalid_content_is_never_stored_and_existing_file_survives(project, bad):
    target = project / "data/raw/football_data/TST_2425.csv"
    target.write_bytes(GOOD)
    before = target.read_bytes()
    with pytest.raises(DownloadError, match="invalid content"):
        run(project, lambda url, t: bad, cfg=sources(allow_fallback=False))
    assert target.read_bytes() == before  # old valid copy untouched
    assert not list(target.parent.glob(".*.part"))


def test_manual_registration_records_manual_origin(project):
    raw = project / "data/raw/football_data/TST_2425.csv"
    raw.write_bytes(GOOD)
    league = load_config("leagues", project / "configs").leagues["DEMO"]
    rec = register_manual(
        raw.parent, project / "data/provenance/football_data", "TST_2425.csv", "https://x/y.csv", league, NOW
    )
    assert rec["origin"] == "manual" and rec["retrieved_at_utc"].startswith("2026-09-26")
    raw.write_bytes(b"<html>")
    with pytest.raises(Exception, match="HTML"):
        register_manual(raw.parent, project / "data/provenance/football_data", "TST_2425.csv", "u", league)


def test_download_all_reports_failures_per_file(project):
    def fetch(url, timeout):
        if "2324" in url:
            raise FetchError("boom", retryable=False)
        return GOOD.replace(b"TST", b"TST")

    done, failed = download_all(project, fetch=fetch, sleep=lambda s: None)
    assert len(done) == 2 and len(failed) == 1 and "TST_2324.csv" in failed[0]


def test_default_fetch_uses_verifying_context_and_maps_tls_errors(monkeypatch):
    captured = {}

    class Boom(Exception):
        pass

    def fake_urlopen(req, timeout, context):
        captured["ctx"] = context
        raise dl.urllib.error.URLError(ssl.SSLCertVerificationError("self-signed"))

    monkeypatch.setattr(dl.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(FetchError, match="TLS verification failed") as e:
        dl.default_fetch("https://example.invalid/x", 1)
    assert e.value.retryable is False
    ctx = captured["ctx"]
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True


def test_default_fetch_network_errors_are_retryable(monkeypatch):
    def fake_urlopen(req, timeout, context):
        raise dl.urllib.error.URLError(OSError("connection reset"))

    monkeypatch.setattr(dl.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(FetchError) as e:
        dl.default_fetch("https://example.invalid/x", 1)
    assert e.value.retryable is True
