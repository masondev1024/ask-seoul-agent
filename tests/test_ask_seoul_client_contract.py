import httpx
import pytest
from conftest import PRODUCT_ID


@pytest.mark.anyio
async def test_search_products_reads_catalog_and_returns_only_four_weather_products() -> None:
    from ask_seoul_agent.clients.ask_seoul import AskSeoulClient

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/v1/catalog"
        assert not request.url.params
        return httpx.Response(
            200,
            json={
                "products": [
                    {"product_id": "transit_route_current", "title": "transit"},
                    {"product_id": "weather_place_risk_window"},
                    {"product_id": "weather_place_current_outlook"},
                    {"product_id": "weather_place_precipitation_window"},
                    {"product_id": "weather_place_forecast_change_daily"},
                ]
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://ask-seoul.test"
    ) as http:
        client = AskSeoulClient(base_url="https://ask-seoul.test", http_client=http)
        results = await client.search_products("weather")

    assert [item["product_id"] for item in results] == [
        "weather_place_current_outlook",
        "weather_place_forecast_change_daily",
        "weather_place_precipitation_window",
        "weather_place_risk_window",
    ]


@pytest.mark.anyio
async def test_preview_product_rejects_malformed_upstream_schema() -> None:
    from ask_seoul_agent.clients.ask_seoul import AskSeoulClient, UpstreamSchemaError

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"rows": "not-a-list"})
        ),
        base_url="https://ask-seoul.test",
    ) as http:
        client = AskSeoulClient(base_url="https://ask-seoul.test", http_client=http)
        with pytest.raises(UpstreamSchemaError):
            await client.preview_product(PRODUCT_ID)


@pytest.mark.anyio
async def test_preview_product_surfaces_stale_503_as_upstream_quality_error() -> None:
    from ask_seoul_agent.clients.ask_seoul import AskSeoulClient, UpstreamQualityError

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(503, json={"error": "publication freshness gate failed"})
        ),
        base_url="https://ask-seoul.test",
    ) as http:
        client = AskSeoulClient(base_url="https://ask-seoul.test", http_client=http)
        with pytest.raises(UpstreamQualityError) as error:
            await client.preview_product(PRODUCT_ID)

    assert error.value.code == "upstream_quality_error"
    assert "품질" in str(error.value) or "최신성" in str(error.value)


@pytest.mark.anyio
async def test_preview_product_timeout_is_sanitized() -> None:
    from ask_seoul_agent.clients.ask_seoul import AskSeoulClient, UpstreamTimeoutError

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("token=secret should not leak", request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://ask-seoul.test"
    ) as http:
        client = AskSeoulClient(base_url="https://ask-seoul.test", http_client=http)
        with pytest.raises(UpstreamTimeoutError) as error:
            await client.preview_product(PRODUCT_ID)

    assert "secret" not in str(error.value)
    assert "timeout" in str(error.value).lower()


@pytest.mark.anyio
async def test_retryable_status_uses_bounded_retries_before_success() -> None:
    from ask_seoul_agent.clients.ask_seoul import AskSeoulClient

    attempts = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(429, headers={"retry-after": "0"}, json={"detail": "busy"})
        return httpx.Response(200, json={"products": [{"product_id": PRODUCT_ID}]})

    async def record_sleep(delay: float) -> None:
        sleeps.append(delay)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = AskSeoulClient(
            base_url="https://ask-seoul.test",
            http_client=http,
            sleeper=record_sleep,
            jitter=lambda delay: delay,
        )
        results = await client.search_products("weather")

    assert results[0]["product_id"] == PRODUCT_ID
    assert attempts == 3
    assert sleeps == [0.0, 0.0]


@pytest.mark.anyio
async def test_preview_carries_sanitized_upstream_request_id() -> None:
    from ask_seoul_agent.clients.ask_seoul import AskSeoulClient

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"x-request-id": "req_live_123"},
                json={"product_id": PRODUCT_ID, "rows": []},
            )
        )
    ) as http:
        client = AskSeoulClient(base_url="https://ask-seoul.test", http_client=http)
        preview = await client.preview_product(PRODUCT_ID)

    assert preview["request_id"] == "req_live_123"


@pytest.mark.anyio
async def test_preview_rejects_product_id_outside_agent_allowlist() -> None:
    from ask_seoul_agent.clients.ask_seoul import AskSeoulClient

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: None)) as http:
        client = AskSeoulClient(base_url="https://ask-seoul.test", http_client=http)
        with pytest.raises(ValueError):
            await client.preview_product("1-not_agent_safe")


@pytest.mark.anyio
async def test_preview_rejects_valid_non_weather_product_before_http_call() -> None:
    from ask_seoul_agent.clients.ask_seoul import AskSeoulClient

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"rows": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = AskSeoulClient(base_url="https://ask-seoul.test", http_client=http)
        with pytest.raises(ValueError):
            await client.preview_product("transit_route_current")

    assert calls == 0


@pytest.mark.anyio
async def test_preview_rejects_non_object_rows_as_schema_drift() -> None:
    from ask_seoul_agent.clients.ask_seoul import AskSeoulClient, UpstreamSchemaError

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"rows": [{"ok": True}, "bad-row"]})
        )
    ) as http:
        client = AskSeoulClient(base_url="https://ask-seoul.test", http_client=http)
        with pytest.raises(UpstreamSchemaError):
            await client.preview_product(PRODUCT_ID)


@pytest.mark.anyio
async def test_search_ignores_product_id_outside_agent_allowlist() -> None:
    from ask_seoul_agent.clients.ask_seoul import AskSeoulClient

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"products": [{"product_id": "../unsafe", "title": "unsafe"}]},
            )
        )
    ) as http:
        client = AskSeoulClient(base_url="https://ask-seoul.test", http_client=http)
        results = await client.search_products("weather")

    assert results == []


@pytest.mark.anyio
async def test_search_ignores_unrelated_catalog_schema_drift_but_keeps_weather() -> None:
    from ask_seoul_agent.clients.ask_seoul import AskSeoulClient

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "products": [
                        "malformed-transit-row",
                        {"product_id": "../unsafe", "title": "unrelated"},
                        {"title": "missing id"},
                        {"product_id": PRODUCT_ID},
                    ]
                },
            )
        )
    ) as http:
        client = AskSeoulClient(base_url="https://ask-seoul.test", http_client=http)
        results = await client.search_products("기상 위험")

    assert [item["product_id"] for item in results] == [PRODUCT_ID]
