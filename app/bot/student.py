"""Сценарий студента (п. 3.1 ТЗ): даты → преподаватель → проект → техника → отправка."""

from __future__ import annotations

from datetime import date, timedelta
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import async_sessionmaker

from .. import access, notify, services, texts
from ..config import Settings
from ..models import Status
from ..validation import check_dates
from . import keyboards as kb

router = Router(name="student")

CANCELLABLE = (Status.PENDING, Status.APPROVED, Status.SCHEDULED)


class NewReq(StatesGroup):
    dates = State()
    teacher = State()
    title = State()
    items = State()
    confirm = State()


def _d(v: str | None) -> date | None:
    return date.fromisoformat(v) if v else None


def _cart(data: dict) -> dict[int, int]:
    # В хранилище FSM ключи — строки (JSON-совместимость)
    return {int(k): v for k, v in (data.get("cart") or {}).items()}


async def _student_or_warn(message: Message, sm: async_sessionmaker, settings: Settings):
    async with sm() as s:
        st = await services.get_student(s, message.chat.id)
    if st is None:
        await message.answer("Сначала зарегистрируйтесь: /start")
        return None
    denied = services.can_book(st, settings)
    if denied:
        await message.answer(denied)
        return None
    return st


def _calendar_bounds(student, today: date, settings: Settings) -> tuple[date, date]:
    first = access.first_allowed_date(student.course, student.course_year, today)
    lo = max(today, first)
    hi = today + timedelta(days=settings.max_advance_days + settings.max_booking_days - 1)
    return lo, max(lo, hi)


def _dates_prompt(start: date | None, lo: date, today: date) -> str:
    head = "📅 <b>Шаг 1/4. Даты</b>\n"
    if lo > today:
        head += f"Для вашего курса бронирование доступно с <b>{texts.d_short(lo)}</b>.\n"
    if start is None:
        return head + "Выберите дату начала (день получения техники)."
    return (head + f"Начало: <b>{texts.d_short(start)}</b>.\n"
            "Выберите дату окончания (день возврата) или нажмите «Только один день».")


async def _show_calendar(target: Message, state: FSMContext, sm, settings: Settings, today: date,
                         edit: bool, ym: tuple[int, int] | None = None) -> None:
    async with sm() as s:
        st = await services.get_student(s, target.chat.id)
    lo, hi = _calendar_bounds(st, today, settings)
    data = await state.get_data()
    start = _d(data.get("start"))
    y, m = ym or ((start or lo).year, (start or lo).month)
    markup = kb.calendar_kb("req", y, m, lo, hi, sel_start=start, allow_single=start is not None)
    text = _dates_prompt(start, lo, today)
    if edit:
        await target.edit_text(text, reply_markup=markup)
    else:
        await target.answer(text, reply_markup=markup)


@router.message(Command("new"))
@router.message(F.text == texts.BTN_NEW)
async def new_request(message: Message, state: FSMContext, sm: async_sessionmaker,
                      settings: Settings, clock) -> None:
    if await _student_or_warn(message, sm, settings) is None:
        return
    await state.clear()
    await state.set_state(NewReq.dates)
    await state.update_data(cart={})
    await _show_calendar(message, state, sm, settings, clock().date(), edit=False)


