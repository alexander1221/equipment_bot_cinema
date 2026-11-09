"""Фоновые задачи: истечение неподтверждённых заявок и напоминания о возврате."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime

from aiogram import Bot
from sqlalchemy.ext.asyncio import async_sessionmaker

from . import notify, services
from .config import Settings

log = logging.getLogger(__name__)


async def run_once(bot: Bot, sm: async_sessionmaker, settings: Settings,
                   clock: Callable[[], datetime]) -> None:
    async with sm() as s:
        now = clock()
        for b in await services.expire_pending(s, now, settings.pending_ttl_hours):
            await notify.expired(bot, s, b)
        for b in await services.due_return_reminders(s, now):
            await notify.return_reminder(bot, b)


async def loop(bot: Bot, sm: async_sessionmaker, settings: Settings,
               clock: Callable[[], datetime], period_s: int = 600) -> None:
    while True:
        try:
            await run_once(bot, sm, settings, clock)
        except Exception:  # фоновая задача не должна умирать от одной ошибки
            log.exception("background job failed")
        await asyncio.sleep(period_s)
