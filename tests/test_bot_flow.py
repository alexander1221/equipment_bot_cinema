"""Сквозные сценарии бота по пп. 3.1–3.3 ТЗ через поддельный Telegram.

Сегодня (часы теста) — 22.10.2026, четверг.
"""

from datetime import date

from aiogram.methods import EditMessageText, SendMessage
from sqlalchemy import select

from app import access, services
from app.models import Role, Staff, Status

from .conftest import button, last_markup, markup_of

TEACHER, SPEC, STUDENT, FRESHMAN, STUDENT2 = 100, 200, 300, 400, 500


async def bind(h, uid: int, role: Role, name: str | None = None) -> Staff:
    async with h.sm() as s:
        q = select(Staff).where(Staff.role == role)
        if name:
            q = q.where(Staff.full_name == name)
        st = (await s.scalars(q)).first()
    await h.text(uid, f"/start inv_{st.invite_token}")
    return st


async def register(h, uid: int, course: int, fio: str, verify: bool = True) -> list:
    calls = await h.text(uid, "/start")
    await h.click(uid, button(calls, f"{course} курс"))
    await h.text(uid, fio)
    calls = await h.text(uid, "КП-21")
    assert "отправлен на проверку" in h.sent_to(uid, calls)[0].text
    if verify:  # подтверждение специалистом (сам сценарий кнопок — в test_verification_*)
        async with h.sm() as s:
            st = await services.get_student(s, uid)
            await services.verify_student(s, st.id, course, "test", h.clock())
    return calls


def exact(calls, text: str) -> str:
    return [d for t, d in last_markup(calls) if t == text][0]


async def pick_dates(h, uid: int, start: date, end: date) -> list:
    from app.texts import MONTHS_NOM

    calls = await h.text(uid, "📝 Новая заявка")
    # Листаем календарь до месяца начала брони
    while f"{MONTHS_NOM[start.month - 1]} {start.year}" not in [t for t, _ in last_markup(calls)]:
        calls = await h.click(uid, exact(calls, "›"))
    calls = await h.click(uid, exact(calls, str(start.day)))
    if start == end:
        return await h.click(uid, button(calls, "Только один день"))
    while f"{MONTHS_NOM[end.month - 1]} {end.year}" not in [t for t, _ in last_markup(calls)]:
        calls = await h.click(uid, exact(calls, "›"))
    return await h.click(uid, exact(calls, str(end.day)))


async def build_request(h, uid: int, teacher: str, start: date, end: date, title: str) -> list:
    calls = await pick_dates(h, uid, start, end)
    calls = await h.click(uid, button(calls, teacher))
    return await h.text(uid, title)


def item_names(calls) -> list[str]:
    return [t for t, _ in last_markup(calls)]


async def test_full_flow(h):
    await bind(h, TEACHER, Role.TEACHER, "Егорычев")
    await bind(h, SPEC, Role.SPECIALIST)
    await register(h, STUDENT, 2, "сидоров пётр")

    calls = await build_request(h, STUDENT, "Егорычев", date(2026, 10, 25), date(2026, 10, 27),
                                "Курсовой фильм «Ветер»")
    names = " | ".join(item_names(calls))
    # 2 курс осенью = столбец B матрицы: базовая техника и звук, без GoPro и без FX3
    assert "Sony a6400" in names and "Rode NTG-3B" in names
    assert "GoPro" not in names and "FX3" not in names and "Godox" not in names

    calls = await h.click(STUDENT, button(calls, "Sony a6400"))
    calls = await h.click(STUDENT, button(calls, "+"))
    assert any("Sony a6400" in t and "× 2" in t for t in item_names(calls))
    calls = await h.click(STUDENT, button(calls, "Rode NTG-3B"))
    calls = await h.click(STUDENT, button(calls, "Далее"))
    calls = await h.click(STUDENT, button(calls, "Отправить на согласование"))

    # п. 3.1.6: статус «Ожидает подтверждения преподавателя»
    edits = [c for c in calls if isinstance(c, EditMessageText) and c.chat_id == STUDENT]
    assert "Ожидает подтверждения преподавателя" in edits[-1].text
    # п. 3.2.1: уведомление преподавателю с кнопками
    tmsg = h.sent_to(TEACHER, calls)[-1]
    assert "Сидоров Пётр" in tmsg.text and "2 курс" in tmsg.text
    assert "25.10.2026" in tmsg.text and "27.10.2026" in tmsg.text and "Подтверждаете?" in tmsg.text
    assert [t for t, _ in markup_of(tmsg)] == ["✅ Подтверждаю", "❌ Отклоняю"]

    # Чужой преподаватель не может согласовать
    other = await bind(h, 101, Role.TEACHER, "Григорьев")
    assert other
    alerts = h.alerts(await h.click(101, button([markup_of(tmsg)[0]], "Подтверждаю")))
    assert "другой преподаватель" in alerts[0]

    # п. 3.2.3: подтверждение → специалисту
    calls = await h.click(TEACHER, markup_of(tmsg)[0][1])
    smsg = h.sent_to(SPEC, calls)[-1]
    assert "согласована преподавателем" in smsg.text and "Егорычев" in smsg.text
    assert "подтверждена" in h.sent_to(STUDENT, calls)[-1].text

    # п. 3.3.2: шаблон с датой/временем/местом, меняем время на 14:30
    calls = await h.click(SPEC, button([markup_of(smsg)[0]], "Назначить"))
    draft = [c for c in calls if isinstance(c, EditMessageText)][-1]
    assert "ауд. 106.1" in draft.text and "14:00" in draft.text and "25 октября" in draft.text
    calls = await h.click(SPEC, button(calls, "Время"))
    calls = await h.click(SPEC, button(calls, "14:30"))
    calls = await h.click(SPEC, button(calls, "Отправить студенту"))
    final = h.sent_to(STUDENT, calls)[-1].text
    # п. 3.3.3: студенту — где и когда забрать
    assert "25 октября" in final and "14:30" in final and "ауд. 106.1" in final

    calls = await h.click(SPEC, button(calls, "выдано"))
    assert "выдано" in h.sent_to(STUDENT, calls)[-1].text
    calls = await h.click(SPEC, button(calls, "возвращено"))
    assert "Возврат" in h.sent_to(STUDENT, calls)[-1].text

    async with h.sm() as s:
        b = await services.get_booking(s, 1)
        assert b.status == Status.RETURNED
        assert {it.equipment.name: it.quantity for it in b.items} == {
            "Sony a6400 Kit 16-50mm II": 2, "Микрофон-пушка Rode NTG-3B конденсаторный": 1}


