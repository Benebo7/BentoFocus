from datetime import datetime
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
