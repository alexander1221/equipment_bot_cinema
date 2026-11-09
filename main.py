"""Точка входа сервиса «Выдача оборудования».

    python main.py init-db      создать таблицы и заполнить инвентарь/преподавателей
    python main.py run          запустить бота и веб-панель (один процесс)
    python main.py web          только веб-панель (без Telegram; уведомления не уходят)
    python main.py invites      напечатать ссылки-приглашения сотрудников

Бот и панель работают в одном процессе намеренно: бизнес-операции
сериализуются общей asyncio-блокировкой (app/services.py), а панель
отправляет уведомления через тот же экземпляр бота.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

import uvicorn
from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError
from aiohttp_socks import ProxyConnectionError, ProxyError, ProxyTimeoutError
from sqlalchemy import select

from app import jobs
from app.bot.setup import BOT_COMMANDS, build_bot, build_dispatcher
from app.config import get_settings
from app.db import init_db, make_engine, make_sessionmaker, now_naive
from app.models import Staff
from app.web.app import create_app

log = logging.getLogger("equipment_bot")


def _die(msg: str) -> None:
    print(f"[error] {msg}\nПодробнее: docs/TROUBLESHOOTING.md", file=sys.stderr)
    sys.exit(1)


async def cmd_init_db() -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    await init_db(engine)
    await engine.dispose()
    print(f"[ok] База готова: {settings.database_url}")


async def cmd_invites() -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    await init_db(engine)
    sm = make_sessionmaker(engine)
    username = ""
    if settings.bot_token:
        bot = build_bot(settings)
        try:
            username = (await bot.get_me()).username or ""
        except Exception as e:  # нет сети или неверный токен — покажем команды
            print(f"[warn] Не удалось получить имя бота: {e}")
        finally:
            await bot.session.close()
    async with sm() as s:
        for st in await s.scalars(select(Staff).order_by(Staff.role, Staff.full_name)):
            link = (f"https://t.me/{username}?start=inv_{st.invite_token}" if username
                    else f"/start inv_{st.invite_token}")
            mark = "подключён" if st.tg_id else "не подключён"
            print(f"{st.role:<11} {st.full_name:<25} {mark:<13} {link}")
    await engine.dispose()


async def cmd_run(with_bot: bool) -> None:
    settings = get_settings()
    if with_bot and not settings.bot_token:
        _die("BOT_TOKEN не задан в .env (получите токен у @BotFather)")
    if settings.invalid_admin_ids:
        _die(f"ADMIN_IDS в .env: {', '.join(settings.invalid_admin_ids)} — не числовой Telegram ID.\n"
             "Нужны числа через запятую, например ADMIN_IDS=123456789. @username не подходит: "
             "бот не может писать по нему. Свой ID покажет команда /id в боте "
             "(или оставьте ADMIN_IDS пустым и подключитесь ссылкой из панели → «Сотрудники»)")
    if not (settings.web_admin_password or settings.web_specialist_password):
        log.warning("Пароли веб-панели не заданы — вход в панель будет невозможен")
    engine = make_engine(settings.database_url)
    await init_db(engine)
    sm = make_sessionmaker(engine)

    def clock():
        return now_naive(settings.tz)

    bot = build_bot(settings) if with_bot else None
    app = create_app(sm, settings, clock, bot)
    server = uvicorn.Server(uvicorn.Config(app, host=settings.web_host, port=settings.web_port,
                                           log_level="info"))
    tasks = []
    if bot is not None:
        try:
            me = await bot.get_me()
        except TelegramUnauthorizedError:
            await bot.session.close()
            _die("Telegram отклонил BOT_TOKEN — проверьте токен от @BotFather")
        except (ProxyError, ProxyConnectionError, ProxyTimeoutError) as e:
            await bot.session.close()
            _die(f"Прокси {settings.telegram_proxy} не пропустил запрос к Telegram ({e}).\n"
                 "Проверьте адрес, порт и тип прокси (socks5:// или http://) и что VPN/прокси-клиент запущен")
        except TelegramNetworkError as e:
            await bot.session.close()
            via = f" через прокси {settings.telegram_proxy}" if settings.telegram_proxy else ""
            _die(f"Нет связи с Telegram Bot API{via} ({e}).\n"
                 "Ping до api.telegram.org не показатель: боту нужен HTTPS (порт 443).\n"
                 "Проверка: Test-NetConnection api.telegram.org -Port 443 (Windows) или "
                 "curl -sI https://api.telegram.org.\n"
                 "Если порт закрыт — укажите TELEGRAM_PROXY в .env (socks5://… или http://…) "
                 "или запустите сервис на сервере с доступом к Telegram")
        app.state.bot_username = me.username or ""
        log.info("Бот @%s запущен", me.username)
        await bot.set_my_commands(BOT_COMMANDS)
        dp = build_dispatcher(sm, settings, clock)
        tasks += [dp.start_polling(bot, handle_signals=False), jobs.loop(bot, sm, settings, clock)]
    tasks.insert(0, server.serve())
    running = [asyncio.ensure_future(t) for t in tasks]
    try:
        # Если упала или остановилась любая часть (Ctrl+C останавливает uvicorn),
        # гасим остальные, чтобы процесс не висел «наполовину живым».
        done, pending = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for t in done:
            t.result()  # пробрасываем исключение упавшей части
    finally:
        if bot is not None:
            await bot.session.close()
        await engine.dispose()


def main() -> None:
    p = argparse.ArgumentParser(description="Сервис выдачи оборудования кафедры кинопроизводства")
    p.add_argument("command", choices=["init-db", "run", "web", "invites"])
    p.add_argument("--debug", action="store_true", help="подробный лог")
    args = p.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    coro = {"init-db": cmd_init_db, "invites": cmd_invites}.get(args.command)
    try:
        if coro:
            asyncio.run(coro())
        else:
            asyncio.run(cmd_run(with_bot=args.command == "run"))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
