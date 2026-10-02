"""LLM provider abstraction. Domain code depends on ``LLMProvider``, never on a vendor SDK,
so providers can be added or swapped without touching business logic."""

import functools
import json
import time
from dataclasses import dataclass
from typing import Any, Protocol

import anthropic

from app.ai.untrusted import UNTRUSTED_DATA_RULES, with_rules
from app.core.config import Settings


class LLMError(Exception):
    """The provider could not produce a usable result. Safe to show a generic message."""


@dataclass(frozen=True)
class LLMJsonResult:
    data: dict[str, Any]
    provider: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int


class LLMProvider(Protocol):
    name: str
    model: str

    async def complete_json(
        self, *, system: str, prompt: str, schema: dict[str, Any], max_tokens: int = 16000
    ) -> LLMJsonResult:
        """Return a JSON object that conforms to ``schema``."""
        ...


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self, api_key: str, model: str, *, timeout: float = 120.0, max_retries: int = 1
    ) -> None:
        self.model = model
        self._client = _client(api_key, timeout, max_retries)

    async def complete_json(
        self, *, system: str, prompt: str, schema: dict[str, Any], max_tokens: int = 16000
    ) -> LLMJsonResult:
        started = time.perf_counter()
        # Every call states that tagged data is never instructions (prompt injection).
        system = with_rules(system) if UNTRUSTED_DATA_RULES not in system else system
        try:
            response = await self._client.beta.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_config={
                    "effort": "medium",
                    "format": {"type": "json_schema", "schema": schema},
                },
                # Route policy declines to a fallback model instead of failing outright.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.AuthenticationError as exc:
            raise LLMError("The AI provider rejected the API key.") from exc
        except anthropic.RateLimitError as exc:
            raise LLMError("The AI provider is rate limiting requests.") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"The AI provider returned an error ({exc.status_code}).") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("Could not reach the AI provider.") from exc

        if response.stop_reason == "refusal":
            raise LLMError("The AI provider declined to process this document.")
        if response.stop_reason == "max_tokens":
            raise LLMError("The AI response was cut off before it finished.")
        text = next((block.text for block in response.content if block.type == "text"), None)
        if text is None:
            raise LLMError("The AI provider returned no content.")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError("The AI provider returned malformed JSON.") from exc
        if not isinstance(data, dict):
            raise LLMError("The AI provider returned an unexpected shape.")
        return LLMJsonResult(
            data=data,
            provider=self.name,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )


@functools.lru_cache(maxsize=4)
def _client(api_key: str, timeout: float, max_retries: int) -> anthropic.AsyncAnthropic:
    """One client (and connection pool) per process and configuration, not per request."""
    return anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout, max_retries=max_retries)


def get_llm_provider(settings: Settings) -> LLMProvider | None:
    """The configured provider, or None when no credentials are configured."""
    if settings.llm_provider == "anthropic" and settings.anthropic_api_key is not None:
        key = settings.anthropic_api_key.get_secret_value()
        if key:
            return AnthropicProvider(
                api_key=key,
                model=settings.llm_model,
                timeout=settings.llm_timeout_seconds,
                max_retries=settings.llm_max_retries,
            )
    return None
