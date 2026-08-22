import pytest
from pydantic import ValidationError


def test_chat_request_rejects_blank_question() -> None:
    from ask_seoul_agent.models import ChatRequest

    with pytest.raises(ValidationError):
        ChatRequest(question="")


def test_chat_request_rejects_control_characters() -> None:
    from ask_seoul_agent.models import ChatRequest

    with pytest.raises(ValidationError):
        ChatRequest(question="show me\u0000weather risk")


def test_chat_request_rejects_question_over_500_characters() -> None:
    from ask_seoul_agent.models import ChatRequest

    with pytest.raises(ValidationError):
        ChatRequest(question="가" * 501)


def test_tool_call_rejects_invalid_product_id_argument() -> None:
    from ask_seoul_agent.models import ToolCall

    with pytest.raises(ValidationError):
        ToolCall(
            id="call_1",
            name="preview_product",
            arguments={"product_id": "../weather_place_risk_window"},
        )


def test_final_envelope_fails_closed_when_no_evidence_exists() -> None:
    from ask_seoul_agent.models import EvidenceStatus, FinalEnvelope

    envelope = FinalEnvelope.from_agent_state(
        trace_id="trace-1",
        provider="demo",
        answer_from_model="The model claims unsupported facts.",
        tool_results=[],
        usage=None,
        elapsed_ms=10,
    )

    assert envelope.evidence_status == EvidenceStatus.INSUFFICIENT_DATA
    assert "unsupported facts" not in envelope.answer
    assert envelope.evidence == []


def test_final_envelope_marks_preview_as_sample_only_evidence(preview_payload) -> None:
    from ask_seoul_agent.models import EvidenceStatus, FinalEnvelope, ToolResult

    result = ToolResult(
        call_id="call_1",
        tool="preview_product",
        status="ok",
        content=preview_payload,
        source="https://ask-seoul.kr/api/v1/preview/weather_place_risk_window",
    )
    envelope = FinalEnvelope.from_agent_state(
        trace_id="trace-1",
        provider="demo",
        answer_from_model="Gangnam has high risk in the preview sample.",
        tool_results=[result],
        usage=None,
        elapsed_ms=10,
    )

    assert envelope.evidence_status == EvidenceStatus.GROUNDED_PREVIEW
    assert "sample" in envelope.answer.lower() or "샘플" in envelope.answer
    assert envelope.evidence[0].sample_only is True
    assert envelope.evidence[0].row_count == 5


def test_final_envelope_fails_closed_for_empty_preview_rows() -> None:
    from ask_seoul_agent.models import EvidenceStatus, FinalEnvelope, ToolResult

    result = ToolResult(
        call_id="call_1",
        tool="preview_product",
        status="ok",
        content={"product_id": "weather_place_risk_window", "rows": []},
        source="https://ask-seoul.kr/api/v1/preview/weather_place_risk_window",
    )

    envelope = FinalEnvelope.from_agent_state(
        trace_id="trace-1",
        provider="demo",
        answer_from_model="The model claims a result despite an empty preview.",
        tool_results=[result],
        usage=None,
        elapsed_ms=10,
    )

    assert envelope.evidence_status == EvidenceStatus.INSUFFICIENT_DATA
    assert envelope.evidence == []
    assert "claims a result" not in envelope.answer
