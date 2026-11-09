"""Подневная занятость. Ожидания посчитаны вручную по таблицам в комментариях."""

import random
from datetime import date, timedelta

import pytest

from app.availability import (
    Span,
    daily_usage,
    effective_span,
    find_shortages,
    free_for_range,
    overbooked_days,
)

D = lambda day: date(2026, 11, day)  # noqa: E731


def test_gap_between_bookings():
    # T=1; брони 1–3 и 5–7. День:  1 2 3 4 5 6 7
    #                     занято:  1 1 1 0 1 1 1
    spans = [Span(D(1), D(3), {1: 1}), Span(D(5), D(7), {1: 1})]
    assert free_for_range({1: 1}, spans, D(4), D(4)) == {1: 1}
    assert free_for_range({1: 1}, spans, D(1), D(7)) == {1: 0}
    assert find_shortages({1: 1}, {1: 1}, spans, D(3), D(5)) == {1: (1, 0)}


def test_max_not_sum():
    # T=2; те же брони: в каждый день занята 1 ⇒ свободна 1 на 1–7,
    # хотя сумма пересекающихся броней = 2.
    spans = [Span(D(1), D(3), {1: 1}), Span(D(5), D(7), {1: 1})]
    assert free_for_range({1: 2}, spans, D(1), D(7)) == {1: 1}
    assert find_shortages({1: 1}, {1: 2}, spans, D(1), D(7)) == {}


def test_overlapping_stack():
    # T=10 (a6400). Брони: 3 шт 1–5, 4 шт 4–8, 2 шт 5–5.
    # День:   1 2 3 4 5 6 7 8
    # занято: 3 3 3 7 9 4 4 4
    spans = [Span(D(1), D(5), {2: 3}), Span(D(4), D(8), {2: 4}), Span(D(5), D(5), {2: 2})]
    u = daily_usage(spans, D(1), D(8))[2]
    assert [u[D(i)] for i in range(1, 9)] == [3, 3, 3, 7, 9, 4, 4, 4]
    assert free_for_range({2: 10}, spans, D(1), D(8)) == {2: 1}
    assert free_for_range({2: 10}, spans, D(6), D(8)) == {2: 6}
    assert find_shortages({2: 2}, {2: 10}, spans, D(5), D(5)) == {2: (2, 1)}


def test_untouched_items_and_edges():
    spans = [Span(D(1), D(1), {1: 1})]
    # Бронь кончается 1-го — 2-го свободно (даты включительно)
    assert free_for_range({1: 1, 5: 3}, spans, D(2), D(3)) == {1: 1, 5: 3}
    assert free_for_range({1: 1}, spans, D(1), D(1)) == {1: 0}
    assert free_for_range({1: 1}, [], D(1), D(1)) == {1: 1}
    with pytest.raises(ValueError):
        daily_usage([], D(2), D(1))


def test_overbooked_after_writeoff():
    spans = [Span(D(1), D(2), {1: 2})]
    assert overbooked_days({1: 1}, spans, D(1), D(5)) == {1: [D(1), D(2)]}
    assert overbooked_days({1: 2}, spans, D(1), D(5)) == {}


def test_issued_overdue_blocks_until_today():
    assert effective_span(D(1), D(3), True, D(10)) == (D(1), D(10))
    assert effective_span(D(1), D(3), False, D(10)) == (D(1), D(3))
    assert effective_span(D(1), D(20), True, D(10)) == (D(1), D(20))


def test_random_against_bruteforce():
    # Независимый оракул: проверяем каждую единицу по дням «в лоб»
    rnd = random.Random(7)
    for _ in range(300):
        total = rnd.randint(1, 5)
        spans = []
        for _ in range(rnd.randint(0, 6)):
            s = D(1) + timedelta(days=rnd.randint(0, 20))
            spans.append(Span(s, s + timedelta(days=rnd.randint(0, 6)), {1: rnd.randint(1, 2)}))
        s = D(1) + timedelta(days=rnd.randint(0, 20))
        e = s + timedelta(days=rnd.randint(0, 6))
        worst = 0
        d = s
        while d <= e:
            worst = max(worst, sum(sp.items[1] for sp in spans if sp.start <= d <= sp.end))
            d += timedelta(days=1)
        assert free_for_range({1: total}, spans, s, e)[1] == total - worst
