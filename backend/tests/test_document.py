import json

from sqlalchemy.orm import Session

from app.checks import cents_to_yuan, weather_phrase
from app.config import get_settings
from app.db import get_engine, now_iso
from app.documents import write_plan
from app.models import Itinerary


def test_document_uses_saved_rows(client):
    line = weather_phrase(0, 15.2, 22.4)
    days = [
        {
            "date": "2026-09-26",
            "weather_line": line,
            "stops": [
                {"period": "上午", "name": "青岛海滨", "category": "景点"},
                {"period": "下午", "name": "青岛市博物馆", "category": "博物馆"},
            ],
            "legs": [
                {
                    "from_name": "青岛海滨",
                    "to_name": "青岛市博物馆",
                    "mode": "driving",
                    "minutes": 10,
                    "kilometers": 2.5,
                }
            ],
        }
    ]
    with Session(get_engine()) as session:
        session.add(
            Itinerary(
                title="青岛一日",
                cities_json=json.dumps(["青岛"], ensure_ascii=False),
                people=2,
                start_date="2026-09-26",
                end_date="2026-09-26",
                days_json=json.dumps(days, ensure_ascii=False),
                total_known_cents=80000,
                budget_items_json=json.dumps(
                    [
                        {
                            "name": "酒店",
                            "price_yuan": "400.00",
                            "quantity": 2,
                            "source": "user",
                            "source_url": None,
                        }
                    ],
                    ensure_ascii=False,
                ),
                unknown_json=json.dumps([{"name": "门票", "reason": "未提供价格"}], ensure_ascii=False),
                sources_json=json.dumps(
                    [{"title": "青岛市博物馆", "url": "https://example.org/museum"}],
                    ensure_ascii=False,
                ),
                document_path=None,
                created_at=now_iso(),
            )
        )
        session.commit()
    result = write_plan(1)
    assert result["ok"] is True
    text = (get_settings().plans_dir / "1.md").read_text(encoding="utf-8")
    for heading in ("行程概要", "每日安排", "交通", "费用", "信息来源", "说明"):
        assert f"## {heading}" in text
    assert "青岛海滨" in text
    assert "青岛市博物馆" in text
    assert cents_to_yuan(80000) in text
    assert "未计入：门票（搜索摘要中没有价格）" in text
    assert "https://example.org/museum" in text
    assert "已知合计 800.00 元" in text


def test_missing_itinerary_does_not_create_a_file(client):
    result = write_plan(9)
    assert result == {"ok": False, "error": "行程不存在"}
    assert list(get_settings().plans_dir.glob("*.md")) == []
