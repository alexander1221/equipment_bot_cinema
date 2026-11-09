"""Optional teacher comments: delivery, authorization and stale requests."""
from datetime import date

import pytest

from app import notify, services
from app.bot import keyboards as kb
from app.models import Role, Status
from .conftest import button
from .test_bot_flow import bind, register, TEACHER, STUDENT, SPEC


async def pending(h):
    teacher = await bind(h, TEACHER, Role.TEACHER, "Егорычев")
    await bind(h, SPEC, Role.SPECIALIST)
    await register(h, STUDENT, 2, "Сидоров Пётр")
    async with h.sm() as s:
        student = await services.get_student(s, STUDENT)
        equipment = next(e for e in await services.equipment_list(s)
                         if e.name == "Sony a6400 Kit 16-50mm II")
        b = await services.create_student_booking(
            s, h.settings, student, teacher.id, date(2026, 11, 2), date(2026, 11, 2),
            "Этюд", {equipment.id: 1}, h.clock())
        await notify.new_request(h.bot, s, h.settings, b)
        return b.id


@pytest.mark.parametrize("approve", [True, False])
async def test_decision_with_comment(h, approve):
    bid = await pending(h)
    await h.click(TEACHER, kb.DecisionCommentCb(booking=bid, ok=approve).pack())
    async with h.sm() as s:
        assert (await services.get_booking(s, bid)).status == Status.PENDING
    comment = 'Нужно <уточнить> план & сроки'
    calls = await h.text(TEACHER, comment)
    async with h.sm() as s:
        b = await services.get_booking(s, bid)
        assert b.status == (Status.APPROVED if approve else Status.REJECTED)
        assert b.comment == comment
    assert "&lt;уточнить&gt; план &amp; сроки" in h.sent_to(STUDENT, calls)[-1].text
    if approve:
        assert "&lt;уточнить&gt;" in h.sent_to(SPEC, calls)[-1].text
    # A later message must not change the accepted decision.
    await h.text(TEACHER, "Ещё текст")
    async with h.sm() as s:
        assert (await services.get_booking(s, bid)).comment == comment


@pytest.mark.parametrize("approve", [True, False])
async def test_skip_comment(h, approve):
    bid = await pending(h)
    calls = await h.click(TEACHER, kb.DecisionCommentCb(booking=bid, ok=approve).pack())
    calls = await h.click(TEACHER, button(calls, "Без комментария"))
    assert "Комментарий:" not in h.sent_to(STUDENT, calls)[-1].text
    async with h.sm() as s:
        b = await services.get_booking(s, bid)
        assert b.status == (Status.APPROVED if approve else Status.REJECTED)
        assert b.comment == ""


async def test_cancel_comment(h):
    bid = await pending(h)
    calls = await h.click(TEACHER, kb.DecisionCommentCb(booking=bid, ok=False).pack())
    await h.click(TEACHER, button(calls, "Отмена"))
    await h.text(TEACHER, "Не принимать решение")
    async with h.sm() as s:
        assert (await services.get_booking(s, bid)).status == Status.PENDING


async def test_comment_permissions_and_stale_decision(h):
    bid = await pending(h)
    cb = kb.DecisionCommentCb(booking=bid, ok=False).pack()
    assert "другой преподаватель" in h.alerts(await h.click(STUDENT, cb))[0]
    await h.click(TEACHER, cb)
    # Another authorized user decides while the teacher is typing.
    await h.click(SPEC, kb.DecideCb(booking=bid, ok=True).pack())
    calls = await h.text(TEACHER, "Поздний отказ")
    assert not h.sent_to(STUDENT, calls)
    async with h.sm() as s:
        b = await services.get_booking(s, bid)
        assert b.status == Status.APPROVED and b.comment == ""


async def test_invalid_comment_keeps_request_pending(h):
    bid = await pending(h)
    await h.click(TEACHER, kb.DecisionCommentCb(booking=bid, ok=True).pack())
    for text in ("   ", "а" * 501):
        calls = await h.text(TEACHER, text)
        assert "от 1 до 500" in h.sent_to(TEACHER, calls)[-1].text
    async with h.sm() as s:
        assert (await services.get_booking(s, bid)).status == Status.PENDING
