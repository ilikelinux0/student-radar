"""Gemini scoring with a fake client. No network and no model id in code."""

import json
import logging

import httpx
import pytest

from radar.models import Settings
from radar.scorer import (
    ScoreError,
    ScoringItem,
    gemini_client,
    personal_spec,
    score_with_gemini,
)

MODEL = "test-model"
FIT_SUMMARY = "Final-year student looking for security internships."
SPEC = personal_spec(FIT_SUMMARY, "2026-10-11")
VALID = {
    "fit": 8,
    "category": "internship",
    "value": "$1000",
    "deadline": "2026-12-01",
    "summary": "A security internship for students.",
    "reasoning": "Matches the stated goals.",
    "offer_key": "example:security-intern",
    "org": "Example",
    "program_name": "Security intern",
}


class FakeGemini:
    def __init__(self, texts: list[str], usage: dict[str, int] | None = None) -> None:
        self._texts = list(texts)
        self.calls: list[dict[str, str]] = []
        self._usage = usage

    def generate(
        self,
        *,
        model: str,
        system: str,
        user: str,
        max_output_tokens: int = 800,
    ) -> dict[str, object]:
        del max_output_tokens
        self.calls.append({"model": model, "system": system, "user": user})
        if not self._texts:
            raise AssertionError("unexpected generate call")
        text = self._texts.pop(0)
        body: dict[str, object] = {
            "candidates": [{"content": {"parts": [{"text": text}], "role": "model"}}],
        }
        if self._usage is not None:
            body["usageMetadata"] = self._usage
        return body


def _settings(limit: int = 40, batch: int = 1) -> Settings:
    return Settings(
        max_llm_items_per_run=limit,
        llm_batch_size=batch,
        keywords=["credits"],
        user_agent="StudentRadar/0.1 (personal research bot)",
        request_delay_seconds=0,
        scorer_provider="gemini",
    )


def _item(excerpt: str) -> ScoringItem:
    return ScoringItem(
        source_name="Example",
        url="https://example.com/replace-me",
        excerpt=excerpt,
    )


def _use_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "secret-key")
    monkeypatch.setenv("GEMINI_MODEL", MODEL)


def test_gemini_parses_json_and_keeps_the_excerpt_in_tags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if a Gemini reply is dropped or the excerpt leaves <page_content>."""
    _use_env(monkeypatch)
    client = FakeGemini([json.dumps(VALID)])

    outcomes = score_with_gemini(
        [_item("Free tier for startups.")], _settings(), client, spec=SPEC
    )

    assert outcomes[0].attempted is True
    score = outcomes[0].score
    assert score is not None
    assert score.fit == 8
    assert score.category == "internship"
    call = client.calls[0]
    assert call["model"] == MODEL
    assert FIT_SUMMARY in call["system"]
    assert "<page_content>" in call["system"]
    assert "<page_content>\nFree tier for startups.\n</page_content>" in call["user"]


def test_gemini_retries_invalid_json_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Breaks if invalid JSON is accepted or retried more than once."""
    _use_env(monkeypatch)
    client = FakeGemini(["not json", "still not json"])

    outcomes = score_with_gemini([_item("A grant.")], _settings(), client, spec=SPEC)

    assert len(client.calls) == 2
    assert "invalid JSON" in client.calls[1]["user"]
    assert outcomes[0].score is None
    assert outcomes[0].attempted is True


def test_gemini_budget_leaves_the_rest_unattempted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if the Gemini cap scores every item."""
    _use_env(monkeypatch)
    client = FakeGemini([json.dumps(VALID), json.dumps(VALID), json.dumps(VALID)])

    outcomes = score_with_gemini(
        [_item("one"), _item("two"), _item("three")],
        _settings(limit=2),
        client,
        spec=SPEC,
    )

    assert len(client.calls) == 2
    assert [item.attempted for item in outcomes] == [True, True, False]


def test_gemini_missing_key_calls_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Breaks if a missing key is replaced with a hardcoded model or key."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_MODEL", MODEL)
    client = FakeGemini([json.dumps(VALID)])

    with pytest.raises(ScoreError, match="GEMINI_API_KEY"):
        score_with_gemini([_item("credits")], _settings(), client, spec=SPEC)

    assert client.calls == []


def test_gemini_logs_token_counts_only(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Breaks if the page body or the API key is written to the log."""
    _use_env(monkeypatch)
    client = FakeGemini(
        [json.dumps(VALID)],
        usage={"promptTokenCount": 11, "candidatesTokenCount": 7},
    )
    caplog.set_level(logging.INFO, logger="radar.scorer")

    score_with_gemini([_item("secret excerpt body")], _settings(), client, spec=SPEC)

    assert "input=11" in caplog.text
    assert "output=7" in caplog.text
    assert "secret excerpt body" not in caplog.text
    assert "secret-key" not in caplog.text


