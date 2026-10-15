"""Small model-provider extension point with shared validation and recovery."""

import json
import math
import os
from typing import Any, Protocol, runtime_checkable

from .errors import ValidationError
from .llm import EFFORT, MODEL
from .validation import validate


@runtime_checkable
class JsonProvider(Protocol):
    """Return JSON-like output. Nabu owns validation, two attempts and call limits.

    Implementations must enforce their own request timeout and disable hidden
    retries if they want the request count to reflect actual network attempts.
    """
    def generate(self, *, agent: str, system: str, payload: dict, schema: dict) -> Any: ...


class AnthropicProvider:
    """Optional live provider. Imports the SDK only when explicitly constructed."""
    def __init__(self, *, model: str | None = None, effort: str | None = None,
                 timeout: float = 60.0, client=None):
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValidationError("timeout must be a finite positive number")
        self.model = model or os.environ.get("NABU_MODEL", MODEL)
        self.effort = effort or os.environ.get("NABU_EFFORT", EFFORT)
        self._owns_client = client is None
        if client is None:
            try:
                import anthropic
            except ImportError as exc:
                raise ImportError('Install live support with pip install "nabu-delivery[live]"') from exc
            client = anthropic.Anthropic(timeout=timeout, max_retries=0)
        self.client = client

    def close(self):
        """Close a client created here; injected clients remain caller-owned."""
        if self._owns_client:
            self.client.close()
            self._owns_client = False

    def generate(self, *, agent: str, system: str, payload: dict, schema: dict):
        response = self.client.messages.create(
            model=self.model, max_tokens=16000, system=system,
            messages=[{"role": "user", "content": json.dumps(payload, default=str)}],
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
        )
        if response.stop_reason != "end_turn":
            raise ValueError("Incomplete model response")
        text = "".join(block.text for block in response.content if block.type == "text")
        return json.loads(text)


class ProviderRunner:
    """Internal bridge to the existing agent workflow."""
    def __init__(self, log, provider: JsonProvider | None, max_calls: int, calls: int = 0):
        self.log, self.provider, self.max_calls, self.calls = log, provider, max_calls, calls

    def json(self, agent, system, payload, schema, fallback):
        def recover():
            result = fallback()
            validate(result, schema)
            return result
        if self.provider is None:
            return recover()
        for attempt in (1, 2):
            if self.calls >= self.max_calls:
                self.log.emit(agent, "recovered", "model call budget exhausted; using deterministic result")
                return recover()
            try:
                self.calls += 1
                result = self.provider.generate(agent=agent, system=system, payload=payload, schema=schema)
                validate(result, schema)
                return result
            except Exception as exc:
                self.log.emit(agent, "retry" if attempt == 1 else "recovered",
                              f"provider attempt {attempt} failed ({type(exc).__name__}); "
                              + ("retrying" if attempt == 1 else "using deterministic result"))
        return recover()
