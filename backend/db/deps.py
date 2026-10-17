from sqlalchemy.orm import Session

from backend.db.session import SessionLocal


def get_db() -> Session:
    """Yield a request-scoped session.

    Guarantees:
    - on any exception the transaction is rolled back, so the connection returns
      to the pool in a usable state;
    - the session is always closed, even if commit/rollback raises.
    """
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
