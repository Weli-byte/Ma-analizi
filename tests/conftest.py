import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
GOLDEN_ROOT = Path(__file__).parent / "fixtures" / "golden" / "root"
KICKOFF = datetime(2024, 1, 20, 15, 0, tzinfo=UTC)
DV = "dv-0123456789ab"  # a syntactically valid content-derived data version


def git(path: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)


def make_project(dest: Path, commit: bool = True) -> Path:
    """Copy of the golden project as a fresh git repo (raw data committed, outputs ignored)."""
    shutil.copytree(GOLDEN_ROOT, dest)
    shutil.copy(REPO / "requirements.lock", dest / "requirements.lock")
    (dest / ".gitignore").write_text("data/processed/\ndata/features/\nartifacts/\n")
    git(dest, "init", "-q")
    git(dest, "config", "user.email", "test@example.com")
    git(dest, "config", "user.name", "test")
    if commit:
        git(dest, "add", "-A")
        git(dest, "commit", "-q", "-m", "golden fixture")
    return dest


def build_all(root: Path, mode: str = "strict"):
    """Run pipeline -> features on a project; returns the dataset ref."""
    from datetime import date

    from src.data.dataset import resolve_dataset
    from src.data.pipeline import run_pipeline
    from src.features.builder import build_features

    run_pipeline(root, mode, as_of=date(2026, 9, 25))
    build_features(root, mode, audit_samples=40)
    return resolve_dataset(root / "data" / "processed")


@pytest.fixture
def project(tmp_path) -> Path:
    return make_project(tmp_path / "proj")


@pytest.fixture
def built(project) -> Path:
    build_all(project)
    return project


@pytest.fixture
def kickoff():
    return KICKOFF


@pytest.fixture
def fixture_kwargs():
    return dict(
        fixture_id="fx1",
        league_id="EPL",
        season="2023-24",
        kickoff_utc=KICKOFF,
        home_id="t1",
        away_id="t2",
    )


@pytest.fixture
def finished_kwargs(fixture_kwargs):
    return dict(
        fixture_kwargs,
        status="finished",
        home_goals=2,
        away_goals=1,
        result_available_at_utc=KICKOFF + timedelta(hours=3),
        result_available_at_source="inferred",
    )


@pytest.fixture
def pred_kwargs():
    cutoff = KICKOFF - timedelta(hours=24)
    return dict(
        fixture_id="fx1",
        model_id="always_home",
        model_version="1.0.0",
        feature_version="fv2",
        data_version=DV,
        kickoff_utc=KICKOFF,
        information_cutoff=cutoff,
        generated_at=cutoff,
        p_home=0.5,
        p_draw=0.3,
        p_away=0.2,
    )
