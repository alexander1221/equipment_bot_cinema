"""Отправка уведомлений участникам. Используется и ботом, и веб-панелью.

Ошибки Telegram (пользователь заблокировал бота, сообщение уже изменено)
не должны ронять бизнес-операцию: они логируются, операция в БД остаётся.
"""

from __future__ import annotations

import logging
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from . import services, texts
from .bot import keyboards as kb
from .config import Settings
from .models import Booking, Status

log = logging.getLogger(__name__)


async def send(bot: Bot, chat_id: int, text: str, markup: InlineKeyboardMarkup | None = None):
    try:
        return await bot.send_message(chat_id, text, reply_markup=markup)
    except TelegramAPIError as e:
        log.warning("send to %s failed: %s", chat_id, e)
        return None


async def to_student(bot: Bot, b: Booking, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    if b.student is not None:
        await send(bot, b.student.tg_id, text, markup)


async def to_specialists(bot: Bot, s: AsyncSession, settings: Settings, text: str,
                         markup: InlineKeyboardMarkup | None = None, booking_id: int | None = None,
                         kind: str = "specialist") -> int:
    sent = 0
    for chat_id in await services.specialist_chat_ids(s, settings):
        msg = await send(bot, chat_id, text, markup)
        if msg is not None:
            sent += 1
            if booking_id is not None:
                await services.remember_notice(s, booking_id, chat_id, msg.message_id, kind)
    return sent


async def new_request(bot: Bot, s: AsyncSession, settings: Settings, b: Booking) -> None:
    """Заявка создана: преподавателю — запрос на согласование (п. 3.2 ТЗ)."""
    teacher = b.teacher
    if teacher is not None and teacher.tg_id:
        msg = await send(bot, teacher.tg_id, texts.teacher_request(b), kb.decide_kb(b.id))
        if msg is not None:
            await services.remember_notice(s, b.id, teacher.tg_id, msg.message_id, "teacher")
            return
    # Преподаватель ещё не подключился к боту — специалисты могут согласовать в панели
    await to_specialists(
        bot, s, settings,
        f"⚠️ Заявка №{b.id}: преподаватель {escape(teacher.full_name if teacher else '—')} "
        "не подключён к боту (или недоступен). Согласуйте заявку с ним и отметьте решение в веб-панели.\n\n"
        + texts.teacher_request(b),
    )


async def close_notices(bot: Bot, s: AsyncSession, booking_id: int, text: str,
                        kind: str | None = None, except_message: tuple[int, int] | None = None) -> None:
    """Заменить сообщения с кнопками по заявке на итоговый текст без кнопок."""
    for n in await services.pop_notices(s, booking_id, kind):
        if except_message == (n.chat_id, n.message_id):
            continue
        try:
            await bot.edit_message_text(text=text, chat_id=n.chat_id, message_id=n.message_id)
        except TelegramAPIError as e:
            log.info("edit notice %s/%s failed: %s", n.chat_id, n.message_id, e)


async def decided(bot: Bot, s: AsyncSession, settings: Settings, b: Booking,
                  except_message: tuple[int, int] | None = None) -> None:
    """Преподаватель (или специалист в панели) принял решение."""
    reason = f"\nКомментарий: {escape(b.comment)}" if b.comment else ""
    if b.status == Status.APPROVED:
        await close_notices(bot, s, b.id, texts.teacher_request(b).rsplit("\n\n", 1)[0]
                            + f"\n\n✅ Подтверждено ({escape(b.decided_by)})" + reason, "teacher", except_message)
        await to_student(bot, b, f"✅ Заявка №{b.id} подтверждена преподавателем. "
                                 "Специалист по выдаче скоро назначит время — бот пришлёт сообщение." + reason)
        await to_specialists(bot, s, settings, texts.specialist_request(b), kb.open_issue_kb(b.id), b.id)
    else:
        await close_notices(bot, s, b.id, texts.teacher_request(b).rsplit("\n\n", 1)[0]
                            + f"\n\n❌ Отклонено ({escape(b.decided_by)})" + reason, "teacher", except_message)
        await to_student(bot, b, f"❌ Заявка №{b.id} отклонена преподавателем.{reason}\n"
                                 "Можно подать новую заявку через «📝 Новая заявка».")


async def scheduled(bot: Bot, s: AsyncSession, b: Booking,
                    except_message: tuple[int, int] | None = None) -> None:
    await to_student(bot, b, texts.issue_message(b))
    await close_notices(bot, s, b.id, f"📨 Заявка №{b.id}: выдача назначена, студент уведомлён.\n\n"
                        + texts.issue_message(b), "specialist", except_message)


async def issued(bot: Bot, b: Booking) -> None:
    await to_student(bot, b, f"📦 Оборудование по заявке №{b.id} выдано.\n"
                             f"Вернуть до: <b>{texts.d_long(b.end_date)}</b>.")


async def returned(bot: Bot, b: Booking) -> None:
    await to_student(bot, b, f"✔️ Возврат по заявке №{b.id} принят. Спасибо!")


async def cancelled(bot: Bot, s: AsyncSession, settings: Settings, b: Booking, by_student: bool) -> None:
    await close_notices(bot, s, b.id, f"🚫 Заявка №{b.id} отменена.")
    if by_student:
        if b.decided_at is not None:  # специалисты уже видели заявку
            await to_specialists(bot, s, settings,
                                 f"🚫 Студент отменил заявку №{b.id} ({texts.student_line(b)}).")
    else:
        await to_student(bot, b, f"🚫 Заявка №{b.id} отменена специалистом по выдаче."
                         + (f"\n{escape(b.comment.splitlines()[-1])}" if b.comment else ""))


async def expired(bot: Bot, s: AsyncSession, b: Booking) -> None:
    await close_notices(bot, s, b.id, f"⌛ Заявка №{b.id}: срок согласования истёк.")
    await to_student(bot, b, f"⌛ Преподаватель не ответил на заявку №{b.id} вовремя, она закрыта. "
                             "Свяжитесь с преподавателем и подайте заявку заново.")


async def return_reminder(bot: Bot, b: Booking) -> None:
    await to_student(bot, b, f"⏰ Напоминание: оборудование по заявке №{b.id} нужно вернуть "
                             f"до <b>{texts.d_long(b.end_date)}</b>.\n{texts.items_lines(b)}")


async def student_to_verify(bot: Bot, s: AsyncSession, settings: Settings, st, today) -> None:  # noqa: ANN001
    course = services.course_on(st, today)
    sent = await to_specialists(
        bot, s, settings,
        "🆕 <b>Новый студент ждёт подтверждения</b>\n\n" + texts.student_card(st, course)
        + "\n\nСверьте со списком группы. До подтверждения студент не может подавать заявки.",
        kb.verify_student_kb(st.id, course))
    if not sent:
        log.warning("student %s awaits verification, but no specialist is connected to the bot", st.id)


async def student_verified(bot: Bot, st, course: int) -> None:  # noqa: ANN001
    await send(bot, st.tg_id, f"✅ Ваш профиль подтверждён ({course} курс). Теперь можно подавать заявки: "
                              "«📝 Новая заявка».")


async def student_blocked(bot: Bot, st) -> None:  # noqa: ANN001
    await send(bot, st.tg_id, "🚫 Регистрация не подтверждена. Если это ошибка — обратитесь на кафедру.")


async def changed(bot: Bot, s: AsyncSession, settings: Settings, b: Booking, desc: str) -> None:
    """Специалист изменил бронь: студенту — что поменялось; преподавателю — новый запрос."""
    if b.kind != "student":
        return
    await to_student(bot, b, f"✏️ Заявка №{b.id} изменена специалистом по выдаче:\n{escape(desc)}\n\n"
                             + texts.booking_card(b))
    if b.status == Status.PENDING:
        # Старое сообщение преподавателю устарело — заменяем его новым
        await close_notices(bot, s, b.id, f"✏️ Заявка №{b.id} изменена, см. новое сообщение.", "teacher")
        await new_request(bot, s, settings, b)


async def early_return_request(bot: Bot, s: AsyncSession, settings: Settings, b: Booking) -> None:
    await to_specialists(
        bot, s, settings,
        f"↩️ Студент хочет вернуть оборудование досрочно — заявка №{b.id}\n{texts.student_line(b)}\n"
        f"Срок был до {texts.d_short(b.end_date)}\n{texts.items_lines(b)}\n\n"
        "Нажмите кнопку после проверки комплектности.",
        kb.state_kb(b.id, "returned", "↩️ Отметить: возвращено"))
