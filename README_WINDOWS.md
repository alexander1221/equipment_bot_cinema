# Установка на Windows

**TL;DR**
1. ZIP → правой кнопкой → «Свойства» → «Разблокировать» → OK, затем распаковать
   в короткую папку, например `C:\work\`.
2. Установить Python 3.12: `winget install -e --id Python.Python.3.12`.
3. Двойной клик по `windows_first_run.bat`.
4. Открыть `.env` в Блокноте, вписать `BOT_TOKEN` и пароли панели.
5. Запуск: `.venv\Scripts\python.exe main.py run`.
6. Панель: http://localhost:8080.

## Подробно

1. Python. `winget install -e --id Python.Python.3.12` (или с python.org, галочка
   «Add python.exe to PATH»). Проверка: `py -3.12 --version`.
2. `windows_first_run.bat` найдёт Python через `py`, запустит `setup.ps1` в обход
   ExecutionPolicy и выполнит 6 шагов: проверки, `.venv`, зависимости, `.env`, база,
   `verify_install.py` и тесты. Вручную: `powershell -NoProfile -ExecutionPolicy Bypass -File setup.ps1`.
3. `.env` создаётся из `.env.example`, секрет сессий генерируется автоматически.
4. Чтобы сервис работал постоянно, удобнее Docker Desktop (`docker compose up -d`)
   или служба через NSSM — но для кафедры лучше Linux-сервер.

## Особенности Windows

- **ExecutionPolicy.** `windows_first_run.bat` запускает PowerShell с
  `-ExecutionPolicy Bypass` — менять политику системы не нужно.
- **Длинные пути.** Проект рассчитан на распаковку в папку с путём до 59 символов
  (например, `C:\Users\<имя>\Downloads\`). При ошибке «путь слишком длинный» —
  распакуйте ближе к корню диска (`C:\work\`).
- **Кириллица в пути** допустима, но если что-то не так — попробуйте `C:\work\`.
- **PowerShell 5.1 и 7+** — скрипт работает в обоих.
- **«Предупреждение о безопасности … [R] Выполнить однократно».** Это метка
  «из Интернета» на файлах из ZIP. Лучше всего выполнить `Unblock-File .\equipment_bot.zip`
  **до** распаковки. Если уже распаковано — в папке проекта
  `Get-ChildItem -Recurse | Unblock-File`, или запуск через `windows_first_run.bat`.
  Ответ `R` безопасен.
- **Кракозябры вместо `...` в выводе установки** — сообщите разработчику версию
  Windows и язык системы (скрипты выводят только ASCII, этого быть не должно).
- **py launcher**: `py -0` покажет установленные версии; `setup.ps1 -PythonBin "py -3.13"`
  выберет другую.

Установка на Windows при сборке не запускалась (проверены только колёса
зависимостей для win_amd64) — если что-то пойдёт не так, см. docs/TROUBLESHOOTING.md.
