"""Monta o estado do painel numa consulta só. O volume é pequeno (um usuário,
um computador), então agregar em Python é mais simples e rápido o bastante."""

import re
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta

from sqlmodel import func, select

from . import config
from .db import logical_day, session
from .focus import focus
from .models import FixedExpense, FocusSession, HabitLog, Transaction, WindowSpan

HEATMAP_WEEKS = 26
GYM_WEEKS = 52
TREND_WEEKS = 12
DISTRACT_DAYS = 14

# Distração de dias que já acabaram: não muda mais, então é calculada uma vez
# (o mini pede o estado a cada 1,5 s; reler duas semanas de janelas toda vez
# seria desperdício). Zera ao reiniciar o serviço.
_distract_cache: dict[date, tuple[int, int]] = {}
_first_day: date | None = None  # primeiro dia com dado do coletor


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _day_bounds(d: date) -> tuple[datetime, datetime]:
    start = datetime.combine(d, time(config.DAY_START_HOUR))
    return start, start + timedelta(days=1)


def _session_dict(s: FocusSession) -> dict:
    return {
        "id": s.id,
        "day": s.day.isoformat(),
        "started_at": s.started_at.isoformat(),
        "ended_at": s.ended_at.isoformat() if s.ended_at else None,
        "planned_min": s.planned_min,
        "status": s.status,
        "intention": s.intention,
        "allow_youtube": s.allow_youtube,
        "focus_min": round(_focus_s(s) / 60, 1),
    }


def _focus_s(s: FocusSession) -> float:
    return focus.elapsed_s(s) if s.status == "running" else s.focus_s


def dashboard() -> dict:
    today = logical_day()
    week_start = _monday(today)
    heat_start = week_start - timedelta(weeks=HEATMAP_WEEKS - 1)
    gym_start = week_start - timedelta(weeks=GYM_WEEKS - 1)
    first = min(heat_start, gym_start)

    with session() as db:
        sessions = db.exec(select(FocusSession).where(FocusSession.day >= first)).all()
        habits = db.exec(select(HabitLog).where(HabitLog.day >= first)).all()
        day_start, day_end = _day_bounds(today)
        spans = db.exec(
            select(WindowSpan).where(WindowSpan.end >= day_start, WindowSpan.start < day_end)
        ).all()
        recent = db.exec(
            select(FocusSession).where(FocusSession.status != "running").order_by(FocusSession.started_at.desc()).limit(6)
        ).all()

    # Pausa e "último bloco" vêm do último bloco com timer, não de um registrado à mão.
    last_block = next((r for r in recent if r.status != "manual"), None)

    live = focus.current
    if live is not None:  # o que está na memória é mais novo que o banco
        sessions = [s for s in sessions if s.id != live.id] + [live]

    # Foco por dia (minutos efetivos dos blocos).
    focus_by_day: dict[date, float] = defaultdict(float)
    blocks_by_day: Counter[date] = Counter()
    for s in sessions:
        focus_by_day[s.day] += _focus_s(s) / 60
        if _focus_s(s) >= 60:
            blocks_by_day[s.day] += 1

    habit_days: dict[str, set[date]] = defaultdict(set)
    for h in habits:
        habit_days[h.habit].add(h.day)

    # Grades diárias (semanas completas, segunda a domingo).
    days = [heat_start + timedelta(days=i) for i in range((today - heat_start).days + 1)]
    focus_grid = [
        {"d": d.isoformat(), "m": round(focus_by_day.get(d, 0)), "f": d in habit_days["faculdade"]} for d in days
    ]
    reading_grid = [{"d": d.isoformat(), "v": d in habit_days["leitura"]} for d in days]

    gym_weeks = []
    for i in range(GYM_WEEKS):
        ws = gym_start + timedelta(weeks=i)
        n = sum(1 for d in habit_days["academia"] if ws <= d < ws + timedelta(days=7))
        gym_weeks.append({"w": ws.isoformat(), "n": n})

    # Semana atual.
    week_days = [week_start + timedelta(days=i) for i in range(7)]
    week = {
        "start": week_start.isoformat(),
        "academia": sum(d in habit_days["academia"] for d in week_days),
        "leitura": sum(d in habit_days["leitura"] for d in week_days),
        "faculdade": [d in habit_days["faculdade"] for d in week_days],
        "focus_min": [round(focus_by_day.get(d, 0)) for d in week_days],
        "goals": config.WEEKLY_GOALS,
    }

    # Dias ativos nos últimos 30 (sem streak: número que se recupera sozinho).
    last30 = {today - timedelta(days=i) for i in range(30)}
    active = {d for d in last30 if focus_by_day.get(d, 0) >= 1}
    active |= (habit_days["leitura"] | habit_days["academia"]) & last30

    pc, distractions = _pc_today(spans, day_start, day_end)
    distraction_days = _distraction_days(today, pc)

    # Tendência: foco por semana nas últimas 12.
    weekly = []
    for i in range(TREND_WEEKS):
        ws = week_start - timedelta(weeks=TREND_WEEKS - 1 - i)
        weekly.append({"w": ws.isoformat(), "m": round(sum(focus_by_day.get(ws + timedelta(days=k), 0) for k in range(7)))})
    past = [w["m"] for w in weekly[:-1] if w["m"] > 0]

    hours = _focus_by_hour([x for x in sessions if x.day > today - timedelta(days=30)])

    best_week = 0
    for i in range(HEATMAP_WEEKS):
        ws = heat_start + timedelta(weeks=i)
        best_week = max(best_week, sum(focus_by_day.get(ws + timedelta(days=k), 0) for k in range(7)))

    return {
        "now": datetime.now().isoformat(timespec="seconds"),
        "today": today.isoformat(),
        "durations": config.FOCUS_DURATIONS,
        "cycles": config.CYCLE_OPTIONS,
        "plan": focus.plan,
        "break_until": _break_until(last_block),
        "current": _session_dict(live) if live else None,
        "ends_at": focus.ends_at().isoformat() if live else None,
        "last": _session_dict(last_block) if last_block else None,
        "recent": [_session_dict(s) for s in recent],
        "today_focus_min": round(focus_by_day.get(today, 0)),
        "today_blocks": blocks_by_day.get(today, 0),
        "habits_today": sorted(h for h, ds in habit_days.items() if today in ds),
        "habits_yesterday": sorted(h for h, ds in habit_days.items() if today - timedelta(days=1) in ds),
        "week": week,
        "focus_grid": focus_grid,
        "reading_grid": reading_grid,
        "gym_weeks": gym_weeks,
        "active_30": len(active),
        "best_week_min": round(best_week),
        "this_week_min": round(sum(week["focus_min"])),
        # Semana passada até o mesmo dia da semana: comparação justa no meio da semana.
        "prev_week_same_point_min": round(
            sum(focus_by_day.get(week_start - timedelta(days=7 - k), 0) for k in range(today.weekday() + 1))
        ),
        "weekly": weekly,
        "weekly_avg": round(sum(past) / len(past)) if past else 0,
        "hours": [round(m) for m in hours],
        "best_hours": _best_window(hours),
        "blocked_list": list(dict.fromkeys(label for label, _, _ in config.BLOCK_RULES)),
        "pc": {k: round(v / 60) for k, v in pc.items()},
        "distraction_days": distraction_days,
        "distractions": [
            {"label": label, "cheap": cheap, "min": round(v / 60)} for (label, cheap), v in distractions.most_common()
        ],
    }


