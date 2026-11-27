from datetime import date, datetime

from pydantic import NaiveDatetime
from sqlmodel import Field, SQLModel, UniqueConstraint


class FocusSession(SQLModel, table=True):
    """Um bloco de foco (pomodoro). Só o tempo daqui conta como foco.

    Datas são locais e sem fuso (NaiveDatetime): app de uma máquina só, e o
    "dia" é o dia do relógio da parede."""

    id: int | None = Field(default=None, primary_key=True)
    day: date = Field(index=True)  # dia lógico (vira às 4h)
    started_at: NaiveDatetime
    planned_min: int
    ended_at: NaiveDatetime | None = None
    status: str = "running"  # running | done | cancelled | manual (registrado à mão, sem timer)
    intention: str = ""
    allow_youtube: bool = False
    task_id: int | None = Field(default=None, foreign_key="task.id")
    # Tempo efetivo (s), fechado ao terminar: o bloco inteiro, ou até onde
    # foi se parou antes. Sem descontos: parado no PC = estudando no caderno.
    focus_s: float = 0


class WindowSpan(SQLModel, table=True):
    """Intervalo contínuo numa mesma janela/categoria. Amostras iguais em
    sequência viram uma linha só."""

    id: int | None = Field(default=None, primary_key=True)
    start: NaiveDatetime = Field(index=True)
    end: NaiveDatetime
    wm_class: str = ""
    title: str = ""
    category: str  # active | distraction | idle
    label: str = ""  # nome da regra de bloqueio (YouTube, Jogos...)
    session_id: int | None = Field(default=None, foreign_key="focussession.id")


class HabitLog(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("day", "habit"),)

    id: int | None = Field(default=None, primary_key=True)
    day: date = Field(index=True)
    habit: str


class Transaction(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    day: date = Field(index=True)
    amount_cents: int  # sempre positivo; o sinal vem de `kind`
    kind: str  # in | out
    category: str = ""
    description: str = ""


class FixedExpense(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    amount_cents: int


class KV(SQLModel, table=True):
    """Pequeno estado persistente (ex.: valor original das notificações)."""

    key: str = Field(primary_key=True)
    value: str


class Task(SQLModel, table=True):
    """Uma tarefa de foco (estudo/código) ou afazer/compromisso (sem blocos de foco)."""

    id: int | None = Field(default=None, primary_key=True)
    parent_id: int | None = Field(default=None, foreign_key="task.id", index=True)  # Se for subtópico
    title: str
    description: str = ""
    kind: str = "focus"  # focus (estudo/código c/ blocos) | todo (afazer/compromisso s/ timer)
    time: str | None = None  # HH:MM se for compromisso com hora
    reminder_minutes: int = 15  # minutos de antecedência para alertar no Telegram
    reminder_sent: bool = False
    planned_blocks: int = 1  # estimativa de blocos de 25m (somente para kind="focus")
    completed_blocks: int = 0  # blocos de foco efetivamente concluídos
    status: str = "inbox"  # inbox | today | done | cancelled | missed
    day: date | None = Field(default=None, index=True)
    source: str = "manual"  # telegram | manual | github | email
    created_at: NaiveDatetime = Field(default_factory=datetime.now)
    completed_at: NaiveDatetime | None = None


class DayPlan(SQLModel, table=True):
    """Roteiro / anotação de texto livre do dia (macro-planejamento)."""

    day: date = Field(primary_key=True)
    content: str = ""
    updated_at: NaiveDatetime = Field(default_factory=datetime.now)



