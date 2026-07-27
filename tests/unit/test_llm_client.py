from unittest.mock import patch

import pytest

from core.llm_client import LMStudioClient


@pytest.fixture
def llm_client() -> LMStudioClient:
    with patch("core.llm_client.requests.Session"):
        return LMStudioClient(base_url="http://not-used.invalid")


def test_parse_plain_json(llm_client: LMStudioClient) -> None:
    assert llm_client._parse_json_response('{"status": "ok"}') == {"status": "ok"}


def test_parse_json_in_markdown_fence(llm_client: LMStudioClient) -> None:
    assert llm_client._parse_json_response('```json\n{"status": "ok"}\n```') == {"status": "ok"}


def test_parse_json_after_leading_prose(llm_client: LMStudioClient) -> None:
    assert llm_client._parse_json_response('Here is the result: {"status": "ok"}') == {
        "status": "ok"
    }


def test_malformed_json_returns_error_shape(llm_client: LMStudioClient) -> None:
    raw = '{"status": invalid}'

    result = llm_client._parse_json_response(raw)

    assert result == {"error": "JSON parse failed", "raw_response": raw}
