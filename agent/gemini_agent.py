import logging
from datetime import timedelta
from typing import Optional

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None

from . import config, db
from .tools import AGENT_TOOLS

logger = logging.getLogger("agent.gemini")

WEEKDAYS_PT = [
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
]


class GeminiAgent:
    """Agente Autônomo com Contexto em Tempo Real, Suporte a Lote e Tools via google-genai."""

    def __init__(self):
        if not config.GEMINI_API_KEY:
            self.client = None
        else:
            self.client = genai.Client(api_key=config.GEMINI_API_KEY)

    def _build_tasks_snapshot(self) -> str:
        today_str = config.today_brt().isoformat()
        tomorrow_str = (config.today_brt() + timedelta(days=1)).isoformat()

        all_tasks = [t for t in db.get_tasks() if t.status not in ("done", "cancelled")]
        if not all_tasks:
            return "Nenhuma tarefa pendente no momento."

        focus_tasks = [t for t in all_tasks if t.kind == "focus"]
        todo_tasks = [t for t in all_tasks if t.kind in ("todo", "event")]

        parent_map = {t.id: t.title for t in focus_tasks if t.parent_id is None}
        parents_with_children = {t.parent_id for t in focus_tasks if t.parent_id is not None}

        def _fmt_day(d: Optional[str], status: str) -> str:
            if d == today_str or (status == "today" and not d):
                return f"HOJE ({today_str})"
            if d == tomorrow_str:
                return f"AMANHÃ ({tomorrow_str})"
            if d:
                return d
            return "Sem data (Backlog)"

        lines = ["=== ESTUDOS & FOCO ATIVOS (kind='focus') ==="]
        for t in sorted(focus_tasks, key=lambda x: (x.parent_id or 0, x.id or 0)):
            if t.parent_id:
                folder = f"[Subtópico de: {parent_map.get(t.parent_id, '?')}] "
                lines.append(f"- ID {t.id}: \"{t.title}\" {folder}| Data: {_fmt_day(t.day, t.status)}")
            elif t.id in parents_with_children:
                lines.append(f"- ID {t.id}: \"{t.title}\" [PASTA COM SUBTÓPICOS - só os subtópicos dela têm data]")
            else:
                lines.append(f"- ID {t.id}: \"{t.title}\" [Tópico sem subtópicos - PODE ter data] | Data: {_fmt_day(t.day, t.status)}")

        lines.append("\n=== AFAZERES & COMPROMISSOS ATIVOS (kind='todo') ===")
        for t in sorted(todo_tasks, key=lambda x: (x.day or "9999", x.time or "99:99", x.id or 0)):
            time_str = f" às {t.time}" if t.time else ""
            lines.append(f"- ID {t.id}: \"{t.title}\" | Data: {_fmt_day(t.day, t.status)}{time_str}")

        return "\n".join(lines)

    def _build_system_instruction(self) -> str:
        today = config.today_brt()
        tomorrow = today + timedelta(days=1)

        # Calendário dos próximos 7 dias para precisão absoluta de datas
        cal_lines = []
        for i in range(7):
            d = today + timedelta(days=i)
            wd = WEEKDAYS_PT[d.weekday()]
            label = " (HOJE)" if i == 0 else (" (AMANHÃ)" if i == 1 else "")
            cal_lines.append(f"  • {d.isoformat()} = {wd}{label}")
        calendar_str = "\n".join(cal_lines)

        tasks_snapshot = self._build_tasks_snapshot()
        plan_today = (db.get_day_plan(today.isoformat()).get("content") or "").strip() or "(vazio)"
        plan_tomorrow = (db.get_day_plan(tomorrow.isoformat()).get("content") or "").strip() or "(vazio)"

        return f"""Você é o assistente pessoal do usuário no BentoFocus.
Fuso horário atual: America/Sao_Paulo.

CALENDÁRIO DE REFERÊNCIA (use estas datas exatas):
{calendar_str}

ROTEIRO EM TEXTO PURO ATUAL:
- Roteiro de Hoje ({today.isoformat()}): {plan_today}
- Roteiro de Amanhã ({tomorrow.isoformat()}): {plan_tomorrow}

ESTADO ATUAL DO BANCO DE DADOS (IDs reais para você manipular):
{tasks_snapshot}

=====================================================================
ENTENDA A SEPARAÇÃO RÍGIDA DOS 3 MUNDOS DO USUÁRIO (REGRA MAIS IMPORTANTE!):

1. ROTEIRO DO DIA (Ferramenta `set_day_plan` / `get_day_plan`):
   - É APENAS um bloco de notas em TEXTO PURO E CORRIDO para o usuário deixar registrado o que pretende fazer no dia (ex: "De manhã vou ficar estudando em casa, à tarde vou descansar e ir pra academia, e à noite vou pra faculdade").
   - NÃO force estrutura! Não precisa quebrar em "Manhã: / Tarde: / Noite:" nem inventar horários se ele não pediu. Escreva como um texto corrido, simples e natural.
   - TOTALMENTE SEPARADO DE TAREFAS: Quando o usuário estiver contando o roteiro/planejamento do dia dele (ex: ir pra academia, ir pra faculdade, descansar, estudar de manhã), salve APENAS no `set_day_plan`. É TERMINANTEMENTE PROIBIDO chamar `create_task` para criar afazeres de "ir pra academia", "aula na faculdade", "descansar", etc.!

2. FOCO & ESTUDO (`kind='focus'`):
   - Tópicos e subtópicos técnicos de estudo (ex: Kubernetes, Redes, Terraform, Subnet, OSI, Linux, AWS, Prometheus, Docker).
   - QUALQUER tópico que NÃO tenha subtópicos (e qualquer subtópico) PODE ter data normalmente (`hoje`, `amanha`, `YYYY-MM-DD`, ou `clear` para sem data).
   - Um tópico só deixa de ter data própria se ele tiver subtópicos dentro dele (nesse caso, a data vai nos subtópicos).

3. AFAZERES & COMPROMISSOS (`kind='todo'`):
   - Apenas coisas pontuais/específicas para não esquecer (ex: "Comprar mantimentos", "Consulta médica", "Resolver pendência no banco").

=====================================================================
COMO EXECUTAR OPERAÇÕES DE DATA E MÚLTIPLOS ITENS (LOTE):

1. MUDAR DATA / AGENDAR UM OU VÁRIOS ITENS EXISTENTES (`schedule_tasks`):
   - Sempre olhe primeiro a lista "ESTADO ATUAL DO BANCO DE DADOS" acima!
   - Se o tópico ou afazer JÁ EXISTE na lista acima, NUNCA use `create_task` (não duplique!).
   - Use `schedule_tasks(task_ids=[...], day='...')` passando os IDs existentes.
   - Exemplo 1 (vários para a mesma data): "bota Terraform, Subnet e OSI pra amanhã" -> chame `schedule_tasks(task_ids=[38, 39, 25], day='amanha')`.
   - Exemplo 2 (tirar de hoje / deixar sem data): "tira Service Mesh e revisão de linux de hoje" -> chame `schedule_tasks(task_ids=[50, 46], day='clear')`.
   - Exemplo 3 (datas diferentes na mesma fala): "bota a compra pra amanhã e o exame pra sexta" -> chame `schedule_tasks` duas vezes (uma para cada data) na mesma rodada!

2. CRIAR VÁRIOS ITENS NOVOS:
   - Se o usuário pedir para adicionar vários estudos ou afazeres que ainda NÃO existem na lista, chame `create_task` para cada um deles (passando `day='hoje'`, `'amanha'`, `'YYYY-MM-DD'` ou `None` se for sem data).

3. CONCLUIR OU APAGAR VÁRIOS ITENS:
   - Use `complete_tasks(task_ids=[...])` ou `delete_tasks(task_ids=[...])`.

4. RESPOSTA AO USUÁRIO:
   - Seja curto, direto e natural em português. Confirme exatamente o que foi alterado (com as datas certas).
"""

    def handle_message(self, text_or_prompt: str, audio_bytes: Optional[bytes] = None, mime_type: str = "audio/ogg") -> str:
        """Processa a mensagem (texto ou áudio) executando chamadas de ferramentas de forma autônoma."""
        if not self.client:
            return "❌ GEMINI_API_KEY não configurada no .env!"

        system_instruction = self._build_system_instruction()

        models_to_try = [config.GEMINI_MODEL]
        fallback = getattr(config, "GEMINI_FALLBACK_MODEL", "gemini-3.5-flash-lite")
        if fallback and fallback not in models_to_try:
            models_to_try.append(fallback)

        last_err = None
        for model_name in models_to_try:
            try:
                chat = self.client.chats.create(
                    model=model_name,
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        tools=AGENT_TOOLS,
                        temperature=0.1,
                    ),
                )

                if audio_bytes:
                    audio_part = types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)
                    prompt = (
                        text_or_prompt
                        or "Ouça com atenção o áudio do usuário. Identifique todas as tarefas, datas ou roteiro do dia mencionados e execute todas as ações necessárias usando as ferramentas."
                    )
                    response = chat.send_message([audio_part, prompt])
                else:
                    response = chat.send_message(text_or_prompt)

                return response.text or "✅ Feito!"
            except Exception as e:
                last_err = e
                logger.warning(f"Erro com modelo {model_name}: {e}. Tentando fallback se disponível...")

        logger.error(f"Falha em todos os modelos Gemini: {last_err}")
        return f"❌ Erro ao processar: {last_err}"
