from datetime import date

import pytest

from app.validation import check_dates, normalize_fio, parse_time


@pytest.mark.parametrize("raw,expected", [
    ("иванов иван", "Иванов Иван"),
    ("  ПЕТРОВА   анна  сергеевна ", "Петрова Анна Сергеевна"),
    ("петров-водкин кузьма", "Петров-Водкин Кузьма"),
    ("Smith John", "Smith John"),
    ("Ёлкин Пётр", "Ёлкин Пётр"),
])
def test_fio_ok(raw, expected):
    assert normalize_fio(raw) == expected


@pytest.mark.parametrize("raw", ["Иванов", "", "Иванов 1ван", "Иванов И.", "Иванов И",
                                 "Иванов Иван 😀", "Иванов--Петров Иван", "а б в г д е"])
def test_fio_bad(raw):
    with pytest.raises(ValueError):
        normalize_fio(raw)


T = date(2026, 10, 22)


def test_dates_rules():
    ok = dict(today=T, max_days=14, max_advance=90)
    assert check_dates(date(2026, 10, 22), date(2026, 11, 4), **ok) is None  # ровно 14 дней
    assert "14" in check_dates(date(2026, 10, 22), date(2026, 11, 5), **ok)  # 15 дней
    assert "прошла" in check_dates(date(2026, 10, 21), date(2026, 10, 22), **ok)
    assert "раньше" in check_dates(date(2026, 10, 25), date(2026, 10, 24), **ok)
    assert "90" in check_dates(date(2027, 1, 21), date(2027, 1, 22), **ok)
    assert "1 января" in check_dates(date(2026, 11, 1), date(2026, 11, 2), level=0, **ok)


@pytest.mark.parametrize("raw,exp", [("14:30", "14:30"), ("9.05", "09:05"), ("18 00", "18:00")])
def test_time_ok(raw, exp):
    assert parse_time(raw) == exp


@pytest.mark.parametrize("raw", ["25:00", "14:61", "6:00", "abc", "22:30"])
def test_time_bad(raw):
    with pytest.raises(ValueError):
        parse_time(raw)
