"""AI provider abstraction (PRD §2: OpenAI Responses API behind a provider interface)."""

from __future__ import annotations

from typing import NamedTuple, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class AIUsage(NamedTuple):
    input_tokens: int = 0
    output_tokens: int = 0


class AIError(Exception):
    """Base error. ``transient`` errors are retried with backoff by the queue."""

    transient = False

    def __init__(self, message: str, usage: AIUsage | None = None) -> None:
        super().__init__(message)
        self.usage = usage or AIUsage()


class TransientAIError(AIError):
    transient = True


class AIConfigError(AIError):
    """Missing/invalid key, unknown model, no quota: retrying won't help."""


class AIOutputError(AIError):
    """The model returned nothing usable (refusal, truncation)."""


class BudgetExceeded(AIError):
    pass


class AIProvider(Protocol):
    name: str

    def structured(
        self, *, model: str, instructions: str, input_text: str, schema: type[T], max_output_tokens: int
    ) -> tuple[T, AIUsage]: ...


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, timeout: float = 180.0) -> None:
        from openai import OpenAI

        # Retries are handled by our persistent queue, not inside the SDK.
        self._client = OpenAI(api_key=api_key, max_retries=0, timeout=timeout)

    def structured(
        self, *, model: str, instructions: str, input_text: str, schema: type[T], max_output_tokens: int
    ) -> tuple[T, AIUsage]:
        import openai

        try:
            response = self._client.responses.parse(
                model=model,
                instructions=instructions,
                input=input_text,
                text_format=schema,
                max_output_tokens=max_output_tokens,
            )
        except openai.RateLimitError as exc:
            if getattr(exc, "code", None) == "insufficient_quota":
                raise AIConfigError("Your OpenAI account has no remaining quota. Check billing on platform.openai.com.") from exc
            raise TransientAIError("OpenAI rate limit reached; will retry.") from exc
        except (openai.APIConnectionError, openai.APITimeoutError) as exc:
            raise TransientAIError("Could not reach OpenAI (network or timeout); will retry.") from exc
        except openai.InternalServerError as exc:
            raise TransientAIError("OpenAI had a server error; will retry.") from exc
        except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
            raise AIConfigError("OpenAI rejected the API key. Update it in Settings.") from exc
        except openai.NotFoundError as exc:
            raise AIConfigError(f"Model '{model}' isn't available to this API key. Change it in Settings.") from exc
        except openai.BadRequestError as exc:
            raise AIError(f"OpenAI rejected the request: {getattr(exc, 'message', exc)}") from exc
        except openai.APIStatusError as exc:
            if exc.status_code >= 500:
                raise TransientAIError(f"OpenAI error {exc.status_code}; will retry.") from exc
            raise AIError(f"OpenAI error {exc.status_code}: {getattr(exc, 'message', exc)}") from exc

        usage = response.usage
        tokens = AIUsage(usage.input_tokens or 0, usage.output_tokens or 0) if usage else AIUsage()
        parsed = response.output_parsed
        if parsed is None:
            reason = getattr(response.incomplete_details, "reason", None) or response.status or "no output"
            raise AIOutputError(f"The model returned no structured result ({reason}).", usage=tokens)
        return parsed, tokens
