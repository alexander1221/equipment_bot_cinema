"""Подключение к БД, создание таблиц и начальное наполнение."""

from __future__ import annotations

import secrets
from datetime import datetime
from pathlib import Path

from sqlalchemy import event, func, inspect, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from .models import Base, Equipment, Role, Staff
from .seed_data import EQUIPMENT, TEACHERS


def make_engine(url: str) -> AsyncEngine:
    if url.startswith("sqlite"):
        # Каталог для файла БД должен существовать до подключения
        path = url.split(":///", 1)[-1]
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(url)
    if url.startswith("sqlite"):

        @event.listens_for(engine.sync_engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

    return engine


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


def new_token() -> str:
    return secrets.token_urlsafe(12)


# Столбцы, добавленные после 1.0.0: (таблица, столбец, SQL-определение).
# create_all не меняет существующие таблицы, поэтому досоздаём их сами.
# Старые студенты получают status='pending' — их нужно подтвердить заново,
# так как их курс никто не проверял.
_ADDED_COLUMNS = [
    ("students", "group_name", "VARCHAR(50) NOT NULL DEFAULT ''"),
    ("students", "status", "VARCHAR(20) NOT NULL DEFAULT 'pending'"),
    ("students", "verified_at", "DATETIME"),
    ("students", "verified_by", "VARCHAR(200) NOT NULL DEFAULT ''"),
]


def _add_missing_columns(conn) -> list[str]:  # noqa: ANN001
    insp = inspect(conn)
    added = []
    for table, column, ddl in _ADDED_COLUMNS:
        if column not in {c["name"] for c in insp.get_columns(table)}:
            if conn.dialect.name == "postgresql":
                ddl = ddl.replace("DATETIME", "TIMESTAMP")
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            added.append(f"{table}.{column}")
    return added


async def init_db(engine: AsyncEngine, seed: bool = True) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)
    if not seed:
        return
    sm = make_sessionmaker(engine)
    async with sm() as s:
        # Наполняем только пустую БД, чтобы не затереть правки из панели
        if not await s.scalar(select(func.count()).select_from(Equipment)):
            for i, (cat, name, qty, tier, hidden) in enumerate(EQUIPMENT):
                s.add(Equipment(category=cat, name=name, total_quantity=qty,
                                access_tier=tier, hidden=hidden, sort_order=i))
        if not await s.scalar(select(func.count()).select_from(Staff)):
            for name in TEACHERS:
                s.add(Staff(full_name=name, role=Role.TEACHER, invite_token=new_token()))
            s.add(Staff(full_name="Специалист по выдаче", role=Role.SPECIALIST,
                        invite_token=new_token()))
        await s.commit()


def now_naive(tz) -> datetime:  # noqa: ANN001
    # В БД храним местное время без tzinfo (SQLite не хранит зоны)
    return datetime.now(tz).replace(tzinfo=None)
