"""TELEGRAM_PROXY: запросы бота действительно идут через прокси.

Оракул — сам прокси: локальный HTTP-прокси записывает строку CONNECT,
которую ему прислал клиент. Наружу ничего не уходит: прокси отвечает 403.
"""

import asyncio

import pytest
from aiohttp_socks import ProxyError

from app.bot.setup import build_bot
from app.config import Settings


async def test_requests_go_through_proxy():
    seen: list[str] = []

    async def handle(reader, writer):
        seen.append((await reader.readline()).decode().strip())
        writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    bot = build_bot(Settings(_env_file=None, bot_token="1:x", telegram_proxy=f"http://127.0.0.1:{port}"))
    try:
        with pytest.raises(ProxyError):  # 403 от прокси — main.py превращает его в понятное сообщение
            await bot.get_me()
    finally:
        await bot.session.close()
        server.close()
    assert seen and seen[0].startswith("CONNECT api.telegram.org:443")
