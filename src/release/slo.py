"""Service level objectives and error budget of the published product (ADR 0097).

    python -m src.release.slo --markets-root . [--odds-dir artifacts/odds_remote] [--probe-url https://...] --history artifacts/markets/_ops/slo_history.jsonl --out artifacts/markets/slo.json
                              [--notify-state artifacts/markets/notified_slo.json]

Each hourly cloud run measures the indicators below as of its own clock, appends one line to the history and writes `slo.json` (read by the status page `/durum`). Targets were fixed in
this module before the first measurement; changing one is an ADR change. An indicator without its data is NOT_MEASURED (never assumed met).
- forecast_freshness: hours since the newest published forecast (target <= 2);
- fresh_forecast_share: of the fixtures kicking off in the next 48 hours, the share whose latest forecast is younger than 3 hours (>= 0.95);
- stage_completion: of the stages due in the last 7 days (t-24h / t-90m / t-30m / kickoff after the fixture's first forecast), the share with a frozen record (>= 0.95);
- settlement: of the fixtures that kicked off 6 to 72 hours ago, the share settled in the track record (>= 0.95);
- odds_freshness: hours since the newest exact odds quote (<= 24);
- calibration_ece_1x2: top-label expected calibration error (10 bins) of the published 1X2 forecasts on the settled record (<= 0.05, or the 95th percentile of the ECE a perfectly calibrated
  forecaster shows on the same matches when that is larger; only with at least 100 matches);
- api latency p95 / p99 and availability of the live site, from PROBE_N real requests to `/v1/health` (p95 <= 1500 ms, p99 <= 3000 ms, availability >= 0.99);
- error budget: hourly runs. Target: 95 % of the hours since the history began have a recorded run; the budget is the 5 % of hours that may be missed, and `budget_left` is what remains of it
  over the last 28 days.
"""

import argparse
import json
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from src.cli_utils import configure_output

TARGETS = {
    "forecast_freshness_hours": ("<=", 2.0),
    "fresh_forecast_share": (">=", 0.95),
    "stage_completion": (">=", 0.95),
    "settlement": (">=", 0.95),
    "odds_freshness_hours": ("<=", 24.0),
    "calibration_ece_1x2": ("<=", 0.05),
    "api_p95_ms": ("<=", 1500.0),
    "api_p99_ms": ("<=", 3000.0),
    "api_availability": (">=", 0.99),
    "run_availability_28d": (">=", 0.95),
}
CRITICAL = {"forecast_freshness_hours": 6.0, "api_availability": 0.9}   # a breach beyond these is critical (sent at once); other breaches are warnings (weekly)
PROBE_N = 20
STAGES = (("t-24h", timedelta(hours=24)), ("t-90m", timedelta(minutes=90)), ("t-30m", timedelta(minutes=30)), ("kickoff", timedelta(0)))
MIN_ECE_N = 100
ECE_SIMULATIONS = 500
BUDGET_DAYS = 28


def _utc(t: str) -> datetime:
    return datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(UTC)


def _hours(a: datetime, b: datetime) -> float:
    return round((a - b).total_seconds() / 3600.0, 2)


def forecasts(arts: dict[str, list[dict]], now: datetime) -> dict:
    gen = [_utc(a["generated_at"]) for v in arts.values() for a in v if a.get("generated_at")]
    out = {"forecast_freshness_hours": _hours(now, max(gen)) if gen else None}
    soon = [v for v in arts.values() if v and now < _utc(v[0]["kickoff_utc"]) <= now + timedelta(hours=48)]
    fresh = sum(1 for v in soon if now - max(_utc(a["generated_at"]) for a in v) <= timedelta(hours=3))
    out["fresh_forecast_share"] = round(fresh / len(soon), 4) if soon else None
    out["fixtures_next_48h"] = len(soon)
    return out


