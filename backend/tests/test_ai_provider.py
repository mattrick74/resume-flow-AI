from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ValidationError

from resumeflow.services.ai.provider import AIIncompleteOutputError, AIUsage, OpenAIProvider
from resumeflow.services.ai.service import estimate_cost_micros


class ExampleResult(BaseModel):
    value: str


class RecordingResponses:
    def __init__(self) -> None:
        self.request: dict = {}

    def stream(self, **request):
        self.request = request
        response = SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=2_000,
                output_tokens=100,
                input_tokens_details=SimpleNamespace(cached_tokens=1_500, cache_write_tokens=200),
            ),
            output_parsed=ExampleResult(value="ok"),
            incomplete_details=None,
            status="completed",
        )
        return RecordingStream(response)


class RecordingStream:
    def __init__(self, response) -> None:
        self.response = response

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def get_final_response(self):
        return self.response


class RaisingResponses:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def stream(self, **_request):
        return RaisingStream(self.error)


class RaisingStream:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def get_final_response(self):
        raise self.error


def provider_with_recorder() -> tuple[OpenAIProvider, RecordingResponses]:
    recorder = RecordingResponses()
    provider = OpenAIProvider.__new__(OpenAIProvider)
    provider._client = SimpleNamespace(responses=recorder)
    return provider, recorder


def test_candidate_prefix_uses_explicit_cache_breakpoint_for_supported_model():
    provider, recorder = provider_with_recorder()

    result, usage = provider.structured(
        model="gpt-6.1-sol",
        instructions="write accurately",
        cache_prefix="<candidate_evidence>candidate</candidate_evidence>",
        input_text="<job_description>job</job_description>",
        schema=ExampleResult,
        max_output_tokens=3_000,
    )

    assert result.value == "ok"
    assert usage == AIUsage(2_000, 100, 1_500, 200)
    assert recorder.request["store"] is False
    assert recorder.request["prompt_cache_options"] == {"mode": "explicit", "ttl": "30m"}
    prefix, job = recorder.request["input"]
    assert prefix["role"] == job["role"] == "user"
    assert prefix["content"][0]["prompt_cache_breakpoint"] == {"mode": "explicit"}
    assert "candidate" in prefix["content"][0]["text"]
    assert "job" in job["content"][0]["text"]


def test_candidate_prefix_falls_back_without_explicit_options_for_older_model():
    provider, recorder = provider_with_recorder()

    provider.structured(
        model="gpt-4.1",
        instructions="write accurately",
        cache_prefix="candidate",
        input_text="job",
        schema=ExampleResult,
        max_output_tokens=3_000,
    )

    assert "prompt_cache_options" not in recorder.request
    assert "prompt_cache_breakpoint" not in recorder.request["input"][0]["content"][0]
    assert recorder.request["input"][0]["content"][0]["text"] == "candidate"
    assert recorder.request["input"][1]["content"][0]["text"] == "job"


def test_cached_usage_reduces_estimated_cost_for_gpt_6_1_sol():
    prices = {"gpt-6.1-sol": {"input": 2.0, "output": 10.0}}
    uncached = estimate_cost_micros(prices, "gpt-6.1-sol", AIUsage(2_000, 100))
    cached = estimate_cost_micros(prices, "gpt-6.1-sol", AIUsage(2_000, 100, 1_500, 200))

    assert uncached == 5_000
    assert cached == 2_250


def test_truncated_structured_json_is_converted_to_retryable_error():
    with pytest.raises(ValidationError) as captured:
        ExampleResult.model_validate_json('{"value":"unfinished')
    parse_error = captured.value
    provider = OpenAIProvider.__new__(OpenAIProvider)
    provider._client = SimpleNamespace(responses=RaisingResponses(parse_error))

    with pytest.raises(AIIncompleteOutputError, match="incomplete structured JSON") as raised:
        provider.structured(
            model="gpt-6.1-sol",
            instructions="write accurately",
            input_text="job",
            schema=ExampleResult,
            max_output_tokens=12_000,
        )

    assert raised.value.transient is True
    assert raised.value.__cause__ is parse_error
    assert raised.value.usage == AIUsage(output_tokens=12_000)
    assert "unfinished" not in str(raised.value)


def test_incomplete_stream_without_completed_event_is_retryable():
    stream_error = RuntimeError("Didn't receive a `response.completed` event.")
    provider = OpenAIProvider.__new__(OpenAIProvider)
    provider._client = SimpleNamespace(responses=RaisingResponses(stream_error))

    with pytest.raises(AIIncompleteOutputError, match="before completion") as raised:
        provider.structured(
            model="gpt-6.1-sol",
            instructions="write accurately",
            input_text="job",
            schema=ExampleResult,
            max_output_tokens=12_000,
        )

    assert raised.value.__cause__ is stream_error
    assert raised.value.usage == AIUsage(output_tokens=12_000)


def test_max_output_token_response_is_converted_to_retryable_error():
    response = SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=12_000,
            input_tokens_details=SimpleNamespace(cached_tokens=0, cache_write_tokens=0),
        ),
        output_parsed=None,
        incomplete_details=SimpleNamespace(reason="max_output_tokens"),
        status="incomplete",
    )
    provider = OpenAIProvider.__new__(OpenAIProvider)
    provider._client = SimpleNamespace(
        responses=SimpleNamespace(stream=lambda **_request: RecordingStream(response))
    )

    with pytest.raises(AIIncompleteOutputError, match="token limit") as raised:
        provider.structured(
            model="gpt-6.1-sol",
            instructions="write accurately",
            input_text="job",
            schema=ExampleResult,
            max_output_tokens=12_000,
        )

    assert raised.value.usage == AIUsage(100, 12_000, 0, 0)
