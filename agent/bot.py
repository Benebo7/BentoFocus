import asyncio
import datetime
import io
import logging
from zoneinfo import ZoneInfo
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import config, db
from .digest import generate_morning_digest
from .gemini_agent import GeminiAgent
from .gemini_parser import GeminiParser
from .models import GeminiVoiceResponse

logger = logging.getLogger("agent.bot")


def _is_authorized(user_id: int) -> bool:
    return bool(config.TELEGRAM_ALLOWED_USER_ID) and user_id == config.TELEGRAM_ALLOWED_USER_ID


def _format_today_agenda() -> str:
    today_str = config.today_brt().isoformat()
    focus_today = db.get_today_focus()
    todos_today = db.get_today_todos()
    plan_txt = (db.get_day_plan(today_str).get("content") or "").strip()

    if not focus_today and not todos_today and not plan_txt:
        return "☀️ Nada agendado para hoje ainda!\n\n💡 _Dica: mande áudio falando o que quer estudar ou fazer hoje._"

    lines = [f"📅 *Agenda de Hoje ({today_str})*\n"]
    if plan_txt:
        lines.append("📝 *Roteiro do Dia:*")
        lines.append(plan_txt)
        lines.append("")

    if focus_today:
        total_b = sum(t.planned_blocks for t in focus_today if t.status != "done")
        lines.append(f"🎯 *Tópicos de Foco* ({total_b} blocos previstos):")
        for f in focus_today:
            icon = "✅" if f.status == "done" else "▫️"
            prog = f"[{f.completed_blocks}/{f.planned_blocks}b]"
            lines.append(f"{icon} `{f.id}` | {prog} *{f.title}*")
        lines.append("")

    if todos_today:
        lines.append("📋 *Afazeres de Hoje* (sem timer):")
        for td in todos_today:
            icon = "✅" if td.status == "done" else "▫️"
            time_str = f"`{td.time}` " if td.time else ""
            lines.append(f"{icon} {time_str}*{td.title}* (`{td.id}`)")
        lines.append("")

    return "\n".join(lines).strip()


def _format_tomorrow_agenda() -> str:
    tomorrow_str = (config.today_brt() + datetime.timedelta(days=1)).isoformat()
    all_tasks = db.get_tasks()
    focus_tomorrow = [t for t in all_tasks if t.kind == "focus" and t.day == tomorrow_str and t.status != "cancelled"]
    todos_tomorrow = [t for t in all_tasks if t.kind in ("todo", "event") and t.day == tomorrow_str and t.status != "cancelled"]
    plan_txt = (db.get_day_plan(tomorrow_str).get("content") or "").strip()

    if not focus_tomorrow and not todos_tomorrow and not plan_txt:
        return f"🌅 *Agenda de Amanhã ({tomorrow_str})*\n\nNada agendado para amanhã ainda!\n💡 _Dica: diga 'amanhã quero estudar X' para agendar um subtópico._"

    lines = [f"🌅 *Agenda de Amanhã ({tomorrow_str})*\n"]
    if plan_txt:
        lines.append("📝 *Roteiro do Dia:*")
        lines.append(plan_txt)
        lines.append("")

    if focus_tomorrow:
        total_b = sum(t.planned_blocks for t in focus_tomorrow if t.status != "done")
        lines.append(f"🎯 *Tópicos de Foco* ({total_b} blocos previstos):")
        for f in focus_tomorrow:
            prog = f"[{f.completed_blocks}/{f.planned_blocks}b]"
            lines.append(f"▫️ `{f.id}` | {prog} *{f.title}*")
        lines.append("")

    if todos_tomorrow:
        lines.append("📋 *Afazeres & Compromissos:*")
        for td in sorted(todos_tomorrow, key=lambda x: x.time or "99:99"):
            time_str = f"`{td.time}` " if td.time else ""
            lines.append(f"▫️ {time_str}*{td.title}* (`{td.id}`)")
        lines.append("")

    return "\n".join(lines).strip()


