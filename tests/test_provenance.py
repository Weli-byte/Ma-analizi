import subprocess
from pathlib import Path

import pytest

from src.provenance import ProvenanceError, collect, git_info, lock_hash
from src.runmode import RunMode, policy


def make_repo(path: Path, commit: bool = True) -> None:
    run = lambda *a: subprocess.run(["git", *a], cwd=path, check=True, capture_output=True)  # noqa: E731
    run("init", "-q")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    (path / "requirements.lock").write_text("pkg==1\n")
    (path / "a.txt").write_text("x")
    if commit:
        run("add", "-A")
        run("commit", "-q", "-m", "init")


def test_clean_repo_real_sha(tmp_path):
    make_repo(tmp_path)
    g = git_info(tmp_path)
    assert len(g.sha) == 40 and not g.dirty and g.dirty_files == ()
    p = collect(RunMode.STRICT, tmp_path)
    assert p.git_sha == g.sha and p.dependency_lock_hash == lock_hash(tmp_path)


def test_dirty_repo_recorded_in_research_and_rejected_in_strict(tmp_path):
    make_repo(tmp_path)
    (tmp_path / "a.txt").write_text("changed")
    (tmp_path / "new.txt").write_text("n")
    p = collect(RunMode.RESEARCH, tmp_path)
    assert p.git_dirty and p.dirty_files == ("a.txt", "new.txt")
    with pytest.raises(ProvenanceError, match="clean working tree"):
        collect(RunMode.STRICT, tmp_path)
    with pytest.raises(ProvenanceError):
        collect(RunMode.FINAL, tmp_path)


def test_unknown_sha_only_allowed_in_development(tmp_path):
    make_repo(tmp_path, commit=False)
    assert git_info(tmp_path).sha == "unknown"
    assert collect(RunMode.DEVELOPMENT, tmp_path).git_sha == "unknown"
    with pytest.raises(ProvenanceError, match="real git commit SHA"):
        collect(RunMode.RESEARCH, tmp_path)


def test_missing_lock_fails_research(tmp_path):
    make_repo(tmp_path)
    (tmp_path / "requirements.lock").unlink()
    subprocess.run(["git", "commit", "-qam", "rm"], cwd=tmp_path, check=True, capture_output=True)
    with pytest.raises(ProvenanceError, match="requirements.lock"):
        collect(RunMode.RESEARCH, tmp_path)


def test_mode_policies_are_monotonic():
    d, r, s, f = (policy(m) for m in RunMode)
    assert not d.require_real_git_sha and r.require_real_git_sha
    assert not r.require_clean_git and s.require_clean_git and f.require_clean_git
    assert not s.allow_final_access and f.allow_final_access
    assert s.fail_on_quality_warnings and not r.fail_on_quality_warnings
    assert d.tolerate_unexpected_missing_features and not s.tolerate_unexpected_missing_features
