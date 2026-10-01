"""S9: tabulate `ModelResult`s (from `runner.evaluate`) into a leaderboard -- global, per-league,
per-season, and per-model-class. Deliberately NOT a single ranked "winner" table: CLAUDE.md's
non-negotiable rule is multiple metrics together, no overall winner; this module returns every
model's every metric (plus its confidence interval and sample size) for every scope, and leaves
ranking/reading to the report, never collapses it to one number here.

"Horizon" (days-to-kickoff) is part of the original sprint plan's leaderboard dimensions but has
no meaning yet in this repo -- there is no live/forward fixture feed (S13/S14 scope) to measure a
horizon against. Omitted here; add a `by_horizon` scope alongside the others once S13/S14 exist,
following the exact same pattern.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LeaderboardRow:
    model_id: str
    model_class: str
    scope: str  # "global" | f"league:{league}" | f"season:{season}"
    n: int
    metrics: dict[str, float]
    confidence_intervals: dict[str, dict] = field(default_factory=dict)


def build_leaderboard(results) -> dict[str, list[LeaderboardRow]]:
    """`results`: `list[runner.ModelResult]` (or anything with the same `.model_id`,
    `.model_class`, `.metrics`, `.confidence_intervals`, `.by_league`, `.by_season`,
    `.metrics["n"]` shape -- duck-typed, not import-coupled to `runner`, so an LLM-sourced
    result built the same shape can join the same leaderboard without a `runner` dependency)."""
    rows: dict[str, list[LeaderboardRow]] = {
        "global": [], "by_league": [], "by_season": [], "by_model_class": [],
    }  # fmt: skip
    by_class: dict[str, list[LeaderboardRow]] = {}
    for r in results:
        g = LeaderboardRow(
            r.model_id, r.model_class, "global", int(r.metrics.get("n", 0)), r.metrics, r.confidence_intervals
        )
        rows["global"].append(g)
        by_class.setdefault(r.model_class, []).append(g)
        for league, m in r.by_league.items():
            n = int(m.get("n", 0))
            metrics = {k: v for k, v in m.items() if k != "n"}
            scope = f"league:{league}"
            rows["by_league"].append(LeaderboardRow(r.model_id, r.model_class, scope, n, metrics))
        for season, m in r.by_season.items():
            n = int(m.get("n", 0))
            metrics = {k: v for k, v in m.items() if k != "n"}
            scope = f"season:{season}"
            rows["by_season"].append(LeaderboardRow(r.model_id, r.model_class, scope, n, metrics))
    for _cls, cls_rows in sorted(by_class.items()):
        rows["by_model_class"].extend(cls_rows)
    return rows


def render_leaderboard_md(leaderboard: dict[str, list[LeaderboardRow]], metric_names: list[str]) -> str:
    lines = ["## Leaderboard", "", "No single overall winner: read every metric together."]
    for scope_name, rows in leaderboard.items():
        if scope_name == "by_model_class":
            continue  # same rows as "global", regrouped; not re-rendered as its own table
        if not rows:
            continue
        header = "| model | class | scope | n | " + " | ".join(metric_names) + " |"
        lines += ["", f"### {scope_name}", "", header, "|---|---|---|---|" + "---|" * len(metric_names)]
        for row in rows:
            vals = " | ".join(f"{row.metrics.get(m, float('nan')):.4f}" for m in metric_names)
            lines.append(f"| {row.model_id} | {row.model_class} | {row.scope} | {row.n} | {vals} |")
    return "\n".join(lines) + "\n"
