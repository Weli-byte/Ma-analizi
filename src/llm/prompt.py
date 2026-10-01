"""Prompt template + version (S8). Bump `PROMPT_VERSION` whenever the template's INSTRUCTIONS
change (wording that could change model behavior) -- not for whitespace-only edits. Stored on
every `LLMCallRecord` so a later metric shift can be attributed to a prompt change, not silently
blamed on the model.
"""

import hashlib
import json

from src.versioning import canonical_json

PROMPT_VERSION = "llm-prompt-v1"

_INSTRUCTIONS = """You are forecasting the 1X2 (home win / draw / away win) outcome of a football \
match. You are given a structured snapshot of information that was available strictly BEFORE \
the match's information_cutoff timestamp -- nothing about the match's actual result, in-game \
events, or anything occurring at or after kickoff_utc. Do not assume access to any information \
outside this snapshot.

Respond with STRICT JSON only, no prose outside the JSON object, matching exactly this schema:
{"home_probability": <float 0-1>, "draw_probability": <float 0-1>, "away_probability": <float 0-1>, \
"confidence": <float 0-1>, "short_reasoning": <string, one sentence>}

The three probabilities must sum to 1.0 (within normal floating-point tolerance). Output nothing \
else: no markdown code fences, no explanation before or after the JSON object."""


def build_prompt(snapshot: dict) -> str:
    return f"{_INSTRUCTIONS}\n\nSNAPSHOT:\n{json.dumps(snapshot, sort_keys=True, indent=2)}"


def snapshot_hash(snapshot: dict) -> str:
    return hashlib.sha256(canonical_json(snapshot).encode()).hexdigest()


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode()).hexdigest()
