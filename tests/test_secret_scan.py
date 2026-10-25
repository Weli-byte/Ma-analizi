"""The secret-scan workflow (ADR 0078): pinned and checksum-verified scanner, a canary that proves the scan works, no way to let a finding pass."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WF = (ROOT / ".github" / "workflows" / "secret-scan.yml").read_text(encoding="utf-8")


def test_the_scanner_is_a_pinned_release_with_a_verified_checksum():
    assert re.search(r'GITLEAKS_VERSION: "8\.\d+\.\d+"', WF) and re.search(r'GITLEAKS_SHA256: "[0-9a-f]{64}"', WF)
    assert "sha256sum -c -" in WF and "releases/download/v${GITLEAKS_VERSION}/" in WF and "set -euo pipefail" in WF


def test_a_finding_fails_the_job_and_nothing_can_make_it_pass():
    assert "--exit-code 1" in WF and "continue-on-error" not in WF and "|| true" not in WF and "exit-code 0" not in WF
    assert "--redact" in WF                                                  # a leaked value is never printed to the log
    assert "fetch-depth: 0" in WF                                            # the whole history, not the last commit
    assert "pull_request" in WF and "branches: [main]" in WF and "schedule" in WF


def test_a_canary_proves_the_scanner_detects_a_secret_and_the_workflow_holds_no_secret_itself():
    assert "canary" in WF and "did not flag the canary" in WF and "secrets.token_hex(16)" in WF           # random at run time: the file contains nothing that looks like a key
    assert "permissions:\n  contents: read" in WF and "secrets." not in WF                                  # least privilege, and no repository secret is needed


def test_the_gitleaks_config_extends_the_default_rules_and_has_no_blanket_allowlist():
    cfg = (ROOT / ".gitleaks.toml").read_text(encoding="utf-8")
    assert "useDefault = true" in cfg
    assert not re.search(r"^\s*\[\[allowlists\]\]", cfg, re.M) and not re.search(r"^\s*paths\s*=", cfg, re.M)       # no active allowlist: every future entry is a reviewed commit
