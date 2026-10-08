"""Render the dashboard view model as ONE self-contained HTML file (no external requests, inline SVG,
light/dark via prefers-color-scheme). Every value is HTML-escaped; unknown values render as UNKNOWN,
never as 0 (ADR 0034)."""

from html import escape as e

CSS = """
:root{--bg:#fff;--fg:#1a1a1a;--mut:#666;--card:#f6f7f9;--line:#d9dce1;--ok:#1a7f37;--warn:#9a6700;--bad:#cf222e;--acc:#0969da}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--fg:#e6edf3;--mut:#8b949e;--card:#161b22;--line:#30363d;--ok:#3fb950;--warn:#d29922;--bad:#f85149;--acc:#58a6ff}}
body{background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif;margin:0 auto;max-width:1100px;padding:16px}
h1{font-size:20px}h2{font-size:16px;border-bottom:1px solid var(--line);padding-bottom:4px;margin-top:28px}
table{border-collapse:collapse;width:100%;margin:6px 0;display:block;overflow-x:auto}
th,td{border-bottom:1px solid var(--line);padding:3px 8px;text-align:left;white-space:nowrap;font-size:13px}
.card{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:8px 12px;margin:8px 0}
.mut{color:var(--mut)}code{font-size:12px}
.tag{border-radius:4px;padding:0 6px;font-size:12px;font-weight:600;border:1px solid var(--line)}
.OBSERVED,.OK,.exact{color:var(--ok)}.INFERRED,.WARNING,.approximate{color:var(--warn)}
.UNKNOWN,.STALE,.FAILED,.CRITICAL,.unknown{color:var(--bad)}
svg{max-width:100%}
"""


def tag(status) -> str:
    s = str(status)
    return f'<span class="tag {e(s)}">{e(s)}</span>'


def f(x, nd=3) -> str:
    return "UNKNOWN" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else e(str(x)))