def _focus_by_hour(sessions: list[FocusSession]) -> list[float]:
    """Minutos de foco em cada hora do dia (0-23), espalhando cada bloco pelas
    horas que ele ocupou: um bloco das 9h40 às 10h25 dá 20 min às 9h e 25 às 10h."""
    out = [0.0] * 24
    for s in sessions:
        t, end = s.started_at, s.started_at + timedelta(seconds=_focus_s(s))
        while t < end:
            nxt = min(end, t.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
            out[t.hour] += (nxt - t).total_seconds() / 60
            t = nxt
    return out


def _best_window(hours: list[float], size: int = 2) -> list[int] | None:
    """Início e fim (exclusivo) da janela de `size` horas com mais foco."""
    if sum(hours) < 30:  # pouco dado: não inventa um "horário de ouro"
        return None
    start = max(range(24 - size + 1), key=lambda h: sum(hours[h:h + size]))
    return [start, start + size]


def _break_until(last: FocusSession | None) -> str | None:
    """Fim da pausa atual: o início do próximo bloco da sequência ou, num
    bloco avulso concluído, o fim da pausa sugerida."""
    if focus.current is not None:
        return None
    if focus.plan and focus.plan.get("next_at"):
        return focus.plan["next_at"]
    if last and last.status == "done" and last.ended_at:
        until = last.ended_at + timedelta(minutes=config.break_minutes(last.planned_min))
        return until.isoformat() if until > datetime.now() else None
    return None


_COUNT_PREFIX = re.compile(r"^\(\d+\)\s*")  # "(3) " de notificações no título


def _pc_today(spans: list[WindowSpan], day_start: datetime, day_end: datetime) -> tuple[Counter, Counter]:
    """Tempo do dia no PC por categoria (active, distraction, cheap, idle) e
    por distração. Barata = feed/vídeo curto (ver CHEAP_LABELS em config)."""
    secs_of = {sp.id: (min(sp.end, day_end) - max(sp.start, day_start)).total_seconds() for sp in spans}

    # Quanto cada vídeo do YouTube somou no dia (o mesmo vídeo pode estar
    # quebrado em vários intervalos se você alternou de janela).
    yt_total: Counter[str] = Counter()
    for sp in spans:
        if sp.category == "distraction" and sp.label == "YouTube":
            yt_total[_COUNT_PREFIX.sub("", sp.title)] += secs_of[sp.id]

    pc: Counter[str] = Counter()
    distractions: Counter[tuple[str, bool]] = Counter()
    for sp in spans:
        secs = secs_of[sp.id]
        if sp.category != "distraction":
            pc[sp.category] += secs
            continue
        label = sp.label or "Outros"
        if label == "YouTube":
            title = _COUNT_PREFIX.sub("", sp.title)
            cheap = bool(config.YOUTUBE_FEED.search(title)) or yt_total[title] < config.YOUTUBE_SHORT_S
            if cheap:
                label = "YouTube Shorts / feed"
        else:
            cheap = label in config.CHEAP_LABELS
        pc["cheap" if cheap else "distraction"] += secs
        distractions[(label, cheap)] += secs
    return pc, distractions


def _distraction_days(today: date, pc_today: Counter) -> list[dict]:
    """Minutos de distração normal (n) e barata (c) por dia, nas últimas
    DISTRACT_DAYS, com a mesma regra do "no PC hoje" (vídeo curto = barata)."""
    global _first_day
    if _first_day is None:
        with session() as db:
            first = db.exec(select(func.min(WindowSpan.start))).one()
        _first_day = logical_day(first) if first else today
    # Antes de o coletor existir não houve "zero distração": o dia fica de fora.
    days = [d for d in (today - timedelta(days=i) for i in range(DISTRACT_DAYS - 1, 0, -1)) if d >= _first_day]
    values = {d: _distract_cache[d] for d in days if d in _distract_cache}
    missing = [d for d in days if d not in values]
    if missing:
        lo, hi = _day_bounds(missing[0])[0], _day_bounds(missing[-1])[1]
        with session() as db:
            spans = db.exec(select(WindowSpan).where(
                WindowSpan.category == "distraction", WindowSpan.end > lo, WindowSpan.start < hi)).all()
        for d in missing:
            ds, de = _day_bounds(d)
            pc, _ = _pc_today([sp for sp in spans if sp.end > ds and sp.start < de], ds, de)
            values[d] = (round(pc["distraction"] / 60), round(pc["cheap"] / 60))
            # Só guarda dia acabado há uns minutos: o coletor grava a cada 30 s.
            if datetime.now() - de > timedelta(minutes=5):
                _distract_cache[d] = values[d]
    values[today] = (round(pc_today["distraction"] / 60), round(pc_today["cheap"] / 60))
    return [{"d": d.isoformat(), "n": values[d][0], "c": values[d][1]} for d in [*days, today]]


def finance(month: str | None = None) -> dict:
    today = date.today()
    y, m = map(int, month.split("-")) if month else (today.year, today.month)
    first = date(y, m, 1)
    nxt = date(y + (m == 12), m % 12 + 1, 1)

    with session() as db:
        txs = db.exec(
            select(Transaction).where(Transaction.day >= first, Transaction.day < nxt).order_by(Transaction.day.desc(), Transaction.id.desc())
        ).all()
        fixed = db.exec(select(FixedExpense).order_by(FixedExpense.amount_cents.desc())).all()

    income = sum(t.amount_cents for t in txs if t.kind == "in")
    spent = sum(t.amount_cents for t in txs if t.kind == "out")
    fixed_total = sum(f.amount_cents for f in fixed)
    free = income - fixed_total - spent
    if first <= today < nxt:
        days_left = (nxt - today).days
    else:
        days_left = 0

    by_cat: Counter[str] = Counter()
    for t in txs:
        if t.kind == "out":
            by_cat[t.category or "Outros"] += t.amount_cents

    return {
        "month": f"{y:04d}-{m:02d}",
        "income": income,
        "spent": spent,
        "fixed_total": fixed_total,
        "free": free,
        "days_left": days_left,
        "per_day": free // days_left if days_left and free > 0 else 0,
        "by_category": [{"category": k, "cents": v} for k, v in by_cat.most_common()],
        "transactions": [t.model_dump(mode="json") for t in txs],
        "fixed": [f.model_dump() for f in fixed],
    }
