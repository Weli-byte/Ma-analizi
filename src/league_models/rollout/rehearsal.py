"""Sprint 6 (ADR 0068): staging rollback rehearsal, run in a scratch directory with a SYNTHETIC approved model (label TEST_ONLY: no real model is approved).

It proves the mechanics, not any model: canary on -> users split -> a fail-closed alarm trips the kill switch -> every user is GLOBAL again -> stored predictions are
byte-identical -> removing the flag and replaying is idempotent -> a schema-mismatched record is detected. The real staging environment does not exist for this project:
that is reported as NOT_VERIFIED, never as done."""

import hashlib
import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

from src.league_models.licenses import load_manifest
from src.league_models.prediction_router import GLOBAL, RegistryEntry

from . import control, monitoring, records
from .control import ROLLOUT_CONFIG


def _entry(d: Path, model, status, mode, league, target):
    p = d / f"{model}_{league.replace('*', 'ALL')}_{target}.txt"
    p.write_text("artifact", encoding="utf-8")
    return RegistryEntry(model, "v-test", mode, target, league, status, p.name, hashlib.sha256(b"artifact").hexdigest(), "booster_file")


def rehearse(now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    steps: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        raw = yaml.safe_load(ROLLOUT_CONFIG.read_text(encoding="utf-8"))
        raw["kill_switch"], raw["canary"]["active_fraction"] = False, 0.5
        (d / "rollout.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
        cfg = control.load_rollout(d / "rollout.yaml")
        reg = control.LeagueModelRegistry([_entry(d, "elo_s2", "GLOBAL_REFERENCE", GLOBAL, "*", "1x2"), _entry(d, "SYNTHETIC_CANDIDATE", "APPROVED_FOR_CANARY", "LEAGUE_ADAPTED", "ITA1", "1x2")], d)
        lic = {k: v.model_copy(update={"plan_status": "PERMITTED"}) for k, v in load_manifest().items()}
        router = control.CanaryRouter(cfg, reg, lic, {"SYNTHETIC_CANDIDATE": ["openfootball_domestic"]}, d / "audit.jsonl", d)
        users = [f"user-{i}" for i in range(400)]
        served = [router.route(u, "ITA1", "1x2", "t-24h", now).served for u in users]
        share = served.count("SYNTHETIC_CANDIDATE") / len(users)
        steps.append({"step": "canary on at 50% (synthetic approved model)", "candidate_share": round(share, 3), "ok": 0.4 < share < 0.6})

        store = records.ProspectiveStore(d / "prospective.jsonl")
        kick = now + timedelta(hours=23, minutes=30)
        r0 = records.ProspectiveRecord("fdorg-1", "ITA1", kick.isoformat(), now.isoformat(), "t-24h", "v1", "global", "global", 0.5, 0.3, 0.2, 0.5, 0.5, "snap", ("openfootball_domestic",), {"lineups": "UNKNOWN"})
        store.put(r0, now)
        before = (d / "prospective.jsonl").read_bytes()

        dec = monitoring.apply_alerts([monitoring.Alert("critical", "rehearsal_zero_coverage", "simulated alarm", True)], cfg, d, d, now)
        after_served = {router.route(u, "ITA1", "1x2", "t-24h", now).served for u in users}
        steps.append({"step": "fail-closed alarm trips the kill switch", "decision": dec["routing"], "all_users_global": after_served == {"GLOBAL"}, "ok": after_served == {"GLOBAL"}})
        steps.append({"step": "stored prediction records untouched by the rollback", "ok": (d / "prospective.jsonl").read_bytes() == before})

        (d / cfg["kill_switch_file"]).unlink()
        again = {router.route(u, "ITA1", "1x2", "t-24h", now).served for u in users}
        steps.append({"step": "flag removed deliberately: routing resumes (the owner's act)", "ok": "SYNTHETIC_CANDIDATE" in again})
        steps.append({"step": "idempotent replay of the same record", "result": store.put(r0, now + timedelta(minutes=3)), "ok": store.put(r0, now + timedelta(minutes=3)) == "EXISTS"})

        old = json.loads((d / "prospective.jsonl").read_text(encoding="utf-8").splitlines()[0])
        old["schema_version"] = "lm-prospective-v0"
        steps.append({"step": "migration check: an old-schema record is detected", "ok": any(a.key == "schema_mismatch" for a in monitoring.check_records([old], cfg))})
        audit = (d / "audit.jsonl").read_text(encoding="utf-8")
        steps.append({"step": "audit log has the served version per decision and no raw user id", "ok": "user-17" not in audit and '"served"' in audit, "decisions_logged": len(audit.splitlines())})
    return {"label": "TEST_ONLY staging simulation with a SYNTHETIC approved model; not a rehearsal of any real model and not a production evidence", "run_at_utc": now.isoformat(),
            "all_ok": all(s["ok"] for s in steps), "steps": steps, "real_staging_environment": "NOT_VERIFIED: this project has no separate staging deployment"}  # fmt: skip


if __name__ == "__main__":
    print(json.dumps(rehearse(), indent=2, sort_keys=True))
