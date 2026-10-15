"""User store (ADR 0061): the SQLite backend behaves as before, and the Turso wire format matches Turso's documented
HTTP API (payloads below are the documented shapes). The real network round trip is `tests/integration/test_turso_live.py`."""

import pytest

from src.web.store import (
    StoreError,
    UserStore,
    parse_pipeline,
    pipeline_body,
    turso_endpoint,
)


@pytest.fixture(autouse=True)
def _no_ambient_config(monkeypatch):
    for name in ("FORECAST_TURSO_URL", "FORECAST_TURSO_TOKEN", "FORECAST_USER_DB"):
        monkeypatch.delenv(name, raising=False)


def test_sqlite_backend_favourites_roundtrip_and_delete(tmp_path):
    store = UserStore(tmp_path)
    assert store.backend == "sqlite" and store.path == tmp_path / "artifacts" / "users.sqlite"
    store.touch("u1", "google", "Ada", "ada@example.com")
    assert store.toggle("u1", "t1") is True
    assert store.toggle("u1", "t2") is True
    assert store.favorites("u1") == ["t1", "t2"]
    assert store.toggle("u1", "t1") is False
    assert store.favorites("u1") == ["t2"]
    store.delete_user("u1")
    assert store.favorites("u1") == []


def test_turso_pipeline_body_matches_documented_shape():
    body = pipeline_body(
        [("SELECT * FROM users WHERE name = ?", ("Turso",)), ("SELECT ?, ?, ?", (7, None, 1.5))]
    )
    assert body["requests"][0] == {
        "type": "execute",
        "stmt": {"sql": "SELECT * FROM users WHERE name = ?", "args": [{"type": "text", "value": "Turso"}]},
    }
    assert body["requests"][1]["stmt"]["args"] == [
        {"type": "integer", "value": "7"},
        {"type": "null"},
        {"type": "float", "value": 1.5},
    ]
    assert body["requests"][-1] == {"type": "close"}


def test_turso_response_is_decoded_into_python_values():
    payload = {
        "baton": None,
        "base_url": None,
        "results": [
            {
                "type": "ok",
                "response": {
                    "type": "execute",
                    "result": {
                        "cols": [{"name": "name", "decltype": "TEXT"}, {"name": "n", "decltype": "INTEGER"}],
                        "rows": [[{"type": "text", "value": "Turso"}, {"type": "integer", "value": "3"}]],
                        "affected_row_count": 0,
                        "last_insert_rowid": None,
                    },
                },
            },
            {"type": "ok", "response": {"type": "close"}},
        ],
    }
    (result,) = parse_pipeline(payload, 1)
    assert result.rows == [("Turso", 3)] and result.affected == 0


def test_turso_error_entry_raises_with_server_message():
    payload = {
        "results": [
            {"type": "error", "error": {"message": "SQLite error: no such table: users", "code": "X"}}
        ]
    }
    with pytest.raises(StoreError, match="no such table"):
        parse_pipeline(payload, 1)
    with pytest.raises(StoreError, match="returned 0 results"):
        parse_pipeline({"results": []}, 1)


def test_turso_endpoint_requires_tls():
    assert turso_endpoint("libsql://db-org.turso.io") == "https://db-org.turso.io"
    assert turso_endpoint(" https://db-org.turso.io/ ") == "https://db-org.turso.io"
    with pytest.raises(StoreError, match="TLS"):
        turso_endpoint("http://db-org.turso.io")


def test_half_configured_turso_fails_loudly_instead_of_using_the_file(tmp_path, monkeypatch):
    monkeypatch.setenv("FORECAST_TURSO_URL", "libsql://db-org.turso.io")
    with pytest.raises(StoreError, match="FORECAST_TURSO_TOKEN"):
        UserStore(tmp_path)
    assert not (tmp_path / "artifacts").exists()


def test_turso_is_selected_without_touching_the_network_or_disk(tmp_path, monkeypatch):
    monkeypatch.setenv("FORECAST_TURSO_URL", "libsql://db-org.turso.io")
    monkeypatch.setenv("FORECAST_TURSO_TOKEN", "x")
    store = UserStore(tmp_path)  # schema is created lazily on first use, so no call happens here
    assert store.backend == "turso" and store.path is None
    assert not (tmp_path / "artifacts").exists()