def _execute_voice_actions(result: GeminiVoiceResponse) -> list[str]:
    logs = []
    today_str = config.today_brt().isoformat()
    for act in result.actions:
        if act.action_type == "create" and act.task:
            t = act.task
            parent_id = None
            if t.parent_topic:
                parent_id = db.find_or_create_parent_topic(t.parent_topic)

            kind = "todo" if t.kind in ("todo", "event") else "focus"
            day = t.due_date
            status = "today" if t.due_date == today_str else "inbox"

            created = db.create_task(
                title=t.title,
                parent_id=parent_id,
                description=t.description,
                kind=kind,
                time=t.time,
                reminder_minutes=t.reminder_minutes,
                planned_blocks=t.planned_blocks if kind == "focus" else 1,
                day=day,
                status=status,
                source="telegram",
            )
            if kind == "focus":
                folder_str = f"[{t.parent_topic}] " if t.parent_topic else ""
                logs.append(f"🎯 *{folder_str}{created.title}* ({created.planned_blocks}b)")
            else:
                date_str = f" p/ {created.day}" if created.day else " (sem data)"
                time_str = f" às {created.time}" if created.time else ""
                logs.append(f"📋 *{created.title}*{time_str}{date_str}")

        elif act.action_type == "set_date" and act.target_task_id:
            db.set_task_date(act.target_task_id, act.target_date)
            date_label = "sem data" if act.target_date == "clear" or not act.target_date else act.target_date
            logs.append(f"📅 Item `{act.target_task_id}` atualizado para `{date_label}`")

        elif act.action_type == "pull_today" and act.target_task_id:
            if act.target_date and act.target_date != today_str:
                db.set_task_date(act.target_task_id, act.target_date)
                logs.append(f"📅 Item `{act.target_task_id}` agendado para `{act.target_date}`")
            else:
                db.pull_to_today(act.target_task_id)
                logs.append(f"☀️ Item `{act.target_task_id}` puxado para a lista de Hoje")

        elif act.action_type == "progress" and act.target_task_id:
            db.record_progress(act.target_task_id)
            logs.append(f"🔥 +1 bloco registrado no item `{act.target_task_id}`!")

        elif act.action_type == "postpone" and act.target_task_id:
            db.postpone_task(act.target_task_id, days=act.postpone_days)
            logs.append(f"⏩ Item `{act.target_task_id}` adiado em {act.postpone_days} dia(s)")

        elif act.action_type == "complete" and act.target_task_id:
            db.complete_task(act.target_task_id)
            logs.append(f"🎉 Item `{act.target_task_id}` concluído!")

        elif act.action_type == "delete" and act.target_task_id:
            db.delete_task(act.target_task_id)
            logs.append(f"🗑️ Item `{act.target_task_id}` removido")

    return logs


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not user.id:
        return
    if not config.TELEGRAM_ALLOWED_USER_ID:
        await update.message.reply_text(
            f"🆔 Seu Telegram ID: `{user.id}`\n\n"
            f"Defina `TELEGRAM_ALLOWED_USER_ID={user.id}` no `agent/.env` e reinicie o bot.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    if not _is_authorized(user.id):
        return
    _save_last_chat_id(user.id)
    msg = (
        f"Olá! 👋\n\n"
        f"Seu agente pessoal do **BentoFocus** está pronto.\n"
        f"🆔 Seu Telegram ID: `{user.id}`\n\n"
        f"Como usar:\n"
        f"• Mande áudio livre (ex: *'anota CNI no Kubernetes'*, *'agenda dentista pro dia 5'*, *'já fiz redes'*).\n"
        f"• `/hoje` — Ver foco e afazeres de hoje\n"
        f"• `/amanha` — Ver tópicos e afazeres agendados para amanhã\n"
        f"• `/estudos` — Ver tópicos de estudo no backlog\n"
        f"• `/afazeres` — Ver afazeres gerais e agendados\n"
        f"• `/digest` — Ver resumo matinal completo\n"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update.effective_user.id):
        return
    if update.effective_user and update.effective_user.id:
        _save_last_chat_id(update.effective_user.id)

    msg = await update.message.reply_text("🎙️ Ouvindo e processando...")
    try:
        voice = update.message.voice
        file = await context.bot.get_file(voice.file_id)
        
        buffer = bytearray()
        await file.download_as_bytearray(buffer)

        # Chama o Agente Autônomo com Tools
        agent = GeminiAgent()
        reply_text = await asyncio.to_thread(agent.handle_message, "", bytes(buffer), "audio/ogg")

        try:
            await msg.delete()
        except Exception:
            pass

        try:
            await update.message.reply_text(reply_text, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            await update.message.reply_text(reply_text)
    except Exception as e:
        logger.exception("Erro ao processar áudio")
        try:
            await msg.delete()
        except Exception:
            pass
        await update.message.reply_text(f"❌ Erro ao processar áudio: {e}")


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update.effective_user.id):
        return
    if update.effective_user and update.effective_user.id:
        _save_last_chat_id(update.effective_user.id)

    text = update.message.text
    if text.startswith("/"):
        return

    try:
        # Chama o Agente Autônomo com Tools
        agent = GeminiAgent()
        reply_text = await asyncio.to_thread(agent.handle_message, text)
        try:
            await update.message.reply_text(reply_text, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            await update.message.reply_text(reply_text)
    except Exception as e:
        logger.exception("Erro ao processar texto")
        await update.message.reply_text(f"❌ Erro: {e}")


async def hoje_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update.effective_user.id):
        return
    await update.message.reply_text(_format_today_agenda(), parse_mode=ParseMode.MARKDOWN)


async def amanha_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update.effective_user.id):
        return
    await update.message.reply_text(_format_tomorrow_agenda(), parse_mode=ParseMode.MARKDOWN)