def test_gemini_client_puts_the_key_in_a_header() -> None:
    """Breaks if the API key is put in the URL or the call hits the network."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["header"] = request.headers.get("x-goog-api-key")
        seen["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"candidates": []})

    client = gemini_client("secret-key", transport=httpx.MockTransport(handler))
    try:
        client.generate(model=MODEL, system="Be brief.", user="Hello")
    finally:
        client.close()

    url = seen["url"]
    assert isinstance(url, str)
    assert url == (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{MODEL}:generateContent"
    )
    assert "secret-key" not in url
    assert seen["header"] == "secret-key"
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["system_instruction"]["parts"][0]["text"] == "Be brief."
    assert body["generation_config"]["temperature"] == 0


def test_gemini_batch_falls_back_to_single_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if a bad Gemini batch is accepted or not scored one by one."""
    _use_env(monkeypatch)
    other = dict(VALID)
    other["offer_key"] = "example:grant"
    client = FakeGemini(["[]", json.dumps(VALID), json.dumps(other)])
    items = [
        ScoringItem("Example", "https://example.com/a", "alpha credits"),
        ScoringItem("Example", "https://example.com/b", "beta credits"),
    ]

    outcomes = score_with_gemini(items, _settings(batch=8), client, spec=SPEC)

    assert len(client.calls) == 3
    assert "JSON array" in client.calls[0]["user"]
    assert "JSON array" not in client.calls[1]["user"]
    assert outcomes[0].score is not None
    assert outcomes[1].score is not None
    assert outcomes[1].score.offer_key == "example:grant"


def test_gemini_client_asks_for_json() -> None:
    """Breaks if Gemini is allowed to wrap its JSON in prose or code fences."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"candidates": []})

    client = gemini_client("secret-key", transport=httpx.MockTransport(handler))
    try:
        client.generate(model=MODEL, system="Be brief.", user="Hello")
    finally:
        client.close()

    body = seen["body"]
    assert isinstance(body, dict)
    assert body["generation_config"]["response_mime_type"] == "application/json"


def test_gemini_fenced_json_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Breaks if a reply in a ```json fence costs a retry."""
    _use_env(monkeypatch)
    client = FakeGemini(["```json\n" + json.dumps(VALID) + "\n```"])

    outcomes = score_with_gemini([_item("credits")], _settings(), client, spec=SPEC)

    assert len(client.calls) == 1
    assert outcomes[0].score is not None


class FailingGemini(FakeGemini):
    """Answers the first call, then fails like a 429 from the API."""

    def generate(self, **kwargs: object) -> dict[str, object]:
        if self.calls:
            request = httpx.Request("POST", "https://example.com")
            raise httpx.HTTPStatusError(
                "429", request=request, response=httpx.Response(429, request=request)
            )
        return super().generate(**kwargs)  # type: ignore[arg-type]


def test_gemini_error_keeps_scores_already_made(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Breaks if a rate limit on a later call throws away earlier scores."""
    _use_env(monkeypatch)
    client = FailingGemini([json.dumps(VALID)])
    caplog.set_level(logging.WARNING, logger="radar.scorer")

    outcomes = score_with_gemini(
        [_item("one"), _item("two"), _item("three")], _settings(), client, spec=SPEC
    )

    assert outcomes[0].score is not None
    assert [item.attempted for item in outcomes] == [True, False, False]
    assert "status=429" in caplog.text
    assert "secret-key" not in caplog.text


def test_gemini_batch_rescores_only_the_invalid_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if one bad object makes every item in the batch cost a call."""
    _use_env(monkeypatch)
    bad = dict(VALID)
    bad["category"] = "not-a-category"
    other = dict(VALID)
    other["offer_key"] = "example:grant"
    client = FakeGemini(
        [json.dumps([VALID, bad, VALID]), json.dumps(other)],
    )
    items = [
        ScoringItem("Example", f"https://example.com/{name}", f"{name} credits")
        for name in ("a", "b", "c")
    ]

    outcomes = score_with_gemini(items, _settings(batch=8), client, spec=SPEC)

    assert len(client.calls) == 2
    assert "https://example.com/b" in client.calls[1]["user"]
    assert all(outcome.score is not None for outcome in outcomes)
    assert outcomes[1].score is not None
    assert outcomes[1].score.offer_key == "example:grant"


def test_gemini_single_object_wrapped_in_an_array_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if `[{...}]` for one item costs a retry and then gives up."""
    _use_env(monkeypatch)
    client = FakeGemini([json.dumps([VALID])])

    outcomes = score_with_gemini([_item("credits")], _settings(), client, spec=SPEC)

    assert len(client.calls) == 1
    assert outcomes[0].score is not None
    assert outcomes[0].score.offer_key == "example:security-intern"


def test_gemini_array_of_two_is_still_invalid_for_one_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if a reply about two offers is silently cut to the first one."""
    _use_env(monkeypatch)
    client = FakeGemini([json.dumps([VALID, VALID]), json.dumps([VALID, VALID])])

    outcomes = score_with_gemini([_item("credits")], _settings(), client, spec=SPEC)

    assert len(client.calls) == 2
    assert outcomes[0].score is None


def test_gemini_client_retries_an_overloaded_model() -> None:
    """Breaks if one 503 from an overloaded model costs the whole day's scoring."""
    statuses = [503, 200]
    slept: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        status = statuses.pop(0)
        return httpx.Response(status, json={"candidates": []})

    client = gemini_client(
        "secret-key", transport=httpx.MockTransport(handler), sleeper=slept.append
    )
    try:
        client.generate(model=MODEL, system="Be brief.", user="Hello")
    finally:
        client.close()

    assert statuses == []
    assert len(slept) == 1


def test_gemini_client_does_not_retry_a_bad_request() -> None:
    """Breaks if a wrong model id (404) is retried instead of failing at once."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404, json={"error": {"message": "not found"}})

    client = gemini_client(
        "secret-key",
        transport=httpx.MockTransport(handler),
        sleeper=lambda _seconds: None,
    )
    try:
        with pytest.raises(httpx.HTTPStatusError):
            client.generate(model=MODEL, system="Be brief.", user="Hello")
    finally:
        client.close()

    assert calls["n"] == 1
