"""Disaster-recovery and rollback drills (ADR 0097). Each drill runs for real and its outcome is recorded in `docs/phase3/s18_drills.json`.

    python -m src.release.drills restore --markets-root <markets-data checkout>   # backup + restore of the published state, every file and every forecast hash verified
    python -m src.release.drills migration                                        # user-store migration (ADR 0062 `plan` column) applied and rolled back on a scratch SQLite file
    python -m src.release.drills model-rollback                                   # canary stop criterion -> champion restored in a scratch copy of configs/release/canary.yaml
The deployment drills (Vercel `dry-run` / `preview` / `execute`, ADR 0080) run in the `vercel-rollback-drill` workflow; their run ids are added with `record-workflow`.
Rows written by the migration drill and the challenger evidence of the model drill are TEST_ONLY (a drill, never a forecast).
"""

import argparse
import hashlib
import json
import sqlite3
import sys
import tempfile
import zipfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from src.cli_utils import configure_output

REPO = Path(__file__).resolve().parents[2]
RECORD = REPO / "docs" / "phase3" / "s18_drills.json"
INCLUDE = ("artifacts/markets", "artifacts/stage_records", "artifacts/results", "artifacts/lineups")


def _record(name: str, result: dict, path: Path = RECORD) -> dict:
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    data[name] = {**result, "ran_at": datetime.now(UTC).isoformat()}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return data[name]


def _files(root: Path) -> dict[str, str]:
    out = {}
    for inc in INCLUDE:
        for f in sorted((root / inc).rglob("*")):
            if f.is_file() and "_cache" not in f.parts:
                out[f.relative_to(root).as_posix()] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


def restore(markets_root: Path) -> dict:
    """Back up the published state to a zip with a manifest, restore it into an empty directory, verify every file and recompute the content hash of every forecast."""
    from src.markets.track import load_artifacts
    from src.versioning import canonical_json

    t0 = datetime.now(UTC)
    before = _files(markets_root)
    with tempfile.TemporaryDirectory() as tmp:
        zpath = Path(tmp) / "backup.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for rel in before:
                z.write(markets_root / rel, rel)
            z.writestr("MANIFEST.json", json.dumps(before, sort_keys=True))
        target = Path(tmp) / "restored"
        with zipfile.ZipFile(zpath) as z:
            z.extractall(target)
        manifest = json.loads((target / "MANIFEST.json").read_text(encoding="utf-8"))
        after = _files(target)
        bad_files = [k for k in manifest if after.get(k) != manifest[k]]
        bad_hash = 0
        arts = load_artifacts(target)
        for v in arts.values():
            for a in v:
                body = {k: x for k, x in a.items() if k not in ("content_hash", "generated_at")}
                bad_hash += hashlib.sha256(canonical_json(body).encode()).hexdigest() != a["content_hash"]
        size = zpath.stat().st_size
    secs = (datetime.now(UTC) - t0).total_seconds()
    ok = not bad_files and bad_hash == 0 and len(after) == len(before)
    return {"status": "PASS" if ok else "FAIL", "files": len(before), "files_mismatched": len(bad_files), "forecasts": sum(len(v) for v in arts.values()),
            "forecast_hash_mismatches": bad_hash, "backup_bytes": size, "seconds": round(secs, 1), "rto_note": "restore of the published state takes this long on the runner; the data repo itself is the off-site copy"}  # fmt: skip


