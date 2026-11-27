import json
import logging
import threading
import urllib.request
from datetime import date, datetime, timedelta
from typing import Any, Optional
from sqlmodel import select

from . import config
from .db import logical_day, session
from .models import DayPlan, FocusSession, Task

logger = logging.getLogger("bentofocus.tasks")


def _task_dict(
    t: Task,
    sec_by_task: dict[int, float] | None = None,
    parent_map: dict[int, str] | None = None,
) -> dict[str, Any]:
    sec = (sec_by_task or {}).get(t.id or -1, 0.0)
    return {
        "id": t.id,
        "parent_id": t.parent_id,
        "parent_title": (parent_map or {}).get(t.parent_id) if t.parent_id else None,
        "title": t.title,
        "description": t.description or "",
        "kind": t.kind or "focus",
        "time": t.time,
        "reminder_minutes": t.reminder_minutes,
        "reminder_sent": bool(t.reminder_sent),
        "planned_blocks": t.planned_blocks or 1,
        "completed_blocks": t.completed_blocks or 0,
        "focus_min": round(sec / 60),
        "status": t.status or "inbox",
        "day": t.day.isoformat() if t.day else None,
        "source": t.source or "manual",
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "completed_at": t.completed_at.isoformat() if t.completed_at else None,
    }


def get_organized_tasks() -> dict[str, Any]:
    """Retorna as tarefas organizadas nos dois mundos: Foco (Estudo/Projetos) e Afazeres (Vida/Compromissos)."""
    today = logical_day()
    today_iso = today.isoformat()
    with session() as db:
        all_tasks = db.exec(select(Task).order_by(Task.id.asc())).all()
        all_sessions = db.exec(select(FocusSession)).all()

    # Soma tempo real de foco (FocusSession) por tarefa (via task_id ou nome da intenção)
    title_to_id = {t.title.strip().lower(): t.id for t in all_tasks if t.id is not None}
    sec_by_task: dict[int, float] = {}
    for s in all_sessions:
        tid = s.task_id
        if not tid and s.intention:
            tid = title_to_id.get(s.intention.strip().lower())
        if tid:
            sec_by_task[tid] = sec_by_task.get(tid, 0.0) + (s.focus_s or 0.0)

    parent_map = {t.id: t.title for t in all_tasks if t.id is not None and t.parent_id is None}
    all_dicts = [_task_dict(t, sec_by_task, parent_map) for t in all_tasks]

    # --- 1. TÓPICOS DE FOCO & ESTUDO ---
    all_focus = [t for t in all_dicts if t["kind"] == "focus" and t["status"] != "cancelled"]
    parents_with_children = {t["parent_id"] for t in all_focus if t["parent_id"] is not None}

    # Agenda de foco: itens marcados para Hoje ou agendados com data (apenas tópicos sem subtópicos ou os próprios subtópicos)
    focus_today = [
        t for t in all_focus
        if t["id"] not in parents_with_children and (t["day"] == today_iso or (t["status"] == "today" and not t["day"]))
    ]
    focus_today = sorted(focus_today, key=lambda x: (x["status"] == "done", x["id"] or 0))

    focus_scheduled = [
        t for t in all_focus
        if t["id"] not in parents_with_children and t["day"] and t["day"] != today_iso
    ]
    focus_scheduled = sorted(focus_scheduled, key=lambda x: (x["day"] or "", x["status"] == "done", x["id"] or 0))

    # Pastas de estudo (Tópicos Pai) e seus subtópicos (ativos E concluídos continuam visíveis na pasta)
    parent_ids = {t["id"] for t in all_focus if t["parent_id"] is None}
    parents = [t for t in all_focus if t["parent_id"] is None or t["parent_id"] not in parent_ids]
    children = [t for t in all_focus if t["parent_id"] is not None and t["parent_id"] in parent_ids]

    focus_backlog = []
    for p in parents:
        sub = [c for c in children if c["parent_id"] == p["id"]]
        sub_sorted = sorted(sub, key=lambda x: (x["status"] == "done", x["id"] or 0))
        p_copy = dict(p)
        p_copy["subtopics"] = sub_sorted
        p_copy["total_focus_min"] = p["focus_min"] + sum(c["focus_min"] for c in sub_sorted)
        focus_backlog.append(p_copy)

    # Ordena pastas: ativas primeiro, concluídas depois
    focus_backlog = sorted(focus_backlog, key=lambda x: (x["status"] == "done", x["id"] or 0))

    # --- 2. AFAZERES & COMPROMISSOS ---
    all_todos = [t for t in all_dicts if t["kind"] in ("todo", "event") and t["status"] != "cancelled"]

    todos_today = [
        t for t in all_todos
        if t["day"] == today_iso or (t["status"] == "today" and not t["day"])
    ]
    todos_today = sorted(todos_today, key=lambda x: (x["status"] == "done", x["time"] or "99:99", x["id"] or 0))

    todos_scheduled = [
        t for t in all_todos
        if t["day"] and t["day"] != today_iso and t["status"] != "done"
    ]
    todos_scheduled = sorted(todos_scheduled, key=lambda x: (x["day"] or "", x["time"] or "99:99"))

    todos_inbox = [
        t for t in all_todos
        if not t["day"] and t["status"] != "done"
    ]

    todos_done = [
        t for t in all_todos
        if t["status"] == "done"
    ]
    todos_done = sorted(todos_done, key=lambda x: x["completed_at"] or "", reverse=True)

    return {
        "today_str": today_iso,
        "focus_today": focus_today,
        "focus_scheduled": focus_scheduled,
        "focus_backlog": focus_backlog,
        "todos_today": todos_today,
        "todos_scheduled": todos_scheduled,
        "todos_inbox": todos_inbox,
        "todos_done": todos_done,
    }


