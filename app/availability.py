"""Расчёт занятости оборудования по дням.

Документация по стандарту Wilkinson (§1–§7, §8′ — нечисловой код).

§1 ТЕОРИЯ
---------
Позиция e имеет T_e единиц (total_quantity). Бронь b занимает q_{b,e}
единиц позиции e на каждый день отрезка [s_b, f_b] (даты включительно).
Учитываются только брони в «блокирующих» статусах (ожидает преподавателя,
согласована, выдача назначена, выдано, внутренняя бронь).

Занятость позиции в день d:

    used_e(d) = Σ_{b: s_b ≤ d ≤ f_b} q_{b,e}

Свободно на весь отрезок [s, f]:

    free_e(s, f) = T_e − max_{d ∈ [s, f]} used_e(d)

Запрос {e: q_e} на [s, f] выполним ⇔ q_e ≤ free_e(s, f) для всех e.
Берётся именно максимум по дням, а не сумма пересекающихся броней.
Пример: T = 1, брони на 1–3 и 5–7 число по одной единице. Запрос на
1–7 невыполним (единица занята 1–3 и 5–7), но для T = 2 он выполним,
хотя сумма пересекающихся броней равна 2 = T: в каждый отдельный день
занята только одна единица. Отсюда — подневный учёт.

Выданное, но не возвращённое оборудование (статус ISSUED) занимает
единицы до фактического возврата: его отрезок продлевается до
max(f_b, today). Иначе просрочка сделала бы технику «свободной».

§2 АЛГОРИТМ
-----------
Длина брони ограничена (по умолчанию 14 дней), а окно шкалы в панели —
десятками дней, поэтому прямой подневный перебор достаточен:

    для d от s до f:  used[e][d] = Σ q_{b,e} по броням, покрывающим d
    free[e] = T_e − max_d used[e][d]

Сложность O(|B| · D), где D = f − s + 1, |B| — число пересекающихся броней.

§3 ПРИМЕНЕНИЯ
-------------
* free_for_range — список доступного оборудования в боте (п. 3.1.4 ТЗ);
* find_shortages — проверка заявки перед записью (п. 3.1.5 ТЗ);
* daily_usage — шкала занятости в веб-панели (п. 5.1 ТЗ);
* overbooked_days — предупреждение после списания (п. 5.3 ТЗ).

§4 СПЕЦИФИКАЦИЯ
---------------
Span(start: date, end: date, items: dict[int, int]) — одна бронь:
    items — {equipment_id: quantity}, quantity ≥ 1; start ≤ end.
daily_usage(spans, start, end) -> dict[int, dict[date, int]]
free_for_range(totals: dict[int, int], spans, start, end) -> dict[int, int]
    может вернуть отрицательное число, если после списания позиция
    оказалась переброненной; вызывающий код трактует ≤ 0 как «нет».
find_shortages(request: dict[int, int], totals, spans, start, end)
    -> dict[int, tuple[int, int]] — {e: (запрошено, свободно)} для
    невыполнимых позиций; пустой словарь ⇔ запрос выполним.
overbooked_days(totals, spans, start, end) -> dict[int, list[date]]
effective_span(start, end, issued, today) — продление для ISSUED.

§5 ПРОЦЕДУРЫ — ниже.

§6 ЗАМЕЧАНИЯ
------------
Функции чистые и не ходят в БД: выборку броней делает app/services.py,
поэтому логику можно тестировать без базы. Брони, не пересекающие
[start, end], просто не дают вклада.

§7 ПРИМЕРЫ — tests/test_availability.py, ожидания посчитаны вручную
по таблицам «день × бронь» в тексте теста.

§8′ УСТОЙЧИВОСТЬ
----------------
start > end → ValueError. Пустой список броней → free = T_e.
Позиция из запроса, отсутствующая в totals (удалена) → считается T_e = 0.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class Span:
    start: date
    end: date
    items: dict[int, int]


def _days(start: date, end: date) -> list[date]:
    if start > end:
        raise ValueError(f"start {start} is after end {end}")
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def effective_span(start: date, end: date, issued: bool, today: date) -> tuple[date, date]:
    # Невозвращённая техника занята как минимум до сегодняшнего дня
    if issued and end < today:
        return start, today
    return start, end


def daily_usage(spans: list[Span], start: date, end: date) -> dict[int, dict[date, int]]:
    days = _days(start, end)
    usage: dict[int, dict[date, int]] = defaultdict(lambda: {d: 0 for d in days})
    for sp in spans:
        # Пересечение отрезка брони с окном [start, end]
        lo, hi = max(sp.start, start), min(sp.end, end)
        if lo > hi:
            continue
        for d in _days(lo, hi):
            for eq_id, qty in sp.items.items():
                usage[eq_id][d] += qty
    return dict(usage)


def free_for_range(
    totals: dict[int, int], spans: list[Span], start: date, end: date
) -> dict[int, int]:
    usage = daily_usage(spans, start, end)
    # free_e = T_e − max_d used_e(d)
    return {
        eq_id: total - (max(usage[eq_id].values()) if eq_id in usage else 0)
        for eq_id, total in totals.items()
    }


def find_shortages(
    request: dict[int, int],
    totals: dict[int, int],
    spans: list[Span],
    start: date,
    end: date,
) -> dict[int, tuple[int, int]]:
    free = free_for_range({e: totals.get(e, 0) for e in request}, spans, start, end)
    return {e: (q, max(free[e], 0)) for e, q in request.items() if q > free[e]}


def overbooked_days(
    totals: dict[int, int], spans: list[Span], start: date, end: date
) -> dict[int, list[date]]:
    usage = daily_usage(spans, start, end)
    result: dict[int, list[date]] = {}
    for eq_id, per_day in usage.items():
        bad = [d for d, u in per_day.items() if u > totals.get(eq_id, 0)]
        if bad:
            result[eq_id] = bad
    return result