async def estudos_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update.effective_user.id):
        return
    all_focus = db.get_tasks(kind="focus")
    all_focus = [t for t in all_focus if t.status != "done"]
    if not all_focus:
        await update.message.reply_text("📚 Nenhum tópico de estudo cadastrado. Mande um áudio criando um!")
        return

    parents = [t for t in all_focus if t.parent_id is None]
    children = [t for t in all_focus if t.parent_id is not None]

    lines = ["📚 *Tópicos de Estudo & Foco:*\n"]
    for p in parents:
        sub = [c for c in children if c.parent_id == p.id]
        lines.append(f"📁 *{p.title}* (`{p.id}`)")
        for s in sub:
            prog = f"[{s.completed_blocks}/{s.planned_blocks}b]"
            tag_today = " *(Hoje)*" if s.status == "today" else ""
            lines.append(f"  └ 🎯 `{s.id}` | {prog} {s.title}{tag_today}")
        if not sub:
            prog = f"[{p.completed_blocks}/{p.planned_blocks}b]"
            lines.append(f"  └ 🎯 {prog} (sem subtópicos)")
        lines.append("")

    lines.append("_Use `/puxar <id>` para trazer um subtópico para Hoje._")
    await update.message.reply_text("\n".join(lines).strip(), parse_mode=ParseMode.MARKDOWN)


async def afazeres_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update.effective_user.id):
        return
    all_todos = [t for t in db.get_tasks() if t.kind in ("todo", "event") and t.status != "done"]
    if not all_todos:
        await update.message.reply_text("📋 Nenhum afazer pendente no momento!")
        return

    inbox = [t for t in all_todos if not t.day]
    scheduled = [t for t in all_todos if t.day]

    lines = ["📋 *Lista de Afazeres & Compromissos:*\n"]
    if scheduled:
        lines.append("📅 *Agendados:*")
        for s in sorted(scheduled, key=lambda x: (x.day or "", x.time or "")):
            time_str = f" às {s.time}" if s.time else ""
            lines.append(f"• `{s.id}` | {s.day}{time_str} — *{s.title}*")
        lines.append("")

    if inbox:
        lines.append("📥 *Sem data (Inbox):*")
        for i in inbox:
            lines.append(f"• `{i.id}` | *{i.title}*")
        lines.append("")

    lines.append("_Dica: mande 'agenda <ID> pro dia X' ou use `/feito <id>`._")
    await update.message.reply_text("\n".join(lines).strip(), parse_mode=ParseMode.MARKDOWN)


async def puxar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Informe o ID da tarefa para puxar para hoje. Ex: `/puxar 3`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        task_id = int(context.args[0])
        ok = db.pull_to_today(task_id)
        if ok:
            await update.message.reply_text(f"☀️ Item `{task_id}` puxado para a lista de Hoje!\n\n" + _format_today_agenda(), parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(f"⚠️ Item `{task_id}` não encontrado.")
    except ValueError:
        await update.message.reply_text("ID inválido.")


async def feito_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Informe o ID da tarefa. Ex: `/feito 3`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        task_id = int(context.args[0])
        ok = db.complete_task(task_id)
        if ok:
            msg = f"🎉 Item `{task_id}` marcado como concluído!\n\n" + _format_today_agenda()
            await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(f"⚠️ Item `{task_id}` não encontrado.", parse_mode=ParseMode.MARKDOWN)
    except ValueError:
        await update.message.reply_text("ID inválido.")


async def adiar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Informe o ID da tarefa para adiar. Ex: `/adiar 3`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        task_id = int(context.args[0])
        res = db.postpone_task(task_id, days=1)
        if res:
            msg = f"⏩ Item `{task_id}` adiado para amanhã!\n\n" + _format_today_agenda()
            await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(f"⚠️ Item `{task_id}` não encontrado.", parse_mode=ParseMode.MARKDOWN)
    except ValueError:
        await update.message.reply_text("ID inválido.")


async def digest_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update.effective_user.id):
        return
    msg = await update.message.reply_text("⏳ Compilando resumo matinal...")
    digest_text = generate_morning_digest()
    try:
        await msg.edit_text(digest_text, parse_mode=ParseMode.MARKDOWN)
    except Exception:
        await msg.edit_text(digest_text)


