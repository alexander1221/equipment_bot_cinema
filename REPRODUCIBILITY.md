# Воспроизводимость

## Зафиксированные версии (verified 2026-10-22 via PyPI JSON API, check_external.py)

| Компонент | Версия | Зачем |
|---|---|---|
| Python | 3.12 (поддерживается 3.11–3.14) | 3.11+ — SQLAlchemy 2.1; <3.15 — aiogram 3.31 |
| aiogram | 3.31.0 | Telegram Bot API |
| fastapi / starlette | 0.159.1 / 1.9.0 | веб-панель |
| uvicorn | 0.54.0 | HTTP-сервер |
| SQLAlchemy / greenlet / aiosqlite | 2.1.6 / 3.5.6 / 0.22.1 | БД |
| pydantic-settings | 2.16.1 | настройки из .env |
| Jinja2 | 3.1.6 | шаблоны панели |
| tzdata | 2026.5 | часовые пояса (Windows) |
| Docker-образ | python:3.12-slim | тег проверен на Docker Hub |

Полный список с хэшами: `requirements-lock-linux.txt`, `requirements-lock-win_amd64.txt`
(сгенерированы `uv pip compile --generate-hashes --python-version 3.12`).

## Четыре уровня установки

1. **Обычно:** `bash setup.sh` (Windows: `windows_first_run.bat`).
2. **Побитово те же пакеты:** `pip install --require-hashes -r requirements-lock-linux.txt`
   (setup.sh делает это сам на Linux x86_64).
3. **Без интернета:** на машине с интернетом
   `pip download -r requirements-lock-linux.txt -d wheels`, перенести папку,
   затем `pip install --no-index --find-links wheels -r requirements-lock-linux.txt`.
4. **Полный архив:** `bash setup.sh --offline-bundle equipment_bot_bundle.tar.xz` —
   проект + колёса в одном файле.

Внешних данных и моделей нет: список оборудования встроен (`app/seed_data.py`),
после первого запуска он живёт в БД.

## Если источники пропадут

- **PyPI** — используйте архив уровня 3/4 или зеркало (например, корпоративный
  devpi / Nexus).
- **Docker Hub** — образ можно собрать на любом `python:3.12` (Debian), Dockerfile
  не зависит от особенностей slim.
- **Telegram Bot API** меняется, но обратно совместимо; aiogram 3.x следует за ним.
  Если через годы Bot API сломает совместимость — обновить aiogram в пределах 3.x.

## Чек-лист архивации

- [ ] `data/equipment.db` (вся история заявок и инвентаря) — **главное**.
- [ ] `.env` (токен бота, пароли) — хранить отдельно, не в общем доступе.
- [ ] Архив проекта с lock-файлами.
- [ ] Офлайн-бандл (`--offline-bundle`), если нужна установка без интернета.

## Что вероятно сломается через 3–5 лет

- **Python 3.12** выйдет из поддержки в октябре 2028 — образы и колёса для него
  перестанут обновляться. Переход на новый Python: обновить пины, прогнать тесты.
- **Starlette** уже предупреждает, что `TestClient` на httpx устаревает (замена —
  httpx2): тесты панели потребуют правки при обновлении starlette.
- **aiogram 4.x**, если выйдет, изменит API обработчиков — оставаться на 3.x или
  переписывать `app/bot/`.
- **Telegram** может ограничить или изменить доступ к Bot API в отдельных сетях —
  см. `TELEGRAM_API_SERVER` в docs/TROUBLESHOOTING.md.

## Команды для будущего вас

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install --require-hashes -r requirements-lock-linux.txt
python verify_install.py && python -m pytest -q
cp /backup/equipment.db data/ && python main.py run
```
