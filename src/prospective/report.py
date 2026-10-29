"""Sprint 12 report: prospective-only record of the published forecasts (ADR 0090).

`python -m src.prospective.report --markets-root <markets-data checkout> --as-of 2026-10-29T14:00:00Z --out docs/prospective/sprint12_prospective_record.md`

Only forecasts published before kick-off are scored (ADR 0048); nothing here is a backtest. Hit tables show hits / matches with Wilson 95 % intervals, per league, for the last 30 and 100
matches and for all of them, next to the trivial strategies. The audit section reports immutability, timing and settlement (unsettled fixtures are listed, never filled).
"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from src.cli_utils import configure_output
from src.markets.track import wilson

from .audit import SETTLE_GRACE, audit

MARKETS = (("pick_hit", "1X2"), ("ou_hit", "2.5 Alt/Üst"), ("btts_hit", "KG"), ("score_hit", "Skor"))


def cell(hits: int, n: int, ci: bool = False) -> str:
    if n == 0:
        return "-"
    s = f"{hits}/{n} (%{100 * hits / n:.1f})"
    if ci:
        lo, hi = wilson(hits, n)
        s += f" [{100 * lo:.0f}-{100 * hi:.0f}]"
    return s


def table(rows: list[dict], label: str = "Lig") -> list[str]:
    out = [f"| {label} | Maç | " + " | ".join(m for _, m in MARKETS) + " |", "|---|---|" + "---|" * len(MARKETS)]
    leagues: dict[str, list[dict]] = {}
    for r in rows:
        leagues.setdefault(r["league"], []).append(r)
    for lg, rs in sorted(leagues.items(), key=lambda kv: -len(kv[1])):
        out.append(f"| {lg} | {len(rs)} | " + " | ".join(cell(sum(1 for x in rs if x[k]), len(rs)) for k, _ in MARKETS) + " |")
    out.append(f"| **Toplam** | **{len(rows)}** | " + " | ".join(f"**{cell(sum(1 for x in rows if x[k]), len(rows), ci=True)}**" for k, _ in MARKETS) + " |")
    return out


def naive(rows: list[dict]) -> list[str]:
    n = len(rows)
    home = sum(1 for x in rows if x["result"] == "H")
    over = sum(1 for x in rows if x["over_actual"])
    yes = sum(1 for x in rows if (x["btts_pick"] == "Var") == x["btts_hit"])
    return ["| basit strateji | 1X2 | 2.5 Alt/Üst | KG |", "|---|---|---|---|",
            f"| hep ev sahibi / hep üst / hep KG var | {cell(home, n, True)} | {cell(over, n, True)} | {cell(yes, n, True)} |"]  # fmt: skip


STAGES = (("t-24h", 24 * 60), ("t-90m", 90), ("t-30m", 30))


def stage_rows(markets_root) -> tuple[list[dict], int]:
    """The published product forecast as it stood at each stage cutoff (the last artifact at or before kick-off minus the stage), scored on the settled matches.
    Fixtures whose stored kick-off date differs from the played date (a provisional date) are left out: their stage cutoffs would be wrong. Returns (rows, left out)."""
    import math

    from src.markets.track import load_artifacts

    root = Path(markets_root)
    track = json.loads((root / "artifacts" / "markets" / "track_record.json").read_text(encoding="utf-8"))
    arts = load_artifacts(root)
    out, skipped = [], 0
    for m in track["matches"]:
        cand = arts.get(m["key"], [])
        if not cand:
            continue
        kickoff = datetime.fromisoformat(cand[0]["kickoff_utc"])
        if kickoff.date().isoformat() != m["date"]:
            skipped += 1
            continue
        h, a = (int(x) for x in m["score"].split("-"))
        y = 0 if h > a else 1 if h == a else 2
        for stage, minutes in STAGES:
            cut = kickoff.timestamp() - minutes * 60
            seen = [x for x in cand if datetime.fromisoformat(x["information_cutoff"]).timestamp() <= cut]
            if not seen:
                continue
            art = max(seen, key=lambda x: x["information_cutoff"])
            i = art["intelligence"]
            hp = [i["result"]["headline_probs"][k] for k in ("home", "draw", "away")]
            over = next(x["over"] for x in i["goals"]["over_under"] if x["line"] == 2.5)
            out.append({"stage": stage, "pick_hit": max(range(3), key=lambda k: hp[k]) == y, "ou_hit": (over >= 0.5) == (h + a > 2), "btts_hit": (i["goals"]["btts_yes"] >= 0.5) == (h > 0 and a > 0),
                        "ll": -math.log(max(hp[y], 1e-12))})  # fmt: skip
    return out, skipped


def stage_table(rows: list[dict], skipped: int) -> list[str]:
    lines = ["## Forecast at each stage (published product forecast as it stood at the cutoff)", "", "| aşama | maç | 1X2 | 2.5 Alt/Üst | KG | 1X2 log loss |", "|---|---|---|---|---|---|"]
    for stage, _ in STAGES:
        rs = [r for r in rows if r["stage"] == stage]
        if rs:
            n = len(rs)
            lines.append(f"| {stage} | {n} | {cell(sum(r['pick_hit'] for r in rs), n)} | {cell(sum(r['ou_hit'] for r in rs), n)} | {cell(sum(r['btts_hit'] for r in rs), n)} | {sum(r['ll'] for r in rs) / n:.4f} |")
    lines += ["", f"A stage counts only matches that had a published forecast by that cutoff, so the rows are not the same matches; {skipped} fixtures stored under a provisional date are left out. The live record above uses the LAST forecast before kick-off.", ""]
    return lines


def build(track: dict, aud: dict, as_of: datetime, gates: dict, stages: list[str] | None = None) -> str:
    rows = sorted(track["matches"], key=lambda x: (x["date"], x["key"]))
    h = track["headline_1x2"]
    lines = [
        "# Sprint 12 - prospective record of the published forecasts (ADR 0090)", "",
        f"As of {as_of.isoformat()} · live record generated {track['generated_at']} · {len(rows)} settled matches from {rows[0]['date']} to {rows[-1]['date']} · `python -m src.prospective.report`.",
        "Only forecasts published BEFORE kick-off are scored; backtests are in the league-programme and Phase 2 sprint reports, never mixed in here.", "",
        "## Audit",
        f"- Immutability: {aud['artifacts']} stored forecasts, content hash recomputed for every one: **{len(aud['hash_mismatch'])} mismatches**.",
        f"- Timing: information cutoff differs from the generation time in {aud['cutoff_differs_from_generated']}; published before kick-off {aud['pre_kickoff']}, after kick-off {aud['post_kickoff']} (never scored).",
        f"- Settlement: of {aud['fixtures_with_forecast_past_grace']} fixtures with a pre-kick-off forecast that kicked off more than {int(SETTLE_GRACE.total_seconds() // 3600)} h before the report, {aud['settled']} are settled and **{len(aud['unsettled'])} are not** (listed below; they are missing from every rate in this report, so the rates cover the settled matches only).", "",
        "## All settled matches", "", *table(rows), "", *naive(rows), "",
        f"Primary metrics on all {track['matches_scored']} matches (lower is better): 1X2 log loss {h['log_loss']:.4f} (model only {h['log_loss_model_only']:.4f}, league base rate {h['log_loss_league_base_rate']:.4f}), Brier {h['brier']:.4f}, RPS {h['rps']:.4f}; over/under 2.5 log loss {track['over_under_2_5_log_loss']:.4f}; BTTS log loss {track['btts_log_loss']:.4f}.", "",
        "## Last 100 settled matches", "", *table(rows[-100:]), "",
        "## Last 30 settled matches", "", *table(rows[-30:]), "",
        "Leagues with fewer than 20 matches are noise; one league's high rate is not a result.", "",
        *(stages or []),
    ]  # fmt: skip
    lag = aud.get("settlement_lag_hours") or {}
    if lag.get("n"):
        lines += [f"Settlement lag (kick-off to the first time a collector saw the result, an upper bound): median {lag['median']:.1f} h, 90 % within {lag['p90']:.1f} h, max {lag['max']:.1f} h over {lag['n']} results.", ""]
    if gates:
        lines += ["## Live gates (pre-registered)", ""]
        for name, g in gates.items():
            lines.append(f"- {name}: {g}")
        lines.append("")
    if aud["unsettled"]:
        lines += ["## Unsettled fixtures (forecast published, no result stored)", "", "| fixture | league | kick-off | age (days) | reason |", "|---|---|---|---|---|"]
        lines += [f"| {u['fixture']} | {u['league']} | {u['kickoff'][:16]} | {u['age_days']} | {u.get('reason', '')} |" for u in sorted(aud["unsettled"], key=lambda u: u["kickoff"])]
        lines.append("")
    return "\n".join(lines)


def _gates(root: Path) -> dict:
    out = {}
    exp = root / "artifacts" / "markets" / "experimental_status.json"
    if exp.exists():
        e = json.loads(exp.read_text(encoding="utf-8"))
        out["experimental card (ADR 0073)"] = f"{e.get('status')} - {e.get('status_reason', '')} ({e.get('matches_paired')} matches)"
    sg = root / "artifacts" / "markets" / "shadow_gate.json"
    if sg.exists():
        s = json.loads(sg.read_text(encoding="utf-8"))
        fams = s.get("families") or {}
        out["shadow families (ADR 0076)"] = ", ".join(f"{k}: {v.get('status')}" for k, v in sorted(fams.items())) or "no family block"
    return out


def main(argv=None) -> int:
    configure_output()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--markets-root", required=True)
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    root = Path(a.markets_root)
    as_of = datetime.fromisoformat(a.as_of.replace("Z", "+00:00")) if a.as_of else datetime.now(UTC)
    track = json.loads((root / "artifacts" / "markets" / "track_record.json").read_text(encoding="utf-8"))
    srows, skipped = stage_rows(root)
    text = build(track, audit(root, as_of), as_of, _gates(root), stage_table(srows, skipped))
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text + "\n", encoding="utf-8", newline="\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
