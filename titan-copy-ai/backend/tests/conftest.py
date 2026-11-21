import os

import pytest

# Tests run against PostgreSQL when TITAN_TEST_DATABASE_URL is set (CI + local), else SQLite.
os.environ.setdefault("TITAN_MASTER_KEY", "dGl0YW4tdGVzdC1tYXN0ZXIta2V5LTMyLWJ5dGVzISE")
os.environ.setdefault("TITAN_BOOTSTRAP_CODE", "TEST-CODE-1234")
os.environ["TITAN_LLM_ENABLED"] = "false"


@pytest.fixture()
def db(tmp_path):
    from titan import db as dbm
    from titan.events import MemoryBus, set_bus

    url = os.environ.get("TITAN_TEST_DATABASE_URL") or f"sqlite:///{tmp_path}/t.db"
    dbm.configure(url)
    dbm.drop_all()
    dbm.init_db()
    bus = MemoryBus()
    set_bus(bus)
    yield dbm
    dbm.drop_all()


@pytest.fixture()
def bus(db):
    from titan.events import get_bus

    return get_bus()
