"""预算、搜索名额、坐标和行程保存的纯校验。"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
_today_override: date | None = None


def set_today(day: date | None) -> None:
    """测试里固定上海的今天。传入 None 时恢复系统日期。"""
    global _today_override
    _today_override = day


def shanghai_today() -> date:
    if _today_override is not None:
        return _today_override
    return datetime.now(SHANGHAI).date()


def cents_to_yuan(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    amount = abs(int(cents))
    return f"{sign}{amount // 100}.{amount % 100:02d}"


def quantize_coord(value) -> str:
    dec = Decimal(str(value)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    return format(dec, "f")


def point_key(latitude, longitude) -> tuple[str, str]:
    return (quantize_coord(latitude), quantize_coord(longitude))


def route_key(lat1, lon1, lat2, lon2, mode: str) -> tuple[str, str, str, str, str]:
    start = point_key(lat1, lon1)
    end = point_key(lat2, lon2)
    return (start[0], start[1], end[0], end[1], mode)


def round_half_up_int(value) -> int:
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def format_km(value) -> str:
    dec = Decimal(str(value)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return format(dec, "f")


def km_equal(left, right) -> bool:
    return format_km(left) == format_km(right)


def weather_label(code: int) -> str:
    if code == 0:
        return "晴"
    if code == 1:
        return "晴间多云"
    if code == 2:
        return "多云"
    if code == 3:
        return "阴"
    if code in {45, 48}:
        return "雾"
    if 51 <= code <= 67:
        return "雨"
    if 71 <= code <= 77:
        return "雪"
    if 80 <= code <= 82:
        return "阵雨"
    if 85 <= code <= 86:
        return "阵雪"
    if 95 <= code <= 99:
        return "雷暴"
    return "未知"


def weather_phrase(code: int, t_min, t_max) -> str:
    low = round_half_up_int(t_min)
    high = round_half_up_int(t_max)
    return f"{weather_label(code)} {low}\u2013{high}°C"


def parse_price(value) -> tuple[Decimal | None, str | None]:
    """把价格收成两位小数。None 表示未提供，不是格式错误。"""
    if value is None or value == "":
        return None, None
    if isinstance(value, bool):
        return None, "金额最多两位小数"
    try:
        if isinstance(value, Decimal):
            dec = value
        elif isinstance(value, int):
            dec = Decimal(value)
        else:
            dec = Decimal(str(value).strip())
    except Exception:
        return None, "金额最多两位小数"
    if dec < 0 or dec != dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP):
        return None, "金额最多两位小数"
    return dec, None


def yuan_to_cents(value) -> tuple[int | None, str | None]:
    dec, err = parse_price(value)
    if err or dec is None:
        return None, err or "总额与最近一次预算不一致"
    return int((dec * 100).to_integral_value()), None


@dataclass
class BudgetItem:
    name: str
    price_yuan: Decimal | int | str | None
    quantity: int
    source_url: str | None = None


@dataclass
class BudgetOutcome:
    ok: bool
    error: str | None = None
    total_known_cents: int = 0
    total_known_yuan: str = "0.00"
    people: int = 0
    nights: int = 0
    items: list = field(default_factory=list)
    unknown: list = field(default_factory=list)

    def snapshot(self) -> dict:
        return {
            "people": self.people,
            "nights": self.nights,
            "items": self.items,
            "unknown": self.unknown,
        }


def _as_mapping(item) -> dict:
    if isinstance(item, BudgetItem):
        return {
            "name": item.name,
            "price_yuan": item.price_yuan,
            "quantity": item.quantity,
            "source_url": item.source_url,
        }
    if hasattr(item, "model_dump"):
        return item.model_dump()
    return dict(item)


def estimate_budget_cents(people, nights, items, seen_urls: set[str]) -> BudgetOutcome:
    if isinstance(people, bool) or not isinstance(people, int) or not 1 <= people <= 30:
        return BudgetOutcome(False, "人数需在 1 到 30")
    if isinstance(nights, bool) or not isinstance(nights, int) or not 0 <= nights <= 14:
        return BudgetOutcome(False, "晚数需在 0 到 14")
    counted = []
    unknown = []
    total = 0
    for raw in items:
        item = _as_mapping(raw)
        name = str(item.get("name") or "").strip()
        if not name:
            return BudgetOutcome(False, "费用项名称不能为空")
        price, err = parse_price(item.get("price_yuan"))
        if err:
            return BudgetOutcome(False, err)
        quantity = item.get("quantity")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= 99:
            return BudgetOutcome(False, "数量需在 1 到 99")
        source_url = item.get("source_url")
        if source_url is not None:
            source_url = str(source_url).strip() or None
        if price is None:
            unknown.append({"name": name, "reason": "未提供价格"})
            continue
        if source_url is not None and source_url not in seen_urls:
            return BudgetOutcome(False, "source_url 不在本会话搜索结果中")
        cents = int((price * 100).to_integral_value()) * quantity
        total += cents
        counted.append(
            {
                "name": name,
                "price_yuan": format(price.quantize(Decimal("0.01")), "f"),
                "quantity": quantity,
                "source": "user" if source_url is None else "url",
                "source_url": source_url,
                "cents": cents,
            }
        )
    return BudgetOutcome(
        True,
        None,
        total,
        cents_to_yuan(total),
        people,
        nights,
        counted,
        unknown,
    )


def apply_budget(state, people, nights, items) -> dict:
    """成功时才覆盖 state 上的最近一次预算。"""
    outcome = estimate_budget_cents(people, nights, items, set(state.seen_urls))
    if not outcome.ok:
        return {"ok": False, "error": outcome.error}
    state.last_budget_cents = outcome.total_known_cents
    state.last_budget = outcome.snapshot()
    return {
        "ok": True,
        "total_known_cents": outcome.total_known_cents,
        "total_known_yuan": outcome.total_known_yuan,
        "items": outcome.items,
        "unknown": outcome.unknown,
    }


def consume_search_slot(state) -> str | None:
    """第 5 次直接拒绝，不增加计数。调用方在此之前不能打外部接口。"""
    if state.search_count >= 4:
        return "已达搜索次数"
    state.search_count += 1
    return None


def normalize_cities(cities) -> list[str]:
    found = []
    for city in cities or []:
        name = str(city).strip()
        if name and name not in found:
            found.append(name)
    return found


def _int_in(value, low, high) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and low <= value <= high


def validate_itinerary(payload: dict, state) -> str | None:
    title = str(payload.get("title") or "").strip()
    if not 1 <= len(title) <= 40:
        return "标题需为 1 到 40 字"
    cities = normalize_cities(payload.get("cities"))
    if not 1 <= len(cities) <= 2:
        return "城市需为 1 到 2 座"
    if not _int_in(payload.get("people"), 1, 30):
        return "人数需在 1 到 30"
    days = payload.get("days") or []
    if not 1 <= len(days) <= 5:
        return "天数需在 1 到 5"
    for day in days:
        stops = day.get("stops") or []
        if not 0 <= len(stops) <= 3:
            return "每天站点需在 0 到 3"
    if state.last_budget_cents is None:
        return "总额与最近一次预算不一致"
    cents, _err = yuan_to_cents(payload.get("total_known_yuan"))
    if cents is None or cents != state.last_budget_cents:
        return "总额与最近一次预算不一致"
    snapshot = state.last_budget or {}
    expected_unknown = {item["name"] for item in snapshot.get("unknown") or []}
    given_unknown = {str(name) for name in payload.get("unknown_items") or []}
    if given_unknown != expected_unknown:
        return "未知项与预算不一致"
    lines = state.weather_lines or {}
    for day in days:
        expected = lines.get(day.get("date"))
        if not expected or day.get("weather_line") != expected:
            return f"天气句与记录不一致，期望 {expected or '无'}"
        for leg in day.get("legs") or []:
            key = route_key(
                leg.get("from_latitude"),
                leg.get("from_longitude"),
                leg.get("to_latitude"),
                leg.get("to_longitude"),
                leg.get("mode"),
            )
            recorded = (state.routes or {}).get(key)
            if recorded is None or recorded[0] != leg.get("minutes") or not km_equal(recorded[1], leg.get("kilometers")):
                if recorded is None:
                    return "路程与记录不一致，期望 无 分钟、无 公里"
                return f"路程与记录不一致，期望 {recorded[0]} 分钟、{format_km(recorded[1])} 公里"
    for source in payload.get("sources") or []:
        url = source.get("url") if isinstance(source, dict) else None
        if not url or url not in state.seen_urls:
            return "来源链接不在本会话搜索结果中"
    return None
