"""Матрица доступа студентов к оборудованию (п. 2 ТЗ).

Документация по стандарту Wilkinson (§1–§7, §8′ — нечисловой код).

§1 ТЕОРИЯ
---------
В ТЗ доступ задан таблицей с тремя столбцами:

    A = «1 курс с 01.01»,
    B = «2 курс: 01.09–31.12»,
    C = «2 курс с 01.01 и старшие курсы».

Каждая позиция в таблице имеет вид ✅✅✅, ❌✅✅ или ❌❌✅ (либо «скрыто»).
Столбцы вложены: всё, что доступно в A, доступно в B, всё из B — в C.
Значит, достаточно хранить у позиции одно число — уровень (tier):

    tier 1 — ✅✅✅ (базовые камеры, штативы, стабилизаторы),
    tier 2 — ❌✅✅ (звук),
    tier 3 — ❌❌✅ (всё остальное),
    hidden — не показывается никогда (Sony FX3 / FX30).

А у студента на конкретную дату d — уровень L(d) ∈ {0,1,2,3}. Позиция
доступна ⇔ не hidden и tier ≤ L(d). Уровень 0 означает «ничего нельзя»:
это первокурсники до 1 января (в ТЗ: «если выбран 1 курс — дата начала
должна быть не ранее 1 января»).

Курс меняется 1 сентября, поэтому студент указывает курс один раз, а
система помнит учебный год, в котором он был указан, и сама сдвигает курс.

§2 АЛГОРИТМ
-----------
    учебный_год(d) = d.year,      если d.month ≥ 9
                     d.year − 1,  иначе
    осень(d)       = d.month ≥ 9          (сентябрь–декабрь)
    курс(d)        = course + учебный_год(d) − course_year

    L(d) = 0, если курс(d) ≤ 0
           0, если курс(d) = 1 и осень(d)
           1, если курс(d) = 1 и не осень(d)
           2, если курс(d) = 2 и осень(d)
           3, если курс(d) = 2 и не осень(d)
           3, если курс(d) ≥ 3

Для брони [s, f] используется L(s). Это корректно, потому что L(d)
не убывает по d (свойство проверяется в tests/test_access.py): внутри
учебного года осень → весна только повышает уровень, а на границе
31.08 → 01.09 курс растёт на 1, что тоже не понижает уровень
(1-весна=1 → 2-осень=2; 2-весна=3 → 3-осень=3). Следовательно,
min_{d∈[s,f]} L(d) = L(s).

§3 ПРИМЕНЕНИЯ
-------------
* Фильтр списка оборудования в боте (студент видит только tier ≤ L(s)).
* Повторная проверка состава заявки в сервисе перед записью в БД
  (защита от подделанного callback'а).
* Сообщение об ошибке первокурснику, выбравшему дату до 1 января.

§4 СПЕЦИФИКАЦИЯ
---------------
academic_year(d: date) -> int — год сентября учебного года, содержащего d.
effective_course(course: int ≥ 1, course_year: int, d: date) -> int — курс на дату d.
student_level(course: int ≥ 1, course_year: int, d: date) -> int ∈ {0,1,2,3}.
is_allowed(tier: int ∈ {1,2,3}, hidden: bool, level: int) -> bool.
first_allowed_date(course, course_year, today: date) -> date — ближайшая
    дата ≥ today с L > 0 (для текста ошибки).
is_hidden_query(text: str) -> bool — запрос похож на поиск Sony FX3/FX30.

§5 ПРОЦЕДУРЫ — ниже.

§6 ЗАМЕЧАНИЯ
------------
Курс > 4 (магистратура, повторный год) просто даёт уровень 3. Если студент
ошибся с курсом, он меняет его в профиле; course_year обновляется на текущий
учебный год.

§7 ПРИМЕРЫ (проверяются в tests/test_access.py по таблице ТЗ, а не по коду)
---------------------------------------------------------------------------
course=1, course_year=2026: 2026-10-22 → 0; 2027-01-01 → 1; 2027-09-01 → 2.
course=2, course_year=2026: 2026-12-31 → 2; 2027-01-01 → 3.

§8′ УСТОЙЧИВОСТЬ
----------------
Некорректный курс (< 1) → ValueError. Функции чистые, O(1).
"""

from __future__ import annotations

import re
from datetime import date

HIDDEN_MESSAGE = "Данная камера выдается только по отдельному запросу руководства кафедры"

# FX3, FX30, «fx 3», «фх3» и т.п.
_HIDDEN_RE = re.compile(r"(fx|фх|фкс)\s*-?\s*3", re.IGNORECASE)


def academic_year(d: date) -> int:
    # Учебный год начинается 1 сентября и называется по году сентября
    return d.year if d.month >= 9 else d.year - 1


def effective_course(course: int, course_year: int, d: date) -> int:
    if course < 1:
        raise ValueError(f"course must be >= 1, got {course}")
    # Курс растёт на 1 с каждым новым учебным годом после регистрации
    return course + academic_year(d) - course_year


def student_level(course: int, course_year: int, d: date) -> int:
    c = effective_course(course, course_year, d)
    autumn = d.month >= 9
    if c <= 0:
        return 0
    if c == 1:
        return 0 if autumn else 1
    if c == 2:
        return 2 if autumn else 3
    return 3


def is_allowed(tier: int, hidden: bool, level: int) -> bool:
    return (not hidden) and tier <= level


def first_allowed_date(course: int, course_year: int, today: date) -> date:
    # L(d) не убывает, а меняется только 1 января и 1 сентября —
    # достаточно перебрать ближайшие такие границы.
    if student_level(course, course_year, today) > 0:
        return today
    candidates = []
    for y in (today.year, today.year + 1, today.year + 2):
        candidates += [date(y, 1, 1), date(y, 9, 1)]
    for c in sorted(candidates):
        if c > today and student_level(course, course_year, c) > 0:
            return c
    raise ValueError("no allowed date found")  # недостижимо при course >= 1


def is_hidden_query(text: str) -> bool:
    return bool(_HIDDEN_RE.search(text or ""))
