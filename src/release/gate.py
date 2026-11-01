"""Release gate (ADR 0097): one place that says whether this commit may be released, separately for the research product that runs today and for a commercial launch.

    python -m src.release.gate [--ci-status success] [--bundle dist/mac-tahmin] [--out docs/phase3/s18_release_gate.json]

Checks (each PASS / FAIL / NOT_CHECKED with its evidence):
- evidence: the ADRs and reports of Phase 3 sprints 13-17 exist (a missing sprint BLOCKS the release);
- champion pin: `configs/release/canary.yaml` names the model the code serves; no challenger is bound without a PROMOTION_CANDIDATE;
- ci: GitHub Actions concluded success for this commit (passed in by the workflow; never assumed);
- drills: restore, migration rollback, model rollback and the deployment drills of `docs/phase3/s18_drills.json` passed;
- security: the API fails closed without keys and rate-limits, cookies are HttpOnly / SameSite and Secure behind HTTPS, every workflow declares token permissions, no secret value appears in
  the site bundle (the values of the secret environment variables are compared, never printed), the secret-scan allowlist is empty, provider errors in the call log carry no key;
- licences: per source of `configs/league_models/source_licenses.yaml`; UNKNOWN or CONDITIONAL is never treated as approved. A commercial launch needs every source the product uses to be
  PERMITTED; the research product may run on CONDITIONAL / RESEARCH_ONLY sources with the attribution and the no-resale rule of the odds provider (raw odds are never redistributed).
"""

import argparse
import json
import os
from pathlib import Path

import yaml

from src.cli_utils import configure_output

REPO = Path(__file__).resolve().parents[2]
EVIDENCE = {
    "S13": ("docs/adr/0091-phase3-sprint13-prediction-lineage.md", "docs/phase3/sprint13_report.md"),
    "S14": ("docs/adr/0092-phase3-sprint14-drift-quality-retrain.md", "docs/phase3/sprint14_drift_report.md"),
    "S15": ("docs/adr/0093-phase3-sprint15-stage-records.md", "docs/phase3/sprint15_stage_report.md"),
    "S16": ("docs/adr/0095-phase3-sprint16-uncertainty-selective.md", "docs/phase3/sprint16_report.md"),
    "S17": ("docs/adr/0096-phase3-sprint17-ablation-lab-promotion-gates.md", "docs/phase3/sprint17_report.md"),
}
PRODUCT_SOURCES = ("openfootball_domestic", "openfootball_cups", "the_odds_api", "espn_scoreboard", "football_data_org", "highlightly", "api_football")
def secret_env_names() -> list[str]:
    """The secret names the workflows reference (`secrets.NAME`), so the list never drifts from the deployment and no name literal sits in the code."""
    import re

    names = set()
    for f in (REPO / ".github" / "workflows").glob("*.yml"):
        names |= set(re.findall(r"secrets\.([A-Z][A-Z0-9_]+)", f.read_text(encoding="utf-8")))
    return sorted(names - {"GITHUB_TOKEN"})


DRILLS = ("restore", "migration_rollback", "model_rollback", "deploy_dry_run", "deploy_preview")


def _c(ok: bool | None, evidence) -> dict:
    return {"status": "NOT_CHECKED" if ok is None else ("PASS" if ok else "FAIL"), "evidence": evidence}


def evidence() -> dict:
    missing = [p for ps in EVIDENCE.values() for p in ps if not (REPO / p).is_file()]
    return _c(not missing, {"missing": missing, "sprints": list(EVIDENCE)})


def champion_pin() -> dict:
    from src.markets.model import MODEL_VERSION

    from . import canary

    cfg = canary.load()
    return _c(cfg["champion"] == MODEL_VERSION and (cfg["challenger"] is None) == (cfg["stage"] == "shadow"), {"pinned": cfg["champion"], "served": MODEL_VERSION, "challenger": cfg["challenger"], "stage": cfg["stage"]})