def stage_completion(root: Path, arts: dict[str, list[dict]], now: datetime) -> dict:
    due = written = 0
    sdir = root / "artifacts" / "stage_records"
    for key, v in arts.items():
        kick = _utc(v[-1]["kickoff_utc"])
        if v[-1].get("kickoff_time_known") is False or not now - timedelta(days=7) <= kick <= now:
            continue
        first = min(_utc(a["generated_at"]) for a in v)
        have = {p.name.split("__")[0] for p in (sdir / key).glob("*__ko*.json")} if (sdir / key).is_dir() else set()
        for name, before in STAGES:
            if kick - before > first:
                due += 1
                written += name in have
    return {"stage_completion": round(written / due, 4) if due else None, "stages_due_7d": due}


def settlement_and_calibration(root: Path, arts: dict[str, list[dict]], now: datetime) -> dict:
    p = root / "artifacts" / "markets" / "track_record.json"
    if not p.is_file():
        return {"settlement": None, "calibration_ece_1x2": None}
    track = json.loads(p.read_text(encoding="utf-8"))
    settled = {m["key"] for m in track["matches"]}
    due = [k for k, v in arts.items() if v[-1].get("kickoff_time_known") is not False and now - timedelta(hours=72) <= _utc(v[-1]["kickoff_utc"]) <= now - timedelta(hours=6)]
    out = {"settlement": round(sum(k in settled for k in due) / len(due), 4) if due else None, "settlement_due": len(due)}
    probs, ys = [], []
    for m in track["matches"]:
        cand = [a for a in arts.get(m["key"], []) if _utc(a["information_cutoff"]) < _utc(a["kickoff_utc"])]
        if not cand:
            continue
        hp = max(cand, key=lambda a: a["information_cutoff"])["intelligence"]["result"]["headline_probs"]
        probs.append([hp["home"], hp["draw"], hp["away"]])
        ys.append({"H": 0, "D": 1, "A": 2}[m["result"]])
    out["ece_n"] = len(ys)
    if len(ys) >= MIN_ECE_N:
        p_, y_ = np.array(probs), np.array(ys)
        out["calibration_ece_1x2"] = round(ece(p_, y_), 4)
        rng = np.random.default_rng(20261101)  # ECE of a PERFECTLY calibrated forecaster on this sample: its 95th percentile is the noise floor at this n
        cum = p_.cumsum(1)
        null = [ece(p_, (rng.random((len(y_), 1)) > cum).sum(1)) for _ in range(ECE_SIMULATIONS)]
        out["ece_null95"] = round(float(np.percentile(null, 95)), 4)
    else:
        out["calibration_ece_1x2"] = None
    return out


def ece(p: np.ndarray, y: np.ndarray) -> float:
    conf, hit = p.max(1), (p.argmax(1) == y).astype(float)
    bins = np.minimum((conf * 10).astype(int), 9)
    return float(sum(abs(hit[bins == b].mean() - conf[bins == b].mean()) * (bins == b).mean() for b in range(10) if (bins == b).any()))


def odds_freshness(root: Path, now: datetime, odds_dir: Path | None) -> dict:
    from src.drift import quality

    try:
        f = quality.check(root, now, odds_dir=odds_dir)["freshness"].get("odds") or {}
    except (OSError, ValueError, KeyError):
        f = {}  # an unreadable store is NOT_MEASURED, never met
    return {"odds_freshness_hours": f.get("age_hours")}


def probe(url: str, n: int = PROBE_N, timeout: float = 15.0) -> dict:
    lat, ok = [], 0
    for _ in range(n):
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(urllib.request.Request(url.rstrip("/") + "/v1/health", headers={"User-Agent": "welliddia-slo-probe"}), timeout=timeout) as r:  # noqa: S310
                r.read()
                ok += r.status == 200
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        lat.append((time.monotonic() - t0) * 1000)
    a = np.array(lat)
    return {"api_p95_ms": round(float(np.percentile(a, 95)), 1), "api_p99_ms": round(float(np.percentile(a, 99)), 1), "api_p50_ms": round(float(np.percentile(a, 50)), 1),
            "api_availability": round(ok / n, 4), "probe_n": n}  # fmt: skip


