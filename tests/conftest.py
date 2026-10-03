import os
import tempfile

import pytest
from sqlalchemy import event

from app import create_app, seed_demo_data
from app.extensions import db as _db


@pytest.fixture()
def app():
    db_fd, db_path = tempfile.mkstemp(suffix=".sqlite", prefix="limeslake-test-")
    os.close(db_fd)
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"

    app = create_app()
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)

    with app.app_context():
        engine = app.extensions["sqlalchemy"].engine

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - test infra
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()

        _db.create_all()
        seed_demo_data()

    yield app

    with app.app_context():
        _db.session.remove()
        _db.drop_all()
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(db_path + suffix)
        except OSError:
            pass


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def login(client):
    def _login(username="admin", password="123456"):
        resp = client.post(
            "/auth/login",
            data={"username": username, "password": password},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        return client

    return _login