@router.callback_query(kb.Cal.filter(F.scope == "req"), NewReq.dates)
async def calendar_click(cb: CallbackQuery, callback_data: kb.Cal, state: FSMContext,
                         sm: async_sessionmaker, settings: Settings, clock) -> None:
    today = clock().date()
    data = await state.get_data()
    start = _d(data.get("start"))
    act = callback_data.act
    if act == "noop":
        await cb.answer()
        return
    if act == "nav":
        y, m = map(int, callback_data.v.split("-"))
        await _show_calendar(cb.message, state, sm, settings, today, edit=True, ym=(y, m))
        await cb.answer()
        return
    if act == "reset":
        await state.update_data(start=None)
        await _show_calendar(cb.message, state, sm, settings, today, edit=True)
        await cb.answer()
        return
    if act == "pick":
        d = date.fromisoformat(callback_data.v)
        if start is None or d < start:
            await state.update_data(start=d.isoformat())
            await _show_calendar(cb.message, state, sm, settings, today, edit=True)
            await cb.answer()
            return
        end = d
    elif act == "one" and start is not None:
        end = start
    else:
        await cb.answer()
        return

    async with sm() as s:
        st = await services.get_student(s, cb.from_user.id)
    err = check_dates(start, end, today, settings.max_booking_days, settings.max_advance_days,
                      services.level_for(st, start),
                      access.first_allowed_date(st.course, st.course_year, today))
    if err:
        await cb.answer(err, show_alert=True)
        return
    await state.update_data(end=end.isoformat())
    await cb.answer()
    if data.get("teacher_id") and data.get("title"):
        # Даты меняли уже после выбора преподавателя и проекта — сразу к технике
        await _show_items(cb.message, state, sm, clock().date(), edit=True)
        return
    await _ask_teacher(cb.message, state, sm, start, end)


async def _ask_teacher(msg: Message, state: FSMContext, sm, start: date, end: date) -> None:
    async with sm() as s:
        ts = await services.teachers(s)
    await state.set_state(NewReq.teacher)
    await msg.edit_text(f"Даты: <b>{texts.period(start, end)}</b>\n\n"
                        "👩‍🏫 <b>Шаг 2/4. Преподаватель</b>\nКто согласует заявку?",
                        reply_markup=kb.teachers_kb(ts))


@router.callback_query(kb.TeacherCb.filter(), NewReq.teacher)
async def pick_teacher(cb: CallbackQuery, callback_data: kb.TeacherCb, state: FSMContext,
                       sm: async_sessionmaker) -> None:
    async with sm() as s:
        t = await s.get(services.Staff, callback_data.staff_id)
    await state.update_data(teacher_id=callback_data.staff_id, teacher_name=t.full_name if t else "")
    await state.set_state(NewReq.title)
    await cb.message.edit_text(
        f"Преподаватель: <b>{escape(t.full_name if t else '')}</b>\n\n"
        "🎬 <b>Шаг 3/4. Учебный проект</b>\nОборудование выдаётся только под учебные проекты.\n"
        "Напишите название проекта или задания, например: <i>Курсовой фильм «Ветер»</i>")
    await cb.answer()


@router.message(NewReq.title, F.text)
async def enter_title(message: Message, state: FSMContext, sm: async_sessionmaker, clock) -> None:
    title = " ".join(message.text.split())
    if not (3 <= len(title) <= 200) or title.startswith("/"):
        await message.answer("Название должно быть от 3 до 200 символов. Попробуйте ещё раз.")
        return
    await state.update_data(title=title)
    await _show_items(message, state, sm, clock().date(), edit=False)


async def _show_items(msg: Message, state: FSMContext, sm, today: date, edit: bool) -> None:
    data = await state.get_data()
    start, end = _d(data["start"]), _d(data["end"])
    async with sm() as s:
        st = await services.get_student(s, msg.chat.id)
        rows = await services.student_catalog(s, st, start, end, today)
    free = {r.equipment.id: r.free for r in rows}
    # Подрезаем корзину под актуальные остатки (даты могли поменяться)
    cart = {e: min(q, free[e]) for e, q in _cart(data).items() if e in free}
    await state.update_data(cart={str(k): v for k, v in cart.items()})
    await state.set_state(NewReq.items)
    if not rows:
        text = (f"На {texts.period(start, end)} нет свободного оборудования, доступного вашему курсу.\n"
                "Попробуйте другие даты.")
        markup = kb.conflict_kb()
    else:
        text = (f"📦 <b>Шаг 4/4. Оборудование</b> на {texts.period(start, end)}\n"
                "Нажмите на позицию, чтобы добавить или убрать её; «+» и «−» меняют количество.\n"
                "В списке только техника, доступная вашему курсу и свободная на эти даты.")
        markup = kb.items_kb(rows, cart)
    if edit:
        await msg.edit_text(text, reply_markup=markup)
    else:
        await msg.answer(text, reply_markup=markup)


