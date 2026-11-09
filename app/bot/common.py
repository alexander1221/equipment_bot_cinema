"""/start, регистрация студента, подключение сотрудников, профиль, помощь, FX3."""

from __future__ import annotations

from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart, Filter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import async_sessionmaker

from .. import access, notify, services, texts
from ..config import Settings
from ..models import STUDENT_STATUS_TITLES, Role, StudentStatus
from ..validation import normalize_fio
from . import keyboards as kb

router = Router(name="common")
fx3_router = Router(name="fx3")

ROLE_TITLES = {Role.TEACHER: "преподаватель", Role.SPECIALIST: "специалист по выдаче",
               Role.ADMIN: "администратор"}


class Reg(StatesGroup):
    course = State()
    fio = State()
    group = State()


@router.message(CommandStart(deep_link=True, magic=F.args.startswith("inv_")))
async def staff_invite(message: Message, command: CommandObject, state: FSMContext,
                       sm: async_sessionmaker) -> None:
    await state.clear()
    async with sm() as s:
        st = await services.bind_staff(s, command.args[4:], message.from_user.id)
    if st is None:
        await message.answer("Ссылка-приглашение недействительна. Попросите новую у администратора.")
        return
    await message.answer(
        f"Вы подключены как <b>{ROLE_TITLES[Role(st.role)]}</b>: {escape(st.full_name)}.\n"
        "Сюда будут приходить уведомления о заявках.\n\n" + texts.HELP_STAFF
    )


@router.message(CommandStart())
async def start(message: Message, state: FSMContext, sm: async_sessionmaker, settings: Settings) -> None:
    await state.clear()
    async with sm() as s:
        staff = await services.staff_by_tg(s, message.from_user.id)
        student = await services.get_student(s, message.from_user.id)
    if staff is not None and student is None:
        await message.answer(f"Здравствуйте, {escape(staff.full_name)}!\n\n{texts.HELP_STAFF}")
        return
    if student is not None:
        note = services.can_book(student, settings)
        await message.answer(f"Здравствуйте, {escape(student.full_name)}!\n\n{texts.HELP_STUDENT}"
                             + (f"\n\n⚠️ {note}" if note else ""),
                             reply_markup=kb.main_menu())
        return
    await state.set_state(Reg.course)
    await message.answer(
        "Здравствуйте! Это бот выдачи учебного кинооборудования кафедры кинопроизводства.\n\n"
        "Для начала выберите ваш курс:", reply_markup=kb.courses())


@router.callback_query(kb.CourseCb.filter(), Reg.course)
async def pick_course(cb: CallbackQuery, callback_data: kb.CourseCb, state: FSMContext) -> None:
    await state.update_data(course=callback_data.course)
    await state.set_state(Reg.fio)
    await cb.message.edit_text(f"Курс: <b>{callback_data.course}</b>.\n\n"
                               "Теперь введите ФИО полностью, например: <i>Иванов Иван Иванович</i>")
    await cb.answer()


@router.message(Reg.fio, F.text)
async def enter_fio(message: Message, state: FSMContext) -> None:
    try:
        fio = normalize_fio(message.text)
    except ValueError as e:
        await message.answer(f"{e}\nПопробуйте ещё раз.")
        return
    await state.update_data(fio=fio)
    await state.set_state(Reg.group)
    await message.answer("Укажите вашу учебную группу, например: <i>КП-21</i>")


@router.message(Reg.group, F.text)
async def enter_group(message: Message, state: FSMContext, sm: async_sessionmaker,
                      settings: Settings, clock, bot: Bot) -> None:
    group = " ".join(message.text.split())
    if not (2 <= len(group) <= 30) or group.startswith("/"):
        await message.answer("Название группы — от 2 до 30 символов. Попробуйте ещё раз.")
        return
    data = await state.get_data()
    async with sm() as s:
        st = await services.save_student(
            s, message.from_user.id, data["fio"], data["course"], message.from_user.username or "",
            clock(), group_name=group, auto_verify=not settings.require_student_verification)
        await state.clear()
        if st.status == StudentStatus.PENDING:
            await message.answer(
                f"Спасибо, {escape(st.full_name)}! Профиль отправлен на проверку специалисту кафедры: "
                "он сверит ФИО, группу и курс. Бот сообщит, когда можно будет подавать заявки.",
                reply_markup=kb.main_menu())
            await notify.student_to_verify(bot, s, settings, st, clock().date())
        elif st.status == StudentStatus.BLOCKED:
            await message.answer("Профиль сохранён, но доступ к бронированию закрыт. Обратитесь на кафедру.",
                                 reply_markup=kb.main_menu())
        else:
            await message.answer(f"Готово, {escape(st.full_name)}! Вы зарегистрированы.\n\n{texts.HELP_STUDENT}",
                                 reply_markup=kb.main_menu())


@router.message(Command("profile"))
@router.message(F.text == texts.BTN_PROFILE)
async def profile(message: Message, sm: async_sessionmaker, clock) -> None:
    async with sm() as s:
        st = await services.get_student(s, message.from_user.id)
    if st is None:
        await message.answer("Вы ещё не зарегистрированы. Нажмите /start")
        return
    today = clock().date()
    course = services.course_on(st, today)
    await message.answer(
        f"👤 <b>{escape(st.full_name)}</b>\nГруппа: {escape(st.group_name or '—')}\nКурс: {course}\n"
        f"Статус: {STUDENT_STATUS_TITLES[StudentStatus(st.status)]}\n\n"
        "<i>Если изменить ФИО, группу или курс, профиль снова уйдёт на проверку специалисту.</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✏️ Изменить курс и ФИО", callback_data=kb.FlowCb(act="profile").pack())]]))


@router.callback_query(kb.FlowCb.filter(F.act == "profile"))
async def profile_edit(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(Reg.course)
    await cb.message.edit_text("Выберите ваш курс (после изменения профиль снова проверит специалист):",
                               reply_markup=kb.courses())
    await cb.answer()


@router.message(Command("help"))
@router.message(F.text == texts.BTN_HELP)
async def help_(message: Message, sm: async_sessionmaker) -> None:
    async with sm() as s:
        staff = await services.staff_by_tg(s, message.from_user.id)
    await message.answer(texts.HELP_STAFF if staff else texts.HELP_STUDENT)


@router.message(Command("id"))
async def my_id(message: Message) -> None:
    # Нужен администратору, чтобы вписать свой ID в ADMIN_IDS
    await message.answer(f"Ваш Telegram ID: <code>{message.from_user.id}</code>")


class HiddenQuery(Filter):
    """Сообщение похоже на поиск Sony FX3/FX30 (п. 1.3 ТЗ)."""

    async def __call__(self, message: Message) -> bool:
        return access.is_hidden_query(message.text or "")


@fx3_router.message(F.text, HiddenQuery())
async def hidden_camera(message: Message) -> None:
    await message.answer(access.HIDDEN_MESSAGE)
