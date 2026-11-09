"""Общие фикстуры: БД в памяти, поддельная сессия Telegram, конструктор апдейтов.

FakeSession подменяет сетевой слой aiogram: каждый вызов Bot API
записывается в список и получает правдоподобный ответ. Так обработчики
бота проверяются целиком — от входящего апдейта до исходящих сообщений —
без доступа к api.telegram.org.
"""

from __future__ import annotations

import itertools
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, GetMe, SendMessage
from aiogram.types import Chat, InlineKeyboardMarkup, Message, User

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.bot import common, setup, staff, student  # noqa: E402
from app.bot.setup import build_dispatcher  # noqa: E402
from app.config import Settings  # noqa: E402
from app.db import init_db, make_engine, make_sessionmaker  # noqa: E402

TOKEN = "42:TEST"


class FakeSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[Any] = []
        self._ids = itertools.count(1000)

    async def close(self) -> None:
        pass

    async def make_request(self, bot, method, timeout=None):  # noqa: ANN001
        self.calls.append(method)
        if isinstance(method, SendMessage):
            return Message(message_id=next(self._ids), date=datetime.now(),
                           chat=Chat(id=method.chat_id, type="private"), text=method.text,
                           reply_markup=method.reply_markup
                           if isinstance(method.reply_markup, InlineKeyboardMarkup) else None)
        if isinstance(method, GetMe):
            return User(id=42, is_bot=True, first_name="Bot", username="test_bot")
        return True

    async def stream_content(self, *a, **kw):  # pragma: no cover
        yield b""


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class Harness:
    """Отправка апдейтов от имени пользователей и чтение ответов бота."""

    def __init__(self, bot: Bot, dp, session: FakeSession, sm, settings: Settings, clock: Clock):
        self.bot, self.dp, self.session, self.sm, self.settings, self.clock = bot, dp, session, sm, settings, clock
        self._upd = itertools.count(1)
        self._msg = itertools.count(1)

    def _user(self, uid: int) -> dict:
        return {"id": uid, "is_bot": False, "first_name": f"U{uid}", "username": f"user{uid}"}

    async def text(self, uid: int, text: str) -> list:
        n = len(self.session.calls)
        msg = {"message_id": next(self._msg), "date": int(self.clock.now.timestamp()),
               "chat": {"id": uid, "type": "private"}, "from": self._user(uid), "text": text}
        if text.startswith("/"):
            cmd = text.split()[0]
            msg["entities"] = [{"type": "bot_command", "offset": 0, "length": len(cmd)}]
        await self.dp.feed_raw_update(self.bot, {"update_id": next(self._upd), "message": msg})
        return self.session.calls[n:]

    async def click(self, uid: int, callback_data: str, message_id: int = 1, text: str = "x") -> list:
        n = len(self.session.calls)
        cq = {"id": str(next(self._upd)), "from": self._user(uid), "chat_instance": "ci",
              "data": callback_data,
              "message": {"message_id": message_id, "date": int(self.clock.now.timestamp()),
                          "chat": {"id": uid, "type": "private"}, "text": text}}
        await self.dp.feed_raw_update(self.bot, {"update_id": next(self._upd), "callback_query": cq})
        return self.session.calls[n:]

    def sent_to(self, chat_id: int, calls: list | None = None) -> list[SendMessage]:
        return [c for c in (calls if calls is not None else self.session.calls)
                if isinstance(c, SendMessage) and c.chat_id == chat_id]

    def alerts(self, calls: list) -> list[str]:
        return [c.text for c in calls if isinstance(c, AnswerCallbackQuery) and c.text]


def markup_of(call) -> list[tuple[str, str]]:
    """(текст, callback_data) всех inline-кнопок сообщения/правки."""
    rm = getattr(call, "reply_markup", None)
    if rm is None or not hasattr(rm, "inline_keyboard"):
        return []
    return [(b.text, b.callback_data) for row in rm.inline_keyboard for b in row]


def last_markup(calls: list) -> list[tuple[str, str]]:
    for c in reversed(calls):
        m = markup_of(c)
        if m:
            return m
    return []


def button(calls_or_markup, needle: str) -> str:
    m = calls_or_markup if calls_or_markup and isinstance(calls_or_markup[0], tuple) else last_markup(calls_or_markup)
    for text, data in m:
        if needle in text:
            return data
    raise AssertionError(f"button {needle!r} not found in {[t for t, _ in m]}")


@pytest.fixture
async def engine(tmp_path):
    e = make_engine(f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}")
    await init_db(e)
    yield e
    await e.dispose()


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, bot_token=TOKEN, web_admin_password="adm",
                    web_specialist_password="spec", web_secret_key="k")


@pytest.fixture
def clock() -> Clock:
    return Clock(datetime(2026, 10, 22, 12, 0))


@pytest.fixture
async def h(engine, settings, clock) -> Harness:
    session = FakeSession()
    bot = Bot(TOKEN, session=session)
    sm = make_sessionmaker(engine)
    # Роутеры — модульные синглтоны, а диспетчер в тестах создаётся заново
    # для каждого теста: отвязываем их от диспетчера предыдущего теста.
    for r in (common.router, common.fx3_router, staff.router, student.router, setup.fallback):
        r._parent_router = None
    dp = build_dispatcher(sm, settings, clock)
    return Harness(bot, dp, session, sm, settings, clock)
