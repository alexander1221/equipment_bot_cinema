"""Настройки сервиса из переменных окружения / файла .env.

Все параметры, которые кафедра может захотеть поменять без правки кода,
собраны здесь. Значения по умолчанию соответствуют ТЗ (срок брони 14 дней,
место выдачи «ауд. 106.1»).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Telegram ---
    bot_token: str = ""
    # Адрес своего Bot API сервера или зеркала (если api.telegram.org недоступен)
    telegram_api_server: str = ""
    # Прокси для запросов к Bot API: socks5://127.0.0.1:10808, http://user:pass@host:3128
    telegram_proxy: str = ""
    # Telegram ID администраторов через запятую (получают все уведомления специалиста)
    admin_ids: str = ""

    # --- База данных ---
    database_url: str = f"sqlite+aiosqlite:///{(PROJECT_ROOT / 'data' / 'equipment.db').as_posix()}"

    # --- Время ---
    timezone: str = "Europe/Moscow"

    # --- Правила бронирования ---
    max_booking_days: int = 14  # максимальная длина брони, дней включительно
    max_advance_days: int = 90  # насколько вперёд можно бронировать
    pending_ttl_hours: int = 48  # через сколько часов неподтверждённая заявка сгорает
    # Студент может подавать заявки только после подтверждения профиля специалистом
    require_student_verification: bool = True

    # --- Выдача ---
    issue_default_time: str = "14:00"
    issue_places: str = "ауд. 106.1"  # варианты через «;», первый — по умолчанию

    # --- Веб-панель ---
    web_host: str = "0.0.0.0"
    web_port: int = 8080
    web_admin_password: str = ""
    web_specialist_password: str = ""
    web_secret_key: str = ""
    # Внешний адрес панели (для ссылок в сообщениях бота); можно оставить пустым
    public_base_url: str = ""

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def _admin_tokens(self) -> list[str]:
        return [x.strip() for x in self.admin_ids.replace(";", ",").split(",") if x.strip()]

    @property
    def admin_id_list(self) -> list[int]:
        # Только числовые ID: Bot API не пишет пользователю по @username.
        # Неверные значения пропускаются (main.py сообщает о них при запуске),
        # чтобы опечатка в .env не роняла обработку заявок.
        return [int(x) for x in self._admin_tokens() if x.lstrip("-").isdigit()]

    @property
    def invalid_admin_ids(self) -> list[str]:
        return [x for x in self._admin_tokens() if not x.lstrip("-").isdigit()]

    @property
    def issue_place_list(self) -> list[str]:
        places = [p.strip() for p in self.issue_places.split(";") if p.strip()]
        return places or ["ауд. 106.1"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
