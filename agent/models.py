from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel, Field


class TaskItem(BaseModel):
    id: Optional[int] = None
    parent_id: Optional[int] = None  # Para subtópicos de estudo/foco
    title: str
    description: str = ""
    kind: str = "focus"  # focus (estudo/código com blocos) | todo (afazer/compromisso sem timer)
    time: Optional[str] = None  # HH:MM
    reminder_minutes: int = 15
    reminder_sent: bool = False
    planned_blocks: int = Field(default=1, ge=1, le=8)
    completed_blocks: int = Field(default=0, ge=0)
    status: str = "inbox"  # inbox | today | done | cancelled | missed
    day: Optional[str] = None  # YYYY-MM-DD
    source: str = "telegram"
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    completed_at: Optional[str] = None


class ExtractedTask(BaseModel):
    title: str = Field(description="Título curto e claro da tarefa, subtópico ou afazer")
    parent_topic: Optional[str] = Field(default=None, description="Nome do tópico pai se for subtópico de estudo (ex: 'Kubernetes' para 'CNI & Redes')")
    description: str = Field(default="", description="Detalhes adicionais ou contexto")
    kind: str = Field(default="focus", description="'focus' para estudo, código, projetos e deep work; 'todo' para afazeres da vida, compras, burocracias, consultas e compromissos.")
    time: Optional[str] = Field(default=None, description="Horário fixo HH:MM se for compromisso com hora marcada; senão null.")
    reminder_minutes: int = Field(default=15, description="Minutos de antecedência do lembrete (padrão 15).")
    planned_blocks: int = Field(default=1, description="Estimativa de blocos de 25m (1 a 4) - USAR SOMENTE SE kind=='focus'. Se for todo use 1.")
    due_date: Optional[str] = Field(default=None, description="Data YYYY-MM-DD se dita expressamente; se for sem data definida, use null.")
    priority: str = Field(default="normal", description="high, normal, low")


class VoiceAction(BaseModel):
    action_type: str = Field(
        description="Ação: 'create' (criar foco ou afazer), 'set_date' (definir/alterar/remover data de tarefa existente), 'pull_today' (puxar estudo/afazer para Hoje), 'postpone' (adiar), 'progress' (somar bloco feito), 'complete' (marcar feito), 'delete' (apagar)."
    )
    # Para 'create':
    task: Optional[ExtractedTask] = None
    # Para ações em tarefas existentes:
    target_task_id: Optional[int] = Field(default=None, description="ID da tarefa da lista de contexto que o usuário quer alterar.")
    target_date: Optional[str] = Field(default=None, description="Nova data YYYY-MM-DD (ou 'clear' para remover data) na ação set_date.")
    postpone_days: int = Field(default=1, description="Quantidade de dias para adiar (padrão 1).")


class GeminiVoiceResponse(BaseModel):
    actions: list[VoiceAction] = Field(description="Lista de ações extraídas do áudio ou texto.")
    reply_summary: str = Field(description="Mensagem curta, direta e informal confirmando as ações (ex: 'Agendei o dentista para 05/11 e adicionei CNI no estudo de Kubernetes').")
    notes: str = Field(default="", description="Pensamentos ou notas soltas que não são tarefas práticas.")
