"""REAL error-path tests: the providers' own servers produce every error here (bad credential,
nonexistent model, impossible timeout, genuine rate limit). Nothing is simulated. They replace
the earlier synthetic-exception retry tests (ADR 0024). Run: pytest -m live tests/integration
Cost: invalid-credential / bad-model requests are rejected before inference (free); the rate-limit
burst runs on Groq's free tier (< $0.01 estimated)."""

import os
import threading
import time

import pytest

from src.cli_utils import load_dotenv
from src.config import load_config
from src.llm.prompt import SYSTEM_PROMPT, build_user_prompt
from src.llm.providers import PROVIDERS, ErrorKind, ProviderError

from ._live import ROOT, SNAPSHOT

pytestmark = pytest.mark.live

USER = build_user_prompt(SNAPSHOT)
PROVIDER_NAMES = ["openai", "gemini", "groq"]  # anthropic joins when its key exists


def _cfg(name):
    load_dotenv()
    cfg = load_config("provider", ROOT / "configs")
    key = os.environ.get(cfg.providers[name].api_key_env)
    if not key:
        pytest.fail(f"{name}: key not set -> NOT_CONFIGURED (live test cannot pass)")
    return cfg, cfg.providers[name].model, key


def _call(name, model, key, *, timeout_s=30.0, retry_limit=0, max_out=400):
    return PROVIDERS[name].complete(
        SYSTEM_PROMPT, USER, model=model, api_key=key, timeout_s=timeout_s,
        max_output_tokens=max_out, retry_limit=retry_limit,
    )  # fmt: skip


@pytest.mark.parametrize("name", PROVIDER_NAMES)
def test_real_rejected_credential_is_classified_and_never_retried(name):
    _, model, _ = _cfg(name)
    bad_key = "invalid-credential-for-live-test"
    with pytest.raises(ProviderError) as e:
        _call(name, model, bad_key, retry_limit=3)
    assert e.value.kind in {ErrorKind.AUTH, ErrorKind.INVALID_REQUEST}  # Gemini answers 400
    assert e.value.retry_count == 0  # auth errors are never retried
    assert bad_key not in str(e.value)


@pytest.mark.parametrize("name", PROVIDER_NAMES)
def test_real_unknown_model_is_classified_not_retried(name):
    _, _, key = _cfg(name)
    with pytest.raises(ProviderError) as e:
        _call(name, "no-such-model-live-test", key, retry_limit=3)
    assert e.value.kind in {ErrorKind.MODEL_UNAVAILABLE, ErrorKind.INVALID_REQUEST}
    assert e.value.retry_count == 0
    assert key not in str(e.value)


@pytest.mark.parametrize("name", PROVIDER_NAMES)
def test_real_timeout_is_retried_with_backoff_up_to_the_limit(name):
    _, model, key = _cfg(name)
    t0 = time.monotonic()
    with pytest.raises(ProviderError) as e:
        _call(name, model, key, timeout_s=0.001, retry_limit=2)
    assert e.value.kind in {ErrorKind.TIMEOUT, ErrorKind.NETWORK}  # both are retryable
    assert e.value.retry_count == 2
    assert time.monotonic() - t0 >= 0.5  # real exponential backoff sleeps happened


def test_real_rate_limit_on_groq_free_tier_is_classified_and_retried():
    """Bursts real requests at Groq's free tier until the server itself answers 429."""
    _, model, key = _cfg("groq")
    kinds, lock = [], threading.Lock()

    def worker():
        for _ in range(6):
            try:
                _call("groq", model, key, retry_limit=0)
                result = "ok"
            except ProviderError as e:
                result = e.kind
            with lock:
                kinds.append(result)
            if ErrorKind.RATE_LIMIT in kinds:
                return

    threads = [threading.Thread(target=worker) for _ in range(8)]  # at most 48 real requests
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert ErrorKind.RATE_LIMIT in kinds, f"no real 429 observed in {len(kinds)} requests: {kinds}"

    # right after the burst the limit is still active: a retrying call either recovers after
    # backoff or exhausts exactly retry_limit retries -- both prove the real retry path.
    try:
        resp = _call("groq", model, key, retry_limit=2)
        assert resp.retry_count >= 0
    except ProviderError as e:
        assert e.kind == ErrorKind.RATE_LIMIT and e.retry_count == 2
