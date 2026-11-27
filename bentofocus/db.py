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
    _migrate_schema()


def _migrate_schema() -> None:
    with engine.connect() as conn:
        res = conn.exec_driver_sql("PRAGMA table_info(task)").fetchall()
        cols = {r[1] for r in res}
        if cols:
            if "parent_id" not in cols:
                conn.exec_driver_sql("ALTER TABLE task ADD COLUMN parent_id INTEGER")
            if "kind" not in cols:
                conn.exec_driver_sql("ALTER TABLE task ADD COLUMN kind TEXT DEFAULT 'focus'")
            if "time" not in cols:
                conn.exec_driver_sql("ALTER TABLE task ADD COLUMN time TEXT")
            if "reminder_minutes" not in cols:
                conn.exec_driver_sql("ALTER TABLE task ADD COLUMN reminder_minutes INTEGER DEFAULT 15")
            if "reminder_sent" not in cols:
                conn.exec_driver_sql("ALTER TABLE task ADD COLUMN reminder_sent BOOLEAN DEFAULT 0")
            if "completed_blocks" not in cols:
                conn.exec_driver_sql("ALTER TABLE task ADD COLUMN completed_blocks INTEGER DEFAULT 0")
            conn.commit()

        res_fs = conn.exec_driver_sql("PRAGMA table_info(focussession)").fetchall()
        cols_fs = {r[1] for r in res_fs}
        if cols_fs and "task_id" not in cols_fs:
            conn.exec_driver_sql("ALTER TABLE focussession ADD COLUMN task_id INTEGER")
            conn.commit()


def session() -> Session:
    return Session(engine)


def logical_day(dt: datetime | None = None) -> date:
    dt = dt or datetime.now()
    return (dt - timedelta(hours=config.DAY_START_HOUR)).date()
