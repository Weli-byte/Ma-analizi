"""`python scripts/make_space_bundle.py [--out dist/space]`: what the Hugging Face Space needs.

Image recipe, start script, README with the Space front matter, locked slim
requirements, `src/` and `configs/`. No secret, artifact or local data is included:
the Space gets forecasts from the private data repo at start-up."""

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = shutil.ignore_patterns("__pycache__", "*.pyc")


def build(out: Path) -> list[str]:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for name in ("Dockerfile", "space_start.sh", "README.md"):
        shutil.copy(ROOT / "deploy" / "space" / name, out / name)
    shutil.copy(ROOT / "requirements-api.lock", out / "requirements-api.lock")
    shutil.copytree(ROOT / "src", out / "src", ignore=SKIP)
    shutil.copytree(ROOT / "configs", out / "configs", ignore=SKIP)
    return sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "dist" / "space"))
    files = build(Path(ap.parse_args(argv).out))
    print(f"bundle written: {len(files)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
