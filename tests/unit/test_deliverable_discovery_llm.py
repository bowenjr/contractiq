import json

from core.deliverable_discovery_extraction import RawUnit
from core.deliverable_discovery_llm import (
    CORRECTIVE_INSTRUCTION,
    DISCOVERY_MAX_TOKENS,
    DISCOVERY_RETRY_MAX_TOKENS,
    INVALID_REASON_PREFIX,
    PassageOutcomeKind,
    analyse_units,
    is_truncated_reason,
    propose_candidates,
)
from core.llm_client import StructuredReply


class _StubLLM:
    def __init__(self, responses: list[dict]) -> None:
        self._responses = responses
        self.calls = 0

    def complete_json(
        self,
        prompt,
        system_prompt="",
        temperature=0.05,
        max_tokens=1024,
        context_label=None,
        timeout=None,
    ):
        response = self._responses[self.calls]
        self.calls += 1
        return response


class _StructuredStubLLM:
    def __init__(self, responses: list[StructuredReply | Exception]) -> None:
        self._responses = responses
        self.calls: list[dict[str, object]] = []

    def complete_structured(self, **kwargs: object) -> StructuredReply:
        response = self._responses[len(self.calls)]
        self.calls.append(kwargs)
        if isinstance(response, Exception):
            raise response
        return response


def _unit(text: str = "Submit the signed Bid Form with your offer.") -> RawUnit:
    return RawUnit(locator_type="PAGE", locator="p.1", text=text, readable=True)


def test_valid_candidate_is_accepted_and_tagged_with_its_own_extraction_id() -> None:
    llm = _StubLLM(
        [
            {
                "candidates": [
                    {
                        "title": "Signed Bid Form",
                        "category": "FORM",
                        "criticality": "REQUIRED",
                        "condition_text": None,
                        "due_date": None,
                        "requested_format": None,
                        "requested_recipient": None,
                        "proposed_owner": None,
                    }
                ]
            }
        ]
    )
    candidates, rejected = propose_candidates(llm, [("DDX-1", _unit())])
    assert rejected == 0
    assert len(candidates) == 1
    assert candidates[0].extraction_id == "DDX-1"
    assert candidates[0].title == "Signed Bid Form"


def test_malformed_response_is_rejected_not_silently_dropped() -> None:
    llm = _StubLLM([{"not_candidates": []}])
    candidates, rejected = propose_candidates(llm, [("DDX-1", _unit())])
    assert candidates == []
    assert rejected == 1


def test_unparseable_json_is_invalid_output_not_transport_failure() -> None:
    class _InvalidJsonLLM:
        def complete_json(self, **kwargs):
            raise json.JSONDecodeError("synthetic invalid reply", "not json", 0)

    result = analyse_units(_InvalidJsonLLM(), [("DDX-1", _unit())])
    assert result.invalid_count == 1
    assert result.outcomes[0].outcome is PassageOutcomeKind.INVALID_OUTPUT


def test_invalid_schema_candidate_is_rejected() -> None:
    llm = _StubLLM([{"candidates": [{"title": "", "category": "FORM", "criticality": "REQUIRED"}]}])
    candidates, rejected = propose_candidates(llm, [("DDX-1", _unit())])
    assert candidates == []
    assert rejected == 1


def test_invalid_due_date_is_rejected_never_guessed() -> None:
    llm = _StubLLM(
        [
            {
                "candidates": [
                    {
                        "title": "Pricing Schedule",
                        "category": "FORM",
                        "criticality": "REQUIRED",
                        "due_date": "next Friday",
                    }
                ]
            }
        ]
    )
    candidates, rejected = propose_candidates(llm, [("DDX-1", _unit())])
    assert candidates == []
    assert rejected == 1


def test_unreadable_units_are_never_sent_to_the_llm() -> None:
    llm = _StubLLM([])
    unreadable = RawUnit(
        locator_type="PAGE", locator="p.2", text="", readable=False, failure_reason="scanned"
    )
    candidates, rejected = propose_candidates(llm, [("DDX-2", unreadable)])
    assert candidates == []
    assert rejected == 0
    assert llm.calls == 0


