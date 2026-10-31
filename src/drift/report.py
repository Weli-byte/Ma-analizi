"""Sprint 14 report: data quality, input drift and performance drift of the product, on separate panels (ADR 0092).

`python -m src.drift.report --markets-root <markets-data checkout> [--odds-root <odds-data checkout>] --out docs/phase3/sprint14_drift_report.md`
"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from src.cli_utils import configure_output

from . import monitor, quality

CRITICAL = ("freshness:live_record", "freshness:forecast", "schema", "duplicates:results", "consistency", "performance_drift")


def severity(alarm: str) -> str:
    return "critical" if alarm.startswith(CRITICAL) else "warning"


def notify(alarms: list[str], state: Path, now: datetime) -> list[str]:
    """Telegram (ADR 0077): a critical alarm once per day, a warning once per ISO week (a known, slow problem such as stale odds must not repeat every run)."""
    from src.mlops.notify import notify_once

    year, week, _ = now.isocalendar()
    out = []
    for a in alarms:
        sev = severity(a)
        period = now.date().isoformat() if sev == "critical" else f"{year}-W{week:02d}"
        out.append(f"{a}: " + notify_once(state, f"drift:{a}:{period}", f"Veri/kayma alarmı ({sev}): {a} — ayrıntı: python -m src.drift.report (ADR 0092)"))
    return out

REPO = Path(__file__).resolve().parents[2]
THRESHOLDS = REPO / "docs" / "phase3" / "s14_thresholds.json"


def _fmt(m: dict) -> str:
    if m.get("status") != "MEASURED":
        return f"{m.get('status')} (n {m.get('n_reference')} / {m.get('n_current')}, minimum {m.get('min_n')})"
    extra = f", KS {m['ks']:.3f}" if "ks" in m else ""
    over = f" — over threshold: {', '.join(m['over_threshold'])}" if m.get("over_threshold") else ""
    psi = f"PSI {m['psi']:.3f}, " if "psi" in m else ""
    return f"{psi}JS {m['js']:.4f}{extra} (n {m['n_reference']} / {m['n_current']}){over}"


def build(q: dict, inp: dict, perf: dict, thresholds: dict, now: datetime) -> str:
    lines = [
        "# Sprint 14 - data quality, input drift and performance drift (ADR 0092)", "", f"As of {now.isoformat()} · `python -m src.drift.report` · thresholds from validation history (`docs/phase3/s14_thresholds.json`, {thresholds['window_pairs']} window pairs, margin {thresholds['margin']}).", "",
        f"## 1. Data quality — {q['status']}", "", "| check | value | limit | status |", "|---|---|---|---|",
        *[f"| freshness {k} | {v.get('age_hours', '-')} h | {v.get('limit_hours', '-')} h | {v['status']} |" for k, v in q["freshness"].items()],
        f"| schema | forecasts with missing fields {len(q['schema']['forecasts_missing_fields'])} of {q['schema']['forecasts_checked']}; results {len(q['schema']['results_missing_fields'])} of {q['schema']['results_checked']} | 0 | {'ALARM' if 'schema' in q['alarms'] else 'OK'} |",
        f"| duplicate results (same pair within 3 days) | {q['duplicates']['n_result']} | 0 | {'ALARM' if q['duplicates']['n_result'] else 'OK'} |",
        f"| forecast pairs within 3 days (provisional dates, ADR 0090) | {q['duplicates']['n_forecast']} | report | INFO |",
        f"| result with a future kick-off / result and void | {len(q['consistency']['result_with_future_kickoff'])} / {len(q['consistency']['result_and_void'])} | 0 | {'ALARM' if 'consistency' in q['alarms'] else 'OK'} |",
        "", f"Alarms: {', '.join(q['alarms']) or 'none'}.", "",
        f"## 2. Input drift (model outputs and league mix) — {inp.get('status')}", "",
        (f"Reference: fixtures of the first {inp['reference']['days']} days of the record from {inp['reference']['from'][:10]} (n {inp['reference']['n']}); current: from {inp['current']['from'][:10]}, the last {inp['current']['days']} days, never overlapping the reference (n {inp['current']['n']})." if "reference" in inp else "No forecasts."), "",
        *[f"- {k}: {_fmt(v)}" for k, v in inp["features"].items() if isinstance(v, dict) and "status" in v],
        *[f"- {k}: reference {v['reference']}, current {v['current']}" for k, v in inp["features"].items() if "share" in k],
        "", f"Alarms: {', '.join(inp.get('alarms', [])) or 'none'}.", "",
        f"## 3. Performance drift (settled matches only) — {perf['status']}", "",
    ]  # fmt: skip
    if perf["status"] == "INSUFFICIENT_DATA":
        lines.append(f"- {perf['settled']} settled matches ({perf['needed']} needed: first {monitor.REFERENCE_N} against the last {monitor.CURRENT_N}){'; ' + perf['reason'] if perf.get('reason') else ''}: no performance verdict.")
    else:
        lines += [f"- {t}: log loss {m['log_loss_reference']:.4f} (first {perf['reference_n']}) → {m['log_loss_current']:.4f} (last {perf['current_n']}), change {m['delta']:+.4f}, interval {m['interval'][0]:+.4f} to {m['interval'][1]:+.4f} at alpha {perf['alpha']:.4f}" for t, m in perf["markets"].items()]
        lines.append(f"- Alarms: {', '.join(perf['alarms']) or 'none'} (an alarm needs the whole interval above 0).")
    lines += ["", "Input drift and performance drift are reported apart: input drift needs no result, performance drift waits for settled results (label latency) and never counts an open match.", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    configure_output()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--markets-root", required=True)
    ap.add_argument("--odds-root", default=None)
    ap.add_argument("--odds-dir", default=None, help="folder of the stored quotes when it is not <root>/artifacts/odds (the cloud job: artifacts/odds_remote)")
    ap.add_argument("--json-out", default=None, help="machine-readable status (alarms per panel)")
    ap.add_argument("--notify-state", default=None, help="send the alarms to Telegram, deduplicated through this state file")
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    now = datetime.fromisoformat(a.as_of.replace("Z", "+00:00")) if a.as_of else datetime.now(UTC)
    th = json.loads(THRESHOLDS.read_text(encoding="utf-8"))
    q = quality.check(a.markets_root, now, a.odds_root, a.odds_dir)
    inp = monitor.input_drift(monitor.forecast_rows(a.markets_root), th["thresholds"], now)
    perf = monitor.performance_drift(monitor.settled_losses(a.markets_root))
    text = build(q, inp, perf, th, now)
    alarms = [*q["alarms"], *inp.get("alarms", []), *perf.get("alarms", [])]
    if a.json_out:
        Path(a.json_out).write_text(json.dumps({"generated_at": now.isoformat(), "quality": q["status"], "input_drift": inp.get("status"), "performance_drift": perf["status"],
                                                "alarms": [{"alarm": x, "severity": severity(x)} for x in alarms]}, indent=1) + "\n", encoding="utf-8", newline="\n")  # fmt: skip
    if a.notify_state:
        print("notify:", notify(alarms, Path(a.notify_state), now))
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8", newline="\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
