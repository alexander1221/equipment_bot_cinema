"""Веб-панель (п. 3.4 и 5 ТЗ): вход, внутренняя бронь, списание, назначение выдачи."""

from datetime import date
from html.parser import HTMLParser

import pytest
from aiogram import Bot
from aiogram.methods import SendMessage
from fastapi.testclient import TestClient

from app import services
from app.db import make_sessionmaker
from app.models import Status
from app.web.app import create_app

from .conftest import TOKEN, FakeSession


@pytest.fixture
async def web(engine, settings, clock):
    session = FakeSession()
    bot = Bot(TOKEN, session=session)
    sm = make_sessionmaker(engine)
    client = TestClient(create_app(sm, settings, clock, bot))
    return client, sm, session


def login(client, pwd="spec"):
    r = client.post("/login", data={"password": pwd})
    assert r.url.path == "/"


async def ids(sm):
    async with sm() as s:
        return {e.name: e.id for e in await services.equipment_list(s)}


async def test_login_required_and_roles(web):
    client, _sm, _ = web
    assert client.get("/inventory").url.path == "/login"
    r = client.post("/login", data={"password": "nope"})
    assert r.url.path == "/login" and "Неверный пароль" in r.text
    login(client, "spec")
    assert client.get("/staff").status_code == 403  # сотрудники — только админ
    client.get("/logout")
    login(client, "adm")
    r = client.get("/staff")
    assert r.status_code == 200 and "Егорычев" in r.text and "inv_" in r.text


async def test_internal_event_blocks_and_shows_on_timeline(web, clock):
    client, sm, _ = web
    login(client)
    eq = await ids(sm)
    a6400 = eq["Sony a6400 Kit 16-50mm II"]
    r = client.post("/events/new", data={"title": "Съёмка дня открытых дверей",
                                         "start": "2026-11-05", "end": "2026-11-06", f"q_{a6400}": "10"})
    assert "Внутренняя бронь №1 создана" in r.text
    # Повторная бронь сверх остатка — ошибка с понятным текстом
    r = client.post("/events/new", data={"title": "Ещё одна", "start": "2026-11-06",
                                         "end": "2026-11-06", f"q_{a6400}": "1"})
    assert "уже занято" in r.text
    r = client.get("/timeline?start=2026-11-04&days=7")
    assert "10/10" in r.text and "Съёмка дня открытых дверей" in r.text
    async with sm() as s:
        b = await services.get_booking(s, 1)
        assert b.status == Status.INTERNAL


async def test_writeoff_reports_overbooking(web, clock):
    client, sm, _ = web
    login(client)
    eq = await ids(sm)
    tl60 = eq["Godox TL60"]
    async with sm() as s:
        await services.create_internal(s, "Мастер-класс", date(2026, 11, 3), date(2026, 11, 4),
                                       {tl60: 2}, "t", clock())
    r = client.post(f"/inventory/{tl60}/adjust", data={"qty": "1", "op": "writeoff", "reason": "сломан"})
    assert "превышает остаток" in r.text and "Мастер-класс" in r.text and "03.11" in r.text
    async with sm() as s:
        e = await s.get(services.Equipment, tl60)
        assert e.total_quantity == 1
    r = client.post(f"/inventory/{tl60}/adjust", data={"qty": "5", "op": "writeoff"})
    assert "Нельзя списать больше" in r.text


async def test_panel_approve_and_schedule_notifies_student(web, clock, settings):
    client, sm, session = web
    login(client)
    eq = await ids(sm)
    async with sm() as s:
        st = await services.save_student(s, 777, "Сидоров Пётр", 2, "sid", clock(), auto_verify=True)
        t = (await services.teachers(s))[0]
        b = await services.create_student_booking(s, settings, st, t.id, date(2026, 10, 26),
                                                  date(2026, 10, 27), "Этюд",
                                                  {eq["Штатив QZSD Q338 3D"]: 1}, clock())
    r = client.post(f"/requests/{b.id}/decide", data={"decision": "approve"})
    assert "Заявка согласована" in r.text and "Назначение выдачи" in r.text
    r = client.post(f"/requests/{b.id}/issue", data={"issue_date": "2026-10-26", "issue_time": "15:30",
                                                     "issue_place": "ауд. 106.1", "action": "send"})
    assert "Сообщение отправлено студенту" in r.text
    to_student = [c.text for c in session.calls if isinstance(c, SendMessage) and c.chat_id == 777]
    assert any("15:30" in m and "26 октября" in m and "ауд. 106.1" in m for m in to_student)
    r = client.post(f"/requests/{b.id}/state", data={"act": "issued"})
    assert "Отмечено: выдано" in r.text
    r = client.get("/requests?f=active")
    assert "Сидоров Пётр" in r.text