def test_llm_transport_exception_is_not_counted_as_rejected_output() -> None:
    class _RaisingLLM:
        def complete_json(self, **kwargs):
            raise RuntimeError("connection lost")

    candidates, rejected = propose_candidates(_RaisingLLM(), [("DDX-1", _unit())])
    assert candidates == []
    assert rejected == 0

    result = analyse_units(_RaisingLLM(), [("DDX-1", _unit())])
    assert result.outcomes[0].outcome is PassageOutcomeKind.MODEL_FAILURE
    assert result.outcomes[0].safe_reason == "RuntimeError: connection lost"


def test_truncated_reply_retries_once_with_larger_allowance() -> None:
    llm = _StructuredStubLLM(
        [
            StructuredReply(content="", finish_reason="length"),
            StructuredReply(content='{"candidates": []}', finish_reason="stop"),
        ]
    )

    result = analyse_units(llm, [("DDX-1", _unit())])

    assert result.outcomes[0].outcome is PassageOutcomeKind.ANALYSED
    assert len(llm.calls) == 2
    assert llm.calls[0]["max_tokens"] == DISCOVERY_MAX_TOKENS
    assert llm.calls[1]["max_tokens"] == DISCOVERY_RETRY_MAX_TOKENS
    assert llm.calls[0]["extra_body"]["chat_template_kwargs"] == {  # type: ignore[index]
        "enable_thinking": False
    }
    assert llm.calls[1]["extra_body"]["chat_template_kwargs"] == {  # type: ignore[index]
        "enable_thinking": False
    }
    assert CORRECTIVE_INSTRUCTION in str(llm.calls[1]["system_prompt"])


def test_two_truncated_replies_are_invalid_after_one_retry() -> None:
    llm = _StructuredStubLLM(
        [
            StructuredReply(content="", finish_reason="length"),
            StructuredReply(content="", finish_reason="length"),
        ]
    )

    result = analyse_units(llm, [("DDX-1", _unit())])

    outcome = result.outcomes[0]
    assert outcome.outcome is PassageOutcomeKind.INVALID_OUTPUT
    assert is_truncated_reason(outcome.safe_reason)
    assert outcome.safe_reason is not None
    assert outcome.safe_reason.endswith("(after one retry)")
    assert len(llm.calls) == 2


def test_two_invalid_json_replies_are_invalid_after_one_retry() -> None:
    llm = _StructuredStubLLM(
        [
            StructuredReply(content="not json", finish_reason="stop"),
            StructuredReply(content="still not json", finish_reason="stop"),
        ]
    )

    result = analyse_units(llm, [("DDX-1", _unit())])

    outcome = result.outcomes[0]
    assert outcome.outcome is PassageOutcomeKind.INVALID_OUTPUT
    assert outcome.safe_reason is not None
    assert outcome.safe_reason.startswith(INVALID_REASON_PREFIX)
    assert len(llm.calls) == 2
    assert llm.calls[1]["max_tokens"] == DISCOVERY_MAX_TOKENS


def test_valid_empty_candidate_list_is_analysed_without_retry() -> None:
    llm = _StructuredStubLLM([StructuredReply(content='{"candidates": []}', finish_reason="stop")])

    result = analyse_units(llm, [("DDX-1", _unit())])

    assert result.outcomes[0].outcome is PassageOutcomeKind.ANALYSED
    assert result.outcomes[0].candidate_count == 0
    assert len(llm.calls) == 1


def test_structured_transport_failure_is_not_retried() -> None:
    llm = _StructuredStubLLM([ConnectionError("connection lost")])

    result = analyse_units(llm, [("DDX-1", _unit())])

    assert result.outcomes[0].outcome is PassageOutcomeKind.MODEL_FAILURE
    assert result.outcomes[0].transport_failure is True
    assert len(llm.calls) == 1


def test_transport_failure_on_corrective_retry_is_model_failure() -> None:
    llm = _StructuredStubLLM(
        [
            StructuredReply(content="not json", finish_reason="stop"),
            ConnectionError("connection lost"),
        ]
    )

    result = analyse_units(llm, [("DDX-1", _unit())])

    assert result.outcomes[0].outcome is PassageOutcomeKind.MODEL_FAILURE
    assert result.outcomes[0].transport_failure is True
    assert len(llm.calls) == 2


def test_legacy_complete_json_client_still_yields_analysed() -> None:
    llm = _StubLLM([{"candidates": []}])

    result = analyse_units(llm, [("DDX-1", _unit())])

    assert result.outcomes[0].outcome is PassageOutcomeKind.ANALYSED
    assert llm.calls == 1
