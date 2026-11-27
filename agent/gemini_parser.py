import json
from datetime import date
from typing import Optional

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None

from . import config
from .models import GeminiVoiceResponse, VoiceAction, ExtractedTask, TaskItem


class GeminiParser:
    def __init__(self):
        if not config.GEMINI_API_KEY:
            self.client = None
        else:
            self.client = genai.Client(api_key=config.GEMINI_API_KEY)

    def _build_context(self, current_tasks: list[TaskItem] | None) -> str:
        today = date.today()
        from datetime import timedelta
        tomorrow = today + timedelta(days=1)
        weekday_pt = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"][today.weekday()]
        tomorrow_pt = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"][tomorrow.weekday()]

        tasks_list = "Nenhuma tarefa cadastrada no momento."
        if current_tasks:
            lines = []
            for t in current_tasks:
                parent_str = f" [Pai ID: {t.parent_id}]" if t.parent_id else ""
                time_str = f", Horário: {t.time}" if t.time else ""
                day_str = f", Data: {t.day}" if t.day else ", Sem data"
                blocks_str = f", Blocos: {t.completed_blocks}/{t.planned_blocks}" if t.kind == "focus" else ""
                lines.append(f"- ID {t.id}: \"{t.title}\" (Tipo: {t.kind}{parent_str}{day_str}{time_str}{blocks_str}, Status: {t.status})")
            tasks_list = "\n".join(lines)

        return f"""
Você é o assistente pessoal de produtividade e agenda do usuário no BentoFocus.
Ele usa o BentoFocus para gerenciar blocos de foco e afazeres da vida.

Hoje é {today.isoformat()} ({weekday_pt}).
Amanhã é {tomorrow.isoformat()} ({tomorrow_pt}).

LISTA DE TAREFAS / ESTUDOS / AFAZERES ATUAIS:
{tasks_list}

O usuário tem DOIS MUNDOS:
1. FOCO & ESTUDO ('focus'):
   - Tópico Pai (pasta/tema amplo, ex: Kubernetes, Docker, Redes): NÃO TEM DATA. Serve para agrupar.
   - SUBTÓPICOS de estudo (ex: "CoreDNS & Services", "CNI & Redes", "Prometheus"): SÃO AS UNIDADES PRÁTICAS E TÊM DATA!
     * Podem estar sem data (guardados na pasta/backlog).
     * Podem ser agendados para HOJE ({today.isoformat()}).
     * Podem ser agendados para AMANHÃ ({tomorrow.isoformat()}) ou outra data futura!
     * Como o estudo é contínuo e não é simplesmente "feito e acabou", os subtópicos têm data justamente para planejar QUANDO ele vai estudar aquele tópico.
     * O usuário frequentemente faz planejamento noturno do dia seguinte: se ele disser "amanhã estudo esse tópico", "amanhã quero estudar X", "bota X pra amanhã", "coloca CNI pra amanhã":
       -> Se o tópico/subtópico já existe na lista, use 'set_date' com target_task_id e target_date='{tomorrow.isoformat()}'. NUNCA use 'create' para um estudo que já existe!
       -> Se for um novo subtópico citado, use 'create' com kind='focus', parent_topic=(se citado) e due_date='{tomorrow.isoformat()}'.

2. AFAZERES ('todo'): Coisas práticas do mundo real, burocracias, compras, consultas e compromissos com hora (ex: "ir ao dentista", "médico", "comprar passagem").
   - NÃO USAM BLOCOS DE FOCO.
   - Podem ter data (YYYY-MM-DD) ou serem sem data.
   - Podem ter horário fixo (time: HH:MM, ex: '15:00').

REGRAS CRÍTICAS PARA ESCOLHER A AÇÃO:

1. 'set_date': Agendar, alterar data ou remover data de QUALQUER estudo existente ou afazer existente:
   - Ex: "amanhã quero estudar CoreDNS", "bota CNI pra amanhã", "amanhã estudo esse tópico", "passa o dentista pro dia 5", "tira a data de X" (target_date='clear').
   - Encontre a tarefa correspondente na lista e aponte target_task_id. Calcule target_date (YYYY-MM-DD).

2. 'pull_today': Puxar um estudo ou afazer para HOJE ({today.isoformat()}):
   - Ex: "puxa CNI pra hoje", "vou estudar redes hoje", "coloca X no foco de hoje".
   - target_task_id do item.

3. 'create': Criar NOVO item (APENAS se ele NÃO existir na lista acima):
   - Se for estudo novo: kind='focus', title=nome, parent_topic=pasta pai (se citada), due_date=(data se citada, ex: amanhã ou hoje).
   - Se for afazer novo: kind='todo', time=HH:MM (se tiver hora), due_date=(data se citada).

4. 'progress': Registrar mais um bloco de foco feito:
   - Ex: "fiz mais um bloco de CNI", "mais um bloco de foco".
   - target_task_id do item.

5. 'postpone': Adiar tarefa existente em X dias:
   - target_task_id e postpone_days.

6. 'complete': Marcar como concluído:
   - Ex: "já fui no dentista", "conclui a revisão de PR", "terminei X".
   - target_task_id do item.

7. 'delete': Apagar ou cancelar tarefa:
   - Ex: "apaga a tarefa X", "cancela a consulta".
   - target_task_id do item.

Resposta ('reply_summary'):
- Confirmação direta, concisa e informal em português de parceiro técnico (ex: "Agendei CNI para amanhã (30/10) dentro de Kubernetes.").
"""

    def parse_text(self, text: str, current_tasks: list[TaskItem] | None = None) -> GeminiVoiceResponse:
        if not self.client:
            return GeminiVoiceResponse(
                actions=[VoiceAction(action_type="create", task=ExtractedTask(title=text.strip(), planned_blocks=1))],
                reply_summary="Anotado!",
                notes=""
            )

        prompt = f"Mensagem do usuário: \"{text}\"\nIdentifique as ações a serem tomadas."
        response = self.client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=[prompt],
            config=types.GenerateContentConfig(
                system_instruction=self._build_context(current_tasks),
                response_mime_type="application/json",
                response_schema=GeminiVoiceResponse,
                temperature=0.1,
            ),
        )
        return GeminiVoiceResponse.model_validate_json(response.text)

    def parse_audio(self, audio_bytes: bytes, current_tasks: list[TaskItem] | None = None, mime_type: str = "audio/ogg") -> GeminiVoiceResponse:
        if not self.client:
            raise RuntimeError("GEMINI_API_KEY não configurada no .env!")

        audio_part = types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)
        prompt = "Escute o áudio de voz do usuário e identifique as ações a serem tomadas com base nas tarefas dele."

        response = self.client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=[audio_part, prompt],
            config=types.GenerateContentConfig(
                system_instruction=self._build_context(current_tasks),
                response_mime_type="application/json",
                response_schema=GeminiVoiceResponse,
                temperature=0.1,
            ),
        )
        return GeminiVoiceResponse.model_validate_json(response.text)
