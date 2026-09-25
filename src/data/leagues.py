"""League/season helpers. Formats live in configs/leagues.yaml (LeagueFormat), never in code."""

from src.config import LeagueFormat


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
    if len(season) != 7 or season[4] != "-":
        raise ValueError(f"invalid season {season!r}")
    return season[2:4] + season[5:7]


def season_start_year(season: str) -> int:
    return int(season[:4])


def season_date_window(season: str, fmt: LeagueFormat) -> tuple[tuple[int, int], tuple[int, int]]:
    """((start_year, start_month), (end_year, end_month)) within which matches may fall."""
    y = season_start_year(season)
    return (y, fmt.season_start_month), (y + 1, fmt.season_end_month)
