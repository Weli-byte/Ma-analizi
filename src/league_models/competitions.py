"""The nine competitions of the programme and their season formats
(configs/league_models/competitions.yaml)."""

from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "configs" / "league_models" / "competitions.yaml"
SOURCE_IDS = {"openfootball": "openfootball_domestic", "openfootball_cups": "openfootball_cups"}


@dataclass(frozen=True)
class Competition:
    canonical_id: str
    repo_key: str  # the key used elsewhere in this repository (EPL, LALIGA, ..., UCL, UEL, UECL)
    name: str
    kind: str  # "domestic" | "uefa"
    country: str | None
    source: str  # licence-manifest provider id
    file_code: str | None  # openfootball league file stem (domestic)
    file_stem: str | None  # openfootball cups file stem (uefa)
    first_season: int | None = (
        None  # start year of the first season the competition existed (None = before the audit window)
    )


@dataclass(frozen=True)
class LeagueConfig:
    competitions: dict[str, Competition]
    history_start_year: int
    result_lag_hours: int
    domestic_sizes: dict[str, list[dict]]
    uefa_formats: dict[str, list[dict]]
    format_status: str
    max_missingness_for_training: float
    min_kickoff_time_known: float
    min_identity_coverage: float

    def by_repo_key(self, repo_key: str) -> Competition:
        for c in self.competitions.values():
            if c.repo_key == repo_key:
                return c
        raise KeyError(f"unknown repo league key: {repo_key}")


def season_label(start_year: int) -> str:
    return f"{start_year}-{str(start_year + 1)[2:]}"


def load_config(path: Path = CONFIG_PATH) -> LeagueConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    comps = {}
    for cid, c in raw["competitions"].items():
        comps[cid] = Competition(
            canonical_id=cid,
            repo_key=c["repo_key"],
            name=c["name"],
            kind=c["kind"],
            country=c.get("country"),
            source=SOURCE_IDS[c["source"]],
            file_code=c.get("file_code"),
            file_stem=c.get("file_stem"),
            first_season=c.get("first_season"),
        )
    rules = raw["engineering_rules"]
    return LeagueConfig(
        comps,
        int(raw["history_start_year"]),
        int(raw["result_lag_hours"]),
        raw["domestic_sizes"],
        raw["uefa_formats"],
        raw["format_status"],
        float(rules["max_missingness_for_training"]),
        float(rules["min_kickoff_time_known"]),
        float(rules["min_identity_coverage"]),
    )


def _latest_from(entries: list[dict], start_year: int) -> dict | None:
    applicable = [e for e in entries if e["from"] <= start_year]
    return max(applicable, key=lambda e: e["from"]) if applicable else None


def format_version(cfg: LeagueConfig, canonical_id: str, start_year: int) -> str:
    """UEFA format id for the season, `domestic` for a league, `UNKNOWN_FORMAT` before the first declared
    one."""
    if cfg.competitions[canonical_id].kind == "domestic":
        return "domestic"
    entry = _latest_from(cfg.uefa_formats[canonical_id], start_year)
    return entry["id"] if entry else "UNKNOWN_FORMAT"


def expected_matches(cfg: LeagueConfig, canonical_id: str, start_year: int) -> int | None:
    """Matches of a complete double round-robin season, or None when the league size is not known."""
    if cfg.competitions[canonical_id].kind != "domestic":
        return None
    entry = _latest_from(cfg.domestic_sizes[canonical_id], start_year)
    n = entry["n_teams"] if entry else None
    return n * (n - 1) if n else None
