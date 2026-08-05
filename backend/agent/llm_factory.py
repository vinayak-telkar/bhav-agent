"""
Builds the (primary, fallback) chat-model pair for a given agent role
("digest" or "chat"), reading provider/model names from .env — never
hardcoded, since free-tier catalogs churn (tech spec §1/§11). Returns raw
BaseChatModel instances; callers compose `.with_structured_output(...)` and
`.with_fallbacks([...])` themselves, since the right composition order
differs between digest's structured-output synthesis and chat's tool-bound
ReAct loop (not built this iteration — see specs/12).
"""
import os

from langchain_cerebras import ChatCerebras
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_groq import ChatGroq


def build_chat_models(role: str, max_tokens: int = 16000) -> tuple[BaseChatModel, BaseChatModel | None]:
    """role: 'DIGEST' or 'CHAT' — matches the .env prefix (tech spec §1)."""
    prefix = role.upper()
    primary = _instantiate(
        provider=_require_env(f"{prefix}_PROVIDER"),
        model=_require_env(f"{prefix}_MODEL"),
        max_tokens=max_tokens,
    )

    fallback_provider = os.environ.get(f"{prefix}_FALLBACK_PROVIDER")
    fallback_model = os.environ.get(f"{prefix}_FALLBACK_MODEL")
    fallback = (
        _instantiate(provider=fallback_provider, model=fallback_model, max_tokens=max_tokens)
        if fallback_provider and fallback_model
        else None
    )
    return primary, fallback


def _require_env(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        raise RuntimeError(f"missing required .env setting: {key}")
    return value


def _instantiate(provider: str, model: str, max_tokens: int) -> BaseChatModel:
    if provider == "groq":
        return ChatGroq(model=model, max_tokens=max_tokens, temperature=0.2)
    if provider == "cerebras":
        return ChatCerebras(model=model, max_tokens=max_tokens, temperature=0.2)
    raise ValueError(f"unknown model provider '{provider}' (expected 'groq' or 'cerebras')")
