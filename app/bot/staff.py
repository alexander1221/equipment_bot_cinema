"""Сценарии преподавателя (п. 3.2 ТЗ) и специалиста по выдаче (п. 3.3 ТЗ)."""

from __future__ import annotations

from datetime import date
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import async_sessionmaker

from .. import notify, services, texts
from ..config import Settings
from ..models import Status
from ..validation import parse_time
from . import keyboards as kb

router = Router(name="staff")


class Spec(StatesGroup):
    time = State()
    place = State()
    custom = State()


# ---------------------------------------------------------------- преподаватель


@router.callback_query(kb.DecideCb.filter())
async def decide(cb: CallbackQuery, callback_data: kb.DecideCb, sm: async_sessionmaker,
                 settings: Settings, clock, bot: Bot) -> None:
    async with sm() as s:
        b = await services.get_booking(s, callback_data.booking)
        if b is None:
            await cb.answer("Заявка не найдена.", show_alert=True)
            return
        me = await services.staff_by_tg(s, cb.from_user.id)
        own = b.teacher is not None and b.teacher.tg_id == cb.from_user.id
        if not own and not await services.is_specialist(s, cb.from_user.id, settings):
            await cb.answer("Эту заявку согласует другой преподаватель.", show_alert=True)
            return
        who = me.full_name if me else str(cb.from_user.id)
        try:
            b = await services.decide(s, settings, b.id, callback_data.ok, who, clock())
        except services.BookingError as e:
            await cb.answer(str(e), show_alert=True)
            await cb.message.edit_reply_markup(reply_markup=None)
            return
        verdict = "✅ Вы подтвердили заявку." if callback_data.ok else "❌ Вы отклонили заявку."
        await cb.message.edit_text(texts.teacher_request(b).rsplit("\n\n", 1)[0] + f"\n\n{verdict}")
        await cb.answer()
        await notify.decided(bot, s, settings, b, except_message=(cb.message.chat.id, cb.message.message_id))


# ---------------------------------------------------------------- специалист


async def _guard(cb: CallbackQuery, s, settings: Settings) -> bool:
    if not await services.is_specialist(s, cb.from_user.id, settings):
        await cb.answer("Доступно только специалисту по выдаче.", show_alert=True)
        return False
    return True


async def _draft(msg: Message, b, edit: bool = True) -> None:
    text, markup = texts.issue_draft(b), kb.issue_draft_kb(b.id)
    if edit:
        await msg.edit_text(text, reply_markup=markup)
    else:
        await msg.answer(text, reply_markup=markup)


@router.callback_query(kb.IssueCb.filter())
async def issue_click(cb: CallbackQuery, callback_data: kb.IssueCb, state: FSMContext,
                      sm: async_sessionmaker, settings: Settings, clock, bot: Bot) -> None:
    act, bid = callback_data.act, callback_data.booking
    async with sm() as s:
        if not await _guard(cb, s, settings):
            return
        b = await services.get_booking(s, bid)
        if b is None or b.status not in (Status.APPROVED, Status.SCHEDULED):
            title = texts.status_line(b) if b else "не найдена"
            await cb.message.edit_text(f"Заявка №{bid}: {title}.")
            await cb.answer()
            return
        today = clock().date()
        if act in ("open", "back"):
            await _draft(cb.message, b)
        elif act == "date":
            d = b.issue_date or today
            await cb.message.edit_text(
                f"Заявка №{bid}: выберите дату выдачи (бронь {texts.period(b.start_date, b.end_date)}).",
                reply_markup=kb.calendar_kb(f"i{bid}", d.year, d.month, today, max(today, b.end_date),
                                            sel_start=b.issue_date, back=True))
        elif act == "time":
            await cb.message.edit_text(f"Заявка №{bid}: выберите время выдачи.", reply_markup=kb.time_kb(bid))
        elif act == "t":
            v = callback_data.v
            b = await services.update_issue_draft(s, bid, issue_time=f"{v[:2]}:{v[2:]}")
            await _draft(cb.message, b)
        elif act == "place":
            await cb.message.edit_text(f"Заявка №{bid}: выберите место выдачи.",
                                       reply_markup=kb.place_kb(bid, settings.issue_place_list))
        elif act == "pl":
            places = settings.issue_place_list
            b = await services.update_issue_draft(s, bid, issue_place=places[int(callback_data.v) % len(places)])
            await _draft(cb.message, b)
        elif act in ("typetime", "typeplace", "custom"):
            st = {"typetime": Spec.time, "typeplace": Spec.place, "custom": Spec.custom}[act]
            prompt = {
                "typetime": "Введите время выдачи в формате ЧЧ:ММ, например 14:30.",
                "typeplace": "Введите место выдачи, например: ауд. 106.1.",
                "custom": "Отправьте текст сообщения студенту целиком (дата, время, место, что взять с собой).\n"
                          "Отправьте «-», чтобы вернуться к шаблону.",
            }[act]
            await state.set_state(st)
            await state.update_data(booking=bid, chat=cb.message.chat.id, msg=cb.message.message_id)
            await cb.message.answer(prompt)
        elif act == "send":
            try:
                b = await services.schedule_issue(s, bid, str(cb.from_user.id))
            except services.BookingError as e:
                await cb.answer(str(e), show_alert=True)
                return
            await cb.message.edit_text(f"📨 Отправлено студенту:\n\n{texts.issue_message(b)}",
                                       reply_markup=kb.state_kb(bid, "issued", "📦 Отметить: выдано"))
            await notify.scheduled(bot, s, b, except_message=(cb.message.chat.id, cb.message.message_id))
        await cb.answer()