def drills(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    st = {d: (data.get(d) or {}).get("status", "NOT_RUN") for d in DRILLS}
    return _c(all(v == "PASS" for v in st.values()), st)


def security(bundle: Path | None) -> dict:
    from fastapi.testclient import TestClient

    from src.api.app import create_app

    out = {}
    c = TestClient(create_app(REPO, keys=[]), raise_server_exceptions=False)
    out["api_fails_closed"] = c.get("/v1/fixtures").status_code == 503
    c = TestClient(create_app(REPO, keys=["k"], per_minute=2, clock=lambda: 30.0), raise_server_exceptions=False)  # a fixed clock: all calls in one window
    codes = [c.get("/v1/fixtures", headers={"X-API-Key": "k"}).status_code for _ in range(3)]
    out["api_rate_limited"] = 429 in codes
    r = TestClient(create_app(REPO, keys=["k"]), base_url="https://testserver").post("/giris/misafir", headers={"X-Forwarded-Proto": "https"}, follow_redirects=False)
    cookie = r.headers.get("set-cookie", "").lower()
    out["session_cookie_flags"] = all(f in cookie for f in ("httponly", "samesite=lax", "secure"))
    wf = sorted((REPO / ".github" / "workflows").glob("*.yml"))
    no_perm = [p.name for p in wf if "\npermissions:" not in "\n" + p.read_text(encoding="utf-8")]
    out["workflow_permissions"] = not no_perm
    gl = (REPO / ".gitleaks.toml").read_text(encoding="utf-8").splitlines() if (REPO / ".gitleaks.toml").exists() else []
    active = [x.strip() for x in gl if x.strip() and not x.strip().startswith("#")]
    out["secret_scan_no_allowlist"] = not any(x in ("[allowlist]", "[[allowlists]]") for x in active)
    values = [v for k in secret_env_names() if (v := os.environ.get(k)) and len(v) >= 8]
    if bundle and bundle.is_dir() and values:
        leaked = [f.relative_to(bundle).as_posix() for f in bundle.rglob("*") if f.is_file() and f.stat().st_size < 20_000_000 and any(v.encode() in f.read_bytes() for v in values)]
        out["bundle_has_no_secret"] = not leaked
        out["bundle_secret_values_compared"] = len(values)
        out["bundle_files_with_secret"] = len(leaked)
    else:
        out["bundle_has_no_secret"] = None
    calls = REPO / "artifacts" / "ops" / "provider_calls.jsonl"
    if calls.is_file():
        text = calls.read_text(encoding="utf-8", errors="replace")
        out["call_log_redacted"] = "apikey=" not in text.lower() and not any(v in text for v in values)
    checks = {k: v for k, v in out.items() if isinstance(v, bool) or v is None}
    return _c(all(v for v in checks.values() if v is not None), {**out, "no_workflow_permissions": no_perm})


def licences() -> dict:
    srcs = {s["provider_id"]: s for s in yaml.safe_load((REPO / "configs" / "league_models" / "source_licenses.yaml").read_text(encoding="utf-8"))["sources"]}
    used = {p: srcs[p]["plan_status"] for p in PRODUCT_SOURCES if p in srcs}
    not_permitted = sorted(p for p, s in used.items() if s != "PERMITTED")
    return {"research_product": _c(not any(s == "BLOCKED" for s in used.values()), used), "commercial_launch": _c(not not_permitted, {"not_permitted": not_permitted})}


def gate(ci_status: str | None, bundle: Path | None, drills_path: Path) -> dict:
    lic = licences()
    checks = {"evidence": evidence(), "champion_pin": champion_pin(), "ci": _c(None if ci_status is None else ci_status == "success", ci_status), "drills": drills(drills_path),
              "security": security(bundle), "licences_research": lic["research_product"], "licences_commercial": lic["commercial_launch"]}  # fmt: skip
    core = ("evidence", "champion_pin", "ci", "drills", "security", "licences_research")
    if checks["evidence"]["status"] == "FAIL":
        research = "BLOCKED"
    elif all(checks[k]["status"] == "PASS" for k in core):
        research = "GO"
    elif any(checks[k]["status"] == "FAIL" for k in core):
        research = "NO_GO"
    else:
        research = "CONDITIONAL_GO"
    return {"checks": checks, "research_release": research, "commercial_launch": "GO" if checks["licences_commercial"]["status"] == "PASS" and research == "GO" else "NO_GO"}


def main(argv=None) -> int:
    configure_output()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ci-status", default=None)
    ap.add_argument("--bundle", default=None)
    ap.add_argument("--drills", default=str(REPO / "docs" / "phase3" / "s18_drills.json"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--require-clean-bundle", action="store_true", help="exit 1 when a secret value is found in the bundle (the deploy step stops)")
    a = ap.parse_args(argv)
    res = gate(a.ci_status, Path(a.bundle) if a.bundle else None, Path(a.drills))
    text = json.dumps(res, indent=1, sort_keys=True, default=str)
    if a.out:
        Path(a.out).write_text(text + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"research_release": res["research_release"], "commercial_launch": res["commercial_launch"], **{k: v["status"] for k, v in res["checks"].items()}}))
    if a.require_clean_bundle and res["checks"]["security"]["evidence"].get("bundle_has_no_secret") is False:
        print("::error::a secret value was found in the site bundle; the deploy is stopped")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
