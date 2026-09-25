"""Team-name normalization -> canonical team_id, with a review queue for ambiguous names."""

import difflib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import yaml

FUZZY_THRESHOLD = 0.85


def normalize_name(raw: str) -> str:
    s = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9 ]", "", s.lower().replace("'", "").replace(".", ""))
    return re.sub(r"\s+", " ", s).strip()


def slug(name: str) -> str:
    return normalize_name(name).replace(" ", "_")


@dataclass
class TeamRegistry:
    """canonical teams + alias table. Persisted as JSON so reruns are stable."""

    aliases: dict[str, dict[str, str]] = field(default_factory=dict)  # country -> norm -> canonical
    teams: dict[str, dict[str, str]] = field(default_factory=dict)  # team_id -> record
    review_queue: dict[str, dict[str, str]] = field(default_factory=dict)  # key -> entry

    @classmethod
    def from_alias_file(cls, path: Path) -> "TeamRegistry":
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        aliases: dict[str, dict[str, str]] = {}
        for country, mapping in data.items():
            table = {normalize_name(v): v for v in mapping.values()}  # canonical names map to self
            table.update({normalize_name(k): v for k, v in mapping.items()})
            aliases[country] = table
        return cls(aliases=aliases)

    def _canonicals(self, country: str) -> dict[str, str]:
        """normalized-name -> canonical_name for known teams of a country."""
        known = {
            normalize_name(t["canonical_name"]): t["canonical_name"]
            for t in self.teams.values()
            if t["country"] == country
        }
        known.update({k: v for k, v in self.aliases.get(country, {}).items()})
        return known

    def resolve(self, raw_name: str, country: str) -> str | None:
        """Return team_id, or None when ambiguous (entry added to review queue)."""
        norm = normalize_name(raw_name)
        if not norm:
            return None
        known = self._canonicals(country)
        if norm in known:
            return self._register(known[norm], country)
        close = difflib.get_close_matches(norm, list(known), n=1, cutoff=FUZZY_THRESHOLD)
        if close:
            self.review_queue[f"{country}:{norm}"] = {
                "raw_name": raw_name,
                "country": country,
                "suggestion": known[close[0]],
            }
            return None
        return self._register(" ".join(w.capitalize() for w in norm.split()), country)

    def _register(self, canonical: str, country: str) -> str:
        team_id = f"{country}_{slug(canonical)}"
        self.teams.setdefault(
            team_id, {"team_id": team_id, "canonical_name": canonical, "country": country}
        )
        return team_id

    def to_json(self) -> str:
        return json.dumps(
            {"teams": self.teams, "review_queue": self.review_queue}, indent=2, sort_keys=True
        )