@router.callback_query(kb.Cal.filter(F.scope.startswith("i")))
async def issue_calendar(cb: CallbackQuery, callback_data: kb.Cal, sm: async_sessionmaker,
                         settings: Settings, clock) -> None:
    bid = int(callback_data.scope[1:])
    async with sm() as s:
        if not await _guard(cb, s, settings):
            return
        b = await services.get_booking(s, bid)
        if b is None:
            await cb.answer("Заявка не найдена.", show_alert=True)
            return
        today = clock().date()
        act = callback_data.act
        if act == "nav":
            y, m = map(int, callback_data.v.split("-"))
            await cb.message.edit_reply_markup(reply_markup=kb.calendar_kb(
                callback_data.scope, y, m, today, max(today, b.end_date), sel_start=b.issue_date, back=True))
        elif act == "pick":
            try:
                b = await services.update_issue_draft(s, bid, issue_date=date.fromisoformat(callback_data.v))
            except services.BookingError as e:
                await cb.answer(str(e), show_alert=True)
                return
            await _draft(cb.message, b)
        elif act == "back":
            await _draft(cb.message, b)
    await cb.answer()


async def _apply_text(message: Message, state: FSMContext, sm, bot: Bot, **fields) -> None:
    data = await state.get_data()
    await state.clear()
    async with sm() as s:
        try:
            b = await services.update_issue_draft(s, data["booking"], **fields)
        except services.BookingError as e:
            await message.answer(str(e))
            return
    # Обновляем исходное сообщение-черновик, а не плодим новые
    try:
        await bot.edit_message_text(texts.issue_draft(b), chat_id=data["chat"], message_id=data["msg"],
                                    reply_markup=kb.issue_draft_kb(b.id))
        await message.answer("Черновик обновлён ↑")
    except Exception:  # сообщение могло быть удалено — пришлём новое
        await _draft(message, b, edit=False)


@router.message(Spec.time, F.text)
async def type_time(message: Message, state: FSMContext, sm: async_sessionmaker, bot: Bot) -> None:
    try:
        t = parse_time(message.text)
    except ValueError as e:
        await message.answer(str(e))
        return
    await _apply_text(message, state, sm, bot, issue_time=t)


@router.message(Spec.place, F.text)
async def type_place(message: Message, state: FSMContext, sm: async_sessionmaker, bot: Bot) -> None:
    place = " ".join(message.text.split())[:200]
    await _apply_text(message, state, sm, bot, issue_place=place)


@router.message(Spec.custom, F.text)
async def type_custom(message: Message, state: FSMContext, sm: async_sessionmaker, bot: Bot) -> None:
    text = "" if message.text.strip() == "-" else message.text.strip()[:2000]
    await _apply_text(message, state, sm, bot, custom_text=text)