def run_availability(history: list[dict], now: datetime) -> dict:
    hours = {_utc(h["ts"]).replace(minute=0, second=0, microsecond=0) for h in history}
    if not hours:
        return {"run_availability_28d": None}
    start = max(min(hours), (now - timedelta(days=BUDGET_DAYS)).replace(minute=0, second=0, microsecond=0))
    expected = int((now.replace(minute=0, second=0, microsecond=0) - start).total_seconds() // 3600) + 1
    seen = sum(1 for h in hours if h >= start)
    share = seen / expected
    allowed = (1 - TARGETS["run_availability_28d"][1]) * expected
    return {"run_availability_28d": round(share, 4), "hours_expected": expected, "hours_with_run": seen, "budget_left": round(1 - (expected - seen) / allowed, 4) if allowed else None}


def evaluate(sli: dict) -> dict:
    out = {}
    for k, (op, target) in TARGETS.items():
        v = sli.get(k)
        if v is None:
            out[k] = {"value": None, "target": f"{op} {target}", "status": "NOT_MEASURED"}
            continue
        if k == "calibration_ece_1x2" and sli.get("ece_null95") is not None:
            target = max(target, sli["ece_null95"])  # a breach must exceed what sampling noise alone gives at this n (the lesson of the ADR 0092 amendment)
        met = v <= target if op == "<=" else v >= target
        crit = k in CRITICAL and ((v > CRITICAL[k]) if op == "<=" else (v < CRITICAL[k]))
        out[k] = {"value": v, "target": f"{op} {target}", "status": "MET" if met else ("CRITICAL" if crit else "BREACHED")}
    return out


def measure(root: Path, now: datetime, odds_dir: Path | None, probe_url: str | None, history: list[dict]) -> dict:
    from src.markets.track import load_artifacts
    from src.release import breaker

    arts = load_artifacts(root)
    sli = {**forecasts(arts, now), **stage_completion(root, arts, now), **settlement_and_calibration(root, arts, now), **odds_freshness(root, now, odds_dir)}
    if probe_url:
        sli.update(probe(probe_url))
    sli.update(run_availability([*history, {"ts": now.isoformat()}], now))
    return {"generated_at": now.isoformat(), "sli": sli, "objectives": evaluate(sli), "breakers": breaker.snapshot()}


def notify(res: dict, state: Path, now: datetime) -> list[str]:
    from src.mlops.notify import notify_once

    out = []
    week = f"{now.isocalendar()[0]}-W{now.isocalendar()[1]:02d}"
    for k, o in res["objectives"].items():
        if o["status"] in ("BREACHED", "CRITICAL"):
            period = now.date().isoformat() if o["status"] == "CRITICAL" else week
            sev = "kritik" if o["status"] == "CRITICAL" else "uyarı"
            out.append(f"{k}: " + notify_once(state, f"slo:{k}:{period}", f"Hizmet hedefi ({sev}): {k} = {o['value']} (hedef {o['target']}) — durum sayfası /durum (ADR 0097)"))
    for p, s in res["breakers"].items():
        if s.get("open"):
            out.append(f"breaker:{p}: " + notify_once(state, f"breaker:{p}:{now.date().isoformat()}", f"Sağlayıcı devre kesici açık: {p} ({s.get('reason')}) — çağrılar bekletiliyor (ADR 0097)"))
    return out


def read_history(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def main(argv=None) -> int:
    configure_output()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--markets-root", default=".")
    ap.add_argument("--odds-dir", default=None)
    ap.add_argument("--probe-url", default=None)
    ap.add_argument("--history", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--notify-state", default=None)
    a = ap.parse_args(argv)
    now = datetime.now(UTC)
    hist_path = Path(a.history)
    res = measure(Path(a.markets_root), now, Path(a.odds_dir) if a.odds_dir else None, a.probe_url, read_history(hist_path))
    hist_path.parent.mkdir(parents=True, exist_ok=True)
    with hist_path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"ts": now.isoformat(), **{k: v for k, v in res["sli"].items() if not isinstance(v, dict)}}, sort_keys=True) + "\n")
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({k: v["status"] for k, v in res["objectives"].items()}))
    if a.notify_state:
        print("notify:", notify(res, Path(a.notify_state), now))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
