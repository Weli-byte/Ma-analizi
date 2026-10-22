"""Sprint 6 (ADR 0068): static, read-only shadow dashboard. Prospective (pre-match stored) results are shown apart from the backtest, with the sample size and a plain
caution; no bet advice, no odds. Everything comes from the append-only stores."""

import html
import json
from datetime import datetime
from pathlib import Path

CSS = ("body{font-family:system-ui,sans-serif;margin:0;background:#f6f7f5;color:#1c2b1c}main{max-width:980px;margin:0 auto;padding:16px}"
       "h1{font-size:22px}h2{font-size:17px;margin-top:28px}table{border-collapse:collapse;width:100%;background:#fff;font-size:13px}"
       "th,td{border:1px solid #d5dbd2;padding:5px 8px;text-align:left}th{background:#e8efe5}.warn{background:#fff4d6;border:1px solid #e3c26a;padding:10px;margin:12px 0}"
       ".bt{background:#eef2f8;border:1px solid #b8c4d8;padding:10px;margin:12px 0}small{color:#5a6b5a}")


def render(scorecard: dict, records: list[dict], alerts: list[dict], kill: dict, backtest: dict | None, now: datetime) -> str:
    e = html.escape
    rows = []
    by = {}
    for r in sorted(records, key=lambda x: (x["kickoff_utc"], x["fixture_id"], x["family"])):
        by.setdefault((r["fixture_id"], r["stage"]), []).append(r)
    for (fid, stage), recs in list(by.items())[-60:]:
        for r in recs:
            p = [r.get(k) for k in ("p_home", "p_draw", "p_away", "p_over25", "p_btts_yes")]
            cells = "".join(f"<td>{'' if v is None else f'{v:.3f}'}</td>" for v in p)
            rows.append(f"<tr><td>{e(fid)}</td><td>{e(r['league_id'])}</td><td>{e(r['kickoff_utc'][:16])}</td><td>{e(stage)}</td><td>{e(r['family'])}</td>{cells}<td>{e(r['model_version'])}</td></tr>")
    pair_rows = "".join(
        f"<tr><td>{e(x['candidate'])}</td><td>{e(x['stage'])}</td><td>{x['resolved_pairs']}</td><td>{x['candidate_log_loss']}</td><td>{x['global_log_loss']}</td>"
        f"<td>{x['delta_log_loss_candidate_minus_global']}</td><td>{e(x['verdict'])}</td></tr>" for x in scorecard["pairs"]
    ) or "<tr><td colspan=7>No resolved pre-match pair yet: nothing can be said about accuracy.</td></tr>"
    alert_rows = "".join(f"<tr><td>{e(a['severity'])}</td><td>{e(a['key'])}</td><td>{e(a['message'])}</td></tr>" for a in alerts[-20:]) or "<tr><td colspan=3>none</td></tr>"
    bt = ""
    if backtest:
        bt = f"<div class=bt><b>BACKTEST (different protocol, not prospective):</b> {e(json.dumps(backtest))}</div>"
    return (f"<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>League models shadow</title><style>{CSS}</style><main>"
            f"<h1>League-model shadow run</h1><p><small>Generated {e(now.isoformat())}. Research evidence only: nothing here is shown to users and nothing changes the predictions users see.</small></p>"
            f"<div class=warn><b>Routing:</b> {e(kill['routing'])} &middot; <b>Records:</b> {scorecard['records_total']} for {scorecard['fixtures_with_records']} fixtures "
            f"&middot; <b>Resolved fixtures:</b> {scorecard['resolved_fixtures']} &middot; <b>Pending results:</b> {scorecard['pending_outcomes']}<br>{e(scorecard['caution'])}</div>"
            f"<h2>Prospective scorecard (stored before kick-off)</h2><table><tr><th>Candidate</th><th>Stage</th><th>Resolved pairs</th><th>Candidate log loss</th><th>Global log loss</th><th>Delta</th><th>Verdict</th></tr>{pair_rows}</table>"
            f"{bt}<h2>Latest records</h2><table><tr><th>Fixture</th><th>League</th><th>Kick-off UTC</th><th>Stage</th><th>Family</th><th>P home</th><th>P draw</th><th>P away</th><th>P over 2.5</th><th>P BTTS</th><th>Version</th></tr>{''.join(rows)}</table>"
            f"<h2>Alerts</h2><table><tr><th>Severity</th><th>Key</th><th>Message</th></tr>{alert_rows}</table></main></html>")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
