"""Data-quality objectives of the stores the product reads (ADR 0092). Each check names its source and returns OK / ALARM with the evidence; nothing is repaired here.

- freshness: age of the live record, of the newest forecast, of the newest result per league, of the newest stored line-up and of the newest exact odds quote, against FRESHNESS_HOURS;
- schema: the newest forecast of every fixture and every stored result carry the fields the readers need;
- duplicates: two forecast fixtures with the same home and away club within 3 days (a provisional date next to the real one), and two results of one ordered pair within 3 days;
- status consistency: a stored result whose kick-off is still in the future, or a match stored both as a result and as void;
- coverage: a league with at least COVERAGE_MIN forecast fixtures in the previous 14 days and none in the last 14 days.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

FRESHNESS_HOURS = {"live_record": 8, "forecast": 8, "results_league": 24 * 7, "lineups": 24 * 4, "odds": 48}
FORECAST_FIELDS = ("fixture_key", "kickoff_utc", "information_cutoff", "generated_at", "content_hash", "model_version", "data_version", "intelligence")
RESULT_FIELDS = ("home_id", "away_id", "kickoff_utc", "home_goals", "away_goals")
COVERAGE_MIN = 5
LEAGUE_OFFSEASON = {"UCL", "UEL", "UECL"}  # cups have long gaps between match weeks: not a coverage loss


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _age(now: datetime, t: datetime | None) -> float | None:
    return None if t is None else round((now - t).total_seconds() / 3600, 1)


def check(markets_root, now: datetime, odds_root=None) -> dict:
    root = Path(markets_root) / "artifacts"
    alarms, out = [], {"freshness": {}, "schema": {}, "duplicates": {}, "consistency": {}, "coverage": {}}
    track = root / "markets" / "track_record.json"
    t_track = _utc(json.loads(track.read_text(encoding="utf-8"))["generated_at"]) if track.exists() else None
    newest_forecast, latest_by_fixture = None, {}
    for d in sorted(p for p in (root / "markets").glob("*__*__*") if p.is_dir()):
        files = sorted(d.glob("intel-*.json"))
        if files:
            body = json.loads(files[-1].read_text(encoding="utf-8"))
            latest_by_fixture[d.name] = body
            c = _utc(body["generated_at"]) if "generated_at" in body else None
            newest_forecast = c if c and (newest_forecast is None or c > newest_forecast) else newest_forecast
    results, void = {}, {}
    for p in sorted((root / "results").glob("*.json")) if (root / "results").exists() else []:
        store = json.loads(p.read_text(encoding="utf-8"))
        results[p.stem] = store.get("matches", {})
        void.update(store.get("void", {}))
    lineups = [p for p in (root / "lineups").glob("*__*__*/lineup-*.json")] if (root / "lineups").exists() else []
    newest_lineup = max((_utc(json.loads(p.read_text(encoding="utf-8"))["observed_at"]) for p in lineups), default=None)
    newest_odds = None
    if odds_root:
        for q in (Path(odds_root) / "artifacts" / "odds").glob("*/quotes.jsonl"):
            last = q.read_text(encoding="utf-8").strip().splitlines()[-1:] or []
            for line in last:
                t = _utc(json.loads(line)["observed_at"])
                newest_odds = t if newest_odds is None or t > newest_odds else newest_odds
    for name, t in (("live_record", t_track), ("forecast", newest_forecast), ("lineups", newest_lineup), ("odds", newest_odds if odds_root else "NOT_CHECKED")):
        if t == "NOT_CHECKED":
            out["freshness"][name] = {"status": "NOT_CHECKED"}
            continue
        age = _age(now, t)
        ok = age is not None and age <= FRESHNESS_HOURS[name]
        out["freshness"][name] = {"age_hours": age, "limit_hours": FRESHNESS_HOURS[name], "status": "OK" if ok else "ALARM"}
        if not ok:
            alarms.append(f"freshness:{name}")
    for lg, ms in results.items():
        seen = [_utc(r["first_seen"]) for r in ms.values() if r.get("first_seen")]
        age = _age(now, max(seen)) if seen else None
        ok = age is not None and age <= FRESHNESS_HOURS["results_league"]
        out["freshness"][f"results:{lg}"] = {"age_hours": age, "limit_hours": FRESHNESS_HOURS["results_league"], "status": "OK" if ok else ("CUP_GAP" if lg in LEAGUE_OFFSEASON else "ALARM")}
        if not ok and lg not in LEAGUE_OFFSEASON:
            alarms.append(f"freshness:results:{lg}")
    bad_f = [k for k, b in latest_by_fixture.items() if any(f not in b for f in FORECAST_FIELDS)]
    bad_r = [f"{lg}/{k}" for lg, ms in results.items() for k, r in ms.items() if any(f not in r for f in RESULT_FIELDS)]
    out["schema"] = {"forecasts_checked": len(latest_by_fixture), "forecasts_missing_fields": bad_f[:10], "results_checked": sum(len(v) for v in results.values()), "results_missing_fields": bad_r[:10]}
    if bad_f or bad_r:
        alarms.append("schema")
    pairs: dict[tuple[str, str], list[datetime]] = {}
    for k in latest_by_fixture:
        h, a, day = k.split("__")
        pairs.setdefault((h, a), []).append(datetime.fromisoformat(day))
    dup_f = [f"{h}__{a}" for (h, a), ds in pairs.items() if any(abs((x - y).days) <= 3 for i, x in enumerate(sorted(ds)) for y in sorted(ds)[i + 1 :])]
    rp: dict[tuple[str, str, str], list[datetime]] = {}
    for lg, ms in results.items():
        for r in ms.values():
            if all(f in r for f in RESULT_FIELDS):
                rp.setdefault((lg, r["home_id"], r["away_id"]), []).append(_utc(r["kickoff_utc"]))
    dup_r = [f"{lg}:{h}__{a}" for (lg, h, a), ds in rp.items() if any(abs((x - y).days) <= 3 for i, x in enumerate(sorted(ds)) for y in sorted(ds)[i + 1 :])]
    out["duplicates"] = {"forecast_pairs_within_3_days": dup_f[:20], "n_forecast": len(dup_f), "result_pairs_within_3_days": dup_r[:20], "n_result": len(dup_r)}
    if dup_r:
        alarms.append("duplicates:results")
    future = [f"{lg}/{k}" for lg, ms in results.items() for k, r in ms.items() if "kickoff_utc" in r and _utc(r["kickoff_utc"]) > now]
    both = sorted(set(void) & {k for ms in results.values() for k in ms})
    out["consistency"] = {"result_with_future_kickoff": future[:10], "result_and_void": both[:10]}
    if future or both:
        alarms.append("consistency")
    per_league: dict[str, list[datetime]] = {}
    for b in latest_by_fixture.values():
        per_league.setdefault(b.get("league", "?"), []).append(_utc(b["kickoff_utc"]))
    lost = []
    for lg, ks in per_league.items():
        prev = sum(1 for k in ks if now - timedelta(days=28) <= k < now - timedelta(days=14))
        last = sum(1 for k in ks if now - timedelta(days=14) <= k <= now + timedelta(days=7))
        out["coverage"][lg] = {"previous_14_days": prev, "last_14_days_and_next_7": last}
        if prev >= COVERAGE_MIN and last == 0 and lg not in LEAGUE_OFFSEASON:
            lost.append(lg)
    if lost:
        alarms.append("coverage:" + ",".join(sorted(lost)))
    out["alarms"] = alarms
    out["status"] = "ALARM" if alarms else "OK"
    return out
