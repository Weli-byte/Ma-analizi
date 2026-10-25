"""The Vercel rollback drill (ADR 0080): static checks of the script and the workflow. The real drill is the manual workflow."""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SH = (ROOT / "scripts" / "vercel_rollback_drill.sh").read_text(encoding="utf-8")
WF = (ROOT / ".github" / "workflows" / "vercel-rollback-drill.yml").read_text(encoding="utf-8")


def test_the_drill_defaults_to_changing_nothing_and_always_restores_the_original_deployment():
    assert 'mode="${1:-dry-run}"' in SH and 'if [ "$mode" != "execute" ]; then echo "dry-run: nothing changed' in SH
    assert "trap restore EXIT" in SH and "vercel@latest promote" in SH and "vercel@latest rollback" in SH
    assert 'production_id' in SH and '[ "$now" = "$previous" ]' in SH and '[ "$back" = "$current" ]' in SH         # every step verifies which deployment is production
    assert "DRILL OK" in SH and SH.count("/v1/health") == 1


def test_the_drill_never_prints_the_token_and_the_preview_is_never_production():
    assert "echo" not in "".join(line for line in SH.splitlines() if "VERCEL_TOKEN" in line and line.lstrip().startswith("echo"))
    assert "--prod" not in SH                                                  # a preview deploy has no --prod flag anywhere in this script
    assert "READY preview" in SH and "make_vercel_bundle.py --out dist/preview" in SH


def test_the_workflow_is_manual_with_three_modes_and_uses_the_existing_secrets_only():
    assert "workflow_dispatch" in WF and "schedule" not in WF and "push:" not in WF and "pull_request" not in WF
    assert "options: [dry-run, preview, execute]" in WF and "default: dry-run" in WF
    for name in ("VERCEL_TOKEN", "VERCEL_ORG_ID", "VERCEL_PROJECT_ID"):
        assert "${{ secrets." + name + " }}" in WF
    assert "permissions:\n  contents: read" in WF


@pytest.mark.skipif(not Path("/bin/bash").exists() and not Path("C:/Program Files/Git/bin/bash.exe").exists(), reason="no bash")
def test_the_script_parses():
    bash = "/bin/bash" if Path("/bin/bash").exists() else "C:/Program Files/Git/bin/bash.exe"
    assert subprocess.run([bash, "-n", str(ROOT / "scripts" / "vercel_rollback_drill.sh")], capture_output=True).returncode == 0
