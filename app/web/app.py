"""Веб-панель специалиста по выдаче (п. 3.4 и 5 ТЗ).

Страницы:
  /            — сводка на сегодня
  /timeline    — шкала занятости «оборудование × дни»
  /requests    — заявки с фильтрами (новые, ожидают выдачи, активные, завершённые)
  /requests/N  — карточка заявки: согласование, назначение выдачи, выдача/возврат, отмена
  /inventory   — инвентарь: списание и поступление, уровень доступа, новые позиции
  /events/new  — внутренняя бронь (мастер-класс, съёмка мероприятия)
  /staff       — сотрудники и ссылки-приглашения в бот (только администратор)

Вход — по паролю из .env (WEB_SPECIALIST_PASSWORD / WEB_ADMIN_PASSWORD).
"""

from __future__ import annotations

import hmac
import logging
import secrets
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path

from aiogram import Bot
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from .. import notify, services, texts
from ..config import Settings
from ..db import new_token
from ..models import (
    STATUS_FILTERS,
    STATUS_TITLES,
    STUDENT_STATUS_TITLES,
    BookingKind,
    Equipment,
    InventoryLog,
    Role,
    Staff,
    Status,
    StudentStatus,
)
from ..validation import parse_time

log = logging.getLogger(__name__)
TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
TEMPLATES.env.globals.update(texts=texts, STATUS_TITLES=STATUS_TITLES, Status=Status,
                             STATUS_FILTERS=STATUS_FILTERS, Role=Role, BookingKind=BookingKind,
                             STUDENT_STATUS_TITLES=STUDENT_STATUS_TITLES)


class LoginRequired(Exception):
    pass


class Forbidden(Exception):
    pass


