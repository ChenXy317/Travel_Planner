from sqlalchemy import Integer, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tool_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_calls_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, index=True)


class CacheRow:
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cache_key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    fetched_at: Mapped[str] = mapped_column(Text, nullable=False)


class GeoCache(CacheRow, Base):
    __tablename__ = "geo_cache"


class WeatherCache(CacheRow, Base):
    __tablename__ = "weather_cache"


class RouteCache(CacheRow, Base):
    __tablename__ = "route_cache"


class SearchCache(CacheRow, Base):
    __tablename__ = "search_cache"


class SessionState(Base):
    """单行会话事实，id 恒为 1。搜索次数不在这张表里。"""

    __tablename__ = "session_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_budget_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_budget_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    seen_urls_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    points_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    routes_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    weather_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class Itinerary(Base):
    __tablename__ = "itineraries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    cities_json: Mapped[str] = mapped_column(Text, nullable=False)
    people: Mapped[int] = mapped_column(Integer, nullable=False)
    start_date: Mapped[str] = mapped_column(Text, nullable=False)
    end_date: Mapped[str] = mapped_column(Text, nullable=False)
    days_json: Mapped[str] = mapped_column(Text, nullable=False)
    total_known_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    budget_items_json: Mapped[str] = mapped_column(Text, nullable=False)
    unknown_json: Mapped[str] = mapped_column(Text, nullable=False)
    sources_json: Mapped[str] = mapped_column(Text, nullable=False)
    document_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, index=True)
