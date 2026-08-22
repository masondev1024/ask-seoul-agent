import json
import sys
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
for candidate in (PROJECT_ROOT, SRC_ROOT):
    if candidate.exists():
        sys.path.insert(0, str(candidate))


PRODUCT_ID = "weather_place_risk_window"


@pytest.fixture
def product_candidate() -> dict[str, Any]:
    return {
        "product_id": PRODUCT_ID,
        "title": "Weather place risk window",
        "freshness": "2026-08-22T00:00:00+09:00",
        "matched_terms": ["weather", "risk"],
        "matched_patterns": ["weather_place"],
    }


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def preview_payload() -> dict[str, Any]:
    return {
        "product_id": PRODUCT_ID,
        "freshness": "2026-08-22T00:00:00+09:00",
        "rows": [
            {"place": "Gangnam", "risk": "high", "window": "14:00-16:00"},
            {"place": "Jongno", "risk": "medium", "window": "15:00-17:00"},
            {"place": "Mapo", "risk": "low", "window": "11:00-12:00"},
            {"place": "Yongsan", "risk": "medium", "window": "18:00-20:00"},
            {"place": "Songpa", "risk": "high", "window": "13:00-15:00"},
        ],
    }


class FakeAskSeoulClient:
    def __init__(
        self,
        *,
        product: dict[str, Any] | None = None,
        preview: dict[str, Any] | None = None,
        search_error: Exception | None = None,
        preview_error: Exception | None = None,
    ) -> None:
        self.product = product
        self.preview = preview
        self.search_error = search_error
        self.preview_error = preview_error
        self.calls: list[tuple[str, str]] = []

    async def search_products(self, query: str) -> list[dict[str, Any]]:
        self.calls.append(("search_products", query))
        if self.search_error:
            raise self.search_error
        return [self.product] if self.product else []

    async def preview_product(self, product_id: str) -> dict[str, Any]:
        self.calls.append(("preview_product", product_id))
        if self.preview_error:
            raise self.preview_error
        if self.preview is None:
            return {"product_id": product_id, "rows": []}
        return self.preview


def parse_sse_events(raw: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in raw.strip().split("\n\n"):
        event_name = None
        data = None
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip()
            if line.startswith("data:"):
                data = json.loads(line.split(":", 1)[1].strip())
        if data is not None:
            if event_name is not None:
                data.setdefault("event", event_name)
            events.append(data)
    return events
