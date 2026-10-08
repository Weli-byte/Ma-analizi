"""Build the `availability.injuries` block of a stage snapshot from REAL provider data (ADR 0028).

Status vocabulary (never collapses unknown into "none"): OBSERVED (a provider answered),
UNKNOWN (no provider covers this fixture), FAILED (a provider was asked and errored). Lineups stay
UNKNOWN: no implemented provider supports them (see `src.ingestion.capabilities`).

Cutoff rule: a player entry is used only if the provider dated it (`effective_at`) at or before the
stage's information cutoff; later-dated entries are counted in `excluded_post_cutoff`, never used.
Absence from the list means "no flag from the provider", not "will play".
"""

from datetime import datetime

from src.ingestion.interfaces import AvailabilityStatus, PlayerAvailability

NOTE = (
    "provider flags only (not an official team injury list); absence = no flag, not confirmation "
    "to play; availability_pct is the provider's next-round chance"
)


def unknown_block(reason: str) -> dict:
    return {"status": "UNKNOWN", "reason": reason}


def failed_block(source: str, error: str) -> dict:
    return {"status": "FAILED", "source": source, "reason": error[:200]}


def injuries_block(
    players: list[PlayerAvailability],
    team_ids: tuple[str, str],
    cutoff: datetime,
    observed_at: datetime,
    source: str,
    raw_response_sha256: str | None,
) -> dict:
    mine = [p for p in players if p.team_id in team_ids]
    used, excluded = [], 0
    for p in mine:
        as_of = p.effective_at or p.observed_at  # provider date if given, else the state as of our fetch
        if as_of > cutoff:
            excluded += 1  # dated/observed after the cutoff: cannot be known at cutoff
            continue
        used.append(
            {
                "player": p.player,
                "team_id": p.team_id,
                "status": p.status.value,
                "availability_pct": p.availability_pct,
                "detail": p.detail,
                "source": p.source,
                "effective_at": p.effective_at.isoformat() if p.effective_at else None,
                "as_of": as_of.isoformat(),  # the single time the cutoff rule is checked against
                "confidence": p.confidence,
            }
        )
    used.sort(key=lambda d: (d["team_id"], d["player"]))
    return {
        "status": "OBSERVED",
        "source": source,
        "observed_at": observed_at.isoformat(),
        "raw_response_sha256": raw_response_sha256,
        "cutoff_rule": "(effective_at or observed_at) <= information_cutoff",
        "players": used,
        "excluded_post_cutoff": excluded,
        "note": NOTE,
        "counts": {s.value: sum(1 for d in used if d["status"] == s.value) for s in AvailabilityStatus},
    }


def merge_blocks(blocks: list[dict], team_ids: tuple[str, str]) -> dict:
    """One availability.injuries block from several sources. OBSERVED if any source answered; a source
    that FAILED is listed (never silently dropped); players keep their `source`."""
    observed = [b for b in blocks if b["status"] == "OBSERVED"]
    if not observed:
        return blocks[0]
    players = sorted(
        (p for b in observed for p in b["players"]), key=lambda d: (d["team_id"], d["player"], d["source"])
    )
    return {
        "status": "OBSERVED",
        "source": "+".join(sorted({b["source"] for b in observed})),
        "sources": {
            b["source"]: {"observed_at": b["observed_at"], "raw_response_sha256": b["raw_response_sha256"]}
            for b in observed
        },
        "failed_sources": {b["source"]: b["reason"] for b in blocks if b["status"] == "FAILED"},
        "observed_at": max(b["observed_at"] for b in observed),
        "cutoff_rule": observed[0]["cutoff_rule"],
        "players": players,
        "excluded_post_cutoff": sum(b["excluded_post_cutoff"] for b in observed),
        "note": NOTE,
        "counts": {s.value: sum(1 for d in players if d["status"] == s.value) for s in AvailabilityStatus},
    }
