from contextlib import contextmanager
from typing import Iterator

from sqlalchemy.orm import Session

from backend.db.session import SessionLocal


@contextmanager
def get_db_ctx() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
