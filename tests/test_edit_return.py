"""Изменение брони специалистом, досрочный и частичный возврат.

Ожидаемая занятость считается вручную от количеств из списка ITHub
(a6400 — 10 шт., Zoom H6e — 1 шт.)."""

from datetime import date

import pytest
from aiogram.methods import SendMessage

from app import notify, services
from app.models import Role, Status

from .conftest import button, markup_of
from .test_bot_flow import SPEC, STUDENT, TEACHER, bind, register
from .test_web import ids, login, web  # noqa: F401 — фикстура web


async def make_booking(sm, settings, clock, items: dict[str, int], start, end, tg=777, status=None):
    async with sm() as s:
        st = await services.get_student(s, tg) or await services.save_student(
            s, tg, "Сидоров Пётр", 3, "sid", clock(), auto_verify=True)
        t = (await services.teachers(s))[0]
        eq = {e.name: e.id for e in await services.equipment_list(s)}
        b = await services.create_student_booking(s, settings, st, t.id, start, end, "Этюд",
                                                  {eq[n]: q for n, q in items.items()}, clock())
        if status in (Status.APPROVED, Status.ISSUED):
            await services.decide(s, settings, b.id, True, "t", clock())
        if status == Status.ISSUED:
            await services.mark_issued(s, b.id, clock())
        return b.id, eq


async def free(sm, eq_id, d, today):
    async with sm() as s:
        spans = await services.load_spans(s, d, d, today)
        tot = await services.totals(s)
    from app.availability import free_for_range
    return free_for_range({eq_id: tot[eq_id]}, spans, d, d)[eq_id]


async def test_early_return_frees_equipment(engine, settings, clock):
    from app.db import make_sessionmaker
    sm = make_sessionmaker(engine)
    bid, eq = await make_booking(sm, settings, clock, {"Sony a6400 Kit 16-50mm II": 3},
                                 date(2026, 10, 22), date(2026, 10, 31), status=Status.ISSUED)
    cam = eq["Sony a6400 Kit 16-50mm II"]
    assert await free(sm, cam, date(2026, 10, 28), clock().date()) == 7
    async with sm() as s:
        await services.mark_returned(s, bid, clock())  # 22.10, срок был до 31.10
    assert await free(sm, cam, date(2026, 10, 28), clock().date()) == 10


async def test_partial_return_and_extension(engine, settings, clock):
    from app.db import make_sessionmaker
    sm = make_sessionmaker(engine)
    bid, eq = await make_booking(sm, settings, clock, {"Sony a6400 Kit 16-50mm II": 3},
                                 date(2026, 10, 22), date(2026, 10, 25), status=Status.ISSUED)
    cam = eq["Sony a6400 Kit 16-50mm II"]
    async with sm() as s:
        # Частичный возврат: 1 камера осталась у студента, срок продлён до 28.10
        b, desc = await services.update_booking(s, bid, date(2026, 10, 22), date(2026, 10, 28),
                                                {cam: 1}, "spec", clock())
    assert b.status == Status.ISSUED and "3 → 1" in desc and "25.10" in desc and "28.10" in desc
    assert await free(sm, cam, date(2026, 10, 27), clock().date()) == 9
    async with sm() as s:
        with pytest.raises(services.BookingError, match="дату начала"):
            await services.update_booking(s, bid, date(2026, 10, 23), date(2026, 10, 28), {cam: 1}, "x", clock())


async def test_extension_blocked_by_other_booking(engine, settings, clock):
    from app.db import make_sessionmaker
    sm = make_sessionmaker(engine)
    b1, eq = await make_booking(sm, settings, clock, {"Портативный рекордер Zoom H6e Essential": 1},
                                date(2026, 10, 26), date(2026, 10, 27), status=Status.APPROVED)
    await make_booking(sm, settings, clock, {"Портативный рекордер Zoom H6e Essential": 1},
                       date(2026, 10, 29), date(2026, 10, 30), tg=778)
    zoom = eq["Портативный рекордер Zoom H6e Essential"]
    async with sm() as s:
        with pytest.raises(services.BookingError, match="занято"):
            await services.update_booking(s, b1, date(2026, 10, 26), date(2026, 10, 29), {zoom: 1}, "x", clock())
        # До 28-го свободно — продление проходит; сама бронь себе не мешает
        b, _ = await services.update_booking(s, b1, date(2026, 10, 26), date(2026, 10, 28), {zoom: 1}, "x", clock())
        assert b.end_date == date(2026, 10, 28) and "Изменено" in b.comment
        with pytest.raises(services.BookingError, match="Ничего"):
            await services.update_booking(s, b1, date(2026, 10, 26), date(2026, 10, 28), {zoom: 1}, "x", clock())
        with pytest.raises(services.BookingError, match="Отмените"):
            await services.update_booking(s, b1, date(2026, 10, 26), date(2026, 10, 28), {zoom: 0}, "x", clock())


