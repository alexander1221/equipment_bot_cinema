"""Inline-клавиатуры бота: календарь, выбор оборудования, кнопки сотрудников."""

from __future__ import annotations

import calendar
from datetime import date

from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from .. import texts
from ..services import CatalogRow


class Cal(CallbackData, prefix="cal"):
    scope: str  # «req» — даты брони; «i<id>» — дата выдачи для заявки id
    act: str  # pick | nav | one | reset | noop | back
    v: str = ""


class CourseCb(CallbackData, prefix="crs"):
    course: int


class TeacherCb(CallbackData, prefix="tch"):
    staff_id: int


class ItemCb(CallbackData, prefix="itm"):
    act: str  # t — вкл/выкл, p — плюс, m — минус, noop
    eq: int = 0


class FlowCb(CallbackData, prefix="flw"):
    act: str  # items_done | send | edit_items | edit_dates | cancel | back_dates


class DecideCb(CallbackData, prefix="dec"):
    booking: int
    ok: bool


class IssueCb(CallbackData, prefix="iss"):
    booking: int
    act: str  # open | date | time | place | custom | send | t | pl | typetime | typeplace | back
    v: str = ""  # для времени — «1430» (двоеточие — разделитель callback_data)


class StudentCb(CallbackData, prefix="std"):
    student: int
    act: str  # ok — подтвердить с курсом course | fix — выбрать другой курс | block
    course: int = 0


def verify_student_kb(student_id: int, course: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ Подтвердить, {course} курс",
                              callback_data=StudentCb(student=student_id, act="ok", course=course).pack())],
        [InlineKeyboardButton(text="✏️ Курс другой",
                              callback_data=StudentCb(student=student_id, act="fix").pack()),
         InlineKeyboardButton(text="🚫 Отклонить",
                              callback_data=StudentCb(student=student_id, act="block").pack())],
    ])


def student_course_kb(student_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"{c} курс",
                             callback_data=StudentCb(student=student_id, act="ok", course=c).pack())
        for c in (1, 2, 3, 4)
    ]])


class StateCb(CallbackData, prefix="stt"):
    booking: int
    act: str  # issued | returned | cancel


def main_menu(is_staff: bool = False) -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text=texts.BTN_NEW), KeyboardButton(text=texts.BTN_MY)],
            [KeyboardButton(text=texts.BTN_PROFILE), KeyboardButton(text=texts.BTN_HELP)]]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def courses() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"{c} курс", callback_data=CourseCb(course=c).pack())
        for c in (1, 2, 3, 4)
    ]])


def _month_shift(y: int, m: int, k: int) -> tuple[int, int]:
    idx = y * 12 + (m - 1) + k
    return idx // 12, idx % 12 + 1


def calendar_kb(
    scope: str,
    year: int,
    month: int,
    min_date: date,
    max_date: date,
    sel_start: date | None = None,
    sel_end: date | None = None,
    allow_single: bool = False,
    back: bool = False,
) -> InlineKeyboardMarkup:
    """Месяц сеткой 7×N; даты вне [min_date, max_date] неактивны."""
    noop = Cal(scope=scope, act="noop").pack()
    rows: list[list[InlineKeyboardButton]] = []
    py, pm = _month_shift(year, month, -1)
    ny, nm = _month_shift(year, month, 1)
    can_prev = (py, pm) >= (min_date.year, min_date.month)
    can_next = (ny, nm) <= (max_date.year, max_date.month)
    rows.append([
        InlineKeyboardButton(text="‹" if can_prev else " ",
                             callback_data=Cal(scope=scope, act="nav", v=f"{py}-{pm}").pack() if can_prev else noop),
        InlineKeyboardButton(text=f"{texts.MONTHS_NOM[month - 1]} {year}", callback_data=noop),
        InlineKeyboardButton(text="›" if can_next else " ",
                             callback_data=Cal(scope=scope, act="nav", v=f"{ny}-{nm}").pack() if can_next else noop),
    ])
    rows.append([InlineKeyboardButton(text=w, callback_data=noop) for w in texts.WEEKDAYS])
    for week in calendar.Calendar(firstweekday=0).monthdatescalendar(year, month):
        row = []
        for d in week:
            if d.month != month or not (min_date <= d <= max_date):
                row.append(InlineKeyboardButton(text=" " if d.month != month else "·", callback_data=noop))
                continue
            label = str(d.day)
            if sel_start and (d == sel_start or (sel_end and sel_start <= d <= sel_end)):
                label = f"[{d.day}]"
            row.append(InlineKeyboardButton(text=label,
                                            callback_data=Cal(scope=scope, act="pick", v=d.isoformat()).pack()))
        rows.append(row)
    extra = []
    if allow_single:
        extra.append(InlineKeyboardButton(text="✅ Только один день",
                                          callback_data=Cal(scope=scope, act="one").pack()))
    if sel_start:
        extra.append(InlineKeyboardButton(text="↺ Сбросить", callback_data=Cal(scope=scope, act="reset").pack()))
    if extra:
        rows.append(extra)
    if back:
        rows.append([InlineKeyboardButton(text="‹ Назад", callback_data=Cal(scope=scope, act="back").pack())])
    else:
        rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data=FlowCb(act="cancel").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def teachers_kb(teachers: list) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=t.full_name, callback_data=TeacherCb(staff_id=t.id).pack())]
            for t in teachers]
    rows.append([InlineKeyboardButton(text="‹ Изменить даты", callback_data=FlowCb(act="edit_dates").pack()),
                 InlineKeyboardButton(text="❌ Отмена", callback_data=FlowCb(act="cancel").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def items_kb(rows_data: list[CatalogRow], cart: dict[int, int]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    category = None
    for r in rows_data:
        e = r.equipment
        if e.category != category:
            category = e.category
            rows.append([InlineKeyboardButton(text=f"— {category} —",
                                              callback_data=ItemCb(act="noop").pack())])
        q = cart.get(e.id, 0)
        if q:
            label = f"☑️ {e.name}" + (f" × {q}" if r.free > 1 else "")
        else:
            label = f"▫️ {e.name}" + (f" (своб. {r.free})" if r.free > 1 else "")
        row = [InlineKeyboardButton(text=label, callback_data=ItemCb(act="t", eq=e.id).pack())]
        if q and r.free > 1:
            row += [InlineKeyboardButton(text="−", callback_data=ItemCb(act="m", eq=e.id).pack()),
                    InlineKeyboardButton(text="+", callback_data=ItemCb(act="p", eq=e.id).pack())]
        rows.append(row)
    n = sum(cart.values())
    rows.append([InlineKeyboardButton(text="‹ Даты", callback_data=FlowCb(act="edit_dates").pack()),
                 InlineKeyboardButton(text=f"Далее ({n}) ›", callback_data=FlowCb(act="items_done").pack())])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data=FlowCb(act="cancel").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📨 Отправить на согласование", callback_data=FlowCb(act="send").pack())],
        [InlineKeyboardButton(text="✏️ Состав", callback_data=FlowCb(act="edit_items").pack()),
         InlineKeyboardButton(text="📅 Даты", callback_data=FlowCb(act="edit_dates").pack())],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=FlowCb(act="cancel").pack())],
    ])


