from __future__ import annotations

from typing import Any

import pytest

WEATHER_PRODUCT_IDS = [
    "weather_place_current_outlook",
    "weather_place_forecast_change_daily",
    "weather_place_precipitation_window",
    "weather_place_risk_window",
]


class MixedAskSeoulClient:
    def __init__(self, products: list[dict[str, Any]]) -> None:
        self.products = products
        self.search_calls: list[str] = []
        self.preview_calls: list[str] = []

    async def search_products(self, query: str) -> list[dict[str, Any]]:
        self.search_calls.append(query)
        return self.products

    async def preview_product(self, product_id: str) -> dict[str, Any]:
        self.preview_calls.append(product_id)
        return {"product_id": product_id, "rows": [{"place_name": "강남구"}]}


class FailingCurrentOutlookClient(MixedAskSeoulClient):
    async def preview_product(self, product_id: str) -> dict[str, Any]:
        from ask_seoul_agent.clients.ask_seoul import UpstreamQualityError

        self.preview_calls.append(product_id)
        if product_id == "weather_place_current_outlook":
            raise UpstreamQualityError(
                "product_not_ready",
                code="upstream_quality_error",
                retryable=False,
                status_code=503,
            )
        return await super().preview_product(product_id)


def _product(product_id: str, title: str) -> dict[str, Any]:
    return {
        "product_id": product_id,
        "title": title,
        "freshness": "2026-08-22T00:00:00+09:00",
        "usage_patterns": [{"sql": "SELECT * FROM unrelated_table"}],
        "columns": [{"name": "wide_internal_column"}],
    }


@pytest.mark.anyio
async def test_search_products_exposes_only_four_served_weather_products() -> None:
    from ask_seoul_agent.tools import ToolContext, ToolRegistry

    registry = ToolRegistry(
        ask_seoul=MixedAskSeoulClient(
            [
                _product("transit_route_current", "Transit route current"),
                _product("weather_place_current_outlook", "Weather current outlook"),
                _product("culture_event_calendar", "Culture event calendar"),
                _product("weather_place_forecast_change_daily", "Weather forecast change"),
                _product("weather_place_precipitation_window", "Weather precipitation window"),
                _product("weather_place_risk_window", "Weather risk window"),
            ]
        )
    )

    result = await registry.execute(
        "search_products",
        {"query": "오늘 서울 날씨 위험과 강수 정보를 알려줘"},
        context=ToolContext(),
        trace_id="trace-weather",
    )

    assert [item["product_id"] for item in result.content["candidates"]] == WEATHER_PRODUCT_IDS
    serialized = str(result.content["candidates"])
    assert "usage_patterns" not in serialized
    assert "SELECT " not in serialized
    assert "transit" not in serialized.casefold()


@pytest.mark.anyio
async def test_preview_product_rejects_non_weather_product_without_upstream_preview() -> None:
    from ask_seoul_agent.tools import ToolContext, ToolExecutionError, ToolRegistry

    fake_client = MixedAskSeoulClient([])
    registry = ToolRegistry(ask_seoul=fake_client)
    context = ToolContext(
        discovered_products={
            "transit_route_current": _product("transit_route_current", "Transit route current")
        }
    )

    with pytest.raises(ToolExecutionError):
        await registry.execute(
            "preview_product",
            {"product_id": "transit_route_current"},
            context=context,
            trace_id="trace-weather",
        )

    assert fake_client.preview_calls == []


@pytest.mark.anyio
async def test_out_of_scope_transit_risk_question_returns_no_candidates_or_upstream_call() -> None:
    from ask_seoul_agent.tools import ToolContext, ToolRegistry

    fake_client = MixedAskSeoulClient(
        [_product("weather_place_risk_window", "Weather risk window")]
    )
    registry = ToolRegistry(ask_seoul=fake_client)

    result = await registry.execute(
        "search_products",
        {"query": "서울 지하철 교통 위험 정보를 알려줘"},
        context=ToolContext(),
        trace_id="trace-weather",
    )

    assert result.content["candidates"] == []
    assert fake_client.search_calls == []


