import json
import sqlite3
import urllib.request
from datetime import date, datetime, timedelta
from typing import Any, Optional

from . import config
from .models import TaskItem


def _is_turso() -> bool:
    return bool(config.TURSO_URL and config.TURSO_TOKEN)


def _turso_execute(sql: str, args: list[Any] | None = None) -> dict[str, Any]:
    args = args or []
    formatted_args = []
    for a in args:
        if a is None:
            formatted_args.append({"type": "null"})
        elif isinstance(a, int):
            formatted_args.append({"type": "integer", "value": str(a)})
        elif isinstance(a, float):
            formatted_args.append({"type": "float", "value": a})
        else:
            formatted_args.append({"type": "text", "value": str(a)})

    base_url = config.TURSO_URL.replace("libsql://", "https://").rstrip("/")
    url = f"{base_url}/v2/pipeline"

    payload = {
        "requests": [
            {
                "type": "execute",
                "stmt": {
                    "sql": sql,
                    "args": formatted_args,
                },
            },
            {"type": "close"},
        ]
    }
    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data_bytes,
        headers={
            "Authorization": f"Bearer {config.TURSO_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10.0) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        results = data.get("results", [])
        if results and results[0].get("type") == "ok":
            return results[0]["response"]["result"]
        elif results and results[0].get("type") == "error":
            raise RuntimeError(f"Turso error: {results[0].get('error')}")
        return {}


def init_db() -> None:
    ddl = """
    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        parent_id INTEGER,
        title TEXT NOT NULL,
        description TEXT DEFAULT '',
        kind TEXT DEFAULT 'focus',
        time TEXT,
        reminder_minutes INTEGER DEFAULT 15,
        reminder_sent INTEGER DEFAULT 0,
        planned_blocks INTEGER DEFAULT 1,
        completed_blocks INTEGER DEFAULT 0,
        status TEXT DEFAULT 'inbox',
        day TEXT,
        source TEXT DEFAULT 'telegram',
        created_at TEXT,
        completed_at TEXT
    );
    """
    ddl_directives = """
    CREATE TABLE IF NOT EXISTS directives (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        instruction TEXT NOT NULL,
        target_date TEXT,
        status TEXT DEFAULT 'active',
        created_at TEXT
    );
    """
    ddl_day_plans = """
    CREATE TABLE IF NOT EXISTS day_plans (
        day TEXT PRIMARY KEY,
        content TEXT DEFAULT '',
        updated_at TEXT
    );
    """
    if _is_turso():
        _turso_execute(ddl)
        _turso_execute(ddl_directives)
        _turso_execute(ddl_day_plans)
        for col in ("parent_id INTEGER", "completed_blocks INTEGER DEFAULT 0"):
            try:
                _turso_execute(f"ALTER TABLE tasks ADD COLUMN {col}")
            except Exception:
                pass
    else:
        config.SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(config.SQLITE_PATH) as conn:
            conn.execute(ddl)
            conn.execute(ddl_directives)
            conn.execute(ddl_day_plans)
            for col in ("parent_id INTEGER", "completed_blocks INTEGER DEFAULT 0"):
                try:
                    conn.execute(f"ALTER TABLE tasks ADD COLUMN {col}")
                except Exception:
                    pass
            conn.commit()


def create_task(
    title: str,
    parent_id: Optional[int] = None,
    description: str = "",
    kind: str = "focus",
    time: Optional[str] = None,
    reminder_minutes: int = 15,
    planned_blocks: int = 1,
    completed_blocks: int = 0,
    day: Optional[str] = None,
    status: str = "inbox",
    source: str = "telegram",
) -> TaskItem:
    created_at = config.now_brt().isoformat()
    if day is None and status == "today":
        day = config.today_brt().isoformat()

    sql = """
    INSERT INTO tasks (parent_id, title, description, kind, time, reminder_minutes, reminder_sent, planned_blocks, completed_blocks, status, day, source, created_at)
    VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)
    """
    args = [parent_id, title, description, kind, time, reminder_minutes, planned_blocks, completed_blocks, status, day, source, created_at]

    if parent_id is not None:
        # Se o tópico pai recebeu um subtópico, limpa eventual data própria do pai
        set_task_date(parent_id, "clear")

    if _is_turso():
        res = _turso_execute(sql, args)
        row_id = int(res.get("last_insert_rowid", "0")) or None
    else:
        with sqlite3.connect(config.SQLITE_PATH) as conn:
            cur = conn.cursor()
            cur.execute(sql, args)
            conn.commit()
            row_id = cur.lastrowid

    return TaskItem(
        id=row_id,
        parent_id=parent_id,
        title=title,
        description=description,
        kind=kind,
        time=time,
        reminder_minutes=reminder_minutes,
        reminder_sent=False,
        planned_blocks=planned_blocks,
        completed_blocks=completed_blocks,
        status=status,
        day=day,
        source=source,
        created_at=created_at,
    )


def _row_to_task(row: list[Any], cols: list[str]) -> TaskItem:
    d = dict(zip(cols, row))
    return TaskItem(
        id=d.get("id"),
        parent_id=d.get("parent_id"),
        title=d.get("title", ""),
        description=d.get("description", "") or "",
        kind=d.get("kind", "focus") or "focus",
        time=d.get("time"),
        reminder_minutes=int(d.get("reminder_minutes", 15) or 15),
        reminder_sent=bool(d.get("reminder_sent", 0)),
        planned_blocks=int(d.get("planned_blocks", 1) or 1),
        completed_blocks=int(d.get("completed_blocks", 0) or 0),
        status=d.get("status", "inbox"),
        day=d.get("day"),
        source=d.get("source", "telegram"),
        created_at=d.get("created_at") or "",
        completed_at=d.get("completed_at"),
    )


def get_tasks(status: Optional[str] = None, day: Optional[str] = None, kind: Optional[str] = None) -> list[TaskItem]:
    conditions = []
    args = []
    if status:
        conditions.append("status = ?")
        args.append(status)
    if day:
        conditions.append("day = ?")
        args.append(day)
    if kind:
        conditions.append("kind = ?")
        args.append(kind)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = f"SELECT id, parent_id, title, description, kind, time, reminder_minutes, reminder_sent, planned_blocks, completed_blocks, status, day, source, created_at, completed_at FROM tasks {where} ORDER BY id DESC"

    cols = ["id", "parent_id", "title", "description", "kind", "time", "reminder_minutes", "reminder_sent", "planned_blocks", "completed_blocks", "status", "day", "source", "created_at", "completed_at"]

    if _is_turso():
        res = _turso_execute(sql, args)
        rows_data = res.get("rows", [])
        tasks = []
        for r in rows_data:
            values = []
            for item in r:
                val = item.get("value")
                if item.get("type") == "integer" and val is not None:
                    val = int(val)
                values.append(val)
            tasks.append(_row_to_task(values, cols))
        return tasks
    else:
        if not config.SQLITE_PATH.exists():
            init_db()
        with sqlite3.connect(config.SQLITE_PATH) as conn:
            cur = conn.cursor()
            cur.execute(sql, args)
            rows = cur.fetchall()
            return [_row_to_task(list(r), cols) for r in rows]


def find_or_create_parent_topic(topic_name: str) -> int:
    """Procura um tópico pai de foco pelo título ou cria um se não existir."""
    topic_name = topic_name.strip()
    all_focus = get_tasks(kind="focus")
    for t in all_focus:
        if t.parent_id is None and t.title.lower() == topic_name.lower():
            return t.id

    # Não achou: cria o tópico pai
    new_parent = create_task(
        title=topic_name,
        kind="focus",
        status="inbox",
        source="telegram",
    )
    return new_parent.id


def get_today_focus() -> list[TaskItem]:
    """Retorna itens de foco agendados para hoje."""
    today_str = config.today_brt().isoformat()
    all_focus = get_tasks(kind="focus")
    return [
        t for t in all_focus
        if t.status != "cancelled" and (t.day == today_str or (t.status == "today" and not t.day))
    ]


def get_today_todos() -> list[TaskItem]:
    """Retorna afazeres e compromissos marcados para hoje."""
    today_str = config.today_brt().isoformat()
    all_active = get_tasks()
    todos = [
        t for t in all_active
        if t.kind in ("todo", "event") and (t.day == today_str or (t.status == "today" and not t.day)) and t.status != "cancelled"
    ]
    return sorted(todos, key=lambda x: x.time or "99:99")


def complete_task(task_id: int) -> bool:
    now_str = config.now_brt().isoformat()
    sql = "UPDATE tasks SET status = 'done', completed_at = ? WHERE id = ?"
    args = [now_str, task_id]

    if _is_turso():
        res = _turso_execute(sql, args)
        return int(res.get("affected_row_count", 0)) > 0
    else:
        with sqlite3.connect(config.SQLITE_PATH) as conn:
            cur = conn.cursor()
            cur.execute(sql, args)
            conn.commit()
            return cur.rowcount > 0


def set_task_date(task_id: int, day: Optional[str], time: Optional[str] = None) -> bool:
    """Define ou altera a data e hora de uma tarefa/afazer."""
    today_str = config.today_brt().isoformat()
    if day in ("clear", "none", "null", "sem data", ""):
        day = None
        status = "inbox"
    elif day == today_str:
        status = "today"
    elif day:
        status = "inbox"
    else:
        status = "inbox"

    if time:
        sql = "UPDATE tasks SET day = ?, time = ?, status = ?, reminder_sent = 0 WHERE id = ?"
        args = [day, time, status, task_id]
    else:
        sql = "UPDATE tasks SET day = ?, status = ?, reminder_sent = 0 WHERE id = ?"
        args = [day, status, task_id]

    if _is_turso():
        res = _turso_execute(sql, args)
        return int(res.get("affected_row_count", 0)) > 0
    else:
        with sqlite3.connect(config.SQLITE_PATH) as conn:
            cur = conn.cursor()
            cur.execute(sql, args)
            conn.commit()
            return cur.rowcount > 0


def pull_to_today(task_id: int) -> bool:
    """Puxa uma tarefa ou subtópico de foco para o dia de Hoje."""
    today_str = config.today_brt().isoformat()
    return set_task_date(task_id, today_str)


def record_progress(task_id: int) -> bool:
    """Soma +1 bloco de foco concluído."""
    sql = "UPDATE tasks SET completed_blocks = COALESCE(completed_blocks, 0) + 1 WHERE id = ?"
    args = [task_id]
    if _is_turso():
        res = _turso_execute(sql, args)
        return int(res.get("affected_row_count", 0)) > 0
    else:
        with sqlite3.connect(config.SQLITE_PATH) as conn:
            cur = conn.cursor()
            cur.execute(sql, args)
            conn.commit()
            return cur.rowcount > 0


def delete_task(task_id: int) -> bool:
    sql = "DELETE FROM tasks WHERE id = ?"
    args = [task_id]
    if _is_turso():
        res = _turso_execute(sql, args)
        return int(res.get("affected_row_count", 0)) > 0
    else:
        with sqlite3.connect(config.SQLITE_PATH) as conn:
            cur = conn.cursor()
            cur.execute(sql, args)
            conn.commit()
            return cur.rowcount > 0


def postpone_task(task_id: int, days: int = 1) -> Optional[TaskItem]:
    target_date = (config.today_brt() + timedelta(days=days)).isoformat()
    ok = set_task_date(task_id, target_date)
    return TaskItem(id=task_id, title="", day=target_date) if ok else None


def get_due_reminders() -> list[TaskItem]:
    today = config.today_brt()
    today_str = today.isoformat()
    now = config.now_brt()
    tasks = get_tasks(day=today_str)
    due = []
    for t in tasks:
        if t.kind in ("todo", "event") and t.time and not t.reminder_sent and t.status != "done":
            try:
                h, m = map(int, t.time.split(":"))
                event_dt = datetime.combine(today, datetime.min.time()).replace(hour=h, minute=m)
                remind_dt = event_dt - timedelta(minutes=t.reminder_minutes)
                if remind_dt <= now <= event_dt + timedelta(minutes=5):
                    due.append(t)
            except Exception:
                continue
    return due


def mark_reminder_sent(task_id: int) -> None:
    sql = "UPDATE tasks SET reminder_sent = 1 WHERE id = ?"
    if _is_turso():
        _turso_execute(sql, [task_id])
    else:
        with sqlite3.connect(config.SQLITE_PATH) as conn:
            conn.execute(sql, [task_id])
            conn.commit()


# --- Diretivas de Longo Prazo / Memória do Agente ---


def add_directive(instruction: str, target_date: Optional[str] = None) -> int:
    """Salva uma instrução futura para o agente executar (ex: no digest ou em data X)."""
    created_at = config.now_brt().isoformat()
    sql = "INSERT INTO directives (instruction, target_date, status, created_at) VALUES (?, ?, 'active', ?)"
    args = [instruction.strip(), target_date, created_at]
    if _is_turso():
        res = _turso_execute(sql, args)
        return int(res.get("last_insert_rowid", "0")) or 0
    else:
        with sqlite3.connect(config.SQLITE_PATH) as conn:
            cur = conn.cursor()
            cur.execute(sql, args)
            conn.commit()
            return cur.lastrowid or 0


def get_active_directives(target_date: Optional[str] = None) -> list[dict[str, Any]]:
    """Retorna diretivas ativas (sem data específica ou marcadas para a data informada)."""
    conditions = ["status = 'active'"]
    args = []
    if target_date:
        conditions.append("(target_date IS NULL OR target_date <= ?)")
        args.append(target_date)

    where = f"WHERE {' AND '.join(conditions)}"
    sql = f"SELECT id, instruction, target_date, status, created_at FROM directives {where} ORDER BY id ASC"
    cols = ["id", "instruction", "target_date", "status", "created_at"]

    if _is_turso():
        res = _turso_execute(sql, args)
        rows_data = res.get("rows", [])
        out = []
        for r in rows_data:
            vals = [item.get("value") for item in r]
            out.append(dict(zip(cols, vals)))
        return out
    else:
        with sqlite3.connect(config.SQLITE_PATH) as conn:
            cur = conn.cursor()
            cur.execute(sql, args)
            rows = cur.fetchall()
            return [dict(zip(cols, r)) for r in rows]


def complete_directive(directive_id: int) -> bool:
    """Marca uma diretiva como executada."""
    sql = "UPDATE directives SET status = 'completed' WHERE id = ?"
    if _is_turso():
        res = _turso_execute(sql, [directive_id])
        return int(res.get("affected_row_count", 0)) > 0
    else:
        with sqlite3.connect(config.SQLITE_PATH) as conn:
            cur = conn.cursor()
            cur.execute(sql, [directive_id])
            conn.commit()
            return cur.rowcount > 0


# --- Roteiro / Planejamento do Dia ---


def get_day_plan(day_str: Optional[str] = None) -> dict[str, Any]:
    """Retorna o roteiro/anotação de texto de um dia específico (formato YYYY-MM-DD)."""
    day_str = day_str or config.today_brt().isoformat()
    sql = "SELECT day, content, updated_at FROM day_plans WHERE day = ?"
    cols = ["day", "content", "updated_at"]
    if _is_turso():
        res = _turso_execute(sql, [day_str])
        rows = res.get("rows", [])
        if not rows:
            return {"day": day_str, "content": "", "updated_at": None}
        vals = [item.get("value") for item in rows[0]]
        return dict(zip(cols, vals))
    else:
        with sqlite3.connect(config.SQLITE_PATH) as conn:
            cur = conn.cursor()
            cur.execute(sql, [day_str])
            row = cur.fetchone()
            if not row:
                return {"day": day_str, "content": "", "updated_at": None}
            return dict(zip(cols, row))


def set_day_plan(day_str: str, content: str) -> dict[str, Any]:
    """Salva ou atualiza o roteiro/anotação de texto do dia."""
    now = config.now_brt().isoformat()
    sql = """
    INSERT INTO day_plans (day, content, updated_at)
    VALUES (?, ?, ?)
    ON CONFLICT(day) DO UPDATE SET
        content=excluded.content,
        updated_at=excluded.updated_at
    """
    args = [day_str, content, now]
    if _is_turso():
        _turso_execute(sql, args)
    else:
        with sqlite3.connect(config.SQLITE_PATH) as conn:
            cur = conn.cursor()
            cur.execute(sql, args)
            conn.commit()
    return {"day": day_str, "content": content, "updated_at": now}

