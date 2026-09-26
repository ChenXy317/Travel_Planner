"""按已保存的行程行填写六节计划。不接受模型正文。"""

import json
import logging
import os

from sqlalchemy.orm import Session

from app.checks import cents_to_yuan, format_km
from app.config import get_settings
from app.db import get_engine
from app.models import Itinerary

log = logging.getLogger("app.documents")

NOTE = (
    "路程来自 OSRM 演示服务估算，不保证可用，也不是购票。天气来自 Open-Meteo，须署名，许可为 CC BY 4.0。"
    "地点来自 OpenStreetMap 贡献者，经由 Overpass 查询。开放时间和价格只抄搜索摘要；"
    "摘要里没有的价格没有用城市均价补上。本助手不订机票、火车票、酒店或门票。搜索由 Brave Search 提供。"
)


def _mode_label(mode: str) -> str:
    return "驾车" if mode == "driving" else "步行"


def render_plan(doc: dict) -> str:
    cities = "、".join(doc["cities"])
    yuan = doc["total_known_yuan"]
    lines = [
        f"# {doc['title']}",
        f"{doc['start_date']} 至 {doc['end_date']} · {doc['people']} 人",
        "",
        "## 行程概要",
        f"{cities}，共 {len(doc['days'])} 天。已知费用 {yuan} 元。",
        "",
        "## 每日安排",
    ]
    for day in doc["days"]:
        lines.append(f"### {day['date'][5:]} {day['weather_line']}")
        stops = day.get("stops") or []
        legs = day.get("legs") or []
        for index, stop in enumerate(stops):
            lines.append(f"- {stop['period']} {stop['name']}（{stop['category']}）")
            if index < len(legs):
                leg = legs[index]
                lines.append(f"- 前往 {leg['to_name']}，{_mode_label(leg['mode'])}约 {leg['minutes']} 分钟")
    lines.extend(["", "## 交通"])
    for day in doc["days"]:
        for leg in day.get("legs") or []:
            lines.append(
                f"- {leg['from_name']} → {leg['to_name']}：{_mode_label(leg['mode'])} "
                f"{format_km(leg['kilometers'])} 公里，约 {leg['minutes']} 分钟（OSRM 估算）"
            )
    lines.extend(["", "## 费用", f"已知合计 {yuan} 元"])
    for item in doc["budget_items"]:
        if item.get("source") == "user" or not item.get("source_url"):
            lines.append(f"- {item['name']} {item['price_yuan']} 元 × {item['quantity']}（用户提供）")
        else:
            lines.append(
                f"- {item['name']} {item['price_yuan']} 元 × {item['quantity']}（来源：{item['source_url']}）"
            )
    for item in doc["unknown_items"]:
        lines.append(f"未计入：{item['name']}（搜索摘要中没有价格）")
    lines.extend(["", "## 信息来源"])
    for source in doc["sources"]:
        lines.append(f"- {source['title']} — {source['url']}")
    lines.extend(["", "## 说明", NOTE, ""])
    return "\n".join(lines)


def preview_of(text: str) -> str:
    return "\n".join(text.splitlines()[:20])[:800]


def _document(row: Itinerary) -> dict:
    return {
        "title": row.title,
        "start_date": row.start_date,
        "end_date": row.end_date,
        "people": row.people,
        "cities": json.loads(row.cities_json),
        "days": json.loads(row.days_json),
        "total_known_yuan": cents_to_yuan(row.total_known_cents),
        "budget_items": json.loads(row.budget_items_json),
        "unknown_items": json.loads(row.unknown_json),
        "sources": json.loads(row.sources_json),
    }


def write_plan(itinerary_id: int) -> dict:
    relative = f"backend/data/plans/{itinerary_id}.md"
    with Session(get_engine()) as session:
        row = session.get(Itinerary, itinerary_id)
        if row is None:
            return {"ok": False, "error": "行程不存在"}
        text = render_plan(_document(row))
        directory = get_settings().plans_dir
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{itinerary_id}.md"
        temporary = directory / f"{itinerary_id}.md.tmp"
        try:
            temporary.write_text(text, encoding="utf-8")
            os.replace(temporary, path)
        except Exception:
            log.exception("文档写入失败")
            temporary.unlink(missing_ok=True)
            return {"ok": False, "error": "文档写入失败"}
        try:
            row.document_path = relative
            session.commit()
        except Exception:
            log.exception("文档写入失败")
            path.unlink(missing_ok=True)
            return {"ok": False, "error": "文档写入失败"}
    return {"ok": True, "path": relative, "preview": preview_of(text), "itinerary_id": itinerary_id}
