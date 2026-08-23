from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from memorygraph import ProviderError
from memorygraph.dream import SourceBundle, SourceObservation
from memorygraph.providers import OpenAICompatibleConfig, OpenAICompatibleDreamProvider


@dataclass
class FakeTransport:
    response: dict[str, Any]
    requests: list[tuple[dict[str, Any], dict[str, str]]] = field(default_factory=list)

    def request(self, *, payload, headers):
        self.requests.append((dict(payload), dict(headers)))
        return self.response


def _bundle() -> SourceBundle:
    return SourceBundle(
        bundle_id="bundle-1",
        bank_id="bank-1",
        reason="test",
        priority=1,
        observations=(
            SourceObservation(
                observation_id="observation-1",
                source_key="session-1",
                content="Acme uses Python 3.13.",
                actor_type="user",
                observed_at=datetime(2026, 8, 22, tzinfo=UTC),
                trust_class="owner_explicit",
            ),
        ),
    )


def test_provider_uses_strict_structured_responses_without_storing(monkeypatch) -> None:
    monkeypatch.setenv("TEST_PROVIDER_KEY", "secret")
    transport = FakeTransport(
        {
            "id": "response-1",
            "model": "test-model",
            "output_text": ('{"entities":[],"claims":[],"warnings":[],"provider_notes":[]}'),
            "usage": {"input_tokens": 12, "output_tokens": 4, "total_tokens": 16},
        }
    )
    provider = OpenAICompatibleDreamProvider(
        OpenAICompatibleConfig(model="test-model", api_key_env="TEST_PROVIDER_KEY"),
        transport=transport,
    )

    result = provider.extract(_bundle())

    assert result.candidates.claims == ()
    assert result.trace.usage is not None
    assert result.trace.usage.total_tokens == 16
    payload, headers = transport.requests[0]
    assert payload["store"] is False
    assert payload["text"]["format"]["type"] == "json_schema"
    assert payload["text"]["format"]["strict"] is True
    assert headers["Authorization"] == "Bearer secret"
    assert "untrusted data" in payload["instructions"]


def test_provider_fails_before_network_when_credential_is_missing(monkeypatch) -> None:
    monkeypatch.delenv("MISSING_PROVIDER_KEY", raising=False)
    transport = FakeTransport({})
    provider = OpenAICompatibleDreamProvider(
        OpenAICompatibleConfig(model="test-model", api_key_env="MISSING_PROVIDER_KEY"),
        transport=transport,
    )

    with pytest.raises(ProviderError, match="environment variable"):
        provider.extract(_bundle())

    assert transport.requests == []
