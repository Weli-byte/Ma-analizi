import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRET = re.compile(r"(sk-[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{30,}|sk-ant-[A-Za-z0-9_-]{20,})")
SOURCES = list((ROOT / "src").rglob("*.py")) + list((ROOT / "scripts").rglob("*.py"))


def test_no_hardcoded_secrets():
    files = list((ROOT / "src").rglob("*.py")) + list((ROOT / "configs").glob("*.yaml"))
    for p in files:
        assert not SECRET.search(p.read_text(encoding="utf-8")), p


def test_required_layout_and_docs():
    dirs = [
        "apps/api", "apps/dashboard", "apps/worker", "src/data", "src/features", "src/models",
        "src/evaluation", "src/ensemble", "src/value", "tests", "configs", "reports",
        "tests/fixtures/golden",
    ]  # fmt: skip
    for d in dirs:
        assert (ROOT / d).is_dir(), d
    for f in ["docs/benchmark_protocol.md", "docs/leakage_policy.md", "docs/versioning.md",
              "docs/data_sources/licensing.md", "docs/data_sources/xg.md"]:  # fmt: skip
        assert (ROOT / f).is_file(), f


def test_all_required_adrs_exist_with_required_sections():
    names = ["0002-data-provenance", "0003-data-versioning", "0004-final-test-isolation",
             "0005-timezone-policy", "0006-result-availability-policy", "0007-odds-timestamp-policy",
             "0008-rest-days-policy", "0009-missing-feature-policy", "0010-team-identity-resolution",
             "0011-dependency-reproducibility", "0012-evaluation-split-policy"]  # fmt: skip
    for n in names:
        text = (ROOT / "docs" / "adr" / f"{n}.md").read_text(encoding="utf-8")
        for section in ("Context", "Decision", "Alternatives", "Consequences", "Status"):
            assert f"## {section}" in text, f"{n} lacks '{section}'"


def test_no_assert_statements_in_production_code():
    offenders = []
    for path in (ROOT / "src").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Assert):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not offenders, offenders


def test_tls_verification_is_never_disabled_and_no_blanket_process_kills():
    banned = ["verify=False", "CERT_NONE", "check_hostname = False", "--insecure", "_create_unverified_context",
              "taskkill", "killall", "pkill"]  # fmt: skip
    for path in SOURCES:
        text = path.read_text(encoding="utf-8")
        for token in banned:
            assert token not in text, f"{path.relative_to(ROOT)} contains {token!r}"


def test_no_skip_or_true_hacks_in_tests():
    this = Path(__file__).name
    for path in Path(__file__).parent.glob("test_*.py"):
        if path.name == this:
            continue
        text = path.read_text(encoding="utf-8")
        assert " or True" not in text, path.name
        assert "except Exception" not in text or path.name == "test_repo_hygiene.py", path.name