async def roteiro_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update.effective_user.id):
        return
    args = context.args or []
    today = config.today_brt()
    target_day = today.isoformat()
    day_label = "hoje"

    if args and args[0].lower() in ("amanha", "amanhã"):
        target_day = (today + datetime.timedelta(days=1)).isoformat()
        day_label = "amanhã"
        args = args[1:]

    text_to_set = " ".join(args).strip()
    if text_to_set:
        db.set_day_plan(target_day, text_to_set)
        await update.message.reply_text(f"✅ Roteiro de {day_label} ({target_day}) salvo:\n\n{text_to_set}")
    else:
        plan = db.get_day_plan(target_day)
        content = (plan.get("content") or "").strip()
        if content:
            await update.message.reply_text(f"📋 *Roteiro de {day_label}* ({target_day}):\n\n{content}", parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(f"Nenhum roteiro anotado para {day_label} ({target_day}).\nPara definir: `/roteiro <seu texto>` ou `/roteiro amanha <seu texto>`", parse_mode=ParseMode.MARKDOWN)



def _save_last_chat_id(chat_id: int):
    try:
        from pathlib import Path
        path = config.DATA_DIR / "last_chat_id.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(chat_id).strip())
    except Exception:
        pass


def _get_target_chat_id() -> int:
    if config.TELEGRAM_ALLOWED_USER_ID:
        return config.TELEGRAM_ALLOWED_USER_ID
    try:
        path = config.DATA_DIR / "last_chat_id.txt"
        if path.exists():
            val = path.read_text().strip()
            if val.isdigit() and int(val) > 0:
                return int(val)
    except Exception:
        pass
    return 0


async def check_event_reminders_job(context: ContextTypes.DEFAULT_TYPE):
    target_id = _get_target_chat_id()
    if not target_id:
        return
    due = db.get_due_reminders()
    for task in due:
        msg = (
            f"🔔 *LEMBRETE DE COMPROMISSO!*\n\n"
            f"⏰ *{task.title}* às *{task.time}*\n"
            f"Faltam aproximadamente {task.reminder_minutes} minutos!\n\n"
            f"Conclua falando por áudio ou `/feito {task.id}`"
        )
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text=msg,
                parse_mode=ParseMode.MARKDOWN,
            )
            db.mark_reminder_sent(task.id)
            logger.info(f"Lembrete enviado para tarefa {task.id}: {task.title}")
        except Exception as e:
            logger.error(f"Erro ao enviar lembrete para tarefa {task.id}: {e}")


async def daily_digest_job(context: ContextTypes.DEFAULT_TYPE):
    target_id = _get_target_chat_id()
    if not target_id:
        logger.warning("Digest matinal cancelado: nenhum TELEGRAM_ALLOWED_USER_ID nem last_chat_id encontrado.")
        return
    try:
        logger.info(f"Disparando daily digest para chat_id {target_id}...")
        digest_text = generate_morning_digest()
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text=digest_text,
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as parse_err:
            logger.warning(f"Erro de markdown no digest ({parse_err}). Enviando texto puro...")
            await context.bot.send_message(
                chat_id=target_id,
                text=digest_text,
            )
        logger.info("Daily digest enviado com sucesso!")
    except Exception as e:
        logger.exception(f"Erro ao disparar daily digest: {e}")


def create_bot_app() -> Application:
    if not config.TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN não está definido no .env!")

    app = (
        ApplicationBuilder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .read_timeout(30)
        .write_timeout(30)
        .connect_timeout(20)
        .build()
    )

    # Handlers
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("hoje", hoje_cmd))
    app.add_handler(CommandHandler("amanha", amanha_cmd))
    app.add_handler(CommandHandler("estudos", estudos_cmd))
    app.add_handler(CommandHandler("foco", estudos_cmd))
    app.add_handler(CommandHandler("afazeres", afazeres_cmd))
    app.add_handler(CommandHandler("inbox", afazeres_cmd))
    app.add_handler(CommandHandler("puxar", puxar_cmd))
    app.add_handler(CommandHandler("feito", feito_cmd))
    app.add_handler(CommandHandler("adiar", adiar_cmd))
    app.add_handler(CommandHandler("digest", digest_cmd))
    app.add_handler(CommandHandler("roteiro", roteiro_cmd))
    app.add_handler(MessageHandler(filters.VOICE, voice_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    if app.job_queue:
        app.job_queue.run_repeating(check_event_reminders_job, interval=60, first=10, name="event_reminders")

        try:
            tz = ZoneInfo("America/Sao_Paulo")
            digest_time = datetime.time(hour=config.DIGEST_HOUR, minute=config.DIGEST_MINUTE, tzinfo=tz)
            app.job_queue.run_daily(daily_digest_job, time=digest_time, name="daily_morning_digest")
            logger.info(f"Digest agendado para {digest_time} (America/Sao_Paulo)")
        except Exception as e:
            logger.warning(f"Não foi possível registrar o job diário: {e}")

    return app
