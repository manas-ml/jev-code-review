"""OpenRouter chat-completions client (OpenAI-compatible) with retry, tracing and
pydantic-validated JSON output."""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from codereview.config import Settings
from codereview.tracing import CallRecord, Tracer, hash_state

T = TypeVar("T", bound=BaseModel)

RETRY_STATUS = {429, 500, 502, 503, 504}


class LLMResponse(BaseModel):
    content: str = ""
    tool_calls: list[dict[str, Any]] = []
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class LLM(Protocol):
    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        stage: str,
        tracer: Tracer | None = None,
        json_mode: bool = False,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse: ...


class LLMError(RuntimeError):
    pass


class OpenRouterClient:
    def __init__(
        self,
        settings: Settings,
        http: httpx.AsyncClient | None = None,
        *,
        max_retries: int = 4,
        backoff_base: float = 0.5,
    ) -> None:
        self.settings = settings
        self.http = http or httpx.AsyncClient(timeout=120)
        self.max_retries = max_retries
        self.backoff_base = backoff_base

    def _cost(self, model: str, inp: int, out: int, reported: float | None) -> float:
        if reported is not None:
            return reported
        price = self.settings.prices.get(model)
        if price is None:
            return 0.0
        return (inp * price.input + out * price.output) / 1_000_000

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        stage: str,
        tracer: Tracer | None = None,
        json_mode: bool = False,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        body: dict[str, Any] = {"model": model, "messages": messages, "usage": {"include": True}}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if tools:
            body["tools"] = tools
        headers = {"Authorization": f"Bearer {self.settings.openrouter_api_key}"}
        url = f"{self.settings.openrouter_base_url}/chat/completions"
        start = time.perf_counter()
        last_err = ""
        for attempt in range(self.max_retries + 1):
            try:
                r = await self.http.post(url, json=body, headers=headers)
            except httpx.TransportError as e:
                last_err = repr(e)
            else:
                if r.status_code in RETRY_STATUS:
                    last_err = f"HTTP {r.status_code}"
                elif r.status_code >= 400:
                    self._trace(tracer, stage, model, start, body, error=f"HTTP {r.status_code}")
                    raise LLMError(f"HTTP {r.status_code}: {r.text[:300]}")
                else:
                    return self._parse(r.json(), tracer, stage, model, start, body)
            if attempt < self.max_retries:
                await asyncio.sleep(self.backoff_base * 2**attempt)
        self._trace(tracer, stage, model, start, body, error=last_err)
        raise LLMError(f"LLM call failed after retries: {last_err}")

    def _parse(
        self,
        data: dict[str, Any],
        tracer: Tracer | None,
        stage: str,
        model: str,
        start: float,
        body: dict[str, Any],
    ) -> LLMResponse:
        msg = data["choices"][0]["message"]
        usage = data.get("usage") or {}
        inp = int(usage.get("prompt_tokens", 0))
        out = int(usage.get("completion_tokens", 0))
        resp = LLMResponse(
            content=msg.get("content") or "",
            tool_calls=msg.get("tool_calls") or [],
            input_tokens=inp,
            output_tokens=out,
            cost_usd=self._cost(model, inp, out, usage.get("cost")),
        )
        self._trace(tracer, stage, model, start, body, resp=resp)
        return resp

    def _trace(
        self,
        tracer: Tracer | None,
        stage: str,
        model: str,
        start: float,
        body: dict[str, Any],
        *,
        resp: LLMResponse | None = None,
        error: str | None = None,
    ) -> None:
        if tracer is None:
            return
        tracer.record(
            CallRecord(
                kind="llm",
                stage=stage,
                model=model,
                latency_s=time.perf_counter() - start,
                input_tokens=resp.input_tokens if resp else 0,
                output_tokens=resp.output_tokens if resp else 0,
                cost_usd=resp.cost_usd if resp else 0.0,
                request_hash=hash_state(json.dumps(body["messages"], default=str)),
                error=error,
            )
        )


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.M)


def extract_json(text: str) -> Any:
    text = _FENCE.sub("", text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
        if start < 0:
            raise
        end = max(text.rfind("}"), text.rfind("]"))
        return json.loads(text[start : end + 1])


async def chat_validated(
    llm: LLM,
    schema: type[T],
    messages: list[dict[str, Any]],
    *,
    model: str,
    stage: str,
    tracer: Tracer | None = None,
) -> T:
    """JSON-mode call validated by pydantic; retries once feeding back the error."""
    msgs = list(messages)
    last_error = ""
    for _ in range(2):
        resp = await llm.chat(msgs, model=model, stage=stage, tracer=tracer, json_mode=True)
        try:
            return schema.model_validate(extract_json(resp.content))
        except (ValidationError, json.JSONDecodeError) as e:
            last_error = str(e)[:1500]
            msgs = [
                *messages,
                {"role": "assistant", "content": resp.content},
                {
                    "role": "user",
                    "content": "Your output failed validation. Return corrected JSON only. "
                    f"Error: {last_error}",
                },
            ]
    raise LLMError(f"structured output failed validation twice: {last_error}")
