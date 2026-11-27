import os
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Sao_Paulo")
DATA_DIR = Path(__file__).parent


def now_brt() -> datetime:
    """Retorna o datetime atual no fuso America/Sao_Paulo (sem tzinfo para fácil comparação)."""
    return datetime.now(TZ).replace(tzinfo=None)


def today_brt() -> date:
    """Retorna a data atual no fuso America/Sao_Paulo."""
    return datetime.now(TZ).date()


# Carrega .env se existir na raiz do agent
ENV_PATH = Path(__file__).parent / ".env"
try:
    from dotenv import load_dotenv
    if ENV_PATH.exists():
        load_dotenv(ENV_PATH)
except ImportError:
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
# ID numérico do seu usuário do Telegram (para evitar que estranhos mandem comando no seu bot)
# Deixe 0 ou vazio se não quiser filtrar no início
TELEGRAM_ALLOWED_USER_ID = int(os.getenv("TELEGRAM_ALLOWED_USER_ID", "0"))

# Gemini API (Google AI Studio)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
GEMINI_FALLBACK_MODEL = "gemini-3.5-flash-lite"

# GitHub (Personal Access Token)
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_USERNAME = os.getenv("GITHUB_USERNAME", "")
GITHUB_TARGET_REPO = os.getenv("GITHUB_TARGET_REPO", "")

# E-mail (IMAP)
IMAP_HOST = os.getenv("IMAP_HOST", "imap.gmail.com")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
IMAP_USER = os.getenv("IMAP_USER", "")
IMAP_PASSWORD = os.getenv("IMAP_PASSWORD", "").replace(" ", "").strip()  # App Password gerada na conta Google

# Banco compartilhado (Turso / libSQL)
TURSO_URL = os.getenv("TURSO_URL", "")  # e.g. https://your-db-org.turso.io
TURSO_TOKEN = os.getenv("TURSO_TOKEN", "")
# Fallback local se Turso não estiver configurado
SQLITE_PATH = Path(os.getenv("AGENT_SQLITE_PATH", Path(__file__).parent / "agent_tasks.db"))

# Horário do resumo matinal diário (HH:MM)
DIGEST_HOUR = int(os.getenv("DIGEST_HOUR", "8"))
DIGEST_MINUTE = int(os.getenv("DIGEST_MINUTE", "0"))

