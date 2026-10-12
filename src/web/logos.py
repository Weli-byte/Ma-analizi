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
        if not enabled() or not self.dir.exists():
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

    def team(self, team_id: str) -> Path | None:
        return self.teams.get(team_id)

    def league(self, key: str) -> Path | None:
        if not enabled() or key not in LEAGUE_LOGO_IDS:
            return None
        f = self.dir / f"league_{LEAGUE_LOGO_IDS[key]}.png"
        return f if f.exists() else None

    def team_url(self, team_id: str) -> str | None:
        return f"/logo/team/{team_id}" if self.team(team_id) else None

    def league_url(self, key: str) -> str | None:
        return f"/logo/league/{key}" if self.league(key) else None
