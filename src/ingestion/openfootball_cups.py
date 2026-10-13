"""openfootball/champions-league (CC0 public domain): Champions League, Europa League and Conference League results.

Verified 2026-10-13 on the real repository: per season a `cl.txt` (2019-20 onward, complete through the 2025-26 final),
`el.txt` (2020-21..2024-25) and `conf.txt` (2021-22..2024-25) in the Football.TXT format; there is NO file for the running
2026-27 season and none for the 2025-26 Europa/Conference League. Team names carry the country in brackets, e.g.
`Arsenal FC (ENG)`. Result shapes (all seen in the real files):
  `2-2 (1-0)`                          full time 2-2, half time 1-0
  `0-0`                                full time, no half time listed
  `3-2 a.e.t. (3-0, 1-0)`              after extra time 3-2; (90 minutes, half time)
  `4-3 pen. 1-1 a.e.t. (1-1, 0-1)`     penalties 4-3, after extra time 1-1; 90 minutes 1-1, half time 0-1
The goals models use the 90-MINUTE score. Kick-off times are CET/CEST clock times (converted with Europe/Paris, INFERRED).
"""

import re
import urllib.request
from dataclasses import dataclass
from datetime import UTC, date, datetime, time

from src.data.timezones import InvalidLocalTime, date_only_to_utc, local_to_utc
from src.mlops.oplog import logged_urlopen

from .endpoints import endpoint

BASE = "https://raw.githubusercontent.com/openfootball/champions-league/master"
SOURCE = "openfootball-cups"
COMPETITIONS = {"UCL": "cl", "UEL": "el", "UECL": "conf"}  # repo league key -> file stem
FIRST_SEASON = {"UCL": 2019, "UEL": 2020, "UECL": 2021}  # first season start year the repository has
LAST_SEASON = {"UCL": 2025, "UEL": 2024, "UECL": 2024}  # last season it has (checked 2026-10-13)
MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    )
}

DATE_RE = re.compile(r"^\s+(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+([A-Z][a-z]{2})\s+(\d{1,2})(?:\s+(\d{4}))?\s*$")
MATCH_RE = re.compile(
    r"^\s*(?:(?P<time>\d{1,2}[:.]\d{2})\s+)?(?P<home>.+?)\s+\((?P<hc>[A-Z]{3})\)\s+v\s+"
    r"(?P<away>.+?)\s+\((?P<ac>[A-Z]{3})\)(?P<rest>\s+\d+-\d+.*)?\s*$"
)
STAGE_RE = re.compile(r"^▪\s*(.+?)\s*$")
PAIR = re.compile(r"(\d+)-(\d+)")


@dataclass(frozen=True)
class CupMatch:
    competition: str
    season: str  # "2025-26"
    kickoff_utc: datetime
    home: str
    home_cc: str
    away: str
    away_cc: str
    goals: tuple[int, int] | None  # 90-minute score; None = not played yet
    stage: str
    time_known: bool


def score_90(rest: str | None) -> tuple[int, int] | None:
    """The 90-minute score from the text after the team names (see the module docstring)."""
    if not rest or not rest.strip():
        return None
    rest = rest.strip()
    pairs = PAIR.findall(rest)
    if not pairs:
        return None
    if "a.e.t." in rest:
        inner = re.search(r"\((\d+)-(\d+)\s*(?:,|\))", rest)  # first pair inside the brackets
        if inner:
            return int(inner.group(1)), int(inner.group(2))
        return None  # extra time without a 90-minute score: not usable
    return int(pairs[0][0]), int(pairs[0][1])


def parse(text: str, competition: str, start_year: int) -> list[CupMatch]:
    season = f"{start_year}-{str(start_year + 1)[2:]}"
    out: list[CupMatch] = []
    stage, year, new_year_phase, clock, day = "", start_year, False, None, None
    for line in text.splitlines():
        s = STAGE_RE.match(line)
        if s:
            stage = s.group(1)
            continue
        d = DATE_RE.match(line)
        if d:
            month = MONTHS[d.group(1)]
            if d.group(3):
                year = int(d.group(3))
                new_year_phase = month <= 6 and year > start_year
            elif month <= 6:
                year, new_year_phase = start_year + 1, True  # January-June belong to the second calendar year
            else:
                # July-December: the first calendar year, except after the new year already began (2019-20 resumed in August)
                year = start_year + 1 if new_year_phase else start_year
            day = date(year, month, int(d.group(2)))
            clock = None
            continue
        m = MATCH_RE.match(line)
        if not m or day is None:
            continue
        if m.group("time"):
            hh, mm = m.group("time").replace(".", ":").split(":")
            clock = time(int(hh), int(mm))
        try:
            kickoff = (
                local_to_utc(day, clock or time(0, 0), "Europe/Paris") if clock else date_only_to_utc(day)
            )
        except InvalidLocalTime:
            kickoff = date_only_to_utc(day)
        out.append(
            CupMatch(
                competition,
                season,
                kickoff.astimezone(UTC),
                m.group("home").strip(),
                m.group("hc"),
                m.group("away").strip(),
                m.group("ac"),
                score_90(m.group("rest")),
                stage,
                clock is not None,
            )  # fmt: skip
        )
    return out


def is_neutral_stage(competition: str, season: str, stage: str) -> bool:
    """A match played at ONE neutral venue: the final of every cup, and the single-leg quarter-finals and
    semi-finals of the 2019-20 Champions League (the Lisbon "Final 8"). Other neutral venues (the 2020-21 COVID
    relocations) are not marked: the files do not say where a match was played."""
    last = stage.split(",")[-1].strip()
    if last == "Final":
        return True
    return competition == "UCL" and season == "2019-20" and last in ("Quarterfinals", "Semifinals")


def is_final_date(competition: str, kickoff, others) -> bool:
    """Forecasting side: fixtures come from the odds provider without a stage. A cup match from 15 May to the end of
    June with NO other match of the same competition within four days is the final (semi-final second legs are
    played in pairs on one night and end by 18 May; the single finals of 2021-2026 stand alone, verified on the files)."""
    from datetime import timedelta

    if competition not in COMPETITIONS or not (
        (kickoff.month == 5 and kickoff.day >= 15) or kickoff.month == 6
    ):
        return False
    return all(abs(o - kickoff) > timedelta(days=4) for o in others if o != kickoff)


def season_url(competition: str, start_year: int) -> str:
    return f"{endpoint('OPENFOOTBALL_CUPS_BASE_URL', BASE)}/{start_year}-{str(start_year + 1)[2:]}/{COMPETITIONS[competition]}.txt"


def fetch(competition: str, start_year: int, timeout: float = 30.0) -> bytes:
    return logged_urlopen(
        SOURCE, "season", urllib.request.Request(season_url(competition, start_year)), timeout
    )