async def test_panel_edit_notifies_student(web, settings, clock):  # noqa: F811
    client, sm, session = web
    login(client)
    bid, eq = await make_booking(sm, settings, clock, {"Штатив QZSD Q338 3D": 1},
                                 date(2026, 10, 26), date(2026, 10, 27), status=Status.APPROVED)
    tripod, cam = eq["Штатив QZSD Q338 3D"], eq["Sony a6400 Kit 16-50mm II"]
    r = client.get(f"/requests/{bid}")
    assert "Изменить даты или состав" in r.text
    r = client.post(f"/requests/{bid}/edit", data={"start": "2026-10-26", "end": "2026-10-29",
                                                   f"q_{tripod}": "2", f"q_{cam}": "1"})
    assert "Изменено" in r.text and "студент уведомлён" in r.text
    msgs = [c.text for c in session.calls if isinstance(c, SendMessage) and c.chat_id == 777]
    assert any("изменена специалистом" in m and "29.10" in m and "Sony a6400" in m for m in msgs)


async def test_pending_edit_resends_to_teacher(h):
    await bind(h, TEACHER, Role.TEACHER, "Егорычев")
    await register(h, STUDENT, 2, "Сидоров Пётр")
    async with h.sm() as s:
        st = await services.get_student(s, STUDENT)
        t = next(x for x in await services.teachers(s) if x.full_name == "Егорычев")
        eq = {e.name: e.id for e in await services.equipment_list(s)}
        b = await services.create_student_booking(s, h.settings, st, t.id, date(2026, 11, 2), date(2026, 11, 2),
                                                  "Этюд", {eq["Штатив QZSD Q338 3D"]: 1}, h.clock())
        await notify.new_request(h.bot, s, h.settings, b)
        n = len(h.session.calls)
        b, desc = await services.update_booking(s, b.id, date(2026, 11, 2), date(2026, 11, 3),
                                                {eq["Штатив QZSD Q338 3D"]: 2}, "spec", h.clock())
        await notify.changed(h.bot, s, h.settings, b, desc)
    new = h.sent_to(TEACHER, h.session.calls[n:])
    assert new and "03.11.2026" in new[-1].text and "× 2" in new[-1].text
    assert [t for t, _ in markup_of(new[-1])] == ["✅ Подтверждаю", "❌ Отклоняю"]


async def test_student_early_return_request(h):
    await bind(h, SPEC, Role.SPECIALIST)
    await register(h, STUDENT, 2, "Сидоров Пётр")
    async with h.sm() as s:
        st = await services.get_student(s, STUDENT)
        t = (await services.teachers(s))[0]
        eq = {e.name: e.id for e in await services.equipment_list(s)}
        b = await services.create_student_booking(s, h.settings, st, t.id, date(2026, 10, 22), date(2026, 10, 30),
                                                  "Этюд", {eq["Штатив QZSD Q338 3D"]: 1}, h.clock())
        await services.decide(s, h.settings, b.id, True, "t", h.clock())
        await services.mark_issued(s, b.id, h.clock())
    calls = await h.text(STUDENT, "📋 Мои заявки")
    calls = await h.click(STUDENT, button(calls, "Хочу вернуть раньше"))
    spec_msg = h.sent_to(SPEC, calls)[-1]
    assert "досрочно" in spec_msg.text and "Сидоров Пётр" in spec_msg.text
    assert "после проверки" in h.sent_to(STUDENT, calls)[-1].text
    async with h.sm() as s:  # сам по себе запрос ничего не меняет
        assert (await services.get_booking(s, b.id)).status == Status.ISSUED
    calls = await h.click(SPEC, button([markup_of(spec_msg)[0]], "возвращено"))
    assert "Возврат" in h.sent_to(STUDENT, calls)[-1].text
    async with h.sm() as s:
        assert (await services.get_booking(s, b.id)).status == Status.RETURNED
