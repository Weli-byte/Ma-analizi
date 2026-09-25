import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRET = re.compile(r"(sk-[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{30,}|sk-ant-[A-Za-z0-9_-]{20,})")


def test_no_hardcoded_secrets():
    files = list(ROOT.glob("src/**/*.py")) + list(ROOT.glob("configs/*.yaml"))
    for p in files:
        assert not SECRET.search(p.read_text(encoding="utf-8")), p


def test_required_layout():
    dirs = [
        "apps/api", "apps/dashboard", "apps/worker", "src/data", "src/features",
        "src/models", "src/evaluation", "src/ensemble", "src/value", "tests",
        "configs", "reports",
    ]  # fmt: skip
    for d in dirs:
        assert (ROOT / d).is_dir(), d
    assert (ROOT / "docs/benchmark_protocol.md").is_file()