def conflict_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить состав", callback_data=FlowCb(act="edit_items").pack()),
         InlineKeyboardButton(text="📅 Изменить даты", callback_data=FlowCb(act="edit_dates").pack())],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=FlowCb(act="cancel").pack())],
    ])


def decide_kb(booking_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Подтверждаю", callback_data=DecideCb(booking=booking_id, ok=True).pack()),
        InlineKeyboardButton(text="❌ Отклоняю", callback_data=DecideCb(booking=booking_id, ok=False).pack()),
    ]])


def open_issue_kb(booking_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🗓 Назначить выдачу", callback_data=IssueCb(booking=booking_id, act="open").pack()),
    ]])


def issue_draft_kb(booking_id: int) -> InlineKeyboardMarkup:
    cb = lambda act: IssueCb(booking=booking_id, act=act).pack()  # noqa: E731
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Дата", callback_data=cb("date")),
         InlineKeyboardButton(text="🕑 Время", callback_data=cb("time")),
         InlineKeyboardButton(text="📍 Место", callback_data=cb("place"))],
        [InlineKeyboardButton(text="✏️ Свой текст", callback_data=cb("custom"))],
        [InlineKeyboardButton(text="📨 Отправить студенту", callback_data=cb("send"))],
    ])


TIME_SLOTS = [f"{h:02d}:{m:02d}" for h in range(10, 19) for m in (0, 30) if (h, m) <= (18, 0)]


def time_kb(booking_id: int) -> InlineKeyboardMarkup:
    rows, row = [], []
    for t in TIME_SLOTS:
        row.append(InlineKeyboardButton(text=t, callback_data=IssueCb(booking=booking_id, act="t", v=t.replace(":", "")).pack()))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="⌨️ Ввести вручную",
                                      callback_data=IssueCb(booking=booking_id, act="typetime").pack()),
                 InlineKeyboardButton(text="‹ Назад", callback_data=IssueCb(booking=booking_id, act="back").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def place_kb(booking_id: int, places: list[str]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=p, callback_data=IssueCb(booking=booking_id, act="pl", v=str(i)).pack())]
            for i, p in enumerate(places)]
    rows.append([InlineKeyboardButton(text="⌨️ Ввести вручную",
                                      callback_data=IssueCb(booking=booking_id, act="typeplace").pack()),
                 InlineKeyboardButton(text="‹ Назад", callback_data=IssueCb(booking=booking_id, act="back").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def state_kb(booking_id: int, act: str, label: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=label, callback_data=StateCb(booking=booking_id, act=act).pack())]])


def my_bookings_kb(cancellable: list[int], issued: list[int] = ()) -> InlineKeyboardMarkup | None:
    rows = [[InlineKeyboardButton(text=f"🚫 Отменить №{bid}", callback_data=StateCb(booking=bid, act="cancel").pack())]
            for bid in cancellable]
    rows += [[InlineKeyboardButton(text=f"↩️ Хочу вернуть раньше №{bid}",
                                   callback_data=StateCb(booking=bid, act="early").pack())]
             for bid in issued]
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
