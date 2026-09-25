"""Team identity: canonical teams + provenance-carrying aliases + review queue (ADR 0010).

Unknown names are never silently mapped. Fuzzy similarity is used for SUGGESTIONS only; an alias
becomes effective only when a human approves it (python -m src.data.team_resolution approve ...).
"""

import difflib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

HEADER = (
    "# Team identity store. canonical `teams` + `aliases` (with provenance and validity window).\n"
    "# Add aliases with: python -m src.data.team_resolution approve --raw NAME --team-id ID\n"
)


def normalize_name(raw: str) -> str:
    s = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9 ]", "", s.lower().replace("'", "").replace(".", ""))
    return re.sub(r"\s+", " ", s).strip()


def slug(name: str) -> str:
    return normalize_name(name).replace(" ", "_")


@dataclass(frozen=True)
class Alias:
    source: str
    raw_name: str
    team_id: str
    valid_from: date | None = None
    valid_to: date | None = None
    provenance: str = "manual"  # manual | canonical | fuzzy_approved | auto_registered
    approved_by: str = ""
    confidence: float = 1.0

    def active_on(self, day: date | None) -> bool:
        if day is None:
            return True
        return (self.valid_from is None or self.valid_from <= day) and (
            self.valid_to is None or day <= self.valid_to
        )


@dataclass
class Resolution:
    team_id: str | None
    provenance: str  # alias provenance, or "unresolved"
    suggestions: list[dict] = field(default_factory=list)


@dataclass
class TeamDirectory:
    teams: dict[str, dict[str, str]] = field(default_factory=dict)  # team_id -> record
    aliases: list[Alias] = field(default_factory=list)
    review_queue: dict[str, dict] = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=dict)  # "source|raw|team_id" -> count

    # ------------------------------------------------------------- persistence
    @classmethod
    def load(cls, path: Path) -> "TeamDirectory":
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        d = cls()
        for t in data.get("teams", []):
            d.teams[t["team_id"]] = {
                "team_id": t["team_id"],
                "canonical_name": t["canonical_name"],
                "country": t["country"],
            }
        for a in data.get("aliases", []):
            d.aliases.append(
                Alias(
                    source=a["source"],
                    raw_name=a["raw_name"],
                    team_id=a["team_id"],
                    valid_from=_d(a.get("valid_from")),
                    valid_to=_d(a.get("valid_to")),
                    provenance=a.get("provenance", "manual"),
                    approved_by=a.get("approved_by", ""),
                    confidence=float(a.get("confidence", 1.0)),
                )
            )
        return d

    def dump(self, path: Path) -> None:
        payload = {
            "teams": [self.teams[k] for k in sorted(self.teams)],
            "aliases": [
                {
                    "source": a.source,
                    "raw_name": a.raw_name,
                    "team_id": a.team_id,
                    "valid_from": a.valid_from.isoformat() if a.valid_from else None,
                    "valid_to": a.valid_to.isoformat() if a.valid_to else None,
                    "provenance": a.provenance,
                    "approved_by": a.approved_by,
                    "confidence": a.confidence,
                }
                for a in sorted(self.aliases, key=lambda x: (x.source, x.raw_name, x.team_id))
            ],
        }
        path.write_text(
            HEADER + yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )

    # -------------------------------------------------------------- validation
    def validate(self) -> list[str]:
        """Integrity problems of the store itself (used by `team_resolution validate`)."""
        problems: list[str] = []
        for a in self.aliases:
            if a.team_id not in self.teams:
                problems.append(f"alias {a.raw_name!r} points to unknown team_id {a.team_id}")
            if a.valid_from and a.valid_to and a.valid_from > a.valid_to:
                problems.append(f"alias {a.raw_name!r}: valid_from after valid_to")
        by_key: dict[tuple[str, str], list[Alias]] = {}
        for a in self.aliases:
            by_key.setdefault((a.source, normalize_name(a.raw_name)), []).append(a)
        for (source, raw), group in by_key.items():
            for i, x in enumerate(group):
                for y in group[i + 1 :]:
                    if x.team_id != y.team_id and _overlap(x, y):
                        problems.append(
                            f"conflicting aliases for {raw!r} ({source}): {x.team_id} vs {y.team_id}"
                        )
        for tid, t in self.teams.items():
            if not tid.startswith(t["country"] + "_"):
                problems.append(f"team_id {tid} does not start with its country {t['country']}_")
        return problems

    # -------------------------------------------------------------- resolution
    def _candidates(self, source: str, country: str, day: date | None) -> dict[str, tuple[str, str]]:
        """normalized name -> (team_id, provenance) for names valid for this source/country/date."""
        out: dict[str, tuple[str, str]] = {}
        for t in self.teams.values():
            if t["country"] == country:
                out[normalize_name(t["canonical_name"])] = (t["team_id"], "canonical")
        for a in self.aliases:
            team = self.teams.get(a.team_id)
            if a.source == source and team and team["country"] == country and a.active_on(day):
                out[normalize_name(a.raw_name)] = (a.team_id, a.provenance)
        return out

    def resolve(
        self,
        source: str,
        raw_name: str,
        country: str,
        day: date | None = None,
        suggest_cutoff: float = 0.6,
        auto_register: bool = False,
    ) -> Resolution:
        norm = normalize_name(raw_name)
        if not norm:
            return Resolution(None, "unresolved")
        cands = self._candidates(source, country, day)
        if norm in cands:
            team_id, prov = cands[norm]
            key = f"{source}|{raw_name}|{team_id}"
            self.usage[key] = self.usage.get(key, 0) + 1
            return Resolution(team_id, prov)
        matches = difflib.get_close_matches(norm, list(cands), n=3, cutoff=suggest_cutoff)
        suggestions = [
            {
                "team_id": cands[m][0],
                "candidate": m,
                "similarity": round(difflib.SequenceMatcher(None, norm, m).ratio(), 3),
            }
            for m in matches
        ]
        if auto_register and not suggestions:
            team_id = f"{country}_{slug(raw_name)}"
            self.teams.setdefault(
                team_id,
                {"team_id": team_id, "canonical_name": raw_name.strip(), "country": country},
            )
            self.aliases.append(
                Alias(source, raw_name, team_id, provenance="auto_registered", confidence=0.0)
            )
            return Resolution(team_id, "auto_registered")
        self.review_queue[f"{source}|{country}|{norm}"] = {
            "source": source,
            "country": country,
            "raw_name": raw_name,
            "first_seen": day.isoformat() if day else None,
            "suggestions": suggestions,
        }
        return Resolution(None, "unresolved", suggestions)


def _d(v) -> date | None:
    if v is None or v == "":
        return None
    return v if isinstance(v, date) else date.fromisoformat(str(v))


def _overlap(x: Alias, y: Alias) -> bool:
    lo = max(x.valid_from or date.min, y.valid_from or date.min)
    hi = min(x.valid_to or date.max, y.valid_to or date.max)
    return lo <= hi
