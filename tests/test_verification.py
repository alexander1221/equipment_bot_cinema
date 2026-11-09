"""Проверка студентов специалистом: курс и ФИО — со слов студента, поэтому
заявки возможны только после подтверждения, а любая правка профиля его снимает."""

from datetime import date

import pytest
from sqlalchemy import text

from app import services
from app.db import init_db, make_engine
from app.models import Role, StudentStatus

from .conftest import button, markup_of
from .test_bot_flow import SPEC, STUDENT, TEACHER, bind, build_request, register

FRESH = 600


async def test_unverified_student_cannot_book(h):
    await register(h, STUDENT, 2, "Сидоров Пётр", verify=False)
    calls = await h.text(STUDENT, "📝 Новая заявка")
    assert "не подтверждён" in h.sent_to(STUDENT, calls)[-1].text
    async with h.sm() as s:
        st = await services.get_student(s, STUDENT)
        t = (await services.teachers(s))[0]
        with pytest.raises(services.BookingError, match="не подтверждён"):
            await services.create_student_booking(s, h.settings, st, t.id, date(2026, 10, 26),
                                                  date(2026, 10, 26), "x", {2: 1}, h.clock())


async def test_specialist_fixes_claimed_course_in_bot(h):
    await bind(h, SPEC, Role.SPECIALIST)
    # Первокурсник выдаёт себя за 4 курс
    calls = await register(h, FRESH, 4, "Новиков Олег", verify=False)
    card = h.sent_to(SPEC, calls)[-1]
    assert "Новиков Олег" in card.text and "КП-21" in card.text and "4" in card.text
    assert any("Подтвердить, 4 курс" in t for t, _ in markup_of(card))
    calls = await h.click(SPEC, button([markup_of(card)[1]], "Курс другой"))
    calls = await h.click(SPEC, button(calls, "1 курс"))
    assert "1 курс" in h.sent_to(FRESH, calls)[-1].text
    # Теперь бот считает его первокурсником: бронь только с 1 января
    calls = await h.text(FRESH, "📝 Новая заявка")
    assert "01.01.2027" in h.sent_to(FRESH, calls)[-1].text


async def test_specialist_rejects(h):
    await bind(h, SPEC, Role.SPECIALIST)
    calls = await register(h, FRESH, 3, "Неизвестный Человек", verify=False)
    card = h.sent_to(SPEC, calls)[-1]
    calls = await h.click(SPEC, button([markup_of(card)[2]], "Отклонить"))
    assert "не подтверждена" in h.sent_to(FRESH, calls)[-1].text
    calls = await h.text(FRESH, "📝 Новая заявка")
    assert "закрыт" in h.sent_to(FRESH, calls)[-1].text


async def test_not_a_specialist_cannot_verify(h):
    await bind(h, SPEC, Role.SPECIALIST)
    calls = await register(h, FRESH, 4, "Новиков Олег", verify=False)
    ok_btn = markup_of(h.sent_to(SPEC, calls)[-1])[0][1]
    calls = await h.click(FRESH, ok_btn)  # студент пытается подтвердить сам себя
    assert "только специалисту" in h.alerts(calls)[0]
    async with h.sm() as s:
        assert (await services.get_student(s, FRESH)).status == StudentStatus.PENDING


async def test_course_change_resets_verification(h):
    await bind(h, SPEC, Role.SPECIALIST)
    await register(h, STUDENT, 1, "Сидоров Пётр")
    calls = await h.text(STUDENT, "/profile")
    calls = await h.click(STUDENT, button(calls, "Изменить"))
    calls = await h.click(STUDENT, button(calls, "4 курс"))
    await h.text(STUDENT, "Сидоров Пётр")
    calls = await h.text(STUDENT, "КП-21")
    assert "Новый студент" in h.sent_to(SPEC, calls)[-1].text
    async with h.sm() as s:
        assert (await services.get_student(s, STUDENT)).status == StudentStatus.PENDING
    calls = await h.text(STUDENT, "📝 Новая заявка")
    assert "не подтверждён" in h.sent_to(STUDENT, calls)[-1].text


async def test_teacher_sees_verified_mark(h):
    await bind(h, TEACHER, Role.TEACHER, "Егорычев")
    await register(h, STUDENT, 2, "Сидоров Пётр")
    c = await build_request(h, STUDENT, "Егорычев", date(2026, 11, 2), date(2026, 11, 2), "Этюд")
    c = await h.click(STUDENT, button(c, "Штатив QZSD"))
    c = await h.click(STUDENT, button(c, "Далее"))
    calls = await h.click(STUDENT, button(c, "Отправить"))
    msg = h.sent_to(TEACHER, calls)[-1].text
    assert "подтверждены кафедрой" in msg and "КП-21" in msg and "@user300" in msg


async def test_migration_adds_columns_to_old_db(tmp_path):
    # База версии 1.0.0: таблица students без новых столбцов, в ней уже есть студент
    url = f"sqlite+aiosqlite:///{(tmp_path / 'old.db').as_posix()}"
    e = make_engine(url)
    async with e.begin() as conn:
        await conn.execute(text(
            "CREATE TABLE students (id INTEGER PRIMARY KEY, tg_id BIGINT UNIQUE, username VARCHAR(100),"
            " full_name VARCHAR(200), course INTEGER, course_year INTEGER, created_at DATETIME)"))
        await conn.execute(text("INSERT INTO students VALUES (1, 5, 'u', 'Старый Студент', 4, 2026,"
                                " '2026-10-01 10:00:00')"))
    await init_db(e)
    await init_db(e)  # повторный запуск не должен падать
    from app.db import make_sessionmaker
    async with make_sessionmaker(e)() as s:
        st = await services.get_student(s, 5)
        # Курс старого студента никто не проверял — он должен пройти проверку
        assert st.status == StudentStatus.PENDING and st.group_name == "" and st.full_name == "Старый Студент"
    await e.dispose()
