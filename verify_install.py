#!/usr/bin/env python3
"""
verify_install.py -- post-install verification for the equipment-booking service.

Documentation convention: Wilkinson-style with §8' (no numerics here).

§1 PURPOSE   Prove that the environment can run the service: interpreter,
             packages (including runtime-only ones: greenlet, tzdata,
             python-multipart), configuration (.env), database, and one
             end-to-end smoke operation through the real service layer.
§2 METHOD    A list of Check(name, fn); each fn returns (ok, message) and
             never raises -- exceptions are caught and reported as failures.
§3 USE       Last step of setup.sh / setup.ps1; first step of any bug report.
§4 SPEC      python verify_install.py [--quick]
             --quick  skip the smoke test. Exit 0 if all pass, else 1.
             .env checks are warnings when the file is absent (first install).
§5 CODE      below.
§6 NOTES     ALL OUTPUT IS ASCII: setup.ps1 reads this script through a pipe
             where Python writes cp1251 and PowerShell decodes cp866.
             Telegram connectivity is NOT checked here (it needs a real token
             and network); `python main.py run` reports it with a clear message.
§7 TESTS     The smoke test books 2 of the 10 Sony a6400 (TZ table) for one
             day in a temporary in-memory DB and expects 8 free -- oracle:
             10 - 2 from the equipment list, not from the code.
§8' LIMITS   Import success does not prove the bot token is valid.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
from importlib import metadata
import os
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# SQLAlchemy 2.1 and pydantic-settings 2.16 need >= 3.11; aiogram 3.31 needs < 3.15.
PY_MIN, PY_MAX_EXCL = (3, 11), (3, 15)
PACKAGES = ["aiogram", "aiohttp_socks", "fastapi", "uvicorn", "jinja2", "multipart", "itsdangerous",
            "sqlalchemy", "greenlet", "aiosqlite", "pydantic_settings", "tzdata"]

OK_MARK, FAIL_MARK, WARN_MARK = "[OK]", "[FAIL]", "[WARN]"
COLOR = sys.stdout.isatty() and (os.name != "nt" or "WT_SESSION" in os.environ or os.environ.get("TERM"))
G, R, Y, Z = ("\033[32m", "\033[31m", "\033[33m", "\033[0m") if COLOR else ("", "", "", "")


def ascii_only(s: str) -> str:
    return str(s).encode("ascii", "backslashreplace").decode("ascii")


class Check:
    def __init__(self, name, fn, heavy=False, soft=False):
        self.name, self.fn, self.heavy, self.soft = name, fn, heavy, soft

    def run(self):
        try:
            return self.fn()
        except Exception as e:  # a check never crashes the run
            return False, f"{type(e).__name__}: {str(e)[:200]}"


def check_python():
    v = sys.version_info[:2]
    ok = PY_MIN <= v < PY_MAX_EXCL
    return ok, f"{platform.python_version()} (need >= {PY_MIN[0]}.{PY_MIN[1]}, < {PY_MAX_EXCL[0]}.{PY_MAX_EXCL[1]})"


# Модуль -> имя дистрибутива для importlib.metadata (версия без устаревших __version__)
DIST = {"multipart": "python-multipart", "pydantic_settings": "pydantic-settings",
        "aiohttp_socks": "aiohttp-socks"}


def make_import_check(mod):
    def fn():
        importlib.import_module(mod)
        try:
            return True, metadata.version(DIST.get(mod, mod))
        except metadata.PackageNotFoundError:
            return True, "imported"
    return fn


def check_env():
    if not (ROOT / ".env").exists():
        return False, ".env not found: copy .env.example to .env and fill BOT_TOKEN and passwords"
    from app.config import Settings
    s = Settings()
    missing = [k for k, v in {"BOT_TOKEN": s.bot_token, "WEB_SPECIALIST_PASSWORD": s.web_specialist_password,
                              "WEB_ADMIN_PASSWORD": s.web_admin_password,
                              "WEB_SECRET_KEY": s.web_secret_key}.items() if not v]
    if missing:
        return False, "empty in .env: " + ", ".join(missing)
    if s.invalid_admin_ids:
        return False, "ADMIN_IDS must be numeric Telegram IDs (use /id in the bot), not @username"
    s.tz  # noqa: B018 -- raises if TIMEZONE is unknown
    return True, f"timezone {s.timezone}, max booking {s.max_booking_days} days"


def check_db():
    from app.config import get_settings
    from app.db import init_db, make_engine

    async def go():
        e = make_engine(get_settings().database_url)
        await init_db(e)
        await e.dispose()

    asyncio.run(go())
    return True, "tables ready"


def smoke_test():
    from datetime import date, datetime

    from app import services
    from app.config import Settings
    from app.db import init_db, make_engine, make_sessionmaker

    async def go():
        e = make_engine("sqlite+aiosqlite:///:memory:")
        await init_db(e)
        sm = make_sessionmaker(e)
        async with sm() as s:
            now = datetime(2026, 10, 22, 12)
            st = await services.save_student(s, 1, "Test Student", 3, "", now, auto_verify=True)
            teacher = (await services.teachers(s))[0]
            eq = {x.name: x.id for x in await services.equipment_list(s)}
            cam = eq["Sony a6400 Kit 16-50mm II"]
            d = date(2026, 10, 23)
            await services.create_student_booking(s, Settings(_env_file=None), st, teacher.id, d, d,
                                                  "smoke", {cam: 2}, now)
            rows = await services.student_catalog(s, st, d, d, now.date())
        await e.dispose()
        return {r.equipment.id: r.free for r in rows}[cam]

    free = asyncio.run(go())
    return free == 8, f"booking 2 of 10 cameras leaves {free} free (expected 8)"


def build_checks():
    checks = [Check("Python version", check_python)]
    checks += [Check(f"import {p}", make_import_check(p)) for p in PACKAGES]
    checks += [Check(".env settings", check_env, soft=True), Check("database", check_db),
               Check("smoke test", smoke_test, heavy=True)]
    return checks


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Verify the installation")
    ap.add_argument("--quick", action="store_true", help="skip the smoke test")
    a = ap.parse_args(argv)
    checks = [c for c in build_checks() if not (a.quick and c.heavy)]
    n_ok, hard_fail = 0, 0
    for c in checks:
        ok, msg = c.run()
        n_ok += ok
        if ok:
            mark = f"{G}{OK_MARK}{Z}"
        elif c.soft:
            mark = f"{Y}{WARN_MARK}{Z}"
        else:
            mark, hard_fail = f"{R}{FAIL_MARK}{Z}", hard_fail + 1
        print(f"  {mark} {c.name:<26} {ascii_only(msg)}")
    print(f"\n{n_ok}/{len(checks)} checks passed")
    if n_ok < len(checks):
        print("See docs/TROUBLESHOOTING.md for each failed check.")
    return 0 if hard_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
