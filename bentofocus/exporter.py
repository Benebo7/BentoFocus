"""Resumo diário e exportação em JSON / Markdown para um dia lógico."""

from datetime import date, datetime

from sqlmodel import select

from . import stats
from .db import logical_day, session
from .focus import focus
from .models import FocusSession, HabitLog, WindowSpan

_SESSION_FIELDS = (
    "started_at",
    "ended_at",
    "focus_min",
    "planned_min",
    "status",
    "intention",
)

_STATUS_LABELS = {
    "done": "completo",
    "manual": "sem timer",
    "cancelled": "parado antes",
    "running": "em andamento",
}

_HABIT_LABELS = {
    "leitura": "Leitura",
    "academia": "Academia",
    "faculdade": "Faculdade",
}

_WEEKDAYS = (
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
)


def _fmt_min(minutes: float) -> str:
    m = round(minutes or 0)
    if m < 60:
        return f"{m}m"
    return f"{m // 60}h {m % 60:02d}m"


def _format_session_line(item: dict) -> str:
    start_hm = datetime.fromisoformat(item["started_at"]).strftime("%H:%M")
    end_hm = (
        datetime.fromisoformat(item["ended_at"]).strftime("%H:%M")
        if item["ended_at"]
        else "em andamento"
    )
    intention = " ".join((item["intention"] or "").split()) or "sem intenção definida"
    focus_str = _fmt_min(item["focus_min"])
    duration = focus_str if item["status"] == "manual" else f"{focus_str}/{item['planned_min']}m"
    status_label = _STATUS_LABELS.get(item["status"], item["status"])
    return f"- **{start_hm}–{end_hm}** · {intention} ({duration}, {status_label})"


def _format_markdown(
    target_day: date,
    focus_minutes: int,
    blocks_count: int,
    sessions: list[dict],
    habits: list[str],
    pc: dict[str, int],
    top_distractions: list[dict],
) -> str:
    weekday = _WEEKDAYS[target_day.weekday()]
    block_word = "bloco" if blocks_count == 1 else "blocos"

    lines = [
        f"# BentoFocus — {target_day.isoformat()} ({weekday})",
        "",
        "## Foco",
        f"- **Total:** {_fmt_min(focus_minutes)} em {blocks_count} {block_word}",
    ]
    if sessions:
        lines.extend(_format_session_line(s) for s in sessions)
    else:
        lines.append("- Nenhum bloco registrado neste dia.")

    lines.extend(["", "## Hábitos"])
    if habits:
        lines.extend(f"- [x] {_HABIT_LABELS.get(h, h.capitalize())}" for h in habits)
    else:
        lines.append("- Nenhum hábito registrado.")

    lines.extend(["", "## No PC"])
    if pc["total"] > 0:
        lines.extend(
            [
                f"- **Total:** {_fmt_min(pc['total'])}",
                f"- **Ativo:** {_fmt_min(pc['active'])}",
                f"- **Distração:** {_fmt_min(pc['distraction'])}",
                f"- **Distração barata:** {_fmt_min(pc['cheap'])}",
                f"- **Inativo:** {_fmt_min(pc['idle'])}",
            ]
        )
    else:
        lines.extend(
            [
                f"- **Total:** {_fmt_min(0)}",
                "- Sem atividade registrada no PC.",
            ]
        )

    lines.extend(["", "### Principais distrações"])
    if top_distractions:
        for d in top_distractions:
            cheap_tag = " (barata)" if d["cheap"] else ""
            lines.append(f"- **{d['label']}**: {_fmt_min(d['min'])}{cheap_tag}")
    else:
        lines.append("- Nenhuma distração registrada.")

    return "\n".join(lines) + "\n"


def daily_summary(day: date | None = None) -> dict:
    """Agrega foco, hábitos, uso de PC e distrações de um dia lógico."""
    target_day = day or logical_day()
    day_start, day_end = stats._day_bounds(target_day)

    with session() as db:
        sessions = db.exec(
            select(FocusSession)
            .where(FocusSession.day == target_day)
            .order_by(FocusSession.started_at, FocusSession.id)
        ).all()
        habit_rows = db.exec(
            select(HabitLog).where(HabitLog.day == target_day)
        ).all()
        spans = db.exec(
            select(WindowSpan).where(WindowSpan.end > day_start, WindowSpan.start < day_end)
        ).all()

    live = focus.current
    if live is not None and live.day == target_day:
        sessions = sorted(
            [s for s in sessions if s.id != live.id] + [live],
            key=lambda s: (s.started_at, s.id or 0),
        )

    focus_minutes = round(sum(stats._focus_s(s) for s in sessions) / 60)
    blocks_count = sum(1 for s in sessions if stats._focus_s(s) >= 60)
    session_items = [
        {k: full[k] for k in _SESSION_FIELDS}
        for full in (stats._session_dict(s) for s in sessions)
    ]

    habits = sorted({h.habit for h in habit_rows})

    pc_raw, distractions_raw = stats._pc_today(spans, day_start, day_end)
    pc = {
        bucket: round(pc_raw.get(bucket, 0) / 60)
        for bucket in ("active", "distraction", "cheap", "idle")
    }
    pc["total"] = sum(pc.values())

    top_distractions = []
    for (label, cheap), secs in sorted(
        distractions_raw.items(),
        key=lambda item: (-item[1], item[0][0], item[0][1]),
    ):
        mins = round(secs / 60)
        if mins >= 1:
            top_distractions.append({"label": label, "min": mins, "cheap": cheap})

    markdown = _format_markdown(
        target_day=target_day,
        focus_minutes=focus_minutes,
        blocks_count=blocks_count,
        sessions=session_items,
        habits=habits,
        pc=pc,
        top_distractions=top_distractions,
    )

    return {
        "day": target_day.isoformat(),
        "focus_minutes": focus_minutes,
        "blocks_count": blocks_count,
        "sessions": session_items,
        "habits": habits,
        "pc": pc,
        "top_distractions": top_distractions,
        "markdown": markdown,
    }