async def test_fx3_hidden_message(h):
    await register(h, STUDENT, 3, "Иванов Иван")
    for q in ("Sony FX3", "есть fx30?"):
        calls = await h.text(STUDENT, q)
        assert h.sent_to(STUDENT, calls)[-1].text == access.HIDDEN_MESSAGE


async def test_freshman_before_january(h):
    await register(h, FRESHMAN, 1, "Новиков Олег")
    calls = await h.text(FRESHMAN, "📝 Новая заявка")
    msg = h.sent_to(FRESHMAN, calls)[-1]
    assert "01.01.2027" in msg.text
    # Сетка открывается на январе 2027, октябрьских дней нет
    texts = [t for t, _ in markup_of(msg)]
    assert "Январь 2027" in texts


async def test_conflict_and_teacher_reject(h):
    await bind(h, TEACHER, Role.TEACHER, "Егорычев")
    await register(h, STUDENT, 2, "Сидоров Пётр")
    await register(h, STUDENT2, 2, "Кузнецова Мария")
    d = date(2026, 11, 2)

    # Оба собирают заявку на единственный Zoom H6e
    c1 = await build_request(h, STUDENT, "Егорычев", d, d, "Документальный этюд")
    c1 = await h.click(STUDENT, button(c1, "Zoom H6e"))
    c1 = await h.click(STUDENT, button(c1, "Далее"))
    c2 = await build_request(h, STUDENT2, "Егорычев", d, d, "Интервью")
    c2 = await h.click(STUDENT2, button(c2, "Zoom H6e"))
    c2 = await h.click(STUDENT2, button(c2, "Далее"))

    await h.click(STUDENT, button(c1, "Отправить"))
    # п. 3.1.5: второй получает ошибку и предложение изменить даты/состав
    c2 = await h.click(STUDENT2, button(c2, "Отправить"))
    err = [c for c in c2 if isinstance(c, EditMessageText)][-1]
    assert "занято" in err.text and "Zoom H6e" in err.text
    assert any("даты" in t for t, _ in markup_of(err))

    # Преподаватель отклоняет первую заявку — Zoom снова свободен
    tmsg = h.sent_to(TEACHER)[-1]
    calls = await h.click(TEACHER, markup_of(tmsg)[1][1])
    assert "отклонена" in h.sent_to(STUDENT, calls)[-1].text
    c2 = await h.click(STUDENT2, button(c2, "Изменить состав"))
    assert any("Zoom H6e" in t for t in item_names(c2))


async def test_internal_booking_blocks_students(h, clock):
    await register(h, STUDENT, 4, "Орлова Вера")
    async with h.sm() as s:
        eqs = {e.name: e.id for e in await services.equipment_list(s)}
        await services.create_internal(s, "Мастер-класс по свету", date(2026, 11, 10), date(2026, 11, 12),
                                       {eqs["Amaran Aputure MC Pro"]: 1, eqs["Godox TL60"]: 2},
                                       "test", clock())
    calls = await build_request(h, STUDENT, "Егорычев", date(2026, 11, 11), date(2026, 11, 11), "Короткий метр")
    names = " | ".join(item_names(calls))
    assert "Amaran" not in names and "Godox TL60" not in names
    assert "Godox S60Bi" in names  # 4 курс видит свет, S60Bi не занят


async def test_pending_expiry(h, clock):
    from datetime import timedelta

    from app import jobs

    await bind(h, TEACHER, Role.TEACHER, "Егорычев")
    await register(h, STUDENT, 2, "Сидоров Пётр")
    c = await build_request(h, STUDENT, "Егорычев", date(2026, 11, 2), date(2026, 11, 3), "Этюд")
    c = await h.click(STUDENT, button(c, "Штатив QZSD"))
    c = await h.click(STUDENT, button(c, "Далее"))
    await h.click(STUDENT, button(c, "Отправить"))
    clock.now += timedelta(hours=49)
    n = len(h.session.calls)
    await jobs.run_once(h.bot, h.sm, h.settings, clock)
    assert "не ответил" in h.sent_to(STUDENT, h.session.calls[n:])[-1].text
    async with h.sm() as s:
        assert (await services.get_booking(s, 1)).status == Status.EXPIRED


async def test_unknown_staff_link(h):
    calls = await h.text(999, "/start inv_wrong")
    assert "недействительна" in h.sent_to(999, calls)[-1].text
    assert all(isinstance(c, SendMessage) for c in calls)
