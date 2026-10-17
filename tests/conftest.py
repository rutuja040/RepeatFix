import atexit
import os
import sys
import tempfile

# Force an isolated SQLite database for the whole test session BEFORE any backend
# module (and therefore backend.core.config) is imported, so tests never touch the
# development database. The file is removed when the session ends.
_fd, _db_path = tempfile.mkstemp(suffix="_repeatfix_test.db")
os.close(_fd)
os.environ["DB_URL"] = f"sqlite:///{_db_path}"

if "backend.core.config" in sys.modules:  # pragma: no cover - defensive
    sys.modules["backend.core.config"].settings.db_url = os.environ["DB_URL"]


def _cleanup() -> None:
    try:
        os.remove(_db_path)
    except OSError:
        pass


atexit.register(_cleanup)

import pytest  # noqa: E402


@pytest.fixture()
def client():
    """Function-scoped TestClient backed by a freshly reset test database.

    Tables are dropped and recreated before each test, so tests are independent
    of each other and of any previously stored rows.
    """
    from fastapi.testclient import TestClient

    from backend.db.session import Base, engine
    import backend.models  # noqa: F401  # register models

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    from backend.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def client_no_raise():
    """Like ``client`` but does not re-raise unhandled server exceptions.

    Used to assert that application error handlers return generic 500 bodies
    without leaking internal details.
    """
    from fastapi.testclient import TestClient

    from backend.db.session import Base, engine
    import backend.models  # noqa: F401

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    from backend.main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
