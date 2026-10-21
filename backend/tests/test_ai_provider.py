from __future__ import annotations

from types import SimpleNamespace

from pydantic import BaseModel

from resumeflow.services.ai.provider import AIUsage, OpenAIProvider
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
