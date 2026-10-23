"""AI provider abstraction (PRD §2: OpenAI Responses API behind a provider interface)."""

from __future__ import annotations

import logging
import re
from threading import BoundedSemaphore
from typing import NamedTuple, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)
logger = logging.getLogger(__name__)

# A generation job can run resume and cover-letter requests together. Keep that
# useful parallelism, but prevent multiple queue workers from multiplying it into
# a burst of long-lived TLS connections on less reliable networks.
_OPENAI_REQUEST_LIMIT = BoundedSemaphore(2)


class AIUsage(NamedTuple):
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0


def supports_explicit_prompt_caching(model: str) -> bool:
    """Explicit breakpoints are supported by GPT-5.6 and later model families."""
    match = re.match(r"^gpt-(\d+)(?:\.(\d+))?", model.lower())
    if match is None:
        return False
    major, minor = int(match.group(1)), int(match.group(2) or 0)
    return major > 5 or (major == 5 and minor >= 6)


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
    """The model returned no usable completed result (for example, a refusal)."""


class AIIncompleteOutputError(TransientAIError):
    """A structured response ended before it could be parsed and may succeed on retry."""


class BudgetExceeded(AIError):
    pass


class AIProvider(Protocol):
    name: str

    def structured(
        self,
        *,
        model: str,
        instructions: str,
        input_text: str,
        schema: type[T],
        max_output_tokens: int,
        cache_prefix: str | None = None,
    ) -> tuple[T, AIUsage]: ...


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, timeout: float = 180.0) -> None:
        from openai import OpenAI

        # Retries are handled by our persistent queue, not inside the SDK.
        self._client = OpenAI(api_key=api_key, max_retries=0, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def structured(
        self,
        *,
        model: str,
        instructions: str,
        input_text: str,
        schema: type[T],
        max_output_tokens: int,
        cache_prefix: str | None = None,
    ) -> tuple[T, AIUsage]:
        import openai

        request_input: object = input_text
        cache_options: dict[str, str] | None = None
        if cache_prefix:
            prefix: dict[str, object] = {"type": "input_text", "text": cache_prefix}
            if supports_explicit_prompt_caching(model):
                prefix["prompt_cache_breakpoint"] = {"mode": "explicit"}
                cache_options = {"mode": "explicit", "ttl": "30m"}
            # Candidate and job text are untrusted data, so both remain user-role messages.
            request_input = [
                {"role": "user", "content": [prefix]},
                {"role": "user", "content": [{"type": "input_text", "text": input_text}]},
            ]

        request: dict[str, object] = {
            "model": model,
            "instructions": instructions,
            "input": request_input,
            "text_format": schema,
            "max_output_tokens": max_output_tokens,
            "store": False,
        }
        if cache_options is not None:
            request["prompt_cache_options"] = cache_options
        try:
            # Streaming keeps a long generation connection active instead of
            # waiting silently for one large response. This is materially more
            # reliable behind proxies/firewalls that close idle HTTPS requests,
            # and the SDK still parses the final event into ``schema``.
            with _OPENAI_REQUEST_LIMIT:
                with self._client.responses.stream(**request) as stream:
                    response = stream.get_final_response()
        except openai.RateLimitError as exc:
            if getattr(exc, "code", None) == "insufficient_quota":
                raise AIConfigError("Your OpenAI account has no remaining quota. Check billing on platform.openai.com.") from exc
            raise TransientAIError("OpenAI rate limit reached; will retry.") from exc
        except openai.APITimeoutError as exc:
            logger.warning("OpenAI request timed out: %s", exc)
            raise TransientAIError("OpenAI request timed out; will retry the failed request.") from exc
        except openai.APIConnectionError as exc:
            cause = exc.__cause__
            detail = f"{type(cause).__name__}: {cause}" if cause is not None else str(exc)
            logger.warning("OpenAI connection failed: %s", detail)
            raise TransientAIError("OpenAI connection was interrupted; will retry the failed request.") from exc
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
        except ValidationError as exc:
            # The Responses SDK validates ``response.output_text.done`` before
            # returning the final response. If output hits its token limit, the
            # JSON can end in the middle of a string and Pydantic raises here,
            # before ``response.status`` or ``incomplete_details`` is available.
            error_types = {str(error.get("type", "validation_error")) for error in exc.errors()}
            if "json_invalid" in error_types:
                logger.warning(
                    "OpenAI returned incomplete structured JSON (validation types=%s)",
                    ",".join(sorted(error_types)),
                )
                raise AIIncompleteOutputError(
                    "OpenAI returned incomplete structured JSON.",
                    # The SDK raises before exposing response.usage. Charging the
                    # requested ceiling is conservative and prevents retries from
                    # bypassing the application's cost budget.
                    usage=AIUsage(output_tokens=max_output_tokens),
                ) from exc
            logger.warning(
                "OpenAI structured response did not match its schema (validation types=%s)",
                ",".join(sorted(error_types)),
            )
            raise AIOutputError("OpenAI returned a structured response in an unexpected format.") from exc
        except RuntimeError as exc:
            # Incomplete streamed Responses do not emit response.completed, so
            # get_final_response() cannot return the object containing the
            # max_output_tokens reason. Treat only that SDK message as retryable;
            # unrelated RuntimeErrors remain programming errors.
            if "Didn't receive" in str(exc) and "response.completed" in str(exc):
                logger.warning("OpenAI structured response stream ended before completion")
                raise AIIncompleteOutputError(
                    "OpenAI ended the structured response before completion.",
                    usage=AIUsage(output_tokens=max_output_tokens),
                ) from exc
            raise

        usage = response.usage
        if usage:
            details = getattr(usage, "input_tokens_details", None)
            tokens = AIUsage(
                usage.input_tokens or 0,
                usage.output_tokens or 0,
                getattr(details, "cached_tokens", 0) or 0,
                getattr(details, "cache_write_tokens", 0) or 0,
            )
            logger.info(
                "OpenAI usage model=%s input=%d cached=%d cache_write=%d output=%d",
                model,
                tokens.input_tokens,
                tokens.cached_input_tokens,
                tokens.cache_write_input_tokens,
                tokens.output_tokens,
            )
        else:
            tokens = AIUsage()
        parsed = response.output_parsed
        if parsed is None:
            details = getattr(response, "incomplete_details", None)
            status = getattr(response, "status", None)
            reason = getattr(details, "reason", None) or status or "no output"
            if status == "incomplete" and reason == "max_output_tokens":
                raise AIIncompleteOutputError(
                    "OpenAI reached the structured response token limit.",
                    usage=tokens,
                )
            raise AIOutputError(f"The model returned no structured result ({reason}).", usage=tokens)
        return parsed, tokens
