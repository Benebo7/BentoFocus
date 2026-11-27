# BentoFocus Agent

Optional always-on **Telegram** companion for BentoFocus. It turns voice notes and text messages into structured tasks, schedules timed reminders, compiles a morning digest (agenda, GitHub repository activity, and unread IMAP emails), and syncs tasks/day plans with the desktop app via **Turso (libSQL)** (or local SQLite for testing).

> [!NOTE]
> Like the rest of BentoFocus, this agent was built 100% with AI-assisted coding for personal use. The bot replies and Gemini prompts are currently in Brazilian Portuguese (`pt-BR`).

## Features

1. **Autonomous Gemini Agent with Tool Calling**:
   - Uses `google-genai` tool calling ([`gemini_agent.py`](gemini_agent.py) + [`tools.py`](tools.py)) to distinguish between:
     - **Day Plan (`day_plans`)**: Free-text macro plan for the day (e.g., morning/afternoon/evening routine).
     - **Focus / Study Topics (`kind="focus"`)**: Technical study folders and subtopics tracked in 25-minute blocks.
     - **Todos & Appointments (`kind="todo"`)**: Everyday tasks, errands, and timed events (`HH:MM`).
   - Supports batch scheduling, completing, postponing, and deleting existing items without duplicating them.
2. **Timed Appointment Reminders**:
   - Checks timed items every 60 seconds and sends a Telegram reminder `reminder_minutes` (default: 15 min) before the scheduled time.
3. **Scheduled Morning Digest**:
   - Runs daily at `DIGEST_HOUR:DIGEST_MINUTE` (default `08:00` `America/Sao_Paulo`), combining your day plan, active directives, today's tasks/events, new community PRs/issues on `GITHUB_TARGET_REPO`, and unread IMAP emails.
4. **Cloud Sync via Turso (libSQL)**:
   - When `TURSO_URL` and `TURSO_TOKEN` are set, both the cloud Telegram agent and your local BentoFocus desktop instance share the same task and day-plan state.

## Telegram Commands

- **Voice note / free text**: Speak or type naturally (batch updates, scheduling, day plans, or saving future directives).
- `/start`: Shows your Telegram user ID and quick usage help.
- `/hoje`: Shows today's day plan, focus topics, and todos.
- `/amanha`: Shows tomorrow's scheduled focus topics and todos.
- `/estudos` (alias `/foco`): Lists study folders and subtopics in the backlog.
- `/afazeres` (alias `/inbox`): Lists pending todos and inbox items.
- `/puxar <id>`: Moves a task or study subtopic to today.
- `/feito <id>`: Marks an item as done.
- `/adiar <id>`: Postpones an item to tomorrow.
- `/roteiro [amanha] <text>`: Views or updates the free-text day plan.
- `/digest`: Generates and sends the morning digest immediately.

## Setup & Deployment

1. Copy the example environment file and fill in your credentials:

```bash
cd agent
cp .env.example .env
```

2. Run locally for testing (uses local `agent_tasks.db` if `TURSO_URL`/`TURSO_TOKEN` are empty):

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd ..
agent/.venv/bin/python -m agent.main
```

3. Or deploy on a Linux VM (e.g., via `ssh <user>@<your-server-ip>`):

```bash
cd BentoFocus/agent
./deploy/setup_azure.sh
nano .env
sudo systemctl restart bentofocus-agent
sudo journalctl -u bentofocus-agent -f
```

> [!IMPORTANT]
> **Security:** The bot **fails closed** when `TELEGRAM_ALLOWED_USER_ID` is `0` — all commands except `/start` are ignored until configured. After running `/start` on your bot for the first time, copy your numeric Telegram user ID into `TELEGRAM_ALLOWED_USER_ID` in `agent/.env` and restart the service.