@router.callback_query(kb.ItemCb.filter(), NewReq.items)
async def item_click(cb: CallbackQuery, callback_data: kb.ItemCb, state: FSMContext,
                     sm: async_sessionmaker, clock) -> None:
    if callback_data.act == "noop":
        await cb.answer()
        return
    data = await state.get_data()
    cart = _cart(data)
    async with sm() as s:
        st = await services.get_student(s, cb.from_user.id)
        rows = await services.student_catalog(s, st, _d(data["start"]), _d(data["end"]), clock().date())
    free = {r.equipment.id: r.free for r in rows}
    e = callback_data.eq
    if e not in free:
        await cb.answer("Эта позиция уже недоступна.", show_alert=True)
        cart.pop(e, None)
    elif callback_data.act == "t":
        if e in cart:
            cart.pop(e)
        else:
            cart[e] = 1
    elif callback_data.act == "p":
        if cart.get(e, 0) >= free[e]:
            await cb.answer(f"Свободно только {free[e]}.")
        else:
            cart[e] = cart.get(e, 0) + 1
    elif callback_data.act == "m":
        cart[e] = cart.get(e, 0) - 1
        if cart[e] <= 0:
            cart.pop(e)
    await state.update_data(cart={str(k): v for k, v in cart.items()})
    await cb.message.edit_reply_markup(reply_markup=kb.items_kb(rows, cart))
    await cb.answer()


def _summary(data: dict, names: dict[int, str]) -> str:
    lines = "\n".join(f"• {escape(names.get(e, '?'))}" + (f" × {q}" if q > 1 else "")
                      for e, q in _cart(data).items())
    return (f"<b>Проверьте заявку</b>\n\n"
            f"Даты: <b>{texts.period(_d(data['start']), _d(data['end']))}</b>\n"
            f"Преподаватель: <b>{escape(data.get('teacher_name', ''))}</b>\n"
            f"Проект: «{escape(data.get('title', ''))}»\n\n"
            f"Оборудование:\n{lines}")


@router.callback_query(kb.FlowCb.filter(F.act == "items_done"), NewReq.items)
async def items_done(cb: CallbackQuery, state: FSMContext, sm: async_sessionmaker) -> None:
    data = await state.get_data()
    if not _cart(data):
        await cb.answer("Выберите хотя бы одну позицию.", show_alert=True)
        return
    async with sm() as s:
        names = {e.id: e.name for e in await services.equipment_list(s)}
    await state.set_state(NewReq.confirm)
    await cb.message.edit_text(_summary(data, names), reply_markup=kb.confirm_kb())
    await cb.answer()


@router.callback_query(kb.FlowCb.filter(F.act == "send"), NewReq.confirm)
async def send_request(cb: CallbackQuery, state: FSMContext, sm: async_sessionmaker,
                       settings: Settings, clock, bot: Bot) -> None:
    data = await state.get_data()
    async with sm() as s:
        st = await services.get_student(s, cb.from_user.id)
        try:
            b = await services.create_student_booking(
                s, settings, st, data["teacher_id"], _d(data["start"]), _d(data["end"]),
                data["title"], _cart(data), clock())
        except services.BookingError as e:
            # п. 3.1.5 ТЗ: оборудование занято — ошибка и предложение изменить даты/состав
            await cb.message.edit_text(f"⚠️ {escape(str(e))}", reply_markup=kb.conflict_kb())
            await cb.answer()
            return
        await state.clear()
        await cb.message.edit_text(f"📨 Заявка №{b.id} отправлена.\n\n{texts.booking_card(b)}")
        await cb.answer("Заявка отправлена")
        await notify.new_request(bot, s, settings, b)


