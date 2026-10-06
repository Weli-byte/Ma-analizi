"""Prompt template + versioning (S8, ADR 0024). Every prompt is identified by `PROMPT_ID`,
`PROMPT_VERSION`, the sha256 of the system prompt and of the exact user prompt, and
`SCHEMA_VERSION`. Any change to the instruction wording that could change model behavior REQUIRES
a `PROMPT_VERSION` bump and an ADR -- a production benchmark prompt is never edited silently
(`tests/test_llm.py::test_system_prompt_hash_is_pinned` fails on an unversioned edit).
"""

import hashlib
import json
from dataclasses import dataclass

from src.versioning import canonical_json

from .contract import SCHEMA_VERSION

PROMPT_ID = "football-1x2-forecast"
PROMPT_VERSION = "llm-prompt-v2"

SYSTEM_PROMPT = """You are a football (soccer) match forecaster. Estimate the probabilities of a \
home win, a draw and an away win for ONE match.

You are given a structured snapshot of information that was available strictly BEFORE the \
snapshot's information_cutoff timestamp. It contains nothing about the match's result, in-game \
events, or anything at or after kickoff. Use only that snapshot; do not assume access to any other \
information about this match.

Return a JSON object matching the provided schema. home_probability, draw_probability and \
away_probability are each between 0 and 1 and must sum to 1. predicted_home_goals, \
predicted_away_goals, confidence (0 to 1) and analysis_summary (one short sentence) are optional: \
use null when you do not want to give them."""


@dataclass(frozen=True)
class PromptMeta:
    prompt_id: str
    prompt_version: str
    system_prompt_hash: str
    user_prompt_hash: str
    schema_version: str


def build_user_prompt(snapshot: dict) -> str:
    return f"SNAPSHOT:\n{json.dumps(snapshot, sort_keys=True, indent=2)}"


def system_prompt_hash() -> str:
    return hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()


def prompt_meta(user_prompt: str) -> PromptMeta:
    return PromptMeta(
        PROMPT_ID,
        PROMPT_VERSION,
        system_prompt_hash(),
        hashlib.sha256(user_prompt.encode()).hexdigest(),
        SCHEMA_VERSION,
    )


def snapshot_hash(snapshot: dict) -> str:
    return hashlib.sha256(canonical_json(snapshot).encode()).hexdigest()
