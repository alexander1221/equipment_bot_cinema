"""Проверка пользовательского ввода: ФИО, даты брони, время выдачи.

Документация по стандарту Wilkinson (§1–§7, §8′ — нечисловой код).

§1 ТЕОРИЯ
---------
ТЗ (п. 3.1) задаёт правила:
  * ФИО — только буквы, минимум 2 слова;
  * дата брони не в прошлом;
  * для 1 курса дата начала не ранее 1 января (через матрицу доступа:
    уровень студента на дату начала должен быть > 0, см. app/access.py);
  * максимальный срок брони — max_booking_days (по умолчанию 14) дней.
Дополнительно (не в ТЗ, настраивается): нельзя бронировать дальше чем на
max_advance_days дней вперёд — иначе один студент может «застолбить»
технику на семестр.

ФИО: слова из букв (кириллица, латиница, ё), внутри слова допускается
одиночный дефис (двойные фамилии: «Петров-Водкин»). Это единственное
отступление от «только буквы», и оно сделано сознательно.

§2 АЛГОРИТМ
-----------
normalize_fio: схлопнуть пробелы → разбить на слова → каждое слово должно
соответствовать ^[буквы]+(-[буквы]+)?$ и иметь ≥ 2 букв → слов 2..4 →
привести к виду «Иванов Иван Иванович» (первая буква заглавная).

check_dates(s, f): по порядку
  s < today                         → «дата в прошлом»
  f < s                             → «окончание раньше начала»
  f − s + 1 > max_days              → «слишком длинная бронь»
  s > today + max_advance           → «слишком далеко»
  level(s) = 0                      → «1 курс — не ранее 1 января»
Первое нарушенное правило определяет текст ошибки.

§3 ПРИМЕНЕНИЯ
-------------
Регистрация студента, календарь в боте, форма внутренней брони в панели
(там проверка курса не нужна — передаётся level=None).

§4 СПЕЦИФИКАЦИЯ
---------------
normalize_fio(raw: str) -> str, ValueError с текстом для пользователя.
check_dates(start, end, today, max_days, max_advance, level: int|None,
            first_allowed: date|None) -> str|None — текст ошибки или None.
parse_time(raw: str) -> str — «HH:MM», 07:00..22:00, ValueError иначе.

§5 ПРОЦЕДУРЫ — ниже.

§6 ЗАМЕЧАНИЯ
------------
Тексты ошибок адресованы студенту и показываются как есть.

§7 ПРИМЕРЫ — tests/test_validation.py.

§8′ УСТОЙЧИВОСТЬ
----------------
Пустая строка, цифры, эмодзи, латиница вперемешку — отклоняются с
понятным текстом. Длина ФИО ограничена 120 символами.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

_WORD_RE = re.compile(r"^[A-Za-zА-Яа-яЁё]+(-[A-Za-zА-Яа-яЁё]+)?$")
_TIME_RE = re.compile(r"^\s*(\d{1,2})[:.\-\s](\d{2})\s*$")


def normalize_fio(raw: str) -> str:
    text = " ".join((raw or "").split())
    if not text:
        raise ValueError("Введите фамилию и имя.")
    if len(text) > 120:
        raise ValueError("Слишком длинно. Введите фамилию, имя и (при наличии) отчество.")
    words = text.split(" ")
    for w in words:
        if not _WORD_RE.match(w):
            raise ValueError("ФИО может содержать только буквы (и дефис в двойной фамилии).")
        if len(w.replace("-", "")) < 2:
            raise ValueError("Каждое слово ФИО должно содержать хотя бы две буквы.")
    if len(words) < 2:
        raise ValueError("Нужно минимум два слова: фамилия и имя.")
    if len(words) > 4:
        raise ValueError("Слишком много слов. Введите фамилию, имя и отчество.")
    # «иванов-петров иван» → «Иванов-Петров Иван»
    return " ".join("-".join(p[:1].upper() + p[1:].lower() for p in w.split("-")) for w in words)


def check_dates(
    start: date,
    end: date,
    today: date,
    max_days: int,
    max_advance: int,
    level: int | None = None,
    first_allowed: date | None = None,
) -> str | None:
    if start < today:
        return "Дата начала уже прошла. Выберите сегодняшнюю или будущую дату."
    if end < start:
        return "Дата окончания раньше даты начала."
    if (end - start).days + 1 > max_days:
        return f"Максимальный срок брони — {max_days} дн. Сократите период."
    if start > today + timedelta(days=max_advance):
        return f"Бронировать можно не дальше чем на {max_advance} дн. вперёд."
    if level == 0:
        when = first_allowed.strftime("%d.%m.%Y") if first_allowed else "1 января"
        return (
            "Студентам 1 курса оборудование выдаётся с 1 января. "
            f"Выберите дату начала не раньше {when}."
        )
    return None


def parse_time(raw: str) -> str:
    m = _TIME_RE.match(raw or "")
    if not m:
        raise ValueError("Введите время в формате ЧЧ:ММ, например 14:30.")
    h, mi = int(m.group(1)), int(m.group(2))
    if not (7 <= h <= 22) or mi > 59 or (h == 22 and mi > 0):
        raise ValueError("Время выдачи должно быть между 07:00 и 22:00.")
    return f"{h:02d}:{mi:02d}"