def create_app(sm: async_sessionmaker, settings: Settings, clock: Callable[[], datetime],
               bot: Bot | None) -> FastAPI:
    app = FastAPI(title="Выдача оборудования", docs_url=None, redoc_url=None, openapi_url=None)
    secret = settings.web_secret_key
    if not secret:
        log.warning("WEB_SECRET_KEY не задан: сессии панели сбросятся при перезапуске")
        secret = secrets.token_urlsafe(32)
    app.add_middleware(SessionMiddleware, secret_key=secret, same_site="lax", max_age=12 * 3600)
    app.state.bot_username = ""

    # ------------------------------------------------------------ служебное

    @app.exception_handler(LoginRequired)
    async def _login_required(request: Request, exc: LoginRequired) -> Response:
        return RedirectResponse("/login", status_code=303)

    @app.exception_handler(Forbidden)
    async def _forbidden(request: Request, exc: Forbidden) -> Response:
        return HTMLResponse("Доступно только администратору. <a href='/'>На главную</a>", status_code=403)

    def role_of(request: Request, admin: bool = False) -> str:
        role = request.session.get("role")
        if role not in ("admin", "specialist"):
            raise LoginRequired()
        if admin and role != "admin":
            raise Forbidden()
        return role

    def flash(request: Request, text: str, kind: str = "ok") -> None:
        request.session.setdefault("flash", []).append([kind, text])

    def render(request: Request, name: str, **ctx) -> Response:
        msgs = request.session.pop("flash", [])
        return TEMPLATES.TemplateResponse(request, name, {
            "role": request.session.get("role"), "flashes": msgs, "today": clock().date(), **ctx})

    def back(url: str) -> RedirectResponse:
        return RedirectResponse(url, status_code=303)

    def actor(request: Request) -> str:
        return f"панель:{request.session.get('role')}"

    # ------------------------------------------------------------ вход

    @app.get("/login")
    async def login_page(request: Request) -> Response:
        configured = bool(settings.web_admin_password or settings.web_specialist_password)
        return render(request, "login.html", configured=configured)

    @app.post("/login")
    async def login(request: Request) -> Response:
        form = await request.form()
        pwd = str(form.get("password", ""))
        role = None
        # compare_digest — сравнение за постоянное время
        if settings.web_admin_password and hmac.compare_digest(pwd, settings.web_admin_password):
            role = "admin"
        elif settings.web_specialist_password and hmac.compare_digest(pwd, settings.web_specialist_password):
            role = "specialist"
        if role is None:
            flash(request, "Неверный пароль", "err")
            return back("/login")
        request.session["role"] = role
        return back("/")

    @app.get("/logout")
    async def logout(request: Request) -> Response:
        request.session.clear()
        return back("/login")

    # ------------------------------------------------------------ сводка

    @app.get("/")
    async def dashboard(request: Request) -> Response:
        role_of(request)
        today = clock().date()
        async with sm() as s:
            issues, returns = await services.today_agenda(s, today)
            counts = {}
            for key, (_title, sts) in STATUS_FILTERS.items():
                counts[key] = len(await services.list_bookings(s, sts, limit=10000))
            overdue = [b for b in returns if b.end_date < today]
            pending_students = len(await services.list_students(s, StudentStatus.PENDING))
        return render(request, "dashboard.html", issues=issues, returns=returns, counts=counts,
                      overdue=overdue, pending_students=pending_students)

    # ------------------------------------------------------------ шкала

    @app.get("/timeline")
    async def timeline(request: Request, start: str = "", days: int = 21) -> Response:
        role_of(request)
        today = clock().date()
        try:
            d0 = date.fromisoformat(start) if start else today
        except ValueError:
            d0 = today
        days = max(7, min(days, 62))
        d1 = d0 + timedelta(days=days - 1)
        async with sm() as s:
            eqs, grid = await services.timeline(s, d0, d1, today)
        day_list = [d0 + timedelta(days=i) for i in range(days)]
        # Ячейка: (занято, всего, css-класс, подсказка «кто взял»)
        cells: dict[int, list[tuple[int, int, str, str]]] = {}
        for e in eqs:
            row = []
            for d in day_list:
                lst = grid.get(e.id, {}).get(d, [])
                used = sum(q for _b, q in lst)
                total = e.total_quantity
                css = ("over" if used > total else "full" if used and used >= total
                       else "part" if used else "free")
                tip = "\n".join(
                    f"№{b.id} {b.title if b.kind == BookingKind.INTERNAL else (b.student.full_name if b.student else '')}"
                    f" ×{q} ({STATUS_TITLES[Status(b.status)]})" for b, q in lst)
                row.append((used, total, css, tip))
            cells[e.id] = row
        return render(request, "timeline.html", eqs=eqs, cells=cells, days=day_list, d0=d0, ndays=days,
                      prev=(d0 - timedelta(days=days)).isoformat(),
                      next=(d0 + timedelta(days=days)).isoformat())

    # ------------------------------------------------------------ заявки

    @app.get("/requests")
    async def requests_page(request: Request, f: str = "new") -> Response:
        role_of(request)
        sts = STATUS_FILTERS[f][1] if f in STATUS_FILTERS else None
        async with sm() as s:
            bs = await services.list_bookings(s, sts)
        return render(request, "requests.html", bookings=bs, f=f)

    @app.get("/requests/{bid}")
    async def request_card(request: Request, bid: int) -> Response:
        role_of(request)
        async with sm() as s:
            b = await services.get_booking(s, bid)
            if b is None:
                return HTMLResponse("Заявка не найдена", status_code=404)
            eqs = await services.equipment_list(s)
        return render(request, "request.html", b=b, places=settings.issue_place_list, eqs=eqs,
                      qty={it.equipment_id: it.quantity for it in b.items},
                      preview=texts.plain(texts.issue_message(b)) if b.issue_date or b.issue_custom_text else "")

    @app.post("/requests/{bid}/edit")
    async def request_edit(request: Request, bid: int) -> Response:
        role_of(request)
        form = await request.form()
        items = {}
        for k, v in form.items():
            if k.startswith("q_") and str(v).strip():
                try:
                    items[int(k[2:])] = int(str(v))
                except ValueError:
                    continue
        async with sm() as s:
            try:
                start = date.fromisoformat(str(form.get("start")))
                end = date.fromisoformat(str(form.get("end")))
                b, desc = await services.update_booking(s, bid, start, end, items, actor(request), clock())
            except ValueError:
                flash(request, "Проверьте даты", "err")
                return back(f"/requests/{bid}")
            except services.BookingError as e:
                flash(request, str(e), "err")
                return back(f"/requests/{bid}")
            if bot:
                await notify.changed(bot, s, settings, b, desc)
        flash(request, f"Изменено: {desc}" + (" — студент уведомлён" if b.kind == BookingKind.STUDENT else ""))
        return back(f"/requests/{bid}")

    @app.post("/requests/{bid}/decide")
    async def request_decide(request: Request, bid: int) -> Response:
        role_of(request)
        form = await request.form()
        approve = form.get("decision") == "approve"
        async with sm() as s:
            try:
                b = await services.decide(s, settings, bid, approve,
                                          f"{actor(request)} (за преподавателя)", clock(),
                                          comment=str(form.get("comment", "")).strip())
            except services.BookingError as e:
                flash(request, str(e), "err")
                return back(f"/requests/{bid}")
            if bot:
                await notify.decided(bot, s, settings, b)
        flash(request, "Заявка согласована" if approve else "Заявка отклонена")
        return back(f"/requests/{bid}")

    @app.post("/requests/{bid}/issue")
    async def request_issue(request: Request, bid: int) -> Response:
        role_of(request)
        form = await request.form()
        try:
            issue_date = date.fromisoformat(str(form.get("issue_date")))
            issue_time = parse_time(str(form.get("issue_time", "")))
        except ValueError as e:
            flash(request, f"Проверьте дату и время: {e}", "err")
            return back(f"/requests/{bid}")
        async with sm() as s:
            try:
                b = await services.update_issue_draft(
                    s, bid, issue_date=issue_date, issue_time=issue_time,
                    issue_place=str(form.get("issue_place", "")).strip()[:200],
                    custom_text=str(form.get("custom_text", "")).strip()[:2000])
                if form.get("action") == "send":
                    b = await services.schedule_issue(s, bid, actor(request))
                    if bot:
                        await notify.scheduled(bot, s, b)
                    flash(request, "Сообщение отправлено студенту")
                else:
                    flash(request, "Черновик сохранён")
            except services.BookingError as e:
                flash(request, str(e), "err")
        return back(f"/requests/{bid}")

    @app.post("/requests/{bid}/state")
    async def request_state(request: Request, bid: int) -> Response:
        role_of(request)
        form = await request.form()
        act = form.get("act")
        async with sm() as s:
            try:
                if act == "issued":
                    b = await services.mark_issued(s, bid, clock())
                    if bot:
                        await notify.issued(bot, b)
                    flash(request, "Отмечено: выдано")
                elif act == "returned":
                    b = await services.mark_returned(s, bid, clock())
                    if bot:
                        await notify.returned(bot, b)
                    flash(request, "Отмечено: возвращено")
                elif act == "cancel":
                    b = await services.cancel(s, bid, actor(request), clock(),
                                              reason=str(form.get("reason", "")).strip())
                    if bot and b.kind == BookingKind.STUDENT:
                        await notify.cancelled(bot, s, settings, b, by_student=False)
                    flash(request, "Отменено")
            except services.BookingError as e:
                flash(request, str(e), "err")
        return back(f"/requests/{bid}")

    # ------------------------------------------------------------ внутренние брони

    @app.get("/events/new")
    async def event_form(request: Request) -> Response:
        role_of(request)
        async with sm() as s:
            eqs = await services.equipment_list(s)
        today = clock().date()
        return render(request, "event_new.html", eqs=eqs, form={"start": today.isoformat(),
                                                                "end": today.isoformat()}, qty={})

    @app.post("/events/new")
    async def event_create(request: Request) -> Response:
        role_of(request)
        form = await request.form()
        items, qty = {}, {}
        for k, v in form.items():
            if k.startswith("q_") and str(v).strip():
                try:
                    n = int(str(v))
                except ValueError:
                    continue
                qty[int(k[2:])] = n
                if n > 0:
                    items[int(k[2:])] = n
        async with sm() as s:
            try:
                start = date.fromisoformat(str(form.get("start")))
                end = date.fromisoformat(str(form.get("end")))
                b = await services.create_internal(s, str(form.get("title", "")), start, end, items,
                                                   actor(request), clock())
            except (ValueError, services.BookingError) as e:
                flash(request, str(e) if isinstance(e, services.BookingError) else "Проверьте даты", "err")
                eqs = await services.equipment_list(s)
                return render(request, "event_new.html", eqs=eqs, form=dict(form), qty=qty)
        flash(request, f"Внутренняя бронь №{b.id} создана")
        return back(f"/requests/{b.id}")

    # ------------------------------------------------------------ инвентарь

    @app.get("/inventory")
    async def inventory(request: Request) -> Response:
        role_of(request)
        async with sm() as s:
            eqs = await services.equipment_list(s)
            logs = list(await s.scalars(select(InventoryLog).order_by(InventoryLog.id.desc()).limit(30)))
            conflicts = request.session.pop("conflicts", None)
            conflict_bookings = []
            if conflicts:
                conflict_bookings = await services.conflicting_bookings(
                    s, conflicts["eq"], [date.fromisoformat(d) for d in conflicts["days"]], clock().date())
        return render(request, "inventory.html", eqs=eqs, logs=logs, conflicts=conflicts,
                      conflict_bookings=conflict_bookings)

    @app.post("/inventory/{eid}/adjust")
    async def inventory_adjust(request: Request, eid: int) -> Response:
        role_of(request)
        form = await request.form()
        try:
            n = int(str(form.get("qty", "0")))
        except ValueError:
            n = 0
        if n <= 0:
            flash(request, "Количество должно быть положительным", "err")
            return back("/inventory")
        delta = -n if form.get("op") == "writeoff" else n
        async with sm() as s:
            try:
                bad = await services.adjust_inventory(s, eid, delta, str(form.get("reason", "")).strip(),
                                                      actor(request), clock(), clock().date())
            except services.BookingError as e:
                flash(request, str(e), "err")
                return back("/inventory")
        flash(request, "Списано" if delta < 0 else "Добавлено")
        if bad:
            days = bad[eid]
            request.session["conflicts"] = {"eq": eid, "days": [d.isoformat() for d in days]}
            flash(request, "После списания часть броней превышает остаток — см. список ниже", "err")
        return back("/inventory")

    @app.post("/inventory/{eid}/edit")
    async def inventory_edit(request: Request, eid: int) -> Response:
        role_of(request)
        form = await request.form()
        async with sm() as s:
            e = await s.get(Equipment, eid)
            if e is None:
                return back("/inventory")
            e.name = str(form.get("name", e.name)).strip() or e.name
            e.category = str(form.get("category", e.category)).strip() or e.category
            e.access_tier = min(3, max(1, int(str(form.get("tier", e.access_tier)))))
            e.hidden = form.get("hidden") == "on"
            await s.commit()
        flash(request, "Сохранено")
        return back("/inventory")

    @app.post("/inventory/new")
    async def inventory_new(request: Request) -> Response:
        role_of(request)
        form = await request.form()
        name = str(form.get("name", "")).strip()
        category = str(form.get("category", "")).strip()
        try:
            qty = max(0, int(str(form.get("qty", "1"))))
            tier = min(3, max(1, int(str(form.get("tier", "3")))))
        except ValueError:
            qty, tier = 1, 3
        if not name or not category:
            flash(request, "Укажите категорию и наименование", "err")
            return back("/inventory")
        async with sm() as s:
            if await s.scalar(select(Equipment).where(Equipment.name == name)):
                flash(request, "Такая позиция уже есть", "err")
                return back("/inventory")
            # Новая позиция встаёт в конец своей категории
            same = [e for e in await services.equipment_list(s) if e.category == category]
            order = (max(e.sort_order for e in same) if same else 10_000) + 1
            e = Equipment(category=category, name=name, total_quantity=qty, access_tier=tier,
                          hidden=form.get("hidden") == "on", sort_order=order)
            s.add(e)
            await s.flush()
            s.add(InventoryLog(equipment_id=e.id, delta=qty, reason="Новая позиция",
                               actor=actor(request), created_at=clock()))
            await s.commit()
        flash(request, "Позиция добавлена")
        return back("/inventory")

    # ------------------------------------------------------------ студенты

    @app.get("/students")
    async def students_page(request: Request, f: str = "pending") -> Response:
        role_of(request)
        status = StudentStatus(f) if f in {x.value for x in StudentStatus} else None
        today = clock().date()
        async with sm() as s:
            studs = await services.list_students(s, status)
        rows = [(st, services.course_on(st, today)) for st in studs]
        return render(request, "students.html", rows=rows, f=f)

    @app.post("/students/{sid}")
    async def students_action(request: Request, sid: int) -> Response:
        role_of(request)
        form = await request.form()
        act = form.get("act")
        async with sm() as s:
            try:
                if act == "verify":
                    course = int(str(form.get("course", "0")))
                    st = await services.verify_student(s, sid, course, actor(request), clock())
                    if bot:
                        await notify.student_verified(bot, st, course)
                    flash(request, f"{st.full_name}: подтверждён, {course} курс")
                elif act == "block":
                    st = await services.set_student_blocked(s, sid, True, actor(request), clock())
                    if bot:
                        await notify.student_blocked(bot, st)
                    flash(request, f"{st.full_name}: заблокирован")
                elif act == "unblock":
                    st = await services.set_student_blocked(s, sid, False, actor(request), clock())
                    flash(request, f"{st.full_name}: разблокирован, ждёт подтверждения")
            except (ValueError, services.BookingError) as e:
                flash(request, str(e), "err")
        return back(f"/students?f={form.get('back', 'pending')}")

    # ------------------------------------------------------------ сотрудники

    @app.get("/staff")
    async def staff_page(request: Request) -> Response:
        role_of(request, admin=True)
        if bot and not app.state.bot_username:
            try:
                app.state.bot_username = (await bot.get_me()).username or ""
            except Exception:  # без сети ссылки покажем как команду /start
                log.warning("bot.get_me failed")
        async with sm() as s:
            staff = list(await s.scalars(select(Staff).order_by(Staff.role, Staff.full_name)))
        return render(request, "staff.html", staff=staff, bot_username=app.state.bot_username)

    @app.post("/staff/new")
    async def staff_new(request: Request) -> Response:
        role_of(request, admin=True)
        form = await request.form()
        name = str(form.get("full_name", "")).strip()
        role = str(form.get("role", "teacher"))
        if not name or role not in {r.value for r in Role}:
            flash(request, "Укажите ФИО и роль", "err")
            return back("/staff")
        async with sm() as s:
            s.add(Staff(full_name=name, role=Role(role), invite_token=new_token()))
            await s.commit()
        flash(request, "Сотрудник добавлен — отправьте ему ссылку-приглашение")
        return back("/staff")

    @app.post("/staff/{sid}/edit")
    async def staff_edit(request: Request, sid: int) -> Response:
        role_of(request, admin=True)
        form = await request.form()
        async with sm() as s:
            st = await s.get(Staff, sid)
            if st is None:
                return back("/staff")
            if form.get("act") == "relink":
                # Новая ссылка; старая перестаёт работать, привязка к Telegram снимается
                st.invite_token, st.tg_id = new_token(), None
            else:
                st.full_name = str(form.get("full_name", st.full_name)).strip() or st.full_name
                st.active = form.get("active") == "on"
            await s.commit()
        flash(request, "Сохранено")
        return back("/staff")

    return app

