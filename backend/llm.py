


import asyncio
import json
import os
from dataclasses import dataclass

from openai import AsyncOpenAI, RateLimitError

ROLE_QA = "qa"
ROLE_CHAPTER = "chapter"

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_DEFAULT_MODEL = "google/gemini-3.1-flash-lite"

QA_MODEL_DEFAULT = OPENROUTER_DEFAULT_MODEL
CHAPTER_MODEL_DEFAULT = OPENROUTER_DEFAULT_MODEL


DEFAULT_MAX_ATTEMPTS = 4
DEFAULT_BACKOFF = 5.0


_QUOTA_MARKERS = (
    "free-models-per-day",
    "per day",
    "daily",
    "quota exceeded",
    "insufficient credits",
)


class LLMConfigError(RuntimeError):
    pass


class LLMQuotaExhaustedError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMConfig:
    role: str
    model: str
    base_url: str
    api_key: str

    def client(self) -> AsyncOpenAI:
        return AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)


@dataclass(frozen=True)
class _RoleSpec:
    default_base_url: str
    default_model: str
    inherit_shared: bool = True


_ROLES: dict[str, _RoleSpec] = {
    ROLE_QA: _RoleSpec(OPENROUTER_BASE_URL, QA_MODEL_DEFAULT),
    ROLE_CHAPTER: _RoleSpec(OPENROUTER_BASE_URL, CHAPTER_MODEL_DEFAULT),
}


def _env(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


def _spec(role: str) -> _RoleSpec:
    try:
        return _ROLES[role]
    except KeyError:
        raise LLMConfigError(
            f"未知 LLM 角色 {role!r}，可用角色: {sorted(_ROLES)}"
        ) from None


def _role_pair(role: str) -> tuple[str | None, str | None]:
    upper = role.upper()
    base_url = _env(f"LLM_{upper}_BASE_URL")
    api_key = _env(f"LLM_{upper}_API_KEY")
    if bool(base_url) != bool(api_key):
        missing = f"LLM_{upper}_API_KEY" if base_url else f"LLM_{upper}_BASE_URL"
        raise LLMConfigError(
            f"{role} 角色只配了一半：LLM_{upper}_BASE_URL 与 LLM_{upper}_API_KEY"
            f" 必须同时设置（当前缺 {missing}）。半配会让该角色继承共享配置，"
            "可能把请求发到另一个供应商。"
        )
    return base_url, api_key


def model_for(role: str) -> str:
    spec = _spec(role)
    return _env(f"LLM_{role.upper()}_MODEL") or spec.default_model


def base_url_for(role: str) -> str:
    spec = _spec(role)
    base_url, _ = _role_pair(role)
    if base_url:
        return base_url
    if spec.inherit_shared:
        shared = _env("LLM_BASE_URL")
        if shared:
            return shared
    return spec.default_base_url


def api_key_for(role: str) -> str:
    spec = _spec(role)
    _, api_key = _role_pair(role)
    if api_key:
        return api_key
    if not spec.inherit_shared:
        upper = role.upper()
        raise LLMConfigError(
            f"{role} 角色必须显式配置：请设置 LLM_{upper}_BASE_URL 与 "
            f"LLM_{upper}_API_KEY（该角色故意不继承共享配置，避免把共享 key "
            "发到另一个供应商）。"
        )
    shared = _env("LLM_API_KEY")
    if shared:
        return shared
    raise LLMConfigError(
        f"{role} 角色没有可用的 API key：请设置 LLM_API_KEY（共享）或 "
        f"LLM_{role.upper()}_API_KEY。"
    )


def resolve(role: str) -> LLMConfig:
    return LLMConfig(
        role=role,
        model=model_for(role),
        base_url=base_url_for(role),
        api_key=api_key_for(role),
    )


def make_client(role: str) -> AsyncOpenAI:

    return resolve(role).client()


def _rate_limit_text(exc: RateLimitError) -> str:
    body = getattr(exc, "body", None)
    if isinstance(body, (dict, list)):
        body = json.dumps(body, ensure_ascii=False)
    return f"{body or ''} {exc}".lower()


def is_quota_exhausted(exc: RateLimitError) -> bool:

    text = _rate_limit_text(exc)
    return any(marker in text for marker in _QUOTA_MARKERS)


async def chat_with_retry(
    client: AsyncOpenAI,
    *,
    backoff: float | None = None,
    max_attempts: int | None = None,
    **kwargs,
):


    attempts = max_attempts or int(
        os.getenv("LLM_RETRY_MAX_ATTEMPTS", str(DEFAULT_MAX_ATTEMPTS))
    )
    first_backoff = (
        backoff
        if backoff is not None
        else float(os.getenv("LLM_RETRY_BACKOFF", str(DEFAULT_BACKOFF)))
    )
    for attempt in range(attempts):
        try:
            return await client.chat.completions.create(**kwargs)
        except RateLimitError as exc:
            if is_quota_exhausted(exc):
                raise LLMQuotaExhaustedError(
                    f"LLM 额度已耗尽（非每分钟限流，重试无意义）：{exc}"
                ) from exc
            if attempt == attempts - 1:
                raise
            await asyncio.sleep(first_backoff * (2 ** attempt))
    raise RuntimeError("unreachable")