def create_task(
    title: str,
    parent_id: Optional[int] = None,
    description: str = "",
    kind: str = "focus",
    time: Optional[str] = None,
    reminder_minutes: int = 15,
    planned_blocks: int = 1,
    completed_blocks: int = 0,
    day: Optional[date] = None,
    status: str = "inbox",
    source: str = "manual",
) -> Task:
    if config.TURSO_URL and config.TURSO_TOKEN:
        try:
            sync_turso()
        except Exception:
            pass
    with session() as db:
        parent_to_push = None
        if parent_id is not None:
            parent_task = db.get(Task, parent_id)
            if parent_task and (parent_task.day is not None or parent_task.status == "today"):
                parent_task.day = None
                parent_task.status = "inbox"
                db.add(parent_task)
                parent_to_push = parent_task

        task = Task(
            parent_id=parent_id,
            title=title.strip(),
            description=description.strip(),
            kind=kind,
            time=time.strip() if time else None,
            reminder_minutes=reminder_minutes,
            planned_blocks=planned_blocks if kind == "focus" else 1,
            completed_blocks=completed_blocks,
            day=day,
            status=status,
            source=source,
            created_at=datetime.now(),
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        if parent_to_push:
            db.refresh(parent_to_push)
            _turso_push_single(parent_to_push)
        _turso_push_single(task)
        return task


def toggle_task(task_id: int) -> Optional[Task]:
    with session() as db:
        task = db.get(Task, task_id)
        if not task:
            return None
        if task.status == "done":
            task.status = "today" if task.day == logical_day() else "inbox"
            task.completed_at = None
        else:
            task.status = "done"
            task.completed_at = datetime.now()
        db.add(task)
        db.commit()
        db.refresh(task)
        _turso_push_single(task)
        return task


def record_progress(task_id: int) -> Optional[Task]:
    """Incrementa +1 bloco concluído na tarefa de foco."""
    with session() as db:
        task = db.get(Task, task_id)
        if not task:
            return None
        task.completed_blocks = (task.completed_blocks or 0) + 1
        db.add(task)
        db.commit()
        db.refresh(task)
        _turso_push_single(task)
        return task


def set_task_date(task_id: int, target_day: Optional[date], status: Optional[str] = None) -> Optional[Task]:
    with session() as db:
        task = db.get(Task, task_id)
        if not task:
            return None
        task.day = target_day
        if task.status != "done":
            if status:
                task.status = status
            elif target_day == logical_day():
                task.status = "today"
            else:
                task.status = "inbox"
        task.reminder_sent = False
        db.add(task)
        db.commit()
        db.refresh(task)
        _turso_push_single(task)
        return task


def pull_to_today(task_id: int) -> Optional[Task]:
    return set_task_date(task_id, logical_day(), status="today")


def delete_task(task_id: int) -> bool:
    with session() as db:
        task = db.get(Task, task_id)
        if not task:
            return False
        children = db.exec(select(Task).where(Task.parent_id == task_id)).all()
        child_ids = [c.id for c in children if c.id is not None]
        for c in children:
            db.delete(c)
        db.delete(task)
        db.commit()
        for cid in child_ids:
            _turso_delete_single(cid)
        _turso_delete_single(task_id)
        return True


# --- Sincronização com Turso / libSQL -----------------------------------------


def _turso_execute(sql: str, args: list[Any] | None = None) -> dict[str, Any]:
    if not (config.TURSO_URL and config.TURSO_TOKEN):
        return {}

    formatted_args = []
    for a in (args or []):
        if a is None:
            formatted_args.append({"type": "null"})
        elif isinstance(a, int):
            formatted_args.append({"type": "integer", "value": str(a)})
        else:
            formatted_args.append({"type": "text", "value": str(a)})

    base_url = config.TURSO_URL.replace("libsql://", "https://").rstrip("/")
    url = f"{base_url}/v2/pipeline"

    payload = {
        "requests": [
            {"type": "execute", "stmt": {"sql": sql, "args": formatted_args}},
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
    try:
        with urllib.request.urlopen(req, timeout=6.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            results = data.get("results", [])
            if results and results[0].get("type") == "ok":
                return results[0]["response"]["result"]
    except Exception as e:
        logger.warning(f"Erro ao consultar Turso via HTTP: {e}")
    return {}


def _turso_push_single(task: Task):
    if not (config.TURSO_URL and config.TURSO_TOKEN):
        return
    sql = """
    INSERT INTO tasks (id, parent_id, title, description, kind, time, reminder_minutes, reminder_sent, planned_blocks, completed_blocks, status, day, source, created_at, completed_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
        parent_id=excluded.parent_id,
        title=excluded.title,
        description=excluded.description,
        kind=excluded.kind,
        time=excluded.time,
        reminder_minutes=excluded.reminder_minutes,
        reminder_sent=excluded.reminder_sent,
        planned_blocks=excluded.planned_blocks,
        completed_blocks=excluded.completed_blocks,
        status=excluded.status,
        day=excluded.day,
        completed_at=excluded.completed_at
    """
    args = [
        task.id,
        task.parent_id,
        task.title,
        task.description,
        task.kind,
        task.time,
        task.reminder_minutes,
        1 if task.reminder_sent else 0,
        task.planned_blocks,
        task.completed_blocks,
        task.status,
        task.day.isoformat() if task.day else None,
        task.source,
        task.created_at.isoformat() if task.created_at else None,
        task.completed_at.isoformat() if task.completed_at else None,
    ]
    threading.Thread(target=_turso_execute, args=(sql, args), daemon=True).start()


def _turso_delete_single(task_id: int):
    if not (config.TURSO_URL and config.TURSO_TOKEN):
        return
    threading.Thread(target=_turso_execute, args=("DELETE FROM tasks WHERE id = ?", [task_id]), daemon=True).start()


def sync_turso() -> dict:
    if not (config.TURSO_URL and config.TURSO_TOKEN):
        return {"synced": False, "reason": "Turso não configurado (TURSO_URL / TURSO_TOKEN)."}

    cols = ["id", "parent_id", "title", "description", "kind", "time", "reminder_minutes", "reminder_sent", "planned_blocks", "completed_blocks", "status", "day", "source", "created_at", "completed_at"]
    sql = "SELECT id, parent_id, title, description, kind, time, reminder_minutes, reminder_sent, planned_blocks, completed_blocks, status, day, source, created_at, completed_at FROM tasks"
    res = _turso_execute(sql)
    rows_data = res.get("rows", [])
    if not rows_data:
        return {"synced": True, "count": 0}

    count = 0
    turso_ids = set()
    with session() as db:
        for r in rows_data:
            vals = []
            for item in r:
                val = item.get("value")
                if item.get("type") == "integer" and val is not None:
                    val = int(val)
                vals.append(val)
            d = dict(zip(cols, vals))

            t_id = d.get("id")
            if not t_id:
                continue
            turso_ids.add(t_id)

            day_val = None
            if d.get("day"):
                try:
                    day_val = date.fromisoformat(str(d["day"])[:10])
                except Exception:
                    day_val = None

            created_val = datetime.now()
            if d.get("created_at"):
                try:
                    created_val = datetime.fromisoformat(str(d["created_at"]))
                except Exception:
                    pass

            completed_val = None
            if d.get("completed_at"):
                try:
                    completed_val = datetime.fromisoformat(str(d["completed_at"]))
                except Exception:
                    pass

            existing = db.get(Task, t_id)
            if existing:
                existing.parent_id = d.get("parent_id")
                existing.title = d.get("title", "") or ""
                existing.description = d.get("description", "") or ""
                existing.kind = d.get("kind", "focus") or "focus"
                existing.time = d.get("time")
                existing.reminder_minutes = int(d.get("reminder_minutes", 15) or 15)
                existing.reminder_sent = bool(d.get("reminder_sent", 0))
                existing.planned_blocks = int(d.get("planned_blocks", 1) or 1)
                existing.completed_blocks = int(d.get("completed_blocks", 0) or 0)
                existing.status = d.get("status", "inbox") or "inbox"
                existing.day = day_val
                existing.completed_at = completed_val
                db.add(existing)
            else:
                new_task = Task(
                    id=t_id,
                    parent_id=d.get("parent_id"),
                    title=d.get("title", "") or "",
                    description=d.get("description", "") or "",
                    kind=d.get("kind", "focus") or "focus",
                    time=d.get("time"),
                    reminder_minutes=int(d.get("reminder_minutes", 15) or 15),
                    reminder_sent=bool(d.get("reminder_sent", 0)),
                    planned_blocks=int(d.get("planned_blocks", 1) or 1),
                    completed_blocks=int(d.get("completed_blocks", 0) or 0),
                    status=d.get("status", "inbox") or "inbox",
                    day=day_val,
                    source=d.get("source", "telegram") or "telegram",
                    created_at=created_val,
                    completed_at=completed_val,
                )
                db.add(new_task)
            count += 1

        # Remove localmente itens que foram apagados no Turso pelo agente
        if turso_ids:
            all_local = db.exec(select(Task)).all()
            for lt in all_local:
                if lt.id is not None and lt.id not in turso_ids:
                    db.delete(lt)

        db.commit()

    # Também sincroniza day_plans do Turso
    try:
        _turso_execute("CREATE TABLE IF NOT EXISTS day_plans (day TEXT PRIMARY KEY, content TEXT, updated_at TEXT)")
        res_dp = _turso_execute("SELECT day, content, updated_at FROM day_plans")
        rows_dp = res_dp.get("rows", [])
        if rows_dp:
            with session() as db:
                for r in rows_dp:
                    d_day = r[0].get("value")
                    d_content = r[1].get("value") or ""
                    d_upd = r[2].get("value")
                    if not d_day:
                        continue
                    p_day = date.fromisoformat(str(d_day)[:10])
                    p_upd = datetime.fromisoformat(str(d_upd)) if d_upd else datetime.now()
                    existing = db.get(DayPlan, p_day)
                    if existing:
                        existing.content = d_content
                        existing.updated_at = p_upd
                        db.add(existing)
                    else:
                        db.add(DayPlan(day=p_day, content=d_content, updated_at=p_upd))
                db.commit()
    except Exception as e:
        logger.warning(f"Erro ao sincronizar day_plans do Turso: {e}")

    return {"synced": True, "count": count}


# --- Roteiro / Planejamento do Dia --------------------------------------------


def get_day_plan(target_day: date | None = None) -> dict[str, Any]:
    target_day = target_day or logical_day()
    with session() as db:
        plan = db.get(DayPlan, target_day)
        if not plan:
            return {
                "day": target_day.isoformat(),
                "content": "",
                "updated_at": None,
            }
        return {
            "day": plan.day.isoformat(),
            "content": plan.content or "",
            "updated_at": plan.updated_at.isoformat() if plan.updated_at else None,
        }


def set_day_plan(target_day: date, content: str) -> dict[str, Any]:
    now = datetime.now()
    with session() as db:
        plan = db.get(DayPlan, target_day)
        if plan:
            plan.content = content
            plan.updated_at = now
        else:
            plan = DayPlan(day=target_day, content=content, updated_at=now)
        db.add(plan)
        db.commit()
        db.refresh(plan)

    _turso_push_day_plan(target_day, content, now)

    return {
        "day": plan.day.isoformat(),
        "content": plan.content,
        "updated_at": plan.updated_at.isoformat(),
    }


def _turso_push_day_plan(target_day: date, content: str, updated_at: datetime):
    if not (config.TURSO_URL and config.TURSO_TOKEN):
        return
    _turso_execute("CREATE TABLE IF NOT EXISTS day_plans (day TEXT PRIMARY KEY, content TEXT, updated_at TEXT)")
    sql = """
    INSERT INTO day_plans (day, content, updated_at)
    VALUES (?, ?, ?)
    ON CONFLICT(day) DO UPDATE SET
        content=excluded.content,
        updated_at=excluded.updated_at
    """
    args = [target_day.isoformat(), content, updated_at.isoformat()]
    threading.Thread(target=_turso_execute, args=(sql, args), daemon=True).start()