def table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return '<p class="mut">no data</p>'
    head = "".join(f"<th>{e(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><tr>{head}</tr>{body}</table>"


def prob_bar(p) -> str:
    w, cols = 160, ("#2da44e", "#8c959f", "#cf222e")
    x, parts = 0.0, []
    for v, c in zip(p, cols, strict=True):
        parts.append(f'<rect x="{x:.1f}" y="0" width="{v * w:.1f}" height="12" fill="{c}"/>')
        x += v * w
    return f'<svg width="{w}" height="12" role="img" aria-label="H/D/A">{"".join(parts)}</svg> ' + " / ".join(
        f"{v:.2f}" for v in p
    )


def reliability_svg(bins) -> str:
    s = 150
    pts = "".join(
        f'<circle cx="{b["confidence"] * s:.1f}" cy="{s - b["accuracy"] * s:.1f}" '
        f'r="{2 + min(b["count"], 8)}" fill="var(--acc)"/>'
        for b in bins
        if b["count"]
    )
    return (
        f'<svg width="{s}" height="{s}" viewBox="0 0 {s} {s}" role="img" aria-label="reliability">'
        f'<rect width="{s}" height="{s}" fill="none" stroke="var(--line)"/>'
        f'<line x1="0" y1="{s}" x2="{s}" y2="0" stroke="var(--mut)" stroke-dasharray="3"/>{pts}</svg>'
    )


def pred_table(preds) -> str:
    rows = [
        [
            e(p["match"]), e(p["model_id"]), e(p["provider"] or "-"), prob_bar(p["p"]),
            e(p["generated_at"]), e(p["information_cutoff"]), f"<code>{e(p['prediction_id'])}</code>",
            f"<code>{e(p['data_version'])}</code>", e(p["feature_version"]),
            e(p["prompt_version"] or "-"), e(p["source"]),
        ]
        for p in preds
    ]  # fmt: skip
    head = ["match", "model", "provider", "H / D / A", "generated_at", "cutoff", "prediction_id",
            "data_version", "feature_version", "prompt_version", "source"]  # fmt: skip
    return table(head, rows)


def _freshness(ops) -> list[str]:
    fr = ops["data_freshness"]
    out = [
        "<h2>Data freshness &amp; API health</h2>",
        f"<div class='card'>results history: {tag(fr['status'])} latest result "
        f"{f(fr.get('latest_result_utc'))}, age {f(fr.get('age_days'))} d</div>",
    ]
    rows = [
        [e(n), tag(p["status"]), f(p["requests"]), f(p["errors"]), f(p["latency_p50_ms"], 0),
         f(p["latency_p95_ms"], 0), f(p["last_success"]), f(p["last_failure"])]
        for n, p in ops["providers"].items()
    ]  # fmt: skip
    out.append(
        table(
            ["provider", "status", "requests", "errors", "p50 ms", "p95 ms", "last success", "last failure"],
            rows,
        )
    )
    hb = [
        [e(n), tag(h.get("status")), f(h.get("last_run") or h.get("last_beat"))]
        for n, h in ops["heartbeats"].items()
    ]
    out.append(table(["job", "status", "last run"], hb))
    cr = ops["odds_api_credits"]
    left = f(cr["remaining"]) if cr else "UNKNOWN"
    when = f(cr["updated_at"]) if cr else "UNKNOWN"
    out.append(f"<p>The Odds API credits remaining: {left} (as of {when})</p>")
    return out


def _upcoming(vm) -> list[str]:
    up = [m for m in vm["matches"] if m["upcoming"]]
    rows = []
    for m in up:
        odds = (
            "<br>".join(
                f"{e(src)} {tag(r['quality'])} {e(r['bookmaker'])}: {' / '.join(f'{o:.2f}' for o in r['odds'])}"
                for src, rs in m["odds"].items()
                for r in rs
            )
            or "UNKNOWN"
        )
        rows.append([e(m["home"]), e(m["away"]), e(m["kickoff_utc"]), e(m["league"] or "-"),
                     e(", ".join(m["stages"]) or "no stage yet"), tag(m["injuries"]), tag(m["lineups"]), odds])  # fmt: skip
    head = [
        "home",
        "away",
        "kickoff UTC",
        "league",
        "stages",
        "injuries",
        "lineups",
        "raw decimal odds H / D / A",
    ]
    return [f"<h2>Upcoming matches ({len(up)})</h2>", table(head, rows)]


def _picks(vm) -> list[str]:
    pk = vm["picks"]
    out = ["<h2>Bet suggestions (model-based research, not guarantees)</h2>"]
    out.append(
        f"<div class='card'>Thresholds: edge &ge; {pk['thresholds']['min_edge']}, EV &ge; "
        f"{pk['thresholds']['min_ev']}. Only complete <b>exact</b>-timestamp odds qualify. "
        f"{e(pk['note'])}</div>"
    )
    rows = [
        [e(p["fixture_id"]), e(p["selection"]), f(p["odds"], 2), e(p["bookmaker"]), f(p["mean_model_prob"]),
         f(p["market_prob_devig"]), f(p["edge"]), f(p["ev_per_unit"]), f(p["stake_hint_pct_of_bankroll"], 2) + "%",
         f"{p['models_agreeing']}/{p['models_evaluated']}", tag(p["confidence"]), e(p["observed_at"])]
        for p in pk["picks"]
    ]  # fmt: skip
    head = ["fixture", "pick", "odds", "bookmaker", "model p", "market p (de-vig)", "edge", "EV / unit",
            "stake hint (% bankroll, 1/4 Kelly, capped)", "models agree", "confidence", "odds observed"]  # fmt: skip
    out.append(table(head, rows))
    if pk["picks"]:
        out.append(
            "<ul class='mut'>" + "".join(f"<li>{e(c)}</li>" for c in pk["picks"][0]["caveats"]) + "</ul>"
        )
    return out


def _live(vm) -> list[str]:
    out = ["<h2>Live matches</h2>"]
    if not vm["live"]:
        out.append('<p class="mut">no live match recorded</p>')
    for lv in vm["live"]:
        st = lv["state"]
        score = "UNKNOWN" if not st.get("score") else f"{st['score'][0]}-{st['score'][1]}"
        out.append(
            f"<div class='card'><b>{e(lv['fixture_id'])}</b> minute {f(st.get('minute'))} "
            f"{tag(lv['minute_status'])} score {score}, events {lv['events']}</div>"
        )
        rows = [
            [e(p["model_id"]), f(p["p_home"]), f(p["p_draw"]), f(p["p_away"]), f(p["match_minute"]),
             e(p["minute_source"]), e(p["calibration_status"]), f"<code>{e(p['prediction_id'])}</code>",
             f"<code>{e(p['data_version'])}</code>", e(p["feature_version"])]
            for p in lv["forecasts"]
        ]  # fmt: skip
        out.append(
            table(
                [
                    "model",
                    "H",
                    "D",
                    "A",
                    "minute",
                    "minute_source",
                    "calibration",
                    "prediction_id",
                    "data_version",
                    "feature_version",
                ],
                rows,
            )
        )
    return out


def _models(mo) -> list[str]:
    out = ["<h2>Model &amp; provider comparison</h2>"]
    if not mo["available"]:
        return [*out, f"<p class='mut'>{e(mo['reason'])}</p>"]
    out.append(
        f"<div class='card'>run <code>{e(mo['run'])}</code> - n per model {mo['n_per_model']} - "
        f"{e(mo['caveat'])} Non-LLM: {e(mo['non_llm'])}.</div>"
    )
    rows = [
        [e(r["model_id"]), e(r["model_class"]), f(r["n"]), f(r["log_loss"]), f(r["brier"]), f(r["rps"]),
         f(r["ece_raw"]), f(r["accuracy"]),
         f"{f(r['ci'].get('brier', {}).get('lower'))} - {f(r['ci'].get('brier', {}).get('upper'))}"]
        for r in mo["models"]
    ]  # fmt: skip
    out.append(
        table(["model", "class", "n", "log loss", "brier", "RPS", "ECE", "accuracy", "brier 95% CI"], rows)
    )
    out.append("<h2>Calibration</h2>")
    rows = []
    for m, c in mo["calibration"].items():
        raw, cal = c.get("raw", {}), c.get("calibrated", {})
        rows.append([e(m), "skipped: " + e(c["reason"]) if c["skipped"] else "fitted", f(c.get("temperature")),
                     f(c.get("fit_rows")), f(c.get("report_rows")), f(raw.get("log_loss")),
                     f(cal.get("log_loss")), f(raw.get("ece_raw"))])  # fmt: skip
    out.append(
        table(
            [
                "model",
                "state",
                "temperature",
                "fit rows",
                "report rows",
                "log loss raw",
                "log loss calibrated",
                "ECE raw",
            ],
            rows,
        )
    )
    out.append(
        "<div style='display:flex;flex-wrap:wrap;gap:16px'>"
        + "".join(
            f"<div><div class='mut'>{e(m)} (n={r['n']})</div>{reliability_svg(r['bins'])}</div>"
            for m, r in mo["reliability"].items()
        )
        + "</div>"
    )
    out.append("<h2>Historical performance (by league / season)</h2>")
    rows = []
    for h in mo["history"]:
        for kind, d in (("league", h["by_league"]), ("season", h["by_season"])):
            for key, m in d.items():
                rows.append(
                    [e(h["model_id"]), kind, e(key), f(m.get("n")), f(m.get("log_loss")), f(m.get("brier"))]
                )
    out.append(table(["model", "by", "key", "n", "log loss", "brier"], rows))
    return out


def _usage(ops) -> list[str]:
    rows = [
        [e(n), f(p["calls"]), f(p["successful"]), f(p["failed"]), f(p["failure_rate"]), f(p["rate_limit_rate"]),
         f(p["latency_p50_ms"], 0), f(p["latency_p95_ms"], 0), f(p["tokens_total"]), f(p["estimated_cost_usd"], 4)]
        for n, p in ops["llm_by_provider"].items()
    ]  # fmt: skip
    head = [
        "provider",
        "calls",
        "ok",
        "failed",
        "failure rate",
        "429 rate",
        "p50 ms",
        "p95 ms",
        "tokens",
        "est. cost USD",
    ]
    return [
        "<h2>LLM usage &amp; cost</h2>",
        table(head, rows),
        f"<p class='mut'>pricing table {e(ops['pricing_version'])}; estimates, not invoices. Anthropic: NOT_CONFIGURED.</p>",
    ]


def render_html(vm: dict) -> str:
    out = [
        "<h1>Football forecasting dashboard</h1>"
        f"<p class='mut'>generated {e(vm['generated_at'])} - paper only, no betting advice, research data</p>"
    ]
    out += _freshness(vm["ops"])
    out += _upcoming(vm)
    out.append("<h2>Pre-match probabilities</h2>")
    out.append(pred_table(vm["predictions"]) if vm["predictions"] else '<p class="mut">no prediction yet</p>')
    out.append("<h2>Forecast updates</h2>")
    out.append(
        table(
            ["match", "delta"],
            [[e(u["match"]), e(str({k: v for k, v in u.items() if k != "match"}))] for u in vm["updates"]],
        )
    )
    out += _picks(vm)
    out += _live(vm)
    out += _models(vm["models"])
    out += _usage(vm["ops"])
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'><title>Forecast dashboard</title>"
        f"<style>{CSS}</style></head><body>{''.join(out)}</body></html>"
    )