@router.callback_query(kb.StateCb.filter(F.act.in_({"issued", "returned"})))
async def state_change(cb: CallbackQuery, callback_data: kb.StateCb, sm: async_sessionmaker,
                       settings: Settings, clock, bot: Bot) -> None:
    async with sm() as s:
        if not await _guard(cb, s, settings):
            return
        try:
            if callback_data.act == "issued":
                b = await services.mark_issued(s, callback_data.booking, clock())
                await cb.message.edit_text(
                    f"📦 Заявка №{b.id} выдана: {texts.student_line(b)}\n"
                    f"Вернуть до {texts.d_short(b.end_date)}\n{texts.items_lines(b)}",
                    reply_markup=kb.state_kb(b.id, "returned", "↩️ Отметить: возвращено"))
                await notify.issued(bot, b)
            else:
                b = await services.mark_returned(s, callback_data.booking, clock())
                await cb.message.edit_text(f"✔️ Заявка №{b.id}: возврат принят ({texts.student_line(b)}).")
                await notify.returned(bot, b)
        except services.BookingError as e:
            await cb.answer(str(e), show_alert=True)
            return
    await cb.answer()


@router.message(Command("today"))
async def today_cmd(message: Message, sm: async_sessionmaker, settings: Settings, clock) -> None:
    async with sm() as s:
        if not await services.is_specialist(s, message.from_user.id, settings):
            await message.answer("Команда доступна специалисту по выдаче.")
            return
        today = clock().date()
        issues, returns = await services.today_agenda(s, today)
    if not issues and not returns:
        await message.answer(f"На {texts.d_short(today)} выдач и возвратов нет.")
        return
    await message.answer(f"<b>{texts.d_long(today)}</b>: выдач — {len(issues)}, возвратов — {len(returns)}.")
    for b in issues:
        when = f"{texts.d_short(b.issue_date)} {b.issue_time}" if b.issue_date else ""
        if b.status == Status.APPROVED:
            await message.answer(f"🗓 Выдача не назначена: №{b.id}\n{texts.student_line(b)}\n"
                                 f"{texts.items_lines(b)}", reply_markup=kb.open_issue_kb(b.id))
        else:
            await message.answer(f"📤 Выдача №{b.id} ({when}, {escape(b.issue_place)})\n"
                                 f"{texts.student_line(b)}\n{texts.items_lines(b)}",
                                 reply_markup=kb.state_kb(b.id, "issued", "📦 Отметить: выдано"))
    for b in returns:
        late = " — <b>просрочено</b>" if b.end_date < today else ""
        await message.answer(f"📥 Возврат №{b.id} до {texts.d_short(b.end_date)}{late}\n"
                             f"{texts.student_line(b)}\n{texts.items_lines(b)}",
                             reply_markup=kb.state_kb(b.id, "returned", "↩️ Отметить: возвращено"))


@router.message(Command("pending"))
async def pending_cmd(message: Message, sm: async_sessionmaker, settings: Settings) -> None:
    async with sm() as s:
        if not await services.is_specialist(s, message.from_user.id, settings):
            await message.answer("Команда доступна специалисту по выдаче.")
            return
        bs = await services.list_bookings(s, (Status.APPROVED,))
    if not bs:
        await message.answer("Нет заявок, ожидающих назначения выдачи.")
        return
    for b in bs:
        await message.answer(texts.specialist_request(b), reply_markup=kb.open_issue_kb(b.id))


# ---------------------------------------------------------------- проверка студентов


@router.callback_query(kb.StudentCb.filter())
async def verify_student(cb: CallbackQuery, callback_data: kb.StudentCb, sm: async_sessionmaker,
                         settings: Settings, clock, bot: Bot) -> None:
    async with sm() as s:
        if not await _guard(cb, s, settings):
            return
        st = await s.get(services.Student, callback_data.student)
        if st is None:
            await cb.answer("Студент не найден.", show_alert=True)
            return
        if callback_data.act == "fix":
            await cb.message.edit_reply_markup(reply_markup=kb.student_course_kb(st.id))
            await cb.answer("Выберите правильный курс")
            return
        me = await services.staff_by_tg(s, cb.from_user.id)
        who = me.full_name if me else str(cb.from_user.id)
        card = texts.student_card(st, services.course_on(st, clock().date()))
        if callback_data.act == "ok":
            st = await services.verify_student(s, st.id, callback_data.course, who, clock())
            await cb.message.edit_text(f"✅ Подтверждён ({callback_data.course} курс), {escape(who)}\n\n{card}")
            await notify.student_verified(bot, st, callback_data.course)
        elif callback_data.act == "block":
            st = await services.set_student_blocked(s, st.id, True, who, clock())
            await cb.message.edit_text(f"🚫 Отклонён, {escape(who)}\n\n{card}")
            await notify.student_blocked(bot, st)
    await cb.answer()
