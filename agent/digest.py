import logging
from datetime import date

from . import config, db
from .github_checker import GitHubChecker
from .email_checker import EmailChecker

logger = logging.getLogger("agent.digest")


def generate_morning_digest() -> str:
    """Gera o resumo matinal agregando tarefas do dia, diretivas ativas, novidades do repositório GitHub configurado e E-mails."""
    today = config.today_brt()
    focus_tasks = db.get_today_focus()
    todos_tasks = db.get_today_todos()
    github_data = GitHubChecker().get_summary()
    email_items = EmailChecker().get_summary()
    active_directives = db.get_active_directives(target_date=today.isoformat())
    day_plan_data = db.get_day_plan(today.isoformat())
    day_plan_txt = (day_plan_data.get("content") or "").strip() or "Nenhum roteiro macro anotado para hoje."

    events_txt = "\n".join([f"- 📋 {e.title}" + (f" às {e.time}" if e.time else "") for e in todos_tasks]) or "Nenhum afazer/compromisso para hoje."
    focus_txt = "\n".join([f"- [ID {f.id}] {f.title} ({f.completed_blocks}/{f.planned_blocks} blocos de 25m)" for f in focus_tasks]) or "Nenhum tópico de estudo puxado para hoje."
    repo_prs_txt = "\n".join([f"- {item}" for item in github_data.get("repo_prs", [])]) or "Nenhum PR recente de outros contribuidores."
    repo_issues_txt = "\n".join([f"- {item}" for item in github_data.get("repo_issues", [])]) or "Nenhuma issue nova recente."
    email_txt = "\n".join([f"- {item}" for item in email_items]) or "Nenhum e-mail não lido relevante."
    directives_txt = "\n".join([f"- [Diretiva #{d['id']}] {d['instruction']}" for d in active_directives]) or "Nenhuma diretiva personalizada para hoje."

    if config.GEMINI_API_KEY:
        try:
            from google import genai
            from .tools import AGENT_TOOLS
            client = genai.Client(api_key=config.GEMINI_API_KEY)
            prompt = f"""
Você é o assistente pessoal do usuário no BentoFocus.
Hoje é {today.strftime('%d/%m/%Y')}.

DADOS DA MANHÃ:

[ROTEIRO / PLANEJAMENTO MACRO DO DIA]
{day_plan_txt}

[DIRETIVAS / PEDIDOS ESPECIAIS PARA HOJE]
{directives_txt}

[COMPROMISSOS DO DIA]
{events_txt}

[TAREFAS DE FOCO & ESTUDO DE HOJE]
{focus_txt}

[NOVIDADES NO REPOSITÓRIO ALVO ({config.GITHUB_TARGET_REPO or 'não configurado'})]
Novos PRs da comunidade:
{repo_prs_txt}

Novas Issues da comunidade:
{repo_issues_txt}

[E-MAILS NÃO LIDOS]
{email_txt}

Instruções cruciais:
1. ROTEIRO MACRO DO DIA:
   - Se houver anotações no roteiro macro (ex: turnos do dia, manhã, tarde, noite), apresente logo na abertura como a visão geral do dia.
2. DIRETIVAS DO USUÁRIO:
   - Se houver diretivas/pedidos especiais para hoje (ex: checar se certo PR resolveu a review do bot, verificar algo no GitHub ou banco), USE as ferramentas (github_inspect_pr, etc.) para checar o status e relatar a resposta diretamente no digest! Se a diretiva pedir para criar uma tarefa no BentoFocus mediante certa condição, execute com a ferramenta correspondente.
3. FOCO NO REPOSITÓRIO ALVO (GITHUB):
   - O usuário quer saber o que há de novo no repositório {config.GITHUB_TARGET_REPO or 'configurado'} para saber se vale a pena revisar ou pegar para resolver.
   - Mostre os novos PRs abertos pela comunidade e novas issues. Destaque quem abriu e do que se trata.
   - NÃO invente notificações de PRs próprios ou de outros repositórios.
4. COMPROMISSOS & FOCO:
   - Apresente os afazeres com horário de hoje e os tópicos de estudo/foco que estão para hoje com seus IDs e blocos.
5. E-MAILS:
   - Se só tiver newsletters/promoções, resuma em uma linha curta e divertida. Só dê destaque se for algo pessoal, faculdade ou processo seletivo real.
6. Formatação:
   - Markdown limpo para Telegram. Evite underscores soltos dentro de palavras ou nomes de branches para não quebrar a formatação.
   - Tom direto, descontraído e de parceiro técnico.
"""
            chat = client.chats.create(
                model=config.GEMINI_MODEL,
                config={
                    "tools": AGENT_TOOLS,
                    "temperature": 0.2,
                },
            )
            res = chat.send_message(prompt)
            return res.text
        except Exception as e:
            logger.warning(f"Erro ao chamar Gemini no digest: {e}")

    # Fallback sem Gemini
    total_blocks = sum(t.planned_blocks for t in focus_tasks)
    lines = [
        f"🌅 *Bom dia!* — {today.strftime('%d/%m/%Y')}\n",
    ]
    if day_plan_txt and "Nenhum roteiro" not in day_plan_txt:
        lines.append("📋 *Roteiro do Dia:*")
        lines.append(day_plan_txt)
        lines.append("")

    if todos_tasks:
        lines.append("⏰ *Compromissos & Afazeres de Hoje:*")
        for e in todos_tasks:
            lines.append(f"• `{e.time or '—'}` {e.title}")
        lines.append("")

    lines.append(f"🎯 *Foco & Estudo de Hoje* ({total_blocks} blocos previstos):")
    for f in focus_tasks:
        lines.append(f"• `{f.id}` | [{f.planned_blocks}b] {f.title}")
    if not focus_tasks:
        lines.append("• Nenhum tópico puxado para hoje.")

    if github_data.get("repo_prs"):
        lines.append(f"\n📦 *Novos PRs no {config.GITHUB_TARGET_REPO} (para revisar):*")
        for p in github_data["repo_prs"]:
            lines.append(f"• {p}")

    if github_data.get("repo_issues"):
        lines.append(f"\n🐛 *Novas Issues no {config.GITHUB_TARGET_REPO}:*")
        for iss in github_data["repo_issues"]:
            lines.append(f"• {iss}")

    if email_items:
        lines.append("\n📬 *E-mails recentes:*")
        for m in email_items[:3]:
            lines.append(f"• {m}")

    lines.append("\n_Bora pra cima!_")
    return "\n".join(lines)
