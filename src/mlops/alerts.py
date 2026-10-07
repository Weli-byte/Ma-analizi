"""Alerts (ADR 0032): turn a monitoring report into a list of alerts, append them to
artifacts/ops/alerts.jsonl. An alert is information; nothing here changes a model, retrains, or
switches a provider (no silent fallback)."""

import json
from dataclasses import asdict, dataclass
from datetime import datetime

from .oplog import _append


@dataclass(frozen=True)
class Alert:
    severity: str  # critical | warning
    key: str
    message: str


def _sev(status: str) -> str | None:
    return {"STALE": "critical", "FAILED": "critical", "WARNING": "warning"}.get(status)


def evaluate(report: dict) -> list[Alert]:
    out: list[Alert] = []
    df = report.get("data_freshness", {})
    if sev := _sev(df.get("status", "")):
        out.append(
            Alert(
                sev,
                "data_freshness",
                f"dataset is {df.get('status')}: newest result {df.get('latest_result_utc')} "
                f"({df.get('age_days')} days old)",
            )
        )
    for name, h in report.get("providers", {}).items():
        if sev := _sev(h["status"]):
            out.append(
                Alert(
                    sev,
                    f"provider:{name}",
                    f"{name} {h['status']}: error_rate={h['error_rate']}, "
                    f"minutes_since_success={h['minutes_since_success']}, errors={h['error_kinds']}",
                )
            )
    for name, h in report.get("heartbeats", {}).items():
        if sev := _sev(h["status"]):
            out.append(
                Alert(
                    sev,
                    f"scheduler:{name}",
                    f"{name} ticks {h['status']}: last beat {h.get('last_beat')}, "
                    f"{h.get('minutes_since_last')} min ago, {h.get('n_gaps', 0)} gap(s) over the limit",
                )
            )
    bad = report.get("ops_log_corrupt_lines", {})
    if any(bad.values()):
        msg = f"corrupt lines skipped in the operational log: {bad}"
        out.append(Alert("warning", "ops_log_corrupt", msg))
    llm = report.get("llm", {})
    for b in llm.get("breaches", []):
        out.append(
            Alert(
                "warning",
                f"llm:{b}",
                f"LLM {b} above its threshold (calls={llm['calls']}, failed={llm['failed']}, "
                f"cost24h=${llm['cost_last_24h_usd']})",
            )
        )
    for model, d in report.get("prediction_distribution", {}).items():
        if d["status"] == "WARNING":
            out.append(
                Alert(
                    "warning",
                    f"prediction_drift:{model}",
                    f"{model} top-probability PSI {d['psi_top_probability']} above threshold",
                )
            )
    fh = report.get("feature_health", {})
    if fh.get("status") == "WARNING":
        out.append(
            Alert(
                "warning",
                "feature_health",
                f"feature drift/missingness: drifting={fh['drifting_features']}, "
                f"missing_share={fh['missing_share']}",
            )
        )
    md = report.get("metric_drift", {})
    if md.get("status") == "WARNING":
        out.append(
            Alert(
                "warning",
                "metric_drift",
                f"settled log loss {md['log_loss']} vs reference {md['reference_log_loss']} (+{md['delta']})",
            )
        )
    return out


def persist(alerts: list[Alert], now: datetime) -> None:
    for a in alerts:
        _append("alerts.jsonl", {"ts": now.isoformat(), **asdict(a)})


def render(alerts: list[Alert]) -> str:
    if not alerts:
        return "no alerts"
    return "\n".join(f"[{a.severity.upper()}] {a.key}: {a.message}" for a in alerts)


def dumps(alerts: list[Alert]) -> str:
    return json.dumps([asdict(a) for a in alerts], indent=2)
