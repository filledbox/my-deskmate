


import httpx
import pytest
from openai import RateLimitError

from backend.llm import (
    OPENROUTER_DEFAULT_MODEL,
    ROLE_CHAPTER,
    ROLE_QA,
    LLMConfigError,
    _role_pair,
    api_key_for,
    base_url_for,
    is_quota_exhausted,
    model_for,
)

ROLE_ENV = {
    ROLE_QA: ("LLM_QA_BASE_URL", "LLM_QA_API_KEY", "LLM_QA_MODEL"),
    ROLE_CHAPTER: ("LLM_CHAPTER_BASE_URL", "LLM_CHAPTER_API_KEY", "LLM_CHAPTER_MODEL"),
}
SHARED = ("LLM_BASE_URL", "LLM_API_KEY")


@pytest.fixture
def clean_env(monkeypatch):
    for names in ROLE_ENV.values():
        for n in names:
            monkeypatch.delenv(n, raising=False)
    for n in SHARED:
        monkeypatch.delenv(n, raising=False)
    return monkeypatch


def rate_limit(body=None, message="rate limited"):
    req = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
    resp = httpx.Response(429, request=req)
    return RateLimitError(message, response=resp, body=body)


@pytest.mark.parametrize("role", [ROLE_QA, ROLE_CHAPTER])
def test_role_pair_half_config_raises(clean_env, role):
    base, key, _ = ROLE_ENV[role]
    clean_env.setenv(base, "https://example.invalid/v1")
    with pytest.raises(LLMConfigError) as e:
        _role_pair(role)
    assert key in str(e.value)
    assert "只配了一半" in str(e.value)


@pytest.mark.parametrize("role", [ROLE_QA, ROLE_CHAPTER])
def test_role_pair_key_without_base_url_raises(clean_env, role):
    base, key, _ = ROLE_ENV[role]
    clean_env.setenv(key, "sk-x")
    with pytest.raises(LLMConfigError):
        _role_pair(role)


def test_role_pair_returns_none_when_nothing_set(clean_env):
    assert _role_pair(ROLE_QA) == (None, None)


def test_role_pair_returns_both_when_fully_set(clean_env):
    clean_env.setenv("LLM_QA_BASE_URL", "https://a.invalid/v1")
    clean_env.setenv("LLM_QA_API_KEY", "sk-a")
    assert _role_pair(ROLE_QA) == ("https://a.invalid/v1", "sk-a")


def test_blank_values_count_as_unset(clean_env):
    clean_env.setenv("LLM_QA_BASE_URL", "   ")
    clean_env.setenv("LLM_QA_API_KEY", "")
    assert _role_pair(ROLE_QA) == (None, None)


def test_qa_inherits_shared_key_when_role_unset(clean_env):
    clean_env.setenv("LLM_API_KEY", "sk-shared")
    assert api_key_for(ROLE_QA) == "sk-shared"


def test_role_key_wins_over_shared(clean_env):
    clean_env.setenv("LLM_API_KEY", "sk-shared")
    clean_env.setenv("LLM_QA_BASE_URL", "https://qa.invalid/v1")
    clean_env.setenv("LLM_QA_API_KEY", "sk-qa")
    assert api_key_for(ROLE_QA) == "sk-qa"
    assert base_url_for(ROLE_QA) == "https://qa.invalid/v1"


def test_no_key_at_all_raises(clean_env):
    with pytest.raises(LLMConfigError):
        api_key_for(ROLE_QA)


def test_model_override_and_default(clean_env):
    default = model_for(ROLE_QA)
    assert default == OPENROUTER_DEFAULT_MODEL == "google/gemini-3.1-flash-lite"
    clean_env.setenv("LLM_QA_MODEL", "some-model")
    assert model_for(ROLE_QA) == "some-model"


def test_unknown_role_raises(clean_env):
    with pytest.raises(LLMConfigError):
        model_for("nope")


def test_quota_markers_detected():
    assert is_quota_exhausted(rate_limit({"error": {"message": "free-models-per-day"}}))
    assert is_quota_exhausted(rate_limit(message="Quota exceeded for this billing period"))
    assert is_quota_exhausted(rate_limit(message="insufficient credits"))


def test_plain_rate_limit_is_not_quota():

    assert not is_quota_exhausted(rate_limit(message="requests per minute exceeded"))


def test_quota_marker_in_body_string_is_detected():
    assert is_quota_exhausted(rate_limit(body={"message": "you have hit your daily limit"}))
