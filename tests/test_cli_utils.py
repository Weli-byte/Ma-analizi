"""`.env` loader used by every `python -m ...` entrypoint via `configure_output`."""

import os

from src.cli_utils import load_dotenv


def test_load_dotenv_sets_unset_variables(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_DOTENV_KEY", raising=False)
    (tmp_path / ".env").write_text("TEST_DOTENV_KEY=secret-value\n", encoding="utf-8")
    load_dotenv(tmp_path / ".env")
    assert os.environ["TEST_DOTENV_KEY"] == "secret-value"


def test_load_dotenv_never_overrides_a_real_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_DOTENV_KEY2", "real-value")
    (tmp_path / ".env").write_text("TEST_DOTENV_KEY2=file-value\n", encoding="utf-8")
    load_dotenv(tmp_path / ".env")
    assert os.environ["TEST_DOTENV_KEY2"] == "real-value"


def test_load_dotenv_ignores_comments_and_blank_lines(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_DOTENV_KEY3", raising=False)
    (tmp_path / ".env").write_text("# comment\n\nTEST_DOTENV_KEY3=value3\n", encoding="utf-8")
    load_dotenv(tmp_path / ".env")
    assert os.environ["TEST_DOTENV_KEY3"] == "value3"


def test_load_dotenv_missing_file_is_a_noop(tmp_path):
    load_dotenv(tmp_path / "nope.env")  # must not raise


def test_load_dotenv_malformed_line_is_skipped_not_fatal(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_DOTENV_KEY4", raising=False)
    (tmp_path / ".env").write_text("not a valid line\nTEST_DOTENV_KEY4=ok\n", encoding="utf-8")
    load_dotenv(tmp_path / ".env")
    assert os.environ["TEST_DOTENV_KEY4"] == "ok"
