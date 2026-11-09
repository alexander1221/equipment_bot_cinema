"""Схема базы данных.

Таблицы:
    equipment       — позиции инвентаря с количеством и уровнем доступа;
    inventory_log   — история списаний/поступлений (кто, сколько, почему);
    staff           — преподаватели, специалисты по выдаче, администраторы;
    students        — студенты, прошедшие регистрацию в боте;
    bookings        — заявки студентов и внутренние брони кафедры;
    booking_items   — состав брони (позиция × количество);
    notices         — отправленные сотрудникам сообщения с кнопками, чтобы
                      после обработки заявки убрать кнопки у всех.
"""

from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Role(enum.StrEnum):
    TEACHER = "teacher"
    SPECIALIST = "specialist"
    ADMIN = "admin"


class StudentStatus(enum.StrEnum):
    PENDING = "pending"  # зарегистрировался, ждёт проверки специалистом
    VERIFIED = "verified"  # специалист подтвердил личность и курс
    BLOCKED = "blocked"  # доступ закрыт


STUDENT_STATUS_TITLES = {
    StudentStatus.PENDING: "Ждёт подтверждения",
    StudentStatus.VERIFIED: "Подтверждён",
    StudentStatus.BLOCKED: "Заблокирован",
}


class BookingKind(enum.StrEnum):
    STUDENT = "student"
    INTERNAL = "internal"


class Status(enum.StrEnum):
    PENDING = "pending"  # ждёт решения преподавателя
    APPROVED = "approved"  # согласована, ждёт назначения выдачи
    SCHEDULED = "scheduled"  # выдача назначена, студент уведомлён
    ISSUED = "issued"  # оборудование на руках
    RETURNED = "returned"  # возвращено
    REJECTED = "rejected"  # отклонена преподавателем
    CANCELLED = "cancelled"  # отменена студентом или специалистом
    EXPIRED = "expired"  # преподаватель не ответил вовремя
    INTERNAL = "internal"  # внутренняя бронь кафедры (мастер-класс и т.п.)


# Статусы, при которых оборудование считается занятым.
BLOCKING_STATUSES = (
    Status.PENDING,
    Status.APPROVED,
    Status.SCHEDULED,
    Status.ISSUED,
    Status.INTERNAL,
)

STATUS_TITLES = {
    Status.PENDING: "Ожидает подтверждения преподавателя",
    Status.APPROVED: "Согласована, ожидает назначения выдачи",
    Status.SCHEDULED: "Выдача назначена",
    Status.ISSUED: "Выдано",
    Status.RETURNED: "Возвращено",
    Status.REJECTED: "Отклонена",
    Status.CANCELLED: "Отменена",
    Status.EXPIRED: "Истёк срок согласования",
    Status.INTERNAL: "Внутренняя бронь",
}

# Фильтры веб-панели из п. 5.2 ТЗ.
STATUS_FILTERS = {
    "new": ("Новые", (Status.PENDING, Status.APPROVED)),
    "waiting": ("Ожидают выдачи", (Status.SCHEDULED,)),
    "active": ("Активные", (Status.ISSUED,)),
    "done": (
        "Завершённые",
        (Status.RETURNED, Status.REJECTED, Status.CANCELLED, Status.EXPIRED),
    ),
    "internal": ("Внутренние", (Status.INTERNAL,)),
}


class Equipment(Base):
    __tablename__ = "equipment"

    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(300), unique=True)
    total_quantity: Mapped[int] = mapped_column(Integer, default=1)
    # Минимальный уровень доступа студента (1..3), см. app/access.py
    access_tier: Mapped[int] = mapped_column(Integer, default=3)
    # Скрытые позиции (Sony FX3) не показываются студентам вообще
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class InventoryLog(Base):
    __tablename__ = "inventory_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    equipment_id: Mapped[int] = mapped_column(ForeignKey("equipment.id"))
    delta: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime)

    equipment: Mapped[Equipment] = relationship(lazy="joined")


class Staff(Base):
    __tablename__ = "staff"

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[Role] = mapped_column(String(20))
    tg_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True)
    invite_token: Mapped[str] = mapped_column(String(64), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Student(Base):
    __tablename__ = "students"

    id: Mapped[int] = mapped_column(primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    username: Mapped[str] = mapped_column(String(100), default="")
    full_name: Mapped[str] = mapped_column(String(200))
    course: Mapped[int] = mapped_column(Integer)
    # Учебный год (год сентября), в котором студент указал курс; нужен,
    # чтобы курс автоматически рос с 1 сентября (app/access.py).
    course_year: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    # Учебная группа — со слов студента, нужна специалисту для сверки со списками
    group_name: Mapped[str] = mapped_column(String(50), default="")
    # Курс и ФИО указывает сам студент, поэтому до проверки специалистом заявки
    # подавать нельзя: иначе первокурсник «станет» старшекурсником одной кнопкой.
    status: Mapped[StudentStatus] = mapped_column(String(20), default=StudentStatus.PENDING)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    verified_by: Mapped[str] = mapped_column(String(200), default="")


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[BookingKind] = mapped_column(String(20))
    status: Mapped[Status] = mapped_column(String(20), index=True)
    start_date: Mapped[date] = mapped_column(Date, index=True)
    end_date: Mapped[date] = mapped_column(Date, index=True)
    # Название учебного проекта (для студента) или мероприятия (для внутренней брони)
    title: Mapped[str] = mapped_column(String(300), default="")

    student_id: Mapped[int | None] = mapped_column(ForeignKey("students.id"), nullable=True)
    teacher_id: Mapped[int | None] = mapped_column(ForeignKey("staff.id"), nullable=True)
    # Снимок курса на момент подачи (курс у студента может потом измениться)
    course_at_request: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime)
    created_by: Mapped[str] = mapped_column(String(100), default="")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decided_by: Mapped[str] = mapped_column(String(200), default="")
    comment: Mapped[str] = mapped_column(Text, default="")

    # Параметры выдачи (черновик, пока статус APPROVED; итог — после SCHEDULED)
    issue_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    issue_time: Mapped[str] = mapped_column(String(5), default="")
    issue_place: Mapped[str] = mapped_column(String(200), default="")
    issue_custom_text: Mapped[str] = mapped_column(Text, default="")
    issued_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    returned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    return_reminder_sent: Mapped[bool] = mapped_column(Boolean, default=False)

    student: Mapped[Student | None] = relationship(lazy="joined")
    teacher: Mapped[Staff | None] = relationship(lazy="joined")
    items: Mapped[list[BookingItem]] = relationship(
        back_populates="booking", cascade="all, delete-orphan", lazy="selectin"
    )


class BookingItem(Base):
    __tablename__ = "booking_items"
    __table_args__ = (UniqueConstraint("booking_id", "equipment_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id", ondelete="CASCADE"))
    equipment_id: Mapped[int] = mapped_column(ForeignKey("equipment.id"))
    quantity: Mapped[int] = mapped_column(Integer, default=1)

    booking: Mapped[Booking] = relationship(back_populates="items")
    equipment: Mapped[Equipment] = relationship(lazy="joined")


class Notice(Base):
    __tablename__ = "notices"

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id", ondelete="CASCADE"))
    chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(Integer)
    # teacher — запрос на согласование; specialist — запрос на назначение выдачи
    kind: Mapped[str] = mapped_column(String(20))
