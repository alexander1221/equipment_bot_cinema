"""Сборка бота и диспетчера."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, CallbackQuery, Message
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..config import Settings
from . import common, staff, student

fallback = Router(name="fallback")


@fallback.message()
async def unknown_message(message: Message) -> None:
    await message.answer("Не понял сообщение. Воспользуйтесь меню внизу или командой /help.")


@fallback.callback_query()
async def unknown_callback(cb: CallbackQuery) -> None:
    await cb.answer("Кнопка устарела.")


def build_bot(settings: Settings) -> Bot:
    kwargs: dict = {}
    if settings.telegram_api_server:
        kwargs["api"] = TelegramAPIServer.from_base(settings.telegram_api_server)
    if settings.telegram_proxy:
        # aiohttp не берёт системный прокси Windows сам, поэтому задаётся явно;
        # http/socks4/socks5 обслуживает пакет aiohttp-socks
        kwargs["proxy"] = settings.telegram_proxy
    session = AiohttpSession(**kwargs) if kwargs else None
    return Bot(settings.bot_token, session=session,
               default=DefaultBotProperties(parse_mode=ParseMode.HTML))


def build_dispatcher(sm: async_sessionmaker, settings: Settings,
                     clock: Callable[[], datetime]) -> Dispatcher:
    # sm, settings, clock попадают в обработчики как именованные аргументы
    dp = Dispatcher(storage=MemoryStorage(), sm=sm, settings=settings, clock=clock)
    # Порядок важен: обработчики свободного текста в конкретных состояниях
    # (ФИО, название проекта, ввод времени) должны срабатывать раньше
    # реакции на «FX3» и раньше fallback.
    dp.include_routers(common.router, staff.router, student.router, common.fx3_router, fallback)
    return dp


BOT_COMMANDS = [
    BotCommand(command="start", description="Главное меню"),
    BotCommand(command="new", description="Новая заявка"),
    BotCommand(command="my", description="Мои заявки"),
    BotCommand(command="profile", description="Профиль: курс и ФИО"),
    BotCommand(command="help", description="Помощь"),
]
