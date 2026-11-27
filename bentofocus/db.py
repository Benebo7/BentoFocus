from datetime import date, datetime, timedelta

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from . import config

config.DATA_DIR.mkdir(parents=True, exist_ok=True)
engine = create_engine(f"sqlite:///{config.DB_PATH}", connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _sqlite_pragmas(conn, _):
    # WAL: a API lê enquanto o coletor escreve, sem "database is locked".
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")


def init_db() -> None:
    from . import models  # noqa: F401  (registra as tabelas)

    SQLModel.metadata.create_all(engine)


def session() -> Session:
    return Session(engine)


def logical_day(dt: datetime | None = None) -> date:
    dt = dt or datetime.now()
    return (dt - timedelta(hours=config.DAY_START_HOUR)).date()
