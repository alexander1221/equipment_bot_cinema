"""Бизнес-логика: заявки, согласование, выдача, внутренние брони, инвентарь.

Слой не знает про Telegram и HTML: бот и веб-панель вызывают одни и те же
функции, а уведомления отправляет app/notify.py. Все изменения, влияющие
на занятость, выполняются под общей блокировкой _lock, поэтому две заявки,
поданные в одну секунду на последнюю свободную единицу, не пройдут обе:
вторая увидит первую при повторной проверке.

Блокировка — asyncio.Lock, т.е. рассчитана на один процесс (бот и панель
запускаются вместе, см. main.py). При переходе на несколько процессов
нужна блокировка на стороне БД (SELECT … FOR UPDATE в PostgreSQL).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import access
from .availability import Span, daily_usage, effective_span, find_shortages, overbooked_days
from .config import Settings
from .models import (
    BLOCKING_STATUSES,
    Booking,
    BookingItem,
    BookingKind,
    Equipment,
    InventoryLog,
    Notice,
    Role,
    Staff,
    Status,
    Student,
    StudentStatus,
)
from .validation import check_dates

_lock = asyncio.Lock()


class BookingError(Exception):
    """Ошибка, текст которой можно показать пользователю как есть."""


# ---------------------------------------------------------------- справочники


async def equipment_list(s: AsyncSession, include_hidden: bool = True) -> list[Equipment]:
    q = select(Equipment).order_by(Equipment.sort_order, Equipment.id)
    if not include_hidden:
        q = q.where(Equipment.hidden.is_(False))
    return list(await s.scalars(q))


async def totals(s: AsyncSession) -> dict[int, int]:
    return {e.id: e.total_quantity for e in await equipment_list(s)}


async def teachers(s: AsyncSession) -> list[Staff]:
    q = select(Staff).where(Staff.role == Role.TEACHER, Staff.active.is_(True)).order_by(Staff.full_name)
    return list(await s.scalars(q))


async def specialist_chat_ids(s: AsyncSession, settings: Settings) -> list[int]:
    q = select(Staff.tg_id).where(
        Staff.role.in_([Role.SPECIALIST, Role.ADMIN]),
        Staff.active.is_(True),
        Staff.tg_id.is_not(None),
    )
    ids = list(await s.scalars(q))
    for a in settings.admin_id_list:
        if a not in ids:
            ids.append(a)
    return ids


async def staff_by_tg(s: AsyncSession, tg_id: int) -> Staff | None:
    return await s.scalar(select(Staff).where(Staff.tg_id == tg_id, Staff.active.is_(True)))


async def is_specialist(s: AsyncSession, tg_id: int, settings: Settings) -> bool:
    if tg_id in settings.admin_id_list:
        return True
    st = await staff_by_tg(s, tg_id)
    return st is not None and st.role in (Role.SPECIALIST, Role.ADMIN)


async def bind_staff(s: AsyncSession, token: str, tg_id: int) -> Staff | None:
    st = await s.scalar(select(Staff).where(Staff.invite_token == token, Staff.active.is_(True)))
    if st is None:
        return None
    # Один Telegram-аккаунт — одна запись сотрудника
    other = await s.scalar(select(Staff).where(Staff.tg_id == tg_id, Staff.id != st.id))
    if other is not None:
        other.tg_id = None
    st.tg_id = tg_id
    await s.commit()
    return st


# ---------------------------------------------------------------- студенты


async def get_student(s: AsyncSession, tg_id: int) -> Student | None:
    return await s.scalar(select(Student).where(Student.tg_id == tg_id))


async def save_student(
    s: AsyncSession, tg_id: int, full_name: str, course: int, username: str, now: datetime,
    group_name: str = "", auto_verify: bool = False,
) -> Student:
    """Регистрация или правка профиля студентом.

    Любое изменение ФИО, курса или группы снимает подтверждение: проверенным
    остаётся только то, что видел специалист. Заблокированный студент
    остаётся заблокированным.
    """
    if course not in (1, 2, 3, 4):
        raise BookingError("Курс должен быть от 1 до 4.")
    st = await get_student(s, tg_id)
    status = StudentStatus.VERIFIED if auto_verify else StudentStatus.PENDING
    if st is None:
        st = Student(tg_id=tg_id, created_at=now, full_name=full_name, course=course,
                     course_year=access.academic_year(now.date()), username=username,
                     group_name=group_name, status=status)
        s.add(st)
    else:
        today = now.date()
        changed = (st.full_name != full_name or st.group_name != group_name
                   or access.effective_course(st.course, st.course_year, today) != course)
        st.full_name, st.username, st.group_name = full_name, username, group_name
        st.course, st.course_year = course, access.academic_year(today)
        if changed and st.status != StudentStatus.BLOCKED:
            st.status, st.verified_at, st.verified_by = status, None, ""
    if st.status == StudentStatus.VERIFIED and st.verified_at is None:
        st.verified_at, st.verified_by = now, "автоматически"
    await s.commit()
    return st


async def verify_student(
    s: AsyncSession, student_id: int, course: int, by: str, now: datetime
) -> Student:
    """Специалист подтверждает студента и курс (курс можно исправить)."""
    if course not in (1, 2, 3, 4):
        raise BookingError("Курс должен быть от 1 до 4.")
    st = await s.get(Student, student_id)
    if st is None:
        raise BookingError("Студент не найден.")
    if access.effective_course(st.course, st.course_year, now.date()) != course:
        st.course, st.course_year = course, access.academic_year(now.date())
    st.status, st.verified_at, st.verified_by = StudentStatus.VERIFIED, now, by
    await s.commit()
    return st


async def set_student_blocked(
    s: AsyncSession, student_id: int, blocked: bool, by: str, now: datetime
) -> Student:
    st = await s.get(Student, student_id)
    if st is None:
        raise BookingError("Студент не найден.")
    st.status = StudentStatus.BLOCKED if blocked else StudentStatus.PENDING
    st.verified_at, st.verified_by = (now, by) if blocked else (None, "")
    await s.commit()
    return st


async def list_students(s: AsyncSession, status: StudentStatus | None = None) -> list[Student]:
    q = select(Student).order_by(Student.created_at.desc())
    if status:
        q = q.where(Student.status == status)
    return list(await s.scalars(q))


def can_book(student: Student, settings: Settings) -> str | None:
    """None — можно подавать заявки; иначе текст для студента."""
    if student.status == StudentStatus.BLOCKED:
        return "Доступ к бронированию закрыт. Обратитесь на кафедру."
    if settings.require_student_verification and student.status != StudentStatus.VERIFIED:
        return ("Ваш профиль ещё не подтверждён специалистом кафедры. "
                "Как только его проверят, бот пришлёт сообщение — после этого можно подавать заявки.")
    return None


def level_for(student: Student, d: date) -> int:
    return access.student_level(student.course, student.course_year, d)


def course_on(student: Student, d: date) -> int:
    return access.effective_course(student.course, student.course_year, d)


# ---------------------------------------------------------------- занятость


async def load_spans(
    s: AsyncSession, start: date, end: date, today: date, exclude_id: int | None = None
) -> list[Span]:
    # Выданная техника занята до возврата, поэтому ISSUED берём и с прошлым концом
    q = select(Booking).where(
        Booking.status.in_(BLOCKING_STATUSES),
        Booking.start_date <= end,
        or_(Booking.end_date >= start, Booking.status == Status.ISSUED),
    )
    if exclude_id is not None:
        q = q.where(Booking.id != exclude_id)
    spans = []
    for b in await s.scalars(q):
        lo, hi = effective_span(b.start_date, b.end_date, b.status == Status.ISSUED, today)
        if hi < start:
            continue
        spans.append(Span(lo, hi, {it.equipment_id: it.quantity for it in b.items}))
    return spans


@dataclass
class CatalogRow:
    equipment: Equipment
    free: int


async def student_catalog(
    s: AsyncSession, student: Student, start: date, end: date, today: date
) -> list[CatalogRow]:
    """Оборудование, доступное студенту по матрице и свободное на [start, end]."""
    level = level_for(student, start)
    eqs = [e for e in await equipment_list(s, include_hidden=False)
           if access.is_allowed(e.access_tier, e.hidden, level)]
    spans = await load_spans(s, start, end, today)
    usage = daily_usage(spans, start, end)
    rows = []
    for e in eqs:
        used = max(usage[e.id].values()) if e.id in usage else 0
        free = e.total_quantity - used
        if free > 0:
            rows.append(CatalogRow(e, free))
    return rows


async def _check_free(
    s: AsyncSession, items: dict[int, int], start: date, end: date, today: date,
    exclude_id: int | None = None,
) -> None:
    eqs = {e.id: e for e in await equipment_list(s)}
    unknown = [e for e in items if e not in eqs]
    if unknown:
        raise BookingError("Часть оборудования больше не числится в инвентаре. Начните заново.")
    spans = await load_spans(s, start, end, today, exclude_id)
    short = find_shortages(items, {e: eqs[e].total_quantity for e in items}, spans, start, end)
    if short:
        lines = [f"• {eqs[e].name}: нужно {need}, свободно {free}" for e, (need, free) in short.items()]
        raise BookingError(
            "Это оборудование уже занято на выбранные даты:\n" + "\n".join(lines)
            + "\n\nИзмените даты или состав заявки."
        )


# ---------------------------------------------------------------- заявки студентов


async def create_student_booking(
    s: AsyncSession,
    settings: Settings,
    student: Student,
    teacher_id: int,
    start: date,
    end: date,
    title: str,
    items: dict[int, int],
    now: datetime,
) -> Booking:
    today = now.date()
    denied = can_book(student, settings)
    if denied:
        raise BookingError(denied)
    items = {e: q for e, q in items.items() if q > 0}
    if not items:
        raise BookingError("Не выбрано ни одной позиции.")
    level = level_for(student, start)
    err = check_dates(start, end, today, settings.max_booking_days, settings.max_advance_days,
                      level, access.first_allowed_date(student.course, student.course_year, today))
    if err:
        raise BookingError(err)
    teacher = await s.get(Staff, teacher_id)
    if teacher is None or teacher.role != Role.TEACHER or not teacher.active:
        raise BookingError("Преподаватель не найден. Выберите преподавателя заново.")
    async with _lock:
        eqs = {e.id: e for e in await equipment_list(s)}
        for e_id in items:
            e = eqs.get(e_id)
            # Повторная проверка матрицы доступа: callback мог быть подделан
            if e is None or not access.is_allowed(e.access_tier, e.hidden, level):
                raise BookingError("В заявке есть оборудование, недоступное для вашего курса.")
        await _check_free(s, items, start, end, today)
        b = Booking(
            kind=BookingKind.STUDENT, status=Status.PENDING, start_date=start, end_date=end,
            title=title, student_id=student.id, teacher_id=teacher_id,
            course_at_request=course_on(student, start), created_at=now,
            created_by=f"student:{student.tg_id}",
        )
        b.items = [BookingItem(equipment_id=e, quantity=q) for e, q in items.items()]
        s.add(b)
        await s.commit()
    return await get_booking(s, b.id)


async def get_booking(s: AsyncSession, booking_id: int) -> Booking | None:
    # populate_existing: после commit в другой функции хотим свежие связи
    return await s.get(Booking, booking_id, populate_existing=True)


async def student_bookings(s: AsyncSession, student: Student, limit: int = 10) -> list[Booking]:
    q = (select(Booking).where(Booking.student_id == student.id)
         .order_by(Booking.id.desc()).limit(limit))
    return list(await s.scalars(q))


async def decide(
    s: AsyncSession, settings: Settings, booking_id: int, approve: bool, by: str, now: datetime,
    comment: str = "",
) -> Booking:
    b = await get_booking(s, booking_id)
    if b is None:
        raise BookingError("Заявка не найдена.")
    if b.status != Status.PENDING:
        raise BookingError("Эта заявка уже обработана.")
    b.decided_at, b.decided_by = now, by
    if comment:
        b.comment = comment
    if approve:
        b.status = Status.APPROVED
        # Черновик выдачи: в день начала брони (или сегодня, если начало уже наступило)
        b.issue_date = max(b.start_date, now.date())
        b.issue_time = settings.issue_default_time
        b.issue_place = settings.issue_place_list[0]
    else:
        b.status = Status.REJECTED
    await s.commit()
    return b


async def update_issue_draft(
    s: AsyncSession, booking_id: int, *, issue_date: date | None = None,
    issue_time: str | None = None, issue_place: str | None = None,
    custom_text: str | None = None,
) -> Booking:
    b = await get_booking(s, booking_id)
    if b is None:
        raise BookingError("Заявка не найдена.")
    if b.status not in (Status.APPROVED, Status.SCHEDULED):
        raise BookingError("Назначить выдачу можно только для согласованной заявки.")
    if issue_date is not None:
        b.issue_date = issue_date
    if issue_time is not None:
        b.issue_time = issue_time
    if issue_place is not None:
        b.issue_place = issue_place
    if custom_text is not None:
        b.issue_custom_text = custom_text
    await s.commit()
    return b


async def schedule_issue(s: AsyncSession, booking_id: int, by: str) -> Booking:
    b = await get_booking(s, booking_id)
    if b is None:
        raise BookingError("Заявка не найдена.")
    if b.status not in (Status.APPROVED, Status.SCHEDULED):
        raise BookingError("Эта заявка не ожидает назначения выдачи.")
    if not b.issue_custom_text and not (b.issue_date and b.issue_time and b.issue_place):
        raise BookingError("Укажите дату, время и место выдачи.")
    b.status = Status.SCHEDULED
    b.decided_by = b.decided_by or by
    await s.commit()
    return b


async def mark_issued(s: AsyncSession, booking_id: int, now: datetime) -> Booking:
    b = await get_booking(s, booking_id)
    if b is None or b.status not in (Status.APPROVED, Status.SCHEDULED):
        raise BookingError("Отметить выдачу можно только для согласованной заявки.")
    b.status, b.issued_at = Status.ISSUED, now
    await s.commit()
    return b


async def mark_returned(s: AsyncSession, booking_id: int, now: datetime) -> Booking:
    b = await get_booking(s, booking_id)
    if b is None or b.status != Status.ISSUED:
        raise BookingError("Отметить возврат можно только для выданного оборудования.")
    b.status, b.returned_at = Status.RETURNED, now
    await s.commit()
    return b


async def cancel(
    s: AsyncSession, booking_id: int, by: str, now: datetime, reason: str = "",
    student: Student | None = None,
) -> Booking:
    b = await get_booking(s, booking_id)
    if b is None:
        raise BookingError("Заявка не найдена.")
    if student is not None and b.student_id != student.id:
        raise BookingError("Это не ваша заявка.")
    allowed = (Status.PENDING, Status.APPROVED, Status.SCHEDULED, Status.INTERNAL)
    if b.status not in allowed:
        raise BookingError("Эту заявку уже нельзя отменить.")
    b.status = Status.CANCELLED
    b.comment = (b.comment + "\n" if b.comment else "") + f"Отменено ({by}) {now:%d.%m %H:%M}" + (
        f": {reason}" if reason else "")
    await s.commit()
    return b


EDITABLE = (Status.PENDING, Status.APPROVED, Status.SCHEDULED, Status.ISSUED, Status.INTERNAL)


async def update_booking(
    s: AsyncSession, booking_id: int, start: date, end: date, items: dict[int, int], by: str,
    now: datetime,
) -> tuple[Booking, str]:
    """Изменение дат и состава действующей брони специалистом.

    Занятость проверяется так же, как при создании, но без самой этой брони,
    поэтому продление и добавление техники проходят, только если она свободна.
    Ограничения матрицы и срока брони не применяются: решение принимает
    сотрудник кафедры. Для выданной брони дату начала менять нельзя (техника
    уже на руках); уменьшение количества — это частичный возврат.
    Возвращает (бронь, описание изменений для уведомления и истории).
    """
    items = {e: q for e, q in items.items() if q > 0}
    if not items:
        raise BookingError("В брони не осталось оборудования. Отмените её или отметьте возврат.")
    if end < start:
        raise BookingError("Дата окончания раньше даты начала.")
    async with _lock:
        b = await get_booking(s, booking_id)
        if b is None:
            raise BookingError("Заявка не найдена.")
        if b.status not in EDITABLE:
            raise BookingError("Закрытую заявку изменить нельзя.")
        if b.status == Status.ISSUED and start != b.start_date:
            raise BookingError("Оборудование уже выдано — дату начала менять нельзя.")
        if start != b.start_date and start < now.date():
            raise BookingError("Новая дата начала уже прошла.")
        await _check_free(s, items, start, end, now.date(), exclude_id=b.id)

        names = {e.id: e.name for e in await equipment_list(s)}
        old = {it.equipment_id: it.quantity for it in b.items}
        changes = []
        if (start, end) != (b.start_date, b.end_date):
            changes.append(f"даты: {b.start_date:%d.%m}–{b.end_date:%d.%m} → {start:%d.%m}–{end:%d.%m}")
        for e in sorted(set(old) | set(items), key=lambda x: names.get(x, "")):
            o, n = old.get(e, 0), items.get(e, 0)
            if o != n:
                changes.append(f"{names.get(e, '?')}: {o} → {n}")
        if not changes:
            raise BookingError("Ничего не изменилось.")

        # Правим состав на месте: удалить-и-вставить ту же позицию в одном
        # flush нарушило бы уникальность (booking_id, equipment_id)
        for it in list(b.items):
            if it.equipment_id not in items:
                b.items.remove(it)
            else:
                it.quantity = items[it.equipment_id]
        for e, q in items.items():
            if e not in old:
                b.items.append(BookingItem(equipment_id=e, quantity=q))
        b.start_date, b.end_date = start, end
        if b.issue_date and not (start <= b.issue_date <= end) and b.status != Status.ISSUED:
            b.issue_date = max(start, now.date())
        if b.status == Status.ISSUED:
            b.return_reminder_sent = False  # новый срок — новое напоминание
        desc = "; ".join(changes)
        b.comment = (b.comment + "\n" if b.comment else "") + f"Изменено ({by}) {now:%d.%m %H:%M}: {desc}"
        await s.commit()
    return await get_booking(s, b.id), desc


async def list_bookings(
    s: AsyncSession, statuses: tuple[Status, ...] | None = None, limit: int = 300
) -> list[Booking]:
    q = select(Booking).order_by(Booking.start_date.desc(), Booking.id.desc()).limit(limit)
    if statuses:
        q = q.where(Booking.status.in_(statuses))
    return list(await s.scalars(q))


# ---------------------------------------------------------------- внутренние брони


async def create_internal(
    s: AsyncSession, title: str, start: date, end: date, items: dict[int, int], by: str,
    now: datetime,
) -> Booking:
    items = {e: q for e, q in items.items() if q > 0}
    title = title.strip()
    if not title:
        raise BookingError("Укажите название мероприятия.")
    if not items:
        raise BookingError("Не выбрано ни одной позиции.")
    if end < start:
        raise BookingError("Дата окончания раньше даты начала.")
    async with _lock:
        await _check_free(s, items, start, end, now.date())
        b = Booking(kind=BookingKind.INTERNAL, status=Status.INTERNAL, start_date=start,
                    end_date=end, title=title, created_at=now, created_by=by)
        b.items = [BookingItem(equipment_id=e, quantity=q) for e, q in items.items()]
        s.add(b)
        await s.commit()
    return await get_booking(s, b.id)


# ---------------------------------------------------------------- фоновые задачи


async def expire_pending(s: AsyncSession, now: datetime, ttl_hours: int) -> list[Booking]:
    q = select(Booking).where(Booking.status == Status.PENDING,
                              or_(Booking.created_at < now - timedelta(hours=ttl_hours),
                                  Booking.end_date < now.date()))
    expired = list(await s.scalars(q))
    for b in expired:
        b.status, b.decided_at = Status.EXPIRED, now
    await s.commit()
    return expired


async def due_return_reminders(s: AsyncSession, now: datetime) -> list[Booking]:
    """Выданные брони, у которых завтра (или уже) срок возврата; с 10:00."""
    if now.hour < 10:
        return []
    q = select(Booking).where(
        Booking.status == Status.ISSUED,
        Booking.return_reminder_sent.is_(False),
        Booking.end_date <= now.date() + timedelta(days=1),
    )
    due = list(await s.scalars(q))
    for b in due:
        b.return_reminder_sent = True
    await s.commit()
    return due


async def today_agenda(s: AsyncSession, today: date) -> tuple[list[Booking], list[Booking]]:
    issues = await s.scalars(select(Booking).where(
        Booking.status.in_([Status.SCHEDULED, Status.APPROVED]), Booking.issue_date <= today))
    returns = await s.scalars(select(Booking).where(
        Booking.status == Status.ISSUED, Booking.end_date <= today))
    return list(issues), list(returns)


# ---------------------------------------------------------------- инвентарь


async def adjust_inventory(
    s: AsyncSession, equipment_id: int, delta: int, reason: str, actor: str, now: datetime,
    today: date,
) -> dict[int, list[date]]:
    """Списание (delta < 0) или поступление (delta > 0).

    Возвращает {equipment_id: [дни]} — дни, на которые после списания брони
    превышают количество; специалисту нужно решить, кого из них отменить.
    """
    if delta == 0:
        raise BookingError("Количество должно быть ненулевым.")
    async with _lock:
        e = await s.get(Equipment, equipment_id)
        if e is None:
            raise BookingError("Позиция не найдена.")
        if e.total_quantity + delta < 0:
            raise BookingError(f"Нельзя списать больше, чем есть ({e.total_quantity}).")
        e.total_quantity += delta
        s.add(InventoryLog(equipment_id=e.id, delta=delta, reason=reason, actor=actor, created_at=now))
        await s.commit()
    horizon = today + timedelta(days=365)
    spans = await load_spans(s, today, horizon, today)
    return {k: v for k, v in overbooked_days({e.id: e.total_quantity}, spans, today, horizon).items()
            if k == e.id}


async def conflicting_bookings(
    s: AsyncSession, equipment_id: int, days: list[date], today: date
) -> list[Booking]:
    if not days:
        return []
    lo, hi = min(days), max(days)
    q = select(Booking).join(BookingItem).where(
        BookingItem.equipment_id == equipment_id,
        Booking.status.in_(BLOCKING_STATUSES),
        Booking.start_date <= hi,
        or_(Booking.end_date >= lo, Booking.status == Status.ISSUED),
    ).order_by(Booking.start_date)
    return list((await s.scalars(q)).unique())


# ---------------------------------------------------------------- шкала занятости


async def timeline(
    s: AsyncSession, start: date, end: date, today: date
) -> tuple[list[Equipment], dict[int, dict[date, list[tuple[Booking, int]]]]]:
    """Для каждой позиции и дня — список (бронь, количество)."""
    eqs = await equipment_list(s)
    q = select(Booking).where(
        Booking.status.in_(BLOCKING_STATUSES),
        Booking.start_date <= end,
        or_(Booking.end_date >= start, Booking.status == Status.ISSUED),
    )
    grid: dict[int, dict[date, list[tuple[Booking, int]]]] = {e.id: {} for e in eqs}
    for b in await s.scalars(q):
        lo, hi = effective_span(b.start_date, b.end_date, b.status == Status.ISSUED, today)
        lo, hi = max(lo, start), min(hi, end)
        d = lo
        while d <= hi:
            for it in b.items:
                grid.setdefault(it.equipment_id, {}).setdefault(d, []).append((b, it.quantity))
            d += timedelta(days=1)
    return eqs, grid


# ---------------------------------------------------------------- уведомления


async def remember_notice(s: AsyncSession, booking_id: int, chat_id: int, message_id: int,
                          kind: str) -> None:
    s.add(Notice(booking_id=booking_id, chat_id=chat_id, message_id=message_id, kind=kind))
    await s.commit()


async def pop_notices(s: AsyncSession, booking_id: int, kind: str | None = None) -> list[Notice]:
    q = select(Notice).where(Notice.booking_id == booking_id)
    if kind:
        q = q.where(Notice.kind == kind)
    notices = list(await s.scalars(q))
    for n in notices:
        await s.delete(n)
    await s.commit()
    return notices