def migration() -> dict:
    """Apply ADR 0062's migration to a pre-0062 users table through the real UserStore code, then roll it back (DROP COLUMN) and check that the pre-0062 code path still reads every row."""
    from src.web import store as st

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db = Path(tmp) / "users.sqlite"
        with closing(sqlite3.connect(db)) as c, c:
            c.execute("CREATE TABLE users(uid TEXT PRIMARY KEY, provider TEXT NOT NULL, name TEXT NOT NULL, email TEXT, created_at TEXT NOT NULL, last_seen TEXT NOT NULL)")
            c.execute("CREATE TABLE favorites(uid TEXT NOT NULL, team_id TEXT NOT NULL, added_at TEXT NOT NULL, PRIMARY KEY(uid, team_id))")
            c.executemany("INSERT INTO users VALUES (?,?,?,?,?,?)", [(f"TEST_ONLY_{i}", "guest", f"TEST_ONLY {i}", None, "2026-11-01T00:00:00+00:00", "2026-11-01T00:00:00+00:00") for i in range(5)])
            c.execute("INSERT INTO favorites VALUES ('TEST_ONLY_0', 'ENG_arsenal', '2026-11-01T00:00:00+00:00')")
        s = st.UserStore.__new__(st.UserStore)
        s._backend, s._ready = st._SqliteBackend(db), False
        s._ensure()  # the real migration path of the app
        with closing(sqlite3.connect(db)) as c, c:
            cols_up = [r[1] for r in c.execute("PRAGMA table_info(users)")]
            plans = {r[0] for r in c.execute("SELECT plan FROM users")}
            c.execute("ALTER TABLE users DROP COLUMN plan")  # the rollback statement (SQLite >= 3.35)
            cols_down = [r[1] for r in c.execute("PRAGMA table_info(users)")]
            rows = c.execute("SELECT uid, provider, name, email, created_at, last_seen FROM users").fetchall()
            favs = c.execute("SELECT COUNT(*) FROM favorites").fetchone()[0]
        s2 = st.UserStore.__new__(st.UserStore)
        s2._backend, s2._ready = st._SqliteBackend(db), False
        s2._ensure()  # re-applying after the rollback is idempotent
        with closing(sqlite3.connect(db)) as c, c:
            cols_again = [r[1] for r in c.execute("PRAGMA table_info(users)")]
    ok = "plan" in cols_up and plans == {"free"} and "plan" not in cols_down and len(rows) == 5 and favs == 1 and "plan" in cols_again
    return {"status": "PASS" if ok else "FAIL", "sqlite": sqlite3.sqlite_version, "columns_after_migration": cols_up, "columns_after_rollback": cols_down, "rows_kept": len(rows),
            "favourites_kept": favs, "reapplied": "plan" in cols_again, "note": "TEST_ONLY rows on a scratch file; the live Turso store was not touched (its migration was verified live in ADR 0062)"}  # fmt: skip


def model_rollback() -> dict:
    """A challenger bound in a scratch copy of the canary config, a stop criterion on TEST_ONLY evidence, ROLLBACK applied: the champion serves every user again; the real config is unchanged."""
    from src.markets.model import MODEL_VERSION

    from . import canary

    real = canary.CONFIG.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "canary.yaml"
        p.write_text(real.replace("challenger: null", "challenger: TEST_ONLY-candidate").replace("stage: shadow", "stage: canary_5"), encoding="utf-8")
        cfg = canary.load(p)
        users = [f"user{i}" for i in range(2000)]
        share_before = sum(canary.route(u, cfg) == "challenger" for u in users) / len(users)
        decision = canary.evaluate(cfg, {"days": 3, "settled": 40, "delta_interval": [0.01, 0.05], "ece_delta": 0.0, "critical_slo": [], "leakage": "PASS"})  # TEST_ONLY evidence
        canary.apply(cfg, decision, p)
        after = canary.load(p)
        share_after = sum(canary.route(u, after) == "challenger" for u in users) / len(users)
    unchanged = canary.CONFIG.read_text(encoding="utf-8") == real
    ok = decision["action"] == "ROLLBACK" and share_after == 0 and after["stage"] == "shadow" and unchanged and canary.load()["champion"] == MODEL_VERSION
    return {"status": "PASS" if ok else "FAIL", "challenger_share_before": share_before, "decision": decision["action"], "reason": decision["reason"], "challenger_share_after": share_after,
            "real_config_unchanged": unchanged, "champion_pinned": canary.load()["champion"]}  # fmt: skip


def main(argv=None) -> int:
    configure_output()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("drill", choices=("restore", "migration", "model-rollback", "record-workflow"))
    ap.add_argument("--markets-root", default=None)
    ap.add_argument("--name", default=None)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--conclusion", default=None)
    ap.add_argument("--out", default=str(RECORD))
    a = ap.parse_args(argv)
    out = Path(a.out)
    if a.drill == "restore":
        res = _record("restore", restore(Path(a.markets_root)), out)
    elif a.drill == "migration":
        res = _record("migration_rollback", migration(), out)
    elif a.drill == "model-rollback":
        res = _record("model_rollback", model_rollback(), out)
    else:
        res = _record(a.name, {"status": "PASS" if a.conclusion == "success" else "FAIL", "workflow_run": a.run_id, "conclusion": a.conclusion}, out)
    print(json.dumps(res, sort_keys=True))
    return 0 if res["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
