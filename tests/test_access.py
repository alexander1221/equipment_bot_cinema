"""Матрица доступа. Оракул — таблица из п. 2 ТЗ, переписанная сюда вручную
(✅/❌ по столбцам A, B, C), а не уровни из app/seed_data.py."""

from datetime import date, timedelta

import pytest

from app import access
from app.seed_data import EQUIPMENT

Y, N, H = True, False, None  # H — «скрыто»
# Наименование (как в seed) → (1 курс с 01.01, 2 курс 01.09–31.12, 2 курс с 01.01 и старшие)
TZ_MATRIX = {
    "Sony FX3": (H, H, H),
    "Sony a6400 Kit 16-50mm II": (Y, Y, Y),
    "Экшн-камера GoPro HERO 13 Black Edition": (N, N, Y),
    "Комплект объективов DZOFilm Y24 Educational Catta Ace 35-80 мм и VESPID 16/25 мм (Arri PL)": (N, N, Y),
    "Sony FE 70-200mm f/2.8 GM OSS II": (Y, Y, Y),
    "Стабилизатор DJI Ronin RS 3 Pro": (Y, Y, Y),
    "Система поддержки DigitalFoto Easyrig ER818": (Y, Y, Y),
    "Плечевой риг Tilta Shoulder Rig облегчённый TA-LSR-B": (Y, Y, Y),
    "Система поддержки DigitalFoto DA118 Serene для Easyrig (1-18кг)": (Y, Y, Y),
    "Штатив QZSD Q338 3D": (Y, Y, Y),
    "Штатив Sirui SQ100 с головой VHS18": (N, N, Y),
    "Видеомонитор-рекордер накамерный Atomos Ninja V": (N, N, Y),
    "Режиссерский монитор Lilliput BM150-4KS": (N, N, Y),
    "Видеосендер DJI Transmission (High-Bright Monitor Combo)": (N, N, Y),
    "Радиофокус Tilta Nucleus Nano II WLC-T05": (N, N, Y),
    "Amaran Aputure MC Pro": (N, N, Y),
    "Godox TL60": (N, N, Y),
    "Godox S60Bi": (N, N, Y),
    'Комплект флагов KUPO KT-2436K 24"X36" OPEN END NET KITS': (N, N, Y),
    "C-Stand KUPO CS-20MK Kits": (N, N, Y),
    "C-Stand KUPO CS-40MK Kits": (N, N, Y),
    "Микрофон-пушка Rode NTG-3B конденсаторный": (N, Y, Y),
    "Портативный рекордер Zoom H6e Essential": (N, Y, Y),
    "Радиосистема Deity THEOS (RX + 2 TX) + кейс Deity THEOS Safe Case": (N, Y, Y),
    "Адаптер DZOFilm Octopus Arri PL - Sony E": (N, N, Y),
}

# Количества из файла «Оборудование ITHub» (лист 1)
XLSX_QTY = {"Sony a6400 Kit 16-50mm II": 10, "Sony FE 70-200mm f/2.8 GM OSS II": 2,
            "Штатив QZSD Q338 3D": 5, "Godox TL60": 2, "Godox S60Bi": 5,
            "C-Stand KUPO CS-20MK Kits": 5, "C-Stand KUPO CS-40MK Kits": 5}

# Представители столбцов: (курс, год регистрации, дата)
COL_A = (1, 2026, date(2027, 1, 15))   # 1 курс после 1 января
COL_B = (2, 2026, date(2026, 10, 22))  # 2 курс осенью
COL_C = (2, 2026, date(2027, 2, 1))    # 2 курс после 1 января
COL_C2 = (4, 2026, date(2026, 10, 22))  # старший курс


def test_seed_covers_matrix_exactly():
    assert {e[1] for e in EQUIPMENT} == set(TZ_MATRIX)
    for _cat, name, qty, _t, _h in EQUIPMENT:
        assert qty == XLSX_QTY.get(name, 1), name


@pytest.mark.parametrize("col_idx,col", [(0, COL_A), (1, COL_B), (2, COL_C), (2, COL_C2)])
def test_matrix_matches_tz(col_idx, col):
    level = access.student_level(*col)
    for _cat, name, _q, tier, hidden in EQUIPMENT:
        expected = TZ_MATRIX[name][col_idx]
        got = access.is_allowed(tier, hidden, level)
        assert got == bool(expected), f"{name}: column {col_idx}"


def test_first_year_blocked_before_january():
    # «Если выбран 1 курс — дата начала должна быть не ранее 1 января»
    assert access.student_level(1, 2026, date(2026, 12, 31)) == 0
    assert access.student_level(1, 2026, date(2027, 1, 1)) == 1
    assert access.first_allowed_date(1, 2026, date(2026, 10, 22)) == date(2027, 1, 1)
    assert access.first_allowed_date(2, 2026, date(2026, 10, 22)) == date(2026, 10, 22)


def test_course_grows_on_september_first():
    assert access.effective_course(1, 2026, date(2027, 8, 31)) == 1
    assert access.effective_course(1, 2026, date(2027, 9, 1)) == 2
    assert access.student_level(1, 2026, date(2027, 9, 1)) == 2


@pytest.mark.parametrize("course", [1, 2, 3, 4])
def test_level_is_monotone(course):
    # Свойство из §2 access.py: L(d) не убывает ⇒ min по отрезку = L(начала)
    d, prev = date(2026, 9, 1), -1
    while d < date(2030, 9, 1):
        lv = access.student_level(course, 2026, d)
        assert lv >= prev, d
        prev = lv
        d += timedelta(days=1)


def test_invalid_course():
    with pytest.raises(ValueError):
        access.effective_course(0, 2026, date(2026, 10, 1))


@pytest.mark.parametrize("q,hit", [
    ("Sony FX3", True), ("fx30", True), ("ФХ3", True), ("FX 3 нужна", True), ("fx-3", True),
    ("a6400", False), ("Godox TL60", False), ("", False),
])
def test_hidden_query(q, hit):
    assert access.is_hidden_query(q) is hit
