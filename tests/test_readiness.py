"""The readiness report (ADR 0081): names only, never a value."""

from src import readiness


def test_a_set_and_a_missing_item_are_reported_without_any_value():
    env = {"FOOTBALL_DATA_ORG_API_KEY": "SECRET-VALUE-123", "TWILIO_ACCOUNT_SID": "AC-secret", "TWILIO_AUTH_TOKEN": ""}
    rows = {r["item"]: r for r in readiness.local_status(env)}
    assert rows["football-data.org key"]["status"] == "SET" and rows["football-data.org key"]["next"] == ""
    phone = rows["Phone sign-in (Twilio Verify)"]
    assert phone["status"] == "NOT_SET" and phone["missing"] == ["TWILIO_AUTH_TOKEN", "TWILIO_VERIFY_SID"]               # all three are needed; an empty value counts as missing
    text = readiness.render(readiness.local_status(env), None, "basic")
    assert "SECRET-VALUE-123" not in text and "AC-secret" not in text and "football-data.org key: SET" in text and "Highlightly planı" in text and "basic" in text


def test_the_github_section_lists_names_only_and_says_so_when_gh_is_unavailable():
    rows = readiness.github_status({"VERCEL_TOKEN", "TELEGRAM_BOT_TOKEN"})
    by = {r["item"]: r["status"] for r in rows}
    assert by["VERCEL_TOKEN"] == "SET" and by["DATA_REPO_DEPLOY_KEY"] == "NOT_SET"
    assert all("UNKNOWN" in r["status"] for r in readiness.github_status(None))


def test_the_command_runs_and_the_real_plan_flag_is_read(capsys, monkeypatch):
    monkeypatch.setattr(readiness, "load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert readiness.main([]) == 0
    out = capsys.readouterr().out
    assert "== Bilgisayar" in out and "Anthropic key: NOT_SET" in out and readiness.stats_plan() in ("basic", "pro")
