"""Тексты сообщений и форматирование. Все сообщения — HTML (parse_mode=HTML)."""

from __future__ import annotations

from datetime import date
import re
from html import escape, unescape

from .models import Booking, BookingKind, STATUS_TITLES, Status

MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
              "августа", "сентября", "октября", "ноября", "декабря"]
MONTHS_NOM = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль",
              "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]

STATUS_ICONS = {
    Status.PENDING: "⏳", Status.APPROVED: "✅", Status.SCHEDULED: "📅", Status.ISSUED: "📦",
    Status.RETURNED: "✔️", Status.REJECTED: "❌", Status.CANCELLED: "🚫", Status.EXPIRED: "⌛",
    Status.INTERNAL: "🏫",
}

BTN_NEW = "📝 Новая заявка"
BTN_MY = "📋 Мои заявки"
BTN_PROFILE = "👤 Профиль"
BTN_HELP = "❓ Помощь"


def plain(html_text: str) -> str:
    """HTML-сообщение Telegram → обычный текст (для предпросмотра в панели)."""
    return unescape(re.sub(r"<[^>]+>", "", html_text))


def d_short(d: date) -> str:
    return d.strftime("%d.%m.%Y")


def d_long(d: date) -> str:
    return f"{d.day} {MONTHS_GEN[d.month - 1]} ({WEEKDAYS[d.weekday()]})"


def period(start: date, end: date) -> str:
    if start == end:
        return d_short(start)
    return f"{d_short(start)} – {d_short(end)}"


def items_lines(b: Booking) -> str:
    return "\n".join(
        f"• {escape(it.equipment.name)}" + (f" × {it.quantity}" if it.quantity > 1 else "")
        for it in b.items
    )


def items_inline(b: Booking) -> str:
    return ", ".join(
        escape(it.equipment.name) + (f" × {it.quantity}" if it.quantity > 1 else "") for it in b.items
    )


def student_line(b: Booking) -> str:
    if b.kind == BookingKind.INTERNAL or b.student is None:
        return f"Внутренняя бронь: {escape(b.title)}"
    course = b.course_at_request or b.student.course
    user = f" (@{escape(b.student.username)})" if b.student.username else ""
    return f"{escape(b.student.full_name)}, {course} курс{user}"


def status_line(b: Booking) -> str:
    return f"{STATUS_ICONS.get(Status(b.status), '')} {STATUS_TITLES[Status(b.status)]}"


def student_card(st, course: int) -> str:  # noqa: ANN001
    user = f"@{escape(st.username)}" if st.username else "нет username"
    return (f"<b>{escape(st.full_name)}</b>\nГруппа: {escape(st.group_name or '—')}\n"
            f"Курс (со слов студента): {course}\nTelegram: {user}, id <code>{st.tg_id}</code>")


def teacher_request(b: Booking) -> str:
    # Формулировка из п. 3.2.1 ТЗ + отметка о проверке профиля специалистом
    st = b.student
    group = f", гр. {escape(st.group_name)}" if st.group_name else ""
    user = f" (@{escape(st.username)})" if st.username else ""
    checked = "✔️ профиль и курс подтверждены кафедрой" if st.status == "verified" else "⚠️ профиль не проверен"
    return (
        f"📨 <b>Заявка №{b.id}</b>\n\n"
        f"Студент <b>{escape(st.full_name)}</b>{user}, {b.course_at_request} курс{group}, "
        f"просит выдать:\n{items_lines(b)}\n\n"
        f"с <b>{d_short(b.start_date)}</b> по <b>{d_short(b.end_date)}</b> "
        f"для съёмок проекта «{escape(b.title)}».\n{checked}\n\nПодтверждаете?"
    )


def specialist_request(b: Booking) -> str:
    # Формулировка из п. 3.3.1 ТЗ
    teacher = escape(b.teacher.full_name) if b.teacher else "—"
    return (
        f"✅ <b>Заявка №{b.id}</b> от {student_line(b)} согласована преподавателем "
        f"<b>{teacher}</b>.\n\n"
        f"Оборудование:\n{items_lines(b)}\n\n"
        f"Даты: <b>{period(b.start_date, b.end_date)}</b>\n"
        f"Проект: «{escape(b.title)}»"
        + (f"\nКомментарий преподавателя: {escape(b.comment)}" if b.comment else "")
    )


def issue_message(b: Booking) -> str:
    """Финальное сообщение студенту (шаблон из п. 3.3.2 ТЗ)."""
    if b.issue_custom_text:
        body = escape(b.issue_custom_text)
    else:
        body = (
            f"Забрать оборудование: <b>{d_long(b.issue_date)}, {escape(b.issue_time)}, "
            f"{escape(b.issue_place)}</b>.\n"
            f"Вернуть до: <b>{d_long(b.end_date)}</b>.\n\n"
            "При себе иметь студенческий билет. Проверьте комплектность при получении."
        )
    return (
        f"🎬 <b>Заявка №{b.id} согласована.</b>\n\n{body}\n\n"
        f"Оборудование:\n{items_lines(b)}"
    )


def issue_draft(b: Booking) -> str:
    return (
        f"{specialist_request(b)}\n\n"
        "<b>Сообщение студенту (черновик):</b>\n"
        "────────────\n"
        f"{issue_message(b)}\n"
        "────────────\n"
        "Измените дату, время или место кнопками ниже и нажмите «Отправить студенту»."
    )


def booking_card(b: Booking, for_student: bool = True) -> str:
    lines = [f"<b>Заявка №{b.id}</b> — {status_line(b)}",
             f"Даты: {period(b.start_date, b.end_date)}"]
    if b.title:
        lines.append(f"Проект: «{escape(b.title)}»")
    if b.teacher and for_student:
        lines.append(f"Преподаватель: {escape(b.teacher.full_name)}")
    lines.append(items_lines(b))
    if b.status in (Status.SCHEDULED,) and b.issue_date:
        if b.issue_custom_text:
            lines.append(f"Выдача: {escape(b.issue_custom_text)}")
        else:
            lines.append(f"Выдача: {d_long(b.issue_date)}, {b.issue_time}, {escape(b.issue_place)}")
    return "\n".join(lines)


HELP_STUDENT = (
    "Бот выдачи учебного кинооборудования кафедры кинопроизводства.\n\n"
    "Как получить оборудование:\n"
    "1. «📝 Новая заявка» → выберите даты, преподавателя, проект и технику.\n"
    "2. Преподаватель подтверждает заявку.\n"
    "3. Специалист назначает время выдачи — бот пришлёт, где и когда забрать.\n\n"
    "Оборудование выдаётся только под учебные проекты. Список техники зависит от курса.\n"
    "/start — меню, /my — мои заявки, /profile — изменить курс или ФИО."
)

HELP_STAFF = (
    "Команды сотрудника:\n"
    "/today — выдачи и возвраты на сегодня\n"
    "/pending — заявки, ожидающие назначения выдачи\n"
    "Полная картина и внутренние брони — в веб-панели."
)
