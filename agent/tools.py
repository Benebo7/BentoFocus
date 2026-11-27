"""Conjunto de Ferramentas (Tools) disponíveis para o Agente Autônomo BentoFocus.

Permite ao Gemini consultar e manipular:
- BentoFocus Tasks (Foco, Subtópicos, Afazeres, Agendamento em lote ou individual)
- Roteiro do Dia (Anotação em texto puro totalmente separada das tarefas)
- GitHub (repositório alvo configurado em GITHUB_TARGET_REPO: listar PRs recentes, inspecionar detalhes, reviews, commits e comentários)
- Diretivas/Memória (instruções salvas para executar em datas futuras ou no digest)
"""

import logging
import re
from datetime import date, timedelta
from typing import Any, Optional

from . import config, db
from .github_checker import GitHubChecker

logger = logging.getLogger("agent.tools")


def _parse_day(day_str: Optional[str]) -> Optional[str]:
    """Converte expressões naturais ('hoje', 'amanha', 'sexta', '06/11', 'dia 10', 'clear')
    para 'YYYY-MM-DD' ou 'clear', sempre usando o fuso America/Sao_Paulo."""
    if day_str is None:
        return None
    s = str(day_str).strip().lower()
    if not s or s in ("clear", "sem data", "none", "null", "backlog", "remover", "tirar", "limpar"):
        return "clear"

    today = config.today_brt()
    if s in ("hoje", "today"):
        return today.isoformat()
    if s in ("amanha", "amanhã", "tomorrow"):
        return (today + timedelta(days=1)).isoformat()
    if s in ("depois de amanha", "depois de amanhã"):
        return (today + timedelta(days=2)).isoformat()

    weekdays = {
        "segunda": 0, "segunda-feira": 0,
        "terca": 1, "terça": 1, "terça-feira": 1, "terca-feira": 1,
        "quarta": 2, "quarta-feira": 2,
        "quinta": 3, "quinta-feira": 3,
        "sexta": 4, "sexta-feira": 4,
        "sabado": 5, "sábado": 5,
        "domingo": 6,
    }
    if s in weekdays:
        target_wd = weekdays[s]
        days_ahead = (target_wd - today.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        return (today + timedelta(days=days_ahead)).isoformat()

    m_iso = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
    if m_iso:
        y, m, d = map(int, m_iso.groups())
        return date(y, m, d).isoformat()

    m_dmy = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$", s)
    if m_dmy:
        d, m, y = map(int, m_dmy.groups())
        return date(y, m, d).isoformat()

    m_dm = re.match(r"^(\d{1,2})[/-](\d{1,2})$", s)
    if m_dm:
        d, m = map(int, m_dm.groups())
        return date(today.year, m, d).isoformat()

    m_day = re.match(r"^(?:dia\s+)?(\d{1,2})$", s)
    if m_day:
        d = int(m_day.group(1))
        try:
            candidate = date(today.year, today.month, d)
            if candidate < today:
                if today.month == 12:
                    candidate = date(today.year + 1, 1, d)
                else:
                    candidate = date(today.year, today.month + 1, d)
            return candidate.isoformat()
        except ValueError:
            pass

    return s


# --- 1. Ferramentas de Tarefas / Agenda (BentoFocus) ---


def list_tasks(status: Optional[str] = None, day: Optional[str] = None, kind: Optional[str] = None) -> list[dict[str, Any]]:
    """Lista as tarefas, tópicos de foco ou afazeres cadastrados no BentoFocus.

    Args:
        status: Filtrar por status ('inbox', 'today', 'done', 'cancelled') ou None para todos.
        day: Filtrar por data específica ('YYYY-MM-DD', 'hoje', 'amanha') ou None.
        kind: 'focus' (estudos/tópicos técnicos com blocos) ou 'todo' (afazeres/compromissos).
    """
    parsed_day = _parse_day(day) if day else None
    if parsed_day == "clear":
        parsed_day = None
    tasks = db.get_tasks(status=status, day=parsed_day, kind=kind)
    return [
        {
            "id": t.id,
            "title": t.title,
            "kind": t.kind,
            "parent_id": t.parent_id,
            "day": t.day,
            "time": t.time,
            "planned_blocks": t.planned_blocks,
            "completed_blocks": t.completed_blocks,
            "status": t.status,
        }
        for t in tasks
    ]


def create_task(
    title: str,
    kind: str = "focus",
    parent_topic: Optional[str] = None,
    day: Optional[str] = None,
    time: Optional[str] = None,
    planned_blocks: int = 1,
) -> dict[str, Any]:
    """Cria uma NOVA tarefa de foco, subtópico de estudo ou afazer (APENAS se não existir na lista atual!).

    Args:
        title: Nome do tópico, subtópico ou afazer.
        kind: 'focus' para estudos técnicos/código/projetos; 'todo' para afazeres pontuais da vida/compras/burocracias.
        parent_topic: Nome da pasta/tema pai de estudo (ex: 'Kubernetes', 'Service Monitoring'). Se informado, cria dentro da pasta.
        day: Data ('YYYY-MM-DD', 'hoje', 'amanha', '06/11') se tiver dia definido, ou None para sem data (backlog).
        time: Horário HH:MM apenas se for compromisso com hora marcada (ex: '14:30'); senão None.
        planned_blocks: Quantidade de blocos de 25m previstos (padrão 1, apenas para kind='focus').
    """
    today_str = config.today_brt().isoformat()
    parsed_day = _parse_day(day) if day else None
    if parsed_day == "clear":
        parsed_day = None

    parent_id = None
    if parent_topic:
        parent_id = db.find_or_create_parent_topic(parent_topic)

    status = "today" if parsed_day == today_str else "inbox"
    task = db.create_task(
        title=title,
        parent_id=parent_id,
        kind=kind,
        time=time,
        planned_blocks=planned_blocks if kind == "focus" else 1,
        day=parsed_day,
        status=status,
        source="telegram",
    )
    return {
        "success": True,
        "task_id": task.id,
        "title": task.title,
        "kind": task.kind,
        "parent_id": task.parent_id,
        "day": task.day,
        "status": task.status,
    }


def schedule_tasks(task_ids: list[int], day: str, time: Optional[str] = None) -> dict[str, Any]:
    """Agenda, altera a data ou remove a data de UMA ou VÁRIAS tarefas/estudos existentes de uma só vez.

    Use sempre esta função quando o usuário pedir para colocar um ou mais itens para hoje, amanhã, outra data ou tirar a data.

    Args:
        task_ids: Lista de IDs numéricos das tarefas (ex: [25, 38, 39] ou [44]).
        day: Nova data ('YYYY-MM-DD', 'hoje', 'amanha', 'sexta', '06/11') ou 'clear' para remover a data (voltar pro backlog).
        time: Horário 'HH:MM' opcional, ou None.
    """
    parsed_day = _parse_day(day) or "clear"
    updated = []
    for tid in task_ids:
        ok = db.set_task_date(int(tid), day=parsed_day, time=time)
        updated.append({"task_id": int(tid), "success": ok, "new_day": None if parsed_day == "clear" else parsed_day})
    return {"updated": updated}


def update_task_schedule(task_id: int, day: Optional[str] = None, time: Optional[str] = None) -> dict[str, Any]:
    """Atualiza a data ou horário de uma única tarefa/estudo existente.

    Args:
        task_id: ID numérico da tarefa existente.
        day: Nova data ('YYYY-MM-DD', 'hoje', 'amanha', '06/11') ou 'clear' para remover a data.
        time: Horário 'HH:MM' se quiser definir/alterar hora, ou None.
    """
    parsed_day = _parse_day(day) or "clear"
    ok = db.set_task_date(int(task_id), day=parsed_day, time=time)
    return {"success": ok, "task_id": int(task_id), "new_day": None if parsed_day == "clear" else parsed_day, "new_time": time}


def pull_task_to_today(task_id: int) -> dict[str, Any]:
    """Puxa uma tarefa ou subtópico de foco diretamente para o dia de Hoje."""
    ok = db.pull_to_today(int(task_id))
    return {"success": ok, "task_id": int(task_id), "day": config.today_brt().isoformat(), "status": "today"}


def complete_tasks(task_ids: list[int]) -> dict[str, Any]:
    """Marca uma ou várias tarefas de foco ou afazeres como concluídas de uma só vez.

    Args:
        task_ids: Lista de IDs numéricos das tarefas a concluir (ex: [35, 44]).
    """
    results = []
    for tid in task_ids:
        ok = db.complete_task(int(tid))
        results.append({"task_id": int(tid), "success": ok})
    return {"completed": results}


def complete_task(task_id: int) -> dict[str, Any]:
    """Marca uma tarefa de foco ou afazer como concluído."""
    ok = db.complete_task(int(task_id))
    return {"success": ok, "task_id": int(task_id), "status": "done"}


def record_focus_progress(task_id: int) -> dict[str, Any]:
    """Registra +1 bloco de foco (25 min) concluído numa tarefa de foco."""
    ok = db.record_progress(int(task_id))
    return {"success": ok, "task_id": int(task_id), "added_block": 1}


def delete_tasks(task_ids: list[int]) -> dict[str, Any]:
    """Apaga uma ou várias tarefas do BentoFocus de uma só vez.

    Args:
        task_ids: Lista de IDs numéricos das tarefas para apagar.
    """
    results = []
    for tid in task_ids:
        ok = db.delete_task(int(tid))
        results.append({"task_id": int(tid), "success": ok})
    return {"deleted": results}


def delete_task(task_id: int) -> dict[str, Any]:
    """Apaga uma tarefa do BentoFocus."""
    ok = db.delete_task(int(task_id))
    return {"success": ok, "task_id": int(task_id)}


# --- 2. Ferramentas de Roteiro do Dia (Texto Puro Livre) ---


def get_day_plan(day: Optional[str] = None) -> dict[str, Any]:
    """Lê o roteiro/anotação em texto puro de um dia específico.

    Args:
        day: 'hoje', 'amanha' ou 'YYYY-MM-DD'. Padrão: 'hoje'.
    """
    parsed = _parse_day(day) if day else config.today_brt().isoformat()
    if not parsed or parsed == "clear":
        parsed = config.today_brt().isoformat()
    return db.get_day_plan(parsed)


def set_day_plan(content: str, day: Optional[str] = None) -> dict[str, Any]:
    """Salva o Roteiro do Dia como um TEXTO PURO E LIVRE, exatamente como uma anotação corrida.

    REGRAS OBRIGATÓRIAS:
    - Escreva texto corrido/natural sem forçar estrutura engessada (não precisa dividir em "Manhã: / Tarde: / Noite:" nem criar listas com horários se o usuário só falou naturalmente).
    - O Roteiro do Dia é 100% SEPARADO de Tarefas/Afazeres! NUNCA chame create_task para atividades do roteiro (como ir pra academia, ir pra faculdade, descansar ou estudar).

    Args:
        content: Texto puro e livre descrevendo o roteiro do dia.
        day: 'hoje', 'amanha' ou 'YYYY-MM-DD'. Padrão: 'hoje'.
    """
    parsed = _parse_day(day) if day else config.today_brt().isoformat()
    if not parsed or parsed == "clear":
        parsed = config.today_brt().isoformat()
    return db.set_day_plan(parsed, content.strip())


# --- 3. Ferramentas de GitHub (repositório alvo configurado em GITHUB_TARGET_REPO) ---


def github_list_recent_prs(limit: int = 5) -> list[dict[str, Any]]:
    """Lista os Pull Requests mais recentes abertos no repositório alvo por outros contribuidores."""
    gh = GitHubChecker()
    if not gh.is_configured():
        return [{"error": "GitHub não configurado."}]
    try:
        url = f"https://api.github.com/repos/{gh.target_repo}/pulls?state=open&sort=created&direction=desc&per_page={max(10, limit * 2)}"
        pulls = gh._fetch(url)
        out = []
        for p in pulls:
            author = p.get("user", {}).get("login", "")
            if author.lower() == gh.username.lower() or author.endswith("[bot]"):
                continue
            out.append({
                "number": p.get("number"),
                "title": p.get("title"),
                "author": author,
                "created_at": p.get("created_at"),
                "draft": bool(p.get("draft")),
                "html_url": p.get("html_url"),
            })
            if len(out) >= limit:
                break
        return out
    except Exception as e:
        logger.warning(f"Erro em github_list_recent_prs: {e}")
        return [{"error": str(e)}]


def github_inspect_pr(pr_number: int) -> dict[str, Any]:
    """Inspeciona em detalhes um Pull Request do repositório alvo: descrição, reviews, commits e comentários."""
    gh = GitHubChecker()
    if not gh.is_configured():
        return {"error": "GitHub não configurado."}
    try:
        pr = gh._fetch(f"https://api.github.com/repos/{gh.target_repo}/pulls/{pr_number}")
        commits = gh._fetch(f"https://api.github.com/repos/{gh.target_repo}/pulls/{pr_number}/commits")
        reviews = gh._fetch(f"https://api.github.com/repos/{gh.target_repo}/pulls/{pr_number}/reviews")
        comments = gh._fetch(f"https://api.github.com/repos/{gh.target_repo}/pulls/{pr_number}/comments")

        recent_commits = [
            {
                "message": c.get("commit", {}).get("message", "").split("\n")[0],
                "author": c.get("commit", {}).get("author", {}).get("name"),
                "date": c.get("commit", {}).get("author", {}).get("date"),
            }
            for c in (commits or [])[-3:]
        ]

        recent_reviews = [
            {
                "reviewer": r.get("user", {}).get("login"),
                "state": r.get("state"),
                "body": (r.get("body") or "")[:120],
                "submitted_at": r.get("submitted_at"),
            }
            for r in (reviews or [])[-5:]
        ]

        recent_comments = [
            {
                "author": cm.get("user", {}).get("login"),
                "body": (cm.get("body") or "")[:120],
                "created_at": cm.get("created_at"),
            }
            for cm in (comments or [])[-5:]
        ]

        return {
            "number": pr.get("number"),
            "title": pr.get("title"),
            "author": pr.get("user", {}).get("login"),
            "state": pr.get("state"),
            "draft": bool(pr.get("draft")),
            "created_at": pr.get("created_at"),
            "updated_at": pr.get("updated_at"),
            "total_commits": len(commits) if isinstance(commits, list) else 0,
            "recent_commits": recent_commits,
            "reviews": recent_reviews,
            "review_comments": recent_comments,
        }
    except Exception as e:
        logger.warning(f"Erro em github_inspect_pr({pr_number}): {e}")
        return {"error": str(e)}


def github_list_recent_issues(limit: int = 5) -> list[dict[str, Any]]:
    """Lista as issues mais recentes abertas no repositório alvo pela comunidade."""
    gh = GitHubChecker()
    if not gh.is_configured():
        return [{"error": "GitHub não configurado."}]
    try:
        url = f"https://api.github.com/repos/{gh.target_repo}/issues?state=open&sort=created&direction=desc&per_page={max(10, limit * 2)}"
        issues = gh._fetch(url)
        out = []
        for i in issues:
            if "pull_request" in i:
                continue
            author = i.get("user", {}).get("login", "")
            if author.lower() == gh.username.lower():
                continue
            labels = [l.get("name") for l in i.get("labels", []) if l.get("name")]
            out.append({
                "number": i.get("number"),
                "title": i.get("title"),
                "author": author,
                "labels": labels,
                "created_at": i.get("created_at"),
                "html_url": i.get("html_url"),
            })
            if len(out) >= limit:
                break
        return out
    except Exception as e:
        logger.warning(f"Erro em github_list_recent_issues: {e}")
        return [{"error": str(e)}]


# --- 4. Ferramentas de Memória & Diretivas Futuras ---


def save_future_directive(instruction: str, target_date: Optional[str] = None) -> dict[str, Any]:
    """Salva uma instrução ou diretiva persistente que o agente deve executar no futuro (ex: no digest de amanhã ou em data X)."""
    parsed_date = _parse_day(target_date) if target_date else None
    if parsed_date == "clear":
        parsed_date = None
    dir_id = db.add_directive(instruction=instruction, target_date=parsed_date)
    return {"success": True, "directive_id": dir_id, "instruction": instruction, "target_date": parsed_date}


def list_active_directives() -> list[dict[str, Any]]:
    """Lista as instruções ou diretivas de longo prazo salvas e ainda ativas."""
    return db.get_active_directives()


# Catálogo com todas as tools prontas para o Gemini
AGENT_TOOLS = [
    schedule_tasks,
    update_task_schedule,
    pull_task_to_today,
    create_task,
    complete_tasks,
    complete_task,
    delete_tasks,
    delete_task,
    record_focus_progress,
    list_tasks,
    get_day_plan,
    set_day_plan,
    github_list_recent_prs,
    github_inspect_pr,
    github_list_recent_issues,
    save_future_directive,
    list_active_directives,
]
