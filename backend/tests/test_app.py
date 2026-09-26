from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.db import get_engine
from app.models import SessionState


def test_health(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_tables_and_session_row(client):
    engine = get_engine()
    names = set(inspect(engine).get_table_names())
    assert {
        "messages",
        "geo_cache",
        "weather_cache",
        "route_cache",
        "search_cache",
        "session_state",
        "itineraries",
    } <= names
    with Session(engine) as session:
        row = session.get(SessionState, 1)
        assert row is not None
        assert row.seen_urls_json == "[]"


def test_empty_lists(client):
    assert client.get("/api/v1/messages").json() == {"messages": []}
    assert client.get("/api/v1/itineraries").json() == {"itineraries": []}


def test_missing_itinerary(client):
    detail = client.get("/api/v1/itineraries/1")
    document = client.get("/api/v1/itineraries/1/document")
    assert detail.status_code == 404
    assert document.status_code == 404
    assert detail.json()["ok"] is False
