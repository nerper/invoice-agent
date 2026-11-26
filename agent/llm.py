"""OpenRouter client (OpenAI-compatible) with retries and cost extraction."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from agent.config import Settings


class LLMError(Exception):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    args_raw: str


@dataclass
class LLMResponse:
    tool_calls: list[ToolCall] = field(default_factory=list)
    content: str | None = None
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0


# ---------------------------------------------------------------------------
# THE MODEL CLIENT: talks to the model through OpenRouter (OpenAI-compatible API).
# This is the only place the network/LLM is touched.
# ---------------------------------------------------------------------------
class OpenRouterLLM:
    def __init__(self, settings: Settings) -> None:
        from openai import OpenAI

        if not settings.api_key:
            raise LLMError("OPENROUTER_API_KEY is not set (add it to .env)")
        if not settings.model:
            raise LLMError("MODEL is not set (add it to .env)")
        self.s = settings
        # base_url is what makes this "a model through OpenRouter".
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=settings.api_key,
            timeout=settings.llm_timeout_s,
            max_retries=0,
        )

    def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LLMResponse:
        import openai

        last: Exception | None = None
        for attempt in range(self.s.llm_max_retries + 1):
            try:
                r = self.client.chat.completions.create(
                    model=self.s.model,
                    messages=messages,
                    tools=tools,
                    # "required" = the model MUST call a tool every turn (never just chats).
                    tool_choice="required",
                    temperature=self.s.temperature,
                    extra_body={"usage": {"include": True}},
                )
                return self._parse(r)
            # Retry only on transient problems (timeout, connection, rate limit, 5xx).
            except (
                openai.APITimeoutError,
                openai.APIConnectionError,
                openai.RateLimitError,
            ) as e:
                last = e
            except openai.APIStatusError as e:
                if e.status_code < 500:
                    raise LLMError(
                        f"LLM request rejected ({e.status_code}): {e.message}"
                    ) from e
                last = e
            if attempt < self.s.llm_max_retries:
                time.sleep(2**attempt)
        raise LLMError(f"LLM unavailable after retries: {type(last).__name__}")

    def _parse(self, r: Any) -> LLMResponse:
        if not r.choices:
            raise LLMError("LLM returned no choices")
        msg = r.choices[0].message
        u = r.usage
        tin, tout = (u.prompt_tokens, u.completion_tokens) if u else (0, 0)
        # Work out what this call cost (OpenRouter reports it; fall back to token pricing).
        cost = None
        if u is not None:
            cost = getattr(u, "cost", None)
            if cost is None and getattr(u, "model_extra", None):
                cost = u.model_extra.get("cost")
        if cost is None:
            cost = (
                tin * self.s.fallback_usd_per_mtok_in
                + tout * self.s.fallback_usd_per_mtok_out
            ) / 1e6
        calls = [
            ToolCall(c.id, c.function.name, c.function.arguments or "{}")
            for c in (msg.tool_calls or [])
        ]
        return LLMResponse(calls, msg.content, float(cost), tin, tout)