async def test_students_page_verify_and_block(web, clock):
    client, sm, session = web
    login(client)
    async with sm() as s:
        st = await services.save_student(s, 888, "Новиков Олег", 4, "nov", clock(), group_name="КП-25")
    r = client.get("/students")
    assert "Новиков Олег" in r.text and "КП-25" in r.text
    # Студент назвался 4 курсом, специалист по списку видит 1 курс
    r = client.post(f"/students/{st.id}", data={"act": "verify", "course": "1"})
    assert "подтверждён, 1 курс" in r.text
    async with sm() as s:
        st = await services.get_student(s, 888)
        assert st.status == "verified" and services.course_on(st, clock().date()) == 1
    assert any(isinstance(c, SendMessage) and c.chat_id == 888 and "1 курс" in c.text for c in session.calls)
    r = client.post(f"/students/{st.id}", data={"act": "block", "back": "verified"})
    async with sm() as s:
        assert (await services.get_student(s, 888)).status == "blocked"


class TimeSelects(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.controls = {}
        self.current = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "select":
            self.current = attrs.get("name")
            self.controls[self.current] = {"required": "required" in attrs, "options": []}
        elif tag == "option" and self.current is not None:
            self.controls[self.current]["options"].append(
                (attrs.get("value"), "selected" in attrs))

    def handle_endtag(self, tag):
        if tag == "select":
            self.current = None


@pytest.fixture
async def issue_booking(web, clock, settings):
    client, sm, session = web
    login(client)
    eq = await ids(sm)
    async with sm() as s:
        student = await services.save_student(s, 777, "Сидоров Пётр", 2, "sid", clock(), auto_verify=True)
        teacher = (await services.teachers(s))[0]
        booking = await services.create_student_booking(
            s, settings, student, teacher.id, date(2026, 10, 26), date(2026, 10, 27),
            "Этюд", {eq["Штатив QZSD Q338 3D"]: 1}, clock())
        await services.decide(s, settings, booking.id, True, "Преподаватель", clock())
        bid = booking.id
    return client, sm, session, bid


@pytest.mark.parametrize("hour,minute", [("07", "00"), ("12", "05"), ("15", "30"), ("22", "00")])
async def test_issue_time_24h_roundtrip_and_notification(issue_booking, hour, minute):
    client, sm, session, bid = issue_booking
    response = client.post(f"/requests/{bid}/issue", data={
        "issue_date": "2026-10-26", "issue_hour": hour, "issue_minute": minute,
        "issue_place": "ауд. 106.1", "action": "send"})
    assert "Сообщение отправлено студенту" in response.text
    async with sm() as s:
        booking = await services.get_booking(s, bid)
        assert booking.issue_time == f"{hour}:{minute}"
        assert booking.status == Status.SCHEDULED
    assert any(isinstance(c, SendMessage) and c.chat_id == 777
               and f"{hour}:{minute}" in c.text for c in session.calls)
    controls = TimeSelects(response.text).controls
    assert controls["issue_hour"]["required"] and controls["issue_minute"]["required"]
    assert [v for v, _ in controls["issue_hour"]["options"] if v] == [f"{v:02d}" for v in range(7, 23)]
    assert [v for v, _ in controls["issue_minute"]["options"] if v] == [f"{v:02d}" for v in range(60)]
    assert [v for v, selected in controls["issue_hour"]["options"] if selected] == [hour]
    assert [v for v, selected in controls["issue_minute"]["options"] if selected] == [minute]
    assert 'type="time"' not in response.text


@pytest.mark.parametrize("parts", [
    {"issue_hour": "06", "issue_minute": "59"},
    {"issue_hour": "22", "issue_minute": "01"},
    {"issue_hour": "14", "issue_minute": "60"},
    {"issue_hour": "24", "issue_minute": "00"},
    {"issue_hour": "14"}, {"issue_minute": "30"},
    {"issue_hour": "", "issue_minute": ""},
    {"issue_hour": "02 PM", "issue_minute": "30"},
])
async def test_invalid_issue_time_does_not_notify_or_change_booking(issue_booking, parts):
    client, sm, session, bid = issue_booking
    async with sm() as s:
        before = (await services.get_booking(s, bid)).issue_time
    response = client.post(f"/requests/{bid}/issue", data={
        "issue_date": "2026-10-26", "issue_place": "ауд. 106.1", "action": "send",
        "issue_time": "15:30", **parts})
    assert "Проверьте дату и время" in response.text
    async with sm() as s:
        booking = await services.get_booking(s, bid)
        assert booking.status == Status.APPROVED
        assert booking.issue_time == before
    assert not any(isinstance(c, SendMessage) and c.chat_id == 777 for c in session.calls)
