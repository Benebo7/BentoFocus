import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, NaiveDatetime
from sqlmodel import select

from . import config, exporter, mini, music, stats, tasks
from .collector.worker import collector
from .db import init_db, logical_day, session
from .focus import focus
from .models import FixedExpense, HabitLog, Transaction

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    await focus.recover()
    task = asyncio.create_task(collector.run())
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="BentoFocus", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def revalidate_static(request: Request, call_next):
    """app.js/app.css sem cabeçalho de cache: o WebKit guardava uma cópia velha
    e juntava com o index.html novo (que vai como no-store), quebrando a página.
    no-cache = sempre pergunta (resposta 304 se não mudou; é tudo local)."""
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


def _page(name: str) -> HTMLResponse:
    """A página com ?v=<data do arquivo> no app.js/app.css: mudou o arquivo,
    muda o endereço, e o WebKit nunca junta um app.js velho do cache com uma
    página nova."""
    html = (STATIC / name).read_text()
    for asset in ("app.js", "app.css"):
        html = html.replace(f'/static/{asset}"', f'/static/{asset}?v={int((STATIC / asset).stat().st_mtime)}"')
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/", include_in_schema=False)
async def index():
    return _page("index.html")


# Todos os endpoints são async de propósito: rodam no mesmo event loop do
# coletor, então nunca disputam o bloco em andamento entre threads.


@app.get("/mini", include_in_schema=False)
async def mini_page():
    return _page("mini.html")


@app.post("/api/mini")
async def mini_open():
    mini.open()
    return {"ok": True}


@app.get("/api/music")
async def music_state():
    return await music.state()


@app.post("/api/music/{action}")
async def music_action(action: str, value: float | None = None):
    if action == "volume":
        if value is None:
            raise HTTPException(400, "Informe value (0 a 1).")
        await music.set_volume(value)
    elif action == "open":
        asyncio.create_task(music.start(play=False))
    elif action in music.ACTIONS:
        await music.control(action)
    else:
        raise HTTPException(404, "Ação desconhecida.")
    return await music.state()


@app.get("/api/state")
async def state():
    return stats.dashboard()


@app.get("/api/collector")
async def collector_status():
    return collector.status


# --- foco ---------------------------------------------------------------------


class StartIn(BaseModel):
    intention: str = Field("", max_length=140)
    minutes: int = Field(25, ge=5, le=180)
    allow_youtube: bool = False
    cycles: int = Field(1, ge=1, le=6)
    spotify: bool = False
    task_id: int | None = None


@app.post("/api/focus/start")
async def focus_start(body: StartIn):
    try:
        await focus.start(
            body.intention,
            body.minutes,
            body.allow_youtube,
            body.cycles,
            body.spotify,
            task_id=body.task_id,
        )
    except ValueError as e:
        raise HTTPException(409, str(e))
    return stats.dashboard()


@app.post("/api/focus/extend")
async def focus_extend(minutes: int = 5):
    try:
        await focus.extend(minutes)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return stats.dashboard()


@app.post("/api/focus/stop")
async def focus_stop():
    # Parar antes do fim não é "falha": o tempo feito conta igual. Só não
    # dispara a notificação e a pausa de bloco concluído.
    early = focus.current is not None and datetime.now() < focus.ends_at()
    await focus.stop(cancelled=early, notify=not early)
    return stats.dashboard()


@app.post("/api/focus/skip-break")
async def focus_skip_break():
    try:
        await focus.skip_break()
    except ValueError as e:
        raise HTTPException(409, str(e))
    return stats.dashboard()


class LogIn(BaseModel):
    minutes: int = Field(ge=5, le=600)
    intention: str = Field("", max_length=140)
    ended_at: NaiveDatetime | None = None  # padrão: agora


@app.post("/api/focus/log")
async def focus_log(body: LogIn):
    """Foco feito sem o timer: entra no dia como bloco registrado à mão."""
    try:
        await focus.log(body.minutes, body.intention, body.ended_at)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return stats.dashboard()


@app.delete("/api/focus/{session_id}")
async def focus_delete(session_id: int):
    try:
        focus.delete_manual(session_id)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return stats.dashboard()


@app.post("/api/focus/end-plan")
async def focus_end_plan():
    await focus.end_plan()
    return stats.dashboard()


# --- hábitos ------------------------------------------------------------------


class HabitIn(BaseModel):
    habit: str
    day: date | None = None


