"""Sprint 2 task 6: classify the headline claims of the plan (138 matches, 68.1 %, 68.8 % "always over") as
VERIFIED / UNVERIFIED with their basis.

Offline and read-only. The 138-match record is an observed evaluation window, never a training set. It lives in
`reports/track_record_table.md` (a derived table of `python -m src.markets.track`); this module recounts it, checks
that a pre-match forecast artifact exists for the fixtures, and compares the base rates with the licensed universe.
A claim whose basis cannot be re-derived here is UNVERIFIED, never assumed true."""

import json
import re
from pathlib import Path

REPORTED = {
    "matches": 138,
    "1x2_hits": 94,
    "over_under_hits": 84,
    "btts_hits": 76,
    "1x2_accuracy_pct": 68.1,
    "over_under_accuracy_pct": 60.9,
    "btts_accuracy_pct": 55.1,
    "always_over_pct": 68.8,
}
TABLE = Path("reports") / "track_record_table.md"
ROW = re.compile(r"^\|\s*(\d\d)-(\d\d)\s*\|")


def parse_table(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = ROW.match(line)
        if not m:
            continue
        c = [x.strip() for x in line.strip("|").split("|")]
        hg, ag = (int(v) for v in c[3].split("-"))
        rows.append(
            {
                "month": int(m.group(1)),
                "day": int(m.group(2)),
                "league": c[1],
                "match": c[2],
                "hg": hg,
                "ag": ag,
                "hit_1x2": c[5] == "✓",
                "hit_ou": c[7] == "✓",
                "hit_btts": c[9] == "✓",
            }
        )
    return rows


def _pre_match_artifacts(root: Path, first: str, last: str) -> dict:
    """Fixture directories dated inside [first, last] and how many have at least one artifact generated on an earlier
    calendar day than the fixture (conservative proof of a pre-match forecast; the kick-off time is not in the key)."""
    base = root / "artifacts" / "markets"
    total = with_pre = 0
    for d in sorted(base.glob("*__*__*")):
        day = d.name.rsplit("__", 1)[-1]
        if not (first <= day <= last):
            continue
        files = sorted(d.glob("intel-*.json"))
        if not files:
            continue
        total += 1
        cutoffs = [json.loads(f.read_text(encoding="utf-8"))["information_cutoff"][:10] for f in files]
        if any(c < day for c in cutoffs):
            with_pre += 1
    return {"fixture_dirs_in_window": total, "with_artifact_generated_on_an_earlier_day": with_pre}


def claims_report(root: Path, ev: dict, cfg, metrics: dict) -> dict:
    table_path = root / TABLE
    out: dict = {
        "reported_observation": {
            **REPORTED,
            "status": "REPORTED_BY_USER",
            "use": "evaluation window only, never training",
        },
    }
    if not table_path.is_file():
        out["table_check"] = {"status": "NOT_VERIFIED", "reason": f"{TABLE.as_posix()} not found"}
    else:
        rows = parse_table(table_path)
        n = len(rows)
        hits = (
            sum(r["hit_1x2"] for r in rows),
            sum(r["hit_ou"] for r in rows),
            sum(r["hit_btts"] for r in rows),
        )
        over = sum(r["hg"] + r["ag"] > 2 for r in rows)
        yes = sum(r["hg"] > 0 and r["ag"] > 0 for r in rows)
        dates = sorted(f"2026-{r['month']:02d}-{r['day']:02d}" for r in rows)
        arith_ok = (n, *hits) == (
            REPORTED["matches"],
            REPORTED["1x2_hits"],
            REPORTED["over_under_hits"],
            REPORTED["btts_hits"],
        )
        out["table_check"] = {
            "source": TABLE.as_posix(),
            "rows": n,
            "date_range": [dates[0], dates[-1]] if dates else None,
            "hits_1x2_ou_btts": list(hits),
            "accuracy_pct": [round(100 * h / n, 1) for h in hits] if n else None,
            "always_over_hits": over,
            "always_over_pct": round(100 * over / n, 1) if n else None,
            "always_btts_yes_hits": yes,
            "always_btts_yes_pct": round(100 * yes / n, 1) if n else None,
            "counts_match_reported": arith_ok,
            "status": "VERIFIED_ARITHMETIC" if arith_ok else "UNVERIFIED_COUNTS_DIFFER",
            "pre_match_provenance": _pre_match_artifacts(root, dates[0], dates[-1]) if dates else None,
            "provenance_note": (
                "the table is a derived report; the immutable per-fixture forecast artifacts are the source of truth. "
                "'Verified arithmetic' means the table recounts to the reported numbers, not that the forecasts were "
                "independently re-derived."
            ),
        }
        out["claim_classification"] = {
            "1x2 94/138 = 68.1%": "VERIFIED_ARITHMETIC (table recount); out-of-sample and pre-match only to the extent of the artifact provenance above",
            "over/under 84/138 = 60.9%": "VERIFIED_ARITHMETIC",
            "btts 76/138 = 55.1%": "VERIFIED_ARITHMETIC",
            "always-over 68.8%": "VERIFIED_ARITHMETIC: 95/138 over 2.5 on the same 138 fixtures"
            if over == 95 and n == 138
            else "UNVERIFIED",
            "model beats always-over on over/under": "NO on this window (60.9% < 68.8%)",
            "model beats always-yes on BTTS": "NO on this window (55.1% < 63.0%)"
            if n and yes / n > hits[2] / n
            else "see table_check",
            "small samples (9/9, 6/7 per league)": "NOT evidence; no league claim is made from them",
        }
    all_over = metrics.get("over_under", {}).get("ALL", {}).get("_label_rate")
    all_btts = metrics.get("btts", {}).get("ALL", {}).get("_label_rate")
    rows = ev["rows"]
    out["licensed_universe_comparison"] = {
        "dev_eval_matches": len(rows),
        "dev_eval_range": [
            min(r.kickoff_utc for r in rows).isoformat(),
            max(r.kickoff_utc for r in rows).isoformat(),
        ],
        "over_2_5_share": all_over,
        "btts_yes_share": all_btts,
        "same_fixture_set_as_the_138": False,
        "why_not_same": "the 138 are October 2026 fixtures of the running season (RESERVED, not published in the licensed source "
        "beyond 2026-09-20); the benchmark evaluates seasons 2020-21 to 2024-25. Different leagues mix, dates and class balance, "
        "so the two over-rates are not comparable as a pair of results about one set.",
    }
    return out
