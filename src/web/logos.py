"""Club and league logos for the web UI (ADR 0052).

Logos are club/league trademarks. They are shown ONLY when `FORECAST_UI_LOGOS=1` (a local demonstration, e.g. for a
teacher), taken from the private review store (`artifacts/logos`, downloaded on a runner from Highlightly: identification
use only, terms 6.3). A public deployment must leave the variable unset: pages then show neutral initials badges.
"""

import json
import os
from pathlib import Path

from src.config import config_dir_for
from src.data.teams import TeamDirectory
from src.markets.alias_review_page import hl_ids

LEAGUE_LOGO_IDS = {
    "EPL": 33973,
    "LALIGA": 119924,
    "BUNDESLIGA": 67162,
    "SERIEA": 115669,
    "LIGUE1": 52695,
    "SUPERLIG": 173537,
}


def enabled() -> bool:
    return os.environ.get("FORECAST_UI_LOGOS") == "1"


class LogoIndex:
    def __init__(self, root: Path):
        self.dir = Path(root) / "artifacts" / "logos"
        self.teams: dict[str, Path] = {}
        self.crests = Path(root) / "artifacts" / "crests"
        if not enabled():
            return
        if not self.dir.exists():
            self._add_crests()
            return
        directory = TeamDirectory.load(config_dir_for(root) / "team_aliases.yaml")
        for tid, ids in hl_ids(Path(root), directory).items():
            f = next((self.dir / f"{i}.png" for i in sorted(ids) if (self.dir / f"{i}.png").exists()), None)
            if f:
                self.teams[tid] = f
        search = self.dir / "_search.json"
        if search.exists():  # clubs the store has never listed (found by name search)
            for tid, m in json.loads(search.read_text(encoding="utf-8")).items():
                f = self.dir / f"{m['id']}.png"
                if tid not in self.teams and f.exists():
                    self.teams[tid] = f
        self._add_crests()

    def _add_crests(self) -> None:
        """Club crests from football-data.org (ADR 0059) for clubs the Highlightly logos do not cover."""
        if not self.crests.exists():
            return
        for f in sorted(self.crests.iterdir()):
            if f.suffix in (".png", ".svg") and not f.stem.startswith("league_"):
                self.teams.setdefault(f.stem, f)

    def team(self, team_id: str) -> Path | None:
        return self.teams.get(team_id)

    def league(self, key: str) -> Path | None:
        if not enabled():
            return None
        if key in LEAGUE_LOGO_IDS and (self.dir / f"league_{LEAGUE_LOGO_IDS[key]}.png").exists():
            return self.dir / f"league_{LEAGUE_LOGO_IDS[key]}.png"
        for ext in ("png", "svg"):  # competition emblem from football-data.org (e.g. the Champions League)
            if (self.crests / f"league_{key}.{ext}").exists():
                return self.crests / f"league_{key}.{ext}"
        return None

    def team_url(self, team_id: str) -> str | None:
        return f"/logo/team/{team_id}" if self.team(team_id) else None

    def league_url(self, key: str) -> str | None:
        return f"/logo/league/{key}" if self.league(key) else None