@app.post("/api/habits/toggle")
async def habit_toggle(body: HabitIn):
    if body.habit not in config.HABITS:
        raise HTTPException(400, "Hábito desconhecido.")
    day = body.day or logical_day()
    with session() as db:
        row = db.exec(select(HabitLog).where(HabitLog.day == day, HabitLog.habit == body.habit)).first()
        if row:
            db.delete(row)
        else:
            db.add(HabitLog(day=day, habit=body.habit))
        db.commit()
    return stats.dashboard()


# --- finanças -----------------------------------------------------------------


class TxIn(BaseModel):
    amount_cents: int = Field(gt=0)
    kind: str = Field(pattern="^(in|out)$")
    category: str = Field("", max_length=40)
    description: str = Field("", max_length=120)
    day: date | None = None


class FixedIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    amount_cents: int = Field(gt=0)


@app.get("/api/finance")
async def finance(month: str | None = None):
    return stats.finance(month)


@app.post("/api/finance/tx")
async def finance_add(body: TxIn):
    with session() as db:
        db.add(Transaction(**body.model_dump(exclude={"day"}), day=body.day or date.today()))
        db.commit()
    return stats.finance()


@app.delete("/api/finance/tx/{tx_id}")
async def finance_delete(tx_id: int):
    with session() as db:
        if row := db.get(Transaction, tx_id):
            db.delete(row)
            db.commit()
    return stats.finance()


@app.post("/api/finance/fixed")
async def fixed_add(body: FixedIn):
    with session() as db:
        db.add(FixedExpense(**body.model_dump()))
        db.commit()
    return stats.finance()


@app.delete("/api/finance/fixed/{fixed_id}")
async def fixed_delete(fixed_id: int):
    with session() as db:
        if row := db.get(FixedExpense, fixed_id):
            db.delete(row)
            db.commit()
    return stats.finance()


# --- tarefas, estudos & agenda ------------------------------------------------


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    parent_id: int | None = None
    description: str = Field("", max_length=500)
    kind: str = "focus"  # focus | todo
    time: str | None = None
    reminder_minutes: int = 15
    planned_blocks: int = Field(1, ge=1, le=8)
    completed_blocks: int = 0
    day: date | None = None
    status: str = "inbox"


class TaskDateIn(BaseModel):
    day: date | None = None
    status: str | None = None


@app.get("/api/tasks")
async def tasks_get():
    return tasks.get_organized_tasks()


@app.post("/api/tasks")
async def tasks_add(body: TaskIn):
    tasks.create_task(
        title=body.title,
        parent_id=body.parent_id,
        description=body.description,
        kind=body.kind,
        time=body.time,
        reminder_minutes=body.reminder_minutes,
        planned_blocks=body.planned_blocks,
        completed_blocks=body.completed_blocks,
        day=body.day,
        status=body.status,
    )
    return tasks.get_organized_tasks()


@app.post("/api/tasks/{task_id}/toggle")
async def tasks_toggle(task_id: int):
    tasks.toggle_task(task_id)
    return tasks.get_organized_tasks()


@app.post("/api/tasks/{task_id}/progress")
async def tasks_progress(task_id: int):
    tasks.record_progress(task_id)
    return tasks.get_organized_tasks()


@app.post("/api/tasks/{task_id}/pull")
async def tasks_pull(task_id: int):
    tasks.pull_to_today(task_id)
    return tasks.get_organized_tasks()


@app.post("/api/tasks/{task_id}/set-date")
async def tasks_set_date(task_id: int, body: TaskDateIn):
    tasks.set_task_date(task_id, body.day, body.status)
    return tasks.get_organized_tasks()


@app.delete("/api/tasks/{task_id}")
async def tasks_delete(task_id: int):
    tasks.delete_task(task_id)
    return tasks.get_organized_tasks()


@app.post("/api/tasks/sync")
async def tasks_sync():
    res = tasks.sync_turso()
    data = tasks.get_organized_tasks()
    return {"sync_result": res, "tasks": data}


class DayPlanIn(BaseModel):
    day: date | None = None
    content: str = ""


@app.get("/api/day-plan")
async def day_plan_get(day: date | None = None):
    return tasks.get_day_plan(day)


@app.post("/api/day-plan")
async def day_plan_set(body: DayPlanIn):
    target_day = body.day or logical_day()
    return tasks.set_day_plan(target_day, body.content)


# --- resumo & exportação ------------------------------------------------------


@app.get("/api/export/daily")
async def export_daily(day: date | None = None, format: str = "json"):
    if format not in ("json", "markdown"):
        raise HTTPException(400, "Formato inválido. Use 'json' ou 'markdown'.")
    summary = exporter.daily_summary(day)
    if format == "markdown":
        return PlainTextResponse(
            content=summary["markdown"],
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="bentofocus-{summary["day"]}.md"'
            },
        )
    return summary



