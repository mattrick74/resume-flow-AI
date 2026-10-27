"""A scriptable stand-in for the OpenAI provider. Records every prompt it sees."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from resumeflow.services.ai.provider import AIError, AIUsage


class FakeProvider:
    name = "fake"

    def __init__(self) -> None:
        self.handlers: dict[str, Callable[[str], Any]] = {}
        self.calls: list[dict[str, Any]] = []
        self.fail_with: AIError | None = None

    def on(self, schema_name: str, handler: Callable[[str], Any]) -> None:
        self.handlers[schema_name] = handler

    def _both_documents(self) -> Callable[[str], Any] | None:
        """The single request for both documents (v2.0) answered from the separate resume and cover-letter scripts, so
        a test can script each document on its own. A missing script fails the request like the real model would."""
        resume, cover = self.handlers.get("AIResumeTailoring"), self.handlers.get("AICoverLetter")
        if resume is None or cover is None:
            return None

        def handler(input_text: str) -> dict[str, Any]:
            parts = [resume(input_text), cover(input_text)]
            resume_part, cover_part = (part.model_dump() if hasattr(part, "model_dump") else part for part in parts)
            return {"resume": resume_part, "cover_letter": cover_part}

        return handler

    def structured(self, *, model, instructions, input_text, schema, max_output_tokens, cache_prefix=None):
        full_input = f"{cache_prefix}\n\n{input_text}" if cache_prefix else input_text
        self.calls.append(
            {
                "model": model,
                "schema": schema.__name__,
                "instructions": instructions,
                "input": full_input,
                "cache_prefix": cache_prefix,
                "max_output_tokens": max_output_tokens,
            }
        )
        if self.fail_with is not None:
            raise self.fail_with
        handler = self.handlers.get(schema.__name__)
        if handler is None and schema.__name__ == "AIApplicationDocuments":
            handler = self._both_documents()
        if handler is None:
            raise AIError(f"FakeProvider has no handler for {schema.__name__}")
        result = handler(full_input)
        if isinstance(result, dict):
            result = schema.model_validate(result)
        return result, AIUsage(input_tokens=1000, output_tokens=500)
