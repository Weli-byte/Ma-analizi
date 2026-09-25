"""League / season mapping for the football-data.co.uk source."""

from dataclasses import dataclass


@dataclass(frozen=True)
class League:
    league_id: str
    source_code: str  # football-data "Div" / file code
    country: str  # ISO-ish 3 letter, used in team ids
    country_name: str
    name: str
    tier: int
    matches_per_season: int


LEAGUES: dict[str, League] = {
    "EPL": League("EPL", "E0", "ENG", "England", "Premier League", 1, 380),
    "LALIGA": League("LALIGA", "SP1", "ESP", "Spain", "La Liga", 1, 380),
}
BY_SOURCE_CODE = {lg.source_code: lg for lg in LEAGUES.values()}


def season_from_code(code: str) -> str:
    """'2324' -> '2023-24' (football-data season folder code)."""
    if len(code) != 4 or not code.isdigit():
        raise ValueError(f"invalid season code {code!r}")
    a, b = int(code[:2]), int(code[2:])
    if b != (a + 1) % 100:
        raise ValueError(f"season code {code!r} is not consecutive years")
    return f"20{code[:2]}-{code[2:]}"


def season_to_code(season: str) -> str:
    """'2023-24' -> '2324'."""
    return season[2:4] + season[5:7]
