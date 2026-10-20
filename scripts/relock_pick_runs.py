"""Which parked CI runs may the relock job approve? (called by .github/workflows/relock.yml; unit-tested)

Input (stdin): the JSON of `GET /repos/{repo}/actions/runs?branch=chore/relock&event=pull_request`.
Output: ids of runs that are ALL of: the ci workflow, parked as `action_required`, built
from exactly `sha` (the commit the job just pushed), and whose head repository is `repo`
itself. A fork PR from a branch that happens to be called chore/relock has another head
repository and another commit, so it is never approved (it would bypass the approval gate).

Usage: python scripts/relock_pick_runs.py <sha> <owner/repo> < runs.json"""

import json
import sys

WORKFLOW_PATH = ".github/workflows/ci.yml"


def approvable(payload: dict, sha: str, repo: str) -> list[int]:
    ids = []
    for run in payload.get("workflow_runs", []):
        head_repo = (run.get("head_repository") or {}).get("full_name")
        if (
            run.get("path") == WORKFLOW_PATH
            and run.get("event") == "pull_request"
            and run.get("conclusion") == "action_required"
            and run.get("head_sha") == sha
            and head_repo == repo
        ):
            ids.append(int(run["id"]))
    return ids


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    print("\n".join(str(i) for i in approvable(json.load(sys.stdin), sys.argv[1], sys.argv[2])))
