"""Retrain-candidate pipeline of the market model, shadow only (ADR 0092).

`fit_candidate` refits the market model as of a cutoff and describes the result with an immutable manifest: the cutoff, the history's content-derived data version and size, the config
hash, the code commit, the seed (the fit is deterministic: none is used) and the SHA-256 of the fitted parameters (attack / defence strengths, league levels, rho, count-model rates). The
same manifest inputs give the same hash, which is the reproducibility check. `register` appends the candidate to `artifacts/registry/market_candidates.jsonl` with status SHADOW_CANDIDATE.
Nothing here changes the production model: a candidate can only be compared in the experiment gate of sprint 17 and promoted with a human approval and a rollback target.

    python -m src.drift.retrain --as-of 2026-10-31T00:00:00Z --registry artifacts/registry/market_candidates.jsonl
"""

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from src.cli_utils import configure_output
from src.config import config_dir_for, load_config
from src.lineage.record import code_commit
from src.markets.model import MODEL_VERSION, fit_markets
from src.versioning import canonical_json

REPO = Path(__file__).resolve().parents[2]


def _table(t) -> dict:
    return {"attack": {k: round(v, 10) for k, v in sorted(t.attack.items())}, "defence": {k: round(v, 10) for k, v in sorted(t.defence.items())},
            "mu_home": {k: round(v, 10) for k, v in sorted(t.mu_home.items())}, "mu_away": {k: round(v, 10) for k, v in sorted(t.mu_away.items())}, "alpha": round(t.alpha, 10)}  # fmt: skip


def parameters(mm) -> dict:
    return {"goals": _table(mm.goals), "rho": round(mm.rho, 10), "counts": {k: _table(v) for k, v in sorted(mm.counts.items())}, "n_matches": mm.n_matches}


def fit_candidate(matches, as_of: datetime, cfg, data_version: str, config_hash: str) -> dict:
    mm = fit_markets(matches, as_of, cfg)
    params = parameters(mm)
    artifact = hashlib.sha256(canonical_json(params).encode()).hexdigest()
    manifest = {"schema": "market-candidate-v1", "model": MODEL_VERSION, "as_of_utc": as_of.isoformat(), "training_window": f"every match finished (available) by {as_of.isoformat()}",
                "history_data_version": data_version, "history_matches": mm.n_matches, "config_hash": config_hash, "code_commit": code_commit(), "seed": None,
                "calibration": "none (identity champion, ADR 0089)", "evaluation_protocol": "sprint 17 experiment gate (same fixtures, same cutoffs); not evaluated here",
                "artifact_sha256": artifact, "status": "SHADOW_CANDIDATE"}  # fmt: skip
    manifest["manifest_sha256"] = hashlib.sha256(canonical_json({k: v for k, v in manifest.items() if k != "code_commit"}).encode()).hexdigest()
    return manifest


def register(manifest: dict, registry: Path) -> str:
    """Append once; the same artifact is not registered twice (idempotent)."""
    registry.parent.mkdir(parents=True, exist_ok=True)
    known = {json.loads(x)["artifact_sha256"] for x in registry.read_text(encoding="utf-8").splitlines() if x.strip()} if registry.exists() else set()
    if manifest["artifact_sha256"] in known:
        return "EXISTS"
    with registry.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({**manifest, "registered_at": datetime.now(UTC).isoformat()}, sort_keys=True) + "\n")
    return "REGISTERED"


def main(argv=None) -> int:
    from src.data.teams import TeamDirectory
    from src.markets.of_source import load_world

    configure_output()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--as-of", required=True)
    ap.add_argument("--registry", default=str(REPO / "artifacts" / "registry" / "market_candidates.jsonl"))
    a = ap.parse_args(argv)
    as_of = datetime.fromisoformat(a.as_of.replace("Z", "+00:00"))
    cdir = config_dir_for(REPO)
    cfg = load_config("markets", cdir)
    world = load_world(REPO, TeamDirectory.load(cdir / "team_aliases.yaml"), cfg, now=as_of)
    config_hash = hashlib.sha256((cdir / "markets.yaml").read_bytes()).hexdigest()[:16]
    first = fit_candidate(world.history, as_of, cfg, world.data_version, config_hash)
    again = fit_candidate(world.history, as_of, cfg, world.data_version, config_hash)
    first["reproduced"] = first["artifact_sha256"] == again["artifact_sha256"]
    print(json.dumps(first, indent=1))
    print("registry:", register(first, Path(a.registry)))
    return 0 if first["reproduced"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
