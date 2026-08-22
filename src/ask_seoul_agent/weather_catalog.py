"""Canonical scope and deterministic routing for served weather products."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, TypeGuard

WeatherProductId = Literal[
    "weather_place_current_outlook",
    "weather_place_forecast_change_daily",
    "weather_place_precipitation_window",
    "weather_place_risk_window",
]


@dataclass(frozen=True, slots=True)
class WeatherProduct:
    product_id: WeatherProductId
    title: str
    question: str


WEATHER_PRODUCTS: tuple[WeatherProduct, ...] = (
    WeatherProduct(
        product_id="weather_place_current_outlook",
        title="장소별 현재 날씨 전망",
        question="지금 서울 주요 장소들의 기상 상태는 대체로 어떻습니까?",
    ),
    WeatherProduct(
        product_id="weather_place_forecast_change_daily",
        title="장소별 일간 예보 변화",
        question="서울 주요 장소의 오늘 예보는 직전 KMA 발표보다 어떻게 달라졌습니까?",
    ),
    WeatherProduct(
        product_id="weather_place_precipitation_window",
        title="장소별 강수 예상 시간대",
        question="서울 주요 장소 중 비나 눈이 연속으로 예보된 시간대는 언제입니까?",
    ),
    WeatherProduct(
        product_id="weather_place_risk_window",
        title="장소별 기상 위험 예상 시간대",
        question="서울 주요 장소 중 방문·이동 주의가 필요할 수 있는 예보 시간과 근거는 무엇입니까?",
    ),
)

WEATHER_PRODUCT_IDS: tuple[WeatherProductId, ...] = tuple(
    product.product_id for product in WEATHER_PRODUCTS
)
WEATHER_PRODUCT_ID_SET = frozenset(WEATHER_PRODUCT_IDS)
WEATHER_PRODUCT_BY_ID = MappingProxyType(
    {product.product_id: product for product in WEATHER_PRODUCTS}
)

_RISK_TERMS = (
    "위험",
    "주의",
    "폭염",
    "한파",
    "호우",
    "대설",
    "강풍",
    "risk",
    "heat",
    "cold",
    "wind",
)
_PRECIPITATION_TERMS = (
    "강수",
    "비나눈",
    "비",
    "눈",
    "우산",
    "precipitation",
    "rain",
    "snow",
)
_CHANGE_TERMS = (
    "직전",
    "변화",
    "변경",
    "바뀌",
    "달라",
    "비교",
    "change",
    "changed",
    "revision",
    "previous",
)
_CURRENT_TERMS = (
    "현재",
    "지금",
    "오늘날씨",
    "날씨상태",
    "전망",
    "기온",
    "온도",
    "체감온도",
    "습도",
    "current",
    "outlook",
    "temperature",
    "humidity",
)
_WEATHER_TERMS = ("날씨", "기상", "예보", "weather", "forecast", "kma")
_OUT_OF_SCOPE_TERMS = (
    "지하철",
    "버스",
    "교통",
    "주차",
    "환승",
    "노선",
    "문화",
    "행사",
    "인구",
    "혼잡",
    "상권",
    "transit",
    "traffic",
    "parking",
    "culture",
    "population",
)


def is_weather_product_id(product_id: object) -> TypeGuard[WeatherProductId]:
    return isinstance(product_id, str) and product_id in WEATHER_PRODUCT_ID_SET


def filter_weather_products(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return one bounded-order entry for each canonical in-scope product."""

    by_id = {
        product_id: dict(product)
        for product in products
        if is_weather_product_id(product_id := product.get("product_id"))
    }
    return [by_id[product_id] for product_id in WEATHER_PRODUCT_IDS if product_id in by_id]


def is_explicitly_out_of_scope(query: str) -> bool:
    normalized = "".join(query.casefold().split())
    return any(term in normalized for term in _OUT_OF_SCOPE_TERMS)


def select_weather_product_id(
    query: str,
    *,
    available_product_ids: Collection[str] | None = None,
) -> WeatherProductId | None:
    """Select a product deterministically from a Korean or English weather intent."""

    normalized = "".join(query.casefold().split())
    selected: WeatherProductId | None = None

    for product_id in WEATHER_PRODUCT_IDS:
        if product_id in normalized:
            selected = product_id
            break
    if selected is None and is_explicitly_out_of_scope(query):
        return None
    if selected is None and any(term in normalized for term in _RISK_TERMS):
        selected = "weather_place_risk_window"
    elif selected is None and any(term in normalized for term in _CHANGE_TERMS):
        selected = "weather_place_forecast_change_daily"
    elif selected is None and any(term in normalized for term in _PRECIPITATION_TERMS):
        selected = "weather_place_precipitation_window"
    elif selected is None and any(term in normalized for term in _CURRENT_TERMS):
        selected = "weather_place_current_outlook"
    elif selected is None and any(term in normalized for term in _WEATHER_TERMS):
        selected = "weather_place_current_outlook"

    if selected is None:
        return None
    if available_product_ids is not None and selected not in available_product_ids:
        return None
    return selected


def weather_product_title(product_id: str) -> str:
    if not is_weather_product_id(product_id):
        return product_id
    return WEATHER_PRODUCT_BY_ID[product_id].title
