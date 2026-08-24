import json
from unittest.mock import MagicMock

from comicload.config import LlmConfig
from comicload.quarantine.llm import describe_cover


def test_describe_cover_returns_empty_when_no_image():
    config = LlmConfig(enabled=True, api_key="secret")
    query, err = describe_cover(None, config)
    assert query == ""
    assert err is not None


def test_describe_cover_returns_empty_when_no_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    config = LlmConfig(enabled=True, api_key="")
    query, err = describe_cover(b"pixels", config)
    assert query == ""
    assert "No API key found" in (err or "")


def test_describe_cover_anthropic_provider(monkeypatch):
    config = LlmConfig(enabled=True, provider="anthropic", api_key="sk-test-123")

    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({"content": [{"text": "Superman #35 2024"}]}).encode(
        "utf-8"
    )
    mock_resp.__enter__.return_value = mock_resp

    called_req = []

    def mock_urlopen(req, timeout=15):
        called_req.append(req)
        return mock_resp

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

    query, err = describe_cover(b"png_pixels", config)

    assert query == "Superman #35 2024"
    assert err is None
    assert len(called_req) == 1
    assert called_req[0].headers["X-api-key"] == "sk-test-123"


def test_describe_cover_openai_provider(monkeypatch):
    config = LlmConfig(enabled=True, provider="openai", api_key="sk-openai-456")

    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(
        {"choices": [{"message": {"content": "Alex + Ada #2"}}]}
    ).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    called_req = []

    def mock_urlopen(req, timeout=15):
        called_req.append(req)
        return mock_resp

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

    query, err = describe_cover(b"jpg_pixels", config)

    assert query == "Alex + Ada #2"
    assert err is None
    assert len(called_req) == 1
    assert called_req[0].headers["Authorization"] == "Bearer sk-openai-456"


def test_describe_cover_fails_closed_on_network_error(monkeypatch):
    config = LlmConfig(enabled=True, provider="anthropic", api_key="sk-test-123")

    def mock_urlopen(req, timeout=15):
        raise OSError("Network unreachable")

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

    query, err = describe_cover(b"pixels", config)
    assert query == ""
    assert err is not None