@pytest.mark.anyio
async def test_common_temperature_question_is_not_suppressed_before_catalog_lookup() -> None:
    from ask_seoul_agent.tools import ToolContext, ToolRegistry

    fake_client = MixedAskSeoulClient(
        [_product("weather_place_current_outlook", "Current outlook")]
    )
    registry = ToolRegistry(ask_seoul=fake_client)

    result = await registry.execute(
        "search_products",
        {"query": "서울 기온과 습도 알려줘"},
        context=ToolContext(),
        trace_id="trace-weather",
    )

    assert [item["product_id"] for item in result.content["candidates"]] == [
        "weather_place_current_outlook"
    ]
    assert fake_client.search_calls == ["서울 기온과 습도 알려줘"]


@pytest.mark.anyio
async def test_out_of_scope_question_returns_no_candidates_without_catalog_call() -> None:
    from ask_seoul_agent.tools import ToolContext, ToolRegistry

    fake_client = MixedAskSeoulClient([_product("weather_place_risk_window", "Weather risk")])
    registry = ToolRegistry(ask_seoul=fake_client)

    result = await registry.execute(
        "search_products",
        {"query": "서울 지하철 막차 시간 알려줘"},
        context=ToolContext(),
        trace_id="trace-weather",
    )

    assert result.content["candidates"] == []
    assert fake_client.preview_calls == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("question", "expected_product_id"),
    [
        ("서울 강남구 지금 날씨 상태 알려줘", "weather_place_current_outlook"),
        ("서울 기온과 습도 알려줘", "weather_place_current_outlook"),
        ("직전 발표 대비 날씨 예보 변화를 알려줘", "weather_place_forecast_change_daily"),
        ("오늘 비나 눈이 올 시간대가 언제야?", "weather_place_precipitation_window"),
        ("폭염이나 호우 위험 시간대를 알려줘", "weather_place_risk_window"),
    ],
)
async def test_demo_provider_routes_korean_weather_questions_to_matching_product(
    question: str, expected_product_id: str
) -> None:
    from ask_seoul_agent.models import ToolResult
    from ask_seoul_agent.providers.demo import DemoProvider

    provider = DemoProvider()
    turn = await provider.complete(
        messages=[
            {"role": "user", "content": question},
            {
                "role": "tool",
                "content": ToolResult(
                    call_id="search_1",
                    tool="search_products",
                    status="ok",
                    content={
                        "candidates": [
                            _product(product_id, product_id)
                            for product_id in WEATHER_PRODUCT_IDS
                        ]
                    },
                ),
            },
        ],
        tools=provider.tool_schemas(),
        timeout_s=1,
    )

    assert turn.tool_calls[0].arguments["product_id"] == expected_product_id


@pytest.mark.anyio
async def test_demo_provider_returns_korean_final_answer_for_preview_evidence() -> None:
    from ask_seoul_agent.models import ToolResult
    from ask_seoul_agent.providers.demo import DemoProvider

    provider = DemoProvider()
    turn = await provider.complete(
        messages=[
            {"role": "user", "content": "폭염 위험 시간대를 알려줘"},
            {
                "role": "tool",
                "content": ToolResult(
                    call_id="preview_1",
                    tool="preview_product",
                    status="ok",
                    content={
                        "product_id": "weather_place_risk_window",
                        "rows": [
                            {
                                "place_name": "강남구",
                                "risk_labels": "폭염",
                                "forecast_at": "2026-08-22 15:00:00",
                            }
                        ],
                    },
                ),
            },
        ],
        tools=provider.tool_schemas(),
        timeout_s=1,
    )

    assert "미리보기" in (turn.text or "")
    assert "Demo provider" not in (turn.text or "")


