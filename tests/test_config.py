"""ADMIN_IDS: опечатка в .env не должна ронять обработку заявок."""

from app import services
from app.config import Settings


def test_admin_ids_parsing():
    s = Settings(_env_file=None, admin_ids="@alexfrostfl, 123456789; 42 ,")
    assert s.admin_id_list == [123456789, 42]
    assert s.invalid_admin_ids == ["@alexfrostfl"]
    assert Settings(_env_file=None, admin_ids="").admin_id_list == []


async def test_specialist_ids_survive_bad_admin_ids(engine):
    from app.db import make_sessionmaker
    async with make_sessionmaker(engine)() as s:
        ids = await services.specialist_chat_ids(s, Settings(_env_file=None, admin_ids="@alexfrostfl,7"))
    assert ids == [7]
