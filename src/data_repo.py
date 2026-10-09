"""Where the cloud workflows keep raw/derived data (ADR 0045): a PRIVATE repository, not this public one.

The Odds API terms forbid redistributing its data as downloadable raw files, so the quotes must not sit on a
public branch. Local machines fetch with their own git credentials; the workflows use a repository-scoped
deploy key (secret DATA_REPO_DEPLOY_KEY). `DATA_REPO_URL` overrides the default (CI sets an ssh alias).
"""

import os

DEFAULT_URL = "https://github.com/Weli-byte/Ma-analizi2.git"


def data_repo_url() -> str:
    return os.environ.get("DATA_REPO_URL") or DEFAULT_URL
