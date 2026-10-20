"""The relock job's decision of which parked ci runs to approve. The payload shape is the real GitHub API shape (fields checked on
a real run of this repository on 2026-10-20); the fork entries are constructed, since a real fork PR needs a second account."""

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "relock_pick_runs", Path(__file__).resolve().parents[1] / "scripts" / "relock_pick_runs.py"
)
pick = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pick)

REPO, SHA = "Weli-byte/Ma-analizi", "b7f1a1d5cd7a16f25f4166d5c927946c5119b352"


def run(id_, **over):
    base = {
        "id": id_,
        "path": ".github/workflows/ci.yml",
        "event": "pull_request",
        "conclusion": "action_required",
        "head_sha": SHA,
        "head_branch": "chore/relock",
        "head_repository": {"full_name": REPO},
    }
    return {**base, **over}


def test_the_parked_run_of_our_own_commit_in_this_repository_is_approved():
    assert pick.approvable({"workflow_runs": [run(1)]}, SHA, REPO) == [1]


def test_a_fork_branch_named_chore_relock_is_never_approved():
    fork_same_sha = run(2, head_repository={"full_name": "attacker/Ma-analizi"})
    fork_other_sha = run(3, head_sha="0" * 40, head_repository={"full_name": "attacker/Ma-analizi"})
    deleted_fork = run(4, head_repository=None)
    payload = {"workflow_runs": [fork_same_sha, fork_other_sha, deleted_fork, run(1)]}
    assert pick.approvable(payload, SHA, REPO) == [1]


def test_other_commits_workflows_events_and_states_are_left_alone():
    payload = {
        "workflow_runs": [
            run(5, head_sha="1" * 40),  # an older push of the rolling branch
            run(6, path=".github/workflows/markets-cloud.yml"),
            run(7, event="workflow_dispatch"),
            run(8, conclusion="success"),
            run(9, conclusion=None),
        ]
    }
    assert pick.approvable(payload, SHA, REPO) == []
    assert pick.approvable({}, SHA, REPO) == []
