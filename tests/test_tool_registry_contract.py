import pytest
from conftest import PRODUCT_ID, FakeAskSeoulClient


@pytest.mark.anyio
async def test_preview_requires_product_id_discovered_by_search(
    product_candidate, preview_payload
) -> None:
    from ask_seoul_agent.tools import ToolContext, ToolExecutionError, ToolRegistry

    registry = ToolRegistry(
        ask_seoul=FakeAskSeoulClient(product=product_candidate, preview=preview_payload)
    )
    context = ToolContext()

    with pytest.raises(ToolExecutionError):
        await registry.execute(
            "preview_product",
            {"product_id": PRODUCT_ID},
            context=context,
            trace_id="trace-1",
        )


@pytest.mark.anyio
async def test_search_then_preview_allows_discovered_product(
    product_candidate, preview_payload
) -> None:
    from ask_seoul_agent.tools import ToolContext, ToolRegistry

    registry = ToolRegistry(
        ask_seoul=FakeAskSeoulClient(product=product_candidate, preview=preview_payload)
    )
    context = ToolContext()

    search = await registry.execute(
        "search_products", {"query": "weather risk"}, context=context, trace_id="trace-1"
    )
    preview = await registry.execute(
        "preview_product", {"product_id": PRODUCT_ID}, context=context, trace_id="trace-1"
    )

    assert search.status == "ok"
    assert preview.status == "ok"
    assert preview.content["product_id"] == PRODUCT_ID
    assert len(preview.content["rows"]) == 5


@pytest.mark.anyio
async def test_unknown_tool_is_rejected_before_upstream_call(product_candidate) -> None:
    from ask_seoul_agent.tools import ToolContext, ToolExecutionError, ToolRegistry

    fake_client = FakeAskSeoulClient(product=product_candidate)
    registry = ToolRegistry(ask_seoul=fake_client)
    context = ToolContext()

    with pytest.raises(ToolExecutionError):
        await registry.execute(
            "query_product", {"sql": "select * from x"}, context=context, trace_id="trace-1"
        )

    assert fake_client.calls == []