@router.callback_query(kb.FlowCb.filter(F.act == "edit_items"))
async def edit_items(cb: CallbackQuery, state: FSMContext, sm: async_sessionmaker, clock) -> None:
    data = await state.get_data()
    if not data.get("end"):
        await cb.answer("Сессия устарела. Начните новую заявку.", show_alert=True)
        return
    await _show_items(cb.message, state, sm, clock().date(), edit=True)
    await cb.answer()


@router.callback_query(kb.FlowCb.filter(F.act == "edit_dates"))
async def edit_dates(cb: CallbackQuery, state: FSMContext, sm: async_sessionmaker,
                     settings: Settings, clock) -> None:
    if await state.get_state() is None:
        await cb.answer("Сессия устарела. Начните новую заявку.", show_alert=True)
        return
    await state.update_data(start=None, end=None)
    await state.set_state(NewReq.dates)
    await _show_calendar(cb.message, state, sm, settings, clock().date(), edit=True)
    await cb.answer()


@router.callback_query(kb.FlowCb.filter(F.act == "cancel"))
async def cancel_flow(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.message.edit_text("Заявка не отправлена. Чтобы начать заново — «📝 Новая заявка».")
    await cb.answer()


@router.callback_query(kb.Cal.filter(F.scope == "req"))
@router.callback_query(kb.ItemCb.filter())
@router.callback_query(kb.TeacherCb.filter())
async def stale_flow(cb: CallbackQuery) -> None:
    await cb.answer("Эта форма устарела. Начните новую заявку.", show_alert=True)


@router.message(Command("my"))
@router.message(F.text == texts.BTN_MY)
async def my_bookings(message: Message, sm: async_sessionmaker) -> None:
    async with sm() as s:
        st = await services.get_student(s, message.from_user.id)
        if st is None:
            await message.answer("Сначала зарегистрируйтесь: /start")
            return
        bs = await services.student_bookings(s, st)
    if not bs:
        await message.answer("У вас пока нет заявок. Нажмите «📝 Новая заявка».")
        return
    text = "\n\n".join(texts.booking_card(b) for b in bs)
    await message.answer(text, reply_markup=kb.my_bookings_kb(
        [b.id for b in bs if b.status in CANCELLABLE], [b.id for b in bs if b.status == Status.ISSUED]))


@router.callback_query(kb.StateCb.filter(F.act == "cancel"))
async def student_cancel(cb: CallbackQuery, callback_data: kb.StateCb, sm: async_sessionmaker,
                         settings: Settings, clock, bot: Bot) -> None:
    async with sm() as s:
        st = await services.get_student(s, cb.from_user.id)
        if st is None:
            await cb.answer()
            return
        try:
            b = await services.cancel(s, callback_data.booking, "студент", clock(), student=st)
        except services.BookingError as e:
            await cb.answer(str(e), show_alert=True)
            return
        await cb.answer(f"Заявка №{b.id} отменена")
        await cb.message.answer(f"🚫 Заявка №{b.id} отменена.")
        await notify.cancelled(bot, s, settings, b, by_student=True)


@router.callback_query(kb.StateCb.filter(F.act == "early"))
async def student_early_return(cb: CallbackQuery, callback_data: kb.StateCb, sm: async_sessionmaker,
                               settings: Settings, bot: Bot) -> None:
    # Студент только сообщает о намерении: возврат засчитывает специалист после проверки
    async with sm() as s:
        st = await services.get_student(s, cb.from_user.id)
        b = await services.get_booking(s, callback_data.booking)
        if st is None or b is None or b.student_id != st.id or b.status != Status.ISSUED:
            await cb.answer("Эта заявка сейчас не на руках.", show_alert=True)
            return
        await notify.early_return_request(bot, s, settings, b)
    await cb.answer()
    await cb.message.answer(
        f"Специалист получил сообщение о досрочном возврате по заявке №{b.id}. Принесите оборудование — "
        "возврат будет засчитан после проверки комплектности, бот пришлёт подтверждение.")
