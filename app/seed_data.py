"""Начальный состав инвентаря и преподавателей.

Источник: файл «Оборудование ITHub» (лист 1) и матрица доступа из п. 2 ТЗ.
access_tier: 1 = ✅✅✅, 2 = ❌✅✅, 3 = ❌❌✅ (см. app/access.py).
Категории «Хранение и передача данных» и «АКБ и зарядные устройства» в
исходной таблице пустые — их можно наполнить позже в веб-панели.
После первого запуска источником правды является БД, а не этот файл.
"""

from __future__ import annotations

# (категория, наименование, количество, tier, hidden)
EQUIPMENT: list[tuple[str, str, int, int, bool]] = [
    ("Камеры", "Sony FX3", 1, 3, True),
    ("Камеры", "Sony a6400 Kit 16-50mm II", 10, 1, False),
    ("Камеры", "Экшн-камера GoPro HERO 13 Black Edition", 1, 3, False),
    ("Объективы", "Комплект объективов DZOFilm Y24 Educational Catta Ace 35-80 мм и VESPID 16/25 мм (Arri PL)", 1, 3, False),
    ("Объективы", "Sony FE 70-200mm f/2.8 GM OSS II", 2, 1, False),
    ("Системы стабилизации", "Стабилизатор DJI Ronin RS 3 Pro", 1, 1, False),
    ("Системы стабилизации", "Система поддержки DigitalFoto Easyrig ER818", 1, 1, False),
    ("Системы стабилизации", "Плечевой риг Tilta Shoulder Rig облегчённый TA-LSR-B", 1, 1, False),
    ("Системы стабилизации", "Система поддержки DigitalFoto DA118 Serene для Easyrig (1-18кг)", 1, 1, False),
    ("Штативы для камер", "Штатив QZSD Q338 3D", 5, 1, False),
    ("Штативы для камер", "Штатив Sirui SQ100 с головой VHS18", 1, 3, False),
    ("Мониторы", "Видеомонитор-рекордер накамерный Atomos Ninja V", 1, 3, False),
    ("Мониторы", "Режиссерский монитор Lilliput BM150-4KS", 1, 3, False),
    ("Видеосендеры", "Видеосендер DJI Transmission (High-Bright Monitor Combo)", 1, 3, False),
    ("Фоллоу-фокус", "Радиофокус Tilta Nucleus Nano II WLC-T05", 1, 3, False),
    ("Осветительные приборы", "Amaran Aputure MC Pro", 1, 3, False),
    ("Осветительные приборы", "Godox TL60", 2, 3, False),
    ("Осветительные приборы", "Godox S60Bi", 5, 3, False),
    ("Плоскота", 'Комплект флагов KUPO KT-2436K 24"X36" OPEN END NET KITS', 1, 3, False),
    ("Стойки для света", "C-Stand KUPO CS-20MK Kits", 5, 3, False),
    ("Стойки для света", "C-Stand KUPO CS-40MK Kits", 5, 3, False),
    ("Звук", "Микрофон-пушка Rode NTG-3B конденсаторный", 1, 2, False),
    ("Звук", "Портативный рекордер Zoom H6e Essential", 1, 2, False),
    ("Звук", "Радиосистема Deity THEOS (RX + 2 TX) + кейс Deity THEOS Safe Case", 1, 2, False),
    ("Аксессуары", "Адаптер DZOFilm Octopus Arri PL - Sony E", 1, 3, False),
]

# Из п. 3.1.3 ТЗ. Полные ФИО можно дописать в веб-панели («Сотрудники»).
TEACHERS = ["Егорычев", "Григорьев", "Арсентьев", "Чинилин", "Карчава"]