@pytest.mark.anyio
async def test_demo_provider_hides_internal_product_id_and_localizes_change_state() -> None:
    from ask_seoul_agent.models import ToolResult
    from ask_seoul_agent.providers.demo import DemoProvider

    provider = DemoProvider()
    turn = await provider.complete(
        messages=[
            {"role": "user", "content": "직전 발표 대비 예보 변화를 알려줘"},
            {
                "role": "tool",
                "content": ToolResult(
                    call_id="preview_1",
                    tool="preview_product",
                    status="ok",
                    content={
                        "product_id": "weather_place_forecast_change_daily",
                        "rows": [
                            {
                                "place_name": "청운효자동",
                                "forecast_date": "2026-08-23",
                                "change_state": "unchanged",
                            }
                        ],
                    },
                ),
            },
        ],
        tools=provider.tool_schemas(),
        timeout_s=1,
    )

    answer = turn.text or ""
    assert "장소별 일간 예보 변화" in answer
    assert "변화 없음" in answer
    assert "샘플 요약:\n- 청운효자동" in answer
    assert "weather_place_forecast_change_daily" not in answer
    assert "unchanged" not in answer


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("question", "expected_product_id"),
    [
        ("서울 주요 장소의 현재 날씨 전망을 알려줘", "weather_place_current_outlook"),
        ("직전 발표 대비 예보가 어떻게 달라졌어?", "weather_place_forecast_change_daily"),
        ("비나 눈이 이어지는 시간대는 언제야?", "weather_place_precipitation_window"),
        ("폭염이나 호우 위험 시간대를 알려줘", "weather_place_risk_window"),
    ],
)
async def test_demo_agent_e2e_uses_expected_weather_product(
    question: str, expected_product_id: str
) -> None:
    from ask_seoul_agent.agent import AgentRunner
    from ask_seoul_agent.models import EvidenceStatus
    from ask_seoul_agent.providers.demo import DemoProvider
    from ask_seoul_agent.tools import ToolRegistry

    client = MixedAskSeoulClient(
        [_product(product_id, product_id) for product_id in WEATHER_PRODUCT_IDS]
    )
    runner = AgentRunner(
        provider=DemoProvider(),
        tools=ToolRegistry(ask_seoul=client),
        provider_name="demo",
    )

    events = [event async for event in runner.stream(question, trace_id="trace-weather")]
    tool_calls = [event for event in events if event["event"] == "tool.call"]
    final = next(event for event in events if event["event"] == "assistant.final")

    assert [event["tool_name"] for event in tool_calls] == [
        "search_products",
        "preview_product",
    ]
    assert tool_calls[1]["input"]["product_id"] == expected_product_id
    assert client.preview_calls == [expected_product_id]
    assert final["envelope"].evidence_status == EvidenceStatus.GROUNDED_PREVIEW
    assert final["envelope"].evidence[0].product_id == expected_product_id


@pytest.mark.anyio
async def test_current_outlook_quality_gate_does_not_fall_back_to_other_product() -> None:
    from ask_seoul_agent.agent import AgentRunner
    from ask_seoul_agent.models import EvidenceStatus
    from ask_seoul_agent.providers.demo import DemoProvider
    from ask_seoul_agent.tools import ToolRegistry

    client = FailingCurrentOutlookClient(
        [_product(product_id, product_id) for product_id in WEATHER_PRODUCT_IDS]
    )
    runner = AgentRunner(
        provider=DemoProvider(),
        tools=ToolRegistry(ask_seoul=client),
        provider_name="demo",
    )

    events = [
        event
        async for event in runner.stream(
            "서울 주요 장소의 현재 날씨 전망을 알려줘", trace_id="trace-weather"
        )
    ]
    final = next(event for event in events if event["event"] == "assistant.final")

    assert client.preview_calls == ["weather_place_current_outlook"]
    assert final["envelope"].evidence_status == EvidenceStatus.INSUFFICIENT_DATA
    assert final["envelope"].evidence == []
