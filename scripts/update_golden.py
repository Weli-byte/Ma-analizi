"""Regenerate the golden expected artifacts (tests/fixtures/golden/expected/).

    python scripts/update_golden.py

Only run this when an INTENTIONAL change alters normalized data, features, predictions or metrics,
and write an ADR explaining why (docs/adr/). Unexpected diffs are regressions, not reasons to update.
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from conftest import make_project  # noqa: E402
from golden_util import GOLDEN_DIR, normalized_fixtures_csv, run_chain  # noqa: E402


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="golden-"))
    try:
        proj = make_project(tmp / "proj")
        summary = run_chain(proj)
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        (GOLDEN_DIR / "golden.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
        )
        (GOLDEN_DIR / "normalized_fixtures.csv").write_text(
            normalized_fixtures_csv(proj), encoding="utf-8", newline="\n"
        )
        print(f"golden artifacts written to {GOLDEN_DIR}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
